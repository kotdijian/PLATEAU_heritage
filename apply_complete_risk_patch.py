#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import py_compile
import shutil
import sys

ROOT = Path.cwd()
BUILD = ROOT / "tools" / "build_summary_results.py"
RENDER = ROOT / "tools" / "render_summary_maps.py"
CITY = ROOT / "tools" / "render_city_hazard_focus.py"
HERE = Path(__file__).resolve().parent
SUMMARY_SRC = HERE / "complete_risk_summary.py"
OVERVIEW_SRC = HERE / "complete_risk_overview.py"
SUMMARY_DST = ROOT / "tools" / "complete_risk_summary.py"
OVERVIEW_DST = ROOT / "tools" / "complete_risk_overview.py"


def backup(path: Path):
    bak = path.with_suffix(path.suffix + ".bak_complete_risk")
    if not bak.exists():
        shutil.copy2(path, bak)
        print("backup:", bak)
    return bak


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(label + ": already applied")
        return text
    if old not in text:
        raise RuntimeError(label + ": expected source text not found")
    print(label + ": applied")
    return text.replace(old, new, 1)


def main():
    for p in (BUILD, RENDER, CITY, SUMMARY_SRC, OVERVIEW_SRC):
        if not p.exists():
            raise SystemExit(f"ERROR: missing {p}")

    for p in (BUILD, RENDER, CITY):
        backup(p)

    shutil.copy2(SUMMARY_SRC, SUMMARY_DST)
    shutil.copy2(OVERVIEW_SRC, OVERVIEW_DST)
    print("installed:", SUMMARY_DST)
    print("installed:", OVERVIEW_DST)

    # ------------------------------------------------------------
    # build_summary_results.py
    # ------------------------------------------------------------
    text = BUILD.read_text(encoding="utf-8")

    old_scenarios = '''SCENARIOS = [
    "都心南部直下地震",
    "都心東部直下地震",
    "都心西部直下地震",
    "大正関東地震",
    "南海トラフ巨大地震",
]'''
    new_scenarios = '''SCENARIOS = [
    "都心南部直下地震",
    "都心東部直下地震",
    "都心西部直下地震",
    "多摩東部直下地震",
    "多摩西部直下地震",
    "立川断層帯地震",
    "大正関東地震",
    "南海トラフ巨大地震",
]'''
    text = replace_once(text, old_scenarios, new_scenarios, "build: 8 seismic scenarios")

    import_anchor = "import pyogrio\n"
    import_line = "from complete_risk_summary import build_complete_risk_summary\n"
    if import_line not in text:
        if import_anchor not in text:
            raise RuntimeError("build: import anchor not found")
        text = text.replace(import_anchor, import_anchor + import_line, 1)
        print("build: helper import applied")

    old_call = '    risk_long = build_risk_presence(meta, seismic, fire, best_water, a31a, landslide, tables)'
    new_call = '''    risk_long = build_complete_risk_summary(
        source=source,
        locations=locations,
        meta=meta,
        seismic=seismic,
        fire=fire,
        native=native,
        external=external,
        a31a=a31a,
        contents=contents,
        tables=tables,
        metadata_dir=metadata_dir,
    )'''
    text = replace_once(text, old_call, new_call, "build: complete risk summary call")
    BUILD.write_text(text, encoding="utf-8")

    # ------------------------------------------------------------
    # render_summary_maps.py
    # ------------------------------------------------------------
    text = RENDER.read_text(encoding="utf-8")
    import_anchor = "import pyogrio\n"
    import_line = (
        "from complete_risk_overview import "
        "plot_liquefaction_overviews as complete_plot_liquefaction_overviews, "
        "plot_landslide_overview as complete_plot_landslide_overview\n"
    )
    if "complete_plot_liquefaction_overviews" not in text:
        if import_anchor not in text:
            raise RuntimeError("render: import anchor not found")
        text = text.replace(import_anchor, import_anchor + import_line, 1)
        print("render: helper import applied")

    fire_line = '    plot_fire_overview(source, rep, overview / "fire_mainland.png", admin)\n'
    inund_line = '    plot_inundation_overviews(source, contents, rep, overview, tables, admin)'
    fire_pos = text.find(fire_line)
    inund_pos = text.find(inund_line, fire_pos if fire_pos >= 0 else 0)
    if fire_pos < 0 or inund_pos < 0:
        raise RuntimeError("render: overview main anchors not found")
    between_start = fire_pos + len(fire_line)
    replacement_mid = (
        '    complete_plot_liquefaction_overviews(source, rep, overview, tables, admin)\n'
        '    complete_plot_landslide_overview(\n'
        '        source, rep, overview / "landslide_mainland.png", tables, admin\n'
        '    )\n'
    )
    text = text[:between_start] + replacement_mid + text[inund_pos:]
    print("render: normalized liquefaction + landslide overview calls")
    RENDER.write_text(text, encoding="utf-8")

    # ------------------------------------------------------------
    # render_city_hazard_focus.py
    # Make municipality red-point classification consistent with PL>0.
    # ------------------------------------------------------------
    text = CITY.read_text(encoding="utf-8")
    if 'if "liquefaction_pl" in hazard.columns:' in text:
        print("city: liquefaction PL>0 hit rule already present")
    else:
        anchor = '''    if points.empty or hazard.empty:
        return points.copy(), points.iloc[0:0].copy()

    try:'''
        insert = '''    if points.empty or hazard.empty:
        return points.copy(), points.iloc[0:0].copy()

    # Liquefaction: only PL>0 is an independent risk hit.
    if "liquefaction_pl" in hazard.columns:
        hazard = hazard[
            gpd.pd.to_numeric(hazard["liquefaction_pl"], errors="coerce") > 0
        ].copy()
        if hazard.empty:
            return points.copy(), points.iloc[0:0].copy()

    try:'''
        if anchor not in text:
            raise RuntimeError("city: classify_points_by_hazard anchor not found")
        text = text.replace(anchor, insert, 1)
        print("city: liquefaction PL>0 hit rule applied")
    CITY.write_text(text, encoding="utf-8")

    # ------------------------------------------------------------
    # Syntax check with rollback on failure.
    # ------------------------------------------------------------
    targets = [BUILD, RENDER, CITY, SUMMARY_DST, OVERVIEW_DST]
    try:
        for p in targets:
            py_compile.compile(str(p), doraise=True)
            print("compile OK:", p)
    except Exception:
        print("ERROR: compile failed; restoring original main scripts", file=sys.stderr)
        for p in (BUILD, RENDER, CITY):
            bak = p.with_suffix(p.suffix + ".bak_complete_risk")
            if bak.exists():
                shutil.copy2(bak, p)
        raise

    b = BUILD.read_text(encoding="utf-8")
    r = RENDER.read_text(encoding="utf-8")
    checks = {
        "8 seismic scenarios": all(x in b for x in ["多摩東部直下地震", "多摩西部直下地震", "立川断層帯地震"]),
        "complete risk import": "from complete_risk_summary import build_complete_risk_summary" in b,
        "complete risk call": "risk_long = build_complete_risk_summary(" in b,
        "liquefaction overview": "plot_liquefaction_overviews(" in r,
        "landslide overview": "plot_landslide_overview(" in r,
    }
    print("\nVERIFY")
    failed = False
    for name, ok in checks.items():
        print(("  OK   " if ok else "  FAIL ") + name)
        failed |= not ok
    if failed:
        raise SystemExit(2)

    print("\nPATCH COMPLETE")
    print("Next: regenerate Summary Results with --force, then run render_summary_maps.py")


if __name__ == "__main__":
    main()
