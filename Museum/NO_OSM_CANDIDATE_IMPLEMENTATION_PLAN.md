# OSM候補なし施設の位置補完実装案

対象は`museum_osm_audit.csv`で`no_candidate`となった施設のうち、PLATEAU建物が未確定の施設である。OSMに施設objectがないことと、所在地情報がないことは同義ではないため、再検索前に既存の検証済み住所・ABR座標を利用する。

## 実装順序

1. **ABR座標の再利用**  
   `museum_location_enrichment.csv`に`coordinate_use=building_candidate`の座標があれば、同一自治体内のPLATEAU footprintを300m以内・上位12棟に限定してレビューキューへ送る。自動確定条件は従来どおり一意包含だけとする。
2. **公式施設ページの構造化情報**  
   canonicalの`official_url`、自治体・国公立館の公式ページからJSON-LDの`PostalAddress`、`GeoCoordinates`、アクセスページ、地図リンクを取得する。名称と自治体が一致する公式主体だけを採用する。
3. **行政オープンデータ**  
   東京都オープンデータの博物館データ、公共施設一覧、区市町村の文化・教育・観光施設データをmanifest駆動で取得する。名称完全一致または明示aliasと自治体コード一致を必要条件とする。
4. **文化系横断API**  
   文化遺産オンライン参加館とジャパンサーチWeb APIを追加候補として照会する。元機関、元URL、取得日時、レスポンスSHA-256を保持し、集約サイトだけを根拠とする自動確定は行わない。
5. **人手確認**  
   上記で一意化できないものは、住所点、候補建物、公式ページを同じレビュー画面に表示する。

## 自動採用条件

- 公式施設・行政機関が明示した住所または座標
- canonical名称または承認済みaliasと自治体コードが一致
- 移転・分館・同名施設の競合がない
- 座標を使用する場合は粒度が街区より詳細
- PLATEAU自動確定は一つのfootprintに包含される場合のみ

それ以外は`needs_review`とし、最近傍だけを理由に確定しない。

## 主な機械取得候補

- [東京都オープンデータ「施設関連情報_博物館」](https://spec.api.metro.tokyo.lg.jp/spec/t000021d2000000004-1185de5e293357197b75aaceff889770-0)
- [東京都オープンデータ「公共施設一覧」](https://catalog.data.metro.tokyo.lg.jp/dataset/t000029d0000000030)
- [文化遺産オンライン・博物館一覧](https://online.bunka.go.jp/museums)
- [ジャパンサーチ開発者向けWeb API](https://jpsearch.go.jp/static/developer/webapi/ja.html)
- [デジタル庁アドレス・ベース・レジストリ](https://www.digital.go.jp/policies/base_registry_address)

## 出力案

- `museum_no_osm_location_candidates.csv`
- `museum_no_osm_source_audit.csv`
- `museum_no_osm_review.csv`
- 既存の`museum_manual_review_buildings`への候補追加

まずABR座標保有数をGPKGから集計する。これにより、62件すべてを再度ウェブ検索するのではなく、追加収集が本当に必要な対象だけを限定できる。
