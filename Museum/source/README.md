# Museumソース収集・所在地Collector

`build_museum_manifest.py` は、東京都内の博物館・資料館・美術館・動物園・水族館等について、複数の公開名簿から候補レコードを収集し、名称と自治体コードを正規化したうえで、重複照合用のManifestを生成するスクリプトです。

`scripts/build_museum_locations.py` v0.2.5は、245件のcanonical施設について、PLATEAU照合より先に所在地の特定率を高めるための標準Collectorです。既存Manifest、確認済みoverride、文化遺産オンライン、追加の公式一覧CSV、施設公式ページを優先順位付きで統合し、原データを変更せず`data/museum_location_enrichment.csv`へoverlayとして保存します。自動受理できない候補は`data/museum_location_review.csv`へ分離します。v0.2.5ではABR v2の配列内`result`と、ABR v3のGeoJSON FeatureCollectionの両方を展開し、自治体コードと座標値域を検証してから座標を採用します。

所在地抽出では、住所の直後に連結されたTEL・FAX、交通案内、バリアフリー案内、建物の設計・施工情報等を除去し、`国立市…`のように都道府県名が省略された公式表記へ`東京都`を補います。阿拉伯数字のみならず、`青海二丁目地先`のような正式住所も受理します。際限なく抽出できない不正値や本文混入値は自動採用せず、候補と出典URLをreview出力に残します。照合は`正規化名称 + 5桁自治体コード`の完全一致に限定し、あいまい一致は行いません。同順位の出典が異なる住所を示した場合は自動採用しません。施設別の確認済み所在地と公式所在地ページは`config/location_source_overrides.csv`で明示できます。

従来の`scripts/enrich_museum_locations.py`はHTML抽出・ABR接続の互換部品として残しますが、単独実行は標準フローから廃止します。

文化庁「全国の博物館」の登録博物館・指定施設を中核データとし、博物館関係団体、自治体、地域ミュージアムネットワーク等を追加ソースとして扱います。令和6年度社会教育調査における東京都の210施設は規模の参照値であり、出力件数を210へ一致させる処理は行いません。

## 処理範囲

このスクリプトが行う処理は次のとおりです。

1. `config/sources.json` に登録された公開ページを取得する。
2. 情報源ごとのHTML構造に対応したCollectorで施設情報を抽出する。
3. 施設名称、所在地、郵便番号、電話番号、公式URL等を共通スキーマへ正規化する。
4. 所在地から東京都の5桁自治体コードを付与する。
5. `正規化名称 + 5桁自治体コード` の完全一致で重複を整理する。
6. 候補データ、照合結果、情報源台帳、集計、Markdown Manifestを出力する。
7. 取得ページのキャッシュとSHA-256を用いて取得結果を追跡可能にする。

住所のジオコーディング、PLATEAU建物ポリゴンとの空間照合、施設種別の最終判定は、このスクリプトの処理範囲外です。

## ディレクトリ構成

```text
Museum/source/
├── README.md
├── requirements.txt
├── config/
│   ├── sources.json
│   ├── tokyo_municipalities.csv
│   ├── name_aliases.csv
│   ├── location_source_overrides.csv
│   └── location_source_records.example.csv
├── scripts/
│   ├── build_museum_manifest.py
│   ├── build_museum_locations.py
│   └── enrich_museum_locations.py       # 内部互換部品
├── tests/
│   ├── test_build_museum_manifest.py
│   ├── test_build_museum_locations.py
│   └── test_enrich_museum_locations.py
├── cache/                         # 取得HTML。Git管理対象外
└── data/
    ├── museum_sources_manifest.csv
    ├── museum_candidates.csv
    ├── museum_reconciliation.csv
    ├── museum_location_enrichment.csv
    ├── museum_location_review.csv
    ├── museum_location_candidates.csv
    ├── museum_location_summary.json
    ├── summary.json
    └── MUSEUM_DATA_MANIFEST.md
```

## 動作環境

- Python 3.10以上を推奨
- インターネット接続（オンライン取得時）
- `lxml`

依存パッケージをインストールします。

```bash
cd /path/to/PLATEAU_heritage/Museum/source
python -m pip install -r requirements.txt
```

## 基本実行

`Museum/source/` をカレントディレクトリとして実行する場合：

```bash
python scripts/build_museum_manifest.py
```

リポジトリルートから実行する場合：

