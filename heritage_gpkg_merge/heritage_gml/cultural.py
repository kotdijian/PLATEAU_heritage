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
    "id": ["NO", "No", "no", "ID", "id", "文化財ID", "管理番号", "番号"],
    "name": ["名称", "文化財名称", "文化財名", "name", "title"],
    "place_name": ["場所名称", "施設名称", "所在地名称", "所在名称", "place_name", "site_name"],
    "address_detail": ["方書", "住所詳細", "所在地詳細", "所在詳細", "address_detail", "address_note"],
    "owner": ["所有者等", "所有者", "管理者", "owner"],
    "address": ["住所", "所在地", "所在", "address"],
    "municipality": ["市区町村名", "自治体名", "市町村", "municipality", "city"],
    "municipality_code": ["全国地方公共団体コード", "自治体コード", "市区町村コード", "municipality_code", "city_code"],
    "latitude": ["緯度", "lat", "latitude", "Latitude"],
    "longitude": ["経度", "lon", "lng", "longitude", "Longitude"],
    "category": ["文化財分類", "指定区分", "分類", "category"],
    "type": ["種類", "種別", "文化財種類", "type"],
    "designation": ["指定等", "指定登録区分", "designation"],
    "designation_date": ["文化財指定日", "指定年月日", "指定日", "designation_date"],
}


def discover_files(data_dir: str | Path, recursive: bool = False) -> list[Path]:
    p = Path(data_dir).resolve()
    globber = p.rglob if recursive else p.glob
    items = []
    for pat in ("*.csv", "*.CSV", "*.json", "*.JSON", "*.geojson", "*.GeoJSON"):
        items.extend(globber(pat))
    out, seen = [], set()
    for f in sorted(items):
        parts = {x.lower() for x in f.parts}
        if ".cache" in parts or "output" in parts or f.name.startswith("heritage_"):
            continue
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out


def _read_csv(path: Path) -> pd.DataFrame:
    raw = path.read_bytes()
    last = None
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
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
        for k in ("data", "results", "records", "items"):
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
    if str(source_crs).upper() in ("EPSG:4326", "4326"):
        return geom
    tr = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    return shp_transform(tr.transform, geom)


def _as_point(geom):
    """Cultural source geometry is normalized to one representative Point.

    The pipeline deliberately does not create a buffer or use cultural
    polygons/areas for matching.  If a non-point geometry is supplied, its
    representative_point() is retained only as a point observation.
    """
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "Point":
        return geom
    try:
        return geom.representative_point()
    except Exception:
        return None


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


def classify_entity(typ: str, cultural_cfg: dict) -> str:
    mapping = cultural_cfg.get("type_class_map") or {}
    key = norm_text(typ)
    cls = mapping.get(key, cultural_cfg.get("default_entity_class", "point"))
    if cls not in {"building_direct", "point", "movable"}:
        raise ValueError(f"Invalid entity class '{cls}' for type '{typ}'. Expected building_direct/point/movable.")
    return cls


def geometry_role_for(entity_class: str) -> str:
    # v0.5: movable records are no longer collapsed into address-group points.
    # Their source point is preserved exactly like other non-building records.
    if entity_class == "building_direct":
        return "building_candidate_point"
    return "representative_point"


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
            issues.append({
                "source_file": str(path),
                "reason": "not_cultural_record_dataset: no recognized name column",
            })
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
            geom = _as_point(geom)

            def val(key):
                c = cols.get(key)
                return norm_text(row.get(c, "")) if c else ""

            typ = val("type")
            entity_class = classify_entity(typ, cultural_cfg)
            records.append(CulturalRecord(
                source_file=str(path),
                record_id=val("id") or f"{path.stem}:{idx}",
                name=val("name"),
                place_name=val("place_name"),
                address_detail=val("address_detail"),
                owner=val("owner"),
                address=val("address"),
                municipality=val("municipality") or city.city,
                municipality_code=city.city_code,
                category=val("category"),
                type=typ,
                designation=val("designation"),
                designation_date=val("designation_date"),
                geometry=geom,
                entity_class=entity_class,
                geometry_role=geometry_role_for(entity_class),
                movable=(entity_class == "movable"),
            ))

    # Dedupe across source files without collapsing different designation/type rows.
    dedup = {}
    for r in records:
        xy = ""
        if r.geometry is not None:
            xy = f"{r.geometry.x:.7f},{r.geometry.y:.7f}"
        key = (
            norm_key(r.name), compact_address(r.address), xy,
            norm_key(r.category), norm_key(r.type), norm_key(r.designation),
        )
        dedup.setdefault(key, r)
    return list(dedup.values()), issues


