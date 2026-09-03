# 東京都・都内自治体 文化財オープンデータ API カタログ
生成日: 2026-08-27

## ファイル
- `tokyo_cultural_property_api_catalog_2026-08-27.csv`: 「文化財一覧」型APIを確認した東京都本体＋自治体の一覧
- `tokyo_cultural_property_api_catalog_2026-08-27.json`: 同内容のJSON
- `tokyo_cultural_property_api_sources_2026-08-27.yml`: Cultural PLATEAU Extractor等で利用しやすいYAML
- `tokyo_cultural_property_api_coverage_2026-08-27.csv`: 東京都本体＋都内62自治体の確認状況

## API URL
東京都オープンデータAPIカタログでは、API ID が確定している場合、原則として以下を使用する。

- 仕様: `https://spec.api.metro.tokyo.lg.jp/spec/{api_id}`
- JSON: `https://service.api.metro.tokyo.lg.jp/api/{api_id}/json`
- XML: `https://service.api.metro.tokyo.lg.jp/api/{api_id}/xml`
- HTTP method: POST

## 判定
- `API確認`: 現行東京都APIカタログで文化財一覧型APIとAPI IDを確認
- `API確認・現行API ID未特定`: API掲載は確認したが現行ハッシュ付きAPI IDを一意に確定できなかったもの
- `CSVカタログあり・API未確認`: 文化財一覧CSV等は存在するが東京都APIカタログ上の対応APIを今回確認できなかったもの
- `関連APIあり（非統合）`: 文化財関連APIはあるが、指定・登録文化財をまとめた統合一覧ではないもの
- `統合文化財一覧API未確認`: 今回の確認では現行統合APIを確認できなかったもの。「存在しない」という意味ではない。

## 注意
APIカタログには旧推奨データセットと自治体標準オープンデータセットが併存する場合がある。
同一自治体で複数API IDが見つかった場合は、原則として新しいデータセット／リソースを優先した。
API IDはリソース更新等で変更される可能性があるため、ツール実装では定期的な再取得・検証を推奨する。
