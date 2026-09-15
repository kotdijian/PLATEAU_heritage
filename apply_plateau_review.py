#!/usr/bin/env python3
"""Validate and apply human decisions exported by render_plateau_review.py."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
from pathlib import Path

import geopandas as gpd
import pandas as pd

TOOL_VERSION = "0.1.0"
ALLOWED_DECISIONS = {
    "auto_match_human_confirmed",
    "location_guided_human_confirmed",
    "multiple_buildings_human_selected",
    "auto_candidates_rejected_location_guided_selected",
    "plateau_footprint_absent",
    "location_requires_review",
    "deferred",
}
CONFIRMED_DECISIONS = {
    "auto_match_human_confirmed",
    "location_guided_human_confirmed",
    "multiple_buildings_human_selected",
    "auto_candidates_rejected_location_guided_selected",
}


def split_ids(value: object) -> list[str]:
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def read_sql_table(gpkg: Path, table: str) -> pd.DataFrame:
    with sqlite3.connect(gpkg) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            raise ValueError(f"Required table is missing: {table}")
        return pd.read_sql_query(f'SELECT * FROM "{table}"', connection)


def apply(review_gpkg: Path, audit_csv: Path, output: Path) -> dict[str, object]:
    review_gpkg = review_gpkg.expanduser().resolve()
    audit_csv = audit_csv.expanduser().resolve()
    output = output.expanduser().resolve()
    if not review_gpkg.is_file():
        raise FileNotFoundError(f"Review GeoPackage not found: {review_gpkg}")
    if not audit_csv.is_file():
        raise FileNotFoundError(f"Audit CSV not found: {audit_csv}")
    base_audit = read_sql_table(review_gpkg, "review_audit")
    buildings = gpd.read_file(review_gpkg, layer="review_neighborhood_buildings")
    with audit_csv.open(encoding="utf-8-sig", newline="") as stream:
        decisions = list(csv.DictReader(stream))
    if not decisions:
        raise ValueError("Audit CSV contains no decisions")

    entity_ids = set(base_audit["entity_id"].astype(str))
    available = {
        str(entity_id): set(group["building_gml_id"].astype(str))
        for entity_id, group in buildings.groupby("entity_id")
    }
    validated: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(decisions, 2):
        entity_id = str(row.get("entity_id", "")).strip()
        decision = str(row.get("human_decision_type", "")).strip()
        selected = split_ids(row.get("human_selected_building_ids"))
        if not entity_id or entity_id not in entity_ids:
            raise ValueError(f"CSV row {row_number}: unknown entity_id {entity_id!r}")
        if entity_id in validated:
            raise ValueError(f"CSV row {row_number}: duplicate entity_id {entity_id!r}")
        if decision not in ALLOWED_DECISIONS:
            raise ValueError(f"CSV row {row_number}: invalid human_decision_type {decision!r}")
        if decision in CONFIRMED_DECISIONS and not selected:
            raise ValueError(f"CSV row {row_number}: confirmed decision requires selected building(s)")
        if decision == "multiple_buildings_human_selected" and len(selected) < 2:
            raise ValueError(f"CSV row {row_number}: multiple-building decision requires at least two buildings")
        unknown = set(selected) - available.get(entity_id, set())
        if unknown:
            raise ValueError(
                f"CSV row {row_number}: selected building(s) outside review neighborhood: "
                + ";".join(sorted(unknown))
            )
        validated[entity_id] = {key: str(value or "") for key, value in row.items()}

    merged = base_audit.copy()
    merged["review_applied"] = 0
    for index, row in merged.iterrows():
        entity_id = str(row["entity_id"])
        decision = validated.get(entity_id)
        if decision is None:
            continue
        for column in (
            "human_status", "human_decision_type", "human_selected_building_ids",
            "human_rejected_building_ids", "reviewer", "reviewed_at", "notes",
        ):
            merged.at[index, column] = decision.get(column, "")
        merged.at[index, "review_applied"] = 1

    chosen_rows = []
    for entity_id, row in validated.items():
        if row["human_decision_type"] not in CONFIRMED_DECISIONS:
            continue
        for building_id in split_ids(row.get("human_selected_building_ids")):
            chosen_rows.append((entity_id, building_id, row["human_decision_type"]))
    chosen = pd.DataFrame(chosen_rows, columns=["entity_id", "building_gml_id", "human_decision_type"])
    selected = buildings.iloc[0:0].copy()
    if not chosen.empty:
        selected = buildings.merge(chosen, on=["entity_id", "building_gml_id"], how="inner")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output == review_gpkg:
        raise ValueError("Output must differ from the source review GeoPackage")
    shutil.copy2(review_gpkg, output)
    with sqlite3.connect(output) as connection:
        merged.to_sql("review_audit", connection, if_exists="replace", index=False)
    if not selected.empty:
        selected.to_file(
            output, layer="review_human_selected_buildings", driver="GPKG", engine="pyogrio"
        )
    summary = {
        "tool_version": TOOL_VERSION,
        "review_entities": len(base_audit),
        "human_decisions_applied": len(validated),
        "human_confirmed_entities": sum(
            row["human_decision_type"] in CONFIRMED_DECISIONS for row in validated.values()
        ),
        "human_selected_building_links": len(selected),
        "output_gpkg": str(output),
    }
    output.with_suffix(".apply-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Apply PLATEAU human-review decisions")
    result.add_argument("review_gpkg", type=Path)
    result.add_argument("audit_csv", type=Path)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        print(json.dumps(apply(args.review_gpkg, args.audit_csv, args.output), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
