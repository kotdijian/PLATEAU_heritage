#!/usr/bin/env python3
from pathlib import Path

p = Path('tools/render_summary_maps.py')
if not p.exists():
    raise SystemExit('ERROR: tools/render_summary_maps.py not found; run from repository root')

s = p.read_text(encoding='utf-8')

if 'def plot_liquefaction_overviews(' in s and 'def plot_landslide_overview(' in s:
    print('Already patched:', p)
    raise SystemExit(0)

insert_anchor = '\n\ndef a31a_depth_plot_values(hz: gpd.GeoDataFrame) -> pd.Series:\n'
if insert_anchor not in s:
    raise SystemExit('ERROR: insertion anchor not found; repository version differs')

block = r'''

def liquefaction_scenarios(source: Path) -> list[str]:
    """Return non-empty scenarios stored in hazard_liquefaction_250m."""
    uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=180) as con:
        rows = con.execute(
            """
            SELECT DISTINCT scenario
            FROM hazard_liquefaction_250m
            WHERE scenario IS NOT NULL AND TRIM(scenario) <> ''
            ORDER BY scenario
            """
        ).fetchall()
    return [str(row[0]).strip() for row in rows if str(row[0]).strip()]


def plot_liquefaction_overviews(
    source: Path,
    points: gpd.GeoDataFrame,
    outdir: Path,
    admin: gpd.GeoDataFrame | None = None,
) -> None:
    """Render actual 250 m liquefaction PL meshes for every stored scenario."""
    bbox = REGION_BBOX["mainland"]
    scenarios = liquefaction_scenarios(source)
    if not scenarios:
        print("[overview liquefaction] WARNING: no scenarios found")
        return

    for scenario in scenarios:
        print(f"[overview liquefaction] {scenario}")
        escaped = scenario.replace("'", "''")
        hz = pyogrio.read_dataframe(
            source,
            layer="hazard_liquefaction_250m",
            columns=["scenario", "liquefaction_pl"],
            bbox=bbox,
            where=f"scenario = '{escaped}'",
        )
        hz = ensure_wgs84(hz)
        if hz.empty:
            continue

        hz = hz.copy()
        hz["_liquefaction_pl"] = pd.to_numeric(
            hz["liquefaction_pl"], errors="coerce"
        )
        hz = hz[hz["_liquefaction_pl"].notna()].copy()
        if hz.empty:
            continue

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
            pts.plot(
                ax=ax,
                color="black",
                markersize=4,
                alpha=0.75,
                zorder=25,
            )

        add_admin_overlay(ax, admin)
        ax.set_xlim(bbox[0], bbox[2])
        ax.set_ylim(bbox[1], bbox[3])
        ax.set_title(f"東京都本土部：液状化 {scenario}")
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        fig.tight_layout()

        safe = re.sub(
            r"[^0-9A-Za-z一-龠ぁ-んァ-ヶ_-]+", "_", scenario
        )
        fig.savefig(
            outdir / f"liquefaction_{safe}_mainland.png",
            dpi=200,
            bbox_inches="tight",
        )
        plt.close(fig)


def plot_landslide_overview(
    source: Path,
    contents: pd.DataFrame,
    points: gpd.GeoDataFrame,
    out: Path,
    tables_dir: Path,
    admin: gpd.GeoDataFrame | None = None,
) -> None:
    """Render actual A33/A46/A47/A52 sediment/landslide hazard layers."""
    bbox = REGION_BBOX["mainland"]
    prefixes = [
        ("hazard_sediment_warning_a33_", "土砂災害警戒区域", "#D73027"),
        ("hazard_landslide_prevention_a46_", "地すべり防止区域", "#FC8D59"),
        ("hazard_steep_slope_a47_", "急傾斜地崩壊危険区域", "#FEE08B"),
        ("hazard_sabo_designated_a52_", "砂防指定地", "#91CF60"),
    ]
    available = contents["table_name"].astype(str).tolist()
    drawn = []

    fig, ax = plt.subplots(figsize=(11, 8))
    for prefix, label, color in prefixes:
        layers = [name for name in available if name.startswith(prefix)]
        for layer in layers:
            hz = pyogrio.read_dataframe(source, layer=layer, bbox=bbox)
            hz = ensure_wgs84(hz)
            if hz.empty:
                continue
            geom_types = set(hz.geom_type.dropna().astype(str))
            if any("Polygon" in x for x in geom_types):
                hz.plot(
                    ax=ax, color=color, edgecolor=color,
                    linewidth=0.35, alpha=0.42, zorder=2,
                )
            elif any("LineString" in x for x in geom_types):
                hz.plot(
                    ax=ax, color=color, linewidth=0.8,
                    alpha=0.72, zorder=2,
                )
            else:
                hz.plot(
                    ax=ax, color=color, markersize=5,
                    alpha=0.72, zorder=2,
                )
            drawn.append((label, color))

    if not drawn:
        plt.close(fig)
        print("[overview landslide] WARNING: no hazard layers found")
        return

    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]].copy()
    affected = set()
    risk_path = tables_dir / "record_risk_types.csv"
    if risk_path.exists():
        risk = pd.read_csv(risk_path, dtype={"record_id": str})
        if {"record_id", "risk_type"}.issubset(risk.columns):
            affected = set(
                risk.loc[
                    risk["risk_type"].astype(str) == "landslide",
                    "record_id",
                ].astype(str)
            )

    if not pts.empty:
        ids = pts["record_id"].astype(str)
        outside = pts.loc[~ids.isin(affected)]
        inside = pts.loc[ids.isin(affected)]
        if not outside.empty:
            outside.plot(
                ax=ax, color="black", markersize=4,
                alpha=0.60, zorder=25,
            )
        if not inside.empty:
            inside.plot(
                ax=ax, color="red", markersize=7,
                alpha=0.90, zorder=26,
            )

    add_admin_overlay(ax, admin)
    ax.set_xlim(bbox[0], bbox[2])
    ax.set_ylim(bbox[1], bbox[3])
    ax.set_title("東京都本土部：土砂災害関連区域")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")

    handles = []
    seen = set()
    for label, color in drawn:
        if label in seen:
            continue
        handles.append(Patch(
            facecolor=color,
            edgecolor=color,
            alpha=0.55,
            label=label,
        ))
        seen.add(label)
    handles.extend([
        Line2D(
            [0], [0], marker="o", linestyle="",
            color="red", markersize=6,
            label="土砂災害リスク該当文化財",
        ),
        Line2D(
            [0], [0], marker="o", linestyle="",
            color="black", markersize=5,
            label="その他の文化財",
        ),
    ])
    ax.legend(handles=handles, loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
'''

