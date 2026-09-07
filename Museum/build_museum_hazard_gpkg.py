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
import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import pandas as pd
from lxml import etree
from pyproj import Geod
from shapely.geometry import Point

# Prefer the checked-out PLATEAU_heritage code when this script is executed as
# ``python Museum/build_museum_hazard_gpkg.py``. The path is derived from this
# file and never contains a user-specific filesystem location.
SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
for import_root in (SCRIPT_DIR, REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from heritage_gml.catalog import fetch_citygml_files_for_condition
from heritage_gml.citygml import scan_buildings
from heritage_gml.model import CulturalRecord, PlateauCity, PlateauFile
from heritage_gml.output import buildings_df as heritage_buildings_df
from heritage_gml.plateau import download_files, local_files, resolve_remote_files
from heritage_gml.util import compact_address

try:
    from heritage_gml.output import disaster_risk_rows
except ImportError:  # Report a precise requirement after parsing the CLI.
    disaster_risk_rows = None

ROOT = SCRIPT_DIR
DEFAULT_MUSEUM_DATA = ROOT / "source" / "data"
DEFAULT_PLATEAU_DIR = ROOT.parent / ".cache" / "plateau"
GEOD = Geod(ellps="GRS80")
TOOL_VERSION = "0.3.1"

SPACE_FIELDS = [
    "space_id", "museum_id", "space_type", "space_name", "presence_status",
    "floor_label", "floor_min", "floor_max", "is_basement",
    "floor_elevation_min_m", "floor_elevation_max_m", "collections_present",
    "source_url", "source_authority", "source_date", "retrieved_at",
    "review_status", "notes",
]
SPACE_ASSESSMENT_FIELDS = [
    "assessment_id", "space_id", "museum_id", "building_gml_id",
    "risk_index", "risk_type", "description_code", "description_label",
    "rank_code", "rank_label", "depth_m", "space_type", "space_name",
    "floor_label", "floor_min", "floor_max", "is_basement",
    "floor_elevation_min_m", "floor_elevation_max_m", "collections_present",
    "exposure_status", "assessment_basis", "model_version",
]
INUNDATION_RISK_TYPES = {
    "river_flooding", "inland_flooding", "high_tide", "tsunami",
    "reservoir_flooding",
}


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
TOKYO_CULTURE_DETAILED_USAGE = {"1122": "文化施設"}
USAGE_LABELS = {"422": "文教厚生施設"}
DETAILED_USAGE_LABELS = {
    "422": "文教厚生施設",
    "4223": "文教厚生施設3",
    **STRONG_DETAILED_USAGE,
    **TOKYO_CULTURE_DETAILED_USAGE,
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
    "site_address_match", "point_in_building", "unique_precise_point_match",
    "detailed_usage_match", "candidate_building_count",
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


def xml_localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_xml_building(member):
    for element in member.iter():
        if xml_localname(element.tag) == "Building":
            return element
    return None


def _normalized_xml_value(value: str | None) -> str:
    """Ignore XML formatting whitespace while preserving semantic token order."""
    return " ".join((value or "").split())


_ABSENT_HIGHER_LOD_SOURCE_METADATA = {
    "geometrySrcDescLod3": ("999", "DataQualityAttribute_geometrySrcDesc.xml"),
    "geometrySrcDescLod4": ("999", "DataQualityAttribute_geometrySrcDesc.xml"),
    "appearanceSrcDescLod3": ("99", "DataQualityAttribute_appearanceSrcDesc.xml"),
    "appearanceSrcDescLod4": ("99", "DataQualityAttribute_appearanceSrcDesc.xml"),
}


def _is_absent_higher_lod_source_metadata(element) -> bool:
    """Identify explicit no-data markers that do not change Building content.

    Tokyo cross-boundary mesh copies can differ only because one municipal
    package explicitly records unavailable LOD3/LOD4 geometry/appearance source
    metadata while the other omits those leaves. The accepted values below are
    deliberately exact; real geometry, attributes and disaster risks remain in
    the duplicate comparison.
    """
    expected = _ABSENT_HIGHER_LOD_SOURCE_METADATA.get(xml_localname(element.tag))
    if expected is None or len(element):
        return False
    expected_value, expected_codespace = expected
    if _normalized_xml_value(element.text) != expected_value:
        return False
    if Path(_normalized_xml_value(element.get("codeSpace"))).name != expected_codespace:
        return False
    parent = element.getparent()
    return parent is not None and xml_localname(parent.tag) == "DataQualityAttribute"


def _semantic_building_digest(
    building, *, ignore_absent_higher_lod_source_metadata: bool = False,
    local_names_only: bool = False,
) -> str:
    """Hash expanded names, attributes, text and hierarchy, not XML formatting.

    Canonical XML still preserves whitespace-only text nodes and namespace
    prefixes. API responses can therefore serialize the same Building
    differently. Clark-notation names identify namespace URIs independently of
    prefixes, normalized text ignores indentation, and end tokens retain the
    element hierarchy.
    """
    digest = hashlib.sha256()
    for event, element in etree.iterwalk(building, events=("start", "end")):
        if (
            ignore_absent_higher_lod_source_metadata
            and _is_absent_higher_lod_source_metadata(element)
        ):
            continue
        if event == "start":
            digest.update(b"S\0")
            tag_name = xml_localname(element.tag) if local_names_only else str(element.tag)
            digest.update(tag_name.encode("utf-8"))
            digest.update(b"\0")
            attributes = [
                (
                    xml_localname(key) if local_names_only else str(key),
                    _normalized_xml_value(value),
                )
                for key, value in element.attrib.items()
            ]
            for key, value in sorted(attributes):
                digest.update(key.encode("utf-8"))
                digest.update(b"=")
                digest.update(value.encode("utf-8"))
                digest.update(b"\0")
            digest.update(b"T\0")
            digest.update(_normalized_xml_value(element.text).encode("utf-8"))
            digest.update(b"\0")
        else:
            digest.update(b"E\0")
            tag_name = xml_localname(element.tag) if local_names_only else str(element.tag)
            digest.update(tag_name.encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


def _semantic_building_multiset_digest(building) -> str:
    """Hash semantic XML facts without depending on sibling element order.

    PLATEAU municipal packages can serialize the same cross-boundary Building
    with extension siblings in a different order. The extractor does not use
    that order. Each fact still retains its complete hierarchy, local tag and
    attribute names, normalized text, and attribute values. Text token order in
    coordinate lists and every hazard value therefore remain significant.
    """
    facts: list[bytes] = []

    def visit(element, path: list[str]) -> None:
        if _is_absent_higher_lod_source_metadata(element):
            return
        current_path = [*path, xml_localname(element.tag)]
        attributes = sorted(
            (xml_localname(key), _normalized_xml_value(value))
            for key, value in element.attrib.items()
        )
        facts.append(json.dumps(
            {
                "path": current_path,
                "attributes": attributes,
                "text": _normalized_xml_value(element.text),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"))
        for child in element:
            visit(child, current_path)

    visit(building, [])
    digest = hashlib.sha256()
    for fact in sorted(facts):
        digest.update(len(fact).to_bytes(8, "big"))
        digest.update(fact)
    return digest.hexdigest()


def _absent_higher_lod_source_metadata_count(building) -> int:
    return sum(
        1 for element in building.iter()
        if _is_absent_higher_lod_source_metadata(element)
    )


def _embedded_municipality_code(building) -> str:
    """Read the Tokyo source municipality from generic survey attributes."""
    for element in building.iter():
        if xml_localname(element.tag) != "stringAttribute":
            continue
        attribute_name = _normalized_xml_value(element.get("name"))
        if "区市町村コード" not in attribute_name:
            continue
        for child in element.iter():
            if xml_localname(child.tag) != "value":
                continue
            match = re.match(r"^(13\d{3})", _normalized_xml_value(child.text))
            if match:
                return match.group(1)
    return ""


def audit_gml_id_duplicates(
    plateau_files: list[PlateauFile], *, progress: bool = False
) -> dict[str, Any]:
    """Count duplicate Building IDs and reject conflicting duplicate payloads.

    PLATEAU API queries can save the same mesh under more than one municipality
    cache directory. Identical copies are safe and are deduplicated later by the
    shared scanner. A reused gml:id with different XML is not safe: the shared
    scanner would otherwise select one record according to input order.
    """
    first_seen: dict[str, dict[str, str]] = {}
    duplicate_ids: set[str] = set()
    duplicate_occurrences = 0
    resolved_conflicts: list[dict[str, str]] = []
    unresolved_conflicts: list[dict[str, str]] = []
    preferred_sources: dict[str, str] = {}
    building_elements = 0
    gml_id_attr = "{http://www.opengis.net/gml}id"

    files = sorted(
        (plateau_file for plateau_file in plateau_files if plateau_file.local_path),
        key=lambda plateau_file: str(plateau_file.local_path),
    )
    for index, plateau_file in enumerate(files, start=1):
        path = str(plateau_file.local_path)
        context = etree.iterparse(
            path,
            events=("end",),
            huge_tree=True,
            recover=True,
        )
        for _, member in context:
            if xml_localname(member.tag) != "cityObjectMember":
                continue
            building = _find_xml_building(member)
            if building is not None:
                gml_id = building.get(gml_id_attr) or building.get("id") or ""
                if gml_id:
                    building_elements += 1
                    digest = _semantic_building_digest(building)
                    metadata_digest = _semantic_building_digest(
                        building,
                        ignore_absent_higher_lod_source_metadata=True,
                    )
                    comparison_digest = _semantic_building_digest(
                        building,
                        ignore_absent_higher_lod_source_metadata=True,
                        local_names_only=True,
                    )
                    multiset_digest = _semantic_building_multiset_digest(building)
                    current = {
                        "digest": digest,
                        "metadata_digest": metadata_digest,
                        "comparison_digest": comparison_digest,
                        "multiset_digest": multiset_digest,
                        "source_gml": path,
                        "source_city_code": text(getattr(plateau_file, "city_code", "")),
                        "embedded_city_code": _embedded_municipality_code(building),
                        "absent_higher_lod_metadata_count": str(
                            _absent_higher_lod_source_metadata_count(building)
                        ),
                    }
                    previous = first_seen.get(gml_id)
                    if previous is None:
                        first_seen[gml_id] = current
                    else:
                        duplicate_ids.add(gml_id)
                        duplicate_occurrences += 1
                        if digest != previous["digest"]:
                            conflict = {
                                "gml_id": gml_id,
                                "first_source_gml": previous["source_gml"],
                                "duplicate_source_gml": path,
                                "first_source_city_code": previous["source_city_code"],
                                "duplicate_source_city_code": current["source_city_code"],
                                "first_embedded_city_code": previous["embedded_city_code"],
                                "duplicate_embedded_city_code": current["embedded_city_code"],
                            }
                            if metadata_digest == previous["metadata_digest"]:
                                conflict["resolution"] = (
                                    "absent_higher_lod_source_metadata_only"
                                )
                                candidates = [previous, current]
                                metadata_counts = [
                                    int(row["absent_higher_lod_metadata_count"])
                                    for row in candidates
                                ]
                                if metadata_counts[0] != metadata_counts[1]:
                                    authoritative = candidates[
                                        metadata_counts.index(max(metadata_counts))
                                    ]
                                    conflict["preferred_source_gml"] = (
                                        authoritative["source_gml"]
                                    )
                                    preferred_sources[gml_id] = authoritative["source_gml"]
                                resolved_conflicts.append(conflict)
                            elif comparison_digest == previous["comparison_digest"]:
                                conflict["resolution"] = (
                                    "namespace_uri_and_optional_absent_higher_lod_metadata_only"
                                )
                                candidates = [previous, current]
                                metadata_counts = [
                                    int(row["absent_higher_lod_metadata_count"])
                                    for row in candidates
                                ]
                                if metadata_counts[0] != metadata_counts[1]:
                                    authoritative = candidates[
                                        metadata_counts.index(max(metadata_counts))
                                    ]
                                    conflict["preferred_source_gml"] = (
                                        authoritative["source_gml"]
                                    )
                                    preferred_sources[gml_id] = authoritative["source_gml"]
                                resolved_conflicts.append(conflict)
                            elif multiset_digest == previous["multiset_digest"]:
                                conflict["resolution"] = (
                                    "citygml_sibling_order_and_optional_metadata_only"
                                )
                                candidates = [previous, current]
                                metadata_counts = [
                                    int(row["absent_higher_lod_metadata_count"])
                                    for row in candidates
                                ]
                                if metadata_counts[0] != metadata_counts[1]:
                                    authoritative = candidates[
                                        metadata_counts.index(max(metadata_counts))
                                    ]
                                    conflict["preferred_source_gml"] = (
                                        authoritative["source_gml"]
                                    )
                                    preferred_sources[gml_id] = authoritative["source_gml"]
                                resolved_conflicts.append(conflict)
                            else:
                                embedded_codes = {
                                    value for value in (
                                        previous["embedded_city_code"],
                                        current["embedded_city_code"],
                                    ) if value
                                }
                                candidates = [previous, current]
                                authoritative = [
                                    row for row in candidates
                                    if row["embedded_city_code"]
                                    and row["source_city_code"] == row["embedded_city_code"]
                                ]
                                if len(embedded_codes) == 1 and len(authoritative) == 1:
                                    conflict["resolution"] = "embedded_municipality_matches_source"
                                    conflict["preferred_source_gml"] = authoritative[0]["source_gml"]
                                    preferred_sources[gml_id] = authoritative[0]["source_gml"]
                                    resolved_conflicts.append(conflict)
                                else:
                                    conflict["resolution"] = "unresolved"
                                    unresolved_conflicts.append(conflict)
            member.clear()
            parent = member.getparent()
            if parent is not None:
                while member.getprevious() is not None:
                    del parent[0]
        del context
        if progress and (index == len(files) or index % 10 == 0):
            print(
                f"PLATEAU duplicate audit: {index}/{len(files)} files",
                flush=True,
            )

    return {
        "building_elements_with_gml_id": building_elements,
        "unique_gml_id_count": len(first_seen),
        "duplicate_gml_id_count": len(duplicate_ids),
        "duplicate_occurrences": duplicate_occurrences,
        "duplicate_conflict_count": len(resolved_conflicts) + len(unresolved_conflicts),
        "resolved_duplicate_conflict_count": len(resolved_conflicts),
        "unresolved_duplicate_conflict_count": len(unresolved_conflicts),
        "duplicate_comparison": "extractor_semantic_xml_v4",
        "metadata_normalized_duplicate_conflict_count": sum(
            row.get("resolution") == "absent_higher_lod_source_metadata_only"
            for row in resolved_conflicts
        ),
        "namespace_normalized_duplicate_conflict_count": sum(
            row.get("resolution")
            == "namespace_uri_and_optional_absent_higher_lod_metadata_only"
            for row in resolved_conflicts
        ),
        "order_normalized_duplicate_conflict_count": sum(
            row.get("resolution")
            == "citygml_sibling_order_and_optional_metadata_only"
            for row in resolved_conflicts
        ),
        "duplicate_gml_id_samples": sorted(duplicate_ids)[:20],
        "resolved_duplicate_conflicts": resolved_conflicts[:20],
        "duplicate_conflicts": unresolved_conflicts[:20],
        "_preferred_source_by_gml_id": preferred_sources,
        "_unresolved_gml_ids": sorted({
            row["gml_id"] for row in unresolved_conflicts
        }),
    }


def scan_buildings_with_preferences(
    plateau_files: list[PlateauFile],
    preferred_sources: dict[str, str],
    *,
    excluded_gml_ids: set[str] | None = None,
    progress: bool = False,
):
    """Scan each GML independently and select the audited authoritative copy."""
    selected = {}
    excluded_gml_ids = excluded_gml_ids or set()
    files = sorted(
        (plateau_file for plateau_file in plateau_files if plateau_file.local_path),
        key=lambda plateau_file: str(plateau_file.local_path),
    )
    for index, plateau_file in enumerate(files, start=1):
        for building in scan_buildings([plateau_file], progress=False):
            if building.gml_id in excluded_gml_ids:
                continue
            existing = selected.get(building.gml_id)
            if existing is None:
                selected[building.gml_id] = building
                continue
            preferred = preferred_sources.get(building.gml_id, "")
            if preferred and str(building.source_file) == preferred:
                selected[building.gml_id] = building
            elif preferred and str(existing.source_file) == preferred:
                continue
            # For semantically identical duplicates without an explicit
            # preference, retain the first record from the sorted file list.
        if progress and (index == len(files) or index % 10 == 0):
            print(f"PLATEAU Building scan: {index}/{len(files)} files", flush=True)
    return list(selected.values())


def first_nonempty(rows: list[dict[str, str]], field: str, *, longest: bool = False) -> str:
    values = uniq(row.get(field, "") for row in rows)
    if not values:
        return ""
    return max(values, key=len) if longest else values[0]


def display_name(value: str) -> str:
    return re.sub(r"^[◎○〇●]\s*", "", text(value)).strip()


def museum_address_key(value: str) -> str:
    """Canonical exact-address key, ignoring only redundant country/prefecture text."""
    key = compact_address(value)
    key = re.sub(r"^(?:〒)?\d{3}-?\d{4}", "", key)
    key = re.sub(r"^(?:日本)?東京都", "", key)
    return key if re.search(r"\d", key) else ""


def museum_site_address_key(value: str) -> str:
    """Base street/parcel key; building name and floor remain review-only differences."""
    key = museum_address_key(value)
    if not re.search(r"\d", key):
        return ""
    match = re.match(r"^(.+?\d+(?:-\d+){1,3})(?=[^\d-]|$)", key)
    return match.group(1) if match else key


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


def optional_float(value: Any) -> float | None:
    try:
        return float(value) if text(value) else None
    except (TypeError, ValueError):
        return None


def optional_int(value: Any) -> int | None:
    try:
        return int(text(value)) if text(value) else None
    except (TypeError, ValueError):
        return None


def _space_source_path(data_dir: Path) -> Path | None:
    candidates = [
        data_dir / "museum_facility_spaces.csv",
        data_dir.parent / "config" / "facility_spaces.csv",
    ]
    return next((path for path in candidates if path.is_file()), None)


def load_facility_spaces(
    data_dir: Path, facilities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Load multi-floor facility/collection-storage observations.

    One facility can have any number of rows. Signed floors use negative values
    for basements; floor elevations are metres relative to the Building ground
    level and remain optional rather than being inferred from storey count.
    """
    source_path = _space_source_path(data_dir)
    raw_rows = read_csv_rows(source_path) if source_path else []
    valid_museum_ids = {row["museum_id"] for row in facilities}
    spaces: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rows, start=1):
        museum_id = text(raw.get("museum_id"))
        if not museum_id:
            continue
        if museum_id not in valid_museum_ids:
            raise ValueError(
                f"Unknown museum_id in facility spaces row {index}: {museum_id}"
            )
        floor_min = optional_int(raw.get("floor_min"))
        floor_max = optional_int(raw.get("floor_max"))
        if floor_min is not None and floor_max is None:
            floor_max = floor_min
        if floor_max is not None and floor_min is None:
            floor_min = floor_max
        if floor_min is not None and floor_max is not None and floor_min > floor_max:
            raise ValueError(
                f"floor_min exceeds floor_max in facility spaces row {index}"
            )
        space_type = text(raw.get("space_type")) or "museum_occupancy"
        presence_status = text(raw.get("presence_status")) or "yes"
        collections_present = text(raw.get("collections_present")) or "unknown"
        review_status = text(raw.get("review_status")) or "accepted"
        for field, value, allowed in (
            ("presence_status", presence_status, {"yes", "no", "unknown"}),
            ("collections_present", collections_present, {"yes", "no", "unknown"}),
            ("review_status", review_status, {"accepted", "needs_review"}),
        ):
            if value not in allowed:
                raise ValueError(
                    f"Invalid {field} in facility spaces row {index}: {value}"
                )
        floor_label = text(raw.get("floor_label"))
        is_basement = text(raw.get("is_basement"))
        if not is_basement:
            is_basement = (
                "yes" if floor_min is not None and floor_min < 0
                else "no" if floor_min is not None else "unknown"
            )
        if is_basement not in {"yes", "no", "unknown"}:
            raise ValueError(
                f"Invalid is_basement in facility spaces row {index}: {is_basement}"
            )
        space_id = text(raw.get("space_id"))
        if not space_id:
            key = "|".join((museum_id, space_type, floor_label, str(index)))
            space_id = "MSP-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        row = {field: text(raw.get(field)) for field in SPACE_FIELDS}
        row.update({
            "space_id": space_id,
            "museum_id": museum_id,
            "space_type": space_type,
            "presence_status": presence_status,
            "floor_label": floor_label,
            "floor_min": floor_min,
            "floor_max": floor_max,
            "is_basement": is_basement,
            "floor_elevation_min_m": optional_float(raw.get("floor_elevation_min_m")),
            "floor_elevation_max_m": optional_float(raw.get("floor_elevation_max_m")),
            "collections_present": collections_present,
            "review_status": review_status,
        })
        spaces.append(row)

    spaces_by_museum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    accepted_claims_by_museum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in spaces:
        if row["review_status"] == "accepted":
            accepted_claims_by_museum[row["museum_id"]].append(row)
        if row["review_status"] == "accepted" and row["presence_status"] == "yes":
            spaces_by_museum[row["museum_id"]].append(row)
    for facility in facilities:
        rows = spaces_by_museum.get(facility["museum_id"], [])
        accepted_claims = accepted_claims_by_museum.get(facility["museum_id"], [])
        floors = [
            value for row in rows for value in (row["floor_min"], row["floor_max"])
            if value is not None
        ]
        storage = [row for row in rows if row["space_type"] == "collection_storage"]
        storage_absent = any(
            row["space_type"] == "collection_storage"
            and row["presence_status"] == "no"
            for row in accepted_claims
        )
        storage_floors = [
            value for row in storage for value in (row["floor_min"], row["floor_max"])
            if value is not None
        ]
        facility.update({
            "floor_data_status": "available" if rows else "unknown",
            "facility_floor_labels": joined(row["floor_label"] for row in rows),
            "facility_floor_min": min(floors) if floors else None,
            "facility_floor_max": max(floors) if floors else None,
            "facility_spans_multiple_floors": int(
                bool(floors) and min(floors) != max(floors)
            ),
            "collection_storage_status": (
                "yes" if storage else "no" if storage_absent else "unknown"
            ),
            "storage_floor_labels": joined(row["floor_label"] for row in storage),
            "storage_floor_min": min(storage_floors) if storage_floors else None,
            "storage_floor_max": max(storage_floors) if storage_floors else None,
            "storage_in_basement": (
                "yes" if any(row["is_basement"] == "yes" for row in storage)
                else "no" if storage else "unknown"
            ),
        })
    return spaces


def apply_location_enrichment(
    facilities: list[dict[str, Any]], data_dir: Path
) -> None:
    """Overlay accepted web/ABR evidence without changing the source manifest."""
    enrichment_path = data_dir / "museum_location_enrichment.csv"
    rows = read_csv_rows(enrichment_path) if enrichment_path.is_file() else []
    accepted = {
        row.get("museum_id", ""): row
        for row in rows
        if row.get("museum_id") and row.get("review_status") == "accepted"
    }
    fields = (
        "location_type", "address_source_url", "source_authority",
        "extraction_method", "geocoder", "geocode_score", "match_level",
        "coordinate_level", "coordinate_use", "review_status", "retrieved_at",
        "content_sha256",
    )
    for facility in facilities:
        row = accepted.get(facility["museum_id"], {})
        facility["source_manifest_address"] = text(facility.get("address"))
        facility["source_manifest_postal_code"] = text(facility.get("postal_code"))
        facility["location_overlay_applied"] = int(bool(row))
        if row.get("address_normalized"):
            facility["address"] = text(row["address_normalized"])
            facility["postal_code"] = text(row.get("postal_code")) or facility.get(
                "postal_code", ""
            )
        facility["latitude"] = optional_float(row.get("latitude"))
        facility["longitude"] = optional_float(row.get("longitude"))
        for field in fields:
            output_field = field if field == "location_type" else f"location_{field}"
            facility[output_field] = text(row.get(field))


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

    apply_location_enrichment(facilities, data_dir)
    source_records.sort(key=lambda row: (row.get("canonical_facility_id", ""), row["record_id"]))
    return facilities, source_records


def city_names_from_facilities(facilities: list[dict[str, Any]]) -> dict[str, str]:
    return {
        text(row["municipality_code"]): text(row["municipality_name"])
        for row in facilities if text(row["municipality_code"])
    }


def plateau_cities_from_facilities(facilities: list[dict[str, Any]]) -> list[PlateauCity]:
    return [
        PlateauCity(
            pref_code=city_code[:2], pref="東京都", city_code=city_code,
            city=city_name, year="latest", feature_types=["bldg"], url="",
        )
        for city_code, city_name in sorted(city_names_from_facilities(facilities).items())
    ]


def discover_plateau_files(plateau_dir: Path, facilities: list[dict[str, Any]]):
    files_by_path = {}
    for city in plateau_cities_from_facilities(facilities):
        for plateau_file in local_files(plateau_dir, city):
            if plateau_file.local_path:
                files_by_path[plateau_file.local_path] = plateau_file
    return [files_by_path[path] for path in sorted(files_by_path)]


def museum_query_address(facility: dict[str, Any]) -> str:
    """Return an official address or a conservative name+municipality query."""
    return text(facility.get("address")) or " ".join(
        value for value in (
            text(facility.get("municipality_name")),
            text(facility.get("canonical_name")),
        ) if value
    )


def facility_point(facility: dict[str, Any]):
    latitude = optional_float(facility.get("latitude"))
    longitude = optional_float(facility.get("longitude"))
    if latitude is None or longitude is None:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return Point(longitude, latitude)


def resolve_targeted_remote_files(
    api_base: str, facilities: list[dict[str, Any]], timeout_s: int,
) -> tuple[list[PlateauFile], list[dict[str, Any]]]:
    """Resolve bldg meshes around known facilities through API geocoding."""
    facilities_by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for facility in facilities:
        facilities_by_city[text(facility.get("municipality_code"))].append(facility)

    file_map: dict[str, PlateauFile] = {}
    issues: list[dict[str, Any]] = []
    cities = plateau_cities_from_facilities(facilities)
    for index, city in enumerate(cities, 1):
        print(
            f"  PLATEAU targeted query city [{index}/{len(cities)}]: "
            f"{city.city_code} {city.city}",
            flush=True,
        )
        records = [
            CulturalRecord(
                source_file="Museum/source/data/museum_candidates.csv",
                record_id=text(facility.get("museum_id")),
                name=text(facility.get("canonical_name")),
                address=museum_query_address(facility),
                municipality=city.city,
                municipality_code=city.city_code,
                geometry=facility_point(facility),
            )
            for facility in facilities_by_city.get(city.city_code, [])
            if museum_query_address(facility)
        ]
        remote, city_issues = resolve_remote_files(
            api_base, city, records, timeout_s,
            use_geocode=True, progress=True,
        )
        issues.extend(city_issues)
        for plateau_file in remote:
            if plateau_file.url:
                file_map[plateau_file.url] = plateau_file
    return list(file_map.values()), issues


def resolve_municipality_remote_files(
    api_base: str, facilities: list[dict[str, Any]], timeout_s: int,
) -> tuple[list[PlateauFile], list[dict[str, Any]]]:
    """Resolve every bldg mesh for all municipalities represented in manifest."""
    file_map: dict[str, PlateauFile] = {}
    issues: list[dict[str, Any]] = []
    cities = plateau_cities_from_facilities(facilities)
    for index, city in enumerate(cities, 1):
        print(
            f"  PLATEAU municipality query [{index}/{len(cities)}]: "
            f"{city.city_code} {city.city}",
            flush=True,
        )
        try:
            rows = fetch_citygml_files_for_condition(
                api_base, city.city_code, city.city_code, timeout_s
            )
        except Exception as exc:
            issues.append({
                "city_code": city.city_code,
                "condition": city.city_code,
                "reason": f"plateau_query_error: {type(exc).__name__}: {exc}",
            })
            continue
        for row in rows:
            url = text(row.get("url"))
            if not url:
                continue
            file_map[url] = PlateauFile(
                city_code=city.city_code,
                city_name=city.city,
                code=text(row.get("code")),
                url=url,
                max_lod=row.get("maxLod"),
                file_size=row.get("fileSize"),
                features=row.get("features"),
            )
    return list(file_map.values()), issues


def acquire_plateau_files(
    source: str, plateau_dir: Path, facilities: list[dict[str, Any]],
    api_base: str, timeout_s: int, retries: int,
) -> tuple[list[PlateauFile], list[dict[str, Any]]]:
    if source == "local":
        if not plateau_dir.is_dir():
            raise FileNotFoundError(
                f"PLATEAU local directory not found: {plateau_dir}. "
                "Use --plateau-source api-targeted to reacquire relevant meshes, "
                "or api-municipality for exhaustive municipality coverage."
            )
        return discover_plateau_files(plateau_dir, facilities), []

    plateau_dir.mkdir(parents=True, exist_ok=True)
    if source == "api-targeted":
        remote, query_issues = resolve_targeted_remote_files(api_base, facilities, timeout_s)
    else:
        remote, query_issues = resolve_municipality_remote_files(api_base, facilities, timeout_s)
    print(f"PLATEAU remote bldg files resolved: {len(remote)}", flush=True)
    downloaded, download_issues = download_files(
        remote, plateau_dir,
        connect_timeout_s=min(timeout_s, 30), read_timeout_s=timeout_s,
        retries=retries, progress=True,
    )
    usable = [plateau_file for plateau_file in downloaded if plateau_file.local_path]
    return usable, [*query_issues, *download_issues]


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


def is_tokyo_culture_usage(
    city_code: str, detailed_usage_code: str, detailed_usage_codespace: str
) -> bool:
    """Recognize Tokyo land-use survey code 1122 without treating it as Museum-specific."""
    if not text(city_code).startswith("13"):
        return False
    if text(detailed_usage_code) not in TOKYO_CULTURE_DETAILED_USAGE:
        return False
    codespace_name = Path(text(detailed_usage_codespace)).name
    return not codespace_name or codespace_name == "BuildingDetailAttribute_detailedUsage.xml"


def municipality_name_to_code(facilities: list[dict[str, Any]]) -> dict[str, str]:
    """Load all Tokyo municipalities, with the active manifest as fallback."""
    mapping: dict[str, str] = {}
    config_path = ROOT / "source" / "config" / "tokyo_municipalities.csv"
    if config_path.is_file():
        for row in read_csv_rows(config_path):
            name = text(row.get("municipality_name"))
            code = text(row.get("municipality_code"))
            if name and code:
                mapping[name] = code
    for facility in facilities:
        name = text(facility.get("municipality_name"))
        code = text(facility.get("municipality_code"))
        if name and code:
            mapping[name] = code
    return mapping


def building_matching_city(
    building, municipality_codes: dict[str, str]
) -> tuple[str, str]:
    """Prefer the municipality written in the Building address over query/cache scope."""
    address = compact_address(getattr(building, "address", ""))
    for name in sorted(municipality_codes, key=len, reverse=True):
        if name in address:
            return municipality_codes[name], "plateau_address"
    return text(getattr(building, "city_code", "")), "source_dataset"


def index_facilities(facilities: list[dict[str, Any]]):
    by_city_name: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_city_address: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_city_site_address: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_city_point: dict[str, list[tuple[dict[str, Any], Any]]] = defaultdict(list)
    for facility in facilities:
        city = text(facility["municipality_code"])
        name_key = normalize_name(text(facility["canonical_name"]))
        address_key = museum_address_key(facility["address"])
        site_address_key = museum_site_address_key(facility["address"])
        if city and name_key:
            by_city_name[(city, name_key)].append(facility)
        if city and address_key:
            by_city_address[(city, address_key)].append(facility)
        if city and site_address_key:
            by_city_site_address[(city, site_address_key)].append(facility)
        point = facility_point(facility)
        if (
            city and point is not None
            and facility.get("location_coordinate_use") == "building_candidate"
        ):
            by_city_point[city].append((facility, point))
    return by_city_name, by_city_address, by_city_site_address, by_city_point


def match_buildings(buildings, facilities: list[dict[str, Any]]):
    by_city_name, by_city_address, by_city_site_address, by_city_point = index_facilities(
        facilities
    )
    municipality_codes = municipality_name_to_code(facilities)
    links: list[dict[str, Any]] = []
    building_status: dict[str, dict[str, Any]] = {}

    # Count all footprint hits before classifying any one link. A precise ABR
    # point is accepted only when it selects exactly one PLATEAU Building.
    point_hits_by_building: dict[str, list[dict[str, Any]]] = defaultdict(list)
    point_building_counts: Counter[str] = Counter()
    building_cities: dict[str, tuple[str, str]] = {}
    for building in buildings:
        city, city_method = building_matching_city(building, municipality_codes)
        building_cities[building.gml_id] = (city, city_method)
        geometry = getattr(building, "geometry", None)
        if geometry is None:
            continue
        for facility, point in by_city_point.get(city, []):
            try:
                if geometry.covers(point):
                    point_hits_by_building[building.gml_id].append(facility)
                    point_building_counts[facility["museum_id"]] += 1
            except Exception:
                continue

    for building in buildings:
        city, city_method = building_cities[building.gml_id]
        name_key = normalize_name(text(building.name))
        address_key = museum_address_key(building.address)
        site_address_key = museum_site_address_key(building.address)
        usage_code, usage_label, usage_codespace, detail_code, detail_label, detail_codespace = building_usage(building)
        strong_usage = detail_code in STRONG_DETAILED_USAGE
        tokyo_culture_usage = is_tokyo_culture_usage(city, detail_code, detail_codespace)
        keyword_candidate = has_museum_keyword(building.name)
        name_hits = by_city_name.get((city, name_key), []) if name_key else []
        address_hits = by_city_address.get((city, address_key), []) if address_key else []
        site_address_hits = (
            by_city_site_address.get((city, site_address_key), []) if site_address_key else []
        )
        point_hits = point_hits_by_building.get(building.gml_id, [])
        facilities_by_id = {
            row["museum_id"]: row
            for row in [*name_hits, *address_hits, *site_address_hits, *point_hits]
        }

        confirmed_ids: list[str] = []
        review_ids: list[str] = []
        for facility_id, facility in sorted(facilities_by_id.items()):
            exact_name = facility in name_hits
            exact_address = facility in address_hits
            site_address = facility in site_address_hits and not exact_address
            point_in_building = facility in point_hits
            unique_precise_point = (
                point_in_building
                and point_building_counts[facility_id] == 1
                and facility.get("location_coordinate_use") == "building_candidate"
            )
            # Address plus an exact museum/zoo detailed-use code is accepted only
            # when the source address identifies one facility. Shared addresses
            # remain reviewable because campuses and complexes can contain several.
            confirmed = (
                exact_name
                or unique_precise_point
                or (exact_address and strong_usage and len(address_hits) == 1)
            )
            match_status = "confirmed" if confirmed else "needs_review"
            methods = []
            if exact_name:
                methods.append("exact_name")
            if exact_address:
                methods.append("exact_address")
            if site_address:
                methods.append("site_address")
            if point_in_building:
                methods.append("point_in_building")
            if unique_precise_point:
                methods.append("unique_precise_point_in_building")
            if strong_usage:
                methods.append("detailed_usage_exact_museum")
            if tokyo_culture_usage:
                methods.append("tokyo_culture_facility")
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
                "site_address_match": int(site_address),
                "point_in_building": int(point_in_building),
                "unique_precise_point_match": int(unique_precise_point),
                "detailed_usage_match": int(strong_usage or tokyo_culture_usage),
                "candidate_building_count": 0,
                "manual_override": 0,
                "review_required": int(not confirmed),
                "matched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "source_gml": text(building.source_file),
            })
            (confirmed_ids if confirmed else review_ids).append(facility_id)

        plateau_candidate = strong_usage or tokyo_culture_usage or keyword_candidate
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
            "source_city_code": text(building.city_code),
            "matching_city_code": city,
            "matching_city_method": city_method,
            "confirmed_ids": confirmed_ids,
            "review_ids": review_ids,
            "candidate_methods": joined([
                "detailed_usage_exact_museum" if strong_usage else "",
                "tokyo_culture_facility" if tokyo_culture_usage else "",
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
            "floor_data_status": joined(
                row["floor_data_status"] for row in confirmed_facilities
            ),
            "facility_floor_labels": joined(
                row["facility_floor_labels"] for row in confirmed_facilities
            ),
            "facility_floor_min": min(
                (
                    row["facility_floor_min"] for row in confirmed_facilities
                    if row["facility_floor_min"] is not None
                ),
                default=None,
            ),
            "facility_floor_max": max(
                (
                    row["facility_floor_max"] for row in confirmed_facilities
                    if row["facility_floor_max"] is not None
                ),
                default=None,
            ),
            "facility_spans_multiple_floors": int(any(
                row["facility_spans_multiple_floors"] for row in confirmed_facilities
            )),
            "collection_storage_status": joined(
                row["collection_storage_status"] for row in confirmed_facilities
            ) or "unknown",
            "storage_floor_labels": joined(
                row["storage_floor_labels"] for row in confirmed_facilities
            ),
            "storage_floor_min": min(
                (
                    row["storage_floor_min"] for row in confirmed_facilities
                    if row["storage_floor_min"] is not None
                ),
                default=None,
            ),
            "storage_floor_max": max(
                (
                    row["storage_floor_max"] for row in confirmed_facilities
                    if row["storage_floor_max"] is not None
                ),
                default=None,
            ),
            "storage_in_basement": joined(
                row["storage_in_basement"] for row in confirmed_facilities
            ) or "unknown",
            "match_status": state["status"],
            "match_methods": joined(
                method
                for link in confirmed_links
                for method in link["match_methods"].split(";")
                if method
            ) or state["candidate_methods"],
            "source_count": sum(int(row["source_record_count"]) for row in confirmed_facilities),
            "review_required": int(state["status"] != "confirmed"),
            "source_city_code": state["source_city_code"],
            "matching_city_code": state["matching_city_code"],
            "matching_city_method": state["matching_city_method"],
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


def facility_points_frame(facilities) -> gpd.GeoDataFrame:
    """Create a map-ready point layer without promoting it to a Building match."""
    rows = []
    for facility in facilities:
        point = facility_point(facility)
        if point is None:
            continue
        rows.append({
            **facility,
            "display_name": text(facility.get("canonical_name")),
            "geometry": point,
        })
    if not rows:
        return gpd.GeoDataFrame(
            columns=["geometry"], geometry="geometry", crs="EPSG:4326"
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


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


def assess_space_inundation(
    space: dict[str, Any], risk_type: str, depth_m: float | None,
) -> tuple[str, str]:
    """Return an auditable exposure class without inventing a floor height."""
    if risk_type not in INUNDATION_RISK_TYPES:
        return "not_assessed_non_inundation", "future hazard-specific model required"
    if depth_m is None:
        return "hazard_present_depth_unknown", "PLATEAU risk has no numeric depth"
    if depth_m <= 0:
        return "above_water_level", "inundation depth is zero"
    lower_height = optional_float(space.get("floor_elevation_min_m"))
    if lower_height is not None:
        if depth_m >= lower_height:
            return "potentially_exposed", "depth >= recorded floor lower elevation"
        return "above_water_level", "depth < recorded floor lower elevation"
    if space.get("is_basement") == "yes":
        return "potentially_exposed", "basement space and positive inundation depth"
    floor_min = optional_int(space.get("floor_min"))
    if floor_min is not None and floor_min <= 1:
        return "potentially_exposed", "ground/first-floor space and positive inundation depth"
    if floor_min is not None and floor_min > 1:
        return "undetermined_floor_elevation", "upper floor recorded but floor height unknown"
    return "undetermined_floor", "facility floor is unknown"


def build_space_hazard_assessments(buildings, spaces, links) -> list[dict[str, Any]]:
    """Cross confirmed Museum spaces with every PLATEAU Building risk record."""
    buildings_by_id = {building.gml_id: building for building in buildings}
    spaces_by_museum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for space in spaces:
        if space["review_status"] == "accepted" and space["presence_status"] == "yes":
            spaces_by_museum[space["museum_id"]].append(space)
    rows: list[dict[str, Any]] = []
    seen_links = set()
    for link in links:
        if link.get("match_status") != "confirmed":
            continue
        link_key = (link["museum_id"], link["building_gml_id"])
        if link_key in seen_links:
            continue
        seen_links.add(link_key)
        building = buildings_by_id.get(link["building_gml_id"])
        if building is None:
            continue
        for space in spaces_by_museum.get(link["museum_id"], []):
            for risk_index, risk in enumerate(
                getattr(building, "disaster_risks", None) or []
            ):
                risk_type = text(getattr(risk, "risk_type", ""))
                depth_m = optional_float(getattr(risk, "depth_m", None))
                exposure_status, assessment_basis = assess_space_inundation(
                    space, risk_type, depth_m
                )
                key = "|".join((
                    space["space_id"], building.gml_id, str(risk_index), risk_type
                ))
                rows.append({
                    "assessment_id": "MSH-" + hashlib.sha1(
                        key.encode("utf-8")
                    ).hexdigest()[:14],
                    "space_id": space["space_id"],
                    "museum_id": space["museum_id"],
                    "building_gml_id": building.gml_id,
                    "risk_index": risk_index,
                    "risk_type": risk_type,
                    "description_code": text(getattr(risk, "description_code", "")),
                    "description_label": text(getattr(risk, "description_label", "")),
                    "rank_code": text(getattr(risk, "rank_code", "")),
                    "rank_label": text(getattr(risk, "rank_label", "")),
                    "depth_m": depth_m,
                    "space_type": space["space_type"],
                    "space_name": space["space_name"],
                    "floor_label": space["floor_label"],
                    "floor_min": space["floor_min"],
                    "floor_max": space["floor_max"],
                    "is_basement": space["is_basement"],
                    "floor_elevation_min_m": space["floor_elevation_min_m"],
                    "floor_elevation_max_m": space["floor_elevation_max_m"],
                    "collections_present": space["collections_present"],
                    "exposure_status": exposure_status,
                    "assessment_basis": assessment_basis,
                    "model_version": "floor_inundation_potential_v0.1",
                })
    return rows


def write_output(
    source_gpkg: Path, output_gpkg: Path, confirmed: gpd.GeoDataFrame,
    candidates: gpd.GeoDataFrame, facility_points: gpd.GeoDataFrame,
    facilities, source_records, facility_spaces, space_hazard_assessments,
    links, unresolved, buildings, overwrite: bool,
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
    if not facility_points.empty:
        facility_points.to_file(
            output_gpkg, layer="museum_facility_points", driver="GPKG",
            engine="pyogrio", mode="a",
        )

    with sqlite3.connect(output_gpkg) as connection:
        write_attribute_table(connection, "museum_facilities", facilities)
        write_attribute_table(connection, "museum_source_records", source_records)
        write_attribute_table(
            connection, "museum_facility_spaces", facility_spaces, SPACE_FIELDS
        )
        write_attribute_table(
            connection, "museum_space_hazard_assessment",
            space_hazard_assessments, SPACE_ASSESSMENT_FIELDS,
        )
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
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    parser.add_argument("source_gpkg", type=Path, help="Existing hazard GeoPackage; never modified")
    parser.add_argument(
        "--plateau-source",
        choices=("local", "api-targeted", "api-municipality"),
        default="local",
        help=(
            "local: reuse existing GML; api-targeted: download meshes around known facilities; "
            "api-municipality: download all bldg meshes for represented municipalities"
        ),
    )
    parser.add_argument("--plateau-local-dir", type=Path, default=DEFAULT_PLATEAU_DIR,
                        help="Local GML directory, or cache destination in an API mode")
    parser.add_argument(
        "--plateau-api-base", default="https://api.plateauview.mlit.go.jp",
        help="PLATEAU data-catalog API base URL",
    )
    parser.add_argument("--plateau-timeout", type=int, default=180,
                        help="API/download read timeout in seconds")
    parser.add_argument("--download-retries", type=int, default=3,
                        help="Download attempts per GML file")
    parser.add_argument(
        "--skip-duplicate-audit",
        action="store_true",
        help=(
            "Skip the pre-scan that verifies duplicate gml:id payloads. "
            "Use only when input duplication has already been audited."
        ),
    )
    parser.add_argument(
        "--exclude-unresolved-duplicates",
        action="store_true",
        help=(
            "After auditing, exclude every occurrence of unresolved duplicate "
            "gml:id values instead of selecting one copy. This is deterministic "
            "and records the exclusion in the summary."
        ),
    )
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

    facilities, source_records = load_museum_data(museum_data_dir)
    facility_spaces = load_facility_spaces(museum_data_dir, facilities)
    plateau_files, acquisition_issues = acquire_plateau_files(
        args.plateau_source, plateau_dir, facilities,
        args.plateau_api_base, args.plateau_timeout, args.download_retries,
    )
    if not plateau_files:
        raise RuntimeError(
            f"No usable PLATEAU bldg GML files found for source={args.plateau_source}: "
            f"{plateau_dir}; acquisition issues={len(acquisition_issues)}"
        )
    print(f"Museum facilities: {len(facilities)}", flush=True)
    print(f"PLATEAU bldg files: {len(plateau_files)}", flush=True)
    if disaster_risk_rows is None:
        raise RuntimeError(
            "This tool requires the Extractor v0.5.5 disaster-risk output API. "
            "Align the repository and environment to v0.5.5 before running it."
        )
    duplicate_audit: dict[str, Any]
    preferred_sources: dict[str, str] = {}
    excluded_gml_ids: set[str] = set()
    if args.skip_duplicate_audit:
        if args.exclude_unresolved_duplicates:
            raise ValueError(
                "--exclude-unresolved-duplicates requires the duplicate audit"
            )
        duplicate_audit = {
            "audit_skipped": True,
            "building_elements_with_gml_id": None,
            "unique_gml_id_count": None,
            "duplicate_gml_id_count": None,
            "duplicate_occurrences": None,
            "duplicate_conflict_count": None,
            "resolved_duplicate_conflict_count": None,
            "unresolved_duplicate_conflict_count": None,
            "metadata_normalized_duplicate_conflict_count": None,
            "namespace_normalized_duplicate_conflict_count": None,
            "order_normalized_duplicate_conflict_count": None,
            "duplicate_gml_id_samples": [],
            "resolved_duplicate_conflicts": [],
            "duplicate_conflicts": [],
        }
    else:
        audit_result = audit_gml_id_duplicates(plateau_files, progress=True)
        preferred_sources = audit_result.pop("_preferred_source_by_gml_id")
        unresolved_gml_ids = set(audit_result.pop("_unresolved_gml_ids"))
        duplicate_audit = {
            "audit_skipped": False,
            **audit_result,
        }
        print(
            "PLATEAU duplicate gml:id: "
            f"{duplicate_audit['duplicate_gml_id_count']} IDs / "
            f"{duplicate_audit['duplicate_occurrences']} extra occurrences / "
            f"{duplicate_audit['duplicate_conflict_count']} semantic conflicts / "
            f"{duplicate_audit['resolved_duplicate_conflict_count']} resolved / "
            f"{duplicate_audit['unresolved_duplicate_conflict_count']} unresolved",
            flush=True,
        )
        if duplicate_audit["unresolved_duplicate_conflict_count"]:
            if args.exclude_unresolved_duplicates:
                excluded_gml_ids = unresolved_gml_ids
                duplicate_audit["unresolved_duplicate_action"] = (
                    "all_occurrences_excluded"
                )
                duplicate_audit["excluded_unresolved_duplicate_gml_id_count"] = len(
                    excluded_gml_ids
                )
                duplicate_audit["excluded_unresolved_duplicate_gml_id_samples"] = sorted(
                    excluded_gml_ids
                )[:20]
                print(
                    "PLATEAU unresolved duplicate gml:id values excluded: "
                    f"{len(excluded_gml_ids)}",
                    flush=True,
                )
            else:
                samples = joined(
                    row["gml_id"] for row in duplicate_audit["duplicate_conflicts"][:5]
                )
                raise RuntimeError(
                    "Unresolved duplicate PLATEAU gml:id conflicts detected; "
                    "refusing selection without an authoritative source. "
                    "Use --exclude-unresolved-duplicates to exclude every copy "
                    f"deterministically. Samples: {samples}"
                )

    buildings = scan_buildings_with_preferences(
        plateau_files,
        preferred_sources,
        excluded_gml_ids=excluded_gml_ids,
        progress=True,
    )
    print(f"PLATEAU Buildings scanned: {len(buildings)}", flush=True)

    links, building_status = match_buildings(buildings, facilities)
    confirmed, candidates = building_frames(buildings, facilities, links, building_status)
    cities_with_files = {plateau_file.city_code for plateau_file in plateau_files}
    facilities_out, unresolved = facility_status_rows(facilities, links, cities_with_files)
    facility_points = facility_points_frame(facilities_out)
    space_hazard_assessments = build_space_hazard_assessments(
        buildings, facility_spaces, links
    )
    match_method_counts = Counter(
        method
        for link in links
        for method in text(link.get("match_methods")).split(";")
        if method
    )
    building_candidate_method_counts = Counter(
        method
        for state in building_status.values()
        for method in text(state.get("candidate_methods")).split(";")
        if method
    )
    summary = {
        "museum_hazard_tool_version": TOOL_VERSION,
        "source_gpkg": str(source_gpkg),
        "output_gpkg": str(output_gpkg),
        "plateau_local_dir": str(plateau_dir),
        "plateau_source": args.plateau_source,
        "plateau_acquisition_issue_count": len(acquisition_issues),
        "plateau_acquisition_issues": acquisition_issues,
        "museum_facilities": len(facilities_out),
        "museum_source_records": len(source_records),
        "museum_facility_spaces": len(facility_spaces),
        "museum_facilities_with_floor_data": sum(
            row.get("floor_data_status") == "available" for row in facilities_out
        ),
        "museum_facilities_with_collection_storage": sum(
            row.get("collection_storage_status") == "yes" for row in facilities_out
        ),
        "museum_space_hazard_assessments": len(space_hazard_assessments),
        "space_exposure_status_counts": dict(Counter(
            row["exposure_status"] for row in space_hazard_assessments
        )),
        "location_enriched_address_count": sum(
            bool(row.get("location_address_source_url"))
            and row.get("location_extraction_method") != "existing_manifest"
            for row in facilities_out
        ),
        "location_coordinate_count": sum(
            row.get("latitude") is not None and row.get("longitude") is not None
            for row in facilities_out
        ),
        "location_building_candidate_point_count": sum(
            row.get("location_coordinate_use") == "building_candidate"
            for row in facilities_out
        ),
        "museum_facility_points": len(facility_points),
        "plateau_files": len(plateau_files),
        "plateau_duplicate_audit": duplicate_audit,
        "plateau_buildings_scanned": len(buildings),
        "confirmed_museum_buildings": len(confirmed),
        "candidate_museum_buildings": len(candidates),
        "building_links": len(links),
        "building_link_status_counts": dict(Counter(link["match_status"] for link in links)),
        "match_method_counts": dict(match_method_counts),
        "building_candidate_method_counts": dict(building_candidate_method_counts),
        "selected_building_address_municipality_override_count": sum(
            state["source_city_code"] != state["matching_city_code"]
            and state["matching_city_method"] == "plateau_address"
            for state in building_status.values()
        ),
        "confirmed_facilities": sum(row["match_status"] == "confirmed" for row in facilities_out),
        "unresolved_facilities": len(unresolved),
        "unresolved_reason_counts": dict(Counter(row["reason"] for row in unresolved)),
        "dry_run": bool(args.dry_run),
    }

    if not args.dry_run:
        summary["new_disaster_risk_rows"] = write_output(
            source_gpkg, output_gpkg, confirmed, candidates, facility_points, facilities_out,
            source_records, facility_spaces, space_hazard_assessments,
            links, unresolved, buildings, args.overwrite,
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
