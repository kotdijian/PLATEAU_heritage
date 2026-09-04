#!/usr/bin/env python3
"""Build a reproducible Tokyo museum source manifest and conservative reconciliation."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from lxml import html


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
REFERENCE_TOTAL = 210
USER_AGENT = "PLATEAU-heritage-museum-manifest/0.1 (+research; contact via repository)"

CANDIDATE_FIELDS = [
    "record_id", "source_id", "source_role", "source_tier", "facility_name_raw",
    "facility_name_normalized", "municipality_code", "municipality_name", "postal_code",
    "address", "phone", "official_url", "facility_type", "museum_law_status",
    "record_status", "retrieved_at", "source_url", "notes",
]


def text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def normalize_name(value: str, aliases: dict[str, str] | None = None) -> str:
    value = unicodedata.normalize("NFKC", text(value))
    value = re.sub(r"^[◎○〇●]\s*", "", value)
    value = re.sub(r"[（(](?:休館中|長期休館中|閉館中)[）)]", "", value)
    value = re.sub(r"[\s・･,，、。．.\-‐‑‒–—―]+", "", value)
    value = value.casefold()
    if aliases and value in aliases:
        return aliases[value]
    return value


def stable_id(prefix: str, *values: str) -> str:
    body = "|".join(values).encode("utf-8")
    return f"{prefix}-{hashlib.sha1(body).hexdigest()[:12]}"


def read_csv_dict(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_municipalities() -> tuple[dict[str, str], dict[str, str]]:
    rows = read_csv_dict(CONFIG_DIR / "tokyo_municipalities.csv")
    code_to_name = {r["municipality_code"]: r["municipality_name"] for r in rows}
    name_to_code = {v: k for k, v in code_to_name.items()}
    return code_to_name, name_to_code


def load_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for row in read_csv_dict(CONFIG_DIR / "name_aliases.csv"):
        alias = normalize_name(row["alias"])
        aliases[alias] = normalize_name(row["canonical_name"])
    return aliases


def municipality_from_text(value: str, name_to_code: dict[str, str]) -> tuple[str, str]:
    value = text(value)
    for name in sorted(name_to_code, key=len, reverse=True):
        if name in value:
            return name_to_code[name], name
    return "", ""


def split_postal_address(value: str) -> tuple[str, str]:
    value = text(value)
    match = re.search(r"〒?\s*(\d{3}-\d{4})", value)
    postal = match.group(1) if match else ""
    if match:
        value = (value[: match.start()] + value[match.end() :]).strip()
    return postal, value


def absolute_url(base: str, href: str | None) -> str:
    return urllib.parse.urljoin(base, href or "") if href else ""


def fetch(source: dict[str, Any], offline: bool, refresh: bool) -> tuple[bytes, str]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{source['source_id']}.html"
    if cache_path.exists() and (offline or not refresh):
        data = cache_path.read_bytes()
        return data, "cache"
    if offline:
        raise FileNotFoundError(f"cache missing: {cache_path}")
    request = urllib.request.Request(source["url"], headers={"User-Agent": USER_AGENT})
    error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            cache_path.write_bytes(data)
            return data, "network"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed after retries: {error}")


def candidate(
    source: dict[str, Any], name: str, retrieved_at: str, aliases: dict[str, str],
    municipality_code: str = "", municipality_name: str = "", postal_code: str = "",
    address: str = "", phone: str = "", official_url: str = "", facility_type: str = "museum",
    museum_law_status: str = "", record_status: str = "supplement_candidate", notes: str = "",
) -> dict[str, str]:
    normalized = normalize_name(name, aliases)
    rid = stable_id("REC", source["source_id"], normalized, municipality_code, address)
    return {
        "record_id": rid,
        "source_id": source["source_id"],
        "source_role": source["source_role"],
        "source_tier": source["source_tier"],
        "facility_name_raw": text(name),
        "facility_name_normalized": normalized,
        "municipality_code": municipality_code,
        "municipality_name": municipality_name,
        "postal_code": postal_code,
        "address": text(address),
        "phone": text(phone),
        "official_url": official_url,
        "facility_type": facility_type,
        "museum_law_status": museum_law_status,
        "record_status": record_status,
        "retrieved_at": retrieved_at,
        "source_url": source["url"],
        "notes": notes,
    }


def collect_bunka_core(source, doc, retrieved_at, aliases, name_to_code):
    headings = doc.xpath('//h3[normalize-space(.)="東京都"]')
    if not headings:
        raise ValueError("東京都 section not found")
    container = next(headings[0].itersiblings())
    rows: list[dict[str, str]] = []
    for section in container.xpath('./div[contains(@class,"pref_item_child")]'):
        classes = section.get("class", "")
        is_registered = "registered" in classes
        status = "core_registered" if is_registered else "core_designated"
        legal = "registered" if is_registered else "designated_facility"
        for li in section.xpath('.//li'):
            name = text(li.xpath('string(.//*[contains(@class,"mus_name")])'))
            locality = text(li.xpath('string(.//*[contains(@class,"location")])'))
            if not name:
                continue
            code, muni = municipality_from_text(locality, name_to_code)
            link = li.xpath('.//a[1]')
            rows.append(candidate(
                source, name, retrieved_at, aliases, code, muni, official_url=(link[0].get("href") if link else ""),
                museum_law_status=legal, record_status=status,
            ))
    counts = {s: sum(r["record_status"] == s for r in rows) for s in ("core_registered", "core_designated")}
    if counts != {"core_registered": 83, "core_designated": 50}:
        raise ValueError(f"unexpected core counts: {counts}")
    return rows


def collect_jcsm(source, doc, retrieved_at, aliases, name_to_code):
    target = None
    for table in doc.xpath('//table'):
        heading = text(table.xpath('string(preceding::*[self::h2 or self::h3 or self::h4][1])'))
        if heading == "東京都":
            target = table
            break
    if target is None:
        raise ValueError("Tokyo table not found")
    rows = []
    for tr in target.xpath('.//tr[position()>1]'):
        cells = tr.xpath('./th|./td')
        if len(cells) < 2:
            continue
        name, raw_address = text(cells[0].text_content()), text(cells[1].text_content())
        postal, address = split_postal_address(raw_address)
        code, muni = municipality_from_text(address, name_to_code)
        link = cells[0].xpath('.//a[1]')
        rows.append(candidate(
            source, name, retrieved_at, aliases, code, muni, postal, address,
            official_url=absolute_url(source["url"], link[0].get("href") if link else ""),
            facility_type="science_museum", record_status="supplement_confirmed",
        ))
    return rows


def collect_jaza_table(source, doc, retrieved_at, aliases, name_to_code):
    rows = []
    for tr in doc.xpath('//table//tr'):
        cells = tr.xpath('./th|./td')
        values = [text(c.text_content()) for c in cells]
        if len(values) < 4 or "東京都" not in values[2]:
            continue
        postal, address = split_postal_address(values[2])
        code, muni = municipality_from_text(address, name_to_code)
        link = cells[0].xpath('.//a[1]')
        rows.append(candidate(
            source, values[0], retrieved_at, aliases, code, muni, postal, address, phone=values[3],
            official_url=absolute_url(source["url"], link[0].get("href") if link else ""),
            facility_type=source.get("facility_type", "museum"), record_status="supplement_confirmed",
        ))
    return rows


def collect_jaa(source, doc, retrieved_at, aliases, name_to_code):
    rows = []
    for tr in doc.xpath('//table//tr'):
        cells = tr.xpath('./th|./td')
        values = [text(c.text_content()) for c in cells]
        if len(values) < 2 or values[0] != "東京":
            continue
        link = cells[1].xpath('.//a[1]')
        rows.append(candidate(
            source, values[1], retrieved_at, aliases,
            official_url=absolute_url(source["url"], link[0].get("href") if link else ""),
            facility_type=source.get("facility_type", "aquarium"), record_status="needs_review",
            notes="公開名簿に住所がないため所在地詳細を要確認",
        ))
    return rows


def collect_bunkyo(source, doc, retrieved_at, aliases, name_to_code):
    tables = doc.xpath('//table[.//tr[1]//*[contains(normalize-space(.),"名称")]]')
    if not tables:
        raise ValueError("facility table not found")
    rows = []
    for tr in tables[-1].xpath('.//tr[position()>1]'):
        cells = tr.xpath('./th|./td')
        if len(cells) < 3:
            continue
        name, phone, address = (text(c.text_content()) for c in cells[:3])
        name = re.sub(r"[（(](?:外部リンク|休館)[）)]", "", name).strip()
        code, muni = municipality_from_text(address, name_to_code)
        if not code:
            code, muni = "13105", "文京区"
        link = cells[0].xpath('.//a[1]')
        status = "temporarily_closed" if "休館" in text(cells[0].text_content()) else "supplement_confirmed"
        rows.append(candidate(
            source, name, retrieved_at, aliases, code, muni, address=address, phone=phone,
            official_url=absolute_url(source["url"], link[0].get("href") if link else ""),
            facility_type="museum_or_cultural_facility", record_status=status,
        ))
    return rows


def collect_minato(source, doc, retrieved_at, aliases, name_to_code):
    rows = []
    for li in doc.xpath('//li[contains(concat(" ",normalize-space(@class)," ")," museum ")]'):
        name = text(li.xpath('string(.//a[contains(@class,"name")][1])'))
        address_local = text(li.xpath('string(.//*[contains(@class,"address")][1])'))
        if not name:
            continue
        address = f"東京都港区{address_local}" if address_local and "東京都" not in address_local else address_local
        link = li.xpath('.//a[contains(@class,"name")][1]')
        rows.append(candidate(
            source, name, retrieved_at, aliases, "13103", "港区", address=address,
            official_url=absolute_url(source["url"], link[0].get("href") if link else ""),
            facility_type="museum_or_display_facility", record_status="supplement_confirmed",
        ))
    return rows


def collect_chiyoda(source, doc, retrieved_at, aliases, name_to_code):
    rows = []
    for link in doc.xpath('//a[contains(@href,"shisetsu/")]'):
        name = text(link.xpath('string(.//div[contains(@class,"name")]/p)'))
        if not name:
            continue
        is_closed = "休館中" in name
        clean_name = re.sub(r"[（(]休館中[）)]", "", name).strip()
        rows.append(candidate(
            source, clean_name, retrieved_at, aliases, "13101", "千代田区",
            official_url=absolute_url(source["url"], link.get("href")),
            facility_type="museum_or_knowledge_facility",
            record_status="temporarily_closed" if is_closed else "supplement_confirmed",
            notes="一覧ページには住所がないため詳細ページまたは館公式サイトで補完",
        ))
    return rows


COLLECTORS = {
    "bunka_core": collect_bunka_core,
    "jcsm": collect_jcsm,
    "jaza_table": collect_jaza_table,
    "jaa": collect_jaa,
    "bunkyo": collect_bunkyo,
    "minato": collect_minato,
    "chiyoda": collect_chiyoda,
}


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def reconcile(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    core_by_key: dict[str, dict[str, str]] = {}
    for row in rows:
        if row["source_role"] != "core":
            continue
        key = f"{row['facility_name_normalized']}|{row['municipality_code']}"
        core_by_key[key] = row

    seen_supplement: dict[str, str] = {}
    output = []
    for row in rows:
        key = f"{row['facility_name_normalized']}|{row['municipality_code']}"
        if row["source_role"] == "core":
            status, matched, canonical = "core_unique", "", stable_id("MUS", key)
        elif not row["municipality_code"]:
            status, matched, canonical = "needs_review", "", stable_id("MUS", row["record_id"])
        elif key in core_by_key:
            matched = core_by_key[key]["record_id"]
            status, canonical = "duplicate_core", stable_id("MUS", key)
        elif key in seen_supplement:
            matched = seen_supplement[key]
            status, canonical = "duplicate_supplement", stable_id("MUS", key)
        else:
            seen_supplement[key] = row["record_id"]
            status, matched, canonical = "supplement_unique", "", stable_id("MUS", key)
        output.append({
            "record_id": row["record_id"],
            "canonical_facility_id": canonical,
            "source_id": row["source_id"],
            "facility_name_raw": row["facility_name_raw"],
            "facility_name_normalized": row["facility_name_normalized"],
            "municipality_code": row["municipality_code"],
            "municipality_name": row["municipality_name"],
            "match_key": key,
            "match_status": status,
            "matched_record_id": matched,
            "review_required": "true" if status == "needs_review" else "false",
            "notes": row["notes"],
        })
    return output


def build_summary(candidates: list[dict[str, str]], reconciled: list[dict[str, str]]) -> dict[str, Any]:
    count = lambda field, value: sum(r[field] == value for r in reconciled)
    registered = sum(r["record_status"] == "core_registered" for r in candidates)
    designated = sum(r["record_status"] == "core_designated" for r in candidates)
    supplement_unique = count("match_status", "supplement_unique")
    combined = registered + designated + supplement_unique
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "reference_social_education_survey_r6": REFERENCE_TOTAL,
        "reference_is_target": False,
        "core_registered_records": registered,
        "core_designated_records": designated,
        "core_total": registered + designated,
        "supplement_source_records": sum(r["source_role"] != "core" for r in candidates),
        "supplement_unique_candidates": supplement_unique,
        "duplicate_core_source_records": count("match_status", "duplicate_core"),
        "duplicate_supplement_source_records": count("match_status", "duplicate_supplement"),
        "needs_review_source_records": count("match_status", "needs_review"),
        "combined_unique_estimate": combined,
        "reference_difference": combined - REFERENCE_TOTAL,
        "reference_ratio": round(combined / REFERENCE_TOTAL, 4),
        "interpretation": "The 210 facilities are a scale reference, not a required total or record-level denominator.",
    }


def write_markdown(path: Path, source_rows, summary, errors):
    lines = [
        "# Tokyo Museum Data Manifest", "",
        f"生成日時（UTC）: {summary['generated_at']}", "",
        "## 1. 方針", "",
        "文化庁の登録博物館・指定施設を中核とし、その他の名簿・自治体・観光系情報を追加ソースとして保持する。令和6年度社会教育調査の210施設は規模の参照値であり、完全一致を要件としない。", "",
        "## 2. 現時点の集計", "",
        "| 項目 | 件数 |", "|---|---:|",
        f"| 登録博物館（中核） | {summary['core_registered_records']} |",
        f"| 指定施設（中核） | {summary['core_designated_records']} |",
        f"| 中核計 | {summary['core_total']} |",
        f"| 追加ソースの取得レコード | {summary['supplement_source_records']} |",
        f"| 中核との重複レコード | {summary['duplicate_core_source_records']} |",
        f"| 追加ソース間の重複レコード | {summary['duplicate_supplement_source_records']} |",
        f"| 追加のユニーク候補 | {summary['supplement_unique_candidates']} |",
        f"| 要確認レコード | {summary['needs_review_source_records']} |",
        f"| 中核＋追加候補の暫定ユニーク推計 | {summary['combined_unique_estimate']} |",
        f"| R6社会教育調査の参照値 | {summary['reference_social_education_survey_r6']} |", "",
        "暫定ユニーク推計は、正規化名称と5桁自治体コードが完全一致する場合だけを自動重複として整理した値である。住所未確認、別館、複合施設、改称等の目視確認により変動する。", "",
        "## 3. 情報源", "",
        "| Tier | 役割 | 情報源 | 取得状態 | 取得件数 | URL |", "|---|---|---|---|---:|---|",
    ]
    for row in source_rows:
        lines.append(f"| {row['source_tier']} | {row['source_role']} | {row['source_name']} | {row['retrieval_status']} | {row['record_count']} | {row['url']} |")
    lines += [
        "", "## 4. 自動照合規則", "",
        "1. Unicode NFKC、空白・限定的な約物除去、明示的な名称別名表で名称を正規化する。",
        "2. `正規化名称 + 5桁自治体コード` が中核と完全一致した場合だけ `duplicate_core` とする。",
        "3. 同じキーが追加ソース間で重複した場合は、最初のレコードを候補として保持し、以後を `duplicate_supplement` とする。",
        "4. 自治体コードを確定できないレコードは自動統合せず `needs_review` とする。",
        "5. 曖昧一致、近傍住所、電話番号だけによる自動統合は行わない。", "",
        "## 5. 限界", "",
        "- 文化遺産オンラインと日本博物館協会は有力な追加ソースだが、現段階では安定した施設一覧一括取得方法を確定していないため、情報源台帳への収録に留めた。",
        "- 地域ミュージアムネットワークには庭園、図書館、文書館、ギャラリー等が含まれる場合があり、最終的な対象判定が必要である。",
        "- 登録・指定一覧には完全な住所がないため、PLATEAU建物ポリゴンとの照合前に公式サイト等から所在地を補完する必要がある。",
        "- 210は集計上の参照値であり、個別施設の欠落を示す名簿ではない。", "",
    ]
    if errors:
        lines += ["## 6. 取得エラー", ""] + [f"- `{k}`: {v}" for k, v in sorted(errors.items())] + [""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="Use cached HTML only")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached HTML")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR, help="Output directory")
    args = parser.parse_args(argv)

    sources = json.loads((CONFIG_DIR / "sources.json").read_text(encoding="utf-8"))
    _, name_to_code = load_municipalities()
    aliases = load_aliases()
    retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    candidates: list[dict[str, str]] = []
    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}

    automatic = [s for s in sources if s["collector"] != "manifest_only"]

    def run_source(source):
        data, mode = fetch(source, args.offline, args.refresh)
        doc = html.fromstring(data)
        rows = COLLECTORS[source["collector"]](source, doc, retrieved_at, aliases, name_to_code)
        return rows, mode, hashlib.sha256(data).hexdigest()

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        future_to_source = {pool.submit(run_source, source): source for source in automatic}
        for future in as_completed(future_to_source):
            source = future_to_source[future]
            try:
                rows, mode, digest = future.result()
                candidates.extend(rows)
                results[source["source_id"]] = {"status": f"retrieved_{mode}", "count": len(rows), "sha256": digest}
            except Exception as exc:
                errors[source["source_id"]] = f"{type(exc).__name__}: {exc}"
                results[source["source_id"]] = {"status": "error", "count": 0, "sha256": ""}

    candidates.sort(key=lambda r: (0 if r["source_role"] == "core" else 1, r["source_id"], r["municipality_code"], r["facility_name_normalized"]))
    if not any(r["source_role"] == "core" for r in candidates):
        print("ERROR: core source was not collected; outputs were not replaced", file=sys.stderr)
        for key, value in errors.items():
            print(f"  {key}: {value}", file=sys.stderr)
        return 2

    source_rows = []
    for source in sources:
        result = results.get(source["source_id"], {"status": "manifest_only", "count": 0, "sha256": ""})
        source_rows.append({
            **source,
            "retrieved_at": retrieved_at if result["status"].startswith("retrieved") else "",
            "retrieval_status": result["status"],
            "record_count": result["count"],
            "snapshot_sha256": result["sha256"],
        })

    reconciled = reconcile(candidates)
    summary = build_summary(candidates, reconciled)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "museum_candidates.csv", candidates, CANDIDATE_FIELDS)
    reconciliation_fields = list(reconciled[0].keys()) if reconciled else []
    write_csv(output_dir / "museum_reconciliation.csv", reconciled, reconciliation_fields)
    source_fields = [
        "source_id", "source_name", "source_role", "source_tier", "collector", "url", "scope",
        "reference_count", "retrieved_at", "retrieval_status", "record_count", "snapshot_sha256", "notes",
    ]
    write_csv(output_dir / "museum_sources_manifest.csv", source_rows, source_fields)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(output_dir / "MUSEUM_DATA_MANIFEST.md", source_rows, summary, errors)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if errors:
        print("WARN: some supplement sources failed; see MUSEUM_DATA_MANIFEST.md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
