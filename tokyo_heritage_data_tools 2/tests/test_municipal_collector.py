import json
from pathlib import Path

import pytest
import requests

from heritage_data_tools.collectors import municipal as m


class FakeResponse:
    def __init__(self, *, status=200, content=b"", json_obj=None):
        self.status_code = status
        self.content = content
        self._json_obj = json_obj

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} Client Error")
            err.response = self
            raise err

    def json(self):
        if self._json_obj is None:
            return json.loads(self.content.decode("utf-8"))
        return self._json_obj


class FakeSession:
    def __init__(self, gets=None, posts=None):
        self.gets = list(gets or [])
        self.posts = list(posts or [])
        self.post_calls = []
        self.get_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.gets.pop(0)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.posts.pop(0)


def write_manifest(path: Path, entry: dict):
    import yaml
    path.write_text(yaml.safe_dump({"sources": {"13115": entry}}, allow_unicode=True), encoding="utf-8")


def test_tokyo_api_post_uses_json_content_type_and_paginates():
    fs = FakeSession(posts=[
        FakeResponse(json_obj={"total": 3, "subtotal": 2, "limit": 2, "offset": None, "hits": [{"名称": "A"}, {"名称": "B"}]}),
        FakeResponse(json_obj={"total": 3, "subtotal": 1, "limit": 2, "offset": 2, "hits": [{"名称": "C"}]}),
    ])
    payload, meta = m._post_api_json(fs, "https://example.test/json", (1, 1), 2)
    obj = json.loads(payload)
    assert [x["名称"] for x in obj["hits"]] == ["A", "B", "C"]
    assert meta["records_collected"] == 3
    assert meta["pages"] == 2
    assert fs.post_calls[0][1]["json"] == {}
    assert fs.post_calls[0][1]["headers"]["Accept"] == "application/json"
    assert fs.post_calls[0][1]["headers"]["Content-Type"] == "application/json"
    assert fs.post_calls[0][1]["params"] == {"limit": 2}
    assert fs.post_calls[1][1]["params"] == {"limit": 2, "offset": 2}


def test_csv_failure_falls_back_to_api(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.yml"
    write_manifest(manifest, {
        "organization": "杉並区",
        "dataset_title": "文化財一覧",
        "source_csv_url": "https://example.test/stale.csv",
        "json_endpoint": "https://example.test/json",
        "method": "POST",
    })
    fs = FakeSession(
        gets=[FakeResponse(status=404)],
        posts=[FakeResponse(json_obj={"total": 1, "subtotal": 1, "hits": [{"名称": "A"}]})],
    )
    monkeypatch.setattr(m, "session", lambda retries=3: fs)
    out = tmp_path / "raw"
    results = m.collect(manifest, out, api_page_size=1000)
    assert results[0]["status"] == "downloaded"
    assert results[0]["source_type"] == "json"
    assert results[0]["fallback_from"] == ["csv"]
    assert (out / "13115" / "data.json").exists()
    assert not (out / "13115" / "data.csv").exists()
    source_meta = json.loads((out / "13115" / "source.json").read_text(encoding="utf-8"))
    assert source_meta["attempts"][0]["source_type"] == "csv"


def test_existing_csv_is_cached_without_network(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.yml"
    write_manifest(manifest, {
        "organization": "杉並区",
        "dataset_title": "文化財一覧",
        "source_csv_url": "https://example.test/file.csv",
        "json_endpoint": "https://example.test/json",
        "method": "POST",
    })
    d = tmp_path / "raw" / "13115"
    d.mkdir(parents=True)
    (d / "data.csv").write_bytes(b"x,y\n1,2\n")
    monkeypatch.setattr(m, "session", lambda retries=3: FakeSession())
    results = m.collect(manifest, tmp_path / "raw")
    assert results[0]["status"] == "cached"
    assert results[0]["source_type"] == "csv"
