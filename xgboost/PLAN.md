# xgboost — plan for rebuilding features / transforms / recipes / cook on the new access layer

Bottom line: the old modeling stack is portable, but two of its load-bearing mechanisms have no counterpart in the new access layer and must be *replaced by design rather than translated*. The old stack read a raster **cell grid** per site and bucketed cells by **euclidean distance to the sensor**; the new layer publishes covariates at **catchment (COMID) grain** and no grid exists. The replacement is flow-distance bucketing over the reach network, which is strictly more defensible hydrologically and happens to be cheaper. Everything else — targets, cross-site nitrate, long-run composition, static descriptors, the CV harness and its metric set — ports with mechanical changes.

Scope discipline: **nothing in this directory may require an edit to `data/access`.** Where a change there would clearly help, it goes in `project-notes.md` for Isaac to review, and the workaround lives here.

## The cohort

One filter, three conditions, applied once in `config.py` + `cohort.py`:

1. **Cornbelt seed box** — `[-100.272, 34.916, -81.192, 46.921]` from `data/build/parameters/scope.toml`. Read from that file at runtime, never copied as a literal, so the project cannot drift from the pipeline's own geography.
2. **Surface types** — `stream`, `ditch`, `canal`. Deliberately excludes wells/groundwater (different water), lakes/tidal (no contributing basin), and the sub-catchment practice types (`tile_outlet`, `saturated_buffer`, `bioreactor`, `field`, `blind_inlet`) whose basin is not their contributing area — the same `NO_SURFACE_BASIN` / `SUB_CATCHMENT` logic the pipeline already encodes, applied as a project preference.
3. **≥365 days of nitrate** in the published record.
4. **Basin at or under 50,000 km².** Added after measurement, not by anticipation: the cohort's basins are median 1,157 km², but the mainstem records reach 1.77M km² (Mississippi at Cape Girardeau — 640,172 upstream reaches). A catchment-mean corn share over half a continent is a national average wearing a site's name, and one such basin dominates any pooled statistic it enters. The ceiling keeps 171 of 188 records and is measured against VAA's own `totdasqkm`, so it costs a lookup rather than a walk.

Measured against the current (pre-publication) sensors table: **190 sensors** (106 USGS, 79 IWQIS, 5 MPCA), median 1,441 nitrate days, **339,563 sensor-days** — versus the old cohort's 116 sites / 175,973 rows. Roughly 1.6× the sites and 1.9× the rows before any modeling change.

`cohort.py` is the project's own thin layer over `data.access`: it resolves the cohort, caches per-site records, and exposes exactly the site view the feature builders consume. Every read in this project goes through it; nothing else imports `data.access` directly. That keeps the "no access-layer edits" rule enforceable by inspection.

## What replaces the distance buckets

The old geometry (`src/features/transformers.py`): a per-site **grid** of covariate cells, each carrying `frac_cell_in_basin` and `dist_to_sensor`; cells bucketed by euclidean distance edges (`REG_EDGES=(8_000, 30_000)`, `CLF_EDGES=(5_000, 20_000)`), aggregated per bucket with either an area-weighted mean or an `exp(-d/λ)` decay weight, and — for weather — shifted by a travel-time lag `d / velocity`.

The new geometry: a site's basin is a **weighted COMID set** (`accumulate` from its snapped reach, or `aggregate.from_polygon` for a custom polygon), and every covariate is already reduced to the catchment. So the bucketing axis becomes:

**Flow distance**, from NHDPlus VAA `pathlength` — the distance along the flow path from a reach to its terminal outlet. For upstream reach *R* and the sensor's reach *S*, `flow_dist(R) = pathlength(R) − pathlength(S)`, in kilometers, ≥ 0 by construction over an upstream set. Buckets are edges on that quantity.

Why this is better than a translation of the old euclidean bucketing:

- **It is the distance water actually travels.** The old euclidean distance was a proxy for it, and a poor one where a basin wraps around a divide: two cells equidistant from the sensor can be 3 km and 40 km of channel away.
- **Travel-time lags become principled.** VAA also ships `pathtimema` (modeled mean path time), so the lag for a bucket can come from the authority's own hydraulics rather than from a hand-set `_VEL` constant. Fall back to `flow_dist / velocity` where `pathtimema` is absent.
- **It costs nothing.** `pathlength` is a column on a table we already load; no per-site raster grid, no distance matrix.

