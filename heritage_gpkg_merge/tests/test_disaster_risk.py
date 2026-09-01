import json
import sqlite3
from pathlib import Path

from shapely.geometry import Point

from heritage_gml.citygml import scan_buildings, write_subset
from heritage_gml.model import CulturalRecord, PlateauFile
from heritage_gml.output import buildings_df, write_gpkg


GML = '''<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel
  xmlns:core="http://www.opengis.net/citygml/2.0"
  xmlns:gml="http://www.opengis.net/gml"
  xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
  xmlns:uro="https://www.geospatial.jp/iur/uro/3.0">
  <core:cityObjectMember>
    <bldg:Building gml:id="b1">
      <gml:name>Risk Building</gml:name>
      <bldg:lod0FootPrint>
        <gml:MultiSurface>
          <gml:surfaceMember>
            <gml:Polygon>
              <gml:exterior><gml:LinearRing>
                <gml:posList>35.0 139.0 35.0 139.001 35.001 139.001 35.001 139.0 35.0 139.0</gml:posList>
              </gml:LinearRing></gml:exterior>
            </gml:Polygon>
          </gml:surfaceMember>
        </gml:MultiSurface>
      </bldg:lod0FootPrint>
      <uro:bldgDisasterRiskAttribute>
        <uro:RiverFloodingRiskAttribute>
          <uro:description codeSpace="../../codelists/RiverFloodingRiskAttribute_description.xml">101</uro:description>
          <uro:rank codeSpace="../../codelists/RiverFloodingRiskAttribute_rank.xml">2</uro:rank>
          <uro:depth uom="m">1.50</uro:depth>
          <uro:adminType codeSpace="../../codelists/RiverFloodingRiskAttribute_adminType.xml">2</uro:adminType>
          <uro:scale codeSpace="../../codelists/RiverFloodingRiskAttribute_scale.xml">1</uro:scale>
          <uro:duration uom="hour">20.0</uro:duration>
        </uro:RiverFloodingRiskAttribute>
      </uro:bldgDisasterRiskAttribute>
      <uro:bldgDisasterRiskAttribute>
        <uro:RiverFloodingRiskAttribute>
          <uro:description codeSpace="../../codelists/RiverFloodingRiskAttribute_description.xml">102</uro:description>
          <uro:rank codeSpace="../../codelists/RiverFloodingRiskAttribute_rank.xml">3</uro:rank>
          <uro:depth uom="m">4.25</uro:depth>
          <uro:adminType codeSpace="../../codelists/RiverFloodingRiskAttribute_adminType.xml">1</uro:adminType>
          <uro:scale codeSpace="../../codelists/RiverFloodingRiskAttribute_scale.xml">2</uro:scale>
          <uro:duration uom="hour">30.0</uro:duration>
        </uro:RiverFloodingRiskAttribute>
      </uro:bldgDisasterRiskAttribute>
      <uro:bldgDisasterRiskAttribute>
        <uro:TsunamiRiskAttribute>
          <uro:description>T1</uro:description>
          <uro:rank>2</uro:rank>
          <uro:depth uom="m">2.0</uro:depth>
        </uro:TsunamiRiskAttribute>
      </uro:bldgDisasterRiskAttribute>
      <uro:bldgDisasterRiskAttribute>
        <uro:HighTideRiskAttribute>
          <uro:description>H1</uro:description>
          <uro:rankOrg>H-RANK</uro:rankOrg>
          <uro:depth uom="cm">250</uro:depth>
        </uro:HighTideRiskAttribute>
      </uro:bldgDisasterRiskAttribute>
      <uro:bldgDisasterRiskAttribute>
        <uro:InlandFloodingRiskAttribute>
          <uro:description>I1</uro:description>
          <uro:rank>1</uro:rank>
          <uro:depth uom="m">0.4</uro:depth>
        </uro:InlandFloodingRiskAttribute>
      </uro:bldgDisasterRiskAttribute>
      <uro:bldgDisasterRiskAttribute>
        <uro:ReservoirFloodingRiskAttribute>
          <uro:description>R1</uro:description>
          <uro:rank>1</uro:rank>
          <uro:depth uom="m">0.8</uro:depth>
        </uro:ReservoirFloodingRiskAttribute>
      </uro:bldgDisasterRiskAttribute>
      <uro:bldgDisasterRiskAttribute>
        <uro:LandSlideRiskAttribute>
          <uro:description>L1</uro:description>
          <uro:areaType>A2</uro:areaType>
        </uro:LandSlideRiskAttribute>
      </uro:bldgDisasterRiskAttribute>
    </bldg:Building>
  </core:cityObjectMember>
</core:CityModel>
'''


def _codelist(path: Path, rows):
    body = ''.join(
        f'<gml:dictionaryEntry><gml:Definition><gml:identifier>{code}</gml:identifier><gml:name>{label}</gml:name></gml:Definition></gml:dictionaryEntry>'
        for code, label in rows
    )
    path.write_text(
        f'<?xml version="1.0"?><gml:Dictionary xmlns:gml="http://www.opengis.net/gml">{body}</gml:Dictionary>',
        encoding='utf-8',
    )


