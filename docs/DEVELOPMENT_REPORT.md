# PLATEAU Heritage Data Pipeline 開発レポート

## 東京都文化財オープンデータの収集・正規化・分類と PLATEAU Building 突合

**版:** Draft 0.3  
**基準日:** 2026-09-02  
**対象:** `kotdijian/PLATEAU_heritage` および 2026年8月から9月の東京都全域処理

![東京都文化財データ統合の全体像](images/tokyo_heritage_pipeline.png)

## エグゼクティブサマリー

本開発は、国・東京都・区市町村が別々の制度、語彙、書式、位置精度で公開する文化財情報を、出典を失わずに共通スキーマへ正規化し、Project PLATEAU の `bldg:Building` と保守的に突合するデータパイプラインを構築したものである。目的は、文化財の所在を推測的な面へ置き換えることではない。原レコード、代表点、直接一致した建築物、複数建築物から成る文化財Complexを区別し、確定できない関係を監査可能なまま残すことにある。

東京都62区市町村を探索し、23自治体で機械利用可能な自治体公開データを取得できた。取得率は37.1%である。処理対象は国755件、東京都245件、区市町村4,134件、合計5,134件であった。Nationalは、国指定文化財等データベースのCSV出力を保存する経路と、文化遺産オンライン上で同データベース由来資料を東京都に限定して取得する経路を統合する。区市町村データは分類工程により、区市町村レベルとして自動採用3,341件、国・都レベルとの重複候補185件、要確認608件に分離した。さらに東京都リストの更新時点差による例外1件を手動除外し、今回の直接入力は国755件、東京都244件、区市町村3,341件、計4,340件とした。したがって「公開データを集めた件数」と「そのまま統合できる件数」は同一ではない。

最も大きな開発課題は、CSVとJSONの違いよりも、同じ意味を持つ列名の揺れ、同じ列名に異なる意味を入れる運用、自治体独自制度名、住所と方書の混在、代表座標と個体座標の区別欠如であった。実装では15論理項目に85個の列名エイリアスを定義し、26列のcanonical schemaへ写像した。さらに283件の分類規則と、荒川区261件のレコード単位overrideを用いて8列の分類属性を付与した。曖昧な値は推測せず `unknown` または `needs_review` に送る。

PLATEAUとの結合は、原則として文化財点がBuilding footprintに完全包含または境界接触する場合のみ採用する。建造物レコードに限り、正規化名称または住所の完全一致を補助証拠とする。buffer、最近傍、曖昧一致、convex hull、建物間空地の補完は行わない。同一Complex内で複数レコードが完全同一座標を共有する場合、その点を個別建築物位置として流用しない。この抑制的設計により、誤った「精密化」を避けながら、GML、GeoPackage、JSON/XML、監査CSVに根拠と未解決状態を保存できる。

> **数値の検証区分**  
> `repository verified` はGitHub `main` のCSV、manifest、設定またはコードから再計算した値、`run-log verified` は今回の全域処理ログ・集計出力で確認した値を示す。GitHub `main` には全域の `raw/tidy/gml_input` が収録されていないため、国755件、区市町村4,134件、分類分岐3,341/185/608件はリポジトリ単独では再計算できない。

### 主要指標

| 指標 | 値 | 検証区分 |
|---|---:|---|
| 東京都の区市町村総数 | 62 | repository verified |
| 利用可能な自治体データ | 23自治体 | repository verified |
| 自治体データ取得率 | 37.1% | repository verified |
| 国データ | 755件 | run-log verified |
| 東京都データ | 245件 | repository verified |
| 区市町村データ | 4,134件 | run-log verified |
| 3系統の処理対象 | 5,134件 | run-log verified |
| 手動除外 | 1件（哲学堂公園） | source-date verified |
| Extractor直接入力 | 4,340件 | derived |
| 区市町村として自動採用 | 3,341件 | run-log verified |
| 国・都レベル重複候補 | 185件 | run-log verified |
| 要確認 | 608件 | run-log verified |
| canonical schema | 26列 | repository verified |
| 追加分類属性 | 8列 | repository verified |
| 分類グロッサリー | 283規則 | repository verified |
| 荒川区record override | 261件 | repository verified |

---

# 1. 調査目的とデータ取得範囲

## 1.1 目的

文化財オープンデータをPLATEAU建築物モデルへ接続するには、単に緯度・経度を重ねるだけでは足りない。文化財レコードは、建造物、可動資料、史跡、名勝、天然記念物、無形文化財など性質の異なる対象を同一表に収める。また所在地は、個体位置、所蔵施設、寺社境内、行政上の代表点、非公開化された概略位置のいずれでもあり得る。

本開発では次の三つを分離した。

1. **データ取得:** 公開主体ごとの原データを改変せず保存し、取得日時、URL、hash、fallback履歴を記録する。
2. **意味の正規化:** 列名、文字コード、制度語彙、自治体コード、文化財類型を共通表現へ写像する。
3. **空間的突合:** 代表点からPLATEAU Buildingを直接特定できる場合だけ関係を生成し、不確実なものを未解決として残す。

この分離により、分類規則を変更してもPLATEAU CityGMLを再取得する必要がなく、逆にPLATEAU年度を更新しても原文化財データの意味正規化をやり直す必要がない。

## 1.2 データソースの3系統

| 系統 | 公開主体・取得元 | 役割 | 今回の件数 |
|---|---|---|---:|
| national | 文化庁「文化遺産オンライン／国指定文化財等データベース」等 | 国指定・登録・選定等 | 755 |
| prefectural_tokyo | 東京都教育庁 `130001_cultural_property.csv` | 東京都指定文化財 | 245 |
| municipal_source | 区市町村公式CSV、東京都Open Data API | 自治体指定・登録・独自制度。ただし国・都データを含む場合がある | 4,134 |

三系統を別々に扱う理由は、データセットの公開主体が指定主体と一致しないためである。区市町村が公開する「文化財一覧」には、同じ区域内の国指定・都指定文化財が含まれることがある。したがって、`source_level=municipal_source` は「自治体が公開した」というprovenanceであり、`designation_level=municipal` を意味しない。

### 1.2.1 Nationalの二つの取得経路

Nationalは、国指定文化財等データベースと文化遺産オンラインを同一サイトとして扱うのではなく、役割の異なる二つの取得経路として扱う。

| 取得経路 | 公式サービスの性格 | パイプラインでの取得方法 | 主なraw成果物 |
|---|---|---|---|
| 国指定文化財等データベース | 文化財保護法に基づき国が指定・登録・選定した文化財等を公開 | 画面から分割出力したCSVを手動ingestし、内容を変えず保存 | `official_export_*.csv`, `official_csv_ingest_manifest.json` |
| 文化遺産オンライン | 文化庁が運営する文化遺産ポータル。博物館・美術館資料に加え、国指定文化財等データベース等との連携情報を掲載 | 検索を東京都かつ「国指定文化財等データベース」に限定し、検索ページと詳細ページを取得 | `records.jsonl`, `search_pages/*.html.gz`, `detail_pages/*.html.gz`, `collection_manifest.json` |

