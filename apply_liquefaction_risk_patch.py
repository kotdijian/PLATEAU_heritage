#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apply independent liquefaction-risk aggregation and red-point overview rendering.

Targets:
  tools/build_summary_results.py
  tools/render_summary_maps.py

Assumptions:
- The earlier liquefaction-overview patch has already added
  plot_one_liquefaction_overview(...) / plot_liquefaction_overviews(...)
  to render_summary_maps.py.
- Liquefaction source layers are discovered by prefix hazard_liquefaction*.

Risk-presence rule:
- A cultural-property record is counted as liquefaction risk when the maximum
  published PL value at any of its analysis locations is > 0 for at least one
  scenario.
- Raw PL values are preserved in CSV outputs.

Usage:
  cd /Users/noguchiatsushi/Documents/GitHub/PLATEAU_heritage
  python /path/to/apply_liquefaction_risk_patch.py
  python -m py_compile tools/build_summary_results.py tools/render_summary_maps.py
"""

from pathlib import Path
import shutil
import sys

BUILD = Path("tools/build_summary_results.py")
RENDER = Path("tools/render_summary_maps.py")

BUILD_MARKER = "# --- LIQUEFACTION RISK PATCH BEGIN ---"
RENDER_MARKER = "# --- LIQUEFACTION RED-POINT PATCH BEGIN ---"


def backup(path: Path) -> None:
    bak = path.with_suffix(path.suffix + ".bak_liquefaction_risk")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"backup: {bak}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"patch target not found: {label}")
    return text.replace(old, new, 1)


def patch_build() -> None:
    if not BUILD.exists():
        raise RuntimeError(f"not found: {BUILD}")
    backup(BUILD)
    text = BUILD.read_text(encoding="utf-8")

    if BUILD_MARKER not in text:
        anchor = "\ndef landslide_presence("
        pos = text.find(anchor)
        if pos < 0:
            raise RuntimeError("def landslide_presence(...) not found")

        block = r'''

# --- LIQUEFACTION RISK PATCH BEGIN ---
LIQUEFACTION_LAYER_PREFIX = "hazard_liquefaction"
LIQUEFACTION_RISK_MIN_PL = 0.0


def classify_liquefaction_pl(value) -> str:
    """Human-readable PL class; raw PL remains the primary numeric value."""
    if pd.isna(value):
        return "No data"
    x = float(value)
    if x <= 0:
        return "PL=0"
    if x <= 5:
        return "0<PL≤5"
    if x <= 15:
        return "5<PL≤15"
    return "PL>15"


def detect_liquefaction_value_column(gdf: gpd.GeoDataFrame) -> str | None:
    preferred = [
        "liquefaction_pl",
        "PLcorrected",
        "Plcorrecte",
        "PL値",
        "pl",
        "PL",
    ]
    for col in preferred:
        if col in gdf.columns:
            values = pd.to_numeric(gdf[col], errors="coerce")
            if values.notna().any():
                return col
    for col in gdf.columns:
        if col == "geometry":
            continue
        if "pl" in str(col).lower():
            values = pd.to_numeric(gdf[col], errors="coerce")
            if values.notna().any():
                return col
    return None


def liquefaction_results(
    source: Path,
    locations: gpd.GeoDataFrame,
    meta: pd.DataFrame,
    contents: pd.DataFrame,
    tables: Path,
) -> pd.DataFrame:
    """
    Assign published liquefaction PL values to cultural-property locations.

    Supports both a combined hazard_liquefaction* layer with a scenario
    column and scenario-specific hazard_liquefaction* layers.
    """
    names = contents["table_name"].fillna("").astype(str)
    layer_rows = contents[
        names.str.startswith(LIQUEFACTION_LAYER_PREFIX)
    ].copy()

    out_cols = [
        "record_id",
        "municipality_code",
        "municipality_name",
        "designation_level",
        "designation_status",
        "heritage_type_major",
        "heritage_type_detail_norm",
        "entity_class",
        "name",
        "scenario",
        "liquefaction_pl",
        "liquefaction_class",
        "risk_type",
        "risk_type_ja",
        "hazard_source",
        "risk_basis",
    ]

    if layer_rows.empty:
        print("[liquefaction] WARNING: no hazard_liquefaction* layers found")
        empty = pd.DataFrame(columns=out_cols)
        empty.to_csv(
            tables / "liquefaction_all_scenarios_records.csv",
            index=False,
            encoding="utf-8-sig",
        )
        empty.to_csv(
            tables / "liquefaction_risk_records.csv",
            index=False,
            encoding="utf-8-sig",
        )
        return empty

    parts = []

    for _, info in layer_rows.iterrows():
        layer = str(info["table_name"])
        targets = subset_locations_for_extent(locations, info)
        if targets.empty:
            continue

        print(f"[liquefaction] {layer}: targets={len(targets):,}")
        hz = pyogrio.read_dataframe(source, layer=layer)
        hz = ensure_wgs84(hz)
        if hz.empty:
            continue

        value_col = detect_liquefaction_value_column(hz)
        if value_col is None:
            print(
                f"[liquefaction] WARNING: PL value column not found in {layer}; "
                f"columns={list(hz.columns)}"
            )
            continue

        hz = hz.copy()
        hz["_liquefaction_pl"] = pd.to_numeric(
            hz[value_col], errors="coerce"
        )
        hz = hz[hz["_liquefaction_pl"].notna()].copy()
        if hz.empty:
            continue

        if "scenario" not in hz.columns:
            scenario = layer
            for prefix in (
                "hazard_liquefaction_250m_",
                "hazard_liquefaction_",
            ):
                if scenario.startswith(prefix):
                    scenario = scenario[len(prefix):]
                    break
            if scenario in {"", layer, "250m"}:
                scenario = layer.replace("hazard_", "")
            hz["scenario"] = scenario
        else:
            hz["scenario"] = (
                hz["scenario"].fillna("").astype(str).str.strip()
            )
            fallback = layer
            for prefix in (
                "hazard_liquefaction_250m_",
                "hazard_liquefaction_",
            ):
                if fallback.startswith(prefix):
                    fallback = fallback[len(prefix):]
                    break
            hz.loc[hz["scenario"] == "", "scenario"] = fallback

        joined = spatial_join_polygons(
            targets,
            hz,
            ["scenario", "_liquefaction_pl"],
        )
        if joined.empty:
            continue

        joined["scenario"] = joined["scenario"].fillna("").astype(str)
        agg = (
            joined.groupby(["record_id", "scenario"], as_index=False)[
                "_liquefaction_pl"
            ]
            .max()
            .rename(columns={"_liquefaction_pl": "liquefaction_pl"})
        )

        rec = meta.merge(agg, on="record_id", how="inner")
        rec["liquefaction_class"] = rec["liquefaction_pl"].map(
            classify_liquefaction_pl
        )
        rec["risk_type"] = "liquefaction"
        rec["risk_type_ja"] = "液状化"
        rec["hazard_source"] = layer
        rec["risk_basis"] = "liquefaction mesh polygon intersection"
        parts.append(rec[out_cols])

    if parts:
        all_records = pd.concat(parts, ignore_index=True)
        all_records = (
            all_records
            .sort_values(["record_id", "scenario", "liquefaction_pl"])
            .drop_duplicates(["record_id", "scenario"], keep="last")
        )
    else:
        all_records = pd.DataFrame(columns=out_cols)

    all_records.to_csv(
        tables / "liquefaction_all_scenarios_records.csv",
        index=False,
        encoding="utf-8-sig",
    )

    risk_records = all_records[
        pd.to_numeric(all_records["liquefaction_pl"], errors="coerce")
        > LIQUEFACTION_RISK_MIN_PL
    ].copy()
    risk_records.to_csv(
        tables / "liquefaction_risk_records.csv",
        index=False,
        encoding="utf-8-sig",
    )

    if not all_records.empty:
        for scenario, sub in all_records.groupby("scenario", dropna=False):
            scenario = str(scenario).strip() or "unknown"
            sub.to_csv(
                tables / f"liquefaction_{slug(scenario)}_records.csv",
                index=False,
                encoding="utf-8-sig",
            )

        class_order = [
            "PL=0",
            "0<PL≤5",
            "5<PL≤15",
            "PL>15",
            "No data",
        ]
        for label, dim in {
            "municipality": "municipality_name",
            "designation_level": "designation_level",
            "designation_status": "designation_status",
            "cultural_type": "heritage_type_major",
        }.items():
            tab = crosstab_count(
                all_records,
                dim,
                "liquefaction_class",
                class_order,
            )
            tab.to_csv(
                tables / f"liquefaction_{label}.csv",
                index=False,
                encoding="utf-8-sig",
            )

    print(
        "[liquefaction] records with PL="
        f"{all_records['record_id'].nunique() if not all_records.empty else 0:,}; "
        "risk records (PL>0)="
        f"{risk_records['record_id'].nunique() if not risk_records.empty else 0:,}"
    )
    return all_records


# --- LIQUEFACTION RISK PATCH END ---
'''
        text = text[:pos] + block + text[pos:]
        print("build: inserted liquefaction aggregation block")
    else:
        print("build: aggregation block already present")

    # build_risk_presence signature
    old_sig = '''def build_risk_presence(
    meta: pd.DataFrame,
    seismic: pd.DataFrame,
    fire: pd.DataFrame,
    water: pd.DataFrame,
'''
    new_sig = '''def build_risk_presence(
    meta: pd.DataFrame,
    seismic: pd.DataFrame,
    fire: pd.DataFrame,
    liquefaction: pd.DataFrame,
    water: pd.DataFrame,
'''
    if new_sig not in text:
        text = replace_once(text, old_sig, new_sig, "build_risk_presence signature")
        print("build: added liquefaction argument")

    old_presence = '''    if not fire.empty:
        for rid in fire.loc[fire["fire_class"].notna(), "record_id"].unique():
            rows.append((rid, "fire"))
    if not water.empty:
'''
    new_presence = '''    if not fire.empty:
        for rid in fire.loc[fire["fire_class"].notna(), "record_id"].unique():
            rows.append((rid, "fire"))
    if not liquefaction.empty:
        liq_risk = liquefaction[
            pd.to_numeric(
                liquefaction["liquefaction_pl"], errors="coerce"
            ) > LIQUEFACTION_RISK_MIN_PL
        ]
        for rid in liq_risk["record_id"].dropna().astype(str).unique():
            rows.append((rid, "liquefaction"))
    if not water.empty:
'''
    if 'rows.append((rid, "liquefaction"))' not in text:
        text = replace_once(text, old_presence, new_presence, "liquefaction risk presence")
        print("build: added liquefaction risk_type rows")

    old_contents = '''    contents = gpkg_contents(source)

    print("\\n=== A31a FLOOD POLYGON ASSIGNMENT ===")
'''
    new_contents = '''    contents = gpkg_contents(source)

    print("\\n=== LIQUEFACTION ===")
    liquefaction = liquefaction_results(
        source,
        locations,
        meta,
        contents,
        tables,
    )

    print("\\n=== A31a FLOOD POLYGON ASSIGNMENT ===")
'''
    if 'print("\\n=== LIQUEFACTION ===")' not in text:
        text = replace_once(text, old_contents, new_contents, "main liquefaction step")
        print("build: added LIQUEFACTION step")

    old_call = '''    risk_long = build_risk_presence(meta, seismic, fire, best_water, a31a, landslide, tables)
'''
    new_call = '''    risk_long = build_risk_presence(meta, seismic, fire, liquefaction, best_water, a31a, landslide, tables)
'''
    if new_call not in text:
        text = replace_once(text, old_call, new_call, "build_risk_presence call")
        print("build: wired liquefaction into risk summary")

    old_summary = '''        "a31a_flood_records": int(a31a["record_id"].nunique()) if not a31a.empty else 0,
        "risk_type_rows": int(len(risk_long)),
'''
    new_summary = '''        "a31a_flood_records": int(a31a["record_id"].nunique()) if not a31a.empty else 0,
        "liquefaction_records_with_pl": int(
            liquefaction["record_id"].nunique()
        ) if not liquefaction.empty else 0,
        "liquefaction_risk_records": int(
            liquefaction.loc[
                pd.to_numeric(
                    liquefaction["liquefaction_pl"], errors="coerce"
                ) > LIQUEFACTION_RISK_MIN_PL,
                "record_id",
            ].nunique()
        ) if not liquefaction.empty else 0,
        "risk_type_rows": int(len(risk_long)),
'''
    if '"liquefaction_risk_records"' not in text:
        text = replace_once(text, old_summary, new_summary, "run_summary liquefaction counts")
        print("build: added run_summary counts")

    BUILD.write_text(text, encoding="utf-8")
    print(f"updated: {BUILD}")


def patch_render() -> None:
    if not RENDER.exists():
        raise RuntimeError(f"not found: {RENDER}")
    backup(RENDER)
    text = RENDER.read_text(encoding="utf-8")

    if "from matplotlib.lines import Line2D" not in text:
        if "import matplotlib.pyplot as plt\n" in text:
            text = text.replace(
                "import matplotlib.pyplot as plt\n",
                "import matplotlib.pyplot as plt\nfrom matplotlib.lines import Line2D\n",
                1,
            )
        else:
            raise RuntimeError("matplotlib.pyplot import not found")

    if RENDER_MARKER not in text:
        anchor = "\ndef plot_one_liquefaction_overview("
        pos = text.find(anchor)
        if pos < 0:
            raise RuntimeError(
                "plot_one_liquefaction_overview(...) not found. "
                "Apply the earlier liquefaction-overview patch first."
            )

        helper = r'''

# --- LIQUEFACTION RED-POINT PATCH BEGIN ---
def load_liquefaction_risk_ids_for_scenario(
    out: Path,
    scenario: str,
) -> set[str]:
    """Load scenario-specific liquefaction risk records produced by the builder."""
    try:
        results_dir = out.parents[2]
    except IndexError:
        return set()

    path = results_dir / "tables" / "liquefaction_risk_records.csv"
    if not path.exists():
        print(
            "[overview liquefaction] WARNING: "
            f"{path} not found; run build_summary_results.py first"
        )
        return set()

    df = pd.read_csv(path, dtype={"record_id": str, "scenario": str})
    if "record_id" not in df.columns:
        return set()

    if "scenario" in df.columns:
        wanted = str(scenario).strip()
        df = df[
            df["scenario"].fillna("").astype(str).str.strip() == wanted
        ]

    return set(df["record_id"].dropna().astype(str))


# --- LIQUEFACTION RED-POINT PATCH END ---
'''
        text = text[:pos] + helper + text[pos:]
        print("render: inserted risk-ID helper")

    start = text.find("def plot_one_liquefaction_overview(")
    end = text.find("\ndef plot_liquefaction_overviews(", start)
    if start < 0 or end < 0:
        raise RuntimeError("could not isolate plot_one_liquefaction_overview")

    block = text[start:end]
    if "液状化リスク該当文化財（PL>0）" not in block:
        old_points = '''    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
    if not pts.empty:
        pts.plot(
            ax=ax,
            color="black",
            markersize=4,
            alpha=0.75,
            zorder=25,
        )
'''
        new_points = '''    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]].copy()
    risk_ids = load_liquefaction_risk_ids_for_scenario(out, scenario)

    if not pts.empty:
        ids = pts["record_id"].astype(str)
        hit = pts.loc[ids.isin(risk_ids)]
        other = pts.loc[~ids.isin(risk_ids)]

        if not other.empty:
            other.plot(
                ax=ax,
                color="black",
                markersize=4,
                alpha=0.60,
                zorder=25,
            )
        if not hit.empty:
            hit.plot(
                ax=ax,
                color="red",
                markersize=7,
                alpha=0.95,
                zorder=26,
            )

        ax.legend(
            handles=[
                Line2D(
                    [0], [0], marker="o", linestyle="",
                    color="red", markersize=6,
                    label="液状化リスク該当文化財（PL>0）",
                ),
                Line2D(
                    [0], [0], marker="o", linestyle="",
                    color="black", markersize=5,
                    label="その他の文化財",
                ),
            ],
            loc="best",
            fontsize=8,
        )
'''
        if old_points in block:
            block = block.replace(old_points, new_points, 1)
        else:
            compact_points = """    pts = points.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
    if not pts.empty:
        pts.plot(ax=ax, color="black", markersize=4, alpha=0.75, zorder=25)
"""
            if compact_points not in block:
                raise RuntimeError(
                    "existing all-black point block was not found in "
                    "plot_one_liquefaction_overview"
                )
            block = block.replace(compact_points, new_points, 1)
        text = text[:start] + block + text[end:]
        print("render: liquefaction-risk points changed to red")
    else:
        print("render: red-point logic already present")

    RENDER.write_text(text, encoding="utf-8")
    print(f"updated: {RENDER}")


def main() -> None:
    try:
        patch_build()
        patch_render()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

    print("\nPATCH COMPLETE")
    print("Backups use suffix .bak_liquefaction_risk")
    print("Next: py_compile, rebuild Summary Results with --force, then render overview.")


if __name__ == "__main__":
    main()
