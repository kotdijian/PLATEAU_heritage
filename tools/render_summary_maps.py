#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pyogrio
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from shapely.geometry import box

try:
    import contextily as ctx
    HAS_CTX = True
except Exception:
    HAS_CTX = False

FIG_ROOT = Path("summary_results/figures")
WGS84 = "EPSG:4326"
WEBM = "EPSG:3857"

DETAIL_CENTERS = {
    "tokyo_station": (139.767125, 35.681236, "東京駅"),
    "ueno_hs": (139.782327, 35.717581, "東京都立上野高校"),
    "ryogoku_sta": (139.793564, 35.696203, "JR両国駅"),
    "tawaramachi_sta": (139.790284, 35.710800, "東京メトロ田原町駅"),
}
DETAIL_SEISMIC_SCENARIOS = [
    "都心南部直下地震",
    "都心東部直下地震",
    "都心西部直下地震",
    "大正関東地震",
    "南海トラフ巨大地震",
]


def slugify(text: str) -> str:
    text = str(text)
    text = re.sub(r"[\\/\s]+", "_", text)
    text = re.sub(r"[^0-9A-Za-z_\-\u3000-\u30FF\u4E00-\u9FFF]", "", text)
    return text.strip("_") or "unnamed"


def ensure_wgs84(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf is None or gdf.empty:
        return gpd.GeoDataFrame(geometry=[], crs=WGS84)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84, allow_override=True)
    elif str(gdf.crs) != WGS84:
        gdf = gdf.to_crs(WGS84)
    return gdf


def list_layers(gpkg: Path) -> list[str]:
    return [name for name, _ in pyogrio.list_layers(gpkg)]


def find_layer(gpkg: Path, candidates: Iterable[str], contains: Iterable[str] | None = None) -> str:
    layers = list_layers(gpkg)
    for cand in candidates:
        if cand in layers:
            return cand
    if contains:
        for layer in layers:
            low = layer.lower()
            if all(token.lower() in low for token in contains):
                return layer
    raise RuntimeError(f"Layer not found. candidates={list(candidates)}, contains={list(contains or [])}")


def read_layer(gpkg: Path, layer: str, bbox=None) -> gpd.GeoDataFrame:
    gdf = pyogrio.read_dataframe(gpkg, layer=layer, bbox=bbox)
    return ensure_wgs84(gdf)


def load_admin(gpkg: Path) -> gpd.GeoDataFrame:
    try:
        layer = find_layer(gpkg, ["admin_boundary_n03_2024", "admin_boundary"], ["admin", "boundary"])
        return read_layer(gpkg, layer)
    except Exception:
        return gpd.GeoDataFrame(geometry=[], crs=WGS84)


def load_points(gpkg: Path) -> gpd.GeoDataFrame:
    layer = find_layer(gpkg, ["heritage_buildings_point", "heritage_points"], ["heritage", "point"])
    return read_layer(gpkg, layer)


def load_footprints(gpkg: Path) -> gpd.GeoDataFrame:
    layer = find_layer(gpkg, ["heritage_buildings_footprints", "heritage_buildings_footprint"], ["heritage", "footprint"])
    return read_layer(gpkg, layer)


def clip_gdf(gdf: gpd.GeoDataFrame, bbox4326) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf.copy()
    geom = box(*bbox4326)
    return gdf[gdf.intersects(geom)].copy()


def region_groups(admin: gpd.GeoDataFrame) -> dict[str, tuple[float, float, float, float]]:
    # fixed extents, easier and stable
    return {
        "mainland": (138.35, 35.15, 140.45, 35.95),
        "izu": (138.7, 32.9, 140.6, 34.8),
        "ogasawara": (141.0, 26.0, 143.5, 27.9),
    }


def find_seismic_value_column(gdf: gpd.GeoDataFrame) -> str:
    preferred = ["seismic_intensity", "intensity", "jma_intensity", "value"]
    for c in preferred:
        if c in gdf.columns:
            return c
    numeric = [c for c in gdf.columns if c != "geometry" and np.issubdtype(gdf[c].dtype, np.number)]
    if numeric:
        return numeric[0]
    raise RuntimeError(f"Seismic value column not found. columns={list(gdf.columns)}")


