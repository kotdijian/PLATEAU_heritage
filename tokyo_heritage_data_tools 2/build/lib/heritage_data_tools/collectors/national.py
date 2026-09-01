from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
import gzip
import json
import math
import re
import time

from bs4 import BeautifulSoup

from ..http import session
from ..util import utc_now, write_json, validate_pref_code


BASE = "https://bunka.nii.ac.jp"
SEARCH_URL = BASE + "/heritages/search"
MUSEUM_FILTER = "国指定文化財等データベース"


def _save_gzip(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)


def _count_from_text(text: str) -> int | None:
    vals = [int(x.replace(",", "")) for x in re.findall(r"([0-9][0-9,]*)件", text)]
    return max(vals) if vals else None


def parse_search_page(html: str) -> tuple[int | None, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    count = _count_from_text(soup.get_text(" ", strip=True))
    urls = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"/heritages/detail/\d+", href):
            u = urljoin(BASE, href)
            if u not in seen:
                seen.add(u)
                urls.append(u)
    return count, urls


def _pairs_from_soup(soup: BeautifulSoup) -> dict[str, str]:
    pairs = {}

    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            k = dt.get_text(" ", strip=True)
            v = dd.get_text(" ", strip=True)
            if k and v:
                pairs.setdefault(k, v)

    for tr in soup.find_all("tr"):
        th = tr.find("th")
        td = tr.find("td")
        if th and td:
            k = th.get_text(" ", strip=True)
            v = td.get_text(" ", strip=True)
            if k and v:
                pairs.setdefault(k, v)

    # Some versions use adjacent heading/value blocks rather than dt/dd.
    labels = (
        "名称", "ふりがな", "文化財種類", "種別", "所在地", "所在都道府県",
        "所有者", "管理団体", "指定年月日", "登録年月日", "選定年月日",
        "時代", "員数", "構造及び形式",
    )
    for label in labels:
        node = soup.find(string=lambda x: isinstance(x, str) and x.strip().rstrip("：:") == label)
        if node:
            parent = node.parent
            nxt = parent.find_next()
            tries = 0
            while nxt is not None and tries < 6:
                txt = nxt.get_text(" ", strip=True) if hasattr(nxt, "get_text") else ""
                if txt and txt != label:
                    pairs.setdefault(label, txt)
                    break
                nxt = nxt.find_next()
                tries += 1
    return pairs


def _first(pairs: dict[str, str], *needles: str) -> str:
    for needle in needles:
        for k, v in pairs.items():
            kk = re.sub(r"\s+", "", k)
            if needle in kk and v:
                return v.strip()
    return ""


def _coordinates(html: str) -> tuple[float | None, float | None]:
    patterns = [
        # lat/lng JavaScript or JSON keys
        r'["\\\']?(?:lat|latitude)["\\\']?\s*[:=]\s*["\\\']?([0-9]{2}\.[0-9]+).*?["\\\']?(?:lng|lon|longitude)["\\\']?\s*[:=]\s*["\\\']?([0-9]{3}\.[0-9]+)',
        r'["\\\']?(?:lng|lon|longitude)["\\\']?\s*[:=]\s*["\\\']?([0-9]{3}\.[0-9]+).*?["\\\']?(?:lat|latitude)["\\\']?\s*[:=]\s*["\\\']?([0-9]{2}\.[0-9]+)',
        r'LatLng\(\s*([0-9]{2}\.[0-9]+)\s*,\s*([0-9]{3}\.[0-9]+)\s*\)',
    ]
    for i, pat in enumerate(patterns):
        m = re.search(pat, html, flags=re.I | re.S)
        if not m:
            continue
        a, b = float(m.group(1)), float(m.group(2))
        if i == 1:
            lon, lat = a, b
        else:
            lat, lon = a, b
        if 20 <= lat <= 50 and 120 <= lon <= 155:
            return lat, lon
    return None, None


