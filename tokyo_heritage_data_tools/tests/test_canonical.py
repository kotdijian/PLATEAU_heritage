from heritage_data_tools.normalizers.common import canonical_frame

def test_canonical_columns_are_gml_compatible():
    df = canonical_frame([{"name":"A","municipality_code":"13105","type":"建造物"}])
    for col in [
        "name","place_name","owner","address","municipality","municipality_code",
        "category","type","designation","designation_date","latitude","longitude"
    ]:
        assert col in df.columns
