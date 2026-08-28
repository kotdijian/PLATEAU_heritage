from pathlib import Path
import requests

from heritage_gml.model import PlateauFile
import heritage_gml.plateau as plateau


class FakeResponse:
    def __init__(self, chunks=(b"<gml/>",)):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=1024 * 1024):
        yield from self._chunks


def test_download_retries_then_succeeds(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ReadTimeout("temporary")
        return FakeResponse()

    monkeypatch.setattr(plateau.requests, "get", fake_get)
    monkeypatch.setattr(plateau.time, "sleep", lambda _: None)

    pf = PlateauFile("12345", "X", "mesh", "https://example.test/x.gml")
    files, issues = plateau.download_files(
        [pf], tmp_path, connect_timeout_s=1, read_timeout_s=1,
        retries=3, backoff_s=0,
    )
    assert calls["n"] == 2
    assert issues == []
    assert files[0].local_path
    assert Path(files[0].local_path).exists()


def test_download_terminal_failure_is_reported_not_raised(tmp_path, monkeypatch):
    def fake_get(*args, **kwargs):
        raise requests.ReadTimeout("still unavailable")

    monkeypatch.setattr(plateau.requests, "get", fake_get)
    monkeypatch.setattr(plateau.time, "sleep", lambda _: None)

    pf = PlateauFile("12345", "X", "mesh", "https://example.test/x.gml")
    files, issues = plateau.download_files(
        [pf], tmp_path, connect_timeout_s=1, read_timeout_s=1,
        retries=2, backoff_s=0,
    )
    assert files[0].local_path is None
    assert len(issues) == 1
    assert "plateau_download_error" in issues[0]["reason"]
    assert not list(tmp_path.rglob("*.part"))
