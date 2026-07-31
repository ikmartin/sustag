# Raster investigation — legacy IWQIS vs HydroSHEDS flow direction

Motivation: swapping the D8 flow-direction raster from the legacy IWQIS Iowa PNG (`direction500m.png`) to HydroSHEDS (`flowdir_15s.tif`) coincided with a small drop in the Iowa model (base CLF `between_rate_r2` 0.334→0.269, base auc 0.864→0.848; see `experiments/feature-manifest/EXPERIMENTS.md`), suggesting "the original Iowa raster is better/more accurate." This directory tests that claim four ways. **Headline: the premise is not supported.** Against NHD ground truth HydroSHEDS is *marginally more* faithful to Iowa's real channel network, not less; the HydroSHEDS handling in `src/data/d8.py` is correct (not a bad implementation); and the model shift is explained by `dist_to_sensor` moving ~1.4 km per cell (a different-but-equally-valid feature realization) against a noisy cross-site metric — not by the new raster being a worse truth.

Everything here drives the real `src/data/d8.py` at each source (HydroSHEDS when `flowdir_15s.tif` is present, legacy fallback when it is hidden) so the comparison reflects exactly what the pipeline computes.

## The two rasters

| | legacy IWQIS `direction500m.png` | HydroSHEDS `flowdir_15s.tif` |
|---|---|---|
| grid | 1741×1057, ~0.004167° (~15 arcsec) | Iowa clip of the global 15 arcsec grid |
| encoding | numeric keypad (7 8 9 / 4 5 6 / 1 2 3; 0 = nodata) | ESRI powers-of-2 (E=1, SE=2, …, NE=128; 0 ocean, 255 sink) |
| georef | hardcoded origin (no CRS metadata) | proper GeoTIFF, EPSG:4326 affine |
| source | IIHR / Iowa Flood Center (IWQIS) | HydroSHEDS v1, SRTM-derived, hydrologically conditioned |
| coverage | Iowa only | global (this is why the pipeline moved to it) |