```bash
python Museum/source/scripts/build_museum_manifest.py
```

既定では、キャッシュが存在する情報源はキャッシュを使用し、存在しない情報源だけをネットワークから取得します。出力は `Museum/source/data/` に作成されます。

## 所在地Collector（標準フロー）

Manifest生成後に実行します。

```bash
python Museum/source/scripts/build_museum_locations.py
```

Collectorは次の順で所在地候補を評価します。

| 優先度 | 出典 | 採用条件 |
|---:|---|---|
| 120 | `location_source_overrides.csv` | 人が公式ページを確認済み |
| 110 | 既存canonical Manifest | 中核・追加ソースが保持する既存住所 |
| 100 | canonical施設の公式ページ | 施設名・自治体を支持する単一住所 |
| 90 | `--location-source-csv` | 追加した公的・公式一覧との完全一致 |
| 80 | 文化遺産オンライン | 施設詳細の名称＋自治体コード完全一致 |

文化遺産オンラインは東京都の施設一覧と各施設詳細ページを構造化ソースとして取得します。未解決施設だけに公式ページ探索を実行するため、245件すべてを一律に検索する方式ではありません。公式HTMLは既定では保存せず、利用条件上保存可能と確認した場合だけ`--cache-official-pages`を指定します。文化遺産オンラインの一覧・詳細は再現性確保のため専用cacheへ保存します。

日本博物館協会、文化庁、自治体等から別途取得した公式一覧CSVを追加する場合は、`config/location_source_records.example.csv`を複製して実データへ置換し、次のように指定します。オプションは複数回指定できます。

```bash
python Museum/source/scripts/build_museum_locations.py \
  --location-source-csv /path/to/japan_museum_association_tokyo.csv \
  --location-source-csv /path/to/municipal_official_museums.csv
```

必須列は`facility_name,address`です。`municipality_code`が空の場合のみ、住所に含まれる東京都区市町村名から5桁コードを保守的に補います。`source_id`、`source_url`、`source_authority`、`priority`等も保持できます。

現在のローカル入力だけを使う安全な基準値は、所在地確定121/245件（49.4%）です。外部取得による改善目標は、60%超の148件以上を最低基準、90%超の221件以上を理想基準とします。実行ごとに`museum_location_summary.json`へ件数、率、出典別採用件数、目標到達可否を記録します。

主なオプションは次のとおりです。

| オプション | 内容 |
|---|---|
| `--refresh` | 文化遺産オンラインのcacheを再取得する |
| `--offline` | 構造化ソースをcacheだけで処理する |
| `--location-source-csv PATH` | 追加の公式所在地一覧を読み込む（複数指定可） |
| `--skip-cultural-online` | 文化遺産オンラインを使用しない |
| `--skip-official-fallback` | 残件の施設公式ページ探索を行わない |
| `--cache-official-pages` | 利用条件確認済みの場合だけ公式ページHTMLを保存する |
| `--abr-api-base URL` | 受理住所をデジタル庁ABR互換APIで座標化する（`ABR_GEOCODER_URL`でも指定可） |

出力は次の4ファイルです。

| ファイル | 内容 |
|---|---|
| `museum_location_enrichment.csv` | 確定所在地。GPKG生成ツールが直接読むoverlay |
| `museum_location_review.csv` | 未解決または住所競合で要確認の施設 |
| `museum_location_candidates.csv` | 採用・不採用・未照合を含む候補監査表 |
| `museum_location_summary.json` | KPI、出典別件数、取得状態 |

所在地確定後、博物館機能・展示室・収蔵庫の所在階は`config/facility_spaces.csv`へ別途記録します。これは建物自体の地上・地下階数とは異なる施設内空間情報です。複数階は用途・連続階範囲ごとに複数行とし、未調査の収蔵庫を「なし」と推定しません。GPKG生成時に`museum_facility_spaces`と`museum_space_hazard_assessment`へ変換されます。

## OpenStreetMap照合パイロット

`scripts/build_museum_osm_matches.py` v0.1.1は、東京都内の博物館・美術館・資料館・
動物園・水族館等をOverpass APIから一括取得し、canonical 245施設とローカルで
照合します。この段階ではOSMを候補・監査根拠としてのみ使用し、canonical一覧、
所在地overlay、PLATEAU建物リンク、GPKGを変更しません。

