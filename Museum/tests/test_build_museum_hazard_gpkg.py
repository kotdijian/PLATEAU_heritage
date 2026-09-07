from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from shapely.geometry import Point, Polygon

from Museum.build_museum_hazard_gpkg import (
    LINK_FIELDS,
    apply_location_enrichment,
    assess_space_inundation,
    audit_gml_id_duplicates,
    build_space_hazard_assessments,
    choose_facility_type,
    default_output_path,
    load_museum_data,
    load_osm_spatial_evidence,
    load_facility_spaces,
    match_buildings,
    museum_address_key,
    museum_query_address,
    museum_site_address_key,
    scan_buildings_with_preferences,
    write_attribute_table,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MUSEUM_DATA = REPOSITORY_ROOT / "Museum" / "source" / "data"


def facility(
    museum_id: str, name: str, address: str = "", municipality_code: str = "13101"
):
    return {
        "museum_id": museum_id,
        "canonical_name": name,
        "municipality_code": municipality_code,
        "address": address,
    }


def building(
    gml_id: str,
    name: str = "",
    address: str = "",
    detail: str = "",
    city_code: str = "13101",
    detail_codespace: str = "../../codelists/BuildingDetailAttribute_detailedUsage.xml",
):
    return SimpleNamespace(
        gml_id=gml_id,
        building_id=f"bid-{gml_id}",
        city_code=city_code,
        name=name,
        address=address,
        usage="422",
        detailed_usage=detail,
        detailed_usage_codespace=detail_codespace,
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

    def test_accepted_location_overlay_replaces_working_address_and_preserves_source(self):
        facilities = [{
            "museum_id": "m1", "address": "旧住所", "postal_code": "000-0000"
        }]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "museum_location_enrichment.csv"
            path.write_text(
                "museum_id,address_normalized,postal_code,latitude,longitude,"
                "coordinate_use,review_status\n"
                "m1,東京都千代田区丸の内1-1,100-0005,35.681,139.767,"
                "building_candidate,accepted\n",
                encoding="utf-8",
            )
            apply_location_enrichment(facilities, Path(temp_dir))
        self.assertEqual(facilities[0]["address"], "東京都千代田区丸の内1-1")
        self.assertEqual(facilities[0]["source_manifest_address"], "旧住所")
        self.assertEqual(facilities[0]["source_manifest_postal_code"], "000-0000")
        self.assertEqual(facilities[0]["location_overlay_applied"], 1)
        self.assertEqual(facilities[0]["latitude"], 35.681)
        self.assertEqual(facilities[0]["location_coordinate_use"], "building_candidate")

    def test_multiple_floors_and_collection_storage_are_normalized(self):
        facilities = [{"museum_id": "m1"}]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "museum_facility_spaces.csv"
            path.write_text(
                "space_id,museum_id,space_type,space_name,presence_status,"
                "floor_label,floor_min,floor_max,is_basement,"
                "floor_elevation_min_m,floor_elevation_max_m,collections_present,"
                "source_url,source_authority,source_date,retrieved_at,review_status,notes\n"
                "s1,m1,museum_occupancy,展示室,yes,1-3F,1,3,no,0,9,yes,"
                "https://example.jp,official,2026-01-01,,accepted,\n"
                "s2,m1,collection_storage,収蔵庫,yes,B1,-1,-1,yes,-3,0,yes,"
                "https://example.jp,official,2026-01-01,,accepted,\n",
                encoding="utf-8",
            )
            spaces = load_facility_spaces(Path(temp_dir), facilities)
        self.assertEqual(len(spaces), 2)
        self.assertEqual(facilities[0]["facility_floor_min"], -1)
        self.assertEqual(facilities[0]["facility_floor_max"], 3)
        self.assertEqual(facilities[0]["facility_spans_multiple_floors"], 1)
        self.assertEqual(facilities[0]["collection_storage_status"], "yes")
        self.assertEqual(facilities[0]["storage_in_basement"], "yes")

    def test_explicit_absence_of_collection_storage_is_preserved(self):
        facilities = [{"museum_id": "m1"}]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "museum_facility_spaces.csv"
            path.write_text(
                ",".join([
                    "space_id", "museum_id", "space_type", "space_name",
                    "presence_status", "floor_label", "floor_min", "floor_max",
                    "is_basement", "floor_elevation_min_m", "floor_elevation_max_m",
                    "collections_present", "source_url", "source_authority",
                    "source_date", "retrieved_at", "review_status", "notes",
                ]) + "\n"
                "s1,m1,collection_storage,,no,,,,unknown,,,unknown,"
                "https://example.jp,official,2026-01-01,,accepted,\n",
                encoding="utf-8",
            )
            load_facility_spaces(Path(temp_dir), facilities)
        self.assertEqual(facilities[0]["collection_storage_status"], "no")


class MatchingTests(unittest.TestCase):
    def test_unique_osm_node_confirms_building(self):
        museum = facility("mo", "名称不一致")
        target = building("bo", name="別名")
        target.geometry = Polygon([(139, 35), (140, 35), (140, 36), (139, 36)])
        evidence = {
            "mo": {
                "museum_id": "mo", "municipality_code": "13101",
                "geometry": Point(139.5, 35.5), "geometry_kind": "point",
                "method": "osm_node_in_building", "osm_type": "node",
                "osm_id": "123", "osm_url": "https://www.openstreetmap.org/node/123",
                "osm_object_role": "facility_feature",
            }
        }
        links, states = match_buildings([target], [museum], evidence)
        self.assertEqual(links[0]["match_status"], "confirmed")
        self.assertEqual(links[0]["osm_unique_spatial_match"], 1)
        self.assertIn("osm_node_in_building", links[0]["match_methods"])
        self.assertEqual(states["bo"]["status"], "confirmed")

    def test_osm_polygon_intersecting_multiple_buildings_requires_review(self):
        museum = facility("mo", "名称不一致")
        targets = [building("bo1", name="別名1"), building("bo2", name="別名2")]
        targets[0].geometry = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        targets[1].geometry = Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])
        evidence = {
            "mo": {
                "museum_id": "mo", "municipality_code": "13101",
                "geometry": Polygon([(0.5, 0), (1.5, 0), (1.5, 1), (0.5, 1)]),
                "geometry_kind": "polygon", "method": "osm_geometry_overlap",
                "osm_type": "way", "osm_id": "456",
                "osm_url": "https://www.openstreetmap.org/way/456",
                "osm_object_role": "facility_feature",
            }
        }
        links, states = match_buildings(targets, [museum], evidence)
        self.assertEqual(len(links), 2)
        self.assertTrue(all(row["match_status"] == "needs_review" for row in links))
        self.assertTrue(all(row["osm_unique_spatial_match"] == 0 for row in links))

    def test_osm_loader_excludes_coordinate_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            audit = directory / "audit.csv"
            audit.write_text(
                "museum_id,municipality_code,plateau_match_scope,osm_status,"
                "selected_osm_type,selected_osm_id,selected_osm_url,"
                "selected_latitude,selected_longitude,selected_object_role,coordinate_conflict\n"
                "safe,13101,candidate_discovery,high_confidence_unique,node,1,url,35.5,139.5,facility_feature,false\n"
                "conflict,13101,candidate_discovery,high_confidence_unique,node,2,url,35.5,139.5,facility_feature,true\n",
                encoding="utf-8",
            )
            evidence, counts = load_osm_spatial_evidence(
                audit, directory / "missing_geometry.json"
            )
        self.assertEqual(set(evidence), {"safe"})
        self.assertEqual(counts["high_confidence_discovery"], 2)
        self.assertEqual(counts["coordinate_conflict_excluded"], 1)

    def test_unique_precise_point_confirms_building(self):
        class CoveringGeometry:
            def covers(self, point):
                return True

        museum = facility("mp", "名称不一致")
        museum.update({
            "latitude": 35.68,
            "longitude": 139.76,
            "location_coordinate_use": "building_candidate",
        })
        target = building("bp", name="別名")
        target.geometry = CoveringGeometry()
        links, states = match_buildings([target], [museum])
        self.assertEqual(links[0]["match_status"], "confirmed")
        self.assertEqual(links[0]["point_in_building"], 1)
        self.assertEqual(links[0]["unique_precise_point_match"], 1)
        self.assertIn("point_in_building", links[0]["match_methods"])
        self.assertIn("unique_precise_point_in_building", links[0]["match_methods"])
        self.assertEqual(states["bp"]["status"], "confirmed")

    def test_precise_point_covering_multiple_buildings_requires_review(self):
        class CoveringGeometry:
            def covers(self, point):
                return True

        museum = facility("mp", "名称不一致")
        museum.update({
            "latitude": 35.68,
            "longitude": 139.76,
            "location_coordinate_use": "building_candidate",
        })
        targets = [building("bp1", name="別名1"), building("bp2", name="別名2")]
        for target in targets:
            target.geometry = CoveringGeometry()
        links, states = match_buildings(targets, [museum])
        self.assertEqual(len(links), 2)
        self.assertTrue(all(row["match_status"] == "needs_review" for row in links))
        self.assertTrue(all(row["unique_precise_point_match"] == 0 for row in links))
        self.assertTrue(all(state["status"] == "needs_review" for state in states.values()))

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
            [facility(
                "m5",
                "テスト館",
                "文京区後楽1丁目3番61号 東京ドームシティ6F",
                municipality_code="13105",
            )],
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

    def test_tokyo_culture_usage_is_candidate_not_museum_confirmation(self):
        links, states = match_buildings(
            [building("b6", address="東京都千代田区丸の内1-1", detail="1122")],
            [facility("m6", "テスト館", "東京都千代田区丸の内1丁目1番")],
        )
        self.assertEqual(links[0]["match_status"], "needs_review")
        self.assertEqual(
            links[0]["match_methods"],
            "exact_address;tokyo_culture_facility",
        )
        self.assertEqual(states["b6"]["status"], "needs_review")

    def test_tokyo_culture_usage_without_source_match_is_plateau_candidate(self):
        links, states = match_buildings(
            [building("b7", detail="1122")],
            [facility("m7", "別の博物館")],
        )
        self.assertEqual(links, [])
        self.assertEqual(states["b7"]["status"], "plateau_only_candidate")
        self.assertEqual(states["b7"]["candidate_methods"], "tokyo_culture_facility")

    def test_1122_is_not_globally_interpreted_as_tokyo_culture(self):
        links, states = match_buildings(
            [building("b8", detail="1122", city_code="14100")],
            [facility("m8", "別の博物館")],
        )
        self.assertEqual(links, [])
        self.assertNotIn("b8", states)

    def test_plateau_address_municipality_overrides_mesh_query_scope(self):
        links, states = match_buildings(
            [building(
                "b9",
                name="国立テスト博物館",
                address="日本 東京都千代田区丸の内一丁目",
                city_code="13105",
            )],
            [facility("m9", "国立テスト博物館")],
        )
        self.assertEqual(links[0]["match_status"], "confirmed")
        self.assertEqual(states["b9"]["source_city_code"], "13105")
        self.assertEqual(states["b9"]["matching_city_code"], "13101")
        self.assertEqual(states["b9"]["matching_city_method"], "plateau_address")


