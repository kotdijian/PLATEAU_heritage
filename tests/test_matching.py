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
        complex_name="Sample", geometry=Point(x,y), address=address,
        entity_class=cls, movable=(cls == "movable")
    )


def cfg():
    return {
        "point_in_building": True,
        "building_direct_exact_name": True,
        "building_direct_exact_address": True,
    }


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
    assert result["point_rows"][0]["reason"] == "point_not_in_building"


def test_building_direct_exact_address_can_match():
    r = rec(cls="building_direct", x=138.0, y=34.0, address="X1-2")
    b = building()
    b.address = "X1丁目2番"
    result = match_city([r], [b], cfg())
    assert "b1" in result["selected"]
    assert "exact_address" in result["links"][0]["match_methods"]


def test_movable_same_cultural_address_attaches_to_matched_building():
    anchor = rec(rid="1", cls="point", address="X1-2")
    movable1 = rec(rid="2", cls="movable", x=138.0, y=34.0, address="X1丁目2番")
    movable1.name = "Item A"
    movable2 = rec(rid="3", cls="movable", x=138.0, y=34.0, address="X1-2")
    movable2.name = "Item B"
    result = match_city([anchor, movable1, movable2], [building()], cfg())
    assert len(result["movable_group_rows"]) == 1
    group = result["movable_group_rows"][0]
    assert group["item_count"] == 2
    assert group["linked_building_ids"] == "b1"
    assert group["match_methods"] == "same_cultural_address"
    assert not any(p["point_kind"] == "movable_group" for p in result["point_rows"])
    assert len(result["selected"]["b1"]["movable_groups"][0]["items"]) == 2


def test_unmatched_movable_group_becomes_one_point_with_list():
    m1 = rec(rid="2", cls="movable", x=138.0, y=34.0, address="Y1-1")
    m2 = rec(rid="3", cls="movable", x=138.0, y=34.0, address="Y1丁目1番")
    result = match_city([m1, m2], [building()], cfg())
    points = [p for p in result["point_rows"] if p["point_kind"] == "movable_group"]
    assert len(points) == 1
    assert points[0]["item_count"] == 2
    assert '"record_id":"2"' in points[0]["attached_items_json"]


def test_complex_members_preserve_each_matched_building():
    r1 = rec(rid="1", cls="point", x=139.0005, y=35.0005)
    r2 = rec(rid="2", cls="point", x=139.0025, y=35.0005)
    r1.complex_id = r2.complex_id = "12345-HG00001"
    r1.complex_name = r2.complex_name = "A寺"
    b1 = building("b1", 139.0, 35.0)
    b2 = building("b2", 139.002, 35.0)
    result = match_city([r1, r2], [b1, b2], cfg())
    members = result["complex_member_rows"]
    assert {m["building_gml_id"] for m in members} == {"b1", "b2"}
    summary = result["complex_rows"][0]
    assert summary["matched_building_count"] == 2
    assert summary["status"] == "matched_building_complex"
