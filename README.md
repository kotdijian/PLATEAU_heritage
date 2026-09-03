# PLATEAU Heritage-GML Extractor v0.5.5

<!-- PUBLIC-DATA-START -->

## 公開データ

本リポジトリでは、東京都の文化財オープンデータと Project PLATEAU の
CityGML 建築物データを対応付け、文化財の位置・建築物形状・災害リスク情報を
統合した公開用GISデータを提供します。

大容量の解析用データをそのまま配布するのではなく、一般的なGISやWeb GISで
利用しやすい派生データを公開対象としています。

### 公開ファイル

#### `public_data/13_heritage_public.gpkg`

文化財を中心とした公開用 GeoPackage です。

主な収録レイヤ：

- `heritage_records` — 文化財レコード
- `heritage_points` — 文化財位置
- `heritage_buildings_point` — PLATEAU建築物の代表点
- `heritage_buildings_footprint` — 文化財に対応したPLATEAU建築物形状
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

PLATEAU CityGMLから抽出した詳細な災害リスク原情報
`plateau_disaster_risk` は公開版には含めていません。

また、東京都全域の震度・液状化・浸水・津波等の大容量ハザード原データも
このGeoPackageには含めていません。

#### `public_data/hazard_map.gpkg`

文化財との位置関係をGIS上で確認するための代表的なハザード地図を収録します。

収録レイヤ：

- `hazard_region_risk` — 地震に関する地域危険度
- `hazard_fire_spread_town` — 火災危険度
- `hazard_sediment_warning_a33_polygon` — 土砂災害警戒区域
- `hazard_sabo_designated_a52_polygon` — 砂防指定地

解析用完全版に含まれる震度8地震シナリオ、液状化5地震シナリオ、
河川別浸水データ、高潮、津波等の大容量レイヤは含めていません。

### GeoJSON

Web GISや簡易なデータ利用のため、主要レイヤを
WGS84（EPSG:4326）のGeoJSONでも提供します。

```text
public_data/geojson/
├── heritage_buildings_risk.geojson
├── heritage_buildings_footprint_risk.geojson
├── heritage_complexes.geojson
└── heritage_source_points.geojson
```

- `heritage_buildings_risk.geojson`  
  文化財に対応した建築物の代表点と災害リスク属性

- `heritage_buildings_footprint_risk.geojson`  
  文化財建築物のポリゴンと災害リスク属性

- `heritage_complexes.geojson`  
  複合文化財の空間情報

- `heritage_source_points.geojson`  
  元の文化財位置情報

### 出典・ライセンス

公開データに使用した各原データの出典・ライセンスは
`public_data/SOURCE_LICENSES.csv` に整理しています。

GeoPackage内にも以下のメタデータテーブルを収録しています。

- `source_license`
- `hazard_source_manifest`

個別データを再配布・二次利用する場合は、
`SOURCE_LICENSES.csv` と各原データ提供者の最新の利用条件を確認してください。

### 完全版データ

解析過程で生成する完全版 `13_heritage_hazards.gpkg` は、
東京都全域の詳細なハザードデータを含むため約12 GBとなり、
GitHubでは公開していません。

GitHub上のデータは、この完全版GeoPackageから公開に必要な情報を抽出した
派生データです。

完全版ファイルの同一性確認用として、

```text
output/13_heritage_hazards.sha256
output/13_heritage_hazards_fileinfo.txt
```

をリポジトリに保持します。

### 公開データ構成

```text
public_data/
├── 13_heritage_public.gpkg
├── hazard_map.gpkg
├── SOURCE_LICENSES.csv
└── geojson/
    ├── heritage_buildings_risk.geojson
    ├── heritage_buildings_footprint_risk.geojson
    ├── heritage_complexes.geojson
    └── heritage_source_points.geojson
```

公開データは `tools/build_public_release.py` により
`13_heritage_hazards.gpkg` から再生成できます。

<!-- PUBLIC-DATA-END -->

---

## 開発・ツール

事前取得済みの文化財 CSV / JSON / GeoJSON と Project PLATEAU の `bldg:Building` CityGML を照合し、
文化財 Building、Building Complex、文化財レコードの位置情報を **GML + GeoPackage** として出力する Python CLI です。

コード内に特定の都道府県名・自治体名・自治体コードは固定していません。


## v0.5.5 の修正: CLI起動とバージョン整合性