def _fixture(tmp_path):
    gml_dir = tmp_path / 'udx' / 'bldg'
    cl_dir = tmp_path / 'codelists'
    gml_dir.mkdir(parents=True)
    cl_dir.mkdir(parents=True)
    gml = gml_dir / '533946_bldg_6697.gml'
    gml.write_text(GML, encoding='utf-8')
    _codelist(cl_dir / 'RiverFloodingRiskAttribute_description.xml', [('101','River A'),('102','River B')])
    _codelist(cl_dir / 'RiverFloodingRiskAttribute_rank.xml', [('2','0.5m以上3m未満'),('3','3m以上5m未満')])
    _codelist(cl_dir / 'RiverFloodingRiskAttribute_adminType.xml', [('1','国'),('2','都道府県')])
    _codelist(cl_dir / 'RiverFloodingRiskAttribute_scale.xml', [('1','L1（計画規模）'),('2','L2（想定最大規模）')])
    pf = PlateauFile(city_code='13106', city_name='台東区', code='533946', url='', local_path=str(gml))
    return pf


def _selected():
    return {'b1': {
        'complex_ids': [], 'complex_names': [], 'record_ids': ['r1'],
        'record_names': ['Risk Building'], 'record_types': ['建造物'],
        'entity_classes': ['building_direct'], 'designation_level_codes': ['municipal'],
        'designation_status_codes': ['designated'], 'heritage_type_major_codes': ['tangible'],
        'heritage_type_details': ['建造物'], 'methods': ['point_in_building'],
    }}


def test_scan_buildings_extracts_all_six_disaster_risk_types_and_codelists(tmp_path):
    pf = _fixture(tmp_path)
    buildings = scan_buildings([pf])
    assert len(buildings) == 1
    b = buildings[0]
    assert len(b.disaster_risks) == 7
    assert {r.risk_type for r in b.disaster_risks} == {
        'river_flooding', 'tsunami', 'high_tide', 'inland_flooding',
        'reservoir_flooding', 'landslide'
    }
    rivers = [r for r in b.disaster_risks if r.risk_type == 'river_flooding']
    assert rivers[0].description_code == '101'
    assert rivers[0].description_label == 'River A'
    assert rivers[0].rank_label == '0.5m以上3m未満'
    assert rivers[0].admin_type_label == '都道府県'
    assert rivers[0].scale_label == 'L1（計画規模）'
    assert rivers[0].depth_m == 1.5
    assert rivers[1].duration_h == 30.0
    high = next(r for r in b.disaster_risks if r.risk_type == 'high_tide')
    assert high.depth_value == 250.0
    assert high.depth_m == 2.5


def test_building_polygon_contains_query_friendly_disaster_risk_summary(tmp_path):
    pf = _fixture(tmp_path)
    b = scan_buildings([pf])[0]
    gdf = buildings_df([b], _selected())
    row = gdf.iloc[0]
    assert row['disaster_risk_count'] == 7
    assert row['river_flood_count'] == 2
    assert row['river_flood_max_depth_m'] == 4.25
    assert row['river_flood_max_duration_h'] == 30.0
    assert row['river_flood_descriptions'] == 'River A;River B'
    assert row['river_flood_ranks'] == '0.5m以上3m未満;3m以上5m未満'
    assert row['tsunami_max_depth_m'] == 2.0
    assert row['high_tide_max_depth_m'] == 2.5
    assert row['landslide_count'] == 1
    payload = json.loads(row['disaster_risks_json'])
    assert len(payload) == 7
    assert payload[0]['risk_type'] == 'river_flooding'


def test_gpkg_has_normalized_disaster_risk_table_and_subset_gml_preserves_source_attributes(tmp_path):
    pf = _fixture(tmp_path)
    b = scan_buildings([pf])[0]
    r = CulturalRecord(
        source_file='municipal.csv', record_id='r1', name='Risk Building',
        municipality='台東区', municipality_code='13106', type='建造物',
        geometry=Point(139.0005, 35.0005), matched_building_ids=['b1'],
        spatial_match_status='building_matched',
    )
    out = tmp_path / '13106_heritage.gpkg'
    write_gpkg(out, [r], [b], _selected(), [], [], [], [], [], [])
    with sqlite3.connect(out) as conn:
        rows = conn.execute(
            'SELECT risk_type, description_code, description_label, depth_m, duration_h '
            'FROM plateau_disaster_risk ORDER BY risk_index'
        ).fetchall()
        assert len(rows) == 7
        assert rows[0] == ('river_flooding', '101', 'River A', 1.5, 20.0)
        kind = conn.execute(
            "SELECT data_type FROM gpkg_contents WHERE table_name='plateau_disaster_risk'"
        ).fetchone()[0]
        assert kind == 'attributes'

    subset = tmp_path / '13106_heritage_buildings.gml'
    written = write_subset(subset, [pf], _selected())
    assert written == {'b1'}
    text = subset.read_text(encoding='utf-8')
    assert 'RiverFloodingRiskAttribute' in text
    assert 'LandSlideRiskAttribute' in text
    assert '<uro:depth uom="m">1.50</uro:depth>' in text or 'uom="m">1.50<' in text
