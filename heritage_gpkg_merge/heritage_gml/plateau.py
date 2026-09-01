from __future__ import annotations
from pathlib import Path
import hashlib, re, shutil, time
import requests
from .catalog import fetch_citygml_files_for_condition
from .model import CulturalRecord, PlateauCity, PlateauFile
from .util import unique_keep_order, safe_filename


def _conditions_for_record(r: CulturalRecord, use_geocode: bool) -> list[str]:
    g = r.geometry
    if g is not None and not g.is_empty:
        if g.geom_type == "Point":
            return [f"r:{g.x:.10f},{g.y:.10f}"]
        minx, miny, maxx, maxy = g.bounds
        if minx < maxx and miny < maxy:
            return [f"r:{minx:.10f},{miny:.10f},{maxx:.10f},{maxy:.10f}"]
        c = g.centroid
        return [f"r:{c.x:.10f},{c.y:.10f}"]
    if use_geocode and r.address:
        return [f"g:{r.address}"]
    return []


def resolve_remote_files(api_base: str, city: PlateauCity, records: list[CulturalRecord],
                         timeout_s: int, use_geocode: bool = False,
                         progress: bool = False):
    """Resolve the exact PLATEAU bldg mesh files needed for the records.

    No buffer, nearest-neighbour lookup, or inferred cultural-property polygon is
    introduced here.  ``progress`` only changes logging, never query semantics.
    """
    conditions = []
    for r in records:
        conditions.extend(_conditions_for_record(r, use_geocode))
    conditions = unique_keep_order(conditions)
    file_map, issues = {}, []
    total = len(conditions)
    for i, cond in enumerate(conditions, 1):
        if progress:
            print(f"  PLATEAU query [{i}/{total}]", flush=True)
        try:
            rows = fetch_citygml_files_for_condition(api_base, cond, city.city_code, timeout_s)
        except Exception as e:
            issues.append({"city_code": city.city_code, "condition": cond,
                           "reason": f"plateau_query_error: {e}"})
            continue
        for f in rows:
            url = str(f.get("url", ""))
            if not url:
                continue
            file_map[url] = PlateauFile(
                city_code=city.city_code, city_name=city.city,
                code=str(f.get("code", "")), url=url,
                max_lod=f.get("maxLod"), file_size=f.get("fileSize"),
                features=f.get("features"),
            )
    return list(file_map.values()), issues


def _download_name(pf: PlateauFile) -> str:
    tail = pf.url.split("?")[0].rstrip("/").split("/")[-1]
    if tail.lower().endswith(".gml"):
        return safe_filename(tail)
    h = hashlib.sha1(pf.url.encode("utf-8")).hexdigest()[:12]
    return f"{pf.city_code}_{safe_filename(pf.code or 'bldg')}_{h}.gml"


def purge_city_cache(cache_dir: str | Path, city_code: str) -> Path:
    """Delete the complete PLATEAU cache for one municipality.

    This is intentionally municipality-wide rather than file-by-file.  A read
    failure means the cache is no longer trusted as a set, so the whole set is
    reacquired on the single automatic recovery attempt.
    """
    target = Path(cache_dir).resolve() / str(city_code)
    if target.exists():
        shutil.rmtree(target)
    return target


def download_files(
    files: list[PlateauFile],
    cache_dir: str | Path,
    timeout_s: int | None = None,
    *,
    connect_timeout_s: int | float | None = None,
    read_timeout_s: int | float | None = None,
    retries: int = 3,
    backoff_s: float = 2.0,
    progress: bool = False,
):
    """Download PLATEAU GML files with cache and bounded retries.

    Returns ``(files, issues)``. Failed downloads keep ``local_path=None`` and
    are reported in ``issues``; they do not raise after the final retry.
    Existing non-empty cached files are reused. Content-level cache failures are
    handled later by the CityGML reader, which can trigger municipality-wide
    cache purge and reacquisition in API mode.
    """
    base = Path(cache_dir)
    base.mkdir(parents=True, exist_ok=True)
    issues = []

    if connect_timeout_s is None:
        connect_timeout_s = timeout_s or 120
    if read_timeout_s is None:
        read_timeout_s = timeout_s or 120
    request_timeout = (float(connect_timeout_s), float(read_timeout_s))
    attempts = max(1, int(retries))
    total = len(files)

    for i, pf in enumerate(files, 1):
        city_dir = base / pf.city_code
        city_dir.mkdir(parents=True, exist_ok=True)
        dest = city_dir / _download_name(pf)
        if dest.exists() and dest.stat().st_size > 0:
            pf.local_path = str(dest)
            if progress:
                print(f"  PLATEAU cache [{i}/{total}]: {dest.name}", flush=True)
            continue

        # Do not retain a stale path if the file was just purged or is absent.
        pf.local_path = None
        if progress:
            print(f"  PLATEAU download [{i}/{total}]: {dest.name}", flush=True)

        part = dest.with_suffix(dest.suffix + ".part")
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                if part.exists():
                    part.unlink()
                with requests.get(
                    pf.url, stream=True, timeout=request_timeout,
                    allow_redirects=True,
                ) as r:
                    r.raise_for_status()
                    with part.open("wb") as w:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                w.write(chunk)
                if not part.exists() or part.stat().st_size == 0:
                    raise IOError("download completed with an empty file")
                part.replace(dest)
                pf.local_path = str(dest)
                last_error = None
                break
            except (requests.RequestException, OSError) as e:
                last_error = e
                if part.exists():
                    try:
                        part.unlink()
                    except OSError:
                        pass
                if attempt < attempts:
                    delay = float(backoff_s) * (2 ** (attempt - 1))
                    print(
                        f"  download retry {attempt}/{attempts - 1}: "
                        f"{pf.code or pf.url} ({type(e).__name__}: {e})",
                        flush=True,
                    )
                    time.sleep(delay)

        if last_error is not None:
            pf.local_path = None
            issues.append({
                "city_code": pf.city_code,
                "file_code": pf.code,
                "url": pf.url,
                "attempts": attempts,
                "reason": f"plateau_download_error: {type(last_error).__name__}: {last_error}",
            })

    return files, issues


def local_files(local_dir: str | Path, city: PlateauCity):
    base = Path(local_dir).resolve()
    out = []
    for p in sorted(base.rglob("*.gml")):
        lname = p.name.lower()
        if "bldg" not in lname and "building" not in lname:
            continue
        mentions = re.findall(r"(?<!\d)(\d{5})(?!\d)", str(p))
        if mentions and city.city_code not in mentions:
            continue
        code = p.name.split("_")[0] if "_" in p.name else ""
        out.append(PlateauFile(city.city_code, city.city, code, "", local_path=str(p)))
    return out
