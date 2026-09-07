from __future__ import annotations

import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio

LIQUEFACTION_RISK_MIN_PL = 0.0
LANDSLIDE_PREFIXES = {
    "hazard_sediment_warning_a33_": "土砂災害警戒区域",
    "hazard_landslide_prevention_a46_": "地すべり防止区域",
    "hazard_steep_slope_a47_": "急傾斜地崩壊危険区域",
    "hazard_sabo_designated_a52_": "砂防指定地",
}
RISK_TYPE_JA = {
    "seismic": "想定震度",
    "fire": "延焼危険度",
    "liquefaction": "液状化",
    "river_flooding": "河川浸水",
    "high_tide": "高潮",
    "tsunami": "津波",
    "landslide": "土砂災害",
    "region_risk": "地域危険度",
}


def _ensure_wgs84(gdf):
    if gdf.crs is None:
        return gdf.set_crs(4326)
    if gdf.crs.to_epsg() != 4326:
        return gdf.to_crs(4326)
    return gdf


def _join(locations, polygons, value_cols):
    if locations.empty or polygons.empty:
        return pd.DataFrame()
    loc = _ensure_wgs84(locations)
    poly = _ensure_wgs84(polygons)
    cols = [c for c in value_cols if c in poly.columns] + ["geometry"]
    joined = gpd.sjoin(loc, poly[cols], how="inner", predicate="intersects")
    return pd.DataFrame(joined.drop(columns=["geometry", "index_right"], errors="ignore"))


def _liq_value_col(gdf):
    for col in ("liquefaction_pl", "PLcorrected", "Plcorrecte", "PL値", "pl", "PL"):
        if col in gdf.columns and pd.to_numeric(gdf[col], errors="coerce").notna().any():
            return col
    for col in gdf.columns:
        if col != "geometry" and "pl" in str(col).lower():
            if pd.to_numeric(gdf[col], errors="coerce").notna().any():
                return col
    return None


def _liq_class(v):
    if pd.isna(v):
        return "No data"
    x = float(v)
    if x <= 0:
        return "PL=0"
    if x <= 5:
        return "0<PL≤5"
    if x <= 15:
        return "5<PL≤15"
    return "PL>15"


def _base_meta_cols(meta):
    wanted = [
        "record_id", "municipality_code", "municipality_name",
        "designation_level", "designation_status", "heritage_type_major",
        "heritage_type_detail_norm", "entity_class", "name",
    ]
    return [c for c in wanted if c in meta.columns]


LIQUEFACTION_CLASS_ORDER = ["PL=0", "0<PL≤5", "5<PL≤15", "PL>15", "No data"]


def _liq_slug(text):
    text = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(text)).strip("_")
    return text or "liquefaction"


def _write_liquefaction_municipality_crosstab(rec, scenario, tables):
    """Write the canonical municipality × PL-class crosstab for one scenario.

    Contract:
      municipality_code, municipality_name,
      PL=0, 0<PL≤5, 5<PL≤15, PL>15, No data, Total

    Total excludes No data. Municipalities whose valid Total is zero are omitted.
    """
    wanted = ["municipality_code", "municipality_name", "record_id", "risk_class"]
    missing = [c for c in wanted if c not in rec.columns]
    if missing:
        raise RuntimeError(f"liquefaction crosstab missing columns: {missing}")

    tmp = rec[wanted].copy()
    tmp["municipality_code"] = (
        tmp["municipality_code"].fillna("").astype(str)
        .str.replace(r"\\.0$", "", regex=True).str.zfill(5)
    )
    tmp["municipality_name"] = tmp["municipality_name"].fillna("不明").astype(str)
    tmp["risk_class"] = tmp["risk_class"].fillna("No data").astype(str)
    tmp = tmp.drop_duplicates("record_id")

    tab = (
        tmp.groupby(["municipality_code", "municipality_name", "risk_class"], dropna=False, observed=True)
        .size().unstack("risk_class", fill_value=0).reset_index()
    )
    for c in LIQUEFACTION_CLASS_ORDER:
        if c not in tab.columns:
            tab[c] = 0
    tab = tab[["municipality_code", "municipality_name", *LIQUEFACTION_CLASS_ORDER]]
    valid_cols = [c for c in LIQUEFACTION_CLASS_ORDER if c != "No data"]
    tab["Total"] = tab[valid_cols].sum(axis=1)
    tab = tab[tab["Total"] > 0].copy()
    tab = tab.sort_values(["municipality_code", "municipality_name"], kind="stable")

    out = tables / f"liquefaction_{_liq_slug(scenario)}_municipality.csv"
    tab.to_csv(out, index=False, encoding="utf-8-sig")
    return out


