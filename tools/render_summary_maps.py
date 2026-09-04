#!/usr/bin/env python3
"""
Render Summary Results maps from 13_heritage_hazards.gpkg.

Requires outputs from build_summary_results.py in --results-dir.
Detail maps use GSI pale-map tiles through contextily at zoom 16.
"""

from __future__ import annotations

import argparse
import math
import re
import sqlite3
from pathlib import Path

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
import pyogrio
from pyproj import Transformer

try:
    import contextily as ctx
except Exception:
    ctx = None


OVERVIEW_SCENARIOS = [
    "都心南部直下地震",
    "都心東部直下地震",
    "都心西部直下地震",
    "多摩東部直下地震",
    "多摩西部直下地震",
    "立川断層帯地震",
    "大正関東地震",
    "南海トラフ巨大地震",
]

# Detail maps keep the previously selected five representative scenarios.
DETAIL_SCENARIOS = [
    "都心南部直下地震",
    "都心東部直下地震",
    "都心西部直下地震",
    "大正関東地震",
    "南海トラフ巨大地震",
]

DETAIL_CENTERS = {
    "東京駅": (35.68126, 139.76671),
    "東京都立上野高校": (35.7186246, 139.7698412),
    "JR両国駅": (35.6957371, 139.7936379),
    "東京メトロ田原町駅": (35.70984, 139.79076),
}

# (min_lon, min_lat, max_lon, max_lat)
REGION_BBOX = {
    "mainland": (138.90, 35.48, 139.95, 35.93),
    "izu": (138.45, 30.0, 140.55, 35.20),
    "ogasawara": (141.50, 24.0, 142.60, 27.60),
}

REGION_LABEL = {
    "mainland": "東京都本土部（島嶼部除く）",
    "izu": "伊豆諸島",
    "ogasawara": "小笠原諸島",
}

IZU_AREAS = {"三宅島", "八丈島", "利島", "大島", "御蔵島", "新島_式根島", "神津島", "青ヶ島"}
OGASAWARA_AREAS = {"母島", "父島"}

SEISMIC_LABELS = ["5弱未満", "5弱", "5強", "6弱", "6強以上"]
SEISMIC_BOUNDS = [-10, 4.5, 5.0, 5.5, 6.0, 10]
DEPTH_LABELS = ["0", "0–0.5 m", "0.5–3 m", "3–5 m", "5 m以上"]
DEPTH_BOUNDS = [-1e-12, 1e-12, 0.5, 3.0, 5.0, 1e6]

WEBMERC = 3857
WGS84 = 4326
TO_3857 = Transformer.from_crs(WGS84, WEBMERC, always_xy=True)
TO_4326 = Transformer.from_crs(WEBMERC, WGS84, always_xy=True)


def configure_fonts() -> None:
    candidates = ["Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic", "Noto Sans CJK JP"]
    installed = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
    for f in candidates:
        if f in installed:
            matplotlib.rcParams["font.family"] = f
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def ensure_wgs84(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        return gdf.set_crs(4326)
    if gdf.crs.to_epsg() != 4326:
        return gdf.to_crs(4326)
    return gdf


def gpkg_contents(source: Path) -> pd.DataFrame:
    uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=180) as con:
        return pd.read_sql_query(
            "SELECT table_name,data_type,min_x,min_y,max_x,max_y,srs_id FROM gpkg_contents ORDER BY table_name",
            con,
        )


