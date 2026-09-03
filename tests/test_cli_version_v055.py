import subprocess
import sys
from pathlib import Path


def test_importing_cli_does_not_import_pipeline():
    code = (
        "import sys; import heritage_gml.cli; "
        "print('heritage_gml.pipeline' in sys.modules); "
        "print('pyproj' in sys.modules); print('geopandas' in sys.modules); print('shapely' in sys.modules)"
    )
    cp = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert cp.stdout.splitlines() == ["False", "False", "False", "False"]


def test_version_command_does_not_import_pipeline():
    code = (
        "import sys; from heritage_gml.cli import main; "
        "\ntry:\n main(['--version'])\nexcept SystemExit as e:\n "
        "print('EXIT', e.code); print('PIPELINE', 'heritage_gml.pipeline' in sys.modules); "
        "print('PYPROJ', 'pyproj' in sys.modules); print('GEOPANDAS', 'geopandas' in sys.modules); print('SHAPELY', 'shapely' in sys.modules)"
    )
    cp = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    lines = cp.stdout.splitlines()
    assert lines[0] == "heritage-gml 0.5.5"
    assert lines[1:] == ["EXIT 0", "PIPELINE False", "PYPROJ False", "GEOPANDAS False", "SHAPELY False"]


def test_source_versions_are_consistent():
    import heritage_gml
    import heritage_gml.heritage as heritage

    assert heritage_gml.__version__ == "0.5.5"
    doc = heritage.build_heritage_document("13101", "千代田区", [], [], {"selected": {}, "point_rows": [], "complex_rows": [], "complex_member_rows": [], "complex_record_rows": []})
    assert doc["version"] == "0.5.5"

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if pyproject.exists():
        import tomllib
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        assert data["project"]["version"] == "0.5.5"
