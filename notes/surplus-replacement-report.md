# Replacing gTREND: is there a nitrogen-surplus dataset that runs to the present?

**Question.** The surplus layer (gTREND-Nitrogen, 250 m, annual) ends in 2017 while the site fleet now runs to 2026. What can replace or extend it?

**Short answer: nothing, and the reason is structural.** Every candidate traces back to the USDA Census of Agriculture, which runs on a five-year cycle. 2017 was the last census folded into any published gridded product. Every dataset that *appears* to extend past 2017 does so by holding the 2017 value constant or linearly extrapolating it — including the 2026 community reference dataset built specifically for this modelling problem. The genuinely new information exists (the 2022 Census was released in February 2024) but nobody has published the gridded/catchment product yet.

Compiled 2026-07-27.

> **Provenance.** The measurements here were produced by throwaway scripts in a scratch experiment directory that has since been deleted, so the numbers are not re-runnable as written. Three pieces are worth knowing if any of this needs reproducing: the cutoff cost (§1) is a groupby over the per-site raw water parquets against `filtered_sites.json`; the full-history layer (§5a) was rebuilt from all 88 `Surplus_N_{year}.tif` rasters by reusing `_make_surplus.load_surplus_source` and its aggregation arithmetic, hoisting the square→cell overlay out of the year loop and computing it over the **union** of valid pixels across all years (a single reference year under-covers the others, since the finite/nodata pixel set varies by year — that error reached 0.9967 relative); and the rebuild was validated by reproducing the tracked `surplus_global.parquet` exactly on the 18 overlapping years.

---

## 1. What the cutoff currently costs

Surplus is an annual layer, and [`transformers.merge_on_date`](../src/features/transformers.py) left-joins annual frames onto the daily spine by year. Any date whose year has no surplus row becomes **NaN in every surplus feature** — there is no forward-fill. Measured over the current 127-site fleet:

| | |
|---|---|
| total nitrate site-days | **189,868** |
| with surplus (≤ 2017) | 58,954 — **31.0%** |
| **surplus = NaN** | **130,914 — 69.0%** |
| sites >50% uncovered | 95 of 127 |
| **sites 100% uncovered** | **35** — no surplus signal at any row |

IWQIS sites are 71.4% uncovered, USGS 64.5%. This confirms and quantifies the suspicion recorded in [`findings.md`](../experiments/feature-manifest/findings.md) — *"surplus_norm — not as useful, probably nerfed by 2017 cutoff."*

Worth noting what this implies: in the latest feature-manifest run, `surplus_norm_whole` was the **rank-1 add-one** for CLF (+0.0123 lofo_auc, +0.042 between_rate_r2) and `surplus_norm` rank 4 (+0.0086, +0.068 between_rate_r2) — **while contributing nothing on 69% of rows.** Whatever fixes the coverage is operating on a feature that already earns its place on a third of the data.

---

## 2. Candidates, and why they all stop at 2017

| Dataset | Unit | Years | Genuinely new post-2017? | Verdict |
|---|---|---|---|---|
| **gTREND-Nitrogen** (current) | 250 m grid | 1930–2017 | — | in use |
| **IWAND-Nitrogen** (2026) | 250 m grid + basin means | 1980–**2023** | **No** — forcings *are* gTREND, held constant 2017→2023 | no new N info; see §4 |
| **Dynamic SPARROW CONUS** (2026) | NHDPlusV2 reach, **seasonal** | 2000–**2020** | **No** — linearly interpolated/extrapolated from Sekellick & Sherr | no new N info; useful for other reasons (§3) |
| **Sekellick & Sherr** (2024) | **NHDPlusV2 catchment (COMID)** | 2002, 2007, 2012, 2017 | No | best *join key*, same cap (§3) |
| **Falcone / USGS OFR 2020-1153** (2021) | county | 1950–2017, census years | No | the upstream source of most of the above |
| **EPA / AAPFCO fertilizer purchased** | **state** | 2002–2011, 2016, 2017 | No | too coarse regardless |
| **FAOSTAT cropland nutrient budget** | **country** | 1961–**2023** | Yes, but country-level | useless at basin scale |
| **USDA 2022 Census of Agriculture** | county | 2022 (released 2024-02-13) | **Yes** | the only real new information (§5) |

