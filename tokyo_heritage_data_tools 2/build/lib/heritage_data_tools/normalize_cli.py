from __future__ import annotations

import argparse
import sys

from .normalizers.municipal import normalize as normalize_municipal
from .normalizers.national import normalize as normalize_national


def main():
    p = argparse.ArgumentParser(
        prog="heritage-normalize",
        description="Normalize raw national or municipal cultural-property data for Heritage-GML."
    )
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("municipal")
    m.add_argument("--input", required=True)
    m.add_argument("--output", required=True)
    m.add_argument("--config", default=None)

    n = sub.add_parser("national")
    n.add_argument("--input", required=True)
    n.add_argument("--output", required=True)

    args = p.parse_args()
    try:
        if args.command == "municipal":
            normalize_municipal(args.input, args.output, config_path=args.config)
        else:
            normalize_national(args.input, args.output)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
