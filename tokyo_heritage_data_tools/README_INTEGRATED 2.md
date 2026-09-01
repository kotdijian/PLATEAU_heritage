# PLATEAU Heritage Data Pipeline — Integrated README

**対象バージョン**

- `PLATEAU Heritage-GML Extractor` v0.5.0
- `Tokyo Heritage Data Tools` v0.2.1
- `merge_heritage_gpkg.py` v0.5.0（Extractor に同梱）

このREADMEは、文化財オープンデータの取得・正規化から、Project PLATEAU の `bldg:Building` との照合、Building Complex の生成、自治体別 GML / GeoPackage 出力、都道府県単位 GeoPackage 統合までを一つのワークフローとして説明します。

---

## 1. 全体構成

本システムは、役割の異なる2つのPythonパッケージと1つの統合処理から構成します。

```text
Tokyo Heritage Data Tools
    │
    │  国指定・区市町村指定文化財データ
    │  取得 / raw保存 / 正規化
    ▼
文化財入力データ
    ├─ 130001_cultural_property.csv  # 東京都指定・原データをそのまま使用
    ├─ national.csv                  # 国指定等・正規化済み
    └─ municipal.csv                 # 区市町村指定・正規化済み
    │
    ▼
PLATEAU Heritage-GML Extractor
    │
    │  PLATEAU Building取得
    │  文化財 ↔ Building照合
    │  Building Complex生成
    ▼
自治体別成果物
    ├─ <5桁自治体コード>_heritage_buildings.gml
    └─ <5桁自治体コード>_heritage.gpkg
    │
    ▼
merge_heritage_gpkg.py
    │
    ▼
都道府県統合成果物
    └─ <2桁都道府県コード>_heritage.gpkg
```

### 基本原則

- **文化財データ取得・正規化**と**PLATEAU照合**を分離する。
- 東京都教育庁の `130001_cultural_property.csv` は再取得・再正規化しない。
- PLATEAU Building は元の CityGML を保持し、文化財 Building を別の推定形状へ置き換えない。
- Building Complex は、**直接一致した Building の Polygon 群**として表現する。
- buffer、検索半径、nearest neighbour、convex hull、建物間空地の補完は行わない。
- GeoPackage を QGIS 用のGISマスター成果物とし、GMLを3D Building の正式成果物として保持する。

---

## 2. 各ツールの役割

### 2.1 Tokyo Heritage Data Tools

東京都内について、以下を行う前処理ツールです。

```text
heritage-collect
heritage-normalize
```

対象:

- 国指定等文化財
- 区市町村指定文化財

対象外:

- 東京都教育庁 `130001_cultural_property.csv`

東京都CSVは既存原データをそのまま Extractor に渡します。

### 2.2 PLATEAU Heritage-GML Extractor

事前取得済みの文化財 CSV / JSON / GeoJSON と、Project PLATEAU の `bldg:Building` CityGML を照合します。

主な処理:

```text
文化財レコード
    ↓
Complex判定
    ↓
PLATEAU Buildingとの直接照合
    ↓
Building / Complex / Point / unresolved の関係を記録
    ↓
GML + GPKG
```

Extractor 自体は文化財オープンデータAPIを呼びません。

### 2.3 `merge_heritage_gpkg.py`

自治体別 `xxxxx_heritage.gpkg` が生成された**後**に実行する独立統合処理です。

PLATEAU取得、文化財データ取得、再照合は行いません。

---

## 3. 推奨ワークスペース

```text
PLATEAU_heritage/
├── tokyo_heritage_data_tools/
├── plateau_heritage_gml/
├── merge_heritage_gpkg.py
│
├── 13Tokyo/
│   ├── prefectural/
│   │   └── 130001_cultural_property.csv
│   │
│   ├── raw/
│   │   ├── national/
│   │   └── municipal/
│   │
│   ├── tidy/
│   │   ├── national.csv
│   │   ├── municipal.csv
│   │   └── review / report files ...
│   │
│   └── gml_input/
│       ├── 130001_cultural_property.csv
│       ├── national.csv
│       └── municipal.csv
│
└── output/
    ├── 13101/
    │   ├── 13101_heritage_buildings.gml
    │   └── 13101_heritage.gpkg
    ├── 13102/
    │   ├── 13102_heritage_buildings.gml
    │   └── 13102_heritage.gpkg
    ├── ...
    └── 13_heritage.gpkg
```

