# Results Report

## 東京都文化財・PLATEAU・災害リスク統合分析

**集約対象:** `13_heritage_hazards.gpkg`  
**報告更新日:** 2026-09-05

本報告は、東京都内の文化財オープンデータを統合し、Project PLATEAU の建築物情報および各種災害関連データと空間的に対応付けた結果をまとめる。開発過程、実装履歴、バージョン履歴は扱わず、現在得られている集計値・地図成果・公開成果を記載する。

> **集計上の注意**  
> 現在 `summary_results/tables/` に格納されている定量集計は、A31a（国土数値情報 洪水浸水想定区域・荒川／多摩川）を canonical GeoPackage に統合する直前の集計結果である。A31a は現在の `13_heritage_hazards.gpkg` と Detail 地図には含まれているが、本報告に示す既存の河川浸水リスク件数にはまだ加算していない。このため、A31a に関する定量値は記載しない。

---

## 1. 成果の概要

現在の canonical GeoPackage は 11.677 GiB、258 レイヤ／テーブルからなり、A31a の荒川・多摩川レイヤを含む。

| 項目 | 結果 |
|---|---:|
| GeoPackage | `13_heritage_hazards.gpkg` |
| ファイルサイズ | 12,537,577,472 bytes / 11.677 GiB |
| レイヤ／テーブル数 | 258 |
| A31a 荒川 | 収録 |
| A31a 多摩川 | 収録 |
| A31a source_license | 1 record |
| SHA-256 | `baa5191389723a29131a257a708b481fa2c630439ba4abe44c5164dcefe92bb3` |

文化財レコードは全 4,423 件で、このうち不動産文化財を主たる分析対象 2,866 件、動産文化財 1,557 件として分離した。解析に使用できる位置を持つ不動産文化財は 1,453 レコード、解析位置は 1,725 箇所である。

| 文化財データ | 件数 | 全レコード比 |
|---|---:|---:|
| 全文化財レコード | 4,423 | 100.0% |
| 不動産文化財（主分析対象） | 2,866 | 64.8% |
| 動産文化財 | 1,557 | 35.2% |
| 解析位置を持つ不動産文化財 | 1,453 | 不動産文化財の 50.7% |
| 解析位置 | 1,725 | — |

解析位置は、PLATEAU 建築物と対応した場合には建築物代表点を用い、それ以外では文化財レコード自身の point を用いる。このため、1文化財レコードが複数の建築物に対応する場合があり、解析位置数は解析対象レコード数より多い。

参照: `summary_results/metadata/run_summary.json`

---

## 2. 文化財の地域分布

不動産文化財 2,866 件は 56 区市町村に分布する。集計対象のうち、23区内が 2,096 件（73.1%）、市部が 735 件（25.6%）、町村・島嶼部が 35 件（1.2%）である。

件数上位10自治体は以下のとおりである。

| 順位 | 自治体 | 件数 | 構成比 |
|---:|---|---:|---:|
| 1 | 江東区 | 399 | 13.9% |
| 2 | 新宿区 | 256 | 8.9% |
| 3 | 江戸川区 | 242 | 8.4% |
| 4 | 葛飾区 | 218 | 7.6% |
| 5 | 豊島区 | 159 | 5.5% |
| 6 | 台東区 | 139 | 4.8% |
| 7 | あきる野市 | 133 | 4.6% |
| 8 | 墨田区 | 115 | 4.0% |
| 9 | 杉並区 | 103 | 3.6% |
| 10 | 東久留米市 | 96 | 3.3% |

上位10自治体で 1,860 件、全不動産文化財の 64.9% を占める。したがって、統合済みデータの分布は自治体間で均等ではなく、収録件数の多い地域に強く集中している。この差は文化財の実分布だけでなく、原データの公開範囲・粒度・記録単位の違いも含むため、自治体間の単純な文化財密度比較には注意が必要である。

主要集計表:

- `summary_results/tables/municipality_record_counts.csv`
- `summary_results/tables/municipality_designation_level.csv`
- `summary_results/tables/municipality_designation_status.csv`
- `summary_results/tables/municipality_cultural_type_major.csv`
- `summary_results/tables/municipality_cultural_type_detail.csv`
- `summary_results/tables/movable_cultural_properties.csv`

---

