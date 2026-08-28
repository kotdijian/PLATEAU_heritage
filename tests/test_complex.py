from shapely.geometry import Point
from heritage_gml.model import CulturalRecord
from heritage_gml.cultural import assign_complexes

def rec(i, place="", owner="", address="", x=139.0, y=35.0):
    return CulturalRecord(
        source_file="x.csv", record_id=str(i), name=f"N{i}",
        place_name=place, owner=owner, address=address,
        municipality_code="12345", geometry=Point(x,y)
    )

def test_place_priority():
    rs = [rec(1,place="A寺",owner="O1",address="a1"),
          rec(2,place="A寺",owner="O2",address="a2")]
    assign_complexes(rs)
    assert rs[0].complex_id == rs[1].complex_id

def test_owner_address():
    rs = [rec(1,owner="O",address="a"),rec(2,owner="O",address="a")]
    assign_complexes(rs)
    assert rs[0].complex_id == rs[1].complex_id

def test_spatial_fallback_without_buffer():
    rs = [rec(1,x=139.1,y=35.1),rec(2,x=139.1,y=35.1),rec(3,x=139.1001,y=35.1001)]
    assign_complexes(rs)
    assert rs[0].complex_id == rs[1].complex_id
    assert rs[0].complex_id != rs[2].complex_id
