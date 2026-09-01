from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
import shutil
import sqlite3

import pandas as pd
from lxml import etree

from .citygml import GML_NS, GEN2_NS, GEN3_NS, localname


CLASS_COLS = [
    "designation_level_code", "designation_level_ja",
    "designation_status_code", "designation_status_ja",
    "heritage_type_major_code", "heritage_type_major_ja",
    "heritage_type_detail", "classification_confidence",
]
AGG_COLS = [
    "designation_levels", "designation_statuses",
    "heritage_type_majors", "heritage_type_details",
]


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    return str(v).strip()


def _norm(v) -> str:
    return re.sub(r"\s+", "", _text(v).replace("　", " "))


def _read_csv(path: str | Path) -> pd.DataFrame:
    last = None
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False)
        except Exception as e:
            last = e
    raise ValueError(f"CSV decode failed: {path}: {last}")


def _first(row, names) -> str:
    for n in names:
        if n in row and _text(row[n]):
            return _text(row[n])
    return ""


def _csv_record(row) -> dict:
    return {
        "record_id": _first(row, ["record_id", "source_record_id", "NO", "No", "ID", "id", "文化財ID", "管理番号", "番号"]),
        "name": _first(row, ["name", "名称", "文化財名称", "文化財名"]),
        "owner": _first(row, ["owner", "所有者等", "所有者", "管理者"]),
        "category": _first(row, ["category", "文化財分類", "指定区分", "分類", "カテゴリ"]),
        "type": _first(row, ["type", "種類", "種別", "文化財種類", "ジャンル"]),
        "municipality_code": _first(row, ["municipality_code", "市区町村コード", "自治体コード", "全国地方公共団体コード"]),
        **{c: _first(row, [c]) for c in CLASS_COLS},
    }


def _classification_index(paths) -> list[dict]:
    rows = []
    for p in paths:
        df = _read_csv(p)
        missing = [c for c in CLASS_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"classified CSV is missing columns {missing}: {p}")
        rows.extend(_csv_record(r) for r in df.to_dict(orient="records"))
    return rows


def _norm_id(v) -> str:
    s = _norm(v)
    if re.fullmatch(r"\d+", s):
        return str(int(s))
    return s


def _candidate_keys(r: dict):
    rid = _norm_id(r.get("record_id"))
    name, owner, cat, typ, mcode = map(_norm, [
        r.get("name"), r.get("owner"), r.get("category"), r.get("type"), r.get("municipality_code")
    ])
    return [
        ("id_name_cat", rid, name, cat) if rid and name else None,
        ("name_owner_cat_type_muni", name, owner, cat, typ, mcode) if name and owner and mcode else None,
        ("name_owner_cat_type", name, owner, cat, typ) if name and owner else None,
        ("name_cat_type_muni", name, cat, typ, mcode) if name and mcode else None,
        ("name_cat_type", name, cat, typ) if name else None,
        ("id_muni", rid, mcode) if rid and mcode else None,
    ]


def _make_lookup(classified: list[dict]):
    tmp = defaultdict(list)
    for r in classified:
        for k in _candidate_keys(r):
            if k:
                tmp[k].append(r)
    return {k: v[0] for k, v in tmp.items() if len(v) == 1}


def _find_classification(row: dict, lookup: dict):
    for k in _candidate_keys(row):
        if k and k in lookup:
            return lookup[k]
    return None


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _ensure_columns(conn, table, columns):
    existing = _columns(conn, table)
    for c in columns:
        if c not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}" TEXT')


def _select_rows(conn, table):
    cols = _columns(conn, table)
    wanted = [c for c in [
        "record_id", "entity_id", "name", "owner", "category", "type",
        "municipality_code", "complex_id", "record_ids", "building_gml_id"
    ] if c in cols]
    sql = f'SELECT rowid, {", ".join([f"\"{c}\"" for c in wanted])} FROM "{table}"'
    rows = []
    for vals in conn.execute(sql):
        d = {"rowid": vals[0]}
        d.update(dict(zip(wanted, vals[1:])))
        if "entity_id" in d and "record_id" not in d:
            d["record_id"] = d.get("entity_id", "")
        rows.append(d)
    return rows


