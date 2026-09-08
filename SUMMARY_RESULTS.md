# Summary Results

## 東京都文化財・PLATEAU・災害リスク統合分析

**最終更新:** 2026-09-08  
**最終レポート:** [`index.html`](./index.html)  
**集約対象:** `13_heritage_hazards.gpkg`  
**機械可読サマリー:** [`summary_results/metadata/run_summary.json`](./summary_results/metadata/run_summary.json)

本ファイルは、最終確定版 Results Report (`index.html`) と `summary_results/metadata/run_summary.json` を基準に、現行成果を簡潔に整理したサマリーである。開発途中のパッチ、旧版レポート、旧集計値は扱わない。詳細な地図、クロス集計、Detail Case、Municipality Case、Appendix、データソースは `index.html` を参照する。

---

## 1. 現行成果の位置づけ

現行成果は次の3層で管理する。

1. **`index.html`** — 最終確定版 Results Report。画像をHTML内に埋め込んだ静的レポートで、ブラウザ表示およびChromeからのPDF印刷を想定する。
2. **`summary_results/metadata/run_summary.json`** — 現行集計処理の主要件数・設定を保持する機械可読サマリー。
3. **`SUMMARY_RESULTS.md`** — 上記2成果を人間向けに要約する文書。

最終レポートは実行時のCSV再読込を行わない静的構成とし、表示後に表値が変更されない。画像も埋め込み済みである。

---

## 2. 解析対象

`run_summary.json` に記録されている現行集計値は以下のとおりである。

| 項目 | 現行値 |
|---|---:|
| GeoPackage | `13_heritage_hazards.gpkg` |
| ファイルサイズ | 12,537,577,472 bytes（約11.677 GiB） |
| 全文化財レコード | 4,423 |
| 不動産文化財（主分析対象） | 2,866 |
| 動産文化財 | 1,557 |
| 解析位置を持つ不動産文化財 | 1,453 |
| 解析位置 | 1,725 |
| A31a 浸水想定区域との対応レコード | 241 |
| 災害リスク類型付与行 | 5,151 |
| point-grid 水害データの最大探索距離 | 25 m |

解析位置数が解析対象レコード数を上回るのは、1件の文化財が複数のPLATEAU建築物等の解析位置に対応し得るためである。

---

## 3. 災害リスク統合結果

現行の災害リスク類型付与行は合計 **5,151行**である。1文化財レコードに複数の災害種別が対応し得るため、この値はユニーク文化財件数ではない。

| 災害リスク類型 | 付与行数 |
|---|---:|
| 想定震度 | 1,452 |
| 地震時延焼危険度 | 1,428 |
| 液状化 | 687 |
| 河川浸水 | 1,304 |
| 高潮 | 190 |
| 土砂災害 | 82 |
| 津波 | 8 |
| **合計** | **5,151** |

主要なリスク類型集計は `summary_results/tables/risk_type_by_*.csv` に出力される。

---

## 4. 想定震度シナリオ

現行処理では次の **8シナリオ**を扱う。

1. 都心南部直下地震
2. 都心東部直下地震
3. 都心西部直下地震
4. 多摩東部直下地震
5. 多摩西部直下地震
6. 立川断層帯地震
7. 大正関東地震
8. 南海トラフ巨大地震

最終レポートではシナリオ別の地図・集計を比較できるように構成している。

---

## 5. ハザード集計の扱い

### 5.1 想定震度

想定震度は正規化した震度階級を用い、自治体等の単位でクロス集計する。

### 5.2 地震時延焼危険度

PLATEAU建築物等に対応する地震時延焼危険度を文化財解析位置へ対応付け、独立したリスク類型として集計する。

### 5.3 液状化

液状化はPL値を基礎とし、最終レポートでは次の階級で扱う。

- `PL=0`
- `0<PL≤5`
- `5<PL≤15`
- `PL>15`
- `No data`

`PL>0` を液状化リスク付与の基準として扱い、`No data` は有効値とは分離する。

### 5.4 河川浸水

