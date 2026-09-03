#!/usr/bin/env python3
"""
PLATEAU Heritage disaster-risk enrichment v4

Assumption:
- Within Tokyo, the same (risk_type, semantic_field, code) maps to exactly one label.
- If conflicting labels are found for the same code key, abort immediately.
- Different semantic fields may reuse the same numeric code (e.g. scale=2 and adminType=2).

No CityGML rescan is performed.
Existing codes in plateau_disaster_risk are resolved only from codelist XML files.
The full heritage_buildings_footprint feature set is preserved.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import pandas as pd


RISK_ORDER = {
    "river_flooding": 10,
    "tsunami": 20,
    "high_tide": 30,
    "inland_flooding": 40,
    "reservoir_flooding": 50,
    "landslide": 60,
}

RISK_PREFIXES = {
    "river_flooding": ("RiverFloodingRiskAttribute", "RiverFloodingRisk"),
    "tsunami": ("TsunamiRiskAttribute", "TsunamiRisk"),
    "high_tide": ("HighTideRiskAttribute", "HighTideRisk"),
    "inland_flooding": ("InlandFloodingRiskAttribute", "InlandFloodingRisk"),
    "reservoir_flooding": ("ReservoirFloodingRiskAttribute", "ReservoirFloodingRisk"),
    "landslide": (
        "LandSlideRiskAttribute",
        "LandslideRiskAttribute",
        "LandSlideRisk",
        "LandslideRisk",
    ),
}

FIELD_XML_SUFFIX = {
    "description": "description",
    "scale": "scale",
    "admin_type": "adminType",
    "rank": "rank",
    "rank_org": "rankOrg",
    "area_type": "areaType",
}

NUMERICISH = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


class CodeCollisionError(RuntimeError):
    pass


def parse_args():
    p = argparse.ArgumentParser(
        description=(
            "Resolve disaster-risk codes from codelist XMLs. "
            "Abort on conflicting code->label mappings."
        )
    )
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument(
        "--codelist-root",
        action="append",
        required=True,
        type=Path,
        help="Root containing codelist XML files. Repeatable.",
    )
    p.add_argument("--max-slots", type=int, default=8)
    p.add_argument(
        "--strict-unresolved",
        action="store_true",
        help="Abort if any risk code remains unresolved.",
    )
    return p.parse_args()


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def txt(v) -> str:
    return "" if pd.isna(v) else str(v).strip()


def localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def normalize_code(v) -> str:
    s = txt(v)
    if s.endswith(".0") and NUMERICISH.fullmatch(s):
        return s[:-2]
    return s


def sqlite_tables(gpkg: Path) -> list[str]:
    with sqlite3.connect(gpkg) as con:
        return [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table','view') ORDER BY name"
            ).fetchall()
        ]


def read_table(gpkg: Path, table: str) -> pd.DataFrame:
    with sqlite3.connect(gpkg) as con:
        return pd.read_sql_query(f"SELECT * FROM {qident(table)}", con)


def require_columns(df: pd.DataFrame, cols: Iterable[str], source: str):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(
            f"{source}: missing required columns: {missing}\n"
            f"available={list(df.columns)}"
        )


def human_label_candidate(label: str, code: str) -> bool:
    label = txt(label)
    code = normalize_code(code)
    return bool(label and label != code and not NUMERICISH.fullmatch(label))


def infer_semantic_from_basename(basename: str):
    stem = Path(basename).stem

    field = None
    prefix = None
    for semantic, suffix in FIELD_XML_SUFFIX.items():
        token = "_" + suffix
        if stem.lower().endswith(token.lower()):
            field = semantic
            prefix = stem[:-len(token)]
            break

    if not field or not prefix:
        return None, None

    for risk_type, prefixes in RISK_PREFIXES.items():
        if any(prefix.lower() == p.lower() for p in prefixes):
            return risk_type, field

    return None, None


def parse_codelist_xml(path: Path) -> dict[str, str]:
    try:
        root = ET.parse(path).getroot()
    except Exception as e:
        raise ValueError(f"Failed to parse XML: {path}: {e}") from e

    mapping: dict[str, str] = {}

    candidates = [
        elem for elem in root.iter()
        if localname(elem.tag) in {"Definition", "dictionaryEntry", "entry"}
    ]

    for elem in candidates:
        descendants = list(elem.iter())

        identifiers = [
            txt(x.text)
            for x in descendants
            if localname(x.tag) in {"identifier", "code", "value"} and txt(x.text)
        ]
        descriptions = [
            txt(x.text)
            for x in descendants
            if localname(x.tag) in {"description", "label", "title"} and txt(x.text)
        ]
        names = [
            txt(x.text)
            for x in descendants
            if localname(x.tag) == "name" and txt(x.text)
        ]

        code = normalize_code(identifiers[0]) if identifiers else ""
        if not code:
            nums = [n for n in names if NUMERICISH.fullmatch(n)]
            if nums:
                code = normalize_code(nums[0])

        label = ""
        for cand in descriptions + names:
            if human_label_candidate(cand, code):
                label = cand
                break

        if code and label:
            old = mapping.get(code)
            if old is not None and old != label:
                raise CodeCollisionError(
                    "Conflicting labels inside one XML:\n"
                    f"  file={path}\n"
                    f"  code={code}\n"
                    f"  label_A={old}\n"
                    f"  label_B={label}"
                )
            mapping[code] = label

    if not mapping:
        for parent in root.iter():
            vals = {}
            for child in list(parent):
                key = localname(child.tag).lower()
                value = txt(child.text)
                if value:
                    vals[key] = value

            code = normalize_code(
                vals.get("identifier")
                or vals.get("code")
                or vals.get("value")
                or ""
            )
            label = (
                vals.get("description")
                or vals.get("label")
                or vals.get("title")
                or vals.get("name")
                or ""
            )

            if code and human_label_candidate(label, code):
                old = mapping.get(code)
                if old is not None and old != label:
                    raise CodeCollisionError(
                        "Conflicting labels inside one XML:\n"
                        f"  file={path}\n"
                        f"  code={code}\n"
                        f"  label_A={old}\n"
                        f"  label_B={label}"
                    )
                mapping[code] = label

    return mapping


@dataclass
class RegistryValue:
    label: str
    files: list[str]


class GlobalCodeRegistry:
    def __init__(self, roots: list[Path]):
        self.roots = [p.resolve() for p in roots]
        self.basename_index: dict[tuple[str, str], RegistryValue] = {}
        self.semantic_index: dict[tuple[str, str, str], RegistryValue] = {}
        self.scanned_files: list[Path] = []
        self.relevant_files: list[Path] = []

    def _add(self, index, key, label: str, path: Path, scope: str):
        existing = index.get(key)
        if existing is None:
            index[key] = RegistryValue(label, [str(path)])
            return

        if existing.label != label:
            raise CodeCollisionError(
                f"CODE COLLISION [{scope}]\n"
                f"  key={key}\n"
                f"  label_A={existing.label}\n"
                f"  file_A={existing.files[0]}\n"
                f"  label_B={label}\n"
                f"  file_B={path}"
            )

        if str(path) not in existing.files:
            existing.files.append(str(path))

    def build(self):
        xmls: list[Path] = []

        for root in self.roots:
            if not root.exists():
                raise FileNotFoundError(f"codelist root not found: {root}")
            if root.is_file() and root.suffix.lower() == ".xml":
                xmls.append(root.resolve())
            elif root.is_dir():
                xmls.extend(p.resolve() for p in root.rglob("*.xml"))

        xmls = sorted(set(xmls))
        self.scanned_files = xmls

        for path in xmls:
            risk_type, field = infer_semantic_from_basename(path.name)
            if not risk_type or not field:
                continue

            self.relevant_files.append(path)
            mapping = parse_codelist_xml(path)

            for code, label in mapping.items():
                self._add(
                    self.basename_index,
                    (path.name.lower(), code),
                    label,
                    path,
                    "basename+code",
                )
                self._add(
                    self.semantic_index,
                    (risk_type, field, code),
                    label,
                    path,
                    "risk_type+field+code",
                )

    def resolve(
        self,
        risk_type: str,
        field: str,
        code,
        raw_label="",
        codespace="",
    ) -> tuple[str, str, str]:
        code = normalize_code(code)
        raw_label = txt(raw_label)
        codespace = txt(codespace)

        if not code:
            if raw_label:
                return raw_label, "raw_label", ""
            return "", "empty", ""

        if human_label_candidate(raw_label, code):
            hit = self.semantic_index.get((risk_type, field, code))
            if hit and hit.label != raw_label:
                raise CodeCollisionError(
                    "Existing GPKG label conflicts with codelist registry:\n"
                    f"  key=({risk_type}, {field}, {code})\n"
                    f"  gpkg_label={raw_label}\n"
                    f"  codelist_label={hit.label}\n"
                    f"  codelist_files={hit.files}"
                )
            return raw_label, "existing_label", ""

        if codespace:
            basename = Path(codespace.split("#", 1)[0]).name.lower()
            hit = self.basename_index.get((basename, code))
            if hit:
                return hit.label, "codelist_basename", ";".join(hit.files)

        hit = self.semantic_index.get((risk_type, field, code))
        if hit:
            return hit.label, "codelist_global", ";".join(hit.files)

        return f"code:{code}", "unresolved", ""


def enrich_field(df: pd.DataFrame, registry: GlobalCodeRegistry, field: str):
    code_col = f"{field}_code"
    label_col = f"{field}_label"
    cs_col = f"{field}_codespace"

    if code_col not in df.columns:
        return

    if label_col not in df.columns:
        df[label_col] = ""
    if cs_col not in df.columns:
        df[cs_col] = ""

    labels = []
    statuses = []
    sources = []

    for _, row in df.iterrows():
        label, status, source = registry.resolve(
            txt(row.get("risk_type", "")),
            field,
            row.get(code_col, ""),
            row.get(label_col, ""),
            row.get(cs_col, ""),
        )
        labels.append(label)
        statuses.append(status)
        sources.append(source)

    df[f"{field}_label_hr"] = labels
    df[f"{field}_resolution_status"] = statuses
    df[f"{field}_resolution_sources"] = sources


def hr(row: pd.Series, field: str) -> str:
    for col in (f"{field}_label_hr", f"{field}_label"):
        v = txt(row.get(col, ""))
        if v:
            return v

    code = normalize_code(row.get(f"{field}_code", ""))
    return f"code:{code}" if code else ""


def risk_class(row: pd.Series) -> str:
    for field in ("rank", "rank_org", "area_type"):
        v = hr(row, field)
        if v:
            return v
    return ""


def make_risk_label(row: pd.Series) -> str:
    parts = []

    type_label = txt(row.get("risk_type_ja", "")) or txt(row.get("risk_type", ""))
    if type_label:
        parts.append(type_label)

    for col in ("hazard_source", "hazard_model", "risk_class"):
        v = txt(row.get(col, ""))
        if v:
            parts.append(v)

    if "depth_m" in row.index and pd.notna(row.get("depth_m")):
        try:
            parts.append(f"深さ {float(row.get('depth_m')):g} m")
        except Exception:
            pass

    if "duration_h" in row.index and pd.notna(row.get("duration_h")):
        try:
            parts.append(f"継続 {float(row.get('duration_h')):g} h")
        except Exception:
            pass

    return "｜".join(parts)


def register_attribute_table(con: sqlite3.Connection, name: str, rows: int):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    con.execute("DELETE FROM gpkg_contents WHERE table_name=?", (name,))
    con.execute(
        """
        INSERT INTO gpkg_contents
        (table_name,data_type,identifier,description,last_change,
         min_x,min_y,max_x,max_y,srs_id)
        VALUES (?, 'attributes', ?, ?, ?, NULL,NULL,NULL,NULL,NULL)
        """,
        (name, name, f"Heritage disaster-risk table ({rows} rows)", now),
    )


def write_attribute_table(gpkg: Path, name: str, df: pd.DataFrame):
    with sqlite3.connect(gpkg) as con:
        df.to_sql(name, con, if_exists="replace", index=False)
        register_attribute_table(con, name, len(df))
        con.commit()


def drop_spatial_layer_if_exists(gpkg: Path, layer: str):
    with sqlite3.connect(gpkg) as con:
        exists = con.execute(
            "SELECT 1 FROM gpkg_contents WHERE table_name=?",
            (layer,),
        ).fetchone()
        if not exists:
            return

        con.execute("DELETE FROM gpkg_geometry_columns WHERE table_name=?", (layer,))
        con.execute("DELETE FROM gpkg_contents WHERE table_name=?", (layer,))
        try:
            con.execute("DELETE FROM gpkg_extensions WHERE table_name=?", (layer,))
        except sqlite3.OperationalError:
            pass
        con.execute(f"DROP TABLE IF EXISTS {qident(layer)}")
        con.commit()


def main() -> int:
    a = parse_args()
    src = a.input.resolve()
    dst = a.output.resolve()

    if not src.exists():
        raise FileNotFoundError(src)
    if src == dst:
        raise ValueError("--output must differ from --input")
    if a.max_slots < 1:
        raise ValueError("--max-slots must be >= 1")

    required = {"heritage_buildings_footprint", "plateau_disaster_risk"}
    missing = sorted(required - set(sqlite_tables(src)))
    if missing:
        raise ValueError(f"input GPKG missing required layers/tables: {missing}")

    print("[1/8] Build global codelist registry")
    registry = GlobalCodeRegistry(a.codelist_root)
    registry.build()

    print(f"  XML files scanned       : {len(registry.scanned_files):,}")
    print(f"  relevant codelist files : {len(registry.relevant_files):,}")
    print(f"  semantic mappings       : {len(registry.semantic_index):,}")
    print("  code collisions         : 0")

    print("[2/8] Read Heritage GPKG")
    buildings = gpd.read_file(src, layer="heritage_buildings_footprint")
    risks = read_table(src, "plateau_disaster_risk")

    require_columns(buildings, ["gml_id"], "heritage_buildings_footprint")
    require_columns(
        risks,
        ["building_gml_id", "risk_index", "risk_type"],
        "plateau_disaster_risk",
    )

    original_count = len(buildings)
    buildings["gml_id"] = buildings["gml_id"].map(txt)
    risks["building_gml_id"] = risks["building_gml_id"].map(txt)

    print(f"  heritage building features: {original_count:,}")
    print(f"  raw disaster risk rows    : {len(risks):,}")

    print("[3/8] Keep risk rows linked to heritage Buildings")
    valid_ids = set(buildings["gml_id"])
    enriched = risks[risks["building_gml_id"].isin(valid_ids)].copy()

    info_cols = [
        c for c in [
            "gml_id",
            "municipality_code",
            "municipality_name",
            "building_id",
            "name",
            "address",
            "record_ids",
            "record_names",
            "record_types",
            "entity_classes",
            "designation_levels",
            "designation_statuses",
            "heritage_type_majors",
            "heritage_type_details",
        ]
        if c in buildings.columns
    ]

    info = buildings[info_cols].drop_duplicates("gml_id").rename(
        columns={"gml_id": "building_gml_id"}
    )

    enriched = enriched.merge(
        info,
        on="building_gml_id",
        how="left",
        suffixes=("", "_building"),
    )

    print(f"  heritage risk rows: {len(enriched):,}")

    print("[4/8] Resolve codes")
    for field in (
        "description",
        "scale",
        "admin_type",
        "rank",
        "rank_org",
        "area_type",
    ):
        enrich_field(enriched, registry, field)

    enriched["hazard_source"] = enriched.apply(lambda r: hr(r, "description"), axis=1)
    enriched["hazard_model"] = enriched.apply(lambda r: hr(r, "scale"), axis=1)
    enriched["hazard_admin_type"] = enriched.apply(lambda r: hr(r, "admin_type"), axis=1)
    enriched["risk_class"] = enriched.apply(risk_class, axis=1)

    enriched["hazard_source_key"] = enriched.apply(
        lambda r: " | ".join(
            p for p in (
                txt(r.get("risk_type", "")),
                txt(r.get("hazard_source", "")),
                txt(r.get("hazard_model", "")),
                txt(r.get("hazard_admin_type", "")),
            )
            if p
        ),
        axis=1,
    )

    enriched["risk_label"] = enriched.apply(make_risk_label, axis=1)

    status_cols = [c for c in enriched.columns if c.endswith("_resolution_status")]
    unresolved_mask = pd.Series(False, index=enriched.index)

    for c in status_cols:
        unresolved_mask |= enriched[c].astype(str).eq("unresolved")

    unresolved = enriched[unresolved_mask].copy()
    print(f"  unresolved risk rows: {len(unresolved):,}")

    if a.strict_unresolved and len(unresolved):
        raise RuntimeError(
            f"Unresolved codes remain: {len(unresolved)} rows. "
            "No output was written."
        )

    print("[5/8] Assign risk slots")
    enriched["_risk_order"] = (
        enriched["risk_type"].map(RISK_ORDER).fillna(999).astype(int)
    )

    sort_cols = ["building_gml_id", "_risk_order", "risk_type"]
    for c in (
        "hazard_source",
        "hazard_model",
        "hazard_admin_type",
        "risk_class",
        "depth_m",
        "duration_h",
        "risk_index",
    ):
        if c in enriched.columns:
            sort_cols.append(c)

    enriched = enriched.sort_values(
        sort_cols,
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)

    enriched["risk_slot"] = enriched.groupby("building_gml_id").cumcount() + 1

    max_observed = int(enriched["risk_slot"].max()) if len(enriched) else 0

    if max_observed > a.max_slots:
        bad = (
            enriched.groupby("building_gml_id")["risk_slot"].max()
            .loc[lambda s: s > a.max_slots]
            .sort_values(ascending=False)
        )
        preview = "\n".join(
            f"  {bid}: {n}" for bid, n in bad.head(20).items()
        )
        raise RuntimeError(
            f"Observed risk count exceeds --max-slots={a.max_slots}; "
            f"observed max={max_observed}. No data truncated.\n{preview}"
        )

    print(f"  max risk slots/building: {max_observed}")

    print("[6/8] Build riskwide Building layer")
    wide = buildings.copy()

    old_risk_cols = [
        c for c in wide.columns
        if (
            c == "disaster_risk_types"
            or c == "disaster_risks_json"
            or c.startswith("river_flood_")
            or c.startswith("tsunami_")
            or c.startswith("high_tide_")
            or c.startswith("inland_flood_")
            or c.startswith("reservoir_flood_")
            or c.startswith("landslide_")
            or re.match(r"^risk\d\d_", c)
        )
    ]

    wide = wide.drop(columns=old_risk_cols, errors="ignore")

    counts = (
        enriched.groupby("building_gml_id")
        .size()
        .rename("disaster_risk_count")
        .reset_index()
    )

    wide = wide.drop(columns=["disaster_risk_count"], errors="ignore").merge(
        counts,
        left_on="gml_id",
        right_on="building_gml_id",
        how="left",
    ).drop(columns=["building_gml_id"])

    wide["disaster_risk_count"] = wide["disaster_risk_count"].fillna(0).astype(int)

    slot_fields = [
        ("risk_type", "type"),
        ("risk_type_ja", "type_ja"),
        ("hazard_source", "source"),
        ("description_code", "source_code"),
        ("description_resolution_status", "source_status"),
        ("hazard_model", "model"),
        ("scale_code", "model_code"),
        ("scale_resolution_status", "model_status"),
        ("hazard_admin_type", "admin_type"),
        ("admin_type_code", "admin_code"),
        ("admin_type_resolution_status", "admin_status"),
        ("risk_class", "class"),
        ("rank_label_hr", "rank"),
        ("rank_code", "rank_code"),
        ("rank_org_label_hr", "rank_org"),
        ("rank_org_code", "rank_org_code"),
        ("depth_m", "depth_m"),
        ("duration_h", "duration_h"),
        ("area_type_label_hr", "area_type"),
        ("area_type_code", "area_type_code"),
        ("hazard_source_key", "source_key"),
        ("risk_label", "label"),
    ]

    for slot in range(1, a.max_slots + 1):
        sub = enriched[enriched["risk_slot"].eq(slot)].copy()
        keep = ["building_gml_id"]
        ren = {}

        for source_col, suffix in slot_fields:
            if source_col in sub.columns:
                keep.append(source_col)
                ren[source_col] = f"risk{slot:02d}_{suffix}"

        sub = sub[keep].rename(columns=ren)

        wide = wide.merge(
            sub,
            left_on="gml_id",
            right_on="building_gml_id",
            how="left",
        ).drop(columns=["building_gml_id"])

    if len(wide) != original_count:
        raise RuntimeError(
            f"Feature-count invariant failed: "
            f"original={original_count}, riskwide={len(wide)}"
        )

    if int(wide["disaster_risk_count"].sum()) != len(enriched):
        raise RuntimeError(
            f"Risk-count invariant failed: "
            f"wide sum={int(wide['disaster_risk_count'].sum())}, "
            f"long rows={len(enriched)}"
        )

    print(f"  riskwide features: {len(wide):,}")

    print("[7/8] Write integrated output GPKG")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()

    shutil.copy2(src, dst)

    drop_spatial_layer_if_exists(dst, "heritage_buildings_footprint_riskwide")

    wide.to_file(
        dst,
        layer="heritage_buildings_footprint_riskwide",
        driver="GPKG",
        engine="pyogrio",
    )

    enriched_out = enriched.drop(columns=["_risk_order"], errors="ignore")
    write_attribute_table(dst, "heritage_disaster_risk", enriched_out)

    unresolved_cols = [
        c for c in [
            "building_gml_id",
            "municipality_code",
            "risk_index",
            "risk_type",
            "description_code",
            "description_label",
            "description_codespace",
            "description_label_hr",
            "description_resolution_status",
            "scale_code",
            "scale_label",
            "scale_codespace",
            "scale_label_hr",
            "scale_resolution_status",
            "admin_type_code",
            "admin_type_label",
            "admin_type_codespace",
            "admin_type_label_hr",
            "admin_type_resolution_status",
            "rank_code",
            "rank_label",
            "rank_codespace",
            "rank_label_hr",
            "rank_resolution_status",
            "rank_org_code",
            "rank_org_label",
            "rank_org_codespace",
            "rank_org_label_hr",
            "rank_org_resolution_status",
            "area_type_code",
            "area_type_label",
            "area_type_codespace",
            "area_type_label_hr",
            "area_type_resolution_status",
            "source_gml",
        ]
        if c in unresolved.columns
    ]

    if len(unresolved):
        write_attribute_table(
            dst,
            "heritage_disaster_unresolved_codes",
            unresolved[unresolved_cols].copy(),
        )
    else:
        write_attribute_table(
            dst,
            "heritage_disaster_unresolved_codes",
            pd.DataFrame(
                columns=["building_gml_id", "risk_type", "code", "status"]
            ),
        )

    metadata = pd.DataFrame([{
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_gpkg": str(src),
        "output_gpkg": str(dst),
        "codelist_roots": json.dumps(
            [str(p.resolve()) for p in a.codelist_root],
            ensure_ascii=False,
        ),
        "uniqueness_assumption": (
            "Within Tokyo, same risk_type + semantic_field + code "
            "must map to one label."
        ),
        "collision_policy": (
            "Abort immediately before output if conflicting labels are detected."
        ),
        "codelist_xml_scanned": len(registry.scanned_files),
        "relevant_codelist_xml": len(registry.relevant_files),
        "semantic_mapping_count": len(registry.semantic_index),
        "building_features_original": original_count,
        "building_features_riskwide": len(wide),
        "risk_rows": len(enriched_out),
        "unresolved_risk_rows": len(unresolved),
        "max_slots_configured": a.max_slots,
        "max_slots_observed": max_observed,
        "feature_count_invariant": len(wide) == original_count,
        "risk_count_invariant": (
            int(wide["disaster_risk_count"].sum()) == len(enriched_out)
        ),
    }])

    write_attribute_table(dst, "heritage_disaster_metadata", metadata)

    print("[8/8] Final validation")
    written = gpd.read_file(
        dst,
        layer="heritage_buildings_footprint_riskwide",
    )

    if len(written) != original_count:
        raise RuntimeError(
            f"Written feature count mismatch: {len(written)} != {original_count}"
        )

    print()
    print("SUCCESS")
    print(f"  output                    : {dst}")
    print(f"  original Building features: {original_count:,}")
    print(f"  riskwide Building features: {len(written):,}")
    print(f"  heritage risk rows        : {len(enriched_out):,}")
    print(f"  max risks/building        : {max_observed}")
    print(f"  unresolved risk rows      : {len(unresolved):,}")
    print("  code collisions           : 0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CodeCollisionError as e:
        print("\nFATAL: CODE COLLISION DETECTED", file=sys.stderr)
        print(str(e), file=sys.stderr)
        print(
            "\nThe Tokyo-wide unique-code assumption is invalid. "
            "Processing stopped.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
