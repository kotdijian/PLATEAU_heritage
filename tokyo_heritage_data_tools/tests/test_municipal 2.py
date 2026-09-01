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


def test_municipal_normalization_preserves_address_detail(tmp_path):
    from heritage_data_tools.normalizers.municipal import normalize
    src = tmp_path / "raw" / "13106"
    src.mkdir(parents=True)
    (src / "data.csv").write_text(
        "名称,文化財分類,種類,住所,方書,市区町村名,指定等\n"
        "西仏板碑,市指定文化財,歴史資料,東京都台東区浅草2-3-1,浅草寺境内,台東区,台東区指定\n",
        encoding="utf-8-sig",
    )
    (src / "source.json").write_text(
        '{"manifest_entry":{"organization":"台東区","dataset_title":"文化財一覧"}}',
        encoding="utf-8",
    )
    out = tmp_path / "tidy"
    municipal, excluded, review = normalize(tmp_path / "raw", out)
    assert len(municipal) == 1
    row = municipal.iloc[0]
    assert row["address_detail"] == "浅草寺境内"
    assert row["entity_class"] == "point"
    assert row["geometry_role"] == "representative_point"