Bucket weights: each catchment contributes its `w` (the weighted-set membership, 1.0 for a plain accumulate, fractional for a polygon cut) times its area — the same "partial cells count partially" semantics as `frac_cell_in_basin`, at a different grain. The exp-decay variant carries over verbatim with flow distance substituted, and is worth keeping precisely because the old ablations found the decayed and undecayed surplus blocks *both* earned their place.

**Consequence to state plainly:** feature values will NOT match the old model's numerically, and no ablation result transfers. The old `_b0/_b1/_b2` columns and the new `_f0/_f1/_f2` are different quantities. Every curation decision inherited from `recipes.py` is therefore a *hypothesis to re-test*, not a settled result — the plan below treats them as starting points, and the first experiment is a full-feature-set baseline against which the old drops get re-measured.

## Basins are ours to build

The access layer publishes the network and the machinery (`snap`, `accumulate`, `aggregate`) but **not** basins — deliberately, since delineation is a modeling preference. So `basins.py` owns it:

- **Primary**: `accumulate ∘ snap` — snap the sensor's stated coordinates to a reach, walk `fromnode`/`tonode` upstream, return the COMID set with areas. This is the basin-editor's `basin1`, proven to 0.000% against VAA `totdasqkm` on the worked example.
- **Guards**, ported from what the pipeline learned: refuse the walk on `NON_DRAINAGE_FTYPE` (coastline) reaches; flag basins above a CONUS ceiling; flag `snap_status != "ok"` sites; record the area against `vaa.totdasqkm` and keep the discrepancy as a column (it is a data-quality feature in its own right).
- **Cached** to `xgboost/cache/basins/{snr_id}.parquet` — the walk is seconds per site but hundreds of sites × repeated experiments is not, and the cache is a plain artifact the project owns.
- A `basin_km2` sanity table is written once so a bad delineation is visible before it becomes a feature.

## Module by module

### `config.py`
Cohort constants (seed-box name, surface types, min nitrate days), bucket edges per task, cache paths, the target threshold (10 mg/L), and the CV parameters. Nothing computed; it is the project's `scope.toml`.

### `cohort.py` — the project access layer
- `sites()` → the cohort frame (uid, snr_id, lat/lon, comid, type, n_days_nitrate), filtered per `config`.
- `record(code)` → the published daily record for one site, via `access.get_record`.
- `basin(code)` → the weighted COMID set, via `basins.py`, cached.
- `covariates(code, products)` / `weather(code, window)` → per-basin reductions via `access.get_covariates` / `get_weather`.
- `pooled_nitrate()` → the all-sites daily nitrate matrix backing the cross-site features (the old `_state_daily_wide`), cached once per store stamp.

Every function keyed on the **SNR code**, not the source uid — the published record is SNR-keyed, and using the source uid would silently pick one member of a merged bundle.

### `transforms.py`
The mechanical half, ported and re-grounded:
- `flow_buckets(wset, outlet_comid, edges)` → COMID → bucket index, on flow distance.
- `agg_to_buckets(frame, mapping, keys, how)` → the group-and-weight primitive (sum / weighted mean / exp-decay), the direct heir of `agg_grid_to_buckets`.
- `bucket_lags(wset, mapping, velocity=None)` → per-bucket travel time, from `pathtimema` when present else `flow_dist / velocity`.
- `lag_buckets`, `flatten_buckets`, `merge_on_date`, `tag_values` — port nearly verbatim; they are frame plumbing with no geometry in them.

### `features.py`
Ported one-for-one where the input exists, with the bucketing swapped:

| old | new | note |
|---|---|---|
| `daily_nitrate`, `nitrate_daily_rolling`, `nitrate_violations*` | same, off `cohort.record` | targets; the published record is already daily and masked |
| `agg_crops`, `agg_crops_normalized` | same, over `access.get_covariates(..., "crops", remap="default")` | the 8-class remap reproduces the old class set at read time |
| `agg_surplus`, `agg_surplus_normalized` | same, over `"nutrients"` | gTREND surplus + fertilizer; the old `total_kg_N` dead column simply never gets built |
| `agg_weather`, `agg_weather_w_lag`, `agg_weather_normalized` | same, over `access.get_weather` | gridMET variable names differ from the old fire-weather set — see below |
| `rolling_precip` | same | basin-mean precip, trailing sums |
| `site_static` | rebuilt | see below |
| `longrun_composition`, `longrun_cell_rotation` | same reductions, per-catchment instead of per-cell | the per-cell rotation becomes per-catchment; the cancellation argument still holds |
| `nitrate_avg_except_this`, `rolling_nitrate_avg_except_this` | same, off `cohort.pooled_nitrate` | now over a 190-site cohort rather than 116 |
| `neighbor_nitrate` | rebuilt on `access.get_proximity` + the reach walk | the old reach-graph came from build3's basins graph, which no longer exists |
| `doy_climatology_pure_signal` | verbatim | dates only |

