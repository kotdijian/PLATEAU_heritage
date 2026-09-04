from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from Museum.build_museum_hazard_gpkg import (
    LINK_FIELDS,
    choose_facility_type,
    default_output_path,
    load_museum_data,
    match_buildings,
    museum_address_key,
    museum_query_address,
    museum_site_address_key,
    write_attribute_table,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MUSEUM_DATA = REPOSITORY_ROOT / "Museum" / "source" / "data"


def facility(museum_id: str, name: str, address: str = ""):
    return {
        "museum_id": museum_id,
        "canonical_name": name,
        "municipality_code": "13101",
        "address": address,
    }


def building(gml_id: str, name: str = "", address: str = "", detail: str = ""):
    return SimpleNamespace(
        gml_id=gml_id,
        building_id=f"bid-{gml_id}",
        city_code="13101",
        name=name,
        address=address,
        usage="422",
        detailed_usage=detail,
        source_file="synthetic_bldg.gml",
    )


class ManifestTests(unittest.TestCase):
    def test_only_accepted_reconciliation_rows_become_facilities(self):
        facilities, source_records = load_museum_data(MUSEUM_DATA)
        self.assertEqual(len(facilities), 245)
        self.assertEqual(len(source_records), 295)
        self.assertTrue(all(row["scope_status"] == "candidate" for row in facilities))

    def test_specific_facility_type_wins_over_generic_museum(self):
        rows = [{"facility_type": "museum"}, {"facility_type": "aquarium"}]
        self.assertEqual(choose_facility_type(rows), "aquarium")

    def test_targeted_query_prefers_address_and_falls_back_to_name(self):
        self.assertEqual(
            museum_query_address({
                "address": "東京都千代田区丸の内1-1",
                "municipality_name": "千代田区",
                "canonical_name": "テスト博物館",
            }),
            "東京都千代田区丸の内1-1",
        )
        self.assertEqual(
            museum_query_address({
                "address": "",
                "municipality_name": "千代田区",
                "canonical_name": "テスト博物館",
            }),
            "千代田区 テスト博物館",
        )

    def test_address_keys_absorb_prefecture_and_keep_site_suffix_separate(self):
        self.assertEqual(
            museum_address_key("東京都文京区後楽1丁目3番61号"),
            "文京区後楽1-3-61",
        )
        self.assertEqual(
            museum_address_key("文京区後楽1丁目3番61号 東京ドームシティ6F"),
            "文京区後楽1-3-61東京ドームシティ6f",
        )
        self.assertEqual(
            museum_site_address_key("文京区後楽1丁目3番61号 東京ドームシティ6F"),
            "文京区後楽1-3-61",
        )
        self.assertEqual(museum_address_key("立て替えのため休館中"), "")


class MatchingTests(unittest.TestCase):
    def test_exact_name_and_municipality_confirms(self):
        links, states = match_buildings(
            [building("b1", name="国立テスト博物館")],
            [facility("m1", "国立テスト博物館")],
        )
        self.assertEqual(links[0]["match_status"], "confirmed")
        self.assertEqual(states["b1"]["status"], "confirmed")

    def test_unique_exact_address_and_strong_usage_confirms(self):
        links, states = match_buildings(
            [building("b2", address="東京都千代田区丸の内1-1", detail="422302")],
            [facility("m2", "テスト館", "東京都千代田区丸の内1丁目1番")],
        )
        self.assertEqual(links[0]["match_status"], "confirmed")
        self.assertEqual(states["b2"]["status"], "confirmed")

    def test_address_alone_requires_review(self):
        links, states = match_buildings(
            [building("b3", address="東京都千代田区丸の内1-1")],
            [facility("m3", "テスト館", "東京都千代田区丸の内1丁目1番")],
        )
        self.assertEqual(links[0]["match_status"], "needs_review")
        self.assertEqual(states["b3"]["status"], "needs_review")

    def test_site_address_with_building_suffix_requires_review(self):
        links, states = match_buildings(
            [building("b5", address="東京都文京区後楽1丁目3番61号")],
            [facility("m5", "テスト館", "文京区後楽1丁目3番61号 東京ドームシティ6F")],
        )
        self.assertEqual(links[0]["match_status"], "needs_review")
        self.assertEqual(links[0]["match_methods"], "site_address")
        self.assertEqual(states["b5"]["status"], "needs_review")

    def test_detailed_usage_without_source_match_is_candidate_only(self):
        links, states = match_buildings(
            [building("b4", name="名称不明", detail="422305")],
            [facility("m4", "別の動物園")],
        )
        self.assertEqual(links, [])
        self.assertEqual(states["b4"]["status"], "plateau_only_candidate")


class OutputTests(unittest.TestCase):
    def test_default_output_does_not_equal_source(self):
        source = Path("13_heritage_hazards.gpkg")
        self.assertEqual(default_output_path(source), Path("13_museum_hazards.gpkg"))

    def test_empty_normalized_table_has_stable_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "test.sqlite"
            with sqlite3.connect(path) as connection:
                write_attribute_table(connection, "museum_building_links", [], LINK_FIELDS)
                columns = [
                    row[1]
                    for row in connection.execute("PRAGMA table_info(museum_building_links)")
                ]
            self.assertEqual(columns, LINK_FIELDS)


if __name__ == "__main__":
    unittest.main()
