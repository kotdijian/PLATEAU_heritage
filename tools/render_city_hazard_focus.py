#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
render_city_hazard_focus.py

仕様変更版:
- 旧来の「浸水区域のみ」ではなく、浸水区域を除く全災害レイヤを対象にする
- 対象都市ごとにフォルダを作成する
- 都市範囲内に該当ハザード地物が存在しない場合は、その画像出力をスキップする

想定入力:
- 13_heritage_hazards.gpkg / 13_heritage_hazards_a31a.gpkg など
- 行政界レイヤ: admin_boundary_n03_2024
- 文化財 point レイヤ: heritage_buildings_point / heritage_points / heritage_source_points など
- 文化財 footprint レイヤ: heritage_buildings_footprint / heritage_buildings_footprints など

出力:
<outdir>/<都市名>/ 以下に PNG を保存
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

import geopandas as gpd
import matplotlib.pyplot as plt
import pyogrio
from matplotlib.lines import Line2D
from shapely.geometry import box

try:
    import contextily as ctx
except Exception:
    ctx = None


# -----------------------------
# basic helpers
# -----------------------------

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def sanitize_filename(name: str) -> str:
    name = str(name).strip()
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = re.sub(r"\s+", "_", name)
    return name or "unnamed"


def normalize_name(text: str) -> str:
    """Normalize municipality labels for matching user input to N03 names."""
    if text is None:
        return ""
    s = str(text).strip()
    s = re.sub(r"^(東京都|北海道|(?:京都|大阪)府|.{2,4}県)", "", s)
    s = re.sub(r"(市|区|町|村)$", "", s)
    return s


def configure_fonts() -> None:
    import matplotlib
    import matplotlib.font_manager as fm

    preferred = [
        "Hiragino Sans",
        "Hiragino Kaku Gothic ProN",
        "IPAexGothic",
        "IPAGothic",
        "Noto Sans CJK JP",
        "Yu Gothic",
        "MS Gothic",
    ]
    installed = {f.name for f in fm.fontManager.ttflist}
    for name in preferred:
        if name in installed:
            matplotlib.rcParams["font.family"] = name
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def list_layer_names(path: Path) -> list[str]:
    """Return only layer names from pyogrio.list_layers() across return formats."""
    info = pyogrio.list_layers(path)

    if hasattr(info, "columns") and "name" in info.columns:
        return info["name"].astype(str).tolist()

    result = []
    for row in info:
        if isinstance(row, str):
            result.append(row)
            continue
        try:
            if len(row) >= 1:
                result.append(str(row[0]))
                continue
        except TypeError:
            pass
        result.append(str(row))
    return result


def find_layer(
    path: Path,
    candidates: Optional[Iterable[str]] = None,
    contains: Optional[Iterable[str]] = None,
) -> str:
    layers = list_layer_names(path)

    if candidates:
        for cand in candidates:
            if cand in layers:
                return cand

    if contains:
        for layer in layers:
            lname = layer.lower()
            if all(token.lower() in lname for token in contains):
                return layer

    raise RuntimeError(
        f"Layer not found. candidates={list(candidates or [])}, "
        f"contains={list(contains or [])}, layers={layers[:25]}..."
    )


