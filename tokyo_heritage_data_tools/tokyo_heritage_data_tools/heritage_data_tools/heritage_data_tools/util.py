from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
import json
import math
import re
import unicodedata

import pandas as pd


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, float) and math.isnan(value):
            return ""
    except Exception:
        pass
    return unicodedata.normalize("NFKC", str(value)).strip()


def norm(value: Any) -> str:
    s = text(value)
    s = re.sub(r"[\s　]+", "", s)
    s = s.replace("ヶ", "ケ")
    return s


def sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_csv_bytes(data: bytes) -> pd.DataFrame:
    last = None
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return pd.read_csv(BytesIO(data), encoding=enc)
        except Exception as e:
            last = e
    raise ValueError(f"CSV decoding failed: {last}")


def read_csv_file(path: str | Path) -> pd.DataFrame:
    return read_csv_bytes(Path(path).read_bytes())


def write_json(path: str | Path, obj):
    Path(path).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def list_of_dicts(obj: Any) -> list[dict]:
    """Find the most plausible records array in heterogeneous API JSON."""
    if isinstance(obj, list):
        rows = [x for x in obj if isinstance(x, dict)]
        return rows
    if not isinstance(obj, dict):
        return []

    preferred = ("data", "results", "records", "items", "result")
    for k in preferred:
        v = obj.get(k)
        rows = list_of_dicts(v)
        if rows:
            return rows

    candidates = []
    for k, v in obj.items():
        rows = list_of_dicts(v)
        if rows:
            candidates.append((len(rows), k, rows))
    if not candidates:
        return []
    candidates.sort(reverse=True)
    return candidates[0][2]


def safe_name(s: str) -> str:
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", text(s))
    return s.strip("_") or "source"


def validate_pref_code(code: str) -> str:
    s = str(code).strip()
    if not re.fullmatch(r"\d{2}", s):
        raise ValueError("prefecture code must be exactly 2 digits")
    if not (1 <= int(s) <= 47):
        raise ValueError("prefecture code must be 01..47")
    return s


def validate_municipal_code(code: str) -> str:
    s = str(code).strip()
    if not re.fullmatch(r"\d{5}", s):
        raise ValueError("municipality code must be exactly 5 digits without check digit")
    if not (1 <= int(s[:2]) <= 47):
        raise ValueError("invalid prefecture prefix")
    return s
