# Museum所在地確認 override batch 1

確認日: 2026-09-06

v0.2.3の`museum_location_review.csv`に残った32施設から、施設・国立機関・自治体の公式ページで所在地を一意に確認できた8施設を`config/location_source_overrides.csv`へ追加した。

| museum_id | 施設名 | 確認住所 | 出典 |
|---|---|---|---|
| MUS-13a3749db7b4 | 府中市美術館 | 東京都府中市浅間町1丁目3番地（都立府中の森公園内） | https://www.city.fuchu.tokyo.jp/shisetu/komyunite/gekijo/bijyutukan.html |
| MUS-2da88e2469af | 草間彌生美術館 | 東京都新宿区弁天町107 | https://yayoikusamamuseum.jp/access/ |
| MUS-3541571d7c2c | 荏原 畠山記念館 | 東京都港区白金台2-20-12 | https://www.hatakeyama-museum.org/guide/access/ |
| MUS-a05dd0225aa2 | 切手の博物館 | 東京都豊島区目白1-4-23 | https://kitte-museum.jp/guide/access/ |
| MUS-a44cfd6cc5c2 | 皇居三の丸尚蔵館 | 東京都千代田区千代田1-8 皇居東御苑内 | https://shozokan.nich.go.jp/visit/access |
| MUS-ac49a46ba464 | 東京オペラシティアートギャラリー | 東京都新宿区西新宿3-20-2 | https://www.operacity.jp/ag/access/ |
| MUS-b2f65beb4419 | 古賀政男音楽博物館 | 東京都渋谷区上原三丁目6-12 | https://www.koga.or.jp/access/ |
| MUS-beb0e8f2c733 | 世田谷区立世田谷美術館 | 東京都世田谷区砧公園1-2 | https://www.setagayaartmuseum.or.jp/guide/access/ |

## 期待値

v0.2.3の確定213件へ8件が追加されるため、他の入力・取得結果が同一なら次の値を期待する。

```text
verified_address_count: 221
verified_address_rate: 0.9020408163265307
location_needs_review_count: 24
address_over_90_percent: true
```

この件数は実行前の予測値であり、再生成した`museum_location_summary.json`で確定する。

## 再生成

リポジトリルートから実行する。

```bash
python Museum/source/scripts/build_museum_locations.py
```

構造化ソースも更新する場合は次のとおり。

```bash
python Museum/source/scripts/build_museum_locations.py --refresh
```
