from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import re
from lxml import etree
from pyproj import CRS, Transformer
from shapely.geometry import Polygon
from shapely.ops import unary_union
from .model import BuildingRecord, DisasterRiskRecord, PlateauFile

GML_NS = "http://www.opengis.net/gml"
GEN2_NS = "http://www.opengis.net/citygml/generics/2.0"
GEN3_NS = "http://www.opengis.net/citygml/generics/3.0"


class CityGMLReadError(RuntimeError):
    """CityGML could not be read from its local path."""

    def __init__(self, path: str | Path, stage: str, original: Exception):
        self.path = str(path)
        self.stage = stage
        self.original = original
        super().__init__(
            f"CityGML read failed during {stage}: {self.path} "
            f"({type(original).__name__}: {original})"
        )


def _raise_read_error(path: str | Path, stage: str, exc: Exception):
    raise CityGMLReadError(path, stage, exc) from exc

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



RISK_TYPES = {
    "RiverFloodingRiskAttribute": ("river_flooding", "洪水浸水想定区域"),
    "TsunamiRiskAttribute": ("tsunami", "津波浸水想定"),
    "HighTideRiskAttribute": ("high_tide", "高潮浸水想定"),
    "InlandFloodingRiskAttribute": ("inland_flooding", "内水浸水想定"),
    "ReservoirFloodingRiskAttribute": ("reservoir_flooding", "ため池ハザードマップ"),
    "LandSlideRiskAttribute": ("landslide", "土砂災害警戒区域"),
}


def _float_text(e):
    if e is None or e.text is None:
        return None
    try:
        return float(e.text.strip())
    except Exception:
        return None


def _int_text(e):
    value = _float_text(e)
    if value is None or not value.is_integer():
        return None
    return int(value)


def _length_m(e):
    value = _float_text(e)
    if value is None:
        return None
    unit = (e.get("uom") or "m").strip().lower()
    if unit in ("", "m", "meter", "metre", "meters", "metres"):
        return value
    if unit in ("cm", "centimeter", "centimetre", "centimeters", "centimetres"):
        return value / 100.0
    if unit in ("mm", "millimeter", "millimetre", "millimeters", "millimetres"):
        return value / 1000.0
    return None


def _first_child(element, wanted: str):
    for e in element.iter():
        if e is element:
            continue
        if localname(e.tag) == wanted:
            return e
    return None


def _normalize_depth_m(value, uom: str):
    if value is None:
        return None
    u = (uom or "").strip().lower()
    if u in ("", "m", "meter", "metre", "meters", "metres"):
        return float(value)
    if u in ("cm", "centimeter", "centimetre", "centimeters", "centimetres"):
        return float(value) / 100.0
    if u in ("mm", "millimeter", "millimetre", "millimeters", "millimetres"):
        return float(value) / 1000.0
    return None


def _normalize_duration_h(value, uom: str):
    if value is None:
        return None
    u = (uom or "").strip().lower()
    if u in ("", "hour", "hours", "h", "hr", "hrs"):
        return float(value)
    if u in ("minute", "minutes", "min", "mins"):
        return float(value) / 60.0
    if u in ("second", "seconds", "s", "sec", "secs"):
        return float(value) / 3600.0
    return None