def bbox_intersects(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def webmerc_bbox(center_lat: float, center_lon: float, radius_m: float) -> tuple[float, float, float, float]:
    x, y = TO_3857.transform(center_lon, center_lat)
    return (x - radius_m, y - radius_m, x + radius_m, y + radius_m)


def bbox_3857_to_4326(b) -> tuple[float, float, float, float]:
    minlon, minlat = TO_4326.transform(b[0], b[1])
    maxlon, maxlat = TO_4326.transform(b[2], b[3])
    return (minlon, minlat, maxlon, maxlat)


def discrete_cmap(n: int, name: str = "viridis"):
    base = plt.get_cmap(name)
    return ListedColormap([base(i / max(n - 1, 1)) for i in range(n)])


def seismic_cmap_norm():
    # low intensity = yellow, high intensity = purple
    cmap = ListedColormap(["#F0F921", "#F89640", "#CC4778", "#9C179E", "#0D0887"])
    norm = BoundaryNorm(SEISMIC_BOUNDS, cmap.N)
    return cmap, norm


def depth_cmap_norm():
    # 0 = white, deeper water = darker blue
    cmap = ListedColormap(["#FFFFFF", "#CFE8FF", "#6BAED6", "#2171B5", "#08306B"])
    norm = BoundaryNorm(DEPTH_BOUNDS, cmap.N)
    return cmap, norm


def save_no_data(path: Path, title: str, note: str = "該当データなし") -> None:
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.text(0.5, 0.5, note, ha="center", va="center", fontsize=16, transform=ax.transAxes)
    ax.set_title(title)
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def add_admin_overlay(ax, admin: gpd.GeoDataFrame | None) -> None:
    """Overlay municipality boundaries for mainland Tokyo."""
    if admin is None or admin.empty:
        return
    admin.boundary.plot(ax=ax, color="#666666", linewidth=0.45, alpha=0.9, zorder=20)


def read_fire_full(source: Path, bbox) -> gpd.GeoDataFrame:
    """Read fire layer including the risk-value column."""
    hz = pyogrio.read_dataframe(source, layer="hazard_fire_spread_town", bbox=bbox)
    return ensure_wgs84(hz)


def fire_value_column(hz: gpd.GeoDataFrame) -> str | None:
    # Actual source column is mojibake after SHP decoding but contains '360mm'.
    candidates = [
        c for c in hz.columns
        if c != "geometry" and ("360mm" in str(c) or "risk" in str(c).lower() or "rank" in str(c).lower())
    ]
    for c in candidates:
        if pd.to_numeric(hz[c], errors="coerce").notna().any():
            return c
    numeric = [
        c for c in hz.columns
        if c not in {"fid", "geometry"} and pd.api.types.is_numeric_dtype(hz[c])
    ]
    return numeric[0] if numeric else None


def plot_fire_classes(ax, hz: gpd.GeoDataFrame, value_col: str, alpha: float = 0.85, legend: bool = True) -> None:
    values = pd.to_numeric(hz[value_col], errors="coerce")
    unique = sorted(values.dropna().unique().tolist())
    if not unique:
        hz.plot(ax=ax, color="#f4a261", alpha=0.8, linewidth=0, zorder=1)
        return

    labels = [f"{v:g}" for v in unique]
    mapping = {v: f"{v:g}" for v in unique}
    draw = hz.copy()
    draw["_fire_class"] = values.map(mapping)
    draw["_fire_class"] = pd.Categorical(draw["_fire_class"], categories=labels, ordered=True)

    cmap = ListedColormap(plt.get_cmap("YlOrRd")(np.linspace(0.22, 0.95, len(labels))))
    draw.plot(
        column="_fire_class",
        ax=ax,
        categorical=True,
        cmap=cmap,
        legend=legend,
        alpha=alpha,
        linewidth=0,
        zorder=1,
        legend_kwds={"title": "地震時延焼危険度"},
    )


def plot_inundation_points_from_assignment(
    ax,
    points: gpd.GeoDataFrame,
    assignment: pd.DataFrame,
    area: str,
    bbox,
) -> None:
    """Red = record assigned positive inundation depth in this area; black = others."""
    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]].copy()
    if pts.empty:
        return

    affected = set()
    if assignment is not None and not assignment.empty:
        sub = assignment[
            (assignment["risk_type"].astype(str) == "river_flooding")
            & (assignment["hazard_source"].astype(str) == area)
        ].copy()
        depth = pd.to_numeric(sub["depth_m"], errors="coerce")
        affected = set(sub.loc[depth > 0, "record_id"].astype(str))

    ids = pts["record_id"].astype(str)
    inside = ids.isin(affected)
    outside_pts = pts.loc[~inside]
    inside_pts = pts.loc[inside]

    if not outside_pts.empty:
        outside_pts.plot(ax=ax, color="black", markersize=4.5, alpha=0.72, zorder=25)
    if not inside_pts.empty:
        inside_pts.plot(ax=ax, color="red", markersize=7, alpha=0.9, zorder=26)

    handles = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="red",
               markeredgecolor="none", markersize=6, label="浸水予想区域内"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="black",
               markeredgecolor="none", markersize=5, label="区域外"),
    ]
    ax.legend(handles=handles, title="文化財 point", loc="upper right",
              frameon=True, fontsize=8, title_fontsize=9)


def add_discrete_legend(ax, cmap, labels, title: str):
    handles = [Patch(facecolor=cmap(i), edgecolor="none", label=label) for i, label in enumerate(labels)]
    ax.legend(handles=handles, title=title, loc="lower left", frameon=True, fontsize=8, title_fontsize=9)


def point_grid_max(gdf: gpd.GeoDataFrame, value_col: str, bbox, width: int = 1400, height: int | None = None):
    if gdf.empty:
        return None, None
    if height is None:
        dx = max(bbox[2] - bbox[0], 1e-9)
        dy = max(bbox[3] - bbox[1], 1e-9)
        height = max(300, min(1800, int(width * dy / dx)))
    coords = gdf.geometry.get_coordinates()
    vals = pd.to_numeric(gdf[value_col], errors="coerce").to_numpy()
    xs = coords["x"].to_numpy()
    ys = coords["y"].to_numpy()
    ok = np.isfinite(vals) & np.isfinite(xs) & np.isfinite(ys)
    xs, ys, vals = xs[ok], ys[ok], vals[ok]
    if len(vals) == 0:
        return None, None
    ix = ((xs - bbox[0]) / (bbox[2] - bbox[0]) * (width - 1)).astype(int)
    iy = ((ys - bbox[1]) / (bbox[3] - bbox[1]) * (height - 1)).astype(int)
    keep = (ix >= 0) & (ix < width) & (iy >= 0) & (iy < height)
    ix, iy, vals = ix[keep], iy[keep], vals[keep]
    flat = np.full(width * height, -np.inf, dtype=float)
    np.maximum.at(flat, iy * width + ix, vals)
    arr = flat.reshape(height, width)
    arr[arr == -np.inf] = np.nan
    return arr, (bbox[0], bbox[2], bbox[1], bbox[3])


