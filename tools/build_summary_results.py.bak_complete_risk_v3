#!/usr/bin/env python3
"""
Build tabular Summary Results from 13_heritage_hazards.gpkg.

The script is read-only with respect to the source GeoPackage.
It generates record-based cultural-property statistics and disaster-risk
cross-tabulations for all non-movable cultural-property records. Movable
records are exported separately.

Primary output:
  summary_results/
    tables/
    cache/
    metadata/

A companion renderer, render_summary_maps.py, creates overview maps from
the same source GPKG and these generated tables. This builder never writes
or removes files under summary_results/figures/.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio


SCENARIOS = [
    "都心南部直下地震",
    "都心東部直下地震",
    "都心西部直下地震",
    "大正関東地震",
    "南海トラフ巨大地震",
]

DESIGNATION_LEVEL_MAP = {
    "national": "National",
    "prefectural": "Tokyo Metropolitan",
    "municipal": "Municipal",
    "unknown": "Unknown",
    "": "Unknown",
}

DESIGNATION_STATUS_MAP = {
    "designated": "Designated",
    "registered": "Registered",
    "selected": "Other",
    "record_selected": "Other",
    "local_other": "Other",
    "unknown": "Unknown",
    "": "Unknown",
}

WATER_RISK_TYPES = {"river_flooding", "high_tide", "tsunami"}
POINT_GRID_MAX_DISTANCE_M = 25.0
A31A_PREFIX = "hazard_inundation_a31a_"
A31A_DEPTH_ORDER = ["0–0.5 m", "0.5–3 m", "3–5 m", "5 m以上"]
A31A_DEPTH_RANK = {label: i + 1 for i, label in enumerate(A31A_DEPTH_ORDER)}


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def slug(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(text)).strip("_")
    return text or "unnamed"


def read_sql_table(source: Path, table: str, columns: Iterable[str] | None = None) -> pd.DataFrame:
    cols = "*" if columns is None else ", ".join(qident(c) for c in columns)
    uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=180) as con:
        return pd.read_sql_query(f"SELECT {cols} FROM {qident(table)}", con)


def gpkg_contents(source: Path) -> pd.DataFrame:
    uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=180) as con:
        return pd.read_sql_query(
            """
            SELECT table_name, data_type, min_x, min_y, max_x, max_y, srs_id
            FROM gpkg_contents
            ORDER BY table_name
            """,
            con,
        )


def ensure_wgs84(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)
    return gdf


def classify_seismic(value) -> str:
    if pd.isna(value):
        return "No data"
    x = float(value)
    if x < 4.5:
        return "5弱未満"
    if x < 5.0:
        return "5弱"
    if x < 5.5:
        return "5強"
    if x < 6.0:
        return "6弱"
    return "6強以上"


def classify_depth(value) -> str:
    if pd.isna(value):
        return "No data"
    x = float(value)
    if x <= 0:
        return "0"
    if x < 0.5:
        return "0–0.5 m"
    if x < 3.0:
        return "0.5–3 m"
    if x < 5.0:
        return "3–5 m"
    return "5 m以上"


def normalize_records(records: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    records = records.copy()
    records["designation_level"] = (
        records["designation_level_code"].fillna("").astype(str).map(DESIGNATION_LEVEL_MAP).fillna("Unknown")
    )
    records["designation_status"] = (
        records["designation_status_code"].fillna("").astype(str).map(DESIGNATION_STATUS_MAP).fillna("Other")
    )
    records["heritage_type_major"] = records["heritage_type_major_ja"].fillna("未判定").replace("", "未判定")
    records["heritage_type_detail_norm"] = records["heritage_type_detail"].fillna("未細分").replace("", "未細分")
    records["municipality_code"] = records["municipality_code"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5)
    records["municipality_name"] = records["municipality_name"].fillna("不明")
    records["record_id"] = records["record_id"].astype(str)
    records["entity_class"] = records["entity_class"].fillna("unknown")
    return records


def explode_building_points(source: Path, records: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    cols = ["record_ids", "geometry"]
    bp = pyogrio.read_dataframe(source, layer="heritage_buildings_point", columns=["record_ids"])
    bp = ensure_wgs84(bp)
    bp = bp[bp["record_ids"].notna() & (bp["record_ids"].astype(str).str.len() > 0)].copy()
    bp["record_id"] = bp["record_ids"].astype(str).str.split(";")
    bp = bp.explode("record_id", ignore_index=True)
    bp["record_id"] = bp["record_id"].astype(str).str.strip()
    bp = bp[bp["record_id"].isin(set(records["record_id"]))]
    return bp[["record_id", "geometry"]]


def build_analysis_locations(source: Path, immovable: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Use every matched PLATEAU building point; otherwise use the record point."""
    bp = explode_building_points(source, immovable)
    building_ids = set(bp["record_id"])

    attrs = [
        "record_id", "municipality_code", "municipality_name", "designation_level",
        "designation_status", "heritage_type_major", "heritage_type_detail_norm",
        "entity_class", "name",
    ]
    bp = bp.merge(immovable[attrs], on="record_id", how="left")
    bp["location_basis"] = "PLATEAU building representative point"

    rp = immovable[~immovable["record_id"].isin(building_ids)].copy()
    rp = rp[rp.geometry.notna() & ~rp.geometry.is_empty]
    rp = rp[attrs + ["geometry"]]
    rp["location_basis"] = "cultural-property record point"

    loc = pd.concat([bp, rp], ignore_index=True)
    loc = gpd.GeoDataFrame(loc, geometry="geometry", crs="EPSG:4326")
    loc = loc.drop_duplicates(subset=["record_id", "location_basis", "geometry"])
    return loc