s = s.replace(insert_anchor, block + insert_anchor, 1)

call_anchor = '''    plot_fire_overview(source, rep, overview / "fire_mainland.png", admin)\n    plot_inundation_overviews(source, contents, rep, overview, tables, admin)\n    plot_storm_overview(source, rep, overview / "storm_surge_mainland.png", admin)\n    plot_tsunami_overviews(source, contents, rep, overview, admin)\n'''
call_repl = '''    plot_fire_overview(source, rep, overview / "fire_mainland.png", admin)\n    plot_liquefaction_overviews(source, rep, overview, admin)\n    plot_inundation_overviews(source, contents, rep, overview, tables, admin)\n    plot_storm_overview(source, rep, overview / "storm_surge_mainland.png", admin)\n    plot_tsunami_overviews(source, contents, rep, overview, admin)\n    plot_landslide_overview(\n        source,\n        contents,\n        rep,\n        overview / "landslide_mainland.png",\n        tables,\n        admin,\n    )\n'''
if call_anchor not in s:
    raise SystemExit('ERROR: main() call anchor not found; repository version differs')
s = s.replace(call_anchor, call_repl, 1)

p.write_text(s, encoding='utf-8')
print('Patched:', p)
print('Added: liquefaction_<scenario>_mainland.png')
print('Added: landslide_mainland.png')
