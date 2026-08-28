from __future__ import annotations
from lxml import etree
from shapely.geometry import mapping

from .util import json_dump


HG_NS = "urn:heritage-gml:prototype:0.4"
GML_NS = "http://www.opengis.net/gml"


def _geometry_json(g):
    return mapping(g) if g is not None and not g.is_empty else None


def _record_dict(r):
    return {
        "record_id": r.record_id,
        "name": r.name,
        "place_name": r.place_name,
        "owner": r.owner,
        "address": r.address,
        "category": r.category,
        "type": r.type,
        "designation": r.designation,
        "designation_date": r.designation_date,
        "entity_class": r.entity_class,
        "geometry_role": r.geometry_role,
        "geometry": _geometry_json(r.geometry),
        "complex_id": r.complex_id,
        "complex_name": r.complex_name,
        "movable_group_id": r.movable_group_id,
        "matched_building_gml_ids": r.matched_building_ids,
        "match_methods": r.match_methods,
        "source_file": r.source_file,
    }


def build_heritage_document(city_code, city_name, records, buildings, match_result):
    selected = match_result["selected"]
    building_by_id = {b.gml_id: b for b in buildings}

    building_entities = []
    for gid in sorted(selected):
        meta = selected[gid]
        b = building_by_id.get(gid)
        building_entities.append({
            "gml_id": gid,
            "building_id": b.building_id if b else "",
            "name": b.name if b else "",
            "address": b.address if b else "",
            "record_ids": meta.get("record_ids", []),
            "record_names": meta.get("record_names", []),
            "record_types": meta.get("record_types", []),
            "entity_classes": meta.get("entity_classes", []),
            "complex_ids": meta.get("complex_ids", []),
            "match_methods": meta.get("methods", []),
            "movable_groups": meta.get("movable_groups", []),
        })

    point_entities = []
    for p in match_result["point_rows"]:
        d = {k: v for k, v in p.items() if k != "geometry"}
        d["geometry"] = _geometry_json(p.get("geometry"))
        point_entities.append(d)

    return {
        "type": "HeritageGMLPrototype",
        "version": "0.4",
        "namespace": HG_NS,
        "note": "Prototype companion model; not an official CityGML ADE.",
        "municipality_code": city_code,
        "municipality_name": city_name,
        "records": [_record_dict(r) for r in records],
        "buildings": building_entities,
        "points": point_entities,
        "movable_groups": match_result["movable_group_rows"],
        "complexes": match_result["complex_rows"],
        "complex_members": match_result.get("complex_member_rows", []),
    }


def write_json(path, doc):
    json_dump(path, doc)


def _text(parent, name, value):
    if value in (None, ""):
        return
    e = etree.SubElement(parent, f"{{{HG_NS}}}{name}")
    e.text = str(value)


def write_xml(path, doc):
    root = etree.Element(f"{{{HG_NS}}}HeritageDataset", nsmap={"hg": HG_NS, "gml": GML_NS})
    root.set("version", str(doc.get("version", "0.4")))
    _text(root, "municipalityCode", doc.get("municipality_code"))
    _text(root, "municipalityName", doc.get("municipality_name"))

    bes = etree.SubElement(root, f"{{{HG_NS}}}buildingEntities")
    for b in doc.get("buildings", []):
        be = etree.SubElement(bes, f"{{{HG_NS}}}BuildingAssociation")
        be.set("gmlId", b["gml_id"])
        _text(be, "name", b.get("name"))
        _text(be, "address", b.get("address"))
        for cid in b.get("complex_ids", []):
            c = etree.SubElement(be, f"{{{HG_NS}}}ComplexReference")
            c.set("complexId", cid)
        for rid in b.get("record_ids", []):
            r = etree.SubElement(be, f"{{{HG_NS}}}CulturalRecordReference")
            r.set("recordId", rid)
        for group in b.get("movable_groups", []):
            mg = etree.SubElement(be, f"{{{HG_NS}}}MovableGroup")
            mg.set("id", group.get("group_id", ""))
            _text(mg, "address", group.get("address"))
            for item in group.get("items", []):
                it = etree.SubElement(mg, f"{{{HG_NS}}}MovableItem")
                it.set("recordId", str(item.get("record_id", "")))
                _text(it, "name", item.get("name"))
                _text(it, "type", item.get("type"))

    ces = etree.SubElement(root, f"{{{HG_NS}}}buildingComplexes")
    members_by_complex = {}
    for m in doc.get("complex_members", []):
        members_by_complex.setdefault(m.get("complex_id", ""), []).append(m)
    for c in doc.get("complexes", []):
        cid = c.get("complex_id", "")
        members = members_by_complex.get(cid, [])
        if not members:
            continue
        ce = etree.SubElement(ces, f"{{{HG_NS}}}BuildingComplex")
        ce.set("id", cid)
        _text(ce, "name", c.get("complex_name"))
        _text(ce, "recordCount", c.get("record_count"))
        for m in members:
            me = etree.SubElement(ce, f"{{{HG_NS}}}BuildingMember")
            me.set("gmlId", str(m.get("building_gml_id", "")))
            _text(me, "buildingId", m.get("building_id"))
            _text(me, "recordIds", m.get("record_ids"))
            _text(me, "matchMethods", m.get("match_methods"))

    pes = etree.SubElement(root, f"{{{HG_NS}}}pointEntities")
    for p in doc.get("points", []):
        pe = etree.SubElement(pes, f"{{{HG_NS}}}HeritagePoint")
        pe.set("id", str(p.get("point_id", "")))
        pe.set("kind", str(p.get("point_kind", "")))
        _text(pe, "name", p.get("name"))
        _text(pe, "address", p.get("address"))
        _text(pe, "type", p.get("type"))
        _text(pe, "entityClass", p.get("entity_class"))
        _text(pe, "reason", p.get("reason"))
        geom = p.get("geometry")
        if geom and geom.get("type") == "Point":
            coords = geom.get("coordinates") or []
            if len(coords) >= 2:
                pt = etree.SubElement(pe, f"{{{GML_NS}}}Point")
                pt.set("srsName", "http://www.opengis.net/def/crs/OGC/1.3/CRS84")
                pos = etree.SubElement(pt, f"{{{GML_NS}}}pos")
                # CRS84 explicitly uses longitude, latitude order.
                pos.text = f"{coords[0]} {coords[1]}"

    etree.ElementTree(root).write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)