def crosstab_count(df: pd.DataFrame, row: str, col: str, value_order: list[str] | None = None) -> pd.DataFrame:
    tab = pd.crosstab(df[row], df[col], dropna=False)
    if value_order:
        for c in value_order:
            if c not in tab.columns:
                tab[c] = 0
        remaining = [c for c in tab.columns if c not in value_order]
        tab = tab[value_order + remaining]
    tab["Total"] = tab.sum(axis=1)
    return tab.reset_index()


def write_basic_tables(immovable: gpd.GeoDataFrame, movable: gpd.GeoDataFrame, tables: Path) -> None:
    by_muni = (
        immovable.groupby(["municipality_code", "municipality_name"], dropna=False)
        .size().rename("record_count").reset_index().sort_values(["municipality_code", "municipality_name"])
    )
    by_muni.to_csv(tables / "municipality_record_counts.csv", index=False, encoding="utf-8-sig")

    for col, name in [
        ("designation_level", "municipality_designation_level.csv"),
        ("designation_status", "municipality_designation_status.csv"),
        ("heritage_type_major", "municipality_cultural_type_major.csv"),
        ("heritage_type_detail_norm", "municipality_cultural_type_detail.csv"),
    ]:
        tab = pd.crosstab(
            [immovable["municipality_code"], immovable["municipality_name"]],
            immovable[col],
            dropna=False,
        ).reset_index()
        tab["Total"] = tab.select_dtypes(include=[np.number]).sum(axis=1)
        tab.to_csv(tables / name, index=False, encoding="utf-8-sig")

    movable_cols = [
        c for c in [
            "municipality_code", "municipality_name", "record_id", "name", "category", "type",
            "designation_level_code", "designation_level_ja", "designation_status_code",
            "designation_status_ja", "heritage_type_major_code", "heritage_type_major_ja",
            "heritage_type_detail", "owner", "address", "source_file",
        ] if c in movable.columns
    ]
    movable[movable_cols].to_csv(tables / "movable_cultural_properties.csv", index=False, encoding="utf-8-sig")


def spatial_join_polygons(locations: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame, value_cols: list[str]) -> pd.DataFrame:
    if locations.empty or polygons.empty:
        return pd.DataFrame(columns=list(locations.columns) + value_cols)
    locations = ensure_wgs84(locations)
    polygons = ensure_wgs84(polygons)
    cols = [c for c in value_cols if c in polygons.columns] + ["geometry"]
    joined = gpd.sjoin(locations, polygons[cols], how="inner", predicate="intersects")
    return pd.DataFrame(joined.drop(columns=["geometry", "index_right"], errors="ignore"))


def seismic_results(source: Path, locations: gpd.GeoDataFrame, meta: pd.DataFrame, tables: Path) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    all_rows = []
    by_scenario = {}
    class_order = ["5弱未満", "5弱", "5強", "6弱", "6強以上", "No data"]

    for scenario in SCENARIOS:
        layer = f"hazard_seismic_50m_{scenario}"
        print(f"[seismic] {scenario}")
        hz = pyogrio.read_dataframe(source, layer=layer, columns=["seismic_intensity"])
        hz = ensure_wgs84(hz)
        joined = spatial_join_polygons(locations, hz, ["seismic_intensity"])
        if joined.empty:
            rec = pd.DataFrame(columns=["record_id", "seismic_intensity"])
        else:
            rec = joined.groupby("record_id", as_index=False)["seismic_intensity"].max()
        rec = meta.merge(rec, on="record_id", how="left")
        rec["scenario"] = scenario
        rec["seismic_class"] = rec["seismic_intensity"].map(classify_seismic)
        rec.to_csv(tables / f"seismic_{slug(scenario)}_records.csv", index=False, encoding="utf-8-sig")

        dimensions = {
            "municipality": "municipality_name",
            "designation_level": "designation_level",
            "designation_status": "designation_status",
            "cultural_type": "heritage_type_major",
        }
        for label, dim in dimensions.items():
            tab = crosstab_count(rec, dim, "seismic_class", class_order)
            tab.to_csv(
                tables / f"seismic_{slug(scenario)}_{label}.csv",
                index=False,
                encoding="utf-8-sig",
            )
        all_rows.append(rec)
        by_scenario[scenario] = rec
        del hz, joined

    combined = pd.concat(all_rows, ignore_index=True)
    combined.to_csv(tables / "seismic_all_scenarios_records.csv", index=False, encoding="utf-8-sig")
    return combined, by_scenario


