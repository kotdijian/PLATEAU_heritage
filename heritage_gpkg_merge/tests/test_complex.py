from shapely.geometry import Point
from heritage_gml.model import CulturalRecord
from heritage_gml.cultural import assign_complexes


def rec(i, place="", detail="", owner="", address="", x=139.0, y=35.0):
    return CulturalRecord(
        source_file="x.csv", record_id=str(i), name=f"N{i}",
        place_name=place, address_detail=detail, owner=owner, address=address,
        municipality_code="12345", geometry=Point(x,y)
    )


def test_place_priority():
    rs = [rec(1,place="A寺",owner="O1",address="a1"),
          rec(2,place="A寺",owner="O2",address="a2")]
    assign_complexes(rs)
    assert rs[0].complex_id == rs[1].complex_id
    assert rs[0].complex_grouping_method == "place_name"


def test_owner_address():
    rs = [rec(1,owner="O",address="a"),rec(2,owner="O",address="a")]
    assign_complexes(rs)
    assert rs[0].complex_id == rs[1].complex_id
    assert rs[0].complex_grouping_method == "owner_address"


def test_address_detail_is_preserved_and_can_name_complex():
    rs = [
        rec(1, detail="浅草寺境内", owner="宗教法人 浅草寺", address="東京都台東区浅草2-3-1"),
        rec(2, detail="浅草寺内", owner="宗教法人 浅草寺", address="東京都台東区浅草2-3-1"),
    ]
    assign_complexes(rs)
    assert rs[0].complex_id == rs[1].complex_id
    assert rs[0].complex_name == "浅草寺"
    assert rs[0].complex_record_count == 2
    assert rs[0].source_location_role == "shared_complex_coordinate"



def test_address_detail_suffix_normalization_preserves_facility_name():
    cases = [
        ("浅草寺境内", "浅草寺"),
        ("浅草寺内", "浅草寺"),
        ("小石川後楽園内", "小石川後楽園"),
        ("日枝神社内", "日枝神社"),
        ("東京国立博物館内", "東京国立博物館"),
        ("聖徳寺墓地内", "聖徳寺"),
        ("清泉女子大学内", "清泉女子大学"),
    ]
    for n, (detail, expected) in enumerate(cases, 1):
        r = rec(n, detail=detail, owner="", address="")
        assign_complexes([r])
        assert r.complex_name == expected


def test_spatial_fallback_without_buffer():
    rs = [rec(1,x=139.1,y=35.1),rec(2,x=139.1,y=35.1),rec(3,x=139.1001,y=35.1001)]
    assign_complexes(rs)
    assert rs[0].complex_id == rs[1].complex_id
    assert rs[0].complex_id != rs[2].complex_id
