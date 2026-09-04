#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
render_city_hazard_focus.py

用途:
- 浸水区域を除く全災害レイヤを対象にする
- city mode: 自治体単位の災害図を作成する
- center mode: 任意中心座標の detail 災害図を作成する
- 対象範囲内に該当ハザード地物が存在しない場合は画像出力をスキップする

想定入力:
- 13_heritage_hazards.gpkg / 13_heritage_hazards_a31a.gpkg など
- 行政界レイヤ: admin_boundary_n03_2024
- 文化財 point レイヤ: heritage_buildings_point / heritage_points / heritage_source_points など
- 文化財 footprint レイヤ: heritage_buildings_footprint / heritage_buildings_footprints など

出力:
- city mode:   summary_results/figures/city/<自治体名>/
- center mode: summary_results/figures/detail/<地点名>/hazard/
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
import numpy as np
import pyogrio
from matplotlib.cm import ScalarMappable
from matplotlib.colors import BoundaryNorm, Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
from shapely.geometry import box

try:
    import contextily as ctx
except Exception:
    ctx = None


SEISMIC_LABELS = ["5弱未満", "5弱", "5強", "6弱", "6強以上"]
SEISMIC_BOUNDS = [-10, 4.5, 5.0, 5.5, 6.0, 10]
SEISMIC_COLORS = ["#F0F921", "#F89640", "#CC4778", "#9C179E", "#0D0887"]

# Canonical Summary Results detail centers: (lat, lon)
DETAIL_CENTERS = {
    "東京駅": (35.68126, 139.76671),
    "東京都立上野高校": (35.7186246, 139.7698412),
    "JR両国駅": (35.6957371, 139.7936379),
    "東京メトロ田原町駅": (35.70984, 139.79076),
}


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


def bbox_from_center(lat: float, lon: float, radius_km: float = 0.8):
    """Return a WGS84 bbox around a center point."""
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.1))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


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

def is_seismic_layer(hazard_name: str) -> bool:
    return "seismic" in hazard_name.lower()


def add_seismic_legend(ax) -> None:
    handles = [
        Patch(facecolor=color, edgecolor="none", label=label)
        for color, label in zip(SEISMIC_COLORS, SEISMIC_LABELS)
    ]
    leg = ax.legend(
        handles=handles,
        title="想定震度",
        loc="upper right",
        fontsize=8,
        title_fontsize=9,
        frameon=True,
    )
    ax.add_artist(leg)


def add_compact_numeric_colorbar(ax, values, cmap_name: str, label: str) -> None:
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return

    vmin = float(np.nanmin(vals))
    vmax = float(np.nanmax(vals))
    if math.isclose(vmin, vmax):
        span = max(abs(vmin) * 0.01, 0.5)
        vmin -= span
        vmax += span

    cax = inset_axes(
        ax,
        width="2.7%",
        height="30%",
        loc="upper right",
        borderpad=1.2,
    )
    sm = ScalarMappable(norm=Normalize(vmin=vmin, vmax=vmax), cmap=cmap_name)
    sm.set_array([])
    cb = ax.figure.colorbar(sm, cax=cax)
    cb.ax.tick_params(labelsize=7)
    cb.set_label(label, fontsize=8)


def plot_hazard_surface(
    ax,
    hazard_name: str,
    hazard: gpd.GeoDataFrame,
    numeric_col: Optional[str],
) -> None:
    if hazard.empty:
        return

    if numeric_col and numeric_col in hazard.columns:
        numeric = gpd.pd.to_numeric(hazard[numeric_col], errors="coerce")

        if is_seismic_layer(hazard_name):
            cmap = plt.matplotlib.colors.ListedColormap(SEISMIC_COLORS)
            norm = BoundaryNorm(SEISMIC_BOUNDS, cmap.N)
            hazard.assign(_plot_value=numeric).plot(
                ax=ax,
                column="_plot_value",
                cmap=cmap,
                norm=norm,
                linewidth=0,
                edgecolor="none",
                alpha=0.58,
                legend=False,
                zorder=3,
            )
            add_seismic_legend(ax)
            return

        cmap_name = "plasma_r" if "liquefaction" in hazard_name.lower() else "viridis"
        hazard.assign(_plot_value=numeric).plot(
            ax=ax,
            column="_plot_value",
            cmap=cmap_name,
            linewidth=0,
            edgecolor="none",
            alpha=0.55,
            legend=False,
            zorder=3,
        )
        add_compact_numeric_colorbar(
            ax,
            numeric.to_numpy(),
            cmap_name,
            numeric_col,
        )
        return

    if is_area_like(hazard):
        hazard.plot(
            ax=ax,
            color="#69b3a2",
            edgecolor="#2f6f62",
            linewidth=0.4,
            alpha=0.45,
            zorder=3,
        )
    else:
        hazard.plot(
            ax=ax,
            color="#2f6f62",
            markersize=12,
            alpha=0.75,
            zorder=3,
        )