def detect_fire_value_column(source: Path, layer: str) -> str:
    info = pyogrio.read_info(source, layer=layer)
    fields = list(info["fields"])
    for f in fields:
        if str(f).startswith("T360mm_") or "360mm" in str(f):
            return str(f)
    sample = pyogrio.read_dataframe(source, layer=layer, max_features=20)
    candidates = [
        c for c in sample.columns
        if c != "geometry" and c.lower() != "fid" and pd.api.types.is_numeric_dtype(sample[c])
    ]
    if not candidates:
        raise RuntimeError(f"Could not detect fire-risk value column in {layer}")
    return candidates[0]


def fire_results(source: Path, locations: gpd.GeoDataFrame, meta: pd.DataFrame, tables: Path) -> pd.DataFrame:
    layer = "hazard_fire_spread_town"
    value_col = detect_fire_value_column(source, layer)
    print(f"[fire] value column: {value_col!r}")
    hz = pyogrio.read_dataframe(source, layer=layer, columns=[value_col])
    hz = ensure_wgs84(hz)
    joined = spatial_join_polygons(locations, hz, [value_col])
    if joined.empty:
        rec = meta.copy()
        rec["fire_class"] = np.nan
    else:
        agg = joined.groupby("record_id", as_index=False)[value_col].max().rename(columns={value_col: "fire_class"})
        rec = meta.merge(agg, on="record_id", how="left")
    rec.to_csv(tables / "fire_spread_records.csv", index=False, encoding="utf-8-sig")
    for label, dim in {
        "municipality": "municipality_name",
        "designation_level": "designation_level",
        "designation_status": "designation_status",
        "cultural_type": "heritage_type_major",
    }.items():
        tmp = rec.copy()
        tmp["fire_class_label"] = tmp["fire_class"].map(lambda x: "No data" if pd.isna(x) else str(int(x)) if float(x).is_integer() else str(x))
        crosstab_count(tmp, dim, "fire_class_label").to_csv(
            tables / f"fire_spread_{label}.csv", index=False, encoding="utf-8-sig"
        )
    return rec


def native_plateau_risks(source: Path, valid_record_ids: set[str]) -> pd.DataFrame:
    cols = [
        "record_ids", "risk_type", "risk_type_ja", "depth_m", "hazard_source",
        "hazard_model", "risk_class", "municipality_name",
    ]
    df = read_sql_table(source, "heritage_disaster_risk", cols)
    df = df[df["record_ids"].notna() & (df["record_ids"].astype(str).str.len() > 0)].copy()
    df["record_id"] = df["record_ids"].astype(str).str.split(";")
    df = df.explode("record_id", ignore_index=True)
    df["record_id"] = df["record_id"].astype(str).str.strip()
    df = df[df["record_id"].isin(valid_record_ids)]
    df["scenario"] = df["hazard_model"].fillna("")
    df["risk_basis"] = "PLATEAU building risk"
    return df[
        ["record_id", "risk_type", "risk_type_ja", "depth_m", "hazard_source", "scenario", "risk_class", "risk_basis"]
    ]


def subset_locations_for_extent(locations: gpd.GeoDataFrame, row: pd.Series, pad_deg: float = 0.0003) -> gpd.GeoDataFrame:
    if any(pd.isna(row.get(c)) for c in ["min_x", "min_y", "max_x", "max_y"]):
        return locations
    return locations.cx[
        float(row["min_x"]) - pad_deg : float(row["max_x"]) + pad_deg,
        float(row["min_y"]) - pad_deg : float(row["max_y"]) + pad_deg,
    ]


def read_point_hazard_near_targets(
    source: Path,
    layer: str,
    targets: gpd.GeoDataFrame,
    columns: list[str],
    margin_deg: float = 0.001,
) -> gpd.GeoDataFrame:
    if targets.empty:
        return gpd.GeoDataFrame(columns=columns + ["geometry"], geometry="geometry", crs="EPSG:4326")
    minx, miny, maxx, maxy = targets.total_bounds
    bbox = (minx - margin_deg, miny - margin_deg, maxx + margin_deg, maxy + margin_deg)
    hz = pyogrio.read_dataframe(source, layer=layer, columns=columns, bbox=bbox)
    return ensure_wgs84(hz)


def nearest_grid_join(
    targets: gpd.GeoDataFrame,
    hz: gpd.GeoDataFrame,
    value_col: str,
    max_distance_m: float = POINT_GRID_MAX_DISTANCE_M,
) -> pd.DataFrame:
    if targets.empty or hz.empty:
        return pd.DataFrame(columns=["record_id", value_col])
    t = targets.to_crs(3857)
    h = hz.to_crs(3857)
    joined = gpd.sjoin_nearest(
        t,
        h[[value_col, "geometry"]],
        how="inner",
        max_distance=max_distance_m,
        distance_col="distance_m",
    )
    if joined.empty:
        return pd.DataFrame(columns=["record_id", value_col])
    return pd.DataFrame(joined[["record_id", value_col, "distance_m"]])


