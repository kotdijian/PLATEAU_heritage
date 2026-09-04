#!/usr/bin/env python3
"""Build a map-ready Tokyo museum Building and hazard GeoPackage.

The tool deliberately reuses PLATEAU_heritage for CityGML discovery, Building
footprints, and disaster-risk extraction. Museum source normalization is reused
from source/scripts/build_museum_manifest.py. This module only implements the
Museum-specific facility consolidation, conservative matching, and GPKG views.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import pandas as pd
from pyproj import Geod

# Prefer the checked-out PLATEAU_heritage code when this script is executed as
# ``python Museum/build_museum_hazard_gpkg.py``. The path is derived from this
# file and never contains a user-specific filesystem location.
SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
for import_root in (SCRIPT_DIR, REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from heritage_gml.citygml import scan_buildings
from heritage_gml.model import PlateauCity
from heritage_gml.output import buildings_df as heritage_buildings_df
from heritage_gml.plateau import local_files
from heritage_gml.util import compact_address

try:
    from heritage_gml.output import disaster_risk_rows
except ImportError:  # Report a precise requirement after parsing the CLI.
    disaster_risk_rows = None

ROOT = SCRIPT_DIR
DEFAULT_MUSEUM_DATA = ROOT / "source" / "data"
DEFAULT_PLATEAU_DIR = ROOT.parent / ".cache" / "plateau"
GEOD = Geod(ellps="GRS80")


def _load_normalize_name():
    """Load the shared Museum normalizer by its repository-relative path."""
    module_path = ROOT / "source" / "scripts" / "build_museum_manifest.py"
    if not module_path.is_file():
        raise FileNotFoundError(
            "Missing Museum source tool: "
            f"{module_path}. Install the complete Museum bundle, not only this Python file."
        )
    spec = importlib.util.spec_from_file_location("museum_source_manifest", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load Museum source tool: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.normalize_name


normalize_name = _load_normalize_name()

STRONG_DETAILED_USAGE = {"422302": "博物館", "422305": "動物園"}
USAGE_LABELS = {"422": "文教厚生施設"}
DETAILED_USAGE_LABELS = {
    "422": "文教厚生施設",
    "4223": "文教厚生施設3",
    **STRONG_DETAILED_USAGE,
}
MUSEUM_NAME_KEYWORDS = (
    "博物館", "美術館", "資料館", "記念館", "科学館", "動物園", "水族館",
    "植物園", "museum", "gallery", "aquarium", "zoo",
)
LAW_STATUS_LABELS = {
    "registered": "登録博物館",
    "designated_facility": "指定施設",
    "museum_equivalent": "博物館相当施設",
    "similar_facility": "博物館類似施設",
}
FACILITY_TYPE_LABELS = {
    "museum": "博物館",
    "science_museum": "科学系博物館",
    "zoo": "動物園",
    "aquarium": "水族館",
    "aquarium_or_living_collection": "水族館・生体展示施設",
    "museum_or_display_facility": "博物館・展示施設",
    "museum_or_cultural_facility": "博物館・文化施設",
    "museum_or_knowledge_facility": "博物館・知識施設",
}
FACILITY_TYPE_PRIORITY = {
    "zoo": 100,
    "aquarium": 100,
    "science_museum": 90,
    "aquarium_or_living_collection": 80,
    "museum_or_display_facility": 70,
    "museum_or_cultural_facility": 60,
    "museum_or_knowledge_facility": 60,
    "museum": 10,
}

LINK_FIELDS = [
    "link_id", "museum_id", "building_gml_id", "building_id", "building_role",
    "match_status", "match_methods", "exact_name", "exact_address",
    "point_in_building", "detailed_usage_match", "candidate_building_count",
    "manual_override", "review_required", "matched_at", "source_gml",
]
UNRESOLVED_FIELDS = [
    "museum_id", "museum_name", "municipality_code", "municipality_name",
    "reason", "candidate_count", "candidate_building_ids", "review_required",
]


def text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return " ".join(str(value).replace("\u3000", " ").split())


def uniq(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        value = text(value)
        if value and value not in out:
            out.append(value)
    return out


def joined(values: Iterable[Any]) -> str:
    return ";".join(uniq(values))


def first_nonempty(rows: list[dict[str, str]], field: str, *, longest: bool = False) -> str:
    values = uniq(row.get(field, "") for row in rows)
    if not values:
        return ""
    return max(values, key=len) if longest else values[0]


def display_name(value: str) -> str:
    return re.sub(r"^[◎○〇●]\s*", "", text(value)).strip()


def infer_ownership(name: str, municipality_name: str) -> tuple[str, str]:
    """Return a conservative name-based sector and its derivation method."""
    value = display_name(name)
    if value.startswith("国立") or "国立大学" in value:
        return "national", "name_rule"
    if value.startswith("東京都") or value.startswith("都立"):
        return "metropolitan", "name_rule"
    if re.search(r"(?:区立|市立|町立|村立)", value):
        return "municipal", "name_rule"
    if municipality_name and value.startswith(municipality_name):
        return "municipal", "name_rule"
    if re.search(r"(?:大学|学園|学院)", value):
        return "university", "name_rule"
    return "unknown", "unresolved"


def choose_facility_type(rows: list[dict[str, str]]) -> str:
    """Prefer a specific supplemental type over the generic core value."""
    values = uniq(row.get("facility_type", "") for row in rows)
    if not values:
        return "museum"
    return max(
        values,
        key=lambda value: (FACILITY_TYPE_PRIORITY.get(value, 50), -values.index(value)),
    )


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [{key: text(value) for key, value in row.items()} for row in csv.DictReader(handle)]


def load_museum_data(data_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    candidates_path = data_dir / "museum_candidates.csv"
    reconciliation_path = data_dir / "museum_reconciliation.csv"
    if not candidates_path.is_file() or not reconciliation_path.is_file():
        raise FileNotFoundError(
            "Museum data requires museum_candidates.csv and museum_reconciliation.csv: "
            f"{data_dir}"
        )

    candidates = {row["record_id"]: row for row in read_csv_rows(candidates_path)}
    reconciliation = read_csv_rows(reconciliation_path)
    source_records: list[dict[str, str]] = []
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)

    for result in reconciliation:
        record_id = result.get("record_id", "")
        if record_id not in candidates:
            raise ValueError(f"Reconciliation record not found in candidates: {record_id}")
        row = {**candidates[record_id], **result}
        source_records.append(row)
        facility_id = result.get("canonical_facility_id", "")
        # Keep unresolved source records for provenance without promoting them
        # into canonical facilities. This preserves the accepted 245-facility
        # manifest instead of silently counting four review-only records.
        if facility_id and result.get("match_status") != "needs_review":
            groups[facility_id].append(row)

    facilities: list[dict[str, Any]] = []
    for facility_id, rows in sorted(groups.items()):
        rows.sort(
            key=lambda row: (
                0 if row.get("source_role") == "core" else 1,
                0 if row.get("match_status") in {"core_unique", "supplement_unique"} else 1,
                row.get("source_id", ""),
            )
        )
        name = display_name(first_nonempty(rows, "facility_name_raw"))
        municipality_name = first_nonempty(rows, "municipality_name")
        ownership_type, ownership_method = infer_ownership(name, municipality_name)
        facility_type = choose_facility_type(rows)
        law_status = first_nonempty(rows, "museum_law_status")
        retrieved = sorted(uniq(row.get("retrieved_at", "") for row in rows))
        group_statuses = set(row.get("match_status", "") for row in rows)
        scope_status = (
            "candidate" if group_statuses & {"core_unique", "supplement_unique"} else "needs_review"
        )
        facilities.append({
            "museum_id": facility_id,
            "canonical_name": name,
            "normalized_name": first_nonempty(rows, "facility_name_normalized"),
            "municipality_code": first_nonempty(rows, "municipality_code"),
            "municipality_name": municipality_name,
            "postal_code": first_nonempty(rows, "postal_code"),
            "address": first_nonempty(rows, "address", longest=True),
            "phone": first_nonempty(rows, "phone"),
            "official_url": first_nonempty(rows, "official_url"),
            "facility_type_code": facility_type,
            "facility_type_label": FACILITY_TYPE_LABELS.get(facility_type, facility_type),
            "museum_law_status_code": law_status or "unknown",
            "museum_law_status_label": LAW_STATUS_LABELS.get(law_status, "未判定"),
            "operator_name": "",
            "ownership_type": ownership_type,
            "ownership_method": ownership_method,
            "scope_status": scope_status,
            "source_record_count": len(rows),
            "source_ids": joined(row.get("source_id", "") for row in rows),
            "source_roles": joined(row.get("source_role", "") for row in rows),
            "source_tiers": joined(row.get("source_tier", "") for row in rows),
            "first_retrieved_at": retrieved[0] if retrieved else "",
            "last_retrieved_at": retrieved[-1] if retrieved else "",
        })

    source_records.sort(key=lambda row: (row.get("canonical_facility_id", ""), row["record_id"]))
    return facilities, source_records


def city_names_from_facilities(facilities: list[dict[str, Any]]) -> dict[str, str]:
    return {
        text(row["municipality_code"]): text(row["municipality_name"])
        for row in facilities if text(row["municipality_code"])
    }


def discover_plateau_files(plateau_dir: Path, facilities: list[dict[str, Any]]):
    files_by_path = {}
    for city_code, city_name in sorted(city_names_from_facilities(facilities).items()):
        city = PlateauCity(
            pref_code=city_code[:2], pref="東京都", city_code=city_code,
            city=city_name, year="local", feature_types=["bldg"], url="",
        )
        for plateau_file in local_files(plateau_dir, city):
            if plateau_file.local_path:
                files_by_path[plateau_file.local_path] = plateau_file
    return [files_by_path[path] for path in sorted(files_by_path)]


def building_usage(building) -> tuple[str, str, str, str, str, str]:
    usage_code = text(getattr(building, "usage", ""))
    usage_label = text(getattr(building, "usage_label", "")) or USAGE_LABELS.get(usage_code, "")
    usage_codespace = text(getattr(building, "usage_codespace", ""))
    detail_code = text(getattr(building, "detailed_usage", ""))
    detail_label = text(getattr(building, "detailed_usage_label", "")) or DETAILED_USAGE_LABELS.get(detail_code, "")
    detail_codespace = text(getattr(building, "detailed_usage_codespace", ""))
    return usage_code, usage_label, usage_codespace, detail_code, detail_label, detail_codespace


def has_museum_keyword(value: str) -> bool:
    folded = text(value).casefold()
    return any(keyword.casefold() in folded for keyword in MUSEUM_NAME_KEYWORDS)


def index_facilities(facilities: list[dict[str, Any]]):
    by_city_name: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_city_address: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for facility in facilities:
        city = text(facility["municipality_code"])
        name_key = normalize_name(text(facility["canonical_name"]))
        address_key = compact_address(facility["address"])
        if city and name_key:
            by_city_name[(city, name_key)].append(facility)
        if city and address_key:
            by_city_address[(city, address_key)].append(facility)
    return by_city_name, by_city_address


def match_buildings(buildings, facilities: list[dict[str, Any]]):
    by_city_name, by_city_address = index_facilities(facilities)
    links: list[dict[str, Any]] = []
    building_status: dict[str, dict[str, Any]] = {}

    for building in buildings:
        city = text(building.city_code)
        name_key = normalize_name(text(building.name))
        address_key = compact_address(building.address)
        usage_code, usage_label, usage_codespace, detail_code, detail_label, detail_codespace = building_usage(building)
        strong_usage = detail_code in STRONG_DETAILED_USAGE
        keyword_candidate = has_museum_keyword(building.name)
        name_hits = by_city_name.get((city, name_key), []) if name_key else []
        address_hits = by_city_address.get((city, address_key), []) if address_key else []
        facilities_by_id = {
            row["museum_id"]: row for row in [*name_hits, *address_hits]
        }

        confirmed_ids: list[str] = []
        review_ids: list[str] = []
        for facility_id, facility in sorted(facilities_by_id.items()):
            exact_name = facility in name_hits
            exact_address = facility in address_hits
            # Address plus an exact museum/zoo detailed-use code is accepted only
            # when the source address identifies one facility. Shared addresses
            # remain reviewable because campuses and complexes can contain several.
            confirmed = exact_name or (exact_address and strong_usage and len(address_hits) == 1)
            match_status = "confirmed" if confirmed else "needs_review"
            methods = []
            if exact_name:
                methods.append("exact_name")
            if exact_address:
                methods.append("exact_address")
            if strong_usage:
                methods.append("detailed_usage")
            links.append({
                "link_id": f"{facility_id}|{building.gml_id}",
                "museum_id": facility_id,
                "building_gml_id": building.gml_id,
                "building_id": text(building.building_id),
                "building_role": "unknown",
                "match_status": match_status,
                "match_methods": ";".join(methods),
                "exact_name": int(exact_name),
                "exact_address": int(exact_address),
                "point_in_building": 0,
                "detailed_usage_match": int(strong_usage),
                "candidate_building_count": 0,
                "manual_override": 0,
                "review_required": int(not confirmed),
                "matched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "source_gml": text(building.source_file),
            })
            (confirmed_ids if confirmed else review_ids).append(facility_id)

        plateau_candidate = strong_usage or keyword_candidate
        if confirmed_ids:
            status = "confirmed"
        elif review_ids:
            status = "needs_review"
        elif plateau_candidate:
            status = "plateau_only_candidate"
        else:
            continue
        building_status[building.gml_id] = {
            "status": status,
            "confirmed_ids": confirmed_ids,
            "review_ids": review_ids,
            "candidate_methods": joined([
                "detailed_usage" if strong_usage else "",
                "name_keyword" if keyword_candidate else "",
            ]),
            "usage_code": usage_code,
            "usage_label": usage_label,
            "usage_codespace": usage_codespace,
            "detailed_usage_code": detail_code,
            "detailed_usage_label": detail_label,
            "detailed_usage_codespace": detail_codespace,
        }

    counts = defaultdict(int)
    for link in links:
        counts[link["museum_id"]] += 1
    for link in links:
        link["candidate_building_count"] = counts[link["museum_id"]]
    return links, building_status


def footprint_area_m2(geometry) -> float | None:
    if geometry is None or geometry.is_empty:
        return None
    try:
        area, _ = GEOD.geometry_area_perimeter(geometry)
        return abs(float(area))
    except (TypeError, ValueError):
        return None


def risk_records(building, risk_type: str):
    return [
        risk for risk in (getattr(building, "disaster_risks", None) or [])
        if text(getattr(risk, "risk_type", "")) == risk_type
    ]


def worst_rank(building, risk_type: str) -> str:
    ranked = []
    for risk in risk_records(building, risk_type):
        label = text(getattr(risk, "rank_label", "")) or text(getattr(risk, "rank_code", ""))
        depth = getattr(risk, "depth_m", None)
        numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", label)]
        severity = float(depth) if depth is not None else (max(numbers) if numbers else -1.0)
        ranked.append((severity, label))
    return max(ranked, default=(-1.0, ""))[1]


def landslide_worst(building) -> str:
    values = uniq(
        text(getattr(risk, "area_type_label", "")) or text(getattr(risk, "area_type_code", ""))
        for risk in risk_records(building, "landslide")
    )
    if not values:
        return ""
    return next((value for value in values if "特別" in value), values[0])


def empty_selected_meta(methods: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "complex_ids": [], "complex_names": [], "record_ids": [],
        "record_names": [], "record_types": [], "entity_classes": [],
        "designation_level_codes": [], "designation_status_codes": [],
        "heritage_type_major_codes": [], "heritage_type_details": [],
        "methods": list(methods),
    }


def building_frames(buildings, facilities: list[dict[str, Any]], links, building_status):
    facility_by_id = {row["museum_id"]: row for row in facilities}
    building_by_id = {building.gml_id: building for building in buildings}
    link_by_building: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        link_by_building[link["building_gml_id"]].append(link)

    selected_meta = {
        gml_id: empty_selected_meta(
            method for link in link_by_building.get(gml_id, [])
            if link["match_status"] == "confirmed"
            for method in link["match_methods"].split(";") if method
        )
        for gml_id in building_status
    }
    base = heritage_buildings_df(buildings, selected_meta)
    if base.empty:
        base = gpd.GeoDataFrame(columns=["gml_id", "geometry"], geometry="geometry", crs="EPSG:4326")
    base = base.rename(columns={
        "name": "plateau_name", "address": "plateau_address",
        "usage": "usage_code", "detailed_usage": "detailed_usage_code",
        "disaster_risk_count": "hazard_count",
        "disaster_risk_types": "hazard_types",
    })

    extra_rows = []
    for gml_id in base.get("gml_id", pd.Series(dtype=str)):
        building = building_by_id[gml_id]
        state = building_status[gml_id]
        confirmed_links = [
            link for link in link_by_building.get(gml_id, [])
            if link["match_status"] == "confirmed"
        ]
        confirmed_facilities = [facility_by_id[link["museum_id"]] for link in confirmed_links]
        extra_rows.append({
            "gml_id": gml_id,
            "display_name": joined(row["canonical_name"] for row in confirmed_facilities) or text(building.name),
            "museum_ids": joined(row["museum_id"] for row in confirmed_facilities),
            "museum_names": joined(row["canonical_name"] for row in confirmed_facilities),
            "museum_count": len(confirmed_facilities),
            "facility_types": joined(row["facility_type_label"] for row in confirmed_facilities),
            "law_statuses": joined(row["museum_law_status_label"] for row in confirmed_facilities),
            "ownership_types": joined(row["ownership_type"] for row in confirmed_facilities),
            "operator_names": joined(row["operator_name"] for row in confirmed_facilities),
            "facility_address": first_nonempty(confirmed_facilities, "address", longest=True) if confirmed_facilities else "",
            "phone": joined(row["phone"] for row in confirmed_facilities),
            "official_url": joined(row["official_url"] for row in confirmed_facilities),
            "match_status": state["status"],
            "match_methods": joined(
                method
                for link in confirmed_links
                for method in link["match_methods"].split(";")
                if method
            ) or state["candidate_methods"],
            "source_count": sum(int(row["source_record_count"]) for row in confirmed_facilities),
            "review_required": int(state["status"] != "confirmed"),
            "usage_code": state["usage_code"],
            "usage_label": state["usage_label"],
            "usage_codespace": state["usage_codespace"],
            "detailed_usage_code": state["detailed_usage_code"],
            "detailed_usage_label": state["detailed_usage_label"],
            "detailed_usage_codespace": state["detailed_usage_codespace"],
            "measured_height_m": getattr(building, "measured_height_m", None),
            "storeys_above": getattr(building, "storeys_above", None),
            "storeys_below": getattr(building, "storeys_below", None),
            "year_of_construction": getattr(building, "year_of_construction", None),
            "structure_type_code": text(getattr(building, "structure_type", "")),
            "structure_type_label": text(getattr(building, "structure_type_label", "")),
            "fireproof_type_code": text(getattr(building, "fireproof_type", "")),
            "fireproof_type_label": text(getattr(building, "fireproof_type_label", "")),
            "footprint_area_m2": footprint_area_m2(building.geometry),
            "has_any_hazard": int(bool(getattr(building, "disaster_risks", None))),
            "has_river_flood": int(bool(risk_records(building, "river_flooding"))),
            "river_flood_worst_rank": worst_rank(building, "river_flooding"),
            "river_flood_water_systems": joined(
                text(getattr(risk, "description_label", "")) or text(getattr(risk, "description_code", ""))
                for risk in risk_records(building, "river_flooding")
            ),
            "has_inland_flood": int(bool(risk_records(building, "inland_flooding"))),
            "inland_flood_worst_rank": worst_rank(building, "inland_flooding"),
            "has_high_tide": int(bool(risk_records(building, "high_tide"))),
            "high_tide_worst_rank": worst_rank(building, "high_tide"),
            "has_tsunami": int(bool(risk_records(building, "tsunami"))),
            "tsunami_worst_rank": worst_rank(building, "tsunami"),
            "has_reservoir_flood": int(bool(risk_records(building, "reservoir_flooding"))),
            "reservoir_flood_worst_rank": worst_rank(building, "reservoir_flooding"),
            "has_landslide": int(bool(risk_records(building, "landslide"))),
            "landslide_worst_class": landslide_worst(building),
        })

    extras = pd.DataFrame(extra_rows)
    if not extras.empty:
        # Museum/physical fields replace same-named compatibility columns from
        # heritage_buildings_df; risk-summary fields remain from the shared tool.
        duplicate = [column for column in extras.columns if column != "gml_id" and column in base.columns]
        base = base.drop(columns=duplicate)
        base = base.merge(extras, on="gml_id", how="left")
        base = gpd.GeoDataFrame(base, geometry="geometry", crs="EPSG:4326")

    confirmed_ids = {
        gml_id for gml_id, state in building_status.items() if state["status"] == "confirmed"
    }
    confirmed = base[base["gml_id"].isin(confirmed_ids)].copy()
    candidates = base[~base["gml_id"].isin(confirmed_ids)].copy()
    return confirmed, candidates


def facility_status_rows(facilities, links, plateau_cities_with_files: set[str]):
    links_by_facility: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        links_by_facility[link["museum_id"]].append(link)
    output, unresolved = [], []
    for facility in facilities:
        ff = links_by_facility.get(facility["museum_id"], [])
        confirmed = [link for link in ff if link["match_status"] == "confirmed"]
        review = [link for link in ff if link["match_status"] == "needs_review"]
        if confirmed:
            match_status = "confirmed"
            reason = ""
        elif review:
            match_status = "needs_review"
            reason = "candidate_buildings_require_review"
        elif facility["municipality_code"] not in plateau_cities_with_files:
            match_status = "unresolved"
            reason = "no_plateau_gml_for_municipality"
        elif not facility["address"]:
            match_status = "unresolved"
            reason = "no_exact_name_and_missing_address"
        else:
            match_status = "unresolved"
            reason = "no_exact_name_or_supported_address_match"
        row = {
            **facility,
            "match_status": match_status,
            "matched_building_count": len(confirmed),
            "matched_building_ids": joined(link["building_gml_id"] for link in confirmed),
            "candidate_building_count": len(review),
            "candidate_building_ids": joined(link["building_gml_id"] for link in review),
        }
        output.append(row)
        if match_status != "confirmed":
            unresolved.append({
                "museum_id": facility["museum_id"],
                "museum_name": facility["canonical_name"],
                "municipality_code": facility["municipality_code"],
                "municipality_name": facility["municipality_name"],
                "reason": reason,
                "candidate_count": len(review),
                "candidate_building_ids": joined(link["building_gml_id"] for link in review),
                "review_required": 1,
            })
    return output, unresolved


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)
    ).fetchone() is not None


def register_attribute_table(connection: sqlite3.Connection, name: str, row_count: int) -> None:
    if not table_exists(connection, "gpkg_contents"):
        return
    connection.execute("DELETE FROM gpkg_contents WHERE table_name=?", (name,))
    connection.execute(
        """
        INSERT INTO gpkg_contents
        (table_name, data_type, identifier, description, last_change,
         min_x, min_y, max_x, max_y, srs_id)
        VALUES (?, 'attributes', ?, ?, ?, NULL, NULL, NULL, NULL, NULL)
        """,
        (
            name, name, f"Museum attribute table ({row_count} rows)",
            dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        ),
    )


def write_attribute_table(
    connection: sqlite3.Connection, name: str, rows: list[dict[str, Any]],
    columns: list[str] | None = None,
) -> None:
    frame = pd.DataFrame(rows, columns=columns)
    frame.to_sql(name, connection, if_exists="replace", index=False)
    register_attribute_table(connection, name, len(frame))


def append_risk_rows(connection: sqlite3.Connection, buildings, included_ids: set[str]) -> int:
    if disaster_risk_rows is None:
        return 0
    selected = {gml_id: {} for gml_id in included_ids}
    new_rows = disaster_risk_rows(buildings, selected)
    if not new_rows:
        return 0
    new_frame = pd.DataFrame(new_rows)
    if table_exists(connection, "plateau_disaster_risk"):
        old_frame = pd.read_sql_query("SELECT * FROM plateau_disaster_risk", connection)
        all_columns = list(dict.fromkeys([*old_frame.columns, *new_frame.columns]))
        merged = pd.concat(
            [old_frame.reindex(columns=all_columns), new_frame.reindex(columns=all_columns)],
            ignore_index=True,
        )
    else:
        merged = new_frame
    dedup_columns = [
        column for column in (
            "building_gml_id", "risk_index", "risk_type", "description_code",
            "rank_code", "scale_code", "area_type_code",
        ) if column in merged.columns
    ]
    if dedup_columns:
        merged = merged.drop_duplicates(subset=dedup_columns, keep="last")
    merged.to_sql("plateau_disaster_risk", connection, if_exists="replace", index=False)
    register_attribute_table(connection, "plateau_disaster_risk", len(merged))
    return len(new_frame)


def write_output(
    source_gpkg: Path, output_gpkg: Path, confirmed: gpd.GeoDataFrame,
    candidates: gpd.GeoDataFrame, facilities, source_records, links, unresolved,
    buildings, overwrite: bool,
):
    if source_gpkg.resolve() == output_gpkg.resolve():
        raise ValueError("Output must differ from the source hazard GeoPackage")
    if output_gpkg.exists():
        if not overwrite:
            raise FileExistsError(f"Output exists; use --overwrite: {output_gpkg}")
        output_gpkg.unlink()
    output_gpkg.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_gpkg, output_gpkg)

    if not confirmed.empty:
        confirmed.to_file(
            output_gpkg, layer="museum_buildings_footprint", driver="GPKG",
            engine="pyogrio", mode="a",
        )
    if not candidates.empty:
        candidates.to_file(
            output_gpkg, layer="museum_building_candidates", driver="GPKG",
            engine="pyogrio", mode="a",
        )

    with sqlite3.connect(output_gpkg) as connection:
        write_attribute_table(connection, "museum_facilities", facilities)
        write_attribute_table(connection, "museum_source_records", source_records)
        write_attribute_table(connection, "museum_building_links", links, LINK_FIELDS)
        write_attribute_table(connection, "museum_unresolved", unresolved, UNRESOLVED_FIELDS)
        risk_count = append_risk_rows(
            connection, buildings, set(confirmed.get("gml_id", [])) | set(candidates.get("gml_id", []))
        )
        connection.commit()
    return risk_count


def default_output_path(source_gpkg: Path) -> Path:
    stem = source_gpkg.stem
    if stem.endswith("_heritage_hazards"):
        stem = stem[: -len("_heritage_hazards")] + "_museum_hazards"
    else:
        stem += "_museum_hazards"
    return source_gpkg.with_name(stem + ".gpkg")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Add map-ready Museum Building layers and normalized Museum tables "
            "to a copy of an existing PLATEAU Heritage hazard GeoPackage."
        )
    )
    parser.add_argument("source_gpkg", type=Path, help="Existing hazard GeoPackage; never modified")
    parser.add_argument("--plateau-local-dir", type=Path, default=DEFAULT_PLATEAU_DIR,
                        help="Directory containing PLATEAU bldg CityGML files")
    parser.add_argument("--museum-data-dir", type=Path, default=DEFAULT_MUSEUM_DATA,
                        help="Directory containing Museum manifest CSV outputs")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output GPKG; default is <source> with _museum_hazards")
    parser.add_argument("--overwrite", action="store_true", help="Replace output GPKG if it exists")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report without writing GPKG")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    source_gpkg = args.source_gpkg.expanduser().resolve()
    plateau_dir = args.plateau_local_dir.expanduser().resolve()
    museum_data_dir = args.museum_data_dir.expanduser().resolve()
    output_gpkg = (
        args.output.expanduser().resolve() if args.output else default_output_path(source_gpkg)
    )
    if not source_gpkg.is_file():
        raise FileNotFoundError(f"Source GeoPackage not found: {source_gpkg}")
    if not plateau_dir.is_dir():
        raise FileNotFoundError(f"PLATEAU local directory not found: {plateau_dir}")

    facilities, source_records = load_museum_data(museum_data_dir)
    plateau_files = discover_plateau_files(plateau_dir, facilities)
    if not plateau_files:
        raise RuntimeError(f"No PLATEAU bldg GML files found under: {plateau_dir}")
    print(f"Museum facilities: {len(facilities)}", flush=True)
    print(f"PLATEAU bldg files: {len(plateau_files)}", flush=True)
    if disaster_risk_rows is None:
        raise RuntimeError(
            "This tool requires the Extractor v0.5.5 disaster-risk output API. "
            "Align the repository and environment to v0.5.5 before running it."
        )
    buildings = scan_buildings(plateau_files, progress=True)
    print(f"PLATEAU Buildings scanned: {len(buildings)}", flush=True)

    links, building_status = match_buildings(buildings, facilities)
    confirmed, candidates = building_frames(buildings, facilities, links, building_status)
    cities_with_files = {plateau_file.city_code for plateau_file in plateau_files}
    facilities_out, unresolved = facility_status_rows(facilities, links, cities_with_files)
    summary = {
        "source_gpkg": str(source_gpkg),
        "output_gpkg": str(output_gpkg),
        "plateau_local_dir": str(plateau_dir),
        "museum_facilities": len(facilities_out),
        "museum_source_records": len(source_records),
        "plateau_files": len(plateau_files),
        "plateau_buildings_scanned": len(buildings),
        "confirmed_museum_buildings": len(confirmed),
        "candidate_museum_buildings": len(candidates),
        "building_links": len(links),
        "confirmed_facilities": sum(row["match_status"] == "confirmed" for row in facilities_out),
        "unresolved_facilities": len(unresolved),
        "dry_run": bool(args.dry_run),
    }

    if not args.dry_run:
        summary["new_disaster_risk_rows"] = write_output(
            source_gpkg, output_gpkg, confirmed, candidates, facilities_out,
            source_records, links, unresolved, buildings, args.overwrite,
        )
        summary_path = output_gpkg.with_suffix(".summary.json")
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
