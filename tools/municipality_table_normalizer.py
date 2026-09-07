#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd

LEADING_SORT_KEYS = (
    "river",
    "risk_type",
    "risk_type_ja",
    "hazard_source",
    "scenario",
    "area",
)

def _norm_code(s: pd.Series) -> pd.Series:
    out = s.fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    mask = out.str.fullmatch(r"\d{1,5}", na=False)
    out.loc[mask] = out.loc[mask].str.zfill(5)
    return out

def _canonical_mapping(meta: pd.DataFrame) -> pd.DataFrame:
    required = {"municipality_code", "municipality_name"}
    if not required.issubset(meta.columns):
        raise RuntimeError(
            f"canonical municipality mapping requires columns {sorted(required)}"
        )
    m = meta[["municipality_code", "municipality_name"]].copy()
    m["municipality_code"] = _norm_code(m["municipality_code"])
    m["municipality_name"] = m["municipality_name"].fillna("").astype(str).str.strip()
    m = m[(m["municipality_code"] != "") & (m["municipality_name"] != "")]
    m = m.drop_duplicates()

    bad_code = m.groupby("municipality_code")["municipality_name"].nunique()
    bad_name = m.groupby("municipality_name")["municipality_code"].nunique()
    if (bad_code > 1).any():
        raise RuntimeError(
            "municipality_code is not one-to-one with municipality_name: "
            + str(bad_code[bad_code > 1].to_dict())
        )
    if (bad_name > 1).any():
        raise RuntimeError(
            "municipality_name is not one-to-one with municipality_code: "
            + str(bad_name[bad_name > 1].to_dict())
        )
    return m.sort_values(["municipality_code", "municipality_name"]).reset_index(drop=True)

def _is_a31a_table(path: Path) -> bool:
    n = path.name.lower()
    return n.startswith("a31a_") or n.startswith("inundation_a31a_")

def _normalize_one(path: Path, mapping: pd.DataFrame) -> dict:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    result = {
        "file": path.name,
        "rows_before": int(len(df)),
        "rows_after": int(len(df)),
        "municipality_code_added": False,
        "invalid_pair_rows_removed": 0,
        "zero_total_artifact_rows_removed": 0,
        "status": "skipped",
    }
    if "municipality_name" not in df.columns:
        return result

    result["status"] = "normalized"
    df["municipality_name"] = df["municipality_name"].astype(str).str.strip()
    name_to_code = dict(zip(mapping["municipality_name"], mapping["municipality_code"]))

    if "municipality_code" not in df.columns:
        insert_at = df.columns.get_loc("municipality_name")
        codes = df["municipality_name"].map(name_to_code).fillna("")
        df.insert(insert_at, "municipality_code", codes)
        result["municipality_code_added"] = True
    else:
        df["municipality_code"] = _norm_code(df["municipality_code"])
        expected = df["municipality_name"].map(name_to_code)
        known = expected.notna()
        invalid = known & (df["municipality_code"] != expected)
        result["invalid_pair_rows_removed"] = int(invalid.sum())
        if invalid.any():
            df = df.loc[~invalid].copy()
        expected = df["municipality_name"].map(name_to_code)
        empty = df["municipality_code"].eq("") & expected.notna()
        df.loc[empty, "municipality_code"] = expected.loc[empty]

    expected = df["municipality_name"].map(name_to_code)
    known = expected.notna()
    df.loc[known, "municipality_code"] = expected.loc[known]

    if _is_a31a_table(path) and "Total" in df.columns:
        total = pd.to_numeric(df["Total"], errors="coerce")
        artifact = total.fillna(0).eq(0)
        result["zero_total_artifact_rows_removed"] = int(artifact.sum())
        if artifact.any():
            df = df.loc[~artifact].copy()

    cols = list(df.columns)
    cols.remove("municipality_code")
    name_pos = cols.index("municipality_name")
    cols.insert(name_pos, "municipality_code")
    df = df[cols].drop_duplicates()

    leading = [
        c for c in LEADING_SORT_KEYS
        if c in df.columns
        and c not in {"municipality_code", "municipality_name"}
        and df.columns.get_loc(c) < df.columns.get_loc("municipality_name")
    ]
    sort_cols = leading + ["municipality_code", "municipality_name"]
    if "record_id" in df.columns:
        sort_cols.append("record_id")
    sort_cols = list(dict.fromkeys(sort_cols))
    df = df.sort_values(sort_cols, kind="stable", na_position="last").reset_index(drop=True)

    expected = df["municipality_name"].map(name_to_code)
    mismatch = expected.notna() & (df["municipality_code"] != expected)
    if mismatch.any():
        sample = df.loc[mismatch, ["municipality_code", "municipality_name"]].head(10)
        raise RuntimeError(
            f"municipality mapping invariant failed in {path.name}:\n{sample}"
        )

    df.to_csv(path, index=False, encoding="utf-8-sig")
    result["rows_after"] = int(len(df))
    return result

def finalize_municipality_tables(
    tables: Path,
    meta: pd.DataFrame,
    metadata_dir: Path | None = None,
) -> list[dict]:
    tables = Path(tables)
    metadata_dir = Path(metadata_dir or (tables.parent / "metadata"))
    metadata_dir.mkdir(parents=True, exist_ok=True)

    mapping = _canonical_mapping(meta)
    mapping.to_csv(
        tables / "municipality_code_name_master.csv",
        index=False,
        encoding="utf-8-sig",
    )

    report = []
    for path in sorted(tables.glob("*.csv")):
        if path.name == "municipality_code_name_master.csv":
            continue
        report.append(_normalize_one(path, mapping))

    municipality_files = [r for r in report if r["status"] == "normalized"]
    removed = sum(r["invalid_pair_rows_removed"] for r in municipality_files)
    added = sum(bool(r["municipality_code_added"]) for r in municipality_files)

    payload = {
        "municipality_count": int(len(mapping)),
        "municipality_csv_count": int(len(municipality_files)),
        "files_with_code_added": int(added),
        "invalid_pair_rows_removed": int(removed),
        "files": report,
    }
    (metadata_dir / "municipality_table_normalization.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        "[municipality tables] "
        f"files={len(municipality_files)}, "
        f"code-added={added}, invalid-pairs-removed={removed}"
    )
    return report

def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tables", type=Path)
    ap.add_argument(
        "--mapping",
        type=Path,
        default=None,
        help="CSV containing municipality_code and municipality_name. "
             "Defaults to municipality_record_counts.csv in tables.",
    )
    args = ap.parse_args()
    mapping_path = args.mapping or (args.tables / "municipality_record_counts.csv")
    meta = pd.read_csv(mapping_path, dtype=str)
    finalize_municipality_tables(args.tables, meta)

if __name__ == "__main__":
    _main()
