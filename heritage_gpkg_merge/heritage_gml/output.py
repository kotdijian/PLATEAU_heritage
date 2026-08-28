from __future__ import annotations
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
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
        "source_file", "record_id", "name", "place_name", "address_detail", "owner", "address",
        "municipality", "municipality_code", "category", "type", "designation",
        "designation_date", "entity_class", "geometry_role", "source_location_role",
        "spatial_match_status", "complex_id", "complex_name", "complex_grouping_method",
        "complex_record_count", "matched_building_ids", "match_methods", "geometry",
    ]
    rows = []
    for r in records:
        rows.append({
            "source_file": r.source_file,
            "record_id": r.record_id,
            "name": r.name,
            "place_name": r.place_name,
            "address_detail": r.address_detail,
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
            "source_location_role": r.source_location_role,
            "spatial_match_status": r.spatial_match_status,
            "complex_id": r.complex_id,
            "complex_name": r.complex_name,
            "complex_grouping_method": r.complex_grouping_method,
            "complex_record_count": r.complex_record_count,
            "matched_building_ids": ";".join(r.matched_building_ids),
            "match_methods": ";".join(r.match_methods),
            "geometry": r.geometry,
        })
    return _gdf(rows, columns)


def buildings_df(buildings, selected):
    """Selected Building footprints for QGIS/GIS analysis.

    Original LOD0/1/2 Building geometry remains in the subset CityGML. This is
    explicitly a 2D analytical footprint derivative.
    """
    columns = [
        "gml_id", "building_id", "city_code", "file_code", "name", "address",
        "usage", "detailed_usage", "complex_ids", "complex_names", "record_ids",
        "record_names", "record_types", "entity_classes", "match_methods",
        "source_gml", "geometry",
    ]
    rows = []
    for b in buildings:
        if b.gml_id not in selected:
            continue
        m = selected[b.gml_id]
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
            "source_gml": b.source_file,
            "geometry": b.geometry,
        })
    return _gdf(rows, columns)


def points_df(point_rows):
    columns = [
        "point_id", "point_kind", "record_id", "record_ids", "name", "names",
        "place_name", "address_detail", "address", "category", "type", "entity_class",
        "geometry_role", "source_location_role", "spatial_match_status", "complex_id",
        "complex_name", "complex_grouping_method", "reason", "item_count",
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
        "complex_id", "complex_name", "grouping_method", "record_count",
        "movable_record_count", "directly_matched_record_count",
        "complex_only_record_count", "shared_coordinate_record_count",
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
            # complex_only/unresolved semantics are kept in attribute tables and
            # heritage_records. We never invent a polygon when no Building is confirmed.
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
            "grouping_method": summary.get("grouping_method", ""),
            "record_count": summary.get("record_count", 0),
            "movable_record_count": summary.get("movable_record_count", 0),
            "directly_matched_record_count": summary.get("directly_matched_record_count", 0),
            "complex_only_record_count": summary.get("complex_only_record_count", 0),
            "shared_coordinate_record_count": summary.get("shared_coordinate_record_count", 0),
            "member_building_count": len(bids),
            "building_gml_ids": ";".join(bids),
            "point_output_count": summary.get("point_output_count", 0),
            "status": summary.get("status", "matched_building_complex"),
            "geometry": geom,
        })
    return _gdf(rows, columns)


def _register_attribute_table(conn, name: str, row_count: int) -> None:
    """Register a plain SQLite table as a GeoPackage attributes table."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute("DELETE FROM gpkg_contents WHERE table_name = ?", (name,))
    conn.execute(
        """
        INSERT INTO gpkg_contents
        (table_name, data_type, identifier, description, last_change,
         min_x, min_y, max_x, max_y, srs_id)
        VALUES (?, 'attributes', ?, ?, ?, NULL, NULL, NULL, NULL, NULL)
        """,
        (name, name, f"Heritage attribute table ({row_count} rows)", now),
    )


def _table(conn, rows, columns, name):
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=columns)
    df.to_sql(name, conn, if_exists="replace", index=False)
    _register_attribute_table(conn, name, len(df))


def write_gpkg(path, records, buildings, selected, point_rows, links, complex_rows,
               complex_member_rows, complex_record_rows, unresolved_rows):
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

    for name, gdf in nonempty:
        gdf.to_file(path, layer=name, driver="GPKG", engine="pyogrio")

    conn = sqlite3.connect(path)
    try:
        _table(conn, links, [
            "record_id", "name", "place_name", "address_detail", "type", "entity_class",
            "complex_id", "complex_name", "building_gml_id", "building_id", "building_name",
            "building_address", "usage", "detailed_usage", "match_methods", "source_gml",
        ], "heritage_building_links")
        _table(conn, complex_rows, [
            "complex_id", "complex_name", "grouping_method", "record_count",
            "movable_record_count", "directly_matched_record_count", "complex_only_record_count",
            "shared_coordinate_record_count", "matched_building_count", "building_gml_ids",
            "point_output_count", "status",
        ], "heritage_complex_summary")
        _table(conn, complex_member_rows, [
            "complex_id", "complex_name", "grouping_method", "building_gml_id", "building_id",
            "building_name", "building_address", "usage", "detailed_usage", "record_ids",
            "record_names", "record_types", "entity_classes", "match_methods", "source_gml",
        ], "heritage_complex_members")
        _table(conn, complex_record_rows, [
            "complex_id", "complex_name", "grouping_method", "record_id", "name", "place_name",
            "address_detail", "type", "entity_class", "source_location_role", "association_status",
            "matched_building_ids", "match_methods",
        ], "heritage_complex_records")
        _table(conn, unresolved_rows, [
            "entity_id", "entity_kind", "name", "place_name", "address_detail", "type",
            "entity_class", "complex_id", "complex_name", "source_location_role",
            "spatial_match_status", "address", "reason",
        ], "heritage_unresolved_entities")
        conn.commit()
    finally:
        conn.close()
