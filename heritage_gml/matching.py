from __future__ import annotations
from collections import defaultdict
import json

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
    meta = selected.setdefault(building.gml_id, {
        "complex_ids": [],
        "complex_names": [],
        "record_ids": [],
        "record_names": [],
        "record_types": [],
        "entity_classes": [],
        "methods": [],
        "movable_groups": [],
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
        "address": record.address,
        "category": record.category,
        "type": record.type,
        "entity_class": record.entity_class,
        "geometry_role": record.geometry_role,
        "complex_id": record.complex_id,
        "complex_name": record.complex_name,
        "reason": reason,
        "item_count": 1,
        "attached_items_json": "",
        "geometry": record.geometry,
    }


def _movable_group_key(record: CulturalRecord) -> tuple:
    addr = compact_address(record.address)
    if addr:
        return ("address", addr)
    # The instruction is to group by same address.  Missing-address records are
    # therefore not merged merely because they happen to share a coordinate.
    return ("record", record.record_id)


def _movable_item_dict(r: CulturalRecord) -> dict:
    return {
        "record_id": r.record_id,
        "name": r.name,
        "category": r.category,
        "type": r.type,
        "designation": r.designation,
        "designation_date": r.designation_date,
        "owner": r.owner,
        "address": r.address,
        "source_file": r.source_file,
    }


def match_city(records: list[CulturalRecord], buildings: list[BuildingRecord], cfg: dict):
    """Match cultural points to PLATEAU Buildings under the v0.3 policy.

    Rules:
    - No buffer, radius, nearest-neighbour or inferred cultural area.
    - building_direct: exact point-in-footprint plus optional exact name/address.
    - point: exact point-in-footprint only; if unmatched, emit as Heritage Point.
    - movable: group by same cultural address. Attach the list to Buildings that
      were already matched by a non-movable record at that address; otherwise
      try the group's own representative point. If still unmatched, emit one
      Heritage Point carrying the item list.
    """
    geoms = [b.geometry for b in buildings]
    tree = STRtree(geoms) if geoms else None

    selected = {}
    links = []
    point_rows = []
    movable_rows = []
    movable_group_rows = []
    unresolved_rows = []

    # Map a cultural source address to buildings established by non-movable records.
    matched_buildings_by_cultural_address: dict[str, set[str]] = defaultdict(set)
    building_by_id = {b.gml_id: b for b in buildings}

    # 1) Non-movable records are matched independently; point identity is retained
    # in the normalized record regardless of whether a Building is found.
    for r in [x for x in records if x.entity_class != "movable"]:
        methods_by_index: dict[int, set[str]] = defaultdict(set)

        if cfg.get("point_in_building", True) and r.geometry is not None:
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

        addr_key = compact_address(r.address)
        for i in final_indices:
            b = buildings[i]
            methods = sorted(methods_by_index[i])
            _select_meta_add(selected, b, r, methods)
            links.append({
                "record_id": r.record_id,
                "name": r.name,
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
            if addr_key:
                matched_buildings_by_cultural_address[addr_key].add(b.gml_id)

        if not final_indices:
            # building-direct also gets a point fallback to avoid data loss.
            reason = "building_direct_unmatched" if r.entity_class == "building_direct" else "point_not_in_building"
            point_rows.append(_point_row_from_record(r, reason))
            unresolved_rows.append({
                "entity_id": r.record_id,
                "entity_kind": "record",
                "name": r.name,
                "type": r.type,
                "entity_class": r.entity_class,
                "address": r.address,
                "reason": reason if r.geometry is not None else "missing_point_geometry",
            })

    # 2) Movable records: group strictly by same cultural address.
    movable_groups: dict[tuple, list[CulturalRecord]] = defaultdict(list)
    for r in [x for x in records if x.entity_class == "movable"]:
        movable_groups[_movable_group_key(r)].append(r)

    for seq, (_, rr) in enumerate(sorted(movable_groups.items(), key=lambda kv: min(x.record_id for x in kv[1])), 1):
        gid = f"{rr[0].municipality_code}-MG{seq:05d}"
        for r in rr:
            r.movable_group_id = gid

        address = next((r.address for r in rr if r.address), "")
        addr_key = compact_address(address)
        valid_points = [r.geometry for r in rr if r.geometry is not None and not r.geometry.is_empty]
        representative_point = valid_points[0] if valid_points else None
        coordinate_count = len({(round(g.x, 9), round(g.y, 9)) for g in valid_points})

        matched_ids: set[str] = set()
        methods: list[str] = []

        # First preference: another cultural record at the same address has
        # already established Building identity.
        if addr_key and matched_buildings_by_cultural_address.get(addr_key):
            matched_ids.update(matched_buildings_by_cultural_address[addr_key])
            methods.append("same_cultural_address")

        # Otherwise use exact point-in-building from any point carried by the group.
        if not matched_ids and cfg.get("point_in_building", True):
            for p in valid_points:
                for i in _point_building_indices(p, tree, geoms):
                    matched_ids.add(buildings[i].gml_id)
            if matched_ids:
                methods.append("movable_point_in_building")

        item_dicts = [_movable_item_dict(r) for r in rr]
        item_json = json.dumps(item_dicts, ensure_ascii=False, separators=(",", ":"))
        matched_list = sorted(matched_ids)

        for r in rr:
            r.matched_building_ids = matched_list
            r.match_methods = list(methods)

        for bid in matched_list:
            b = building_by_id[bid]
            # Movables are attached metadata; they do not become a direct
            # building-record association in record_ids.
            meta = selected.setdefault(bid, {
                "complex_ids": [], "complex_names": [], "record_ids": [],
                "record_names": [], "record_types": [], "entity_classes": [],
                "methods": [], "movable_groups": [],
            })
            meta["methods"] = unique_keep_order(meta["methods"] + methods)
            for r in rr:
                if r.complex_id and r.complex_id not in meta["complex_ids"]:
                    meta["complex_ids"].append(r.complex_id)
                if r.complex_name and r.complex_name not in meta["complex_names"]:
                    meta["complex_names"].append(r.complex_name)
            meta["movable_groups"].append({
                "group_id": gid,
                "address": address,
                "items": item_dicts,
                "match_methods": methods,
            })

        for r in rr:
            movable_rows.append({
                "movable_group_id": gid,
                "record_id": r.record_id,
                "name": r.name,
                "category": r.category,
                "type": r.type,
                "designation": r.designation,
                "address": r.address,
                "linked_building_ids": ";".join(matched_list),
                "match_methods": ";".join(methods),
                "source_file": r.source_file,
            })

        group_row = {
            "movable_group_id": gid,
            "address": address,
            "item_count": len(rr),
            "record_ids": ";".join(r.record_id for r in rr),
            "names": ";".join(r.name for r in rr),
            "linked_building_ids": ";".join(matched_list),
            "match_methods": ";".join(methods),
            "coordinate_count": coordinate_count,
            "point_status": "attached_to_building" if matched_list else ("point_output" if representative_point is not None else "unlocated"),
        }
        movable_group_rows.append(group_row)

        if not matched_list:
            point_rows.append({
                "point_id": f"movable:{gid}",
                "point_kind": "movable_group",
                "record_id": "",
                "record_ids": group_row["record_ids"],
                "name": rr[0].place_name or rr[0].complex_name or rr[0].name,
                "names": group_row["names"],
                "address": address,
                "category": "movable_group",
                "type": ";".join(unique_keep_order([r.type for r in rr if r.type])),
                "entity_class": "movable",
                "geometry_role": "address_group_point",
                "complex_id": ";".join(unique_keep_order([r.complex_id for r in rr if r.complex_id])),
                "complex_name": ";".join(unique_keep_order([r.complex_name for r in rr if r.complex_name])),
                "reason": "movable_group_not_in_building" if representative_point is not None else "movable_group_missing_point_geometry",
                "item_count": len(rr),
                "attached_items_json": item_json,
                "geometry": representative_point,
            })
            unresolved_rows.append({
                "entity_id": gid,
                "entity_kind": "movable_group",
                "name": rr[0].place_name or rr[0].complex_name or rr[0].name,
                "type": "movable_group",
                "entity_class": "movable",
                "address": address,
                "reason": "movable_group_not_in_building" if representative_point is not None else "movable_group_missing_point_geometry",
            })

    # 3) Complexes are semantic groups of the exact Buildings already matched
    # above. No buffer, convex hull, dissolve, or inferred site boundary is
    # created. The GPKG writer will preserve each member Building footprint as
    # one part of a MultiPolygon.
    by_complex = defaultdict(list)
    for r in records:
        by_complex[r.complex_id].append(r)

    complex_rows = []
    complex_member_rows = []
    for cid, rr in by_complex.items():
        bids = unique_keep_order([bid for r in rr for bid in r.matched_building_ids])
        complex_name = rr[0].complex_name
        complex_rows.append({
            "complex_id": cid,
            "complex_name": complex_name,
            "record_count": len(rr),
            "movable_item_count": sum(r.entity_class == "movable" for r in rr),
            "matched_building_count": len(bids),
            "building_gml_ids": ";".join(bids),
            "point_output_count": sum(
                1 for p in point_rows if p.get("complex_id") and cid in str(p.get("complex_id"))
            ),
            "status": "matched_building_complex" if bids else "point_or_unresolved",
        })

        for bid in bids:
            b = building_by_id.get(bid)
            if b is None:
                continue
            member_records = [r for r in rr if bid in r.matched_building_ids]
            complex_member_rows.append({
                "complex_id": cid,
                "complex_name": complex_name,
                "building_gml_id": b.gml_id,
                "building_id": b.building_id,
                "building_name": b.name,
                "building_address": b.address,
                "usage": b.usage,
                "detailed_usage": b.detailed_usage,
                "record_ids": ";".join(unique_keep_order([r.record_id for r in member_records if r.record_id])),
                "record_names": ";".join(unique_keep_order([r.name for r in member_records if r.name])),
                "record_types": ";".join(unique_keep_order([r.type for r in member_records if r.type])),
                "match_methods": ";".join(unique_keep_order([m for r in member_records for m in r.match_methods])),
                "source_gml": b.source_file,
            })

    return {
        "selected": selected,
        "links": links,
        "complex_rows": complex_rows,
        "complex_member_rows": complex_member_rows,
        "movable_rows": movable_rows,
        "movable_group_rows": movable_group_rows,
        "point_rows": point_rows,
        "unresolved_rows": unresolved_rows,
    }