- `heritage-gml --version` は `pipeline` / pandas / pyproj / geopandas を読み込まず、軽量な `argparse` と package version だけで即時終了します。
- `heritage_gml.__version__`、distribution metadata (`pyproject.toml`) を `0.5.5` に統一しました。
- companion Heritage JSON/XML の `version` も package version を参照し、ハードコードされた旧版番号が残らないようにしました。
- source ZIPには `build/`, `dist/`, `*.egg-info`, `*.dist-info`, `__pycache__` を含めません。
- v0.5.4 の自治体単位PLATEAUキャッシュ復旧、v0.5.3 の災害リスク属性、v0.5.2 の文化財分類属性を維持します。matching / Complex / geometry規則は変更しません。

## v0.5.5 クリーンインストール（既存リポジトリでの推奨）

Git作業ツリー、`site-packages`、旧 `*.egg-info` のバージョン混在を避けるため、次の順序を推奨します。

1. 仮想環境を有効化し、実行Pythonを確認する。
2. リポジトリ直下の生成物 `build/`, `dist/`, `plateau_heritage_gml.egg-info/` を削除する。
3. `site-packages` の旧 `plateau_heritage_gml-*.dist-info` / package directory を削除する。
4. source ZIPの0.5.5コードで作業ツリーを更新する。
5. 0.5.5 wheelを `--no-deps` でインストールする。
6. プロジェクトルートと `/tmp` の両方で package version / distribution metadata が0.5.5で一致することを確認する。

`heritage-gml --version` はv0.5.5では重い解析依存関係を読み込まずに終了します。

## v0.5.4 の修正: PLATEAUキャッシュ復旧と実行可視化

v0.5.4 は、v0.5.2 の文化財分類属性対応と v0.5.3 の災害リスク属性対応を維持したまま、PLATEAU CityGML の取得・キャッシュ・読み込み周辺を修正します。**Building matching、Complex grouping、geometry生成の規則は変更しません。**

主な変更:

- APIモードでキャッシュ済みCityGMLの読み込みに `TimeoutError` / `OSError` / XML読込エラーが発生した場合、**当該自治体のPLATEAUキャッシュを一括破棄し、必要なGML全件を再取得して1回だけ再試行**します。個別ファイルだけを推測的に修復・再利用しません。
- 再取得の発生理由、失敗ファイル、段階、再取得結果を `<code>_run_summary.json` の `cache_recovery_events` に記録します。
- `--refresh-plateau-cache` を追加。APIモードで対象自治体のキャッシュを処理前に明示的に破棄し、クリーン再取得できます。
- `scan_buildings()` とsubset GML書き出し時に、現在処理しているGMLファイル名と `[n/total]` を表示します。長時間無表示になる状態を減らします。
- APIカタログ取得前にも進捗メッセージを表示します。
- `--plateau-source local` は**完全オフライン**になり、自治体コード確認のためにPLATEAU API/catalogへアクセスしません。ローカルGMLが読めない場合も自動削除しません。
- localモードのファイル一覧は `<code>_plateau_files_local.csv` に出力し、以前のAPIモードで生成した `<code>_plateau_files.csv` を上書きしません。
- `heritage-gml --version` を追加しました。

通常実行:

```bash
heritage-gml --area-code 13101 --data-dir ./13Tokyo/gml_input
```

キャッシュを明示的に破棄して再取得:

```bash
heritage-gml --area-code 13101 --data-dir ./13Tokyo/gml_input --refresh-plateau-cache
```

ローカルPLATEAUを完全オフラインで使用:

```bash
heritage-gml --area-code 13101 --data-dir ./13Tokyo/gml_input \
  --plateau-source local \
  --plateau-local-dir /path/to/plateau
```

## v0.5.3 の追加機能: PLATEAU Building 災害リスク属性

PLATEAU CityGML の `bldg:Building` に含まれる `uro:bldgDisasterRiskAttribute` を Building 読み込み時に取得し、文化財照合とは独立した Building 属性として保持します。Data Tools 側の修正は不要です。

対応する6類型:

- `uro:RiverFloodingRiskAttribute` → `river_flooding`
- `uro:TsunamiRiskAttribute` → `tsunami`
- `uro:HighTideRiskAttribute` → `high_tide`
- `uro:InlandFloodingRiskAttribute` → `inland_flooding`
- `uro:ReservoirFloodingRiskAttribute` → `reservoir_flooding`
- `uro:LandSlideRiskAttribute` → `landslide`

元CityGMLに含まれる `description`, `rank`, `rankOrg`, `depth`, `adminType`, `scale`, `duration`, `areaType` を必要に応じて取得します。コード値と `codeSpace` は常に保持し、ローカルPLATEAUパッケージ内に参照コードリストが存在する場合はラベルも解決します。API等からGML単体だけを取得してコードリストが手元にない場合は、外部ネットワーク取得をせず、コード値と `codeSpace` のみ保持します。