施設そのものと、施設名を名称に含む入口・案内所・駐輪場・店舗等を区別します。
また同じ施設を表すnode、way、relationは、Wikidata、公式サイト、完全名称と距離を
用いて一つの候補グループへまとめます。高確度判定は個別object数ではなく、候補
グループ数に対して行います。

```bash
cd /Users/noguchiatsushi/Documents/GitHub/PLATEAU_heritage
source .venv/bin/activate

python Museum/source/scripts/build_museum_osm_matches.py
```

初回取得結果は`Museum/source/cache/osm/tokyo_museum.json`へ保存されます。
高確度グループに含まれるway/relationのgeometryは、追加の小規模Overpass queryで
取得して`tokyo_museum_shortlist_geometry.json`へ保存します。v0.1.0の東京都全域
cacheはそのまま再利用できるため、v0.1.1の初回実行ではshortlist geometryだけが
ネットワーク取得されます。

両方のcacheを作成した後は、ネットワークなしで再現実行できます。

```bash
python Museum/source/scripts/build_museum_osm_matches.py --offline
```

geometryをまだ取得せず、既存の東京都全域cacheだけで判定を検証する場合は次を使います。

```bash
python Museum/source/scripts/build_museum_osm_matches.py \
  --offline \
  --skip-geometry
```

Overpassから再取得する場合だけ`--refresh`を使用します。

```bash
python Museum/source/scripts/build_museum_osm_matches.py --refresh
```

既にMuseum GPKGを生成済みの場合は指定できます。`museum_building_links`の
`confirmed`施設を`audit_only`、それ以外を`candidate_discovery`として区別します。
OSM結果が既存のPLATEAU確定を上書きすることはありません。

```bash
python Museum/source/scripts/build_museum_osm_matches.py \
  --museum-gpkg \
  "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_museum_hazards.gpkg"
```

出力は次の3ファイルです。

| ファイル | 内容 |
|---|---|
| `museum_osm_candidates.csv` | 候補リンク、object role、候補group、座標、名称・自治体・距離・公式URL、Wikidata、OSM source、geometry有無 |
| `museum_osm_audit.csv` | canonical施設ごとの候補group数、選択object、ABR座標競合。全245施設を1行ずつ記録 |
| `museum_osm_summary.json` | 取得モード、OSM件数、照合件数、監査・候補探索別KPI |

`high_confidence_unique`は、正規化名称が完全一致し、かつ自治体、公式URLまたは
既存の検証済み座標との距離が施設を支持するOSM施設グループが一つだけの場合です。
この値もPLATEAU建物の確定ではなく、次段の建物候補生成に利用できる高確度な
OSM施設位置を意味します。施設名を借用した周辺POIだけでは高確度にしません。
候補グループが複数なら`multiple_high_confidence`、名称候補だけ
なら`candidate_only`、候補なしは`no_candidate`です。

`coordinate_conflict=true`は、完全名称と自治体または公式URLがOSM施設を支持する
一方、ABR座標から2kmを超えて離れる場合です。OSM・ABRのどちらかを自動採用せず、
座標不一致の確認対象として残します。

OSMデータはODbLです。出力にはOSM object URL、取得データのSHA-256、取得日時を
保持します。成果物を配布するときはOpenStreetMapとcontributorsへの帰属表示、
ODbLの明示、および派生データベースに対するライセンス条件の確認が必要です。

## コマンドラインオプション

| オプション | 既定値 | 内容 |
|---|---:|---|
| `--offline` | 無効 | ネットワークへ接続せず、`cache/` 内のHTMLだけで再生成する |
| `--refresh` | 無効 | 既存キャッシュを使用せず、対応する公開ページを再取得する |
| `--workers N` | `4` | 情報源を並列取得・解析するワーカー数 |
| `--output-dir PATH` | `data/` | CSV、JSON、Markdownの出力先を変更する |
| `-h`, `--help` | — | ヘルプを表示する |

### 公開ページを再取得する

```bash
python scripts/build_museum_manifest.py --refresh
```

### キャッシュだけで再生成する

```bash
python scripts/build_museum_manifest.py --offline
```

キャッシュが不足している情報源は取得エラーとしてManifestへ記録されます。中核ソースを取得できなかった場合は既存出力を置き換えず、終了コード `2` で停止します。

### 検証用ディレクトリへ出力する

```bash
python scripts/build_museum_manifest.py \
  --offline \
  --output-dir /tmp/museum-manifest-check
```

この方法により、既存の `data/` を変更せず再現性を確認できます。