point-grid 型の水害データは、文化財解析位置から最大 **25 m** の探索距離で対応値を取得する。国土数値情報 A31a の荒川・多摩川はpolygonとして扱い、現行集計では計 **241レコード**が対応する。

A31a の内訳は以下である。

| 河川 | 対応レコード |
|---|---:|
| 荒川 | 196 |
| 多摩川 | 45 |
| **計** | **241** |

### 5.5 高潮・津波

高潮・津波は浸水深階級を用いて集計する。津波のAppendixでは、地域・シナリオに一致する正式なMunicipalityクロス集計が存在する場合のみその表を使用し、異なる地域・シナリオの汎用表を流用しない。

### 5.6 土砂災害

土砂災害は独立したハザードとして集計する。Appendixでは全ハザードを含む横持ち表ではなく、土砂災害に限定した自治体クロス集計を使用する。

---

## 6. クロス集計の確定ルール

最終レポートのクロス集計は、次のルールを確定仕様とする。

- **`Total = 有効なリスク階級・スコア列の合計`**
- **`No data` は `Total` に含めない**
- `No data > 0` の場合も、`Total - 有効値合計 = 0` を満たす
- 有効値がなく `Total = 0` となる行は表示しない
- 津波は地域×シナリオに一致する正式なクロス集計を使用する
- 土砂災害は土砂災害専用のクロス集計を使用する

このルールは、最終レポート作成時に静的HTML上の表へ適用されている。

---

## 7. 最終レポートの構成

`index.html` は以下を中心に構成する。

- 全体目次
- 文化財・災害リスクのOverview
- 想定震度
- 地震時延焼危険度
- 液状化
- 河川浸水
- 高潮
- 津波
- 土砂災害
- Detail Case
  - 東京駅
  - 上野周辺
  - 田原町周辺
  - 両国周辺
- Municipality Case
  - 国分寺市
  - 国立市
- Appendix
  - ハザード別・地域別・シナリオ別の地図とクロス集計
  - データソース

Appendixの地図＋クロス集計ページは、Chromeの印刷機能からPDF化した場合にも1ページ内に収まりやすい印刷用レイアウトを適用している。

---

## 8. 主な成果物

```text
index.html
SUMMARY_RESULTS.md
summary_results/
├── figures/
│   ├── overview/
│   ├── detail/
│   └── city/
├── tables/
└── metadata/
    └── run_summary.json
```

- `index.html`: 最終確定版・画像埋め込み済みResults Report
- `summary_results/figures/overview/`: 東京都全体・地域別Overview
- `summary_results/figures/detail/`: Detail Case画像
- `summary_results/figures/city/`: Municipality Case等の自治体単位画像
- `summary_results/tables/`: クロス集計・レコード集計
- `summary_results/metadata/run_summary.json`: 実行結果の主要メタデータ

解析用完全版 `13_heritage_hazards.gpkg` は約12.5 GBであり、GitHubリポジトリでは直接配布しない。

---

## 9. 主な実装

現行処理の中心は以下である。

- `tools/build_summary_results.py` — 集計成果生成
- `tools/render_summary_maps.py` — Overview・ハザード地図生成
- `tools/render_city_hazard_focus.py` — Detail / Municipality系ハザード表示
- `tools/render_inundation_map.py` — 浸水系Detail表示
- `complete_risk_summary.py` — 災害リスク統合集計
- `complete_risk_overview.py` — 液状化・土砂災害等を含むOverview補完処理

開発途中に使用した `apply_*patch.py`、`patch_*.py`、`fix_*.py` 等の一時スクリプトは、必要機能を本体へ統合したうえで削除している。

---

## 10. 参照

- 最終レポート: [`index.html`](./index.html)
- 実行サマリー: [`summary_results/metadata/run_summary.json`](./summary_results/metadata/run_summary.json)
- 集計表: [`summary_results/tables/`](./summary_results/tables/)
- リポジトリ: <https://github.com/kotdijian/PLATEAU_heritage>

数値の再確認では、`SUMMARY_RESULTS.md` 単独ではなく、`run_summary.json` および対応する `summary_results/tables/` を一次的な集計成果として参照する。
