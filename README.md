# PLATEAU Heritage-GML Extractor v0.5.5

東京都を中心とする文化財オープンデータと Project PLATEAU の CityGML 建築物データを照合し、文化財の位置・建築物形状・複合文化財・災害リスクを GeoPackage / GML に統合するための Python ツール群です。

本リポジトリでは、自治体単位の文化財–PLATEAU照合に加え、東京都全域統合、ハザードデータ追加、公開用データ生成、Summary Results 用の集計・地図出力までを扱います。

---

## 公開データ

大容量の解析用完全版をそのまま配布するのではなく、一般的な GIS / Web GIS で利用しやすい派生データを `public_data/` で公開します。

### `public_data/13_heritage_public.gpkg`

文化財を中心とした公開用 GeoPackage です。

主な収録レイヤ・テーブル：

- `heritage_records` — 文化財レコード
- `heritage_points` — 文化財位置
- `heritage_buildings_point` — PLATEAU 建築物の代表点
- `heritage_buildings_footprint` — 文化財に対応した PLATEAU 建築物形状
- `heritage_buildings_footprint_riskwide` — 災害リスク属性を付与した建築物形状
- `heritage_building_complexes` — 複合文化財
- `heritage_building_links` — 文化財と建築物の対応関係
- `heritage_complex_members`
- `heritage_complex_records`
- `heritage_complex_summary`
- `heritage_disaster_risk` — 文化財単位の災害リスク情報
- `heritage_disaster_metadata`
- `hazard_source_manifest`
- `source_license`

PLATEAU CityGML から抽出した詳細な Building 災害リスク原情報 `plateau_disaster_risk` と、東京都全域の大容量ハザード原レイヤは公開版には含めません。

### `public_data/hazard_map.gpkg`

文化財との位置関係を GIS 上で確認するための代表的なハザード地図を収録します。

- `hazard_region_risk` — 地震に関する地域危険度
- `hazard_fire_spread_town` — 地震時延焼危険度
- `hazard_sediment_warning_a33_polygon` — 土砂災害警戒区域
- `hazard_sabo_designated_a52_polygon` — 砂防指定地

解析用完全版に含まれる震度、液状化、河川別浸水、高潮、津波等の大容量レイヤは含めません。

### GeoJSON

主要レイヤは WGS84（EPSG:4326）の GeoJSON でも提供します。

```text
public_data/geojson/
├── heritage_buildings_risk.geojson
├── heritage_buildings_footprint_risk.geojson
├── heritage_complexes.geojson
└── heritage_source_points.geojson
```

### 出典・ライセンス

公開データに使用した各原データの出典・ライセンスは `public_data/SOURCE_LICENSES.csv` に整理しています。GeoPackage 内にも `source_license` と `hazard_source_manifest` を収録します。

個別データを再配布・二次利用する場合は、`SOURCE_LICENSES.csv` と各データ提供者の最新の利用条件を確認してください。

### 完全版データ

解析用完全版 `13_heritage_hazards.gpkg` は東京都全域の詳細なハザードデータを含むため約 12 GB となり、GitHub では公開していません。

同一性確認用として以下をリポジトリに保持します。

```text
output/13_heritage_hazards.sha256
output/13_heritage_hazards_fileinfo.txt
```

公開データは `tools/build_public_release.py` により完全版から再生成できます。

---

## 基本ワークフロー

```text
文化財オープンデータ
        │
        ▼
正規化・分類
        │
        ▼
PLATEAU CityGML と照合
        │
        ├─ heritage Building
        ├─ Building Complex
        └─ standalone Point
        │
        ▼
自治体別 GeoPackage
        │
        ▼
都道府県単位 GeoPackage 統合
        │
        ▼
東京都・国土数値情報等のハザード追加
        │
        ▼
13_heritage_hazards.gpkg
        │
        ├─ public release
        └─ Summary Results
```

---

## インストール

Python 3.10 以上。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

確認：

```bash
heritage-gml --version
heritage-gml --help
heritage-gpkg-merge --help
```

ハザード処理・Summary Results には主に次を使用します。

```bash
python -m pip install requests pandas geopandas shapely pyogrio matplotlib contextily
```

---

## 文化財–PLATEAU 照合

通常実行：

```bash
heritage-gml --area-code 13106 --data-dir ./13Tokyo/gml_input
```

- 2桁コード：都道府県
- 5桁コード：市区町村
- チェックディジットは含めません

ローカル PLATEAU を完全オフラインで使用：

```bash
heritage-gml \
  --area-code 13106 \
  --data-dir ./13Tokyo/gml_input \
  --plateau-source local \
  --plateau-local-dir /path/to/plateau
```

API キャッシュを明示的に破棄して再取得：

```bash
heritage-gml \
  --area-code 13106 \
  --data-dir ./13Tokyo/gml_input \
  --refresh-plateau-cache
```

### 照合原則

文化財レコードは個別に処理します。

