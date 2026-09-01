from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .classifier import classify_csv, classify_frame, detect_scope, resolve_municipal_cross_source, _read_csv_preserve


def _add_common(p):
    p.add_argument("--glossary", default=None, help="Override packaged heritage_classification_glossary.csv")
    p.add_argument("--overrides", default=None, help="Override packaged record_overrides_13118.csv")


def _write(df, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def main():
    p = argparse.ArgumentParser(
        prog="heritage-classify",
        description="Append cultural-property classification attributes without re-fetching data or regenerating geometry."
    )
    sub = p.add_subparsers(dest="command", required=True)

    one = sub.add_parser("file", help="Classify one existing CSV")
    one.add_argument("--input", required=True)
    one.add_argument("--output", required=True)
    one.add_argument("--scope", choices=["auto", "municipal", "national", "prefectural_tokyo"], default="auto")
    _add_common(one)

    batch = sub.add_parser("batch", help="Create classified GML-input CSVs from already-acquired Tokyo/municipal/national data")
    batch.add_argument("--tokyo", default=None, help="Existing Tokyo 130001_cultural_property.csv")
    batch.add_argument("--municipal", default=None, help="Prefer tidy/municipal_all_normalized.csv; municipal.csv also accepted")
    batch.add_argument("--national", default=None, help="Existing tidy/national.csv")
    batch.add_argument("--output-dir", required=True)
    _add_common(batch)

    args = p.parse_args()
    try:
        if args.command == "file":
            summary = classify_csv(
                args.input, args.output, scope=args.scope,
                glossary_path=args.glossary, overrides_path=args.overrides,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return

        if not any((args.tokyo, args.municipal, args.national)):
            raise ValueError("batch requires at least one of --tokyo, --municipal, --national")
        out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
        results = []
        tokyo_df = None; national_df = None

        if args.tokyo:
            raw = _read_csv_preserve(args.tokyo)
            tokyo_df, summary = classify_frame(raw, "prefectural_tokyo", args.glossary, args.overrides)
            pth = out / "130001_cultural_property_classified.csv"; _write(tokyo_df, pth)
            summary.update({"input": args.tokyo, "output": str(pth)}); results.append(summary)

        if args.national:
            raw = _read_csv_preserve(args.national)
            national_df, summary = classify_frame(raw, "national", args.glossary, args.overrides)
            pth = out / "national_classified.csv"; _write(national_df, pth)
            summary.update({"input": args.national, "output": str(pth)}); results.append(summary)

        if args.municipal:
            raw = _read_csv_preserve(args.municipal)
            municipal_df, summary = classify_frame(raw, "municipal", args.glossary, args.overrides)
            municipal_df, xstats = resolve_municipal_cross_source(municipal_df, national_df, tokyo_df)
            level = municipal_df["designation_level_code"].astype(str)
            ready = municipal_df[level == "municipal"].copy()
            cross = municipal_df[level.isin(["national", "prefectural"])].copy()
            review = municipal_df[~level.isin(["municipal", "national", "prefectural"])].copy()
            all_path = out / "municipal_all_classified.csv"
            ready_path = out / "municipal_classified.csv"
            cross_path = out / "municipal_classified_cross_level.csv"
            review_path = out / "municipal_classified_needs_review.csv"
            _write(municipal_df, all_path); _write(ready, ready_path); _write(cross, cross_path); _write(review, review_path)
            summary.update({
                "input": args.municipal,
                "output": str(ready_path),
                "municipal_ready": len(ready), "cross_level": len(cross), "needs_review_level": len(review),
                **xstats,
            })
            results.append(summary)

        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