国指定文化財等データベースは、国宝・重要文化財、登録有形文化財、重要無形文化財、史跡名勝天然記念物、重要文化的景観など、国が指定・登録・選定した文化財等を検索できる公式データベースである。公式画面にはCSV出力機能があるが、2026-05-07の告知では全件出力の不具合により2,000件以下での出力が案内されている。そのため複数条件に分けて取得したCSVを `official_export_001.csv` などとしてraw保存し、後段で重複排除する。

文化遺産オンラインは、国指定文化財だけに限定されたデータベースではなく、全国の博物館・美術館等が提供する作品を含む横断ポータルである。本処理ではポータル全体をNationalとして取得せず、検索条件 `museum=国指定文化財等データベース` と `prefecture_cd=13` を併用して、国指定文化財等データベース由来の東京都分に限定する。したがって、二つの経路は別の指定主体を表すのではなく、同じNational情報へ異なる公開面から到達する補完的経路である。

文化遺産オンライン経由では、検索結果件数からページ数を算出し、全検索ページから詳細URLを収集する。詳細ページから名称、ふりがな、文化財種類、種別、所在地、所有者、保管施設、指定・登録・選定年月日、時代、ページ内座標等を抽出する。取得済みURLは `records.jsonl` から復元して再実行時にskipし、HTMLもgzip保存するため、parser変更時に原ページへ戻って再抽出できる。

文化遺産オンラインの公式URLは2026-03-23に `https://bunka.nii.ac.jp/` から `https://online.bunka.go.jp/` へ変更された。本処理時のraw manifestには取得時点のURLを保持し、レポートの参照先には現在の公式URLを示す。再取得時にはredirectだけに依存せず、検索endpointとHTML構造を確認する必要がある。

## 1.3 自治体coverage

![自治体データの取得coverage](images/municipal_coverage.png)

コード上の東京都自治体マスターは62団体である。2026-08-27版manifestには東京都教育庁1件と区市町村23件、計24ソースが登録されている。区市町村23件の取得候補は次の構成である。

| 取得経路 | 自治体数 | 備考 |
|---|---:|---|
| 直接CSVあり | 16 | 原表の保存を優先 |
| 東京都Open Data APIあり | 22 | JSON POST、ページング |
| CSVとAPIの両方あり | 15 | CSV失敗時にAPI fallback |
| CSVのみ | 1 | API endpoint未確認 |
| APIのみ | 7 | JSONレスポンスをraw保存 |

対象自治体は、中央、新宿、台東、墨田、江東、中野、杉並、豊島、荒川、板橋、練馬、葛飾、江戸川、調布、小金井、小平、東村山、国分寺、国立、狛江、東大和、東久留米、あきる野の23自治体である。

coverage 37.1%は東京都全域の文化財分布を表す無作為標本ではない。公開基盤、更新頻度、座標公開方針、データ作成能力の差を反映したcoverage biasを含む。件数の多寡を文化財密度の差として直接比較してはならない。

---

# 2. ソースデータの異質性

## 2.1 「CSV/JSONの違い」より深い不統一

表面的な形式はCSVまたはJSONであるが、実装上の問題は四層に分かれた。

| 層 | 観測された問題 | 処理上のリスク |
|---|---|---|
| ファイル | UTF-8/UTF-8 BOM/CP932/Shift_JIS、埋込み改行、空行、空列 | 行数誤認、decode失敗、列ずれ |
| 構造 | CSV、API JSON、`hits`配列、ネストした`data/results/records/items` | レコード配列の誤選択、ページ欠落 |
| スキーマ | `名称/name/title`、`所在地/address/所在`など | 必須列未認識、値の取り落とし |
| 意味 | `種類/種別/ジャンル`、`指定/登録/区民文化財`、公開主体と指定主体の不一致 | 誤分類、重複、制度情報の消失 |

東京都CSVは物理的には656行あるが、CP932でCSVとして読むと248レコードである。うち自治体コード `130001` を持つ有効文化財レコードが245件、自治体コード欠落行が3件である。埋込み改行を考慮せず `wc -l` だけで件数を数えると誤る典型例である。

## 2.2 列名の揺れ

正規化器は15論理項目に対して85個の列名エイリアスを持つ。主な対応は次の通りである。

| 論理項目 | 実際の列名例 |
|---|---|
| id | `NO`, `No`, `ID`, `文化財ID`, `管理番号`, `登録番号`, `指定番号` |
| name | `名称`, `文化財名称`, `文化財名`, `name`, `title` |
| place_name | `場所名称`, `施設名称`, `所在名称`, `保管施設`, `site_name` |
| address_detail | `方書`, `住所詳細`, `所在地詳細`, `所在詳細`, `address_note` |
| owner | `所有者等`, `所有者`, `管理者`, `管理団体` |
| address | `住所`, `所在地`, `所在`, `所在地住所` |
| category | `文化財分類`, `カテゴリ`, `指定区分`, `分類`, `文化財種類` |
| type | `種類`, `ジャンル`, `種別`, `文化財種類` |
| designation | `指定等`, `指定登録区分`, `指定・登録区分`, `指定種別` |
| latitude/longitude | `緯度/経度`, `lat/lon`, `latitude/longitude`, `lng` |

エイリアスは列名を共通化するが、意味を勝手に統合しない。例えば `文化財種類` はソースによりcategoryまたはtypeとして使われる可能性があるため、source profileや観測値と併せて判断する必要がある。

## 2.3 値の揺れと制度語彙

自治体データでは、法制度上の大分類と自治体独自の呼称が同じ列に現れる。観測例には `区指定文化財`、`区民史跡`、`区民有形文化財`、`地域文化財`、`市重宝`、`有形文化財（建造物）` などがある。`重要文化財` という文字列を含むだけで国指定と判定すると、自治体独自の説明文や種類欄から誤判定が生じる。

そのため designation level の推定では、原則としてcategoryまたはdesignation欄の明示語彙を使い、raw typeやデータセット名を指定主体の根拠にしない。自治体内で接頭辞のない `指定史跡` のような値は、自治体別規則で解釈する。

## 2.4 住所、方書、施設名

`住所` と `方書` は空間意味が異なる。`東京都台東区浅草2-3-1` は地番・住居表示であり、`浅草寺境内` は施設・区域内であることを示す。方書を住所末尾へ単純連結すると、住所完全一致を壊す一方、方書を捨てるとComplex判定に必要な情報を失う。

canonical schemaでは次を分離して保持する。

- `address`: 行政住所または所在地文字列
- `address_detail`: 方書、境内、構内、施設内等
- `place_name`: 施設・場所名称
- `owner`: 所有者・管理者