def add_heritage_legend(ax, seismic_legend_present: bool = False) -> None:
    handles = [
        Line2D(
            [0], [0],
            marker="o", linestyle="",
            color="#8f8f8f",
            markersize=6,
            label="文化財 point（領域外）",
        ),
        Line2D(
            [0], [0],
            marker="o", linestyle="",
            color="#d7301f",
            markersize=6,
            label="文化財 point（領域内）",
        ),
        Line2D(
            [0], [0],
            marker="s", linestyle="",
            markerfacecolor="#6f6f6f",
            markeredgecolor="#2b2b2b",
            markersize=7,
            label="文化財 building footprint",
        ),
    ]
    ax.legend(
        handles=handles,
        loc="lower left",
        fontsize=8,
        frameon=True,
    )


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
    # Keep the map axes stable. No GeoPandas auto colorbar is allowed to resize it.
    fig, ax = plt.subplots(figsize=(9.0, 7.4))

    minx, miny, maxx, maxy = admin_city.total_bounds
    dx = maxx - minx
    dy = maxy - miny
    pad_x = max(dx * 0.08, 0.003)
    pad_y = max(dy * 0.08, 0.003)
    plot_bounds = (minx - pad_x, miny - pad_y, maxx + pad_x, maxy + pad_y)

    ax.set_xlim(plot_bounds[0], plot_bounds[2])
    ax.set_ylim(plot_bounds[1], plot_bounds[3])

    add_gsi_basemap(ax, crs="EPSG:4326", zoom=zoom)

    plot_hazard_surface(ax, hazard_name, hazard, numeric_col)

    # points: outside first, footprints above points, inside points highlighted last
    points_out, points_in = classify_points_by_hazard(points, hazard)

    if not points_out.empty:
        points_out.plot(
            ax=ax,
            color="#8f8f8f",
            markersize=12,
            alpha=0.85,
            zorder=4,
        )

    if not footprints.empty:
        footprints.plot(
            ax=ax,
            facecolor="#6f6f6f",
            edgecolor="#2b2b2b",
            linewidth=0.35,
            alpha=0.85,
            zorder=5,
        )

    if not points_in.empty:
        points_in.plot(
            ax=ax,
            color="#d7301f",
            markersize=16,
            alpha=0.95,
            zorder=6,
        )

    # city boundary on top
    admin_city.boundary.plot(
        ax=ax,
        color="black",
        linewidth=1.35,
        zorder=7,
    )

    title = f"{city_name}｜{hazard_name}"
    if subtitle:
        title += f"\n{subtitle}"
    ax.set_title(title, fontsize=13, pad=9)

    add_heritage_legend(ax, seismic_legend_present=is_seismic_layer(hazard_name))

    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal", adjustable="box")

    # Fixed margins prevent colorbar/legend from creating the tall blank canvas
    # seen with GeoPandas legend=True.
    fig.subplots_adjust(left=0.035, right=0.985, bottom=0.04, top=0.90)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=220, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def plot_center_hazard(
    out_png: Path,
    label: str,
    hazard_name: str,
    hazard: gpd.GeoDataFrame,
    bbox,
    admin_clip: gpd.GeoDataFrame,
    points: gpd.GeoDataFrame,
    footprints: gpd.GeoDataFrame,
    numeric_col: Optional[str],
    subtitle: Optional[str] = None,
    zoom: int = 16,
) -> None:
    """Render a non-inundation hazard around an arbitrary detail center."""
    fig, ax = plt.subplots(figsize=(9.0, 7.4))
    ax.set_xlim(bbox[0], bbox[2])
    ax.set_ylim(bbox[1], bbox[3])

    add_gsi_basemap(ax, crs="EPSG:4326", zoom=zoom)
    plot_hazard_surface(ax, hazard_name, hazard, numeric_col)

    points_out, points_in = classify_points_by_hazard(points, hazard)
    if not points_out.empty:
        points_out.plot(
            ax=ax, color="#8f8f8f", markersize=12, alpha=0.85, zorder=4
        )
    if not footprints.empty:
        footprints.plot(
            ax=ax,
            facecolor="#6f6f6f",
            edgecolor="#2b2b2b",
            linewidth=0.35,
            alpha=0.85,
            zorder=5,
        )
    if not points_in.empty:
        points_in.plot(
            ax=ax, color="#d7301f", markersize=16, alpha=0.95, zorder=6
        )
    if not admin_clip.empty:
        admin_clip.boundary.plot(
            ax=ax, color="#555555", linewidth=0.7, alpha=0.9, zorder=7
        )

    title = f"{label}｜{hazard_name}"
    if subtitle:
        title += f"\n{subtitle}"
    ax.set_title(title, fontsize=13, pad=9)
    add_heritage_legend(ax, seismic_legend_present=is_seismic_layer(hazard_name))

    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal", adjustable="box")
    fig.subplots_adjust(left=0.035, right=0.985, bottom=0.04, top=0.90)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=220, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def process_center(
    path: Path,
    label: str,
    lat: float,
    lon: float,
    out_root: Path,
    radius_km: float = 0.8,
    zoom: int = 16,
) -> tuple[int, int]:
    bbox = bbox_from_center(lat, lon, radius_km)
    rect = box(*bbox)

    admin = load_admin(path)
    admin_clip = admin[admin.intersects(rect)].copy()
    points = load_points(path, bbox)
    footprints = load_footprints(path, bbox)
    if not points.empty:
        points = points[points.intersects(rect)].copy()
    if not footprints.empty:
        footprints = footprints[footprints.intersects(rect)].copy()

    hazard_layers = discover_hazard_layers(path)
    detail_dir = out_root / sanitize_filename(label) / "hazard"
    detail_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    skipped = 0
    print(f"\n=== {label} ({lat:.7f}, {lon:.7f}) ===")
    print(f"hazard layers discovered: {len(hazard_layers)}")

    for layer in hazard_layers:
        try:
            hz = read_layer_bbox(path, layer, bbox)
        except Exception as e:
            print(f"  SKIP read error: {layer} -> {e}")
            skipped += 1
            continue
        if not hz.empty:
            hz = hz[hz.intersects(rect)].copy()
        if hz.empty:
            skipped += 1
            continue

        if "scenario" in hz.columns:
            scenarios = [
                x for x in hz["scenario"].dropna().astype(str).unique().tolist()
                if x.strip()
            ]
            if len(scenarios) > 1:
                for scenario in sorted(scenarios):
                    sub = hz[hz["scenario"].astype(str) == scenario].copy()
                    if sub.empty:
                        continue
                    numeric_col = infer_numeric_column(layer, sub)
                    hz_name = layer.replace("hazard_", "")
                    out_png = detail_dir / (
                        f"{sanitize_filename(hz_name)}__{sanitize_filename(scenario)}.png"
                    )
                    plot_center_hazard(
                        out_png, label, hz_name, sub, bbox, admin_clip,
                        points, footprints, numeric_col,
                        subtitle=f"scenario: {scenario}", zoom=zoom,
                    )
                    print(f"  OK {out_png.name}")
                    generated += 1
                continue

        numeric_col = infer_numeric_column(layer, hz)
        hz_name = layer.replace("hazard_", "")
        out_png = detail_dir / f"{sanitize_filename(hz_name)}.png"
        plot_center_hazard(
            out_png, label, hz_name, hz, bbox, admin_clip,
            points, footprints, numeric_col, subtitle=None, zoom=zoom,
        )
        print(f"  OK {out_png.name}")
        generated += 1

    return generated, skipped


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
    p = argparse.ArgumentParser(
        description="Render non-inundation hazard focus maps for municipalities or detail centers."
    )
    p.add_argument("gpkg", help="Input GPKG path")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--cities", nargs="+", help="City names, e.g. 国分寺 国立")
    mode.add_argument(
        "--center", nargs=2, type=float, metavar=("LAT", "LON"),
        help="Detail center as latitude longitude",
    )
    mode.add_argument(
        "--detail-defaults", action="store_true",
        help="Render the four canonical Summary Results detail centers",
    )
    p.add_argument("--label", help="Display/output label for --center mode")
    p.add_argument("--radius-km", type=float, default=0.8, help="Detail radius in km (default: 0.8)")
    p.add_argument("--zoom", type=int, default=16, help="GSI basemap zoom for detail mode (default: 16)")
    p.add_argument(
        "--outdir",
        default=None,
        help=(
            "Output root. Defaults to summary_results/figures/city for city mode "
            "and summary_results/figures/detail for center modes."
        ),
    )
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    configure_fonts()

    gpkg = Path(args.gpkg).expanduser().resolve()
    if not gpkg.is_file():
        raise SystemExit(f"Input GPKG not found: {gpkg}")

    total_generated = 0
    total_skipped = 0

    if args.cities:
        out_root = Path(args.outdir or "summary_results/figures/city").expanduser().resolve()
        for city in args.cities:
            gen, skip = process_city(gpkg, city, out_root)
            total_generated += gen
            total_skipped += skip
    elif args.center:
        out_root = Path(args.outdir or "summary_results/figures/detail").expanduser().resolve()
        lat, lon = args.center
        label = args.label or f"center_{lat:.5f}_{lon:.5f}"
        gen, skip = process_center(
            gpkg, label, lat, lon, out_root,
            radius_km=args.radius_km, zoom=args.zoom,
        )
        total_generated += gen
        total_skipped += skip
    else:
        out_root = Path(args.outdir or "summary_results/figures/detail").expanduser().resolve()
        for label, (lat, lon) in DETAIL_CENTERS.items():
            gen, skip = process_center(
                gpkg, label, lat, lon, out_root,
                radius_km=args.radius_km, zoom=args.zoom,
            )
            total_generated += gen
            total_skipped += skip

    print("\nDONE")
    print(f"generated: {total_generated}")
    print(f"skipped  : {total_skipped}")
    print(f"output   : {out_root}")


if __name__ == "__main__":
    main()
