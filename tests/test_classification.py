from heritage_gml.cultural import classify_entity
from heritage_gml.config import DEFAULTS
from heritage_gml.util import compact_address


def test_default_type_mapping():
    cfg = DEFAULTS["cultural"]
    assert classify_entity("建造物", cfg) == "building_direct"
    assert classify_entity("美術工芸品", cfg) == "movable"
    assert classify_entity("考古資料", cfg) == "movable"
    assert classify_entity("史跡", cfg) == "point"
    assert classify_entity("旧跡", cfg) == "point"
    assert classify_entity("天然記念物", cfg) == "point"
    assert classify_entity("名勝", cfg) == "point"


def test_address_hyphen_is_preserved():
    assert compact_address("X1-2") == compact_address("X1丁目2番")
    assert compact_address("X1-2") != compact_address("X12")