class DuplicateAuditTests(unittest.TestCase):
    GML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel
  xmlns:core="http://www.opengis.net/citygml/2.0"
  xmlns:gml="http://www.opengis.net/gml"
  xmlns:bldg="http://www.opengis.net/citygml/building/2.0">
  <core:cityObjectMember>
    <bldg:Building gml:id="{gml_id}"><gml:name>{name}</gml:name></bldg:Building>
  </core:cityObjectMember>
</core:CityModel>
"""

    def test_identical_duplicate_is_counted_without_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir) / "a.gml", Path(temp_dir) / "b.gml"]
            payload = self.GML_TEMPLATE.format(gml_id="bldg-1", name="同一建物")
            for path in paths:
                path.write_text(payload, encoding="utf-8")
            audit = audit_gml_id_duplicates([
                SimpleNamespace(local_path=str(path)) for path in paths
            ])
        self.assertEqual(audit["building_elements_with_gml_id"], 2)
        self.assertEqual(audit["unique_gml_id_count"], 1)
        self.assertEqual(audit["duplicate_gml_id_count"], 1)
        self.assertEqual(audit["duplicate_occurrences"], 1)
        self.assertEqual(audit["duplicate_conflict_count"], 0)

    def test_prefix_and_formatting_differences_are_not_conflicts(self):
        alternate = """<?xml version="1.0" encoding="UTF-8"?>