`plateau-heritage-gml` の既定設定では文化財入力ディレクトリを再帰探索しないため、`gml_input/` のように実際に処理するCSVを1か所へまとめる運用を推奨します。

---

# Part A — Tokyo Heritage Data Tools

## 4. インストール

Python 3.10以上。

```bash
cd tokyo_heritage_data_tools
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install .
```

確認:

```bash
heritage-collect --help
heritage-normalize --help
```

Extractorと同じ仮想環境へインストールしても構いませんが、ツールの役割は独立しています。

---

## 5. 区市町村文化財データの取得

東京都内自治体について、調査済みソースを次のmanifestに保持しています。

```text
manifests/tokyo_municipal_sources_2026-08-27.yml
```

東京都全体:

```bash
heritage-collect municipal \
  --manifest manifests/tokyo_municipal_sources_2026-08-27.yml \
  --area-code 13 \
  --output ./13Tokyo/raw/municipal
```

自治体を限定する場合:

```bash
heritage-collect municipal \
  --manifest manifests/tokyo_municipal_sources_2026-08-27.yml \
  --area-code 13 \
  --codes 13102 13106 \
  --output ./13Tokyo/raw/municipal
```

取得優先順位:

1. 直接CSV URL
2. 東京都Open Data API JSON endpoint
3. 取得可能ソースなしとして記録

既存 `data.csv` / `data.json` は通常再取得しません。再取得時だけ `--overwrite` を指定します。

---

## 6. 区市町村データの正規化

```bash
heritage-normalize municipal \
  --input ./13Tokyo/raw/municipal \
  --output ./13Tokyo/tidy
```

主な出力:

```text
municipal.csv
municipal_all_normalized.csv
municipal_excluded_cross_level.csv
municipal_needs_review.csv
municipal_normalization_report.csv
municipal_normalization_summary.json
```

`municipal.csv` は、自治体から取得したという理由だけで「自治体指定」とは判定しません。

```text
municipal
prefectural
national
ambiguous
```

を明示的語彙から判定し、曖昧なものは `municipal_needs_review.csv` に残します。

---

## 7. 国指定等文化財データの取得

### 7.1 文化遺産オンラインから取得

```bash
heritage-collect national online \
  --pref-code 13 \
  --output ./13Tokyo/raw/national
```

主なraw出力:

```text
raw/national/
├── records.jsonl
├── collection_manifest.json
├── search_pages/*.html.gz
└── detail_pages/*.html.gz
```

取得済み detail URL は再実行時にスキップするため、通常はresume動作になります。

小規模テスト:

```bash
heritage-collect national online \
  --pref-code 13 \
  --output ./13Tokyo/raw/national_test \
  --max-pages 1 \
  --max-details 10
```

文化遺産オンラインのHTML構造変更時はparser修正が必要です。

### 7.2 国指定文化財等DBから手動CSVを取得した場合

```bash
heritage-collect national ingest \
  --input ./downloads/buildings.csv ./downloads/sites.csv \
  --output ./13Tokyo/raw/national
```

元CSVは内容を変更せずrawとして保存します。

---

## 8. 国データの正規化

```bash
heritage-normalize national \
  --input ./13Tokyo/raw/national \
  --output ./13Tokyo/tidy
```

主な出力:

```text
national.csv
national_all_normalized.csv
national_needs_review.csv
national_normalization_report.csv
national_normalization_summary.json
```

`national.csv` は5桁自治体コードまで解決できたレコードです。

---

## 9. Extractor入力用ディレクトリを作る

東京都指定文化財CSVは変更しません。

