from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import json
import sqlite3

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPolygon, Polygon


def _gdf(rows, columns):
    df = pd.DataFrame(rows, columns=columns)
    if "geometry" not in df.columns:
        df["geometry"] = pd.Series(dtype="object")
    return gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")


def records_df(records):
    columns = [
        "source_file", "record_id", "name", "place_name", "owner", "address",
        "municipality", "municipality_code", "category", "type", "designation",
        "designation_date", "entity_class", "geometry_role", "complex_id",
        "complex_name", "movable_group_id", "matched_building_ids",
        "match_methods", "geometry",
    ]
    rows = []
    for r in records:
        rows.append({
            "source_file": r.source_file,
            "record_id": r.record_id,
            "name": r.name,
            "place_name": r.place_name,
            "owner": r.owner,
            "address": r.address,
            "municipality": r.municipality,
            "municipality_code": r.municipality_code,
            "category": r.category,
            "type": r.type,
            "designation": r.designation,
            "designation_date": r.designation_date,
            "entity_class": r.entity_class,
            "geometry_role": r.geometry_role,
            "complex_id": r.complex_id,
            "complex_name": r.complex_name,
            "movable_group_id": r.movable_group_id,
            "matched_building_ids": ";".join(r.matched_building_ids),
            "match_methods": ";".join(r.match_methods),
            "geometry": r.geometry,
        })
    return _gdf(rows, columns)


def buildings_df(buildings, selected):
    """Selected Building footprints for QGIS/GIS analysis.

    The original LOD0/1/2 Building geometry remains in the companion subset
    CityGML. This layer is explicitly the 2D analytical footprint derivative.
    """
    columns = [
        "gml_id", "building_id", "city_code", "file_code", "name", "address",
        "usage", "detailed_usage", "complex_ids", "complex_names", "record_ids",
        "record_names", "record_types", "entity_classes", "match_methods",
        "movable_group_count", "movable_item_count", "movable_items_json",
        "source_gml", "geometry",
    ]
    rows = []
    for b in buildings:
        if b.gml_id not in selected:
            continue
        m = selected[b.gml_id]
        movable_groups = m.get("movable_groups") or []
        movable_items = [item for grp in movable_groups for item in (grp.get("items") or [])]
        rows.append({
            "gml_id": b.gml_id,
            "building_id": b.building_id,
            "city_code": b.city_code,
            "file_code": b.file_code,
            "name": b.name,
            "address": b.address,
            "usage": b.usage,
            "detailed_usage": b.detailed_usage,
            "complex_ids": ";".join(m.get("complex_ids", [])),
            "complex_names": ";".join(m.get("complex_names", [])),
            "record_ids": ";".join(m.get("record_ids", [])),
            "record_names": ";".join(m.get("record_names", [])),
            "record_types": ";".join(m.get("record_types", [])),
            "entity_classes": ";".join(m.get("entity_classes", [])),
            "match_methods": ";".join(m.get("methods", [])),
            "movable_group_count": len(movable_groups),
            "movable_item_count": len(movable_items),
            "movable_items_json": json.dumps(movable_groups, ensure_ascii=False, separators=(",", ":")),
            "source_gml": b.source_file,
            "geometry": b.geometry,
        })
    return _gdf(rows, columns)


def points_df(point_rows):
    columns = [
        "point_id", "point_kind", "record_id", "record_ids", "name", "names",
        "address", "category", "type", "entity_class", "geometry_role",
        "complex_id", "complex_name", "reason", "item_count",
        "attached_items_json", "geometry",
    ]
    return _gdf(point_rows, columns)


