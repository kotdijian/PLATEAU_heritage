#!/usr/bin/env python3
"""Read-only profiler for Summary Results generation."""
from __future__ import annotations
import argparse, json, sqlite3
from pathlib import Path
import pandas as pd
import pyogrio

RELEVANT_PREFIXES = (
    "heritage_", "hazard_region_risk", "hazard_fire_spread",
    "hazard_inundation_", "hazard_storm_surge_", "hazard_tsunami_",
    "hazard_seismic_50m_",
)
RELEVANT_EXACT = {"source_license", "hazard_source_manifest", "admin_boundary_n03_2024"}
CATEGORICAL_HINTS = (
    "municip", "city", "ward", "area_code", "municipality", "designation",
    "register", "level", "category", "type", "class", "kind", "heritage",
    "cultural", "scenario", "risk", "hazard",
)

def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

def table_columns(con, table):
    return [
        {"cid": r[0], "name": r[1], "sqlite_type": r[2], "notnull": r[3], "default": r[4], "pk": r[5]}
        for r in con.execute(f"PRAGMA table_info({qident(table)})").fetchall()
    ]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path("summary_profile"))
    args = ap.parse_args()
    source = args.source.expanduser().resolve()
    out = args.out_dir.expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"ERROR: source not found: {source}")
    out.mkdir(parents=True, exist_ok=True)

    samples, categorical, column_rows = {}, {}, []
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=120) as con:
        qc = con.execute("PRAGMA quick_check").fetchone()[0]
        contents = pd.read_sql_query(
            """SELECT table_name, data_type, identifier, description,
                      min_x, min_y, max_x, max_y, srs_id
               FROM gpkg_contents ORDER BY table_name""", con)
        contents.to_csv(out / "layer_inventory.csv", index=False, encoding="utf-8-sig")
        names = contents["table_name"].tolist()
        relevant = [t for t in names if t in RELEVANT_EXACT or t.startswith(RELEVANT_PREFIXES)]

        geom_map = {}
        try:
            for t, c in con.execute("SELECT table_name, column_name FROM gpkg_geometry_columns"):
                geom_map.setdefault(t, set()).add(c)
        except sqlite3.Error:
            pass

        for table in relevant:
            cols = table_columns(con, table)
            for c in cols:
                column_rows.append({"table": table, **c})
            geom_cols = geom_map.get(table, set())
            sample_cols = [c["name"] for c in cols if c["name"] not in geom_cols][:40]
            if sample_cols:
                try:
                    sql = "SELECT " + ", ".join(qident(c) for c in sample_cols) + f" FROM {qident(table)} LIMIT 5"
                    sdf = pd.read_sql_query(sql, con)
                    samples[table] = sdf.where(pd.notna(sdf), None).to_dict(orient="records")
                except Exception as e:
                    samples[table] = {"ERROR": str(e)}

            for c in sample_cols:
                if not any(h in c.lower() for h in CATEGORICAL_HINTS):
                    continue
                try:
                    n = con.execute(f"SELECT COUNT(DISTINCT {qident(c)}) FROM {qident(table)}").fetchone()[0]
                    if n is not None and n <= 100:
                        vals = [r[0] for r in con.execute(
                            f"SELECT DISTINCT {qident(c)} FROM {qident(table)} "
                            f"WHERE {qident(c)} IS NOT NULL ORDER BY {qident(c)} LIMIT 100"
                        ).fetchall()]
                        categorical[f"{table}.{c}"] = vals
                except Exception:
                    pass

        pd.DataFrame(column_rows).to_csv(out / "layer_columns.csv", index=False, encoding="utf-8-sig")
        for table, filename in [("source_license", "source_license.csv"),
                                ("hazard_source_manifest", "hazard_source_manifest.csv")]:
            if table in names:
                pd.read_sql_query(f"SELECT * FROM {qident(table)}", con).to_csv(
                    out / filename, index=False, encoding="utf-8-sig")

    (out / "relevant_layer_samples.json").write_text(
        json.dumps(samples, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "categorical_values.json").write_text(
        json.dumps(categorical, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    try:
        pd.DataFrame(pyogrio.list_layers(source), columns=["layer", "geometry_type"]).to_csv(
            out / "spatial_layers.csv", index=False, encoding="utf-8-sig")
    except Exception as e:
        pd.DataFrame([{"ERROR": str(e)}]).to_csv(out / "spatial_layers.csv", index=False, encoding="utf-8-sig")

    summary = [
        f"source={source}", f"size_bytes={source.stat().st_size}", f"quick_check={qc}",
        f"gpkg_contents={len(contents)}", f"relevant_tables={len(relevant)}", "",
        "[relevant_tables]", *relevant, "", "SUCCESS"
    ]
    (out / "profile_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))
    print("output:", out)

if __name__ == "__main__":
    main()
