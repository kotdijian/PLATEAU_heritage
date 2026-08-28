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
from .output import write_gpkg
from .util import json_dump, validate_area_code
from .model import PlateauCity


def _write_csv(path: Path, rows):
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _prefixed_name(city_code: str, name: str) -> str:
    """Return a municipality-prefixed output filename, idempotently."""
    prefix = f"{city_code}_"
    return name if name.startswith(prefix) else prefix + name


def _city_path(city_dir: Path, city_code: str, name: str) -> Path:
    return city_dir / _prefixed_name(city_code, name)


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
            year="local", feature_types=["bldg"], url="",
        )]

    payload = fetch_plateau_catalog(p_cfg["api_base"], int(p_cfg["timeout_s"]))
    return cities_for_area(payload, code)


def _normalized_rows(records):
    rows = []
    for r in records:
        rows.append({
            "source_file": r.source_file,
            "record_id": r.record_id,
            "name": r.name,
            "place_name": r.place_name,
            "address_detail": r.address_detail,
            "owner": r.owner,
            "address": r.address,
            "municipality": r.municipality,
            "municipality_code": r.municipality_code,
            "category": r.category,
            "type": r.type,
            "designation": r.designation,
            "designation_date": r.designation_date,
            "entity_class": r.entity_class,
            "geometry_role": r.geometry_role,
            "movable": r.movable,
            "source_location_role": r.source_location_role,
            "spatial_match_status": r.spatial_match_status,
            "complex_id": r.complex_id,
            "complex_name": r.complex_name,
            "complex_grouping_method": r.complex_grouping_method,
            "complex_record_count": r.complex_record_count,
            "matched_building_ids": ";".join(r.matched_building_ids),
            "match_methods": ";".join(r.match_methods),
            "geometry_type": r.geometry.geom_type if r.geometry is not None else "",
            "longitude": r.geometry.x if r.geometry is not None and r.geometry.geom_type == "Point" else "",
            "latitude": r.geometry.y if r.geometry is not None and r.geometry.geom_type == "Point" else "",
            "geometry_wkt": r.geometry.wkt if r.geometry is not None else "",
        })
    return rows


