from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import re

import pandas as pd
import yaml

from .common import (
    canonical_frame, resolve, row_value, numeric, normalize_type,
    entity_class, geometry_role, municipality_from_values,
)
from ..util import read_csv_file, list_of_dicts, text, write_json


def _read_data(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return read_csv_file(path)
    obj = json.loads(path.read_text(encoding="utf-8-sig"))
    return pd.DataFrame(list_of_dicts(obj))


def _source_meta(directory: Path) -> dict:
    p = directory / "source.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _keyword_config(config_path: str | None) -> dict:
    defaults = {
        "municipal": [
            "区指定", "市指定", "町指定", "村指定",
            "区登録", "市登録", "町登録", "村登録",
        ],
        "prefectural": [
            "東京都指定", "都指定", "東京都登録", "都登録",
            "東京都選定", "都選定",
        ],
        "national": [
            "国指定", "国登録", "国選定", "国宝",
            "重要文化財", "登録有形文化財", "登録記念物",
            "重要有形民俗文化財", "重要無形民俗文化財",
            "重要文化的景観", "重要伝統的建造物群保存地区",
        ],
        "default_by_municipality": {},
    }
    if config_path:
        cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        for k, v in cfg.items():
            defaults[k] = v
    return defaults


def infer_designation_level(
    text_blob: str,
    municipality_name: str,
    municipality_code: str,
    cfg: dict,
) -> tuple[str, str]:
    s = text_blob or ""

    # More specific local authority wording wins.
    local_variants = list(cfg.get("municipal") or [])
    if municipality_name:
        local_variants += [
            municipality_name + "指定",
            municipality_name + "登録",
            municipality_name + "選定",
        ]
    if any(k and k in s for k in local_variants):
        return "municipal", "explicit_municipal_keyword"

    if any(k in s for k in (cfg.get("prefectural") or [])):
        return "prefectural", "explicit_prefectural_keyword"

    if any(k in s for k in (cfg.get("national") or [])):
        return "national", "explicit_national_keyword"

    default = (cfg.get("default_by_municipality") or {}).get(municipality_code)
    if default in {"municipal", "prefectural", "national"}:
        return default, "configured_default"

    return "ambiguous", "no_explicit_designation_level"


def normalize(
    input_dir: str | Path,
    output_dir: str | Path,
    config_path: str | None = None,
):
    src = Path(input_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cfg = _keyword_config(config_path)

    rows = []
    reports = []

    for municipality_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        code = municipality_dir.name
        data_files = [p for p in municipality_dir.glob("data.*") if p.suffix.lower() in {".csv", ".json"}]
        if not data_files:
            continue
        data_path = data_files[0]
        meta = _source_meta(municipality_dir)
        manifest_entry = meta.get("manifest_entry") or {}
        collection = meta.get("collection") or {}
        organization = manifest_entry.get("organization") or collection.get("organization") or ""
        dataset_title = manifest_entry.get("dataset_title") or collection.get("dataset_title") or ""

        try:
            df = _read_data(data_path)
        except Exception as e:
            reports.append({
                "municipality_code": code, "organization": organization,
                "source_file": str(data_path), "status": "read_failed",
                "input_rows": 0, "normalized_rows": 0, "error": str(e),
            })
            continue

        cols = {k: resolve(df.columns, k) for k in (
            "id", "name", "name_kana", "place_name", "address_detail", "owner", "address",
            "municipality", "municipality_code", "latitude", "longitude",
            "category", "type", "designation", "designation_date",
        )}

        if not cols["name"]:
            reports.append({
                "municipality_code": code, "organization": organization,
                "source_file": str(data_path), "status": "not_recognized",
                "input_rows": len(df), "normalized_rows": 0,
                "error": "no recognized cultural-property name column",
            })
            continue

        local_count = Counter()
        before = len(rows)

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
            if not mcode:
                mcode = code
            if not mname:
                mname = organization

            raw_category = row_value(row, cols["category"])
            raw_type = row_value(row, cols["type"])
            raw_designation = row_value(row, cols["designation"])
            blob = " ".join([raw_category, raw_designation])
            level, level_reason = infer_designation_level(blob, mname, mcode, cfg)
            local_count[level] += 1

            typ = normalize_type(raw_type, raw_category)
            cls = entity_class(typ)

            rows.append({
                "source_level": "municipal_source",
                "source_authority": organization,
                "source_dataset": dataset_title,
                "source_record_id": row_value(row, cols["id"]) or f"{code}:{idx}",
                "source_url": collection.get("source_url") or manifest_entry.get("source_csv_url") or manifest_entry.get("json_endpoint") or "",
                "source_file": str(data_path),
                "name": name,
                "name_kana": row_value(row, cols["name_kana"]),
                "place_name": row_value(row, cols["place_name"]),
                "address_detail": row_value(row, cols["address_detail"]),
                "owner": row_value(row, cols["owner"]),
                "address": address,
                "municipality": mname,
                "municipality_code": mcode,
                "category": raw_category,
                "type": typ,
                "designation": level,
                "designation_date": row_value(row, cols["designation_date"]),
                "latitude": numeric(row.get(cols["latitude"])) if cols["latitude"] else None,
                "longitude": numeric(row.get(cols["longitude"])) if cols["longitude"] else None,
                "entity_class": cls,
                "geometry_role": geometry_role(cls),
                "designation_level": level,
                "raw_category": raw_category,
                "raw_type": raw_type,
                "raw_designation": raw_designation,
                "_designation_reason": level_reason,
            })

        reports.append({
            "municipality_code": code,
            "organization": organization,
            "source_file": str(data_path),
            "status": "normalized",
            "input_rows": len(df),
            "normalized_rows": len(rows) - before,
            "municipal": local_count["municipal"],
            "prefectural": local_count["prefectural"],
            "national": local_count["national"],
            "ambiguous": local_count["ambiguous"],
            "error": "",
        })

    all_df = pd.DataFrame(rows)
    if all_df.empty:
        all_df = canonical_frame([])

    canonical_cols = canonical_frame([]).columns.tolist()
    for c in canonical_cols:
        if c not in all_df.columns:
            all_df[c] = ""

    municipal = all_df[all_df["designation_level"] == "municipal"].copy()
    excluded = all_df[all_df["designation_level"].isin(["national", "prefectural"])].copy()
    needs_review = all_df[all_df["designation_level"] == "ambiguous"].copy()

    all_df.to_csv(out / "municipal_all_normalized.csv", index=False, encoding="utf-8-sig")
    municipal[canonical_cols].to_csv(out / "municipal.csv", index=False, encoding="utf-8-sig")
    excluded.to_csv(out / "municipal_excluded_cross_level.csv", index=False, encoding="utf-8-sig")
    needs_review.to_csv(out / "municipal_needs_review.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(reports).to_csv(out / "municipal_normalization_report.csv", index=False, encoding="utf-8-sig")

    write_json(out / "municipal_normalization_summary.json", {
        "total_normalized": int(len(all_df)),
        "municipal": int(len(municipal)),
        "excluded_cross_level": int(len(excluded)),
        "needs_review": int(len(needs_review)),
    })
    return municipal, excluded, needs_review