<city:CityModel xmlns:city="http://www.opengis.net/citygml/2.0"
 xmlns:g="http://www.opengis.net/gml"
 xmlns:building="http://www.opengis.net/citygml/building/2.0"><city:cityObjectMember>
<building:Building g:id="bldg-1">
  <g:name>同一建物</g:name>
</building:Building></city:cityObjectMember></city:CityModel>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir) / "a.gml", Path(temp_dir) / "b.gml"]
            paths[0].write_text(
                self.GML_TEMPLATE.format(gml_id="bldg-1", name="同一建物"),
                encoding="utf-8",
            )
            paths[1].write_text(alternate, encoding="utf-8")
            audit = audit_gml_id_duplicates([
                SimpleNamespace(local_path=str(path)) for path in paths
            ])
        self.assertEqual(audit["duplicate_gml_id_count"], 1)
        self.assertEqual(audit["duplicate_conflict_count"], 0)
        self.assertEqual(
            audit["duplicate_comparison"],
            "extractor_semantic_xml_v4",
        )

    def test_sibling_order_difference_is_resolved_without_losing_values(self):
        template = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
 xmlns:gml="http://www.opengis.net/gml"
 xmlns:bldg="http://www.opengis.net/citygml/building/2.0">
 <core:cityObjectMember><bldg:Building gml:id="bldg-1">
  {children}
 </bldg:Building></core:cityObjectMember>
