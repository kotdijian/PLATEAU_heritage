from __future__ import annotations
from pathlib import Path
from typing import Any
from io import BytesIO
import json, math, re
import pandas as pd
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import shape, Point
from shapely.ops import transform as shp_transform
from .model import CulturalRecord, PlateauCity
from .util import norm_text, norm_key, compact_address

ALIASES = {
    "id": ["NO","No","no","ID","id","文化財ID","管理番号","番号"],
    "name": ["名称","文化財名称","文化財名","name","title"],
    "place_name": ["場所名称","施設名称","所在地名称","所在名称","place_name","site_name"],
    "owner": ["所有者等","所有者","管理者","owner"],
    "address": ["住所","所在地","所在","address"],
    "municipality": ["市区町村名","自治体名","市町村","municipality","city"],
    "municipality_code": ["全国地方公共団体コード","自治体コード","市区町村コード","municipality_code","city_code"],
    "latitude": ["緯度","lat","latitude","Latitude"],
    "longitude": ["経度","lon","lng","longitude","Longitude"],
    "category": ["文化財分類","指定区分","分類","category"],
    "type": ["種類","種別","文化財種類","type"],
    "designation": ["指定等","指定登録区分","designation"],
    "designation_date": ["文化財指定日","指定年月日","指定日","designation_date"],
}

def discover_files(data_dir: str | Path, recursive: bool = False) -> list[Path]:
    p = Path(data_dir).resolve()
    globber = p.rglob if recursive else p.glob
    items = []
    for pat in ("*.csv","*.CSV","*.json","*.JSON","*.geojson","*.GeoJSON"):
        items.extend(globber(pat))
    out, seen = [], set()
    for f in sorted(items):
        parts = {x.lower() for x in f.parts}
        if ".cache" in parts or "output" in parts or f.name.startswith("heritage_"):
            continue
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp); out.append(rp)
    return out

def _read_csv(path: Path) -> pd.DataFrame:
    raw = path.read_bytes()
    last = None
    for enc in ("utf-8-sig","utf-8","cp932","shift_jis"):
        try:
            return pd.read_csv(BytesIO(raw), encoding=enc)
        except Exception as e:
            last = e
    raise ValueError(f"CSV decode failed: {path}: {last}")

def _rows_from_geojson(obj: dict) -> pd.DataFrame:
    rows = []
    for i, feat in enumerate(obj.get("features") or []):
        props = dict(feat.get("properties") or {})
        props["__geometry__"] = feat.get("geometry")
        props["__feature_index__"] = i
        rows.append(props)
    return pd.DataFrame(rows)

