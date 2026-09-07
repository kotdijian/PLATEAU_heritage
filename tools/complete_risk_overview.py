from __future__ import annotations

import re
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import pandas as pd
import pyogrio

LANDSLIDE_PREFIXES = {
    "hazard_sediment_warning_a33_": ("土砂災害警戒区域", "#D73027"),
    "hazard_landslide_prevention_a46_": ("地すべり防止区域", "#FC8D59"),
    "hazard_steep_slope_a47_": ("急傾斜地崩壊危険区域", "#FEE08B"),
    "hazard_sabo_designated_a52_": ("砂防指定地", "#91CF60"),
}


def _ensure_wgs84(gdf):
    if gdf.crs is None:
        return gdf.set_crs(4326)
    if gdf.crs.to_epsg() != 4326:
        return gdf.to_crs(4326)
    return gdf


def _safe(text):
    return re.sub(r"[^0-9A-Za-z一-龠ぁ-んァ-ヶ_-]+", "_", str(text))


def _liq_value_col(gdf):
    for col in ("liquefaction_pl", "PLcorrected", "Plcorrecte", "PL値", "pl", "PL"):
        if col in gdf.columns and pd.to_numeric(gdf[col], errors="coerce").notna().any():
            return col
    for col in gdf.columns:
        if col != "geometry" and "pl" in str(col).lower():
            if pd.to_numeric(gdf[col], errors="coerce").notna().any():
                return col
    return None


def plot_liquefaction_overviews(source, points, outdir, tables_dir, admin=None):
    layer_names = [str(row[0]) for row in pyogrio.list_layers(source)]
    layers = [x for x in layer_names if x.startswith("hazard_liquefaction")]
    if not layers:
        print("[overview liquefaction] WARNING: no hazard_liquefaction* layers")
        return

    risk_path = tables_dir / "liquefaction_risk_records.csv"
    risk = pd.read_csv(risk_path, dtype={"record_id": str}) if risk_path.exists() else pd.DataFrame(columns=["record_id", "scenario"])
    bbox = (138.90, 35.48, 139.95, 35.93)
    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]].copy()

    for layer in layers:
        hz = _ensure_wgs84(pyogrio.read_dataframe(source, layer=layer, bbox=bbox))
        if hz.empty:
            continue
        value_col = _liq_value_col(hz)
        if value_col is None:
            print(f"[overview liquefaction] WARNING: PL column missing: {layer}")
            continue
        hz = hz.copy()
        hz["_pl"] = pd.to_numeric(hz[value_col], errors="coerce")
        hz = hz[hz["_pl"].notna()].copy()
        if "scenario" in hz.columns:
            scenarios = sorted(x for x in hz["scenario"].fillna("").astype(str).str.strip().unique().tolist() if x)
        else:
            scenario = layer.replace("hazard_liquefaction_250m_", "").replace("hazard_liquefaction_", "")
            scenarios = [scenario if scenario not in ("", "250m", layer) else "液状化"]

        for scenario in scenarios:
            sub = hz.copy()
            if "scenario" in sub.columns:
                sub = sub[sub["scenario"].fillna("").astype(str).str.strip() == scenario].copy()
            if sub.empty:
                continue
            rr = risk.copy()
            if "scenario" in rr.columns:
                rr = rr[rr["scenario"].fillna("").astype(str).str.strip() == scenario]
            ids = set(rr["record_id"].dropna().astype(str)) if not rr.empty else set()
            hit = pts[pts["record_id"].astype(str).isin(ids)].copy()
            other = pts[~pts["record_id"].astype(str).isin(ids)].copy()

            fig, ax = plt.subplots(figsize=(11, 8))
            sub.plot(ax=ax, column="_pl", cmap="plasma_r", linewidth=0, alpha=0.72, legend=True, legend_kwds={"label": "液状化 PL値"}, zorder=1)
            if admin is not None and not admin.empty:
                admin.boundary.plot(ax=ax, color="#777777", linewidth=0.35, zorder=10)
            if not other.empty:
                other.plot(ax=ax, color="black", markersize=4, alpha=0.60, zorder=25)
            if not hit.empty:
                hit.plot(ax=ax, color="red", markersize=7, alpha=0.95, zorder=26)
            ax.legend(handles=[
                Line2D([0], [0], marker="o", linestyle="", color="red", markersize=6, label="液状化リスク該当文化財（PL>0）"),
                Line2D([0], [0], marker="o", linestyle="", color="black", markersize=5, label="その他の文化財"),
            ], loc="best", fontsize=8)
            ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
            ax.set_title(f"東京都本土部：液状化 {scenario}")
            ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
            fig.tight_layout()
            out = outdir / f"liquefaction_{_safe(scenario)}_mainland.png"
            fig.savefig(out, dpi=200, bbox_inches="tight")
            plt.close(fig)
            print(f"[overview liquefaction] {scenario} -> {out.name}")


def plot_landslide_overview(source, points, out, tables_dir, admin=None):
    layer_names = [str(row[0]) for row in pyogrio.list_layers(source)]
    bbox = (138.90, 35.48, 139.95, 35.93)
    found = []
    fig, ax = plt.subplots(figsize=(11, 8))

    for prefix, (label, color) in LANDSLIDE_PREFIXES.items():
        for layer in [x for x in layer_names if x.startswith(prefix)]:
            hz = _ensure_wgs84(pyogrio.read_dataframe(source, layer=layer, bbox=bbox))
            if hz.empty:
                continue
            gt = set(hz.geom_type.dropna().astype(str))
            if any("Polygon" in x for x in gt):
                hz.plot(ax=ax, color=color, edgecolor=color, linewidth=0.25, alpha=0.40, zorder=1)
            elif any("LineString" in x for x in gt):
                hz.plot(ax=ax, color=color, linewidth=0.7, alpha=0.65, zorder=1)
            else:
                hz.plot(ax=ax, color=color, markersize=5, alpha=0.65, zorder=1)
            found.append((label, color))

    if not found:
        plt.close(fig)
        print("[overview landslide] WARNING: no landslide-related layers")
        return

    risk_path = tables_dir / "landslide_risk_records.csv"
    ids = set()
    if risk_path.exists():
        rr = pd.read_csv(risk_path, dtype={"record_id": str})
        ids = set(rr["record_id"].dropna().astype(str))
    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]].copy()
    hit = pts[pts["record_id"].astype(str).isin(ids)].copy()
    other = pts[~pts["record_id"].astype(str).isin(ids)].copy()

    if admin is not None and not admin.empty:
        admin.boundary.plot(ax=ax, color="#777777", linewidth=0.35, zorder=10)
    if not other.empty:
        other.plot(ax=ax, color="black", markersize=4, alpha=0.60, zorder=25)
    if not hit.empty:
        hit.plot(ax=ax, color="red", markersize=7, alpha=0.95, zorder=26)

    handles = []
    seen = set()
    for label, color in found:
        if label not in seen:
            handles.append(Patch(facecolor=color, edgecolor=color, alpha=0.5, label=label)); seen.add(label)
    handles.extend([
        Line2D([0], [0], marker="o", linestyle="", color="red", markersize=6, label="土砂災害リスク該当文化財"),
        Line2D([0], [0], marker="o", linestyle="", color="black", markersize=5, label="その他の文化財"),
    ])
    ax.legend(handles=handles, loc="best", fontsize=8)
    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_title("東京都本土部：土砂災害関連区域")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    fig.tight_layout(); fig.savefig(out, dpi=200, bbox_inches="tight"); plt.close(fig)
    print(f"[overview landslide] -> {out.name}")
