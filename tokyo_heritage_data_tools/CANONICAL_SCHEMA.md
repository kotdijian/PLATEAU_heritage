# Canonical schema — v0.2.2

`national.csv` と `municipal.csv` は **PLATEAU Heritage-GML Extractor v0.5.x** の入力を意識した共通schemaです。

東京都教育庁 `130001_cultural_property.csv` 自体をこのschemaへ変換することは本ツールの要件ではありません。

| column | meaning |
|---|---|
| source_level | `national` / `municipal_source` |
| source_authority | 文化庁または取得元自治体 |
| source_dataset | 原データセット名 |
| source_record_id | 原典ID |
| source_url | 原典URL |
| source_file | raw/正規化元ファイル |
| name | 文化財名称。所在場所名へ置換しない |
| name_kana | 文化財名称かな |
| place_name | 施設・場所名称 |
| address_detail | 方書・所在地詳細。例: `浅草寺境内` |
| owner | 所有者・管理者 |
| address | 所在地住所 |
| municipality | 市区町村名 |
| municipality_code | 5桁自治体コード |
| category | 指定・文化財分類 |
| type | Extractor用に正規化した種類 |
| designation | 指定レベル等 |
| designation_date | 指定・登録等の日付 |
| latitude / longitude | 原データの位置座標 |
| entity_class | `building_direct` / `movable` / `point`（意味分類） |
| geometry_role | `building_candidate_point` / `representative_point` |
| designation_level | `national` / `prefectural` / `municipal` / `ambiguous` |
| raw_category / raw_type / raw_designation | 原データ語彙 |

## Extractor v0.5.xとの重要な整合点

- `movable` は**意味分類のみ**で、専用の住所グループ処理を意味しません。
- `movable` の `geometry_role` は他の非建造物と同じ `representative_point` です。
- `歴史資料` は `point` とし、種類だけを理由に movable へ一律分類しません。
- `方書` は `address_detail` として失わず保持します。Complex判定は Extractor 側で実施します。
- `shared_complex_coordinate`、`complex_id`、`complex_name`、Building照合結果は Extractor が導出するため、この前処理schemaでは生成しません。
- buffer / nearest neighbour / 推定範囲は生成しません。

## Classification extension — v0.2.3

`heritage-classify` preserves the canonical/source columns and appends:

| column | meaning |
|---|---|
| designation_level_code | `national` / `prefectural` / `municipal` / `unknown` |
| designation_level_ja | `国` / `都` / `区市町村` / `不明` |
| designation_status_code | `designated` / `registered` / `selected` / `record_selected` / `local_other` / `unknown` |
| designation_status_ja | 指定 / 登録 / 選定 / 記録選択 / 独自制度 / 不明 |
| heritage_type_major_code | `tangible` / `intangible` / `folk` / `monument` / `cultural_landscape` / `preservation_technique` / `other` / `unknown` |
| heritage_type_major_ja | 有形文化財 / 無形文化財 / 民俗文化財 / 記念物 / 文化的景観 / 文化財保存技術 / その他 / 未判定 |
| heritage_type_detail | 建造物、考古資料、史跡、天然記念物等の詳細類型 |
| classification_confidence | `high` / `medium` / `low` |

For municipal sources, classification should normally start from `municipal_all_normalized.csv`. The classifier, not the legacy prefilter, creates the final `municipal_classified.csv` used by Heritage-GML.