```bash
mkdir -p ./13Tokyo/gml_input

cp ./13Tokyo/prefectural/130001_cultural_property.csv \
   ./13Tokyo/gml_input/

cp ./13Tokyo/tidy/national.csv \
   ./13Tokyo/gml_input/

cp ./13Tokyo/tidy/municipal.csv \
   ./13Tokyo/gml_input/
```

コピーの代わりにシンボリックリンクを使用しても構いません。

最終的に:

```text
13Tokyo/gml_input/
├── 130001_cultural_property.csv
├── national.csv
└── municipal.csv
```

となれば、3系統を同時にExtractorへ渡せます。

### 指定レベル間の重複について

国・都・区市町村で同じ文化財対象が重複していても、前処理段階では自動的に1レコードへ統合しません。

指定制度と出典の違いを保持するためです。

---

# Part B — PLATEAU Heritage-GML Extractor

## 10. インストール

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

---

## 11. 文化財レコードの意味分類

v0.5.0では `movable` を特別な空間処理ルートとして扱いません。

| `type` | `entity_class` | 処理 |
|---|---|---|
| 建造物 | `building_direct` | 個別レコード + Building候補 |
| 美術工芸品 | `movable` | 個別レコード |
| 考古資料 | `movable` | 個別レコード |
| 古文書 | `movable` | 個別レコード |
| 典籍 | `movable` | 個別レコード |
| 美術工芸品・考古資料 | `movable` | 個別レコード |
| その他 | `point` | 個別レコード |

`movable` は**意味分類だけ**です。

従来の「同一住所の動産を movable group としてまとめ、`name=所在場所名` とする処理」は廃止されています。

`name` は常に文化財レコード自身の名称です。

Data Tools v0.2.xでは `方書` 等を `address_detail` として保持します。これはComplex判定の補助情報になりますが、Data Tools側ではBuildingや範囲を推定しません。

### Tokyo Heritage Data Tools v0.2.1との整合

Data Tools v0.2.1 は Extractor v0.5.x に合わせて更新されています。

- `address_detail` をcanonical schemaに追加し、`方書`等を保持
- `movable` は意味分類のみで、`geometry_role=representative_point`
- `歴史資料` は `point`
- `name` は常に文化財自身の名称
- Complex / `shared_complex_coordinate` / Building照合結果はExtractor側で導出

Extractor v0.5.x は入力 `type` をもとに現在の設定で `entity_class` を判定するため、Data Tools側の `entity_class` は互換性・確認用の意味分類として保持されます。

---

## 12. Building Complex

Complexのグループ化は距離閾値を使わず、次の情報を優先します。

1. 場所名称
2. 所有者 + 住所
3. 住所
4. 方書（上記がない場合の補助）
5. 完全同一点

Complexに含めるPLATEAU Buildingは、所属文化財レコードが**直接Buildingと一致した場合だけ**です。

```text
Heritage Complex
  ├─ Building A Polygon
  ├─ Building B Polygon
  └─ Building C Polygon
```

GPKGの `heritage_building_complexes` は:

```text
1 Complex = 1 MultiPolygon
```

とし、各Building footprintを別partのまま保持します。

行わない処理:

```text
× dissolve / union
× buffer
× convex hull
× nearest Building
× 建物間空地の補完
× Complex範囲の推定
```

Buildingが1棟も確定しないComplexも削除せず、`complex_only` として属性テーブルに残します。

---

## 13. 同一Complexで共有される座標

寺社境内等では、異なる文化財に同一の代表座標が繰り返し付与される場合があります。

同一Complex内で複数レコードが完全に同じ座標を共有した場合:

```text
source_location_role = shared_complex_coordinate
```

とします。

既定では、その共有座標だけを根拠に個別PLATEAU Buildingへ割り当てません。

```yaml
matching:
  match_shared_complex_coordinates: false
```

この安全策は、浅草寺のように「六角堂」「美術工芸品」「板碑」「墓」「石燈籠」等が同じサイト代表座標を共有するケースを想定しています。

---

## 14. PLATEAU Buildingとの照合

### 全レコード共通

文化財PointがBuilding footprintの内部または境界上にある場合のみ:

