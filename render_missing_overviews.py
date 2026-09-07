#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pyogrio

from tools import render_summary_maps as r


def main() -> None:
    ap = argparse.ArgumentParser(
        description='Render only the missing liquefaction and landslide overview maps.'
    )
    ap.add_argument('source', type=Path)
    ap.add_argument('--results-dir', type=Path, default=Path('summary_results'))
    args = ap.parse_args()

    r.configure_fonts()
    source = args.source.expanduser().resolve()
    results = args.results_dir.expanduser().resolve()
    overview = results / 'figures' / 'overview'
    tables = results / 'tables'
    cache_loc = results / 'cache' / 'analysis_locations.gpkg'

    if not source.exists():
        raise SystemExit(f'ERROR source not found: {source}')
    if not cache_loc.exists():
        raise SystemExit(
            'ERROR: run build_summary_results.py first; '
            'analysis_locations.gpkg is missing'
        )

    overview.mkdir(parents=True, exist_ok=True)

    locations = pyogrio.read_dataframe(cache_loc, layer='analysis_locations')
    locations = r.ensure_wgs84(locations)
    rep = locations.drop_duplicates('record_id')

    contents = r.gpkg_contents(source)
    admin = pyogrio.read_dataframe(source, layer='admin_boundary_n03_2024')
    admin = r.ensure_wgs84(admin)

    print('=== MISSING OVERVIEW MAPS ONLY ===')
    r.plot_liquefaction_overviews(source, rep, overview, admin)
    r.plot_landslide_overview(
        source,
        contents,
        rep,
        overview / 'landslide_mainland.png',
        tables,
        admin,
    )
    print('SUCCESS:', overview)


if __name__ == '__main__':
    main()
