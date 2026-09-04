#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from shapely.geometry import Point, box


ADMIN_LAYER_CANDIDATES = [
    "admin_boundary_n03_2024",
]
POINT_LAYER_CANDIDATES = [
    "heritage_buildings_point",
    "heritage_points",
    "heritage_source_points",
]
ADMIN_NAME_COL_CANDIDATES = [
    "N03_004", "N03_003", "N03_002", "city_name", "name", "municipality"
]
DEPTH_COL_CANDIDATES = [
    "inundation_depth_m",
    "想定浸水深",
    "max_depth",
    "depth",
]

DEFAULT_OUTDIR = "./summary_results/figures"
DEFAULT_DPI = 200


def resolve_output_dir(base_outdir: Path, city: list[str] | None, center) -> Path:
    """Route outputs into overview / detail / city.

    - overview: reserved for summary-wide maps from render_summary_maps.py
    - detail: center-specified inundation maps
    - city: municipality-specified inundation maps
    """
    if city:
        return base_outdir / "city"
    if center:
        return base_outdir / "detail"
    return base_outdir / "overview"


def list_layer_names(path: Path) -> list[str]:
    info = gpd.list_layers(path)
    if hasattr(info, "columns") and "name" in info.columns:
        return info["name"].astype(str).tolist()
    layers = []
    for row in info:
        if isinstance(row, (list, tuple)) and row:
            layers.append(str(row[0]))
        else:
            layers.append(str(row))
    return layers


def find_layer(path: Path, candidates: list[str], contains: list[str] | None = None) -> str:
    layers = list_layer_names(path)
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


def get_admin(gpkg: Path) -> gpd.GeoDataFrame:
    layer = find_layer(gpkg, ADMIN_LAYER_CANDIDATES, contains=["admin", "boundary"])
    return gpd.read_file(gpkg, layer=layer).to_crs(4326)


def get_points(gpkg: Path) -> gpd.GeoDataFrame:
    layer = find_layer(gpkg, POINT_LAYER_CANDIDATES, contains=["heritage", "point"])
    return gpd.read_file(gpkg, layer=layer).to_crs(4326)


def get_hazard_layers(gpkg: Path) -> list[str]:
    return [x for x in list_layer_names(gpkg) if x.startswith("hazard_inundation_")]


def resolve_hazard_layers(gpkg: Path, hazard: str) -> list[str]:
    layers = get_hazard_layers(gpkg)
    if hazard == "all":
        return layers
    if hazard == "auto":
        return layers
    h = hazard.strip()
    if h in layers:
        return [h]
    prefixed = f"hazard_inundation_{h}"
    if prefixed in layers:
        return [prefixed]
    matched = [x for x in layers if h in x]
    if not matched:
        raise RuntimeError(f"No inundation layer matches: {hazard}")
    return matched


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


def depth_category(values) -> list[str]:
    cats = []
    for v in values:
        try:
            x = float(v)
        except Exception:
            x = 0.0
        if x <= 0:
            cats.append("0")
        elif x <= 0.5:
            cats.append("0–0.5 m")
        elif x <= 3.0:
            cats.append("0.5–3 m")
        elif x <= 5.0:
            cats.append("3–5 m")
        else:
            cats.append("5 m以上")
    return cats


DEPTH_COLORS = {
    "0": "#ffffff",
    "0–0.5 m": "#cfe8ff",
    "0.5–3 m": "#6baed6",
    "3–5 m": "#2171b5",
    "5 m以上": "#08306b",
}
DEPTH_ORDER = ["0", "0–0.5 m", "0.5–3 m", "3–5 m", "5 m以上"]


def sanitize_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[\\/:*?\"<>| ]+", "_", name)
    name = name.replace("__", "_")
    return name.strip("_")


def bbox_from_center(lat: float, lon: float, radius_km: float | None = None, zoom: int | None = None):
    if radius_km is not None:
        dlat = radius_km / 111.32
        dlon = radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.1))
        return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)

    if zoom is None:
        zoom = 16

    # Approximate WebMercator tile span converted to degrees around the center.
    # We use a visual window roughly ~2 tiles wide/high to make a readable map.
    meters_per_pixel = 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)
    width_px = 1024
    height_px = 1024
    half_width_m = meters_per_pixel * width_px / 2
    half_height_m = meters_per_pixel * height_px / 2
    dlat = half_height_m / 111320.0
    dlon = half_width_m / (111320.0 * max(math.cos(math.radians(lat)), 0.1))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def padded_bounds(bounds, pad_ratio: float = 0.08, min_pad_deg: float = 0.003):
    minx, miny, maxx, maxy = bounds
    padx = max((maxx - minx) * pad_ratio, min_pad_deg)
    pady = max((maxy - miny) * pad_ratio, min_pad_deg)
    return (minx - padx, miny - pady, maxx + padx, maxy + pady)