**The lineage.** gTREND downscales county-scale TREND-Nitrogen to 250 m. Falcone's county estimates are modelled from census-year fertilizer expenditures and land use. Sekellick & Sherr downscale county estimates to NHDPlusV2 catchments for the same four census years. Dynamic SPARROW takes Sekellick & Sherr and interpolates. IWAND takes gTREND and holds it constant. **One census cycle underlies all of them**, so "a different dataset" mostly buys a different join key, not fresher numbers.

The two decisive quotations, both from primary sources:

> "NHDPlusV2-catchment-level estimates of fertilizer (farm and non-farm) and manure (confined and unconfined) application rates from Sekellick and Sherr (2024) are annual totals and available for 2002, 2007, 2012, and 2017. **Linear interpolation and extrapolation was applied to fill and extend those annual estimates** to complete the period of record 2000 through 2020."
> — metadata, [Dynamic SPARROW CONUS data release](https://doi.org/10.5066/P9IJLEIY)

> "For both data formats, **N input forcings were assumed constant from 2017–2023**, as this period extends beyond the final year of the gTREND-Nitrogen source data."
> — [IWAND-Nitrogen](https://pmc.ncbi.nlm.nih.gov/articles/PMC13237047/), *Scientific Data* 2026

---

## 3. What the newer releases *are* good for

Neither adds post-2017 nitrogen information, but both are better-engineered than a 250 m raster for our pipeline.

**Sekellick & Sherr (2024)** — [DOI 10.5066/P9WDBIXC](https://doi.org/10.5066/P9WDBIXC), ~90 MB CSVs keyed by **COMID**: `ag_tn_fert` (agricultural fertilizer N), `ag_tn_cman` (confined manure), `ag_tn_uman` (unconfined manure), `de_tn_fert` (developed/non-farm fertilizer), plus TP twins. Our basins already carry a COMID and `access.get_comid_attributes` already exists, so this is a hash join rather than a raster aggregation — no Voronoi area-weighting, no 250 m→4 km reconciliation. It also **separates manure from fertilizer**, which the current single `surplus_kgha` column does not. That separation is exactly what future-work R5 argues for: Iowa 2017 cross-county CV is 1.029 for manure N versus 0.404 for fertilizer N, with Spearman between them of only 0.611 — 2.5× more between-site spread in manure, and the two are far from interchangeable.

**Dynamic SPARROW** — [DOI 10.5066/P9IJLEIY](https://doi.org/10.5066/P9IJLEIY), 263,494 reaches, 84 seasonal timesteps. Its `predict_tn.csv` carries per-reach per-season `fert_kg_ew`, `man_kg_ew`, `atm_kgtn`, `cropfix_km2`, `septic_kg` and `incareakm2` (the denominator for an intensity). The **seasonality is real even though the annual totals are not** — annual rates were parsed into seasons using CMAQ-modelled ammonia emissions from farm fields, so the within-year shape is new information relative to gTREND's flat annual value. Given that Iowa nitrate is strongly seasonal and the model already leans on `doy_sin`/`doy_cos`/`doy_harmonic2`, a seasonal application signal is a plausible improvement over an annual one.

Two costs to weigh: reach IDs are **GenNet `genid`, not COMID** (a crosswalk is needed, [GenNet DOI 10.5066/P13FCIN4](https://doi.org/10.5066/P13FCIN4)), and `tn.zip` is 13.5 GB. Both are also **model outputs**, not observations — future-work R3 already flags that SPARROW's headline fits are in-sample calibration statistics and must never be quoted beside a LOFO number.

---

## 4. IWAND-Nitrogen is worth reading for a different reason

[IWAND-Nitrogen](https://www.nature.com/articles/s41597-026-06873-5) (*Scientific Data*, 2026) is useless as a surplus replacement — its N forcings are literally gTREND held constant. But it is **574,767 nitrate records from 1,877 CONUS catchments (median 272 samples/gauge), 1980–2023, with 93 watershed attributes**, assembled for exactly our modelling problem. That makes it a candidate for future-work **R8** ("enlarge the donor pool… the only recommendation that attacks n≈20 directly") and for the site-level exceedance-rate track — a different use than the one asked about here.

**Licence blocker: CC BY-NC-ND 4.0.** The *NoDerivatives* clause is a genuine problem for using it as a modelling input — arguably any feature-engineered transform is a derivative. Check this before building on it; the underlying sources are mostly public-domain and could be reassembled independently.

---

## 5. The only route to genuinely new numbers

The **2022 Census of Agriculture** was released 2024-02-13 with county-level fertilizer expenditure and acres treated — precisely the predictors Falcone's method uses (county fertilizer expenditures, land use, acres of fertilized land), available through NASS Quick Stats. Livestock inventories for the manure term are there too.

So a 2022 vintage is *constructible*, and would extend real coverage from 2017 to 2022 — recovering roughly the 2018–2024 span if paired with interpolation, which is most of the current hole. But this is a from-scratch build of somebody else's published method: replicate Falcone's county model, downscale to the grid or to COMIDs by land use, and validate against the 2017 vintage where they overlap. **Realistically 2–4 weeks**, it is genuinely novel work rather than a data pull, and it would be publishable in its own right. That is the honest price of post-2017 surplus.

A cheaper partial: **annual state fertilizer tonnage as a temporal index on a fixed spatial pattern.** Iowa's IDALS collects commercial fertilizer tonnage semi-annually (filed within ten days of the end of January and July), and other states publish equivalents. Scaling the 2017 spatial field by an annual state-level N index gives real year-to-year variation at the cost of assuming the within-state spatial pattern is frozen. Cheap, defensible, and it captures the price-driven swings in application rate that a frozen field cannot — but it is a state-level signal, so it can only move whole-state levels, never between-site ranking within Iowa.

---

## 5a. Can we *forecast* post-2017 surplus from the full gTREND history?

gTREND runs back to 1930, so an obvious alternative to freezing 2017 is to fit a temporal model per cell and forecast 2018–present. **Backtested on the full record, the answer is no.**

The production layer is capped at 2000–2017 by `[surplus] year_start/year_end`, which is too short to fit a temporal model on — an early version of this test trained a 9-year slope and extrapolated 9 years, which tests the estimator more than the idea. A one-off builder therefore rebuilt the layer from all 88 `Surplus_N_{year}.tif` rasters (1930–2017) reusing the production reader and aggregation arithmetic; it reproduces the tracked `surplus_global.parquet` **exactly** on all 18 overlapping years (max|Δ| = 0, identical cell sets). Everything below is 60,970 cells over 88 years.

**Rolling-origin, every available origin, seven forecasters.** Spearman against truth — the ranking channel the between-site term consumes:

| h | n origins | persist | **mean3** | mean5 | damp0.2 | damp0.5 | trend10 | ols |
|---|---|---|---|---|---|---|---|---|
| 3 | 66 | 0.9598 | **0.9611** | 0.9593 | 0.9152 | 0.9318 | 0.9501 | 0.9401 |
| 5 | 64 | 0.9451 | **0.9489** | 0.9470 | 0.9046 | 0.9213 | 0.9254 | 0.9286 |
| 7 | 62 | 0.9361 | **0.9375** | 0.9352 | 0.8936 | 0.9107 | 0.9027 | 0.9171 |
| 9 | 60 | 0.9221 | **0.9242** | 0.9221 | 0.8818 | 0.8987 | 0.8758 | 0.9037 |

**Every trend-based method loses to persistence at every horizon**, and more history does not rescue them. `trend10` — fitting the slope on only the last 10 years, specifically to avoid dragging in the 1940–80 fertilizer ramp that has since plateaued — is the *worst* method at h=9 (0.8758). The only thing that beats persistence is a mild smoother, `mean3`, and it wins by **+0.0013 to +0.0038** — against 0.078 of available headroom at h=9 (0.9221 → 1.0), i.e. it closes about 3% of the gap.

**Correction to an earlier reading of this experiment.** On the short 2000–2017 record the damped variants appeared to improve monotonically as the slope was shrunk toward zero, which was interpreted as "the data says the optimal trend coefficient is ≈ 0." That was a short-window artifact. These estimators shrink toward the *training-window mean*, not toward the last observation; on a 9-year window those nearly coincide, but on a 79-year window the mean sits far below 2008 because of the historical ramp, so `damp0.2` is badly biased low (MAE 16.2 vs persistence 9.1) and is *worse* than `damp0.5`. The defensible statement is narrower: **all trend and mean-reversion variants tested lose to persistence on rank; only a short smoother edges past it, marginally.**

A single-split view of the same data (train 1930–2008 → forecast 2009–2017) gives MAE 7.51 (persist) vs 11.14 (ols) and ρ 0.966 vs 0.923. These MAE values are not comparable to the earlier 2000–2017 run — different cell population — but the within-run pairing is valid.

**And the one thing a forecast could genuinely fix is provably rank-neutral.** The oracle row is a perfect forecast of the common level applied to the frozen 2017 spatial pattern. It cuts MAE by 22% — and leaves Spearman *identical to four decimal places*, because rescaling every cell by one constant is a monotone transform and cannot reorder anything. Since `surplus_norm`'s value shows up as `between_rate_r2` (+0.068), and between-site discrimination consumes ranking, **a level forecast contributes exactly zero to the channel where surplus earns its keep.**

That is a pincer. The spatial pattern — the part that matters — is already ρ ≈ 0.92–0.96-stable under naive persistence, so there is little headroom to forecast, and 88 years of history buys about 3% of what remains. The level — the part that genuinely moves — cannot affect between-site ranking no matter how well it is predicted. Forecasting reproduces the frozen 2017 pattern with extra steps and extra error.

**Why forward-fill nonetheless failed in `_experiment12`, and why forecasting would fail the same way.** The old test measured `surplus_extended` lofo_r2 **−0.166** vs `surplus_stop2017` **+0.013** (CLF lofo_auc 0.777 vs 0.796). The mechanism is that forward-fill makes surplus a *time-invariant per-site value* on the post-2017 rows — i.e. a **site fingerprint**, the exact pathology that makes `cat_tiles92` harmful today (add_Δ −0.029, drop_Δ +0.014) and that future-work rules out under "fitted per-site random intercepts — unidentifiable at a new site by construction." A forecast inherits this completely, because the forecast is ~0.96-rank-identical to the frozen value.

**The escape is to stop half-freezing it.** Forward-fill produces a feature that varies in time before 2017 and is constant after — its information content changes mid-record. The coherent alternatives are to make it *uniformly* static (one time-invariant per-basin column over the whole record, the future-work §7 C8 rank reframe, which puts surplus in the same class as `tot_tiles92` — a class that demonstrably works and is already in the base set), or to leave it uniformly time-varying and accept NaN. The stability measured here is what licenses the static option: at the 9-year horizon ranks hold at ρ ≈ 0.96, so a static representation discards very little.

**One caveat that reopens the question empirically.** `_experiment12` aggregated surplus with `agg_surplus(..., exp=True)` — the exponential-distance-decay bucketing that [`findings.md`](../experiments/feature-manifest/findings.md) has since identified as the *bad* aggregation. The feature that works now is `surplus_norm`, the area-normalised one, which did not exist when that test ran. The fill ablation is worth re-running on `surplus_norm` at 127 sites — not because forecasting looks promising, but because the −0.166 verdict was measured on a superseded aggregation.

---

## 6. Recommendation

**Do the ablation first — it is nearly free and it may dissolve the problem.** The current NaN behaviour is an accident of a left-join, not a decision. Three alternatives are each a few lines against the existing experiment harness:

1. **forward-fill 2017 → present** (exactly what IWAND does, so there is published precedent)
2. **static per-basin descriptor** — the 2000–2017 mean or rank as a time-invariant column (future-work §7 C8, motivated by reported Iowa county rank-stability of Spearman 0.910–0.969 for farm fertilizer N)
3. **status quo (NaN)**

**Carry a real warning into this.** Future-work §8 records that broadcasting a frozen annual surplus per-day is *"what produced the −0.166 N-surplus failure"*, and explicitly distinguishes surplus — a genuinely time-varying flux — from tile drainage, which is inertial on decadal timescales. Options 1 and 2 are close relatives of the construct that already failed once. That is a reason to **measure** rather than to assume either way: the earlier failure froze a flux and broadcast it across the *whole* record, whereas option 1 freezes it only for the tail while keeping 2000–2017 varying, and the fleet is now 127 sites / 20 families rather than the cohort that produced the −0.166. Judge on `lofo_auc` and `between_rate_r2`, and treat sub-0.005 deltas as noise per the run conventions.

Then, in order:

| # | Action | Effort | Buys |
|---|---|---|---|
| **1** | Ablate fill strategy (forward-fill / **uniformly static rank** / NaN) on `surplus_norm` at 127 sites | ~1 day | possibly recovers 69% of rows at zero data cost; re-tests the −0.166 verdict on the aggregation that superseded it |
| — | **Do not** forecast post-2017 surplus from gTREND history — backtested in §5a: trend extrapolation loses to persistence, and a perfect level forecast is rank-neutral | — | — |
| **2** | Swap the join key to **Sekellick & Sherr COMID** tables; split manure from fertilizer | 2–3 days | drops the raster reconciliation; separates the 2.5×-more-variable manure term |
| **3** | Add SPARROW **seasonal** shape as a within-year multiplier | 3–5 days | real intra-annual signal; costs a GenNet↔COMID crosswalk |
| **4** | State fertilizer-tonnage temporal index | 2–3 days | real annual variation, state-level only |
| **5** | Build the **2022-Census vintage** | 2–4 weeks | the only genuinely new spatial information |
| — | **Do not** adopt IWAND forcings as a surplus replacement (they are gTREND, and CC BY-NC-ND) | — | — |

Note the interaction with what already works: `crops_norm` is the strongest single between-site feature in the current runs, and CDL is current through 2025. Corn/soy rotation is the dominant driver of N application, so the crop layer may already be carrying much of what a refreshed surplus layer would add. That is an argument for doing #1 cheaply and measuring, before committing four weeks to #5.

---

## 7. Evidence

- **Verified by direct measurement:** every number in §1 and §5a was measured directly against data on disk. The full-history layer reproduces the tracked production layer exactly on the 18 overlapping years, which is the check that the rebuild is faithful. The `_experiment12` figures were read from its committed `test_results/_experiment12_preedit{,c}.csv`.
- **Verified from primary sources:** the two quotations in §2 were read from the [dynamic SPARROW ScienceBase metadata XML](https://doi.org/10.5066/P9IJLEIY) and the IWAND-Nitrogen open-access text; the SPARROW variable list from its `dataDictionary_conus.csv`; file manifests from the ScienceBase item API.
- **Search-snippet only:** the Falcone/OFR 2020-1153 method description, EPA/AAPFCO year coverage, FAOSTAT coverage, and the Iowa IDALS filing cadence. Confirm before relying on specifics.
- **Carried from project docs, previously unverified there:** the county rank-stability Spearman figures and the −0.166 surplus failure ([`notes/future-work-report.md`](future-work-report.md) §7 C8 and §8, both tier-3 in that report).