def ensure_wgs84(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.empty:
        if gdf.crs is None:
            gdf = gdf.set_crs(4326, allow_override=True)
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(4326)
        return gdf

    if gdf.crs is None:
        return gdf.set_crs(4326, allow_override=True)
    if gdf.crs.to_epsg() != 4326:
        return gdf.to_crs(4326)
    return gdf


def read_layer_bbox(path: Path, layer: str, bbox4326, columns=None) -> gpd.GeoDataFrame:
    gdf = pyogrio.read_dataframe(path, layer=layer, bbox=bbox4326, columns=columns)
    if "geometry" not in gdf.columns:
        raise RuntimeError(f"Layer has no geometry column: {layer}")
    return ensure_wgs84(gdf)


def add_gsi_basemap(ax, crs="EPSG:4326", zoom=14):
    if ctx is None:
        return
    try:
        source = "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"
        ctx.add_basemap(ax, crs=crs, source=source, attribution=False, zoom=zoom)
    except Exception:
        pass


# -----------------------------
# layer loading
# -----------------------------

def load_admin(path: Path) -> gpd.GeoDataFrame:
    layer = find_layer(path, candidates=["admin_boundary_n03_2024"], contains=["admin", "boundary"])
    gdf = pyogrio.read_dataframe(path, layer=layer)
    return ensure_wgs84(gdf)


def resolve_city(admin: gpd.GeoDataFrame, city_name: str) -> gpd.GeoDataFrame:
    query_name = normalize_name(city_name)

    candidate_cols = [c for c in admin.columns if re.search(r"(N03_004|city|市区町村|name)", str(c), re.I)]
    if not candidate_cols:
        candidate_cols = [c for c in admin.columns if c != "geometry"]

    for col in candidate_cols:
        s = admin[col].fillna("").astype(str)
        mask = s.map(normalize_name) == query_name
        hit = admin.loc[mask].copy()
        if not hit.empty:
            return hit

    raise RuntimeError(f"City not found in admin boundary layer: {city_name}")


def load_points(path: Path, bbox4326) -> gpd.GeoDataFrame:
    candidates = [
        "heritage_buildings_point",
        "heritage_points",
        "heritage_source_points",
        "heritage_point_features",
    ]
    for cand in candidates:
        try:
            return read_layer_bbox(path, cand, bbox4326)
        except Exception:
            pass

    layers = list_layer_names(path)
    for layer in layers:
        low = layer.lower()
        if "heritage" in low and "point" in low:
            try:
                return read_layer_bbox(path, layer, bbox4326)
            except Exception:
                continue

    return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")


def load_footprints(path: Path, bbox4326) -> gpd.GeoDataFrame:
    candidates = [
        "heritage_buildings_footprint",
        "heritage_buildings_footprints",
    ]
    for cand in candidates:
        try:
            return read_layer_bbox(path, cand, bbox4326)
        except Exception:
            pass

    layers = list_layer_names(path)
    for layer in layers:
        low = layer.lower()
        if "heritage" in low and "footprint" in low:
            try:
                return read_layer_bbox(path, layer, bbox4326)
            except Exception:
                continue

    return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")


# -----------------------------
# hazard discovery
# -----------------------------

def discover_hazard_layers(path: Path) -> list[str]:
    layers = list_layer_names(path)
    keep = []
    for layer in layers:
        low = layer.lower()
        if not low.startswith("hazard_"):
            continue
        if low.startswith("hazard_inundation"):
            continue
        if low in {"hazard_source_manifest", "hazard_metadata"}:
            continue
        if low.endswith("_manifest") or low.endswith("_metadata"):
            continue
        keep.append(layer)
    return sorted(keep)


def infer_numeric_column(layer_name: str, gdf: gpd.GeoDataFrame) -> Optional[str]:
    cols = list(gdf.columns)

    preferred_map = [
        "seismic_intensity",
        "liquefaction_pl",
        "subsidence_m",
        "T360mm_焼失棟数",
        "fire_spread_rank",
        "region_risk_rank",
        "rank",
        "class",
        "value",
        "depth",
        "height",
        "arrival",
    ]
    for p in preferred_map:
        if p in cols:
            return p

    for col in cols:
        if col == "geometry":
            continue
        try:
            if gdf[col].dtype.kind in "ifu":
                return col
        except Exception:
            continue
    return None


def is_area_like(gdf: gpd.GeoDataFrame) -> bool:
    if gdf.empty:
        return True
    geom_types = {str(gt) for gt in gdf.geom_type.dropna().unique().tolist()}
    return any(gt in geom_types for gt in ["Polygon", "MultiPolygon", "LineString", "MultiLineString"])


def classify_points_by_hazard(points: gpd.GeoDataFrame, hazard: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    if points.empty or hazard.empty:
        return points.copy(), points.iloc[0:0].copy()

    try:
        union_geom = hazard.unary_union
        inside = points[points.intersects(union_geom)].copy()
        outside = points[~points.intersects(union_geom)].copy()
        return outside, inside
    except Exception:
        return points.copy(), points.iloc[0:0].copy()


# -----------------------------
# plotting
# -----------------------------

def plot_city_hazard(
    out_png: Path,
    city_name: str,
    hazard_name: str,
    hazard: gpd.GeoDataFrame,
    admin_city: gpd.GeoDataFrame,
    points: gpd.GeoDataFrame,
    footprints: gpd.GeoDataFrame,
    numeric_col: Optional[str],
    subtitle: Optional[str] = None,
    zoom: int = 14,
) -> None:
    fig, ax = plt.subplots(1, 1, figsize=(8.5, 8.5))

    minx, miny, maxx, maxy = admin_city.total_bounds
    dx = maxx - minx
    dy = maxy - miny
    pad_x = max(dx * 0.08, 0.003)
    pad_y = max(dy * 0.08, 0.003)

    ax.set_xlim(minx - pad_x, maxx + pad_x)
    ax.set_ylim(miny - pad_y, maxy + pad_y)

    add_gsi_basemap(ax, crs="EPSG:4326", zoom=zoom)

    # city boundary
    admin_city.boundary.plot(ax=ax, color="black", linewidth=1.4, zorder=2)

    # hazard
    if not hazard.empty:
        if numeric_col and numeric_col in hazard.columns:
            # "震度大=紫, 小=黄" 方向に合わせて plasma_r を基本採用
            cmap = "plasma_r" if ("seismic" in hazard_name.lower() or "liquefaction" in hazard_name.lower()) else "viridis"
            hazard.plot(
                ax=ax,
                column=numeric_col,
                cmap=cmap,
                linewidth=0.2,
                edgecolor="none",
                alpha=0.55,
                legend=True,
                zorder=3,
            )
        else:
            if is_area_like(hazard):
                hazard.plot(ax=ax, color="#69b3a2", edgecolor="#2f6f62", linewidth=0.4, alpha=0.45, zorder=3)
            else:
                hazard.plot(ax=ax, color="#2f6f62", markersize=12, alpha=0.75, zorder=3)

    # points: outside first, inside highlighted
    points_out, points_in = classify_points_by_hazard(points, hazard)

    if not points_out.empty:
        points_out.plot(ax=ax, color="#9e9e9e", markersize=12, alpha=0.85, zorder=4)

    # footprints over points per user preference
    if not footprints.empty:
        footprints.plot(ax=ax, facecolor="#6f6f6f", edgecolor="#2b2b2b", linewidth=0.25, alpha=0.85, zorder=5)

    if not points_in.empty:
        points_in.plot(ax=ax, color="#d7301f", markersize=16, alpha=0.95, zorder=6)

    # title
    title = f"{city_name} | {hazard_name}"
    if subtitle:
        title += f"\n{subtitle}"
    ax.set_title(title, fontsize=12)

    # legend
    legend_handles = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#9e9e9e", markersize=7, label="文化財 point（領域外）"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#d7301f", markersize=7, label="文化財 point（領域内）"),
        Line2D([0], [0], marker="s", color="#2b2b2b", markerfacecolor="#6f6f6f", markersize=7, label="文化財 building footprint"),
    ]
    ax.legend(handles=legend_handles, loc="lower left", fontsize=8, frameon=True)

    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=220, bbox_inches="tight")
    plt.close(fig)


