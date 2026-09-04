# Summary Results

> 本文書は `13_heritage_hazards.gpkg` から得られた集計結果・地図成果をまとめる。
> 開発過程、実装仕様、バージョン履歴は含めない。
> 数値・図表は生成スクリプトによって更新する。

## 1. 概要

東京都内の文化財オープンデータを統合し、Project PLATEAU および各種災害関連データと空間的に対応付けた結果を示す。

本書では、不動産文化財をレコード単位で集計する。動産文化財リストを含む場合は別表とする。災害情報は、PLATEAU 建築物ポリゴンに付与された情報だけでなく、point 位置から取得された災害情報も対象に含める。

---

## 2. 文化財データの統合・分布結果

### 2.1 基本集計

区市町村単位で以下を集計する。

- 指定・登録レベル
  - National
  - Tokyo Metropolitan
  - Municipal
- 指定 / 登録
- 文化財類型

#### Table 2-1. 区市町村別文化財レコード数

`tables/municipality_record_counts.csv`

#### Table 2-2. 区市町村 × 指定・登録レベル

`tables/municipality_designation_level.csv`

#### Table 2-3. 区市町村 × 指定 / 登録

`tables/municipality_designation_status.csv`

#### Table 2-4. 区市町村 × 文化財類型

`tables/municipality_cultural_type.csv`

#### Table 2-5. 動産文化財集計（該当する場合）

`tables/movable_cultural_properties.csv`

### 2.2 地図

#### Figure 2-1. 区市町村別文化財件数コロプレス

東京都全域（島嶼部除く）、伊豆諸島、小笠原諸島を分図する。

#### Figure 2-2. 指定・登録レベル別 point 分布

National / Tokyo Metropolitan / Municipal を色分けする。

#### Figure 2-3. 指定 / 登録別 point 分布

#### Figure 2-4. 文化財類型別 point 分布

---

## 3. 災害リスク集計

集計対象には、PLATEAU 建築物に対応する文化財だけでなく、point 位置から災害情報を取得できた文化財も含める。

### 3.1 想定震度

集計対象シナリオ：

1. 都心南部直下地震
2. 都心東部直下地震
3. 都心西部直下地震
4. 大正関東地震
5. 南海トラフ巨大地震

震度階級：

1. 5弱未満
2. 5弱
3. 5強
4. 6弱
5. 6強以上

シナリオごとに、縦軸を自治体、横軸を想定震度階級とするクロス集計表を作成する。

`tables/seismic_<scenario>_municipality.csv`

さらに以下とのクロス集計を作成する。

- 指定・登録レベル
- 指定 / 登録
- 文化財類型

### 3.2 浸水予想区域

浸水深階級：

1. 0
2. 0–0.5 m
3. 0.5–3 m
4. 3–5 m
5. 5 m以上

河川・流域別に集計し、以下とのクロス集計を作成する。

- 区市町村
- 指定・登録レベル
- 指定 / 登録
- 文化財類型

### 3.3 高潮

浸水深を同じ5階級に区分してクロス集計する。

### 3.4 津波

浸水深を同じ5階級に区分し、シナリオ別にクロス集計する。

---

## 4. 災害リスク分布図

### 4.1 東京都全体図

以下の3地域に分けて作成する。

- 東京都本土部（島嶼部除く）
- 伊豆諸島
- 小笠原諸島

小縮尺図では文化財を point で表示する。

想定震度 overview は以下の8シナリオを対象とする。

1. 都心南部直下地震
2. 都心東部直下地震
3. 都心西部直下地震
4. 多摩東部直下地震
5. 多摩西部直下地震
6. 立川断層帯地震
7. 大正関東地震
8. 南海トラフ巨大地震

その他の作成図：

- 火災
- 浸水予想区域：河川・流域別
- 高潮
- 津波：シナリオ別
- 災害リスク付与済み文化財の類型別分布
- 災害リスク類型別 point 分布

Overview は `tools/render_summary_maps.py --stage overview` で生成する。

### 4.2 Z=16 詳細図

詳細図の標準設定は、地理院タイル（淡色地図）、中心から半径 0.8 km、zoom 16 とする。

中心地点：

| 地点 | 緯度 | 経度 |
|---|---:|---:|
| 東京駅 | 35.68126 | 139.76671 |
| 東京都立上野高校 | 35.7186246 | 139.7698412 |
| JR両国駅 | 35.6957371 | 139.7936379 |
| 東京メトロ田原町駅 | 35.70984 | 139.79076 |

表示：

- 地理院タイル（淡色地図）
- 対象文化財 point
- 対象文化財 building footprint
- ハザードレイヤ

Detail の描画責任は次の2ツールに分離する。

- 浸水以外：`tools/render_city_hazard_focus.py`
- 浸水予想区域：`tools/render_inundation_map.py`

4地点を一括生成する場合：

```bash
python tools/render_city_hazard_focus.py \
  /path/to/13_heritage_hazards_a31a.gpkg \
  --detail-defaults \
  --radius-km 0.8 \
  --zoom 16
```

```bash
python tools/render_inundation_map.py \
  /path/to/13_heritage_hazards_a31a.gpkg \
  --detail-defaults \
  --hazard auto \
  --separate \
  --radius-km 0.8 \
  --zoom 16
```

出力構成：

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

各 detail 図では、対象範囲内にリスク地物がないハザードは出力しない。浸水予想区域は、東京都の point-grid 由来レイヤについてグリッドセルとして再構成して描画し、NoData は描画対象から除外する。A31a の荒川・多摩川は polygon のまま描画する。

---

## 5. 公開データ

GitHub 公開用派生データ：

- `public_data/13_heritage_public.gpkg`
- `public_data/hazard_map.gpkg`
- `public_data/geojson/heritage_buildings_risk.geojson`
- `public_data/geojson/heritage_buildings_footprint_risk.geojson`
- `public_data/geojson/heritage_complexes.geojson`
- `public_data/geojson/heritage_source_points.geojson`
- `public_data/SOURCE_LICENSES.csv`

---

## 6. 使用データ・出典

この節は `source_license` および `hazard_source_manifest` から生成する。

`tables/source_datasets.csv`

最低限、以下を掲載する。

- データセット名
- 提供主体
- 対象年 / 版
- 利用目的
- ライセンス
- 原データ URL / resource ID
- 本成果物で使用したレイヤ

---

## 付録：図表凡例

### 想定震度

| 階級 | 表示 |
|---|---|
| 1 | 5弱未満 |
| 2 | 5弱 |
| 3 | 5強 |
| 4 | 6弱 |
| 5 | 6強以上 |
