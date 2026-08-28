from __future__ import annotations

from pathlib import Path
import json
import re

import pandas as pd

from .common import (
    canonical_frame, resolve, row_value, numeric, normalize_type,
    entity_class, geometry_role, municipality_from_values,
)
from ..tokyo_codes import code_from_text
from ..util import read_csv_file, text, write_json


def _from_online_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        address = text(obj.get("address"))
        code, name = code_from_text(address)
        raw_category = text(obj.get("category_raw"))
        raw_type = text(obj.get("type_raw"))
        typ = normalize_type(raw_type, raw_category)
        cls = entity_class(typ)

        designation_label = raw_category or raw_type
        rows.append({
            "source_level": "national",
            "source_authority": "文化庁",
            "source_dataset": "文化遺産オンライン／国指定文化財等データベース",
            "source_record_id": text(obj.get("detail_id")),
            "source_url": text(obj.get("source_url")),
            "source_file": str(path),
            "name": text(obj.get("name")),
            "name_kana": text(obj.get("name_kana")),
            "place_name": text(obj.get("place_name")),
            "owner": text(obj.get("owner")),
            "address": address,
            "municipality": name,
            "municipality_code": code,
            "category": raw_category,
            "type": typ,
            "designation": "national",
            "designation_date": text(obj.get("designation_date")),
            "latitude": obj.get("latitude"),
            "longitude": obj.get("longitude"),
            "entity_class": cls,
            "geometry_role": geometry_role(cls),
            "designation_level": "national",
            "raw_category": raw_category,
            "raw_type": raw_type,
            "raw_designation": designation_label,
        })
    return rows


def _from_official_csv(path: Path) -> list[dict]:
    df = read_csv_file(path)
    cols = {k: resolve(df.columns, k) for k in (
        "id", "name", "name_kana", "place_name", "owner", "address",
        "municipality", "municipality_code", "latitude", "longitude",
        "category", "type", "designation", "designation_date",
    )}
    if not cols["name"]:
        raise ValueError(f"no recognized name column in {path}")

    rows = []
    for idx, row in df.iterrows():
        name = row_value(row, cols["name"])
        if not name:
            continue
        address = row_value(row, cols["address"])
        mcode, mname = municipality_from_values(
            row_value(row, cols["municipality_code"]),
            row_value(row, cols["municipality"]),
            address,
        )
        raw_category = row_value(row, cols["category"])
        raw_type = row_value(row, cols["type"])
        raw_designation = row_value(row, cols["designation"])
        typ = normalize_type(raw_type, raw_category)
        cls = entity_class(typ)

        rows.append({
            "source_level": "national",
            "source_authority": "文化庁",
            "source_dataset": "国指定文化財等データベース CSV export",
            "source_record_id": row_value(row, cols["id"]) or f"{path.stem}:{idx}",
            "source_url": "",
            "source_file": str(path),
            "name": name,
            "name_kana": row_value(row, cols["name_kana"]),
            "place_name": row_value(row, cols["place_name"]),
            "owner": row_value(row, cols["owner"]),
            "address": address,
            "municipality": mname,
            "municipality_code": mcode,
            "category": raw_category,
            "type": typ,
            "designation": "national",
            "designation_date": row_value(row, cols["designation_date"]),
            "latitude": numeric(row.get(cols["latitude"])) if cols["latitude"] else None,
            "longitude": numeric(row.get(cols["longitude"])) if cols["longitude"] else None,
            "entity_class": cls,
            "geometry_role": geometry_role(cls),
            "designation_level": "national",
            "raw_category": raw_category,
            "raw_type": raw_type,
            "raw_designation": raw_designation,
        })
    return rows


def _dedupe_key(row: dict):
    # National records sometimes appear in multiple exported subsets. Prefer
    # stable source IDs; otherwise use name + address + category.
    rid = text(row.get("source_record_id"))
    if rid:
        return ("id", rid)
    return (
        "content",
        text(row.get("name")),
        text(row.get("address")),
        text(row.get("raw_category")),
        text(row.get("raw_type")),
    )


def normalize(input_dir: str | Path, output_dir: str | Path):
    src = Path(input_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    reports = []

    online = src / "records.jsonl"
    if online.exists():
        try:
            r = _from_online_jsonl(online)
            rows.extend(r)
            reports.append({
                "source_file": str(online), "format": "cultural_heritage_online_jsonl",
                "status": "normalized", "rows": len(r), "error": "",
            })
        except Exception as e:
            reports.append({
                "source_file": str(online), "format": "cultural_heritage_online_jsonl",
                "status": "failed", "rows": 0, "error": str(e),
            })

    for p in sorted(src.glob("official_export_*.csv")):
        try:
            r = _from_official_csv(p)
            rows.extend(r)
            reports.append({
                "source_file": str(p), "format": "kunishitei_csv",
                "status": "normalized", "rows": len(r), "error": "",
            })
        except Exception as e:
            reports.append({
                "source_file": str(p), "format": "kunishitei_csv",
                "status": "failed", "rows": 0, "error": str(e),
            })

    dedup = {}
    for row in rows:
        dedup.setdefault(_dedupe_key(row), row)
    rows = list(dedup.values())

    df = canonical_frame(rows)
    # Records without municipality code are valid raw national records, but
    # cannot yet be routed to municipality-based PLATEAU processing.
    ready = df[df["municipality_code"].astype(str).str.fullmatch(r"\d{5}", na=False)].copy()
    review = df[~df.index.isin(ready.index)].copy()

    df.to_csv(out / "national_all_normalized.csv", index=False, encoding="utf-8-sig")
    ready.to_csv(out / "national.csv", index=False, encoding="utf-8-sig")
    review.to_csv(out / "national_needs_review.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(reports).to_csv(out / "national_normalization_report.csv", index=False, encoding="utf-8-sig")
    write_json(out / "national_normalization_summary.json", {
        "total_normalized": int(len(df)),
        "gml_ready": int(len(ready)),
        "needs_review": int(len(review)),
    })
    return ready, review
