from heritage_data_tools.util import list_of_dicts

def test_nested_json():
    obj = {"meta":{"count":2},"result":{"records":[{"名称":"A"},{"名称":"B"}]}}
    rows = list_of_dicts(obj)
    assert len(rows) == 2
    assert rows[0]["名称"] == "A"
