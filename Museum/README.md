# Museum × PLATEAU 災害リスクGPKG生成ツール

`build_museum_hazard_gpkg.py` v0.3.1は、MuseumソースmanifestとPLATEAU CityGMLの建築物を照合し、既存のHeritage災害リスクGeoPackageを複製した上で、博物館向けの空間レイヤ・正規化テーブル・災害リスクを追加します。`museum_location_enrichment.csv`の受理済み住所・座標を照合用overlayとして優先し、原manifest住所は`source_manifest_address`に保存します。博物館の所在階・収蔵庫階は建物階数と分離して保持し、浸水深との対応表を生成します。

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

住所内のカタカナ長音符（例：`東京ドーム`）をハイフンへ誤変換しない修正も適用します。数字間の`ー`だけを住所区切りとして扱います。

```bash
git apply --check Museum/patches/extractor_v0.5.5_address_normalization.patch
git apply Museum/patches/extractor_v0.5.5_address_normalization.patch
```

## 実行

### 1. 221件の住所をABRで座標化

PLATEAU建物住所は町丁目までしか入っていない場合が多いため、住所文字列だけでは建物を一意に特定できません。先にデジタル庁ABR Geocoderへ東京都の住居表示・地番・座標データを導入し、所在地Collectorを再実行します。Collector v0.2.5はABR v2の配列内`result`と、ABR v3のGeoJSON FeatureCollectionの両方を展開し、ABR自治体コードの先頭5桁がmanifestの自治体コードと一致する座標だけを受理します。ABR v3の`rsdtdsp_rsdt`は内部の`residential_detail`、`rsdtdsp_blk`は`residential_block`へ正規化します。

abr-geocoderリポジトリで東京都データを準備する例です。パスワード値は各自で設定してください。

```bash
git clone https://github.com/digital-go-jp/abr-geocoder.git
cd abr-geocoder
cp .env.example .env
# .env の DB_PASSWORD を設定
docker compose up -d postgres
docker compose run --rm abrdb_app init --pref 13 --category all --pos
docker compose run --rm abrdb_app import
docker compose run --rm abrg_app cache build
docker compose up -d abrg_app
curl -s "http://localhost:3000/geocode?address=東京都千代田区紀尾井町1-3"
```

次にPLATEAU_heritageリポジトリでCollectorを実行します。

```bash
cd /Users/noguchiatsushi/Documents/GitHub/PLATEAU_heritage
source .venv/bin/activate

python Museum/source/scripts/build_museum_locations.py \
  --abr-api-base http://localhost:3000
```

`museum_location_summary.json`の`verified_coordinate_count`を確認します。`coordinate_level=residential_detail`または`parcel`だけが`coordinate_use=building_candidate`となり、町字・街区等の低粒度座標はPLATEAUメッシュ取得だけに使用します。

### 2. PLATEAU targeted取得と照合

既知の245施設について、公式住所がある場合は住所、ない場合は「自治体名＋施設名」をPLATEAUデータカタログAPIのジオコーディング条件へ渡し、該当する`bldg`メッシュだけを`.cache/plateau`へ再取得します。`--dry-run`はGPKGを書きませんが、このAPIモードでは検証に必要なGMLキャッシュを作成します。

```bash
cd /Users/noguchiatsushi/Documents/GitHub/PLATEAU_heritage
source .venv/bin/activate

python Museum/build_museum_hazard_gpkg.py \
  "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
  --plateau-source api-targeted \
  --plateau-local-dir .cache/plateau \
  --exclude-unresolved-duplicates \
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
  --exclude-unresolved-duplicates \
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
  --exclude-unresolved-duplicates \
  --dry-run
```

結果件数を確認後、出力を作成します。

```bash
python Museum/build_museum_hazard_gpkg.py \
  "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
  --plateau-source api-targeted \
  --plateau-local-dir .cache/plateau \
  --exclude-unresolved-duplicates \
  --output "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_museum_hazards.gpkg"
```

既存出力を置き換える場合だけ`--overwrite`を加えます。