Complex表示名を作る際は、`境内`、`敷地内`など末尾の所在表現だけを除く。`小石川後楽園内`からは`小石川後楽園`を得るが、施設種別を構成する文字まで削除しない。

## 2.5 APIレスポンスの不統一

東京都Open Data APIはPOST、`Content-Type: application/json`、body `{}` を用い、`limit/offset`で`total`までページングする。複数ページは単一JSONの`hits`へ統合し、`pages`、`records_collected`、`requested_page_size`を付加する。旧形式との互換のため、`data/results/records/items/result`以下の配列を探索するfallbackも持つ。

直接CSVが404、接続失敗、旧URL等で取得できない場合だけAPIへfallbackする。成功したrawファイルにはSHA-256、取得日時、取得URL、HTTP method、fallback履歴を保存する。これにより「正規化できた値」だけでなく、「どの公開物をいつ取得したか」を追跡できる。

## 2.6 National固有の構造差

Nationalの二経路は、同じ国指定等の情報を扱っていても入力構造が異なる。国指定文化財等データベースのCSVは、検索条件ごとの表形式exportであり、列名・文字コード・同一レコードの複数exportへの重複を処理する必要がある。文化遺産オンライン経由は、検索HTMLと詳細HTMLから作った1行1JSONの `records.jsonl` であり、ページ構造変更、項目欠落、座標記述形式の差を処理する必要がある。

| 論点 | 国指定文化財等DB CSV | 文化遺産オンライン経由 |
|---|---|---|
| 取得単位 | 検索条件ごとのCSV export | 検索ページ + 個別詳細ページ |
| record ID | CSV内IDを優先 | 詳細URLの数値ID |
| raw保存 | CSVを無変更で保存 | HTML gzip + 抽出JSONL |
| 件数制約 | export件数を分割する場合がある | 検索結果を20件単位でpage scan |
| 再開 | 複数exportを追加ingest | 取得済み`source_url`をskip |
| 主な変化リスク | export列・出力上限 | URL・HTML構造・埋込み座標形式 |

文化遺産オンラインから得られる `所在地` は必ずしも5桁自治体コードを伴わず、座標も全件には存在しない。National正規化では、住所から自治体名・コードを解決できないレコードを無理に東京都内の自治体へ割り当てず、reviewへ残す。

---

# 3. Canonical schemaへの正規化過程

## 3.1 処理段階

```text
source manifest
    -> raw/data.csv または raw/data.json
    -> encoding・records配列の解決
    -> 列名エイリアス解決
    -> canonical 26列への写像
    -> municipality code・type・entity classの正規化
    -> classified 8列の付加
    -> municipal / cross-level / needs-reviewへ分岐
```

rawを直接上書きしないことが基本原則である。正規化後の値が誤っていた場合でも、原データと変換規則を用いて再生成できる。

## 3.2 26列のcanonical schema

| グループ | 列 | 役割 |
|---|---|---|
| provenance | `source_level`, `source_authority`, `source_dataset`, `source_record_id`, `source_url`, `source_file` | 出典と追跡 |
| identification | `name`, `name_kana`, `place_name`, `owner` | 名称・施設・管理主体 |
| location | `address`, `address_detail`, `municipality`, `municipality_code`, `latitude`, `longitude` | 住所と座標を分離保持 |
| source semantics | `category`, `type`, `designation`, `designation_date` | 正規化利用値 |
| geometry semantics | `entity_class`, `geometry_role` | 空間処理の意味 |
| raw vocabulary | `designation_level`, `raw_category`, `raw_type`, `raw_designation` | 原語彙と指定レベル |

正規化は「列数を減らす」処理ではない。原語彙を `raw_*` とprovenance列に残しつつ、処理に必要なcanonical値を追加する非破壊変換である。

## 3.3 文字列・コードの正規化

文字列はNFKC正規化し、前後空白を除去する。比較用キーでは半角・全角空白を除去し、表記揺れの一部を統一する。ただし表示名自体は保持する。自治体コードは非数字を除去して先頭5桁を採用し、欠落時は自治体名、次に住所から東京都62自治体の辞書と照合する。

座標は数値化できない値を `None` とし、無理に0へ置換しない。0は有効な数値であり、欠損と同一視すると後段の空間処理を誤らせるためである。

## 3.4 type、entity_class、geometry_role

正規化typeは、建築物、考古資料、古文書、典籍、美術工芸品、歴史資料、史跡、名勝、天然記念物等へ寄せる。複合語 `美術工芸品・考古資料` は情報を潰さず保持する。

| entity_class | 意味 | geometry_role |
|---|---|---|
| `building_direct` | 建造物としてBuilding候補になり得る | `building_candidate_point` |
| `movable` | 美術工芸品、考古資料、古文書等の意味分類 | `representative_point` |
| `point` | 史跡、名勝、天然記念物、歴史資料等 | `representative_point` |

`movable` は空間処理を変える命令ではない。同一住所でまとめて建物へ付着させる処理は行わず、他のレコードと同じ個別pathで扱う。

## 3.5 入力例と正規化例

| 段階 | 項目 | 例 |
|---|---|---|
| raw | `名称` | 浅草寺六角堂 |
| raw | `文化財分類` | 都指定文化財 |
| raw | `種類` | 建造物 |
| raw | `住所` | 東京都台東区浅草2-3-1 |
| normalized | `name` | 浅草寺六角堂 |
| normalized | `designation_level` | prefectural |
| normalized | `type` | 建造物 |
| normalized | `entity_class` | building_direct |
| normalized | `geometry_role` | building_candidate_point |
| classified | `designation_level_code` | prefectural |
| classified | `designation_status_code` | designated |
| classified | `heritage_type_major_code` | tangible |

ここで、`name`を`浅草寺`へ置換しない点が重要である。文化財個体名とComplex/施設名は別entityとして保持する。

## 3.6 Nationalの統合・重複排除・ready判定

National正規化器は、`records.jsonl` と `official_export_*.csv` の双方を読み込み、同じ26列のcanonical schemaへ写像する。文化遺産オンライン経由では `detail_id` を `source_record_id` とし、公式CSVではID列を優先する。国指定等データであることは `source_level=national`、`source_authority=文化庁`、`designation_level=national` として保持する。

重複排除は、安定した `source_record_id` がある場合はIDを使い、IDがない場合は `name + address + raw_category + raw_type` の完全一致を使う。曖昧な名称類似や座標近接による統合は行わない。これは、同名別件や附指定、複数所在地を誤って一体化しないためである。

正規化後は次の三段階で出力する。

| 出力 | 内容 | 後段での役割 |
|---|---|---|
| `national_all_normalized.csv` | 二経路を統合・重複排除した全Nationalレコード | 監査・再処理 |
| `national.csv` | 5桁自治体コードまで解決できたレコード | 分類・Extractor入力 |
| `national_needs_review.csv` | 自治体コードを確定できないレコード | 住所・所在地の人手確認 |