def build_liquefaction_records(source, locations, meta, contents, tables):
    """Build complete liquefaction records and canonical crosstabs.

    Unlike the old implementation, the population for every scenario is the
    full non-movable cultural-property metadata table. A left join makes
    unassigned records explicit as risk_class='No data'. Crosstabs are written
    here, upstream of report generation; the HTML finalizer must never pivot
    the record-level CSV.
    """
    feature = contents[contents["data_type"].astype(str) == "features"]
    rows = feature[
        feature["table_name"].fillna("").astype(str).str.startswith("hazard_liquefaction")
    ]
    observed_parts = []
    scenarios = set()
    meta_cols = _base_meta_cols(meta)
    base_meta = meta[meta_cols].drop_duplicates("record_id").copy()

    if rows.empty:
        print("[liquefaction] WARNING: no hazard_liquefaction* feature layers")

    for _, info in rows.iterrows():
        layer = str(info["table_name"])
        print(f"[liquefaction] {layer}")
        hz = _ensure_wgs84(pyogrio.read_dataframe(source, layer=layer))
        if hz.empty:
            continue
        value_col = _liq_value_col(hz)
        if value_col is None:
            print(f"[liquefaction] WARNING: PL value column missing: {layer}; columns={list(hz.columns)}")
            continue
        hz = hz.copy()
        hz["_pl"] = pd.to_numeric(hz[value_col], errors="coerce")

        if "scenario" not in hz.columns:
            fallback = layer.replace("hazard_liquefaction_250m_", "").replace("hazard_liquefaction_", "")
            if fallback in ("", "250m", layer):
                fallback = "液状化"
            hz["scenario"] = fallback
        else:
            hz["scenario"] = hz["scenario"].fillna("").astype(str).str.strip()
            fallback = layer.replace("hazard_liquefaction_250m_", "").replace("hazard_liquefaction_", "")
            if fallback in ("", "250m", layer):
                fallback = "液状化"
            hz.loc[hz["scenario"] == "", "scenario"] = fallback

        scenarios.update(x for x in hz["scenario"].dropna().astype(str).str.strip().unique() if x)
        hz_valid = hz[hz["_pl"].notna()].copy()
        if hz_valid.empty:
            continue

        joined = _join(locations, hz_valid, ["scenario", "_pl"])
        if joined.empty:
            continue
        agg = (
            joined.groupby(["record_id", "scenario"], as_index=False)["_pl"].max()
            .rename(columns={"_pl": "risk_value"})
        )
        agg["hazard_source"] = layer
        observed_parts.append(agg)

    if observed_parts:
        observed = pd.concat(observed_parts, ignore_index=True)
        observed["risk_value"] = pd.to_numeric(observed["risk_value"], errors="coerce")
        observed = (
            observed.sort_values(["record_id", "scenario", "risk_value"])
            .drop_duplicates(["record_id", "scenario"], keep="last")
        )
        scenarios.update(x for x in observed["scenario"].dropna().astype(str).str.strip().unique() if x)
    else:
        observed = pd.DataFrame(columns=["record_id", "scenario", "risk_value", "hazard_source"])

    all_parts = []
    for scenario in sorted(scenarios):
        obs = observed[observed["scenario"].astype(str) == str(scenario)][
            ["record_id", "risk_value", "hazard_source"]
        ].copy()
        rec = base_meta.merge(obs, on="record_id", how="left")
        rec["scenario"] = scenario
        rec["risk_type"] = "liquefaction"
        rec["risk_type_ja"] = "液状化"
        rec["risk_class"] = rec["risk_value"].map(_liq_class)
        rec["hazard_source"] = rec["hazard_source"].fillna("")
        rec["risk_basis"] = np.where(
            rec["risk_value"].notna(),
            "liquefaction mesh polygon intersection",
            "No data",
        )

        rec.to_csv(
            tables / f"liquefaction_{_liq_slug(scenario)}_records.csv",
            index=False,
            encoding="utf-8-sig",
        )
        out_tab = _write_liquefaction_municipality_crosstab(rec, scenario, tables)
        print(f"[liquefaction] wrote crosstab: {out_tab.name}")
        all_parts.append(rec)

    if all_parts:
        all_records = pd.concat(all_parts, ignore_index=True)
    else:
        all_records = pd.DataFrame(columns=[
            *meta_cols, "risk_value", "hazard_source", "scenario",
            "risk_type", "risk_type_ja", "risk_class", "risk_basis",
        ])

    risk_records = all_records[
        pd.to_numeric(all_records.get("risk_value", pd.Series(dtype=float)), errors="coerce")
        > LIQUEFACTION_RISK_MIN_PL
    ].copy() if not all_records.empty else all_records.copy()

    all_records.to_csv(tables / "liquefaction_all_scenarios_records.csv", index=False, encoding="utf-8-sig")
    risk_records.to_csv(tables / "liquefaction_risk_records.csv", index=False, encoding="utf-8-sig")

    evaluated_pairs = int(all_records["risk_value"].notna().sum()) if not all_records.empty else 0
    print(
        f"[liquefaction] scenarios={len(scenarios):,}; evaluated record×scenario={evaluated_pairs:,}; "
        f"risk(PL>0) records={risk_records['record_id'].nunique() if not risk_records.empty else 0:,}"
    )
    return all_records, risk_records

