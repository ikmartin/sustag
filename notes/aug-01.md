# State of the project — 2026-08-01

Consolidates 14 prior notes. Forward-looking only: superseded analysis has been discarded rather than summarised.

# 1. Where the project stands

**Goal.** Drop a pin anywhere in the river network, get a **daily** nitrate prediction. Two variants: `island` (works anywhere, no upstream gauge needed) and `network` (adds nearest-donor features, needs a resolvable neighbour). Primary KPI is the violation flag (≥10 mg/L, CLF); secondary is concentration (REG).

**Every score in the project is currently invalid.** A D8 pour-point snapping bug put 28 of 116 sites' outlets on the wrong river, corrupting `dist_to_sensor` — a base feature in *every* recipe — and `flow_dist_m`, which drives donor selection. Fixed 2026-07-31; grids and the containment graph are rebuilt. Nothing computed before that date can be compared to anything computed after it.

**Where we sit against the literature.** Daily nutrient concentration at a genuinely held-out site has been attempted **exactly twice** and works poorly both times: Pandit 2025 (median KGE **0.18** CONUS, **0.34** Mississippi) and Xia 2025 (nutrients **< 0.45**, while temperature reaches 0.89 and DO 0.72 on the same split). Everything else either predicts at ungauged locations at seasonal/annual resolution (SPARROW, Tso) or predicts daily with every site in training (Saha, Jain).

**No tree-based model has ever been evaluated in that regime.** And most of the field assumes a discharge gauge at the prediction location — Xia, Jain, Gorski and SPARROW all require one. Our feature set contains no discharge at all.

Our current position: median per-site NSE **0.264** (REG full recipe). The number to beat is **0.34 median KGE**, which we cannot yet compute.

---

# 2. What we know

## The cohort is not what it appears to be

**11% of sites are not stream gauges.** Five are deliberate **inflow/outflow pairs around engineered nitrate-removal features** — a CREP wetland, a wetland outlet, a pond, a nutrient-trading study, a treated ditch — showing 47–88% reduction across the structure. Four more are tile outlets whose delineated "basin" is a field, not a catchment. Four are engineered ditches. No watershed covariate can express any of this, so these sites carry irreducible error while being trained on as ordinary gauges.

The metadata to detect this (`iwqis_river`, `iwqis_description`, `iwqis_nickname`) is already fetched and read by nothing.

**Three site pairs are one gauge registered twice** — sequential deployments under 52 m apart with zero overlapping days. They are exactly the 3 two-cycles that make the containment graph **not a DAG**. Merging them lengthens three records substantially (`USGS-05451210` covers 2010–11, `WQS0024` covers 2015–25) and dissolves all three cycles.

**Do not deduplicate by distance.** Eight other pairs sit 200 m–1.9 km apart, are *concurrent*, and correlate at r = 0.006–0.589 — they are the treatment pairs above, genuinely distinct sensors.

**The cohort oscillates 116↔123** depending on which build step ran last. Fixed on the filter side; the producer side (`_make_basins` building from the contract rather than the candidate universe) remains.

## Two mechanisms that shape everything

**Nitrate mobilises, it does not dilute.** Measured C–Q exponent **b = +0.61** on this project's own gauged sites. This kills any `C = load/Q` denominator, any load target, and any load-duration framing. It also means the dilution intuition has the wrong sign — hold priors low on discharge as a covariate.

**Tile drainage makes nonpoint agricultural pollution behave exactly like a point source.** Baseflow carries ~two-thirds of Iowa's annual nitrate export, rising above 80% in spring and late fall. This is why the "dry-weather nitrate floor" test fails and why C–Q slope sign does not discriminate source type.

## Where the model's skill actually comes from

Roughly half the permutation importance sits in **between-blind** features — `rest_of_state_nitrate_lag*` is near-constant across sites on a given date, `doy_*` is identical by construction. So `between_r2` rests entirely on the other half, and every remaining lever there is a re-slice of the same two rasters (CDL, surplus).

