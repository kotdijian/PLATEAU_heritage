from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files as resource_files
from pathlib import Path
import re

import pandas as pd

from .util import text, read_csv_file
from .tokyo_codes import code_from_text


CLASSIFICATION_COLUMNS = [
    "designation_level_code",
    "designation_level_ja",
    "designation_status_code",
    "designation_status_ja",
    "heritage_type_major_code",
    "heritage_type_major_ja",
    "heritage_type_detail",
    "classification_confidence",
]

_CONF_RANK = {"": 0, "low": 1, "medium": 2, "high": 3}


def _read_csv_preserve(path: str | Path) -> pd.DataFrame:
    last = None
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False)
        except Exception as e:
            last = e
    raise ValueError(f"CSV decoding failed: {path}: {last}")


def _default_data_path(name: str) -> Path:
    return Path(str(resource_files("heritage_data_tools").joinpath("data", "classification", name)))


def _norm(value) -> str:
    return re.sub(r"\s+", "", text(value).replace("　", " "))


def _first(row, names) -> str:
    keys = row.index if hasattr(row, "index") else row.keys()
    for name in names:
        if name in keys:
            v = text(row.get(name, ""))
            if v:
                return v
    return ""


def _municipality_code(row) -> str:
    raw = _first(row, [
        "municipality_code", "市区町村コード", "自治体コード", "全国地方公共団体コード",
        "都道府県コード又は市区町村コード",
    ])
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 5 and digits[:5] != "13000":
        return digits[:5]
    name = _first(row, ["municipality", "市区町村名", "自治体名", "市町村"])
    address = _first(row, ["address", "住所", "所在地", "所在"])
    code, _ = code_from_text(name or address)
    return code or (digits[:5] if len(digits) >= 5 else "")


def detect_scope(df: pd.DataFrame, input_path: str | Path, explicit: str = "auto") -> str:
    if explicit != "auto":
        return explicit
    name = Path(input_path).name.lower()
    if name.startswith("130001_cultural_property"):
        return "prefectural_tokyo"
    if "municipal" in name:
        return "municipal"
    if "national" in name:
        return "national"

    if "source_level" in df.columns:
        vals = {_norm(x) for x in df["source_level"].dropna().astype(str) if _norm(x)}
        if vals and vals <= {"national"}:
            return "national"
        if vals and vals <= {"municipal_source", "municipal"}:
            return "municipal"
    if "designation_level" in df.columns:
        vals = {_norm(x) for x in df["designation_level"].dropna().astype(str) if _norm(x)}
        if vals and vals <= {"national"}:
            return "national"
        if vals and vals <= {"municipal"}:
            return "municipal"

    raise ValueError(
        "Could not determine source scope. Use --scope municipal, national, or prefectural_tokyo."
    )


@dataclass
class GlossaryRule:
    priority: int
    source_scope: str
    municipality_code: str
    match_field: str
    match_type: str
    match_value: str
    values: dict

    def matches(self, scope: str, municipality_code: str, field_value: str) -> bool:
        if self.source_scope not in ("", "any", scope):
            return False
        if self.municipality_code:
            if scope == "prefectural_tokyo":
                if self.municipality_code != "13000":
                    return False
            elif self.municipality_code != municipality_code:
                return False
        if self.match_type == "regex":
            try:
                return re.search(self.match_value, field_value) is not None
            except re.error:
                return False
        return _norm(self.match_value) == _norm(field_value)


def load_rules(path: str | Path | None = None) -> list[GlossaryRule]:
    p = Path(path) if path else _default_data_path("heritage_classification_glossary.csv")
    df = _read_csv_preserve(p)
    rules = []
    for _, r in df.iterrows():
        def g(k): return text(r.get(k, ""))
        try:
            priority = int(float(g("priority") or 999))
        except Exception:
            priority = 999
        rules.append(GlossaryRule(
            priority=priority,
            source_scope=g("source_scope"),
            municipality_code=g("municipality_code"),
            match_field=g("match_field"),
            match_type=g("match_type") or "exact",
            match_value=g("match_value"),
            values={k: g(k) for k in [
                "designation_level_code", "designation_level_ja",
                "designation_status_code", "designation_status_ja",
                "heritage_type_major_code", "heritage_type_major_ja",
                "heritage_type_detail", "confidence",
            ]},
        ))
    return sorted(rules, key=lambda x: x.priority)


def load_overrides(path: str | Path | None = None) -> dict[tuple[str, str, str, str], dict]:
    p = Path(path) if path else _default_data_path("record_overrides_13118.csv")
    if not p.exists():
        return {}
    df = _read_csv_preserve(p)
    out = {}
    for _, r in df.iterrows():
        key = (
            text(r.get("municipality_code", "")),
            _norm(r.get("name", "")),
            _norm(r.get("owner", "")),
            _norm(r.get("category", "")),
        )
        out[key] = {k: text(r.get(k, "")) for k in [
            "designation_status_code", "designation_status_ja",
            "heritage_type_major_code", "heritage_type_major_ja",
            "heritage_type_detail", "confidence",
        ]}
    return out


