from pathlib import Path

import pytest

from heritage_gml.citygml import CityGMLReadError, scan_buildings
from heritage_gml.model import CulturalRecord, PlateauCity, PlateauFile
from heritage_gml.plateau import purge_city_cache
import heritage_gml.pipeline as pipeline
import heritage_gml.citygml as citygml


def test_purge_city_cache_removes_only_target_city(tmp_path):
    cache = tmp_path / "cache"
    a = cache / "13101"
    b = cache / "13102"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "a.gml").write_text("x")
    (b / "b.gml").write_text("y")

    removed = purge_city_cache(cache, "13101")
    assert removed == a.resolve()
    assert not a.exists()
    assert (b / "b.gml").exists()


def test_scan_buildings_wraps_timeout_with_path(tmp_path, monkeypatch):
    p = tmp_path / "13101_test_bldg.gml"
    p.write_text("<x/>")
    pf = PlateauFile("13101", "", "x", "", local_path=str(p))

    def boom(*args, **kwargs):
        raise TimeoutError(60, "Operation timed out")

    monkeypatch.setattr(citygml.etree, "iterparse", boom)
    with pytest.raises(CityGMLReadError) as ex:
        scan_buildings([pf])
    assert ex.value.path == str(p)
    assert ex.value.stage == "scan"
    assert isinstance(ex.value.original, TimeoutError)


def test_local_city_resolution_is_offline(tmp_path, monkeypatch):
    d = tmp_path / "13101"
    d.mkdir()
    (d / "53394509_bldg_test.gml").write_text("<x/>")

    def network_must_not_run(*args, **kwargs):
        raise AssertionError("catalog API was called in local mode")

    monkeypatch.setattr(pipeline, "fetch_plateau_catalog", network_must_not_run)
    cfg = {"plateau": {"local_dir": str(tmp_path), "api_base": "x", "timeout_s": 1}}

    rows = pipeline._cities("13101", cfg, "local", str(tmp_path))
    assert [x.city_code for x in rows] == ["13101"]


def test_api_read_failure_purges_city_cache_and_reacquires_all(tmp_path, monkeypatch):
    out = tmp_path / "out"
    cache = tmp_path / "cache"
    data = tmp_path / "data"
    data.mkdir()
    dummy = data / "dummy.csv"
    dummy.write_text("name\nX\n", encoding="utf-8")

    city = PlateauCity("13", "東京都", "13101", "千代田区", "2025", ["bldg"], "")
    rec = CulturalRecord(source_file=str(dummy), record_id="r1", name="X", municipality_code="13101")
    pf = PlateauFile("13101", "千代田区", "mesh", "https://example.test/x.gml")

    monkeypatch.setattr(pipeline, "_cities", lambda *a, **k: [city])
    monkeypatch.setattr(pipeline, "discover_files", lambda *a, **k: [dummy])
    monkeypatch.setattr(pipeline, "load_records_for_city", lambda *a, **k: ([rec], []))
    monkeypatch.setattr(pipeline, "assign_complexes", lambda x: x)
    monkeypatch.setattr(pipeline, "resolve_remote_files", lambda *a, **k: ([pf], []))

    downloads = {"n": 0}

    def fake_download(files, p_cfg, progress=True):
        downloads["n"] += 1
        city_dir = Path(p_cfg["cache_dir"]) / "13101"
        city_dir.mkdir(parents=True, exist_ok=True)
        f = city_dir / "mesh_bldg.gml"
        f.write_text("<x/>")
        files[0].local_path = str(f)
        return files, []

    monkeypatch.setattr(pipeline, "_download_remote_set", fake_download)

    scans = {"n": 0}

    def fake_scan(files, progress=False):
        scans["n"] += 1
        if scans["n"] == 1:
            raise CityGMLReadError(files[0].local_path, "scan", TimeoutError(60, "Operation timed out"))
        return []

    monkeypatch.setattr(pipeline, "scan_buildings", fake_scan)
    monkeypatch.setattr(pipeline, "match_city", lambda *a, **k: {
        "selected": {}, "links": [], "complex_rows": [], "complex_member_rows": [],
        "complex_record_rows": [], "point_rows": [], "unresolved_rows": [],
    })
    monkeypatch.setattr(pipeline, "build_heritage_document", lambda *a, **k: {})
    monkeypatch.setattr(pipeline, "write_json", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "write_xml", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "write_gpkg", lambda *a, **k: None)

    cfg = {
        "cultural": {"recursive": False},
        "plateau": {
            "api_base": "https://example.test", "timeout_s": 1,
            "cache_dir": str(cache), "cache_recovery_retries": 1,
        },
        "matching": {},
        "output": {
            "dir": str(out), "subset_gml_name": "heritage_buildings.gml",
            "heritage_json_name": "heritage_entities.json",
            "heritage_xml_name": "heritage_entities.xml", "gpkg_name": "heritage.gpkg",
            "embed_generic_attributes": True,
        },
    }

    result = pipeline.run_area("13101", data, cfg, plateau_source="api")
    assert downloads["n"] == 2
    assert scans["n"] == 2
    assert result[0]["cache_recovery_count"] == 1
    assert result[0]["cache_recovery_events"][0]["action"] == "purge_municipality_cache_and_redownload_all"