def update_grid_max(grid: np.ndarray, gdf: gpd.GeoDataFrame, value_col: str, bbox):
    if gdf.empty:
        return
    h, w = grid.shape
    coords = gdf.geometry.get_coordinates()
    vals = pd.to_numeric(gdf[value_col], errors="coerce").to_numpy()
    xs, ys = coords["x"].to_numpy(), coords["y"].to_numpy()
    ok = np.isfinite(vals) & np.isfinite(xs) & np.isfinite(ys)
    xs, ys, vals = xs[ok], ys[ok], vals[ok]
    if len(vals) == 0:
        return
    ix = ((xs - bbox[0]) / (bbox[2] - bbox[0]) * (w - 1)).astype(int)
    iy = ((ys - bbox[1]) / (bbox[3] - bbox[1]) * (h - 1)).astype(int)
    keep = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
    ix, iy, vals = ix[keep], iy[keep], vals[keep]
    flat = grid.ravel()
    # NaN -> -inf for maximum update, then restore untouched cells.
    tmp = np.where(np.isnan(flat), -np.inf, flat)
    np.maximum.at(tmp, iy * w + ix, vals)
    flat[:] = np.where(tmp == -np.inf, np.nan, tmp)


def plot_categorical_points(points: gpd.GeoDataFrame, category: str, bbox, title: str, out: Path, boundaries=None):
    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]].copy()
    if pts.empty:
        save_no_data(out, title, "統合データ内に文化財 point なし")
        return
    fig, ax = plt.subplots(figsize=(10, 8))
    if boundaries is not None and not boundaries.empty:
        boundaries.boundary.plot(ax=ax, linewidth=0.4, alpha=0.6)
    cats = sorted(pts[category].fillna("Unknown").astype(str).unique())
    cmap = discrete_cmap(max(len(cats), 1), "tab20")
    handles = []
    for i, c in enumerate(cats):
        sub = pts[pts[category].fillna("Unknown").astype(str) == c]
        sub.plot(ax=ax, markersize=7, alpha=0.75, color=cmap(i), label=c)
        handles.append(Line2D([], [], marker="o", linestyle="", markersize=5, color=cmap(i), label=c))
    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_title(title)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.legend(handles=handles, loc="best", fontsize=8, title=category)
    fig.tight_layout(); fig.savefig(out, dpi=200, bbox_inches="tight"); plt.close(fig)


def plot_choropleth(source: Path, tables: Path, outdir: Path) -> None:
    counts = pd.read_csv(tables / "municipality_record_counts.csv", dtype={"municipality_code": str})
    admin = pyogrio.read_dataframe(source, layer="admin_boundary_n03_2024")
    admin = ensure_wgs84(admin)
    admin["municipality_code"] = admin["N03_007"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5)
    admin = admin.dissolve(by="municipality_code", as_index=False)
    merged = admin.merge(counts, on="municipality_code", how="left")
    merged["record_count"] = merged["record_count"].fillna(0)

    fig, ax = plt.subplots(figsize=(11, 8))
    merged.plot(column="record_count", ax=ax, legend=True, cmap="viridis", edgecolor="white", linewidth=0.3)
    ax.set_title("区市町村別文化財レコード数（行政区域レイヤ収録範囲）")
    ax.set_axis_off(); fig.tight_layout()
    fig.savefig(outdir / "choropleth_municipality_record_count.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    matched = set(merged["municipality_code"])
    unmapped = counts[~counts["municipality_code"].isin(matched)].copy()
    unmapped.to_csv(tables / "choropleth_unmapped_municipalities.csv", index=False, encoding="utf-8-sig")


def read_layer_bbox(source: Path, layer: str, bbox, columns: list[str]) -> gpd.GeoDataFrame:
    try:
        gdf = pyogrio.read_dataframe(source, layer=layer, columns=columns, bbox=bbox)
    except Exception:
        gdf = pyogrio.read_dataframe(source, layer=layer, bbox=bbox)
    return ensure_wgs84(gdf)



def plot_seismic_overview(
    source: Path,
    layer: str,
    scenario: str,
    region: str,
    points: gpd.GeoDataFrame,
    out: Path,
    admin: gpd.GeoDataFrame | None = None,
):
    bbox = REGION_BBOX[region]
    hz = read_layer_bbox(source, layer, bbox, ["seismic_intensity"])
    if hz.empty:
        save_no_data(out, f"{REGION_LABEL[region]}：想定震度 {scenario}")
        return

    centers = hz.to_crs("EPSG:3857")
    centers["geometry"] = centers.geometry.centroid
    centers = centers.to_crs("EPSG:4326")
    arr, extent = point_grid_max(centers, "seismic_intensity", bbox, width=1600)
    cmap, norm = seismic_cmap_norm()

    fig, ax = plt.subplots(figsize=(10, 8))
    if arr is not None:
        ax.imshow(arr, extent=extent, origin="lower", cmap=cmap, norm=norm, interpolation="nearest")

    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
    if not pts.empty:
        pts.plot(ax=ax, color="black", markersize=4, alpha=0.75, zorder=25)

    if region == "mainland":
        add_admin_overlay(ax, admin)

    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_title(f"{REGION_LABEL[region]}：想定震度 {scenario}")
    add_discrete_legend(ax, cmap, SEISMIC_LABELS, "想定震度")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)


