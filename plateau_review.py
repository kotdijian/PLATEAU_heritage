#!/usr/bin/env python3
"""Build a profile-independent human-review package for PLATEAU matches."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import Geod
from shapely.geometry import Point
from shapely.ops import nearest_points

from heritage_gml.citygml import scan_buildings
from heritage_gml.model import PlateauFile

TOOL_VERSION = "0.1.0"
GEOD = Geod(ellps="GRS80")
INDEX_LAYER = "plateau_building_review_index"


def text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return " ".join(str(value).replace("\u3000", " ").split())


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)
    ).fetchone() is not None


def infer_city_code(path: Path) -> str:
    values = re.findall(r"(?<!\d)(13\d{3})(?!\d)", str(path))
    return values[-1] if values else ""


def discover_gml_files(directory: Path) -> list[PlateauFile]:
    files = []
    for path in sorted(directory.resolve().rglob("*.gml")):
        lowered = path.name.lower()
        if "bldg" not in lowered and "building" not in lowered:
            continue
        files.append(PlateauFile(
            city_code=infer_city_code(path), city_name="",
            code=path.name.split("_")[0], url="", local_path=str(path),
        ))
    return files


def file_manifest(files: list[PlateauFile]) -> list[dict[str, object]]:
    rows = []
    for item in files:
        path = Path(item.local_path or "")
        stat = path.stat()
        rows.append({"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    return rows


def manifest_digest(rows: list[dict[str, object]]) -> str:
    value = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_index(plateau_dir: Path, index_path: Path) -> dict[str, object]:
    files = discover_gml_files(plateau_dir)
    if not files:
        raise RuntimeError(f"No PLATEAU bldg GML files found: {plateau_dir}")
    selected: dict[str, object] = {}
    geometry_digests: dict[str, str] = {}
    geometry_conflicts: set[str] = set()
    duplicate_occurrences = 0
    for number, plateau_file in enumerate(files, 1):
        for building in scan_buildings([plateau_file]):
            digest = hashlib.sha256(building.geometry.wkb).hexdigest()
            if building.gml_id not in selected:
                selected[building.gml_id] = building
                geometry_digests[building.gml_id] = digest
            else:
                duplicate_occurrences += 1
                if (
                    geometry_digests[building.gml_id] != digest
                    and not selected[building.gml_id].geometry.equals(building.geometry)
                ):
                    geometry_conflicts.add(building.gml_id)
        if number == len(files) or number % 10 == 0:
            print(f"PLATEAU review index: {number}/{len(files)} files", flush=True)
    rows = []
    for gml_id, building in selected.items():
        if gml_id in geometry_conflicts:
            continue
        rows.append({
            "gml_id": gml_id, "building_id": text(building.building_id),
            "city_code": text(building.city_code), "name": text(building.name),
            "address": text(building.address), "usage": text(building.usage),
            "detailed_usage": text(building.detailed_usage),
            "source_gml": text(building.source_file), "geometry": building.geometry,
        })
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if index_path.exists():
        index_path.unlink()
    frame.to_file(index_path, layer=INDEX_LAYER, driver="GPKG", engine="pyogrio")
    manifest = file_manifest(files)
    summary = {
        "tool_version": TOOL_VERSION, "plateau_files": len(files),
        "indexed_buildings": len(frame), "duplicate_occurrences": duplicate_occurrences,
        "geometry_conflict_ids_excluded": len(geometry_conflicts),
        "geometry_conflict_id_samples": sorted(geometry_conflicts)[:20],
        "source_manifest_sha256": manifest_digest(manifest), "source_manifest": manifest,
    }
    index_path.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def read_table(gpkg: Path, table: str) -> list[dict[str, object]]:
    with sqlite3.connect(gpkg) as connection:
        connection.row_factory = sqlite3.Row
        if not table_exists(connection, table):
            raise ValueError(f"Required table is missing: {table}")
        return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]


def load_museum_entities(gpkg: Path) -> list[dict[str, object]]:
    rows = read_table(gpkg, "museum_facilities")
    links = read_table(gpkg, "museum_building_links")
    confirmed: dict[str, list[dict[str, object]]] = defaultdict(list)
    for link in links:
        if text(link.get("match_status")) == "confirmed":
            confirmed[text(link.get("museum_id"))].append(link)
    entities = []
    for row in rows:
        entity_id = text(row.get("museum_id"))
        machine = confirmed.get(entity_id, [])
        entities.append({
            "entity_id": entity_id,
            "entity_name": text(row.get("canonical_name")),
            "entity_kind": "museum",
            "municipality_code": text(row.get("municipality_code")),
            "municipality_name": text(row.get("municipality_name")),
            "address": text(row.get("address")),
            "longitude": row.get("longitude"), "latitude": row.get("latitude"),
            "location_source": text(row.get("location_address_source_url")),
            "location_level": text(row.get("location_coordinate_level")),
            "machine_status": "confirmed" if machine else "unresolved",
            "machine_building_ids": ";".join(text(link.get("building_gml_id")) for link in machine),
            "machine_match_methods": ";".join(sorted({
                method for link in machine
                for method in text(link.get("match_methods")).split(";") if method
            })),
        })
    return entities


def load_heritage_entities(gpkg: Path) -> list[dict[str, object]]:
    records = gpd.read_file(gpkg, layer="heritage_records")
    entities = []
    for _, row in records.iterrows():
        geometry = row.geometry
        point = geometry if geometry is not None and geometry.geom_type == "Point" else (
            geometry.representative_point() if geometry is not None and not geometry.is_empty else None
        )
        matched = text(row.get("matched_building_ids"))
        entities.append({
            "entity_id": text(row.get("record_id")), "entity_name": text(row.get("name")),
            "entity_kind": "heritage", "municipality_code": text(row.get("municipality_code")),
            "municipality_name": text(row.get("municipality")), "address": text(row.get("address")),
            "longitude": point.x if point is not None else None,
            "latitude": point.y if point is not None else None,
            "location_source": text(row.get("source_file")),
            "location_level": text(row.get("geometry_role")),
            "machine_status": "confirmed" if matched else "unresolved",
            "machine_building_ids": matched,
            "machine_match_methods": text(row.get("match_methods")),
        })
    return entities


def load_entities(gpkg: Path, profile: str) -> list[dict[str, object]]:
    if profile == "auto":
        with sqlite3.connect(gpkg) as connection:
            if table_exists(connection, "museum_facilities"):
                profile = "museum"
            elif table_exists(connection, "heritage_records"):
                profile = "heritage"
            else:
                raise ValueError("Could not infer profile: museum_facilities/heritage_records is missing")
    return load_museum_entities(gpkg) if profile == "museum" else load_heritage_entities(gpkg)


def optional_point(row: dict[str, object]) -> Point | None:
    try:
        longitude, latitude = float(row["longitude"]), float(row["latitude"])
    except (TypeError, ValueError):
        return None
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    return Point(longitude, latitude)


def distance_m(point: Point, geometry) -> float:
    if geometry.covers(point):
        return 0.0
    a, b = nearest_points(point, geometry)
    return float(GEOD.inv(a.x, a.y, b.x, b.y)[2])


def build_review(
    source_gpkg: Path, index_path: Path, output: Path, profile: str, radius_m: float,
) -> dict[str, object]:
    if not source_gpkg.is_file():
        raise FileNotFoundError(f"Source GeoPackage not found: {source_gpkg}")
    if radius_m <= 0:
        raise ValueError("--radius-m must be greater than zero")
    entities = load_entities(source_gpkg, profile)
    buildings = gpd.read_file(index_path, layer=INDEX_LAYER)
    building_by_id = {text(row["gml_id"]): row for _, row in buildings.iterrows()}
    building_positions = {
        text(row["gml_id"]): int(index) for index, row in buildings.iterrows()
    }
    spatial_index = buildings.sindex
    entity_rows, neighborhood_rows, audit_rows = [], [], []
    missing_anchor = 0
    for entity in entities:
        point = optional_point(entity)
        anchor_source = "verified_location"
        if point is None:
            machine_ids = [value for value in text(entity["machine_building_ids"]).split(";") if value]
            machine_building = next((building_by_id.get(value) for value in machine_ids if value in building_by_id), None)
            if machine_building is not None:
                point = machine_building.geometry.representative_point()
                anchor_source = "machine_building_centroid"
        if point is None:
            missing_anchor += 1
            audit_rows.append({**entity, "anchor_source": "missing", "neighborhood_building_count": 0,
                               "human_status": "pending", "human_decision_type": "", "human_selected_building_ids": "",
                               "human_rejected_building_ids": "", "reviewer": "", "reviewed_at": "", "notes": ""})
            continue
        entity_rows.append({**entity, "anchor_source": anchor_source, "geometry": point})
        degree = radius_m / 90_000.0
        indices = set(int(value) for value in spatial_index.query(
            point.buffer(degree), predicate="intersects"
        ))
        count = 0
        machine_ids = set(text(entity["machine_building_ids"]).split(";"))
        indices.update(
            building_positions[value] for value in machine_ids if value in building_positions
        )
        for index in sorted(indices):
            building = buildings.iloc[int(index)]
            measured = distance_m(point, building.geometry)
            if measured > radius_m and text(building["gml_id"]) not in machine_ids:
                continue
            count += 1
            neighborhood_rows.append({
                "review_feature_id": f"{entity['entity_id']}|{building['gml_id']}",
                "entity_id": entity["entity_id"], "entity_name": entity["entity_name"],
                "entity_kind": entity["entity_kind"], "building_gml_id": text(building["gml_id"]),
                "building_name": text(building.get("name")), "building_address": text(building.get("address")),
                "usage": text(building.get("usage")), "detailed_usage": text(building.get("detailed_usage")),
                "distance_m": round(measured, 2), "machine_selected": int(text(building["gml_id"]) in machine_ids),
                "source_gml": text(building.get("source_gml")), "geometry": building.geometry,
            })
        audit_rows.append({**entity, "anchor_source": anchor_source, "neighborhood_building_count": count,
                           "human_status": "pending", "human_decision_type": "", "human_selected_building_ids": "",
                           "human_rejected_building_ids": "", "reviewer": "", "reviewed_at": "", "notes": ""})
    entity_frame = gpd.GeoDataFrame(entity_rows, geometry="geometry", crs="EPSG:4326")
    neighborhood = gpd.GeoDataFrame(neighborhood_rows, geometry="geometry", crs="EPSG:4326")
    if output.exists():
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not entity_frame.empty:
        entity_frame.to_file(
            output, layer="review_location_points", driver="GPKG", engine="pyogrio"
        )
    if not neighborhood.empty:
        neighborhood.to_file(output, layer="review_neighborhood_buildings", driver="GPKG", engine="pyogrio")
    with sqlite3.connect(output) as connection:
        pd.DataFrame(audit_rows).to_sql("review_audit", connection, if_exists="replace", index=False)
        pd.DataFrame([{"profile": profile, "source_gpkg": str(source_gpkg), "index_gpkg": str(index_path),
                       "radius_m": radius_m, "tool_version": TOOL_VERSION}]).to_sql(
            "review_metadata", connection, if_exists="replace", index=False
        )
    summary = {"tool_version": TOOL_VERSION, "entities": len(entities), "entities_with_anchor": len(entity_frame),
               "entities_missing_anchor": missing_anchor, "neighborhood_links": len(neighborhood),
               "machine_confirmed_entities": sum(row["machine_status"] == "confirmed" for row in entities),
               "radius_m": radius_m, "output_gpkg": str(output)}
    output.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a PLATEAU match human-review GeoPackage")
    parser.add_argument("source_gpkg", type=Path)
    parser.add_argument("--profile", choices=("auto", "museum", "heritage"), default="auto")
    parser.add_argument("--plateau-local-dir", type=Path, default=Path(".cache/plateau"))
    parser.add_argument("--building-index", type=Path, default=Path(".cache/plateau_review_buildings.gpkg"))
    parser.add_argument("--rebuild-index", action="store_true")
    parser.add_argument("--radius-m", type=float, default=200.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = args.source_gpkg.expanduser().resolve()
        index = args.building_index.expanduser().resolve()
        output = args.output.expanduser().resolve()
        if args.rebuild_index or not index.is_file():
            print(json.dumps(build_index(args.plateau_local_dir, index), ensure_ascii=False, indent=2))
        print(json.dumps(build_review(source, index, output, args.profile, args.radius_m), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
