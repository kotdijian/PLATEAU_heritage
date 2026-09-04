#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PolyCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from shapely.geometry import box


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

DEPTH_COLORS = {
    "0": "#ffffff",
    "0–0.5 m": "#cfe8ff",
    "0.5–3 m": "#6baed6",
    "3–5 m": "#2171b5",
    "5 m以上": "#08306b",
}
DEPTH_ORDER = ["0", "0–0.5 m", "0.5–3 m", "3–5 m", "5 m以上"]


def configure_fonts() -> None:
    import matplotlib
    import matplotlib.font_manager as fm

    preferred = [
        "Hiragino Sans",
        "Hiragino Kaku Gothic ProN",
        "Yu Gothic",
        "Noto Sans CJK JP",
        "Noto Sans JP",
        "IPAexGothic",
        "IPAGothic",
    ]
    installed = {f.name for f in fm.fontManager.ttflist}
    for name in preferred:
        if name in installed:
            matplotlib.rcParams["font.family"] = name
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def sanitize_filename(name: str) -> str:
    name = str(name).strip()
    name = re.sub(r"[\\/:*?\"<>|+\s]+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_") or "unnamed"


def resolve_output_dir(base_outdir: Path, city: list[str] | None, center) -> Path:
    """Route outputs into detail or municipality-specific city folders."""
    if city:
        folder = "_".join(sanitize_filename(x) for x in city)
        return base_outdir / "city" / folder
    if center:
        return base_outdir / "detail"
    return base_outdir / "overview"


def list_layer_names(path: Path) -> list[str]:
    info = gpd.list_layers(path)
    if hasattr(info, "columns") and "name" in info.columns:
        return info["name"].astype(str).tolist()

    layers = []
    for row in info:
        if isinstance(row, str):
            layers.append(row)
            continue
        try:
            if len(row) >= 1:
                layers.append(str(row[0]))
                continue
        except TypeError:
            pass
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
    name = find_layer(gpkg, ADMIN_LAYER_CANDIDATES, contains=["admin", "boundary"])
    gdf = gpd.read_file(gpkg, layer=name)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326, allow_override=True)
    return gdf.to_crs(4326)


def get_points(gpkg: Path) -> gpd.GeoDataFrame:
    name = find_layer(gpkg, POINT_LAYER_CANDIDATES, contains=["heritage", "point"])
    gdf = gpd.read_file(gpkg, layer=name)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326, allow_override=True)
    return gdf.to_crs(4326)


def get_hazard_layers(gpkg: Path) -> list[str]:
    return [x for x in list_layer_names(gpkg) if x.startswith("hazard_inundation_")]


def resolve_hazard_layers(gpkg: Path, hazard: str) -> list[str]:
    layers = get_hazard_layers(gpkg)
    if hazard in {"all", "auto"}:
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


def normalize_depth_class(value) -> str:
    s = "" if value is None else str(value).strip()
    s = s.replace("-", "–").replace("〜", "–").replace("～", "–")
    s = re.sub(r"\s+", " ", s)
    aliases = {
        "0": "0",
        "0–0.5 m": "0–0.5 m",
        "0–0.5m": "0–0.5 m",
        "0.5–3 m": "0.5–3 m",
        "0.5–3m": "0.5–3 m",
        "3–5 m": "3–5 m",
        "3–5m": "3–5 m",
        "5 m以上": "5 m以上",
        "5m以上": "5 m以上",
    }
    return aliases.get(s, "")


def a31a_rank_category(values) -> list[str]:
    result = []
    for v in values:
        try:
            rank = int(float(v))
        except Exception:
            rank = 0
        if rank <= 0:
            result.append("0")
        elif rank == 1:
            result.append("0–0.5 m")
        elif rank == 2:
            result.append("0.5–3 m")
        elif rank == 3:
            result.append("3–5 m")
        else:
            result.append("5 m以上")
    return result


