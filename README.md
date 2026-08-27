# Cultural PLATEAU Extractor

指定・登録文化財の位置情報を、Project PLATEAU の建築物 CityGML と突合し、
**「文化財を含む建造物 complex」に属する PLATEAU 建物だけを抽出した CityGML**
を生成する Python CLI です。

## 目的

個々の文化財アイテムと PLATEAU `bldg:Building` を厳密に1対1同定するのではなく、

- 寺院・神社
- 邸宅・屋敷
- 博物館・史跡施設
- その他、複数の建造物を含む文化財所在施設

を **文化財 complex** として扱い、その complex に対応する PLATEAU 建物群を抽出します。

## 入力

### 文化財データ

自治体標準オープンデータセット「文化財一覧」を基本形とします。

自動認識する主な列:

- `NO` / `ID`
- `名称`
- `場所名称`
- `住所`
- `緯度`
- `経度`
- `文化財分類`
- `種類`
- `所有者等`
- `文化財指定日`

CSV のほか、設定により HTTP GET/POST の CSV/JSON API も入力可能です。
列名が独自の場合は YAML の `cultural.columns` で対応づけます。

### PLATEAU

通常は PLATEAU 配信サービス API を利用し、

1. 文化財位置から必要な3次メッシュを算定
2. 自治体コードで建築物 CityGML のファイル一覧を取得
3. 必要メッシュの `bldg` GML のみダウンロード

します。

自治体全体の巨大な CityGML ZIP を取得する必要はありません。

## complex の作り方

標準では文化財レコードを次の優先順位でグループ化します。

1. `場所名称`
2. `所有者等 + 住所`
3. `住所`
4. 上記がない場合のみ位置クラスタ

このため、たとえば同じ浅草寺所在地にある建造物・美術工芸品・史跡等は
「浅草寺 complex」としてまとめて PLATEAU と照合できます。

## PLATEAU 建物との照合

complex 内の文化財点群の凸包に `complex_buffer_m` のバッファを付与し、
交差する PLATEAU 建物を候補とします。

寺社と推定できる complex では、`uro:detailedUsage` の

- `422701`: 神社
- `422702`: 寺院

を優先します。ただし属性欠落による取りこぼしを避けるため、
各文化財点の最近傍建物はアンカーとして保持します。

この処理は「自動確定」ではなく、**機械的な候補抽出 + QA** を意図しています。

## インストール

Python 3.10 以上。

```bash
cd PLATEAU_heritage #ディレクトリ名は一例です。リポジトリを展開したディレクトリを指定してください
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
```


## ダウンロード前の plan

文化財入力の読込・complex 化・必要メッシュ算出だけを先に確認できます。

```bash
cultural-plateau plan --config configs/taito_tokyo_designated.yml
```

`core_point_meshes.txt` は文化財座標そのものが属するメッシュ、
`required_meshes.txt` は complex バッファがメッシュ境界を跨ぐ場合の取りこぼしを防ぐため
安全マージンを加えた取得対象です。

## 東京都オープンデータAPIを直接使う例

台東区「文化財一覧」の東京都APIを直接入力する設定例を同梱しています。

```bash
cultural-plateau plan --config configs/taito_ward_tokyo_api.yml
cultural-plateau run  --config configs/taito_ward_tokyo_api.yml
```

API が JSON 配列または `data` / `results` / `records` 等の一般的なラッパーを返す場合は
自動認識します。独自のネストの場合は `cultural.json_path` を指定してください。

## 実行

```bash
cultural-plateau run --config config.example.yml
```

台東区・東京都指定文化財 CSV の例:

1. `130001_cultural_property.csv` をプロジェクト直下へ置く
2. 実行

```bash
cultural-plateau run --config configs/taito_tokyo_designated.yml
```

## 出力

`output.dir` 以下に生成します。

- `cultural_property_buildings.gml`
  - 選択された元 PLATEAU `cityObjectMember` を保持した subset CityGML
  - 任意で `gen:stringAttribute` に complex ID・名称を追記
- `cultural_complex_building_linkage.csv`
  - 文化財 complex ↔ PLATEAU `gml:id` の対応表
- `cultural_complex_summary.csv`
  - complex ごとの候補数・採択数・照合方法
- `cultural_records_normalized.csv`
  - 正規化した文化財入力
- `required_meshes.txt`
  - 必要な3次メッシュ
- `plateau_gml_manifest.csv`
  - 実際に使用した PLATEAU GML
- `cultural_points.geojson`
  - QA 用文化財位置
- `selected_building_footprints.geojson`
  - QA 用抽出建物平面形
- `run_summary.json`

## CityGMLについて

出力は、元の `bldg:Building` を新たに再構築するのではなく、
元 CityGML の `cityObjectMember` をコピーします。LOD1/2 等の幾何と PLATEAU 属性を
できる限りそのまま保持します。

テクスチャ用 `appearanceMember` は subset の軽量化と不要参照の回避のため出力しません。
したがって **幾何・意味属性を利用する分析用 GML** を目的とします。

## 正確な過年度 PLATEAU を使う場合

PLATEAU の個別ファイル一覧 API は最新版を利用する運用を基本としています。
過年度を固定したい場合は、その年度の `udx/bldg/*.gml` を用意して:

```yaml
plateau:
  source: local
  year: "2024"
  local_gml_dir: "/path/to/udx/bldg"
```

としてください。

## 注意

- 文化財座標が境内・敷地代表点であり、指定建造物そのものの位置ではない場合があります。
- 高密度市街地ではバッファ内の隣接建物が混入し得ます。
- PLATEAU の用途属性収録状況は自治体・整備年度で異なります。
- したがって `cultural_complex_building_linkage.csv` と GeoJSON を QA に使用してください。
