import pytest

from heritage_data_tools.util import validate_pref_code, validate_municipal_code


def test_validate_pref_code_accepts_two_digits():
    assert validate_pref_code("13") == "13"
    assert validate_pref_code("01") == "01"


def test_validate_pref_code_rejects_invalid_values():
    for value in ("1", "130", "00", "48", "ab"):
        with pytest.raises(ValueError):
            validate_pref_code(value)


def test_validate_municipal_code_accepts_five_digits():
    assert validate_municipal_code("13106") == "13106"
    assert validate_municipal_code("01101") == "01101"


def test_validate_municipal_code_rejects_invalid_values():
    for value in ("1310", "131060", "00101", "48101", "abcde"):
        with pytest.raises(ValueError):
            validate_municipal_code(value)


def test_municipality_from_values_strips_non_digits():
    from heritage_data_tools.normalizers.common import municipality_from_values
    code, _ = municipality_from_values("13106-7", "台東区", "東京都台東区浅草2-3-1")
    assert code == "13106"