今回の755件は、東京都処理でNational入力として扱った統合・正規化後のレコード数である。GitHubには全域raw/tidyが収録されていないため、この件数はrun-log verifiedとして扱う。

Nationalは直接のExtractor入力であるだけでなく、Municipalデータの指定主体判定にも使う。このcross-source参照は、東京都参照を含む三レベルの照合工程として次節にまとめる。

## 3.7 正規化後の三レベル照合と重複整理

### 3.7.1 二種類の重複を分ける

正規化後の重複整理では、「同じ指定レベル内で同じレコードを複数回取得した重複」と、「自治体公開一覧へ国指定・都指定文化財が再掲されたcross-level重複」を分けて扱う。前者は取得経路の重複であり、後者は公開主体と指定主体の不一致である。両者を同じdeduplication処理へ入れると、同名別件、附指定、複数棟、指定主体の違いを失うため、処理段階と出力を分離する。

| 段階 | 入力 | 照合・整理 | 出力上の扱い |
|---|---|---|---|
| 1. source内整理 | NationalのCSV・オンライン取得 | source ID、なければ内容完全一致 | 同じNationalレコードだけを統合 |
| 2. 個別正規化 | National、東京都、Municipal | 26列canonical schemaへ写像 | source provenanceを維持 |
| 3. 個別分類 | 三レベルの各リスト | glossary、source profile、override | level/status/typeを付与 |
| 4. 国・都リスト確認 | Nationalと東京都 | 原則排他的な公式リストとして確認 | 自動統合せず、更新時点差は例外監査 |
| 5. Municipal照合 | levelがunknownのMunicipal | 正規化名称＋5桁自治体コードの完全一致 | national/prefectural候補を分離 |
| 6. Extractor入力 | 各分類済みCSV | municipal、national、prefecturalを別入力 | cross-level候補とreviewを除外 |

### 3.7.2 Nationalと東京都は原則排他的に扱う

Nationalリストは国指定・登録・選定等、東京都リストは都指定文化財を対象としており、制度上の対象範囲は原則として排他的である。このため、通常処理ではNationalと東京都のレコード同士を名称・座標等で自動統合しない。各公式リストをそれぞれの指定レベルの正本として扱い、両者の完全一致や移行関係はsource snapshotの取得日・更新日を含む事前監査で確認する。

今回の入力では、東京都245件とNational 755件の間に、`正規化名称 + 5桁自治体コード` が完全一致するキーはなかった。ただし名称だけでは `哲学堂公園` と `武家屋敷門` の2件が一致した。`武家屋敷門` は所在地・所有者が異なる同名別文化財である。一方、`哲学堂公園` は2009年に東京都指定名勝、2020年に国指定名勝となった同一対象であり、東京都CSVの最終確認日が2019年であるため、公開リストの更新時点不一致によって旧指定情報が残った事例と判断した。

この事例は一般的な名称照合ロジックへ拡張せず、今回の東京都Extractor入力から `哲学堂公園` 1件を手動除外する。収集・正規化実績は東京都245件として保持し、除外後の東京都入力は244件とする。手動除外の対象、根拠、参照日を監査記録へ残し、raw snapshotの存在自体は消さない。

### 3.7.3 Municipalを国・都参照へ照合する

Municipal sourceの一覧には、自治体指定文化財だけでなく、その区域内の国指定・都指定文化財が再掲される場合がある。そこで、まずcategory、designation、source profile、record overrideにより指定主体が明示できるレコードを分類する。その後も `designation_level_code=unknown` のMunicipalレコードだけを、National参照と東京都参照へ照合する。

照合キーは `空白を除去した正規化名称 + 5桁自治体コード` の完全一致である。参照側でキーが一意である場合に限り、Nationalだけに一致すれば `national`、東京都だけに一致すれば `prefectural` とする。fuzzy name、座標距離、buffer、住所類似による指定主体判定は行わない。原則排他的な国・都参照に同じキーが現れた場合は、データ更新またはキー衝突の例外として自動解決せず監査対象に残す。

### 3.7.4 重複整理後の出力

cross-source照合はレコードを破壊的にmergeする処理ではない。Municipal sourceとして取得したprovenanceを保持したまま、Extractorへ渡すレコードと、上位指定との重複候補、未解決レコードを別ファイルへ分岐する。

| 出力 | 内容 | Extractorでの取扱い |
|---|---|---|
| `national_classified.csv` | National 755件 | 国レベル入力 |
| `130001_cultural_property_classified.csv` | 都取得245件から例外1件を除外 | 都レベル入力244件 |
| `municipal_classified.csv` | municipalと確定した3,341件 | 区市町村レベル入力 |
| `municipal_classified_cross_level.csv` | national/prefecturalと判定した185件 | 二重投入を避けるため除外、監査に使用 |
| `municipal_classified_needs_review.csv` | levelを確定できない608件 | 原則除外、人手確認 |
| `municipal_all_classified.csv` | Municipal source全4,134件 | provenance保持・再分類 |

この結果、収集・正規化した三系統5,134件を保持しつつ、今回のExtractorへの直接入力はNational 755件、東京都244件、Municipal 3,341件の計4,340件となる。これは文化財実体の完全な一意件数ではなく、明示的な重複・例外・要確認を除外した処理入力件数である。

---

# 4. 文化財類型用語のグロッサリー

## 4.1 二つの分類軸

文化財分類では、少なくとも「指定主体・制度状態」と「文化財の内容類型」を分ける必要がある。

| 軸 | canonical値 | 日本語表示 |
|---|---|---|
| designation level | `national`, `prefectural`, `municipal`, `unknown` | 国、都、区市町村、不明 |
| designation status | `designated`, `registered`, `selected`, `record_selected`, `local_other`, `unknown` | 指定、登録、選定、記録選択、独自制度、不明 |
| heritage major type | `tangible`, `intangible`, `folk`, `monument`, `cultural_landscape`, `preservation_technique`, `other`, `unknown` | 有形、無形、民俗、記念物、文化的景観、保存技術、その他、未判定 |

指定・登録・選定は価値の上下関係ではなく、制度上の取扱いを表す。文化財大分類は文化財保護法上の体系を参照しつつ、自治体独自制度を `local_other` として保持できるようにした。文化庁の公開体系では、建造物、美術工芸品、無形文化財、民俗文化財、史跡・名勝・天然記念物等が別系統で示される。

## 4.2 グロッサリー規則の構造

グロッサリーは283規則、18列からなる。内訳は自治体固有200規則、全ソース共通79規則、国3規則、東京都1規則である。match fieldはcategory 204規則、type 79規則、match typeはexact 280、regex 3である。

| 主な列 | 内容 |
|---|---|
| `rule_id`, `priority` | 規則IDと適用順 |
| `source_scope`, `municipality_code` | 適用範囲 |
| `match_field`, `match_type`, `match_value` | 照合対象と値 |
| designation 4列 | 指定主体・状態のcode/日本語 |
| heritage type 3列 | 大分類code/日本語、詳細類型 |
| `confidence`, `basis`, `note` | 確信度、根拠、注記 |

