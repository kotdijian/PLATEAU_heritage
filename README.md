# PLATEAU Heritage-GML Extractor v0.5.4

事前取得済みの文化財 CSV / JSON / GeoJSON と Project PLATEAU の `bldg:Building` CityGML を照合し、
文化財 Building、Building Complex、文化財レコードの位置情報を **GML + GeoPackage** として出力する Python CLI です。

コード内に特定の都道府県名・自治体名・自治体コードは固定していません。

データ収集、正規化、3レベルの重複整理、位置情報の不確実性、PLATEAU属性の取り込みを含む設計と開発過程は、[開発レポート](docs/DEVELOPMENT_REPORT.md)を参照してください。


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

`heritage_buildings_footprint` にはGIS検索用の件数、最大浸水深、区分一覧等を追加します。全列とPLATEAU原属性との対応は「PLATEAUからGPKGへ取り込む属性」に示します。

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

## PLATEAUからGPKGへ取り込む属性

Extractorは対象自治体のPLATEAU Buildingを走査しますが、GPKGへ収録するのは文化財レコードとの照合またはComplex membershipによって選択されたBuildingだけです。全PLATEAU BuildingをGPKGへ複製する処理ではありません。

元のLOD0/LOD1/LOD2を含む `bldg:Building` 要素はsubset CityGMLへ保持し、GPKGにはQGIS等で扱う2D footprint、検索用属性、関係表を格納します。

### Building基本属性：PLATEAU側と出力GPKG側の対比

| PLATEAU側 | GPKG層 | GPKG列名 | 内容・変換 |
|---|---|---|---|
| `bldg:Building/@gml:id` | `heritage_buildings_footprint` | `gml_id` | BuildingのCityGML内識別子 |
| `uro:buildingID` / `buildingId` | 同上 | `building_id` | PLATEAU側の建築物ID。存在しない場合は空欄 |
| dataset/API metadata | 同上 | `city_code` | 5桁自治体コード |
| PLATEAU bldg file metadata | 同上 | `file_code` | 取得対象CityGML fileの地域・mesh等の識別code |
| `gml:name` | 同上 | `name` | 最初に取得できたBuilding名称 |
| `core:Address` 以下 | 同上 | `address` | Address内のleaf textを重複除去して連結した検索用文字列 |
| `bldg:usage` | 同上 | `usage` | 建物用途の元codeまたは文字列 |
| `uro:detailedUsage` | 同上 | `detailed_usage` | 詳細用途の元codeまたは文字列 |
| `bldg:lod0FootPrint` | 同上 | `geometry` | 優先使用する2D footprint。EPSG:4326へ変換 |
| `bldg:lod0RoofEdge` | 同上 | `geometry` | footprintがない場合の優先候補 |
| Building内のpolygon座標 | 同上 | `geometry` | LOD0候補がない場合のfallback。Zを持たない分析用2D geometry |
| source CityGML file | 同上 | `source_gml` | 由来を追跡する元GML path |

GPKGの `address` は階層的住所を単一文字列へflattenした派生値であり、`geometry` は3D Building geometryそのものではありません。元の構造、LOD、正式なURO属性はsubset CityGMLに保持します。`usage` と `detailed_usage` は意味を推定せず、元のcode/textを保持します。

### PLATEAU由来値が伝播するGPKG列

| GPKG層・表 | PLATEAU由来または参照用の列 | 内容 |
|---|---|---|
| `heritage_records` | `matched_building_ids` | 照合したPLATEAU `gml:id`の`;`区切り一覧 |
| `heritage_buildings_footprint` | `gml_id`, `building_id`, `city_code`, `file_code`, `name`, `address`, `usage`, `detailed_usage`, `source_gml`, `geometry` | 選択Buildingの識別・基本属性・由来・2D footprint |
| `heritage_building_complexes` | `building_gml_ids`, `member_building_count`, `geometry` | member Buildingの`gml:id`一覧、件数、footprint集合 |
| `heritage_building_links` | `building_gml_id`, `building_id`, `building_name`, `building_address`, `usage`, `detailed_usage`, `source_gml` | Cultural Recordと個々のPLATEAU Buildingを結ぶ参照属性 |
| `heritage_complex_members` | `building_gml_id`, `building_id`, `building_name`, `building_address`, `usage`, `detailed_usage`, `source_gml` | Complexとmember PLATEAU Buildingを結ぶ参照属性 |
| `heritage_complex_summary` | `building_gml_ids`, `matched_building_count` | Complexに確定したPLATEAU Building ID一覧と件数 |
| `heritage_complex_records` | `matched_building_ids` | Complex内レコードが照合したPLATEAU `gml:id`一覧 |
| `plateau_disaster_risk` | `building_gml_id`, `building_id`, `city_code`, `file_code`, `source_gml` | リスク属性の親Buildingと由来fileを特定するkey |

`heritage_points` と `heritage_unresolved_entities` は文化財側の未一致・standalone対象を保持するため、直接のPLATEAU属性を持ちません。Building属性の中心的な参照先は `heritage_buildings_footprint`、災害リスクの個別値の参照先は `plateau_disaster_risk` です。

### 災害リスク原属性と `plateau_disaster_risk` の対比

`bldg:Building` に付属する `uro:bldgDisasterRiskAttribute` をBuilding走査時に抽出します。災害リスクは文化財分類、Building照合、Complex groupingの証拠には使わず、選択済みBuildingに付加するPLATEAU由来属性として扱います。

