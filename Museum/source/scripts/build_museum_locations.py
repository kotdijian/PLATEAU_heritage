#!/usr/bin/env python3
"""Build an auditable location register for the 245 canonical Tokyo museums.

Priority is given to structured or curated source lists.  Facility-site HTML is
used only for canonical facilities that remain unresolved.  The generated
``museum_location_enrichment.csv`` is compatible with
``Museum/build_museum_hazard_gpkg.py``.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urljoin

import requests
from lxml import html

try:
    from .build_museum_manifest import load_aliases, load_municipalities, normalize_name
    from .enrich_museum_locations import (
        OUTPUT_FIELDS,
        canonical_facilities,
        clean,
        extract_official_location,
        geocode_abr,
        normalize_address,
        write_rows,
    )
except ImportError:  # Direct execution: python Museum/source/scripts/...
    from build_museum_manifest import load_aliases, load_municipalities, normalize_name
    from enrich_museum_locations import (
        OUTPUT_FIELDS,
        canonical_facilities,
        clean,
        extract_official_location,
        geocode_abr,
        normalize_address,
        write_rows,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = SCRIPT_DIR.parent
DEFAULT_DATA_DIR = SOURCE_ROOT / "data"
DEFAULT_CACHE_DIR = SOURCE_ROOT / "cache" / "location_sources"
DEFAULT_OVERRIDES = SOURCE_ROOT / "config" / "location_source_overrides.csv"
DEFAULT_CULTURAL_ONLINE_URL = (
    "https://online.bunka.go.jp/museums/search?prefecture_cd=13"
)
TOOL_VERSION = "0.2.5"

LOCATION_CANDIDATE_FIELDS = [
    "candidate_id", "museum_id", "canonical_name", "municipality_code",
    "municipality_name", "candidate_name", "candidate_name_normalized",
    "address_raw", "address_normalized", "postal_code", "latitude",
    "longitude", "source_id", "source_url", "source_authority",
    "extraction_method", "match_method", "priority", "retrieved_at",
    "content_sha256", "candidate_status", "notes",
]

LEGAL_NAME_PREFIXES = (
    "公益財団法人", "一般財団法人", "公益社団法人", "一般社団法人",
    "財団法人", "社団法人", "学校法人", "国立大学法人", "独立行政法人",
)


def name_match_key(value: str, aliases: dict[str, str] | None = None) -> str:
    """Exact matching key after removing only institutional legal prefixes."""
    value = clean(value)
    value = re.sub(r"^[◎○〇●]\s*", "", value)
    changed = True
    while changed:
        changed = False
        for prefix in LEGAL_NAME_PREFIXES:
            if value.startswith(prefix):
                value = value[len(prefix):].lstrip()
                changed = True
    return normalize_name(value, aliases)


def split_postal_address(value: str) -> tuple[str, str]:
    value = clean(value)
    match = re.search(r"〒?\s*(\d{3}[-－]?\d{4})", value)
    postal = match.group(1).replace("－", "-") if match else ""
    if match:
        value = clean(value[:match.start()] + " " + value[match.end():])
    # Some Cultural Heritage Online records append the same address in English
    # after a slash. Keep the Japanese facility address only. A slash followed
    # by Japanese text (for example building floor notation) is preserved.
    value = re.sub(r"\s*/\s*[0-9][\x00-\x7f]+$", "", value).strip()
    return postal, value


def plausible_facility_address(value: str, municipality_name: str) -> bool:
    """Reject status notes and other non-address text in legacy manifests."""
    value = clean(value)
    has_street_number = bool(
        re.search(r"\d", value)
        or re.search(r"[一二三四五六七八九十百]+(?:丁目|番地?|号)", value)
    )
    malformed_hierarchy = bool(
        re.search(r"[-－−‐‑‒–—―ー]丁目", value)
    )
    trailing_page_text = bool(re.search(
        r"(?:T\s*E\s*L|F\s*A\s*X|徒歩でのご来館|"
        r"バリアフリー(?:のご案内)?|[［\[]アクセス|"
        r"\s設計\s|\s施工\s)",
        value,
        flags=re.IGNORECASE,
    ))
    return bool(
        value
        and has_street_number
        and municipality_name
        and municipality_name in value
        and not malformed_hierarchy
        and not trailing_page_text
    )


def complete_tokyo_address(address: str, municipality_name: str) -> str:
    address = clean(address)
    if municipality_name and address.startswith(municipality_name):
        return "東京都" + address
    return address


def stable_candidate_id(*values: str) -> str:
    body = "|".join(clean(value) for value in values).encode("utf-8")
    return "LOC-" + hashlib.sha1(body).hexdigest()[:14]


def candidate_row(
    *, museum_id: str = "", canonical_name: str = "",
    municipality_code: str = "", municipality_name: str = "",
    candidate_name: str = "", address: str = "", postal_code: str = "",
    latitude: str = "", longitude: str = "", source_id: str,
    source_url: str, source_authority: str, extraction_method: str,
    match_method: str = "", priority: int = 0, retrieved_at: str,
    content_sha256: str = "", candidate_status: str = "candidate",
    notes: str = "", aliases: dict[str, str] | None = None,
) -> dict[str, str]:
    address = complete_tokyo_address(address, municipality_name)
    normalized = normalize_address(address)
    row = {field: "" for field in LOCATION_CANDIDATE_FIELDS}
    row.update({
        "candidate_id": stable_candidate_id(
            source_id, candidate_name, municipality_code, normalized
        ),
        "museum_id": museum_id,
        "canonical_name": canonical_name,
        "municipality_code": municipality_code,
        "municipality_name": municipality_name,
        "candidate_name": clean(candidate_name),
        "candidate_name_normalized": name_match_key(candidate_name, aliases),
        "address_raw": clean(address),
        "address_normalized": normalized,
        "postal_code": clean(postal_code),
        "latitude": clean(latitude),
        "longitude": clean(longitude),
        "source_id": source_id,
        "source_url": clean(source_url),
        "source_authority": source_authority,
        "extraction_method": extraction_method,
        "match_method": match_method,
        "priority": str(priority),
        "retrieved_at": retrieved_at,
        "content_sha256": content_sha256,
        "candidate_status": candidate_status,
        "notes": clean(notes),
    })
    return row


def cache_path(cache_dir: Path, source_id: str, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / source_id / f"{digest}.html"


def fetch_url(
    session: requests.Session, url: str, cache_dir: Path, source_id: str,
    timeout: int, refresh: bool, offline: bool,
) -> tuple[bytes, str, str, str]:
    target = cache_path(cache_dir, source_id, url)
    if target.is_file() and not refresh:
        payload = target.read_bytes()
        return payload, url, hashlib.sha256(payload).hexdigest(), "cache"
    if offline:
        raise FileNotFoundError(f"Cached source not found: {target}")
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.content
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return payload, response.url, hashlib.sha256(payload).hexdigest(), "network"


def nearest_value_after_label(document, label: str) -> str:
    nodes = document.xpath(
        "//*[self::dt or self::th or self::div or self::span or self::p]"
        f"[normalize-space(.)='{label}']"
    )
    for node in nodes:
        sibling = node.getnext()
        if sibling is not None:
            value = clean(" ".join(sibling.itertext()))
            if value:
                return value
        parent = node.getparent()
        if parent is not None:
            text_value = clean(" ".join(parent.itertext()))
            if text_value.startswith(label):
                value = clean(text_value[len(label):])
                if value:
                    return value
    return ""


def parse_utf8_html(payload: bytes, base_url: str = ""):
    """Parse the UTF-8 source without lxml's Latin-1 fallback for bare bytes."""
    return html.fromstring(payload.decode("utf-8", errors="replace"), base_url=base_url)


