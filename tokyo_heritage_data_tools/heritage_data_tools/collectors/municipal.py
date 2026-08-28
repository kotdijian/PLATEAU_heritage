from __future__ import annotations

from pathlib import Path
import json
import time
import yaml

from ..http import session
from ..util import sha256_bytes, utc_now, write_json, safe_name, validate_municipal_code, validate_pref_code


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


def _pick_source(entry: dict):
    # Prefer official/direct CSV when available because it preserves the
    # municipality's published raw table. Fall back to the Tokyo API JSON.
    if entry.get("source_csv_url"):
        return "csv", entry["source_csv_url"], "GET"
    if entry.get("json_endpoint"):
        return "json", entry["json_endpoint"], (entry.get("method") or "POST").upper()
    return None, None, None


def collect(
    manifest_path: str | Path,
    output_dir: str | Path,
    area_code: str = "13",
    codes: list[str] | None = None,
    timeout: tuple[int, int] = (30, 120),
    retries: int = 3,
    overwrite: bool = False,
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
        source_type, url, method = _pick_source(entry)
        municipality_dir = out / code
        municipality_dir.mkdir(parents=True, exist_ok=True)

        meta_path = municipality_dir / "source.json"
        if source_type is None:
            result = {
                "municipality_code": code,
                "organization": entry.get("organization", ""),
                "status": "no_downloadable_source",
                "source_url": "",
                "collected_at": utc_now(),
            }
            write_json(meta_path, {**entry, **result})
            results.append(result)
            print(f"[{code}] no downloadable source")
            continue

        ext = ".csv" if source_type == "csv" else ".json"
        data_path = municipality_dir / f"data{ext}"
        if data_path.exists() and not overwrite:
            result = {
                "municipality_code": code,
                "organization": entry.get("organization", ""),
                "status": "cached",
                "source_type": source_type,
                "source_url": url,
                "local_path": str(data_path),
                "sha256": sha256_bytes(data_path.read_bytes()),
                "collected_at": utc_now(),
            }
            results.append(result)
            print(f"[{code}] cached {data_path.name}")
            continue

        try:
            if method == "POST":
                r = s.post(url, timeout=timeout)
            else:
                r = s.get(url, timeout=timeout)
            r.raise_for_status()
            payload = r.content
            if not payload:
                raise ValueError("empty response")
            tmp = data_path.with_suffix(data_path.suffix + ".part")
            tmp.write_bytes(payload)
            tmp.replace(data_path)

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
            }
            write_json(meta_path, {
                "manifest_entry": entry,
                "collection": result,
            })
            results.append(result)
            print(f"[{code}] downloaded {data_path.name} ({len(payload):,} bytes)")
        except Exception as e:
            for p in municipality_dir.glob("*.part"):
                p.unlink(missing_ok=True)
            result = {
                "municipality_code": code,
                "organization": entry.get("organization", ""),
                "status": "failed",
                "source_type": source_type,
                "source_url": url,
                "error": str(e),
                "collected_at": utc_now(),
            }
            write_json(meta_path, {
                "manifest_entry": entry,
                "collection": result,
            })
            results.append(result)
            print(f"[{code}] FAILED: {e}")

    write_json(out / "collection_manifest.json", {
        "source_manifest": str(Path(manifest_path).resolve()),
        "area_code": area_code,
        "generated_at": utc_now(),
        "results": results,
    })
    return results
