from pathlib import Path
import pandas as pd

from heritage_gml.cultural import load_records_for_city
from heritage_gml.model import PlateauCity, BuildingRecord
from heritage_gml.matching import match_city
from heritage_gml.output import records_df, buildings_df


def _cfg():
    return {
        "type_class_map": {"建造物": "building_direct", "史跡": "point"},
        "default_entity_class": "point",
        "file_overrides": {}, "geometry_columns": [], "columns": {},
        "default_crs": "EPSG:4326",
    }


def test_classified_columns_load_and_pass_to_record_gpkg(tmp_path):
    p = tmp_path / "municipal_classified.csv"
    pd.DataFrame([{
        "name":"Test", "municipality":"台東区", "municipality_code":"13106",
        "type":"建造物", "category":"区指定文化財", "designation":"municipal",
        "longitude":139.79, "latitude":35.71,
        "designation_level_code":"municipal", "designation_level_ja":"区市町村",
        "designation_status_code":"designated", "designation_status_ja":"指定",
        "heritage_type_major_code":"tangible", "heritage_type_major_ja":"有形文化財",
        "heritage_type_detail":"建造物", "classification_confidence":"high",
    }]).to_csv(p, index=False, encoding="utf-8-sig")
    city=PlateauCity(pref_code="13", pref="東京都", city_code="13106", city="台東区", year="x")
    recs, issues=load_records_for_city([p], city, _cfg())
    assert not issues
    assert len(recs)==1
    r=recs[0]
    assert r.designation_level_code=="municipal"
    assert r.heritage_type_major_code=="tangible"
    df=records_df(recs)
    assert df.iloc[0]["heritage_type_detail"]=="建造物"


def test_classification_does_not_change_matching_semantics():
    from shapely.geometry import Point, Polygon
    from heritage_gml.model import CulturalRecord
    r=CulturalRecord(
        source_file="x", record_id="1", name="A", municipality_code="13106",
        type="史跡", entity_class="point", geometry=Point(0.5,0.5),
        designation_level_code="municipal", heritage_type_major_code="monument",
        heritage_type_detail="史跡",
    )
    r.complex_id="13106-HG00001"; r.complex_name="A"; r.complex_grouping_method="singleton"
    b=BuildingRecord(gml_id="b1", source_file="x.gml", city_code="13106", file_code="x", geometry=Polygon([(0,0),(1,0),(1,1),(0,1)]))
    result=match_city([r],[b],{"point_in_building":True,"building_direct_exact_name":True,"building_direct_exact_address":True,"match_shared_complex_coordinates":False})
    assert r.matched_building_ids==["b1"]
    assert result["selected"]["b1"]["heritage_type_major_codes"]==["monument"]
    bdf=buildings_df([b],result["selected"])
    assert bdf.iloc[0]["heritage_type_majors"]=="monument"
