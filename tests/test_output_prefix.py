from heritage_gml.pipeline import _prefixed_name


def test_output_filename_gets_municipality_prefix():
    assert _prefixed_name("13106", "heritage.gpkg") == "13106_heritage.gpkg"
    assert _prefixed_name("13106", "heritage_buildings.gml") == "13106_heritage_buildings.gml"


def test_output_filename_prefix_is_idempotent():
    assert _prefixed_name("13106", "13106_heritage.gpkg") == "13106_heritage.gpkg"