```text
Point ∈ Building footprint
    → point_in_building
```

とします。

### `building_direct` の追加候補

建造物だけは、さらに:

```text
PLATEAU Building名称との完全正規化一致
    → exact_name

PLATEAU Building住所との完全正規化一致
    → exact_address
```

を候補にできます。

Complex内の別レコードがBuildingを確定しても、他のレコードへ自動伝播しません。

```text
Record A → Building 1       # 直接照合
Record B → Complex only     # Building 1へ自動付与しない

Complex  → Building 1       # Complex memberとして保持
```

---

## 15. Extractorの実行

### 15.1 自治体単独

例: 台東区 `13106`

```bash
heritage-gml \
  --area-code 13106 \
  --data-dir ./13Tokyo/gml_input
```

### 15.2 東京都全体

```bash
heritage-gml \
  --area-code 13 \
  --data-dir ./13Tokyo/gml_input
```

2桁コードでは、PLATEAUで対象となる自治体を順次処理します。

### 15.3 事前確認

```bash
heritage-gml \
  --area-code 13 \
  --data-dir ./13Tokyo/gml_input \
  --dry-run
```

### 15.4 中断・失敗後の再実行

```bash
heritage-gml \
  --area-code 13 \
  --data-dir ./13Tokyo/gml_input \
  --resume
```

完了済み自治体をスキップし、未処理・失敗分を継続できます。

### 15.5 ローカルPLATEAU CityGMLを使う場合

```bash
heritage-gml \
  --area-code 13106 \
  --data-dir ./13Tokyo/gml_input \
  --plateau-source local \
  --plateau-local-dir /path/to/plateau
```

---

## 16. 自治体別出力

すべての自治体別ファイルには5桁自治体コードprefixを付けます。

例: 台東区

```text
output/13106/
├── 13106_heritage_buildings.gml
├── 13106_heritage.gpkg
├── 13106_heritage_entities.json
├── 13106_heritage_entities.xml
│
├── 13106_cultural_records_normalized.csv
├── 13106_heritage_building_links.csv
├── 13106_heritage_complex_summary.csv
├── 13106_heritage_complex_members.csv
├── 13106_heritage_complex_records.csv
├── 13106_heritage_point_features.csv
├── 13106_heritage_unresolved_entities.csv
│
├── 13106_plateau_files.csv
├── 13106_plateau_query_issues.csv
├── 13106_plateau_download_issues.csv
├── 13106_input_issues.csv
└── 13106_run_summary.json
```

v0.5.0では `heritage_movable_items` / `heritage_movable_groups` は生成しません。

---

## 17. GML成果物

```text
<code>_heritage_buildings.gml
```

には、選択されたPLATEAU `bldg:Building` を元CityGMLからコピーします。

可能な範囲で元の:

```text
LOD0
LOD1
LOD2
```

を保持します。

文化財関係はGeneric Attributeとして付与します。

主な属性:

```text
heritageComplexId
heritageComplexName
heritageRecordIds
heritageRecordNames
heritageRecordTypes
heritageEntityClasses
heritageMatchMethod
```

---

## 18. GeoPackage成果物

```text
<code>_heritage.gpkg
```

はQGISでのレンダリング・分析用マスターGIS成果物です。

### Spatial layers

#### `heritage_records`

全文化財レコードのソース位置Point。

Buildingに一致したレコードも残します。

主に:

```text
source_location_role
spatial_match_status
complex_id
complex_name
```

等を確認できます。

#### `heritage_buildings_footprint`

直接選択されたPLATEAU Buildingの2D footprint。

#### `heritage_building_complexes`

Buildingが1棟以上直接確定したComplex。

```text
1 Complex = 1 MultiPolygon
```

各member Building Polygonは別partとして保持されます。

#### `heritage_points`

Buildingにも複数レコードComplexにも解決されなかったstandalone Point。

### Attribute tables

```text
heritage_building_links
heritage_complex_summary
heritage_complex_members
heritage_complex_records
heritage_unresolved_entities
```

