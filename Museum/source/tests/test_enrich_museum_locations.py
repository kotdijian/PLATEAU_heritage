from __future__ import annotations

import unittest

from lxml import html

from Museum.source.scripts.enrich_museum_locations import (
    geocode_abr,
    sanitize_address_candidate,
    score_candidate,
    structured_candidates,
    visible_candidates,
)


class LocationEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.facility = {
            "facility_name": "テスト博物館",
            "municipality_name": "千代田区",
            "official_url": "https://example.org/",
        }

    def test_json_ld_address_and_coordinates_are_extracted(self):
        document = html.fromstring("""
<html><body><script type="application/ld+json">
{"@type":"Museum","name":"テスト博物館",
 "address":{"postalCode":"100-0005","addressRegion":"東京都",
 "addressLocality":"千代田区","streetAddress":"丸の内1-1"},
 "geo":{"latitude":35.68,"longitude":139.76}}
</script></body></html>
""")
        rows = structured_candidates(document)
        self.assertEqual(len(rows), 1)
        self.assertIn("東京都千代田区丸の内1-1", rows[0]["address"])
        self.assertEqual(rows[0]["latitude"], "35.68")
        self.assertGreaterEqual(
            score_candidate(rows[0], self.facility, "https://example.org/access"),
            70,
        )

    def test_current_abr_array_result_is_unwrapped_and_validated(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return [{
                    "query": {"input": "東京都千代田区丸の内1-1"},
                    "result": {
                        "score": 1,
                        "match_level": "residential_detail",
                        "coordinate_level": "residential_detail",
                        "lat": 35.681,
                        "lon": 139.767,
                        "lg_code": "131016",
                    },
                }]

        class Session:
            def get(self, endpoint, params, timeout):
                return Response()

        row = geocode_abr(
            Session(), "http://localhost:3000", "東京都千代田区丸の内1-1",
            30, "13101",
        )
        self.assertEqual(row["latitude"], "35.681")
        self.assertEqual(row["longitude"], "139.767")
        self.assertEqual(row["coordinate_use"], "building_candidate")

    def test_abr_municipality_mismatch_rejects_coordinates(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return [{"result": {
                    "coordinate_level": "residential_detail",
                    "lat": 35.681, "lon": 139.767, "lg_code": "131024",
                }}]

        class Session:
            def get(self, endpoint, params, timeout):
                return Response()

        row = geocode_abr(
            Session(), "http://localhost:3000", "東京都千代田区丸の内1-1",
            30, "13101",
        )
        self.assertEqual(row["latitude"], "")
        self.assertEqual(row["coordinate_use"], "rejected_municipality_mismatch")

    def test_abr_v3_geojson_result_is_unwrapped_and_level_is_normalized(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "type": "FeatureCollection",
                    "result_info": {"api_version": "3.0.51"},
                    "features": [{
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [139.736389, 35.679108],
                        },
                        "properties": {
                            "score": 1,
                            "match_level": "rsdtdsp_rsdt",
                            "coordinates_level": "rsdtdsp_rsdt",
                            "ids": {"lg_code": "131016"},
                        },
                    }],
                }

        class Session:
            def get(self, endpoint, params, timeout):
                return Response()

        row = geocode_abr(
            Session(), "http://localhost:3000",
            "東京都千代田区紀尾井町1-3", 30, "13101",
        )
        self.assertEqual(row["latitude"], "35.679108")
        self.assertEqual(row["longitude"], "139.736389")
        self.assertEqual(row["match_level"], "residential_detail")
        self.assertEqual(row["coordinate_level"], "residential_detail")
        self.assertEqual(row["coordinate_use"], "building_candidate")

    def test_abr_v3_low_precision_coordinate_is_query_only(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [139.737562, 35.678722],
                        },
                        "properties": {
                            "score": 1,
                            "match_level": "machiaza",
                            "coordinates_level": "machiaza",
                            "ids": {"lg_code": "131016"},
                        },
                    }],
                }

        class Session:
            def get(self, endpoint, params, timeout):
                return Response()

        row = geocode_abr(
            Session(), "http://localhost:3000",
            "東京都千代田区紀尾井町", 30, "13101",
        )
        self.assertEqual(row["coordinate_level"], "machiaza")
        self.assertEqual(row["coordinate_use"], "plateau_query")

    def test_mailing_address_is_penalized(self):
        document = html.fromstring(
            "<html><body><p>郵便物送付先 東京都千代田区丸の内1-1</p></body></html>"
        )
        row = visible_candidates(document, self.facility)[0]
        self.assertLess(
            score_candidate(row, self.facility, "https://example.org/contact"),
            70,
        )

    def test_contact_details_are_removed_from_address(self):
        value = (
            "東京都千代田区一ツ橋2-6-1 共立女子学園2号館B1F"
            "TEL: 03-3237-2665 FAX: 03-3237-2787"
        )
        self.assertEqual(
            sanitize_address_candidate(value),
            "東京都千代田区一ツ橋2-6-1 共立女子学園2号館B1F",
        )

    def test_spaced_contact_label_is_removed(self):
        self.assertEqual(
            sanitize_address_candidate(
                "東京都新宿区神楽坂1－3 東京理科大学近代科学資料館 "
                "T E L：03-5228-8224"
            ),
            "東京都新宿区神楽坂1－3 東京理科大学近代科学資料館",
        )

    def test_facility_page_label_after_address_is_removed(self):
        facility = {
            "facility_name": "東京理科大学近代科学資料館",
            "municipality_name": "新宿区",
            "official_url": "https://example.org/",
        }
        document = html.fromstring(
            "<p>東京都新宿区神楽坂1－3 東京理科大学近代科学資料館 "
            "T E L：03－5228－8224</p>"
        )
        rows = visible_candidates(document, facility)
        self.assertEqual(rows[0]["address"], "東京都新宿区神楽坂1－3")

    def test_navigation_and_building_description_are_removed(self):
        cases = {
            "東京都渋谷区広尾3-12-36 徒歩でのご来館 JR恵比寿駅西口":
                "東京都渋谷区広尾3-12-36",
            "東京都千代田区丸の内3-1-1 設計 谷口吉郎 施工 竹中工務店":
                "東京都千代田区丸の内3-1-1",
            "東京都台東区上野公園 1-2 バリアフリーのご案内":
                "東京都台東区上野公園 1-2",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(sanitize_address_candidate(value), expected)

    def test_meaningful_closing_parenthesis_is_preserved(self):
        self.assertEqual(
            sanitize_address_candidate("東京都江東区三好4-1-1(木場公園内)"),
            "東京都江東区三好4-1-1(木場公園内)",
        )

    def test_prefecture_is_added_to_municipality_only_official_address(self):
        facility = {
            "facility_name": "くにたち郷土文化館",
            "municipality_name": "国立市",
            "official_url": "https://example.org/",
        }
        document = html.fromstring(
            "<html><body><address>〒186-0011 国立市谷保6231</address></body></html>"
        )
        row = visible_candidates(document, facility)[0]
        self.assertEqual(row["address"], "東京都国立市谷保6231")
        self.assertEqual(row["postal_code"], "186-0011")

    def test_matching_section_heading_disambiguates_two_facility_addresses(self):
        facility = {
            "facility_name": "くにたち郷土文化館",
            "municipality_name": "国立市",
            "official_url": "https://example.org/",
        }
        document = html.fromstring("""
<html><head><title>アクセス | くにたち郷土文化館</title></head><body>
<h2>くにたち郷土文化館</h2><p>〒186-0011 国立市谷保6231 (TEL: 042-576-0211)</p>
<h2>国立市古民家</h2><p>〒186-0012 国立市泉5丁目21番地の20</p>
</body></html>
""")
        rows = visible_candidates(document, facility)
        for row in rows:
            row["page_identity"] = "アクセス | くにたち郷土文化館"
        scores = {
            row["address"]: score_candidate(row, facility, "https://example.org/access")
            for row in rows
        }
        self.assertGreater(
            scores["東京都国立市谷保6231"],
            scores["東京都国立市泉5丁目21番地の20"],
        )


if __name__ == "__main__":
    unittest.main()