def bbox_from_center(
    lat: float,
    lon: float,
    radius_km: float | None = None,
    zoom: int | None = None,
):
    if radius_km is not None:
        dlat = radius_km / 111.32
        dlon = radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.1))
        return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)

    if zoom is None:
        zoom = 16

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
        gpd.pd.concat(selected, ignore_index=True),
        crs=selected[0].crs,
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
    """Select only inundation layers that actually intersect target municipalities."""
    matched = []
    for name in candidate_layers:
        hz = gpd.read_file(gpkg, layer=name, bbox=bbox).to_crs(4326)
        if hz.empty:
            continue
        if hz.intersects(target_geometry).any():
            matched.append(name)
    return matched


def is_point_grid(hz: gpd.GeoDataFrame) -> bool:
    if hz.empty:
        return False
    types = set(hz.geom_type.dropna().astype(str))
    return bool(types) and types.issubset({"Point"})


def load_hazard(gpkg: Path, layer_name: str, bbox):
    hz = gpd.read_file(gpkg, layer=layer_name, bbox=bbox).to_crs(4326)
    if hz.empty:
        return hz

    hz = hz[hz.intersects(box(*bbox))].copy()
    if hz.empty:
        return hz

    if "depth_class_summary" in hz.columns:
        cats = hz["depth_class_summary"].map(normalize_depth_class)
        if (cats != "").any():
            hz["depth_cat"] = cats.where(cats != "", "0")
            return hz

    if "depth_rank_code" in hz.columns:
        hz["depth_cat"] = a31a_rank_category(hz["depth_rank_code"])
        return hz

    depth_col = find_depth_column(hz)
    hz["depth_cat"] = depth_category(hz[depth_col])
    return hz


def _axis_grid_spacing(values: np.ndarray) -> float | None:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return None

    # Geographic raster centers are very close to a regular grid. Rounding at
    # 1e-6 degree removes coordinate jitter while retaining metre-scale cells.
    unique = np.unique(np.round(values, 6))
    if unique.size < 2:
        return None

    diffs = np.diff(np.sort(unique))
    diffs = diffs[diffs > 1e-7]
    if diffs.size == 0:
        return None

    # Missing cells create integer multiples of the base spacing. The lower
    # cluster is therefore the stable estimate of the native cell spacing.
    q35 = float(np.quantile(diffs, 0.35))
    small = diffs[diffs <= q35 * 1.6]
    if small.size == 0:
        small = diffs
    return float(np.median(small))


def infer_point_grid_spacing(hz: gpd.GeoDataFrame) -> tuple[float, float]:
    if not is_point_grid(hz):
        raise ValueError("infer_point_grid_spacing requires Point geometry")

    x = hz.geometry.x.to_numpy(dtype=float)
    y = hz.geometry.y.to_numpy(dtype=float)
    dx = _axis_grid_spacing(x)
    dy = _axis_grid_spacing(y)

    mean_lat = float(np.nanmean(y)) if y.size else 35.7

    if dx is None and dy is not None:
        dx = dy / max(math.cos(math.radians(mean_lat)), 0.2)
    if dy is None and dx is not None:
        dy = dx * max(math.cos(math.radians(mean_lat)), 0.2)

    if dx is None:
        dx = 0.0001
    if dy is None:
        dy = 0.0001

    return float(dx), float(dy)


def plot_point_grid_cells(
    ax,
    hz: gpd.GeoDataFrame,
    alpha: float = 0.88,
    zorder: int = 2,
) -> tuple[float, float]:
    """Render point-grid inundation data as geographic grid cells, never circles."""
    dx, dy = infer_point_grid_spacing(hz)

    x = hz.geometry.x.to_numpy(dtype=float)
    y = hz.geometry.y.to_numpy(dtype=float)
    hx = dx * 0.5025
    hy = dy * 0.5025

    vertices = np.stack(
        [
            np.column_stack((x - hx, y - hy)),
            np.column_stack((x + hx, y - hy)),
            np.column_stack((x + hx, y + hy)),
            np.column_stack((x - hx, y + hy)),
        ],
        axis=1,
    )
    facecolors = [DEPTH_COLORS.get(cat, "#ffffff") for cat in hz["depth_cat"]]

    collection = PolyCollection(
        vertices,
        facecolors=facecolors,
        edgecolors="none",
        linewidths=0,
        alpha=alpha,
        antialiased=False,
        zorder=zorder,
    )
    ax.add_collection(collection)
    return dx, dy