def _source_defaults(scope: str) -> dict:
    if scope == "national":
        return {
            "designation_level_code": "national", "designation_level_ja": "国",
            "designation_status_code": "designated", "designation_status_ja": "指定",
            "confidence": "medium",
        }
    if scope == "prefectural_tokyo":
        return {
            "designation_level_code": "prefectural", "designation_level_ja": "都",
            "designation_status_code": "designated", "designation_status_ja": "指定",
            "confidence": "high",
        }
    if scope == "municipal":
        return {
            "designation_level_code": "unknown", "designation_level_ja": "不明",
            "designation_status_code": "", "designation_status_ja": "",
            "confidence": "medium",
        }
    raise ValueError(f"Unsupported scope: {scope}")


def _existing_level(row) -> tuple[str, str] | None:
    raw = _first(row, ["designation_level", "designation"])
    mapping = {
        "national": ("national", "国"),
        "prefectural": ("prefectural", "都"),
        "municipal": ("municipal", "区市町村"),
    }
    return mapping.get(_norm(raw))


def _reference_key(row) -> tuple[str, str] | None:
    name = _first(row, ["name", "名称", "文化財名称", "文化財名"])
    code = _municipality_code(row)
    if not name or not code:
        return None
    return (_norm(name), code)


def _copy_reference_classification(dst: pd.DataFrame, idx, src_row: dict, level_code: str, level_ja: str):
    dst.at[idx, "designation_level_code"] = level_code
    dst.at[idx, "designation_level_ja"] = level_ja
    for c in [
        "designation_status_code", "designation_status_ja",
        "heritage_type_major_code", "heritage_type_major_ja", "heritage_type_detail",
    ]:
        if _first(src_row, [c]):
            # Preserve a more specific already-resolved municipal-source type;
            # reference rows mainly resolve authority/status.
            if c.startswith("heritage_type") and c in dst.columns and _norm(dst.at[idx, "heritage_type_major_code"]) not in ("", "unknown"):
                continue
            dst.at[idx, c] = _first(src_row, [c])
    dst.at[idx, "classification_confidence"] = "high"


