from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import sqlite3
from typing import Iterable

import geopandas as gpd
import pandas as pd


INPUT_RE = re.compile(r"^(?P<code>\d{5})_heritage\.gpkg$")
SYSTEM_PREFIXES = ("gpkg_", "rtree_", "sqlite_")


@dataclass(frozen=True)
class SourceInfo:
    municipality_code: str
    municipality_name: str
    path: Path


@dataclass
class MergeResult:
    output_path: str
    prefecture_code: str
    source_count: int
    municipality_codes: list[str]
    spatial_layers: dict[str, int]
    attribute_tables: dict[str, int]
    generated_at: str


def _qident(name: str) -> str:
    """Quote a SQLite identifier."""
    return '"' + str(name).replace('"', '""') + '"'


def discover_sources(input_root: str | Path, prefecture_code: str,
                     codes: Iterable[str] | None = None) -> list[tuple[str, Path]]:
    """Find <5-digit>_heritage.gpkg recursively and reject duplicate municipalities."""
    root = Path(input_root)
    if not root.exists():
        raise FileNotFoundError(f"Input root does not exist: {root}")
    if not re.fullmatch(r"\d{2}", str(prefecture_code)):
        raise ValueError("prefecture_code must be a 2-digit code, e.g. 13")

    wanted = None
    if codes:
        wanted = {str(c) for c in codes}
        bad = sorted(c for c in wanted if not re.fullmatch(r"\d{5}", c))
        if bad:
            raise ValueError(f"municipality codes must be 5 digits: {bad}")

    by_code: dict[str, list[Path]] = {}
    for path in sorted(root.rglob("*_heritage.gpkg")):
        m = INPUT_RE.match(path.name)
        if not m:
            continue
        code = m.group("code")
        if not code.startswith(str(prefecture_code)):
            continue
        if wanted is not None and code not in wanted:
            continue
        by_code.setdefault(code, []).append(path)

    duplicates = {code: paths for code, paths in by_code.items() if len(paths) > 1}
    if duplicates:
        detail = "; ".join(
            f"{code}: " + ", ".join(str(p) for p in paths)
            for code, paths in sorted(duplicates.items())
        )
        raise RuntimeError(
            "Multiple municipality GeoPackages were found for the same code. "
            "Refusing to merge to prevent duplicate rows. " + detail
        )

    sources = [(code, paths[0]) for code, paths in sorted(by_code.items())]
    if wanted is not None:
        missing = sorted(wanted - {c for c, _ in sources})
        if missing:
            raise FileNotFoundError(
                "Requested municipality GeoPackages were not found: " + ", ".join(missing)
            )
    if not sources:
        raise FileNotFoundError(
            f"No <5-digit>_heritage.gpkg files for prefecture {prefecture_code} under {root}"
        )
    return sources


def _spatial_table_names(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT table_name FROM gpkg_geometry_columns ORDER BY table_name"
        ).fetchall()
    return [r[0] for r in rows]


