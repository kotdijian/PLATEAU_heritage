# Canonical schema

`national.csv` と `municipal.csv` は `plateau-heritage-gml v0.3.x` の入力を意識した
共通schemaです。

東京都指定文化財CSV自体をこのschemaへ変換することは本ツールの要件ではありません。

| column | meaning |
|---|---|
| source_level | national / municipal_source |
| source_authority | 文化庁または自治体名 |
| source_dataset | データセット名 |
| source_record_id | 原典ID |
| source_url | 原典URL |
| name | 文化財名称 |
| place_name | 施設・場所名称 |
| owner | 所有者・管理者 |
| address | 所在地 |
| municipality_code | 5桁自治体コード |
| category | 指定・文化財分類 |
| type | GML処理用に正規化した種類 |
| designation | national / municipal 等 |
| entity_class | building_direct / movable / point |
| geometry_role | pointの役割 |
| raw_* | 原データ語彙 |
