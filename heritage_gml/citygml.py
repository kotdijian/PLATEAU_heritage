from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import json
import re
from lxml import etree
from pyproj import CRS, Transformer
from shapely.geometry import Polygon
from shapely.ops import unary_union
from .model import BuildingRecord, PlateauFile

GML_NS = "http://www.opengis.net/gml"
GEN2_NS = "http://www.opengis.net/citygml/generics/2.0"
GEN3_NS = "http://www.opengis.net/citygml/generics/3.0"

def localname(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag

def _find_building(member):
    for e in member.iter():
        if localname(e.tag) == "Building":
            return e
    return None

def _all_text(element, wanted: str) -> list[str]:
    out = []
    for e in element.iter():
        if localname(e.tag) == wanted:
            txt = " ".join(x.strip() for x in e.itertext() if x and x.strip())
            if txt:
                out.append(txt)
    return out

def _first_text(element, names: tuple[str, ...]) -> str:
    for n in names:
        vals = _all_text(element, n)
        if vals:
            return vals[0]
    return ""

def _address_text(building) -> str:
    for e in building.iter():
        if localname(e.tag) == "Address":
            vals = []
            for x in e.iter():
                if x is e:
                    continue
                if len(x) == 0 and x.text and x.text.strip():
                    vals.append(x.text.strip())
            if vals:
                return " ".join(dict.fromkeys(vals))
    return ""

def _srs_name(e):
    cur = e
    while cur is not None:
        s = cur.get("srsName")
        if s:
            return s
        cur = cur.getparent()
    return None

def _dimension(e, vals, srs):
    cur = e
    while cur is not None:
        d = cur.get("srsDimension")
        if d:
            try:
                return int(d)
            except Exception:
                pass
        cur = cur.getparent()
    if srs and ("6697" in srs or "6668" in srs):
        return 3
    return 3 if len(vals) % 3 == 0 else 2

def _crs(srs):
    if not srs:
        return None
    m = re.search(r"EPSG(?:/0/|::|:)(\\d+)", srs, re.I)
    if m:
        return CRS.from_epsg(int(m.group(1)))
    try:
        return CRS.from_user_input(srs)
    except Exception:
        return None

def _pair_to_lonlat(a, b, srs):
    if 20 <= a <= 50 and 120 <= b <= 155:
        return b, a
    if 120 <= a <= 155 and 20 <= b <= 50:
        return a, b
    crs = _crs(srs)
    if crs is None:
        raise ValueError(f"Unknown CRS/axis order: {srs}")
    return Transformer.from_crs(crs, 4326, always_xy=True).transform(a, b)

def _polygon_from_poslist(e):
    vals = [float(v) for v in (e.text or "").split()]
    if len(vals) < 6:
        return None
    srs = _srs_name(e)
    dim = _dimension(e, vals, srs)
    pts = []
    for i in range(0, len(vals) - dim + 1, dim):
        pts.append(_pair_to_lonlat(vals[i], vals[i+1], srs))
    if len(pts) < 3:
        return None
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    p = Polygon(pts)
    if not p.is_valid:
        p = p.buffer(0)
    return p if not p.is_empty else None

def footprint(building):
    preferred = []
    for e in building.iter():
        if localname(e.tag) in ("lod0FootPrint", "lod0RoofEdge"):
            preferred.extend(x for x in e.iter() if localname(x.tag) == "posList")
    poslists = preferred or [x for x in building.iter() if localname(x.tag) == "posList"]
    polys = []
    for e in poslists:
        try:
            p = _polygon_from_poslist(e)
        except Exception:
            p = None
        if p is not None:
            polys.append(p)
    if not polys:
        return None
    g = unary_union(polys)
    if g.is_empty:
        return None
    if g.geom_type not in ("Polygon", "MultiPolygon") or g.area == 0:
        g = unary_union([p.convex_hull for p in polys]).convex_hull
    if not g.is_valid:
        g = g.buffer(0)
    return g

def scan_buildings(files: list[PlateauFile]):
    out = []
    for pf in files:
        if not pf.local_path:
            continue
        context = etree.iterparse(pf.local_path, events=("end",), huge_tree=True, recover=True)
        for _, elem in context:
            if localname(elem.tag) != "cityObjectMember":
                continue
            b = _find_building(elem)
            if b is not None:
                gid = b.get(f"{{{GML_NS}}}id") or b.get("id") or ""
                if gid:
                    try:
                        fp = footprint(b)
                    except Exception:
                        fp = None
                    if fp is not None and not fp.is_empty:
                        out.append(BuildingRecord(
                            gml_id=gid, source_file=pf.local_path, city_code=pf.city_code,
                            file_code=pf.code, geometry=fp,
                            name=_first_text(b, ("name",)), address=_address_text(b),
                            usage=_first_text(b, ("usage",)),
                            detailed_usage=_first_text(b, ("detailedUsage",)),
                            building_id=_first_text(b, ("buildingID","buildingId")),
                        ))
            elem.clear()
            parent = elem.getparent()
            if parent is not None:
                while elem.getprevious() is not None:
                    del parent[0]
        del context
    return list({b.gml_id: b for b in out}.values())

def _root_info(path: str):
    for _, elem in etree.iterparse(path, events=("start",), huge_tree=True):
        return elem.tag, dict(elem.nsmap), dict(elem.attrib)
    raise ValueError(f"Empty CityGML: {path}")

def _add_generic(building, name: str, value: str, gen_ns: str):
    if not value:
        return
    node = etree.SubElement(building, f"{{{gen_ns}}}stringAttribute")
    node.set("name", name)
    v = etree.SubElement(node, f"{{{gen_ns}}}value")
    v.text = value

def write_subset(output_path: str | Path, files: list[PlateauFile], selected: dict[str, dict],
                 embed_generic: bool = True):
    first = next((f.local_path for f in files if f.local_path), None)
    if not first:
        raise ValueError("No local CityGML path.")
    root_tag, nsmap, attrib = _root_info(first)
    root_ns = root_tag.split("}", 1)[0].lstrip("{") if "}" in root_tag else ""
    gen_ns = GEN3_NS if root_ns.endswith("/3.0") else GEN2_NS
    if embed_generic and "gen" not in nsmap:
        nsmap["gen"] = gen_ns
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    written = set()
    with etree.xmlfile(str(out), encoding="UTF-8") as xf:
        xf.write_declaration()
        with xf.element(root_tag, attrib=attrib, nsmap=nsmap):
            n = etree.Element(f"{{{GML_NS}}}name")
            n.text = "Heritage-GML: PLATEAU building subset"
            xf.write(n)
            for pf in files:
                if not pf.local_path:
                    continue
                context = etree.iterparse(pf.local_path, events=("end",), huge_tree=True, recover=True)
                for _, elem in context:
                    if localname(elem.tag) != "cityObjectMember":
                        continue
                    b = _find_building(elem)
                    if b is not None:
                        gid = b.get(f"{{{GML_NS}}}id") or b.get("id") or ""
                        if gid in selected and gid not in written:
                            member = deepcopy(elem)
                            if embed_generic:
                                bb = _find_building(member)
                                meta = selected[gid]
                                _add_generic(bb, "heritageComplexId", ";".join(meta.get("complex_ids", [])), gen_ns)
                                _add_generic(bb, "heritageComplexName", ";".join(meta.get("complex_names", [])), gen_ns)
                                _add_generic(bb, "heritageRecordIds", ";".join(meta.get("record_ids", [])), gen_ns)
                                _add_generic(bb, "heritageRecordNames", ";".join(meta.get("record_names", [])), gen_ns)
                                _add_generic(bb, "heritageRecordTypes", ";".join(meta.get("record_types", [])), gen_ns)
                                _add_generic(bb, "heritageEntityClasses", ";".join(meta.get("entity_classes", [])), gen_ns)
                                _add_generic(bb, "heritageMatchMethod", ";".join(meta.get("methods", [])), gen_ns)
                                if meta.get("movable_groups"):
                                    _add_generic(
                                        bb, "heritageMovableItemsJson",
                                        json.dumps(meta["movable_groups"], ensure_ascii=False, separators=(",", ":")),
                                        gen_ns,
                                    )
                            xf.write(member)
                            written.add(gid)
                    elem.clear()
                    parent = elem.getparent()
                    if parent is not None:
                        while elem.getprevious() is not None:
                            del parent[0]
                del context
    return written
