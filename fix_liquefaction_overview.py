#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fix liquefaction overview rendering so it does not assume a single
hazard_liquefaction_250m table.

Usage:
    python fix_liquefaction_overview.py
"""

from pathlib import Path

path = Path("tools/render_summary_maps.py")
text = path.read_text(encoding="utf-8")

start = text.find("def liquefaction_scenarios(")
end = text.find("def plot_landslide_overview(")

if start < 0:
    raise SystemExit("ERROR: def liquefaction_scenarios(...) not found")
if end < 0 or end <= start:
    raise SystemExit("ERROR: def plot_landslide_overview(...) not found after liquefaction block")

replacement = r'''def discover_liquefaction_layers(source: Path) -> list[str]:
    """Discover liquefaction spatial layers without assuming one fixed table name."""
    contents = gpkg_contents(source)
    names = contents.loc[
        contents["table_name"].astype(str).str.startswith("hazard_liquefaction", na=False),
        "table_name",
    ].astype(str).tolist()
    return sorted(names)


def liquefaction_value_column(hz: gpd.GeoDataFrame) -> str | None:
    """Find a usable PL-value field."""
    preferred = [
        "liquefaction_pl",
        "PLcorrected",
        "Plcorrecte",
        "PL値",
        "pl",
        "PL",
    ]
    for col in preferred:
        if col in hz.columns:
            values = pd.to_numeric(hz[col], errors="coerce")
            if values.notna().any():
                return col

    for col in hz.columns:
        if col == "geometry":
            continue
        if "pl" in str(col).lower():
            values = pd.to_numeric(hz[col], errors="coerce")
            if values.notna().any():
                return col
    return None


def plot_one_liquefaction_overview(
    hz: gpd.GeoDataFrame,
    scenario: str,
    points: gpd.GeoDataFrame,
    out: Path,
    admin: gpd.GeoDataFrame | None = None,
) -> None:
    bbox = REGION_BBOX["mainland"]
    if hz.empty:
        return

    value_col = liquefaction_value_column(hz)
    if value_col is None:
        print(
            f"[overview liquefaction] WARNING: PL value column not found: {scenario}; "
            f"columns={list(hz.columns)}"
        )
        return

    hz = hz.copy()
    hz["_liquefaction_pl"] = pd.to_numeric(hz[value_col], errors="coerce")
    hz = hz[hz["_liquefaction_pl"].notna()].copy()
    if hz.empty:
        print(f"[overview liquefaction] WARNING: no numeric PL values: {scenario}")
        return

    fig, ax = plt.subplots(figsize=(11, 8))
    hz.plot(
        column="_liquefaction_pl",
        ax=ax,
        cmap="plasma_r",
        legend=True,
        linewidth=0,
        alpha=0.72,
        zorder=1,
        legend_kwds={"label": "液状化 PL 値"},
    )

    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
    if not pts.empty:
        pts.plot(ax=ax, color="black", markersize=4, alpha=0.75, zorder=25)

    add_admin_overlay(ax, admin)
    ax.set_xlim(bbox[0], bbox[2])
    ax.set_ylim(bbox[1], bbox[3])
    ax.set_title(f"東京都本土部：液状化 {scenario}")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)


def plot_liquefaction_overviews(
    source: Path,
    points: gpd.GeoDataFrame,
    outdir: Path,
    admin: gpd.GeoDataFrame | None = None,
) -> None:
    """Render liquefaction PL meshes from any hazard_liquefaction* layer layout."""
    bbox = REGION_BBOX["mainland"]
    layers = discover_liquefaction_layers(source)

    if not layers:
        print("[overview liquefaction] WARNING: no hazard_liquefaction* layers found")
        return

    print("[overview liquefaction] layers:")
    for layer in layers:
        print(f"  - {layer}")

    for layer in layers:
        try:
            hz = pyogrio.read_dataframe(source, layer=layer, bbox=bbox)
        except Exception as exc:
            print(f"[overview liquefaction] WARNING: read failed {layer}: {exc}")
            continue

        hz = ensure_wgs84(hz)
        if hz.empty:
            continue

        if "scenario" in hz.columns:
            scenario_values = hz["scenario"].dropna().astype(str).str.strip()
            scenarios = sorted(x for x in scenario_values.unique().tolist() if x)
            if scenarios:
                for scenario in scenarios:
                    sub = hz[hz["scenario"].astype(str).str.strip() == scenario].copy()
                    safe = re.sub(r"[^0-9A-Za-z一-龠ぁ-んァ-ヶ_-]+", "_", scenario)
                    out = outdir / f"liquefaction_{safe}_mainland.png"
                    print(
                        f"[overview liquefaction] {scenario} "
                        f"({layer}, {len(sub):,} features)"
                    )
                    plot_one_liquefaction_overview(sub, scenario, points, out, admin)
                continue

        scenario = layer
        for prefix in ("hazard_liquefaction_250m_", "hazard_liquefaction_"):
            if scenario.startswith(prefix):
                scenario = scenario[len(prefix):]
                break
        if scenario in {"250m", "", layer}:
            scenario = layer.replace("hazard_", "")

        safe = re.sub(r"[^0-9A-Za-z一-龠ぁ-んァ-ヶ_-]+", "_", scenario)
        out = outdir / f"liquefaction_{safe}_mainland.png"
        print(
            f"[overview liquefaction] {scenario} "
            f"({layer}, {len(hz):,} features)"
        )
        plot_one_liquefaction_overview(hz, scenario, points, out, admin)


'''

new_text = text[:start] + replacement + text[end:]
path.write_text(new_text, encoding="utf-8")
print(f"updated: {path}")
