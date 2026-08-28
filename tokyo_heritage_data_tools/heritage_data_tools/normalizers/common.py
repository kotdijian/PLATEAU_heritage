from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

import pandas as pd

from ..tokyo_codes import code_from_text, TOKYO_MUNICIPALITIES
from ..util import text, norm, read_csv_file, list_of_dicts


CANONICAL_COLUMNS = [
    "source_level",
    "source_authority",
    "source_dataset",
    "source_record_id",
    "source_url",
    "source_file",
    "name",
    "name_kana",
    "place_name",
    "owner",
    "address",
    "municipality",
    "municipality_code",
    "category",
    "type",
    "designation",
    "designation_date",
    "latitude",
    "longitude",
    "entity_class",
    "geometry_role",
    "designation_level",
    "raw_category",
    "raw_type",
    "raw_designation",
]

ALIASES = {
    "id": ["NO", "No", "no", "ID", "id", "文化財ID", "管理番号", "番号", "登録番号", "指定番号"],
    "name": ["名称", "文化財名称", "文化財名", "name", "title", "名称（日本語）"],
    "name_kana": ["ふりがな", "フリガナ", "名称かな", "名称カナ", "name_kana"],
    "place_name": ["場所名称", "施設名称", "所在地名称", "所在名称", "保管施設", "place_name", "site_name"],
    "owner": ["所有者等", "所有者", "管理者", "管理団体", "owner"],
    "address": ["住所", "所在地", "所在", "所在地住所", "address"],
    "municipality": ["市区町村名", "自治体名", "市町村", "municipality", "city"],
    "municipality_code": ["全国地方公共団体コード", "自治体コード", "市区町村コード", "municipality_code", "city_code"],
    "latitude": ["緯度", "lat", "latitude", "Latitude"],
    "longitude": ["経度", "lon", "lng", "longitude", "Longitude"],
    "category": ["文化財分類", "指定区分", "分類", "category", "文化財種類"],
    "type": ["種類", "種別", "文化財種類", "type"],
    "designation": ["指定等", "指定登録区分", "designation", "指定・登録区分", "指定種別"],
    "designation_date": ["文化財指定日", "指定年月日", "指定日", "登録年月日", "選定年月日", "designation_date"],
}


def resolve(columns, logical: str) -> str | None:
    cols = set(columns)
    for c in ALIASES.get(logical, []):
        if c in cols:
            return c
    # relaxed normalized comparison
    nmap = {norm(c): c for c in columns}
    for c in ALIASES.get(logical, []):
        if norm(c) in nmap:
            return nmap[norm(c)]
    return None


def row_value(row, col) -> str:
    return text(row.get(col, "")) if col else ""


def numeric(value):
    try:
        if value in ("", None):
            return None
        x = float(value)
        if pd.isna(x):
            return None
        return x
    except Exception:
        return None


def normalize_type(raw_type: str, raw_category: str = "") -> str:
    s = f"{text(raw_type)} {text(raw_category)}"
    # Building direct: intentionally normalize to the same vocabulary already
    # understood by plateau-heritage-gml v0.3.x.
    if any(k in s for k in (
        "建造物", "建築", "住居建築", "宗教建築", "近代その他",
        "民家", "住宅", "社寺", "堂", "塔", "門", "橋梁"
    )):
        return "建造物"

    if "考古" in s:
        return "考古資料"
    if "古文書" in s:
        return "古文書"
    if "典籍" in s or "書跡" in s or "文書・書籍" in s:
        return "典籍"
    if "歴史資料" in s:
        return "歴史資料"
    if any(k in s for k in ("絵画", "彫刻", "工芸", "美術工芸", "登録美術品")):
        return "美術工芸品"

    if "天然記念物" in s:
        return "天然記念物"
    if "名勝" in s:
        return "名勝"
    if "史跡" in s:
        return "史跡"

    return text(raw_type) or text(raw_category)


def entity_class(normalized_type: str) -> str:
    if normalized_type == "建造物":
        return "building_direct"
    if normalized_type in {
        "美術工芸品", "考古資料", "古文書", "典籍", "歴史資料",
        "美術工芸品・考古資料",
    }:
        return "movable"
    return "point"


def geometry_role(cls: str) -> str:
    if cls == "building_direct":
        return "building_candidate_point"
    if cls == "movable":
        return "address_group_point"
    return "representative_point"


def municipality_from_values(code_value: str, municipality_value: str, address: str):
    digits = re.sub(r"\\D", "", text(code_value))
    if len(digits) >= 5:
        code = digits[:5]
        return code, TOKYO_MUNICIPALITIES.get(code, text(municipality_value))

    if municipality_value:
        c, n = code_from_text(municipality_value)
        if c:
            return c, n
    c, n = code_from_text(address)
    if c:
        return c, n
    return "", text(municipality_value)


def canonical_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in CANONICAL_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[CANONICAL_COLUMNS]