def build_landslide_records(source, locations, meta, native, contents, tables):
    meta_cols = _base_meta_cols(meta)
    parts = []
    feature = contents[contents["data_type"].astype(str) == "features"]

    if native is not None and not native.empty and "risk_type" in native.columns:
        n = native[native["risk_type"].astype(str) == "landslide"].copy()
        if not n.empty:
            n["record_id"] = n["record_id"].astype(str)
            n["scenario"] = n.get("scenario", "").fillna("").astype(str).str.strip()
            n.loc[n["scenario"] == "", "scenario"] = "PLATEAU建築物リスク"
            n["risk_value"] = np.nan
            n["risk_class"] = n.get("risk_class", "")
            n["hazard_source"] = n.get("hazard_source", "PLATEAU")
            n["risk_basis"] = n.get("risk_basis", "PLATEAU building risk")
            rec = meta[meta_cols].drop_duplicates("record_id").merge(n[["record_id", "scenario", "risk_value", "risk_class", "hazard_source", "risk_basis"]].drop_duplicates(), on="record_id", how="inner")
            rec["risk_type"] = "landslide"
            rec["risk_type_ja"] = "土砂災害"
            parts.append(rec)

    for prefix, label in LANDSLIDE_PREFIXES.items():
        rows = feature[feature["table_name"].fillna("").astype(str).str.startswith(prefix)]
        for _, info in rows.iterrows():
            layer = str(info["table_name"])
            print(f"[landslide] {label}: {layer}")
            hz = _ensure_wgs84(pyogrio.read_dataframe(source, layer=layer))
            joined = _join(locations, hz, [])
            if joined.empty:
                continue
            ids = joined[["record_id"]].drop_duplicates()
            rec = meta[meta_cols].drop_duplicates("record_id").merge(ids, on="record_id", how="inner")
            rec["risk_type"] = "landslide"
            rec["risk_type_ja"] = "土砂災害"
            rec["scenario"] = label
            rec["risk_value"] = np.nan
            rec["risk_class"] = label
            rec["hazard_source"] = layer
            rec["risk_basis"] = "polygon intersection"
            parts.append(rec)

    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if not out.empty:
        out = out.drop_duplicates(["record_id", "scenario", "hazard_source"])
    out.to_csv(tables / "landslide_risk_records.csv", index=False, encoding="utf-8-sig")
    return out


def _std(df, risk_type=None, scenario_default="", value_col=None, class_col=None, source_col=None, basis_col=None):
    if df is None or df.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=df.index)
    out["record_id"] = df["record_id"].astype(str)
    out["risk_type"] = risk_type if risk_type is not None else df.get("risk_type", "").fillna("").astype(str)
    if "scenario" in df.columns:
        out["scenario"] = df["scenario"].fillna("").astype(str).str.strip()
    else:
        out["scenario"] = scenario_default
    out.loc[out["scenario"] == "", "scenario"] = scenario_default
    out["risk_value"] = pd.to_numeric(df[value_col], errors="coerce") if value_col and value_col in df.columns else np.nan
    out["risk_class"] = df[class_col].fillna("").astype(str) if class_col and class_col in df.columns else ""
    out["hazard_source"] = df[source_col].fillna("").astype(str) if source_col and source_col in df.columns else ""
    out["risk_basis"] = df[basis_col].fillna("").astype(str) if basis_col and basis_col in df.columns else ""
    return out.reset_index(drop=True)


