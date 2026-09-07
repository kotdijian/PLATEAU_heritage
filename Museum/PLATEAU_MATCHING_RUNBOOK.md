# Museum × PLATEAU 照合実行手順 v0.3.1

## 目的と判定境界

- canonical施設: 245件
- 検証済み住所: 221件（90.2%）
- 次の処理: ABR座標化 → PLATEAU targeted取得 → footprint照合
- 自動確定: 名称完全一致、強い用途を伴う一意な完全住所一致、または詳細粒度座標が一意に1棟へ入る場合
- 要確認: 低粒度座標、複数棟への点包含、住所だけの一致
- 未解決重複GML: 全コピーを隔離し、ファイル順では選ばない

## 1. ABR Geocoderへ東京都データを導入

Docker Desktopを起動し、abr-geocoderのリポジトリで実行します。

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

## 2. 221件を座標化

```bash
cd /Users/noguchiatsushi/Documents/GitHub/PLATEAU_heritage
source .venv/bin/activate

python Museum/source/scripts/build_museum_locations.py \
  --abr-api-base http://localhost:3000
```

確認します。

```bash
python - <<'PY'
import csv, json
from collections import Counter
from pathlib import Path

root = Path("Museum/source/data")
summary = json.loads((root / "museum_location_summary.json").read_text())
with (root / "museum_location_enrichment.csv").open(encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

print("verified_address_count:", summary["verified_address_count"])
print("verified_coordinate_count:", summary["verified_coordinate_count"])
print("coordinate_use:", dict(Counter(r["coordinate_use"] for r in rows)))
print("coordinate_level:", dict(Counter(r["coordinate_level"] for r in rows)))
PY
```

`verified_address_count=221`が維持され、`verified_coordinate_count`と`building_candidate`が0より大きいことを確認します。

## 3. PLATEAU照合をdry-run

必要に応じて、その前に`Museum/source/config/facility_spaces.csv`へ博物館所在階・展示室・収蔵庫を記録します。複数階は用途・連続階範囲ごとに複数行で記述します。未調査値は推定せず空欄または`unknown`とします。

```bash
python Museum/build_museum_hazard_gpkg.py \
  "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
  --plateau-source api-targeted \
  --plateau-local-dir .cache/plateau \
  --exclude-unresolved-duplicates \
  --dry-run
```

確認対象は次のsummary項目です。

- `location_coordinate_count`
- `location_building_candidate_point_count`
- `confirmed_facilities`
- `building_link_status_counts`
- `match_method_counts.unique_precise_point_in_building`
- `plateau_duplicate_audit.unresolved_duplicate_action`
- `plateau_duplicate_audit.excluded_unresolved_duplicate_gml_id_count`
- `museum_facility_spaces`
- `museum_facilities_with_floor_data`
- `museum_facilities_with_collection_storage`
- `museum_space_hazard_assessments`
- `space_exposure_status_counts`

## 4. GPKGを生成

dry-run結果を確認後に実行します。

```bash
python Museum/build_museum_hazard_gpkg.py \
  "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
  --plateau-source api-targeted \
  --plateau-local-dir .cache/plateau \
  --exclude-unresolved-duplicates \
  --output "/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_museum_hazards.gpkg"
```

既存出力を置き換える場合だけ`--overwrite`を追加します。出力には確定建物、要確認建物、所在地点、施設・出典・リンク・未解決テーブル、PLATEAU災害リスクを含みます。

## 5. 回帰テスト

```bash
python -m unittest discover -s Museum/tests -v
python -m unittest discover -s Museum/source/tests -v
```