def parse_detail_page(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    pairs = _pairs_from_soup(soup)

    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = og["content"].strip()
    if not title:
        for tag in ("h1", "h2"):
            node = soup.find(tag)
            if node and node.get_text(strip=True):
                title = node.get_text(" ", strip=True)
                break

    # Remove common site suffixes from og:title.
    title = re.sub(r"\s*[|｜]\s*文化遺産オンライン.*$", "", title).strip()

    lat, lon = _coordinates(html)
    detail_id_match = re.search(r"/heritages/detail/(\d+)", url)

    return {
        "detail_id": detail_id_match.group(1) if detail_id_match else "",
        "source_url": url,
        "name": _first(pairs, "名称") or title,
        "name_kana": _first(pairs, "ふりがな"),
        "category_raw": _first(pairs, "文化財種類"),
        "type_raw": _first(pairs, "種別"),
        "address": _first(pairs, "所在地"),
        "prefecture_raw": _first(pairs, "所在都道府県", "都道府県"),
        "owner": _first(pairs, "所有者"),
        "place_name": _first(pairs, "保管施設", "公開契約館"),
        "address_detail": _first(pairs, "方書", "所在地詳細", "所在詳細"),
        "designation_date": _first(pairs, "指定年月日", "登録年月日", "選定年月日"),
        "period": _first(pairs, "時代"),
        "latitude": lat,
        "longitude": lon,
        "metadata": pairs,
    }


def _fetch_one(s, url: str, timeout, delay: float):
    if delay > 0:
        time.sleep(delay)
    r = s.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def collect_online(
    pref_code: str,
    output_dir: str | Path,
    timeout: tuple[int, int] = (30, 120),
    retries: int = 3,
    workers: int = 4,
    delay: float = 0.15,
    save_html: bool = True,
    resume: bool = True,
    max_pages: int | None = None,
    max_details: int | None = None,
):
    pref_code = validate_pref_code(pref_code)
    out = Path(output_dir)
    search_dir = out / "search_pages"
    detail_dir = out / "detail_pages"
    out.mkdir(parents=True, exist_ok=True)
    s = session(retries=retries)

    first = s.get(
        SEARCH_URL,
        params={"prefecture_cd": pref_code, "museum": MUSEUM_FILTER, "page": 1},
        timeout=timeout,
    )
    first.raise_for_status()
    total, urls = parse_search_page(first.text)
    if save_html:
        _save_gzip(search_dir / "page_0001.html.gz", first.text)

    page_count = math.ceil(total / 20) if total else 1
    if max_pages:
        page_count = min(page_count, max_pages)

    all_urls = list(urls)
    seen = set(all_urls)

    for page in range(2, page_count + 1):
        r = s.get(
            SEARCH_URL,
            params={"prefecture_cd": pref_code, "museum": MUSEUM_FILTER, "page": page},
            timeout=timeout,
        )
        r.raise_for_status()
        _, page_urls = parse_search_page(r.text)
        if save_html:
            _save_gzip(search_dir / f"page_{page:04d}.html.gz", r.text)
        for u in page_urls:
            if u not in seen:
                seen.add(u)
                all_urls.append(u)
        print(f"search page {page}/{page_count}: detail URLs={len(all_urls)}")
        if delay:
            time.sleep(delay)

    if max_details:
        all_urls = all_urls[:max_details]

    records_path = out / "records.jsonl"
    existing = {}
    if resume and records_path.exists():
        for line in records_path.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(line)
                if obj.get("source_url"):
                    existing[obj["source_url"]] = obj
            except Exception:
                pass

    records = dict(existing)
    to_fetch = [u for u in all_urls if u not in existing]
    errors = []

    def worker(url):
        ss = session(retries=retries)
        html = _fetch_one(ss, url, timeout, delay)
        rec = parse_detail_page(url, html)
        if save_html:
            did = rec.get("detail_id") or str(abs(hash(url)))
            _save_gzip(detail_dir / f"{did}.html.gz", html)
        return rec

    if to_fetch:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futures = {ex.submit(worker, u): u for u in to_fetch}
            done = 0
            for fut in as_completed(futures):
                u = futures[fut]
                done += 1
                try:
                    rec = fut.result()
                    records[u] = rec
                except Exception as e:
                    errors.append({"source_url": u, "error": str(e)})
                if done % 25 == 0 or done == len(to_fetch):
                    print(f"details: {done}/{len(to_fetch)} (errors={len(errors)})")

    ordered = [records[u] for u in all_urls if u in records]
    with records_path.open("w", encoding="utf-8") as f:
        for rec in ordered:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    write_json(out / "collection_manifest.json", {
        "source": "文化遺産オンライン / 国指定文化財等データベース（文化庁）",
        "search_url": SEARCH_URL,
        "prefecture_code": pref_code,
        "reported_total": total,
        "pages_scanned": page_count,
        "detail_urls": len(all_urls),
        "records_collected": len(ordered),
        "errors": errors,
        "collected_at": utc_now(),
    })
    return ordered


def ingest_official_csv(
    input_files: list[str],
    output_dir: str | Path,
    overwrite: bool = False,
):
    """Stable fallback for CSVs manually exported from the official national DB.

    The official database currently documents a <=2,000 record restriction for
    CSV export. This command preserves those exported files as raw source data.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    copied = []
    for i, src in enumerate(input_files, 1):
        p = Path(src).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(p)
        dest = out / f"official_export_{i:03d}{p.suffix.lower() or '.csv'}"
        if dest.exists() and not overwrite:
            raise FileExistsError(dest)
        dest.write_bytes(p.read_bytes())
        copied.append({
            "source_path": str(p),
            "local_path": str(dest),
            "size_bytes": dest.stat().st_size,
        })
    write_json(out / "official_csv_ingest_manifest.json", {
        "source": "国指定文化財等データベース CSV export",
        "ingested_at": utc_now(),
        "files": copied,
    })
    return copied
