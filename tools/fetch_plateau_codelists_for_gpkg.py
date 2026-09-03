#!/usr/bin/env python3
"""
Fetch only the PLATEAU codelist XML files required by an existing Heritage GPKG.

No CityGML ZIP or CityGML body is downloaded.

Method
------
1. Read plateau_disaster_risk in the existing GPKG.
2. Extract *_codespace XML basenames actually referenced by risk rows.
3. Resolve each municipality's exact PLATEAU CMS dataset root by:
   - querying the official CityGML file-search API, and
   - matching the basename of source_gml already recorded in the GPKG.
4. Download only:
       <dataset-root>/codelists/<referenced XML filename>
5. Stop if the exact original dataset root cannot be established.

This is intended to feed build_heritage_disaster_layers_v4.py (or the same
script saved locally as build_heritage_disaster_layers.py).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path, PurePosixPath


API_BASE = "https://api.plateauview.mlit.go.jp/datacatalog/citygml"
UA = "PLATEAU-Heritage-codelist-fetcher/1.0"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, type=Path, help="13_heritage.gpkg")
    p.add_argument(
        "--output",
        type=Path,
        default=Path(".cache/plateau_codelists"),
        help="destination root for downloaded codelists",
    )
    p.add_argument("--timeout", type=int, default=60)
    return p.parse_args()


def qident(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {
        row[1]
        for row in con.execute(f"PRAGMA table_info({qident(table)})").fetchall()
    }


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def clean(v) -> str:
    return "" if v is None else str(v).strip()


def xml_basename(codespace: str) -> str:
    s = clean(codespace)
    if not s:
        return ""
    s = s.split("#", 1)[0]
    base = PurePosixPath(s.replace("\\", "/")).name
    return base if base.lower().endswith(".xml") else ""


def source_basename(source_gml: str) -> str:
    s = clean(source_gml)
    if not s:
        return ""
    return PurePosixPath(s.replace("\\", "/")).name


def http_json(url: str, timeout: int):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def collect_gml_urls(obj) -> list[str]:
    found = []
    if isinstance(obj, dict):
        for v in obj.values():
            found.extend(collect_gml_urls(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(collect_gml_urls(v))
    elif isinstance(obj, str):
        low = obj.lower()
        if low.startswith(("http://", "https://")) and "/udx/" in low and low.endswith(".gml"):
            found.append(obj)
    return found


def dataset_root_from_gml_url(url: str) -> str:
    marker = "/udx/"
    idx = url.find(marker)
    if idx < 0:
        return ""
    return url[:idx].rstrip("/")


def fetch_bytes(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/xml,text/xml,*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def validate_xml(data: bytes, url: str):
    try:
        ET.fromstring(data)
    except Exception as e:
        raise RuntimeError(f"Downloaded file is not valid XML: {url}: {e}") from e


def load_required_rows(gpkg: Path):
    with sqlite3.connect(gpkg) as con:
        if not table_exists(con, "plateau_disaster_risk"):
            raise RuntimeError("plateau_disaster_risk table not found")
        if not table_exists(con, "heritage_buildings_footprint"):
            raise RuntimeError("heritage_buildings_footprint table not found")

        rc = columns(con, "plateau_disaster_risk")
        bc = columns(con, "heritage_buildings_footprint")

        codespace_cols = sorted(c for c in rc if c.endswith("_codespace"))
        if not codespace_cols:
            raise RuntimeError(
                "No *_codespace columns found in plateau_disaster_risk. "
                "Codelists cannot be recovered without a CityGML rescan."
            )

        if "building_gml_id" not in rc:
            raise RuntimeError("plateau_disaster_risk.building_gml_id not found")
        if "gml_id" not in bc:
            raise RuntimeError("heritage_buildings_footprint.gml_id not found")

        city_expr = None
        if "municipality_code" in rc:
            city_expr = "r.municipality_code"
        elif "municipality_code" in bc:
            city_expr = "b.municipality_code"
        else:
            raise RuntimeError(
                "municipality_code not found in risk or footprint table"
            )

        source_expr = None
        if "source_gml" in rc:
            source_expr = "r.source_gml"
        elif "source_gml" in bc:
            source_expr = "b.source_gml"
        else:
            raise RuntimeError(
                "source_gml not found in risk or footprint table. "
                "Exact original PLATEAU dataset cannot be identified safely."
            )

        select_cols = [
            f"{city_expr} AS municipality_code",
            f"{source_expr} AS source_gml",
        ]
        select_cols += [f"r.{qident(c)} AS {qident(c)}" for c in codespace_cols]

        sql = f"""
            SELECT {", ".join(select_cols)}
            FROM plateau_disaster_risk r
            LEFT JOIN heritage_buildings_footprint b
              ON b.gml_id = r.building_gml_id
        """

        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, row)) for row in cur.fetchall()]

    return rows, codespace_cols


def main():
    a = parse_args()
    gpkg = a.input.resolve()
    out = a.output.resolve()

    if not gpkg.exists():
        raise FileNotFoundError(gpkg)

    print("[1/5] Read referenced codelists from GPKG")
    rows, codespace_cols = load_required_rows(gpkg)

    city_sources: dict[str, set[str]] = defaultdict(set)
    city_xmls: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        city = clean(row.get("municipality_code"))
        if not city:
            continue
        city = city.zfill(5)

        sb = source_basename(row.get("source_gml"))
        if sb:
            city_sources[city].add(sb)

        for c in codespace_cols:
            xb = xml_basename(row.get(c))
            if xb:
                city_xmls[city].add(xb)

    cities = sorted(city_xmls)
    if not cities:
        raise RuntimeError("No referenced codelist XMLs found")

    print(f"  municipalities with referenced codelists: {len(cities)}")
    print(f"  total city/XML pairs: {sum(len(v) for v in city_xmls.values())}")

    print("[2/5] Resolve exact PLATEAU dataset roots via official API")
    city_roots: dict[str, set[str]] = {}

    for i, city in enumerate(cities, 1):
        api_url = f"{API_BASE}/{urllib.parse.quote(city)}"
        print(f"  [{i}/{len(cities)}] {city}", end="", flush=True)

        data = http_json(api_url, a.timeout)
        urls = sorted(set(collect_gml_urls(data)))
        if not urls:
            raise RuntimeError(f"\nNo CityGML file URLs returned for municipality {city}")

        by_base = defaultdict(list)
        for url in urls:
            by_base[source_basename(url)].append(url)

        source_names = city_sources.get(city, set())
        matched_urls = []
        for base in source_names:
            matched_urls.extend(by_base.get(base, []))

        if not source_names:
            raise RuntimeError(
                f"\nNo source_gml basename is recorded for municipality {city}; "
                "refusing to guess the dataset root."
            )

        if not matched_urls:
            examples = ", ".join(sorted(source_names)[:5])
            raise RuntimeError(
                f"\nCould not match any recorded source_gml basename for {city} "
                f"against the current PLATEAU API response.\n"
                f"Recorded examples: {examples}\n"
                "This likely means the API dataset version changed after extraction. "
                "Stopping rather than downloading a potentially wrong codelist."
            )

        roots = {
            dataset_root_from_gml_url(u)
            for u in matched_urls
            if dataset_root_from_gml_url(u)
        }
        if not roots:
            raise RuntimeError(f"\nCould not derive dataset root for {city}")

        city_roots[city] = roots
        print(f" -> {len(roots)} exact dataset root(s)")

    print("[3/5] Download only referenced codelist XML files")
    downloaded = 0
    reused = 0
    failures = []

    for city in cities:
        for root in sorted(city_roots[city]):
            dataset_name = PurePosixPath(urllib.parse.urlparse(root).path).name or "dataset"
            dest_dir = out / city / dataset_name
            dest_dir.mkdir(parents=True, exist_ok=True)

            for name in sorted(city_xmls[city]):
                url = root + "/codelists/" + urllib.parse.quote(name)
                dest = dest_dir / name

                if dest.exists() and dest.stat().st_size > 0:
                    try:
                        ET.parse(dest)
                        reused += 1
                        continue
                    except Exception:
                        dest.unlink()

                try:
                    data = fetch_bytes(url, a.timeout)
                    validate_xml(data, url)
                    dest.write_bytes(data)
                    downloaded += 1
                except urllib.error.HTTPError as e:
                    failures.append((city, name, e.code, url))
                except Exception as e:
                    failures.append((city, name, str(e), url))

    print(f"  downloaded: {downloaded}")
    print(f"  reused    : {reused}")

    print("[4/5] Validate completeness")
    if failures:
        print("\nMissing/failed codelists:", file=sys.stderr)
        for city, name, reason, url in failures[:100]:
            print(f"  {city} {name}: {reason}\n    {url}", file=sys.stderr)
        raise RuntimeError(
            f"{len(failures)} codelist download(s) failed. "
            "Do not run v4 until this is resolved."
        )

    xml_count = len(list(out.rglob("*.xml")))
    print(f"  valid local XML files: {xml_count}")

    print("[5/5] Ready for v4")
    print()
    print("Use this codelist root:")
    print(f"  {out}")
    print()
    print("Example:")
    print(
        "python build_heritage_disaster_layers.py \\\n"
        f"  --input {gpkg} \\\n"
        "  --output ./output/13_heritage_enriched.gpkg \\\n"
        f"  --codelist-root {out} \\\n"
        "  --max-slots 8"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