`heritage_buildings_footprint` にはGIS分析用の集約列を追加します。例:

```text
disaster_risk_count
disaster_risk_types
river_flood_count
river_flood_max_depth_m
river_flood_max_duration_h
river_flood_descriptions
river_flood_ranks
river_flood_admin_types
river_flood_scales
tsunami_max_depth_m
high_tide_max_depth_m
inland_flood_max_depth_m
reservoir_flood_max_depth_m
landslide_count
landslide_descriptions
landslide_area_types
disaster_risks_json
```

さらに、Building : risk = 1:N を保持する正規化テーブル `plateau_disaster_risk` をGPKGに追加します。Building Polygon上の集約値は分析用派生属性で、正本はこの1:Nテーブルと元CityGMLです。

この災害リスク属性は、Building matching、Complex grouping、文化財類型判定には使用しません。既存の照合ロジックは変更しません。

元の `bldg:Building` をsubset GMLへ丸ごとコピーするため、`uro:bldgDisasterRiskAttribute` は `<code>_heritage_buildings.gml` にも元のまま保持されます。

## v0.5.1 の修正

### `address_detail` からの Complex 名正規化

v0.5.0 では `小石川後楽園内` の末尾 `園内` をまとめて削除し、`小石川後楽` としてしまう不具合がありました。
v0.5.1 では施設名称を保持し、所在を示す末尾表現だけを除去します。

```text
浅草寺境内       -> 浅草寺
浅草寺内         -> 浅草寺
小石川後楽園内   -> 小石川後楽園
日枝神社内       -> 日枝神社
東京国立博物館内 -> 東京国立博物館
聖徳寺墓地内     -> 聖徳寺
清泉女子大学内   -> 清泉女子大学
```

この正規化は Complex の表示・グループ名生成のためだけに使い、buffer、最近傍、範囲推定には使用しません。

それ以外の v0.5.0 の仕様は維持します。

## v0.5.0 で導入した主要仕様

### 1. movable を他の類型と同じ処理へ統一

`美術工芸品`、`考古資料`、`古文書`、`典籍` 等は `entity_class=movable` を保持しますが、
**同一住所でまとめる専用 movable-group 処理を廃止**しました。

すべての文化財レコードは個別に、同じ流れで処理します。

```text
文化財レコード
  ├─ exact Point ∈ Building footprint
  ├─ building_direct のみ exact name / exact address
  ├─ Building確定 → heritage_building_links
  ├─ Complex所属 → heritage_complex_records
  └─ Buildingにも有意なComplexにも確定しない → heritage_points
```

したがって、movable だけ `name=所在場所名`、`names=文化財名` となることはありません。
`name` は常にその文化財レコードの名称です。

### 2. `方書` を保持

東京都標準CSV等の `方書` を `address_detail` として保持します。
Complex名の補助情報として利用しますが、距離や範囲の推定には使用しません。

### 3. Building Complexを明示化

Complexは、Complexに所属する文化財レコードのうち、**直接PLATEAU Buildingと一致したBuildingだけ**をメンバーとします。

```text
Heritage Complex
  ├─ Building A Polygon
  ├─ Building B Polygon
  └─ Building C Polygon
```

GPKGの `heritage_building_complexes` は 1 Complex = 1 MultiPolygon です。
各Building footprintはMultiPolygonの別partとしてそのまま保持します。

以下は行いません。

- dissolve / union
- buffer
- convex hull
- 最近傍Building
- 建物間空地の補完
- Complex範囲の推定

Buildingが1棟も直接確定しないComplexにはPolygonを作りません。
その場合も `heritage_complex_summary` / `heritage_complex_records` に `complex_only` として残します。

### 4. 同一Complexで共有される完全同一座標

複数の異なる文化財レコードが同一Complex内で完全に同じ座標を持つ場合、
`source_location_role=shared_complex_coordinate` とします。

これは寺社境内などで個別文化財位置ではなくサイト代表座標が繰り返し使われるケースへの安全策です。
デフォルトではこの共有座標をBuilding直接照合には使いません。

```yaml
matching:
  match_shared_complex_coordinates: false
```

明らかに各レコードの正確な位置を示すデータであることが確認できる場合だけ `true` にできます。

## semantic entity_class

| 種類 | entity_class | 空間処理 |
|---|---|---|
| 建造物 | building_direct | 個別レコード処理 + exact name/address候補 |
| 美術工芸品 | movable | 個別レコード処理 |
| 考古資料 | movable | 個別レコード処理 |
| 古文書 | movable | 個別レコード処理 |
| 典籍 | movable | 個別レコード処理 |
| 美術工芸品・考古資料 | movable | 個別レコード処理 |
| その他 | point | 個別レコード処理 |