def _polygon_parts(geom):
    """Flatten Polygon/MultiPolygon without unioning or dissolving members."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [p for p in geom.geoms if not p.is_empty]
    return []


def building_complexes_df(buildings, complex_rows, complex_member_rows):
    """One MultiPolygon feature per matched Heritage Building Complex.

    Geometry is a collection of the exact member Building footprints. It is
    deliberately *not* dissolved, buffered, convex-hulled, or converted to a
    representative point. Space between member Buildings is not inferred to be
    part of the cultural-property geometry.
    """
    columns = [
        "complex_id", "complex_name", "record_count", "movable_item_count",
        "member_building_count", "building_gml_ids", "point_output_count",
        "status", "geometry",
    ]
    building_by_id = {b.gml_id: b for b in buildings}
    member_ids_by_complex = defaultdict(list)
    for row in complex_member_rows:
        cid = row.get("complex_id", "")
        bid = row.get("building_gml_id", "")
        if cid and bid and bid not in member_ids_by_complex[cid]:
            member_ids_by_complex[cid].append(bid)

    rows = []
    for summary in complex_rows:
        cid = summary.get("complex_id", "")
        bids = member_ids_by_complex.get(cid, [])
        if not bids:
            # Unmatched complexes remain in heritage_complex_summary and their
            # source/fallback points remain in heritage_records/heritage_points.
            continue
        parts = []
        for bid in bids:
            b = building_by_id.get(bid)
            if b is not None:
                parts.extend(_polygon_parts(b.geometry))
        if not parts:
            continue
        geom = MultiPolygon(parts)
        rows.append({
            "complex_id": cid,
            "complex_name": summary.get("complex_name", ""),
            "record_count": summary.get("record_count", 0),
            "movable_item_count": summary.get("movable_item_count", 0),
            "member_building_count": len(bids),
            "building_gml_ids": ";".join(bids),
            "point_output_count": summary.get("point_output_count", 0),
            "status": summary.get("status", "matched_building_complex"),
            "geometry": geom,
        })
    return _gdf(rows, columns)


def _table(conn, rows, columns, name):
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=columns)
    df.to_sql(name, conn, if_exists="replace", index=False)


def write_gpkg(path, records, buildings, selected, point_rows, links, complex_rows,
               complex_member_rows, movable_rows, movable_group_rows, unresolved_rows):
    path = Path(path)
    if path.exists():
        path.unlink()

    rdf = records_df(records)
    bdf = buildings_df(buildings, selected)
    pdf = points_df(point_rows)
    cdf = building_complexes_df(buildings, complex_rows, complex_member_rows)

    spatial_layers = [
        ("heritage_records", rdf),
        ("heritage_buildings_footprint", bdf),
        ("heritage_building_complexes", cdf),
        ("heritage_points", pdf),
    ]
    nonempty = [(name, gdf) for name, gdf in spatial_layers if not gdf.empty]
    if not nonempty:
        return

    # Write each spatial layer. The first creates the GeoPackage; subsequent
    # layers append to it.
    for name, gdf in nonempty:
        gdf.to_file(path, layer=name, driver="GPKG", engine="pyogrio")

    conn = sqlite3.connect(path)
    try:
        _table(conn, links, [
            "record_id", "name", "type", "entity_class", "complex_id", "complex_name",
            "building_gml_id", "building_id", "building_name", "building_address",
            "usage", "detailed_usage", "match_methods", "source_gml",
        ], "heritage_building_links")
        _table(conn, complex_rows, [
            "complex_id", "complex_name", "record_count", "movable_item_count",
            "matched_building_count", "building_gml_ids", "point_output_count", "status",
        ], "heritage_complex_summary")
        _table(conn, complex_member_rows, [
            "complex_id", "complex_name", "building_gml_id", "building_id",
            "building_name", "building_address", "usage", "detailed_usage",
            "record_ids", "record_names", "record_types", "match_methods", "source_gml",
        ], "heritage_complex_members")
        _table(conn, movable_rows, [
            "movable_group_id", "record_id", "name", "category", "type",
            "designation", "address", "linked_building_ids", "match_methods", "source_file",
        ], "heritage_movable_items")
        _table(conn, movable_group_rows, [
            "movable_group_id", "address", "item_count", "record_ids", "names",
            "linked_building_ids", "match_methods", "coordinate_count", "point_status",
        ], "heritage_movable_groups")
        _table(conn, unresolved_rows, [
            "entity_id", "entity_kind", "name", "type", "entity_class", "address", "reason",
        ], "heritage_unresolved_entities")
        conn.commit()
    finally:
        conn.close()