def _update_row(conn, table, rowid, values):
    vals = {k: _text(v) for k, v in values.items() if k in CLASS_COLS or k in AGG_COLS}
    if not vals:
        return
    assignments = ", ".join(f'"{k}"=?' for k in vals)
    conn.execute(f'UPDATE "{table}" SET {assignments} WHERE rowid=?', [*vals.values(), rowid])


def _uniq(values):
    out=[]
    for v in values:
        v=_text(v)
        if v and v not in out:
            out.append(v)
    return out


def _aggregate(records):
    return {
        "designation_levels": ";".join(_uniq(r.get("designation_level_code") for r in records)),
        "designation_statuses": ";".join(_uniq(r.get("designation_status_code") for r in records)),
        "heritage_type_majors": ";".join(_uniq(r.get("heritage_type_major_code") for r in records)),
        "heritage_type_details": ";".join(_uniq(r.get("heritage_type_detail") for r in records)),
    }


def patch_gpkg(gpkg_path: str | Path, classified_csvs, output_path: str | Path | None = None, in_place: bool = False):
    src = Path(gpkg_path)
    if in_place:
        dst = src
    else:
        dst = Path(output_path) if output_path else src.with_name(src.stem + "_classified" + src.suffix)
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)

    classified = _classification_index(classified_csvs)
    lookup = _make_lookup(classified)
    conn = sqlite3.connect(dst)
    stats = {"gpkg": str(dst), "heritage_records_matched": 0, "heritage_records_unmatched": 0}
    try:
        tables = _tables(conn)
        record_cache=[]
        by_id=defaultdict(list)
        by_complex=defaultdict(list)

        if "heritage_records" in tables:
            _ensure_columns(conn, "heritage_records", CLASS_COLS)
            for row in _select_rows(conn, "heritage_records"):
                match = _find_classification(row, lookup)
                if match:
                    vals={c: match.get(c, "") for c in CLASS_COLS}
                    _update_row(conn, "heritage_records", row["rowid"], vals)
                    full={**row, **vals}
                    stats["heritage_records_matched"] += 1
                else:
                    full={**row, **{c:"" for c in CLASS_COLS}}
                    stats["heritage_records_unmatched"] += 1
                record_cache.append(full)
                if _text(full.get("record_id")):
                    by_id[_text(full["record_id"])].append(full)
                if _text(full.get("complex_id")):
                    by_complex[_text(full["complex_id"])].append(full)

        # Record-level relational/spatial tables.
        for table in ["heritage_building_links", "heritage_complex_records", "heritage_points", "heritage_unresolved_entities"]:
            if table not in tables:
                continue
            _ensure_columns(conn, table, CLASS_COLS)
            updated=0
            for row in _select_rows(conn, table):
                candidates=by_id.get(_text(row.get("record_id")), [])
                if len(candidates) > 1 and _text(row.get("name")):
                    named=[r for r in candidates if _norm(r.get("name")) == _norm(row.get("name"))]
                    candidates=named or candidates
                if candidates:
                    vals={c: candidates[0].get(c, "") for c in CLASS_COLS}
                    _update_row(conn, table, row["rowid"], vals)
                    updated += 1
            stats[f"{table}_updated"] = updated

        # Aggregate tables/layers.
        for table in ["heritage_complex_summary", "heritage_building_complexes"]:
            if table not in tables:
                continue
            _ensure_columns(conn, table, AGG_COLS)
            updated=0
            for row in _select_rows(conn, table):
                recs=by_complex.get(_text(row.get("complex_id")), [])
                if recs:
                    _update_row(conn, table, row["rowid"], _aggregate(recs)); updated+=1
            stats[f"{table}_updated"] = updated

        for table in ["heritage_complex_members", "heritage_buildings_footprint"]:
            if table not in tables:
                continue
            _ensure_columns(conn, table, AGG_COLS)
            updated=0
            for row in _select_rows(conn, table):
                ids=[x for x in _text(row.get("record_ids")).split(";") if x]
                recs=[]
                for rid in ids:
                    recs.extend(by_id.get(rid, []))
                if recs:
                    _update_row(conn, table, row["rowid"], _aggregate(recs)); updated+=1
            stats[f"{table}_updated"] = updated

        conn.commit()
    finally:
        conn.close()
    return stats