## 3. 災害リスクとの空間対応

災害リスクは、PLATEAU 建築物に付与されたリスク情報と、文化財 point から取得できる外部ハザード情報を併用している。現在追跡されている既存集計では、災害リスク類型の付与行は合計 4,449 行である。1レコードに複数の災害種別が対応し得るため、この値はユニーク文化財数ではない。

| 災害リスク類型 | 付与行数 |
|---|---:|
| 想定震度 | 1,452 |
| 地震時延焼危険度 | 1,428 |
| 河川浸水 | 1,289 |
| 高潮 | 190 |
| 土砂災害 | 82 |
| 津波 | 8 |
| **合計** | **4,449** |

### 3.1 想定震度

定量集計では次の5シナリオを代表シナリオとして使用している。

1. 都心南部直下地震
2. 都心東部直下地震
3. 都心西部直下地震
4. 大正関東地震
5. 南海トラフ巨大地震

想定震度は `5弱未満 / 5弱 / 5強 / 6弱 / 6強以上` の5階級に正規化し、自治体、指定・登録レベル、指定／登録、文化財類型ごとのクロス集計を生成している。

### 3.2 河川浸水

東京都建設局の浸水予想区域図は point-grid として収録されている。文化財リスク付与では point-grid を面として扱わず、文化財解析位置から最大 25 m の範囲で対応するグリッド値を取得する。浸水深は `0 / 0–0.5 m / 0.5–3 m / 3–5 m / 5 m以上` に正規化する。

現在の canonical GeoPackage には、これに加えて国土数値情報 A31a の荒川・多摩川（想定最大規模）が polygon として収録されている。A31a の深度階級は原データの rank を同じ4つの正の浸水深階級へ正規化して扱い、実測値・推定値としての厳密な深度値には変換しない。

**現行の定量集計表は A31a 統合前のため、上表の河川浸水 1,289 行には A31a を含まない。**

### 3.3 高潮・津波

高潮および津波についても同じ浸水深階級を用いる。高潮は浸水深を中心に、津波は地域・シナリオ別データから解析位置との対応を取得する。既存集計では高潮 190 行、津波 8 行のリスク付与が得られている。

主要集計表:

- `summary_results/tables/risk_type_by_municipality.csv`
- `summary_results/tables/risk_type_by_designation_level.csv`
- `summary_results/tables/risk_type_by_designation_status.csv`
- `summary_results/tables/risk_type_by_cultural_type.csv`
- `summary_results/tables/seismic_*`
- `summary_results/tables/river_flooding_depth_*`
- `summary_results/tables/high_tide_depth_*`
- `summary_results/tables/tsunami_depth_*`
- `summary_results/tables/water_risk_best_available_records.csv`
- `summary_results/tables/water_risk_external_point_assignments.csv`

---

## 4. 地図成果

### 4.1 Overview

東京都全体の地図は次の3地域に分けて作成している。

- 東京都本土部（島嶼部除く）
- 伊豆諸島
- 小笠原諸島

Overview の想定震度図は8シナリオを対象とする。

1. 都心南部直下地震
2. 都心東部直下地震
3. 都心西部直下地震
4. 多摩東部直下地震
5. 多摩西部直下地震
6. 立川断層帯地震
7. 大正関東地震
8. 南海トラフ巨大地震

このほか、地震時延焼危険度、河川・流域別浸水予想区域、高潮、津波、災害リスク付与済み文化財の類型別分布、災害リスク類型別 point 分布を作成している。

出力先: `summary_results/figures/overview/`

Overview 地図は `tools/render_summary_maps.py` が担当する。

### 4.2 Detail

Detail は地理院タイル（淡色地図）を背景とし、中心から半径 0.8 km、zoom 16 を標準とする。

| 地点 | 緯度 | 経度 |
|---|---:|---:|
| 東京駅 | 35.68126 | 139.76671 |
| 東京都立上野高校 | 35.7186246 | 139.7698412 |
| JR両国駅 | 35.6957371 | 139.7936379 |
| 東京メトロ田原町駅 | 35.70984 | 139.79076 |

各地点では文化財 point、PLATEAU building footprint、ハザードを重ね合わせている。浸水以外は `tools/render_city_hazard_focus.py`、浸水は `tools/render_inundation_map.py` が担当する。

