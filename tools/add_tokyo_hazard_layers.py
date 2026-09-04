#!/usr/bin/env python3
"""
add_tokyo_hazard_layers.py

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
9. 国土数値情報 A31a 洪水浸水想定区域（河川単位）
   - 想定最大規模
   - 既定対象: 荒川、多摩川

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

A31a関東地方整備局版（荒川・多摩川）だけを既存 hazard GPKG に追加する場合:
python add_tokyo_hazard_layers.py \
  --input ./output/13_heritage_hazards.gpkg \
  --output ./output/13_heritage_hazards_a31a.gpkg \
  --cache ./.cache/hazard_sources \
  --datasets a31a

対象河川を明示する場合:
python add_tokyo_hazard_layers.py \
  --input ./output/13_heritage_hazards.gpkg \
  --output ./output/13_heritage_hazards_a31a.gpkg \
  --datasets a31a \
  --a31a-river 荒川 \
  --a31a-river 多摩川
"""

from __future__ import annotations

import argparse
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


A31A_DATASET = {
    "no": 9,
    "title": "国土数値情報 洪水浸水想定区域（河川単位）",
    "org": "国土交通省 関東地方整備局 / 国土数値情報",
    "file": "A31a-25_83_10_GEOJSON.zip",
    "url": (
        "https://nlftp.mlit.go.jp/ksj/gml/data/"
        "A31a/A31a-25/A31a-25_83_10_GEOJSON.zip"
    ),
    "page": (
        "https://nlftp.mlit.go.jp/ksj/gml/datalist/"
        "KsjTmplt-A31a-2025.html"
    ),
    "year": 2025,
    "scenario": "想定最大規模",
    "source_scope": "関東地方整備局（作成種別コード83）",
    "license": "CC BY 4.0",
    "license_url": "https://nlftp.mlit.go.jp/ksj/other/agreement.html",
    "license_checked_at": "2026-09-05",
    "default_rivers": ["荒川", "多摩川"],
}

A31A_DEPTH_NATIVE = {
    1: ("0–0.5 m", 0.0, 0.5),
    2: ("0.5–3 m", 0.5, 3.0),
    3: ("3–5 m", 3.0, 5.0),
    4: ("5–10 m", 5.0, 10.0),
    5: ("10–20 m", 10.0, 20.0),
    6: ("20 m以上", 20.0, None),
}

A31A_DEPTH_SUMMARY = {
    1: "0–0.5 m",
    2: "0.5–3 m",
    3: "3–5 m",
    4: "5 m以上",
    5: "5 m以上",
    6: "5 m以上",
}