def bbox_from_city(admin: gpd.GeoDataFrame, city_names: list[str]):
    col = find_name_column(admin)
    selected = []
    for city in city_names:
        mask = admin[col].astype(str).str.contains(city, na=False)
        g = admin[mask].copy()
        if g.empty:
            raise RuntimeError(f"City not found in admin layer: {city}")
        selected.append(g)
    merged = gpd.GeoDataFrame(
        gpd.pd.concat(selected, ignore_index=True), crs=selected[0].crs
    )
    geom = merged.union_all()
    return padded_bounds(geom.bounds), merged


def clip_layers_to_bbox(gdf: gpd.GeoDataFrame, bbox):
    rect = box(*bbox)
    return gdf[gdf.intersects(rect)].copy()


def auto_select_hazards(gpkg: Path, bbox, candidate_layers: list[str]) -> list[str]:
    rect = box(*bbox)
    matched = []
    for name in candidate_layers:
        hz = gpd.read_file(gpkg, layer=name, bbox=bbox).to_crs(4326)
        if hz.empty:
            continue
        hz = hz[hz.intersects(rect)]
        if not hz.empty:
            matched.append(name)
    return matched


def auto_select_hazards_for_geometry(
    gpkg: Path,
    bbox,
    target_geometry,
    candidate_layers: list[str],
) -> list[str]:
    """Select only inundation layers that actually intersect the target geometry."""
    matched = []
    for name in candidate_layers:
        hz = gpd.read_file(gpkg, layer=name, bbox=bbox).to_crs(4326)
        if hz.empty:
            continue
        if hz.intersects(target_geometry).any():
            matched.append(name)
    return matched


def load_hazard(gpkg: Path, layer_name: str, bbox):
    hz = gpd.read_file(gpkg, layer=layer_name, bbox=bbox).to_crs(4326)
    if hz.empty:
        return hz
    hz = hz[hz.intersects(box(*bbox))].copy()
    if hz.empty:
        return hz
    depth_col = find_depth_column(hz)
    hz["depth_cat"] = depth_category(hz[depth_col])
    return hz


def plot_points_inout(ax, points: gpd.GeoDataFrame, hazard_union, markersize=12):
    if points.empty:
        return False, False
    inside_mask = points.geometry.within(hazard_union)
    pts_in = points[inside_mask]
    pts_out = points[~inside_mask]
    has_in = not pts_in.empty
    has_out = not pts_out.empty
    if has_out:
        pts_out.plot(ax=ax, markersize=markersize, color="black", zorder=6)
    if has_in:
        pts_in.plot(ax=ax, markersize=markersize + 4, color="red", zorder=7)
    return has_in, has_out


def title_from_layer(layer_name: str) -> str:
    return layer_name.replace("hazard_inundation_", "")


def draw_common(ax, admin_clip: gpd.GeoDataFrame, city_gdf: gpd.GeoDataFrame | None, bbox):
    if not admin_clip.empty:
        admin_clip.boundary.plot(ax=ax, linewidth=0.8, edgecolor="0.55", zorder=4)
    if city_gdf is not None and not city_gdf.empty:
        city_gdf.boundary.plot(ax=ax, linewidth=1.4, edgecolor="0.25", zorder=5)
    ax.set_xlim(bbox[0], bbox[2])
    ax.set_ylim(bbox[1], bbox[3])
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")


def add_legend(ax, show_points_inside: bool, show_points_outside: bool, title="想定浸水深"):
    handles = [Patch(facecolor=DEPTH_COLORS[l], edgecolor="0.6", label=l) for l in DEPTH_ORDER]
    if show_points_inside:
        handles.append(Line2D([], [], marker="o", linestyle="", color="red", label="浸水予想区域内", markersize=5))
    if show_points_outside:
        handles.append(Line2D([], [], marker="o", linestyle="", color="black", label="区域外", markersize=5))
    ax.legend(handles=handles, title=title, loc="lower left", frameon=True)


def plot_single(gpkg: Path, outdir: Path, bbox, admin_clip, city_gdf, points, layer_name: str, base_label: str):
    hz = load_hazard(gpkg, layer_name, bbox)
    if hz.empty:
        print(f"[SKIP] {layer_name}: no features in bbox")
        return None

    fig, ax = plt.subplots(figsize=(8, 8))
    hz.plot(ax=ax, color=hz["depth_cat"].map(DEPTH_COLORS), linewidth=0, alpha=0.88, zorder=2)

    show_in, show_out = plot_points_inout(ax, points, hz.union_all())
    draw_common(ax, admin_clip, city_gdf, bbox)
    ax.set_title(f"{base_label}：{title_from_layer(layer_name)}")
    add_legend(ax, show_in, show_out)

    fig.tight_layout()
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"inundation_{sanitize_filename(base_label)}_{sanitize_filename(title_from_layer(layer_name))}.png"
    fig.savefig(out, dpi=DEFAULT_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out}")
    return out