## 4.3 縮約グロッサリー

| 原語例 | level | status | major/detail | confidence | 解釈上の注意 |
|---|---|---|---|---|---|
| 都指定文化財 | prefectural | designated | raw typeで細分 | high | 東京都dataset固有 |
| 区指定文化財 | municipal | designated | raw typeで細分 | high | 自治体コードと組合せる |
| 区民史跡 | municipal | local_other | monument/史跡 | medium | 自治体独自制度 |
| 区民有形文化財 | municipal | local_other | tangible/未細分 | medium | 国の「重要文化財」と区別 |
| 指定史跡 | municipal | designated | monument/史跡 | high | 接頭辞なしはsource profile必須 |
| 地域文化財 | municipal | local_other | unknown | medium | 内容類型を語だけから断定しない |
| 有形文化財（建造物） | source rule | source rule | tangible/建造物 | high | 指定/登録は別列・overrideで解決 |
| 建造物 | - | - | tangible/建造物 | high | type規則、level根拠にはしない |
| 考古資料 | - | - | tangible/考古資料 | high | entity_classはmovable |
| 史跡 | - | - | monument/史跡 | high | 点は区域境界とは限らない |
| 天然記念物 | - | - | monument/天然記念物 | high | Building対象とは限らない |

## 4.4 record override

荒川区データでは、同じcategory `有形文化財（建造物）` 内に指定と登録が混在し、categoryだけでは制度状態を確定できない。そこで `municipality_code + name + owner + category` の完全一致で261件のrecord overrideを適用する。overrideは一般規則を雑に複雑化する代わりに、特定データセットで確認された事実を明示的に管理する方法である。

---

# 5. 分類判定の優先順位

## 5.1 判定フロー

```text
source scopeの確定
    -> source default
    -> 既存designation level
    -> category exact/regex rule
    -> record override
    -> type exact rule（内容類型のみ）
    -> cross-source exact reference
    -> unknown / needs_review
```

実装上はsource scopeごとのdefaultを置き、具体的規則が非空値だけを上書きする。confidenceは複数根拠のうち最も低い値を採用し、未判定typeまたはstatusがあればlowへ下げる。

## 5.2 指定主体を推測しない

municipal sourceのdefault levelは `unknown` である。自治体データに入っているからmunicipalとみなす処理は行わない。category規則、既存designation level、自治体別source ruleのいずれかで明示できる場合だけmunicipal/national/prefecturalを採用する。

cross-source解決は、正規化文化財名と5桁自治体コードの完全一致だけを使う。Nationalと東京都は原則排他的な参照リストとして扱い、Municipalレコードがどちらか一方の一意キーに一致した場合だけ指定レベルを解決する。両参照に同じキーが現れた場合は、更新時点不一致またはキー衝突の例外として曖昧なまま残す。fuzzy name、座標距離、bufferは指定主体判定にも使用しない。

## 5.3 出力分岐

| 出力 | 条件 | Extractor入力 |
|---|---|---|
| `municipal_classified.csv` | level=municipal | 使用 |
| `municipal_classified_cross_level.csv` | level=national/prefectural | 除外し重複監査 |
| `municipal_classified_needs_review.csv` | level=unknown | 原則除外・人手確認 |
| `municipal_all_classified.csv` | 全municipal-source | 監査・再分類用 |

分類属性はBuilding matchingやgeometry生成の証拠には使わない。分類だけを更新する場合は、既存GPKG/GMLへ属性patchを適用し、PLATEAU取得・照合・Complex生成を再実行しない設計である。

---

# 6. 集計結果

## 6.1 3系統のレコード

![3系統の処理レコード数](images/source_record_counts.png)

| 系統 | 件数 | 構成比 |
|---|---:|---:|
| 国 | 755 | 14.7% |
| 東京都 | 245 | 4.8% |
| 区市町村source | 4,134 | 80.5% |
| 合計 | 5,134 | 100.0% |

自治体sourceが80.5%を占めるが、その全てが自治体指定ではない。次の分類分岐を経て初めてExtractor入力の範囲が決まる。

National 755件は文化遺産オンラインの全公開作品数ではなく、国指定文化財等データベース由来に限定した東京都分を、公式CSV経路とオンライン経路から統合・正規化した処理レコード数である。二経路間の重複はsource IDまたは完全一致キーで除くが、国・都・Municipalという指定制度・出典の異なるレコードはこの段階で一つの実体へ自動統合しない。

東京都245件は収集・正規化したsource record数である。更新時点不一致が確認された `哲学堂公園` 1件を手動除外するため、今回の都レベルExtractor入力は244件となる。したがって、5,134件は収集・正規化段階の三系統合計であり、直接入力4,340件とは集計段階が異なる。

<!-- pagebreak -->

## 6.2 区市町村sourceの分類分岐

![区市町村sourceの分類分岐](images/municipal_classification_split.png)

| 判定 | 件数 | 構成比 | 取扱い |
|---|---:|---:|---|
| municipal自動採用 | 3,341 | 80.8% | municipal入力 |
| 国・都レベル重複候補 | 185 | 4.5% | municipal入力から除外 |
| 要確認 | 608 | 14.7% | reviewへ隔離 |
| 合計 | 4,134 | 100.0% | - |

608件を無理に自動分類しないことは欠陥ではなく、誤った指定主体を確定データとして流通させないための品質管理である。今後はsource profileの追加、原公開ページの確認、自治体照会によりunknownを減らす。

## 6.3 観測語彙

`observed_values.csv` は365行の集約語彙を持ち、municipal 349、東京都16である。municipal category系の観測件数は `文化財分類` 3,592、`カテゴリ` 232、`ジャンル` 175、合計3,999件である。municipal総数4,134件との差135件はcategory系値が空または観測表に現れないレコードであり、欠損自体を品質指標として扱う必要がある。

## 6.4 集計の解釈上の注意

- 自治体別件数は公開範囲と公開形式の影響を強く受ける。
- 同一文化財が国・都・自治体sourceに重複掲載され得る。
- 5,134件は三系統の処理レコード数であり、重複除去後の実体数ではない。
- 同一名称でも附指定、複数棟、所在違いにより別レコードである場合がある。
- 公式リスト間でも更新時点が異なる場合があり、指定レベル移行後の旧レコードは例外監査と手動除外が必要になる。
- 座標欠損や非公開は文化財の不存在を意味しない。

---

# 7. 位置情報の不確実性モデル

## 7.1 座標が表し得るもの

