import sqlite3
from pathlib import Path
import pandas as pd
from lxml import etree

from heritage_gml.classification_patch import patch_gpkg, patch_gml


def _classified(path):
    pd.DataFrame([{
        "record_id":"0000000001", "name":"Test", "owner":"Owner", "category":"都指定文化財", "type":"建造物", "municipality_code":"13106",
        "designation_level_code":"prefectural", "designation_level_ja":"都",
        "designation_status_code":"designated", "designation_status_ja":"指定",
        "heritage_type_major_code":"tangible", "heritage_type_major_ja":"有形文化財",
        "heritage_type_detail":"建造物", "classification_confidence":"high",
    }]).to_csv(path,index=False,encoding="utf-8-sig")


def _db(path):
    c=sqlite3.connect(path)
    c.execute('CREATE TABLE heritage_records (record_id TEXT, name TEXT, owner TEXT, category TEXT, type TEXT, municipality_code TEXT, complex_id TEXT)')
    c.execute('INSERT INTO heritage_records VALUES (?,?,?,?,?,?,?)',("1","Test","Owner","都指定文化財","建造物","13106","C1"))
    c.execute('CREATE TABLE heritage_building_links (record_id TEXT, name TEXT, building_gml_id TEXT)')
    c.execute('INSERT INTO heritage_building_links VALUES (?,?,?)',("1","Test","b1"))
    c.execute('CREATE TABLE heritage_complex_summary (complex_id TEXT)')
    c.execute('INSERT INTO heritage_complex_summary VALUES (?)',("C1",))
    c.execute('CREATE TABLE heritage_buildings_footprint (record_ids TEXT)')
    c.execute('INSERT INTO heritage_buildings_footprint VALUES (?)',("1",))
    c.commit(); c.close()


def test_patch_gpkg_attributes_only(tmp_path):
    gpkg=tmp_path/"x.gpkg"; csvp=tmp_path/"classified.csv"
    _db(gpkg); _classified(csvp)
    stats=patch_gpkg(gpkg,[csvp],in_place=True)
    assert stats["heritage_records_matched"]==1
    c=sqlite3.connect(gpkg)
    assert c.execute('SELECT heritage_type_major_code FROM heritage_records').fetchone()[0]=="tangible"
    assert c.execute('SELECT heritage_type_majors FROM heritage_buildings_footprint').fetchone()[0]=="tangible"
    c.close()


def test_patch_gml_from_patched_links(tmp_path):
    gpkg=tmp_path/"x.gpkg"; csvp=tmp_path/"classified.csv"; gml=tmp_path/"x.gml"
    _db(gpkg); _classified(csvp); patch_gpkg(gpkg,[csvp],in_place=True)
    gml.write_text('''<?xml version="1.0" encoding="UTF-8"?>\n<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" xmlns:gml="http://www.opengis.net/gml" xmlns:bldg="http://www.opengis.net/citygml/building/2.0"><core:cityObjectMember><bldg:Building gml:id="b1"/></core:cityObjectMember></core:CityModel>''',encoding="utf-8")
    stats=patch_gml(gml,gpkg,in_place=True)
    assert stats["buildings_updated"]==1
    root=etree.parse(str(gml)).getroot()
    vals=[]
    for e in root.iter():
        if e.get("name")=="heritageTypeMajors":
            vals.extend([x.text for x in e.iter() if x.tag.endswith("value")])
    assert "tangible" in vals
