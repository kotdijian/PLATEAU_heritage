from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from Museum.source.scripts.build_museum_locations import (
    candidate_matches_facility,
    candidate_row,
    choose_candidate,
    collect_cultural_heritage_online,
    load_location_source_csv,
    name_match_key,
    parse_cultural_online_detail,
    plausible_facility_address,
    split_postal_address,
)


class MuseumLocationBuilderTests(unittest.TestCase):
    def setUp(self):
        self.aliases = {}
        self.name_to_code = {"千代田区": "13101", "世田谷区": "13112"}
        self.facility = {
            "museum_id": "MUS-1",
            "facility_name": "明治大学博物館",
            "municipality_code": "13101",
            "municipality_name": "千代田区",
        }

    def row(self, *, address="東京都千代田区丸の内1-1", priority=80):
        return candidate_row(
            candidate_name="明治大学博物館",
            municipality_code="13101",
            municipality_name="千代田区",
            address=address,
            source_id="test",
            source_url="https://example.org/1",
            source_authority="official",
            extraction_method="test",
            priority=priority,
            retrieved_at="2026-09-06T00:00:00+00:00",
        )

    def test_legal_prefix_is_ignored_but_name_remains_exact(self):
        self.assertEqual(
            name_match_key("公益財団法人 戸栗美術館"),
            name_match_key("戸栗美術館"),
        )
        self.assertNotEqual(
            name_match_key("戸栗美術館"),
            name_match_key("太田記念美術館"),
        )

    def test_cultural_online_detail_parser_extracts_address(self):
        payload = """
<html><body><h1>明治大学博物館</h1><dl>
<dt>所在地</dt><dd>千代田区神田駿河台1-1 アカデミーコモン地階</dd>
</dl></body></html>
""".encode()
        row = parse_cultural_online_detail(
            payload, "https://online.bunka.go.jp/museums/detail/12301",
            self.aliases, self.name_to_code,
            "2026-09-06T00:00:00+00:00", "abc",
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["municipality_code"], "13101")
        self.assertEqual(
            row["address_normalized"],
            "東京都千代田区神田駿河台1-1アカデミーコモン地階",
        )

    def test_bilingual_duplicate_address_is_removed(self):
        postal, address = split_postal_address(
            "三鷹市大沢3-10-2 /10-2, Osawa 3-chome, Mitaka-shi, Tokyo"
        )
        self.assertEqual(postal, "")
        self.assertEqual(address, "三鷹市大沢3-10-2")

    def test_status_note_is_not_an_existing_address(self):
        self.assertFalse(plausible_facility_address(
            "立て替えのため休館中", "文京区"
        ))
        self.assertTrue(plausible_facility_address(
            "東京都文京区目白台1-1-1", "文京区"
        ))

    def test_japanese_numeral_address_is_supported(self):
        self.assertTrue(plausible_facility_address(
            "東京都江東区青海二丁目地先", "江東区"
        ))

    def test_malformed_or_contaminated_address_is_rejected(self):
        self.assertFalse(plausible_facility_address(
            "東京都江東区有明－丁目南西側地先13号その1埋立地", "江東区"
        ))
        self.assertFalse(plausible_facility_address(
            "東京都千代田区丸の内3-1-1 設計 谷口吉郎", "千代田区"
        ))

    def test_exact_name_and_municipality_match(self):
        self.assertTrue(candidate_matches_facility(
            self.row(), self.facility, self.aliases
        ))
        other = self.row()
        other["municipality_code"] = "13112"
        self.assertFalse(candidate_matches_facility(
            other, self.facility, self.aliases
        ))

    def test_single_highest_priority_address_is_selected(self):
        low = self.row(address="東京都千代田区丸の内1-1", priority=80)
        high = self.row(address="東京都千代田区一ツ橋2-6-1", priority=120)
        selected, reason = choose_candidate(self.facility, [low, high])
        self.assertEqual(selected["address_normalized"], "東京都千代田区一ツ橋2-6-1")
        self.assertEqual(reason, "")

    def test_equal_priority_conflict_requires_review(self):
        rows = [
            self.row(address="東京都千代田区丸の内1-1"),
            self.row(address="東京都千代田区一ツ橋2-6-1"),
        ]
        selected, reason = choose_candidate(self.facility, rows)
        self.assertIsNone(selected)
        self.assertEqual(reason, "conflicting_equal_priority_addresses")

    def test_authoritative_csv_infers_municipality_and_normalizes_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "official.csv"
            path.write_text(
                "source_id,facility_name,address,source_url,priority\n"
                "official_list,明治大学博物館,東京都千代田区神田駿河台1-1,"
                "https://example.jp,95\n",
                encoding="utf-8",
            )
            rows = load_location_source_csv(
                path, self.aliases, self.name_to_code,
                "2026-09-06T00:00:00+00:00",
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["municipality_code"], "13101")
        self.assertEqual(rows[0]["priority"], "95")
        self.assertEqual(rows[0]["extraction_method"], "authoritative_csv_import")

    def test_cultural_online_stops_if_prefecture_filter_is_lost(self):
        base = "https://online.bunka.go.jp/museums/search?prefecture_cd=13"
        page2 = "https://online.bunka.go.jp/museums/search/page:2"
        detail = "https://online.bunka.go.jp/museums/detail/12301"
        payloads = {
            base: (
                '<html><body><select><option>100件</option></select>'
                '<p class="g-controlBar_total">79件</p>'
                '<a href="/museums/detail/12301">'
                "明治大学博物館</a></body></html>"
            ).encode(),
            page2: (
                '<html><body><p class="g-controlBar_total">1096件</p>'
                "</body></html>"
            ).encode(),
            detail: (
                "<html><body><h1>明治大学博物館</h1><dl><dt>所在地</dt>"
                "<dd>千代田区神田駿河台1-1</dd></dl></body></html>"
            ).encode(),
        }

        class Response:
            def __init__(self, url):
                self.url = url
                self.content = payloads[url]

            def raise_for_status(self):
                return None

        class Session:
            def get(self, url, timeout):
                return Response(url)

        with tempfile.TemporaryDirectory() as directory:
            rows, status = collect_cultural_heritage_online(
                Session(), base, Path(directory), 5, False, False, 3, 0,
                self.aliases, self.name_to_code,
                "2026-09-06T00:00:00+00:00",
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(status["reported_record_count"], 79)
        self.assertTrue(any(
            "prefecture_filter_lost_on_pagination" in error
            for error in status["errors"]
        ))


if __name__ == "__main__":
    unittest.main()
