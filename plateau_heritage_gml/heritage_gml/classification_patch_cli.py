from __future__ import annotations

import argparse
import json
import sys

from .classification_patch import patch_gpkg, patch_gml


def main():
    p=argparse.ArgumentParser(
        prog="heritage-classification-patch",
        description="Append classification attributes to existing Heritage GeoPackage / subset CityGML without rerunning PLATEAU matching."
    )
    p.add_argument("--gpkg", required=True)
    p.add_argument("--classified", nargs="+", required=True, help="One or more *_classified.csv files")
    p.add_argument("--gml", default=None, help="Optional existing *_heritage_buildings.gml to patch after GPKG")
    p.add_argument("--output-gpkg", default=None)
    p.add_argument("--output-gml", default=None)
    p.add_argument("--in-place", action="store_true", help="Modify the supplied GPKG/GML in place. Back up first.")
    args=p.parse_args()
    try:
        gs=patch_gpkg(args.gpkg,args.classified,output_path=args.output_gpkg,in_place=args.in_place)
        result={"gpkg":gs}
        gpkg_for_gml=gs["gpkg"]
        if args.gml:
            result["gml"]=patch_gml(args.gml,gpkg_for_gml,output_path=args.output_gml,in_place=args.in_place)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        print(f"ERROR: {e}",file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
