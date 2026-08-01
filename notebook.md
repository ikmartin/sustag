# [2026-08-01]

> 🛑 **Work stops until 08-11.** Read [notes/aug-01.md](notes/aug-01.md) first — state-of-project, replaces 14 notes deleted today (all git-tracked, `git restore notes/` recovers them).

## Ready to run

`xgb_config.json` is written and sanity-checked → `cd experiments/xgb-neighbor-count && python run_exp.py --task both`. REG `depth 3, mcw 1231.811, lam 1231.811, 200 trees` (`lofo_r2` 0.4913); CLF `depth 4, mcw 21.98, lam 659.4, 250 trees` (`lofo_prauc` 0.7552). The tuner finished both searches then **died before writing the file** (stderr lost; likely broken pipe — all disk artifacts intact); configs reconstructed from the STAGE 6/6 rows of `tune_<task>.csv`, story in `_provenance`. Narrow-arm check passed both. Watch: REG's `mcw` and `lambda` are byte-identical at 1231.811.

## 🔴 We optimize the wrong metric, and the one we report isn't comparable

**Pandit's 0.34 is a median KGE, not NSE** — no spatial NSE is published, so comparing `macro_r2` to it is invalid. Under the MSE-optimal map (α=r, β=1): NSE = r², KGE = 1−√2(1−r), so **KGE 0.34 ≈ NSE 0.284**. Best `lofo_macro_r2` anywhere is 0.3067; **nothing beats 0.34 — 0 of 233 tuner configs, 0 of 255 manifest entries.** Everything that does clear it is pooled. The map assumes zero per-site bias, which is what fails at a held-out site, so 0.284 is the optimistic threshold.

**New task T1.2b.** The objective, the prefix scan and the tuner all select on pooled row-wise quantities. MSE-optimal predictions are shrunk toward the conditional mean, so α = σ_p/σ_y < 1 systematically — and **KGE penalises α directly**, so reporting KGE while training on pooled MSE trains for the failure the metric punishes. Fix: row weights `1/n_rows(site)` first, then select on `macro_nse`. **Trap:** don't per-site standardise the target — the site mean is unknown at a pin, so it scores well under LOFO and is unusable.

## 🔴 Two finished experiments nobody has read, in `logs/experiments.md`

`discrete-sample-pre-check` and `in-situ-pre-check` import *specific-small-experiments*' `run_exp`, so they inherit its logger and write there; their own directories hold only `cache/` and read as "never ran". Both post-D8-fix.

**`grab_agency` (E2) — dead for REG, fragile for CLF.** REG winner +0.0073 is inside the 0.0085 floor and the `grab_coverage` negative control carries 73% of it. `grab_level` — the arm with the actual measured level — is the *worst* arm, so the ρ=+0.53 basin aggregate is fully absorbed by crops/surplus/tile. CLF `grab_all` clears at +0.0079 (2.1× floor) but superadditively: components sum to +0.0041, every one individually sub-floor. Replicate across seeds first.

**`insitu_q_r*` (T2.9) — complete, both tasks.** Δ vs base (REG 0.4253 / CLF 0.6892):

| r km | REG cover | REG concur | REG lag | CLF concur | CLF lag |
|---|---|---|---|---|---|
| 0 | +0.0127 | −0.0204 | −0.0282 | −0.0182 | −0.0214 |
| 5 | +0.0047 | −0.0149 | −0.0213 | −0.0243 | −0.0283 |
| 10 | **+0.0180** | −0.0125 | −0.0033 | −0.0087 | −0.0134 |
| 20 | +0.0140 | −0.0148 | −0.0188 | −0.0057 | −0.0138 |
| 50 | +0.0021 | **+0.0136** | **+0.0153** | **+0.0118** | **+0.0086** |

**The sign flips at r50, the wrong way for a leakage story.** Small radii *hurt* on both tasks despite 36 sites having a gauge within 200 m; only the negative control helps. At r50, donors forced onto distant mainstems, real discharge clears the floor and coverage collapses. **Hypothesis:** gauages close to one another are intended to measure some specific effect, for instance, a **nitrate removal fascility**. Why else would you install two sensors right next to each other? Our current model has no way of seeing these additional features.

## Other

- **WQP volunteer data is ordinal** — exactly 7 distinct values {0,1,2,5,10,20,50}, 99.8% on colorimetric strip levels vs 3,558 distinct agency values. Mechanically explains the −0.42 anti-correlation; a basin *mean* over it is meaningless. Rescue (untested): fraction-above-threshold is legal on an ordinal scale, and volunteer is 2,595 of 3,359 contained stations — the only route from 57% back to 79% coverage.
- **613 agency results are non-detects carrying negative `n_mgl`, min −347.59**, uncensored. One such sample destroys a basin mean built over a median of 2 stations. Not in the E1 procedure.
- Grab-sample coverage survived the D8 rebuild unchanged (79.3% / 56.9% agency-only) — no redo needed.
- **GNN dismissal withdrawn**, [notes/modeling-proposals-report.md](notes/modeling-proposals-report.md) rewritten as a six-proposal portfolio. The transitive-closure argument was measured on the 116-node site graph, not the graph worth building (regional gauges + COMID reaches as routing intermediates) — Delaware models 456 segments with 183 observed. Sharpest new risk: Graph WaveNet's self-adaptive adjacency comes from per-node embeddings and **a virtual pin has none**, the same transductive objection that kills per-site random effects.

