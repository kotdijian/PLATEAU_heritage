from heritage_data_tools.collectors.national import parse_search_page, parse_detail_page

def test_search_parser():
    html = """
    <html><body>
      <div>42件</div>
      <a href="/heritages/detail/123">A</a>
      <a href="https://bunka.nii.ac.jp/heritages/detail/456">B</a>
      <a href="/heritages/detail/123">dup</a>
    </body></html>
    """
    count, urls = parse_search_page(html)
    assert count == 42
    assert urls == [
        "https://bunka.nii.ac.jp/heritages/detail/123",
        "https://bunka.nii.ac.jp/heritages/detail/456",
    ]

def test_detail_parser():
    html = """
    <html><head>
      <meta property="og:title" content="サンプル建物 | 文化遺産オンライン">
    </head><body>
      <dl>
        <dt>文化財種類</dt><dd>登録有形文化財（建造物）</dd>
        <dt>種別</dt><dd>住居建築</dd>
        <dt>所在地</dt><dd>東京都文京区本郷1-1</dd>
        <dt>登録年月日</dt><dd>2020-01-01</dd>
      </dl>
      <script>var p={"lat":35.70,"lng":139.76};</script>
    </body></html>
    """
    r = parse_detail_page("https://bunka.nii.ac.jp/heritages/detail/123", html)
    assert r["detail_id"] == "123"
    assert r["name"] == "サンプル建物"
    assert r["category_raw"] == "登録有形文化財（建造物）"
    assert r["type_raw"] == "住居建築"
    assert r["address"] == "東京都文京区本郷1-1"
    assert r["latitude"] == 35.70
    assert r["longitude"] == 139.76
