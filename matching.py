from __future__ import annotations
from collections import defaultdict
from shapely.strtree import STRtree
from .model import CulturalRecord, BuildingRecord
from .util import norm_key, compact_address, unique_keep_order

def religious_kind(text: str) -> str | None:
    s = text or ""
    if any(k in s for k in ("神社","神宮","天満宮","八幡宮","稲荷")):
        return "shrine"
    if any(k in s for k in ("寺","寺院","観音","不動尊","阿弥陀")):
        return "temple"
    return None

def _name_match(complex_name: str, b: BuildingRecord, min_chars: int) -> bool:
    ck = norm_key(complex_name)
    if len(ck) < min_chars:
        return False
    for x in (norm_key(b.name), norm_key(b.address)):
        if x and (ck in x or x in ck):
            return True
    return False

def _address_match(addresses: list[str], b: BuildingRecord) -> bool:
    ba = compact_address(b.address)
    if not ba:
        return False
    for a in addresses:
        ca = compact_address(a)
        if ca and (ca == ba or ca in ba or ba in ca):
            return True
    return False

def _usage_match(kind: str | None, b: BuildingRecord, cfg: dict) -> bool:
    if not kind:
        return True
    codes = cfg.get("religious_usage_codes") or {}
    wanted = [str(x) for x in codes.get(kind, [])]
    generic = [str(x) for x in codes.get("generic", [])]
    u = str(b.detailed_usage or b.usage or "")
    return any(u == c or u.startswith(c) for c in wanted + generic)

def match_city(records: list[CulturalRecord], buildings: list[BuildingRecord], cfg: dict):
    by_complex = defaultdict(list)
    for r in records:
        by_complex[r.complex_id].append(r)

    geoms = [b.geometry for b in buildings]
    tree = STRtree(geoms) if geoms else None

    selected, links, complex_rows, movable_rows, unresolved_rows = {}, [], [], [], []
    min_name_chars = int(cfg.get("minimum_semantic_name_chars", 2))

    for cid, rr in by_complex.items():
        cname = rr[0].complex_name
        nonmovable = [r for r in rr if not r.movable]
        addresses = unique_keep_order([r.address for r in rr if r.address])
        kind = religious_kind(" ".join([cname] + [r.name for r in rr] + [r.owner for r in rr]))
        methods_by_index = defaultdict(set)

        # No buffer: polygon intersection or point-in-building only.
        for r in nonmovable:
            g = r.geometry
            if g is None or g.is_empty or tree is None:
                continue
            for raw_i in tree.query(g):
                i = int(raw_i)
                bg = geoms[i]
                if g.geom_type in ("Polygon","MultiPolygon"):
                    if g.intersects(bg):
                        methods_by_index[i].add("range_polygon_intersection")
                elif g.geom_type == "Point":
                    if bg.contains(g) or bg.touches(g):
                        methods_by_index[i].add("point_in_building")
                elif g.intersects(bg):
                    methods_by_index[i].add("geometry_intersection")

        # Semantic matches are limited to already acquired relevant CityGML files.
        for i, b in enumerate(buildings):
            if cfg.get("name_match", True) and _name_match(cname, b, min_name_chars):
                methods_by_index[i].add("name_match")
            if cfg.get("address_match", True) and _address_match(addresses, b):
                methods_by_index[i].add("address_match")

        # Usage can expand only across the same exact normalized address once anchored.
        if cfg.get("usage_match", True) and cfg.get("allow_address_expansion", True) and kind:
            anchored_addresses = {compact_address(buildings[i].address) for i in methods_by_index
                                  if compact_address(buildings[i].address)}
            for i, b in enumerate(buildings):
                ba = compact_address(b.address)
                if ba and ba in anchored_addresses and _usage_match(kind, b, cfg):
                    methods_by_index[i].add("same_address_usage")

        final_indices = sorted(methods_by_index)
        b_ids = [buildings[i].gml_id for i in final_indices]

        if not final_indices:
            unresolved_rows.append({
                "complex_id": cid, "complex_name": cname,
                "reason": "no_building_match_without_fixed_distance",
                "record_count": len(rr)
            })

        for i in final_indices:
            b = buildings[i]
            methods = sorted(methods_by_index[i])
            meta = selected.setdefault(b.gml_id, {"complex_ids": [], "complex_names": [], "methods": []})
            if cid not in meta["complex_ids"]:
                meta["complex_ids"].append(cid)
            if cname not in meta["complex_names"]:
                meta["complex_names"].append(cname)
            meta["methods"] = unique_keep_order(meta["methods"] + methods)
            links.append({
                "complex_id": cid, "complex_name": cname,
                "building_gml_id": b.gml_id, "building_id": b.building_id,
                "building_name": b.name, "building_address": b.address,
                "usage": b.usage, "detailed_usage": b.detailed_usage,
                "match_methods": ";".join(methods), "source_gml": b.source_file
            })

        for r in rr:
            if r.movable:
                movable_rows.append({
                    "complex_id": cid, "complex_name": cname,
                    "record_id": r.record_id, "name": r.name,
                    "category": r.category, "type": r.type,
                    "designation": r.designation, "address": r.address,
                    "linked_building_ids": ";".join(b_ids),
                    "source_file": r.source_file
                })

        complex_rows.append({
            "complex_id": cid, "complex_name": cname,
            "record_count": len(rr),
            "movable_item_count": sum(r.movable for r in rr),
            "matched_building_count": len(final_indices),
            "building_gml_ids": ";".join(b_ids),
            "religious_kind": kind or "",
            "status": "matched" if final_indices else "unresolved"
        })

    return selected, links, complex_rows, movable_rows, unresolved_rows