def read_tabular(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return _read_csv(path)
    obj = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(obj, dict) and obj.get("type") == "FeatureCollection":
        return _rows_from_geojson(obj)
    if isinstance(obj, list):
        return pd.DataFrame(obj)
    if isinstance(obj, dict):
        for k in ("data","results","records","items"):
            if isinstance(obj.get(k), list):
                return pd.DataFrame(obj[k])
        return pd.DataFrame([obj])
    raise ValueError(f"Unsupported JSON structure: {path}")

def _resolve(df: pd.DataFrame, logical: str, explicit: dict) -> str | None:
    if explicit.get(logical):
        c = explicit[logical]
        return c if c in df.columns else None
    for c in ALIASES.get(logical, []):
        if c in df.columns:
            return c
    return None

def _geometry_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if "__geometry__" in df.columns:
        return "__geometry__"
    for c in candidates:
        if c in df.columns:
            return c
    return None

def _parse_geometry(value: Any):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        try:
            return shape(value)
        except Exception:
            return None
    s = norm_text(value)
    if not s:
        return None
    try:
        return shape(json.loads(s)) if s.startswith("{") else wkt.loads(s)
    except Exception:
        return None

def _to_wgs84(geom, source_crs: str):
    if geom is None:
        return None
    if str(source_crs).upper() in ("EPSG:4326","4326"):
        return geom
    tr = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    return shp_transform(tr.transform, geom)

def _filename_code(path: Path) -> str:
    m = re.match(r"^(\d{5})(?:\D|$)", path.name)
    return m.group(1) if m else ""

def _looks_for_city(row, cols, city: PlateauCity, filename_code: str) -> bool:
    ccol = cols.get("municipality_code")
    if ccol:
        raw = re.sub(r"\D", "", norm_text(row.get(ccol, "")))
        if len(raw) >= 5:
            return raw[:5] == city.city_code
    if filename_code:
        return filename_code == city.city_code
    mcol = cols.get("municipality")
    if mcol and city.city and city.city in norm_text(row.get(mcol, "")):
        return True
    acol = cols.get("address")
    if acol and city.city and city.city in norm_text(row.get(acol, "")):
        return True
    return False

def _classify_movable(category: str, typ: str, keywords: list[str]) -> bool:
    s = f"{category} {typ}"
    return any(k in s for k in keywords)

def load_records_for_city(files: list[Path], city: PlateauCity, cultural_cfg: dict):
    records, issues = [], []
    overrides = cultural_cfg.get("file_overrides") or {}
    geometry_candidates = cultural_cfg.get("geometry_columns") or []
    for path in files:
        try:
            df = read_tabular(path)
        except Exception as e:
            issues.append({"source_file": str(path), "reason": f"read_error: {e}"})
            continue
        if df.empty:
            continue
        override = {}
        for pattern, ov in overrides.items():
            if path.match(pattern):
                override.update(ov or {})
        columns_cfg = dict(cultural_cfg.get("columns") or {})
        columns_cfg.update(override.get("columns") or {})
        cols = {k: _resolve(df, k, columns_cfg) for k in ALIASES}
        gcol = override.get("geometry_column") or _geometry_col(df, geometry_candidates)
        source_crs = override.get("input_crs", cultural_cfg.get("input_crs", "EPSG:4326"))
        fcode = _filename_code(path)
        if not cols.get("name"):
            continue

        for idx, row in df.iterrows():
            if not _looks_for_city(row, cols, city, fcode):
                continue
            geom = _parse_geometry(row.get(gcol)) if gcol else None
            if geom is not None:
                try:
                    geom = _to_wgs84(geom, source_crs)
                except Exception as e:
                    issues.append({"source_file": str(path), "row": int(idx), "reason": f"geometry_crs_error: {e}"})
                    geom = None
            if geom is None and cols.get("latitude") and cols.get("longitude"):
                try:
                    lat = float(row.get(cols["latitude"]))
                    lon = float(row.get(cols["longitude"]))
                    if 20 <= lat <= 50 and 120 <= lon <= 155:
                        geom = Point(lon, lat)
                except Exception:
                    pass

            def val(key):
                c = cols.get(key)
                return norm_text(row.get(c, "")) if c else ""

            category, typ = val("category"), val("type")
            records.append(CulturalRecord(
                source_file=str(path),
                record_id=val("id") or f"{path.stem}:{idx}",
                name=val("name"),
                place_name=val("place_name"),
                owner=val("owner"),
                address=val("address"),
                municipality=val("municipality") or city.city,
                municipality_code=city.city_code,
                category=category,
                type=typ,
                designation=val("designation"),
                designation_date=val("designation_date"),
                geometry=geom,
                movable=_classify_movable(category, typ, cultural_cfg.get("movable_keywords") or []),
            ))

    dedup = {}
    for r in records:
        xy = ""
        if r.geometry is not None and r.geometry.geom_type == "Point":
            xy = f"{r.geometry.x:.7f},{r.geometry.y:.7f}"
        key = (norm_key(r.name), compact_address(r.address), xy, norm_key(r.category), norm_key(r.type), norm_key(r.designation))
        dedup.setdefault(key, r)
    return list(dedup.values()), issues

def assign_complexes(records: list[CulturalRecord]) -> list[CulturalRecord]:
    groups, no_key = {}, []
    for i, r in enumerate(records):
        place, owner, addr = norm_key(r.place_name), norm_key(r.owner), compact_address(r.address)
        if place:
            key = ("place", place)
        elif owner and addr:
            key = ("owner_address", owner, addr)
        elif addr:
            key = ("address", addr)
        else:
            key = None
        (groups.setdefault(key, []).append(i) if key else no_key.append(i))

    spatial_groups = []
    for idx in no_key:
        g = records[idx].geometry
        placed = False
        if g is not None:
            for sg in spatial_groups:
                if any(records[j].geometry is not None and g.intersects(records[j].geometry) for j in sg):
                    sg.append(idx); placed = True; break
        if not placed:
            spatial_groups.append([idx])

    group_indices = list(groups.values()) + spatial_groups
    group_indices.sort(key=lambda inds: min(inds))
    for n, inds in enumerate(group_indices, 1):
        cid = f"{records[inds[0]].municipality_code}-HG{n:05d}"
        rr = [records[i] for i in inds]
        names = [r.place_name for r in rr if r.place_name]
        if names:
            cname = names[0]
        elif rr[0].owner:
            cname = re.sub(r"^(宗教法人|公益財団法人|一般財団法人|公益社団法人|一般社団法人)\s*", "", rr[0].owner).strip()
        else:
            cname = rr[0].name
        for i in inds:
            records[i].complex_id = cid
            records[i].complex_name = cname
    return records