## 入力設定

### `config/sources.json`

情報源台帳とCollector設定です。主なフィールドは次のとおりです。

| フィールド | 内容 |
|---|---|
| `source_id` | 情報源の安定識別子。キャッシュファイル名にも使用する |
| `source_name` | 人間可読の情報源名 |
| `source_role` | `core`、`supplement`、`discovery`、`reference` |
| `source_tier` | 情報源の位置づけを示す `A`〜`D` または参照値の `R` |
| `collector` | HTML解析関数名。未実装・台帳のみの場合は `manifest_only` |
| `url` | 取得元URL |
| `scope` | 対象範囲 |
| `reference_count` | 公開ページ等で確認した参考件数 |
| `notes` | 採用条件や注意事項 |

`collector` が `manifest_only` の情報源は台帳とMarkdown Manifestには掲載されますが、施設レコードの自動取得は行いません。

### `config/tokyo_municipalities.csv`

東京都62区市町村の5桁自治体コードと名称の対応表です。所在地文字列に自治体名が含まれる場合、その自治体コードを候補レコードへ付与します。

### `config/name_aliases.csv`

明示的に確認した名称表記だけを統一する別名表です。

```csv
alias,canonical_name,reason
東京都恩賜上野動物園,恩賜上野動物園,運営主体接頭辞の差
```

曖昧な類似名称をこの表へ自動追加する処理はありません。

## 自動取得対象

現行実装は次のCollectorを備えています。

| Collector | 対象 |
|---|---|
| `bunka_core` | 文化庁「全国の博物館」の東京都登録博物館・指定施設 |
| `jcsm` | 全国科学博物館協議会の東京都加盟館 |
| `jaza_table` | 日本動物園水族館協会の東京都所在動物園・水族館 |
| `jaa` | 日本水族館協会の所在地「東京」の正会員 |
| `chiyoda` | 千代田ミュージアムネットワーク参加施設 |
| `bunkyo` | 文の京ミュージアムネットワーク加入施設 |
| `minato` | 港区ミュージアムネットワーク加盟館 |

文化遺産オンラインは施設Manifest側では`manifest_only`ですが、所在地Collectorが東京都一覧と施設詳細を直接取得します。日本博物館協会など、安定した一括取得方法をまだ確定していない情報源は`manifest_only`のままとし、入手した公式一覧を`--location-source-csv`で追加できます。

## 名称正規化

施設名称には次の処理を順番に適用します。

1. Unicode NFKC正規化
2. 全角空白を含む連続空白の整理
3. 先頭の `◎`、`○`、`〇`、`●` の除去
4. 「休館中」等の状態注記の除去
5. 空白、中黒、読点、句点、ハイフン類の除去
6. Unicode対応の小文字化（`casefold`）
7. `name_aliases.csv` による明示的な別名変換

元名称は `facility_name_raw` に保持し、正規化値は `facility_name_normalized` に格納します。

## 重複照合

自動照合キーは次の完全一致です。

```text
facility_name_normalized + "|" + municipality_code
```

| `match_status` | 意味 |
|---|---|
| `core_unique` | 中核ソースのレコード |
| `duplicate_core` | 追加ソースのレコードが中核と完全一致 |
| `supplement_unique` | 中核と一致せず、追加候補として初出 |
| `duplicate_supplement` | 追加ソース間で同じ照合キーが既出 |
| `needs_review` | 自治体コードがなく、自動照合しない |

次の方法による自動統合は行いません。

- 編集距離等による名称の曖昧一致
- 住所の部分一致や近似一致
- 電話番号だけの一致
- 同一建物・複合施設であることの推定
- 改称、移転、分館・本館関係の推定

これらは `museum_reconciliation.csv` を用いて目視確認します。

## 出力

### `data/museum_sources_manifest.csv`

情報源の定義と今回の取得結果です。

| 主な列 | 内容 |
|---|---|
| `source_id` | 情報源識別子 |
| `source_role` / `source_tier` | 情報源の役割とTier |
| `retrieved_at` | 取得日時（UTC） |
| `retrieval_status` | `retrieved_network`、`retrieved_cache`、`manifest_only`、`error` |
| `record_count` | 抽出レコード数 |
| `snapshot_sha256` | 取得HTMLのSHA-256 |

### `data/museum_candidates.csv`

全情報源から取得した未統合の施設レコードです。Excel等で開きやすいようUTF-8 BOM付きCSVで出力します。