def external_inundation_risks(
    source: Path,
    locations: gpd.GeoDataFrame,
    contents: pd.DataFrame,
) -> pd.DataFrame:

    rows = []

    names = contents["table_name"].fillna("").astype(str)

    layers = contents[
        names.str.startswith("hazard_inundation_")
        & ~names.str.startswith(A31A_PREFIX)
    ]

    for _, info in layers.iterrows():

        layer = info["table_name"]

        targets = subset_locations_for_extent(
            locations,
            info,
        )

        if targets.empty:
            continue

        print(
            f"[inundation-point] {layer}: "
            f"targets={len(targets):,}"
        )

        hz = read_point_hazard_near_targets(
            source,
            layer,
            targets,
            ["area", "inundation_depth_m"],
        )

        joined = nearest_grid_join(
            targets,
            hz,
            "inundation_depth_m",
        )

        if joined.empty:
            continue

        joined["risk_type"] = "river_flooding"
        joined["risk_type_ja"] = "浸水予想区域"
        joined["depth_m"] = joined["inundation_depth_m"]

        joined["hazard_source"] = layer.replace(
            "hazard_inundation_",
            "",
        )

        joined["scenario"] = ""

        joined["risk_class"] = (
            joined["depth_m"].map(classify_depth)
        )

        joined["risk_basis"] = (
            "external point-grid sampling"
        )

        rows.append(
            joined[
                [
                    "record_id",
                    "risk_type",
                    "risk_type_ja",
                    "depth_m",
                    "hazard_source",
                    "scenario",
                    "risk_class",
                    "risk_basis",
                ]
            ]
        )

        del hz, joined

    if rows:
        return pd.concat(
            rows,
            ignore_index=True,
        )

    return pd.DataFrame(
        columns=[
            "record_id",
            "risk_type",
            "risk_type_ja",
            "depth_m",
            "hazard_source",
            "scenario",
            "risk_class",
            "risk_basis",
        ]
    )

def external_storm_surge_risks(source: Path, locations: gpd.GeoDataFrame) -> pd.DataFrame:
    print("[storm-surge-point] hazard_storm_surge_depth")
    hz = pyogrio.read_dataframe(source, layer="hazard_storm_surge_depth", columns=["DepthM"])
    hz = ensure_wgs84(hz)
    joined = spatial_join_polygons(locations, hz, ["DepthM"])
    if joined.empty:
        return pd.DataFrame(columns=["record_id", "risk_type", "risk_type_ja", "depth_m", "hazard_source", "scenario", "risk_class", "risk_basis"])
    agg = joined.groupby("record_id", as_index=False)["DepthM"].max()
    agg["risk_type"] = "high_tide"
    agg["risk_type_ja"] = "高潮浸水想定"
    agg["depth_m"] = agg["DepthM"]
    agg["hazard_source"] = "東京都 高潮浸水想定区域図"
    agg["scenario"] = ""
    agg["risk_class"] = agg["depth_m"].map(classify_depth)
    agg["risk_basis"] = "external polygon point-in-area"
    return agg[["record_id", "risk_type", "risk_type_ja", "depth_m", "hazard_source", "scenario", "risk_class", "risk_basis"]]


def external_tsunami_risks(source: Path, locations: gpd.GeoDataFrame, contents: pd.DataFrame) -> pd.DataFrame:
    rows = []
    layers = contents[contents["table_name"].str.startswith("hazard_tsunami_depth_", na=False)]
    for _, info in layers.iterrows():
        layer = info["table_name"]
        targets = subset_locations_for_extent(locations, info)
        if targets.empty:
            continue
        hz = read_point_hazard_near_targets(
            source, layer, targets, ["area", "scenario", "inundation_depth_max_m"]
        )
        if hz.empty:
            continue
        joined = nearest_grid_join(targets, hz, "inundation_depth_max_m")
        if joined.empty:
            continue

        # Recover stable area/scenario values from layer naming / source sample.
        non_geom = hz.drop(columns="geometry")
        area = str(non_geom["area"].dropna().iloc[0]) if "area" in non_geom and non_geom["area"].notna().any() else ""
        scenario = str(non_geom["scenario"].dropna().iloc[0]) if "scenario" in non_geom and non_geom["scenario"].notna().any() else layer.replace("hazard_tsunami_depth_", "")

        print(f"[tsunami-point] {area} / {scenario}: targets={len(targets):,}")
        joined["risk_type"] = "tsunami"
        joined["risk_type_ja"] = "津波浸水想定"
        joined["depth_m"] = joined["inundation_depth_max_m"]
        joined["hazard_source"] = area
        joined["scenario"] = scenario
        joined["risk_class"] = joined["depth_m"].map(classify_depth)
        joined["risk_basis"] = "external point-grid sampling"
        rows.append(joined[["record_id", "risk_type", "risk_type_ja", "depth_m", "hazard_source", "scenario", "risk_class", "risk_basis"]])
        del hz, joined
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["record_id", "risk_type", "risk_type_ja", "depth_m", "hazard_source", "scenario", "risk_class", "risk_basis"]
    )