def find_fire_value_column(gdf: gpd.GeoDataFrame) -> str | None:
    preferred = [
        "fire_spread_rank", "fire_rank", "danger_rank", "rank", "T360mm_危険度", "T360mm_危", "T360mm"
    ]
    for c in preferred:
        if c in gdf.columns:
            return c
    for c in gdf.columns:
        low = c.lower()
        if any(k in low for k in ["fire", "danger", "rank"]):
            return c
    numeric = [c for c in gdf.columns if c != "geometry" and np.issubdtype(gdf[c].dtype, np.number)]
    return numeric[0] if numeric else None


def find_inundation_depth_column(gdf: gpd.GeoDataFrame) -> str | None:
    prefs = [
        "max_depth_m", "depth_m", "depth", "rank", "max_rank", "浸水深ランク", "想定最大浸水深ランク", "想定浸水深ランク"
    ]
    for c in prefs:
        if c in gdf.columns:
            return c
    for c in gdf.columns:
        low = c.lower()
        if "depth" in low or "rank" in low or "浸水" in c:
            return c
    numeric = [c for c in gdf.columns if c != "geometry" and np.issubdtype(gdf[c].dtype, np.number)]
    return numeric[0] if numeric else None


def scenario_from_layer(layer: str, prefix: str) -> str:
    return layer.replace(prefix, "")


def add_gsi_basemap(ax, zoom=16):
    if not HAS_CTX:
        return
    try:
        provider = {
            "url": "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
            "attribution": "GSI Tiles",
            "name": "GSI.Pale",
        }
        ctx.add_basemap(ax, source=provider, zoom=zoom, crs=WEBM)
    except Exception:
        pass


def draw_admin(ax, admin: gpd.GeoDataFrame, bbox=None, color="gray", lw=0.4, zorder=5):
    if admin is None or admin.empty:
        return
    adm = clip_gdf(admin, bbox) if bbox else admin
    if not adm.empty:
        adm.boundary.plot(ax=ax, color=color, linewidth=lw, zorder=zorder)


def hazard_union(gdf: gpd.GeoDataFrame):
    if gdf.empty:
        return None
    try:
        geom = gdf.geometry.union_all()
    except Exception:
        geom = gdf.unary_union
    if geom is None or geom.is_empty:
        return None
    return geom


def split_points_inside(points: gpd.GeoDataFrame, hazard: gpd.GeoDataFrame):
    if points.empty:
        return points.copy(), points.copy()
    hu = hazard_union(hazard)
    if hu is None:
        return points.iloc[0:0].copy(), points.copy()
    mask = points.intersects(hu)
    return points[mask].copy(), points[~mask].copy()


def overlay_heritage(ax, footprints: gpd.GeoDataFrame, points: gpd.GeoDataFrame, point_in=None, point_out=None):
    # point underlay
    if point_out is not None and not point_out.empty:
        point_out.plot(ax=ax, markersize=12, color="#F0E442", edgecolor="black", linewidth=0.2, alpha=0.85, zorder=6)
    elif points is not None and not points.empty:
        points.plot(ax=ax, markersize=10, color="black", alpha=0.7, zorder=6)
    # filled footprints on top of points
    if footprints is not None and not footprints.empty:
        footprints.plot(ax=ax, facecolor="#555555", edgecolor="black", linewidth=0.3, alpha=0.75, zorder=7)
    if point_in is not None and not point_in.empty:
        point_in.plot(ax=ax, markersize=18, color="#D55E00", edgecolor="black", linewidth=0.25, alpha=0.9, zorder=8)