def run_area(area_code: str, data_dir: str | Path, cfg: dict,
             plateau_source: str = "api", plateau_local_dir: str | None = None,
             dry_run: bool = False, resume: bool = False):
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
    total_loaded = 0

    for city in cities:
        label = city.city or city.city_code
        print(f"\n[{city.city_code}] {label}")
        city_dir = out_root / city.city_code
        city_dir.mkdir(parents=True, exist_ok=True)

        if resume:
            summary_path = _city_path(city_dir, city.city_code, "run_summary.json")
            if summary_path.exists():
                try:
                    previous = json.loads(summary_path.read_text(encoding="utf-8"))
                except Exception:
                    previous = {}
                if previous.get("status", "completed") in ("completed", "completed_with_plateau_download_errors"):
                    print("  resume: already completed; skipping")
                    overall.append({**previous, "status": previous.get("status", "completed")})
                    continue

        records, data_issues = load_records_for_city(source_files, city, cfg["cultural"])
        records = assign_complexes(records)
        total_loaded += len(records)
        print(f"  cultural records: {len(records)}")
        if records:
            counts = pd.Series([r.entity_class for r in records]).value_counts().to_dict()
            print("  entity classes: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

        _write_csv(_city_path(city_dir, city.city_code, "input_issues.csv"), data_issues)
        _write_csv(
            _city_path(city_dir, city.city_code, "cultural_records_normalized.csv"),
            _normalized_rows(records),
        )

        if not records:
            overall.append({
                "city_code": city.city_code, "city": city.city,
                "status": "no_local_cultural_records",
            })
            continue

        if dry_run:
            overall.append({
                "city_code": city.city_code, "city": city.city, "status": "dry_run",
                "cultural_records": len(records),
                "building_direct": sum(r.entity_class == "building_direct" for r in records),
                "point": sum(r.entity_class == "point" for r in records),
                "movable": sum(r.entity_class == "movable" for r in records),
                "complexes": len({r.complex_id for r in records}),
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
                use_geocode=bool(p_cfg.get("use_geocoding_condition_for_unlocated", False)),
            )
            gml_files, download_issues = download_files(
                gml_files, p_cfg["cache_dir"], int(p_cfg["timeout_s"]),
                connect_timeout_s=p_cfg.get("connect_timeout_s", p_cfg.get("timeout_s", 120)),
                read_timeout_s=p_cfg.get("read_timeout_s", p_cfg.get("timeout_s", 120)),
                retries=int(p_cfg.get("download_retries", 3)),
                backoff_s=float(p_cfg.get("retry_backoff_s", 2.0)),
            )

        if plateau_source == "local":
            download_issues = []
        _write_csv(_city_path(city_dir, city.city_code, "plateau_query_issues.csv"), query_issues)
        _write_csv(_city_path(city_dir, city.city_code, "plateau_download_issues.csv"), download_issues)
        _write_csv(
            _city_path(city_dir, city.city_code, "plateau_files.csv"),
            [asdict(x) for x in gml_files],
        )
        usable_gml_files = [x for x in gml_files if x.local_path]
        print(f"  PLATEAU bldg GML files: {len(usable_gml_files)}/{len(gml_files)} downloaded")

        acquisition_issues = list(query_issues) + list(download_issues)
        if acquisition_issues:
            status = "plateau_query_failed" if query_issues else "plateau_download_failed"
            failed = {
                "city_code": city.city_code,
                "city": city.city,
                "plateau_year": city.year,
                "cultural_records": len(records),
                "plateau_files_expected": len(gml_files),
                "plateau_files_downloaded": len(usable_gml_files),
                "plateau_query_errors": len(query_issues),
                "plateau_download_errors": len(download_issues),
                "status": status,
            }
            json_dump(_city_path(city_dir, city.city_code, "run_summary.json"), failed)
            overall.append(failed)
            print(
                f"  WARNING: PLATEAU acquisition incomplete "
                f"(query={len(query_issues)}, download={len(download_issues)}); "
                "municipality deferred"
            )
            continue

        buildings = scan_buildings(usable_gml_files)
        print(f"  scanned buildings: {len(buildings)}")

        result = match_city(records, buildings, cfg["matching"])
        selected = result["selected"]
        print(f"  matched buildings: {len(selected)}")
        print(f"  building complexes: {sum(bool(x.get('matched_building_count')) for x in result['complex_rows'])}")
        print(f"  output heritage points: {sum(p.get('geometry') is not None for p in result['point_rows'])}")

        # Matching mutates record status; rewrite normalized data with final links.
        _write_csv(
            _city_path(city_dir, city.city_code, "cultural_records_normalized.csv"),
            _normalized_rows(records),
        )
        _write_csv(_city_path(city_dir, city.city_code, "heritage_building_links.csv"), result["links"])
        _write_csv(_city_path(city_dir, city.city_code, "heritage_complex_summary.csv"), result["complex_rows"])
        _write_csv(_city_path(city_dir, city.city_code, "heritage_complex_members.csv"), result["complex_member_rows"])
        _write_csv(_city_path(city_dir, city.city_code, "heritage_complex_records.csv"), result["complex_record_rows"])
        _write_csv(_city_path(city_dir, city.city_code, "heritage_point_features.csv"), [
            {k: (v.wkt if k == "geometry" and v is not None else v)
             for k, v in row.items()}
            for row in result["point_rows"]
        ])
        _write_csv(_city_path(city_dir, city.city_code, "heritage_unresolved_entities.csv"), result["unresolved_rows"])

        written = set()
        if selected:
            written = write_subset(
                _city_path(city_dir, city.city_code, cfg["output"]["subset_gml_name"]),
                usable_gml_files,
                selected,
                embed_generic=bool(cfg["output"].get("embed_generic_attributes", True)),
            )

        hdoc = build_heritage_document(city.city_code, city.city, records, buildings, result)
        write_json(
            _city_path(city_dir, city.city_code, cfg["output"]["heritage_json_name"]),
            hdoc,
        )
        write_xml(
            _city_path(city_dir, city.city_code, cfg["output"]["heritage_xml_name"]),
            hdoc,
        )

        write_gpkg(
            _city_path(city_dir, city.city_code, cfg["output"]["gpkg_name"]),
            records,
            buildings,
            selected,
            result["point_rows"],
            result["links"],
            result["complex_rows"],
            result["complex_member_rows"],
            result["complex_record_rows"],
            result["unresolved_rows"],
        )

        summary = {
            "city_code": city.city_code,
            "city": city.city,
            "plateau_year": city.year,
            "cultural_records": len(records),
            "building_direct_records": sum(r.entity_class == "building_direct" for r in records),
            "point_records": sum(r.entity_class == "point" for r in records),
            "movable_records": sum(r.entity_class == "movable" for r in records),
            "complexes": len({r.complex_id for r in records}),
            "building_complexes": sum(bool(x.get("matched_building_count")) for x in result["complex_rows"]),
            "complex_member_links": len(result["complex_member_rows"]),
            "plateau_files": len(usable_gml_files),
            "scanned_buildings": len(buildings),
            "selected_buildings": len(selected),
            "written_gml_buildings": len(written),
            "heritage_point_features": sum(p.get("geometry") is not None for p in result["point_rows"]),
            "complex_only_records": sum(r.spatial_match_status == "complex_only" for r in records),
            "shared_complex_coordinate_records": sum(r.source_location_role == "shared_complex_coordinate" for r in records),
            "unresolved_entities": len(result["unresolved_rows"]),
        }
        summary["status"] = "completed"
        json_dump(_city_path(city_dir, city.city_code, "run_summary.json"), summary)
        overall.append(summary)

    if total_loaded == 0:
        raise RuntimeError(
            "No cultural-property records were recognized for any target municipality. "
            "Check that --data-dir contains cultural-property record datasets rather than API catalogs/manifests."
        )

    area_prefix = code
    _write_csv(out_root / f"{area_prefix}_area_summary.csv", overall)
    json_dump(out_root / f"{area_prefix}_area_summary.json", {
        "area_code": code,
        "mode": mode,
        "cities_considered": len(cities),
        "results": overall,
    })
    return overall
