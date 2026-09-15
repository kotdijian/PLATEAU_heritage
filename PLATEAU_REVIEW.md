# PLATEAU 空間照合 Human Review

`plateau_review.py` は、PLATEAU建築物と対象施設・文化財の照合を人が地図上で監査するための共通ツールです。Museum専用ではなく、Museum hazard と Heritage hazard の双方で利用します。Museum固有処理からは `Museum/build_full_review.py` などの薄いラッパーを介して呼び出せます。

## 設計原則

- 自動照合済みを含む全対象を human check の対象にする
- 施設位置の周囲（既定200 m）にある全PLATEAU footprintを提示する
- 地理院地図を背景にして、PLATEAU・OSM・位置情報の不一致を目視確認できるようにする
- 機械判定と人手判定を別フィールドで保持し、自動誤判定の却下も監査記録に残す
- PLATEAU全棟走査は索引GeoPackageへキャッシュし、MuseumとHeritageで再利用する
- 同一 `gml:id` で形状が競合する建物は索引から除外し、索引サマリーへ記録する

## 出力レイヤ

| レイヤ・テーブル | 内容 |
|---|---|
| `review_location_points` | 施設・文化財のレビュー基準点と機械判定 |
| `review_neighborhood_buildings` | 基準点周辺の全PLATEAU建物footprint |
| `review_audit` | 機械判定とhuman checkを分離した監査表 |
| `review_metadata` | profile、入力、索引、検索半径、ツール版 |
| `review_human_selected_buildings` | 監査CSV適用後に人が採用した建物 |

## Museumでの実行

初回だけ `--rebuild-index` を付けます。以後は同じ索引を再利用します。

```bash
python Museum/build_full_review.py \
  "/path/to/13_museum_hazards_osm.gpkg" \
  --plateau-local-dir .cache/plateau \
  --rebuild-index \
  --radius-m 200 \
  --output "/path/to/13_museum_full_review.gpkg"

python Museum/render_full_review.py \
  "/path/to/13_museum_full_review.gpkg"
```

ブラウザで全245施設を確認します。判断は次のように記録されます。

| 画面上の判断 | `human_decision_type` |
|---|---|
| 自動照合が正しい | `auto_match_human_confirmed` |
| 位置情報から1棟を特定 | `location_guided_human_confirmed` |
| 複数棟を施設として採用 | `multiple_buildings_human_selected` |
| 自動候補を誤りとして却下し別棟を採用 | `auto_candidates_rejected_location_guided_selected` |
| PLATEAU footprintが存在しない | `plateau_footprint_absent` |
| 位置情報自体の確認が必要 | `location_requires_review` |
| 判断を保留 | `deferred` |

ダウンロードした監査CSVを検証・適用します。元レビューGPKGは上書きしません。

```bash
python Museum/apply_full_review.py \
  "/path/to/13_museum_full_review.gpkg" \
  ~/Downloads/plateau_human_review_audit.csv \
  --output "/path/to/13_museum_full_review_applied.gpkg"
```

## Heritageでの実行

```bash
python plateau_review.py \
  "/path/to/13_heritage_hazards.gpkg" \
  --profile heritage \
  --plateau-local-dir .cache/plateau \
  --radius-m 200 \
  --output "/path/to/13_heritage_full_review.gpkg"

python render_plateau_review.py "/path/to/13_heritage_full_review.gpkg"

python apply_plateau_review.py \
  "/path/to/13_heritage_full_review.gpkg" \
  ~/Downloads/plateau_human_review_audit.csv \
  --output "/path/to/13_heritage_full_review_applied.gpkg"
```

## 監査CSVの検証

適用時には、対象IDの存在、判断種別、確定時のBuilding選択、選択Buildingが提示範囲に含まれること、対象IDの重複を検証します。不正な行が1件でもあれば適用を中止するため、順序依存や部分適用は起こりません。

地理院タイルを表示するにはインターネット接続が必要です。背景地図の出典表示はHTML内に常時表示されます。
