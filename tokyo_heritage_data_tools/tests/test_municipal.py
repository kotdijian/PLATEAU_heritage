from heritage_data_tools.normalizers.municipal import infer_designation_level, _keyword_config

def cfg():
    return _keyword_config(None)

def test_levels():
    assert infer_designation_level("○○市指定有形文化財", "○○市", "13299", cfg())[0] == "municipal"
    assert infer_designation_level("東京都指定文化財", "○○市", "13299", cfg())[0] == "prefectural"
    assert infer_designation_level("国登録有形文化財", "○○市", "13299", cfg())[0] == "national"
    assert infer_designation_level("有形文化財", "○○市", "13299", cfg())[0] == "ambiguous"

def test_configured_default(tmp_path):
    p = tmp_path / "cfg.yml"
    p.write_text("default_by_municipality:\n  '13299': municipal\n", encoding="utf-8")
    c = _keyword_config(str(p))
    level, reason = infer_designation_level("有形文化財", "○○市", "13299", c)
    assert level == "municipal"
    assert reason == "configured_default"
