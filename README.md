# PLATEAU Heritage-GML Extractor v0.2.0

Project PLATEAU の建築物 CityGML と、事前取得済みの文化財 CSV/JSON/GeoJSON を照合し、
文化財に関係する `bldg:Building` を抽出する汎用 Python CLI です。

コード内に特定の都道府県名・自治体名・自治体コードは固定していません。

## 1. 入力文化財データ

文化財データは実行前に取得済みで、`--data-dir` に置かれていることを前提とします。

対応形式:

- CSV
- JSON
- GeoJSON
- CSV/JSON中のWKT geometry

本プログラムは文化財データ取得APIを呼びません。
データが最新版かどうかも検証しません。

一般的な列名は自動認識します。独自列名は `config.example.yml` の `cultural.columns`
または `file_overrides` で対応づけできます。

## 2. PLATEAU入力

基本はPLATEAU配信サービスAPIです。

- `/datacatalog/plateau-datasets` から公開自治体を列挙
- 文化財の点・指定範囲bboxを `r:` 条件として使用
- `types=bldg` で建築物CityGMLだけ取得

文化財位置に任意の距離バッファは加えません。

`--plateau-source local` は次の用途の任意フォールバックです。

- 特定年度の固定
- API障害時
- 加工済みCityGMLの再利用
- 同一CityGMLを大量反復処理する場合

5桁コードならローカルGMLだけで処理できます。
2桁コードを完全オフラインで一括処理する場合は、PLATEAU
`/datacatalog/plateau-datasets` のJSONを保存し、
設定の `plateau.catalog_file` に指定してください。

## 3. 地域コード

以下では `PREF_CODE` に2桁、`MUNICIPALITY_CODE` に5桁のコードを設定して使用します。

```bash
heritage-gml --area-code "$PREF_CODE" --data-dir ./cultural_data
heritage-gml --area-code "$MUNICIPALITY_CODE" --data-dir ./cultural_data
```

- 2桁: 都道府県コード。PLATEAUで `bldg` が公開されている都道府県内自治体を一括処理
- 5桁: 市区町村コード。当該自治体だけ処理
- チェックディジットは含めない
- 2桁は `01`〜`47`

## 4. 2桁一括処理時の文化財ファイル振り分け

判定優先順位:

1. レコード中の5桁自治体コード
2. ファイル名先頭の5桁自治体コード
3. 市区町村名列
4. 住所中の市区町村名

都道府県全域CSVと自治体別CSVを同じディレクトリに置けます。

## 5. Heritage Complex

レコードのグループ化は以下の優先順位です。

1. 場所名称
2. 所有者 + 住所
3. 住所
4. 空間的位置

第4段階は固定距離を導入せず、同一点または交差するgeometryのみを同一complexとします。
不確実な「近傍だから同一施設」という推測は外部QAに委ねます。

## 6. Building照合

Buildingは次の根拠で抽出します。

1. 指定範囲polygonとPLATEAU Building footprintが交差
2. 文化財pointがBuilding footprint内または境界上
3. Heritage Complex名とPLATEAU建物名称・住所が一致
4. 住所一致
5. 寺社の場合、すでに同一住所でアンカーされたBuilding群の用途属性を補助利用

固定半径、最近傍建物への強制割当は行いません。

位置が代表点で建物外、PLATEAU側に名称・住所もない場合は
`unresolved` として残します。

## 7. 動産文化財

絵画、彫刻、工芸品、書跡、典籍、古文書、考古資料、歴史資料など、明確に動産と判定できる類型は
Building抽出の主判定には使用しません。

同一所在地・同一Heritage Complexとして一覧化し、そのcomplexにBuildingが
抽出されていれば次に参照を記録します。

- `heritage_movable_items.csv`
- `heritage_complexes.json`
- `heritage_complexes.xml`
- `heritage.gpkg` の `movable_items`

複数Building complexの場合、プロトタイプではcomplexの全Building `gml:id` を参照します。
個々の動産を特定Buildingへ絞る作業は外部チェック対象です。

## 8. 出力

自治体ごとに次を生成します。