| PLATEAU側 | `plateau_disaster_risk`列 | 内容・変換 |
|---|---|---|
| 親 `bldg:Building/@gml:id` | `building_gml_id` | リスクを持つBuildingへの外部key |
| `uro:*RiskAttribute` 要素名 | `risk_attribute_type` | `RiverFloodingRiskAttribute`等の元要素名 |
| 要素名からの正規化 | `risk_type`, `risk_type_ja` | 英語codeと日本語類型 |
| `uro:description` | `description_code`, `description_label`, `description_codespace` | 元code、解決label、`codeSpace` |
| `uro:rank` | `rank_code`, `rank_label`, `rank_codespace` | 浸水rank等の元code・label・参照先 |
| `uro:rankOrg` | `rank_org_code`, `rank_org_label`, `rank_org_codespace` | 原典側rank表記 |
| `uro:depth` | `depth_value`, `depth_uom`, `depth_m` | 元数値・単位とm換算値 |
| `uro:adminType` | `admin_type_code`, `admin_type_label`, `admin_type_codespace` | 作成・管理主体区分 |
| `uro:scale` | `scale_code`, `scale_label`, `scale_codespace` | 計画規模・想定最大規模等 |
| `uro:duration` | `duration_value`, `duration_uom`, `duration_h` | 元数値・単位と時間換算値 |
| `uro:areaType` | `area_type_code`, `area_type_label`, `area_type_codespace` | 土砂災害区域種別等 |
| PLATEAU file metadata | `building_id`, `city_code`, `file_code`, `source_gml` | Building ID、自治体、元file |
| 同一Building内の出現順 | `risk_index` | Building内でのリスク属性順序 |

ローカルPLATEAU package内のcodelistを利用できる場合だけ人間可読labelを解決します。GML単体しかない場合や参照先がURLの場合は外部取得で補完せず、codeと`codeSpace`を保持します。`depth_m` はm/cm/mmをmへ、`duration_h` はhour/minute/secondを時間へ換算します。未知単位は推測せず正規化値を空欄にし、元値と単位を残します。

### `heritage_buildings_footprint` の災害リスク集約列

| GPKG列名 | 内容 |
|---|---|
| `disaster_risk_count` | 当該Buildingに付属する全リスク属性数 |
| `disaster_risk_types` | 正規化risk typeの重複なし`;`区切り一覧 |
| `river_flood_count` | 洪水属性数 |
| `river_flood_max_depth_m` | 洪水属性の最大浸水深m |
| `river_flood_max_duration_h` | 洪水属性の最大浸水継続時間h |
| `river_flood_descriptions` | 洪水descriptionのlabel優先一覧 |
| `river_flood_ranks`, `river_flood_rank_orgs` | 洪水rank・原rank一覧 |
| `river_flood_admin_types`, `river_flood_scales` | 洪水の管理主体区分・規模区分一覧 |
| `tsunami_count`, `tsunami_max_depth_m` | 津波属性数・最大浸水深m |
| `tsunami_descriptions`, `tsunami_ranks`, `tsunami_rank_orgs` | 津波description・rank一覧 |
| `high_tide_count`, `high_tide_max_depth_m` | 高潮属性数・最大浸水深m |
| `high_tide_descriptions`, `high_tide_ranks`, `high_tide_rank_orgs` | 高潮description・rank一覧 |
| `inland_flood_count`, `inland_flood_max_depth_m` | 内水属性数・最大浸水深m |
| `inland_flood_descriptions`, `inland_flood_ranks`, `inland_flood_rank_orgs` | 内水description・rank一覧 |
| `reservoir_flood_count`, `reservoir_flood_max_depth_m` | ため池属性数・最大浸水深m |
| `reservoir_flood_descriptions`, `reservoir_flood_ranks`, `reservoir_flood_rank_orgs` | ため池description・rank一覧 |
| `landslide_count` | 土砂災害属性数 |
| `landslide_descriptions`, `landslide_area_types` | 土砂災害description・区域種別一覧 |
| `disaster_risks_json` | 当該Buildingの全リスクレコードを省略せず格納したJSON |

一覧列はcodelist labelを優先し、得られなければraw codeを使い、重複を除いて`;`で連結します。`*_max_depth_m` と `river_flood_max_duration_h` は検索・可視化用の最大値であり、個別値は `plateau_disaster_risk` を参照してください。分析上の正本は元CityGMLとこの1:Nテーブルです。

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

## Museum所在地Collector

`Museum/`には、東京都内の博物館等245施設を対象とする災害リスク評価ツールがあります。所在地Collector v0.2.3の現行成果は検証済み住所221件（90.2%）で、理想基準の90%超へ到達しています。次工程では、この住所をABRで座標化し、PLATEAU Building footprintとの一意なpoint-in-polygon照合へ使用します。

Manifest生成後、次の標準Collectorを実行します。

```bash
python Museum/source/scripts/build_museum_locations.py \
  --abr-api-base http://localhost:3000
```

既存Manifest、確認済みoverride、施設公式ページ、追加公式一覧CSV、文化遺産オンラインを優先順位付きで統合します。自動照合は`正規化名称 + 5桁自治体コード`の完全一致だけを採用し、住所競合は要確認へ分離します。出力`Museum/source/data/museum_location_enrichment.csv`は、後段の`Museum/build_museum_hazard_gpkg.py`がoverlayとして読み込みます。ABR座標のうち住居表示詳細・地番レベルで自治体コードも一致し、かつ一意に1棟へ入るものだけをBuilding確定に使います。未解決の重複`gml:id`は片方を選ばず、明示オプションで全コピーを隔離できます。

詳細は[MuseumツールREADME](Museum/README.md)と[ソースCollector README](Museum/source/README.md)を参照してください。

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