def detail_bbox(lon: float, lat: float, radius_m: float = 800.0):
    # approx for initial bbox then use web mercator exact bounds
    center = gpd.GeoSeries([gpd.points_from_xy([lon], [lat], crs=WGS84)[0]], crs=WGS84).to_crs(WEBM)
    p = center.iloc[0]
    minx, miny, maxx, maxy = p.x - radius_m, p.y - radius_m, p.x + radius_m, p.y + radius_m
    bbox3857 = (minx, miny, maxx, maxy)
    bbox4326 = gpd.GeoSeries([box(*bbox3857)], crs=WEBM).to_crs(WGS84).total_bounds
    return bbox3857, tuple(bbox4326)


def fig_save(fig, out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=220, bbox_inches="tight")
    plt.close(fig)


def plot_seismic_overview(gpkg: Path, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    admin = load_admin(gpkg)
    pts = load_points(gpkg)
    regions = region_groups(admin)
    for layer in list_layers(gpkg):
        if not layer.startswith("hazard_seismic_50m_"):
            continue
        hz = read_layer(gpkg, layer)
        scenario = str(hz["scenario"].dropna().iloc[0]) if "scenario" in hz.columns and hz["scenario"].notna().any() else scenario_from_layer(layer, "hazard_seismic_50m_")
        value_col = find_seismic_value_column(hz)
        for region, bbox in regions.items():
            hzc = clip_gdf(hz, bbox)
            if hzc.empty:
                continue
            ptc = clip_gdf(pts, bbox)
            adc = clip_gdf(admin, bbox)
            fig, ax = plt.subplots(figsize=(8, 8))
            hzc.plot(column=value_col, ax=ax, cmap="plasma_r", legend=True, alpha=0.68, linewidth=0, zorder=1)
            draw_admin(ax, adc, color="gray", lw=0.5, zorder=3)
            if not ptc.empty:
                ptc.plot(ax=ax, markersize=6, color="black", alpha=0.7, zorder=4)
            ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3]); ax.set_axis_off()
            ax.set_title(f"想定震度：{scenario} / {region}")
            fig_save(fig, outdir / f"seismic_{slugify(scenario)}_{region}.png")


def plot_fire_overview(gpkg: Path, outdir: Path):
    try:
        layer = next(l for l in list_layers(gpkg) if l.startswith("hazard_fire_spread"))
    except StopIteration:
        return
    hz = read_layer(gpkg, layer)
    admin = load_admin(gpkg)
    pts = load_points(gpkg)
    regions = region_groups(admin)
    value_col = find_fire_value_column(hz)
    for region, bbox in regions.items():
        hzc = clip_gdf(hz, bbox)
        if hzc.empty:
            continue
        ptc = clip_gdf(pts, bbox)
        adc = clip_gdf(admin, bbox)
        fig, ax = plt.subplots(figsize=(8, 8))
        if value_col:
            hzc.plot(column=value_col, ax=ax, cmap="YlOrRd", legend=True, alpha=0.68, linewidth=0, zorder=1)
        else:
            hzc.plot(ax=ax, color="#fdae61", alpha=0.5, linewidth=0, zorder=1)
        draw_admin(ax, adc, color="gray", lw=0.5, zorder=3)
        if not ptc.empty:
            ptc.plot(ax=ax, markersize=6, color="black", alpha=0.75, zorder=4)
        ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3]); ax.set_axis_off()
        ax.set_title(f"火災延焼危険度 / {region}")
        fig_save(fig, outdir / f"fire_{region}.png")


