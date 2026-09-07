#!/usr/bin/env python3
"""Inspect duplicate PLATEAU Building objects without external search commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

GML_ID = "{http://www.opengis.net/gml}id"


def local(tag) -> str:
    return str(tag).rsplit("}", 1)[-1]


def namespace_uri(tag) -> str:
    value = str(tag)
    return value[1:].split("}", 1)[0] if value.startswith("{") else ""


def norm(value) -> str:
    return " ".join((value or "").split())


def contains_any(path: Path, needles: tuple[bytes, ...]) -> bool:
    """Search a large GML file in bounded memory, including chunk boundaries."""
    overlap_size = max((len(needle) for needle in needles), default=1) - 1
    overlap = b""
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            data = overlap + chunk
            if any(needle in data for needle in needles):
                return True
            overlap = data[-overlap_size:] if overlap_size else b""
    return False


def first_text(building, wanted: str) -> str:
    for element in building.iter():
        if local(element.tag) == wanted:
            value = norm(" ".join(element.itertext()))
            if value:
                return value
    return ""


def address_text(building) -> str:
    for element in building.iter():
        if local(element.tag) != "Address":
            continue
        values = []
        for child in element.iter():
            if len(child) == 0 and norm(child.text):
                value = norm(child.text)
                if value not in values:
                    values.append(value)
        return " ".join(values)
    return ""


def embedded_code(building) -> tuple[str, str, str]:
    for element in building.iter():
        if local(element.tag) != "stringAttribute":
            continue
        name = norm(element.get("name"))
        if "区市町村コード" not in name:
            continue
        for child in element.iter():
            if local(child.tag) != "value":
                continue
            value = norm(child.text)
            match = re.match(r"^(13\d{3})", value)
            if match:
                return match.group(1), name, value
    return "", "", ""


def source_city_code(path: Path) -> str:
    return next(
        (part for part in path.parts if re.fullmatch(r"13\d{3}", part)),
        "",
    )


def inspect_building(building, path: Path, gml_id: str) -> dict:
    embedded, generic_name, generic_value = embedded_code(building)
    tags = Counter(local(element.tag) for element in building.iter())
    full_tags = Counter(str(element.tag) for element in building.iter())
    namespaces = Counter(namespace_uri(element.tag) for element in building.iter())
    poslists = [
        norm(element.text)
        for element in building.iter()
        if local(element.tag) == "posList"
    ]
    coordinate_digest = hashlib.sha256()
    for value in poslists:
        coordinate_digest.update(value.encode("utf-8"))
        coordinate_digest.update(b"\0")

    element_facts = Counter()
    for element in building.iter():
        names = []
        current = element
        while current is not None:
            names.append(local(current.tag))
            if current is building:
                break
            current = current.getparent()
        path_name = "/".join(reversed(names))
        attributes = tuple(
            sorted((local(key), norm(value)) for key, value in element.attrib.items())
        )
        value = "<coordinates>" if local(element.tag) == "posList" else norm(element.text)
        element_facts[(path_name, attributes, value)] += 1
    return {
        "gml_id": gml_id,
        "source_gml": str(path),
        "source_city_code": source_city_code(path),
        "embedded_city_code": embedded,
        "municipality_attribute": generic_name,
        "municipality_attribute_value": generic_value,
        "address": address_text(building),
        "name": first_text(building, "name"),
        "class": first_text(building, "class"),
        "usage": first_text(building, "usage"),
        "detailedUsage": first_text(building, "detailedUsage"),
        "creationDate": first_text(building, "creationDate"),
        "element_count": sum(tags.values()),
        "posList_count": len(poslists),
        "coordinate_token_count": sum(len(value.split()) for value in poslists),
        "coordinate_sha256": coordinate_digest.hexdigest(),
        "tag_counts": dict(sorted(tags.items())),
        "full_tag_counts": dict(sorted(full_tags.items())),
        "namespace_counts": dict(sorted(namespaces.items())),
        "lod0FootPrint": tags["lod0FootPrint"],
        "lod0RoofEdge": tags["lod0RoofEdge"],
        "lod1Solid": tags["lod1Solid"],
        "lod2Solid": tags["lod2Solid"],
        "_element_facts": element_facts,
    }


def fact_text(fact) -> str:
    path_name, attributes, value = fact
    attrs = " ".join(f"{key}={item!r}" for key, item in attributes)
    suffix = " | ".join(part for part in (attrs, value) if part)
    return f"{path_name}: {suffix}" if suffix else path_name


def compare_rows(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["gml_id"]].append(row)
    comparisons = []
    selected_fields = (
        "address", "name", "class", "usage", "detailedUsage", "creationDate",
        "posList_count", "coordinate_token_count", "lod0FootPrint",
        "lod0RoofEdge", "lod1Solid", "lod2Solid",
    )
    for gml_id, group in sorted(grouped.items()):
        first = group[0]
        for other in group[1:]:
            tag_names = sorted(set(first["tag_counts"]) | set(other["tag_counts"]))
            tag_differences = {
                tag: [first["tag_counts"].get(tag, 0), other["tag_counts"].get(tag, 0)]
                for tag in tag_names
                if first["tag_counts"].get(tag, 0) != other["tag_counts"].get(tag, 0)
            }
            namespace_names = sorted(
                set(first["namespace_counts"]) | set(other["namespace_counts"])
            )
            namespace_differences = {
                name: [
                    first["namespace_counts"].get(name, 0),
                    other["namespace_counts"].get(name, 0),
                ]
                for name in namespace_names
                if first["namespace_counts"].get(name, 0)
                != other["namespace_counts"].get(name, 0)
            }
            full_tag_names = sorted(
                set(first["full_tag_counts"]) | set(other["full_tag_counts"])
            )
            full_tag_differences = {
                name: [
                    first["full_tag_counts"].get(name, 0),
                    other["full_tag_counts"].get(name, 0),
                ]
                for name in full_tag_names
                if first["full_tag_counts"].get(name, 0)
                != other["full_tag_counts"].get(name, 0)
            }
            first_only = first["_element_facts"] - other["_element_facts"]
            other_only = other["_element_facts"] - first["_element_facts"]
            comparisons.append({
                "gml_id": gml_id,
                "first_source_gml": first["source_gml"],
                "other_source_gml": other["source_gml"],
                "coordinates_equal": first["coordinate_sha256"] == other["coordinate_sha256"],
                "selected_fields_equal": all(first[field] == other[field] for field in selected_fields),
                "tag_count_differences": tag_differences,
                "namespace_count_differences": namespace_differences,
                "full_tag_count_differences": full_tag_differences,
                "first_only_element_facts": [
                    f"{count} x {fact_text(fact)}" for fact, count in first_only.items()
                ][:80],
                "other_only_element_facts": [
                    f"{count} x {fact_text(fact)}" for fact, count in other_only.items()
                ][:80],
            })
    return comparisons


def inspect(root: Path, targets: set[str]) -> list[dict]:
    needles = tuple(target.encode("utf-8") for target in sorted(targets))
    all_files = sorted(root.rglob("*.gml"))
    paths = []
    for index, path in enumerate(all_files, start=1):
        if contains_any(path, needles):
            paths.append(path)
        if index == len(all_files) or index % 20 == 0:
            print(
                f"File search: {index}/{len(all_files)}; matched files: {len(paths)}",
                flush=True,
            )

    rows = []
    for path in paths:
        context = etree.iterparse(
            str(path), events=("end",), huge_tree=True, recover=True
        )
        for _, member in context:
            if local(member.tag) != "cityObjectMember":
                continue
            for building in member.iter():
                if local(building.tag) != "Building":
                    continue
                gml_id = building.get(GML_ID) or building.get("id") or ""
                if gml_id in targets:
                    rows.append(inspect_building(building, path, gml_id))
                    break
            member.clear()
            parent = member.getparent()
            if parent is not None:
                while member.getprevious() is not None:
                    del parent[0]
        del context
    rows = sorted(rows, key=lambda row: (row["gml_id"], row["source_gml"]))
    comparisons = compare_rows(rows)
    for row in rows:
        del row["_element_facts"]
    return {"records": rows, "comparisons": comparisons}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect selected duplicate PLATEAU Building gml:id values."
    )
    parser.add_argument("plateau_dir", type=Path)
    parser.add_argument("gml_ids", nargs="+")
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.plateau_dir.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"PLATEAU directory not found: {root}")
    report = inspect(root, set(args.gml_ids))
    result = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result, encoding="utf-8")
        print(f"Wrote: {output}")
    else:
        print(result, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