The only change ever measured to clear ~1 SD on that metric was raw crop counts → normalised shares (CLF `between_rate_r2` 0.2375 → 0.4069). Anything that merely rearranges existing information sits inside the noise floor.

---

# 3. Next steps

Tasks are written to be delegable: what, why, where, and done-when.

## Tier 1 — blocking. Nothing else means anything until these land.

### T1.1 Rescore everything post-D8-fix
**Why:** no score in the project is valid; exps 33–36c cannot be compared to any new run.
**Do:** re-run the feature-manifest add-one/drop-one for both tasks, and re-run exps 33–36c. If 37/37c are run, **34/34c must be re-run in the same invocation** so the comparison is paired.
**Done when:** a single post-fix run_id carries base, full, and the network arms for both tasks.

### T1.2 Fix the metric set
**Why:** our headline pooled `lofo_r2` (0.479) is roughly double the median-per-site `lofo_macro_r2` (0.264), and the latter is what the field calls NSE. **KGE is absent entirely**, and it is the only unit in which the prior art can be met.
**Do:** add `kge` and its components (`r`, `α`, `β`), `macro_kge`, `pbias`, `log_nse`, and `frac_nse_above{0, 0.3, 0.5}`. Report median-across-sites beside every pooled value. Do **not** rename `lofo_r2` or `lofo_prauc` — they are load-bearing names selected by `run_exp.py` and `render_results.py`. See `industry_kpis.md` for definitions and the benchmark table.
**Done when:** `macro_kge` appears in the score set and can be quoted against Pandit's 0.34.

### T1.2b Change what XGBoost optimizes, not just what we report
**Why:** T1.2 fixes the *reported* metric. It does not touch the three places that **select** a model, all of which currently optimize a pooled row-wise quantity that is not the deployment objective:

- the training objective — `reg:squarederror`, summed over rows, so a site with 4,000 rows counts 40× a site with 100, and between-site variance sits in the denominator of every improvement;
- tree-count resolution — the prefix scan reads pooled RMSE;
- the tuner's own selection rule — pooled `lofo_r2` / `lofo_prauc`.

Reporting `macro_kge` while optimizing pooled MSE is not merely inconsistent, it is **self-defeating in a specific and predictable way**: the MSE-optimal prediction is shrunk toward the conditional mean, so $\alpha = \sigma_p/\sigma_y < 1$ systematically — and KGE penalises $\alpha$ directly. We would be training for the exact failure the headline metric punishes. This is a known result in hydrology (NSE/MSE-calibrated models under-disperse) and it is why the field moved to KGE in the first place.

**Do:** three changes, in order of cost.

1. **Row weights `1/n_rows(site)`** so every site contributes equally to the loss. This is one argument and it converts pooled MSE into an unbiased estimate of the macro quantity. Do this first — it is nearly free and it is most of the effect.
2. **Select on the macro quantity** — the prefix scan and the tuner both switch from `lofo_r2`/`lofo_prauc` to `macro_nse` (and a per-site macro PR-AUC for CLF, which `industry_kpis.md` notes does not exist yet and must be written).
3. **Consider a KGE-aligned objective** — either a custom objective, or the cheaper route of a post-hoc variance-inflation calibration fitted out-of-fold. Measure before building: if row weighting alone recovers most of $\alpha$, skip it.

**The trap:** do **not** reach for per-site target standardisation to close the gap. The site mean is unknown at an ungauged pin, so a model trained on site anomalies has nothing to add the mean back onto at deployment. It would score beautifully under LOFO and be unusable.

**Done when:** the quantity the tuner maximises, the quantity the prefix scan stops on, and the quantity we quote externally are the same quantity.

**Ordering:** strictly after T1.2 — nothing can select on `macro_kge` before it is computed. And note the consequence for T3.2: **every config tuned before this lands is optimal for a metric we are retiring**, including the neighbour-count configs being tuned now. Those stay usable for *ranking arms against each other* — that is a paired comparison under a shared config and the shared config need not be the final one — but they are not the configs to ship.