def plot_fire_overview(
    source: Path,
    points: gpd.GeoDataFrame,
    out: Path,
    admin: gpd.GeoDataFrame | None = None,
):
    bbox = REGION_BBOX["mainland"]
    hz = read_fire_full(source, bbox)
    if hz.empty:
        save_no_data(out, "東京都本土部：地震時延焼危険度")
        return

    value_col = fire_value_column(hz)
    fig, ax = plt.subplots(figsize=(11, 8))

    if value_col is None:
        hz.plot(ax=ax, color="#f4a261", alpha=0.8, linewidth=0, zorder=1)
        print("[fire overview] WARNING: risk value column not found")
    else:
        vals = sorted(pd.to_numeric(hz[value_col], errors="coerce").dropna().unique().tolist())
        print(f"[fire overview] value_col={value_col!r}, unique={vals}")
        plot_fire_classes(ax, hz, value_col)

    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
    if not pts.empty:
        pts.plot(ax=ax, color="black", markersize=4, alpha=0.75, zorder=25)

    add_admin_overlay(ax, admin)
    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_title("東京都本土部：地震時延焼危険度")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)



def a31a_depth_plot_values(hz: gpd.GeoDataFrame) -> pd.Series:
    """Convert A31a summary classes/ranks to representative depths for the common legend."""
    if "depth_class_summary" in hz.columns:
        mapping = {
            "0–0.5 m": 0.25,
            "0-0.5 m": 0.25,
            "0.5–3 m": 1.0,
            "0.5-3 m": 1.0,
            "3–5 m": 4.0,
            "3-5 m": 4.0,
            "5 m以上": 6.0,
            "5m以上": 6.0,
        }
        vals = hz["depth_class_summary"].astype(str).map(mapping)
        if vals.notna().any():
            return vals

    if "depth_rank_code" in hz.columns:
        rank = pd.to_numeric(hz["depth_rank_code"], errors="coerce")
        return rank.map({1: 0.25, 2: 1.0, 3: 4.0, 4: 6.0, 5: 6.0, 6: 6.0})

    return pd.Series(np.nan, index=hz.index, dtype=float)


def split_points_by_polygon(points: gpd.GeoDataFrame, hazard: gpd.GeoDataFrame):
    if points.empty or hazard.empty:
        return points.iloc[0:0].copy(), points.copy()
    try:
        geom = hazard.geometry.union_all()
    except Exception:
        geom = hazard.geometry.unary_union
    inside_mask = points.geometry.intersects(geom)
    return points.loc[inside_mask].copy(), points.loc[~inside_mask].copy()


def plot_a31a_overview(
    source: Path,
    layer: str,
    points: gpd.GeoDataFrame,
    out: Path,
    admin: gpd.GeoDataFrame | None = None,
):
    bbox = REGION_BBOX["mainland"]
    hz = read_layer_bbox(
        source, layer, bbox,
        ["depth_class_summary", "depth_rank_code", "river_name"]
    )
    if hz.empty:
        return

    cmap, norm = depth_cmap_norm()
    hz = hz.copy()
    hz["_depth_plot"] = a31a_depth_plot_values(hz)

    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]].copy()
    inside, outside = split_points_by_polygon(pts, hz)

    fig, ax = plt.subplots(figsize=(11, 8))
    hz.plot(
        column="_depth_plot",
        ax=ax,
        cmap=cmap,
        norm=norm,
        linewidth=0,
        alpha=0.72,
        zorder=1,
    )
    add_admin_overlay(ax, admin)

    if not outside.empty:
        outside.plot(ax=ax, color="black", markersize=4, alpha=0.70, zorder=25)
    if not inside.empty:
        inside.plot(ax=ax, color="red", markersize=5, alpha=0.85, zorder=26)

    river = layer.replace("hazard_inundation_a31a_", "")
    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_title(f"浸水想定区域（A31a）：{river}")

    handles = [
        Patch(facecolor=cmap(i), edgecolor="none", label=label)
        for i, label in enumerate(DEPTH_LABELS)
    ]
    depth_legend = ax.legend(
        handles=handles, title="想定浸水深",
        loc="lower left", frameon=True, fontsize=8, title_fontsize=9
    )
    ax.add_artist(depth_legend)

    point_handles = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="red",
               markeredgecolor="none", markersize=6, label="浸水想定区域内"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="black",
               markeredgecolor="none", markersize=5, label="区域外"),
    ]
    ax.legend(
        handles=point_handles, title="文化財 point",
        loc="upper right", frameon=True, fontsize=8, title_fontsize=9
    )

    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)