def _aggregate_risk_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse to one record per cultural-property record × risk type."""
    if df.empty:
        return df.copy()

    def join_unique(s):
        vals = sorted({str(x).strip() for x in s if pd.notna(x) and str(x).strip()})
        return ";".join(vals)

    return (
        df.groupby(["record_id", "risk_type"], as_index=False)
        .agg(
            risk_type_ja=("risk_type_ja", join_unique),
            depth_m=("depth_m", "max"),
            hazard_source=("hazard_source", join_unique),
            scenario=("scenario", join_unique),
            risk_class=("risk_class", join_unique),
            risk_basis=("risk_basis", join_unique),
        )
    )


def combine_water_risks(native: pd.DataFrame, external: pd.DataFrame) -> pd.DataFrame:
    """
    One row per record × water-risk type.

    PLATEAU building-native depth values have priority. If PLATEAU reports the
    risk type but has no numeric depth, an external point/polygon assignment is
    allowed to supply the depth value.
    """
    native = _aggregate_risk_rows(native[native["risk_type"].isin(WATER_RISK_TYPES)].copy())
    external = _aggregate_risk_rows(external[external["risk_type"].isin(WATER_RISK_TYPES)].copy())

    if native.empty:
        return external
    if external.empty:
        return native

    n = native.set_index(["record_id", "risk_type"])
    e = external.set_index(["record_id", "risk_type"])
    keys = n.index.union(e.index)
    rows = []

    for key in keys:
        nr = n.loc[key] if key in n.index else None
        er = e.loc[key] if key in e.index else None

        if nr is not None and pd.notna(nr["depth_m"]):
            chosen = nr.copy()
        elif er is not None and pd.notna(er["depth_m"]):
            chosen = er.copy()
            if nr is not None:
                chosen["risk_basis"] = str(nr.get("risk_basis", "")) + "; external depth supplement"
        elif nr is not None:
            chosen = nr.copy()
        else:
            chosen = er.copy()

        row = {"record_id": key[0], "risk_type": key[1]}
        row.update(chosen.to_dict())
        rows.append(row)

    return pd.DataFrame(rows)


def write_water_tables(best: pd.DataFrame, external_all: pd.DataFrame, meta: pd.DataFrame, tables: Path) -> None:
    merged = best.merge(meta, on="record_id", how="left")
    merged["depth_class"] = merged["depth_m"].map(classify_depth)
    merged.to_csv(tables / "water_risk_best_available_records.csv", index=False, encoding="utf-8-sig")
    external_all.to_csv(tables / "water_risk_external_point_assignments.csv", index=False, encoding="utf-8-sig")

    class_order = ["0", "0–0.5 m", "0.5–3 m", "3–5 m", "5 m以上", "No data"]
    for risk_type in sorted(WATER_RISK_TYPES):
        risk_rows = best[best["risk_type"] == risk_type][["record_id", "depth_m", "hazard_source", "scenario", "risk_basis"]].copy()
        # Left join to the full non-movable population so the table total stays
        # record-based and records without an assignable hazard value appear as No data.
        sub = meta.merge(risk_rows, on="record_id", how="left")
        sub["risk_type"] = risk_type
        sub["depth_class"] = sub["depth_m"].map(classify_depth)
        sub.to_csv(tables / f"{risk_type}_depth_all_records.csv", index=False, encoding="utf-8-sig")
        for label, dim in {
            "municipality": "municipality_name",
            "designation_level": "designation_level",
            "designation_status": "designation_status",
            "cultural_type": "heritage_type_major",
        }.items():
            crosstab_count(sub, dim, "depth_class", class_order).to_csv(
                tables / f"{risk_type}_depth_{label}.csv", index=False, encoding="utf-8-sig"
            )

    # River/area-specific tables from all external point-grid matches.
    river = external_all[external_all["risk_type"] == "river_flooding"].merge(meta, on="record_id", how="left")
    if not river.empty:
        river["depth_class"] = river["depth_m"].map(classify_depth)
        for area, sub in river.groupby("hazard_source"):
            crosstab_count(sub, "municipality_name", "depth_class", class_order).to_csv(
                tables / f"inundation_{slug(area)}_municipality.csv", index=False, encoding="utf-8-sig"
            )

    # Tsunami scenario-specific tables use the external scenario layers.
    tsu = external_all[external_all["risk_type"] == "tsunami"].merge(meta, on="record_id", how="left")
    if not tsu.empty:
        tsu["depth_class"] = tsu["depth_m"].map(classify_depth)
        for (area, scenario), sub in tsu.groupby(["hazard_source", "scenario"]):
            crosstab_count(sub, "municipality_name", "depth_class", class_order).to_csv(
                tables / f"tsunami_{slug(area)}_{slug(scenario)}_municipality.csv",
                index=False,
                encoding="utf-8-sig",
            )



def a31a_flood_results(
    source: Path,
    locations: gpd.GeoDataFrame,
    meta: pd.DataFrame,
    contents: pd.DataFrame,
    tables: Path,
    metadata_dir: Path,
) -> pd.DataFrame:
    """
    Aggregate A31a polygon classes.

    A31a stores published depth classes, not a single measured
    numeric depth. Therefore no representative depth_m is invented.
    """

    def write_depth_crosstab(
        df: pd.DataFrame,
        rows: list[str],
        out_path: Path,
    ) -> None:

        if df.empty:
            pd.DataFrame().to_csv(
                out_path,
                index=False,
                encoding="utf-8-sig",
            )
            return

        tab = pd.crosstab(
            index=[df[c] for c in rows],
            columns=df["depth_class"],
            dropna=False,
        )

        for c in A31A_DEPTH_ORDER:
            if c not in tab.columns:
                tab[c] = 0

        tab = tab[A31A_DEPTH_ORDER]
        tab["Total"] = tab.sum(axis=1)

        tab.reset_index().to_csv(
            out_path,
            index=False,
            encoding="utf-8-sig",
        )

    names = (
        contents["table_name"]
        .fillna("")
        .astype(str)
    )

    layer_rows = contents[
        names.str.startswith(A31A_PREFIX)
    ].copy()

    empty_cols = [
        "record_id",
        "municipality_code",
        "municipality_name",
        "designation_level",
        "designation_status",
        "heritage_type_major",
        "heritage_type_detail_norm",
        "entity_class",
        "name",
        "river",
        "risk_type",
        "risk_type_ja",
        "hazard_source",
        "scenario",
        "depth_class",
        "depth_rank",
        "depth_class_native",
        "depth_min_m",
        "depth_max_m",
        "risk_basis",
    ]

    report = {
        "source": str(source),
        "layers": {},
    }

    all_parts = []

    if layer_rows.empty:
        print("[A31a] no A31a polygon layers found")

    for _, info in layer_rows.iterrows():

        layer = str(info["table_name"])
        river = layer[len(A31A_PREFIX):]

        targets = subset_locations_for_extent(
            locations,
            info,
        )

        if targets.empty:
            continue

        print(
            f"[A31a] {river}: "
            f"targets={len(targets):,}"
        )

        hz = pyogrio.read_dataframe(
            source,
            layer=layer,
            columns=[
                "depth_rank_code",
                "depth_class_native",
                "depth_min_m",
                "depth_max_m",
                "depth_class_summary",
                "scenario",
                "river_name",
            ],
        )

        hz = ensure_wgs84(hz)

        hz = hz[
            hz["depth_class_summary"]
            .isin(A31A_DEPTH_ORDER)
        ].copy()

        joined = spatial_join_polygons(
            targets,
            hz,
            [
                "depth_rank_code",
                "depth_class_native",
                "depth_min_m",
                "depth_max_m",
                "depth_class_summary",
                "scenario",
                "river_name",
            ],
        )

        if joined.empty:

            report["layers"][layer] = {
                "river": river,
                "polygon_count": int(len(hz)),
                "matched_record_count": 0,
                "depth_class_counts": {
                    c: 0
                    for c in A31A_DEPTH_ORDER
                },
            }

            continue

        rank_native = pd.to_numeric(
            joined["depth_rank_code"],
            errors="coerce",
        )

        rank_summary = (
            joined["depth_class_summary"]
            .map(A31A_DEPTH_RANK)
        )

        joined["depth_rank"] = (
            rank_native
            .fillna(rank_summary)
            .fillna(0)
            .astype(int)
        )

        # 同一recordに複数地点・複数polygonが重なる場合は
        # 最も深い公表階級を採用する。
        joined = (
            joined
            .sort_values(
                ["record_id", "depth_rank"]
            )
            .drop_duplicates(
                "record_id",
                keep="last",
            )
        )

        rec = meta.merge(
            joined[
                [
                    "record_id",
                    "depth_rank",
                    "depth_class_native",
                    "depth_min_m",
                    "depth_max_m",
                    "depth_class_summary",
                    "scenario",
                ]
            ],
            on="record_id",
            how="inner",
        )

        rec["river"] = river

        rec["risk_type"] = (
            "river_flooding"
        )

        rec["risk_type_ja"] = (
            "洪水浸水想定区域"
        )

        rec["hazard_source"] = (
            f"A31a {river}"
        )

        rec["scenario"] = (
            rec["scenario"]
            .fillna("")
            .replace("", "想定最大規模")
        )

        rec["depth_class"] = (
            rec["depth_class_summary"]
        )

        rec["risk_basis"] = (
            "A31a polygon intersection"
        )

        rec = rec[
            [
                "record_id",
                "municipality_code",
                "municipality_name",
                "designation_level",
                "designation_status",
                "heritage_type_major",
                "heritage_type_detail_norm",
                "entity_class",
                "name",
                "river",
                "risk_type",
                "risk_type_ja",
                "hazard_source",
                "scenario",
                "depth_class",
                "depth_rank",
                "depth_class_native",
                "depth_min_m",
                "depth_max_m",
                "risk_basis",
            ]
        ]

        prefix = (
            f"inundation_a31a_{slug(river)}"
        )

        rec.to_csv(
            tables / f"{prefix}_records.csv",
            index=False,
            encoding="utf-8-sig",
        )

        write_depth_crosstab(
            rec,
            [
                "municipality_code",
                "municipality_name",
            ],
            tables
            / f"{prefix}_municipality.csv",
        )

        write_depth_crosstab(
            rec,
            ["designation_level"],
            tables
            / f"{prefix}_designation_level.csv",
        )

        write_depth_crosstab(
            rec,
            ["designation_status"],
            tables
            / f"{prefix}_designation_status.csv",
        )

        write_depth_crosstab(
            rec,
            ["heritage_type_major"],
            tables
            / f"{prefix}_cultural_type.csv",
        )

        counts = (
            rec["depth_class"]
            .value_counts()
            .reindex(
                A31A_DEPTH_ORDER,
                fill_value=0,
            )
        )

        print(
            f"[A31a] {river}: "
            f"matched records="
            f"{rec['record_id'].nunique():,}"
        )

        for depth_class, count in counts.items():
            print(
                f"    {depth_class}: "
                f"{int(count):,}"
            )

        report["layers"][layer] = {
            "river": river,
            "polygon_count": int(len(hz)),
            "matched_record_count":
                int(rec["record_id"].nunique()),
            "depth_class_counts": {
                c: int(counts[c])
                for c in A31A_DEPTH_ORDER
            },
        }

        all_parts.append(rec)

        del hz, joined, rec

    if all_parts:

        all_records = pd.concat(
            all_parts,
            ignore_index=True,
        )

        all_records.to_csv(
            tables
            / "a31a_flooding_all_records.csv",
            index=False,
            encoding="utf-8-sig",
        )

        write_depth_crosstab(
            all_records,
            [
                "river",
                "municipality_code",
                "municipality_name",
            ],
            tables
            / "a31a_flooding_by_river_municipality.csv",
        )

        write_depth_crosstab(
            all_records,
            [
                "river",
                "designation_level",
            ],
            tables
            / "a31a_flooding_by_river_designation_level.csv",
        )

        write_depth_crosstab(
            all_records,
            [
                "river",
                "designation_status",
            ],
            tables
            / "a31a_flooding_by_river_designation_status.csv",
        )

        write_depth_crosstab(
            all_records,
            [
                "river",
                "heritage_type_major",
            ],
            tables
            / "a31a_flooding_by_river_cultural_type.csv",
        )

    else:

        all_records = pd.DataFrame(
            columns=empty_cols
        )

    report["combined_rows"] = (
        int(len(all_records))
    )

    report["unique_records"] = (
        int(
            all_records["record_id"]
            .nunique()
        )
        if not all_records.empty
        else 0
    )

    (
        metadata_dir
        / "a31a_aggregation.json"
    ).write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return all_records

def landslide_presence(source: Path, locations: gpd.GeoDataFrame, native: pd.DataFrame) -> pd.DataFrame:
    native_ids = set(native.loc[native["risk_type"] == "landslide", "record_id"].astype(str))
    external_ids: set[str] = set()
    try:
        hz = pyogrio.read_dataframe(source, layer="hazard_sediment_warning_a33_polygon")
        hz = ensure_wgs84(hz)
        joined = gpd.sjoin(locations[["record_id", "geometry"]], hz[["geometry"]], how="inner", predicate="intersects")
        external_ids = set(joined["record_id"].astype(str))
    except Exception as e:
        print(f"[warning] landslide point assignment skipped: {e}")
    ids = sorted(native_ids | external_ids)
    return pd.DataFrame({"record_id": ids, "risk_type": "landslide"})


def build_risk_presence(
    meta: pd.DataFrame,
    seismic: pd.DataFrame,
    fire: pd.DataFrame,
    water: pd.DataFrame,
    a31a: pd.DataFrame,
    landslide: pd.DataFrame,
    tables: Path,
) -> pd.DataFrame:
    rows = []
    if not seismic.empty:
        for rid in seismic.loc[seismic["seismic_intensity"].notna(), "record_id"].unique():
            rows.append((rid, "seismic"))
    if not fire.empty:
        for rid in fire.loc[fire["fire_class"].notna(), "record_id"].unique():
            rows.append((rid, "fire"))
    if not water.empty:
        for rid, rtype in water[["record_id", "risk_type"]].drop_duplicates().itertuples(index=False):
            rows.append((rid, rtype))
    if not a31a.empty:
        for rid in a31a["record_id"].dropna().astype(str).unique():
            rows.append((rid, "river_flooding"))
    if not landslide.empty:
        rows.extend(list(landslide[["record_id", "risk_type"]].itertuples(index=False, name=None)))

    long = pd.DataFrame(rows, columns=["record_id", "risk_type"]).drop_duplicates()
    long = long.merge(meta, on="record_id", how="left")
    long.to_csv(tables / "record_risk_types.csv", index=False, encoding="utf-8-sig")

    for label, dim in {
        "municipality": "municipality_name",
        "designation_level": "designation_level",
        "designation_status": "designation_status",
        "cultural_type": "heritage_type_major",
    }.items():
        tab = pd.crosstab(long[dim], long["risk_type"], dropna=False).reset_index()
        tab["Total"] = tab.select_dtypes(include=[np.number]).sum(axis=1)
        tab.to_csv(tables / f"risk_type_by_{label}.csv", index=False, encoding="utf-8-sig")
    return long


def write_source_tables(source: Path, records: gpd.GeoDataFrame, tables: Path) -> None:
    try:
        lic = read_sql_table(source, "source_license")
        lic.to_csv(tables / "source_datasets.csv", index=False, encoding="utf-8-sig")
    except Exception as e:
        print(f"[warning] source_license export failed: {e}")

    try:
        manifest = read_sql_table(source, "hazard_source_manifest")
        manifest.to_csv(tables / "hazard_source_manifest.csv", index=False, encoding="utf-8-sig")
    except Exception as e:
        print(f"[warning] hazard_source_manifest export failed: {e}")

    cultural_cols = [c for c in ["municipality_code", "municipality_name", "source_file", "source_municipality_gpkg"] if c in records.columns]
    if cultural_cols:
        records[cultural_cols].drop_duplicates().sort_values(cultural_cols[:2]).to_csv(
            tables / "cultural_property_source_files.csv", index=False, encoding="utf-8-sig"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path, help="Path to 13_heritage_hazards.gpkg")
    ap.add_argument("--out-dir", type=Path, default=Path("summary_results"))
    ap.add_argument("--skip-point-water", action="store_true", help="Skip external inundation/tsunami point-grid sampling")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    source = args.source.expanduser().resolve()
    out = args.out_dir.expanduser().resolve()
    tables = out / "tables"
    cache = out / "cache"
    metadata_dir = out / "metadata"

    if not source.exists():
        raise SystemExit(f"ERROR: source not found: {source}")

    if out.exists() and not args.force:
        # Allow first run into an existing empty directory, but protect prior result tables.
        sentinel = tables / "municipality_record_counts.csv"
        if sentinel.exists():
            raise SystemExit(f"ERROR: results already exist: {out}\nUse --force to regenerate.")

    tables.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    print("=== READ CULTURAL-PROPERTY RECORDS ===")
    records = pyogrio.read_dataframe(source, layer="heritage_records")
    records = ensure_wgs84(records)
    records = normalize_records(records)
    movable = records[records["entity_class"] == "movable"].copy()
    immovable = records[records["entity_class"] != "movable"].copy()
    print(f"all records:       {len(records):,}")
    print(f"non-movable main:  {len(immovable):,}")
    print(f"movable separate:  {len(movable):,}")

    write_basic_tables(immovable, movable, tables)
    write_source_tables(source, records, tables)

    print("\n=== BUILD ANALYSIS LOCATIONS ===")
    locations = build_analysis_locations(source, immovable)
    print(f"analysis locations: {len(locations):,} for {locations['record_id'].nunique():,} records")
    locations.to_file(cache / "analysis_locations.gpkg", layer="analysis_locations", driver="GPKG")

    meta_cols = [
        "record_id", "municipality_code", "municipality_name", "designation_level",
        "designation_status", "heritage_type_major", "heritage_type_detail_norm",
        "entity_class", "name",
    ]
    meta = immovable[meta_cols].drop_duplicates("record_id")

    print("\n=== SEISMIC ===")
    seismic, _ = seismic_results(source, locations, meta, tables)

    print("\n=== FIRE ===")
    fire = fire_results(source, locations, meta, tables)

    print("\n=== PLATEAU BUILDING RISKS ===")
    native = native_plateau_risks(source, set(meta["record_id"]))
    native.to_csv(tables / "plateau_building_risk_records.csv", index=False, encoding="utf-8-sig")

    contents = gpkg_contents(source)

    print("\n=== A31a FLOOD POLYGON ASSIGNMENT ===")
    a31a = a31a_flood_results(
        source,
        locations,
        meta,
        contents,
        tables,
        metadata_dir,
    )

    external_parts = []

    print("\n=== STORM SURGE POINT ASSIGNMENT ===")
    external_parts.append(external_storm_surge_risks(source, locations))

    if not args.skip_point_water:
        print("\n=== INUNDATION POINT-GRID ASSIGNMENT ===")
        external_parts.append(external_inundation_risks(source, locations, contents))
        print("\n=== TSUNAMI POINT-GRID ASSIGNMENT ===")
        external_parts.append(external_tsunami_risks(source, locations, contents))
    else:
        print("[skip] external inundation/tsunami point-grid sampling")

    external = pd.concat([x for x in external_parts if not x.empty], ignore_index=True) if any(not x.empty for x in external_parts) else pd.DataFrame(
        columns=["record_id", "risk_type", "risk_type_ja", "depth_m", "hazard_source", "scenario", "risk_class", "risk_basis"]
    )
    best_water = combine_water_risks(native, external)
    write_water_tables(best_water, external, meta, tables)

    print("\n=== LANDSLIDE PRESENCE ===")
    landslide = landslide_presence(source, locations, native)

    print("\n=== RISK-TYPE CROSS TABLES ===")
    risk_long = build_risk_presence(meta, seismic, fire, best_water, a31a, landslide, tables)

    run = {
        "source": str(source),
        "source_size_bytes": source.stat().st_size,
        "all_records": int(len(records)),
        "main_non_movable_records": int(len(immovable)),
        "movable_records": int(len(movable)),
        "records_with_analysis_location": int(locations["record_id"].nunique()),
        "analysis_location_count": int(len(locations)),
        "seismic_scenarios": SCENARIOS,
        "water_point_grid_max_distance_m": POINT_GRID_MAX_DISTANCE_M,
        "skip_point_water": bool(args.skip_point_water),
        "a31a_flood_records": int(a31a["record_id"].nunique()) if not a31a.empty else 0,
        "risk_type_rows": int(len(risk_long)),
    }
    (metadata_dir / "run_summary.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nSUCCESS")
    print("output:", out)
    print("Next: run render_summary_maps.py against the same source GPKG and this output directory.")


if __name__ == "__main__":
    main()