### T1.3 Record site type
**Why:** 11% of the cohort are wetland/pond outflows, tile drains or treated ditches. Nothing records this, and diagnostics currently attribute their structural error to feature failure.
**Do:** classify from `iwqis_river`/`iwqis_description`/`iwqis_nickname` into the cohort manifest as `is_treatment_outflow`, `is_tile_outlet`, `is_ditch`. Cross-check against NHD FCode 43624 ("Reservoir: Treatment", 120 in Iowa) and the National Inventory of Dams.
**Done when:** every site carries a type, and per-type performance can be reported.
**Free test that follows immediately:** join the classification against `eval_<task>.csv` from the neighbour-count experiment — these 13 sites should be among the worst-predicted. No new fits.

### T1.4 Fix early stopping on the shipped path
**Why:** `src/eval/cook.py` — the path `train.py`, `run_exp.py` and `demo_model.py` all fit through — **still carries `early_stopping_rounds=50` and an unconditional row-wise 85/15 carve.** The fix landed only in the screening path. Measured cost on one LOFO fold: out-of-family R² peaked at tree **175** while the watched in-distribution RMSE was still falling at tree 1498 of 1500 — **0.0324 R² given up**, ~4× the REG noise floor, at ~8.5× the compute.
**Do:** remove the unconditional carve; resolve tree count by full prefix scan (the out-of-family curve is **not unimodal** — it falls to +0.300 at k=475 and recovers to +0.317 at k=1375, so bisection is wrong).
**Done when:** every shipped model is stopped on the out-of-family objective and trained on 100% of its fold's rows.

### T1.5 Promote the in-situ covariates already on disk
**Why:** `temp_water` (66% of cohort), turbidity (49%), `spec_cond` (45%), DO (41%) and `ph` (40%) are sitting in `raw/water/` and `make_water` throws them away — it writes only `nitrate_con` and `site_uid`. One column-selection change.
**Do:** carry them through under a name that marks them **diagnostic-only**, since none exists at an arbitrary pin.
**Done when:** they are in `processed/water`, and the turbidity MNAR label audit (currently impossible) can run.

## Tier 2 — cheap, decision-relevant

### T2.1 Add the Crumpton removal feature
**Why:** the only thing that addresses a failure mode no current feature can express.
**Do:** download NID (`https://nid.sec.usace.army.mil/api/nation/csv`, 67 MB, 4,128 Iowa dams with **both** pool area and drainage area). Compute per structure:
$$\text{removal} = 1.03\,\mathrm{HLR}^{-0.33},\qquad \mathrm{HLR} = \frac{\text{water yield}}{\text{pool area}/\text{watershed area}}$$
Aggregate `Σ (treated_area_fraction × removal)` upstream of each site. Separately, weight the treated sub-basin's covariates by `(1 − removal)` rather than counting them at full strength.
**Done when:** the feature exists and the five inflow/outflow pairs are checked for improved residuals.

### T2.2 Pull StreamCat
**Why:** annual N inputs **1987–2017**, COMID-keyed — our first time-varying nitrogen covariate, including a legacy-accumulation term.
**Do:** POST by COMID to `https://api.epa.gov/StreamCat/streams/metrics` (~62 calls, 2 minutes, 97% site coverage). Take `n_ff`, `n_lw`, `n_lwr`, `n_ags`, `n_dep`, `n_leg`, `n_tin`, plus static `nsurp`, `npdesdens`, `wwtp*dens`. **Normalise every `ws` value by `CumAreaKm2`** — they are raw upstream sums in kg and otherwise just encode basin size.
**Done when:** the block is in `comid_attrs.parquet` and ablated against base.

### T2.3 Distance-stratified LOFO reporting
**Why:** the only thing that bounds the deployment case. A random pin sits a median **116 km** from the nearest sensor while LOFO tests at far shorter range.
**Do:** bin OOF predictions by each held-out site's distance to its nearest *training* site. Predictions and coordinates already exist.
**Note:** the previously quoted 34 km / 82 km gap figures are **wrong** — they assume leave-one-family-out, but we use `GroupKFold(5)`, which holds out ~a fifth of families at once. Recompute against the real fold construction.