| 空間表現 | 例 | Building照合への意味 |
|---|---|---|
| individual point | 建造物個体の中心付近 | footprint完全包含なら有力 |
| representative point | 史跡、資料、無形文化財の代表位置 | 個体Buildingとは限らない |
| facility point | 博物館、寺社、所有者所在地 | 収蔵物と建物を同一視できない |
| shared complex coordinate | 同一境内の複数文化財に同一座標 | 個別Building位置に流用しない |
| address only | 座標なしの所在地 | 完全住所一致の補助証拠のみ |
| unlocated | 非公開・欠損 | 未解決として保持 |

`latitude/longitude`が存在することと、文化財個体の正確な位置が分かることは同義ではない。特に寺社境内、庭園、博物館内資料では、一つの代表点を複数レコードが共有する。

## 7.2 Complex

Complexは文化財レコードの意味上のまとまりであり、推定敷地polygonではない。`address_detail`、`place_name`、共有住所等からComplexを形成し、次の関係を明示する。

- Complex - cultural record
- Complex - directly matched Building
- cultural record - matched Building

同一Complexの複数レコードが完全同一座標を共有する場合、`shared_complex_coordinate`として識別する。その座標を各レコードの個別Building検索へ使うと、全レコードが同じ建物へ誤接続されるため、安全策として抑制する。

## 7.3 不確実性を消さない出力

Buildingが確定しないComplexは `complex_only` として残し、推定polygonを作らない。座標がある未一致レコードはpoint featureとして出力し、座標もない場合はunresolved tableへ残す。空間成果物に現れないレコードも監査表から消さない。

---

# 8. PLATEAUとの突合設計と出力モデル

## 8.1 PLATEAU Building

PLATEAUの3D都市モデルはCityGMLを基盤とし、建築物は `bldg:Building`、個体識別は `gml:id` で表現される。`gml:id` はデータセット内の識別子であり、更新年度をまたぐ恒久IDではない。このため、出力にはPLATEAU年度、source file、city codeも保持する。

Extractorは対象自治体のBuildingを走査するが、GPKGへ出力するのは文化財レコードとの照合またはComplex membershipによって選択されたBuildingだけである。全PLATEAU BuildingをGPKGへ複製する処理ではない。元のLOD0/LOD1/LOD2を含むBuilding要素はsubset CityGMLへコピーし、GPKGにはQGIS等で扱うための2D footprintと分析用属性を格納する。

## 8.2 PLATEAUから取り込むBuilding基本属性

### 8.2.1 PLATEAU CityGMLとGPKGの対比

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

GPKGの `address` はPLATEAUの階層的住所構造を単一文字列へflattenした派生値であり、`geometry` は3D Building geometryそのものではない。元の構造・LOD・正式なURO属性はsubset CityGMLに保持される。`usage` と `detailed_usage` は自動的に意味推定せず、元のcode/textを保持する。

### 8.2.2 同じBuilding層へ追加する文化財側の列

`heritage_buildings_footprint` はPLATEAU属性だけでなく、文化財との関係を検索しやすくする集約列も持つ。これらはPLATEAU原属性ではなく、本パイプラインが生成した値である。

| GPKG列名 | 内容 |
|---|---|
| `complex_ids`, `complex_names` | Buildingが属する文化財Complex |
| `record_ids`, `record_names`, `record_types` | Buildingへ直接またはComplex経由で関連付けた文化財レコード |
| `entity_classes` | `building_direct`、`point`、`movable`等の文化財処理区分 |
| `designation_levels`, `designation_statuses` | 関連文化財の指定主体・制度状態 |
| `heritage_type_majors`, `heritage_type_details` | 関連文化財の正規化類型 |
| `match_methods` | `point_in_building`、名称・住所完全一致等の照合根拠 |

### 8.2.3 PLATEAU由来値が伝播するGPKG列

Buildingの基本属性は、polygon layerだけでなくRecord―BuildingおよびComplex―Buildingの関係表にも必要最小限を複製する。次表のうち、ID・名称・住所・用途・元fileはPLATEAUから取得した値、複数Building IDの連結やmember数はその値を基にパイプラインが生成した関係情報である。

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

`heritage_points` と `heritage_unresolved_entities` は文化財側の未一致・standalone対象を保持するため、直接のPLATEAU属性を持たない。各関係表のPLATEAU由来列は検索の利便性のために複製されるが、Building属性の中心的な参照先は `heritage_buildings_footprint`、災害リスクの個別値の参照先は `plateau_disaster_risk` である。

## 8.3 PLATEAU災害リスク属性の取り込み

### 8.3.1 取得対象と設計原則

PLATEAU CityGMLの `bldg:Building` に付属する `uro:bldgDisasterRiskAttribute` をBuilding走査時に抽出する。災害リスクは文化財分類やBuilding照合の証拠には使わず、Buildingが文化財との関係によって選択された後に付加されるPLATEAU由来属性として扱う。したがって、災害リスクの有無によって文化財Buildingが選択・除外されることはない。

対応する6類型は次のとおりである。

| PLATEAU要素 | 正規化 `risk_type` | 日本語 |
|---|---|---|
| `uro:RiverFloodingRiskAttribute` | `river_flooding` | 洪水浸水想定区域 |
| `uro:TsunamiRiskAttribute` | `tsunami` | 津波浸水想定 |
| `uro:HighTideRiskAttribute` | `high_tide` | 高潮浸水想定 |
| `uro:InlandFloodingRiskAttribute` | `inland_flooding` | 内水浸水想定 |
| `uro:ReservoirFloodingRiskAttribute` | `reservoir_flooding` | ため池ハザードマップ |
| `uro:LandSlideRiskAttribute` | `landslide` | 土砂災害警戒区域 |

一つのBuildingに同種のリスク属性が複数存在し得るため、GPKGではBuilding polygon上の検索用集約列と、全レコードを保持する `plateau_disaster_risk` 1:N属性テーブルの両方へ出力する。分析上の正本は元CityGMLと1:Nテーブルであり、polygon上の最大値・連結文字列は検索・可視化用の派生値である。

### 8.3.2 災害リスク原属性と1:Nテーブルの対比

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

`codeSpace` がローカルPLATEAU package内のcodelistを参照し、そのfileを利用できる場合だけ人間可読labelを解決する。GML単体しかない場合や参照先がURLの場合、外部取得で補完せず、codeと`codeSpace`を保持してlabelを空欄にする。`depth_m` はm/cm/mmをmへ、`duration_h` はhour/minute/secondを時間へ換算する。未知単位は推測せず正規化値を空欄にし、元値と単位は残す。

### 8.3.3 `heritage_buildings_footprint` の災害リスク列

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

description等の一覧列は、codelist labelが得られればlabelを、得られなければraw codeを用い、重複を除いて`;`で連結する。`*_max_depth_m` と `river_flood_max_duration_h` は同種リスクが複数ある場合の最大値であり、個別値は必ず `plateau_disaster_risk` を参照する。

### 8.3.4 災害リスク属性の他成果物への保持