```text
output/
  <5桁自治体コード>/
    heritage_buildings.gml
    heritage_complexes.json
    heritage_complexes.xml
    heritage.gpkg
    heritage_building_links.csv
    heritage_complex_summary.csv
    heritage_movable_items.csv
    heritage_unresolved_complexes.csv
    cultural_records_normalized.csv
    input_issues.csv
    plateau_files.csv
    plateau_query_issues.csv
    heritage_buildings.geojson
    heritage_records.geojson
    heritage_complexes.geojson
    run_summary.json
```

2桁一括処理ではさらに:

```text
area_<2桁コード>_summary.csv
area_<2桁コード>_summary.json
```

を生成します。

## 9. `heritage_buildings.gml`

本体GMLは新規geometryを再構築せず、元PLATEAUの
`cityObjectMember` / `bldg:Building` をコピーします。

したがって元データに存在する範囲で:

- LOD0
- LOD1
- LOD2
- 建築物属性

を保持します。

付加属性:

- `heritageComplexId`
- `heritageComplexName`
- `heritageMatchMethod`

CityGML 2.0/3.0 の generics namespace は元CityModelのnamespaceから切り替えます。

### 元PLATEAUとの重ね合わせ

LOD2を保持すること自体は問題ありません。
ただし元PLATEAU Buildingとsubset Buildingは同一座標・同一面なので、
両者を3Dで同時に塗りつぶすと **Z-fighting** が起こり得ます。

用途を分けます。

- `heritage_buildings.gml`: 保存・交換・LOD保持
- `heritage.gpkg` / `heritage_buildings.geojson`: PLATEAU全体への重畳・ハイライト・QA

## 10. Companion Heritage-GML

`heritage_complexes.xml/json` は `HeritageComplex` と文化財アイテム、
PLATEAU Buildingへの参照を表す別モデルです。

XML namespace:

```text
urn:heritage-gml:prototype:0.2
```

これは公式CityGML ADEではありません。
本体はPLATEAU互換subset GMLとし、complex構造を別XML/JSONに分離しています。

## 11. GeoPackage

`heritage.gpkg` のspatial layers:

- `heritage_records`
- `heritage_buildings`
- `heritage_complexes`

relational tables:

- `building_links`
- `complex_summary`
- `movable_items`
- `unresolved_complexes`

QGIS等で元PLATEAUと比較する場合は `heritage_buildings` をハイライト表示してください。

## 12. インストール

Python 3.10以上。

```bash
cd plateau_heritage_gml
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
```

## 13. 実行

### 市区町村単独

```bash
heritage-gml \
  --area-code "$MUNICIPALITY_CODE" \
  --data-dir ./cultural_data
```

### 都道府県一括

```bash
heritage-gml \
  --area-code "$PREF_CODE" \
  --data-dir ./cultural_data
```

### 入力文化財データだけ確認

PLATEAU Buildingをダウンロードしません。

```bash
heritage-gml \
  --area-code "$PREF_CODE" \
  --data-dir ./cultural_data \
  --dry-run
```

### ローカルPLATEAU

```bash
heritage-gml \
  --area-code "$MUNICIPALITY_CODE" \
  --data-dir ./cultural_data \
  --plateau-source local \
  --plateau-local-dir /path/to/plateau
```

### 独自列マッピング

```bash
heritage-gml \
  --area-code "$PREF_CODE" \
  --data-dir ./cultural_data \
  --config config.yml
```

## 14. 外部チェック

採否修正UIや手動除外機能は本プログラムには含めません。

チェック用成果物:

- `heritage_building_links.csv`
- `heritage_unresolved_complexes.csv`
- `heritage.gpkg`
- GeoJSON

これらを別プログラムまたは人力で確認する構成です。

## 15. 注意

- PLATEAUの建物名称・住所・用途属性は自治体・年度で収録状況が異なります。
- 文化財の代表点が対象Building上にない場合があります。
- 固定距離を使わないため、不確実なケースは意図的に未解決として残ります。
- 指定範囲polygonが存在する場合、pointより確度の高い照合ができます。
- 文化財CSV/JSONの鮮度はユーザー側で管理してください。
