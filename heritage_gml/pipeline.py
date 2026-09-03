from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import json
import re
import pandas as pd

from .catalog import fetch_plateau_catalog, cities_for_area
from .cultural import discover_files, load_records_for_city, assign_complexes
from .plateau import resolve_remote_files, download_files, local_files, purge_city_cache
from .citygml import scan_buildings, write_subset, CityGMLReadError
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


def _local_cities(area_code: str, local_dir: str | Path) -> list[PlateauCity]:
    """Enumerate local-mode municipalities without any network access."""
    mode, code = validate_area_code(area_code)
    if mode == "municipality":
        return [PlateauCity(
            pref_code=code[:2], pref="", city_code=code, city="",
            year="local", feature_types=["bldg"], url="",
        )]

    base = Path(local_dir).resolve()
    codes = set()
    if base.exists():
        for p in base.rglob("*.gml"):
            lname = p.name.lower()
            if "bldg" not in lname and "building" not in lname:
                continue
            for found in re.findall(r"(?<!\d)(\d{5})(?!\d)", str(p)):
                if found.startswith(code):
                    codes.add(found)
    return [PlateauCity(
        pref_code=c[:2], pref="", city_code=c, city="",
        year="local", feature_types=["bldg"], url="",
    ) for c in sorted(codes)]


def _cities(area_code, cfg, plateau_source, plateau_local_dir=None):
    mode, code = validate_area_code(area_code)
    p_cfg = cfg["plateau"]

    if plateau_source == "local":
        local_dir = plateau_local_dir or p_cfg.get("local_dir")
        if not local_dir:
            raise ValueError("--plateau-source local requires --plateau-local-dir or plateau.local_dir")
        print("PLATEAU source: local (offline; no catalog/API request)", flush=True)
        if p_cfg.get("catalog_file"):
            payload = json.loads(Path(p_cfg["catalog_file"]).read_text(encoding="utf-8"))
            rows = cities_for_area(payload, code)
            if rows:
                return rows
        return _local_cities(code, local_dir)

    if p_cfg.get("catalog_file"):
        print(f"PLATEAU catalog: local file {p_cfg['catalog_file']}", flush=True)
        payload = json.loads(Path(p_cfg["catalog_file"]).read_text(encoding="utf-8"))
        return cities_for_area(payload, code)

    print("PLATEAU catalog: resolving target municipalities from API...", flush=True)
    payload = fetch_plateau_catalog(p_cfg["api_base"], int(p_cfg["timeout_s"]))
    rows = cities_for_area(payload, code)
    print(f"PLATEAU catalog: {len(rows)} target municipality/municipalities", flush=True)
    return rows


def _download_remote_set(gml_files, p_cfg, *, progress=True):
    return download_files(
        gml_files, p_cfg["cache_dir"], int(p_cfg["timeout_s"]),
        connect_timeout_s=p_cfg.get("connect_timeout_s", p_cfg.get("timeout_s", 120)),
        read_timeout_s=p_cfg.get("read_timeout_s", p_cfg.get("timeout_s", 120)),
        retries=int(p_cfg.get("download_retries", 3)),
        backoff_s=float(p_cfg.get("retry_backoff_s", 2.0)),
        progress=progress,
    )


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
            "designation_level_code": r.designation_level_code,
            "designation_level_ja": r.designation_level_ja,
            "designation_status_code": r.designation_status_code,
            "designation_status_ja": r.designation_status_ja,
            "heritage_type_major_code": r.heritage_type_major_code,
            "heritage_type_major_ja": r.heritage_type_major_ja,
            "heritage_type_detail": r.heritage_type_detail,
            "classification_confidence": r.classification_confidence,
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


def _read_failure_summary(city, records, gml_files, error: CityGMLReadError,
                          plateau_source: str, recovery_events: list[dict], status: str):
    return {
        "city_code": city.city_code,
        "city": city.city,
        "plateau_year": city.year,
        "plateau_source": plateau_source,
        "cultural_records": len(records),
        "plateau_files": len([x for x in gml_files if x.local_path]),
        "failed_gml_path": error.path,
        "failed_gml_stage": error.stage,
        "failed_gml_error": f"{type(error.original).__name__}: {error.original}",
        "cache_recovery_count": len(recovery_events),
        "cache_recovery_events": recovery_events,
        "status": status,
    }