def plot_inundation_overview(gpkg: Path, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    admin = load_admin(gpkg)
    pts = load_points(gpkg)
    bbox = region_groups(admin)["mainland"]
    layers = [l for l in list_layers(gpkg) if l.startswith("hazard_inundation")]
    for layer in layers:
        hz = read_layer(gpkg, layer)
        hzc = clip_gdf(hz, bbox)
        if hzc.empty:
            continue
        ptc = clip_gdf(pts, bbox)
        adc = clip_gdf(admin, bbox)
        depth_col = find_inundation_depth_column(hzc)
        inside, outside = split_points_inside(ptc, hzc)
        fig, ax = plt.subplots(figsize=(9, 9))
        if depth_col:
            hzc.plot(column=depth_col, ax=ax, cmap="Blues", legend=True, alpha=0.50, linewidth=0.2, edgecolor="#3182bd", zorder=1)
        else:
            hzc.plot(ax=ax, color="#9ecae1", alpha=0.45, linewidth=0.2, edgecolor="#3182bd", zorder=1)
        draw_admin(ax, adc, color="gray", lw=0.5, zorder=3)
        overlay_heritage(ax, None, ptc, point_in=inside, point_out=outside)
        handles = [
            Patch(facecolor="#9ecae1", edgecolor="#3182bd", label="浸水区域"),
            Line2D([], [], marker="o", linestyle="", markersize=6, markerfacecolor="#D55E00", markeredgecolor="black", label="区域内文化財"),
            Line2D([], [], marker="o", linestyle="", markersize=6, markerfacecolor="#F0E442", markeredgecolor="black", label="区域外文化財"),
        ]
        ax.legend(handles=handles, loc="lower left", fontsize=8)
        ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3]); ax.set_axis_off()
        title = layer.replace("hazard_inundation_", "").replace("hazard_inundation_a31a_", "")
        ax.set_title(f"浸水想定区域：{title}")
        fig_save(fig, outdir / f"inundation_{slugify(title)}.png")


def relevant_inundation_layers(gpkg: Path, bbox4326):
    result = []
    for layer in list_layers(gpkg):
        if not layer.startswith("hazard_inundation"):
            continue
        hz = read_layer(gpkg, layer, bbox=bbox4326)
        if not hz.empty:
            result.append((layer, hz))
    return result


def read_detail_context(gpkg: Path, bbox4326):
    fps = clip_gdf(load_footprints(gpkg), bbox4326)
    pts = clip_gdf(load_points(gpkg), bbox4326)
    admin = clip_gdf(load_admin(gpkg), bbox4326)
    return fps, pts, admin