### T2.4 Run the `nbr_all_no_lag0` arm (gate G2)
**Why:** it is the only donor column set a recipe could actually ship, and it has never been run. Shipping a causal-lag-only recipe rests entirely on it.

### T2.5 Decide forecast vs nowcast (gate G3)
**Why:** a product decision nobody owns. It determines whether `lag0` is legal at all and whether zero-latency donor telemetry is an assumption we are making. **Note the new evidence:** for an inflow/outflow pair a few hundred metres apart, travel time is *minutes* — lag 1 is already a day stale, so lag 0 is the only lag carrying the treatment signal. Saha's dominant predictor is the *contemporaneous* neighbour mean.

### T2.6 Run exps 37/37c
Plan is current and re-verified against the post-fix graph — see `plan_exp37_basin_pool.md`. **Amend for two things it predates:** the 3 two-cycles from duplicate gauges (its `_component_of` walk and "88% have a pool-mate" arithmetic), and the 11% non-stream sites (pooling a treated-ditch outflow into `rest_of_basin_nitrate_avg` is exactly its "interpretation trap"). `base_stream` vs `base` is the cheapest island-legal win available.

### T2.7 Surplus fill-strategy ablation
**Why:** surplus is NaN on **69% of rows**, yet `surplus_norm_whole` was the rank-1 CLF add-one. The prior −0.166 forward-fill verdict was measured on `agg_surplus(exp=True)`, the aggregation since identified as the bad one, so it is **not binding**.
**Do:** ablate forward-fill / uniformly-static rank / status-quo NaN, **on `surplus_norm`**.

### T2.8 E2 — discrete-sample basin aggregate
**Why:** possibly **island-legal**, which would make it the only candidate block that improves the no-donor fallback path. Agency-only basin aggregate correlates ρ = +0.53 with each site's long-run sensor level over 66 basins — but that is *in isolation*, not against what crops/surplus/tile already explain.
**Do:** paired `base` arm, agency data only. **Read the `grab_coverage` (stations + samples only) arm first** — station density tracks population and road access, so if coverage alone carries the effect it is a sampling-effort fingerprint. Exclude stations within a co-location radius of the cohort sensor.
**Unit trap:** WQP returns the same USGS measurements twice, as `mg/l as N` and `mg/l asNO3`, differing by 4.4269. Deduplicate on `(MonitoringLocationIdentifier, ActivityStartDate, value-as-N)` or median nitrate reads 24.05 instead of 5.44.
**Exclude the volunteer programme:** volunteer basin means run 3.5–4.1 mg/L below agency means and **anti-correlate at −0.42** once both sides have ≥50 samples.

### T2.9 E1 — donor discharge, as a skill-vs-radius curve
**Why:** 36 IWQIS nitrate sites sit within 200 m of a USGS gauge, so a naive "nearest discharge gauge" feature reads the target's own discharge — and **LOFO will not catch it, because LOFO holds out sites, not instrument availability.**
**Do:** sweep the exclusion radius including an `r = 0` arm to bound the co-location skew. Coverage is 100% of the cohort at every radius up to 100 km, so the radius is a realism choice. **The radius at which `q_lagged` stops clearing the noise floor is the maximum pin-to-gauge distance worth shipping.**

## Tier 3 — larger, each gated on a number nobody has

### T3.1 What fraction of legal map pins has a resolvable donor?
**The load-bearing unmeasured number.** Everything about the island/network split rests on a 12% no-donor figure measured on *gauged* sites — and gauges sit where someone chose to instrument, so it almost certainly understates the rate for arbitrary pins. This decides whether `network` is the primary model or the exception.
**Sequence:** expand bbox → rebuild → snap random pins to stream order ≥ 4 → run statistics over legal pin locations.

### T3.2 Retune all four models
Both `network_*` models are untuned by design (`_tuned_iters` raises `UntunedRecipe`). **Widen the ladders first** — the last REG fulltune settled at the *top of both depth and lam*, so the search was truncated, not converged. Port `--automatic-expansion` from the feature-manifest tuner to `src/models/tune.py`.