subset CityGMLは選択Building要素を丸ごとコピーするため、`uro:bldgDisasterRiskAttribute` を正式なURO属性のまま保持する。Generic Attributeへ重複コピーしない。companion JSONではBuilding entityの `disaster_risks` 配列へ全リスク属性を格納する。都道府県統合GPKGでは、自治体GPKGの `plateau_disaster_risk` を他の属性テーブルと同様に結合できる。

## 8.4 結合ロジック

| 対象 | 証拠 | 採用条件 |
|---|---|---|
| 全レコード | point-in-footprint | pointがfootprint内部または境界上 |
| building_direct | exact normalized name | 文化財名/場所名とBuilding名が完全一致 |
| building_direct | exact normalized address | 文化財住所とBuilding住所が完全一致 |
| shared complex coordinate | point | 個別照合には原則使用しない |
| non-building point/movable | exact name/address | 単独では使用しない |

複数証拠がある場合、match methodを列挙して保存する。buffer、指定半径、最近傍、文字列fuzzy、地番周辺検索は採用しない。これらはrecallを上げる一方、文化財では「もっとも近い建物」が対象建物である保証がない。

## 8.5 Building Complex geometry

Complex geometryは、直接一致したBuilding footprint群をMultiPolygonのpartとして保持する。dissolve、convex hull、建物間gap fillingを行わない。したがって、出力geometryは「文化財範囲」ではなく「当該Complexに直接関係付けられたPLATEAU建築物集合」である。

## 8.6 出力

| 形式 | 役割 |
|---|---|
| subset CityGML | 元のPLATEAU Buildingを保持した正式3D成果物。generic attributesを付加 |
| GeoPackage | QGIS等で利用する分析・監査マスター |
| companion JSON/XML | Record/Building/Complex/Point関係の機械可読表現 |
| audit CSV | 入力issue、リンク、Complex member、未解決、取得issue |

### 8.6.1 GPKG内の層・表とPLATEAU属性の所在

| GPKG層・表 | 種別 | PLATEAU由来情報 | 主な役割 |
|---|---|---|---|
| `heritage_records` | Point layer | `matched_building_ids`を介した参照 | 全文化財レコードとsource位置 |
| `heritage_buildings_footprint` | Polygon layer | Building基本属性、2D footprint、災害リスク集約 | QGIS表示・検索の中心 |
| `heritage_building_complexes` | MultiPolygon layer | member Buildingのfootprint・`building_gml_ids` | Complexに確定したBuilding集合 |
| `heritage_points` | Point layer | 直接のPLATEAU属性なし | standalone・未一致文化財point |
| `plateau_disaster_risk` | Attribute table | 災害リスク全属性 | Building : risk = 1:Nの正規化表 |
| `heritage_building_links` | Attribute table | Building ID、名称、住所、用途、元GML | Cultural Record : Building link |
| `heritage_complex_members` | Attribute table | Building ID、名称、住所、用途、元GML | Complex : Building link |
| `heritage_complex_records` | Attribute table | `matched_building_ids`を介した参照 | Complex : Cultural Record link |
| `heritage_complex_summary` | Attribute table | `building_gml_ids`とBuilding件数 | geometryの有無を含むComplex集計 |
| `heritage_unresolved_entities` | Attribute table | 直接のPLATEAU属性なし | 未解決対象と理由 |

分類8属性はRecordだけでなく、リンク、Complex、Building集約属性へ伝播するが、geometryやmatching判定は変えない。PLATEAU由来値とパイプライン派生値を同じBuilding layerへ格納する場合も、上記の列定義によって由来を区別する。

---

# 9. 開発中のデータ工学上の問題と改善

## 9.1 API取得

初期実装では東京都Open Data APIへbodyなしPOSTを送り、415 Unsupported Media Typeが発生した。JSON body `{}` と適切なheaderへ修正し、`total`を確認して全ページを収集する方式へ改めた。直接CSVのURL更新・404にも備え、CSV優先、API fallbackとした。

## 9.2 PLATEAUキャッシュ破損

大容量CityGMLの取得・再利用中に `TimeoutError`、`OSError`、XML読込失敗、macOSの `Errno 60` 等が発生した。個別ファイルだけを推測的に残すと、同一自治体のキャッシュ集合が異なる取得状態を混在させる。そこでAPIモードの読込失敗時には自治体単位でキャッシュを一括破棄し、全GMLを再取得して1回だけ再試行する方式へ変更した。local modeではユーザー所有ファイルを削除しない。

## 9.3 自治体単位の中断・再開と監査ログ

東京都全域処理は、自治体ごとにPLATEAU取得量、文化財件数、処理時間が異なる。全域を一つの不可分なjobとすると、1自治体の取得失敗が全体を巻き戻す。そこで自治体単位に出力directoryと `run_summary.json` を作成し、完了自治体をskipして失敗自治体だけ再試行できるようにした。

処理状態は、少なくとも次の区分で記録する。

- `completed`: 文化財読込、PLATEAU取得、照合、成果物生成が完了
- `no_local_cultural_records`: 対象自治体の入力レコードなし
- `plateau_query_failed`: PLATEAU対象fileの解決に失敗
- `plateau_download_failed`: CityGML取得が未完了
- `dry_run`: 文化財読込と分類のみ確認

再開時には既存summaryを読み、完了状態だけをskipする。取得失敗は文化財とBuildingの「不一致」と区別し、query issue、download issue、cache recovery eventを別々に保存する。これにより、文化財位置が原因で一致しなかったのか、外部データ取得が完了していないのかを後から判別できる。

---

# 10. 再現性・provenance・監査可能性

## 10.1 再現性の単位

再現性は「同じコマンドが動く」だけでは成立しない。少なくとも次を一組で保存する。

| 要素 | 保存内容 |
|---|---|
| source | URL、取得日時、HTTP method、raw file、SHA-256 |
| normalization | source profile、alias、canonical schema版、report |
| classification | glossary版、override版、summary、review rows |
| PLATEAU | city、年度、取得file、cache recovery event |
| matching | config、method、link table、unresolved reason |
| software | release artifact hash、Python環境、依存関係 |

## 10.2 監査可能な分岐

`自動確定`、`cross-level候補`、`needs_review`、`manual_exclusion`、`unresolved geometry`を別ファイルに分ける。自動処理から外れたレコードをraw snapshotから削除しない。人手修正はsource profile、glossary rule、record overrideまたは除外台帳として追加し、対象キー、根拠、参照元、判断日をnoteへ記録する。`哲学堂公園` は、東京都CSVの確認日と国指定日の前後関係に基づく `manual_exclusion` の事例である。

## 10.3 推奨リリース構成

```text
PLATEAU_heritage/
  heritage_gml/                 # Extractorの唯一の正本
  tokyo_heritage_data_tools/    # 前処理package
  tools/                        # runner/merge launcher
  tests/
  data/reference/13Tokyo/       # 再現用の公開可能referenceのみ
  docs/
    DEVELOPMENT_REPORT.md
    DEVELOPMENT_REPORT.pdf
    images/
  .gitignore
```