---

# [2026-07-31]

> ⚠️ **every score computed before today is invalid, island and network alike.** A D8 pour-point snapping bug put 28 of 116 sites' outlets on the wrong river, corrupting `dist_to_sensor` (a base feature in *every* recipe) and `flow_dist_m` (donor selection, so exps 36/36c especially). Fixed; site grids and the containment graph are rebuilt. Re-run anything you intend to compare across it. → [notes/D8_bug_fix_explained.md](notes/D8_bug_fix_explained.md)

## 🔴 11% of the cohort is not a stream gauge, and nothing records it

Investigating why 8 pairs of nearby sensors are uncorrelated turned up that they are **paired inflow/outflow monitoring of engineered nitrate-removal features**. The metadata says so outright:

```
WQS0012  "monitors the INFLOW of water to the CREP wetland on Slough Creek"    median 9.0 mg/L
WQS0008  "monitors the OUTFLOW of water from the weir of the CREP wetland"     median 4.8 mg/L
```

Five such pairs, and the outflow member is lower every time — 47%, 72%, 79%, 82%, 88% reduction (CREP wetland, Mud Creek wetland outlet, Perry Pond, Catfish Creek nutrient-trading study, Clear Creek drainage ditch). The low correlation *is the measurement working*.

**Why this breaks the models.** We predict stream nitrate from watershed land use, crops, surplus, weather. A wetland outflow's nitrate is set by the wetland, not the watershed — the covariates say "high-nitrate ag catchment" and the sensor reads 2.3 because 79% was removed in between. No feature in any recipe can express that. Tile outlets are worse: `WQS0055`/`WQS0056` (ARS tile sites) read 17.9 and 15.7 mg/L, far above any stream, and their delineated "basin" is a field, so the whole covariate aggregation is meaningless.

Scanning `iwqis_river` / `iwqis_description` / `iwqis_nickname` over the contract:

| type | n |
|---|---|
| wetland / pond / oxbow / CREP | 5 |
| tile / edge-of-field | 4 |
| ditch | 4 |
| **any non-stream type** | **13 / 116 (11%)** |
| research / experimental marker | 27 / 116 (23%) |

Those three metadata columns are already fetched and **read by nothing**.

**The fix (Isaac's, and it's the right one): subtract the treated area.** For a sensor below a treatment feature, delineate its basin and then *remove the overlap it shares with the feature's contributing area*. For the CREP pair that is `basin(WQS0008) − basin(WQS0012)`, leaving only the untreated land draining directly to the sensor. The containment graph already knows which basins are nested, so the machinery exists. Caveat: subtracting the whole overlap assumes 100% removal when the measured reductions are 47–88%, so it over-corrects slightly — but it is far closer than counting the treated area at full weight, which is what happens today.

Also: **`WQS0066` is the "Monona-Harrison Ditch"** — an engineered channel. It is the one site whose outlet snap survives the D8 fix (still 4.93× oversized), and routing a constructed ditch over a natural-terrain DEM should be expected to fail. Physical mismatch, not a remaining bug.

Cheap test, no new fits: these 13 should be among the worst-predicted sites. `eval_<task>.csv` from the neighbour-count experiment carries per-site scores — just join on the classification.

## 🔴 Three site pairs are the same gauge registered twice

