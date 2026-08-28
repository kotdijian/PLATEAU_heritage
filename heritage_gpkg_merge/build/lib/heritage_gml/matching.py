from __future__ import annotations
from collections import defaultdict

from shapely.strtree import STRtree

from .model import CulturalRecord, BuildingRecord
from .util import norm_key, compact_address, unique_keep_order


def _point_building_indices(point, tree, geoms) -> list[int]:
    """Return exact footprint hits. No buffer or nearest-neighbour logic."""
    if point is None or point.is_empty or tree is None:
        return []
    out = []
    for raw_i in tree.query(point):
        i = int(raw_i)
        bg = geoms[i]
        if bg.contains(point) or bg.touches(point):
            out.append(i)
    return sorted(set(out))


def _building_direct_semantic_indices(record: CulturalRecord, buildings, cfg: dict) -> dict[int, set[str]]:
    """Optional exact semantic matches for building-direct records only."""
    hits: dict[int, set[str]] = defaultdict(set)

    if cfg.get("building_direct_exact_name", True):
        keys = {norm_key(record.name), norm_key(record.place_name)} - {""}
        if keys:
            for i, b in enumerate(buildings):
                bk = norm_key(b.name)
                if bk and bk in keys:
                    hits[i].add("exact_name")

    if cfg.get("building_direct_exact_address", True):
        rk = compact_address(record.address)
        if rk:
            for i, b in enumerate(buildings):
                bk = compact_address(b.address)
                if bk and bk == rk:
                    hits[i].add("exact_address")

    return hits


def _select_meta_add(selected: dict, building: BuildingRecord, record: CulturalRecord, methods: list[str]):
    """Attach a record to a selected Building with one uniform schema.

    v0.5 deliberately does not special-case movable cultural properties here.
    """
    meta = selected.setdefault(building.gml_id, {
        "complex_ids": [],
        "complex_names": [],
        "record_ids": [],
        "record_names": [],
        "record_types": [],
        "entity_classes": [],
        "methods": [],
    })
    for key, value in [
        ("complex_ids", record.complex_id),
        ("complex_names", record.complex_name),
        ("record_ids", record.record_id),
        ("record_names", record.name),
        ("record_types", record.type),
        ("entity_classes", record.entity_class),
    ]:
        if value and value not in meta[key]:
            meta[key].append(value)
    meta["methods"] = unique_keep_order(meta["methods"] + list(methods))


def _point_row_from_record(record: CulturalRecord, reason: str) -> dict:
    return {
        "point_id": f"record:{record.record_id}",
        "point_kind": "cultural_record",
        "record_id": record.record_id,
        "record_ids": record.record_id,
        "name": record.name,
        "names": record.name,
        "place_name": record.place_name,
        "address_detail": record.address_detail,
        "address": record.address,
        "category": record.category,
        "type": record.type,
        "entity_class": record.entity_class,
        "geometry_role": record.geometry_role,
        "source_location_role": record.source_location_role,
        "spatial_match_status": record.spatial_match_status,
        "complex_id": record.complex_id,
        "complex_name": record.complex_name,
        "complex_grouping_method": record.complex_grouping_method,
        "reason": reason,
        "item_count": 1,
        "attached_items_json": "",
        "geometry": record.geometry,
    }


def _allow_point_match(record: CulturalRecord, cfg: dict) -> bool:
    if not cfg.get("point_in_building", True):
        return False
    if record.geometry is None:
        return False
    # A coordinate repeated by multiple records inside the same semantic
    # complex is often a site/complex representative observation rather than
    # the exact position of every object. Do not silently use it as a Building
    # locator unless explicitly requested.
    if (
        record.source_location_role == "shared_complex_coordinate"
        and not cfg.get("match_shared_complex_coordinates", False)
    ):
        return False
    return True


