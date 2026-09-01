from heritage_data_tools.normalizers.common import (
    normalize_type, entity_class, geometry_role, municipality_from_values, resolve
)


def test_type_normalization():
    assert normalize_type("住居建築") == "建造物"
    assert normalize_type("彫刻") == "美術工芸品"
    assert normalize_type("考古資料") == "考古資料"
    assert normalize_type("美術工芸品・考古資料") == "美術工芸品・考古資料"
    assert normalize_type("歴史資料") == "歴史資料"
    assert normalize_type("旧跡") == "旧跡"


def test_entity_class_matches_extractor_v05_semantics():
    assert entity_class("建造物") == "building_direct"
    assert entity_class("美術工芸品") == "movable"
    assert entity_class("考古資料") == "movable"
    assert entity_class("古文書") == "movable"
    assert entity_class("典籍") == "movable"
    assert entity_class("美術工芸品・考古資料") == "movable"
    assert entity_class("歴史資料") == "point"
    assert entity_class("史跡") == "point"


def test_geometry_role_movables_follow_regular_point_path():
    assert geometry_role("building_direct") == "building_candidate_point"
    assert geometry_role("movable") == "representative_point"
    assert geometry_role("point") == "representative_point"


def test_address_detail_alias():
    assert resolve(["名称", "住所", "方書"], "address_detail") == "方書"


def test_tokyo_municipality_inference():
    code, name = municipality_from_values("", "", "東京都文京区本郷1-1")
    assert code == "13105"
    assert name == "文京区"