def _codelist_candidates(source_gml: str | Path, codespace: str):
    """Yield local codelist paths without performing network access."""
    if not codespace:
        return []
    cs = str(codespace).strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", cs, re.I):
        return []
    src = Path(source_gml).resolve()
    candidates = []
    direct = (src.parent / cs).resolve()
    candidates.append(direct)
    base = Path(cs).name
    # PLATEAU local packages normally place code lists under a codelists
    # directory beside/above the udx tree. Search ancestors as a fallback.
    for ancestor in [src.parent, *src.parents]:
        candidates.append((ancestor / "codelists" / base).resolve())
    out = []
    seen = set()
    for c in candidates:
        key = str(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


_CODELIST_CACHE = {}


def _read_codelist(path: Path):
    key = str(path)
    if key in _CODELIST_CACHE:
        return _CODELIST_CACHE[key]
    mapping = {}
    if not path.exists() or not path.is_file():
        _CODELIST_CACHE[key] = mapping
        return mapping
    try:
        root = etree.parse(str(path)).getroot()
        # Standard GML Dictionary / Definition representation. Accept both
        # gml:name and gml:identifier as the code, with description/name as label.
        for d in root.iter():
            if localname(d.tag) != "Definition":
                continue
            names = []
            identifiers = []
            descriptions = []
            for x in d.iter():
                ln = localname(x.tag)
                text = (x.text or "").strip()
                if not text:
                    continue
                if ln == "name":
                    names.append(text)
                elif ln == "identifier":
                    identifiers.append(text)
                elif ln == "description":
                    descriptions.append(text)
            code = identifiers[0] if identifiers else (names[0] if names else "")
            if descriptions:
                label = descriptions[0]
            elif identifiers and names:
                label = names[0]
            else:
                label = names[1] if len(names) > 1 else ""
            if code and label:
                mapping[code] = label
        # Some distributions use simple code/label XML without GML Definition.
        if not mapping:
            for parent in root.iter():
                children = [x for x in parent if isinstance(x.tag, str)]
                if len(children) < 2:
                    continue
                values = [(localname(x.tag).lower(), (x.text or "").strip()) for x in children]
                code = next((v for n, v in values if v and n in ("code", "value", "identifier")), "")
                label = next((v for n, v in values if v and n in ("label", "description", "name")), "")
                if code and label:
                    mapping[code] = label
    except Exception:
        mapping = {}
    _CODELIST_CACHE[key] = mapping
    return mapping


def _code_info(element, field_name: str, source_gml: str | Path):
    e = _first_child(element, field_name)
    if e is None:
        return "", "", ""
    code = (e.text or "").strip()
    codespace = (e.get("codeSpace") or "").strip()
    label = ""
    if code and codespace:
        for candidate in _codelist_candidates(source_gml, codespace):
            mapping = _read_codelist(candidate)
            if code in mapping:
                label = mapping[code]
                break
    return code, label, codespace


def disaster_risks(building, source_gml: str | Path) -> list[DisasterRiskRecord]:
    """Extract PLATEAU bldgDisasterRiskAttribute values from one Building.

    All six Building disaster-risk datatypes defined by the PLATEAU standard are
    supported. Code values and codeSpace references are always retained. Labels
    are resolved from bundled local codelists when they are available.
    """
    out = []
    for wrapper in building.iter():
        if localname(wrapper.tag) != "bldgDisasterRiskAttribute":
            continue
        risk_nodes = [x for x in wrapper.iter() if x is not wrapper and localname(x.tag) in RISK_TYPES]
        # A property normally contains one risk node. De-duplicate defensively.
        seen = set()
        for node in risk_nodes:
            marker = id(node)
            if marker in seen:
                continue
            seen.add(marker)
            attr_type = localname(node.tag)
            risk_type, risk_ja = RISK_TYPES[attr_type]
            description_code, description_label, description_codespace = _code_info(node, "description", source_gml)
            rank_code, rank_label, rank_codespace = _code_info(node, "rank", source_gml)
            rank_org_code, rank_org_label, rank_org_codespace = _code_info(node, "rankOrg", source_gml)
            admin_type_code, admin_type_label, admin_type_codespace = _code_info(node, "adminType", source_gml)
            scale_code, scale_label, scale_codespace = _code_info(node, "scale", source_gml)
            area_type_code, area_type_label, area_type_codespace = _code_info(node, "areaType", source_gml)

            depth_e = _first_child(node, "depth")
            depth_value = _float_text(depth_e)
            depth_uom = (depth_e.get("uom") or "").strip() if depth_e is not None else ""
            duration_e = _first_child(node, "duration")
            duration_value = _float_text(duration_e)
            duration_uom = (duration_e.get("uom") or "").strip() if duration_e is not None else ""

            out.append(DisasterRiskRecord(
                risk_type=risk_type,
                risk_attribute_type=attr_type,
                risk_type_ja=risk_ja,
                description_code=description_code,
                description_label=description_label,
                description_codespace=description_codespace,
                rank_code=rank_code,
                rank_label=rank_label,
                rank_codespace=rank_codespace,
                rank_org_code=rank_org_code,
                rank_org_label=rank_org_label,
                rank_org_codespace=rank_org_codespace,
                depth_value=depth_value,
                depth_uom=depth_uom,
                depth_m=_normalize_depth_m(depth_value, depth_uom),
                admin_type_code=admin_type_code,
                admin_type_label=admin_type_label,
                admin_type_codespace=admin_type_codespace,
                scale_code=scale_code,
                scale_label=scale_label,
                scale_codespace=scale_codespace,
                duration_value=duration_value,
                duration_uom=duration_uom,
                duration_h=_normalize_duration_h(duration_value, duration_uom),
                area_type_code=area_type_code,
                area_type_label=area_type_label,
                area_type_codespace=area_type_codespace,
            ))
    return out


def scan_buildings(files: list[PlateauFile], progress: bool = False):
    out = []
    usable = [pf for pf in files if pf.local_path]
    total = len(usable)
    for i, pf in enumerate(usable, 1):
        path = pf.local_path
        if progress:
            print(f"  scan GML [{i}/{total}]: {Path(path).name}", flush=True)
        context = None
        try:
            context = etree.iterparse(path, events=("end",), huge_tree=True, recover=True)
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
                            usage, usage_label, usage_codespace = _code_info(b, "usage", path)
                            detailed_usage, detailed_usage_label, detailed_usage_codespace = _code_info(
                                b, "detailedUsage", path
                            )
                            structure_type, structure_type_label, structure_type_codespace = _code_info(
                                b, "buildingStructureType", path
                            )
                            fireproof_type, fireproof_type_label, fireproof_type_codespace = _code_info(
                                b, "fireproofStructureType", path
                            )
                            out.append(BuildingRecord(
                                gml_id=gid, source_file=path, city_code=pf.city_code,
                                file_code=pf.code, geometry=fp,
                                name=_first_text(b, ("name",)), address=_address_text(b),
                                usage=usage,
                                usage_label=usage_label,
                                usage_codespace=usage_codespace,
                                detailed_usage=detailed_usage,
                                detailed_usage_label=detailed_usage_label,
                                detailed_usage_codespace=detailed_usage_codespace,
                                building_id=_first_text(b, ("buildingID","buildingId")),
                                measured_height_m=_length_m(_first_child(b, "measuredHeight")),
                                storeys_above=_int_text(_first_child(b, "storeysAboveGround")),
                                storeys_below=_int_text(_first_child(b, "storeysBelowGround")),
                                year_of_construction=_int_text(_first_child(b, "yearOfConstruction")),
                                structure_type=structure_type,
                                structure_type_label=structure_type_label,
                                structure_type_codespace=structure_type_codespace,
                                fireproof_type=fireproof_type,
                                fireproof_type_label=fireproof_type_label,
                                fireproof_type_codespace=fireproof_type_codespace,
                                disaster_risks=disaster_risks(b, path),
                            ))
                elem.clear()
                parent = elem.getparent()
                if parent is not None:
                    while elem.getprevious() is not None:
                        del parent[0]
        except CityGMLReadError:
            raise
        except (TimeoutError, OSError, etree.XMLSyntaxError) as e:
            _raise_read_error(path, "scan", e)
        finally:
            if context is not None:
                del context
    return list({b.gml_id: b for b in out}.values())


def _root_info(path: str):
    try:
        for _, elem in etree.iterparse(path, events=("start",), huge_tree=True):
            return elem.tag, dict(elem.nsmap), dict(elem.attrib)
    except (TimeoutError, OSError, etree.XMLSyntaxError) as e:
        _raise_read_error(path, "root", e)
    raise CityGMLReadError(path, "root", ValueError("empty CityGML"))


def _add_generic(building, name: str, value: str, gen_ns: str):
    if not value:
        return
    node = etree.SubElement(building, f"{{{gen_ns}}}stringAttribute")
    node.set("name", name)
    v = etree.SubElement(node, f"{{{gen_ns}}}value")
    v.text = value

def write_subset(output_path: str | Path, files: list[PlateauFile], selected: dict[str, dict],
                 embed_generic: bool = True, progress: bool = False):
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
    usable = [pf for pf in files if pf.local_path]
    total = len(usable)
    try:
        with etree.xmlfile(str(out), encoding="UTF-8") as xf:
            xf.write_declaration()
            with xf.element(root_tag, attrib=attrib, nsmap=nsmap):
                n = etree.Element(f"{{{GML_NS}}}name")
                n.text = "Heritage-GML: PLATEAU building subset"
                xf.write(n)
                for i, pf in enumerate(usable, 1):
                    path = pf.local_path
                    if progress:
                        print(f"  subset GML [{i}/{total}]: {Path(path).name}", flush=True)
                    context = None
                    try:
                        context = etree.iterparse(path, events=("end",), huge_tree=True, recover=True)
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
                                        _add_generic(bb, "heritageDesignationLevels", ";".join(meta.get("designation_level_codes", [])), gen_ns)
                                        _add_generic(bb, "heritageDesignationLevelLabels", ";".join(meta.get("designation_level_labels", [])), gen_ns)
                                        _add_generic(bb, "heritageDesignationStatuses", ";".join(meta.get("designation_status_codes", [])), gen_ns)
                                        _add_generic(bb, "heritageDesignationStatusLabels", ";".join(meta.get("designation_status_labels", [])), gen_ns)
                                        _add_generic(bb, "heritageTypeMajors", ";".join(meta.get("heritage_type_major_codes", [])), gen_ns)
                                        _add_generic(bb, "heritageTypeMajorLabels", ";".join(meta.get("heritage_type_major_labels", [])), gen_ns)
                                        _add_generic(bb, "heritageTypeDetails", ";".join(meta.get("heritage_type_details", [])), gen_ns)
                                        _add_generic(bb, "heritageMatchMethod", ";".join(meta.get("methods", [])), gen_ns)
                                    xf.write(member)
                                    written.add(gid)
                            elem.clear()
                            parent = elem.getparent()
                            if parent is not None:
                                while elem.getprevious() is not None:
                                    del parent[0]
                    except CityGMLReadError:
                        raise
                    except (TimeoutError, OSError, etree.XMLSyntaxError) as e:
                        _raise_read_error(path, "subset", e)
                    finally:
                        if context is not None:
                            del context
    except CityGMLReadError:
        # Never leave a partial subset GML that could be mistaken for success.
        try:
            if out.exists():
                out.unlink()
        except OSError:
            pass
        raise
    return written