def match_city(records: list[CulturalRecord], buildings: list[BuildingRecord], cfg: dict):
    """Match cultural records and construct Building Complex membership.

    v0.5 rules:
    - No buffer, radius, nearest-neighbour, convex hull, or inferred area.
    - Every record, including movable cultural properties, follows the same
      individual record matching/output path.
    - Exact point-in-footprint is available to all records, subject to the
      shared-complex-coordinate safeguard.
    - building_direct records may additionally use exact normalized name/address.
    - Building Complex geometry is derived only from Buildings directly matched
      by at least one record in that complex. Records are never propagated to a
      specific member Building merely because they share a complex.
    - A multi-record semantic complex with no direct Building match is retained
      as `complex_only`; its source points remain in heritage_records, but are
      not duplicated into the standalone heritage_points fallback layer.
    """
    geoms = [b.geometry for b in buildings]
    tree = STRtree(geoms) if geoms else None
    building_by_id = {b.gml_id: b for b in buildings}

    selected: dict[str, dict] = {}
    links: list[dict] = []
    point_rows: list[dict] = []
    unresolved_rows: list[dict] = []

    # 1) Uniform per-record direct matching.
    for r in records:
        methods_by_index: dict[int, set[str]] = defaultdict(set)

        if _allow_point_match(r, cfg):
            for i in _point_building_indices(r.geometry, tree, geoms):
                methods_by_index[i].add("point_in_building")

        if r.entity_class == "building_direct":
            for i, methods in _building_direct_semantic_indices(r, buildings, cfg).items():
                methods_by_index[i].update(methods)

        final_indices = sorted(methods_by_index)
        r.matched_building_ids = [buildings[i].gml_id for i in final_indices]
        r.match_methods = unique_keep_order(
            [m for i in final_indices for m in sorted(methods_by_index[i])]
        )

        if final_indices:
            r.spatial_match_status = "building_matched"

        for i in final_indices:
            b = buildings[i]
            methods = sorted(methods_by_index[i])
            _select_meta_add(selected, b, r, methods)
            links.append({
                "record_id": r.record_id,
                "name": r.name,
                "place_name": r.place_name,
                "address_detail": r.address_detail,
                "type": r.type,
                "entity_class": r.entity_class,
                "complex_id": r.complex_id,
                "complex_name": r.complex_name,
                "building_gml_id": b.gml_id,
                "building_id": b.building_id,
                "building_name": b.name,
                "building_address": b.address,
                "usage": b.usage,
                "detailed_usage": b.detailed_usage,
                "match_methods": ";".join(methods),
                "source_gml": b.source_file,
            })

    # 2) Build semantic Complex -> directly established Building membership.
    by_complex: dict[str, list[CulturalRecord]] = defaultdict(list)
    for r in records:
        by_complex[r.complex_id].append(r)

    complex_rows: list[dict] = []
    complex_member_rows: list[dict] = []
    complex_record_rows: list[dict] = []
    complex_info: dict[str, dict] = {}

    for cid, rr in by_complex.items():
        bids = unique_keep_order([bid for r in rr for bid in r.matched_building_ids])
        complex_name = rr[0].complex_name
        grouping_method = rr[0].complex_grouping_method
        is_multi_record = len(rr) > 1

        if bids:
            status = "matched_building_complex"
        elif is_multi_record:
            status = "complex_only"
        else:
            status = "unresolved"

        shared_count = sum(r.source_location_role == "shared_complex_coordinate" for r in rr)
        directly_matched_count = sum(bool(r.matched_building_ids) for r in rr)
        complex_only_count = sum(not r.matched_building_ids for r in rr) if is_multi_record else 0

        summary = {
            "complex_id": cid,
            "complex_name": complex_name,
            "grouping_method": grouping_method,
            "record_count": len(rr),
            "movable_record_count": sum(r.entity_class == "movable" for r in rr),
            "directly_matched_record_count": directly_matched_count,
            "complex_only_record_count": complex_only_count,
            "shared_coordinate_record_count": shared_count,
            "matched_building_count": len(bids),
            "building_gml_ids": ";".join(bids),
            "point_output_count": 0,  # filled after fallback-point generation
            "status": status,
        }
        complex_rows.append(summary)
        complex_info[cid] = {"status": status, "bids": bids, "summary": summary, "records": rr}

        for bid in bids:
            b = building_by_id.get(bid)
            if b is None:
                continue
            member_records = [r for r in rr if bid in r.matched_building_ids]
            complex_member_rows.append({
                "complex_id": cid,
                "complex_name": complex_name,
                "grouping_method": grouping_method,
                "building_gml_id": b.gml_id,
                "building_id": b.building_id,
                "building_name": b.name,
                "building_address": b.address,
                "usage": b.usage,
                "detailed_usage": b.detailed_usage,
                "record_ids": ";".join(unique_keep_order([r.record_id for r in member_records if r.record_id])),
                "record_names": ";".join(unique_keep_order([r.name for r in member_records if r.name])),
                "record_types": ";".join(unique_keep_order([r.type for r in member_records if r.type])),
                "entity_classes": ";".join(unique_keep_order([r.entity_class for r in member_records if r.entity_class])),
                "match_methods": ";".join(unique_keep_order([m for r in member_records for m in r.match_methods])),
                "source_gml": b.source_file,
            })

        for r in rr:
            if r.matched_building_ids:
                association_status = "direct_building_match"
            elif is_multi_record:
                association_status = "complex_only"
            else:
                association_status = "unresolved"
            complex_record_rows.append({
                "complex_id": cid,
                "complex_name": complex_name,
                "grouping_method": grouping_method,
                "record_id": r.record_id,
                "name": r.name,
                "place_name": r.place_name,
                "address_detail": r.address_detail,
                "type": r.type,
                "entity_class": r.entity_class,
                "source_location_role": r.source_location_role,
                "association_status": association_status,
                "matched_building_ids": ";".join(r.matched_building_ids),
                "match_methods": ";".join(r.match_methods),
            })

    # 3) Fallback outputs and record-level status.
    for r in records:
        if r.matched_building_ids:
            continue

        info = complex_info.get(r.complex_id, {})
        complex_status = info.get("status", "unresolved")
        complex_bids = info.get("bids", [])

        if complex_status in {"matched_building_complex", "complex_only"} and r.complex_record_count > 1:
            # The record has a meaningful semantic Complex association. Do not
            # duplicate it into the standalone point fallback layer. The source
            # point remains available in heritage_records.
            r.spatial_match_status = "complex_only"
            reason = (
                "complex_member_without_direct_building_match"
                if complex_bids else "complex_only_no_building_match"
            )
        else:
            r.spatial_match_status = "point_unmatched" if r.geometry is not None else "unlocated"
            if r.entity_class == "building_direct":
                reason = "building_direct_unmatched"
            else:
                reason = "record_not_in_building"
            if r.geometry is not None:
                point_rows.append(_point_row_from_record(r, reason))

        unresolved_rows.append({
            "entity_id": r.record_id,
            "entity_kind": "record",
            "name": r.name,
            "place_name": r.place_name,
            "address_detail": r.address_detail,
            "type": r.type,
            "entity_class": r.entity_class,
            "complex_id": r.complex_id,
            "complex_name": r.complex_name,
            "source_location_role": r.source_location_role,
            "spatial_match_status": r.spatial_match_status,
            "address": r.address,
            "reason": reason if r.geometry is not None else (
                "complex_only_missing_source_geometry" if r.spatial_match_status == "complex_only" else "missing_point_geometry"
            ),
        })

    point_counts: dict[str, int] = defaultdict(int)
    for p in point_rows:
        if p.get("complex_id"):
            point_counts[p["complex_id"]] += 1
    for row in complex_rows:
        row["point_output_count"] = point_counts.get(row["complex_id"], 0)

    return {
        "selected": selected,
        "links": links,
        "point_rows": point_rows,
        "complex_rows": complex_rows,
        "complex_member_rows": complex_member_rows,
        "complex_record_rows": complex_record_rows,
        "unresolved_rows": unresolved_rows,
    }
