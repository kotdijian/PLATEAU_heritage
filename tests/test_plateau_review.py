from pathlib import Path
import sqlite3

import geopandas as gpd
from shapely.geometry import Point, Polygon

from plateau_review import distance_m, load_entities, optional_point


def test_optional_point_rejects_invalid_coordinate():
    assert optional_point({"longitude": 139.7, "latitude": 35.7}) == Point(139.7, 35.7)
    assert optional_point({"longitude": 999, "latitude": 35.7}) is None
    assert optional_point({"longitude": "", "latitude": ""}) is None


def test_distance_is_zero_when_point_is_covered():
    polygon = Polygon([(139, 35), (140, 35), (140, 36), (139, 36)])
    assert distance_m(Point(139.5, 35.5), polygon) == 0


def test_auto_profile_reads_museum_tables(tmp_path: Path):
    gpkg = tmp_path / "museum.sqlite"
    with sqlite3.connect(gpkg) as connection:
        connection.execute(
            "CREATE TABLE museum_facilities (museum_id TEXT, canonical_name TEXT, "
            "municipality_code TEXT, municipality_name TEXT, address TEXT, "
            "longitude REAL, latitude REAL, location_address_source_url TEXT, "
            "location_coordinate_level TEXT)"
        )
        connection.execute(
            "INSERT INTO museum_facilities VALUES "
            "('M1','Museum','13101','千代田区','東京都千代田区',139.7,35.7,'official','building')"
        )
        connection.execute(
            "CREATE TABLE museum_building_links (museum_id TEXT, building_gml_id TEXT, "
            "match_status TEXT, match_methods TEXT)"
        )
        connection.execute(
            "INSERT INTO museum_building_links VALUES ('M1','B1','confirmed','exact_name')"
        )
    rows = load_entities(gpkg, "auto")
    assert rows[0]["entity_id"] == "M1"
    assert rows[0]["machine_status"] == "confirmed"
    assert rows[0]["machine_building_ids"] == "B1"