def resolve_municipal_cross_source(
    municipal_df: pd.DataFrame,
    national_df: pd.DataFrame | None = None,
    tokyo_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Resolve only unknown municipal-source designation levels by exact cross-source evidence.

    Evidence is exact normalized cultural-property name + 5-digit municipality code.
    No fuzzy name match, spatial nearest/buffer, or inferred geometry is used.
    """
    out = municipal_df.copy()
    refs = {"national": {}, "prefectural": {}}
    for label, df in (("national", national_df), ("prefectural", tokyo_df)):
        if df is None or df.empty:
            continue
        tmp = {}
        dup = set()
        for row in df.to_dict(orient="records"):
            k = _reference_key(row)
            if not k:
                continue
            if k in tmp:
                dup.add(k)
            else:
                tmp[k] = row
        refs[label] = {k:v for k,v in tmp.items() if k not in dup}

    stats = {"resolved_national_exact": 0, "resolved_prefectural_exact": 0, "ambiguous_both_refs": 0}
    for idx, row in out.iterrows():
        if _norm(row.get("designation_level_code", "")) != "unknown":
            continue
        k = _reference_key(row)
        if not k:
            continue
        nr = refs["national"].get(k)
        tr = refs["prefectural"].get(k)
        if nr and tr:
            stats["ambiguous_both_refs"] += 1
            continue
        if nr:
            _copy_reference_classification(out, idx, nr, "national", "国")
            stats["resolved_national_exact"] += 1
        elif tr:
            _copy_reference_classification(out, idx, tr, "prefectural", "都")
            stats["resolved_prefectural_exact"] += 1
    return out, stats


def _rule_for(rules, scope, municipality_code, field, value):
    if not value:
        return None
    for rule in rules:
        if rule.match_field == field and rule.matches(scope, municipality_code, value):
            return rule
    return None


def _apply_nonempty(dst: dict, src: dict, keys) -> None:
    for k in keys:
        v = text(src.get(k, ""))
        if v:
            dst[k] = v


def _confidence(values: list[str]) -> str:
    present = [v for v in values if v in _CONF_RANK and v]
    if not present:
        return "low"
    return min(present, key=lambda x: _CONF_RANK[x])


def classify_frame(
    df: pd.DataFrame,
    scope: str,
    glossary_path: str | Path | None = None,
    overrides_path: str | Path | None = None,
) -> tuple[pd.DataFrame, dict]:
    rules = load_rules(glossary_path)
    overrides = load_overrides(overrides_path)
    out = df.copy()
    for c in CLASSIFICATION_COLUMNS:
        if c not in out.columns:
            out[c] = ""

    counts = {"rows": len(out), "override": 0, "category_rule": 0, "type_rule": 0, "unresolved_type": 0, "unresolved_status": 0}

    for idx, row in out.iterrows():
        municipality_code = _municipality_code(row)
        category = _first(row, ["category", "文化財分類", "指定区分", "分類", "カテゴリ"])
        typ = _first(row, ["type", "種類", "種別", "文化財種類", "ジャンル"])
        name = _first(row, ["name", "名称", "文化財名称", "文化財名"])
        owner = _first(row, ["owner", "所有者等", "所有者", "管理者"])

        result = _source_defaults(scope)
        confs = [result.get("confidence", "")]
        existing_level = _existing_level(row) if scope == "municipal" else None
        if existing_level:
            result["designation_level_code"], result["designation_level_ja"] = existing_level

        cat_rule = _rule_for(rules, scope, municipality_code, "category", category)
        if cat_rule:
            counts["category_rule"] += 1
            # Source-specific category rules override legacy normalizer levels when
            # they resolve a concrete authority (important for labels such as 市重宝).
            rule_level = cat_rule.values.get("designation_level_code", "")
            if rule_level and rule_level != "unknown":
                _apply_nonempty(result, cat_rule.values, ["designation_level_code", "designation_level_ja"])
            elif not existing_level and scope == "municipal":
                result["designation_level_code"], result["designation_level_ja"] = "unknown", "不明"
            _apply_nonempty(result, cat_rule.values, [
                "designation_status_code", "designation_status_ja",
            ])
            # Category supplies heritage type only when it actually resolved a canonical major.
            if cat_rule.values.get("heritage_type_major_code") not in ("", "unknown"):
                _apply_nonempty(result, cat_rule.values, [
                    "heritage_type_major_code", "heritage_type_major_ja", "heritage_type_detail"
                ])
                confs.append(cat_rule.values.get("confidence", ""))
            confs.append(cat_rule.values.get("confidence", ""))

        # National canonical files are national by construction, but status words override default.
        if scope == "national" and category:
            for rule in rules:
                if rule.source_scope == "national" and rule.match_field == "category" and rule.matches(scope, municipality_code, category):
                    _apply_nonempty(result, rule.values, ["designation_status_code", "designation_status_ja"])
                    confs.append(rule.values.get("confidence", ""))
                    break

        override_key = (municipality_code, _norm(name), _norm(owner), _norm(category))
        ov = overrides.get(override_key)
        if ov:
            counts["override"] += 1
            _apply_nonempty(result, ov, [
                "designation_status_code", "designation_status_ja",
                "heritage_type_major_code", "heritage_type_major_ja", "heritage_type_detail",
            ])
            confs.append(ov.get("confidence", ""))

        # Fill type from normalized/raw type only if category/override did not resolve it.
        if result.get("heritage_type_major_code", "") in ("", "unknown"):
            type_rule = _rule_for(rules, scope, municipality_code, "type", typ)
            if type_rule:
                counts["type_rule"] += 1
                if type_rule.values.get("heritage_type_major_code") not in ("", "unknown"):
                    _apply_nonempty(result, type_rule.values, [
                        "heritage_type_major_code", "heritage_type_major_ja", "heritage_type_detail"
                    ])
                confs.append(type_rule.values.get("confidence", ""))

        if not result.get("designation_level_code"):
            result.update({"designation_level_code": "unknown", "designation_level_ja": "不明"})

        if not result.get("heritage_type_major_code"):
            result.update({
                "heritage_type_major_code": "unknown",
                "heritage_type_major_ja": "未判定",
                "heritage_type_detail": typ or category,
            })
        if not result.get("designation_status_code"):
            result.update({"designation_status_code": "unknown", "designation_status_ja": "不明"})

        if result["heritage_type_major_code"] == "unknown":
            counts["unresolved_type"] += 1
            confs.append("low")
        if result["designation_status_code"] == "unknown":
            counts["unresolved_status"] += 1
            confs.append("low")

        result["classification_confidence"] = _confidence(confs)
        for c in CLASSIFICATION_COLUMNS:
            out.at[idx, c] = result.get(c, "")

    counts["scope"] = scope
    return out, counts


def classify_csv(
    input_path: str | Path,
    output_path: str | Path,
    scope: str = "auto",
    glossary_path: str | Path | None = None,
    overrides_path: str | Path | None = None,
) -> dict:
    input_path = Path(input_path)
    output_path = Path(output_path)
    df = _read_csv_preserve(input_path)
    resolved_scope = detect_scope(df, input_path, scope)
    classified, summary = classify_frame(
        df, resolved_scope, glossary_path=glossary_path, overrides_path=overrides_path
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    classified.to_csv(output_path, index=False, encoding="utf-8-sig")
    summary.update({"input": str(input_path), "output": str(output_path)})
    return summary