Sequential re-registrations, all under 52 m apart with **zero overlapping days**, and they are exactly the 3 cycles that make the containment graph not a DAG (each polygon contains the other's sensor, so containment runs both ways — water flowing uphill):

| survivor (IWQIS preferred) | absorbed | gap between records | daily obs |
|---|---|---|---|
| `WQS0032` | `USGS-05483600` | 142 d | 2,540 / 1,490 |
| `WQS0081` | `USGS-05481000` | 186 d | 1,484 / 1,254 |
| `WQS0024` | `USGS-05451210` | 1,235 d | 2,612 / 367 |

Merging is a **gain**, not just hygiene: `USGS-05451210` covers 2010–2011 and `WQS0024` covers 2015–2025, so keeping only one throws away half the record. Where a brief co-deployment lets it be checked (`USGS-05482500`/`WQS0082`, 22 shared days) the two agencies agree at **r = 0.898, median difference −0.02 mg/L**.

**Do NOT deduplicate by distance.** Eight further pairs sit 200 m–1.9 km apart and are *concurrent*, sharing 389–2156 days at r = 0.006–0.589 — those are the treatment-feature pairs above, genuinely distinct sensors. The test for "one site" is near-coincidence **AND** non-overlapping records. Spec in [notes/audit-of-data-pipeline-0731.md](notes/audit-of-data-pipeline-0731.md) under B12; merging takes the cohort 116 → 113 and dissolves all 3 graph cycles.

Results from some experiments that landed. Look at verdicts.

##### `exp32: longrun_mean` –– testing the long mean, std of crop and surplus features.

Ablation of mean and std experiments. Get one mean and one std feature per crop per basin and one per surplus feature per basin.

**Verdict:** Use only `longrun_mean` features for REG, `longrun_std` is slightly harmful. More of a tossup for CLF between `longrun_both` and `longrun_mean`. The std has positive signal for CLF, but is many more features. My guess is std will be useful as site count grows -- use `longrun_both` unless you need to cut stuff then use `longrun_mean`.

- `REG`: best is using only `longrun_mean` features (no standard deviation, including only std makes it worse than base), 0.4489 lofo_r2 with 81 features over 0.4102 lofo_r2 base recipe with 54 features. Best feature is `surplus_kgha_mean_b1` and `pct_corn_mean_b2`.
- `CLF`: best is both features, very marginally. Recipe `longrun_both` gets `lofo_prauc_lift` of 2.934 and `lofo_auc` of 0.8693, but has 119 features. Using only `longrun_mean` gets a `lofo_prauc_lift` of 2.914 (marginally better) and a `lofo_auc` of 0.872 (marginally better) with only 89 features. The base recipe does worst at `loso_prauc_lift` of 2.684 and `lofo_auc` of 0.8404. The std only recipes score higher than base.

#### Basic Network Feature Ablations

Experiments 33-36c are all ablations of variations on network features. They are all roughly of the following sorts of columns and recipes:

**9 Columns:**
- `nbr_anom_lag0`, donor's anomaly, lag0, in `concurrent` block
- `nbr_anom_lag1`, donor's anomaly, lag1, in `lagged` block
- `nbr_anom_lag3`, donor's anomaly, lag3, in `lagged` block
- `nbr_level_lag1`, donor's raw level at lag1, the only column that can move the between-site channel, since an anomaly is mean-zero per site by construction
- `nbr2_anom_lag`, the `second-hop` donor's anomaly at lag 1 (next site in the same direction)
- `nbr_n_neighbors`, static number, count of monitored neighbors, up + down
- `nbr_area_ratio`, log_10(donor basin area/own basin area) -- proxy for dilution
- `dist_to_neighbor`, meters along the containment edge, D8 flow distance, straight line distance if it doesn't exist, NaN if neither exists
- `neighbor_up`, flag indicator, 1.0 if chosen neighbor is upstream, 0.0 if downstream, NaN if no neighbor.

Then we have the following recipe protoypes:
- `nbr_concurrent` — lag0 only
- `nbr_lagged` — lag1 + lag3
- `nbr_level` — level_lag1 only
- `nbr_2hop` — lagged + twohop
- `nbr_coverage` — the four static scalars only. This is the control that separates "the donor's reading helps" from "merely knowing a donor exists helps"
- `nbr_all` — all nine
- `nbr_all_no_lag0` — all but lag0, i.e. the largest set a shipped recipe could actually carry.

**OVERALL VERDICT:**
- Use `nbr_all_no_lag0` for both recipes, use distance similarity for REG, use area similarity for CLF. Here are the best recipe scores for each task across these experiments:

**REG — `nbr_all_no_lag0` arm** (base 0.4489; floors: 0.0085 headline, 0.0258 between)

| exp | encoding | rule | lofo_r2 | between_r2 | cols |
|---|---|---|---|---|---|
| 36 | both | dist, full graph | **0.5120** | **0.5233** | 14 |
| 33 | both | area, immediate | 0.5097 | 0.5181 | 14 |
| 35 | signed | area, immediate | 0.5047 | 0.4993 | 7 |
| 34 | flag ← ships | area, immediate | 0.4986 | 0.4849 | 8 |

**CLF — `nbr_all_no_lag0` arm** (base 0.6729; floor 0.0037)

| exp | encoding | rule | lofo_prauc | between_rate_r2 | cols |
|---|---|---|---|---|---|
| 33c | both ← ships | area, immediate | **0.7555** | **0.5334** | 14 |
| 36c | both | dist, full graph | 0.7524 | 0.5092 | 14 |
| 34c | flag | area, immediate | 0.7320 | 0.4461 | 8 |
| 35c | signed | area, immediate | 0.7263 | 0.4345 | 7 |

##### `exp33: network features` –– testing basic network feature ablations
Test "nitrate from most similar upstream/downstream neighbor", where similarity is ranked by basin area similarity.
– `REG`: best is keeping all "no_lag0" features, get `lofo_r2` of 0.5097 with 95 features compared to base recipe of 0.4489. Best feature is `surplus_kgha_mean_b1` and then `pct_corn_mean_b2`.
- `CLF`: best is also the `nbr_all_no_lag0` faeature, `lofo_auc` of 0.8957 and `lofo_prauc_lift` of 3.249. Get 130 features over base recipe of 116 features. Not as big of a win as REG. Best feature is a longrun feature, `surplus_kgha_sd_b2`, a standard deviation feature.

##### `exp34: network features variant`
**Verdict:**

Only gets one neighbor, rather than `exp33` where each site has an up and downstream neighbor.



# [2026-07-30]

The new distance features landed in experiments 33 - 35c, and they improve everything. This is expected, but what is fascinating is that it basically closes the gap between lofo and loso everywhere it measured. So it moves lofo quite a bit, but actually it moves loso basically not at all (last full train had loso_auc of 0.88 and the neighbor feature had loso_auc of like 0.89). This is weird. This means that

- model with no neighbor features trained on all sites but 1, tested on that one
- model with neighbor features trained on all sites but 1 and the sites its connected to, tested on 1

Seem to perform the same... note that this isn't literally true because I'm using GroupKFold. What would be interesting is doing a literal LOSO run on all 119 sites, hold one site out at a time and train a model with no graph features. Then train a model in the same way WITH graph features, and compare results. Then compare to a graph feature model evaluated on LOFO. If all are roughly the same then maybe the full model trained on all sites WITHOUT 

*Code changes — the island/network split*
- **Two recipes per task now.** `recipe_REG`/`recipe_CLF` → `island_REG`/`island_CLF` (every feature computable at an arbitrary ungauged pin, statewide aggregates included); new `network_REG`/`network_CLF` add the reach-graph donor block. Network is a strict superset — REG +8 columns (flag encoding), CLF +14 (both directions) — and both share one aggregation pass rather than forking it.
- **The neighbour block moved from `exps.py` into `features.py`** as `neighbor_nitrate()`, with all six bug fixes intact. `lags` is a parameter and 0 is legal, so the same-day column stays *emittable*; `recipes._NBR_LAGS_*` = `(1, 3)` is where it is declined, which keeps the deployment-legality call in one place.
- **`train.py both` now trains four models.** 
- `tune.py`/`fulltune.py` gained `--variant`; `save_model` now records `recipe` in the sidecar, since four models share two targets and the filename was the only disambiguator.
- **`tune_threshold.py` repurposed** to `--target-recall` / `--target-fdr`, solved exactly off the PR curve. `train.build` already writes the beta table, and `BETA_GRID`'s 14 points snap, so "what τ catches 90% of violations" was previously only answerable to grid resolution. It now also refuses a model/recipe pair the sidecar contradicts.
- Added the `nbr_all_no_lag0` arm — one line in `_neighbor_ablation.masks`, so all four variants get it. Nothing measured the column set a recipe could actually ship: `nbr_all` includes lag0, `nbr_lagged` drops level/twohop/cover with it.

*Code changes — feature-manifest tuner*
- **`NOISE_CEILING = 0.003` cost rule**, applied to grid ranking, ceiling raises and expansion steps: a doubling of trees×leaves×samples must buy more than a noise floor or the cheaper config stands. Replayed on the 07-29 CLF grid it independently picks 12,000 trees (0.6768) over 26,530 (0.6791) — 0.65 of a floor for 2.2× the compute.
- **Ceiling growth is per config now**, not per stage: a bound config is refit alone while finished rows keep their scores, which is exact rather than an approximation since prefix scoring makes a config that chose k=400 of 1500 pick the same 400 of 6000. Plus `--max-ceiling` (6000), `--automatic-expansion` (off by default, **still untested**) and `--dump-curve`.
- **Diagnosis of the 22-hour CLF screen run:** stage 2 drove `lam` to the bottom of its ladder and stage 3 did the same to `mcw`, leaving `lr` as the only shrinkage — so the loss creeps down forever and `best_k` is bounded solely by whatever ceiling it is handed (1500 → 26,530, buying +0.0002 to +0.0023 per doubling). Every config that keeps a real `reg_lambda` terminates on its own.

*Code changes — feature-manifest experiment*
- **`run_exp.py` takes a required, mutually exclusive `--island` / `--network`.** No default on purpose: the two are not comparable — network is scored only where a donor resolves, and reach neighbours co-fall with the basin families LOFO holds out — so a bare invocation that silently picked one would write a number into the shared log that nobody could place later. Every entry carries `variant`; `render_results` puts it in the run listing (where a run actually gets chosen) and in the report header. Absent reads as "not recorded", not as island, same as `true_lofo`.
- **`--network` folds the donor block into the BASE**, so every candidate is screened over it and drop-one reports what removing it costs. Four drop units — `nbr_anom` / `nbr_level` / `nbr2` / `nbr_meta` — grouped like `_BASE_BLOCKS` for the same reason, since a 1-column drop cannot clear the floor. Column names derived from `recipes._NBR_LAGS_*` / `_NBR_ENCODING_*` rather than restated. There are deliberately no `add_nbr_*` arms: a candidate already in the base expands to exactly `base` and pays a full CV fit to log fold noise.
- **Hard guard on the donor column names.** `compare_many` silently drops columns the pool lacks — for a *candidate* that shows up as an add-one arm scoring exactly `base`, but these are base columns, so the same drift would shrink the base and move every number in the run with nothing saying why. Any `_nbr_blocks` name the pool does not hold now raises before the first fit. Verified by injecting a typo.
- Verified end to end on both encoding branches: CLF (`both`) 42 → 56 features with drop deltas 4/2/2/6, REG (`flag`) 41 → 49 with 2/1/1/4. Site count unchanged — the 12% with no donor keep the columns all-NaN and still pool.

*Bugs found*
- **`--dump-curve` had never worked, not once.** `curves += [...]` inside `score_point`, which is a closure: an augmented assignment *rebinds*, so `curves` compiles as local to the nested function and the first dumped row raises `UnboundLocalError`. The `rows.append(...)` on the line above survives only because it mutates instead of assigning. Fixed with `.extend`. Every run since the flag landed died on its first config, which is why no `*_curve.csv` has ever existed.
- **`_SCREEN_XGB_CLF` was orphaned.** `_DEFAULTS["screen"]["clf"]` pointed at `_FULL_XGB_CLF`, so the CLF add-one screen had been fitting base-sized arms at the *full* config all along and the 2690-tree screen block was dead code that `grep` finds exactly one reference to — its own definition. Fixed; screen points at its own dict again.
- **The CV splitter was under-linking conflict groups.** `build_conflict_graph` keyed its span lookup on `get_site_ids()` (116) while the containment graph carries 123 nodes, so the 7 filter-dropped sites got `None` from a dict *miss* — indistinguishable from "no nitrate record" — and every edge touching them was silently dropped. 5 hard leaks in the holdout audit; now 0, and **no cohort site changes fold**, so logged scores stay comparable.
- **The per-site grid cache was stale in a way mtimes cannot see.** Cached `frac_cell_in_basin` differed from a live rebuild on *every boundary cell* (interior cells matched exactly) by ≤1.6e-4 — a sub-metre shift in the reprojected basin outline, with unchanged polygon, unchanged code and unchanged library versions. Added `preferred_basin.csv` to the staleness inputs (it decides *which* basin file the check even guards) and a `_build_env.json` provenance stamp; the cache is correctly rejected until `_make_site_grids --force` reruns.
- **The grid builder could never finish — same 123/116 split as the CV splitter, mirrored.** `_make_site_grids._sites()` took its cohort from `preferred_basin.csv` (123) while `build_grid_live` → `get_location` reads the water contract (116), so the 7 filter-dropped sites failed with a bare `index 0 is out of bounds for axis 0 with size 0`. They contributed no cells, but the failures tripped the `if failed:` branch, so `_build_env.json` was **never** stamped and the cache stayed "not current" forever — every site computing its grid live, which is the whole cost the artifact exists to avoid. `_sites()` is now the intersection, with a `[warn]` for the converse hole (a cohort site with no preferred basin — none today), and `get_location` raises a `KeyError` naming the site instead of letting the positional index blow up. Leftover: 7 orphan parquets in `processed/grids/` for the dropped sites; they evaluate as not-current so nothing serves them, and they can't be rebuilt (no sensor location).
- `selftest.virtual_equals_real` was asserting `abs(area_a - area_b) > 1e-6` on a quantity of order 4×10⁹ m² — about 16 significant digits, at or past float64 — so it could fail on summation order alone. Now relative (`rtol=1e-9`), and frame comparison moved off bit-identity.

*Findings*
- 12% of the cohort (14 of 116 sites) resolves no donor in either direction — but these are gauged sites, which sit where someone chose to instrument. The figure for arbitrary map pins is unmeasured and is the number that decides whether `network` is the primary model or the fallback. (Re-measured through the shipped `recipes._neighbor_nitrate` path rather than the `exps.py` one: 102/116 = 87.9%, so the two agree.)
- New `_SCREEN_XGB_CLF` is **depth 3, lr 0.05** — the cost-adjusted winner of stage 1 at 0.6391 / k=2050 / fit_cost 8,036 (adj 0.6002), over depth 5 at 0.6421 / k=2950 / cost 46,256 (adj 0.5956). `n_estimators=500` is **extrapolated, not measured**.

*Next steps*
1. **Do not paste a fulltune winner into `RECIPE_XGB` until 36/36c have re-run.** `run_exp._xgb_config` reads it at import; that is exactly how exp 36 got confounded. It currently holds the values 33/33c used, which is the state wanted at launch.
2. Once the machine is free: `python run_exp.py --full 36 36c 34 35 34c 35c --extra`. One pass closes **G1** (36/36c finally comparable to 33/33c, and 36c run at all) and **G2** (`nbr_all_no_lag0` on every encoding — exp 33 is already picking it up from the in-flight `--full 32 33`).
3. **Measure the base plateau now that `--dump-curve` works** — `python tune.py --task clf --cols base --search "" --dump-curve --ceiling 1500 --max-ceiling 1500`, ~3 min, one config. Read the smallest k still within ~0.006 of its own peak and replace the extrapolated `n_estimators=500`. Cheap insurance on the config every add-one arm fits at, and it is the evidence needed before deciding whether to put the tolerance rule into `best_k`.
4. **Run the manifest, CLF first.** Four runs (`--island` / `--network` × `--task clf` / `--task reg`), and CLF is ~8× cheaper per fit than REG — `_SCREEN_XGB_CLF` is 500 trees at depth 3 (4,000) against `_FULL_XGB_REG`'s 490 at depth 6 (31,360) — so a mistake in the network arms surfaces in a fraction of the wall time. Extrapolating an 8-site smoke, this is an overnight job, not an afternoon one.
5. **Test `--automatic-expansion`** with a forced ladder edge (e.g. `--depths 3,4`) before using it on a real fulltune. Growth, the cost rule and the edge report are all verified; the expansion walk is not, because the smoke winner landed mid-ladder.
6. **Retune all four models.** Both `network_*` land untuned by design — `_tuned_iters` raises rather than defaulting, so `train.py both` trains the island pair and stops loudly. Widen the depth and lam ladders first: the last REG fulltune settled at the top of both, which means truncated, not optimal.
7. Decide **forecast vs nowcast** (G3). It is a product decision, not an experiment, and it determines whether lag0 is legal at all — unavailable to a forecast, available to a nowcast off a live donor feed. Only urgent if G2 shows lag0 is expensive to give up.
8. Still unrun: **E1** (in-situ discharge, network-only) and **E2** (discrete grab samples, possibly island-legal too). Read the coverage-only arm first in each — station density tracks land use, so a coverage arm carrying the effect means a sampling-effort fingerprint rather than a measurement.

# [2026-07-29]

Performed several ablations on the main features. Started with

```python
features = [wb, cb, cb_exp, sb, sb_exp, *lagged_avgs, roll_n_all]
```
- took out `cb`, `cb_exp`, all bucketing, `surplus_expT` columns, and swapped `cb_exp` to be non-bucketed. The exp crops were bucketed too, to be clear.
- Taking out all bucketing hurt both CLF and REG targets, REG a reasonable amount.
- corn_b*_exp features seem potentially harmful in REG, negative perm score when added up across features, dropping them doesn't affect headline score, gains some `between_r2` score, same rmse as baseline.
- pct_corn_b* features good for CLF, mixed for REG: for REG it improves `between_r2`, `macro_r2`, `within_r2`

**CLF**
| recipe_name | prauc | auc | br2 | f2 | fdr_at_f2 |
|---|---|---|---|---|---|
| recipe_CLF2 (baseline) | 0.6779 | 0.8686 | 0.3737 | 0.8580 | 0.5223 |
| crop_exp_no_bucket | 0.6765 | 0.8691 | 0.3810 | 0.8760 | 0.5408 |
| no_w_bck_yes_other_bck | 0.6738 | 0.8677 | 0.3682 | 0.8727 | 0.5367 |
| no_exp_surplus | 0.6710 | 0.8667 | 0.3671 | 0.8619 | 0.5288 |
| no_exp_crops | 0.6709 | 0.8655 | 0.3550 | 0.8621 | 0.5353 |
| no_norm_crops | 0.6652 | 0.8643 | 0.3123 | 0.8735 | 0.5439 |
| no_bucket | 0.6625 | 0.8597 | 0.2898 | 0.8907 | 0.5682 |

**REG**
| recipe_name | lofor2 | rmse | between_r2 | within_r2 | macro_r2 |
|---|---|---|---|---|---|
| no_exp_crops | 0.4531 | 4.0178 | 0.4669 | 0.4101 | 0.2061 |
| recipe_REG2 (baseline) | 0.4522 | 4.0208 | 0.4383 | 0.4107 | 0.1892 |
| crop_exp_no_bucket | 0.4507 | 4.0263 | 0.4543 | 0.4035 | 0.2360 |
| no_w_bck_yes_other_bck | 0.4489 | 4.0331 | 0.4317 | 0.4075 | 0.1895 |
| no_exp_surplus | 0.4483 | 4.0354 | 0.4389 | 0.4102 | 0.2189 |
| no_norm_crops | 0.4464 | 4.0423 | 0.4604 | 0.4203 | 0.2429 |
| no_bucket | 0.4076 | 4.1815 | 0.3725 | 0.4033 | 0.2465 |

*Actions taken*
- **Delete five dead weather vars**, keeping only `fuel_moisture_1000h` for now (+0.020 REG, +0.0075 CLF, essentially all in b1/b2).
- Ship no_exp_crops as the REG recipe, keeping exp_crops in CLF but not bucketed
- **Remove `rest_of_state_nitrate_lag3` for CLF**, it seems dead
- `surplus_kgha_mean_b*` +0.052 REG (of which b1 alone is +0.041), +0.014 CLF. The single best engineered feature family. **Keeping it.** This is the static surplus feature.
- **Keeping** the tile/drainage statics, tile_frac_basin (+0.013 REG, +0.009 CLF) and tile_frac_ag (+0.010 REG).
- Re-tuning on the reduced feature set. Should rerun reg_no_exp_crops and reg_no_norm_crops on a few different seeds and average the results.
- **Switch** bucketed `crops_expT` for non-bucketed `crops_expT` in the CLF recipe.
- **Delete** the `crops_exp` value entirely for REG

*Code changes*
- `recipes.py` split into REG/CLF sections with per-task `_LIVE_WEATHER_PREFIX_*` and `_CROSS_SITE_ROLL_*`; every global read inside `_agg_features_*` is now in its `_cache_by_site` key, so editing one invalidates the memo instead of serving stale frames.
- Small-experiments harness now fits at `train.xgb_for(recipe_REG/CLF, task)` instead of `FAST_XGB`, which was 800 trees at `reg_lambda=5` — c≈0.0009, i.e. no regularization. Every record predating this is incomparable, so `_record` logs the effective config.
- Added exps 33/33c, 34/34c, 35/35c: three encodings of the reach-graph neighbour feature (both directions / direction flag / signed distance), plus a 36/36c scaffold for upstream-definition variants. Six bugs fixed on the way, incl. a NaN-truthiness `or` chain that silently killed the `flow_dist_m`→`euc_dist_m` fallback on all 7 unroutable edges.
- Feature-manifest: `full_feature_set` was missing `surplus_expT` (both tasks) and `crops_whole` (CLF), so drop-one could not test two blocks the shipped recipe carries. Now a superset of shipped, with each unit tagged `shipped`/`n_cols`.
- Manifest cost down 234 → 136 CV fits and 13 → 4-5 aggregations/site, via an `--encodings` gate on the bake-off arms (59% of candidates, none of which can enter `full`) and grouping the 23 unmeasurable single-column base drops into 6 blocks.
- Two XGB regimes for the manifest (`_SCREEN_XGB_*` for add-one arms, `_FULL_XGB_*` for drop-one); `python fulltune.py` now produces all four blocks in one invocation. **Both are still untuned copies of REG's numbers — CLF's `reg_lambda` is c≈5.6 rather than 1.0.**
- `fulltune.py` and `tune.main` both bound `tune()` positionally against a signature where `loso` preceded `true_lofo`, so every fulltune run silently used true-LOFO folds while `--true_lofo` toggled the LOSO pass. Fixed; `--append` also no longer lets a stale CSV row become the stage winner.
- `render_results.py` 1020 → 346 lines, leading with a shipped-gap section (omitted-but-useful / shipped-but-inert, judged against the measured noise floors). CLF drop-one was never sorted at all — `lofo_prauc` vanished in the metric rework, so every delta was NaN and the `Rank` column reported log order; `surplus_norm` at −0.0076 was ranked 33rd.

*Shipped tuner (`src/models/tune.py` + `fulltune.py`)*
- `tuning_logs.json` → `.jsonl`, one appended line per config. As a JSON array it read-inserted-rewrote the whole file per config — O(n²), 80 KB at 195 entries — and, worse, it was **lossy under concurrency**: two fulltune runs racing on read-modify-write took the log from 195 entries to 1. An appended sub-4KB line is atomic on POSIX, so concurrent sweeps interleave instead of clobbering.
- Grid rows now carry `ceiling` and `ceiling_bound`. Under `--append` the CSV accumulates stages *and* ceiling retries, so without them a config that chose 400 trees was indistinguishable from one cut off at 400 — recoverable only as `best_k/k_frac`, and `k_frac` is rounded.
- Ceiling-retry trigger 0.8 → **0.9**, and the retry now grows the ceiling **×1.5 floored to a multiple of `_COARSE`** rather than doubling. Backstop cost drops ~511× → ~75×; the alignment matters because the scan grid is `range(50, ceiling+1, 50)` and an unaligned ceiling leaves `at_ceiling` reporting a shorter prefix than its name claims.
- Retries reuse the stage's own `append` instead of forcing `True`, so stage 1's retry overwrites its own ceiling-bound attempt rather than leaving a truncated measurement sorted above the real winner. `_BIND` moved into `tune.py` so the CSV flag and the retry trigger read one constant.
- Everything after `sites` in `tune()` is keyword-only. Thirteen positional args was correct only by convention — it is exactly the shape that silently misbound `true_lofo`→`loso` in the feature-manifest copy.

# [2026-07-28]

Everything optimized. Tuning redone, actually useful and good now. Should make drop-one feature irrelevant in the feature-manifest, a well-tuned XGBoost ought to ignore columns which don't contribute positive signal. Next step is to run a fulltune.py on the full model, fit a full model, and then use the perm/gain scores to ammend columns. Additionally the gain/perm scores on the fulltuned wide_recipe in the feature-manifest should be enlightening.

Old repo mostly finished, old widget converted to static website, "light" models tuned and deployed, README updated. Old writeups need to be updated, that's all that's left though.

# [2026-07-22]

Had Claude make research report `notes/new-site-report.md`, report on potential for adding new sites.

Another model which could be tried: train on basins. In each basin, choose a site, include the nitrate records of all other sites in that basin as features. For training you move around the basin taking different sites as the target.

Then when you drop a pin, you can utilize the sensor feeds of all other sites connected to that pin's basin. Do this in an adhoc way without making an explicit graph structure to keep it simple.

Next projects:

1. Migrate away from current weather data. Use something national. GridMET works I think, using it for the other weather data already anyways.
2. Expand bounding box to all of united states, or at first to a couple other corn states.
3. Add more sensors, start with the USGS network.

Would like to test idea of a model which incorporates live sensor data across the US (things like nitrate con, flow, water height, discharge, chlorophyl content, oxygen content, pH, etc) and predicts danger level/concentration of nitrate at virtual points. This would be a huge project expansion. Need to run test to see if GNN is worth it or if XGBoost with engineered features can already handle it.

*Finding:* Contained in `experiments/pre-expansion-network-test/`. Only nearest neighbors matter, once you move two nodes over in the graph the correlation between sites completely disappears. The nearest neighbor correlation is strong though. XGBoost with engineered features should be enough.

*Aggregate over whatever set of monitored 1-hop neighbors exists into a fixed set of scalars that are always computable:*

- `nearest_upstream_monitor_anomaly` — the single most-connected neighbor's nitrate anomaly
- `wmean_upstream_monitor_anomaly` — a similarity-weighted mean over all 1-hop monitors (weight by drainage-area overlap / along-network distance — this is exactly the "similarity-weighted donor transfer" the report's R12 described)
- `n_upstream_monitorsr` — the count (0, 1, 2, …)
- `dist_to_nearest_monitor` and frac_basin_area_monitored — how much of the upstream basin is actually observed

Might be worth to have two models, one which uses this neighbor set, another with 0 neighbor features. The other option is to calculate the expected frequency of neighbors in deployment and then randomly mask rows in training to match that frequency. The experiment to run is then "randomly sample valid pins and see how often they have real-sensor neighbors".

# [2026-07-21]

Reworked `_make_basins.py` extensively so that everything comes with a COMID. Turns out the NHDPlus basins are the way to go especially if we will incorporate network effects in the future. Discovered some sites had bad basins, they are now fixed. Discovered one site was a groundwater site with no comparative surface basin, that site was removed (a pity because it was a great site). `Added new-usgs-features.ipynb` notebook to investigate potentially new USGS covariates/sensors to add in an eventual network update, also in there are cells looking around for other nitrate sensors in the US. Discovered Minnesota has its own sensor network, doesn't look good for any other nitrate sensors though. I think lack of data is a problem here. Experiment design was revamped.

Ended by incorporating the new features and running tests. The new static features yielded massive improvements on a medium run ~20 sites for the CLF target, but did basically nothing on the full run on 79 sites. It did improve the REG target noticeablly though, particularly the between_r2 score which measures the model's ability to order sites from least to most nitrate.

Here is a more detailed summary written by Claude for historical purposes.

## Vocabulary (NHDPlus)

- **Reach** — one stream segment between confluences (an NHD flowline), identified by a **COMID**.
- **Catchment** — the *local* land area draining directly into a single reach. Catchments **tile**: they tessellate the landscape 1:1 with reaches, no overlaps, no nesting (the `CAT_` attributes in COMID features).
- **Basin** — the *full upstream* drainage area of a point: the accumulated union of every catchment above it. Basins **nest**: a downstream basin contains all its tributaries' basins (`TOT_` attrs).
- One **COMID** ties them together — a reach + its 1:1 catchment — and is the join key for both the local (`CAT_`) and upstream-accumulated (`TOT_`) attribute tables. **Catchments tile; basins nest.**

## Basin delineation (`_make_basins.py`)

Reworked to three tiers, **every one now carrying a real COMID** (the join key the new static attributes need — this resolved the old "46/85 sites lack a COMID" blocker):

- **basin0** — NWIS authoritative, by site uid. USGS sites only.
- **basin1** — **nearest-flowline snap**: find the closest NHD reach to the sensor, take that COMID's
  upstream basin. Now the **default**.
- **basin2** — containing catchment (`/comid/position`). Demoted to fallback/diagnostic only.

*Why the snap.* `/comid/position` returns the catchment polygon *containing* the point, which near confluences grabs the wrong reach — e.g. an order-2 tributary 510 m away instead of the mainstem 4 m away. Snapping to the nearest flowline fixes that and stamps a correct COMID on every basin. Deploy pins use pure-nearest (no reported area to match against).

## Groundwater discovery

A few "sites" turned out to be groundwater/karst, not surface water: a USGS well (`USGS-415959091441301`, site_type = Well) and IWQIS springs (e.g. Big Spring `WQS0031`). They have **no surface drainage basin**, so they can't be delineated or featured like a stream site. They're now skipped at fetch (`usgs.py`) and filtered out via an `is_groundwater` flag — no wasted pulls, no nonsensical basins.

## New covariates (static, per-basin — the between-site-gap lever)

Joined by the basin's outlet COMID and folded into `SiteData` (`comid_attrs`, `agtile`) by
`access.get_data` / `build_virtual_site_data`, then read by `features._site_static`:

- **COMID-keyed** (ScienceBase): `tot_tiles92` (tile-drainage %), `tot_bfi` (base-flow index %),
  `tot_contact` (subsurface contact time, days). `CAT_` variants carried for diagnostics.
- **AgTile-US** (30 m tile-drainage raster) → `tile_frac_basin` (tile area / basin area) and
  `tile_frac_ag` (tile / CDL-2017 cultivated cropland).

*AgTile caveats found today:* the raster is plain binary (1 = tile-drained), with **no** cropland
mask — so the ag denominator comes from CDL, not the raster. Also fixed a CRS/degenerate-edge-cell
bug in `_make_agtile` that had corrupted coverage (2,502 → 19,043 cells).

## Experiments pipeline (`run_exp.py`)

New single-log experiment engine. An experiment = a **QUESTION** + a dict of named recipes compared
on the same sites/folds; results append to one git-tracked `logs/experiments.jsonl` (rendered to
`experiments.md`), formatted **Question → Winner → Results**.

- Declare saved experiments with `@experiment(id, question, task)` in `exps.py` (a registry populated
  at import). Each run is a **single task** — regression OR classification, never both.
- Added `run_experiment(question, recipes, task, ...)` for **one-off** experiments straight from a
  notebook — no decorator, no registry, no CLI. Always logs + renders, returns the metrics frame.

# [2026-07-20]

Migrated from the old Erdos git repo to this new one. Ran a rather expensive Claude research experiment to investigate new features. It did turn up some useful stuff, it looks like more static covariates can be derived for free from the basin data using stuff keyed to "COMID"s, including tile drainage attributes.