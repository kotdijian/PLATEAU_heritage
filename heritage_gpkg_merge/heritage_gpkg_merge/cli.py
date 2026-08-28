from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .merge import merge_prefecture, write_reports


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="heritage-gpkg-merge",
        description=(
            "Merge municipality <5-digit>_heritage.gpkg files into one "
            "prefecture-level <2-digit>_heritage.gpkg."
        ),
    )
    p.add_argument("--input-root", required=True, help="Root containing municipality output folders/GPKGs")
    p.add_argument("--pref-code", required=True, help="2-digit prefecture code, e.g. 13")
    p.add_argument("--output", help="Output GPKG. Default: <input-root>/<pref-code>_heritage.gpkg")
    p.add_argument(
        "--codes", nargs="*", help="Optional exact 5-digit municipality codes. Missing requested codes cause an error."
    )
    p.add_argument("--target-crs", default="EPSG:4326", help="Target CRS (default EPSG:4326)")
    p.add_argument("--no-report", action="store_true", help="Do not write merge report CSV/JSON")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = merge_prefecture(
            input_root=args.input_root,
            prefecture_code=args.pref_code,
            output_path=args.output,
            codes=args.codes,
            target_crs=args.target_crs,
        )
        out = Path(result.output_path)
        if not args.no_report:
            report = out.with_name(f"{args.pref_code}_heritage_merge_report.csv")
            manifest = out.with_name(f"{args.pref_code}_heritage_merge_manifest.json")
            write_reports(result, report, manifest)
            print(f"report: {report}")
            print(f"manifest: {manifest}")
        print(f"output: {out}")
        print(f"municipalities: {result.source_count}")
        for name, count in sorted(result.spatial_layers.items()):
            print(f"  spatial {name}: {count}")
        for name, count in sorted(result.attribute_tables.items()):
            print(f"  table   {name}: {count}")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