def build_hazard_catalog(contents, tables):
    feature = contents[(contents["data_type"].astype(str) == "features") & contents["table_name"].fillna("").astype(str).str.startswith("hazard_")]
    rows = []
    for layer in feature["table_name"].astype(str):
        hazard_type = "unknown"
        status = "aggregated"
        note = ""
        if layer.startswith("hazard_seismic_"):
            hazard_type = "seismic"
        elif layer == "hazard_fire_spread_town":
            hazard_type = "fire"
        elif layer.startswith("hazard_fire_spread_"):
            hazard_type = "fire"; status = "alternate_representation"; note = "town layer is canonical summary source"
        elif layer.startswith("hazard_liquefaction"):
            hazard_type = "liquefaction"
        elif layer.startswith("hazard_inundation_"):
            hazard_type = "river_flooding"
        elif layer.startswith("hazard_storm_surge") or layer.startswith("hazard_high_tide"):
            hazard_type = "high_tide"
        elif layer.startswith("hazard_tsunami_"):
            hazard_type = "tsunami"
        elif any(layer.startswith(p) for p in LANDSLIDE_PREFIXES):
            hazard_type = "landslide"
        elif layer.startswith("hazard_region_risk"):
            hazard_type = "region_risk"; status = "available_not_aggregated"; note = "composite regional-risk dataset"
        else:
            status = "unrecognized"
        rows.append({"layer_name": layer, "hazard_type": hazard_type, "status": status, "note": note})
    catalog = pd.DataFrame(rows)
    unexpected = catalog[catalog["status"] != "aggregated"].copy() if not catalog.empty else catalog.copy()
    catalog.to_csv(tables / "hazard_catalog.csv", index=False, encoding="utf-8-sig")
    unexpected.to_csv(tables / "hazard_catalog_unexpected.csv", index=False, encoding="utf-8-sig")
    return catalog, unexpected


def _write_tables(long, meta, tables):
    risk = long[["record_id", "risk_type"]].drop_duplicates().merge(meta, on="record_id", how="left")
    risk.to_csv(tables / "record_risk_types.csv", index=False, encoding="utf-8-sig")
    for label, dim in {"municipality":"municipality_name", "designation_level":"designation_level", "designation_status":"designation_status", "cultural_type":"heritage_type_major"}.items():
        tab = pd.crosstab(risk[dim], risk["risk_type"], dropna=False).reset_index()
        tab["Total"] = tab.select_dtypes(include=[np.number]).sum(axis=1)
        tab.to_csv(tables / f"risk_type_by_{label}.csv", index=False, encoding="utf-8-sig")

    x = long.merge(meta, on="record_id", how="left")
    x["risk_type_ja"] = x["risk_type"].map(RISK_TYPE_JA).fillna(x["risk_type"])
    x["scenario_label"] = x["scenario"].fillna("").astype(str).str.strip()
    x.loc[x["scenario_label"] == "", "scenario_label"] = x["risk_type_ja"]
    x["hazard_key"] = x["risk_type_ja"] + "｜" + x["scenario_label"]
    x.to_csv(tables / "record_risk_scenarios.csv", index=False, encoding="utf-8-sig")

    muni = x.drop_duplicates(["municipality_name", "record_id", "hazard_key"]).pivot_table(index="municipality_name", columns="hazard_key", values="record_id", aggfunc="nunique", fill_value=0).reset_index()
    muni.to_csv(tables / "risk_scenario_by_municipality.csv", index=False, encoding="utf-8-sig")

    x["heritage_type_major"] = x["heritage_type_major"].fillna("").replace("", "未分類")
    type_tab = x.drop_duplicates(["heritage_type_major", "record_id", "hazard_key"]).pivot_table(index="heritage_type_major", columns="hazard_key", values="record_id", aggfunc="nunique", fill_value=0).reset_index()
    total = x.drop_duplicates(["record_id", "hazard_key"]).groupby("hazard_key")["record_id"].nunique().to_frame().T
    total.insert(0, "heritage_type_major", "TOTAL")
    pd.concat([type_tab, total], ignore_index=True).to_csv(tables / "risk_scenario_by_heritage_type.csv", index=False, encoding="utf-8-sig")

    city_root = tables.parent / "figures" / "city"
    for municipality, sub in x.groupby("municipality_name", dropna=False):
        municipality = str(municipality) if pd.notna(municipality) else "不明"
        city_dir = city_root / municipality
        city_dir.mkdir(parents=True, exist_ok=True)
        sub.to_csv(city_dir / "hazard_records_long.csv", index=False, encoding="utf-8-sig")
        p = sub.drop_duplicates(["heritage_type_major", "record_id", "hazard_key"]).pivot_table(index="heritage_type_major", columns="hazard_key", values="record_id", aggfunc="nunique", fill_value=0).reset_index()
        t = sub.drop_duplicates(["record_id", "hazard_key"]).groupby("hazard_key")["record_id"].nunique().to_frame().T
        t.insert(0, "heritage_type_major", "TOTAL")
        pd.concat([p, t], ignore_index=True).to_csv(city_dir / "hazard_counts_by_heritage_type.csv", index=False, encoding="utf-8-sig")
        t2 = t.rename(columns={"heritage_type_major":"metric"}).copy(); t2["metric"] = "TOTAL_RECORDS_WITH_RISK"
        t2.to_csv(city_dir / "hazard_counts_total.csv", index=False, encoding="utf-8-sig")
        cat = sub.groupby(["risk_type", "risk_type_ja", "scenario", "hazard_key"], dropna=False)["record_id"].nunique().reset_index(name="affected_record_count")
        cat.to_csv(city_dir / "hazard_catalog.csv", index=False, encoding="utf-8-sig")
    return risk