def plot_hazard_surface(ax, hz: gpd.GeoDataFrame, alpha: float = 0.88, zorder: int = 2):
    if hz.empty:
        return
    if is_point_grid(hz):
        plot_point_grid_cells(ax, hz, alpha=alpha, zorder=zorder)
    else:
        hz.plot(
            ax=ax,
            color=hz["depth_cat"].map(DEPTH_COLORS),
            linewidth=0,
            alpha=alpha,
            zorder=zorder,
        )


def point_grid_inside_mask(
    points: gpd.GeoDataFrame,
    hz: gpd.GeoDataFrame,
) -> np.ndarray:
    if points.empty:
        return np.zeros(0, dtype=bool)
    if hz.empty:
        return np.zeros(len(points), dtype=bool)

    dx, dy = infer_point_grid_spacing(hz)
    hx = hz.geometry.x.to_numpy(dtype=float)
    hy = hz.geometry.y.to_numpy(dtype=float)
    x0 = float(np.nanmin(hx))
    y0 = float(np.nanmin(hy))

    hi = np.rint((hx - x0) / dx).astype(np.int64)
    hj = np.rint((hy - y0) / dy).astype(np.int64)

    risk = np.asarray(hz["depth_cat"].astype(str) != "0")
    risk_keys = {
        (int(i), int(j))
        for i, j, keep in zip(hi, hj, risk)
        if bool(keep)
    }

    px = points.geometry.x.to_numpy(dtype=float)
    py = points.geometry.y.to_numpy(dtype=float)
    pi = np.rint((px - x0) / dx).astype(np.int64)
    pj = np.rint((py - y0) / dy).astype(np.int64)

    nearest_x = x0 + pi * dx
    nearest_y = y0 + pj * dy
    close = (
        (np.abs(px - nearest_x) <= dx * 0.55)
        & (np.abs(py - nearest_y) <= dy * 0.55)
    )

    return np.asarray(
        [
            bool(c) and (int(i), int(j)) in risk_keys
            for c, i, j in zip(close, pi, pj)
        ],
        dtype=bool,
    )


def points_inside_hazard_mask(
    points: gpd.GeoDataFrame,
    hz: gpd.GeoDataFrame,
) -> np.ndarray:
    if points.empty:
        return np.zeros(0, dtype=bool)
    if hz.empty:
        return np.zeros(len(points), dtype=bool)

    if is_point_grid(hz):
        return point_grid_inside_mask(points, hz)

    union = hz.union_all()
    return points.geometry.intersects(union).to_numpy(dtype=bool)


def plot_points_inout(
    ax,
    points: gpd.GeoDataFrame,
    hazard: gpd.GeoDataFrame,
    markersize: int = 12,
):
    if points.empty:
        return False, False

    inside_mask = points_inside_hazard_mask(points, hazard)
    pts_in = points.iloc[np.flatnonzero(inside_mask)]
    pts_out = points.iloc[np.flatnonzero(~inside_mask)]

    has_in = not pts_in.empty
    has_out = not pts_out.empty

    if has_out:
        pts_out.plot(ax=ax, markersize=markersize, color="black", zorder=6)
    if has_in:
        pts_in.plot(ax=ax, markersize=markersize + 4, color="red", zorder=7)
    return has_in, has_out


def title_from_layer(layer_name: str) -> str:
    return layer_name.replace("hazard_inundation_", "")