</core:CityModel>
"""
        first = "<gml:name>同一建物</gml:name><bldg:usage>422</bldg:usage>"
        second = "<bldg:usage>422</bldg:usage><gml:name>同一建物</gml:name>"
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir) / "a.gml", Path(temp_dir) / "b.gml"]
            paths[0].write_text(template.format(children=first), encoding="utf-8")
            paths[1].write_text(template.format(children=second), encoding="utf-8")
            audit = audit_gml_id_duplicates([
                SimpleNamespace(local_path=str(path)) for path in paths
            ])
        self.assertEqual(audit["resolved_duplicate_conflict_count"], 1)
        self.assertEqual(audit["unresolved_duplicate_conflict_count"], 0)
        self.assertEqual(audit["order_normalized_duplicate_conflict_count"], 1)
        self.assertEqual(
            audit["resolved_duplicate_conflicts"][0]["resolution"],
            "citygml_sibling_order_and_optional_metadata_only",
        )

    def test_absent_lod3_lod4_source_metadata_is_a_resolved_difference(self):
        template = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
 xmlns:gml="http://www.opengis.net/gml"
 xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
 xmlns:uro="https://www.geospatial.jp/iur/uro/2.0">
 <core:cityObjectMember><bldg:Building gml:id="bldg-1">
  <gml:name>同一建物</gml:name>
  <uro:bldgDataQualityAttribute><uro:DataQualityAttribute>
   {metadata}
  </uro:DataQualityAttribute></uro:bldgDataQualityAttribute>
 </bldg:Building></core:cityObjectMember>
</core:CityModel>
"""
        metadata = """
<uro:geometrySrcDescLod3 codeSpace="../../codelists/DataQualityAttribute_geometrySrcDesc.xml">999</uro:geometrySrcDescLod3>
<uro:geometrySrcDescLod4 codeSpace="../../codelists/DataQualityAttribute_geometrySrcDesc.xml">999</uro:geometrySrcDescLod4>
<uro:appearanceSrcDescLod3 codeSpace="../../codelists/DataQualityAttribute_appearanceSrcDesc.xml">99</uro:appearanceSrcDescLod3>
<uro:appearanceSrcDescLod4 codeSpace="../../codelists/DataQualityAttribute_appearanceSrcDesc.xml">99</uro:appearanceSrcDescLod4>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir) / "a.gml", Path(temp_dir) / "b.gml"]
            paths[0].write_text(template.format(metadata=""), encoding="utf-8")
            paths[1].write_text(template.format(metadata=metadata), encoding="utf-8")
            audit = audit_gml_id_duplicates([
                SimpleNamespace(local_path=str(path)) for path in paths
            ])
        self.assertEqual(audit["duplicate_conflict_count"], 1)
        self.assertEqual(audit["resolved_duplicate_conflict_count"], 1)
        self.assertEqual(audit["unresolved_duplicate_conflict_count"], 0)
        self.assertEqual(audit["metadata_normalized_duplicate_conflict_count"], 1)
        self.assertEqual(
            audit["resolved_duplicate_conflicts"][0]["resolution"],
            "absent_higher_lod_source_metadata_only",
        )
        self.assertEqual(
            audit["_preferred_source_by_gml_id"]["bldg-1"],
            str(paths[1]),
        )

    def test_namespace_uri_only_difference_matches_extractor_semantics(self):
        template = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
 xmlns:gml="http://www.opengis.net/gml"
 xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
 xmlns:uro="{uro_namespace}">
 <core:cityObjectMember><bldg:Building gml:id="bldg-1">
  <gml:name>同一建物</gml:name>
  <uro:bldgDataQualityAttribute><uro:DataQualityAttribute>
   <uro:geometrySrcDescLod2 codeSpace="../../codelists/DataQualityAttribute_geometrySrcDesc.xml">1</uro:geometrySrcDescLod2>
  </uro:DataQualityAttribute></uro:bldgDataQualityAttribute>
 </bldg:Building></core:cityObjectMember>
</core:CityModel>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir) / "a.gml", Path(temp_dir) / "b.gml"]
            paths[0].write_text(
                template.format(uro_namespace="https://www.geospatial.jp/iur/uro/2.0"),
                encoding="utf-8",
            )
            paths[1].write_text(
                template.format(uro_namespace="https://www.geospatial.jp/iur/uro/3.0"),
                encoding="utf-8",
            )
            audit = audit_gml_id_duplicates([
                SimpleNamespace(local_path=str(path)) for path in paths
            ])
        self.assertEqual(audit["duplicate_conflict_count"], 1)
        self.assertEqual(audit["resolved_duplicate_conflict_count"], 1)
        self.assertEqual(audit["unresolved_duplicate_conflict_count"], 0)
        self.assertEqual(audit["namespace_normalized_duplicate_conflict_count"], 1)
        self.assertEqual(
            audit["resolved_duplicate_conflicts"][0]["resolution"],
            "namespace_uri_and_optional_absent_higher_lod_metadata_only",
        )

    def test_conflicting_duplicate_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir) / "a.gml", Path(temp_dir) / "b.gml"]
            paths[0].write_text(
                self.GML_TEMPLATE.format(gml_id="bldg-1", name="建物A"),
                encoding="utf-8",
            )
            paths[1].write_text(
                self.GML_TEMPLATE.format(gml_id="bldg-1", name="建物B"),
                encoding="utf-8",
            )
            audit = audit_gml_id_duplicates([
                SimpleNamespace(local_path=str(path)) for path in paths
            ])
        self.assertEqual(audit["duplicate_conflict_count"], 1)
        self.assertEqual(audit["resolved_duplicate_conflict_count"], 0)
        self.assertEqual(audit["unresolved_duplicate_conflict_count"], 1)
        self.assertEqual(audit["duplicate_conflicts"][0]["gml_id"], "bldg-1")

    def test_cross_boundary_conflict_prefers_embedded_municipality_source(self):
        template = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
 xmlns:gml="http://www.opengis.net/gml"
 xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
 xmlns:gen="http://www.opengis.net/citygml/generics/2.0">
 <core:cityObjectMember><bldg:Building gml:id="bldg-1">
  <gml:name>{name}</gml:name>
  <gen:stringAttribute name="13+区市町村コード+大字・町コード+町・丁目コード">
   <gen:value>13106007004</gen:value>
  </gen:stringAttribute>
 </bldg:Building></core:cityObjectMember>
</core:CityModel>
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir) / "taito.gml", Path(temp_dir) / "bunkyo.gml"]
            paths[0].write_text(template.format(name="詳細版"), encoding="utf-8")
            paths[1].write_text(template.format(name="簡略版"), encoding="utf-8")
            audit = audit_gml_id_duplicates([
                SimpleNamespace(local_path=str(paths[0]), city_code="13106"),
                SimpleNamespace(local_path=str(paths[1]), city_code="13105"),
            ])
        self.assertEqual(audit["duplicate_conflict_count"], 1)
        self.assertEqual(audit["resolved_duplicate_conflict_count"], 1)
        self.assertEqual(audit["unresolved_duplicate_conflict_count"], 0)
        self.assertEqual(
            audit["_preferred_source_by_gml_id"]["bldg-1"],
            str(paths[0]),
        )

    def test_scan_selects_audited_preferred_source(self):
        files = [
            SimpleNamespace(local_path="/cache/13105/tile.gml"),
            SimpleNamespace(local_path="/cache/13106/tile.gml"),
        ]
        records = {
            files[0].local_path: SimpleNamespace(
                gml_id="bldg-1", source_file=files[0].local_path, marker="simplified"
            ),
            files[1].local_path: SimpleNamespace(
                gml_id="bldg-1", source_file=files[1].local_path, marker="authoritative"
            ),
        }

        def fake_scan(selected_files, progress=False):
            return [records[selected_files[0].local_path]]

        with patch(
            "Museum.build_museum_hazard_gpkg.scan_buildings",
            side_effect=fake_scan,
        ):
            selected = scan_buildings_with_preferences(
                files,
                {"bldg-1": files[1].local_path},
            )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].marker, "authoritative")

    def test_scan_excludes_all_copies_of_unresolved_id(self):
        files = [
            SimpleNamespace(local_path="/cache/13105/tile.gml"),
            SimpleNamespace(local_path="/cache/13106/tile.gml"),
        ]

        def fake_scan(selected_files, progress=False):
            return [SimpleNamespace(
                gml_id="bldg-conflict", source_file=selected_files[0].local_path
            )]

        with patch(
            "Museum.build_museum_hazard_gpkg.scan_buildings",
            side_effect=fake_scan,
        ):
            selected = scan_buildings_with_preferences(
                files, {}, excluded_gml_ids={"bldg-conflict"},
            )
        self.assertEqual(selected, [])


class OutputTests(unittest.TestCase):
    def test_floor_inundation_rules_do_not_invent_upper_floor_height(self):
        status, basis = assess_space_inundation(
            {"floor_min": 2, "is_basement": "no", "floor_elevation_min_m": None},
            "river_flooding", 3.0,
        )
        self.assertEqual(status, "undetermined_floor_elevation")
        status, basis = assess_space_inundation(
            {"floor_min": -1, "is_basement": "yes", "floor_elevation_min_m": None},
            "river_flooding", 0.5,
        )
        self.assertEqual(status, "potentially_exposed")

    def test_space_hazard_table_preserves_river_system_record(self):
        risk = SimpleNamespace(
            risk_type="river_flooding", description_code="arakawa",
            description_label="荒川水系", rank_code="3", rank_label="2-3m",
            depth_m=2.5,
        )
        target = building("b1")
        target.disaster_risks = [risk]
        spaces = [{
            "space_id": "s1", "museum_id": "m1", "space_type": "collection_storage",
            "space_name": "収蔵庫", "presence_status": "yes", "floor_label": "B1",
            "floor_min": -1, "floor_max": -1, "is_basement": "yes",
            "floor_elevation_min_m": None, "floor_elevation_max_m": None,
            "collections_present": "yes", "review_status": "accepted",
        }]
        links = [{
            "museum_id": "m1", "building_gml_id": "b1", "match_status": "confirmed"
        }]
        rows = build_space_hazard_assessments([target], spaces, links)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description_label"], "荒川水系")
        self.assertEqual(rows[0]["exposure_status"], "potentially_exposed")

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
