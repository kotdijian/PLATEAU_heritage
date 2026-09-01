# Changelog

## 0.5.1 - 2026-08-28

- `address_detail` から Complex 表示名を正規化する際、施設種別文字まで削除する不具合を修正。
- `小石川後楽園内 → 小石川後楽園`、`浅草寺内 → 浅草寺`、`日枝神社内 → 日枝神社`、`博物館内 → 博物館` のように、末尾の所在表現だけを除去するよう変更。
- `墓地内`、`境内地`、`敷地内`、`構内`、`境域内`、`境内` は従来どおり所在表現全体を除去。
- Complex grouping、Building matching、GML/GPKG、GPKG merge のその他の仕様は v0.5.0 から変更なし。

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

## 0.5.2
- Accept and pass through eight optional cultural classification columns produced by `heritage-classify`.
- Added classification fields to `heritage_records`, building links, complex-record tables, unresolved tables, and analytical Building/Complex layers.
- Added aggregated classification attributes to selected PLATEAU Building footprints and Building Complex polygons without changing matching or geometry logic.
- Added CityGML generic attributes for designation levels/statuses and heritage type major/detail values.
- Classification attributes are output-only metadata and are never used as Building-matching evidence.
- Added `heritage-classification-patch` for attribute-only updates of already-generated Heritage GPKG and subset CityGML. It does not rerun PLATEAU acquisition, Building matching, Complex grouping, or geometry generation.
- Extractor now recognizes canonical `source_record_id` as an input record ID alias.