- Point が Building footprint の内部または境界上 → `point_in_building`
- `building_direct` のみ PLATEAU Building 名称・住所の完全正規化一致を追加候補とする
- buffer、検索半径、nearest neighbour による Building 推定は行わない
- Complex 内の別レコードで確定した Building を他レコードへ自動伝播しない
- `movable` は意味分類であり、特別なグループ化処理を意味しない

### Building Complex

Complex は直接照合された Building のみをメンバーとします。

```text
Heritage Complex
├─ Building A Polygon
├─ Building B Polygon
└─ Building C Polygon
```

`heritage_building_complexes` は 1 Complex = 1 MultiPolygon です。dissolve / union / buffer / convex hull / 最近傍補完は行いません。

---

## 自治体別出力

例：

```text
output/13106/
├── 13106_heritage_buildings.gml
├── 13106_heritage.gpkg
├── 13106_heritage_entities.json
├── 13106_heritage_entities.xml
├── 13106_cultural_records_normalized.csv
├── 13106_heritage_building_links.csv
├── 13106_heritage_complex_summary.csv
├── 13106_heritage_complex_members.csv
├── 13106_heritage_complex_records.csv
├── 13106_heritage_point_features.csv
├── 13106_heritage_unresolved_entities.csv
└── 13106_run_summary.json
```

### `<code>_heritage.gpkg`

主な Spatial layer：

- `heritage_records`
- `heritage_buildings_footprint`
- `heritage_building_complexes`
- `heritage_points`

主な Attribute table：

- `plateau_disaster_risk`
- `heritage_building_links`
- `heritage_complex_summary`
- `heritage_complex_members`
- `heritage_complex_records`
- `heritage_unresolved_entities`

---

## PLATEAU Building 災害リスク属性

`uro:bldgDisasterRiskAttribute` を Building 読み込み時に取得し、文化財照合とは独立した Building 属性として保持します。

対応類型：

- `uro:RiverFloodingRiskAttribute` → `river_flooding`
- `uro:TsunamiRiskAttribute` → `tsunami`
- `uro:HighTideRiskAttribute` → `high_tide`
- `uro:InlandFloodingRiskAttribute` → `inland_flooding`
- `uro:ReservoirFloodingRiskAttribute` → `reservoir_flooding`
- `uro:LandSlideRiskAttribute` → `landslide`

Building : risk = 1:N は `plateau_disaster_risk` に保持します。Building Polygon 上の集約値は分析用派生属性であり、正本はこの 1:N テーブルと元 CityGML です。

---

## 都道府県 GPKG 統合

```bash
heritage-gpkg-merge \
  --input-root ./output \
  --pref-code 13
```

または：

```bash
python merge_heritage_gpkg.py \
  --input-root ./output \
  --pref-code 13
```

出力：

```text
output/
├── 13_heritage.gpkg
├── 13_heritage_merge_report.csv
└── 13_heritage_merge_manifest.json
```

統合時も Complex の MultiPolygon part を変更せず、dissolve / union / buffer 等を行いません。

---

## 東京都ハザード統合

`tools/add_tokyo_hazard_layers.py` は既存 GeoPackage をコピーし、指定したハザードデータセットを追加します。

例：

```bash
python tools/add_tokyo_hazard_layers.py \
  --input ./output/13_heritage_enriched.gpkg \
  --output ./output/13_heritage_hazards.gpkg \
  --cache ./.cache/hazard_sources
```

一部のデータセットだけを追加：

```bash
python tools/add_tokyo_hazard_layers.py \
  --input ./output/13_heritage_enriched.gpkg \
  --output ./output/13_heritage_hazards.gpkg \
  --datasets region_risk,fire_spread,seismic,liquefaction
```

主なハザード：

- 地震に関する地域危険度
- 地震時延焼危険度
- 震度・液状化
- 東京都「浸水予想区域図」由来の流域別浸水
- 高潮
- 津波
- 国土数値情報の土砂災害系（A33 / A46 / A47 / A52）
- 国土数値情報 A31a 洪水浸水想定区域（河川単位）

### 東京都「浸水予想区域図」と A31a の扱い

両者は置き換え関係ではありません。

東京都の流域別「浸水予想区域図」は、都管理河川を中心とする既存レイヤとして保持します。国管理の大河川については、国土数値情報 A31a を別レイヤとして追加します。

既存例：

```text
hazard_inundation_神田川流域
hazard_inundation_石神井川及び白子川流域
hazard_inundation_野川_仙川_入間川_谷沢川及び丸子川流域
...
```

A31a 追加例：

```text
hazard_inundation_a31a_荒川
hazard_inundation_a31a_多摩川
```

### A31a：荒川・多摩川

荒川・多摩川は国管理河川のため、東京都作成種別 `13` の A31a ではなく、**関東地方整備局・作成種別コード `83`** の 2025 年度版を使用します。

対象アーカイブ：

```text
A31a-25_83_10_GEOJSON.zip
```