raw取得物、PLATEAU cache、自治体別outputは原則Git管理外とし、再生成手順、manifest、hash、集計summaryを管理する。公開再現datasetを置く場合はライセンスと取得日を明記する。

## 10.4 テスト

回帰テストは、area code、API POST/pagination、CSVからAPIへのfallback、encoding、canonical schema、classification passthrough、cross-source exact match、Complex、shared coordinate、matching、output empty、cache recovery、災害リスク属性等を対象とする。成果物のschemaと件数summaryもgolden test化し、版更新時の意図しない変化を検出することが望ましい。

---

# 11. 限界と今後の展開

## 11.1 現時点の限界

1. **coverage bias:** 自治体データ取得率は37.1%で、未公開39自治体を欠く。
2. **位置精度:** 座標の意味・作成方法・公開精度がsource metadataに明示されない場合が多い。
3. **重複実体:** 5,134レコードは処理レコード数であり、文化財実体の一意数ではない。
4. **PLATEAU coverage:** 全自治体・全Buildingに同程度のLOD・名称・住所属性があるわけではない。
5. **年度更新:** `gml:id` はPLATEAU年度をまたぐ恒久IDではない。
6. **分類unknown:** municipal sourceの14.7%が要確認である。
7. **source更新差:** 公式リストの確認日・更新日が揃わず、指定レベル移行後の旧情報が残る場合がある。

## 11.2 短期ロードマップ

| 優先度 | 作業 | 完了条件 |
|---:|---|---|
| 1 | 全域summary固定 | 5,134件と分類分岐を再計算できる集計JSON/CSVを保存 |
| 2 | review 608件の縮減 | rule追加根拠と変更前後件数を記録 |
| 3 | 39自治体の再探索 | 公開/非公開/閲覧のみ/API有無をmanifest化 |
| 4 | 位置品質属性 | source-reported/representative/shared/unknownを明示 |
| 5 | 年度間Building対応 | geometry・住所等による別工程として検討 |
| 6 | source snapshot整備 | 公開条件、取得日、hashを伴う再現用索引を保存 |

## 11.3 全国展開

全国47都道府県への一般化には、東京都用aliasを増やすだけでは不十分である。都道府県ごとに、公開カタログ、自治体コード体系、source profile、制度語彙、位置公開方針をmanifestへ分離する必要がある。一方、canonical 26列、分類8列、unknownを保つ方針、保守的Building突合、Record/Complex/Building分離は全国共通の基盤になり得る。

本開発の中心的成果は、文化財点をBuildingへ大量に結び付けたことではない。messyな行政公開データから、何が確定し、何が推定で、何が未解決かを失わずに、再処理可能なデータへ変換する設計と実装を得たことである。

---

# 付録A. データセットと成果物

| 段階 | 主なファイル | 内容 |
|---|---|---|
| source | `tokyo_municipal_sources_2026-08-27.yml` | 取得先とAPI情報 |
| raw municipal | `<code>/data.csv`, `<code>/data.json`, `source.json` | 自治体原データと取得provenance |
| raw national/online | `records.jsonl`, `search_pages/`, `detail_pages/`, `collection_manifest.json` | 文化遺産オンライン経由の抽出値と原HTML |
| raw national/CSV | `official_export_*.csv`, `official_csv_ingest_manifest.json` | 国指定文化財等DBの手動CSV出力 |
| normalize | `municipal_all_normalized.csv`, `national_all_normalized.csv` | canonical全件 |
| review | `*_needs_review.csv`, `municipal_classified_cross_level.csv` | 未解決・重複候補 |
| manual exclusion | 除外台帳、対象キー、根拠、判断日 | 更新時点差等の例外監査 |
| classify | `*_classified.csv`, classification summary | 8属性を付加 |
| extractor | `<code>_heritage_buildings.gml`, `<code>_heritage.gpkg` | 自治体成果物。GPKGにはBuilding基本属性、文化財関係、災害リスク属性を収録 |
| GPKG risk table | `plateau_disaster_risk` | PLATEAU災害リスクをBuildingとの1:N関係で保持する非空間属性表 |
| audit | links/complex/unresolved/input issues CSV | 判定根拠 |
| merge | `13_heritage.gpkg` | 東京都統合GPKG |

# 付録B. Evidence ledger

| 主張 | 根拠 |
|---|---|
| 62自治体 | `heritage_data_tools/tokyo_codes.py` の辞書件数 |
| 23自治体source | `manifests/tokyo_municipal_sources_2026-08-27.yml` |
| 東京都245件 | `130001_cultural_property.csv` の `130001` record数 |
| 哲学堂公園の更新時点差 | 東京都CSVの都指定日2009-03-16・最終確認日2019-03-29、国CSVの指定日2020-03-10 |
| Extractor直接入力4,340件 | 国755 + 都244 + municipal 3,341 |
| 26 canonical列・85 aliases | `normalizers/common.py` |
| 283規則・261 overrides | classification CSV |
| National二経路 | `collectors/national.py`, `normalizers/national.py`, 両公式サービス |
| 国755、municipal 4,134、3,341/185/608 | 2026年8-9月の全域処理ログ・集計結果 |
| matching抑制規則 | Extractor `matching.py`, changelog, tests |
| PLATEAU Building基本属性とGPKG列の対応 | Extractor `citygml.py`, `output.py`, `model.py` |
| 災害リスク6類型、単位換算、1:N表・集約列 | Extractor `citygml.py`, `output.py`, `model.py`, `tests/test_disaster_risk.py` |

# 参考資料

1. PLATEAU Heritage repository: https://github.com/kotdijian/PLATEAU_heritage
2. 国土交通省 PLATEAU「3D都市モデルデータの基本 - CityGMLのデータ構造」: https://www.mlit.go.jp/plateau/learning/tpc03-2/
3. 国土交通省 PLATEAU「3D都市モデルデータの基本 - LODレベルによる表現の違い」: https://www.mlit.go.jp/plateau/learning/tpc03-3/
4. 国土交通省 PLATEAU「3D都市モデルに別の地理空間情報を紐づけて利用する」: https://www.mlit.go.jp/plateau/learning/tpc27-1/
5. 文化庁「国指定文化財等データベース」: https://kunishitei.bunka.go.jp/
6. 文化遺産オンライン「文化財体系から見る」: https://online.bunka.go.jp/heritages/classification
7. 文化庁「文化財」: https://www.bunka.go.jp/seisaku/bunkazai/

---

**作成注記:** 本文は2026-09-02時点のGitHub `main`、同リポジトリ内のmanifest・schema・glossary・changelog・tests、および今回の東京都全域処理ログを照合して作成した。全域raw/tidy成果物をGitHubへ格納していないため、run-log verified値の完全な第三者再計算は、集計summaryの同梱後に可能となる。
