# Changelog

## 0.2.1
- `--area-code 13` が常に拒否される都道府県コード検証の正規表現バグを修正。
- 5桁自治体コード検証にも同じ修正を適用。
- 2桁都道府県コードと5桁自治体コードの回帰テストを追加。
- README / integrated README / canonical schema のバージョン表記を更新。

## 0.2.0
- PLATEAU Heritage-GML Extractor v0.5.x に正規化出力を整合。
- canonical schema に `address_detail` を追加し、`方書` / `住所詳細` / `所在地詳細` 等を保持。
- `movable` の専用空間処理を前提としないモデルへ変更。`geometry_role=representative_point` とする。
- `歴史資料` を `movable` から除外し、Extractor v0.5.x と同じく `point` として扱う。
- `美術工芸品・考古資料` の複合型を個別型へ潰さず保持できるよう修正。
- 国指定CSV/オンライン正規化でも `address_detail` を伝播。
- README / canonical schema / tests を v0.5.x ワークフローに更新。

## 0.1.0
- Initial Tokyo prototype.
- `heritage-collect municipal`: manifest-driven municipal CSV/API acquisition; Tokyo metropolitan dataset excluded.
- `heritage-collect national online`: Cultural Heritage Online national-database crawler with resume and raw HTML gzip preservation.
- `heritage-collect national ingest`: raw intake for official national database CSV exports.
- `heritage-normalize municipal`: canonical normalization plus national/prefectural/ambiguous segregation.
- `heritage-normalize national`: canonical normalization from Cultural Heritage Online JSONL and official CSV exports.
- Canonical `type` vocabulary aligned with `plateau-heritage-gml v0.3.x`.
