# Changelog

## 0.5.0 - 2026-08-28

- movable専用の同一住所グループ化処理を廃止。
- `entity_class=movable` は意味分類として保持し、他の文化財レコードと同じ個別 matching/output pathへ統一。
- movableの `name` をComplex/場所名へ置換する処理を廃止。常に文化財レコード名を保持。
- `方書` を `address_detail` として入力・GPKG・CSV・companion JSON/XMLへ保持。
- Complex groupingに `grouping_method`, `complex_record_count` を追加。
- 同一Complex内で完全同一座標を共有する複数レコードを `shared_complex_coordinate` として識別。
- shared complex coordinateを個別Building位置として使わない安全策を追加（既定）。
- `heritage_complex_records` を追加し、Complex ↔ 文化財レコード関係を明示。
- Building Complexは直接一致したPLATEAU Buildingのみをmemberとし、MultiPolygon partを保持。
- Building未確定の複数レコードComplexを `complex_only` として保持し、推定Polygonを生成しない。
- `heritage_movable_items`, `heritage_movable_groups` の出力を廃止。
- 都道府県GeoPackage統合ツールを同一パッケージへ統合。
  - CLI: `heritage-gpkg-merge`
  - standalone launcher: `merge_heritage_gpkg.py`
- 自治体別出力の5桁自治体コードprefixを継続。

## 0.4.0 - 2026-08-27

- 主要成果物をGML + GPKGへ整理。
- Building Complex MultiPolygonとComplex↔Building member tableを導入。
- 自治体別outputファイルへ自治体コードprefixを追加。
