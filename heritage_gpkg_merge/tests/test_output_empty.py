from shapely.geometry import Point, Polygon
import sqlite3
from heritage_gml.model import CulturalRecord, BuildingRecord
from heritage_gml.output import buildings_df, points_df, building_complexes_df, write_gpkg


def test_empty_selected_buildings_has_geometry_column():
    b = BuildingRecord(
        gml_id="b1", source_file="x.gml", city_code="12345", file_code="x",
        geometry=Polygon([(139,35),(139.001,35),(139.001,35.001),(139,35.001)])
    )
    gdf = buildings_df([b], {})
    assert gdf.empty
    assert gdf.geometry.name == "geometry"


def test_empty_points_has_geometry_column():
    gdf = points_df([])
    assert gdf.empty
    assert gdf.geometry.name == "geometry"


def test_zero_matches_still_writes_gpkg(tmp_path):
    r = CulturalRecord(
        source_file="x.csv", record_id="1", name="Sample",
        municipality_code="12345", complex_id="12345-HG00001",
        complex_name="Sample", geometry=Point(139,35), entity_class="point",
        spatial_match_status="point_unmatched"
    )
    b = BuildingRecord(
        gml_id="b1", source_file="x.gml", city_code="12345", file_code="x",
        geometry=Polygon([(140,36),(140.001,36),(140.001,36.001),(140,36.001)])
    )
    point_rows = [{
        "point_id":"record:1", "point_kind":"cultural_record", "record_id":"1",
        "record_ids":"1", "name":"Sample", "names":"Sample", "place_name":"",
        "address_detail":"", "address":"", "category":"", "type":"史跡",
        "entity_class":"point", "geometry_role":"representative_point",
        "source_location_role":"record_coordinate", "spatial_match_status":"point_unmatched",
        "complex_id":"12345-HG00001", "complex_name":"Sample",
        "complex_grouping_method":"singleton", "reason":"record_not_in_building", "item_count":1,
        "attached_items_json":"", "geometry":Point(139,35)
    }]
    out = tmp_path / "heritage.gpkg"
    write_gpkg(
        out, [r], [b], {}, point_rows, [],
        [{"complex_id":"12345-HG00001","complex_name":"Sample","grouping_method":"singleton",
          "record_count":1,"movable_record_count":0,"directly_matched_record_count":0,
          "complex_only_record_count":0,"shared_coordinate_record_count":0,
          "matched_building_count":0,"building_gml_ids":"","point_output_count":1,"status":"unresolved"}],
        [], [{"complex_id":"12345-HG00001","complex_name":"Sample","grouping_method":"singleton",
              "record_id":"1","name":"Sample","place_name":"","address_detail":"","type":"史跡",
              "entity_class":"point","source_location_role":"record_coordinate",
              "association_status":"unresolved","matched_building_ids":"","match_methods":""}],
        [{"entity_id":"1","entity_kind":"record","name":"Sample","place_name":"","address_detail":"",
          "type":"史跡","entity_class":"point","complex_id":"12345-HG00001","complex_name":"Sample",
          "source_location_role":"record_coordinate","spatial_match_status":"point_unmatched",
          "address":"","reason":"record_not_in_building"}]
    )
    assert out.exists()
    with sqlite3.connect(out) as conn:
        kind = conn.execute(
            "SELECT data_type FROM gpkg_contents WHERE table_name='heritage_complex_records'"
        ).fetchone()[0]
        assert kind == "attributes"


def test_building_complex_is_multipolygon_without_dissolve():
    b1 = BuildingRecord(
        gml_id="b1", source_file="x.gml", city_code="12345", file_code="x",
        geometry=Polygon([(139,35),(139.001,35),(139.001,35.001),(139,35.001)])
    )
    b2 = BuildingRecord(
        gml_id="b2", source_file="x.gml", city_code="12345", file_code="x",
        geometry=Polygon([(139.002,35),(139.003,35),(139.003,35.001),(139.002,35.001)])
    )
    rows = [{
        "complex_id":"12345-HG00001", "complex_name":"A寺", "grouping_method":"owner_address",
        "record_count":2, "movable_record_count":0, "directly_matched_record_count":2,
        "complex_only_record_count":0, "shared_coordinate_record_count":0,
        "matched_building_count":2, "building_gml_ids":"b1;b2", "point_output_count":0,
        "status":"matched_building_complex",
    }]
    members = [
        {"complex_id":"12345-HG00001", "building_gml_id":"b1"},
        {"complex_id":"12345-HG00001", "building_gml_id":"b2"},
    ]
    gdf = building_complexes_df([b1,b2], rows, members)
    assert len(gdf) == 1
    geom = gdf.iloc[0].geometry
    assert geom.geom_type == "MultiPolygon"
    assert len(geom.geoms) == 2
    assert geom.geoms[0].equals(b1.geometry)
    assert geom.geoms[1].equals(b2.geometry)