def plot_inundation_overviews(
    source: Path,
    contents: pd.DataFrame,
    points: gpd.GeoDataFrame,
    outdir: Path,
    tables_dir: Path,
    admin: gpd.GeoDataFrame | None = None,
):
    bbox = REGION_BBOX["mainland"]
    cmap, norm = depth_cmap_norm()
    layers = contents[
        contents["table_name"].str.startswith("hazard_inundation_", na=False)
    ]["table_name"].tolist()

    assignment_path = tables_dir / "water_risk_external_point_assignments.csv"
    if assignment_path.exists():
        assignment = pd.read_csv(assignment_path, dtype={"record_id": str})
    else:
        assignment = pd.DataFrame(
            columns=["record_id", "risk_type", "depth_m", "hazard_source"]
        )
        print(f"[overview inundation] WARNING: missing {assignment_path}")

    for layer in layers:
        if layer.startswith("hazard_inundation_a31a_"):
            river = layer.replace("hazard_inundation_a31a_", "")
            print(f"[overview inundation A31a] {river}")
            out = outdir / f"inundation_a31a_{re.sub(r'[^0-9A-Za-z一-龠ぁ-んァ-ヶ_-]+','_',river)}.png"
            plot_a31a_overview(source, layer, points, out, admin)
            continue

        area = layer.replace("hazard_inundation_", "")
        print(f"[overview inundation] {area}")
        hz = read_layer_bbox(source, layer, bbox, ["inundation_depth_m"])
        out = outdir / f"inundation_{re.sub(r'[^0-9A-Za-z一-龠ぁ-んァ-ヶ_-]+','_',area)}.png"
        if hz.empty:
            continue

        arr, extent = point_grid_max(hz, "inundation_depth_m", bbox, width=1600)
        fig, ax = plt.subplots(figsize=(11, 8))
        if arr is not None:
            ax.imshow(
                arr, extent=extent, origin="lower",
                cmap=cmap, norm=norm, interpolation="nearest"
            )

        add_admin_overlay(ax, admin)
        plot_inundation_points_from_assignment(ax, points, assignment, area, bbox)

        ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
        ax.set_title(f"浸水予想区域：{area}")

        # Keep inundation-depth legend separately from the point legend.
        handles = [
            Patch(facecolor=cmap(i), edgecolor="none", label=label)
            for i, label in enumerate(DEPTH_LABELS)
        ]
        depth_legend = ax.legend(
            handles=handles, title="想定浸水深",
            loc="lower left", frameon=True, fontsize=8, title_fontsize=9
        )
        ax.add_artist(depth_legend)

        point_handles = [
            Line2D([0], [0], marker="o", color="w", markerfacecolor="red",
                   markeredgecolor="none", markersize=6, label="浸水予想区域内"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor="black",
                   markeredgecolor="none", markersize=5, label="区域外"),
        ]
        ax.legend(
            handles=point_handles, title="文化財 point",
            loc="upper right", frameon=True, fontsize=8, title_fontsize=9
        )

        ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
        fig.tight_layout()
        fig.savefig(out, dpi=200, bbox_inches="tight")
        plt.close(fig)


def plot_storm_overview(
    source: Path,
    points: gpd.GeoDataFrame,
    out: Path,
    admin: gpd.GeoDataFrame | None = None,
):
    bbox = REGION_BBOX["mainland"]
    hz = read_layer_bbox(source, "hazard_storm_surge_depth", bbox, ["DepthM"])
    if hz.empty:
        save_no_data(out, "高潮浸水想定")
        return

    cmap, norm = depth_cmap_norm()
    fig, ax = plt.subplots(figsize=(11, 8))
    hz.plot(column="DepthM", ax=ax, cmap=cmap, norm=norm, linewidth=0, alpha=0.75, zorder=1)

    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
    if not pts.empty:
        pts.plot(ax=ax, color="black", markersize=4, alpha=0.7, zorder=25)

    add_admin_overlay(ax, admin)
    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_title("東京都本土部：高潮浸水想定")
    add_discrete_legend(ax, cmap, DEPTH_LABELS, "想定浸水深")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)

def parse_tsunami_layer(layer: str):
    rest = layer.replace("hazard_tsunami_depth_", "", 1)
    scenarios = [
        "南海トラフ巨大地震_全5ケース最大値",
        "南海トラフ巨大地震_ケース1",
        "南海トラフ巨大地震_ケース2",
        "南海トラフ巨大地震_ケース5",
        "南海トラフ巨大地震_ケース6",
        "南海トラフ巨大地震_ケース8",
        "大正関東地震",
    ]
    for s in scenarios:
        suffix = "_" + s
        if rest.endswith(suffix):
            return rest[:-len(suffix)], s
    return rest, "unknown"


def tsunami_region(area: str) -> str | None:
    if area == "区部": return "mainland"
    if area in IZU_AREAS: return "izu"
    if area in OGASAWARA_AREAS: return "ogasawara"
    return None



def plot_tsunami_overviews(
    source: Path,
    contents: pd.DataFrame,
    points: gpd.GeoDataFrame,
    outdir: Path,
    admin: gpd.GeoDataFrame | None = None,
):
    layer_names = contents[
        contents["table_name"].str.startswith("hazard_tsunami_depth_", na=False)
    ]["table_name"].tolist()

    groups = {}
    for layer in layer_names:
        area, scenario = parse_tsunami_layer(layer)
        region = tsunami_region(area)
        if region:
            groups.setdefault((region, scenario), []).append((layer, area))

    cmap, norm = depth_cmap_norm()
    for (region, scenario), items in groups.items():
        bbox = REGION_BBOX[region]
        width = 1400
        height = max(
            350,
            min(1900, int(width * (bbox[3]-bbox[1]) / (bbox[2]-bbox[0])))
        )
        grid = np.full((height, width), np.nan, dtype=float)
        used = []

        for layer, area in items:
            hz = read_layer_bbox(source, layer, bbox, ["inundation_depth_max_m"])
            if hz.empty:
                continue
            update_grid_max(grid, hz, "inundation_depth_max_m", bbox)
            used.append(area)

        if not used:
            continue

        fig, ax = plt.subplots(figsize=(10, 8))
        ax.imshow(
            grid,
            extent=(bbox[0],bbox[2],bbox[1],bbox[3]),
            origin="lower",
            cmap=cmap,
            norm=norm,
            interpolation="nearest",
        )

        pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
        if not pts.empty:
            pts.plot(ax=ax, color="black", markersize=4, alpha=0.75, zorder=25)

        if region == "mainland":
            add_admin_overlay(ax, admin)

        ax.set_xlim(bbox[0],bbox[2]); ax.set_ylim(bbox[1],bbox[3])
        ax.set_title(f"{REGION_LABEL[region]}：津波浸水深 {scenario}")
        add_discrete_legend(ax, cmap, DEPTH_LABELS, "想定浸水深")
        ax.text(
            0.01, 0.01,
            "対象: " + ", ".join(sorted(set(used))),
            transform=ax.transAxes, fontsize=7, va="bottom"
        )
        ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
        fig.tight_layout()
        fig.savefig(
            outdir / f"tsunami_{region}_{re.sub(r'[^0-9A-Za-z一-龠ぁ-んァ-ヶ_-]+','_',scenario)}.png",
            dpi=200, bbox_inches="tight"
        )
        plt.close(fig)