`movable` は意味分類であり、v0.5では特別なグループ化処理を意味しません。

## Complex grouping

Complexのグループ化は距離閾値を使わず、次の順序です。

1. 場所名称
2. 所有者 + 住所
3. 住所
4. 方書（上記がない場合の補助）
5. 完全同一点

`方書`の `浅草寺境内` / `浅草寺内` 等はComplex名表示用に末尾の所在表現を正規化できますが、
この処理から地理的な範囲を生成することはありません。

## 照合規則

### 全レコード共通

PointがBuilding footprintの内部または境界上にある場合だけ `point_in_building` とします。

```text
Point ∈ Building footprint -> direct Building relation
```

buffer、検索半径、nearest neighbourは使いません。

### building_direct の追加候補

- PLATEAU Building名称との完全正規化一致 → `exact_name`
- PLATEAU Building住所との完全正規化一致 → `exact_address`

これらは `building_direct` のみです。

### Complexと個別Buildingの関係

Complex内の別レコードがBuildingを確定しても、他の文化財レコードをそのBuildingへ自動伝播しません。

```text
Record A -> Building 1   （直接照合）
Record B -> Complex only （Building 1へは自動付与しない）

Complex -> Building 1    （Complex memberとして保持）
```

これにより「同じ寺院だから同じ建物」とする誤推定を防ぎます。

## 地域コード

```bash
heritage-gml --area-code 13 --data-dir ./13Tokyo/gml_input
heritage-gml --area-code 13106 --data-dir ./13Tokyo/gml_input
```

- 2桁: 都道府県コード
- 5桁: 市区町村コード
- チェックディジットは含めない

## 文化財データ

文化財データは実行前に取得済みで `--data-dir` に置きます。
本プログラムは文化財オープンデータAPIを呼びません。

対応:

- CSV
- JSON
- GeoJSON
- WKT geometryを持つCSV/JSON

## PLATEAU取得

通常はPLATEAU配信サービスから `bldg` CityGMLを取得します。
ローカルデータも使用できます。

```bash
heritage-gml \
  --area-code 13106 \
  --data-dir ./13Tokyo/gml_input \
  --plateau-source local \
  --plateau-local-dir /path/to/plateau
```

## 自治体別出力

すべての自治体別ファイルには5桁自治体コードのprefixを付けます。

```text
output/13106/
  13106_heritage_buildings.gml
  13106_heritage.gpkg
  13106_heritage_entities.json
  13106_heritage_entities.xml

  13106_cultural_records_normalized.csv
  13106_heritage_building_links.csv
  13106_heritage_complex_summary.csv
  13106_heritage_complex_members.csv
  13106_heritage_complex_records.csv
  13106_heritage_point_features.csv
  13106_heritage_unresolved_entities.csv

  13106_plateau_files.csv          # API mode
  13106_plateau_files_local.csv    # local mode
  13106_plateau_query_issues.csv
  13106_plateau_download_issues.csv
  13106_input_issues.csv
  13106_run_summary.json
```

v0.5では `heritage_movable_items` / `heritage_movable_groups` は生成しません。

## `<code>_heritage_buildings.gml`

選択されたPLATEAU `bldg:Building` を元CityGMLからコピーし、元のLOD0/LOD1/LOD2を保持します。
Generic Attributeとして主に以下を付与します。

- `heritageComplexId`
- `heritageComplexName`
- `heritageRecordIds`
- `heritageRecordNames`
- `heritageRecordTypes`
- `heritageEntityClasses`
- `heritageMatchMethod`

PLATEAU由来の `uro:bldgDisasterRiskAttribute` はGeneric Attributeへ複製せず、元Building要素内の正式な属性をそのまま保持します。

## `<code>_heritage.gpkg`

QGISでのレンダリング・分析用マスターGIS成果物です。

### Spatial layers

- `heritage_records`
  - 全文化財レコードの**ソース位置観測Point**
  - Buildingに一致したレコードも残す
  - `source_location_role` / `spatial_match_status` を保持
- `heritage_buildings_footprint`
  - 直接選択されたPLATEAU Buildingの2D footprint
  - PLATEAU災害リスクの件数・最大浸水深・カテゴリ等の集約属性を保持
  - `disaster_risks_json` に当該Buildingの全リスク属性を保持
- `heritage_building_complexes`
  - Buildingが1棟以上確定したComplex
  - 1 Complex = 1 MultiPolygon
  - member Building Polygonをpartとしてそのまま保持
- `heritage_points`
  - Buildingにも複数レコードComplexにも解決されなかったstandalone Point