def draw_common(
    ax,
    admin_clip: gpd.GeoDataFrame,
    city_gdf: gpd.GeoDataFrame | None,
    bbox,
):
    if not admin_clip.empty:
        admin_clip.boundary.plot(
            ax=ax,
            linewidth=0.8,
            edgecolor="0.55",
            zorder=4,
        )
    if city_gdf is not None and not city_gdf.empty:
        city_gdf.boundary.plot(
            ax=ax,
            linewidth=1.4,
            edgecolor="0.25",
            zorder=5,
        )
    ax.set_xlim(bbox[0], bbox[2])
    ax.set_ylim(bbox[1], bbox[3])
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal", adjustable="box")


def add_legend(
    ax,
    show_points_inside: bool,
    show_points_outside: bool,
    title="想定浸水深",
):
    handles = [
        Patch(facecolor=DEPTH_COLORS[l], edgecolor="0.6", label=l)
        for l in DEPTH_ORDER
    ]
    if show_points_inside:
        handles.append(
            Line2D(
                [], [],
                marker="o",
                linestyle="",
                color="red",
                label="浸水予想区域内",
                markersize=5,
            )
        )
    if show_points_outside:
        handles.append(
            Line2D(
                [], [],
                marker="o",
                linestyle="",
                color="black",
                label="区域外",
                markersize=5,
            )
        )
    ax.legend(
        handles=handles,
        title=title,
        loc="lower left",
        frameon=True,
        fontsize=8,
        title_fontsize=9,
    )


def plot_single(
    gpkg: Path,
    outdir: Path,
    bbox,
    admin_clip,
    city_gdf,
    points,
    layer_name: str,
    display_label: str,
    file_label: str,
):
    hz = load_hazard(gpkg, layer_name, bbox)
    if hz.empty:
        print(f"[SKIP] {layer_name}: no features in bbox")
        return None

    fig, ax = plt.subplots(figsize=(9, 8))
    plot_hazard_surface(ax, hz, alpha=0.88, zorder=2)

    show_in, show_out = plot_points_inout(ax, points, hz)
    draw_common(ax, admin_clip, city_gdf, bbox)
    ax.set_title(f"{display_label}：{title_from_layer(layer_name)}")
    add_legend(ax, show_in, show_out)

    fig.tight_layout()
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / (
        f"inundation_{sanitize_filename(file_label)}_"
        f"{sanitize_filename(title_from_layer(layer_name))}.png"
    )
    fig.savefig(out, dpi=DEFAULT_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out}")
    return out


def plot_combined(
    gpkg: Path,
    outdir: Path,
    bbox,
    admin_clip,
    city_gdf,
    points,
    layer_names: list[str],
    display_label: str,
    file_label: str,
):
    hazard_frames = []
    for layer_name in layer_names:
        hz = load_hazard(gpkg, layer_name, bbox)
        if not hz.empty:
            hz["hazard_name"] = title_from_layer(layer_name)
            hazard_frames.append(hz)

    if not hazard_frames:
        print("[SKIP] combined: no hazard features in bbox")
        return None

    fig, ax = plt.subplots(figsize=(9, 8))

    inside_any = np.zeros(len(points), dtype=bool)
    for hz in hazard_frames:
        plot_hazard_surface(ax, hz, alpha=0.66, zorder=2)
        if not is_point_grid(hz):
            hz.boundary.plot(
                ax=ax,
                linewidth=0.4,
                edgecolor="0.45",
                alpha=0.6,
                zorder=3,
            )
        if not points.empty:
            inside_any |= points_inside_hazard_mask(points, hz)

    if not points.empty:
        pts_in = points.iloc[np.flatnonzero(inside_any)]
        pts_out = points.iloc[np.flatnonzero(~inside_any)]
        if not pts_out.empty:
            pts_out.plot(ax=ax, markersize=12, color="black", zorder=6)
        if not pts_in.empty:
            pts_in.plot(ax=ax, markersize=16, color="red", zorder=7)
        show_in = not pts_in.empty
        show_out = not pts_out.empty
    else:
        show_in = show_out = False

    draw_common(ax, admin_clip, city_gdf, bbox)
    ax.set_title(f"{display_label}：浸水予想区域（重複表示）")
    add_legend(ax, show_in, show_out)

    fig.tight_layout()
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"inundation_{sanitize_filename(file_label)}_combined.png"
    fig.savefig(out, dpi=DEFAULT_DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out}")
    return out