def read_detail_context(source: Path, locations: gpd.GeoDataFrame, bbox4326):
    footprints = read_layer_bbox(source, "heritage_buildings_footprint_riskwide", bbox4326, ["record_ids", "heritage_type_majors"])
    pts = locations.cx[bbox4326[0]:bbox4326[2], bbox4326[1]:bbox4326[3]].copy()
    return footprints.to_crs(3857), pts.to_crs(3857)



def add_gsi_basemap(ax):
    """Use GSI pale-map tiles for detail maps; continue without tiles on failure."""
    if ctx is None:
        print("[basemap] contextily unavailable; continuing without background tiles")
        return
    try:
        ctx.add_basemap(
            ax,
            source="https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
            zoom=16,
            attribution="地理院タイル（国土地理院）",
            attribution_size=6,
        )
    except Exception as exc:
        print(f"[basemap] GSI tile failed: {type(exc).__name__}: {exc}")
        print("[basemap] continuing without background tiles")

def overlay_heritage(ax, footprints, points):
    # Draw point observations first, then filled PLATEAU footprints above them.
    if not points.empty:
        points.plot(ax=ax, markersize=15, color="black", marker="o", alpha=0.9, zorder=6)
    if not footprints.empty:
        footprints.plot(
            ax=ax,
            facecolor="#555555",
            edgecolor="black",
            linewidth=0.45,
            alpha=0.78,
            zorder=7,
        )


def detail_base(center_lat, center_lon, radius_m):
    b3857 = webmerc_bbox(center_lat, center_lon, radius_m)
    b4326 = bbox_3857_to_4326(b3857)
    return b3857, b4326


def detail_seismic(source: Path, center_name: str, lat: float, lon: float, radius: float, outdir: Path, locations):
    b3857, b4326 = detail_base(lat, lon, radius)
    footprints, points = read_detail_context(source, locations, b4326)
    cmap, norm = seismic_cmap_norm()
    for scenario in DETAIL_SCENARIOS:
        layer = f"hazard_seismic_50m_{scenario}"
        hz = read_layer_bbox(source, layer, b4326, ["seismic_intensity"])
        out = outdir / f"{center_name}_seismic_{scenario}.png"
        if hz.empty:
            continue
        hz = hz.to_crs(3857)
        fig, ax = plt.subplots(figsize=(9, 9))
        hz.plot(column="seismic_intensity", ax=ax, cmap=cmap, norm=norm, linewidth=0, alpha=0.62, zorder=2)
        add_gsi_basemap(ax); overlay_heritage(ax, footprints, points)
        ax.set_xlim(b3857[0], b3857[2]); ax.set_ylim(b3857[1], b3857[3]); ax.set_axis_off()
        ax.set_title(f"{center_name} Z=16：想定震度 {scenario}")
        add_discrete_legend(ax, cmap, SEISMIC_LABELS, "想定震度")
        fig.tight_layout(); fig.savefig(out, dpi=220, bbox_inches="tight"); plt.close(fig)


