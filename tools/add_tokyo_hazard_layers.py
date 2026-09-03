#!/usr/bin/env python3
"""
add_tokyo_hazard_layers_v1_1.py

東京都オープンデータ + 国土数値情報の災害データを自動取得し、
既存の PLATEAU Heritage GeoPackage をコピーした上で災害レイヤを追加する。

対象
----
1. 地震に関する地域危険度（東京都都市整備局）
2. 地震時における地域別延焼危険度（東京消防庁）
3. 震度分布 50m メッシュ（東京都総務局）
4. 液状化 250m メッシュ（東京都総務局）
5. 浸水予想区域図（東京都建設局）
6. 高潮浸水想定区域図（東京都港湾局）
7. 津波浸水分布 10m メッシュ（東京都総務局）
8. 国土数値情報の土砂災害系
   - A33 土砂災害警戒区域
   - A46 地すべり防止区域
   - A47 急傾斜地崩壊危険区域
   - A52 砂防指定地

重要な設計方針
--------------
* 元の GPKG は変更しない。--output にコピーしてから追加する。
* 東京都オープンデータは CKAN Action API (package_show) で
  現在のリソース URL を取得し、公開リソースを直接ダウンロードする。
  URL を固定しないので、カタログ側のリソース更新に追随しやすい。
* 50m震度は東京都公式のメッシュコード規則
  「3次メッシュを緯度・経度方向とも20分割」に従ってポリゴン化する。
* 250m液状化は標準地域メッシュの1/4細分（10桁）としてポリゴン化する。
* 浸水予想区域と津波は CSV に明示された緯度・経度を使って POINT とする。
  メッシュ面積を推定してポリゴンを作らない。
* SHP/GML/GeoJSON は原データの形状をそのまま利用する。
* 取得・変換に失敗したデータセットを無言で飛ばさない。
  デフォルトでは停止し、--continue-on-error 指定時のみ次へ進む。
* すべての取得結果を hazard_source_manifest 属性テーブルへ記録する。

依存
----
pip install requests pandas geopandas shapely pyogrio

実行例
------
python add_tokyo_hazard_layers.py \
  --input ./output/13_heritage_enriched.gpkg \
  --output ./output/13_heritage_hazards.gpkg \
  --cache ./.cache/hazard_sources

津波を区部だけに限定する場合:
python add_tokyo_hazard_layers.py \
  --input ./output/13_heritage_enriched.gpkg \
  --output ./output/13_heritage_hazards.gpkg \
  --cache ./.cache/hazard_sources \
  --tsunami-area 区部

特定データだけ:
python add_tokyo_hazard_layers.py \
  --input ./output/13_heritage_enriched.gpkg \
  --output ./output/13_heritage_hazards.gpkg \
  --datasets region_risk,fire_spread,seismic,liquefaction
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sqlite3
import sys
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import shapely
    from shapely.geometry import Point, box as scalar_box
except Exception as exc:
    raise SystemExit(
        "shapely is required. Install dependencies with:\n"
        "  python -m pip install requests pandas geopandas shapely pyogrio"
    ) from exc


CKAN_PACKAGE_SHOW = (
    "https://catalog.data.metro.tokyo.lg.jp/api/3/action/package_show"
)

TOKYO_DATASETS = {
    "region_risk": {
        "no": 1,
        "id": "t000008d0000000012",
        "title": "地震に関する地域危険度測定調査地域危険度一覧",
        "org": "東京都都市整備局",
    },
    "fire_spread": {
        "no": 2,
        "id": "t000017d0000000005",
        "title": "地震時における地域別延焼危険度測定",
        "org": "東京消防庁",
    },
    "seismic": {
        "no": 3,
        "id": "t000003d2000000390",
        "title": "震度分布・液状化（令和4年度首都直下型地震等による東京の被害想定結果）",
        "org": "東京都総務局",
    },
    "liquefaction": {
        "no": 4,
        "id": "t000003d2000000390",
        "title": "震度分布・液状化（令和4年度首都直下型地震等による東京の被害想定結果）",
        "org": "東京都総務局",
    },
    "inundation": {
        "no": 5,
        "id": "t000014d0000000029",
        "title": "浸水予想区域図",
        "org": "東京都建設局",
    },
    "storm_surge": {
        "no": 6,
        "id": "t000015d1700000007",
        "title": "高潮浸水想定区域図",
        "org": "東京都港湾局",
    },
    "tsunami": {
        "no": 7,
        "id": "t000003d2000000392",
        "title": "津波浸水分布（令和4年度首都直下型地震等による東京の被害想定結果）",
        "org": "東京都総務局",
    },
}

# 国土数値情報の公開ファイル。
# 国土数値情報の標準的なディレクトリ規則に従う。
KSJ_DATASETS = {
    "a33": {
        "no": 8,
        "title": "国土数値情報 土砂災害警戒区域",
        "file": "A33-25_13_GEOJSON.zip",
        "url": (
            "https://nlftp.mlit.go.jp/ksj/gml/data/"
            "A33/A33-25/A33-25_13_GEOJSON.zip"
        ),
        "page": (
            "https://nlftp.mlit.go.jp/ksj/gml/datalist/"
            "KsjTmplt-A33-2025.html"
        ),
        "base_layer": "hazard_sediment_warning_a33",
    },
    "a46": {
        "no": 8,
        "title": "国土数値情報 地すべり防止区域",
        "file": "A46-21_13_GML.zip",
        "url": (
            "https://nlftp.mlit.go.jp/ksj/gml/data/"
            "A46/A46-21/A46-21_13_GML.zip"
        ),
        "page": (
            "https://nlftp.mlit.go.jp/ksj/gml/datalist/"
            "KsjTmplt-A46-2021.html"
        ),
        "base_layer": "hazard_landslide_prevention_a46",
    },
    "a47": {
        "no": 8,
        "title": "国土数値情報 急傾斜地崩壊危険区域",
        "file": "A47-21_13_GML.zip",
        "url": (
            "https://nlftp.mlit.go.jp/ksj/gml/data/"
            "A47/A47-21/A47-21_13_GML.zip"
        ),
        "page": (
            "https://nlftp.mlit.go.jp/ksj/gml/datalist/"
            "KsjTmplt-A47-2021.html"
        ),
        "base_layer": "hazard_steep_slope_a47",
    },
    "a52": {
        "no": 8,
        "title": "国土数値情報 砂防指定地",
        "file": "A52-23_13_GML.zip",
        "url": (
            "https://nlftp.mlit.go.jp/ksj/gml/data/"
            "A52/A52-23/A52-23_13_GML.zip"
        ),
        "page": (
            "https://nlftp.mlit.go.jp/ksj/gml/datalist/"
            "KsjTmplt-A52-2023.html"
        ),
        "base_layer": "hazard_sabo_designated_a52",
    },
}

ALL_DATASET_KEYS = [
    "region_risk",
    "fire_spread",
    "seismic",
    "liquefaction",
    "inundation",
    "storm_surge",
    "tsunami",
    "ksj",
]


def parse_args():
    p = argparse.ArgumentParser(
        description="Automatically add Tokyo hazard datasets to a copy of a GPKG."
    )
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument(
        "--cache",
        type=Path,
        default=Path(".cache/hazard_sources"),
        help="Downloaded source cache.",
    )
    p.add_argument(
        "--datasets",
        default=",".join(ALL_DATASET_KEYS),
        help=(
            "Comma-separated: "
            + ",".join(ALL_DATASET_KEYS)
        ),
    )
    p.add_argument(
        "--tsunami-area",
        action="append",
        default=[],
        help=(
            "Restrict tsunami resources by area name substring. "
            "Repeatable. Example: --tsunami-area 区部"
        ),
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Redownload source files even if cached.",
    )
    p.add_argument(
        "--overwrite-output",
        action="store_true",
        help="Overwrite an existing --output file.",
    )
    p.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Record an error and continue with the remaining datasets.",
    )
    p.add_argument("--timeout", type=int, default=300)
    return p.parse_args()


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def build_session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
    )
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "PLATEAU-Heritage-Hazard-Ingest/1.0",
            "Accept": "*/*",
        }
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


def normalized(s) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[\s　_]+", "", s).lower()


def safe_name(s: str, limit=100) -> str:
    s = unicodedata.normalize("NFKC", str(s))
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")
    return (s or "resource")[:limit]


def layer_token(value: str, limit: int = 48) -> str:
    """Return a QGIS/GeoPackage-friendly, human-readable layer-name token.

    Japanese characters are intentionally preserved. Punctuation and spaces
    become underscores. Long values receive a stable hash suffix so two
    truncated scenario/area names cannot silently collide.
    """
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    token = re.sub(r"[^\w]+", "_", raw, flags=re.UNICODE).strip("_")
    token = token or "unspecified"
    if len(token) <= limit:
        return token
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return token[: max(1, limit - 9)].rstrip("_") + "_" + digest


def make_layer_name(prefix: str, *parts: str, max_len: int = 120) -> str:
    tokens = [layer_token(p) for p in parts if str(p or "").strip()]
    name = prefix if not tokens else prefix + "_" + "_".join(tokens)
    if len(name) <= max_len:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return name[: max_len - 9].rstrip("_") + "_" + digest


def resource_name(r: dict) -> str:
    return str(
        r.get("name")
        or r.get("description")
        or r.get("title")
        or r.get("id")
        or "resource"
    )


def resource_format(r: dict) -> str:
    return str(r.get("format") or "").strip().upper()


def package_show(session: requests.Session, dataset_id: str, timeout: int) -> dict:
    resp = session.get(
        CKAN_PACKAGE_SHOW,
        params={"id": dataset_id},
        timeout=timeout,
    )
    resp.raise_for_status()
    obj = resp.json()
    if not obj.get("success"):
        raise RuntimeError(f"CKAN package_show failed: {dataset_id}: {obj}")
    return obj["result"]


def infer_ext(r: dict) -> str:
    url = str(r.get("url") or "")
    suffix = Path(urlparse(url).path).suffix
    if suffix and len(suffix) <= 8:
        return suffix
    fmt = resource_format(r)
    return {
        "CSV": ".csv",
        "SHP": ".zip",
        "ZIP": ".zip",
        "GEOJSON": ".geojson",
        "JSON": ".json",
    }.get(fmt, ".bin")


def download_url(
    session: requests.Session,
    url: str,
    dest: Path,
    timeout: int,
    refresh=False,
) -> Path:
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    if part.exists():
        part.unlink()

    try:
        with session.get(url, timeout=timeout, stream=True) as r:
            r.raise_for_status()
            with part.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

        if part.stat().st_size == 0:
            raise RuntimeError(f"Downloaded zero-byte file: {url}")

        part.replace(dest)
        return dest

    except Exception as exc:
        part.unlink(missing_ok=True)
        raise RuntimeError(
            f"Download failed or timed out: {url}\n"
            f"destination={dest}\n"
            f"timeout={timeout}s\n"
            f"cause={exc}"
        ) from exc


def download_resource(
    session: requests.Session,
    r: dict,
    cache_dir: Path,
    timeout: int,
    refresh=False,
) -> Path:
    url = str(r.get("url") or "").strip()
    if not url:
        raise RuntimeError(f"Resource has no URL: {resource_name(r)}")

    rid = str(r.get("id") or "noid")
    ext = infer_ext(r)
    filename = f"{rid}_{safe_name(resource_name(r))}{ext}"
    return download_url(
        session,
        url,
        cache_dir / filename,
        timeout,
        refresh,
    )


def is_zip(path: Path) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except Exception:
        return False


def extract_zip(path: Path, refresh=False) -> Path:
    out = path.parent / (path.name + ".d")
    marker = out / ".extract_complete"

    if out.exists() and marker.exists() and not refresh:
        return out

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    with zipfile.ZipFile(path) as zf:
        # Zip Slip protection
        root = out.resolve()
        for member in zf.infolist():
            target = (out / member.filename).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError(f"Unsafe ZIP path: {member.filename}")
        zf.extractall(out)

    marker.write_text(now_iso(), encoding="utf-8")
    return out


def read_csv_auto(path: Path) -> pd.DataFrame:
    last = None
    for enc in ("utf-8-sig", "cp932", "shift_jis", "utf-8"):
        try:
            return pd.read_csv(
                path,
                dtype=str,
                encoding=enc,
                keep_default_na=False,
                low_memory=False,
            )
        except UnicodeDecodeError as e:
            last = e
    raise RuntimeError(f"Cannot decode CSV: {path}: {last}")


def col_by_candidates(df: pd.DataFrame, candidates=(), contains=()):
    normmap = {normalized(c): c for c in df.columns}

    for cand in candidates:
        n = normalized(cand)
        if n in normmap:
            return normmap[n]

    contains_norm = [normalized(x) for x in contains]
    for n, original in normmap.items():
        if all(token in n for token in contains_norm):
            return original

    return None


def numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("－", "-", regex=False)
        .str.strip(),
        errors="coerce",
    )


def valid_lonlat(lon: pd.Series, lat: pd.Series) -> pd.Series:
    # Covers Tokyo mainland and islands while rejecting obvious malformed values.
    return lon.between(120, 155) & lat.between(20, 50)


def ensure_wgs84(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise RuntimeError("Spatial source has no CRS; refusing to guess.")
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)
    return gdf


def add_source_fields(gdf: gpd.GeoDataFrame, dataset_id: str, res: dict):
    gdf = gdf.copy()
    gdf["source_dataset_id"] = dataset_id
    gdf["source_resource_id"] = str(res.get("id") or "")
    gdf["source_resource"] = resource_name(res)
    gdf["source_url"] = str(res.get("url") or "")
    return gdf


def native_shp_resource(
    path: Path,
    dataset_id: str,
    res: dict,
    refresh=False,
) -> gpd.GeoDataFrame:
    if not is_zip(path):
        raise RuntimeError(
            f"SHP resource is not a ZIP archive: {path}. "
            "Shapefile sidecars are required."
        )

    root = extract_zip(path, refresh)
    shp_files = sorted(root.rglob("*.shp"))
    if not shp_files:
        raise RuntimeError(f"No .shp found in {path}")

    parts = []
    for shp in shp_files:
        gdf = gpd.read_file(shp, engine="pyogrio")
        if gdf.empty:
            continue
        gdf = ensure_wgs84(gdf)
        gdf["source_member"] = str(shp.relative_to(root))
        parts.append(gdf)

    if not parts:
        raise RuntimeError(f"No spatial features in {path}")

    combined = gpd.GeoDataFrame(
        pd.concat(parts, ignore_index=True, sort=False),
        geometry="geometry",
        crs="EPSG:4326",
    )
    return add_source_fields(combined, dataset_id, res)


def spatial_layer_names(gpkg: Path) -> set[str]:
    if not gpkg.exists():
        return set()
    try:
        arr = pyogrio.list_layers(gpkg)
        return {str(row[0]) for row in arr}
    except Exception:
        return set()


def write_spatial(
    gpkg: Path,
    layer: str,
    gdf: gpd.GeoDataFrame,
    written_layers: set[str],
    append=False,
):
    if gdf.empty:
        raise RuntimeError(f"Attempt to write empty layer: {layer}")

    if not append and layer in written_layers:
        raise RuntimeError(
            f"Layer already exists in copied input GPKG: {layer}. "
            "Use a clean input GPKG or a new output."
        )

    pyogrio.write_dataframe(
        gdf,
        gpkg,
        layer=layer,
        driver="GPKG",
        append=append,
    )
    written_layers.add(layer)


def append_stream(
    gpkg: Path,
    layer: str,
    gdf: gpd.GeoDataFrame,
    stream_state: dict[str, bool],
    written_layers: set[str],
):
    append = stream_state.get(layer, False)
    write_spatial(
        gpkg,
        layer,
        gdf,
        written_layers,
        append=append,
    )
    stream_state[layer] = True


def manifest_row(
    dataset_no,
    dataset_key,
    title,
    source_org,
    acquisition,
    source_url="",
    resource="",
    layer="",
    geometry_type="",
    row_count=0,
    status="ok",
    note="",
):
    return {
        "dataset_no": dataset_no,
        "dataset_key": dataset_key,
        "title": title,
        "source_org": source_org,
        "acquisition": acquisition,
        "source_url": source_url,
        "resource": resource,
        "layer": layer,
        "geometry_type": geometry_type,
        "row_count": int(row_count),
        "status": status,
        "note": note,
        "ingested_at": now_iso(),
    }


def register_attribute_table(con: sqlite3.Connection, name: str, rows: int):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    con.execute("DELETE FROM gpkg_contents WHERE table_name=?", (name,))
    con.execute(
        """
        INSERT INTO gpkg_contents
        (table_name,data_type,identifier,description,last_change,
         min_x,min_y,max_x,max_y,srs_id)
        VALUES (?, 'attributes', ?, ?, ?, NULL,NULL,NULL,NULL,NULL)
        """,
        (
            name,
            name,
            f"Tokyo hazard source manifest ({rows} rows)",
            now,
        ),
    )


def write_manifest(gpkg: Path, rows: list[dict]):
    df = pd.DataFrame(rows)
    with sqlite3.connect(gpkg) as con:
        df.to_sql(
            "hazard_source_manifest",
            con,
            if_exists="replace",
            index=False,
        )
        register_attribute_table(con, "hazard_source_manifest", len(df))
        con.commit()


# ----------------------------------------------------------------------
# Standard regional mesh decoding
# ----------------------------------------------------------------------

def clean_mesh_code_series(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"\D", "", regex=True)
    )


def third_mesh_base_arrays(code8: pd.Series):
    """
    Standard Japanese 3rd regional mesh (8 digits):
      PP QQ R S T U
    lat = PP * 2/3 deg + R * 5' + T * 30"
    lon = 100 + QQ deg + S * 7.5' + U * 45"
    """
    s = code8.astype(str)
    p = pd.to_numeric(s.str[0:2], errors="coerce")
    q = pd.to_numeric(s.str[2:4], errors="coerce")
    r = pd.to_numeric(s.str[4:5], errors="coerce")
    ss = pd.to_numeric(s.str[5:6], errors="coerce")
    t = pd.to_numeric(s.str[6:7], errors="coerce")
    u = pd.to_numeric(s.str[7:8], errors="coerce")

    lat0 = p * (2.0 / 3.0) + r * (5.0 / 60.0) + t * (30.0 / 3600.0)
    lon0 = 100.0 + q + ss * (7.5 / 60.0) + u * (45.0 / 3600.0)
    return lat0, lon0


def vector_boxes(minx, miny, maxx, maxy):
    arrays = [
        np.asarray(x, dtype=float)
        for x in (minx, miny, maxx, maxy)
    ]
    try:
        # Shapely >= 2
        return shapely.box(*arrays)
    except Exception:
        return [
            scalar_box(a, b, c, d)
            for a, b, c, d in zip(*arrays)
        ]


def make_50m_geometries(codes: pd.Series):
    codes = clean_mesh_code_series(codes)
    valid = codes.str.fullmatch(r"\d{12}") == True

    c = codes[valid]
    base = c.str[:8]
    lat0, lon0 = third_mesh_base_arrays(base)

    ilat = pd.to_numeric(c.str[8:10], errors="coerce")
    ilon = pd.to_numeric(c.str[10:12], errors="coerce")

    valid2 = (
        ilat.between(0, 19)
        & ilon.between(0, 19)
        & lat0.notna()
        & lon0.notna()
    )
    c = c[valid2]
    lat0 = lat0[valid2]
    lon0 = lon0[valid2]
    ilat = ilat[valid2]
    ilon = ilon[valid2]

    dlat = (30.0 / 3600.0) / 20.0
    dlon = (45.0 / 3600.0) / 20.0

    miny = lat0 + ilat * dlat
    minx = lon0 + ilon * dlon
    geom = vector_boxes(minx, miny, minx + dlon, miny + dlat)

    return c.index, c, geom


def make_250m_geometries(codes: pd.Series):
    """
    Standard 10-digit 1/4 regional mesh.
    Digits 9 and 10 recursively quarter the 3rd mesh:
      1=SW, 2=SE, 3=NW, 4=NE
    """
    codes = clean_mesh_code_series(codes)
    valid = codes.str.fullmatch(r"\d{10}") == True
    c = codes[valid]

    base = c.str[:8]
    lat0, lon0 = third_mesh_base_arrays(base)
    dlat = pd.Series(
        np.full(len(c), 30.0 / 3600.0),
        index=c.index,
    )
    dlon = pd.Series(
        np.full(len(c), 45.0 / 3600.0),
        index=c.index,
    )

    okay = lat0.notna() & lon0.notna()

    for pos in (8, 9):
        digit = pd.to_numeric(c.str[pos : pos + 1], errors="coerce")
        okay &= digit.between(1, 4)
        dlat = dlat / 2.0
        dlon = dlon / 2.0
        lat0 = lat0 + digit.isin([3, 4]).astype(float) * dlat
        lon0 = lon0 + digit.isin([2, 4]).astype(float) * dlon

    c = c[okay]
    lat0 = lat0[okay]
    lon0 = lon0[okay]
    dlat = dlat[okay]
    dlon = dlon[okay]

    geom = vector_boxes(lon0, lat0, lon0 + dlon, lat0 + dlat)
    return c.index, c, geom


# ----------------------------------------------------------------------
# Dataset 1: 地域危険度
# ----------------------------------------------------------------------

def ingest_region_risk(ctx):
    info = TOKYO_DATASETS["region_risk"]
    pkg = package_show(ctx.session, info["id"], ctx.timeout)

    resources = [
        r for r in pkg.get("resources", [])
        if resource_format(r) == "SHP"
    ]
    if not resources:
        raise RuntimeError("No SHP resource found for regional risk.")

    parts = []
    selected = []
    for r in resources:
        p = download_resource(
            ctx.session,
            r,
            ctx.cache / "tokyo" / info["id"],
            ctx.timeout,
            ctx.refresh,
        )
        gdf = native_shp_resource(
            p,
            info["id"],
            r,
            ctx.refresh,
        )
        parts.append(gdf)
        selected.append(r)

    combined = gpd.GeoDataFrame(
        pd.concat(parts, ignore_index=True, sort=False),
        geometry="geometry",
        crs="EPSG:4326",
    )
    layer = "hazard_region_risk"
    write_spatial(
        ctx.output,
        layer,
        combined,
        ctx.written_layers,
    )

    for r in selected:
        ctx.manifest.append(
            manifest_row(
                1,
                "region_risk",
                info["title"],
                info["org"],
                "CKAN metadata API + direct SHP download",
                str(r.get("url") or ""),
                resource_name(r),
                layer,
                "Polygon",
                len(combined),
            )
        )


# ----------------------------------------------------------------------
# Dataset 2: 延焼危険度
# ----------------------------------------------------------------------

def ingest_fire_spread(ctx):
    info = TOKYO_DATASETS["fire_spread"]
    pkg = package_show(ctx.session, info["id"], ctx.timeout)

    resources = [
        r for r in pkg.get("resources", [])
        if resource_format(r) == "SHP"
    ]
    if not resources:
        raise RuntimeError("No SHP resources found for fire-spread risk.")

    found_town = False
    found_mesh = False

    for r in resources:
        n = normalized(resource_name(r))
        if "250m" in n or "250ｍ" in n or "メッシュ" in n:
            layer = "hazard_fire_spread_mesh250"
            found_mesh = True
        elif "町丁目" in resource_name(r) or "町丁" in resource_name(r):
            layer = "hazard_fire_spread_town"
            found_town = True
        else:
            # Do not silently guess.
            raise RuntimeError(
                "Cannot classify fire-spread SHP resource as town or 250m mesh: "
                + resource_name(r)
            )

        p = download_resource(
            ctx.session,
            r,
            ctx.cache / "tokyo" / info["id"],
            ctx.timeout,
            ctx.refresh,
        )
        gdf = native_shp_resource(
            p,
            info["id"],
            r,
            ctx.refresh,
        )
        write_spatial(
            ctx.output,
            layer,
            gdf,
            ctx.written_layers,
        )
        ctx.manifest.append(
            manifest_row(
                2,
                "fire_spread",
                info["title"],
                info["org"],
                "CKAN metadata API + direct SHP download",
                str(r.get("url") or ""),
                resource_name(r),
                layer,
                str(gdf.geom_type.mode().iloc[0]),
                len(gdf),
            )
        )

    if not found_town or not found_mesh:
        raise RuntimeError(
            f"Expected both town and 250m fire-spread SHP resources; "
            f"town={found_town}, mesh={found_mesh}"
        )


# ----------------------------------------------------------------------
# Datasets 3/4: 震度・液状化
# ----------------------------------------------------------------------

def seismic_scenario(name: str) -> str:
    s = str(name)
    s = re.sub(r"CSV$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^計測震度50[mｍ]メッシュ別[_＿]?", "", s)
    return s.strip("_＿ ")


def liquefaction_scenario(name: str) -> str:
    s = str(name)
    s = re.sub(r"CSV$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^液状化データ250[mｍ]メッシュ別[_＿]?", "", s)
    return s.strip("_＿ ")


def build_seismic_gdf(df: pd.DataFrame, scenario: str, r: dict):
    code_col = col_by_candidates(
        df,
        candidates=("50mメッシュコード", "50ｍメッシュコード"),
        contains=("50m", "メッシュコード"),
    )
    intensity_col = col_by_candidates(
        df,
        candidates=("計測震度",),
        contains=("計測震度",),
    )
    if not code_col or not intensity_col:
        raise RuntimeError(
            "Seismic CSV lacks expected 50m mesh code or intensity field: "
            f"columns={list(df.columns)}"
        )

    idx, codes, geom = make_50m_geometries(df[code_col])
    if len(idx) == 0:
        raise RuntimeError("No valid 12-digit 50m mesh codes.")

    out = pd.DataFrame(
        {
            "mesh_code": codes.values,
            "scenario": scenario,
            "seismic_intensity": numeric(df.loc[idx, intensity_col]).values,
            "source_resource_id": str(r.get("id") or ""),
            "source_resource": resource_name(r),
            "source_url": str(r.get("url") or ""),
        }
    )
    return gpd.GeoDataFrame(out, geometry=geom, crs="EPSG:4326")


def build_liquefaction_gdf(df: pd.DataFrame, scenario: str, r: dict):
    code_col = col_by_candidates(
        df,
        candidates=("250mメッシュコード", "250ｍメッシュコード"),
        contains=("250m", "メッシュコード"),
    )
    if not code_col:
        raise RuntimeError(
            "Liquefaction CSV lacks 250m mesh code: "
            f"columns={list(df.columns)}"
        )

    pl_col = col_by_candidates(
        df,
        candidates=("Plcorrecte", "PLcorrected", "PL値"),
        contains=("pl",),
    )
    sub_col = col_by_candidates(
        df,
        candidates=("Scorrected", "沈下量（m）", "沈下量(m)"),
        contains=("沈下量",),
    )
    lat_col = col_by_candidates(
        df,
        candidates=("Lat（250mメッシュ中心緯度）", "Lat(250mメッシュ中心緯度)", "緯度"),
        contains=("lat",),
    )
    lon_col = col_by_candidates(
        df,
        candidates=("Lon（250mメッシュ中心経度）", "Lon(250mメッシュ中心経度)", "経度"),
        contains=("lon",),
    )

    idx, codes, geom = make_250m_geometries(df[code_col])
    if len(idx) == 0:
        raise RuntimeError("No valid 10-digit 250m mesh codes.")

    out = pd.DataFrame(
        {
            "mesh_code": codes.values,
            "scenario": scenario,
            "liquefaction_pl": (
                numeric(df.loc[idx, pl_col]).values
                if pl_col else np.nan
            ),
            "subsidence_m": (
                numeric(df.loc[idx, sub_col]).values
                if sub_col else np.nan
            ),
            "center_lat": (
                numeric(df.loc[idx, lat_col]).values
                if lat_col else np.nan
            ),
            "center_lon": (
                numeric(df.loc[idx, lon_col]).values
                if lon_col else np.nan
            ),
            "source_resource_id": str(r.get("id") or ""),
            "source_resource": resource_name(r),
            "source_url": str(r.get("url") or ""),
        }
    )
    return gpd.GeoDataFrame(out, geometry=geom, crs="EPSG:4326")


def ingest_seismic_or_liquefaction(ctx, key: str):
    info = TOKYO_DATASETS[key]
    pkg = package_show(ctx.session, info["id"], ctx.timeout)

    resources = [
        r for r in pkg.get("resources", [])
        if resource_format(r) == "CSV"
    ]

    # Classify from actual CSV headers.
    # v1.5 policy:
    #   seismic      -> one physical GPKG layer per earthquake scenario
    #   liquefaction -> existing long-form layer retained for now
    if key == "liquefaction":
        aggregate_layer = "hazard_liquefaction_250m"

    accepted = 0
    skipped = []

    for i, r in enumerate(resources, 1):
        print(
            f"    {key} candidate [{i}/{len(resources)}] "
            f"{resource_name(r)}",
            flush=True,
        )
        p = download_resource(
            ctx.session,
            r,
            ctx.cache / "tokyo" / info["id"],
            ctx.timeout,
            ctx.refresh,
        )
        print(
            f"      source ready: {p.name} "
            f"({p.stat().st_size / (1024*1024):.1f} MiB)",
            flush=True,
        )
        df = read_csv_auto(p)

        mesh50_col = col_by_candidates(
            df,
            candidates=("50mメッシュコード", "50ｍメッシュコード"),
            contains=("50m", "メッシュコード"),
        )
        intensity_col = col_by_candidates(
            df,
            candidates=("計測震度",),
            contains=("計測震度",),
        )
        mesh250_col = col_by_candidates(
            df,
            candidates=("250mメッシュコード", "250ｍメッシュコード"),
            contains=("250m", "メッシュコード"),
        )
        pl_col = col_by_candidates(
            df,
            candidates=("Plcorrecte", "PLcorrected", "PL値"),
            contains=("pl",),
        )
        subsidence_col = col_by_candidates(
            df,
            candidates=("Scorrected", "沈下量（m）", "沈下量(m)"),
            contains=("沈下量",),
        )

        is_seismic = bool(mesh50_col and intensity_col)
        is_liquefaction = bool(mesh250_col and (pl_col or subsidence_col))

        if key == "seismic":
            if not is_seismic:
                skipped.append(
                    f"{resource_name(r)}: columns={list(df.columns)}"
                )
                continue
            scenario = seismic_scenario(resource_name(r))
            layer = make_layer_name("hazard_seismic_50m", scenario)
            gdf = build_seismic_gdf(df, scenario, r)
            no = 3
            geom_type = "Polygon (official 50m mesh-code rule)"
            write_spatial(
                ctx.output, layer, gdf, ctx.written_layers
            )
        else:
            if not is_liquefaction:
                skipped.append(
                    f"{resource_name(r)}: columns={list(df.columns)}"
                )
                continue
            scenario = liquefaction_scenario(resource_name(r))
            layer = aggregate_layer
            gdf = build_liquefaction_gdf(df, scenario, r)
            no = 4
            geom_type = "Polygon (standard 250m regional mesh)"
            append_stream(
                ctx.output,
                layer,
                gdf,
                ctx.stream_state,
                ctx.written_layers,
            )

        accepted += 1
        ctx.manifest.append(
            manifest_row(
                no, key, info["title"], info["org"],
                "CKAN metadata API + direct CSV download; header-classified",
                str(r.get("url") or ""), resource_name(r), layer,
                geom_type, len(gdf),
                note=(
                    f"scenario={scenario}; "
                    + (
                        "stored as an independent scenario layer"
                        if key == "seismic"
                        else "scenario retained as an attribute in the long-form layer"
                    )
                ),
            )
        )

    if accepted == 0:
        preview = "\n".join("  - " + s for s in skipped[:10])
        raise RuntimeError(
            f"No CSV resource with the expected actual columns was found "
            f"for {key}.\nChecked resources:\n{preview}"
        )

    print(
        f"    accepted {accepted} {key} CSV resource(s); "
        f"skipped {len(skipped)} non-{key} CSV resource(s)"
    )


# ----------------------------------------------------------------------
# Dataset 5: 浸水予想区域図
# ----------------------------------------------------------------------

def inundation_area_name(name: str) -> str:
    s = str(name)
    s = re.sub(r"CSV$", "", s, flags=re.IGNORECASE)
    for token in (
        "浸水予想区域図（改定）",
        "浸水予想区域図(改定)",
        "浸水予想区域図",
    ):
        if token in s:
            return s.split(token, 1)[0].strip(" _＿")
    return s


def build_inundation_points(df: pd.DataFrame, r: dict):
    lon_col = col_by_candidates(
        df,
        candidates=("経度", "経度（度）", "経度(度)"),
        contains=("経度",),
    )
    lat_col = col_by_candidates(
        df,
        candidates=("緯度", "緯度（度）", "緯度(度)"),
        contains=("緯度",),
    )
    depth_col = col_by_candidates(
        df,
        candidates=("浸水深",),
        contains=("浸水深",),
    )
    ground_col = col_by_candidates(
        df,
        candidates=("地盤高",),
        contains=("地盤高",),
    )
    sheet_col = col_by_candidates(
        df,
        candidates=("図郭NO", "図郭No", "図郭番号"),
        contains=("図郭",),
    )

    if not lon_col or not lat_col or not depth_col:
        raise RuntimeError(
            "Inundation CSV lacks longitude/latitude/inundation-depth fields: "
            f"columns={list(df.columns)}"
        )

    lon = numeric(df[lon_col])
    lat = numeric(df[lat_col])
    mask = valid_lonlat(lon, lat) & lon.notna() & lat.notna()

    if not mask.any():
        raise RuntimeError("No valid lon/lat rows in inundation CSV.")

    out = pd.DataFrame(
        {
            "area": inundation_area_name(resource_name(r)),
            "sheet_no": (
                df.loc[mask, sheet_col].astype(str).values
                if sheet_col else ""
            ),
            "inundation_depth_m": numeric(df.loc[mask, depth_col]).values,
            "ground_elevation_m": (
                numeric(df.loc[mask, ground_col]).values
                if ground_col else np.nan
            ),
            "lon": lon[mask].values,
            "lat": lat[mask].values,
            "source_resource_id": str(r.get("id") or ""),
            "source_resource": resource_name(r),
            "source_url": str(r.get("url") or ""),
        }
    )
    geom = gpd.points_from_xy(out["lon"], out["lat"], crs="EPSG:4326")
    return gpd.GeoDataFrame(out, geometry=geom, crs="EPSG:4326")


def ingest_inundation(ctx):
    info = TOKYO_DATASETS["inundation"]
    pkg = package_show(ctx.session, info["id"], ctx.timeout)

    all_csv = [
        r for r in pkg.get("resources", [])
        if resource_format(r) == "CSV"
    ]

    resources = [
        r for r in all_csv
        if "/kensetsu/R3/" in str(r.get("url") or "")
        and "_zukaku" not in str(r.get("url") or "").lower()
    ]

    if len(resources) != 14:
        details = "\n".join(
            f"  - {resource_name(r)} :: {r.get('url')}"
            for r in all_csv
        )
        raise RuntimeError(
            "Expected exactly 14 canonical R3 inundation CSV resources "
            f"but found {len(resources)}.\n"
            "Current CKAN CSV resources:\n" + details
        )

    print(
        f"    selected {len(resources)} canonical R3 inundation CSVs "
        f"(excluded {len(all_csv) - len(resources)} non-R3/duplicate CSVs)"
    )

    for i, r in enumerate(resources, 1):
        area = inundation_area_name(resource_name(r))
        layer = make_layer_name("hazard_inundation", area)
        print(
            f"    inundation [{i}/{len(resources)}] {area} -> {layer}",
            flush=True,
        )
        p = download_resource(
            ctx.session, r, ctx.cache / "tokyo" / info["id"],
            ctx.timeout, ctx.refresh,
        )
        df = read_csv_auto(p)

        try:
            gdf = build_inundation_points(df, r)
        except Exception as exc:
            lon_col = col_by_candidates(
                df, candidates=("経度", "経度（度）", "経度(度)"),
                contains=("経度",),
            )
            lat_col = col_by_candidates(
                df, candidates=("緯度", "緯度（度）", "緯度(度)"),
                contains=("緯度",),
            )
            sample = {}
            if lon_col:
                sample["lon"] = df[lon_col].head(3).astype(str).tolist()
            if lat_col:
                sample["lat"] = df[lat_col].head(3).astype(str).tolist()
            raise RuntimeError(
                f"Inundation source failed: {resource_name(r)}\n"
                f"url={r.get('url')}\n"
                f"columns={list(df.columns)}\n"
                f"coordinate_sample={sample}\n"
                f"cause={exc}"
            ) from exc

        write_spatial(ctx.output, layer, gdf, ctx.written_layers)
        ctx.manifest.append(
            manifest_row(
                5, "inundation", info["title"], info["org"],
                "CKAN metadata API + canonical R3 direct CSV download",
                str(r.get("url") or ""), resource_name(r), layer,
                "Point (explicit CSV longitude/latitude)", len(gdf),
                note=(
                    f"basin/area={area}; stored as an independent flood "
                    "scenario layer. Later R4 split Akigawa files are excluded "
                    "to avoid duplicate coverage. No polygon is inferred; each "
                    "feature is the exact published coordinate."
                ),
            )
        )


# ----------------------------------------------------------------------
# Dataset 6: 高潮
# ----------------------------------------------------------------------

def storm_surge_layer(name: str) -> str | None:
    n = normalized(name)
    if "shp" not in n and "shape" not in n:
        # The CKAN format field is checked separately; this protects
        # ambiguous resources only.
        pass
    if "家屋倒壊" in name:
        return "hazard_storm_surge_house_collapse"
    if "浸水継続時間" in name:
        return "hazard_storm_surge_duration"
    if "浸水深" in name:
        return "hazard_storm_surge_depth"
    return None


def ingest_storm_surge(ctx):
    info = TOKYO_DATASETS["storm_surge"]
    pkg = package_show(ctx.session, info["id"], ctx.timeout)

    all_resources = pkg.get("resources", [])

    # Tokyo CKAN describes the downloadable Shape-format archives as
    # resource format "ZIP", not "SHP".  Select ZIP resources whose names
    # explicitly say "(shpデータ)" so the parallel XLS ZIPs are excluded.
    resources = [
        r for r in all_resources
        if resource_format(r) == "ZIP"
        and (
            "shpデータ" in normalized(resource_name(r))
            or "shape形式" in normalized(str(r.get("description") or ""))
        )
    ]

    if not resources:
        details = "\n".join(
            f"  - format={resource_format(r)!r} "
            f"name={resource_name(r)!r} url={r.get('url')}"
            for r in all_resources
        )
        raise RuntimeError(
            "No ZIP resources containing storm-surge Shape data were found.\n"
            "Current CKAN resources:\n"
            + details
        )

    print(
        f"    selected {len(resources)} Shape-data ZIP resource(s) "
        f"from {len(all_resources)} CKAN resources"
    )

    expected = set()

    for i, r in enumerate(resources, 1):
        print(
            f"    storm_surge [{i}/{len(resources)}] {resource_name(r)}",
            flush=True,
        )

        layer = storm_surge_layer(resource_name(r))
        if not layer:
            raise RuntimeError(
                "Cannot classify storm-surge Shape ZIP resource: "
                + resource_name(r)
            )
        if layer in expected:
            raise RuntimeError(
                f"Duplicate storm-surge semantic layer detected: {layer}\n"
                f"resource={resource_name(r)}"
            )
        expected.add(layer)

        p = download_resource(
            ctx.session,
            r,
            ctx.cache / "tokyo" / info["id"],
            ctx.timeout,
            ctx.refresh,
        )
        print(
            f"      source ready: {p.name} "
            f"({p.stat().st_size / (1024*1024):.1f} MiB)",
            flush=True,
        )

        gdf = native_shp_resource(
            p,
            info["id"],
            r,
            ctx.refresh,
        )
        write_spatial(
            ctx.output,
            layer,
            gdf,
            ctx.written_layers,
        )

        note = (
            "Tokyo CKAN resource format is ZIP; the archive contains "
            "Shape-format files. The parallel XLS ZIP resource is excluded."
        )
        if layer == "hazard_storm_surge_duration":
            note += (
                " In the official source, TermMin=999999 means "
                "'one week or longer' and is a placeholder, not a "
                "calculated duration."
            )

        ctx.manifest.append(
            manifest_row(
                6,
                "storm_surge",
                info["title"],
                info["org"],
                "CKAN metadata API + direct Shape-data ZIP download",
                str(r.get("url") or ""),
                resource_name(r),
                layer,
                str(gdf.geom_type.mode().iloc[0]),
                len(gdf),
                note=note,
            )
        )

    needed = {
        "hazard_storm_surge_depth",
        "hazard_storm_surge_duration",
        "hazard_storm_surge_house_collapse",
    }
    if expected != needed:
        raise RuntimeError(
            "Storm-surge Shape ZIP set incomplete or unexpected. "
            f"expected={sorted(needed)}, found={sorted(expected)}, "
            f"missing={sorted(needed - expected)}, "
            f"extra={sorted(expected - needed)}"
        )


# ----------------------------------------------------------------------
# Dataset 7: 津波
# ----------------------------------------------------------------------

def tsunami_parts(name: str):
    s = re.sub(r"CSV$", "", str(name), flags=re.IGNORECASE)
    if "_" in s:
        area = s.split("_", 1)[0]
    else:
        area = ""

    if "到達時間" in s:
        metric = "arrival"
        metric_label = "到達時間"
    elif "津波高" in s:
        metric = "height"
        metric_label = "津波高"
    elif "浸水深" in s:
        metric = "depth"
        metric_label = "浸水深"
    else:
        raise RuntimeError(f"Unknown tsunami metric resource: {name}")

    scenario = s
    if area and scenario.startswith(area + "_"):
        scenario = scenario[len(area) + 1 :]
    scenario = scenario.replace("_" + metric_label, "")
    scenario = scenario.replace(metric_label, "")
    scenario = scenario.strip("_ ")

    return area, scenario, metric


def tsunami_xy_cols(df):
    lon_col = col_by_candidates(
        df,
        candidates=("経度（度）", "経度(度)", "経度"),
        contains=("経度",),
    )
    lat_col = col_by_candidates(
        df,
        candidates=("緯度（度）", "緯度(度)", "緯度"),
        contains=("緯度",),
    )
    x_col = col_by_candidates(
        df,
        candidates=("X（m）", "X(m)", "X"),
    )
    y_col = col_by_candidates(
        df,
        candidates=("Y（m）", "Y(m)", "Y"),
    )
    return lon_col, lat_col, x_col, y_col


def build_tsunami_points(df: pd.DataFrame, r: dict):
    area, scenario, metric = tsunami_parts(resource_name(r))
    lon_col, lat_col, x_col, y_col = tsunami_xy_cols(df)

    if not lon_col or not lat_col:
        raise RuntimeError(
            "Tsunami CSV lacks explicit longitude/latitude: "
            f"columns={list(df.columns)}"
        )

    lon = numeric(df[lon_col])
    lat = numeric(df[lat_col])
    mask = valid_lonlat(lon, lat) & lon.notna() & lat.notna()
    if not mask.any():
        raise RuntimeError("No valid lon/lat rows in tsunami CSV.")

    common = {
        "area": area,
        "scenario": scenario,
        "x_m": numeric(df.loc[mask, x_col]).values if x_col else np.nan,
        "y_m": numeric(df.loc[mask, y_col]).values if y_col else np.nan,
        "lon": lon[mask].values,
        "lat": lat[mask].values,
        "source_resource_id": str(r.get("id") or ""),
        "source_resource": resource_name(r),
        "source_url": str(r.get("url") or ""),
    }

    if metric == "arrival":
        c1 = col_by_candidates(
            df,
            candidates=("到達時間（1cm）（秒）", "到達時間(1cm)(秒)"),
            contains=("到達時間", "1cm"),
        )
        c30 = col_by_candidates(
            df,
            candidates=("到達時間（30cm）（秒）", "到達時間(30cm)(秒)"),
            contains=("到達時間", "30cm"),
        )
        c100 = col_by_candidates(
            df,
            candidates=("到達時間（1m）（秒）", "到達時間(1m)(秒)"),
            contains=("到達時間", "1m"),
        )
        cmax = col_by_candidates(
            df,
            candidates=("到達時間（最高水位）（秒）", "到達時間(最高水位)(秒)"),
            contains=("到達時間", "最高水位"),
        )
        common.update(
            {
                "arrival_1cm_s": (
                    numeric(df.loc[mask, c1]).values if c1 else np.nan
                ),
                "arrival_30cm_s": (
                    numeric(df.loc[mask, c30]).values if c30 else np.nan
                ),
                "arrival_1m_s": (
                    numeric(df.loc[mask, c100]).values if c100 else np.nan
                ),
                "arrival_maxlevel_s": (
                    numeric(df.loc[mask, cmax]).values if cmax else np.nan
                ),
            }
        )
    elif metric == "height":
        col = col_by_candidates(
            df,
            candidates=("津波高（最大）（m）", "津波高(最大)(m)"),
            contains=("津波高", "最大"),
        )
        if not col:
            raise RuntimeError("Tsunami-height CSV lacks maximum-height field.")
        common["tsunami_height_max_m"] = numeric(df.loc[mask, col]).values

    else:
        col = col_by_candidates(
            df,
            candidates=("浸水深（最大）（m）", "浸水深(最大)(m)"),
            contains=("浸水深", "最大"),
        )
        if not col:
            raise RuntimeError("Tsunami-depth CSV lacks maximum-depth field.")
        common["inundation_depth_max_m"] = numeric(df.loc[mask, col]).values

    out = pd.DataFrame(common)
    geom = gpd.points_from_xy(out["lon"], out["lat"], crs="EPSG:4326")
    return area, scenario, metric, gpd.GeoDataFrame(
        out, geometry=geom, crs="EPSG:4326"
    )


def ingest_tsunami(ctx):
    info = TOKYO_DATASETS["tsunami"]
    pkg = package_show(ctx.session, info["id"], ctx.timeout)
    resources = [
        r for r in pkg.get("resources", [])
        if resource_format(r) == "CSV"
    ]
    if ctx.tsunami_areas:
        resources = [
            r for r in resources
            if any(area in resource_name(r) for area in ctx.tsunami_areas)
        ]

    if not resources:
        raise RuntimeError("No tsunami CSV resources selected.")

    for i, r in enumerate(resources, 1):
        print(
            f"    tsunami [{i}/{len(resources)}] {resource_name(r)}",
            flush=True,
        )
        p = download_resource(
            ctx.session, r, ctx.cache / "tokyo" / info["id"],
            ctx.timeout, ctx.refresh,
        )
        df = read_csv_auto(p)
        area, scenario, metric, gdf = build_tsunami_points(df, r)

        layer = make_layer_name(
            f"hazard_tsunami_{metric}",
            area or "all",
            scenario or "default",
        )
        print(f"      -> {layer}", flush=True)
        write_spatial(ctx.output, layer, gdf, ctx.written_layers)

        ctx.manifest.append(
            manifest_row(
                7, "tsunami", info["title"], info["org"],
                "CKAN metadata API + direct CSV download",
                str(r.get("url") or ""), resource_name(r), layer,
                "Point (explicit CSV longitude/latitude)", len(gdf),
                note=(
                    f"area={area}; scenario={scenario}; metric={metric}; "
                    "stored as an independent tsunami scenario layer. "
                    "Published data are 10m mesh values, but this script does "
                    "not infer cell polygons; it stores published coordinate "
                    "points exactly."
                ),
            )
        )


# ----------------------------------------------------------------------
# Dataset 8: 国土数値情報
# ----------------------------------------------------------------------

def geometry_family(gdf: gpd.GeoDataFrame) -> str:
    kinds = set(gdf.geom_type.dropna().astype(str))
    if not kinds:
        return "unknown"
    if all("Polygon" in x for x in kinds):
        return "polygon"
    if all(("LineString" in x or "Curve" in x) for x in kinds):
        return "line"
    if all("Point" in x for x in kinds):
        return "point"
    return "mixed"


def read_ksj_archive(
    archive: Path,
    source_file: str,
    refresh=False,
) -> dict[str, gpd.GeoDataFrame]:
    root = extract_zip(archive, refresh)
    groups: dict[str, list[gpd.GeoDataFrame]] = {}

    # Prefer GeoJSON/Shapefile when supplied.
    spatial_files = sorted(root.rglob("*.geojson"))
    if not spatial_files:
        spatial_files = sorted(root.rglob("*.shp"))

    if spatial_files:
        for f in spatial_files:
            gdf = gpd.read_file(f, engine="pyogrio")
            if gdf.empty:
                continue
            gdf = ensure_wgs84(gdf)
            gdf["source_member"] = str(f.relative_to(root))
            gdf["source_file"] = source_file
            fam = geometry_family(gdf)
            groups.setdefault(fam, []).append(gdf)
    else:
        # GML archives can include several feature types and metadata XMLs.
        candidates = sorted(root.rglob("*.gml")) + sorted(root.rglob("*.xml"))
        seen = set()

        for f in candidates:
            if f in seen:
                continue
            seen.add(f)

            try:
                layers = pyogrio.list_layers(f)
            except Exception:
                continue

            for layer_name, geom_type in layers:
                if geom_type is None:
                    continue
                try:
                    gdf = pyogrio.read_dataframe(f, layer=layer_name)
                except Exception:
                    continue
                if gdf.empty or "geometry" not in gdf.columns:
                    continue
                gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=gdf.crs)
                gdf = ensure_wgs84(gdf)
                gdf["source_member"] = str(f.relative_to(root))
                gdf["source_feature_layer"] = str(layer_name)
                gdf["source_file"] = source_file
                fam = geometry_family(gdf)
                groups.setdefault(fam, []).append(gdf)

    if not groups:
        raise RuntimeError(f"No readable spatial data found in {archive}")

    out = {}
    for fam, parts in groups.items():
        out[fam] = gpd.GeoDataFrame(
            pd.concat(parts, ignore_index=True, sort=False),
            geometry="geometry",
            crs="EPSG:4326",
        )
    return out


def ingest_ksj(ctx):
    for key, info in KSJ_DATASETS.items():
        print(f"    KSJ {key}: {info['file']}", flush=True)
        dest = ctx.cache / "ksj" / info["file"]
        archive = download_url(
            ctx.session,
            info["url"],
            dest,
            ctx.timeout,
            ctx.refresh,
        )
        groups = read_ksj_archive(
            archive,
            info["file"],
            ctx.refresh,
        )

        for fam, gdf in groups.items():
            # Predictable layer names even when a source has multiple geometry types.
            layer = f"{info['base_layer']}_{fam}"
            gdf = gdf.copy()
            gdf["source_dataset"] = key.upper()
            gdf["source_url"] = info["url"]
            gdf["source_page"] = info["page"]

            write_spatial(
                ctx.output,
                layer,
                gdf,
                ctx.written_layers,
            )
            ctx.manifest.append(
                manifest_row(
                    8,
                    f"ksj_{key}",
                    info["title"],
                    "国土交通省 国土数値情報",
                    "direct public download URL",
                    info["url"],
                    info["file"],
                    layer,
                    fam,
                    len(gdf),
                    note=(
                        "Use conditions and dataset-specific cautions on "
                        f"{info['page']} apply."
                    ),
                )
            )


class Context:
    pass


def main():
    a = parse_args()

    selected = [x.strip() for x in a.datasets.split(",") if x.strip()]
    unknown = sorted(set(selected) - set(ALL_DATASET_KEYS))
    if unknown:
        raise RuntimeError(f"Unknown --datasets values: {unknown}")

    src = a.input.resolve()
    dst = a.output.resolve()
    cache = a.cache.resolve()

    if not src.exists():
        raise FileNotFoundError(src)
    if src == dst:
        raise RuntimeError(
            "--input and --output must differ. "
            "This script intentionally does not modify the source GPKG in place."
        )

    if dst.exists():
        if not a.overwrite_output:
            raise RuntimeError(
                f"Output already exists: {dst}\n"
                "Use --overwrite-output only if replacing it is intended."
            )
        dst.unlink()

    dst.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    print(f"[0] Copy base GPKG\n    {src}\n -> {dst}")
    shutil.copy2(src, dst)

    ctx = Context()
    ctx.session = build_session()
    ctx.input = src
    ctx.output = dst
    ctx.cache = cache
    ctx.timeout = a.timeout
    ctx.refresh = a.refresh
    ctx.tsunami_areas = a.tsunami_area
    ctx.manifest = []
    ctx.stream_state = {}
    ctx.written_layers = spatial_layer_names(dst)

    handlers = [
        ("region_risk", ingest_region_risk),
        ("fire_spread", ingest_fire_spread),
        ("seismic", lambda c: ingest_seismic_or_liquefaction(c, "seismic")),
        ("liquefaction", lambda c: ingest_seismic_or_liquefaction(c, "liquefaction")),
        ("inundation", ingest_inundation),
        ("storm_surge", ingest_storm_surge),
        ("tsunami", ingest_tsunami),
        ("ksj", ingest_ksj),
    ]

    fatal = None

    for key, func in handlers:
        if key not in selected:
            continue

        print(f"\n[{ALL_DATASET_KEYS.index(key)+1}/8] {key}")
        try:
            func(ctx)
            print("    OK")
        except Exception as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)

            info = TOKYO_DATASETS.get(key)
            ctx.manifest.append(
                manifest_row(
                    info["no"] if info else 8,
                    key,
                    info["title"] if info else "国土数値情報 土砂災害系",
                    info["org"] if info else "国土交通省 国土数値情報",
                    "automatic",
                    status="error",
                    note=str(exc),
                )
            )

            if not a.continue_on_error:
                fatal = exc
                break

    if fatal is not None:
        # Source download cache is intentionally preserved.
        # Remove the partial output so it cannot be mistaken for a complete GPKG.
        try:
            dst.unlink()
        except Exception:
            pass
        raise RuntimeError(
            "Hazard ingestion stopped. Partial output GPKG was removed. "
            f"Cache is retained at: {cache}\nCause: {fatal}"
        ) from fatal

    write_manifest(dst, ctx.manifest)

    layers = spatial_layer_names(dst)
    new_layers = sorted(
        x for x in layers
        if x.startswith("hazard_")
    )

    print("\nSUCCESS")
    print(f"  output: {dst}")
    print(f"  cache : {cache}")
    print(f"  hazard spatial layers: {len(new_layers)}")
    for layer in new_layers:
        print(f"    - {layer}")
    print("  attribute table:")
    print("    - hazard_source_manifest")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nFATAL: {exc}", file=sys.stderr)
        raise SystemExit(1)
