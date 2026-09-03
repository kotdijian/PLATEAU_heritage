from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CulturalRecord:
    source_file: str
    record_id: str
    name: str
    place_name: str = ""
    address_detail: str = ""
    owner: str = ""
    address: str = ""
    municipality: str = ""
    municipality_code: str = ""
    category: str = ""
    type: str = ""
    designation: str = ""
    designation_date: str = ""
    # Optional classification attributes supplied by heritage-classify.
    # They are pass-through output attributes only and never affect matching.
    designation_level_code: str = ""
    designation_level_ja: str = ""
    designation_status_code: str = ""
    designation_status_ja: str = ""
    heritage_type_major_code: str = ""
    heritage_type_major_ja: str = ""
    heritage_type_detail: str = ""
    classification_confidence: str = ""
    geometry: Any = None
    # Semantic processing class. v0.5 keeps movable as a semantic class only;
    # it follows the same spatial matching/output path as ordinary point records.
    entity_class: str = "point"
    geometry_role: str = "representative_point"
    movable: bool = False
    complex_id: str = ""
    complex_name: str = ""
    complex_grouping_method: str = ""
    complex_record_count: int = 1
    # record_coordinate / shared_complex_coordinate / missing
    source_location_role: str = "record_coordinate"
    # building_matched / complex_only / point_unmatched / unlocated
    spatial_match_status: str = ""
    matched_building_ids: list[str] = field(default_factory=list)
    match_methods: list[str] = field(default_factory=list)


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
class DisasterRiskRecord:
    """One PLATEAU uro:*RiskAttribute attached to a Building.

    Raw code values and codeSpace references are preserved. Human-readable
    labels are resolved only when the referenced codelist is available locally;
    no network lookup is required. Normalized numeric fields are provided for
    GIS analysis while the original value/uom are also retained.
    """
    risk_type: str
    risk_attribute_type: str
    risk_type_ja: str = ""
    description_code: str = ""
    description_label: str = ""
    description_codespace: str = ""
    rank_code: str = ""
    rank_label: str = ""
    rank_codespace: str = ""
    rank_org_code: str = ""
    rank_org_label: str = ""
    rank_org_codespace: str = ""
    depth_value: float | None = None
    depth_uom: str = ""
    depth_m: float | None = None
    admin_type_code: str = ""
    admin_type_label: str = ""
    admin_type_codespace: str = ""
    scale_code: str = ""
    scale_label: str = ""
    scale_codespace: str = ""
    duration_value: float | None = None
    duration_uom: str = ""
    duration_h: float | None = None
    area_type_code: str = ""
    area_type_label: str = ""
    area_type_codespace: str = ""


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
    disaster_risks: list[DisasterRiskRecord] = field(default_factory=list)
