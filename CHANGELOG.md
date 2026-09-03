# Changelog

## 0.5.5 - 2026-09-02

- `heritage-gml --version` の前に `pipeline` をimportしていたため重い依存関係のロードで長時間待つ問題を修正。引数解析後まで `pipeline`, `pandas`, `pyproj`, `geopandas` 等のロードを遅延。
- CLI `main(argv=None)` を導入し、version pathをテスト可能にした。
- `pyproject.toml`, `heritage_gml.__version__`, companion Heritage JSON/XML versionを0.5.5へ統一。
- source distributionからbuild/egg-info等の生成物を排除するクリーン配布方針を明記。
- v0.5.4以前のPLATEAUキャッシュ復旧、classification、災害リスク、matching/Complex/geometry仕様は変更なし。

## 0.5.4 - 2026-09-02

- APIモードのCityGML読込で `TimeoutError` / `OSError` / XML読込エラーを検出した場合、当該自治体のPLATEAUキャッシュを一括破棄し、GML全件を再取得して1回だけ再試行する復旧処理を追加。
- 個別の破損キャッシュを推測的に残す処理は行わず、自治体単位のキャッシュセットとして再取得する方針に変更。
- `scan_buildings` とsubset GML生成にファイル単位の進捗表示を追加し、読込失敗時に対象GMLパスと処理段階を明示。
- APIカタログ解決・PLATEAU mesh解決・download/cache reuseの進捗表示を追加。
- `--refresh-plateau-cache` を追加し、APIモードで対象自治体キャッシュを処理前に明示的に破棄可能にした。
- `--plateau-source local` を完全オフライン化。5桁自治体実行ではPLATEAU catalog/APIを参照せず、2桁実行ではローカルパス中の5桁自治体コードから対象を列挙。
- localモードの読込失敗ではユーザーのローカルGMLを削除・再取得しない。
- localモードのファイル一覧を `<code>_plateau_files_local.csv` に分離し、API実行で得たURL付き `<code>_plateau_files.csv` を上書きしないよう変更。
- cache recoveryの内容を `run_summary.json` の `cache_recovery_count` / `cache_recovery_events` に記録。
- `heritage-gml --version` を追加。package metadataと `heritage_gml.__version__` を0.5.4に統一。
- v0.5.2のclassification pass-through / `heritage-classification-patch`、v0.5.3のPLATEAU災害リスク属性対応を維持。
- Building matching、Complex grouping、geometry生成規則は変更なし。buffer、nearest-neighbour、convex hull、gap filling等は引き続き使用しない。

## 0.5.3 - 2026-08-29

- PLATEAU `bldg:Building` の `uro:bldgDisasterRiskAttribute` をBuilding読込時に抽出する機能を追加。
- `RiverFloodingRiskAttribute`, `TsunamiRiskAttribute`, `HighTideRiskAttribute`, `InlandFloodingRiskAttribute`, `ReservoirFloodingRiskAttribute`, `LandSlideRiskAttribute` の6類型に対応。
- `description`, `rank`, `rankOrg`, `depth`, `adminType`, `scale`, `duration`, `areaType` のraw code / codeSpaceを保持。
- ローカルPLATEAUパッケージに参照コードリストが存在する場合はラベルを解決。ネットワークによるコードリスト取得は行わない。
- `heritage_buildings_footprint` に災害リスク件数、リスク種別、洪水等の最大浸水深、洪水最大継続時間、カテゴリ集約値、`disaster_risks_json` を追加。
- GPKG属性テーブル `plateau_disaster_risk` を追加し、Buildingと災害リスク属性の1:N関係を保持。
- subset CityGMLでは元Buildingを丸ごとコピーする既存仕様により `uro:bldgDisasterRiskAttribute` をそのまま保持。Generic Attributeへの重複コピーはしない。
- 災害リスク属性は文化財Building照合・Complex grouping・文化財分類には使用しない。
- companion JSONのBuilding entityにも `disaster_risks` を追加。
- 都道府県GPKG mergeはテーブルを動的検出する既存実装のまま `plateau_disaster_risk` を統合可能。
- Data Tools側の変更は不要。

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
