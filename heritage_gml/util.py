from __future__ import annotations
import json, math, re, unicodedata
from pathlib import Path
from typing import Any


def norm_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, float) and math.isnan(value):
            return ""
        if isinstance(value, float) and value.is_integer():
            value = int(value)
    except Exception:
        pass
    s = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", " ", s).strip()


def norm_key(value: Any) -> str:
    """Loose text key for names, not for addresses."""
    s = norm_text(value)
    s = re.sub(r"^(宗教法人|公益財団法人|一般財団法人|公益社団法人|一般社団法人)\s*", "", s)
    s = s.replace("ヶ", "ケ")
    s = re.sub(r"[\s　・･,，.。\-‐‑–—ー_/／()（）\[\]「」『』]", "", s)
    return s.casefold()


def compact_address(value: Any) -> str:
    """Normalize an address without destroying numeric separators.

    v0.2.x passed addresses through norm_key() first, which removed '-' before
    converting 丁目/番/号.  For example 大手町1-2 became 大手町12.  This function
    keeps the hierarchy delimiters and is intentionally conservative: it is a
    stable equality/grouping key, not a geocoder or fuzzy address matcher.
    """
    s = norm_text(value).replace("ヶ", "ケ")
    s = re.sub(r"[‐‑‒–—―ー−ｰ]", "-", s)
    s = re.sub(r"\s+", "", s)
    s = s.replace("丁目", "-").replace("番地", "-").replace("番", "-").replace("号", "")
    s = re.sub(r"[・･,，.。_/／()（）\[\]「」『』]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s.casefold()


def unique_keep_order(values):
    seen, out = set(), []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def validate_area_code(code: str) -> tuple[str, str]:
    s = str(code).strip()
    if not re.fullmatch(r"\d{2}|\d{5}", s):
        raise ValueError("--area-code must be exactly 2 or 5 digits (without check digit).")
    pref = int(s[:2])
    if not 1 <= pref <= 47:
        raise ValueError(f"Invalid prefecture code: {s[:2]}")
    return ("prefecture" if len(s) == 2 else "municipality", s)


def safe_filename(s: str) -> str:
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", s)
    return s.strip("_") or "file"


def json_dump(path: str | Path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
