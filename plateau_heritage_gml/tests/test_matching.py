from shapely.geometry import Point, Polygon
from heritage_gml.model import CulturalRecord, BuildingRecord
from heritage_gml.matching import match_city


def building(gid="b1", x0=139.0, y0=35.0):
    return BuildingRecord(
        gml_id=gid, source_file="x.gml", city_code="12345", file_code="x",
        geometry=Polygon([(x0,y0),(x0+.001,y0),(x0+.001,y0+.001),(x0,y0+.001)])
    )


def rec(rid="1", cls="point", x=139.0005, y=35.0005, address="A-1"):
    return CulturalRecord(
        source_file="x.csv", record_id=rid, name=f"N{rid}",
        municipality_code="12345", complex_id=f"12345-HG{int(rid):05d}" if rid.isdigit() else "12345-HG00001",
        complex_name="Sample", complex_record_count=1,
        geometry=Point(x,y), address=address,
        entity_class=cls, movable=(cls == "movable")
    )


def cfg(**kwargs):
    base = {
        "point_in_building": True,
        "building_direct_exact_name": True,
        "building_direct_exact_address": True,
        "match_shared_complex_coordinates": False,
    }
    base.update(kwargs)
    return base


def test_point_inside_building_matches_exactly():
    r = rec(cls="point")
    result = match_city([r], [building()], cfg())
    assert "b1" in result["selected"]
    assert result["links"][0]["match_methods"] == "point_in_building"
    assert result["point_rows"] == []
    assert r.matched_building_ids == ["b1"]


def test_point_outside_is_output_point_no_buffer():
    r = rec(cls="point", x=139.001001, y=35.0005)
    result = match_city([r], [building()], cfg())
    assert result["selected"] == {}
    assert len(result["point_rows"]) == 1
    assert result["point_rows"][0]["reason"] == "record_not_in_building"


def test_building_direct_exact_address_can_match():
    r = rec(cls="building_direct", x=138.0, y=34.0, address="X1-2")
    b = building()
    b.address = "X1丁目2番"
    result = match_city([r], [b], cfg())
    assert "b1" in result["selected"]
    assert "exact_address" in result["links"][0]["match_methods"]


def test_movable_uses_same_individual_matching_path():
    m = rec(rid="2", cls="movable", x=139.0005, y=35.0005, address="X1-2")
    m.name = "Item A"
    result = match_city([m], [building()], cfg())
    assert m.matched_building_ids == ["b1"]
    assert result["links"][0]["name"] == "Item A"
    assert result["links"][0]["entity_class"] == "movable"
    assert result["selected"]["b1"]["record_names"] == ["Item A"]


def test_unmatched_movable_is_ordinary_cultural_record_point():
    m = rec(rid="2", cls="movable", x=138.0, y=34.0, address="Y1-1")
    m.name = "Movable Item"
    result = match_city([m], [building()], cfg())
    assert len(result["point_rows"]) == 1
    p = result["point_rows"][0]
    assert p["point_kind"] == "cultural_record"
    assert p["name"] == "Movable Item"
    assert p["names"] == "Movable Item"
    assert p["entity_class"] == "movable"


def test_shared_complex_coordinate_is_not_silently_used_for_building_match():
    r1 = rec(rid="1", cls="building_direct", x=139.0005, y=35.0005)
    r2 = rec(rid="2", cls="movable", x=139.0005, y=35.0005)
    for r in (r1, r2):
        r.complex_id = "12345-HG00001"
        r.complex_name = "A寺"
        r.complex_record_count = 2
        r.complex_grouping_method = "owner_address"
        r.source_location_role = "shared_complex_coordinate"
    result = match_city([r1, r2], [building()], cfg())
    assert result["selected"] == {}
    assert result["point_rows"] == []  # represented semantically by the Complex
    assert {r.spatial_match_status for r in (r1, r2)} == {"complex_only"}
    summary = result["complex_rows"][0]
    assert summary["status"] == "complex_only"
    assert summary["record_count"] == 2
    assert summary["matched_building_count"] == 0


def test_complex_members_preserve_each_matched_building():
    r1 = rec(rid="1", cls="point", x=139.0005, y=35.0005)
    r2 = rec(rid="2", cls="point", x=139.0025, y=35.0005)
    r1.complex_id = r2.complex_id = "12345-HG00001"
    r1.complex_name = r2.complex_name = "A寺"
    r1.complex_record_count = r2.complex_record_count = 2
    r1.complex_grouping_method = r2.complex_grouping_method = "owner_address"
    b1 = building("b1", 139.0, 35.0)
    b2 = building("b2", 139.002, 35.0)
    result = match_city([r1, r2], [b1, b2], cfg())
    members = result["complex_member_rows"]
    assert {m["building_gml_id"] for m in members} == {"b1", "b2"}
    summary = result["complex_rows"][0]
    assert summary["matched_building_count"] == 2
    assert summary["status"] == "matched_building_complex"
    assert len(result["complex_record_rows"]) == 2
