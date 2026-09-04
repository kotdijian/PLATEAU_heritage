#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from shapely.geometry import box


INUNDATION_LAYER = "hazard_inundation_野川_仙川_入間川_谷沢川及び丸子川流域"

CITY_FALLBACKS = {
    "国分寺": (139.41, 35.66, 139.51, 35.74),
    "国立": (139.42, 35.66, 139.47, 35.71),
}

DEPTH_COL_CANDIDATES = [
    "inundation_depth_m",
    "想定浸水深",
    "max_depth",
    "depth",
]

ADMIN_NAME_COL_CANDIDATES = [
    "N03_004", "N03_003", "N03_002", "city_name", "name", "municipality"
]

POINT_LAYER_CANDIDATES = [
    "heritage_buildings_point",
    "heritage_points",
    "heritage_source_points",
]


def find_layer(path: Path, candidates: list[str], contains: list[str] | None = None) -> str:
    # geopandas.list_layers() returns a DataFrame in current GeoPandas.
    # Iterating over the DataFrame yields column names ("name", "geometry_type"),
    # which caused the previous layers=['n', 'g'] error.
    layer_info = gpd.list_layers(path)
    if hasattr(layer_info, "columns") and "name" in layer_info.columns:
        layers = layer_info["name"].astype(str).tolist()
    else:
        # Fallback for older return formats.
        layers = []
        for row in layer_info:
            if isinstance(row, (list, tuple)) and row:
                layers.append(str(row[0]))
            else:
                layers.append(str(row))

    for c in candidates:
        if c in layers:
            return c
    if contains:
        for name in layers:
            lname = name.lower()
            if all(tok.lower() in lname for tok in contains):
                return name
    raise RuntimeError(
        f"Layer not found. candidates={candidates}, contains={contains}, "
        f"available_layers={layers[:30]}..."
    )


def load_admin(gpkg: Path) -> gpd.GeoDataFrame:
    name = find_layer(gpkg, ["admin_boundary_n03_2024"], contains=["admin", "boundary"])
    gdf = gpd.read_file(gpkg, layer=name)
    return gdf.to_crs(4326)


def load_points(gpkg: Path) -> gpd.GeoDataFrame:
    name = find_layer(gpkg, POINT_LAYER_CANDIDATES, contains=["heritage", "point"])
    gdf = gpd.read_file(gpkg, layer=name)
    return gdf.to_crs(4326)


def find_name_column(gdf: gpd.GeoDataFrame) -> str:
    for c in ADMIN_NAME_COL_CANDIDATES:
        if c in gdf.columns:
            return c
    raise RuntimeError(f"Admin name column not found. columns={list(gdf.columns)}")


def find_depth_column(gdf: gpd.GeoDataFrame) -> str:
    for c in DEPTH_COL_CANDIDATES:
        if c in gdf.columns:
            return c
    raise RuntimeError(f"Depth column not found. columns={list(gdf.columns)}")


def depth_category(series):
    cats = []
    for v in series.fillna(0):
        try:
            x = float(v)
        except Exception:
            x = 0
        if x <= 0:
            cats.append("0")
        elif x <= 0.5:
            cats.append("0–0.5 m")
        elif x <= 3:
            cats.append("0.5–3 m")
        elif x <= 5:
            cats.append("3–5 m")
        else:
            cats.append("5 m以上")
    return cats


COLOR_MAP = {
    "0": "#ffffff",
    "0–0.5 m": "#cfe8ff",
    "0.5–3 m": "#6baed6",
    "3–5 m": "#2171b5",
    "5 m以上": "#08306b",
}
LABELS = ["0", "0–0.5 m", "0.5–3 m", "3–5 m", "5 m以上"]


def get_city(admin: gpd.GeoDataFrame, city_name: str) -> gpd.GeoDataFrame:
    name_col = find_name_column(admin)
    mask = admin[name_col].astype(str).str.contains(city_name, na=False)
    return admin[mask].copy()


def bounds_with_padding(gdf: gpd.GeoDataFrame, fallback):
    if gdf.empty:
        return fallback, box(*fallback)
    minx, miny, maxx, maxy = gdf.total_bounds
    pad_x = max((maxx - minx) * 0.08, 0.005)
    pad_y = max((maxy - miny) * 0.08, 0.005)
    bounds = (minx - pad_x, miny - pad_y, maxx + pad_x, maxy + pad_y)
    geom = gdf.union_all()
    return bounds, geom


def plot_city(gpkg: Path, city_name: str, outdir: Path):
    admin = load_admin(gpkg)
    points = load_points(gpkg)
    hz = gpd.read_file(gpkg, layer=INUNDATION_LAYER).to_crs(4326)

    city = get_city(admin, city_name)
    fallback = CITY_FALLBACKS[city_name]
    bounds, city_geom = bounds_with_padding(city, fallback)

    hz = hz[hz.intersects(city_geom)].copy()
    points = points[points.intersects(box(*bounds))].copy()
    admin_clip = admin[admin.intersects(box(*bounds))].copy()

    if hz.empty:
        print(f"[WARN] No inundation polygons in {city_name}")
        return

    depth_col = find_depth_column(hz)
    hz["depth_cat"] = depth_category(hz[depth_col])

    inside_idx = points.geometry.within(hz.union_all())
    pts_in = points[inside_idx]
    pts_out = points[~inside_idx]

    fig, ax = plt.subplots(figsize=(8, 8))
    hz.plot(ax=ax, color=hz["depth_cat"].map(COLOR_MAP), linewidth=0, alpha=0.88, zorder=2)

    if not admin_clip.empty:
        admin_clip.boundary.plot(ax=ax, linewidth=0.8, edgecolor="0.55", zorder=4)
    if not city.empty:
        city.boundary.plot(ax=ax, linewidth=1.4, edgecolor="0.25", zorder=5)

    if not pts_out.empty:
        pts_out.plot(ax=ax, markersize=10, color="black", zorder=6)
    if not pts_in.empty:
        pts_in.plot(ax=ax, markersize=14, color="red", zorder=7)

    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    ax.set_title(f"{city_name}市域：浸水予想区域 野川・仙川・入間川・谷沢川及び丸子川流域")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")

    legend = [Patch(facecolor=COLOR_MAP[l], edgecolor="none", label=l) for l in LABELS]
    point_legend = [
        Line2D([], [], marker="o", linestyle="", color="red", label="浸水予想区域内", markersize=5),
        Line2D([], [], marker="o", linestyle="", color="black", label="区域外", markersize=5),
    ]
    ax.legend(handles=legend + point_legend, title="想定浸水深", loc="lower left", frameon=True)

    fig.tight_layout()
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"inundation_{city_name}_nogawa_sengawa.png"
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gpkg", help="Path to 13_heritage_hazards.gpkg")
    ap.add_argument("--outdir", default="./summary_results/figures/overview")
    ap.add_argument("--cities", nargs="+", default=["国分寺", "国立"])
    args = ap.parse_args()

    gpkg = Path(args.gpkg).expanduser().resolve()
    outdir = Path(args.outdir).expanduser().resolve()

    for city in args.cities:
        if city not in CITY_FALLBACKS:
            raise SystemExit(f"Unsupported city for fallback bbox: {city}")
        plot_city(gpkg, city, outdir)


if __name__ == "__main__":
    main()
