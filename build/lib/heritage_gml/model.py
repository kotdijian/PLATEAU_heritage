from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CulturalRecord:
    source_file: str
    record_id: str
    name: str
    place_name: str = ""
    owner: str = ""
    address: str = ""
    municipality: str = ""
    municipality_code: str = ""
    category: str = ""
    type: str = ""
    designation: str = ""
    designation_date: str = ""
    geometry: Any = None
    # Normalized processing class. Values used by the default pipeline are:
    #   building_direct / point / movable
    entity_class: str = "point"
    geometry_role: str = "representative_point"
    movable: bool = False
    complex_id: str = ""
    complex_name: str = ""
    matched_building_ids: list[str] = field(default_factory=list)
    match_methods: list[str] = field(default_factory=list)
    movable_group_id: str = ""


@dataclass
class PlateauCity:
    pref_code: str
    pref: str
    city_code: str
    city: str
    year: str | int
    feature_types: list[str] = field(default_factory=list)
    url: str = ""


@dataclass
class PlateauFile:
    city_code: str
    city_name: str
    code: str
    url: str
    max_lod: int | None = None
    file_size: int | None = None
    features: int | None = None
    local_path: str | None = None


@dataclass
class BuildingRecord:
    gml_id: str
    source_file: str
    city_code: str
    file_code: str
    geometry: Any
    name: str = ""
    address: str = ""
    usage: str = ""
    detailed_usage: str = ""
    building_id: str = ""
