import pytest
from heritage_gml.util import validate_area_code

def test_valid():
    assert validate_area_code("12") == ("prefecture","12")
    assert validate_area_code("12345") == ("municipality","12345")
    assert validate_area_code("01") == ("prefecture","01")

@pytest.mark.parametrize("code", ["1","120","123456","00","48","abc"])
def test_invalid(code):
    with pytest.raises(ValueError):
        validate_area_code(code)
