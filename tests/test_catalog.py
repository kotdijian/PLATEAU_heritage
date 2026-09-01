from heritage_gml.catalog import cities_for_area

def test_prefecture_enumeration():
    payload = {"latest_citygml":[
        {"pref_code":"12","pref":"P","city_code":"12345","city":"A","feature_types":["bldg"],"year":"latest","url":"u"},
        {"pref_code":"12","pref":"P","city_code":"12346","city":"B","feature_types":["tran"],"year":"latest","url":"u"},
        {"pref_code":"14","pref":"Q","city_code":"14100","city":"C","feature_types":["bldg"],"year":"latest","url":"u"},
    ]}
    rows = cities_for_area(payload,"12")
    assert [r.city_code for r in rows] == ["12345"]