def plot_detail_maps(gpkg: Path, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    detail_inund_dir = outdir / "inundation_center"
    layers = list_layers(gpkg)
    try:
        fire_layer = next(l for l in layers if l.startswith("hazard_fire_spread"))
    except StopIteration:
        fire_layer = None

    # seismic details
    seismic_layers = []
    for sc in DETAIL_SEISMIC_SCENARIOS:
        match = next((l for l in layers if l.startswith("hazard_seismic_50m_") and sc in l), None)
        if match:
            seismic_layers.append((sc, match))

    for key, (lon, lat, label) in DETAIL_CENTERS.items():
        bbox3857, bbox4326 = detail_bbox(lon, lat, 900)
        fps, pts, admin = read_detail_context(gpkg, bbox4326)

        # seismic
        for scenario, layer in seismic_layers:
            hz = read_layer(gpkg, layer, bbox=bbox4326)
            if hz.empty:
                continue
            value_col = find_seismic_value_column(hz)
            hz3857 = hz.to_crs(WEBM)
            fps3857 = fps.to_crs(WEBM) if not fps.empty else fps
            pts3857 = pts.to_crs(WEBM) if not pts.empty else pts
            admin3857 = admin.to_crs(WEBM) if not admin.empty else admin
            fig, ax = plt.subplots(figsize=(8, 8))
            hz3857.plot(column=value_col, ax=ax, cmap="plasma_r", legend=True, alpha=0.62, linewidth=0, zorder=1)
            add_gsi_basemap(ax, zoom=16)
            if not admin3857.empty:
                admin3857.boundary.plot(ax=ax, color="gray", linewidth=0.5, zorder=5)
            overlay_heritage(ax, fps3857, pts3857)
            ax.set_xlim(bbox3857[0], bbox3857[2]); ax.set_ylim(bbox3857[1], bbox3857[3]); ax.set_axis_off()
            ax.set_title(f"{label} Z=16：想定震度（{scenario}）")
            fig_save(fig, outdir / f"{key}_seismic_{slugify(scenario)}.png")

        # fire
        if fire_layer:
            hz = read_layer(gpkg, fire_layer, bbox=bbox4326)
            if not hz.empty:
                value_col = find_fire_value_column(hz)
                hz3857 = hz.to_crs(WEBM)
                fps3857 = fps.to_crs(WEBM) if not fps.empty else fps
                pts3857 = pts.to_crs(WEBM) if not pts.empty else pts
                admin3857 = admin.to_crs(WEBM) if not admin.empty else admin
                fig, ax = plt.subplots(figsize=(8, 8))
                if value_col:
                    hz3857.plot(column=value_col, ax=ax, cmap="YlOrRd", legend=True, alpha=0.55, linewidth=0, zorder=1)
                else:
                    hz3857.plot(ax=ax, color="#fdae61", alpha=0.55, linewidth=0, zorder=1)
                add_gsi_basemap(ax, zoom=16)
                if not admin3857.empty:
                    admin3857.boundary.plot(ax=ax, color="gray", linewidth=0.5, zorder=5)
                overlay_heritage(ax, fps3857, pts3857)
                ax.set_xlim(bbox3857[0], bbox3857[2]); ax.set_ylim(bbox3857[1], bbox3857[3]); ax.set_axis_off()
                ax.set_title(f"{label} Z=16：火災延焼危険度")
                fig_save(fig, outdir / f"{key}_fire.png")

        # inundation layers, including A31a 荒川・多摩川
        rel = relevant_inundation_layers(gpkg, bbox4326)
        for layer, hz in rel:
            depth_col = find_inundation_depth_column(hz)
            inside, outside = split_points_inside(pts, hz)
            hz3857 = hz.to_crs(WEBM)
            fps3857 = fps.to_crs(WEBM) if not fps.empty else fps
            in3857 = inside.to_crs(WEBM) if not inside.empty else inside
            out3857 = outside.to_crs(WEBM) if not outside.empty else outside
            admin3857 = admin.to_crs(WEBM) if not admin.empty else admin
            fig, ax = plt.subplots(figsize=(8, 8))
            if depth_col:
                hz3857.plot(column=depth_col, ax=ax, cmap="Blues", legend=True, alpha=0.48, linewidth=0.25, edgecolor="#3182bd", zorder=1)
            else:
                hz3857.plot(ax=ax, color="#9ecae1", alpha=0.42, linewidth=0.25, edgecolor="#3182bd", zorder=1)
            add_gsi_basemap(ax, zoom=16)
            if not admin3857.empty:
                admin3857.boundary.plot(ax=ax, color="gray", linewidth=0.5, zorder=5)
            overlay_heritage(ax, fps3857, pts.iloc[0:0], point_in=in3857, point_out=out3857)
            title = layer.replace("hazard_inundation_", "").replace("hazard_inundation_a31a_", "")
            ax.set_xlim(bbox3857[0], bbox3857[2]); ax.set_ylim(bbox3857[1], bbox3857[3]); ax.set_axis_off()
            ax.set_title(f"{label} Z=16：浸水想定区域（{title}）")
            fig_save(fig, detail_inund_dir / f"{key}_inundation_{slugify(title)}.png")


def parse_args():
    p = argparse.ArgumentParser(description="Render summary maps from 13_heritage_hazards.gpkg")
    p.add_argument("gpkg", help="Path to input hazard GPKG")
    p.add_argument("--stage", choices=["overview", "detail", "all"], default="all")
    p.add_argument("--output-root", default=str(FIG_ROOT), help="Base output directory")
    return p.parse_args()


def main():
    args = parse_args()
    gpkg = Path(args.gpkg)
    out_root = Path(args.output_root)
    overview = out_root / "overview"
    detail = out_root / "detail"

    if args.stage in {"overview", "all"}:
        print("=== OVERVIEW ===")
        plot_seismic_overview(gpkg, overview)
        plot_fire_overview(gpkg, overview)
        plot_inundation_overview(gpkg, overview)
    if args.stage in {"detail", "all"}:
        print("=== DETAIL ===")
        plot_detail_maps(gpkg, detail)
    print(f"SUCCESS: {out_root}")


if __name__ == "__main__":
    main()