**Gated on T1.2b.** Retuning before the selection rule changes buys configs optimised for a metric we are retiring, at full tuning cost. This is the single most expensive way to get the ordering wrong.

### T3.3 The deploy seam
None of it exists: no model selection per task×variant (`deploy/predict.py:22-23` and `widget/model_interface.py:47-48` hardcode one model per task); no per-pin neighbour resolution (`widget/geo_utils.get_downstream_sites` is a stub returning `[]`); and `feature_mismatch` is defined at `predict.py:64` and **called nowhere**, so `predict`'s `reindex` silently NaN-fills missing columns — harmless with one feature set, dangerous with two.

### T3.4 Long-run crop composition
The only remaining candidate that adds *information* rather than rearranging CDL and surplus: `pct_crop_sd` and `rotation_index` are functions of the raster's time series, which no current feature touches. Continuous corn and a corn/soy rotation load very differently at the same mean corn share.
**Protocol that must not be skipped:** ablate on full recipes not light; test mean-block and sd+rotation-block separately then together (mean-only winning is the *suspicious* outcome, being near-collinear with per-year share); **hold the site set fixed across arms** — two sites are worth 0.036 of `between_r2`.
**If it comes back flat**, the next move is not another CDL/surplus derivative but **tile density and soil permeability from SSURGO**.

### T3.5 Site expansion
11 in-footprint USGS candidates, zero new pipelines — priority the two 11.1-yr Illinois sites `USGS-05446500` / `USGS-05447500`. Realistic yield 4–8 sites, 3–6 new families.
**Fix the pcode discovery set first:** a `99133`-only pull silently drops entire USGS networks — **Nebraska's Platte/Elkhorn sensors are under `00630` as IV series and a Nebraska 99133 query returns 0 sites.** Query `99133 + 99137 + 00630 (hasDataTypeCd=iv)`; drop `83554` (a load, not a concentration).
**The real expansion gate is the weather grid**, not nitrate availability — the IEM 4 km Voronoi grid is Iowa-specific, and a national grid means re-basing on CONUS gridMET. **Ship every expansion tier with an Iowa-only LOFO baseline** so a pooling regression is detectable; pooling hurt in Gorski 2024.

### T3.6 Test a sequence model properly, if at all
An LSTM with static-attribute conditioning is the only architecture with a published win in our regime (Kratzert: median NSE 0.69 on 531 held-out basins, beating per-basin-calibrated SAC-SMA at 0.64). Run it **head-to-head against tuned XGBoost on identical pool, folds and metrics**. Expect a tie — and note a tie is still publishable, because nobody has run this comparison in the held-out-site regime for nutrients.
**Do not** build a GNN on the current graph: it is transitively closed, so multi-hop message passing has nothing to do. See `modeling-proposals-report.md`.

---

# 4. Facts that must not be rediscovered

## Ingest traps

- **`CAT_TILES_Early90s` writes tile value `0.0` where `CAT_NODATA == 100`** — true for ~88% of CONUS. Never use it.
- **`-9999` is overloaded**: "no coverage" in NODATA columns, "disconnected from routing" in `ACC_`/`TOT_` value columns.
- **The FGDC XML labels `ACC_TILES92` twice where the CSV header says `TOT_TILES92`.** Parse the header.
- **`mw_sparrow_model_input.txt` carries measured loads (`flux_600_final` etc.) present only at gauged reaches.** Ingesting all 89 columns injects observed N load at exactly our monitored sites. **Use an explicit whitelist.** Related: 20 of our 116 sites sit within 100 m of a SPARROW TN calibration station.
- **SPARROW log-columns use sentinels** — `ldrainden = log(1e-5)`; `ltiles13_perc2` maps zeros to 0.0, colliding with ln(1%). Also 18 negative synthetic COMIDs and rows with `IncAreaKm2 == 0`.
- **ScienceBase does not honour HTTP Range** — a range request pulls the whole 493 MB — and its parquet copies are broken.
- **AgTile's mask is NLCD-2016 and must never be denominated against CDL crop cells** (−18.15% on CONUS cropland).
- **WQP unit duplication**: same measurement twice as `mg/l as N` and `mg/l asNO3`, factor 4.4269.

