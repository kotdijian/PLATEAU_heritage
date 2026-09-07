#!/usr/bin/env python3
"""Add auditable official addresses and optional ABR coordinates to Museum data.

The canonical source CSVs remain immutable.  This script writes an overlay that
``Museum/build_museum_hazard_gpkg.py`` can consume when present.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from lxml import html

SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = SCRIPT_DIR.parent
DEFAULT_DATA_DIR = SOURCE_ROOT / "data"
DEFAULT_CACHE_DIR = SOURCE_ROOT / "cache" / "location_pages"
DEFAULT_OVERRIDES = SOURCE_ROOT / "config" / "location_source_overrides.csv"
TOOL_VERSION = "0.1.2"

OUTPUT_FIELDS = [
    "museum_id", "facility_name", "municipality_code", "municipality_name",
    "address_raw", "address_normalized", "postal_code", "latitude", "longitude",
    "location_type", "address_source_url", "source_authority", "extraction_method",
    "geocoder", "geocode_score", "match_level", "coordinate_level",
    "coordinate_use", "review_status", "review_reason", "retrieved_at",
    "content_sha256", "notes",
]

ADDRESS_PATTERN = re.compile(
    r"(?:〒\s*(?P<postal>\d{3}[-－]?\d{4})\s*)?"
    r"(?:日本(?:国)?\s*)?"
    r"(?P<address>東京都\s*[^\s|｜]{1,20}?(?:区|市|町|村)"
    r"[^\n\r|｜<>]{1,90}?\d[^\n\r|｜<>]{0,45})"
)
ACCESS_HINTS = ("アクセス", "所在地", "交通", "利用案内", "施設案内", "access", "guide", "about")
MAILING_HINTS = ("郵便物", "送付先", "事務室", "事務局", "本部", "問い合わせ")
CONTACT_STOP_PATTERN = re.compile(
    r"(?i)(?:T\s*E\s*L(?:EPHONE)?|電話(?:番号)?|F\s*A\s*X|"
    r"E\s*[-‐‑–—]?\s*MAIL|メール|"
    r"開館時間|営業時間|休館日|お問い合わせ|問合せ)\s*[:：]?"
)
ADDRESS_TRAILING_STOP_PATTERN = re.compile(
    r"(?:\s+|[［\[])(?:"
    r"アクセス|交通アクセス|徒歩でのご来館|"
    r"バリアフリー(?:のご案内)?|館内でのお願い|詳細は|"
    r"設計(?=\s)|施工(?=\s)|敷地面積|延床面積"
    r")"
)
TRANSPORT_STOP_PATTERN = re.compile(
    r"\s+交通\s+(?=西武|JR|東京メトロ|都営|バス|電車)"
)


def clean(value) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def display_name(value: str) -> str:
    return re.sub(r"^[◎○〇●]\s*", "", clean(value)).strip()


def name_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", display_name(value)).casefold()
    value = re.sub(
        r"^(?:公益財団法人|一般財団法人|公益社団法人|一般社団法人|学校法人)",
        "",
        value,
    )
    return re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠々]", "", value)


def sanitize_address_candidate(value: str) -> str:
    """Remove adjacent contact/navigation text without changing the address."""
    value = clean(value)
    stops = [
        match.start()
        for pattern in (
            CONTACT_STOP_PATTERN,
            ADDRESS_TRAILING_STOP_PATTERN,
            TRANSPORT_STOP_PATTERN,
        )
        if (match := pattern.search(value))
    ]
    if stops:
        value = value[:min(stops)]
    # Preserve a meaningful closing parenthesis, such as "(木場公園内)".
    # Only separators and a dangling opening bracket are safe to trim.
    value = re.sub(r"[|｜/／・,，;；:：\s]+$", "", value)
    value = re.sub(r"[(（\[［]+$", "", value)
    return value.strip()


def strip_trailing_facility_name(value: str, facility: dict | None) -> str:
    """Remove a page label copied immediately after an otherwise valid address."""
    value = clean(value)
    facility_name = display_name((facility or {}).get("facility_name", ""))
    if facility_name and value.endswith(" " + facility_name):
        return value[:-(len(facility_name) + 1)].rstrip()
    return value


def normalize_address(value: str) -> str:
    value = clean(value)
    value = re.sub(r"^〒?\s*\d{3}[-－]?\d{4}\s*", "", value)
    value = value.replace("－", "-").replace("−", "-")
    # Long-vowel marks belong to names such as 東京ドーム.  Treat one as an
    # address separator only when it occurs between Arabic digits.
    value = re.sub(r"(?<=\d)ー(?=\d)", "-", value)
    value = re.sub(r"\s+", "", value)
    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def canonical_facilities(data_dir: Path) -> list[dict[str, str]]:
    candidates = {
        row["record_id"]: row for row in read_csv(data_dir / "museum_candidates.csv")
    }
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for result in read_csv(data_dir / "museum_reconciliation.csv"):
        record = candidates.get(result.get("record_id", ""))
        facility_id = result.get("canonical_facility_id", "")
        if record and facility_id and result.get("match_status") != "needs_review":
            groups[facility_id].append({**record, **result})

    facilities = []
    for facility_id, rows in sorted(groups.items()):
        rows.sort(key=lambda row: (
            0 if row.get("source_role") == "core" else 1,
            row.get("source_id", ""),
        ))
        addresses = [clean(row.get("address")) for row in rows if clean(row.get("address"))]
        urls = [clean(row.get("official_url")) for row in rows if clean(row.get("official_url"))]
        evidence_urls = [clean(row.get("source_url")) for row in rows if clean(row.get("source_url"))]
        facilities.append({
            "museum_id": facility_id,
            "facility_name": display_name(rows[0].get("facility_name_raw", "")),
            "municipality_code": clean(rows[0].get("municipality_code")),
            "municipality_name": clean(rows[0].get("municipality_name")),
            "existing_address": max(addresses, key=len) if addresses else "",
            "official_url": urls[0] if urls else "",
            "existing_source_url": evidence_urls[0] if evidence_urls else "",
        })
    return facilities


def load_overrides(path: Path) -> dict[str, str]:
    result = {}
    for row in read_csv(path):
        key = clean(row.get("museum_id")) or clean(row.get("facility_name"))
        if key and clean(row.get("location_url")):
            result[key] = clean(row["location_url"])
    return result


def cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.html"


def fetch(
    session: requests.Session, url: str, cache_dir: Path, timeout: int,
    refresh: bool, cache_pages: bool,
):
    target = cache_path(cache_dir, url)
    if cache_pages and target.is_file() and not refresh:
        payload = target.read_bytes()
        return payload, url, hashlib.sha256(payload).hexdigest()
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.content
    if cache_pages:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return payload, response.url, hashlib.sha256(payload).hexdigest()


def json_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from json_objects(child)


def structured_candidates(document, facility: dict | None = None) -> list[dict]:
    candidates = []
    for node in document.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            value = json.loads(node)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for obj in json_objects(value):
            address = obj.get("address")
            if isinstance(address, dict):
                parts = [
                    address.get("postalCode"), address.get("addressRegion"),
                    address.get("addressLocality"), address.get("streetAddress"),
                ]
                address_text = "".join(clean(part) for part in parts if clean(part))
            else:
                address_text = clean(address)
            municipality = clean((facility or {}).get("municipality_name"))
            if municipality and address_text.startswith(municipality):
                address_text = "東京都" + address_text
            address_text = sanitize_address_candidate(address_text)
            geo = obj.get("geo") if isinstance(obj.get("geo"), dict) else {}
            if address_text:
                candidates.append({
                    "address": address_text,
                    "postal_code": clean(
                        address.get("postalCode") if isinstance(address, dict) else ""
                    ),
                    "latitude": clean(geo.get("latitude")),
                    "longitude": clean(geo.get("longitude")),
                    "extraction_method": "json_ld",
                    "context": clean(obj.get("name")),
                })
    return candidates


def visible_candidates(document, facility: dict | None = None) -> list[dict]:
    candidates = []
    municipality = clean((facility or {}).get("municipality_name"))
    texts = []
    for node in document.xpath(
        "//address|//p|//li|//td|//dd|//h1|//h2|//h3|//div[not(div)]"
    ):
        value = clean(" ".join(node.itertext()))
        if value and len(value) <= 400:
            headings = node.xpath("preceding::h1 | preceding::h2 | preceding::h3")
            section_heading = (
                clean(" ".join(headings[-1].itertext())) if headings else ""
            )
            texts.append((value, section_heading))
    texts.extend(
        (value, "") for value in re.split(r"[\n\r]+", document.text_content())
    )
    seen = set()
    for line, section_heading in texts:
        line = clean(line)
        if not re.search(r"\d", line):
            continue
        matches = list(ADDRESS_PATTERN.finditer(line))
        if not matches and municipality and municipality in line:
            municipal_pattern = re.compile(
                r"(?:〒\s*(?P<postal>\d{3}[-－]?\d{4})\s*)?"
                + rf"(?P<address>{re.escape(municipality)}[^\n\r|｜<>]{{1,90}}?"
                + r"\d[^\n\r|｜<>]{0,45})"
            )
            matches = list(municipal_pattern.finditer(line))
        for match in matches:
            address = sanitize_address_candidate(match.group("address"))
            address = strip_trailing_facility_name(address, facility)
            if municipality and address.startswith(municipality):
                address = "東京都" + address
            key = (normalize_address(address), clean(match.group("postal") or ""))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            candidates.append({
                "address": address,
                "postal_code": clean(match.group("postal") or ""),
                "latitude": "", "longitude": "",
                "extraction_method": "html_text",
                "context": line[:240],
                "section_heading": section_heading,
            })
    return candidates


def score_candidate(candidate: dict, facility: dict, page_url: str) -> int:
    address = clean(candidate.get("address"))
    context = clean(candidate.get("context"))
    score = 0
    if facility["municipality_name"] and facility["municipality_name"] in address:
        score += 60
    if address.startswith("東京都") and re.search(r"\d", address):
        score += 20
    if candidate.get("extraction_method") == "json_ld":
        score += 15
    if any(hint in context.casefold() for hint in ACCESS_HINTS):
        score += 10
    if any(hint in context for hint in MAILING_HINTS):
        score -= 50
    facility_key = name_key(facility.get("facility_name", ""))
    page_key = name_key(candidate.get("page_identity", ""))
    if facility_key and facility_key in page_key:
        score += 25
    section_key = name_key(candidate.get("section_heading", ""))
    if facility_key and facility_key in section_key:
        score += 25
    official_host = urlparse(facility.get("official_url", "")).hostname
    if official_host and urlparse(page_url).hostname == official_host:
        score += 10
    return score


def candidate_links(document, base_url: str) -> list[str]:
    host = urlparse(base_url).hostname
    links = []
    for node in document.xpath("//a[@href]"):
        label = clean(" ".join(node.itertext())).casefold()
        href = clean(node.get("href"))
        joined = urljoin(base_url, href)
        parsed = urlparse(joined)
        hint_text = f"{label} {parsed.path}".casefold()
        if parsed.scheme not in {"http", "https"} or parsed.hostname != host:
            continue
        if any(hint.casefold() in hint_text for hint in ACCESS_HINTS) and joined not in links:
            links.append(joined)
    return links[:3]


def extract_official_location(
    session: requests.Session, facility: dict, start_url: str, cache_dir: Path,
    timeout: int, refresh: bool, cache_pages: bool, delay: float,
) -> dict:
    queue = [start_url]
    visited = set()
    found = []
    errors = []
    while queue and len(visited) < 4:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            payload, final_url, digest = fetch(
                session, url, cache_dir, timeout, refresh, cache_pages
            )
            document = html.fromstring(payload, base_url=final_url)
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
            continue
        page_identity = clean(" ".join(document.xpath(
            "//title/text() | //h1//text() | //h2//text()"
        )))
        candidates = (
            structured_candidates(document, facility)
            + visible_candidates(document, facility)
        )
        for candidate in candidates:
            candidate["page_identity"] = page_identity
            candidate.update({
                "source_url": final_url,
                "content_sha256": digest,
                "score": score_candidate(candidate, facility, final_url),
            })
            found.append(candidate)
        supported_on_page = any(row["score"] >= 70 for row in candidates)
        if supported_on_page:
            queue.clear()
        else:
            queue.extend(
                link for link in candidate_links(document, final_url)
                if link not in visited
            )
        if delay:
            time.sleep(delay)

    distinct = {}
    for candidate in found:
        key = normalize_address(candidate["address"])
        if key and candidate["score"] > distinct.get(key, {}).get("score", -10_000):
            distinct[key] = candidate
    ranked = sorted(distinct.values(), key=lambda row: (-row["score"], row["address"]))
    if not ranked or ranked[0]["score"] < 70:
        return {
            "review_status": "needs_review",
            "review_reason": "no_supported_official_address" if not ranked else "low_score_address",
            "notes": "; ".join(errors)[:1000],
        }
    if len(ranked) > 1 and ranked[0]["score"] == ranked[1]["score"]:
        return {
            "review_status": "needs_review",
            "review_reason": "multiple_equal_official_addresses",
            "notes": " | ".join(row["address"] for row in ranked[:5]),
        }
    selected = ranked[0]
    return {
        "address_raw": selected["address"],
        "address_normalized": normalize_address(selected["address"]),
        "postal_code": selected.get("postal_code", ""),
        "latitude": selected.get("latitude", ""),
        "longitude": selected.get("longitude", ""),
        "location_type": "facility_location",
        "address_source_url": selected["source_url"],
        "source_authority": "facility_or_public_official",
        "extraction_method": selected["extraction_method"],
        "coordinate_use": "building_candidate" if selected.get("latitude") else "",
        "review_status": "accepted",
        "review_reason": "",
        "content_sha256": selected["content_sha256"],
        "notes": f"address_score={selected['score']}",
    }


ABR_V3_LEVEL_MAP = {
    "pref": "prefecture",
    "city": "city",
    "machiaza": "machiaza",
    "machiaza_detail": "machiaza_detail",
    "rsdtdsp_blk": "residential_block",
    "rsdtdsp_rsdt": "residential_detail",
    "parcel": "parcel",
    "unknown": "unknown",
}


def unwrap_geocode_result(payload):
    """Return one normalized result from ABR v2, v3, and legacy responses.

    ABR v2 returns ``[{"query": ..., "result": {...}}]``.  ABR v3 returns a
    GeoJSON FeatureCollection whose coordinates are ``[longitude, latitude]``
    and whose municipality code is nested below ``properties.ids``.
    """
    if isinstance(payload, list):
        return unwrap_geocode_result(payload[0]) if payload else {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("type") == "FeatureCollection":
        features = payload.get("features")
        if not isinstance(features, list) or not features:
            return {}
        feature = features[0]
        if not isinstance(feature, dict):
            return {}
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        properties = properties if isinstance(properties, dict) else {}
        geometry = geometry if isinstance(geometry, dict) else {}
        ids = properties.get("ids")
        ids = ids if isinstance(ids, dict) else {}
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
            coordinates = (None, None)
        raw_match_level = clean(properties.get("match_level"))
        raw_coordinate_level = clean(
            properties.get("coordinates_level")
            or properties.get("coordinate_level")
        )
        return {
            "score": properties.get("score"),
            "match_level": ABR_V3_LEVEL_MAP.get(
                raw_match_level, raw_match_level
            ),
            "coordinate_level": ABR_V3_LEVEL_MAP.get(
                raw_coordinate_level, raw_coordinate_level
            ),
            "lat": coordinates[1],
            "lon": coordinates[0],
            "lg_code": ids.get("lg_code") or properties.get("lg_code"),
        }
    for key in ("result", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return unwrap_geocode_result(value[0]) if value else {}
        if isinstance(value, dict):
            return unwrap_geocode_result(value)
    return payload


def geocode_abr(
    session: requests.Session, base_url: str, address: str, timeout: int,
    expected_municipality_code: str = "",
) -> dict:
    if not base_url or not address:
        return {}
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/geocode"):
        endpoint += "/geocode"
    response = session.get(endpoint, params={"address": address}, timeout=timeout)
    response.raise_for_status()
    row = unwrap_geocode_result(response.json())
    coordinate_level = clean(row.get("coordinate_level"))
    latitude = clean(row.get("lat"))
    longitude = clean(row.get("lon"))
    local_government_code = clean(row.get("lg_code"))
    expected = clean(expected_municipality_code)[:5]
    municipality_matches = not expected or local_government_code[:5] == expected
    try:
        coordinates_valid = (
            20.0 <= float(latitude) <= 46.0
            and 122.0 <= float(longitude) <= 154.5
        )
    except (TypeError, ValueError):
        coordinates_valid = False
    if not municipality_matches or not coordinates_valid:
        latitude = longitude = ""
    coordinate_use = (
        "building_candidate"
        if coordinates_valid and municipality_matches
        and coordinate_level in {"residential_detail", "parcel"}
        else "plateau_query"
        if coordinates_valid and municipality_matches and coordinate_level
        else "rejected_municipality_mismatch"
        if expected and local_government_code and not municipality_matches
        else "rejected_invalid_coordinate"
        if row
        else ""
    )
    return {
        "latitude": latitude,
        "longitude": longitude,
        "geocoder": "digital_agency_abr",
        "geocode_score": clean(row.get("score")),
        "match_level": clean(row.get("match_level")),
        "coordinate_level": coordinate_level,
        "coordinate_use": coordinate_use,
    }


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATA_DIR / "museum_location_enrichment.csv")
    parser.add_argument("--review-output", type=Path, default=DEFAULT_DATA_DIR / "museum_location_review.csv")
    parser.add_argument("--abr-api-base", default=os.environ.get("ABR_GEOCODER_URL", ""))
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--cache-pages", action="store_true",
        help="Cache source HTML only after confirming that the source terms allow it",
    )
    parser.add_argument("--max-facilities", type=int)
    parser.add_argument(
        "--only-missing-address", action="store_true",
        help="Process only facilities whose canonical manifest address is empty",
    )
    return parser


def main() -> int:
    print(
        "DEPRECATED: run build_museum_locations.py for the standard location workflow; "
        "this CLI is retained only for compatibility.",
        file=sys.stderr,
    )
    args = build_parser().parse_args()
    facilities = canonical_facilities(args.data_dir.expanduser().resolve())
    if args.only_missing_address:
        facilities = [row for row in facilities if not row["existing_address"]]
    overrides = load_overrides(args.overrides.expanduser().resolve())
    if args.max_facilities is not None:
        facilities = facilities[: args.max_facilities]
    session = requests.Session()
    session.headers.update({
        "User-Agent": "PLATEAU-Heritage-Museum-location-enricher/0.1 (+research; auditable-cache)"
    })
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    accepted, review = [], []
    for index, facility in enumerate(facilities, 1):
        print(f"Location [{index}/{len(facilities)}] {facility['facility_name']}", flush=True)
        row = {field: "" for field in OUTPUT_FIELDS}
        row.update({
            "museum_id": facility["museum_id"],
            "facility_name": facility["facility_name"],
            "municipality_code": facility["municipality_code"],
            "municipality_name": facility["municipality_name"],
            "retrieved_at": now,
        })
        if facility["existing_address"]:
            row.update({
                "address_raw": facility["existing_address"],
                "address_normalized": normalize_address(facility["existing_address"]),
                "location_type": "facility_location",
                "address_source_url": facility["existing_source_url"],
                "source_authority": "existing_manifest_source",
                "extraction_method": "existing_manifest",
                "review_status": "accepted",
            })
        else:
            start_url = (
                overrides.get(facility["museum_id"])
                or overrides.get(facility["facility_name"])
                or facility["official_url"]
            )
            if not start_url:
                row.update({
                    "review_status": "needs_review",
                    "review_reason": "missing_location_source_url",
                })
            else:
                row.update(extract_official_location(
                    session, facility, start_url, args.cache_dir.expanduser().resolve(),
                    args.timeout, args.refresh, args.cache_pages, args.delay,
                ))
        if row["review_status"] == "accepted" and row["address_normalized"] and args.abr_api_base:
            try:
                row.update(geocode_abr(
                    session, args.abr_api_base, row["address_normalized"],
                    args.timeout, facility["municipality_code"],
                ))
            except Exception as exc:
                row["notes"] = clean(
                    f"{row['notes']}; ABR error: {type(exc).__name__}: {exc}"
                )
        (accepted if row["review_status"] == "accepted" else review).append(row)

    write_rows(args.output.expanduser().resolve(), accepted)
    write_rows(args.review_output.expanduser().resolve(), review)
    print(f"Accepted: {len(accepted)}; review: {len(review)}", flush=True)
    print(f"Wrote: {args.output.expanduser().resolve()}", flush=True)
    print(f"Wrote: {args.review_output.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