```bash
python Museum/build_museum_hazard_gpkg.py \
  "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
  --plateau-source api-targeted \
  --plateau-local-dir .cache/plateau \
  --exclude-unresolved-duplicates \
  --output "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_museum_hazards.gpkg" \
  --overwrite
```

`.cache/plateau`を使うこともできますが、Heritage処理で取得済みのメッシュだけでは東京都全域の建物を網羅しない場合があります。全施設を照合する実行では、対象自治体の全`bldg` GMLを展開したディレクトリを指定してください。

実行時には、共有走査処理より先に全GMLの`gml:id`重複監査を行います。同一IDのXMLが意味的に同一ならAPI検索範囲の重なりによる安全な複製として集計し、1建物に統合します。比較では名前空間URI、属性、本文、要素階層を使用し、字下げ、改行、空白量、名前空間prefixの違いは無視します。

行政界をまたぐメッシュでは、同一IDの詳細版と簡略版が隣接区の取得結果へ重複収録される場合があります。内容が異なっていても、Building内の汎用属性`13+区市町村コード+大字・町コード+町・丁目コード`から得た5桁自治体コードと、GMLを取得した自治体コードが一致する版が一意に存在すれば、その版を正本として採用します。

東京都データでは、形状・利用属性が同一でも、一方の配布版だけにLOD3/LOD4の形状・外観出典が存在しないことを示す`geometrySrcDescLod3=999`、`geometrySrcDescLod4=999`、`appearanceSrcDescLod3=99`、`appearanceSrcDescLod4=99`が付く場合があります。この4項目が所定のDataQualityAttributeコードリストを参照する場合だけ、内容上同等の複製として解決し、明示的な品質メタデータを持つ版を保持します。また、Extractor本体が名前空間URIではなくXML local nameで属性を読む実装に合わせ、local name・属性値・本文・階層が同一で名前空間URIだけが異なる版も抽出上同等として解決します。

同じ階層にあるCityGML要素の記載順だけが異なる複製は、階層付きの意味要素multisetで比較します。タグ名、属性名・値、本文、座標列、災害リスク値はすべて比較に残し、兄弟要素の順序だけを無視します。座標等が異なる未解決IDは既定では停止します。監査済み入力で`--exclude-unresolved-duplicates`を指定すると、競合する全コピーを除外し、summaryへID数とサンプルを記録します。これはファイル順で片方を選ぶ処理ではありません。`--skip-duplicate-audit`は同一入力をすでに監査済みの場合だけ使用します。

実行後、GPKGと同じ場所に`13_museum_hazards.summary.json`を出力します。

## 照合ルール

manifestで`needs_review`の4レコードは`museum_source_records`には保持しますが、確定施設には昇格させません。照合対象は現在のaccepted estimateである245施設です。

| 条件 | 判定 |
|---|---|
| 5桁自治体コード + 正規化名称の完全一致 | `confirmed` |
| 5桁自治体コード + 正規化完全住所の一致 + PLATEAU詳細用途`422302`（博物館）または`422305`（動物園）+ 住所に対応する施設が1件 | `confirmed` |
| ABRの`residential_detail`/`parcel`座標が一意に1棟のPLATEAU footprint内へ入る | `confirmed` |
| 同じ座標が複数棟のfootprintへ入る | `needs_review` |
| 正規化完全住所だけの一致 | `needs_review` |
| 街区・番地まで一致し、ビル名・階数等だけが異なる敷地住所一致 | `needs_review` |
| PLATEAU詳細用途`422302`/`422305`または建物名称のMuseumキーワードだけ | `plateau_only_candidate` |
| 東京都土地利用現況調査の詳細用途`1122`（文化施設）のみ | `plateau_only_candidate` |
| 上記以外 | 未照合。`museum_unresolved`へ記録 |

point-in-buildingでは、ABRの自治体一致と詳細粒度を検証した座標だけを建物確定に使います。町字・街区座標、複数棟に入る点、自治体不一致座標は自動確定しません。buffer、最近傍、あいまい名称一致も自動確定に用いません。

### OSM照合パイロット（PLATEAU照合前の補助工程）