## Do not re-propose — measured and failed

Lagged-weather / advective travel-time routing (`loso_r2` 0.116 → 0.019) · rolling precipitation water balance · **broadcasting frozen annual surplus per-day (−0.166)** · rolling 3D/7D smoothed targets · spike/anomaly z-score targets (`lofo_r2` ≈ −0.0006) · antecedent-moisture × surplus interactions · riparian 0–2 km bucketing (CLF +0.039 PR-AUC but REG worse).

**The rule that produced all seven:** a static acquisition enters as a time-invariant per-basin column, **never broadcast per-day**. Corollary: add the basin-integrated scalar first; bucket into distance rings only if the scalar wins.

Also dead: **per-site C–Q slope as a feature** (two events representing <1% of annual load moved a slope 0.09 → 0.71) · forecasting post-2017 surplus (every trend method loses to persistence; a *perfect* level forecast leaves Spearman identical to four decimals) · isotopes (field campaign, and denitrification pushes fertiliser signatures into the manure window) · spec_cond/DO/pH/turbidity as *donor* covariates (12–16% coverage within 10 km, median separation 52–56 km).

## Latent bugs found but deliberately not fixed

- **`_make_map_overlays.download_flowlines` filters on `"streamorde"` but pynhd returns `StreamOrde`**, so `min_stream_order = 3` has **never fired**. The saved layer holds 309,682 features spanning orders 0–9 including 153,303 order-1 reaches, so `snap_comid` has been snapping against order-1 ditches. **Every basin on disk was delineated under a different rule than the code, config and docstring all claim.** Do not fix before exp 37 — repairing it changes snapping, hence basins, hence everything.
- **`_weighted_group_agg` is valid only for reductions LINEAR in the values.** Tagging a median/max/quantile with `.agg_kind` would not raise — it would silently return wrong numbers. There is no test guarding this.
- `notebooks/demo_model.py:23` imports `_grouped_models` from `src.eval.cook`; it is now `_fold_models`.

## Useful negatives

- **`StreamOrde` is ρ = 0.940 with `log_basin_area`** — nearly a monotone recoding of a column we already have. **`StreamLeve` is only −0.53** and encodes mainstem-vs-tributary position, which the recipe genuinely lacks. Both resolve 116/116 with no NaN and are island-legal.
- **Tile density and cropped area are entangled at R² = 0.95** — they cannot be cleanly separated even as covariates.
- **Nothing replaces gTREND** for nitrogen surplus past 2017; every route traces to the five-yearly USDA Census. IWAND is additionally **CC BY-NC-ND**, which blocks it as a modelling input. **Sekellick & Sherr (DOI 10.5066/P9WDBIXC)** is the better join key — COMID-native, and it **separates manure from fertiliser** (Iowa 2017 cross-county CV 1.029 manure vs 0.404 fertiliser, Spearman between them only 0.611).
- Models trained on monitored sites are **overconfident at unmonitored ones** — 90% intervals covered ~60%. Treat `n_monitored_upstream == 0` as a **UI guardrail** (widen the band), not a feature.

---

# 5. What this file replaces

**Delete (nothing left to extract):** `cv-harness-optimization-report.md` · `new-features.md` · `gemini-new-features.md`

**Delete (extracted above):** `future-work-report.md` · `model-split-report.md` · `future-cv-work-report.md` · `pooling-performance-report.md` · `early-stopping-report.md` · `surplus-replacement-report.md` · `point-pollution-report.md` · `discrete-sample-report.md` · `extra-in-situ-report.md` · `new-site-report.md` · `plan_long_run_composition.md`

**Keep:** `plan_exp37_basin_pool.md` (current, unrun, re-verified against the post-fix graph — annotate for the two-cycles and the non-stream sites) · `audit-of-data-pipeline-0731.md` · `competing-research-report.md` · `model-improvement-research-report.md` · `new-data-report.md` · `modeling-proposals-report.md` · `industry_kpis.md` · `D8_bug_fix_explained.md`
