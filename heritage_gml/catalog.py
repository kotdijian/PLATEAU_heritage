from __future__ import annotations
from typing import Any
import requests
from .model import PlateauCity
from .util import validate_area_code

def _latest_citygml_rows(payload: dict) -> list[dict]:
    rows = payload.get("latest_citygml")
    if isinstance(rows, list):
        return rows
    raw = payload.get("citygml", [])
    by_city = {}
    for r in raw if isinstance(raw, list) else []:
        c = str(r.get("city_code", ""))
        if not c:
            continue
        prev = by_city.get(c)
        if prev is None or int(r.get("year", 0)) > int(prev.get("year", 0)):
            by_city[c] = r
    return list(by_city.values())

def fetch_plateau_catalog(api_base: str, timeout_s: int = 120) -> dict:
    url = f"{api_base.rstrip('/')}/datacatalog/plateau-datasets"
    r = requests.get(url, timeout=timeout_s)
    r.raise_for_status()
    return r.json()

def cities_for_area(payload: dict, area_code: str) -> list[PlateauCity]:
    mode, code = validate_area_code(area_code)
    out = []
    for r in _latest_citygml_rows(payload):
        city_code = str(r.get("city_code", ""))
        pref_code = str(r.get("pref_code", "")).zfill(2)
        ftypes = [str(x) for x in (r.get("feature_types") or [])]
        if "bldg" not in ftypes:
            continue
        if mode == "prefecture" and pref_code != code:
            continue
        if mode == "municipality" and city_code != code:
            continue
        out.append(PlateauCity(
            pref_code=pref_code,
            pref=str(r.get("pref", "")),
            city_code=city_code,
            city=str(r.get("city", "")),
            year=r.get("year", "latest"),
            feature_types=ftypes,
            url=str(r.get("url", "")),
        ))
    dedup = {c.city_code: c for c in out}
    return [dedup[k] for k in sorted(dedup)]

def _city_objects(obj: Any) -> list[dict]:
    out = []
    if isinstance(obj, dict):
        if "cityCode" in obj and "files" in obj:
            out.append(obj)
        for v in obj.values():
            out.extend(_city_objects(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_city_objects(v))
    return out

def fetch_citygml_files_for_condition(api_base: str, condition: str, city_code: str, timeout_s: int = 120) -> list[dict]:
    url = f"{api_base.rstrip('/')}/datacatalog/citygml/{condition}"
    r = requests.get(url, params={"types": "bldg"}, timeout=timeout_s)
    r.raise_for_status()
    objs = _city_objects(r.json())
    exact = [o for o in objs if str(o.get("cityCode", "")) == city_code]
    if not exact and len(objs) == 1:
        exact = objs
    files = []
    for obj in exact:
        fmap = obj.get("files") or {}
        bldg = fmap.get("bldg")
        if bldg is None:
            for k, v in fmap.items():
                if "bldg" in str(k).lower():
                    bldg = v
                    break
        files.extend(bldg or [])
    return files