### Attribute tables

- `plateau_disaster_risk`
  - selected Building ↔ PLATEAU災害リスク属性の1:Nテーブル
  - raw code / label / codeSpace / depth / duration / areaType 等を保持
- `heritage_building_links`
  - 文化財レコード ↔ 直接一致Building
- `heritage_complex_summary`
  - Building有無を含むComplex集計
- `heritage_complex_members`
  - Complex ↔ Building
- `heritage_complex_records`
  - Complex ↔ 文化財レコード
- `heritage_unresolved_entities`
  - Building直接照合が未解決のレコードと理由

## 浅草寺型データの扱い例

同じ住所・座標で、

- 浅草寺六角堂（建造物）
- 木造持国天立像・木造増長天立像（美術工芸品）
- 西仏板碑（歴史資料）
- 戸田茂睡墓（旧跡）
- 六地蔵石燈籠（旧跡）

が与えられた場合、すべてを個別文化財レコードとして保持しつつ、同じ `浅草寺` Complexへ関連付けます。
完全同一座標は `shared_complex_coordinate` となるため、デフォルトではその座標だけを根拠に個々のPLATEAU Buildingへ割り当てません。

## 都道府県GPKG統合

v0.5では統合ツールを同じパッケージに含めます。自治体別生成とは独立して実行します。

インストール後:

```bash
heritage-gpkg-merge \
  --input-root ./output \
  --pref-code 13
```

または、リポジトリ内の独立ランチャーを直接実行できます。

```bash
python merge_heritage_gpkg.py \
  --input-root ./output \
  --pref-code 13
```

入力例:

```text
output/
├── 13101/13101_heritage.gpkg
├── 13102/13102_heritage.gpkg
├── 13103/13103_heritage.gpkg
...
```

出力:

```text
output/
├── 13_heritage.gpkg
├── 13_heritage_merge_report.csv
└── 13_heritage_merge_manifest.json
```

統合ツールは同名レイヤ・テーブルを縦結合するだけです。
`heritage_building_complexes` のMultiPolygon partを変更せず、dissolve/union/buffer等を行いません。

## インストール

Python 3.10以上。

```bash
cd plateau_heritage_gml
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install .
```

既存環境を更新する場合:

```bash
python -m pip install . --force-reinstall
```

確認:

```bash
heritage-gml --help
heritage-gpkg-merge --help
```

## 実行

```bash
heritage-gml --area-code 13 --data-dir ./13Tokyo/gml_input
```

途中再開:

```bash
heritage-gml --area-code 13 --data-dir ./13Tokyo/gml_input --resume
```

PLATEAUダウンロード失敗は自治体別に記録し、2桁一括処理では他自治体を継続します。APIキャッシュ読込失敗時は自治体単位でキャッシュを一括再取得し、1回だけ再試行します。

## Companion XML/JSON

`heritage_entities.xml/json` は公式CityGML ADEではなく、Building、Building Complex、文化財レコードの関係を表すプロトタイプ補助モデルです。

namespace:

```text
urn:heritage-gml:prototype:0.5
```

- Building本体: `<code>_heritage_buildings.gml`
- QGIS/GIS分析: `<code>_heritage.gpkg`
- 関係モデル: `<code>_heritage_entities.xml/json`

## Classified cultural inputs (v0.5.2)

The Extractor accepts the eight optional columns produced by Tokyo Heritage Data Tools `heritage-classify`:

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

They are passed through to record-level outputs and aggregated onto matched Building / Building Complex attributes. They do **not** participate in Building matching, Complex grouping, geometry construction, buffering, or nearest-neighbour logic.

Recommended input after classification:

```text
13Tokyo/gml_input/
├── 130001_cultural_property_classified.csv
├── municipal_classified.csv
└── national_classified.csv
```

Do not keep both classified and unclassified copies in the same input directory.

### Patch already-generated Tokyo outputs without re-running matching

If a municipality GPKG/GML already exists, first create a classified CSV with `heritage-classify`, then patch attributes only:

```bash
heritage-classification-patch \
  --gpkg ./output/13106/13106_heritage.gpkg \
  --gml ./output/13106/13106_heritage_buildings.gml \
  --classified ./13Tokyo/gml_input/130001_cultural_property_classified.csv \
  --in-place
```

Without `--in-place`, `_classified.gpkg` / `_classified.gml` copies are created. The GPKG patch uses SQLite `ALTER TABLE` / `UPDATE`; existing geometry blobs and Building/Complex relationships are not recomputed. The GML patch only adds/replaces classification `gen:stringAttribute` values on already-selected Buildings.
