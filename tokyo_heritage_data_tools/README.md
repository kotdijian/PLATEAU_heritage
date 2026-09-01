# Tokyo Heritage Data Tools v0.2.2

東京都内の文化財データについて、**国指定等**と**区市町村指定**の取得・raw保存・正規化を行い、
**PLATEAU Heritage-GML Extractor v0.5.x** へ渡せる入力CSVを生成する前処理ツールです。

東京都教育庁の `130001_cultural_property.csv` は対象外です。
既に利用可能な東京都指定文化財CSVを再取得・再正規化せず、そのまま Extractor に渡します。

## v0.2.x の要点

**v0.2.2 の主な修正:**

- 東京都Open Data APIへのPOSTを `application/json` + JSON bodyで送信し、415エラーを回避。
- API取得時に `limit` / `offset` を用いて全件取得し、`hits` を1つの `data.json` に統合。
- 直接CSVが404等で失敗した場合、manifestにAPI endpointがあれば自動fallback。
- 杉並区 `13115` のCSV URLを2026年4月更新版へ更新。
- 自治体コード文字列から非数字を除去する正規表現も修正。

**v0.2.1:** `heritage-collect municipal --area-code 13` を拒否していたコード検証の正規表現バグを修正。


- Extractor v0.5.x のcanonical入力に合わせて `address_detail` を追加しました。
- 東京都標準CSV等の `方書` を `address_detail` として保持します。
- `movable` は**意味分類だけ**とし、住所単位のmovable-group処理を前提にしません。
- `movable` の `geometry_role` は他の非建造物と同じ `representative_point` です。
- `歴史資料` は種類だけでは可動文化財とみなさず `entity_class=point` とします。
- `美術工芸品・考古資料` は複合型を保持できます。
- Building Complex、共有座標判定、PLATEAU Building照合はこのツールでは行わず、Extractor側で処理します。

---

## 1. システム内での位置づけ

```text
Tokyo Heritage Data Tools
    │
    │  国指定・自治体指定文化財の取得 / 正規化
    ▼
national.csv / municipal.csv
    │
    ├──────────────┐
    │              │
    │      130001_cultural_property.csv
    │      （東京都指定・原データのまま）
    │              │
    └──────┬───────┘
           ▼
PLATEAU Heritage-GML Extractor v0.5.x
    │
    ├─ Building直接照合
    ├─ Heritage Complex
    ├─ shared_complex_coordinate判定
    └─ GML + GPKG
```

このツールは**入力データ準備層**です。次は行いません。

- PLATEAU API / CityGML取得
- PLATEAU Buildingとの照合
- Building Complexの生成
- buffer / nearest neighbour / 推定範囲の生成
- 自治体GPKGの都道府県単位統合

---

## 2. コマンド

利用者向けコマンドは2本です。

```text
heritage-collect
heritage-normalize
```

国と自治体では取得方法が異なるため、内部実装は分離しています。

```text
heritage_data_tools/
├── collectors/
│   ├── national.py
│   └── municipal.py
└── normalizers/
    ├── national.py
    ├── municipal.py
    └── common.py
```

---

## 3. インストール

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

既存v0.1.xから更新する場合:

```bash
python -m pip install . --force-reinstall
```

---

## 4. 推奨ディレクトリ

```text
13Tokyo/
├── prefectural/
│   └── 130001_cultural_property.csv   # 既存・変更しない
│
├── raw/
│   ├── national/
│   └── municipal/
│
├── tidy/
│   ├── national.csv
│   ├── municipal.csv
│   └── review / report files ...
│
└── gml_input/
    ├── 130001_cultural_property.csv
    ├── national.csv
    └── municipal.csv
```

---

# Part A — 区市町村データ

## 5. 区市町村データの取得

東京都内自治体について、調査済み取得先を次のmanifestに保持しています。

```text
manifests/tokyo_municipal_sources_2026-08-27.yml
```

manifestには主に次を保持します。

- 5桁自治体コード
- 組織名
- データセット名
- CSV URL
- 東京都Open Data API JSON endpoint
- HTTP method
- 調査時の注記

東京都全体:

```bash
heritage-collect municipal \
  --manifest manifests/tokyo_municipal_sources_2026-08-27.yml \
  --area-code 13 \
  --output ./13Tokyo/raw/municipal
```

自治体を限定:

```bash
heritage-collect municipal \
  --manifest manifests/tokyo_municipal_sources_2026-08-27.yml \
  --area-code 13 \
  --codes 13102 13106 \
  --output ./13Tokyo/raw/municipal
```

取得優先順位:

