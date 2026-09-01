from __future__ import annotations

from pathlib import Path
import json
import yaml

from ..http import session
from ..util import (
    sha256_bytes,
    utc_now,
    write_json,
    list_of_dicts,
    validate_municipal_code,
    validate_pref_code,
)


DEFAULT_API_PAGE_SIZE = 1000


def load_manifest(path: str | Path) -> dict:
    obj = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sources = obj.get("sources") or {}
    if not isinstance(sources, dict):
        raise ValueError("manifest must contain a mapping named 'sources'")
    return obj


def _eligible(code: str, area_code: str, codes: set[str] | None) -> bool:
    if code == "13000":  # Tokyo Metropolitan Government dataset is intentionally excluded.
        return False
    if codes and code not in codes:
        return False
    return code.startswith(area_code)


def _source_candidates(entry: dict) -> list[dict]:
    """Return acquisition candidates in priority order.

    Direct/official CSV is preferred because it preserves the municipality's
    published raw table.  If it is unavailable or stale, fall back to the
    Tokyo Open Data API JSON endpoint when one is present.
    """
    out: list[dict] = []
    csv_url = (entry.get("source_csv_url") or "").strip()
    if csv_url:
        out.append({"source_type": "csv", "url": csv_url, "method": "GET"})

    json_url = (entry.get("json_endpoint") or "").strip()
    if json_url:
        out.append({
            "source_type": "json",
            "url": json_url,
            "method": (entry.get("method") or "POST").upper(),
        })
    return out


def _existing_data_file(directory: Path) -> Path | None:
    for name in ("data.csv", "data.json"):
        p = directory / name
        if p.exists():
            return p
    return None


def _as_int(value, default=None):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _api_rows(obj: object) -> list[dict]:
    """Extract record rows from a Tokyo Open Data API response.

    The official API examples use ``hits``.  ``list_of_dicts`` remains as a
    compatibility fallback for heterogeneous/older response shapes.
    """
    if isinstance(obj, dict):
        hits = obj.get("hits")
        if isinstance(hits, list):
            return [x for x in hits if isinstance(x, dict)]
    return list_of_dicts(obj)