def detail_fire(source: Path, center_name: str, lat: float, lon: float, radius: float, outdir: Path, locations):
    b3857, b4326 = detail_base(lat, lon, radius)
    footprints, points = read_detail_context(source, locations, b4326)

    # Read the full town-level layer so the T360mm risk attribute is retained.
    hz = read_fire_full(source, b4326)
    if hz.empty:
        return

    value_col = fire_value_column(hz)
    hz = hz.to_crs(3857)

    fig, ax = plt.subplots(figsize=(9, 9))
    if value_col is not None:
        vals = sorted(pd.to_numeric(hz[value_col], errors="coerce").dropna().unique().tolist())
        print(f"[detail fire] {center_name}: value_col={value_col!r}, unique={vals}")
        plot_fire_classes(ax, hz, value_col, alpha=0.58, legend=True)
    else:
        hz.plot(ax=ax, color="#f4a261", alpha=0.5, linewidth=0, zorder=2)
        print(f"[detail fire] {center_name}: WARNING risk value column not found")

    add_gsi_basemap(ax)
    overlay_heritage(ax, footprints, points)
    ax.set_xlim(b3857[0], b3857[2]); ax.set_ylim(b3857[1], b3857[3]); ax.set_axis_off()
    ax.set_title(f"{center_name} Z=16：地震時延焼危険度")
    fig.tight_layout()
    fig.savefig(outdir / f"{center_name}_fire.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def detail_inundation(source: Path, contents: pd.DataFrame, center_name: str, lat: float, lon: float, radius: float, outdir: Path, locations):
    b3857, b4326 = detail_base(lat, lon, radius)
    footprints, points = read_detail_context(source, locations, b4326)

    layers = []
    for _, r in contents[
        contents["table_name"].str.startswith("hazard_inundation_", na=False)
    ].iterrows():
        ext = (r.min_x, r.min_y, r.max_x, r.max_y)
        if bbox_intersects(ext, b4326):
            layers.append(r.table_name)

    if not layers:
        return

    cmap, norm = depth_cmap_norm()
    fig, ax = plt.subplots(figsize=(9, 9))
    used = []

    for layer in layers:
        if layer.startswith("hazard_inundation_a31a_"):
            hz = read_layer_bbox(
                source, layer, b4326,
                ["depth_class_summary", "depth_rank_code", "river_name"]
            )
            if hz.empty:
                continue
            hz = hz.copy()
            hz["_depth_plot"] = a31a_depth_plot_values(hz)
            hz = hz.to_crs(3857)
            hz.plot(
                ax=ax,
                column="_depth_plot",
                cmap=cmap,
                norm=norm,
                linewidth=0,
                alpha=0.58,
                zorder=2,
            )
            used.append("A31a " + layer.replace("hazard_inundation_a31a_", ""))
            continue

        hz = read_layer_bbox(source, layer, b4326, ["inundation_depth_m"])
        if hz.empty:
            continue
        hz = hz.to_crs(3857)
        # Existing Tokyo inundation source is a published point grid.
        hz.plot(
            ax=ax,
            column="inundation_depth_m",
            cmap=cmap,
            norm=norm,
            markersize=4,
            alpha=0.65,
            zorder=2,
        )
        used.append(layer.replace("hazard_inundation_", ""))

    if not used:
        plt.close(fig)
        return

    add_gsi_basemap(ax)
    overlay_heritage(ax, footprints, points)
    ax.set_xlim(b3857[0], b3857[2]); ax.set_ylim(b3857[1], b3857[3])
    ax.set_axis_off()
    ax.set_title(f"{center_name} Z=16：浸水予想区域")
    add_discrete_legend(ax, cmap, DEPTH_LABELS, "想定浸水深")
    ax.text(
        0.01, 0.01,
        "重ね合わせ: " + ", ".join(used),
        transform=ax.transAxes,
        fontsize=6,
        va="bottom",
        zorder=10,
    )
    fig.tight_layout()
    fig.savefig(outdir / f"{center_name}_inundation.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def detail_storm(source: Path, center_name: str, lat: float, lon: float, radius: float, outdir: Path, locations):
    b3857, b4326 = detail_base(lat, lon, radius)
    footprints, points = read_detail_context(source, locations, b4326)
    hz = read_layer_bbox(source, "hazard_storm_surge_depth", b4326, ["DepthM"])
    if hz.empty:
        return
    hz = hz.to_crs(3857); cmap, norm = depth_cmap_norm()
    fig, ax = plt.subplots(figsize=(9, 9))
    hz.plot(column="DepthM", ax=ax, cmap=cmap, norm=norm, linewidth=0, alpha=0.6, zorder=2)
    add_gsi_basemap(ax); overlay_heritage(ax, footprints, points)
    ax.set_xlim(b3857[0], b3857[2]); ax.set_ylim(b3857[1], b3857[3]); ax.set_axis_off()
    ax.set_title(f"{center_name} Z=16：高潮浸水想定")
    add_discrete_legend(ax, cmap, DEPTH_LABELS, "想定浸水深")
    fig.tight_layout(); fig.savefig(outdir / f"{center_name}_storm_surge.png", dpi=220, bbox_inches="tight"); plt.close(fig)


def detail_tsunami(source: Path, contents: pd.DataFrame, center_name: str, lat: float, lon: float, radius: float, outdir: Path, locations):
    b3857, b4326 = detail_base(lat, lon, radius)
    footprints, points = read_detail_context(source, locations, b4326)
    layer_names = contents[contents["table_name"].str.startswith("hazard_tsunami_depth_", na=False)]["table_name"].tolist()
    groups = {}
    for layer in layer_names:
        row = contents[contents["table_name"] == layer].iloc[0]
        ext = (row.min_x, row.min_y, row.max_x, row.max_y)
        if not bbox_intersects(ext, b4326): continue
        area, scenario = parse_tsunami_layer(layer)
        groups.setdefault(scenario, []).append((layer, area))
    cmap, norm = depth_cmap_norm()
    for scenario, items in groups.items():
        fig, ax = plt.subplots(figsize=(9, 9)); used=[]
        for layer, area in items:
            hz = read_layer_bbox(source, layer, b4326, ["inundation_depth_max_m"])
            if hz.empty: continue
            hz = hz.to_crs(3857)
            hz.plot(ax=ax, column="inundation_depth_max_m", cmap=cmap, norm=norm, markersize=4, alpha=0.65, zorder=2)
            used.append(area)
        if not used:
            plt.close(fig); continue
        add_gsi_basemap(ax); overlay_heritage(ax, footprints, points)
        ax.set_xlim(b3857[0], b3857[2]); ax.set_ylim(b3857[1], b3857[3]); ax.set_axis_off()
        ax.set_title(f"{center_name} Z=16：津波浸水深 {scenario}")
        add_discrete_legend(ax, cmap, DEPTH_LABELS, "想定浸水深")
        fig.tight_layout(); fig.savefig(outdir / f"{center_name}_tsunami_{scenario}.png", dpi=220, bbox_inches="tight"); plt.close(fig)


def plot_risk_distribution(results_dir: Path, points: gpd.GeoDataFrame, overview_dir: Path, boundaries=None):
    risk_file = results_dir / "tables" / "record_risk_types.csv"
    if not risk_file.exists(): return
    risk = pd.read_csv(risk_file, dtype={"record_id": str})
    rep = points.drop_duplicates("record_id").copy()
    # Risk-type point maps, one type at a time.
    for risk_type in sorted(risk["risk_type"].dropna().unique()):
        ids = set(risk.loc[risk["risk_type"] == risk_type, "record_id"].astype(str))
        sub = rep[rep["record_id"].astype(str).isin(ids)]
        for region, bbox in REGION_BBOX.items():
            region_sub = sub.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
            out = overview_dir / f"risk_points_{risk_type}_{region}.png"
            title = f"{REGION_LABEL[region]}：災害リスク point 分布 — {risk_type}"
            if region_sub.empty:
                save_no_data(out, title, "該当 point なし")
                continue
            fig, ax = plt.subplots(figsize=(10,8))
            if boundaries is not None and region == "mainland": boundaries.boundary.plot(ax=ax, linewidth=0.4, alpha=0.5)
            region_sub.plot(ax=ax, markersize=8, alpha=0.75)
            ax.set_xlim(bbox[0],bbox[2]); ax.set_ylim(bbox[1],bbox[3]); ax.set_title(title)
            ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
            fig.tight_layout(); fig.savefig(out,dpi=200,bbox_inches="tight"); plt.close(fig)

    # Cultural-type distribution among records with at least one assigned risk.
    any_ids = set(risk["record_id"].astype(str))
    sub = rep[rep["record_id"].astype(str).isin(any_ids)]
    for region,bbox in REGION_BBOX.items():
        plot_categorical_points(
            sub, "heritage_type_major", bbox,
            f"{REGION_LABEL[region]}：災害リスク付与済み文化財 類型別分布",
            overview_dir / f"risk_assigned_cultural_type_{region}.png",
            boundaries if region=="mainland" else None,
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("--results-dir", type=Path, default=Path("summary_results"))
    ap.add_argument("--stage", choices=["overview","detail","all"], default="all")
    ap.add_argument("--detail-radius-m", type=float, default=1000.0, help="Half-width/height around each Z=16 center")
    args = ap.parse_args()

    configure_fonts()
    source = args.source.expanduser().resolve(); results = args.results_dir.expanduser().resolve()
    cache_loc = results / "cache" / "analysis_locations.gpkg"
    if not source.exists(): raise SystemExit(f"ERROR source not found: {source}")
    if not cache_loc.exists(): raise SystemExit("ERROR: run build_summary_results.py first; analysis_locations.gpkg is missing")

    figures = results / "figures"
    overview = figures / "overview"
    detail = figures / "detail"
    city = figures / "city"
    inundation_center = detail / "inundation_center"
    overview.mkdir(parents=True, exist_ok=True)
    detail.mkdir(parents=True, exist_ok=True)
    city.mkdir(parents=True, exist_ok=True)
    inundation_center.mkdir(parents=True, exist_ok=True)
    tables = results / "tables"
    locations = pyogrio.read_dataframe(cache_loc, layer="analysis_locations")
    locations = ensure_wgs84(locations)
    contents = gpkg_contents(source)

    admin = pyogrio.read_dataframe(source, layer="admin_boundary_n03_2024")
    admin = ensure_wgs84(admin)

    if args.stage in ("overview","all"):
        print("=== OVERVIEW MAPS ===")
        plot_choropleth(source, tables, overview)
        rep = locations.drop_duplicates("record_id")
        for category in ["designation_level","designation_status","heritage_type_major"]:
            for region,bbox in REGION_BBOX.items():
                plot_categorical_points(
                    rep, category, bbox,
                    f"{REGION_LABEL[region]}：{category}",
                    overview / f"points_{category}_{region}.png",
                    admin if region=="mainland" else None,
                )
        plot_risk_distribution(results, rep, overview, admin)
        for scenario in OVERVIEW_SCENARIOS:
            layer = f"hazard_seismic_50m_{scenario}"
            for region in REGION_BBOX:
                print(f"[overview seismic] {scenario} / {region}")
                plot_seismic_overview(source, layer, scenario, region, rep, overview / f"seismic_{scenario}_{region}.png", admin if region=="mainland" else None)
        plot_fire_overview(source, rep, overview / "fire_mainland.png", admin)
        plot_inundation_overviews(source, contents, rep, overview, tables, admin)
        plot_storm_overview(source, rep, overview / "storm_surge_mainland.png", admin)
        plot_tsunami_overviews(source, contents, rep, overview, admin)

    if args.stage in ("detail","all"):
        print("=== Z=16 DETAIL MAPS ===")
        for name,(lat,lon) in DETAIL_CENTERS.items():
            outdir = detail / name
            outdir.mkdir(parents=True, exist_ok=True)
            print(f"[detail] {name}")
            detail_seismic(source,name,lat,lon,args.detail_radius_m,outdir,locations)
            detail_fire(source,name,lat,lon,args.detail_radius_m,outdir,locations)
            detail_inundation(source,contents,name,lat,lon,args.detail_radius_m,inundation_center,locations)
            detail_storm(source,name,lat,lon,args.detail_radius_m,outdir,locations)
            detail_tsunami(source,contents,name,lat,lon,args.detail_radius_m,outdir,locations)

    print("SUCCESS:", figures)
    print("folders:", overview, detail, city)


if __name__ == "__main__":
    main()