def parse_args():
    p = argparse.ArgumentParser(
        description="Render inundation maps from a heritage hazards GPKG."
    )
    p.add_argument("gpkg", help="Path to the input GPKG")
    p.add_argument(
        "--outdir",
        default=DEFAULT_OUTDIR,
        help=(
            "Base output directory. City mode writes to "
            "<outdir>/city/<municipality-folder>/."
        ),
    )
    p.add_argument(
        "--city",
        nargs="+",
        help="City / ward / municipality name(s) to define the map extent",
    )
    p.add_argument(
        "--center",
        nargs=2,
        type=float,
        metavar=("LAT", "LON"),
        help="Center point as latitude longitude",
    )
    p.add_argument("--zoom", type=int, help="Approximate zoom level for center mode")
    p.add_argument("--radius-km", type=float, help="Radius in km for center mode")
    p.add_argument(
        "--hazard",
        default="auto",
        help='Inundation layer selector: auto | all | "流域名" | full layer name',
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--combine",
        action="store_true",
        help="Combine matching hazard layers into one map",
    )
    group.add_argument(
        "--separate",
        action="store_true",
        help="Render one map per matching hazard layer",
    )
    p.add_argument(
        "--list-hazards",
        action="store_true",
        help="List available hazard layers and exit",
    )
    return p.parse_args()


def main():
    args = parse_args()
    configure_fonts()

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
        display_label = "・".join(args.city)
        file_label = "_".join(args.city)
        target_geometry = city_gdf.union_all()
    else:
        lat, lon = args.center
        bbox = bbox_from_center(
            lat,
            lon,
            radius_km=args.radius_km,
            zoom=args.zoom,
        )
        city_gdf = None
        target_geometry = box(*bbox)
        display_label = f"center {lat:.5f}, {lon:.5f}"
        file_label = f"center_{lat:.5f}_{lon:.5f}"

    outdir = resolve_output_dir(base_outdir, args.city, args.center)

    admin_clip = clip_layers_to_bbox(admin, bbox)
    if args.city:
        points_clip = points[points.intersects(target_geometry)].copy()
    else:
        points_clip = clip_layers_to_bbox(points, bbox)

    if args.hazard == "auto":
        candidates = all_hazard_layers
    else:
        candidates = resolve_hazard_layers(gpkg, args.hazard)

    if args.city:
        selected_layers = auto_select_hazards_for_geometry(
            gpkg,
            bbox,
            target_geometry,
            candidates,
        )
    else:
        selected_layers = auto_select_hazards(
            gpkg,
            bbox,
            candidates,
        )

    if not selected_layers:
        raise SystemExit("No inundation layers intersect the specified extent.")

    mode_separate = args.separate or not args.combine

    print(f"Base extent: {display_label}")
    print(f"Output directory: {outdir}")
    print(f"Selected hazard layers: {len(selected_layers)}")
    for layer_name in selected_layers:
        print(f"  - {layer_name}")

    if mode_separate:
        for layer_name in selected_layers:
            plot_single(
                gpkg,
                outdir,
                bbox,
                admin_clip,
                city_gdf,
                points_clip,
                layer_name,
                display_label,
                file_label,
            )
    else:
        plot_combined(
            gpkg,
            outdir,
            bbox,
            admin_clip,
            city_gdf,
            points_clip,
            selected_layers,
            display_label,
            file_label,
        )


if __name__ == "__main__":
    main()
