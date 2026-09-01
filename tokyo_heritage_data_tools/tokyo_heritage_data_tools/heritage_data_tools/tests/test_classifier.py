from pathlib import Path
import pandas as pd

from heritage_data_tools.classifier import classify_frame, classify_csv, detect_scope


def test_tokyo_building_type_from_type_fallback():
    df = pd.DataFrame([{
        "名称": "浅草寺六角堂",
        "文化財分類": "都指定文化財",
        "種類": "建造物",
        "市区町村名": "台東区",
    }])
    out, summary = classify_frame(df, "prefectural_tokyo")
    r = out.iloc[0]
    assert r["designation_level_code"] == "prefectural"
    assert r["designation_status_code"] == "designated"
    assert r["heritage_type_major_code"] == "tangible"
    assert r["heritage_type_detail"] == "建造物"


def test_national_registration_overrides_default():
    df = pd.DataFrame([{
        "name": "X", "category": "登録有形文化財", "type": "建造物",
        "municipality_code": "13106", "designation": "national"
    }])
    out, _ = classify_frame(df, "national")
    r = out.iloc[0]
    assert r["designation_level_code"] == "national"
    assert r["designation_status_code"] == "registered"
    assert r["heritage_type_major_code"] == "tangible"


def test_classify_preserves_original_columns(tmp_path):
    src = tmp_path / "municipal.csv"
    dst = tmp_path / "municipal_classified.csv"
    pd.DataFrame([{
        "name":"A", "owner":"B", "municipality_code":"13102",
        "category":"区指定文化財", "type":"史跡", "designation":"municipal",
        "custom_source_column":"keep-me"
    }]).to_csv(src, index=False, encoding="utf-8-sig")
    classify_csv(src, dst, scope="municipal")
    got = pd.read_csv(dst, dtype=str, keep_default_na=False)
    assert got.loc[0, "custom_source_column"] == "keep-me"
    assert got.loc[0, "designation_level_code"] == "municipal"
    assert got.loc[0, "heritage_type_major_code"] == "monument"


def test_detect_scope_from_filename():
    df = pd.DataFrame([{"name":"A"}])
    assert detect_scope(df, "national.csv") == "national"
    assert detect_scope(df, "municipal.csv") == "municipal"
    assert detect_scope(df, "130001_cultural_property.csv") == "prefectural_tokyo"


def test_municipal_source_specific_category_overrides_legacy_level():
    df = pd.DataFrame([{
        "name":"石皿", "owner":"", "municipality_code":"13220",
        "category":"市重宝", "type":"美術工芸品", "designation":"national", "designation_level":"national"
    }])
    out, _ = classify_frame(df, "municipal")
    assert out.iloc[0]["designation_level_code"] == "municipal"


def test_unknown_municipal_level_resolved_only_by_exact_reference():
    from heritage_data_tools.classifier import resolve_municipal_cross_source
    m = pd.DataFrame([{
        "name":"玉川上水", "municipality_code":"13210",
        "designation_level_code":"unknown", "designation_level_ja":"不明",
        "designation_status_code":"unknown", "designation_status_ja":"不明",
        "heritage_type_major_code":"monument", "heritage_type_major_ja":"記念物",
        "heritage_type_detail":"史跡", "classification_confidence":"low"
    }])
    n = pd.DataFrame([{
        "name":"玉川上水", "municipality_code":"13210",
        "designation_status_code":"designated", "designation_status_ja":"指定",
        "heritage_type_major_code":"monument", "heritage_type_major_ja":"記念物",
        "heritage_type_detail":"史跡", "classification_confidence":"high"
    }])
    out, stats = resolve_municipal_cross_source(m, n, None)
    assert out.iloc[0]["designation_level_code"] == "national"
    assert stats["resolved_national_exact"] == 1
