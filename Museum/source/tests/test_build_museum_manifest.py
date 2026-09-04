import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_museum_manifest.py"
SPEC = importlib.util.spec_from_file_location("museum_manifest", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ManifestLogicTest(unittest.TestCase):
    def test_normalize_name(self):
        self.assertEqual(MODULE.normalize_name("◎ 東京都　葛西・臨海水族園"), "東京都葛西臨海水族園")

    def test_reconcile_requires_name_and_municipality(self):
        core = {
            "record_id": "core", "source_id": "core", "source_role": "core",
            "facility_name_raw": "A館", "facility_name_normalized": "a館",
            "municipality_code": "13101", "municipality_name": "千代田区", "notes": "",
        }
        same = {**core, "record_id": "same", "source_id": "supp", "source_role": "supplement"}
        other = {**same, "record_id": "other", "municipality_code": "13102", "municipality_name": "中央区"}
        statuses = [r["match_status"] for r in MODULE.reconcile([core, same, other])]
        self.assertEqual(statuses, ["core_unique", "duplicate_core", "supplement_unique"])


if __name__ == "__main__":
    unittest.main()

