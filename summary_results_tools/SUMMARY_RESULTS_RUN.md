# Summary Results generation

## 1. Dependencies

The project virtual environment already contains pandas, geopandas, pyogrio, shapely and matplotlib.
For Z=16 maps with OpenStreetMap tiles, add contextily:

```bash
python -m pip install contextily
```

## 2. Generate tables and risk assignments

```bash
python tools/build_summary_results.py \
"/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
--force
```

This stage is read-only for the source GeoPackage.
It may take time because inundation and tsunami point-grid layers are sampled for cultural-property locations.

For a quick first test that skips the heavy inundation/tsunami point-grid sampling:

```bash
python tools/build_summary_results.py \
"/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
--skip-point-water --force
```

## 3. Render overview maps

```bash
python tools/render_summary_maps.py \
"/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
--stage overview
```

## 4. Render Z=16 detail maps

```bash
python tools/render_summary_maps.py \
"/Users/noguchiatsushi/Library/CloudStorage/OneDrive-個人用/ArchaeoDataScience/PLATEAU_Heritage/13_heritage_hazards.gpkg" \
--stage detail
```

Default detail extent is a 2 km × 2 km square centered on each requested point, with OSM tiles requested at zoom 16.
Change it with `--detail-radius-m` if required.

## 5. Output

```text
summary_results/
├── tables/
├── cache/
│   └── analysis_locations.gpkg
├── metadata/
│   └── run_summary.json
└── figures/
    ├── overview/
    └── detail/
```

## Current data caveat

The profiled `admin_boundary_n03_2024` layer covers the mainland Tokyo extent only. Therefore the municipal choropleth is generated for the administrative-boundary coverage actually present in the source GPKG. Izu and Ogasawara are represented with point/hazard overview maps instead. `choropleth_unmapped_municipalities.csv` records any municipality counts that cannot be matched to the included N03 polygons.