def _building_aggregates_from_gpkg(gpkg_path: str | Path):
    conn=sqlite3.connect(gpkg_path)
    try:
        if "heritage_building_links" not in _tables(conn):
            return {}
        cols=_columns(conn,"heritage_building_links")
        needed={"building_gml_id", *CLASS_COLS}
        if not needed.issubset(cols):
            return {}
        rows=[]
        for vals in conn.execute(
            'SELECT building_gml_id, designation_level_code, designation_level_ja, designation_status_code, designation_status_ja, heritage_type_major_code, heritage_type_major_ja, heritage_type_detail FROM heritage_building_links'
        ):
            rows.append(dict(zip([
                "building_gml_id","designation_level_code","designation_level_ja","designation_status_code","designation_status_ja","heritage_type_major_code","heritage_type_major_ja","heritage_type_detail"
            ], vals)))
        by=defaultdict(list)
        for r in rows:
            by[_text(r["building_gml_id"])].append(r)
        out={}
        for gid, rr in by.items():
            out[gid]={
                "heritageDesignationLevels":";".join(_uniq(r["designation_level_code"] for r in rr)),
                "heritageDesignationLevelLabels":";".join(_uniq(r["designation_level_ja"] for r in rr)),
                "heritageDesignationStatuses":";".join(_uniq(r["designation_status_code"] for r in rr)),
                "heritageDesignationStatusLabels":";".join(_uniq(r["designation_status_ja"] for r in rr)),
                "heritageTypeMajors":";".join(_uniq(r["heritage_type_major_code"] for r in rr)),
                "heritageTypeMajorLabels":";".join(_uniq(r["heritage_type_major_ja"] for r in rr)),
                "heritageTypeDetails":";".join(_uniq(r["heritage_type_detail"] for r in rr)),
            }
        return out
    finally:
        conn.close()


def _find_building(element):
    for e in element.iter():
        if localname(e.tag) == "Building":
            return e
    return None


def _set_generic(building, name, value, gen_ns):
    # Idempotent: remove prior attribute with same name, then add current value.
    for child in list(building):
        if localname(child.tag) == "stringAttribute" and child.get("name") == name:
            building.remove(child)
    if not value:
        return
    node=etree.SubElement(building, f"{{{gen_ns}}}stringAttribute")
    node.set("name",name)
    v=etree.SubElement(node,f"{{{gen_ns}}}value")
    v.text=value


def patch_gml(gml_path: str | Path, gpkg_path: str | Path, output_path: str | Path | None = None, in_place: bool = False):
    src=Path(gml_path)
    dst=src if in_place else (Path(output_path) if output_path else src.with_name(src.stem+"_classified"+src.suffix))
    aggs=_building_aggregates_from_gpkg(gpkg_path)
    parser=etree.XMLParser(huge_tree=True, recover=False, remove_blank_text=False)
    tree=etree.parse(str(src),parser)
    root=tree.getroot()
    root_ns=root.tag.split("}",1)[0].lstrip("{") if "}" in root.tag else ""
    gen_ns=GEN3_NS if root_ns.endswith("/3.0") else GEN2_NS
    updated=0
    for member in root.iter():
        if localname(member.tag) != "cityObjectMember":
            continue
        b=_find_building(member)
        if b is None:
            continue
        gid=b.get(f"{{{GML_NS}}}id") or b.get("id") or ""
        vals=aggs.get(gid)
        if not vals:
            continue
        for name,value in vals.items():
            _set_generic(b,name,value,gen_ns)
        updated+=1
    target=dst
    if in_place:
        target=src.with_suffix(src.suffix+".tmp")
    tree.write(str(target),encoding="UTF-8",xml_declaration=True,pretty_print=False)
    if in_place:
        target.replace(src)
    return {"gml":str(dst),"buildings_updated":updated}
