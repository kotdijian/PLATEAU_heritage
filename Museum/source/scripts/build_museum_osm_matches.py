#!/usr/bin/env python3
"""Collect and audit OpenStreetMap candidates for canonical Tokyo museums.

This is an evidence-only pilot.  It never changes the canonical museum list,
PLATEAU building links, or a GeoPackage.  A single Tokyo-wide Overpass result
is cached, then matched locally against all canonical facilities.  When a
Museum hazard GeoPackage is supplied, already-confirmed facilities are marked
as audit-only and the remaining facilities are marked for candidate discovery.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    from .build_museum_manifest import normalize_name
    from .enrich_museum_locations import canonical_facilities, read_csv
except ImportError:  # Direct execution: python Museum/source/scripts/...
    from build_museum_manifest import normalize_name
    from enrich_museum_locations import canonical_facilities, read_csv


SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = SCRIPT_DIR.parent
DEFAULT_DATA_DIR = SOURCE_ROOT / "data"
DEFAULT_CACHE_PATH = SOURCE_ROOT / "cache" / "osm" / "tokyo_museum.json"
DEFAULT_GEOMETRY_CACHE_PATH = (
    SOURCE_ROOT / "cache" / "osm" / "tokyo_museum_shortlist_geometry.json"
)
DEFAULT_ALIASES = SOURCE_ROOT / "config" / "name_aliases.csv"
DEFAULT_ENDPOINT = "https://overpass-api.de/api/interpreter"
TOOL_VERSION = "0.1.2"

OVERPASS_QUERY = r"""
[out:json][timeout:300];
area["ISO3166-2"="JP-13"]["boundary"="administrative"]->.tokyo;
(
  nwr["tourism"~"^(museum|gallery|zoo|aquarium)$"](area.tokyo);
  nwr["amenity"="arts_centre"](area.tokyo);
  nwr["museum"](area.tokyo);
  nwr["name"~"博物館|美術館|資料館|史料館|記念館|科学館|郷土館|歴史館|文学館|動物園|水族館|植物園"](area.tokyo);
);
out center tags;
""".strip()

NAME_TAGS = (
    "name", "name:ja", "official_name", "official_name:ja", "alt_name",
    "short_name", "brand",
)

CANDIDATE_FIELDS = [
    "museum_id", "canonical_name", "municipality_code", "municipality_name",
    "plateau_match_scope", "osm_type", "osm_id", "osm_url", "osm_name",
    "osm_names", "osm_latitude", "osm_longitude", "osm_municipality",
    "osm_address", "osm_website", "osm_operator", "osm_primary_tag",
    "osm_object_role", "osm_wikidata", "osm_source", "osm_source_ref",
    "osm_check_date",
    "name_match", "name_similarity", "municipality_match", "distance_m",
    "website_match", "coordinate_conflict", "candidate_score",
    "high_confidence", "candidate_rank", "candidate_group_id",
    "candidate_group_size", "selected_in_group", "geometry_available",
    "retrieved_at", "osm_data_sha256",
]

AUDIT_FIELDS = [
    "museum_id", "facility_name", "municipality_code", "municipality_name",
    "plateau_match_scope", "osm_status", "candidate_count", "candidate_group_count",
    "high_confidence_count", "high_confidence_group_count",
    "selected_group_id", "selected_group_members", "selected_osm_type", "selected_osm_id",
    "selected_osm_name", "selected_osm_url", "selected_latitude",
    "selected_longitude", "selected_object_role", "selected_score",
    "coordinate_conflict", "selection_reason",
]


def clean(value) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_alias_file(path: Path) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for row in read_csv(path):
        alias = normalize_name(row.get("alias", ""))
        canonical = normalize_name(row.get("canonical_name", ""))
        if alias and canonical:
            aliases[alias] = canonical
    return aliases


def split_osm_names(tags: dict[str, object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for key in NAME_TAGS:
        for value in clean(tags.get(key)).split(";"):
            value = clean(value)
            if value and value not in seen:
                seen.add(value)
                result.append(value)
    return result


def website_host(value: str) -> str:
    value = clean(value)
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    host = (urlparse(value).hostname or "").casefold()
    return host.removeprefix("www.")


def osm_address(tags: dict[str, object]) -> str:
    if clean(tags.get("addr:full")):
        return clean(tags["addr:full"])
    parts = [
        clean(tags.get("addr:province")), clean(tags.get("addr:city")),
        clean(tags.get("addr:ward")), clean(tags.get("addr:suburb")),
        clean(tags.get("addr:quarter")), clean(tags.get("addr:neighbourhood")),
        clean(tags.get("addr:block_number")), clean(tags.get("addr:housenumber")),
    ]
    return " ".join(value for value in parts if value)


def osm_municipality(tags: dict[str, object]) -> str:
    values = [
        clean(tags.get("addr:city")), clean(tags.get("addr:ward")),
        clean(tags.get("addr:municipality")), clean(tags.get("addr:full")),
    ]
    return " ".join(value for value in values if value)


def municipality_evidence(expected: str, osm_value: str) -> str:
    """Classify only an explicit Japanese municipality as a contradiction."""
    expected = clean(expected)
    osm_value = clean(osm_value)
    if expected and expected in osm_value:
        return "match"
    explicit_municipality = bool(re.search(r"[^\s,，]+(?:区|市|町|村)", osm_value))
    return "conflict" if explicit_municipality else "unknown"


def primary_tag(tags: dict[str, object]) -> str:
    for key in ("tourism", "amenity", "museum", "building", "building:use"):
        value = clean(tags.get(key))
        if value:
            return f"{key}={value}"
    return ""


def object_role(
    tags: dict[str, object], osm_type: str, name_match: str, website_match: bool
) -> str:
    """Separate the facility itself from POIs borrowing the facility name."""
    tourism = clean(tags.get("tourism"))
    amenity = clean(tags.get("amenity"))
    building = clean(tags.get("building"))
    building_use = clean(tags.get("building:use"))
    if osm_type in {"way", "relation"} and (
        building == "museum"
        or building_use == "museum"
        or (building and name_match == "exact")
    ):
        return "facility_building"
    if (
        tourism in {"museum", "gallery", "zoo", "aquarium"}
        or amenity == "arts_centre"
        or clean(tags.get("museum"))
    ):
        return "facility_feature"
    if (
        amenity
        or tourism
        or clean(tags.get("entrance"))
        or clean(tags.get("highway"))
        or clean(tags.get("shop"))
    ):
        return "supporting_poi"
    if website_match:
        return "official_site_supported_object"
    return "named_object"


def role_is_confirmation_evidence(role: str) -> bool:
    return role in {
        "facility_building", "facility_feature", "official_site_supported_object",
    }


def object_preference(row: dict[str, object]) -> tuple[int, float, str, int]:
    role = clean(row.get("osm_object_role"))
    osm_type = clean(row.get("osm_type"))
    if role == "facility_building" and osm_type in {"way", "relation"}:
        priority = 0
    elif role == "facility_feature" and osm_type in {"way", "relation"}:
        priority = 1
    elif role == "facility_feature":
        priority = 2
    elif role == "official_site_supported_object":
        priority = 3
    elif role == "named_object":
        priority = 4
    else:
        priority = 5
    return (
        priority,
        -float(row.get("candidate_score") or 0),
        osm_type,
        int(row.get("osm_id") or 0),
    )


def element_coordinate(element: dict[str, object]) -> tuple[float | None, float | None]:
    center = element.get("center") if isinstance(element.get("center"), dict) else {}
    lat = element.get("lat", center.get("lat"))
    lon = element.get("lon", center.get("lon"))
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    value = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(value))


def safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compact_match_name(value: str, aliases: dict[str, str]) -> str:
    return normalize_name(clean(value), aliases)


def loose_match_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", clean(value)).casefold()
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々]", "", value)


def name_evidence(
    facility_name: str, osm_names: list[str], aliases: dict[str, str]
) -> tuple[str, float]:
    facility_exact = compact_match_name(facility_name, aliases)
    facility_loose = loose_match_name(facility_name)
    best_method = ""
    best_similarity = 0.0
    for value in osm_names:
        osm_exact = compact_match_name(value, aliases)
        osm_loose = loose_match_name(value)
        if facility_exact and facility_exact == osm_exact:
            return "exact", 1.0
        similarity = SequenceMatcher(None, facility_loose, osm_loose).ratio()
        if (
            min(len(facility_loose), len(osm_loose)) >= 4
            and (facility_loose in osm_loose or osm_loose in facility_loose)
            and similarity > best_similarity
        ):
            best_method, best_similarity = "contained", similarity
        elif similarity >= 0.72 and similarity > best_similarity:
            best_method, best_similarity = "similar", similarity
    return best_method, best_similarity


def location_rows(path: Path) -> dict[str, dict[str, str]]:
    return {
        clean(row.get("museum_id")): row
        for row in read_csv(path)
        if clean(row.get("museum_id")) and row.get("review_status") == "accepted"
    }


def confirmed_museum_ids(gpkg: Path | None) -> set[str]:
    if gpkg is None:
        return set()
    if not gpkg.is_file():
        raise FileNotFoundError(f"Museum GeoPackage not found: {gpkg}")
    connection = sqlite3.connect(gpkg)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='museum_building_links'"
        ).fetchone()
        if not exists:
            raise ValueError(
                "GeoPackage has no museum_building_links table: " + str(gpkg)
            )
        return {
            clean(row[0]) for row in connection.execute(
                "SELECT DISTINCT museum_id FROM museum_building_links "
                "WHERE match_status='confirmed'"
            ) if clean(row[0])
        }
    finally:
        connection.close()


def fetch_overpass(
    endpoint: str, cache_path: Path, *, refresh: bool, offline: bool,
    timeout: int, user_agent: str,
) -> tuple[dict[str, object], str, str]:
    if cache_path.is_file() and not refresh:
        payload = cache_path.read_bytes()
        return json.loads(payload), hashlib.sha256(payload).hexdigest(), "cache"
    if offline:
        raise FileNotFoundError(f"OSM cache not found: {cache_path}")
    response = requests.post(
        endpoint,
        data={"data": OVERPASS_QUERY},
        headers={"User-Agent": user_agent},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.content
    parsed = json.loads(payload)
    if not isinstance(parsed.get("elements"), list):
        raise ValueError("Unexpected Overpass response: elements is missing")
    if not parsed["elements"]:
        raise ValueError("Unexpected empty Overpass result for the Tokyo-wide query")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(payload)
    return parsed, hashlib.sha256(payload).hexdigest(), "network"


def geometry_query(object_keys: list[tuple[str, str]]) -> str:
    ways = sorted({value for kind, value in object_keys if kind == "way"}, key=int)
    relations = sorted(
        {value for kind, value in object_keys if kind == "relation"}, key=int
    )
    selectors = []
    if ways:
        selectors.append("way(id:" + ",".join(ways) + ");")
    if relations:
        selectors.append("rel(id:" + ",".join(relations) + ");")
    return "\n".join([
        "[out:json][timeout:300];",
        "(",
        *["  " + selector for selector in selectors],
        ");",
        "out body geom;",
    ])


def element_has_full_geometry(element: dict[str, object]) -> bool:
    """Return True only for geometry usable in the next spatial stage."""
    osm_type = clean(element.get("type"))
    if osm_type == "way":
        return bool(element.get("geometry"))
    if osm_type == "relation":
        members = element.get("members")
        return bool(
            isinstance(members, list)
            and any(
                isinstance(member, dict) and bool(member.get("geometry"))
                for member in members
            )
        )
    if osm_type == "node":
        return element.get("lat") is not None and element.get("lon") is not None
    return False


def fetch_shortlist_geometry(
    endpoint: str, cache_path: Path, object_keys: list[tuple[str, str]], *,
    refresh: bool, offline: bool, timeout: int, user_agent: str,
) -> tuple[dict[tuple[str, str], dict[str, object]], str, str]:
    requested = sorted(set(object_keys))
    if not requested:
        return {}, "", "not_required"
    repaired_invalid_cache = False
    if cache_path.is_file() and not refresh:
        wrapper = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_requested = {
            (clean(row.get("type")), clean(row.get("id")))
            for row in wrapper.get("requested_objects", [])
        }
        if set(requested).issubset(cached_requested):
            elements = wrapper.get("elements", [])
            result = {
                (clean(row.get("type")), clean(row.get("id"))): row
                for row in elements if isinstance(row, dict)
            }
            usable = {
                key: row for key, row in result.items()
                if element_has_full_geometry(row)
            }
            if set(requested).issubset(usable):
                digest = hashlib.sha256(cache_path.read_bytes()).hexdigest()
                return result, digest, "cache"
            repaired_invalid_cache = True
        if offline:
            raise RuntimeError(
                "OSM geometry cache is missing requested full geometry: "
                + str(cache_path)
            )
    elif offline:
        raise FileNotFoundError(f"OSM geometry cache not found: {cache_path}")

    collected: dict[tuple[str, str], dict[str, object]] = {}
    for start in range(0, len(requested), 100):
        chunk = requested[start:start + 100]
        query = geometry_query(chunk)
        response = requests.post(
            endpoint,
            data={"data": query},
            headers={"User-Agent": user_agent},
            timeout=timeout,
        )
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed.get("elements"), list):
            raise ValueError("Unexpected geometry response: elements is missing")
        for element in parsed["elements"]:
            if isinstance(element, dict):
                key = (clean(element.get("type")), clean(element.get("id")))
                if all(key):
                    collected[key] = element
    wrapper = {
        "tool_version": TOOL_VERSION,
        "generated_at": utc_now(),
        "requested_objects": [
            {"type": kind, "id": value} for kind, value in requested
        ],
        "elements": list(collected.values()),
        "full_geometry_object_count": sum(
            element_has_full_geometry(row) for row in collected.values()
        ),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    digest = hashlib.sha256(cache_path.read_bytes()).hexdigest()
    return collected, digest, (
        "network_repair" if repaired_invalid_cache else "network"
    )


def build_candidate(
    facility: dict[str, str], element: dict[str, object],
    aliases: dict[str, str], location: dict[str, str] | None,
    plateau_scope: str, retrieved_at: str, digest: str,
) -> dict[str, object] | None:
    tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
    names = split_osm_names(tags)
    match_method, similarity = name_evidence(facility["facility_name"], names, aliases)
    if not match_method:
        return None

    municipality_text = osm_municipality(tags)
    municipality_name = clean(facility.get("municipality_name"))
    municipality_match = municipality_evidence(municipality_name, municipality_text)

    lat, lon = element_coordinate(element)
    facility_lat = safe_float((location or {}).get("latitude"))
    facility_lon = safe_float((location or {}).get("longitude"))
    distance = None
    if None not in (lat, lon, facility_lat, facility_lon):
        distance = haversine_m(facility_lat, facility_lon, lat, lon)

    canonical_host = website_host(facility.get("official_url", ""))
    osm_site = clean(tags.get("website") or tags.get("contact:website"))
    osm_host = website_host(osm_site)
    same_website = bool(canonical_host and osm_host and canonical_host == osm_host)
    osm_type = clean(element.get("type"))
    osm_id = clean(element.get("id"))
    role = object_role(tags, osm_type, match_method, same_website)

    score = {"exact": 100.0, "contained": 70.0, "similar": 55.0}[match_method]
    score += {"match": 20.0, "unknown": 0.0, "conflict": -100.0}[municipality_match]
    if same_website:
        score += 30.0
    if distance is not None:
        if distance <= 100:
            score += 30.0
        elif distance <= 500:
            score += 20.0
        elif distance <= 2_000:
            score += 5.0
        elif distance > 10_000:
            score -= 30.0

    precise_location_support = bool(
        location
        and location.get("coordinate_use") == "building_candidate"
        and distance is not None
        and distance <= 2_000
    )
    supporting_location = (
        municipality_match == "match"
        or same_website
        or precise_location_support
    )
    high_confidence = bool(
        match_method == "exact"
        and municipality_match != "conflict"
        and supporting_location
        and role_is_confirmation_evidence(role)
    )
    coordinate_conflict = bool(
        distance is not None
        and distance > 2_000
        and match_method == "exact"
        and (same_website or municipality_match == "match")
    )
    return {
        "museum_id": facility["museum_id"],
        "canonical_name": facility["facility_name"],
        "municipality_code": facility.get("municipality_code", ""),
        "municipality_name": municipality_name,
        "plateau_match_scope": plateau_scope,
        "osm_type": osm_type,
        "osm_id": osm_id,
        "osm_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "osm_name": names[0] if names else "",
        "osm_names": ";".join(names),
        "osm_latitude": "" if lat is None else f"{lat:.7f}",
        "osm_longitude": "" if lon is None else f"{lon:.7f}",
        "osm_municipality": municipality_text,
        "osm_address": osm_address(tags),
        "osm_website": osm_site,
        "osm_operator": clean(tags.get("operator")),
        "osm_primary_tag": primary_tag(tags),
        "osm_object_role": role,
        "osm_wikidata": clean(tags.get("wikidata")),
        "osm_source": clean(tags.get("source")),
        "osm_source_ref": clean(tags.get("source_ref")),
        "osm_check_date": clean(tags.get("check_date")),
        "name_match": match_method,
        "name_similarity": f"{similarity:.6f}",
        "municipality_match": municipality_match,
        "distance_m": "" if distance is None else f"{distance:.2f}",
        "website_match": str(same_website).lower(),
        "coordinate_conflict": str(coordinate_conflict).lower(),
        "candidate_score": f"{score:.2f}",
        "high_confidence": str(high_confidence).lower(),
        "candidate_rank": "",
        "candidate_group_id": "",
        "candidate_group_size": "",
        "selected_in_group": "false",
        "geometry_available": str(
            osm_type == "node" and lat is not None and lon is not None
        ).lower(),
        "retrieved_at": retrieved_at,
        "osm_data_sha256": digest,
    }


def rows_are_same_osm_facility(
    left: dict[str, object], right: dict[str, object]
) -> bool:
    left_wikidata = clean(left.get("osm_wikidata"))
    right_wikidata = clean(right.get("osm_wikidata"))
    if left_wikidata and left_wikidata == right_wikidata:
        return True
    left_host = website_host(clean(left.get("osm_website")))
    right_host = website_host(clean(right.get("osm_website")))
    if left_host and left_host == right_host:
        return True
    if left.get("name_match") != "exact" or right.get("name_match") != "exact":
        return False
    coordinates = [
        safe_float(left.get("osm_latitude")), safe_float(left.get("osm_longitude")),
        safe_float(right.get("osm_latitude")), safe_float(right.get("osm_longitude")),
    ]
    if any(value is None for value in coordinates):
        return False
    return haversine_m(*coordinates) <= 100


def group_candidates(
    facility_id: str, candidates: list[dict[str, object]]
) -> list[dict[str, object]]:
    parents = list(range(len(candidates)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            if rows_are_same_osm_facility(candidates[left], candidates[right]):
                union(left, right)

    grouped: dict[int, list[dict[str, object]]] = {}
    for index, row in enumerate(candidates):
        grouped.setdefault(find(index), []).append(row)

    groups: list[dict[str, object]] = []
    for members in grouped.values():
        keys = sorted(f"{row['osm_type']}/{row['osm_id']}" for row in members)
        group_id = "OSMG-" + hashlib.sha1(
            (facility_id + "|" + "|".join(keys)).encode()
        ).hexdigest()[:14]
        selected = min(members, key=object_preference)
        for row in members:
            row["candidate_group_id"] = group_id
            row["candidate_group_size"] = len(members)
            row["selected_in_group"] = str(row is selected).lower()
        groups.append({
            "group_id": group_id,
            "members": members,
            "selected": selected,
            "high_confidence": any(
                row["high_confidence"] == "true" for row in members
            ),
        })
    groups.sort(key=lambda group: object_preference(group["selected"]))
    return groups


def audit_facility(
    facility: dict[str, str], candidates: list[dict[str, object]], plateau_scope: str,
    groups: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    groups = groups if groups is not None else group_candidates(
        facility["museum_id"], candidates
    )
    high_objects = [row for row in candidates if row["high_confidence"] == "true"]
    high_groups = [group for group in groups if group["high_confidence"]]
    selected_group = high_groups[0] if len(high_groups) == 1 else (
        groups[0] if groups else None
    )
    selected = selected_group["selected"] if selected_group else None
    if len(high_groups) == 1:
        status = "high_confidence_unique"
        reason = "single corroborated OSM facility group"
    elif len(high_groups) > 1:
        status = "multiple_high_confidence"
        reason = "multiple corroborated OSM facility groups require review"
    elif candidates:
        status = "candidate_only"
        reason = "name candidate lacks sufficient corroboration"
    else:
        status = "no_candidate"
        reason = "no supported OSM name candidate"
    row: dict[str, object] = {
        "museum_id": facility["museum_id"],
        "facility_name": facility["facility_name"],
        "municipality_code": facility.get("municipality_code", ""),
        "municipality_name": facility.get("municipality_name", ""),
        "plateau_match_scope": plateau_scope,
        "osm_status": status,
        "candidate_count": len(candidates),
        "candidate_group_count": len(groups),
        "high_confidence_count": len(high_objects),
        "high_confidence_group_count": len(high_groups),
        "selection_reason": reason,
    }
    if selected:
        row.update({
            "selected_group_id": selected_group["group_id"],
            "selected_group_members": ";".join(
                f"{member['osm_type']}/{member['osm_id']}"
                for member in selected_group["members"]
            ),
            "selected_osm_type": selected["osm_type"],
            "selected_osm_id": selected["osm_id"],
            "selected_osm_name": selected["osm_name"],
            "selected_osm_url": selected["osm_url"],
            "selected_latitude": selected["osm_latitude"],
            "selected_longitude": selected["osm_longitude"],
            "selected_object_role": selected["osm_object_role"],
            "selected_score": selected["candidate_score"],
            "coordinate_conflict": str(any(
                member["coordinate_conflict"] == "true"
                for member in selected_group["members"]
            )).lower(),
        })
    return row


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pilot OSM audit/candidate matching for Tokyo museums"
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument(
        "--geometry-cache", type=Path, default=DEFAULT_GEOMETRY_CACHE_PATH
    )
    parser.add_argument("--aliases", type=Path, default=DEFAULT_ALIASES)
    parser.add_argument(
        "--museum-gpkg", type=Path, default=None,
        help="Optional existing Museum GPKG; confirmed IDs are audit-only",
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument(
        "--user-agent",
        default=(
            "PLATEAU-heritage-museum-osm-pilot/0.1.2 "
            "(+https://github.com/kotdijian/PLATEAU_heritage)"
        ),
    )
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--refresh-geometry", action="store_true",
        help="Refresh only the shortlisted way/relation geometry cache",
    )
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--skip-geometry", action="store_true",
        help="Do not fetch geometry for shortlisted OSM ways/relations",
    )
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    data_dir = args.data_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    cache_path = args.cache.expanduser().resolve()
    geometry_cache_path = args.geometry_cache.expanduser().resolve()
    aliases = load_alias_file(args.aliases.expanduser().resolve())
    facilities = canonical_facilities(data_dir)
    if not facilities:
        raise RuntimeError(f"No canonical facilities found in {data_dir}")
    locations = location_rows(data_dir / "museum_location_enrichment.csv")
    confirmed = confirmed_museum_ids(
        args.museum_gpkg.expanduser().resolve() if args.museum_gpkg else None
    )
    payload, digest, fetch_mode = fetch_overpass(
        args.endpoint, cache_path, refresh=args.refresh, offline=args.offline,
        timeout=args.timeout, user_agent=args.user_agent,
    )
    retrieved_at = utc_now()
    elements_by_key: dict[tuple[str, str], dict[str, object]] = {}
    for element in payload.get("elements", []):
        if isinstance(element, dict):
            key = (clean(element.get("type")), clean(element.get("id")))
            if all(key):
                elements_by_key[key] = element
    elements = list(elements_by_key.values())

    candidate_rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []
    shortlist_geometry_keys: set[tuple[str, str]] = set()
    for facility in facilities:
        scope = "audit_only" if facility["museum_id"] in confirmed else "candidate_discovery"
        candidates = [
            row for element in elements
            if (row := build_candidate(
                facility, element, aliases, locations.get(facility["museum_id"]),
                scope, retrieved_at, digest,
            )) is not None
        ]
        candidates.sort(key=lambda row: (
            -float(row["candidate_score"]), row["osm_type"], int(row["osm_id"])
        ))
        for rank, row in enumerate(candidates, start=1):
            row["candidate_rank"] = rank
        groups = group_candidates(facility["museum_id"], candidates)
        for group in groups:
            if not group["high_confidence"]:
                continue
            for member in group["members"]:
                if member["osm_type"] in {"way", "relation"}:
                    shortlist_geometry_keys.add(
                        (clean(member["osm_type"]), clean(member["osm_id"]))
                    )
        candidate_rows.extend(candidates)
        audit_rows.append(audit_facility(facility, candidates, scope, groups))

    geometry_elements: dict[tuple[str, str], dict[str, object]] = {}
    geometry_digest = ""
    geometry_fetch_mode = "skipped"
    if not args.skip_geometry:
        geometry_elements, geometry_digest, geometry_fetch_mode = fetch_shortlist_geometry(
            args.endpoint, geometry_cache_path, sorted(shortlist_geometry_keys),
            refresh=args.refresh or args.refresh_geometry, offline=args.offline,
            timeout=args.timeout,
            user_agent=args.user_agent,
        )
    usable_geometry_elements = {
        key: element for key, element in geometry_elements.items()
        if element_has_full_geometry(element)
    }
    for row in candidate_rows:
        key = (clean(row["osm_type"]), clean(row["osm_id"]))
        if key in usable_geometry_elements:
            row["geometry_available"] = "true"

    write_csv(output_dir / "museum_osm_candidates.csv", candidate_rows, CANDIDATE_FIELDS)
    write_csv(output_dir / "museum_osm_audit.csv", audit_rows, AUDIT_FIELDS)
    status_counts = Counter(row["osm_status"] for row in audit_rows)
    discovery_rows = [row for row in audit_rows if row["plateau_match_scope"] == "candidate_discovery"]
    audit_only_rows = [row for row in audit_rows if row["plateau_match_scope"] == "audit_only"]
    summary: dict[str, object] = {
        "museum_osm_tool_version": TOOL_VERSION,
        "evidence_only": True,
        "overpass_endpoint": args.endpoint,
        "overpass_fetch_mode": fetch_mode,
        "overpass_query_sha256": hashlib.sha256(OVERPASS_QUERY.encode()).hexdigest(),
        "osm_data_sha256": digest,
        "osm_cache": str(cache_path),
        "canonical_facilities": len(facilities),
        "existing_plateau_confirmed_facilities": len(audit_only_rows),
        "plateau_candidate_discovery_facilities": len(discovery_rows),
        "osm_elements": len(elements),
        "osm_candidate_links": len(candidate_rows),
        "osm_candidate_groups": len({
            row["candidate_group_id"] for row in candidate_rows
            if row["candidate_group_id"]
        }),
        "osm_candidate_object_role_counts": dict(Counter(
            row["osm_object_role"] for row in candidate_rows
        )),
        "osm_high_confidence_objects": sum(
            row["high_confidence"] == "true" for row in candidate_rows
        ),
        "osm_status_counts": dict(status_counts),
        "discovery_high_confidence_unique": sum(
            row["osm_status"] == "high_confidence_unique" for row in discovery_rows
        ),
        "audit_only_high_confidence_unique": sum(
            row["osm_status"] == "high_confidence_unique" for row in audit_only_rows
        ),
        "coordinate_conflict_facilities": sum(
            row.get("coordinate_conflict") == "true" for row in audit_rows
        ),
        "geometry_shortlist_objects": len(shortlist_geometry_keys),
        "geometry_returned_objects": len(geometry_elements),
        "geometry_retrieved_objects": len(usable_geometry_elements),
        "geometry_missing_objects": len(
            shortlist_geometry_keys - set(usable_geometry_elements)
        ),
        "geometry_missing_object_samples": [
            f"{kind}/{value}" for kind, value in sorted(
                shortlist_geometry_keys - set(usable_geometry_elements)
            )[:20]
        ],
        "geometry_fetch_mode": geometry_fetch_mode,
        "geometry_cache": str(geometry_cache_path),
        "geometry_data_sha256": geometry_digest,
        "license": "OpenStreetMap data is available under ODbL; attribution required",
        "generated_at": retrieved_at,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "museum_osm_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