def build_complete_risk_summary(*, source, locations, meta, seismic, fire, native, external, a31a, contents, tables, metadata_dir):
    print("\n=== COMPLETE RISK SUMMARY ===")
    _, liq = build_liquefaction_records(source, locations, meta, contents, tables)
    landslide = build_landslide_records(source, locations, meta, native, contents, tables)
    parts = []
    if not seismic.empty:
        parts.append(_std(seismic[seismic["seismic_intensity"].notna()], risk_type="seismic", value_col="seismic_intensity", class_col="seismic_class"))
    if not fire.empty:
        parts.append(_std(fire[fire["fire_class"].notna()], risk_type="fire", scenario_default="延焼危険度", value_col="fire_class"))
    if not liq.empty:
        parts.append(_std(liq, risk_type="liquefaction", value_col="risk_value", class_col="risk_class", source_col="hazard_source", basis_col="risk_basis"))
    if native is not None and not native.empty:
        parts.append(_std(native, risk_type=None, scenario_default="PLATEAU建築物リスク", value_col="depth_m", class_col="risk_class", source_col="hazard_source", basis_col="risk_basis"))
    if external is not None and not external.empty:
        parts.append(_std(external, risk_type=None, value_col="depth_m", class_col="risk_class", source_col="hazard_source", basis_col="risk_basis"))
    if a31a is not None and not a31a.empty:
        parts.append(_std(a31a, risk_type="river_flooding", scenario_default="想定最大規模", value_col="depth_max_m", class_col="depth_class", source_col="hazard_source", basis_col="risk_basis"))
    if not landslide.empty:
        parts.append(_std(landslide, risk_type="landslide", value_col="risk_value", class_col="risk_class", source_col="hazard_source", basis_col="risk_basis"))
    parts = [p for p in parts if p is not None and not p.empty]
    long = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["record_id","risk_type","scenario","risk_value","risk_class","hazard_source","risk_basis"])
    long["record_id"] = long["record_id"].astype(str)
    long = long.drop_duplicates(["record_id", "risk_type", "scenario", "hazard_source"])
    risk = _write_tables(long, meta, tables)
    catalog, unexpected = build_hazard_catalog(contents, tables)
    city_root = tables.parent / "figures" / "city"
    if city_root.exists():
        for city_dir in [p for p in city_root.iterdir() if p.is_dir()]:
            catalog.to_csv(city_dir / "hazard_catalog_all_source_layers.csv", index=False, encoding="utf-8-sig")
            unexpected.to_csv(city_dir / "hazard_catalog_unexpected.csv", index=False, encoding="utf-8-sig")
    print("[complete risk] risk-type counts:")
    if not risk.empty:
        print(risk["risk_type"].value_counts().to_string())
    print(f"[complete risk] scenario rows={len(long):,}; audit rows={len(unexpected):,}")
    return risk
