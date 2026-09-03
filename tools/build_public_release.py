#!/usr/bin/env python3
"""
Build GitHub-public derivative datasets from 13_heritage_hazards.gpkg.

Outputs
-------
public_data/
├── 13_heritage_public.gpkg
├── hazard_map.gpkg
├── SOURCE_LICENSES.csv
└── geojson/
    ├── heritage_buildings_risk.geojson
    ├── heritage_buildings_footprint_risk.geojson
    ├── heritage_complexes.geojson
    └── heritage_source_points.geojson

Notes
-----
- plateau_disaster_risk is intentionally excluded.
- Large source hazard layers are not included in 13_heritage_public.gpkg.
- README.md is NOT generated here. The repository root README.md should contain
  both the public-data description and the development documentation.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
from pathlib import Path

import pandas as pd
import pyogrio


PUBLIC_SPATIAL = [
    "heritage_records",
    "heritage_points",
    "heritage_buildings_point",
    "heritage_buildings_footprint",
    "heritage_buildings_footprint_riskwide",
    "heritage_building_complexes",
]

PUBLIC_ATTRIBUTES = [
    "heritage_building_links",
    "heritage_complex_members",
    "heritage_complex_records",
    "heritage_complex_summary",
    "heritage_disaster_risk",
    "heritage_disaster_metadata",
    "hazard_source_manifest",
    "source_license",
]

HAZARD_MAP = [
    "hazard_region_risk",
    "hazard_fire_spread_town",
    "hazard_sediment_warning_a33_polygon",
    "hazard_sabo_designated_a52_polygon",
]

GEOJSON = {
    "heritage_buildings_point": "heritage_buildings_risk.geojson",
    "heritage_buildings_footprint_riskwide":
        "heritage_buildings_footprint_risk.geojson",
    "heritage_building_complexes": "heritage_complexes.geojson",
    "heritage_points": "heritage_source_points.geojson",
}


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sha256(path: Path, chunk_size: int = 16 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def sqlite_tables(path: Path) -> set[str]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as con:
        return {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


def read_attribute_table(source: Path, table: str) -> pd.DataFrame:
    with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as con:
        return pd.read_sql_query(
            f"SELECT * FROM {qident(table)}",
            con,
        )


def remove_gpkg_sidecars(path: Path) -> None:
    for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if p.exists():
            p.unlink()


def copy_spatial_layer(source: Path, destination: Path, layer: str) -> None:
    print(f"  spatial: {layer}")
    gdf = pyogrio.read_dataframe(source, layer=layer)
    pyogrio.write_dataframe(
        gdf,
        destination,
        layer=layer,
        driver="GPKG",
    )
    print(f"    features={len(gdf):,}")


def copy_attribute_layer(source: Path, destination: Path, layer: str) -> None:
    print(f"  attributes: {layer}")
    df = read_attribute_table(source, layer)
    pyogrio.write_dataframe(
        df,
        destination,
        layer=layer,
        driver="GPKG",
        layer_options={"ASPATIAL_VARIANT": "GPKG_ATTRIBUTES"},
    )
    print(f"    rows={len(df):,}")


def write_geojson(source: Path, layer: str, destination: Path) -> None:
    print(f"  GeoJSON: {layer}")
    gdf = pyogrio.read_dataframe(source, layer=layer)

    if gdf.crs is not None:
        gdf = gdf.to_crs("EPSG:4326")

    pyogrio.write_dataframe(
        gdf,
        destination,
        driver="GeoJSON",
    )

    print(
        f"    features={len(gdf):,} "
        f"size={destination.stat().st_size / 1024**2:.2f} MiB"
    )


def verify_gpkg(path: Path) -> list[str]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as con:
        qc = con.execute("PRAGMA quick_check").fetchone()[0]
        layers = [
            row[0]
            for row in con.execute(
                "SELECT table_name FROM gpkg_contents ORDER BY table_name"
            )
        ]

    if qc != "ok":
        raise RuntimeError(f"{path}: quick_check={qc}")

    return layers


def write_license_csv(source: Path, destination: Path) -> None:
    df = read_attribute_table(source, "source_license")
    published_layers = set(PUBLIC_SPATIAL + HAZARD_MAP)

    def used_in_release(pattern) -> bool:
        if pd.isna(pattern):
            return False

        names = {
            item.strip()
            for item in str(pattern).split(";")
            if item.strip()
        }
        return bool(names & published_layers)

    if "layer_pattern" in df.columns:
        df["published_in_release"] = df["layer_pattern"].map(used_in_release)

    df.to_csv(
        destination,
        index=False,
        encoding="utf-8-sig",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build GitHub-public GPKG/GeoJSON derivatives from "
            "13_heritage_hazards.gpkg."
        )
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Path to 13_heritage_hazards.gpkg",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("public_data"),
        help="Output directory (default: ./public_data)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace previously generated public-data outputs.",
    )
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    outdir = args.out_dir.expanduser().resolve()

    if not source.exists():
        raise SystemExit(f"ERROR: source not found: {source}")

    required = set(PUBLIC_SPATIAL + PUBLIC_ATTRIBUTES + HAZARD_MAP)
    available = sqlite_tables(source)
    missing = sorted(required - available)

    if missing:
        raise SystemExit(
            "ERROR: missing source tables/layers:\n"
            + "\n".join(f"  - {x}" for x in missing)
        )

    public_gpkg = outdir / "13_heritage_public.gpkg"
    hazard_gpkg = outdir / "hazard_map.gpkg"
    geojson_dir = outdir / "geojson"
    license_csv = outdir / "SOURCE_LICENSES.csv"

    generated_targets = [
        public_gpkg,
        hazard_gpkg,
        license_csv,
        *[geojson_dir / filename for filename in GEOJSON.values()],
    ]

    existing = [p for p in generated_targets if p.exists()]

    if existing and not args.force:
        raise SystemExit(
            "ERROR: output already exists. Use --force to replace:\n"
            + "\n".join(f"  - {p}" for p in existing)
        )

    if args.force:
        remove_gpkg_sidecars(public_gpkg)
        remove_gpkg_sidecars(hazard_gpkg)
        for p in generated_targets:
            if p.exists():
                p.unlink()

    outdir.mkdir(parents=True, exist_ok=True)
    geojson_dir.mkdir(parents=True, exist_ok=True)

    print("\n=== SOURCE ===")
    print(source)
    print(f"size={source.stat().st_size / 1024**3:.2f} GiB")
    print(f"sha256={sha256(source)}")

    print("\n=== PUBLIC GPKG ===")
    for layer in PUBLIC_SPATIAL:
        copy_spatial_layer(source, public_gpkg, layer)
    for layer in PUBLIC_ATTRIBUTES:
        copy_attribute_layer(source, public_gpkg, layer)

    print("\n=== HAZARD MAP GPKG ===")
    for layer in HAZARD_MAP:
        copy_spatial_layer(source, hazard_gpkg, layer)

    print("\n=== GEOJSON ===")
    for layer, filename in GEOJSON.items():
        write_geojson(source, layer, geojson_dir / filename)

    print("\n=== SOURCE LICENSE CSV ===")
    write_license_csv(source, license_csv)

    print("\n=== VERIFY ===")
    public_layers = verify_gpkg(public_gpkg)
    hazard_layers = verify_gpkg(hazard_gpkg)

    if "plateau_disaster_risk" in public_layers:
        raise RuntimeError("plateau_disaster_risk unexpectedly published")

    expected_public = set(PUBLIC_SPATIAL + PUBLIC_ATTRIBUTES)
    expected_hazard = set(HAZARD_MAP)

    if set(public_layers) != expected_public:
        raise RuntimeError(
            "Unexpected layer set in 13_heritage_public.gpkg.\n"
            f"Expected: {sorted(expected_public)}\n"
            f"Actual:   {sorted(public_layers)}"
        )

    if set(hazard_layers) != expected_hazard:
        raise RuntimeError(
            "Unexpected layer set in hazard_map.gpkg.\n"
            f"Expected: {sorted(expected_hazard)}\n"
            f"Actual:   {sorted(hazard_layers)}"
        )

    print("public GPKG:", len(public_layers), "layers/tables")
    print("hazard GPKG:", len(hazard_layers), "layers")

    print("\n=== OUTPUT SIZES ===")
    for p in sorted(x for x in outdir.rglob("*") if x.is_file()):
        mib = p.stat().st_size / 1024**2
        flag = "  <-- CHECK GITHUB SIZE" if mib >= 95 else ""
        print(f"{mib:9.2f} MiB  {p.relative_to(outdir)}{flag}")

    print("\nSUCCESS")
    print("output:", outdir)


if __name__ == "__main__":
    main()