# -----------------------------
# execution
# -----------------------------

def process_city(path: Path, city_name: str, out_root: Path) -> tuple[int, int]:
    admin = load_admin(path)
    admin_city = resolve_city(admin, city_name)
    admin_city = admin_city.dissolve().reset_index(drop=True)
    admin_city = ensure_wgs84(admin_city)

    bbox = tuple(admin_city.total_bounds)

    points = load_points(path, bbox)
    footprints = load_footprints(path, bbox)

    # clip heritage layers to city polygon if possible
    try:
        if not points.empty:
            points = gpd.clip(points, admin_city)
    except Exception:
        pass

    try:
        if not footprints.empty:
            footprints = gpd.clip(footprints, admin_city)
    except Exception:
        pass

    hazard_layers = discover_hazard_layers(path)
    city_dir = out_root / sanitize_filename(city_name)
    city_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    skipped = 0

    print(f"\n=== {city_name} ===")
    print(f"hazard layers discovered: {len(hazard_layers)}")

    for layer in hazard_layers:
        try:
            hz = read_layer_bbox(path, layer, bbox)
        except Exception as e:
            print(f"  SKIP read error: {layer} -> {e}")
            skipped += 1
            continue

        # optional city clip
        try:
            if not hz.empty:
                hz = gpd.clip(hz, admin_city)
        except Exception:
            pass

        if hz.empty:
            print(f"  SKIP empty in city extent: {layer}")
            skipped += 1
            continue

        # split by scenario if scenario column exists with >1 value
        if "scenario" in hz.columns:
            scenarios = [x for x in hz["scenario"].dropna().astype(str).unique().tolist() if x.strip()]
            if len(scenarios) > 1:
                for scenario in sorted(scenarios):
                    sub = hz[hz["scenario"].astype(str) == scenario].copy()
                    if sub.empty:
                        continue
                    numeric_col = infer_numeric_column(layer, sub)
                    hz_name = layer.replace("hazard_", "")
                    out_png = city_dir / f"{sanitize_filename(hz_name)}__{sanitize_filename(scenario)}.png"
                    plot_city_hazard(
                        out_png=out_png,
                        city_name=city_name,
                        hazard_name=hz_name,
                        hazard=sub,
                        admin_city=admin_city,
                        points=points,
                        footprints=footprints,
                        numeric_col=numeric_col,
                        subtitle=f"scenario: {scenario}",
                    )
                    print(f"  OK {out_png.name}")
                    generated += 1
                continue

        numeric_col = infer_numeric_column(layer, hz)
        hz_name = layer.replace("hazard_", "")
        out_png = city_dir / f"{sanitize_filename(hz_name)}.png"
        plot_city_hazard(
            out_png=out_png,
            city_name=city_name,
            hazard_name=hz_name,
            hazard=hz,
            admin_city=admin_city,
            points=points,
            footprints=footprints,
            numeric_col=numeric_col,
            subtitle=None,
        )
        print(f"  OK {out_png.name}")
        generated += 1

    return generated, skipped


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Render city hazard focus maps (all hazards except inundation).")
    p.add_argument("gpkg", help="Input GPKG path")
    p.add_argument("--cities", nargs="+", required=True, help="City names, e.g. 国分寺 国立")
    p.add_argument(
        "--outdir",
        default="summary_results/figures/city",
        help="Output root directory (default: summary_results/figures/city)",
    )
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    configure_fonts()

    gpkg = Path(args.gpkg).expanduser().resolve()
    out_root = Path(args.outdir).expanduser().resolve()

    if not gpkg.is_file():
        raise SystemExit(f"Input GPKG not found: {gpkg}")

    total_generated = 0
    total_skipped = 0

    for city in args.cities:
        gen, skip = process_city(gpkg, city, out_root)
        total_generated += gen
        total_skipped += skip

    print("\nDONE")
    print(f"generated: {total_generated}")
    print(f"skipped  : {total_skipped}")
    print(f"output   : {out_root}")


if __name__ == "__main__":
    main()
