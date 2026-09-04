# Museum × PLATEAU 災害リスクGPKG生成ツール

`build_museum_hazard_gpkg.py` は、MuseumソースmanifestとPLATEAU CityGMLの建築物を照合し、既存のHeritage災害リスクGeoPackageを複製した上で、博物館向けの空間レイヤ・正規化テーブル・災害リスクを追加します。

ソースGPKGは読み取り専用として扱い、直接変更しません。出力先を明示しない場合、`13_heritage_hazards.gpkg` と同じディレクトリに `13_museum_hazards.gpkg` を作成します。

## 前提

- リポジトリと仮想環境がPLATEAU Heritage-GML Extractor v0.5.5に統一されていること
- `Museum/source/data/museum_candidates.csv` と `museum_reconciliation.csv` が生成済みであること
- `geopandas`、`pyogrio`、`shapely`、`pyproj`、`pandas`、`lxml` が仮想環境にあること
- ローカル再利用時は対象自治体のPLATEAU `bldg` CityGMLが展開済みであること。API取得時はネットワーク接続があること

`Museum/source/`の取得・正規化処理については[ソースmanifest README](source/README.md)を参照してください。

### Extractorへの追加属性パッチ

Museum出力で建物高さ、階数、建築年、構造、耐火構造、用途ラベルとcodeSpaceを保持するため、v0.5.5への統一後に次を実行します。Museumツール内にCityGML解析を複製せず、既存の`heritage_gml.citygml.scan_buildings()`を拡張するパッチです。既に同じ属性が実装済みなら適用は不要です。

```bash
cd /Users/noguchiatsushi/Documents/GitHub/PLATEAU_heritage
git apply --check Museum/patches/extractor_v0.5.5_building_attributes.patch
git apply Museum/patches/extractor_v0.5.5_building_attributes.patch
```

## 実行

### キャッシュを削除済みの場合：API targeted取得

既知の245施設について、公式住所がある場合は住所、ない場合は「自治体名＋施設名」をPLATEAUデータカタログAPIのジオコーディング条件へ渡し、該当する`bldg`メッシュだけを`.cache/plateau`へ再取得します。`--dry-run`はGPKGを書きませんが、このAPIモードでは検証に必要なGMLキャッシュを作成します。

```bash
cd /Users/noguchiatsushi/Documents/GitHub/PLATEAU_heritage
source .venv/bin/activate

python Museum/build_museum_hazard_gpkg.py \
  "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
  --plateau-source api-targeted \
  --plateau-local-dir .cache/plateau \
  --dry-run
```

このモードは既知施設の照合を優先する省容量モードです。取得されたメッシュ内ではPLATEAU詳細用途・名称による追加候補も抽出しますが、41自治体全域のPLATEAU単独候補を網羅するものではありません。

### 全域候補も抽出する場合：API municipality取得

manifestに含まれる41自治体について、全`bldg`メッシュを再取得します。PLATEAU側の用途属性から未収録施設候補も網羅的に抽出できますが、ダウンロード量、保存容量、走査時間は大幅に増えます。

```bash
python Museum/build_museum_hazard_gpkg.py \
  "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
  --plateau-source api-municipality \
  --plateau-local-dir .cache/plateau \
  --dry-run
```

### 既存のローカルCityGMLを使う場合

ローカルのPLATEAU CityGML格納先を指定して、書き込みなしの確認を行います。

```bash
cd /Users/noguchiatsushi/Documents/GitHub/PLATEAU_heritage
source .venv/bin/activate

python Museum/build_museum_hazard_gpkg.py \
  "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
  --plateau-source local \
  --plateau-local-dir "/PLATEAU/CityGMLを展開したディレクトリ" \
  --dry-run
```

結果件数を確認後、出力を作成します。

```bash
python Museum/build_museum_hazard_gpkg.py \
  "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
  --plateau-source api-targeted \
  --plateau-local-dir .cache/plateau \
  --output "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_museum_hazards.gpkg"
```

既存出力を置き換える場合だけ`--overwrite`を加えます。

```bash
python Museum/build_museum_hazard_gpkg.py \
  "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
  --plateau-source api-targeted \
  --plateau-local-dir .cache/plateau \
  --output "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_museum_hazards.gpkg" \
  --overwrite
```

`.cache/plateau`を使うこともできますが、Heritage処理で取得済みのメッシュだけでは東京都全域の建物を網羅しない場合があります。全施設を照合する実行では、対象自治体の全`bldg` GMLを展開したディレクトリを指定してください。

実行後、GPKGと同じ場所に`13_museum_hazards.summary.json`を出力します。

## 照合ルール

manifestで`needs_review`の4レコードは`museum_source_records`には保持しますが、確定施設には昇格させません。照合対象は現在のaccepted estimateである245施設です。

| 条件 | 判定 |
|---|---|
| 5桁自治体コード + 正規化名称の完全一致 | `confirmed` |
| 5桁自治体コード + 正規化住所の完全一致 + PLATEAU詳細用途`422302`（博物館）または`422305`（動物園）+ 住所に対応する施設が1件 | `confirmed` |
| 正規化住所だけの完全一致 | `needs_review` |
| PLATEAU詳細用途`422302`/`422305`または建物名称のMuseumキーワードだけ | `plateau_only_candidate` |
| 上記以外 | 未照合。`museum_unresolved`へ記録 |

現在のMuseumソースには座標がないため、point-in-buildingは実行しません。将来、信頼できる公式座標を取り込んだ場合だけ確認条件として追加します。buffer、最近傍、あいまい名称一致は自動確定に用いません。