def _normalized_site_label(value: str) -> str:
    """Normalize a source location label for naming, not for spatial inference.

    Examples: 浅草寺境内 -> 浅草寺, 浅草寺内 -> 浅草寺,
    聖徳寺墓地内 -> 聖徳寺.  This never creates a geometry or distance rule.
    """
    text = norm_text(value)
    if not text:
        return ""
    # Longest/specific suffixes first.
    text = re.sub(r"(?:墓地内|境内地|敷地内|構内|境域内|境内|寺内|社内|園内|館内|内)$", "", text).strip()
    return text


def _owner_label(value: str) -> str:
    return re.sub(
        r"^(宗教法人|学校法人|公益財団法人|一般財団法人|公益社団法人|一般社団法人|社会福祉法人)\s*",
        "", norm_text(value),
    ).strip()


def assign_complexes(records: list[CulturalRecord]) -> list[CulturalRecord]:
    """Assign semantic Heritage Complexes without distance thresholds.

    Grouping priority remains:
      place name -> owner + address -> address -> address detail -> exact point.

    `方書`/address_detail is preserved independently and used as a fallback
    semantic clue.  It does not replace the established owner+address/address
    priority and does not create a buffer or inferred site boundary.

    If two or more records in the same complex share the exact same coordinate,
    those observations are marked `shared_complex_coordinate`.  v0.5 does not
    assume that such a shared coordinate is an object-specific position.
    """
    groups: dict[tuple, list[int]] = {}
    no_key: list[int] = []
    methods: dict[tuple, str] = {}

    for i, r in enumerate(records):
        place = norm_key(r.place_name)
        owner = norm_key(r.owner)
        addr = compact_address(r.address)
        detail = norm_key(_normalized_site_label(r.address_detail))
        if place:
            key = ("place", place)
            method = "place_name"
        elif owner and addr:
            key = ("owner_address", owner, addr)
            method = "owner_address"
        elif addr:
            key = ("address", addr)
            method = "address"
        elif detail:
            key = ("address_detail", detail)
            method = "address_detail"
        else:
            key = None
            method = ""
        if key:
            groups.setdefault(key, []).append(i)
            methods[key] = method
        else:
            no_key.append(i)

    spatial_groups: list[list[int]] = []
    for idx in no_key:
        g = records[idx].geometry
        placed = False
        if g is not None:
            for sg in spatial_groups:
                if any(records[j].geometry is not None and g.equals(records[j].geometry) for j in sg):
                    sg.append(idx)
                    placed = True
                    break
        if not placed:
            spatial_groups.append([idx])

    grouped: list[tuple[list[int], str]] = [(inds, methods[key]) for key, inds in groups.items()]
    grouped += [(inds, "exact_point" if records[inds[0]].geometry is not None else "singleton") for inds in spatial_groups]
    grouped.sort(key=lambda item: min(item[0]))

    for n, (inds, grouping_method) in enumerate(grouped, 1):
        cid = f"{records[inds[0]].municipality_code}-HG{n:05d}"
        rr = [records[i] for i in inds]

        # Prefer explicit place name.  Otherwise use a consistent source 方書
        # label when available, then owner label, then the first record name.
        explicit_places = [r.place_name for r in rr if r.place_name]
        detail_labels = [_normalized_site_label(r.address_detail) for r in rr if _normalized_site_label(r.address_detail)]
        detail_keys = {norm_key(x) for x in detail_labels if x}
        owner_labels = [_owner_label(r.owner) for r in rr if _owner_label(r.owner)]
        owner_keys = {norm_key(x) for x in owner_labels if x}

        if explicit_places:
            cname = explicit_places[0]
        elif len(detail_keys) == 1 and detail_labels:
            cname = detail_labels[0]
        elif len(owner_keys) == 1 and owner_labels:
            cname = owner_labels[0]
        elif rr[0].owner:
            cname = _owner_label(rr[0].owner) or rr[0].name
        else:
            cname = rr[0].name

        coord_counts: dict[tuple[float, float], int] = {}
        for r in rr:
            if r.geometry is not None and getattr(r.geometry, "geom_type", "") == "Point":
                xy = (round(r.geometry.x, 9), round(r.geometry.y, 9))
                coord_counts[xy] = coord_counts.get(xy, 0) + 1

        for i in inds:
            r = records[i]
            r.complex_id = cid
            r.complex_name = cname
            r.complex_grouping_method = grouping_method
            r.complex_record_count = len(inds)
            if r.geometry is None:
                r.source_location_role = "missing"
            elif getattr(r.geometry, "geom_type", "") == "Point":
                xy = (round(r.geometry.x, 9), round(r.geometry.y, 9))
                r.source_location_role = "shared_complex_coordinate" if coord_counts.get(xy, 0) > 1 else "record_coordinate"
            else:
                r.source_location_role = "record_coordinate"
    return records
