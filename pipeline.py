from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import json
import pandas as pd
from .catalog import fetch_plateau_catalog, cities_for_area
from .cultural import discover_files, load_records_for_city, assign_complexes
from .plateau import resolve_remote_files, download_files, local_files
from .citygml import scan_buildings, write_subset
from .matching import match_city
from .heritage import build_heritage_document, write_json, write_xml
from .output import write_gpkg, write_geojsons
from .util import json_dump, validate_area_code
from .model import PlateauCity

def _write_csv(path: Path, rows):
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")

def _cities(area_code, cfg, plateau_source):
    mode, code = validate_area_code(area_code)
    p_cfg = cfg["plateau"]
    if p_cfg.get("catalog_file"):
        payload = json.loads(Path(p_cfg["catalog_file"]).read_text(encoding="utf-8"))
        return cities_for_area(payload, code)

    if plateau_source == "local" and mode == "municipality":
        try:
            payload = fetch_plateau_catalog(p_cfg["api_base"], int(p_cfg["timeout_s"]))
            rows = cities_for_area(payload, code)
            if rows:
                return rows
        except Exception:
            pass
        return [PlateauCity(
            pref_code=code[:2], pref="", city_code=code, city="",
            year="local", feature_types=["bldg"], url=""
        )]

    payload = fetch_plateau_catalog(p_cfg["api_base"], int(p_cfg["timeout_s"]))
    return cities_for_area(payload, code)

def run_area(area_code: str, data_dir: str | Path, cfg: dict,
             plateau_source: str = "api", plateau_local_dir: str | None = None,
             dry_run: bool = False):
    mode, code = validate_area_code(area_code)
    data_dir = Path(data_dir).resolve()
    out_root = Path(cfg["output"]["dir"])
    out_root.mkdir(parents=True, exist_ok=True)

    cities = _cities(code, cfg, plateau_source)
    if not cities:
        raise RuntimeError(f"No PLATEAU city with bldg data found for area code {code}.")

    source_files = discover_files(data_dir, bool(cfg["cultural"].get("recursive", False)))
    if not source_files:
        raise RuntimeError(f"No local CSV/JSON/GeoJSON cultural-property files found in {data_dir}")

    overall = []
    for city in cities:
        label = city.city or city.city_code
        print(f"\n[{city.city_code}] {label}")
        city_dir = out_root / city.city_code
        city_dir.mkdir(parents=True, exist_ok=True)

        records, data_issues = load_records_for_city(source_files, city, cfg["cultural"])
        records = assign_complexes(records)
        print(f"  cultural records: {len(records)}")
        if not records:
            overall.append({"city_code": city.city_code, "city": city.city,
                            "status": "no_local_cultural_records"})
            continue

        normalized = []
        for r in records:
            normalized.append({
                "source_file": r.source_file, "record_id": r.record_id, "name": r.name,
                "place_name": r.place_name, "owner": r.owner, "address": r.address,
                "municipality": r.municipality, "municipality_code": r.municipality_code,
                "category": r.category, "type": r.type, "designation": r.designation,
                "designation_date": r.designation_date, "movable": r.movable,
                "complex_id": r.complex_id, "complex_name": r.complex_name,
                "geometry_type": r.geometry.geom_type if r.geometry is not None else "",
                "geometry_wkt": r.geometry.wkt if r.geometry is not None else ""
            })
        _write_csv(city_dir / "cultural_records_normalized.csv", normalized)
        _write_csv(city_dir / "input_issues.csv", data_issues)

        if dry_run:
            overall.append({
                "city_code": city.city_code, "city": city.city, "status": "dry_run",
                "cultural_records": len(records),
                "complexes": len({r.complex_id for r in records})
            })
            continue

        p_cfg = cfg["plateau"]
        if plateau_source == "local":
            local_dir = plateau_local_dir or p_cfg.get("local_dir")
            if not local_dir:
                raise ValueError("--plateau-source local requires --plateau-local-dir or plateau.local_dir")
            gml_files = local_files(local_dir, city)
            query_issues = []
        else:
            gml_files, query_issues = resolve_remote_files(
                p_cfg["api_base"], city, records, int(p_cfg["timeout_s"]),
                use_geocode=bool(p_cfg.get("use_geocoding_condition_for_unlocated", False))
            )
            gml_files = download_files(gml_files, p_cfg["cache_dir"], int(p_cfg["timeout_s"]))

        _write_csv(city_dir / "plateau_query_issues.csv", query_issues)
        _write_csv(city_dir / "plateau_files.csv", [asdict(x) for x in gml_files])
        print(f"  PLATEAU bldg GML files: {len(gml_files)}")

        buildings = scan_buildings(gml_files)
        print(f"  scanned buildings: {len(buildings)}")

        selected, links, complex_rows, movable_rows, unresolved_rows = match_city(
            records, buildings, cfg["matching"]
        )
        print(f"  matched buildings: {len(selected)}")

        _write_csv(city_dir / "heritage_building_links.csv", links)
        _write_csv(city_dir / "heritage_complex_summary.csv", complex_rows)
        _write_csv(city_dir / "heritage_movable_items.csv", movable_rows)
        _write_csv(city_dir / "heritage_unresolved_complexes.csv", unresolved_rows)

        written = set()
        if selected:
            written = write_subset(
                city_dir / cfg["output"]["subset_gml_name"], gml_files, selected,
                embed_generic=bool(cfg["output"].get("embed_generic_attributes", True))
            )

        hdoc = build_heritage_document(city.city_code, city.city, records, links)
        write_json(city_dir / cfg["output"]["heritage_json_name"], hdoc)
        write_xml(city_dir / cfg["output"]["heritage_xml_name"], hdoc)

        write_gpkg(
            city_dir / cfg["output"]["gpkg_name"], records, buildings, selected,
            links, complex_rows, movable_rows, unresolved_rows
        )
        if cfg["output"].get("write_geojson", True):
            write_geojsons(city_dir, records, buildings, selected, complex_rows)

        summary = {
            "city_code": city.city_code, "city": city.city,
            "plateau_year": city.year, "cultural_records": len(records),
            "complexes": len({r.complex_id for r in records}),
            "plateau_files": len(gml_files), "scanned_buildings": len(buildings),
            "selected_buildings": len(selected), "written_gml_buildings": len(written),
            "movable_items": len(movable_rows),
            "unresolved_complexes": len(unresolved_rows)
        }
        json_dump(city_dir / "run_summary.json", summary)
        overall.append({**summary, "status": "completed"})

    _write_csv(out_root / f"area_{code}_summary.csv", overall)
    json_dump(out_root / f"area_{code}_summary.json", {
        "area_code": code, "mode": mode, "cities_considered": len(cities),
        "results": overall
    })
    return overall