ALL_DATASET_KEYS = [
    "region_risk",
    "fire_spread",
    "seismic",
    "liquefaction",
    "inundation",
    "storm_surge",
    "tsunami",
    "a31a",
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
        "--a31a-river",
        action="append",
        default=[],
        help=(
            "River name to extract from A31a expected-maximum-scale data. "
            "Repeatable. Default: 荒川 and 多摩川."
        ),
    )
    p.add_argument(
        "--a31a-archive",
        type=Path,
        default=None,
        help=(
            "Optional local A31a GeoJSON ZIP. If omitted, the 2025 Tokyo "
            "A31a archive is downloaded automatically."
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
    p.add_argument("--timeout", type=int, default=90)
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

    with session.get(url, timeout=timeout, stream=True) as r:
        r.raise_for_status()
        with part.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    if part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded zero-byte file: {url}")

    part.replace(dest)
    return dest


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
    """Append new manifest rows while preserving rows copied from the input GPKG."""
    new_df = pd.DataFrame(rows)
    with sqlite3.connect(gpkg) as con:
        old_df = pd.DataFrame()
        try:
            old_df = pd.read_sql_query(
                "SELECT * FROM hazard_source_manifest",
                con,
            )
        except Exception:
            pass

        if old_df.empty:
            df = new_df
        elif new_df.empty:
            df = old_df
        else:
            # Align columns without discarding older manifest metadata.
            all_cols = list(dict.fromkeys(list(old_df.columns) + list(new_df.columns)))
            df = pd.concat(
                [
                    old_df.reindex(columns=all_cols),
                    new_df.reindex(columns=all_cols),
                ],
                ignore_index=True,
                sort=False,
            )

        df.to_sql(
            "hazard_source_manifest",
            con,
            if_exists="replace",
            index=False,
        )
        register_attribute_table(con, "hazard_source_manifest", len(df))
        con.commit()


SOURCE_LICENSE_COLUMNS = [
    "source_key",
    "category",
    "source_dataset_id",
    "dataset_title",
    "provider",
    "layer_pattern",
    "license",
    "license_url",
    "redistribution",
    "commercial_use",
    "modification",
    "redistribution_status",
    "attribution",
    "usage_note",
    "license_checked_at",
]


def upsert_source_license_row(gpkg: Path, row: dict) -> None:
    """Insert or replace one source-license row while preserving existing rows."""
    with sqlite3.connect(gpkg) as con:
        old_df = pd.DataFrame()
        try:
            old_df = pd.read_sql_query("SELECT * FROM source_license", con)
        except Exception:
            pass

        row_df = pd.DataFrame([row])

        if old_df.empty and len(old_df.columns) == 0:
            all_cols = list(dict.fromkeys(SOURCE_LICENSE_COLUMNS + list(row_df.columns)))
            df = row_df.reindex(columns=all_cols)
        else:
            all_cols = list(dict.fromkeys(list(old_df.columns) + list(row_df.columns)))
            old_df = old_df.reindex(columns=all_cols)
            row_df = row_df.reindex(columns=all_cols)

            if "source_key" in old_df.columns and row.get("source_key") is not None:
                old_df = old_df[
                    old_df["source_key"].astype(str) != str(row["source_key"])
                ]

            df = pd.concat(
                [old_df, row_df],
                ignore_index=True,
                sort=False,
            )

        df.to_sql(
            "source_license",
            con,
            if_exists="replace",
            index=False,
        )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        con.execute(
            "DELETE FROM gpkg_contents WHERE table_name='source_license'"
        )
        con.execute(
            """
            INSERT INTO gpkg_contents
            (table_name,data_type,identifier,description,last_change,
             min_x,min_y,max_x,max_y,srs_id)
            VALUES ('source_license','attributes','source_license',?,?,
                    NULL,NULL,NULL,NULL,NULL)
            """,
            (
                f"Source datasets, licenses, and reuse conditions ({len(df)} rows)",
                now,
            ),
        )
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

    if key == "seismic":
        resources = [
            r for r in resources
            if (
                "計測震度" in resource_name(r)
                or (
                    "50" in normalized(resource_name(r))
                    and "メッシュ" in resource_name(r)
                )
            )
        ]
        layer = "hazard_seismic_intensity_50m"
    else:
        resources = [
            r for r in resources
            if (
                "液状化" in resource_name(r)
                or (
                    "250" in normalized(resource_name(r))
                    and "メッシュ" in resource_name(r)
                )
            )
        ]
        layer = "hazard_liquefaction_250m"

    if not resources:
        raise RuntimeError(f"No matching CSV resources for {key}")

    for r in resources:
        p = download_resource(
            ctx.session,
            r,
            ctx.cache / "tokyo" / info["id"],
            ctx.timeout,
            ctx.refresh,
        )
        df = read_csv_auto(p)

        if key == "seismic":
            gdf = build_seismic_gdf(
                df,
                seismic_scenario(resource_name(r)),
                r,
            )
            no = 3
            geom_type = "Polygon (official 50m mesh-code rule)"
        else:
            gdf = build_liquefaction_gdf(
                df,
                liquefaction_scenario(resource_name(r)),
                r,
            )
            no = 4
            geom_type = "Polygon (standard 250m regional mesh)"

        append_stream(
            ctx.output,
            layer,
            gdf,
            ctx.stream_state,
            ctx.written_layers,
        )
        ctx.manifest.append(
            manifest_row(
                no,
                key,
                info["title"],
                info["org"],
                "CKAN metadata API + direct CSV download",
                str(r.get("url") or ""),
                resource_name(r),
                layer,
                geom_type,
                len(gdf),
            )
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
    resources = [
        r for r in pkg.get("resources", [])
        if resource_format(r) == "CSV"
    ]
    if not resources:
        raise RuntimeError("No CSV resources found for inundation expected areas.")

    layer = "hazard_inundation_expected_points"

    for r in resources:
        p = download_resource(
            ctx.session,
            r,
            ctx.cache / "tokyo" / info["id"],
            ctx.timeout,
            ctx.refresh,
        )
        df = read_csv_auto(p)
        gdf = build_inundation_points(df, r)

        append_stream(
            ctx.output,
            layer,
            gdf,
            ctx.stream_state,
            ctx.written_layers,
        )
        ctx.manifest.append(
            manifest_row(
                5,
                "inundation",
                info["title"],
                info["org"],
                "CKAN metadata API + direct CSV download",
                str(r.get("url") or ""),
                resource_name(r),
                layer,
                "Point (explicit CSV longitude/latitude)",
                len(gdf),
                note=(
                    "No polygon is inferred. Each feature is the exact "
                    "published coordinate."
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
    resources = [
        r for r in pkg.get("resources", [])
        if resource_format(r) == "SHP"
    ]
    if not resources:
        raise RuntimeError("No SHP resources found for storm surge.")

    expected = set()

    for r in resources:
        layer = storm_surge_layer(resource_name(r))
        if not layer:
            raise RuntimeError(
                "Cannot classify storm-surge SHP resource: "
                + resource_name(r)
            )
        expected.add(layer)

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
                6,
                "storm_surge",
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

    needed = {
        "hazard_storm_surge_depth",
        "hazard_storm_surge_duration",
        "hazard_storm_surge_house_collapse",
    }
    if not needed.issubset(expected):
        raise RuntimeError(
            "Storm-surge SHP set incomplete. "
            f"missing={sorted(needed - expected)}"
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
        layer = "hazard_tsunami_arrival_points"

    elif metric == "height":
        col = col_by_candidates(
            df,
            candidates=("津波高（最大）（m）", "津波高(最大)(m)"),
            contains=("津波高", "最大"),
        )
        if not col:
            raise RuntimeError("Tsunami-height CSV lacks maximum-height field.")
        common["tsunami_height_max_m"] = numeric(df.loc[mask, col]).values
        layer = "hazard_tsunami_height_points"

    else:
        col = col_by_candidates(
            df,
            candidates=("浸水深（最大）（m）", "浸水深(最大)(m)"),
            contains=("浸水深", "最大"),
        )
        if not col:
            raise RuntimeError("Tsunami-depth CSV lacks maximum-depth field.")
        common["inundation_depth_max_m"] = numeric(df.loc[mask, col]).values
        layer = "hazard_tsunami_depth_points"

    out = pd.DataFrame(common)
    geom = gpd.points_from_xy(out["lon"], out["lat"], crs="EPSG:4326")
    return layer, gpd.GeoDataFrame(out, geometry=geom, crs="EPSG:4326")


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
            ctx.session,
            r,
            ctx.cache / "tokyo" / info["id"],
            ctx.timeout,
            ctx.refresh,
        )
        df = read_csv_auto(p)
        layer, gdf = build_tsunami_points(df, r)

        append_stream(
            ctx.output,
            layer,
            gdf,
            ctx.stream_state,
            ctx.written_layers,
        )
        ctx.manifest.append(
            manifest_row(
                7,
                "tsunami",
                info["title"],
                info["org"],
                "CKAN metadata API + direct CSV download",
                str(r.get("url") or ""),
                resource_name(r),
                layer,
                "Point (explicit CSV longitude/latitude)",
                len(gdf),
                note=(
                    "Published data are 10m mesh values, but this script "
                    "does not infer cell polygons; it stores published "
                    "coordinate points exactly."
                ),
            )
        )



# ----------------------------------------------------------------------
# Dataset 9: 国土数値情報 A31a 洪水浸水想定区域（河川単位）
# ----------------------------------------------------------------------

def a31a_target_rivers(ctx) -> list[str]:
    rivers = [str(x).strip() for x in getattr(ctx, "a31a_rivers", []) if str(x).strip()]
    return rivers or list(A31A_DATASET["default_rivers"])


def a31a_extract_root(archive: Path, refresh=False) -> Path:
    return extract_zip(archive, refresh)


def a31a_expected_max_files(root: Path) -> list[Path]:
    """
    A31a GeoJSON archives are split by scenario / river.
    Rather than depending on directory names, select GeoJSON members that
    expose the expected-maximum-scale fields A31a_202 (river name)
    and A31a_205 (depth-rank code).
    """
    out = []
    for f in sorted(root.rglob("*.geojson")):
        try:
            info = pyogrio.read_info(f)
            fields = info.get("fields")
            cols = set(map(str, fields)) if fields is not None else set()
        except Exception:
            cols = set()
        if {"A31a_202", "A31a_205"}.issubset(cols):
            out.append(f)
    return out


def a31a_normalize_river_name(value) -> str:
    return normalized(value)


def a31a_add_normalized_fields(gdf: gpd.GeoDataFrame, source_member: str) -> gpd.GeoDataFrame:
    out = gdf.copy()
    rank = pd.to_numeric(out["A31a_205"], errors="coerce").astype("Int64")

    out["river_code"] = out.get("A31a_201", pd.Series(index=out.index, dtype="object")).astype(str)
    out["river_name"] = out["A31a_202"].astype(str)
    out["river_manager_code"] = out.get("A31a_203", pd.Series(index=out.index, dtype="object")).astype(str)
    out["river_manager"] = out.get("A31a_204", pd.Series(index=out.index, dtype="object")).astype(str)
    out["scenario"] = A31A_DATASET["scenario"]
    out["depth_rank_code"] = rank

    native_label = []
    depth_min = []
    depth_max = []
    summary_label = []
    for value in rank:
        if pd.isna(value):
            native_label.append(None)
            depth_min.append(np.nan)
            depth_max.append(np.nan)
            summary_label.append(None)
            continue
        code = int(value)
        native = A31A_DEPTH_NATIVE.get(code)
        if native is None:
            native_label.append(None)
            depth_min.append(np.nan)
            depth_max.append(np.nan)
            summary_label.append(None)
        else:
            native_label.append(native[0])
            depth_min.append(native[1])
            depth_max.append(np.nan if native[2] is None else native[2])
            summary_label.append(A31A_DEPTH_SUMMARY.get(code))

    out["depth_class_native"] = native_label
    out["depth_min_m"] = depth_min
    out["depth_max_m"] = depth_max
    out["depth_class_summary"] = summary_label
    out["source_dataset"] = "A31a"
    out["source_scope"] = A31A_DATASET["source_scope"]
    out["source_year"] = A31A_DATASET["year"]
    out["source_file"] = A31A_DATASET["file"]
    out["source_member"] = source_member
    out["source_url"] = A31A_DATASET["url"]
    out["source_page"] = A31A_DATASET["page"]
    out["source_license"] = "CC BY 4.0"

    # Keep the original A31a_* fields as well for auditability.
    return out


def a31a_tokyo_clip_geometry(ctx):
    """
    Return the existing Tokyo administrative geometry if available.
    The current project GPKG contains mainland N03 boundaries; clipping is
    therefore appropriate for 荒川 / 多摩川 and reduces cross-prefecture geometry.
    """
    layer = "admin_boundary_n03_2024"
    if layer not in ctx.written_layers:
        return None
    try:
        admin = pyogrio.read_dataframe(ctx.output, layer=layer)
        if admin.empty:
            return None
        admin = ensure_wgs84(admin)
        return admin.geometry.union_all()
    except Exception as exc:
        print(f"    A31a: admin clip unavailable ({exc}); keeping source geometry", flush=True)
        return None


def a31a_clip(gdf: gpd.GeoDataFrame, clip_geom) -> gpd.GeoDataFrame:
    if clip_geom is None or gdf.empty:
        return gdf
    mask = gdf.geometry.intersects(clip_geom)
    out = gdf.loc[mask].copy()
    if out.empty:
        return out
    # The archive is already Tokyo-specific. Intersects is preferred over
    # geometry intersection here to preserve the published polygon geometry.
    return out


def ingest_a31a(ctx):
    info = A31A_DATASET
    rivers = a31a_target_rivers(ctx)

    if getattr(ctx, "a31a_archive", None):
        archive = Path(ctx.a31a_archive).expanduser().resolve()
        if not archive.exists():
            raise FileNotFoundError(archive)
        acquisition = "local archive supplied by --a31a-archive"
        source_url = info["url"]
    else:
        dest = ctx.cache / "ksj" / "a31a" / info["file"]
        print(f"    A31a download: {info['file']}", flush=True)
        archive = download_url(
            ctx.session,
            info["url"],
            dest,
            ctx.timeout,
            ctx.refresh,
        )
        acquisition = "direct public download URL"
        source_url = info["url"]

    root = a31a_extract_root(archive, ctx.refresh)
    members = a31a_expected_max_files(root)
    if not members:
        raise RuntimeError(
            "No expected-maximum-scale A31a GeoJSON found. "
            "Expected fields: A31a_202 and A31a_205."
        )

    print(f"    A31a expected-maximum GeoJSON files: {len(members)}", flush=True)

    target_norm = {a31a_normalize_river_name(x): x for x in rivers}
    matched_parts: dict[str, list[gpd.GeoDataFrame]] = {x: [] for x in rivers}
    available_names = set()
    clip_geom = a31a_tokyo_clip_geometry(ctx)

    for f in members:
        gdf = gpd.read_file(f, engine="pyogrio")
        if gdf.empty:
            continue
        gdf = ensure_wgs84(gdf)
        if "A31a_202" not in gdf.columns or "A31a_205" not in gdf.columns:
            continue

        raw_names = gdf["A31a_202"].dropna().astype(str)
        available_names.update(raw_names.unique().tolist())
        normalized_names = gdf["A31a_202"].map(a31a_normalize_river_name)

        for norm_name, display_name in target_norm.items():
            # Prefer an exact normalized river-name match.
            mask = normalized_names == norm_name

            # Some A31a members may carry a longer official river label.
            # If there is no exact match, accept only names containing the
            # requested name; keep the source river_name unchanged for audit.
            if not mask.any():
                mask = normalized_names.str.contains(
                    re.escape(norm_name),
                    regex=True,
                    na=False,
                )

            sub = gdf.loc[mask].copy()
            if sub.empty:
                continue

            sub = a31a_clip(sub, clip_geom)
            if sub.empty:
                continue

            source_names = sorted(sub["A31a_202"].dropna().astype(str).unique().tolist())
            print(
                f"    A31a target {display_name}: source river name(s)={source_names}",
                flush=True,
            )

            sub = a31a_add_normalized_fields(
                sub,
                str(f.relative_to(root)),
            )
            matched_parts[display_name].append(sub)

    missing = [name for name, parts in matched_parts.items() if not parts]
    if missing:
        sample = sorted(str(x) for x in available_names)[:80]
        raise RuntimeError(
            "A31a target river(s) not found: "
            f"{missing}. Available river-name sample: {sample}"
        )

    written_a31a_layers = []

    for river_name in rivers:
        parts = matched_parts[river_name]
        gdf = gpd.GeoDataFrame(
            pd.concat(parts, ignore_index=True, sort=False),
            geometry="geometry",
            crs="EPSG:4326",
        )

        # Drop exact duplicate geometry/attribute rows that can occur when
        # scenario files are split into multiple members.
        dedupe_cols = [
            c for c in ["A31a_201", "A31a_202", "A31a_205"]
            if c in gdf.columns
        ]
        if dedupe_cols:
            geom_wkb = gdf.geometry.to_wkb()
            tmp = gdf.assign(_geom_wkb=geom_wkb)
            tmp = tmp.drop_duplicates(subset=dedupe_cols + ["_geom_wkb"])
            gdf = gpd.GeoDataFrame(
                tmp.drop(columns=["_geom_wkb"]),
                geometry="geometry",
                crs="EPSG:4326",
            )

        layer = f"hazard_inundation_a31a_{safe_name(river_name, limit=60)}"
        write_spatial(
            ctx.output,
            layer,
            gdf,
            ctx.written_layers,
        )
        written_a31a_layers.append(layer)

        ranks = sorted(
            pd.to_numeric(gdf["depth_rank_code"], errors="coerce")
            .dropna()
            .astype(int)
            .unique()
            .tolist()
        )
        print(
            f"    A31a {river_name}: {len(gdf):,} polygons, "
            f"depth ranks={ranks} -> {layer}",
            flush=True,
        )

        ctx.manifest.append(
            manifest_row(
                info["no"],
                "a31a",
                f"{info['title']} / {river_name} / {info['scenario']}",
                info["org"],
                acquisition,
                source_url,
                info["file"],
                layer,
                "Polygon",
                len(gdf),
                note=(
                    "2025年度 A31a 関東地方整備局 GeoJSON（国管理河川）; 想定最大規模. "
                    "Original A31a_201–A31a_205 fields are retained. "
                    "depth_class_summary maps native ranks 4–6 to 5m以上 "
                    "for Summary Results compatibility. License: CC BY 4.0."
                ),
            )
        )



    upsert_source_license_row(
        ctx.output,
        {
            "source_key": "a31a",
            "category": "hazard",
            "source_dataset_id": "A31a-2025-83",
            "dataset_title": f"{info['title']}（2025年度・関東地方整備局）",
            "provider": info["org"],
            "layer_pattern": ";".join(written_a31a_layers),
            "license": info["license"],
            "license_url": info["license_url"],
            "redistribution": "yes",
            "commercial_use": "yes",
            "modification": "yes",
            "redistribution_status": "confirmed",
            "attribution": (
                "国土交通省「国土数値情報 洪水浸水想定区域（河川単位）"
                "2025年度版」を加工して作成"
            ),
            "usage_note": (
                "国土数値情報ダウンロードサイトコンテンツ利用規約および"
                "A31a個別データページの留意事項に従う。"
                "関東地方整備局（作成種別コード83）の想定最大規模を使用。"
            ),
            "license_checked_at": info["license_checked_at"],
        },
    )
    print(
        f"    A31a license metadata: source_license updated ({info['license']})",
        flush=True,
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
    ctx.a31a_rivers = a.a31a_river
    ctx.a31a_archive = a.a31a_archive
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
        ("a31a", ingest_a31a),
        ("ksj", ingest_ksj),
    ]

    fatal = None

    for key, func in handlers:
        if key not in selected:
            continue

        print(f"\n[{ALL_DATASET_KEYS.index(key)+1}/{len(ALL_DATASET_KEYS)}] {key}")
        try:
            func(ctx)
            print("    OK")
        except Exception as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)

            info = TOKYO_DATASETS.get(key)
            if key == "a31a":
                info = A31A_DATASET
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