def _attribute_table_names(path: Path, spatial_names: set[str]) -> list[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = []
    for (name,) in rows:
        if name in spatial_names:
            continue
        if name.startswith(SYSTEM_PREFIXES):
            continue
        names.append(name)
    return names


def _infer_municipality_name(path: Path, code: str) -> str:
    spatial = set(_spatial_table_names(path))
    # Prefer heritage_records because v0.4 stores the source municipality there.
    if "heritage_records" in spatial:
        try:
            gdf = gpd.read_file(path, layer="heritage_records", engine="pyogrio")
            if "municipality" in gdf.columns:
                vals = [str(v).strip() for v in gdf["municipality"].dropna() if str(v).strip()]
                if vals:
                    return vals[0]
        except Exception:
            pass
    # Fallback to ordinary SQLite tables if a future version stores it there.
    with sqlite3.connect(path) as conn:
        for table in _attribute_table_names(path, spatial):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({_qident(table)})").fetchall()}
            if "municipality" in cols:
                row = conn.execute(
                    f"SELECT municipality FROM {_qident(table)} "
                    "WHERE municipality IS NOT NULL AND TRIM(CAST(municipality AS TEXT)) <> '' LIMIT 1"
                ).fetchone()
                if row and str(row[0]).strip():
                    return str(row[0]).strip()
    return ""


def _check_or_fill_code(df: pd.DataFrame, code: str, source: Path) -> pd.DataFrame:
    """Ensure a municipality_code column exists and does not contradict filename code."""
    df = df.copy()
    if "municipality_code" in df.columns:
        nonblank = {
            str(v).strip() for v in df["municipality_code"].dropna()
            if str(v).strip() and str(v).strip().lower() != "nan"
        }
        conflicts = sorted(v for v in nonblank if v != code)
        if conflicts:
            raise ValueError(
                f"municipality_code conflicts with filename code {code} in {source}: {conflicts}"
            )
        blank = df["municipality_code"].isna() | (df["municipality_code"].astype(str).str.strip() == "")
        df.loc[blank, "municipality_code"] = code
    else:
        df.insert(0, "municipality_code", code)
    return df


def _add_provenance(df: pd.DataFrame, code: str, name: str, source: Path) -> pd.DataFrame:
    df = _check_or_fill_code(df, code, source)
    if "municipality_name" not in df.columns:
        insert_at = 1 if "municipality_code" in df.columns else 0
        df.insert(insert_at, "municipality_name", name)
    else:
        blank = df["municipality_name"].isna() | (df["municipality_name"].astype(str).str.strip() == "")
        df.loc[blank, "municipality_name"] = name
    if "source_municipality_gpkg" not in df.columns:
        df["source_municipality_gpkg"] = source.name
    return df


def _read_attribute_table(path: Path, table: str) -> pd.DataFrame:
    with sqlite3.connect(path) as conn:
        return pd.read_sql_query(f"SELECT * FROM {_qident(table)}", conn)


def _register_attribute_table(conn: sqlite3.Connection, table: str, row_count: int) -> None:
    """Register a plain SQLite table as a GeoPackage attributes table."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute("DELETE FROM gpkg_contents WHERE table_name = ?", (table,))
    conn.execute(
        """
        INSERT INTO gpkg_contents
        (table_name, data_type, identifier, description, last_change,
         min_x, min_y, max_x, max_y, srs_id)
        VALUES (?, 'attributes', ?, ?, ?, NULL, NULL, NULL, NULL, NULL)
        """,
        (table, table, f"Merged Heritage attribute table ({row_count} rows)", now),
    )


def _normalize_crs(gdf: gpd.GeoDataFrame, target_crs: str, source: Path, layer: str) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise ValueError(f"Spatial layer has no CRS: {source} :: {layer}")
    if str(gdf.crs) != str(gpd.GeoSeries([], crs=target_crs).crs):
        gdf = gdf.to_crs(target_crs)
    return gdf


def merge_prefecture(
    input_root: str | Path,
    prefecture_code: str,
    output_path: str | Path | None = None,
    codes: Iterable[str] | None = None,
    target_crs: str = "EPSG:4326",
) -> MergeResult:
    """Merge municipality Heritage GPKGs into one prefecture GeoPackage.

    No spatial dissolve/union/buffer is performed. MultiPolygon parts in
    heritage_building_complexes are preserved as they are stored in each
    municipality GeoPackage.
    """
    root = Path(input_root)
    sources0 = discover_sources(root, prefecture_code, codes=codes)
    sources = [
        SourceInfo(code, _infer_municipality_name(path, code), path)
        for code, path in sources0
    ]

    if output_path is None:
        output = root / f"{prefecture_code}_heritage.gpkg"
    else:
        output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.stem + ".tmp" + output.suffix)
    if tmp.exists():
        tmp.unlink()

    # Discover layer/table names dynamically so future municipality outputs can
    # be merged without changing this program, as long as same-named layers are
    # semantically compatible.
    spatial_names: set[str] = set()
    attribute_names: set[str] = set()
    per_source_spatial: dict[Path, set[str]] = {}
    per_source_attribute: dict[Path, set[str]] = {}
    for src in sources:
        spatial = set(_spatial_table_names(src.path))
        attrs = set(_attribute_table_names(src.path, spatial))
        per_source_spatial[src.path] = spatial
        per_source_attribute[src.path] = attrs
        spatial_names.update(spatial)
        attribute_names.update(attrs)

    spatial_counts: dict[str, int] = {}
    first_spatial = True
    for layer in sorted(spatial_names):
        frames: list[gpd.GeoDataFrame] = []
        for src in sources:
            if layer not in per_source_spatial[src.path]:
                continue
            gdf = gpd.read_file(src.path, layer=layer, engine="pyogrio")
            gdf = _normalize_crs(gdf, target_crs, src.path, layer)
            gdf = gpd.GeoDataFrame(
                _add_provenance(gdf, src.municipality_code, src.municipality_name, src.path),
                geometry=gdf.geometry.name,
                crs=target_crs,
            )
            frames.append(gdf)
        if not frames:
            continue
        merged = gpd.GeoDataFrame(
            pd.concat(frames, ignore_index=True, sort=False),
            geometry=frames[0].geometry.name,
            crs=target_crs,
        )
        if merged.empty:
            continue
        # Each call appends a new layer. The first call creates a valid GPKG.
        merged.to_file(tmp, layer=layer, driver="GPKG", engine="pyogrio")
        first_spatial = False
        spatial_counts[layer] = len(merged)

    if first_spatial:
        raise RuntimeError("No non-empty spatial layers were found; prefecture GPKG was not created.")

    attribute_counts: dict[str, int] = {}
    with sqlite3.connect(tmp) as conn:
        for table in sorted(attribute_names):
            frames: list[pd.DataFrame] = []
            for src in sources:
                if table not in per_source_attribute[src.path]:
                    continue
                df = _read_attribute_table(src.path, table)
                df = _add_provenance(df, src.municipality_code, src.municipality_name, src.path)
                frames.append(df)
            if not frames:
                continue
            merged = pd.concat(frames, ignore_index=True, sort=False)
            merged.to_sql(table, conn, if_exists="replace", index=False)
            _register_attribute_table(conn, table, len(merged))
            attribute_counts[table] = len(merged)
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")

    # Atomic-ish replacement: source municipality packages are never modified.
    if output.exists():
        output.unlink()
    tmp.replace(output)

    result = MergeResult(
        output_path=str(output),
        prefecture_code=str(prefecture_code),
        source_count=len(sources),
        municipality_codes=[s.municipality_code for s in sources],
        spatial_layers=spatial_counts,
        attribute_tables=attribute_counts,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    return result


def write_reports(result: MergeResult, report_csv: str | Path, manifest_json: str | Path) -> None:
    report_rows = []
    for layer, count in sorted(result.spatial_layers.items()):
        report_rows.append({"kind": "spatial", "name": layer, "row_count": count})
    for table, count in sorted(result.attribute_tables.items()):
        report_rows.append({"kind": "attribute", "name": table, "row_count": count})
    pd.DataFrame(report_rows, columns=["kind", "name", "row_count"]).to_csv(
        report_csv, index=False, encoding="utf-8-sig"
    )
    Path(manifest_json).write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8"
    )