`bldg:usage=422`は「文教厚生施設」という広い分類なので、それだけではMuseum候補にしません。`uro:detailedUsage=422302/422305`は強い候補根拠ですが、ソース施設との一致なしに自動確定はしません。

## 出力構造

### 空間レイヤ

| レイヤ | 役割 |
|---|---|
| `museum_buildings_footprint` | 自動確定したMuseum建物ポリゴン。名称表示、既存ハザードとの重ね合わせ、災害種別・深度別の色分けに使う主レイヤ |
| `museum_building_candidates` | 住所一致のみ、PLATEAU詳細用途のみ、名称キーワードのみ等の確認候補。主レイヤには混ぜない |

主レイヤには、表示・分類・リスク可視化で頻繁に使う次の属性を直接持たせます。

| 属性群 | GPKG列 | 内容 |
|---|---|---|
| 表示 | `display_name` | Museum施設名。PLATEAU単独候補ではPLATEAU建物名称 |
| Museum識別 | `museum_ids`, `museum_names`, `museum_count` | 1建物に対応する施設ID・名称・件数 |
| 施設分類 | `facility_types`, `law_statuses`, `ownership_types`, `operator_names` | 施設種別、博物館法区分、設置主体、運営者 |
| 連絡先 | `facility_address`, `phone`, `official_url` | manifest由来の所在地・電話・公式URL |
| 照合 | `match_status`, `match_methods`, `source_count`, `review_required` | 照合状態、根拠、原典レコード数、要確認フラグ |
| PLATEAU識別 | `gml_id`, `building_id`, `city_code`, `file_code`, `source_gml` | 建物・自治体・原典GMLの識別情報 |
| PLATEAU名称・住所 | `plateau_name`, `plateau_address` | PLATEAU建物属性 |
| 用途 | `usage_code`, `usage_label`, `usage_codespace`, `detailed_usage_code`, `detailed_usage_label`, `detailed_usage_codespace` | 建物用途と詳細用途のコード・名称・コードリスト参照 |
| 物理属性 | `measured_height_m`, `storeys_above`, `storeys_below`, `year_of_construction`, `structure_type_code`, `structure_type_label`, `fireproof_type_code`, `fireproof_type_label`, `footprint_area_m2` | 高さ、階数、建築年、構造、耐火構造、測地面積 |
| 全災害 | `has_any_hazard`, `hazard_count`, `hazard_types`, `disaster_risks_json` | 災害属性の有無・件数・種別・非損失JSON |
| 河川洪水 | `has_river_flood`, `river_flood_count`, `river_flood_max_depth_m`, `river_flood_max_duration_h`, `river_flood_worst_rank`, `river_flood_descriptions`, `river_flood_water_systems`, `river_flood_ranks`, `river_flood_rank_orgs`, `river_flood_admin_types`, `river_flood_scales` | 水系別レコードを建物単位に集約した河川洪水情報 |
| その他浸水 | `has_inland_flood`, `inland_flood_max_depth_m`, `inland_flood_worst_rank`, `has_high_tide`, `high_tide_max_depth_m`, `high_tide_worst_rank`, `has_tsunami`, `tsunami_max_depth_m`, `tsunami_worst_rank`, `has_reservoir_flood`, `reservoir_flood_max_depth_m`, `reservoir_flood_worst_rank` | 内水、高潮、津波、ため池の有無・最大深度・最悪ランク |
| 土砂災害 | `has_landslide`, `landslide_count`, `landslide_descriptions`, `landslide_area_types`, `landslide_worst_class` | 警戒区域等の区分 |

災害横断の独自「総合リスクスコア」は作りません。根拠の異なる災害を恣意的に一つの値へ合成せず、利用者が災害種別ごとに表示・抽出できる構造です。

### 正規化テーブル

| テーブル | 粒度・用途 |
|---|---|
| `museum_facilities` | 1行=1確定施設。245施設の名称、所在地、種別、法的位置づけ、所有区分、ソース統合結果 |
| `museum_source_records` | 1行=1取得レコード。295件すべてとreconciliation結果を保持するprovenance表 |
| `museum_building_links` | 1行=施設×建物。照合根拠と確認状態を保持 |
| `museum_unresolved` | 1行=未確定施設。未照合理由と候補建物IDを保持 |
| `plateau_disaster_risk` | 1行=建物×PLATEAU災害リスク属性。水系等の1:N関係を失わず保持 |

## 河川洪水を水系ごとに識別する

主レイヤでは`river_flood_water_systems`（互換列は`river_flood_descriptions`）に水系名をセミコロン区切りで持ちます。簡便な表示・フィルタにはこの列を使います。

QGIS式の例：

```text
array_contains(string_to_array("river_flood_water_systems", ';'), '荒川水系')
```

厳密な分析では、`plateau_disaster_risk`を`building_gml_id = museum_buildings_footprint.gml_id`で結合します。`risk_type='river_flooding'`の各行にある`description_code`、`description_label`、`description_codespace`が水系の識別子・表示名・コードリスト参照です。同じ建物に複数水系が付く場合も別行のまま保持されます。

## テスト

```bash
python -m unittest Museum.tests.test_build_museum_hazard_gpkg -v
python -m unittest Museum.source.tests.test_build_museum_manifest -v
```

実データでは、最初に`--dry-run`でファイル数、走査建物数、確定・候補・未照合件数を確認してください。