```text
summary_results/figures/detail/
├── 東京駅/
│   ├── hazard/
│   └── inundation/
├── 東京都立上野高校/
│   ├── hazard/
│   └── inundation/
├── JR両国駅/
│   ├── hazard/
│   └── inundation/
└── 東京メトロ田原町駅/
    ├── hazard/
    └── inundation/
```

現行4地点の Detail 成果には、地震時延焼危険度、想定震度、液状化、高潮、津波等の対象範囲内ハザードが出力されている。浸水図については、東京都の point-grid を実グリッド間隔に基づくセルとして描画し、NoData を除外する。A31a は polygon のまま描画する。4地点の現行成果には A31a 荒川の浸水図が出力されている。

---

## 5. 公開成果

解析用完全版 `13_heritage_hazards.gpkg` は約12 GBであり、GitHub では直接配布しない。公開用には、文化財を中心とした軽量 GeoPackage と代表ハザード、GeoJSON を生成している。

- `public_data/13_heritage_public.gpkg`
- `public_data/hazard_map.gpkg`
- `public_data/geojson/heritage_buildings_risk.geojson`
- `public_data/geojson/heritage_buildings_footprint_risk.geojson`
- `public_data/geojson/heritage_complexes.geojson`
- `public_data/geojson/heritage_source_points.geojson`
- `public_data/SOURCE_LICENSES.csv`

完全版の同一性確認には以下を使用する。

- `output/13_heritage_hazards.sha256`
- `output/13_heritage_hazards_fileinfo.txt`

---

## 6. 主な使用データ

統合データには、文化財オープンデータと PLATEAU に加え、東京都および国土数値情報の災害関連データを含む。主なものは以下である。

| 種別 | 主なデータ | 提供主体 |
|---|---|---|
| 3D都市モデル | Project PLATEAU 3D都市モデル | 国土交通省・各地方公共団体 |
| 地域危険度 | 地震に関する地域危険度測定調査 | 東京都都市整備局 |
| 火災 | 地震時における地域別延焼危険度測定 | 東京消防庁 |
| 想定震度 | 令和4年度首都直下地震等による東京の被害想定 | 東京都総務局 |
| 液状化 | 同上 | 東京都総務局 |
| 河川浸水 | 浸水予想区域図 | 東京都建設局 |
| 河川浸水 | 洪水浸水想定区域（河川単位）A31a 2025 | 国土交通省・国土数値情報 |
| 高潮 | 高潮浸水想定区域図 | 東京都港湾局 |
| 津波 | 令和4年度首都直下地震等による東京の被害想定 | 東京都総務局 |

詳細な出典、ライセンス、利用条件、収録レイヤは GeoPackage 内の `source_license` / `hazard_source_manifest` および `summary_results/tables/source_datasets.csv` を参照する。

---

## 7. 結果の解釈上の留意点

1. **位置情報の有無** — 主分析対象 2,866 レコードのうち解析位置を持つのは 1,453 レコード（50.7%）であり、災害リスク空間分析は全文化財を網羅していない。
2. **自治体間のデータ差** — 文化財件数の差には実分布だけでなく、自治体ごとのオープンデータ公開範囲、記録単位、分類粒度の差が含まれる。
3. **point-grid の扱い** — 東京都浸水予想区域および一部津波データは point-grid であり、公開点を任意の polygon に変換して判定していない。文化財への付与は近傍グリッド値に基づく。
4. **A31a の深度** — A31a は深度 rank を階級へ正規化しており、厳密な連続深度値として扱わない。
5. **A31a と現行定量表** — canonical GeoPackage と Detail 地図には A31a が反映済みだが、現在追跡されている定量集計は A31a 統合前である。したがって本報告の河川浸水件数は A31a を含まない。

---

## 8. 成果物一覧

```text
13_heritage_hazards.gpkg                # canonical 完全版（repo外）
SUMMARY_RESULTS.md                       # 本報告
summary_results/
├── metadata/run_summary.json
├── tables/                              # 集計結果
└── figures/
    ├── overview/                        # 東京都全体図
    ├── detail/                          # 4地点 Detail
    └── city/                            # 市区町村別図
public_data/                             # GitHub 公開用派生データ
output/13_heritage_hazards.sha256
output/13_heritage_hazards_fileinfo.txt
```