| 列 | 内容 |
|---|---|
| `record_id` | 情報源・正規化名称・自治体コード・住所から生成する安定ID |
| `source_id` | 取得元情報源 |
| `facility_name_raw` | 公開ページ上の名称 |
| `facility_name_normalized` | 照合用の正規化名称 |
| `municipality_code` / `municipality_name` | 5桁自治体コードと自治体名 |
| `postal_code` / `address` | 取得できた郵便番号と所在地 |
| `phone` / `official_url` | 電話番号と施設公式URL |
| `facility_type` | 情報源から設定した暫定施設種別 |
| `museum_law_status` | `registered`、`designated_facility` 等 |
| `record_status` | 中核、追加候補、休館、要確認等の取得時状態 |
| `retrieved_at` / `source_url` | 取得日時と出典URL |

### `data/museum_reconciliation.csv`

各候補レコードの重複照合結果です。

| 主な列 | 内容 |
|---|---|
| `canonical_facility_id` | 照合キーから生成した施設候補ID |
| `match_key` | 正規化名称と自治体コードを結合した照合キー |
| `match_status` | 照合結果 |
| `matched_record_id` | 一致先レコードID |
| `review_required` | 自動照合できず人手確認が必要か |

### `data/summary.json`

中核件数、追加ソース件数、重複件数、追加ユニーク候補数、要確認数、暫定ユニーク推計と210施設との差を機械可読形式で記録します。

### `data/MUSEUM_DATA_MANIFEST.md`

調査方針、集計結果、情報源別取得状況、照合規則、取得エラー、データの限界をまとめた人間可読のManifestです。

## キャッシュと再現性

取得HTMLは `cache/<source_id>.html` に保存されます。`cache/` は第三者サイトの取得内容を含むためGit管理対象外です。

再現性は次の情報で確保します。

- 情報源URLと取得日時
- 取得HTMLのSHA-256
- 情報源ごとの抽出件数
- 入力設定ファイル
- 同一キャッシュを利用する `--offline` 再実行
- 正規化・照合ロジックの単体テスト

公開ページは更新されるため、`--refresh` の結果が過去のManifestと一致するとは限りません。定期再取得時は、更新前後の `museum_sources_manifest.csv`、`summary.json`、`museum_reconciliation.csv` を比較してください。

## テスト

リポジトリルートから実行します。

```bash
python -m unittest discover -s Museum/source/tests -v
```

現在のテストは、名称正規化と「名称が同一でも自治体コードが異なる場合は重複としない」ことを確認します。

## 初回実行結果の例

2026年9月4日の初回取得では、次の結果になりました。

| 項目 | 件数 |
|---|---:|
| 登録博物館（中核） | 83 |
| 指定施設（中核） | 50 |
| 中核合計 | 133 |
| 追加ソース取得レコード | 162 |
| 中核との重複 | 43 |
| 追加ソース間の重複 | 3 |
| 追加ユニーク候補 | 112 |
| 要確認 | 4 |
| 中核＋追加候補の暫定ユニーク推計 | 245 |
| R6社会教育調査の参照値 | 210 |

この245件は、完全一致で整理した処理時点の候補推計です。地域ネットワークには庭園、図書館、文書館、ギャラリー等が含まれる場合があるため、博物館災害リスク評価の最終対象数ではありません。

## Collectorの追加

新しい情報源を自動取得対象にする場合は、次の変更を行います。

1. `config/sources.json` に情報源を追加する。
2. `collect_<source>()` を実装し、共通の `candidate()` でレコードを生成する。
3. `COLLECTORS` にCollector名を登録する。
4. HTML構造と期待件数に対するテストを追加する。
5. `--output-dir` を用いて既存結果と差分確認する。

公開ページのHTML構造変更により誤抽出が起きないよう、対象セクション、列数、所在地条件等を明示して実装してください。

## 既知の制約

- 中核の登録・指定一覧には完全な住所がないため、PLATEAU建物ポリゴンとの照合前に公式サイト等による所在地補完が必要です。
- `manifest_only` の情報源は自動取得件数に含まれません。
- 自治体コードを確定できないレコードは、自動重複整理から除外されます。
- 情報源によって「博物館」「文化施設」「展示施設」の対象範囲が異なります。
- 暫定施設種別は情報源単位で設定されており、最終的な法的位置づけや館種分類を保証しません。