def parse_cultural_online_detail(
    payload: bytes, url: str, aliases: dict[str, str],
    name_to_code: dict[str, str], retrieved_at: str, digest: str,
) -> dict[str, str] | None:
    document = parse_utf8_html(payload, base_url=url)
    names = [clean(value) for value in document.xpath("//h1//text()") if clean(value)]
    name = names[-1] if names else ""
    address = nearest_value_after_label(document, "所在地")
    if not name or not address:
        return None
    postal, address = split_postal_address(address)
    municipality_code = municipality_name = ""
    for candidate_name in sorted(name_to_code, key=len, reverse=True):
        if candidate_name in address:
            municipality_name = candidate_name
            municipality_code = name_to_code[candidate_name]
            break
    if not municipality_code:
        return None
    return candidate_row(
        candidate_name=name,
        municipality_code=municipality_code,
        municipality_name=municipality_name,
        address=address,
        postal_code=postal,
        source_id="cultural_heritage_online",
        source_url=url,
        source_authority="agency_cultural_portal",
        extraction_method="structured_detail_page",
        priority=80,
        retrieved_at=retrieved_at,
        content_sha256=digest,
        aliases=aliases,
    )


def cultural_online_page_url(base_url: str, page: int) -> str:
    if page == 1:
        return base_url
    root = base_url.split("/museums/search", 1)[0]
    # The first search request stores the filter in the server-side session.
    # Pagination links intentionally omit the query string. Re-appending the
    # filter makes this site return page 1 again under a page:2-looking URL.
    return f"{root}/museums/search/page:{page}"


