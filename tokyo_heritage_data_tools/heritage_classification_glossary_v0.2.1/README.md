# Heritage Classification Glossary v0.2.1

This glossary is designed for classification **before** first Heritage-GML/GPKG generation and for attribute-only patching of already-generated outputs.

Key change from v0.2.0: municipal-source records are not automatically treated as municipal designations. Explicit national/prefectural/local category wording is respected, and source-specific unprefixed categories that cannot safely identify the authority are `unknown`. In batch mode, unknown municipal-source rows may be resolved only by exact normalized name + municipality-code matches against the already-acquired national and Tokyo datasets. No fuzzy/spatial matching is used.

Recommended municipal input is `municipal_all_normalized.csv`, not the prefiltered `municipal.csv`, because many local datasets omit the authority prefix in their category labels. The classifier then emits the GML-ready `municipal_classified.csv`, cross-level duplicates, and unresolved review rows separately.

荒川区 13118 uses the bundled row-level override from `指定年度` / `登録年度`.