Both are the *same resolution class* (~15 arcsec ≈ 500 m at Iowa's latitude). The "500 m" in the legacy filename is not a higher-fidelity product — it is the same coarse grid as HydroSHEDS.

## Q1 — Direct cell-by-cell comparison (`compare_rasters.py`)

Sampling both rasters at the same Iowa cell centers and comparing the downstream direction of every cell valid in both (1.27 M cells):

- Flow-direction agreement at nominal alignment: **26.8%**.
- **Shift test** (re-sample legacy at ±1 cell offsets): agreement peaks at **47.0% when legacy is shifted +1 cell in latitude** (vs 26.8% at zero, 17–24% elsewhere). The clean single-pixel peak means (a) there is a ~1-cell (~460 m) latitude *registration* offset between the two products as georeferenced, and (b) once aligned they still agree only ~47%.

Two conclusions. First, ~47% best-aligned agreement is far above random (12.5% for 8 directions) but far below "same data" — these are **genuinely different hydrography products**, agreeing on strong-gradient main stems and disagreeing across flats where D8 direction is DEM/conditioning-sensitive. Second, the ~1-cell latitude offset lives in the legacy raster's absolute georef (HydroSHEDS is a standards-compliant GeoTIFF; the legacy PNG carries a hand-coded origin). In practice the offset is absorbed by outlet snapping in `d8._snap_outlet` and by the downstream analyses' ±1-cell tolerance, so it is a minor legacy quirk, not a driver of the model shift.

## Q2 — Sources and CONUS availability (the source of the source IS national)

Provenance chain, from the served PNG back to a national dataset:

1. **How IWQIS produces it.** The IWQIS / Iowa Flood Information System watershed + raindrop tools (the Demir group's RasterJS, Shahid et al. 2023) do not ship a bespoke flow product — they compute D8 flow direction *from a DEM* ("elevation dataset" is a selectable input) via Priority-Flood conditioning + D8. So `direction500m.png` is a precomputed D8 grid derived from some elevation model, not a primary dataset in its own right.
2. **The DEM under it.** The Iowa Flood Center's hydrography is built from Iowa DNR statewide LiDAR (IIHR: "used laser radar (LiDAR) data provided by Iowa DNR to map all streams draining more than one square mile").
3. **That source is national.** Iowa's DNR LiDAR is folded into the USGS 3D Elevation Program (3DEP) — 10 m (1/3 arcsec) complete for CONUS, 1 m (pure LiDAR) expanding. The elevation source is a tile of a national program, not an Iowa-only survey.

So the answer to "is the Iowa raster derived from a source that exists for the rest of CONUS?" is **yes** — and USGS already publishes the *derived* national product. **NHDPlus HR** ships a **flow-direction (FDR) raster** as a standard component, built from 10 m 3DEP, hydro-enforced (streams burned in, sinks filled), produced for the entire conterminous U.S. and downloadable by HU4/HU8, by state, or nationally. That is a ready-made national D8 grid at ~10 m — roughly **50× finer** than *both* the IWQIS 500 m PNG and the HydroSHEDS 15 s (~500 m) grid the pipeline uses today.

Two honest caveats. First, no document literally states "`direction500m.png` = dataset X"; the chain above (IWQIS D8-from-DEM → IFC uses DNR LiDAR → LiDAR in 3DEP → NHDPlus HR FDR) is well-supported inference, not a cited provenance line for that exact file. Second, the served 500 m grid is a coarsened web layer regardless of source, so its fidelity is capped far below the underlying LiDAR/3DEP — consistent with Q3 finding it only on par with HydroSHEDS. The practical takeaway does not depend on nailing the 500 m provenance: the CONUS-wide upgrade ladder is HydroSHEDS 15 s (current) → MERIT-Hydro ~90 m (global, hydro-conditioned, lighter) → NHDPlus HR FDR ~10 m (3DEP/LiDAR-based, hydro-enforced, finest) — and any of these beats reverting to the coarse Iowa PNG.

Sources: [IWQIS (Iowa Nutrient Research Center)](https://inrc.cals.iastate.edu/project-location/iowa-water-quality-information-system), [IIHR Watersheds & Rivers (DNR LiDAR)](https://www.iihr.uiowa.edu/watersheds-rivers/), [RasterJS — Shahid et al. 2023 (IWQIS D8-from-DEM)](https://eartharxiv.org/repository/object/2979/download/12537/), [USGS 3DEP products](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services), [NHDPlus HR (FDR raster component, CONUS)](https://www.usgs.gov/national-hydrography/nhdplus-high-resolution), [WWF HydroSHEDS 15s drainage direction](https://developers.google.com/earth-engine/datasets/catalog/WWF_HydroSHEDS_15DIR), [MERIT Hydro](https://developers.google.com/earth-engine/datasets/catalog/MERIT_Hydro_v1_0_1).

## Q3 — Is the "badness" an implementation bug? (`validate_vs_nhd.py`, `dist_impl_and_buckets.py`)

No, on both checks.

**Which raster is actually more accurate — vs NHD ground truth.** Derive each raster's stream network by flow accumulation, threshold at a fixed drainage area, and score against the NHD medium-res flowlines (streamorder ≥ 3, 61,562 features):

| threshold | legacy precision / recall | HydroSHEDS precision / recall |
|---|---|---|
| ≥ 200 km² | 70.2% / 10.6% | **74.8% / 12.4%** |
| ≥ 1000 km² | 73.1% / 5.1% | **76.6% / 6.0%** |

HydroSHEDS matches the real channel network **as well or slightly better** than the legacy raster on both precision and recall, at both thresholds. (Recall is low because the big-river thresholds don't recover NHD's small order-3 tributaries; precision is the discriminating metric.) This directly refutes "the Iowa raster is more accurate." It also **validates the HydroSHEDS handling in `d8.py`**: a garbled ESRI-encoding or georef would produce incoherent directions scoring near random (~12%), not 75% precision against NHD.

**Legacy-path parity.** The current `d8.py` legacy path reproduces the pre-refactor git-HEAD `d8.py` **byte-for-byte** (max |Δ| = 0 m over 25,616 cells for USGS-05412500). The d8 rewrite did not change legacy `dist_to_sensor`, so the model shift is the raster, not the refactor.

## Q4 — Then why did the model move? (`dist_impl_and_buckets.py`)

Because `dist_to_sensor` is triple-load-bearing (base feature `mean/max_dist_to_sensor`, distance-**bucket key**, and exp-decay weight `exp(-dist/λ)`), any raster change perturbs the whole feature matrix. Measured legacy-vs-HydroSHEDS over the same grid cells for 8 sites:

- median |Δ dist_to_sensor| ≈ **1.44 km per cell** (range 0.8–6.4 km across sites);
- but **discrete bucket flips are small**: overall **1.7%** of cells change REG bucket (50 km edge), **0.4%** change CLF bucket (2 km / 5 km edges) — though small basins are volatile (USGS-05464475: 8 cells, 25% CLF flips).

So the shift is driven mainly by the **continuous** channels — `mean/max_dist_to_sensor` and especially the exp-decay weight (at CLF λ = 2 km a ~1.4 km move near the sensor changes the weight substantially) — not by wholesale re-bucketing. Combined with the known noise of `between_r2`/`between_rate_r2` at 79 sites / ~20 families (single-run deltas ±0.05–0.09), the base-model change is consistent with a **different-but-equally-valid feature realization plus CV noise**, not a genuine accuracy regression.

## Recommendation

1. **Keep HydroSHEDS.** It is national (the whole point of Phase 2) and, on the NHD ground-truth test, marginally more faithful to Iowa's channels than the legacy raster. There is no accuracy case for reverting to the Iowa PNG.
2. **Do not attribute the model drop to a bad raster or a bad implementation.** It is `dist_to_sensor` moving ~1.4 km/cell against a noisy cross-site metric. Re-judge feature experiments only at a **fixed** raster (already the rule in `EXPERIMENTS.md`), and stamp `d8._SOURCE` into every run (EXP-0a).
3. **If raster accuracy is ever the bottleneck, upgrade — don't revert.** MERIT-Hydro (~90 m) or NHDPlus HR flow direction (~10–30 m) are national and finer than both rasters here; either would beat the legacy IWQIS product it was thought to be worse than.

## Files

- `compare_rasters.py` — Q1 direct cell-by-cell comparison + shift test.
- `validate_vs_nhd.py` — Q3 which raster's derived streams match NHD better.
- `dist_impl_and_buckets.py` — Q3 legacy-path parity + Q4 dist shift / bucket-flip sensitivity.
