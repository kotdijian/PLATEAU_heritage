from __future__ import annotations

import argparse
import sys

from .collectors.municipal import collect as collect_municipal
from .collectors.national import collect_online, ingest_official_csv


def main():
    p = argparse.ArgumentParser(
        prog="heritage-collect",
        description="Collect raw national or municipal cultural-property data."
    )
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("municipal", help="Collect municipal open data from a source manifest.")
    m.add_argument("--manifest", required=True)
    m.add_argument("--output", required=True)
    m.add_argument("--area-code", default="13", help="2-digit prefecture code; Tokyo prototype defaults to 13.")
    m.add_argument("--codes", nargs="*", default=None, help="Optional 5-digit municipality codes.")
    m.add_argument("--connect-timeout", type=int, default=30)
    m.add_argument("--read-timeout", type=int, default=120)
    m.add_argument("--retries", type=int, default=3)
    m.add_argument("--overwrite", action="store_true")
    m.add_argument("--api-page-size", type=int, default=1000, help="Tokyo Open Data API records per request (default: 1000).")

    n = sub.add_parser("national", help="Collect national cultural-property source data.")
    nsub = n.add_subparsers(dest="national_mode", required=True)

    online = nsub.add_parser(
        "online",
        help="Collect records from Cultural Heritage Online filtered to the national database."
    )
    online.add_argument("--pref-code", default="13")
    online.add_argument("--output", required=True)
    online.add_argument("--connect-timeout", type=int, default=30)
    online.add_argument("--read-timeout", type=int, default=120)
    online.add_argument("--retries", type=int, default=3)
    online.add_argument("--workers", type=int, default=4)
    online.add_argument("--delay", type=float, default=0.15)
    online.add_argument("--max-pages", type=int, default=None, help="For testing/partial runs.")
    online.add_argument("--max-details", type=int, default=None, help="For testing/partial runs.")
    online.add_argument("--no-save-html", action="store_true")
    online.add_argument("--no-resume", action="store_true")

    ingest = nsub.add_parser(
        "ingest",
        help="Preserve manually exported CSV files from the official national database as raw input."
    )
    ingest.add_argument("--input", nargs="+", required=True)
    ingest.add_argument("--output", required=True)
    ingest.add_argument("--overwrite", action="store_true")

    args = p.parse_args()
    try:
        if args.command == "municipal":
            collect_municipal(
                args.manifest, args.output,
                area_code=args.area_code,
                codes=args.codes,
                timeout=(args.connect_timeout, args.read_timeout),
                retries=args.retries,
                overwrite=args.overwrite,
                api_page_size=args.api_page_size,
            )
        elif args.national_mode == "online":
            collect_online(
                args.pref_code, args.output,
                timeout=(args.connect_timeout, args.read_timeout),
                retries=args.retries,
                workers=args.workers,
                delay=args.delay,
                save_html=not args.no_save_html,
                resume=not args.no_resume,
                max_pages=args.max_pages,
                max_details=args.max_details,
            )
        else:
            ingest_official_csv(args.input, args.output, overwrite=args.overwrite)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