OpenStreetMapを候補・監査根拠として試験する独立ツールv0.1.1を
`Museum/source/scripts/build_museum_osm_matches.py`に置いています。東京都内の対象
OSM objectを一括取得してローカル照合しますが、既存のPLATEAU建物確定結果や
GPKGを変更しません。施設本体と駐輪場・入口等を分離し、同一施設を表す
node/way/relationを候補グループへ統合した上で、高確度way/relationのgeometryだけを
追加取得します。

```bash
python Museum/source/scripts/build_museum_osm_matches.py
```

Museum GPKGが既にある場合は`--museum-gpkg`で指定します。既存確定施設は
`audit_only`、未確定施設は`candidate_discovery`として出力されます。詳しい出力列、
offline再実行、判定条件は[source README](source/README.md#openstreetmap照合パイロット)
を参照してください。

住所正規化では全角・半角、丁目・番・号、ハイフンを統一し、先頭の`東京都`の有無を吸収します。ビル名・階数を除いた敷地住所は候補抽出にだけ使い、自動確定には使いません。数字を含まない休館注記等は住所として扱いません。

`bldg:usage=422`は「文教厚生施設」という広い分類なので、それだけではMuseum候補にしません。全国標準の`uro:detailedUsage=422302/422305`は博物館・動物園を示す強い候補根拠ですが、ソース施設との一致なしに自動確定はしません。

東京都23区のPLATEAUデータでは、`BuildingDetailAttribute_detailedUsage.xml`を参照しながら、東京都土地利用現況調査の細分類コードが格納されています。`1122`は「文化施設」であり、博物館だけでなく図書館・文化ホール等を含み得るため、自動確定には使いません。東京都コード（自治体コード先頭`13`）かつ`detailedUsage=1122`の建物を`tokyo_culture_facility`候補として別レイヤへ出力します。動物園・水族館等は必ずしも`1122`とは限らないため、manifest起点の照合と名称候補抽出を併用します。

API検索で取得されるGMLメッシュは行政界をまたぐことがあります。照合時には、取得要求・キャッシュ階層の自治体コードより、PLATEAU建物住所に明記された区市町村を優先します。出力ポリゴンには`source_city_code`、`matching_city_code`、`matching_city_method`を併記し、住所から自治体を補正した件数をsummary JSONの`selected_building_address_municipality_override_count`へ記録します。

summary JSONの`plateau_duplicate_audit`には、Building要素数、一意ID数、重複ID数、余分な出現数、意味的内容差、埋込自治体コードで解決した件数、LOD3/LOD4欠損明示メタデータだけの差として解決した件数（`metadata_normalized_duplicate_conflict_count`）、名前空間URI差として解決した件数（`namespace_normalized_duplicate_conflict_count`）、兄弟要素順序差として解決した件数（`order_normalized_duplicate_conflict_count`）、未解決競合件数とサンプルを記録します。`building_candidate_method_counts`には、`tokyo_culture_facility`、`detailed_usage_exact_museum`、`name_keyword`ごとの候補根拠件数を記録します。

## 所在地Collector（PLATEAU照合より先に実行）

現在の最優先KPIは、245施設に対する検証済み所在地の特定率です。PLATEAU Building確定件数は後段のKPIとして扱います。最低基準を60%超（148件以上）、理想基準を90%超（221件以上）とします。

標準Collectorを実行します。

```bash
python Museum/source/scripts/build_museum_locations.py
```

既存Manifestと確認済みoverrideを先に採用し、文化遺産オンラインの東京都施設詳細を`正規化名称 + 5桁自治体コード`の完全一致で照合します。その後、未解決施設だけをcanonical施設の公式URLから探索します。同順位の住所競合や完全一致しない候補は自動採用しません。

日本博物館協会、文化庁、自治体等の公式一覧をCSVで追加する場合は次のように指定します。

```bash
python Museum/source/scripts/build_museum_locations.py \
  --location-source-csv /path/to/authoritative_museum_locations.csv
```

ABRジオコーダーをローカルで起動している場合は、同時に座標を取得できます。

```bash
python Museum/source/scripts/build_museum_locations.py \
  --abr-api-base http://localhost:3000
```

出力は`museum_location_enrichment.csv`、`museum_location_review.csv`、`museum_location_candidates.csv`、`museum_location_summary.json`です。GPKG生成ツールは`review_status=accepted`の所在地overlayだけを使用します。公式ページHTMLは既定では保存しません。利用条件上保存可能と確認した場合だけ`--cache-official-pages`を指定します。

従来の`enrich_museum_locations.py`は新Collectorが内部利用する互換部品として残しますが、単独実行は標準手順から廃止します。

## 博物館所在階・収蔵庫情報

入力は`Museum/source/config/facility_spaces.csv`です。複数階にまたがる施設は1行へ畳み込まず、用途・連続階範囲ごとに複数行で記録します。

```csv
space_id,museum_id,space_type,space_name,presence_status,floor_label,floor_min,floor_max,is_basement,floor_elevation_min_m,floor_elevation_max_m,collections_present,source_url,source_authority,source_date,retrieved_at,review_status,notes
MSP-example-1,MUS-example,museum_occupancy,展示室,yes,1-3F,1,3,no,0,9,yes,https://example.jp,facility_official,2026-09-06,2026-09-06T00:00:00Z,accepted,
MSP-example-2,MUS-example,collection_storage,収蔵庫,yes,B1,-1,-1,yes,-3,0,yes,https://example.jp,facility_official,2026-09-06,2026-09-06T00:00:00Z,accepted,
```

`space_type`は少なくとも`museum_occupancy`、`exhibition`、`collection_storage`を区別します。収蔵庫がないことを公式に確認した場合は`space_type=collection_storage`、`presence_status=no`として記録します。未調査は行を追加せず`unknown`のままにします。

階数は地下を負数（B1=`-1`）、地上1階を`1`として記録します。`floor_elevation_min_m`と`floor_elevation_max_m`はPLATEAU建物地盤面からの相対高さです。確認できない場合は空欄とし、建物総高さや階数から自動推定しません。

## 出力構造

### 空間レイヤ

| レイヤ | 役割 |
|---|---|
| `museum_buildings_footprint` | 自動確定したMuseum建物ポリゴン。名称表示、既存ハザードとの重ね合わせ、災害種別・深度別の色分けに使う主レイヤ |
| `museum_building_candidates` | 住所一致のみ、PLATEAU詳細用途のみ、名称キーワードのみ等の確認候補。主レイヤには混ぜない |
| `museum_facility_points` | ABR・公式情報由来の施設点。建物未確定施設も地図表示でき、`location_coordinate_use`で精度を選別可能 |

主レイヤには、表示・分類・リスク可視化で頻繁に使う次の属性を直接持たせます。

| 属性群 | GPKG列 | 内容 |
|---|---|---|
| 表示 | `display_name` | Museum施設名。PLATEAU単独候補ではPLATEAU建物名称 |
| Museum識別 | `museum_ids`, `museum_names`, `museum_count` | 1建物に対応する施設ID・名称・件数 |
| 施設分類 | `facility_types`, `law_statuses`, `ownership_types`, `operator_names` | 施設種別、博物館法区分、設置主体、運営者 |
| 連絡先 | `facility_address`, `phone`, `official_url` | manifest由来の所在地・電話・公式URL |
| 施設所在階 | `floor_data_status`, `facility_floor_labels`, `facility_floor_min`, `facility_floor_max`, `facility_spans_multiple_floors` | 博物館機能が所在する階と複数階フラグ |
| 収蔵庫 | `collection_storage_status`, `storage_floor_labels`, `storage_floor_min`, `storage_floor_max`, `storage_in_basement` | 収蔵庫の存在・所在階・地下階フラグ |
| 照合 | `match_status`, `match_methods`, `source_count`, `review_required` | 照合状態、根拠、原典レコード数、要確認フラグ |
| PLATEAU識別 | `gml_id`, `building_id`, `city_code`, `source_city_code`, `matching_city_code`, `matching_city_method`, `file_code`, `source_gml` | 建物、取得元自治体、住所から判定した照合自治体、原典GMLの識別情報 |
| PLATEAU名称・住所 | `plateau_name`, `plateau_address` | PLATEAU建物属性 |
| 用途 | `usage_code`, `usage_label`, `usage_codespace`, `detailed_usage_code`, `detailed_usage_label`, `detailed_usage_codespace` | 建物用途と詳細用途のコード・名称・コードリスト参照 |
| 物理属性 | `measured_height_m`, `storeys_above`, `storeys_below`, `year_of_construction`, `structure_type_code`, `structure_type_label`, `fireproof_type_code`, `fireproof_type_label`, `footprint_area_m2` | 高さ、階数、建築年、構造、耐火構造、測地面積 |
| 全災害 | `has_any_hazard`, `hazard_count`, `hazard_types`, `disaster_risks_json` | 災害属性の有無・件数・種別・非損失JSON |
| 河川洪水 | `has_river_flood`, `river_flood_count`, `river_flood_max_depth_m`, `river_flood_max_duration_h`, `river_flood_worst_rank`, `river_flood_descriptions`, `river_flood_water_systems`, `river_flood_ranks`, `river_flood_rank_orgs`, `river_flood_admin_types`, `river_flood_scales` | 水系別レコードを建物単位に集約した河川洪水情報 |
| その他浸水 | `has_inland_flood`, `inland_flood_max_depth_m`, `inland_flood_worst_rank`, `has_high_tide`, `high_tide_max_depth_m`, `high_tide_worst_rank`, `has_tsunami`, `tsunami_max_depth_m`, `tsunami_worst_rank`, `has_reservoir_flood`, `reservoir_flood_max_depth_m`, `reservoir_flood_worst_rank` | 内水、高潮、津波、ため池の有無・最大深度・最悪ランク |
| 土砂災害 | `has_landslide`, `landslide_count`, `landslide_descriptions`, `landslide_area_types`, `landslide_worst_class` | 警戒区域等の区分 |

災害横断の独自「総合リスクスコア」は作りません。根拠の異なる災害を恣意的に一つの値へ合成せず、利用者が災害種別ごとに表示・抽出できる構造です。

### 所在階と浸水深の判定

数値浸水深がある場合、記録済みの`floor_elevation_min_m`と比較します。階高が不明な場合は、地下階または1階を`potentially_exposed`、2階以上を`undetermined_floor_elevation`として扱い、階高を仮定しません。収蔵庫被害候補は`space_type='collection_storage' AND exposure_status='potentially_exposed'`で抽出できます。河川洪水は水系ごとのレコードを分離したまま評価します。

### 将来の地震リスク評価

現段階では地震リスク値を生成しません。将来、震度または地震動加速度（PGA等）を建物ポリゴンへ結合し、PLATEAU由来の`structure_type_code`、`year_of_construction`、`storeys_above`、`storeys_below`、`measured_height_m`等と組み合わせます。モデル名・バージョン・入力地震動・脆弱性曲線を別テーブルに記録し、浸水判定と同じ列へ混合しない設計とします。

### 正規化テーブル

| テーブル | 粒度・用途 |
|---|---|
| `museum_facilities` | 1行=1確定施設。245施設の名称、所在地、種別、法的位置づけ、所有区分、ソース統合結果 |
| `museum_source_records` | 1行=1取得レコード。295件すべてとreconciliation結果を保持するprovenance表 |
| `museum_facility_spaces` | 1行=施設×用途×階範囲。展示室・収蔵庫等を複数階のまま保持 |
| `museum_space_hazard_assessment` | 1行=施設空間×建物×PLATEAU災害レコード。浸水深、所在階、収蔵庫、判定根拠を保持 |
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
python -m unittest Museum.source.tests.test_build_museum_locations -v
python -m unittest Museum.source.tests.test_enrich_museum_locations -v
```

実データでは、最初に`--dry-run`でファイル数、走査建物数、確定・候補・未照合件数を確認してください。