データセットページ：

https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A31a-2025.html

既定では想定最大規模の `荒川` と `多摩川` を抽出します。

```bash
python tools/add_tokyo_hazard_layers.py \
  --input /path/to/13_heritage_hazards.gpkg \
  --output /path/to/13_heritage_hazards_a31a.gpkg \
  --cache ./.cache/hazard_sources \
  --datasets a31a \
  --a31a-river 荒川 \
  --a31a-river 多摩川
```

ローカル取得済み ZIP を使う場合：

```bash
python tools/add_tokyo_hazard_layers.py \
  --input /path/to/13_heritage_hazards.gpkg \
  --output /path/to/13_heritage_hazards_a31a.gpkg \
  --datasets a31a \
  --a31a-archive /path/to/A31a-25_83_10_GEOJSON.zip
```

A31a レイヤには元の `A31a_201`～`A31a_205` を保持したうえで、次の正規化属性を追加します。

```text
river_code
river_name
river_manager_code
river_manager
scenario
depth_rank_code
depth_class_native
depth_min_m
depth_max_m
depth_class_summary
source_dataset
source_scope
source_year
source_url
source_license
```

`depth_class_summary` は Summary Results 用に次へ正規化します。

```text
0–0.5 m
0.5–3 m
3–5 m
5 m以上
```

A31a 本来の `5–10 m / 10–20 m / 20 m以上` は `depth_class_native` に保持します。

---

## Summary Results

完全版 `13_heritage_hazards.gpkg` を対象に、集計表と地図を生成します。

### 1. ソース確認

```bash
python tools/profile_summary_source.py \
  /path/to/13_heritage_hazards.gpkg
```

### 2. 集計

```bash
python tools/build_summary_results.py \
  /path/to/13_heritage_hazards.gpkg
```

主な出力：

```text
summary_results/
├── tables/
├── cache/
├── metadata/
└── figures/
```

### 3. Summary map

```bash
python tools/render_summary_maps.py \
  /path/to/13_heritage_hazards.gpkg \
  --stage overview
```

```bash
python tools/render_summary_maps.py \
  /path/to/13_heritage_hazards.gpkg \
  --stage detail
```

地図出力は次の3フォルダを使用します。

```text
summary_results/figures/
├── overview/
├── detail/
└── city/
```

- `overview/` — 東京都本土部・伊豆諸島・小笠原諸島等の全体図
- `detail/` — 指定地点周辺の詳細図。河川浸水は `detail/inundation_center/` を使用
- `city/` — 市区町村単位の追加図

Detail map の背景には地理院タイル（淡色地図）を使用します。

### 表示区分

想定震度：

```text
5弱未満 / 5弱 / 5強 / 6弱 / 6強以上
```

低震度を黄色、高震度を紫系で表示します。

浸水深：

```text
0 / 0–0.5 m / 0.5–3 m / 3–5 m / 5 m以上
```

浅い側を淡色、深い側を濃青で表示します。文化財 point は浸水区域内を赤、区域外を黒で区別します。

地震時延焼危険度は `hazard_fire_spread_town` の実データ階級を使用します。

### 任意の浸水図

`tools/render_inundation_map.py` は、区市町村名または中心座標を指定して河川別浸水図を追加生成するための独立ツールです。

市区町村：

```bash
python tools/render_inundation_map.py \
  /path/to/13_heritage_hazards.gpkg \
  --city 国分寺 \
  --hazard auto
```

中心座標：

```bash
python tools/render_inundation_map.py \
  /path/to/13_heritage_hazards.gpkg \
  --center 35.68126 139.76671 \
  --zoom 16 \
  --hazard auto
```

利用可能な浸水レイヤ一覧：

```bash
python tools/render_inundation_map.py \
  /path/to/13_heritage_hazards.gpkg \
  --list-hazards
```

---

## Classified cultural inputs

Tokyo Heritage Data Tools `heritage-classify` が生成する以下の任意列を受け入れます。

```text
designation_level_code
designation_level_ja
designation_status_code
designation_status_ja
heritage_type_major_code
heritage_type_major_ja
heritage_type_detail
classification_confidence
```

これらは record-level output に保持され、Building / Building Complex に集約されますが、Building matching、Complex grouping、geometry construction、buffering、nearest-neighbour 判定には使用しません。

---

## v0.5.5

- `heritage-gml --version` を軽量化
- `heritage_gml.__version__` と distribution metadata を `0.5.5` に統一
- companion Heritage JSON/XML の version を package version へ統一
- PLATEAU API キャッシュ復旧、災害リスク属性、文化財分類属性を維持
- matching / Complex / geometry 規則は変更しない

---

## ライセンスと注意

本ツールのコードライセンスと、入力・派生データのライセンスは別に扱ってください。

特に、東京都オープンデータ、Project PLATEAU、国土数値情報、国土地理院等のデータを再配布・公開する場合は、各原データの利用条件と出典表示を確認してください。
