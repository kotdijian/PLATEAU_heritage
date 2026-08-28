from heritage_data_tools.normalizers.common import normalize_type, entity_class, municipality_from_values

def test_type_normalization():
    assert normalize_type("住居建築") == "建造物"
    assert normalize_type("彫刻") == "美術工芸品"
    assert normalize_type("考古資料") == "考古資料"
    assert normalize_type("旧跡") == "旧跡"

def test_entity_class():
    assert entity_class("建造物") == "building_direct"
    assert entity_class("美術工芸品") == "movable"
    assert entity_class("史跡") == "point"

def test_tokyo_municipality_inference():
    code, name = municipality_from_values("", "", "東京都文京区本郷1-1")
    assert code == "13105"
    assert name == "文京区"