def _post_api_json(session_obj, url: str, timeout: tuple[int, int], page_size: int) -> tuple[bytes, dict]:
    """Download a Tokyo Open Data API JSON dataset with correct POST semantics.

    Tokyo's official usage documentation requires POST requests with
    ``Content-Type: application/json`` and an application/json response.  An
    empty JSON object means no column/search filtering.  ``limit``/``offset``
    are supplied as query parameters and pages are combined into a single
    response-shaped JSON object with a ``hits`` array.
    """
    if page_size <= 0:
        raise ValueError("api_page_size must be greater than zero")

    all_rows: list[dict] = []
    first_obj: dict | None = None
    total: int | None = None
    offset = 0
    pages = 0

    while True:
        params = {"limit": page_size}
        if offset:
            params["offset"] = offset
        r = session_obj.post(
            url,
            params=params,
            json={},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=timeout,
        )
        r.raise_for_status()
        try:
            obj = r.json()
        except Exception as e:
            raise ValueError(f"Tokyo Open Data API returned non-JSON content: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError("Tokyo Open Data API response is not a JSON object")

        pages += 1
        if first_obj is None:
            first_obj = obj
            total = _as_int(obj.get("total"), None)

        rows = _api_rows(obj)
        if rows:
            all_rows.extend(rows)

        # If total is absent, one response is the safest deterministic result.
        if total is None:
            break
        if len(all_rows) >= total:
            break
        if not rows:
            raise ValueError(
                f"Tokyo Open Data API pagination stopped before total was reached "
                f"({len(all_rows)}/{total} records)"
            )

        next_offset = len(all_rows)
        if next_offset <= offset:
            raise ValueError("Tokyo Open Data API pagination made no progress")
        offset = next_offset

    assert first_obj is not None
    merged = dict(first_obj)
    merged["hits"] = all_rows
    merged["subtotal"] = len(all_rows)
    if total is not None:
        merged["total"] = total
    merged["limit"] = page_size
    merged["offset"] = 0
    merged["_heritage_collection"] = {
        "pages": pages,
        "records_collected": len(all_rows),
        "requested_page_size": page_size,
    }

    payload = json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
    return payload, {
        "pages": pages,
        "records_collected": len(all_rows),
        "api_total": total,
    }


def _download_candidate(session_obj, candidate: dict, timeout: tuple[int, int], api_page_size: int):
    source_type = candidate["source_type"]
    url = candidate["url"]
    method = candidate["method"]

    if source_type == "json" and method == "POST":
        return _post_api_json(session_obj, url, timeout, api_page_size)

    if method == "POST":
        # Generic POST fallback: still send a JSON body so that APIs requiring
        # application/json do not receive a body-less request.
        r = session_obj.post(
            url,
            json={},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=timeout,
        )
    else:
        r = session_obj.get(url, timeout=timeout)
    r.raise_for_status()
    payload = r.content
    if not payload:
        raise ValueError("empty response")
    return payload, {}


def collect(
    manifest_path: str | Path,
    output_dir: str | Path,
    area_code: str = "13",
    codes: list[str] | None = None,
    timeout: tuple[int, int] = (30, 120),
    retries: int = 3,
    overwrite: bool = False,
    api_page_size: int = DEFAULT_API_PAGE_SIZE,
):
    area_code = validate_pref_code(area_code)
    manifest = load_manifest(manifest_path)
    sources = manifest.get("sources") or {}
    selected_codes = {validate_municipal_code(x) for x in codes} if codes else None

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    s = session(retries=retries)
    results = []

    for code in sorted(sources):
        code = str(code)
        if not _eligible(code, area_code, selected_codes):
            continue
        entry = sources[code] or {}
        candidates = _source_candidates(entry)
        municipality_dir = out / code
        municipality_dir.mkdir(parents=True, exist_ok=True)
        meta_path = municipality_dir / "source.json"

        if not candidates:
            result = {
                "municipality_code": code,
                "organization": entry.get("organization", ""),
                "status": "no_downloadable_source",
                "source_url": "",
                "collected_at": utc_now(),
            }
            write_json(meta_path, {"manifest_entry": entry, "collection": result})
            results.append(result)
            print(f"[{code}] no downloadable source")
            continue

        existing = _existing_data_file(municipality_dir)
        if existing is not None and not overwrite:
            source_type = existing.suffix.lower().lstrip(".")
            source_url = ""
            for c in candidates:
                if c["source_type"] == source_type:
                    source_url = c["url"]
                    break
            result = {
                "municipality_code": code,
                "organization": entry.get("organization", ""),
                "status": "cached",
                "source_type": source_type,
                "source_url": source_url,
                "local_path": str(existing),
                "sha256": sha256_bytes(existing.read_bytes()),
                "collected_at": utc_now(),
            }
            results.append(result)
            print(f"[{code}] cached {existing.name}")
            continue

        attempts = []
        success = None

        # overwrite=True means replace either old representation with the newly
        # successful preferred/fallback source, so remove only after success.
        for candidate in candidates:
            source_type = candidate["source_type"]
            url = candidate["url"]
            method = candidate["method"]
            try:
                payload, extra = _download_candidate(s, candidate, timeout, api_page_size)
                if not payload:
                    raise ValueError("empty response")

                data_path = municipality_dir / f"data.{source_type}"
                tmp = data_path.with_suffix(data_path.suffix + ".part")
                tmp.write_bytes(payload)
                tmp.replace(data_path)

                # Do not leave a stale representation from a previous --overwrite run.
                other = municipality_dir / ("data.json" if source_type == "csv" else "data.csv")
                if other.exists() and overwrite:
                    other.unlink()

                result = {
                    "municipality_code": code,
                    "organization": entry.get("organization", ""),
                    "dataset_title": entry.get("dataset_title", ""),
                    "status": "downloaded",
                    "source_type": source_type,
                    "http_method": method,
                    "source_url": url,
                    "local_path": str(data_path),
                    "size_bytes": len(payload),
                    "sha256": sha256_bytes(payload),
                    "collected_at": utc_now(),
                    **extra,
                }
                if attempts:
                    result["fallback_from"] = [a["source_type"] for a in attempts]
                write_json(meta_path, {
                    "manifest_entry": entry,
                    "collection": result,
                    "attempts": attempts,
                })
                results.append(result)
                success = result
                if attempts:
                    print(
                        f"[{code}] downloaded {data_path.name} ({len(payload):,} bytes) "
                        f"after fallback"
                    )
                else:
                    print(f"[{code}] downloaded {data_path.name} ({len(payload):,} bytes)")
                break
            except Exception as e:
                for p in municipality_dir.glob("*.part"):
                    p.unlink(missing_ok=True)
                attempts.append({
                    "source_type": source_type,
                    "http_method": method,
                    "source_url": url,
                    "error": str(e),
                })
                # Continue to API candidate if direct CSV is stale/unavailable.
                continue

        if success is None:
            last = attempts[-1] if attempts else {}
            result = {
                "municipality_code": code,
                "organization": entry.get("organization", ""),
                "status": "failed",
                "source_type": last.get("source_type", ""),
                "source_url": last.get("source_url", ""),
                "error": last.get("error", "no source succeeded"),
                "attempts": attempts,
                "collected_at": utc_now(),
            }
            write_json(meta_path, {
                "manifest_entry": entry,
                "collection": result,
                "attempts": attempts,
            })
            results.append(result)
            attempted = "; ".join(
                f"{a['source_type']}: {a['error']}" for a in attempts
            )
            print(f"[{code}] FAILED: {attempted}")

    write_json(out / "collection_manifest.json", {
        "source_manifest": str(Path(manifest_path).resolve()),
        "area_code": area_code,
        "generated_at": utc_now(),
        "api_page_size": api_page_size,
        "results": results,
    })
    return results