def collect_cultural_heritage_online(
    session: requests.Session, base_url: str, cache_dir: Path, timeout: int,
    refresh: bool, offline: bool, max_pages: int, delay: float,
    aliases: dict[str, str], name_to_code: dict[str, str], retrieved_at: str,
) -> tuple[list[dict[str, str]], dict[str, object]]:
    detail_urls: list[str] = []
    page_status: list[dict[str, str]] = []
    errors: list[str] = []
    reported_count: int | None = None
    previous_count = -1
    for page in range(1, max_pages + 1):
        url = cultural_online_page_url(base_url, page)
        payload, final_url, digest, mode = fetch_url(
            session, url, cache_dir, "cultural_heritage_online", timeout,
            refresh, offline,
        )
        document = parse_utf8_html(payload, base_url=final_url)
        total_texts = document.xpath(
            "//*[contains(concat(' ', normalize-space(@class), ' '), "
            "' g-controlBar_total ')]/text()"
        )
        count_values = []
        for value in total_texts:
            count_match = re.fullmatch(r"\s*([0-9][0-9,]*)件\s*", value)
            if count_match:
                count_values.append(int(count_match.group(1).replace(",", "")))
        page_reported_count = count_values[0] if count_values else None
        if reported_count is None:
            reported_count = page_reported_count
        elif (
            page_reported_count is not None
            and reported_count is not None
            and page_reported_count != reported_count
        ):
            errors.append(
                "prefecture_filter_lost_on_pagination: "
                f"page={page}, expected_count={reported_count}, "
                f"observed_count={page_reported_count}, url={final_url}"
            )
            break
        urls = [
            urljoin(final_url, anchor.get("href"))
            for anchor in document.xpath('//a[contains(@href,"/museums/detail/")]')
            if anchor.get("href")
        ]
        for detail_url in urls:
            if detail_url not in detail_urls:
                detail_urls.append(detail_url)
        page_status.append({
            "page": str(page), "url": final_url, "mode": mode,
            "sha256": digest, "detail_links": str(len(urls)),
        })
        if reported_count is not None and len(detail_urls) >= reported_count:
            break
        if not urls or len(detail_urls) == previous_count:
            break
        previous_count = len(detail_urls)
        if delay:
            time.sleep(delay)

    rows: list[dict[str, str]] = []
    for index, url in enumerate(detail_urls, 1):
        try:
            payload, final_url, digest, _ = fetch_url(
                session, url, cache_dir, "cultural_heritage_online", timeout,
                refresh, offline,
            )
            row = parse_cultural_online_detail(
                payload, final_url, aliases, name_to_code, retrieved_at, digest
            )
            if row:
                rows.append(row)
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
        if index % 10 == 0 or index == len(detail_urls):
            print(
                f"Cultural Heritage Online details: {index}/{len(detail_urls)}",
                flush=True,
            )
        if delay:
            time.sleep(delay)
    if reported_count is not None and len(rows) < reported_count:
        errors.append(
            "structured_source_incomplete: "
            f"reported={reported_count}, parsed={len(rows)}"
        )
    return rows, {
        "source_id": "cultural_heritage_online",
        "list_pages": page_status,
        "reported_record_count": reported_count,
        "detail_url_count": len(detail_urls),
        "candidate_count": len(rows),
        "errors": errors,
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_verified_overrides(path: Path) -> list[dict[str, str]]:
    return read_csv(path)


def load_location_source_csv(
    path: Path, aliases: dict[str, str], name_to_code: dict[str, str],
    retrieved_at: str,
) -> list[dict[str, str]]:
    """Load an additional authoritative list through the canonical schema.

    ``facility_name`` and ``address`` are required. Municipality can be
    supplied as a five-digit code or inferred conservatively from the address.
    This adapter deliberately performs no fuzzy name or address matching.
    """
    rows: list[dict[str, str]] = []
    source_id_default = re.sub(r"[^0-9A-Za-z_-]+", "_", path.stem).strip("_")
    for source_row in read_csv(path):
        name = clean(
            source_row.get("facility_name")
            or source_row.get("name")
            or source_row.get("title")
        )
        address = clean(
            source_row.get("address")
            or source_row.get("location")
            or source_row.get("所在地")
        )
        if not name or not address:
            continue
        municipality_code = clean(source_row.get("municipality_code"))
        municipality_name = clean(source_row.get("municipality_name"))
        if not municipality_code:
            for candidate_name in sorted(name_to_code, key=len, reverse=True):
                if candidate_name in address:
                    municipality_name = candidate_name
                    municipality_code = name_to_code[candidate_name]
                    break
        if not municipality_code:
            continue
        source_id = clean(source_row.get("source_id")) or source_id_default
        try:
            priority = int(clean(source_row.get("priority")) or "90")
        except ValueError:
            priority = 90
        rows.append(candidate_row(
            candidate_name=name,
            municipality_code=municipality_code,
            municipality_name=municipality_name,
            address=address,
            postal_code=source_row.get("postal_code", ""),
            latitude=source_row.get("latitude", ""),
            longitude=source_row.get("longitude", ""),
            source_id=source_id,
            source_url=source_row.get("source_url", ""),
            source_authority=(
                clean(source_row.get("source_authority"))
                or "authoritative_list"
            ),
            extraction_method="authoritative_csv_import",
            priority=priority,
            retrieved_at=clean(source_row.get("retrieved_at")) or retrieved_at,
            content_sha256=source_row.get("content_sha256", ""),
            notes=source_row.get("notes", ""),
            aliases=aliases,
        ))
    return rows


def find_override(
    overrides: list[dict[str, str]], facility: dict[str, str]
) -> dict[str, str]:
    for row in overrides:
        if clean(row.get("museum_id")) == facility["museum_id"]:
            return row
        if clean(row.get("facility_name")) == facility["facility_name"]:
            return row
    return {}


def candidate_matches_facility(
    row: dict[str, str], facility: dict[str, str], aliases: dict[str, str]
) -> bool:
    return (
        row["municipality_code"] == facility["municipality_code"]
        and name_match_key(row["candidate_name"], aliases)
        == name_match_key(facility["facility_name"], aliases)
    )


def enrichment_row(
    facility: dict[str, str], candidate: dict[str, str],
    *, review_status: str, review_reason: str = "",
) -> dict[str, str]:
    row = {field: "" for field in OUTPUT_FIELDS}
    row.update({
        "museum_id": facility["museum_id"],
        "facility_name": facility["facility_name"],
        "municipality_code": facility["municipality_code"],
        "municipality_name": facility["municipality_name"],
        "address_raw": candidate.get("address_raw", ""),
        "address_normalized": candidate.get("address_normalized", ""),
        "postal_code": candidate.get("postal_code", ""),
        "latitude": candidate.get("latitude", ""),
        "longitude": candidate.get("longitude", ""),
        "location_type": "facility_location" if candidate.get("address_raw") else "",
        "address_source_url": candidate.get("source_url", ""),
        "source_authority": candidate.get("source_authority", ""),
        "extraction_method": candidate.get("extraction_method", ""),
        "match_level": candidate.get("match_method", ""),
        "coordinate_use": (
            "building_candidate" if candidate.get("latitude") else ""
        ),
        "review_status": review_status,
        "review_reason": review_reason,
        "retrieved_at": candidate.get("retrieved_at", ""),
        "content_sha256": candidate.get("content_sha256", ""),
        "notes": candidate.get("notes", ""),
    })
    return row


def choose_candidate(
    facility: dict[str, str], candidates: list[dict[str, str]],
) -> tuple[dict[str, str] | None, str]:
    usable = [row for row in candidates if row.get("address_normalized")]
    if not usable:
        return None, "no_location_candidate"
    maximum = max(int(row.get("priority") or 0) for row in usable)
    top = [row for row in usable if int(row.get("priority") or 0) == maximum]
    addresses = {row["address_normalized"] for row in top}
    if len(addresses) != 1:
        return None, "conflicting_equal_priority_addresses"
    selected = sorted(top, key=lambda row: (row["source_id"], row["source_url"]))[0]
    return selected, ""


def override_candidate(
    facility: dict[str, str], override: dict[str, str], aliases: dict[str, str],
    retrieved_at: str,
) -> dict[str, str] | None:
    address = clean(override.get("address"))
    if not address:
        return None
    return candidate_row(
        museum_id=facility["museum_id"],
        canonical_name=facility["facility_name"],
        municipality_code=facility["municipality_code"],
        municipality_name=facility["municipality_name"],
        candidate_name=facility["facility_name"],
        address=address,
        postal_code=override.get("postal_code", ""),
        latitude=override.get("latitude", ""),
        longitude=override.get("longitude", ""),
        source_id="verified_override",
        source_url=override.get("location_url", ""),
        source_authority=override.get("source_authority", "verified_official"),
        extraction_method="verified_override",
        match_method="manual_exact_facility",
        priority=120,
        retrieved_at=retrieved_at,
        candidate_status="accepted",
        notes=override.get("notes", ""),
        aliases=aliases,
    )


def existing_candidate(
    facility: dict[str, str], aliases: dict[str, str], retrieved_at: str,
) -> dict[str, str] | None:
    if not plausible_facility_address(
        facility.get("existing_address", ""), facility["municipality_name"]
    ):
        return None
    return candidate_row(
        museum_id=facility["museum_id"],
        canonical_name=facility["facility_name"],
        municipality_code=facility["municipality_code"],
        municipality_name=facility["municipality_name"],
        candidate_name=facility["facility_name"],
        address=facility["existing_address"],
        source_id="existing_manifest",
        source_url=facility.get("existing_source_url", ""),
        source_authority="existing_manifest_source",
        extraction_method="existing_manifest",
        match_method="canonical_source_record",
        priority=110,
        retrieved_at=retrieved_at,
        candidate_status="accepted",
        aliases=aliases,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument(
        "--location-source-csv", type=Path, action="append", default=[],
        help=(
            "Additional authoritative location list in the canonical CSV "
            "schema; repeat this option for multiple sources"
        ),
    )
    parser.add_argument("--cultural-online-url", default=DEFAULT_CULTURAL_ONLINE_URL)
    parser.add_argument("--max-list-pages", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--skip-cultural-online", action="store_true")
    parser.add_argument("--skip-official-fallback", action="store_true")
    parser.add_argument(
        "--cache-official-pages", action="store_true",
        help=(
            "Cache facility-site HTML only after confirming the source terms "
            "allow local preservation"
        ),
    )
    parser.add_argument("--max-facilities", type=int)
    parser.add_argument(
        "--abr-api-base", default=os.environ.get("ABR_GEOCODER_URL", ""),
        help="ABR geocoder base URL (or ABR_GEOCODER_URL environment variable)",
    )
    parser.add_argument(
        "--output", type=Path,
        default=DEFAULT_DATA_DIR / "museum_location_enrichment.csv",
    )
    parser.add_argument(
        "--review-output", type=Path,
        default=DEFAULT_DATA_DIR / "museum_location_review.csv",
    )
    parser.add_argument(
        "--candidates-output", type=Path,
        default=DEFAULT_DATA_DIR / "museum_location_candidates.csv",
    )
    parser.add_argument(
        "--summary-output", type=Path,
        default=DEFAULT_DATA_DIR / "museum_location_summary.json",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    cache_dir = args.cache_dir.expanduser().resolve()
    facilities = canonical_facilities(data_dir)
    if args.max_facilities is not None:
        facilities = facilities[:args.max_facilities]
    aliases = load_aliases()
    _, name_to_code = load_municipalities()
    overrides = load_verified_overrides(args.overrides.expanduser().resolve())
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "PLATEAU-Heritage-Museum-location-builder/0.2 (+research)"
    })
    source_manifest: list[dict[str, object]] = []
    structured: list[dict[str, str]] = []
    if not args.skip_cultural_online:
        try:
            structured, source_status = collect_cultural_heritage_online(
                session, args.cultural_online_url, cache_dir, args.timeout,
                args.refresh, args.offline, args.max_list_pages, args.delay,
                aliases, name_to_code, now,
            )
            source_manifest.append(source_status)
        except Exception as exc:
            source_manifest.append({
                "source_id": "cultural_heritage_online",
                "candidate_count": 0,
                "errors": [f"{type(exc).__name__}: {exc}"],
            })

    for csv_path_arg in args.location_source_csv:
        csv_path = csv_path_arg.expanduser().resolve()
        imported = load_location_source_csv(
            csv_path, aliases, name_to_code, now
        )
        structured.extend(imported)
        source_manifest.append({
            "source_id": f"csv:{csv_path.name}",
            "source_path": str(csv_path),
            "candidate_count": len(imported),
            "errors": [] if csv_path.is_file() else ["file_not_found"],
        })

    by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in structured:
        by_key[(row["candidate_name_normalized"], row["municipality_code"])].append(row)

    all_candidates: list[dict[str, str]] = []
    accepted: list[dict[str, str]] = []
    review: list[dict[str, str]] = []
    resolution_counts: Counter[str] = Counter()
    matched_structured_ids: set[str] = set()

    for index, facility in enumerate(facilities, 1):
        print(f"Location [{index}/{len(facilities)}] {facility['facility_name']}", flush=True)
        facility_candidates: list[dict[str, str]] = []
        official_review_result: dict[str, str] = {}
        official_start_url = ""
        existing = existing_candidate(facility, aliases, now)
        if existing:
            facility_candidates.append(existing)
        elif facility.get("existing_address"):
            invalid_existing = candidate_row(
                museum_id=facility["museum_id"],
                canonical_name=facility["facility_name"],
                municipality_code=facility["municipality_code"],
                municipality_name=facility["municipality_name"],
                candidate_name=facility["facility_name"],
                address=facility["existing_address"],
                source_id="existing_manifest",
                source_url=facility.get("existing_source_url", ""),
                source_authority="existing_manifest_source",
                extraction_method="existing_manifest",
                match_method="canonical_source_record",
                priority=110,
                retrieved_at=now,
                candidate_status="rejected_invalid_address",
                notes="Rejected: value lacks municipality name or street number",
                aliases=aliases,
            )
            invalid_existing["address_normalized"] = ""
            facility_candidates.append(invalid_existing)
        override = find_override(overrides, facility)
        verified = override_candidate(facility, override, aliases, now)
        if verified:
            facility_candidates.append(verified)

        key = (
            name_match_key(facility["facility_name"], aliases),
            facility["municipality_code"],
        )
        for row in by_key.get(key, []):
            matched = dict(row)
            matched.update({
                "museum_id": facility["museum_id"],
                "canonical_name": facility["facility_name"],
                "match_method": "exact_name_municipality",
                "candidate_status": "matched_candidate",
            })
            facility_candidates.append(matched)
            matched_structured_ids.add(row["candidate_id"])

        selected, reason = choose_candidate(facility, facility_candidates)
        if selected is None and any(
            row.get("candidate_status") == "rejected_invalid_address"
            for row in facility_candidates
        ):
            reason = "invalid_existing_address"
        selected_priority = int(selected.get("priority") or 0) if selected else 0
        if not args.skip_official_fallback and selected_priority < 100:
            start_url = clean(override.get("location_url")) or facility.get("official_url", "")
            official_start_url = start_url
            if start_url:
                result = extract_official_location(
                    session, facility, start_url, cache_dir / "official_pages",
                    args.timeout, args.refresh, args.cache_official_pages,
                    args.delay,
                )
                if result.get("review_status") == "accepted":
                    official = candidate_row(
                        museum_id=facility["museum_id"],
                        canonical_name=facility["facility_name"],
                        municipality_code=facility["municipality_code"],
                        municipality_name=facility["municipality_name"],
                        candidate_name=facility["facility_name"],
                        address=result.get("address_raw", ""),
                        postal_code=result.get("postal_code", ""),
                        latitude=result.get("latitude", ""),
                        longitude=result.get("longitude", ""),
                        source_id="facility_official_page",
                        source_url=result.get("address_source_url", start_url),
                        source_authority=result.get(
                            "source_authority", "facility_or_public_official"
                        ),
                        extraction_method=result.get("extraction_method", "html_text"),
                        match_method="canonical_official_url",
                        priority=100,
                        retrieved_at=now,
                        content_sha256=result.get("content_sha256", ""),
                        candidate_status="accepted",
                        notes=result.get("notes", ""),
                        aliases=aliases,
                    )
                    if plausible_facility_address(
                        official["address_raw"], facility["municipality_name"]
                    ):
                        facility_candidates.append(official)
                        selected, reason = choose_candidate(
                            facility, facility_candidates
                        )
                    else:
                        official["candidate_status"] = "rejected_invalid_address"
                        official["address_normalized"] = ""
                        official["notes"] = clean(
                            f"{official['notes']}; Rejected: malformed or contaminated "
                            "official-page address"
                        )
                        facility_candidates.append(official)
                        official_review_result = {
                            **result,
                            "notes": official["notes"],
                        }
                        if selected is None:
                            reason = "invalid_official_address"
                elif selected is None:
                    reason = result.get("review_reason") or reason
                    official_review_result = result

        all_candidates.extend(facility_candidates)
        if selected is not None:
            selected["candidate_status"] = "accepted"
            row = enrichment_row(facility, selected, review_status="accepted")
            if row["address_normalized"] and args.abr_api_base:
                try:
                    row.update(geocode_abr(
                        session, args.abr_api_base, row["address_normalized"],
                        args.timeout, facility["municipality_code"],
                    ))
                except Exception as exc:
                    row["notes"] = clean(
                        f"{row['notes']}; ABR error: {type(exc).__name__}: {exc}"
                    )
            accepted.append(row)
            resolution_counts[selected["source_id"]] += 1
        else:
            top = sorted(
                facility_candidates,
                key=lambda row: -int(row.get("priority") or 0),
            )
            evidence = top[0] if top else candidate_row(
                museum_id=facility["museum_id"],
                canonical_name=facility["facility_name"],
                municipality_code=facility["municipality_code"],
                municipality_name=facility["municipality_name"],
                candidate_name=facility["facility_name"],
                source_id="unresolved", source_url="", source_authority="",
                extraction_method="", retrieved_at=now,
            )
            review_row = enrichment_row(
                facility, evidence, review_status="needs_review",
                review_reason=reason or "no_location_candidate",
            )
            if official_review_result:
                review_row["address_source_url"] = clean(
                    official_review_result.get("address_source_url")
                    or official_start_url
                )
                review_row["source_authority"] = clean(
                    official_review_result.get("source_authority")
                    or "facility_or_public_official"
                )
                review_row["extraction_method"] = clean(
                    official_review_result.get("extraction_method")
                    or "official_page_review"
                )
                review_row["content_sha256"] = clean(
                    official_review_result.get("content_sha256")
                )
                review_row["notes"] = clean(
                    official_review_result.get("notes") or review_row["notes"]
                )
            review.append(review_row)

    for row in structured:
        if row["candidate_id"] not in matched_structured_ids:
            unmatched = dict(row)
            unmatched["candidate_status"] = "unmatched_source_record"
            unmatched["notes"] = clean(
                f"{unmatched.get('notes', '')}; no exact canonical "
                "name + municipality match"
            )
            all_candidates.append(unmatched)

    accepted.sort(key=lambda row: row["museum_id"])
    review.sort(key=lambda row: row["museum_id"])
    all_candidates.sort(key=lambda row: (
        row.get("museum_id", ""), -int(row.get("priority") or 0),
        row.get("source_id", ""),
    ))
    write_rows(args.output.expanduser().resolve(), accepted)
    write_rows(args.review_output.expanduser().resolve(), review)
    write_csv(
        args.candidates_output.expanduser().resolve(), all_candidates,
        LOCATION_CANDIDATE_FIELDS,
    )

    total = len(facilities)
    address_count = sum(bool(row["address_normalized"]) for row in accepted)
    coordinate_count = sum(bool(row["latitude"] and row["longitude"]) for row in accepted)
    summary = {
        "tool_version": TOOL_VERSION,
        "generated_at": now,
        "canonical_facilities": total,
        "verified_address_count": address_count,
        "verified_address_rate": address_count / total if total else 0,
        "verified_coordinate_count": coordinate_count,
        "verified_coordinate_rate": coordinate_count / total if total else 0,
        "location_needs_review_count": len(review),
        "target_over_60_count": 148 if total == 245 else int(total * 0.60) + 1,
        "target_over_90_count": 221 if total == 245 else int(total * 0.90) + 1,
        "address_over_60_percent": address_count / total > 0.60 if total else False,
        "address_over_90_percent": address_count / total > 0.90 if total else False,
        "accepted_source_counts": dict(sorted(resolution_counts.items())),
        "structured_candidate_count": len(structured),
        "source_manifest": source_manifest,
    }
    summary_path = args.summary_output.expanduser().resolve()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"Wrote: {args.output.expanduser().resolve()}", flush=True)
    print(f"Wrote: {args.review_output.expanduser().resolve()}", flush=True)
    print(f"Wrote: {args.candidates_output.expanduser().resolve()}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
