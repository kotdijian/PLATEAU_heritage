from __future__ import annotations
import argparse
from pathlib import Path
import sys
from .config import load_config
from .pipeline import run_area
from .util import validate_area_code

def main():
    p = argparse.ArgumentParser(
        prog="heritage-gml",
        description="Generate Heritage-GML from PLATEAU CityGML and local cultural-property data."
    )
    p.add_argument("--area-code", required=True,
                   help="2-digit prefecture code or 5-digit municipality code, without check digit.")
    p.add_argument("--data-dir", default=".",
                   help="Directory containing pre-fetched cultural-property CSV/JSON/GeoJSON files.")
    p.add_argument("--config", default=None, help="Optional YAML configuration.")
    p.add_argument("--plateau-source", choices=["api","local"], default="api")
    p.add_argument("--plateau-local-dir", default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="Only discover/filter cultural datasets and enumerate PLATEAU municipalities.")
    p.add_argument("--resume", action="store_true",
                   help="Skip municipalities whose output/run_summary.json is already completed.")
    args = p.parse_args()

    try:
        validate_area_code(args.area_code)
        cfg = load_config(args.config, Path.cwd())
        run_area(args.area_code, args.data_dir, cfg,
                 plateau_source=args.plateau_source,
                 plateau_local_dir=args.plateau_local_dir,
                 dry_run=args.dry_run, resume=args.resume)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