def run_area(area_code: str, data_dir: str | Path, cfg: dict,
             plateau_source: str = "api", plateau_local_dir: str | None = None,
             dry_run: bool = False, resume: bool = False,
             refresh_plateau_cache: bool = False):
    mode, code = validate_area_code(area_code)
    data_dir = Path(data_dir).resolve()
    out_root = Path(cfg["output"]["dir"])
    out_root.mkdir(parents=True, exist_ok=True)

    cities = _cities(code, cfg, plateau_source, plateau_local_dir)
    if not cities:
        if plateau_source == "local":
            raise RuntimeError(
                f"No local PLATEAU bldg GML municipality could be identified for area code {code}. "
                "Use a 5-digit municipality code or a local directory whose paths contain municipality codes."
            )
        raise RuntimeError(f"No PLATEAU city with bldg data found for area code {code}.")

    source_files = discover_files(data_dir, bool(cfg["cultural"].get("recursive", False)))
    if not source_files:
        raise RuntimeError(f"No local CSV/JSON/GeoJSON cultural-property files found in {data_dir}")

    overall = []
    total_loaded = 0

    for city in cities:
        label = city.city or city.city_code
        print(f"\n[{city.city_code}] {label}", flush=True)
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
                    print("  resume: already completed; skipping", flush=True)
                    overall.append({**previous, "status": previous.get("status", "completed")})
                    continue

        records, data_issues = load_records_for_city(source_files, city, cfg["cultural"])
        records = assign_complexes(records)
        total_loaded += len(records)
        print(f"  cultural records: {len(records)}", flush=True)
        if records:
            counts = pd.Series([r.entity_class for r in records]).value_counts().to_dict()
            print("  entity classes: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), flush=True)

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
        recovery_events: list[dict] = []

        if plateau_source == "local":
            local_dir = plateau_local_dir or p_cfg.get("local_dir")
            gml_files = local_files(local_dir, city)
            query_issues = []
            download_issues = []
            _write_csv(
                _city_path(city_dir, city.city_code, "plateau_files_local.csv"),
                [asdict(x) for x in gml_files],
            )
        else:
            print("  resolving PLATEAU bldg mesh files...", flush=True)
            gml_files, query_issues = resolve_remote_files(
                p_cfg["api_base"], city, records, int(p_cfg["timeout_s"]),
                use_geocode=bool(p_cfg.get("use_geocoding_condition_for_unlocated", False)),
                progress=bool(p_cfg.get("query_progress", False)),
            )
            if refresh_plateau_cache:
                target = purge_city_cache(p_cfg["cache_dir"], city.city_code)
                print(f"  PLATEAU cache refresh: removed {target}", flush=True)
            print(f"  acquiring/reusing {len(gml_files)} PLATEAU bldg GML file(s)...", flush=True)
            gml_files, download_issues = _download_remote_set(gml_files, p_cfg, progress=True)
            _write_csv(
                _city_path(city_dir, city.city_code, "plateau_files.csv"),
                [asdict(x) for x in gml_files],
            )

        _write_csv(_city_path(city_dir, city.city_code, "plateau_query_issues.csv"), query_issues)
        _write_csv(_city_path(city_dir, city.city_code, "plateau_download_issues.csv"), download_issues)
        usable_gml_files = [x for x in gml_files if x.local_path]
        source_word = "local" if plateau_source == "local" else "available"
        print(f"  PLATEAU bldg GML files: {len(usable_gml_files)}/{len(gml_files)} {source_word}", flush=True)

        acquisition_issues = list(query_issues) + list(download_issues)
        if not gml_files and plateau_source == "local":
            acquisition_issues.append({
                "city_code": city.city_code,
                "reason": "no_local_plateau_bldg_gml_files",
            })
        if acquisition_issues:
            status = "plateau_query_failed" if query_issues else "plateau_download_failed"
            if plateau_source == "local":
                status = "local_plateau_files_missing"
            failed = {
                "city_code": city.city_code,
                "city": city.city,
                "plateau_year": city.year,
                "plateau_source": plateau_source,
                "cultural_records": len(records),
                "plateau_files_expected": len(gml_files),
                "plateau_files_downloaded": len(usable_gml_files),
                "plateau_query_errors": len(query_issues),
                "plateau_download_errors": len(download_issues),
                "cache_recovery_count": 0,
                "cache_recovery_events": [],
                "status": status,
            }
            json_dump(_city_path(city_dir, city.city_code, "run_summary.json"), failed)
            overall.append(failed)
            print(
                f"  WARNING: PLATEAU acquisition incomplete "
                f"(query={len(query_issues)}, download={len(download_issues)}); "
                "municipality deferred",
                flush=True,
            )
            continue

        max_recovery = max(0, int(p_cfg.get("cache_recovery_retries", 1)))
        read_failure = None

        while True:
            try:
                buildings = scan_buildings(usable_gml_files, progress=True)
                break
            except CityGMLReadError as e:
                read_failure = e
                if plateau_source != "api" or len(recovery_events) >= max_recovery:
                    break

                event = {
                    "attempt": len(recovery_events) + 1,
                    "stage": e.stage,
                    "failed_path": e.path,
                    "error": f"{type(e.original).__name__}: {e.original}",
                    "action": "purge_municipality_cache_and_redownload_all",
                }
                recovery_events.append(event)
                print(
                    f"  WARNING: unreadable PLATEAU cache detected: {Path(e.path).name}",
                    flush=True,
                )
                target = purge_city_cache(p_cfg["cache_dir"], city.city_code)
                print(f"  cache recovery: removed complete municipality cache {target}", flush=True)
                print("  cache recovery: reacquiring all PLATEAU GML files once...", flush=True)
                gml_files, recovery_download_issues = _download_remote_set(gml_files, p_cfg, progress=True)
                download_issues.extend(recovery_download_issues)
                _write_csv(
                    _city_path(city_dir, city.city_code, "plateau_download_issues.csv"),
                    download_issues,
                )
                _write_csv(
                    _city_path(city_dir, city.city_code, "plateau_files.csv"),
                    [asdict(x) for x in gml_files],
                )
                usable_gml_files = [x for x in gml_files if x.local_path]
                event["files_available_after_recovery"] = len(usable_gml_files)
                event["download_errors"] = len(recovery_download_issues)
                if recovery_download_issues or len(usable_gml_files) != len(gml_files):
                    read_failure = CityGMLReadError(
                        e.path, "recovery_download",
                        OSError("cache recovery could not reacquire the complete municipality GML set"),
                    )
                    break
                read_failure = None

        if read_failure is not None:
            status = "local_plateau_read_failed" if plateau_source == "local" else "plateau_read_failed_after_recovery"
            failed = _read_failure_summary(
                city, records, gml_files, read_failure, plateau_source,
                recovery_events, status,
            )
            json_dump(_city_path(city_dir, city.city_code, "run_summary.json"), failed)
            overall.append(failed)
            if plateau_source == "local":
                msg = (
                    f"Local PLATEAU CityGML is unreadable and local files are never deleted automatically: "
                    f"{read_failure.path} ({type(read_failure.original).__name__}: {read_failure.original})"
                )
            else:
                msg = (
                    f"PLATEAU CityGML remained unreadable after municipality-wide cache refresh: "
                    f"{read_failure.path} ({type(read_failure.original).__name__}: {read_failure.original})"
                )
            if mode == "municipality":
                raise RuntimeError(msg) from read_failure
            print(f"  ERROR: {msg}; municipality deferred", flush=True)
            continue

        print(f"  scanned buildings: {len(buildings)}", flush=True)

        result = match_city(records, buildings, cfg["matching"])
        selected = result["selected"]
        print(f"  matched buildings: {len(selected)}", flush=True)
        print(f"  building complexes: {sum(bool(x.get('matched_building_count')) for x in result['complex_rows'])}", flush=True)
        print(f"  output heritage points: {sum(p.get('geometry') is not None for p in result['point_rows'])}", flush=True)

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
            while True:
                try:
                    written = write_subset(
                        _city_path(city_dir, city.city_code, cfg["output"]["subset_gml_name"]),
                        usable_gml_files,
                        selected,
                        embed_generic=bool(cfg["output"].get("embed_generic_attributes", True)),
                        progress=True,
                    )
                    break
                except CityGMLReadError as e:
                    if plateau_source != "api" or len(recovery_events) >= max_recovery:
                        status = "local_plateau_read_failed" if plateau_source == "local" else "plateau_read_failed_after_recovery"
                        failed = _read_failure_summary(
                            city, records, gml_files, e, plateau_source,
                            recovery_events, status,
                        )
                        json_dump(_city_path(city_dir, city.city_code, "run_summary.json"), failed)
                        if mode == "municipality":
                            if plateau_source == "local":
                                raise RuntimeError(
                                    f"Local PLATEAU CityGML is unreadable during subset output; "
                                    f"local files were not modified: {e.path}"
                                ) from e
                            raise RuntimeError(
                                f"PLATEAU CityGML unreadable during subset output after cache recovery: {e.path}"
                            ) from e
                        print(f"  ERROR: subset GML read failed: {e}; municipality deferred", flush=True)
                        overall.append(failed)
                        written = None
                        break

                    event = {
                        "attempt": len(recovery_events) + 1,
                        "stage": e.stage,
                        "failed_path": e.path,
                        "error": f"{type(e.original).__name__}: {e.original}",
                        "action": "purge_municipality_cache_and_redownload_all",
                    }
                    recovery_events.append(event)
                    print(
                        f"  WARNING: unreadable PLATEAU cache during subset output: {Path(e.path).name}",
                        flush=True,
                    )
                    target = purge_city_cache(p_cfg["cache_dir"], city.city_code)
                    print(f"  cache recovery: removed complete municipality cache {target}", flush=True)
                    gml_files, recovery_download_issues = _download_remote_set(gml_files, p_cfg, progress=True)
                    download_issues.extend(recovery_download_issues)
                    usable_gml_files = [x for x in gml_files if x.local_path]
                    event["files_available_after_recovery"] = len(usable_gml_files)
                    event["download_errors"] = len(recovery_download_issues)
                    _write_csv(
                        _city_path(city_dir, city.city_code, "plateau_download_issues.csv"),
                        download_issues,
                    )
                    _write_csv(
                        _city_path(city_dir, city.city_code, "plateau_files.csv"),
                        [asdict(x) for x in gml_files],
                    )
                    if recovery_download_issues or len(usable_gml_files) != len(gml_files):
                        recovery_error = CityGMLReadError(
                            e.path, "recovery_download",
                            OSError("cache recovery could not reacquire the complete municipality GML set"),
                        )
                        failed = _read_failure_summary(
                            city, records, gml_files, recovery_error, plateau_source,
                            recovery_events, "plateau_recovery_download_failed",
                        )
                        json_dump(_city_path(city_dir, city.city_code, "run_summary.json"), failed)
                        if mode == "municipality":
                            raise RuntimeError("PLATEAU cache recovery failed during subset output") from recovery_error
                        print("  ERROR: PLATEAU cache recovery failed during subset output; municipality deferred", flush=True)
                        overall.append(failed)
                        written = None
                        break

            if written is None:
                continue

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
            "plateau_source": plateau_source,
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
            "selected_buildings_with_disaster_risk": sum(
                b.gml_id in selected and bool(b.disaster_risks) for b in buildings
            ),
            "selected_disaster_risk_records": sum(
                len(b.disaster_risks) for b in buildings if b.gml_id in selected
            ),
            "written_gml_buildings": len(written),
            "heritage_point_features": sum(p.get("geometry") is not None for p in result["point_rows"]),
            "complex_only_records": sum(r.spatial_match_status == "complex_only" for r in records),
            "shared_complex_coordinate_records": sum(r.source_location_role == "shared_complex_coordinate" for r in records),
            "unresolved_entities": len(result["unresolved_rows"]),
            "cache_recovery_count": len(recovery_events),
            "cache_recovery_events": recovery_events,
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