**Two honest gaps:**

1. **The old weather set is not the new one.** The old features leaned on fire-weather indices (`fuel_moisture_1000h` was the *only* weather prefix that survived curation in both tasks, per `recipes.py`) which the new pipeline does not carry — gridMET's fire indices were not in the acquired variable list (pr, tmmn, tmmx, rmin, rmax, sph, vs, srad, vpd, pet, etr). Options, in order of preference: (a) derive an equivalent dryness index from what we have (VPD and the ET pair are the physically closest, and a trailing dryness integral is what `fuel_moisture_1000h` effectively *is* — a ~40-day moisture memory); (b) log it in `project-notes.md` as a candidate acquisition. The plan takes (a) and measures it, and the derived column is named honestly (`dryness_40d`, not `fuel_moisture`).
2. **`site_static`'s tile block.** The old version read AgTile fractions and `cat_tiles92` off the grid; the new one reads `agtile` from `get_covariates` (tile-drained area over the cultivated denominator) and StreamCat catchment attributes are *not* currently published by the access layer — noted in `project-notes.md`. The static block therefore becomes: basin area (log), tile fraction, sensor lat/lon, snap distance, basin-area-vs-VAA discrepancy, upstream reach count, mean slope from VAA, and the long-run composition block.

### `recipes.py`
Same shape as the old: per-task curated lists, `island` and `network` variants, `_assemble` merging blocks onto a date spine and broadcasting statics. The curation constants are carried over as *starting hypotheses* with their provenance noted, and the first experiments re-measure them. `network` needs a donor definition that does not depend on build3's basins graph: use `access.get_proximity` for near neighbours plus an upstream/downstream test over the reach walk.

### `cook.py`
The CV harness ports with the least change of anything — it is already access-agnostic, taking a recipe callable and a site list. Ported: `folds_grouped`, `folds_true_lofo`, `folds_lodo`, `_fold_models`, `_score`, the full score-column set, gain + permutation importance, and the log.json writer. Changed:
- **`basin_groups`** built from the new network: two sites are in one family when one's basin contains the other's outlet (containment over the walked COMID sets), which the old version approximated through build3's basins graph.
- Metric names stay **exactly** as they are (`lofo_r2`, `lofo_prauc` are load-bearing).
- The log gets a `cohort` block (n sites, n rows, seed box, filter) so runs from this project can never be confused with the old ones.

## Baselines to beat

From `experiments/feature-manifest/log.json`, best runs of 2026-07-31 on 116 sites / 175,973 rows:

| task | metric | old best | run |
|---|---|---|---|
| REG | `lofo_r2` | **0.4818** | `drop_crops_norm` (89 feat) |
| CLF | `lofo_prauc` | **0.7223** | `drop_min_rel_humidity_expF` (124 feat) |
| CLF | `lofo_auc` | **0.8783** | `full_feature_set` (127 feat) |

These are *not* like-for-like targets: the new cohort is larger (190 vs 116 sites), geographically wider (the full cornbelt box rather than mostly Iowa), and the features differ by construction. A drop in headline number on a harder, wider cohort is not necessarily a regression — the honest comparison is (a) new model on the new cohort, reported plainly, and (b) new model restricted to the *old* 116-site list where those sites still exist, which is the only apples-to-apples read. The plan runs both.

## Order of work

1. `config.py`, `cohort.py`, `basins.py` — cohort resolves, basins build and cache, area check against VAA passes.
2. `transforms.py` — flow buckets, aggregation primitives; unit-tested on one site against a hand computation.
3. `features.py` — targets first (they gate everything), then covariate blocks, then cross-site, then statics.
4. `recipes.py` — island REG/CLF; network after.
5. `cook.py` — port; smoke on ~10 sites.
6. **Experiment 1**: full feature set, island, both tasks, whole cohort → the new baseline.
7. **Experiment 2**: the old cohort subset → apples-to-apples against 0.4818 / 0.7223.
8. **Experiment 3+**: re-measure the inherited drops (crops_norm, weather prefixes, longrun sd, cross-site blocks), then bucket-edge sweeps on the *new* geometry.