1. `source_csv_url` があれば公式/直接CSVを試行
2. CSVが404・接続失敗等で取得できず、`json_endpoint` があれば東京都Open Data APIへ自動fallback
3. CSV URLがなくAPI endpointのみの場合はAPIを使用
4. どちらもなければ `no_downloadable_source`
5. 東京都教育庁 `13000` は意図的に除外

東京都Open Data APIはPOST時にJSONリクエストとして送信します。

```text
Accept: application/json
Content-Type: application/json
body: {}
```

取得件数は `limit` / `offset` でページングし、レスポンスの `total` まで取得します。
既定の1回あたり件数は1000です。変更する場合:

```bash
heritage-collect municipal \
  --manifest manifests/tokyo_municipal_sources_2026-08-27.yml \
  --area-code 13 \
  --output ./13Tokyo/raw/municipal \
  --api-page-size 500
```

複数ページを取得した場合も、raw出力は1自治体につき1つの `data.json` とし、
全レコードを `hits` にまとめます。`source.json` にはfallback前の失敗も `attempts` として記録します。

出力例:

```text
raw/municipal/
├── 13102/
│   ├── data.csv
│   └── source.json
├── 13116/
│   ├── data.json
│   └── source.json
└── collection_manifest.json
```

既存 `data.csv` / `data.json` はデフォルトで再取得しません。したがって、前回成功済みの自治体を保持したまま、失敗自治体だけを修正版collectorで再試行できます。全件を取り直す場合だけ `--overwrite` を使います。

杉並区 `13115` のmanifestは、2026年4月27日更新ページで公開されている `文化財一覧_令和8年4月時点` CSVへ更新しています。

---

## 6. 区市町村データの正規化

```bash
heritage-normalize municipal \
  --input ./13Tokyo/raw/municipal \
  --output ./13Tokyo/tidy
```

自治体データには国・都・区市町村指定が混在する可能性があるため、
**「自治体から取得したデータ = 自治体指定」にはしません。**

明示的な語彙から、

```text
municipal
prefectural
national
ambiguous
```

を判定します。

主な出力:

```text
tidy/
├── municipal.csv
├── municipal_all_normalized.csv
├── municipal_excluded_cross_level.csv
├── municipal_needs_review.csv
├── municipal_normalization_report.csv
└── municipal_normalization_summary.json
```

- `municipal.csv`: 自治体指定と明示的に判定できたもの
- `municipal_excluded_cross_level.csv`: 国・都指定と判定したもの
- `municipal_needs_review.csv`: 指定主体を安全に確定できなかったもの

曖昧なレコードを自動的に自治体指定へ昇格させません。

必要なら `config/municipal_normalization.example.yml` をコピーし、個別自治体の既知の意味論を追加できます。

---

# Part B — 国指定等データ

## 7. 文化遺産オンラインから取得

東京都の国指定等データについて、文化遺産オンラインの
`国指定文化財等データベース` を対象として収集します。

```bash
heritage-collect national online \
  --pref-code 13 \
  --output ./13Tokyo/raw/national
```

処理:

```text
検索結果ページ
    ↓
detail URL一覧
    ↓
各文化財detail page
    ↓
records.jsonl
```

主な出力:

```text
raw/national/
├── records.jsonl
├── collection_manifest.json
├── search_pages/*.html.gz
└── detail_pages/*.html.gz
```

HTMLはprovenance確認用rawとしてgzip保存します。
取得済みdetail URLは再実行時にスキップするため、通常はresume動作です。

小規模テスト:

```bash
heritage-collect national online \
  --pref-code 13 \
  --output ./13Tokyo/raw/national_test \
  --max-pages 1 \
  --max-details 10
```

文化遺産オンラインのHTML構造に依存するため、サイト変更時はparser修正が必要です。

---

## 8. 国指定文化財等DBのCSV exportをraw取り込み

ブラウザから公式CSVを出力した場合:

```bash
heritage-collect national ingest \
  --input ./downloads/buildings.csv ./downloads/sites.csv \
  --output ./13Tokyo/raw/national
```

元CSVは内容を変更せず、例えば次のように保存します。

```text
official_export_001.csv
official_export_002.csv
```

文化遺産オンライン収集と公式CSV ingestは同じ `raw/national/` に共存できます。
正規化時に重複IDを抑制します。

---

## 9. 国データの正規化

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

`national.csv` は5桁自治体コードまで解決できた、Extractorへルーティング可能なレコードです。
所在地が非公開・不明等で自治体コードを確定できないものは `national_needs_review.csv` に残します。

---

