# build_museum_manifest.py

`build_museum_manifest.py` は、東京都内の博物館・資料館・美術館・動物園・水族館等について、複数の公開名簿から候補レコードを収集し、名称と自治体コードを正規化したうえで、重複照合用のManifestを生成するスクリプトです。

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
│   └── name_aliases.csv
├── scripts/
│   └── build_museum_manifest.py
├── tests/
│   └── test_build_museum_manifest.py
├── cache/                         # 取得HTML。Git管理対象外
└── data/
    ├── museum_sources_manifest.csv
    ├── museum_candidates.csv
    ├── museum_reconciliation.csv
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

文化遺産オンライン、日本博物館協会など、安定した一括取得方法をまだ確定していない情報源は `manifest_only` として登録しています。

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
