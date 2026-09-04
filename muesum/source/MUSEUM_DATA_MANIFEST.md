# Tokyo Museum Data Manifest

生成日時（UTC）: 2026-09-04T16:20:59+00:00

## 1. 方針

文化庁の登録博物館・指定施設を中核とし、その他の名簿・自治体・観光系情報を追加ソースとして保持する。令和6年度社会教育調査の210施設は規模の参照値であり、完全一致を要件としない。

## 2. 現時点の集計

| 項目 | 件数 |
|---|---:|
| 登録博物館（中核） | 83 |
| 指定施設（中核） | 50 |
| 中核計 | 133 |
| 追加ソースの取得レコード | 162 |
| 中核との重複レコード | 43 |
| 追加ソース間の重複レコード | 3 |
| 追加のユニーク候補 | 112 |
| 要確認レコード | 4 |
| 中核＋追加候補の暫定ユニーク推計 | 245 |
| R6社会教育調査の参照値 | 210 |

暫定ユニーク推計は、正規化名称と5桁自治体コードが完全一致する場合だけを自動重複として整理した値である。住所未確認、別館、複合施設、改称等の目視確認により変動する。

## 3. 情報源

| Tier | 役割 | 情報源 | 取得状態 | 取得件数 | URL |
|---|---|---|---|---:|---|
| A | core | 文化庁 博物館総合サイト・全国の博物館 | retrieved_network | 133 | https://museum.bunka.go.jp/guide/ |
| B | supplement | 文化遺産オンライン参加館 | manifest_only | 0 | https://online.bunka.go.jp/ |
| B | supplement | 日本博物館協会 会員館紹介 | manifest_only | 0 | https://www.j-muse.or.jp/introduction/ |
| B | supplement | 全国科学博物館協議会 加盟館リスト | retrieved_network | 21 | https://jcsm.jp/list/membership/ |
| B | supplement | 全国文学館協議会 会員館一覧 | manifest_only | 0 | https://zenbunkyo.com/members |
| B | supplement | 大学博物館等協議会 | manifest_only | 0 | https://univ-museum.jp/ |
| B | supplement | 日本動物園水族館協会 正会員名簿・動物園 | retrieved_network | 8 | https://www.jaza.jp/about-jaza/structure/list-zoo |
| B | supplement | 日本動物園水族館協会 正会員名簿・水族館 | retrieved_network | 5 | https://www.jaza.jp/about-jaza/structure/list-aquarium |
| B | supplement | 日本水族館協会 正会員 | retrieved_network | 4 | https://www.j-aqua.org/member/ |
| C | supplement | 千代田ミュージアムネットワーク | retrieved_network | 36 | https://museum.net.city.chiyoda.lg.jp/list/ |
| C | supplement | 文の京ミュージアムネットワーク | retrieved_network | 38 | https://www.city.bunkyo.lg.jp/b014/p004189.html |
| C | supplement | 港区ミュージアムネットワーク | retrieved_network | 50 | https://www.minato-rekishi.com/musenet/museumlist.html |
| C | supplement | 全国美術館会議 小規模館ネットワーク | manifest_only | 0 | https://www.zenbi.jp/data_list.php?g=103 |
| D | discovery | 小さいとこネット・小さいとこまっぷ | manifest_only | 0 | https://chiisaitokonet.jimdofree.com/%E5%B0%8F%E3%81%95%E3%81%84%E3%81%A8%E3%81%93%E3%81%BE%E3%81%A3%E3%81%B7/ |
| C | supplement | 東京都オープンデータ・観光施設の分布と一覧 | manifest_only | 0 | https://portal.data.metro.tokyo.lg.jp/visualization/distribution-and-list-of-tourist-facilities/ |
| D | discovery | GO TOKYO 区市町村・観光協会関連リンク | manifest_only | 0 | https://www.gotokyo.org/jp/links/index.html |
| R | reference | 令和6年度社会教育調査・東京都博物館類似施設 | manifest_only | 0 | https://www.e-stat.go.jp/stat-search/files?page=1&toukei=00400004&tstat=000001017254 |

## 4. 自動照合規則

1. Unicode NFKC、空白・限定的な約物除去、明示的な名称別名表で名称を正規化する。
2. `正規化名称 + 5桁自治体コード` が中核と完全一致した場合だけ `duplicate_core` とする。
3. 同じキーが追加ソース間で重複した場合は、最初のレコードを候補として保持し、以後を `duplicate_supplement` とする。
4. 自治体コードを確定できないレコードは自動統合せず `needs_review` とする。
5. 曖昧一致、近傍住所、電話番号だけによる自動統合は行わない。

## 5. 限界

- 文化遺産オンラインと日本博物館協会は有力な追加ソースだが、現段階では安定した施設一覧一括取得方法を確定していないため、情報源台帳への収録に留めた。
- 地域ミュージアムネットワークには庭園、図書館、文書館、ギャラリー等が含まれる場合があり、最終的な対象判定が必要である。
- 登録・指定一覧には完全な住所がないため、PLATEAU建物ポリゴンとの照合前に公式サイト等から所在地を補完する必要がある。
- 210は集計上の参照値であり、個別施設の欠落を示す名簿ではない。
