from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from Museum.source.scripts.build_museum_osm_matches import (
    audit_facility,
    build_candidate,
    confirmed_museum_ids,
    element_has_full_geometry,
    fetch_overpass,
    fetch_shortlist_geometry,
    group_candidates,
    geometry_query,
    municipality_evidence,
    name_evidence,
    split_osm_names,
)


class MuseumOsmMatchTests(unittest.TestCase):
    def setUp(self):
        self.facility = {
            "museum_id": "MUS-1",
            "facility_name": "東京都現代美術館",
            "municipality_code": "13108",
            "municipality_name": "江東区",
            "official_url": "https://www.mot-art-museum.jp/",
        }
        self.location = {
            "latitude": "35.67970",
            "longitude": "139.80800",
            "review_status": "accepted",
            "coordinate_use": "building_candidate",
        }

    def element(self, **tags):
        return {
            "type": "way",
            "id": 123,
            "center": {"lat": 35.67971, "lon": 139.80801},
            "tags": {
                "name": "東京都現代美術館",
                "tourism": "museum",
                "addr:city": "江東区",
                **tags,
            },
        }

    def test_osm_names_include_alternate_names(self):
        self.assertEqual(
            split_osm_names({"name": "本館", "alt_name": "旧名;別名"}),
            ["本館", "旧名", "別名"],
        )

    def test_exact_name_and_location_is_high_confidence(self):
        row = build_candidate(
            self.facility, self.element(), {}, self.location,
            "candidate_discovery", "2026-09-06T00:00:00+00:00", "abc",
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["name_match"], "exact")
        self.assertEqual(row["municipality_match"], "match")
        self.assertEqual(row["high_confidence"], "true")
        self.assertLess(float(row["distance_m"]), 5)

    def test_exact_name_without_location_support_is_not_high_confidence(self):
        element = self.element()
        element["tags"].pop("addr:city")
        row = build_candidate(
            self.facility, element, {}, None, "candidate_discovery",
            "2026-09-06T00:00:00+00:00", "abc",
        )
        self.assertEqual(row["high_confidence"], "false")

    def test_same_official_website_supports_exact_name(self):
        element = self.element(website="https://mot-art-museum.jp/guide/")
        element["tags"].pop("addr:city")
        row = build_candidate(
            self.facility, element, {}, None, "candidate_discovery",
            "2026-09-06T00:00:00+00:00", "abc",
        )
        self.assertEqual(row["website_match"], "true")
        self.assertEqual(row["high_confidence"], "true")

    def test_similar_name_is_candidate_only(self):
        method, similarity = name_evidence(
            "東京都現代美術館", ["東京都現代美術館駐車場"], {}
        )
        self.assertEqual(method, "contained")
        self.assertGreater(similarity, 0.7)

    def test_non_japanese_city_label_is_unknown_not_conflict(self):
        self.assertEqual(municipality_evidence("江東区", "Tokyo"), "unknown")
        self.assertEqual(municipality_evidence("江東区", "新宿区"), "conflict")

    def test_audit_does_not_promote_multiple_high_confidence_rows(self):
        first = build_candidate(
            self.facility, self.element(), {}, self.location,
            "audit_only", "2026-09-06T00:00:00+00:00", "abc",
        )
        second_element = self.element()
        second_element["id"] = 456
        second_element["center"] = {"lat": 35.75000, "lon": 139.80000}
        second = build_candidate(
            self.facility, second_element, {}, self.location,
            "audit_only", "2026-09-06T00:00:00+00:00", "abc",
        )
        row = audit_facility(self.facility, [first, second], "audit_only")
        self.assertEqual(row["osm_status"], "multiple_high_confidence")

    def test_same_facility_node_and_way_form_one_group(self):
        first = build_candidate(
            self.facility, self.element(), {}, self.location,
            "candidate_discovery", "2026-09-06T00:00:00+00:00", "abc",
        )
        node = self.element()
        node["type"] = "node"
        node["id"] = 789
        node["lat"] = node["center"]["lat"]
        node["lon"] = node["center"]["lon"]
        node.pop("center")
        second = build_candidate(
            self.facility, node, {}, self.location,
            "candidate_discovery", "2026-09-06T00:00:00+00:00", "abc",
        )
        groups = group_candidates(self.facility["museum_id"], [first, second])
        self.assertEqual(len(groups), 1)
        row = audit_facility(
            self.facility, [first, second], "candidate_discovery", groups
        )
        self.assertEqual(row["osm_status"], "high_confidence_unique")
        self.assertEqual(row["high_confidence_count"], 2)
        self.assertEqual(row["high_confidence_group_count"], 1)
        self.assertEqual(row["selected_osm_type"], "way")

    def test_named_bicycle_rental_is_not_high_confidence(self):
        element = self.element(amenity="bicycle_rental")
        element["tags"].pop("tourism")
        row = build_candidate(
            self.facility, element, {}, self.location,
            "candidate_discovery", "2026-09-06T00:00:00+00:00", "abc",
        )
        self.assertEqual(row["osm_object_role"], "supporting_poi")
        self.assertEqual(row["high_confidence"], "false")

    def test_geometry_query_requests_only_ways_and_relations(self):
        query = geometry_query([
            ("node", "1"), ("way", "20"), ("relation", "30"),
        ])
        self.assertIn("way(id:20);", query)
        self.assertIn("rel(id:30);", query)
        self.assertNotIn("node(id:1)", query)
        self.assertIn("out body geom;", query)
        self.assertNotIn("center", query)

    def test_center_only_object_is_not_full_geometry(self):
        self.assertFalse(element_has_full_geometry({
            "type": "way", "id": 20,
            "center": {"lat": 35.0, "lon": 139.0},
        }))
        self.assertTrue(element_has_full_geometry({
            "type": "way", "id": 20,
            "geometry": [{"lat": 35.0, "lon": 139.0}],
        }))

    def test_confirmed_ids_are_read_without_spatial_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "museum.gpkg"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE museum_building_links "
                "(museum_id TEXT, match_status TEXT)"
            )
            connection.executemany(
                "INSERT INTO museum_building_links VALUES (?, ?)",
                [("MUS-1", "confirmed"), ("MUS-2", "needs_review")],
            )
            connection.commit()
            connection.close()
            self.assertEqual(confirmed_museum_ids(path), {"MUS-1"})

    def test_offline_cache_is_reusable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "osm.json"
            path.write_text(
                json.dumps({"elements": [{"type": "node", "id": 1}]}),
                encoding="utf-8",
            )
            payload, digest, mode = fetch_overpass(
                "https://invalid.example", path, refresh=False, offline=True,
                timeout=1, user_agent="test",
            )
            self.assertEqual(payload, {"elements": [{"type": "node", "id": 1}]})
            self.assertEqual(mode, "cache")
            self.assertEqual(len(digest), 64)

    def test_shortlist_geometry_cache_is_reusable_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geometry.json"
            path.write_text(json.dumps({
                "requested_objects": [{"type": "way", "id": "20"}],
                "elements": [{
                    "type": "way", "id": 20,
                    "geometry": [{"lat": 35.0, "lon": 139.0}],
                }],
            }), encoding="utf-8")
            rows, digest, mode = fetch_shortlist_geometry(
                "https://invalid.example", path, [("way", "20")],
                refresh=False, offline=True, timeout=1, user_agent="test",
            )
            self.assertIn(("way", "20"), rows)
            self.assertEqual(mode, "cache")
            self.assertEqual(len(digest), 64)

    def test_center_only_geometry_cache_is_rejected_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geometry.json"
            path.write_text(json.dumps({
                "requested_objects": [{"type": "way", "id": "20"}],
                "elements": [{
                    "type": "way", "id": 20,
                    "center": {"lat": 35.0, "lon": 139.0},
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "missing requested full geometry"):
                fetch_shortlist_geometry(
                    "https://invalid.example", path, [("way", "20")],
                    refresh=False, offline=True, timeout=1, user_agent="test",
                )


if __name__ == "__main__":
    unittest.main()
