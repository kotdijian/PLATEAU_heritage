from pathlib import Path
import sqlite3

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon, MultiPolygon
import pytest

from heritage_gml.gpkg_merge import discover_sources, merge_prefecture


def make_gpkg(path: Path, code: str, muni: str, x: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    records = gpd.GeoDataFrame([
        {"record_id": f"r-{code}", "municipality": muni, "municipality_code": code,
         "geometry": Point(x, 35.0)}
    ], crs="EPSG:4326")
    poly1 = Polygon([(x,35.0),(x+.001,35.0),(x+.001,35.001),(x,35.001),(x,35.0)])
    poly2 = Polygon([(x+.002,35.0),(x+.003,35.0),(x+.003,35.001),(x+.002,35.001),(x+.002,35.0)])
    bldg = gpd.GeoDataFrame([
        {"gml_id": f"b-{code}-1", "city_code": code, "geometry": poly1},
        {"gml_id": f"b-{code}-2", "city_code": code, "geometry": poly2},
    ], crs="EPSG:4326")
    complexes = gpd.GeoDataFrame([
        {"complex_id": f"{code}-HG00001", "complex_name": f"C-{code}",
         "member_building_count": 2, "geometry": MultiPolygon([poly1, poly2])}
    ], crs="EPSG:4326")
    records.to_file(path, layer="heritage_records", driver="GPKG", engine="pyogrio")
    bldg.to_file(path, layer="heritage_buildings_footprint", driver="GPKG", engine="pyogrio")
    complexes.to_file(path, layer="heritage_building_complexes", driver="GPKG", engine="pyogrio")
    with sqlite3.connect(path) as conn:
        pd.DataFrame([
            {"complex_id": f"{code}-HG00001", "building_gml_id": f"b-{code}-1"},
            {"complex_id": f"{code}-HG00001", "building_gml_id": f"b-{code}-2"},
        ]).to_sql("heritage_complex_members", conn, if_exists="replace", index=False)


def test_merge_two_municipalities(tmp_path):
    make_gpkg(tmp_path/"13101"/"13101_heritage.gpkg", "13101", "千代田区", 139.70)
    make_gpkg(tmp_path/"13102"/"13102_heritage.gpkg", "13102", "中央区", 139.71)
    result = merge_prefecture(tmp_path, "13")
    out = Path(result.output_path)
    assert out.exists()
    records = gpd.read_file(out, layer="heritage_records", engine="pyogrio")
    assert len(records) == 2
    assert set(records.municipality_code.astype(str)) == {"13101", "13102"}
    assert set(records.municipality_name) == {"千代田区", "中央区"}
    complexes = gpd.read_file(out, layer="heritage_building_complexes", engine="pyogrio")
    assert len(complexes) == 2
    assert all(g.geom_type == "MultiPolygon" for g in complexes.geometry)
    assert all(len(g.geoms) == 2 for g in complexes.geometry)
    with sqlite3.connect(out) as conn:
        members = pd.read_sql_query("SELECT * FROM heritage_complex_members", conn)
        assert len(members) == 4
        assert set(members.municipality_code.astype(str)) == {"13101", "13102"}
        kind = conn.execute(
            "SELECT data_type FROM gpkg_contents WHERE table_name='heritage_complex_members'"
        ).fetchone()[0]
        assert kind == "attributes"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_prefecture_filter(tmp_path):
    make_gpkg(tmp_path/"13101"/"13101_heritage.gpkg", "13101", "千代田区", 139.70)
    make_gpkg(tmp_path/"14100"/"14100_heritage.gpkg", "14100", "横浜市", 139.60)
    sources = discover_sources(tmp_path, "13")
    assert [c for c, _ in sources] == ["13101"]


def test_duplicate_code_is_error(tmp_path):
    make_gpkg(tmp_path/"a"/"13101_heritage.gpkg", "13101", "千代田区", 139.70)
    make_gpkg(tmp_path/"b"/"13101_heritage.gpkg", "13101", "千代田区", 139.70)
    with pytest.raises(RuntimeError):
        discover_sources(tmp_path, "13")


def test_requested_missing_code_is_error(tmp_path):
    make_gpkg(tmp_path/"13101"/"13101_heritage.gpkg", "13101", "千代田区", 139.70)
    with pytest.raises(FileNotFoundError):
        discover_sources(tmp_path, "13", codes=["13101", "13102"])
