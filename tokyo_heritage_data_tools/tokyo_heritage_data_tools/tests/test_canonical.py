from heritage_data_tools.normalizers.common import canonical_frame


def test_canonical_columns_are_extractor_v05_compatible():
    df = canonical_frame([{"name":"A","municipality_code":"13105","type":"建造物"}])
    for col in [
        "name","place_name","address_detail","owner","address","municipality","municipality_code",
        "category","type","designation","designation_date","latitude","longitude",
        "entity_class","geometry_role"
    ]:
        assert col in df.columns
