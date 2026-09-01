from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import yaml


DEFAULTS = {
    "cultural": {
        "recursive": False,
        "input_crs": "EPSG:4326",
        "columns": {},
        "file_overrides": {},
        # Explicit semantic type mapping keeps classification tidy/reviewable.
        # v0.5: 'movable' is a semantic class only and follows the same
        # per-record spatial matching/output path as ordinary point records.
        # Values not listed here become 'point'.
        "type_class_map": {
            "建造物": "building_direct",
            "美術工芸品": "movable",
            "考古資料": "movable",
            "古文書": "movable",
            "典籍": "movable",
            "美術工芸品・考古資料": "movable",
        },
        "default_entity_class": "point",
        "geometry_columns": ["geometry", "geom", "wkt", "WKT", "範囲", "ポリゴン"],
    },
    "plateau": {
        "api_base": "https://api.plateauview.mlit.go.jp",
        "timeout_s": 120,
        "connect_timeout_s": 30,
        "read_timeout_s": 180,
        "download_retries": 3,
        "retry_backoff_s": 2.0,
        "cache_dir": ".cache/plateau",
        "local_dir": None,
        "catalog_file": None,
        "use_geocoding_condition_for_unlocated": False,
    },
    "matching": {
        # Every non-movable record is tested by exact point-in-footprint.
        # No buffer, nearest-neighbour search, or inferred area is used.
        "point_in_building": True,
        # Building-direct records may additionally use exact normalized
        # semantic keys.  These do not apply to ordinary point records.
        "building_direct_exact_name": True,
        "building_direct_exact_address": True,
        # A coordinate repeated by multiple records inside the same Complex is
        # treated as a shared complex/site observation, not silently as an
        # object-specific Building position. Set true only if the source is known
        # to provide object-specific coordinates despite exact duplication.
        "match_shared_complex_coordinates": False,
    },
    "output": {
        "dir": "output",
        "subset_gml_name": "heritage_buildings.gml",
        "heritage_json_name": "heritage_entities.json",
        "heritage_xml_name": "heritage_entities.xml",
        "gpkg_name": "heritage.gpkg",
                "embed_generic_attributes": True,
    },
    "runtime": {
        # For 2-digit prefecture batch runs, one municipality failure should
        # not abort the remaining municipalities.  Five-digit runs still fail
        # loudly unless explicitly handled by the caller.
        "continue_on_city_error_in_batch": True,
    },
}


def _merge(a: dict, b: dict) -> dict:
    out = deepcopy(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | None, execution_dir: str | Path) -> dict:
    cfg = deepcopy(DEFAULTS)
    if path:
        p = Path(path).expanduser().resolve()
        with p.open("r", encoding="utf-8") as f:
            cfg = _merge(cfg, yaml.safe_load(f) or {})
        base = p.parent
    else:
        base = Path(execution_dir).resolve()

    for section, key in [
        ("plateau", "cache_dir"),
        ("plateau", "local_dir"),
        ("plateau", "catalog_file"),
        ("output", "dir"),
    ]:
        v = cfg[section].get(key)
        if v and not Path(v).is_absolute():
            cfg[section][key] = str((base / v).resolve())
    return cfg