関係は概念的に次のようになります。

```text
heritage_records
     │
     ├──── heritage_building_links ──── heritage_buildings_footprint
     │
     └──── heritage_complex_records ─── heritage_complex_summary
                                           │
                                           └── heritage_complex_members
                                                    │
                                                    └── Buildings
```

---

# Part C — 都道府県GeoPackage統合

## 19. 実行タイミング

`merge_heritage_gpkg.py` は、自治体別GPKGの生成が完了した**後**に実行します。

```text
heritage-gml
    ↓
13101_heritage.gpkg
13102_heritage.gpkg
13103_heritage.gpkg
...
    ↓
merge_heritage_gpkg.py
    ↓
13_heritage.gpkg
```

途中段階でも実行できますが、その時点で存在する自治体GPKGだけが統合対象となります。

---

## 20. 東京都全体GPKGの生成

Extractorをインストール済みの場合:

```bash
heritage-gpkg-merge \
  --input-root ./output \
  --pref-code 13
```

独立 `.py` として実行する場合:

```bash
python merge_heritage_gpkg.py \
  --input-root ./output \
  --pref-code 13
```

出力:

```text
output/
├── 13_heritage.gpkg
├── 13_heritage_merge_report.csv
└── 13_heritage_merge_manifest.json
```

統合時には自治体ごとにレイヤを分けず、同名レイヤ・テーブルを東京都全体で縦結合します。

```text
13_heritage.gpkg
├── heritage_records
├── heritage_buildings_footprint
├── heritage_building_complexes
├── heritage_points
├── heritage_building_links
├── heritage_complex_summary
├── heritage_complex_members
├── heritage_complex_records
└── heritage_unresolved_entities
```

統合時には各データへ:

```text
municipality_code
municipality_name
source_municipality_gpkg
```

を付与または補完します。

`heritage_building_complexes` のMultiPolygonは変更しません。

```text
× dissolve
× union
× buffer
```

は行いません。

同一自治体コードの `xxxxx_heritage.gpkg` が複数見つかった場合は、二重集計防止のため停止します。

---

# Part D — QGISでの利用

## 21. 基本的な読み込み

東京都全体を扱う場合は、QGISで:

```text
output/13_heritage.gpkg
```

を開きます。

主要レイヤ:

```text
heritage_buildings_footprint
heritage_building_complexes
heritage_records
heritage_points
```

自治体別に絞り込む場合は:

```text
municipality_code = '13106'
```

等でフィルタできます。

### 3D Building

2D分析用GPKGとは別に、自治体別:

```text
13106_heritage_buildings.gml
```

を保持します。

GPKGのfootprintは3D Buildingの代替ではなく、QGIS分析用の派生2D geometryです。

---

# Part E — 重要な互換性・運用上の注意

## 22. `方書` と `address_detail`

Extractor v0.5.x と Tokyo Heritage Data Tools v0.2.1 は、次の所在地詳細列を `address_detail` として扱えるよう整合しています。

```text
方書
住所詳細
所在地詳細
所在詳細
address_detail
address_note
```

したがって:

- `130001_cultural_property.csv` の `方書` はExtractorが直接読み取れる。
- Data Tools v0.2.xで正規化する国・自治体データも、元データに対応列があれば `address_detail` として保持する。
- `address_detail` はComplex判定の補助情報であり、Data Tools側では範囲やBuildingを推定しない。
- v0.1.0で既に生成済みの `national.csv` / `municipal.csv` には `address_detail` がないため、必要ならv0.2.1で再正規化する。

---

## 23. `entity_class` の扱い

Tokyo Heritage Data Tools v0.2.1 はcanonical CSVに:

```text
entity_class
geometry_role
```

を出力します。

ただしExtractor v0.5.0では、現在の `type_class_map` に基づき `type` から再分類します。

そのため前処理側の `entity_class` は出典・確認用情報として扱い、最終空間処理はExtractor側のv0.5.0ルールを基準とします。

---

## 24. 座標は「文化財そのものの位置」とは限らない

文化財オープンデータの座標は:

```text
対象物位置
施設代表点
寺社境内代表点
住所代表点
```

のいずれであるかが明示されない場合があります。

このため、完全同一座標を複数文化財が共有する場合は安全側に倒し、個別Building照合へ自動使用しません。

元座標自体は `heritage_records` に保持します。

---

## 25. 自動推定しないもの

本パイプラインでは、根拠のない空間推定を避けます。

```text
× 固定距離buffer
× nearest Building
× 近接建物の自動採用
× Complex境界の推定
× 建物間空地の文化財範囲化
× 指定レベルの曖昧レコードの自動昇格
```

未解決レコードは削除せず、review / unresolvedとして保持します。

---

# Part F — 最短実行例

## 26. 東京都指定文化財CSVだけでPLATEAU処理する場合

Tokyo Heritage Data Toolsは不要です。

```bash
mkdir -p ./13Tokyo/gml_input
cp ./13Tokyo/prefectural/130001_cultural_property.csv ./13Tokyo/gml_input/

heritage-gml \
  --area-code 13 \
  --data-dir ./13Tokyo/gml_input
```

自治体別GPKG生成完了後:

```bash
python merge_heritage_gpkg.py \
  --input-root ./output \
  --pref-code 13
```

---

## 27. 国・都・区市町村をまとめて処理する場合

```bash
# 1. 区市町村データ取得
heritage-collect municipal \
  --manifest ./tokyo_heritage_data_tools/manifests/tokyo_municipal_sources_2026-08-27.yml \
  --area-code 13 \
  --output ./13Tokyo/raw/municipal

# 2. 区市町村データ正規化
heritage-normalize municipal \
  --input ./13Tokyo/raw/municipal \
  --output ./13Tokyo/tidy

# 3. 国データ取得
heritage-collect national online \
  --pref-code 13 \
  --output ./13Tokyo/raw/national

# 4. 国データ正規化
heritage-normalize national \
  --input ./13Tokyo/raw/national \
  --output ./13Tokyo/tidy

# 5. GML入力をまとめる
mkdir -p ./13Tokyo/gml_input
cp ./13Tokyo/prefectural/130001_cultural_property.csv ./13Tokyo/gml_input/
cp ./13Tokyo/tidy/national.csv ./13Tokyo/gml_input/
cp ./13Tokyo/tidy/municipal.csv ./13Tokyo/gml_input/

# 6. PLATEAU照合・自治体別 GML + GPKG生成
heritage-gml \
  --area-code 13 \
  --data-dir ./13Tokyo/gml_input

# 7. 東京都統合GPKG生成
python merge_heritage_gpkg.py \
  --input-root ./output \
  --pref-code 13
```

再実行時:

```bash
heritage-gml \
  --area-code 13 \
  --data-dir ./13Tokyo/gml_input \
  --resume
```

---

## 28. ツールの位置づけ

```text
raw source archive
        ↓
Tokyo Heritage Data Tools
        ↓
normalized cultural records
        ↓
PLATEAU Heritage-GML Extractor
        ↓
自治体別
  ├─ Heritage-GML
  └─ Heritage GeoPackage
        ↓
merge_heritage_gpkg.py
        ↓
都道府県 Heritage GeoPackage
        ↓
QGIS / analysis / future export
```

`Tokyo Heritage Data Tools` は、東京都指定CSVだけで処理する場合には必須ではありません。

国指定・区市町村指定データを取得・正規化する場合に使用する**独立した前処理ツール**として保持します。

---

## 29. 現在のスコープ

- `PLATEAU Heritage-GML Extractor` は特定自治体をコード内に固定しない汎用設計。
- `Tokyo Heritage Data Tools v0.2.1` は東京都向けプロトタイプであり、東京都内自治体ソースmanifest・東京都自治体コード判定を含む。
- 全国展開時は `Tokyo Heritage Data Tools` を汎用 `Heritage Data Tools` へ拡張することを想定する。
- PLATEAU VIEW対応は本READMEの対象外とし、現時点ではQGISでのレンダリング・分析を優先する。