# Part C — Extractor v0.5.x との互換性

## 10. Canonical schema

`national.csv` と `municipal.csv` は共通列構造です。
主要列:

```text
source_level
source_authority
source_dataset
source_record_id
source_url
source_file
name
name_kana
place_name
address_detail
owner
address
municipality
municipality_code
category
type
designation
designation_date
latitude
longitude
entity_class
geometry_role
designation_level
raw_category
raw_type
raw_designation
```

### `address_detail`

`方書`、`住所詳細`、`所在地詳細`、`所在詳細` 等を `address_detail` に正規化します。

例:

```text
住所           = 東京都台東区浅草2-3-1
address_detail = 浅草寺境内
```

`address_detail` は原データの場所情報を失わないための列です。
このツール自身は、それを使って範囲やBuildingを推定しません。

---

## 11. `type` / `entity_class` の整合

Extractor v0.5.x に合わせて、代表的には次のように正規化します。

| normalized `type` | `entity_class` | `geometry_role` |
|---|---|---|
| 建造物 | `building_direct` | `building_candidate_point` |
| 美術工芸品 | `movable` | `representative_point` |
| 考古資料 | `movable` | `representative_point` |
| 古文書 | `movable` | `representative_point` |
| 典籍 | `movable` | `representative_point` |
| 美術工芸品・考古資料 | `movable` | `representative_point` |
| 歴史資料 | `point` | `representative_point` |
| 史跡 / 旧跡 / 名勝 / 天然記念物 等 | `point` | `representative_point` |

### movable の意味

v0.2.xでは `movable` は**意味分類のみ**です。

```text
× 同じ住所のmovableを1件へまとめる
× nameを所在施設名へ置換する
× movable専用Pointを生成する
```

という処理は行いません。

各文化財レコードの `name` は常に当該文化財自身の名称です。
Extractor v0.5.xでもmovableは他の非建造物レコードと同じ空間処理経路を通ります。

---

## 12. Data Toolsで生成しないもの

次は **Extractorが文化財レコード全体とPLATEAUを見て導出する情報**なので、Data Toolsでは生成しません。

```text
complex_id
complex_name
complex_grouping_method
complex_record_count
source_location_role
spatial_match_status
matched_building_ids
match_methods
```

特に `source_location_role=shared_complex_coordinate` は、同一Complex内で複数レコードが完全同一座標を共有するかを確認して初めて判定できます。
前処理段階で個別レコードの座標を「正確な対象物位置」と決めつけません。

---

## 13. Extractor入力ディレクトリ

東京都指定文化財CSVは変更せず、正規化済み国・自治体CSVと並べます。

```bash
mkdir -p ./13Tokyo/gml_input

cp ./13Tokyo/prefectural/130001_cultural_property.csv \
   ./13Tokyo/gml_input/

cp ./13Tokyo/tidy/national.csv \
   ./13Tokyo/gml_input/

cp ./13Tokyo/tidy/municipal.csv \
   ./13Tokyo/gml_input/
```

最終形:

```text
13Tokyo/gml_input/
├── 130001_cultural_property.csv
├── national.csv
└── municipal.csv
```

Extractor:

```bash
heritage-gml \
  --area-code 13 \
  --data-dir ./13Tokyo/gml_input
```

国・都・区市町村で同一文化財対象が重複しても、Data Toolsでは自動的に1レコードへ統合しません。
指定制度と出典の違いを保持するためです。

---

## 14. 設計原則

- rawは変更せず保存する。
- 指定主体を推測で確定しない。
- `方書`等の原情報を捨てない。
- movableを空間処理上の特別ルートにしない。
- 前処理でbuffer、nearest、推定Polygonを作らない。
- Building / Complex / shared-coordinate判定はExtractorへ委ねる。
- `entity_class` は正規化結果として保持するが、最終的なPLATEAU処理の責任はExtractorに置く。

---

## 15. 東京都Open Data API実装メモ

v0.2.2のAPIリクエスト形式は、東京都オープンデータAPIカタログサイトの「APIの使い方」に示されるJSON POST形式に合わせています。

```text
POST <json_endpoint>?limit=<N>&offset=<N>
Accept: application/json
Content-Type: application/json
{}
```

公式のレスポンス例で用いられる `total` / `subtotal` / `limit` / `offset` / `hits` を基準に全件取得を確認します。

manifestのURLは外部サイト更新により将来変更される可能性があります。直接CSVが失効してもAPI endpointが有効ならfallbackしますが、CSV・API双方が失効した場合は `FAILED` として `source.json` に記録し、他自治体の処理は継続します。