def plot_combined(gpkg: Path, outdir: Path, bbox, admin_clip, city_gdf, points, layer_names: list[str], base_label: str):
    hazard_frames = []
    for layer_name in layer_names:
        hz = load_hazard(gpkg, layer_name, bbox)
        if not hz.empty:
            hz["hazard_name"] = title_from_layer(layer_name)
            hazard_frames.append(hz)

    if not hazard_frames:
        print("[SKIP] combined: no hazard features in bbox")
        return None

    hz_all = gpd.GeoDataFrame(gpd.pd.concat(hazard_frames, ignore_index=True), crs=hazard_frames[0].crs)

    fig, ax = plt.subplots(figsize=(8, 8))
    hz_all.plot(ax=ax, color=hz_all["depth_cat"].map(DEPTH_COLORS), linewidth=0, alpha=0.65, zorder=2)

    # boundary overlays for each hazard layer to hint overlapping extents
    for hz in hazard_frames:
        hz.boundary.plot(ax=ax, linewidth=0.4, edgecolor="0.45", alpha=0.6, zorder=3)

    show_in, show_out = plot_points_inout(ax, points, hz_all.union_all())
    draw_common(ax, admin_clip, city_gdf, bbox)
    ax.set_title(f"{base_label}：浸水予想区域（重複表示）")
    add_legend(ax, show_in, show_out)

    fig.tight_layout()
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"inundation_{sanitize_filename(base_label)}_combined.png"
    fig.savefig(out, dpi=DEFAULT_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out}")
    return out


def parse_args():
    p = argparse.ArgumentParser(
        description="Render inundation maps from a heritage hazards GPKG."
    )
    p.add_argument("gpkg", help="Path to the input GPKG")
    p.add_argument("--outdir", default=DEFAULT_OUTDIR, help="Base output directory (subfolders overview/detail/city are created automatically)")
    p.add_argument(
        "--city",
        nargs="+",
        help="City / ward / municipality name(s) to define the map extent"
    )
    p.add_argument(
        "--center",
        nargs=2,
        type=float,
        metavar=("LAT", "LON"),
        help="Center point as latitude longitude"
    )
    p.add_argument("--zoom", type=int, help="Approximate zoom level for center mode")
    p.add_argument("--radius-km", type=float, help="Radius in km for center mode")
    p.add_argument(
        "--hazard",
        default="auto",
        help='Inundation layer selector: auto | all | "流域名" | full layer name'
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument("--combine", action="store_true", help="Combine matching hazard layers into one map")
    group.add_argument("--separate", action="store_true", help="Render one map per matching hazard layer")
    p.add_argument("--list-hazards", action="store_true", help="List available hazard layers and exit")
    return p.parse_args()


def main():
    args = parse_args()
    gpkg = Path(args.gpkg).expanduser().resolve()
    base_outdir = Path(args.outdir).expanduser().resolve()

    if not gpkg.exists():
        raise SystemExit(f"GPKG not found: {gpkg}")

    all_hazard_layers = get_hazard_layers(gpkg)

    if args.list_hazards:
        for name in all_hazard_layers:
            print(name)
        return

    if not args.city and not args.center:
        raise SystemExit("Specify either --city ... or --center LAT LON")

    if args.city and args.center:
        raise SystemExit("Use either --city or --center, not both")

    admin = get_admin(gpkg)
    points = get_points(gpkg)

    if args.city:
        bbox, city_gdf = bbox_from_city(admin, args.city)
        base_label = "+".join(args.city)
        target_geometry = city_gdf.union_all()
    else:
        lat, lon = args.center
        bbox = bbox_from_center(lat, lon, radius_km=args.radius_km, zoom=args.zoom)
        city_gdf = None
        target_geometry = box(*bbox)
        base_label = f"center_{lat:.5f}_{lon:.5f}"

    # Route output automatically:
    #   --city   -> figures/city
    #   --center -> figures/detail
    outdir = resolve_output_dir(base_outdir, args.city, args.center)

    admin_clip = clip_layers_to_bbox(admin, bbox)
    if args.city:
        # City maps contain cultural-property points inside the selected municipality.
        points_clip = points[points.intersects(target_geometry)].copy()
    else:
        points_clip = clip_layers_to_bbox(points, bbox)

    if args.hazard == "auto":
        candidates = all_hazard_layers
    else:
        candidates = resolve_hazard_layers(gpkg, args.hazard)

    if args.city:
        # Do not select neighboring basins merely because they intersect the padded
        # display bbox; require an actual intersection with the municipality polygon.
        selected_layers = auto_select_hazards_for_geometry(
            gpkg, bbox, target_geometry, candidates
        )
    else:
        selected_layers = auto_select_hazards(gpkg, bbox, candidates)

    if not selected_layers:
        raise SystemExit("No inundation layers intersect the specified extent.")

    mode_separate = args.separate or not args.combine

    print(f"Base extent: {base_label}")
    print(f"Output directory: {outdir}")
    print(f"Selected hazard layers: {len(selected_layers)}")
    for layer_name in selected_layers:
        print(f"  - {layer_name}")

    if mode_separate:
        for layer_name in selected_layers:
            plot_single(gpkg, outdir, bbox, admin_clip, city_gdf, points_clip, layer_name, base_label)
    else:
        plot_combined(gpkg, outdir, bbox, admin_clip, city_gdf, points_clip, selected_layers, base_label)


if __name__ == "__main__":
    main()
