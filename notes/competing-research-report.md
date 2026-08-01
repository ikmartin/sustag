# Competing research, and the metrics we have to beat

Prior-art check conducted 2026-07-31.

## Bottom line

**Daily nutrient concentration at a genuinely held-out site has been attempted twice, and works poorly both times.** That is the whole finding.

- **Ungauged prediction of nutrients — occupied, but only at coarse time resolution.** SPARROW (mean-annual, seasonal in the dynamic variant) and Tso 2023 (seasonal means) both predict at unmonitored reaches. Neither is daily.
- **Daily nitrate ML — occupied, but only where the site is already monitored.** Saha 2023 and Jain 2025 both predict daily, and in both, **every evaluated site is in training**. Saha is explicitly gap-filling and needs a sensor within 50 km.
- **Daily × held-out-site — exactly one published study.** Pandit 2025, median KGE **0.18** CONUS / **0.34** Mississippi, using genuine 12-fold site exclusion. Xia 2025 runs a real spatial split too, but its held-out basins **still consume a measured discharge record**, and it reports nutrients at median KGE **< 0.45** while temperature reaches 0.89 and DO 0.72 on the same split.
- **No tree-based model has ever been evaluated in that regime.** XGBoost and RF appear here only where the site is in training (Jain) or the target is a long-term mean (Tso, Pandit's RF). Jain names PUB as future work and declines it explicitly.
- **Most of the field assumes a discharge gauge at the prediction site** — Xia, Jain, Gorski and SPARROW all require one. Our feature set has no discharge at all.
- **The island model is the genuinely unoccupied artefact.** Saha established that a neighbouring gauge carries the signal; nobody has quantified what remains when you remove it.
- **Arbitrary-point delineation is engineering, not science.** StreamStats already ships pin-drop → delineate → regression, for flow.

**We are aimed at the known failure mode, and that is the strategic fact.** Nutrients are the worst-behaved variable family under spatial transfer — the *Nature Water* review's showcase for ungauged daily prediction is dissolved oxygen, temperature and lake clarity, not nutrients. A modest LOFO result is therefore a contribution; it should not be measured against Saha's 0.75, which is a different and easier problem.

**Three corrections to earlier drafts of this report.** Two overstated the competition: Saha was called "our exact target on our exact network" (it is temporal gap-filling at monitored sites), and Tso's R² 0.71 was treated as a spatial-holdout number (it comes from a random data-point split in which stations appear in both sets). The third overstated *us*: §5 implied `loso_macro_r2` was a temporal-holdout comparator for Saha. It is not — **`cook.py` has no temporal holdout of any kind**, all three of its schemes are spatial, and LOSO is the leaky one. See §5.2.

---

---

## 1. SPARROW does the spatial half already

| | |
|---|---|
| target | long-term **mean annual load**; flow-weighted concentration is derived (load ÷ long-term flow) |
| **but also** | USGS released a **national seasonal dynamic SPARROW, 2000–2020**, lower 48 (`doi:10.5066/P1XIOCF8`) |
| spatial unit | NHDPlus reaches — 2.6M nationally; Midwest model catchments average **2.7 km²** |
| ungauged? | **yes** — calibrated on monitored loads, then applied to unmonitored reaches. That is the design |
| outputs | downloadable, incl. a dedicated Midwest / Upper Mississippi model ([SPARROW Mappers](https://www.usgs.gov/mission-areas/water-resources/science/sparrow-mappers)) |

Two consequences.

**Our added axis is *seasonal → daily*, not *annual → daily*.** Smaller than assumed.

**Do not benchmark against its headline fit.** Robertson & Saad (2019), [SIR 2019-5114](https://pubs.usgs.gov/publication/sir20195114) reports TN explaining 95% of variance in loads and 91% in yields. Load = concentration × flow, and flow spans orders of magnitude across reaches, so most of that R² is drainage area. **It is not commensurable with a concentration R².**

**SPARROW as a feature carries a leakage hazard.** It is tempting as a COMID-keyed prior that a daily model predicts departures from — and the Midwest 2012 model is genuinely COMID-keyed, needing no crosswalk. But **20 of our 116 sites sit within 100 m of a SPARROW TN calibration station** (25 within 1 km), including `USGS-05451210` at 0 m. A fitted SPARROW load at those reaches is partly a function of our own target measured at our own sites. Use only *structural* columns — source shares, `DEL_FRAC`, in-stream decay — and drop every fitted-load and `flux_*` column. See the model-improvement report for the verified column list.

## 2. Saha et al. 2023 — verified from full text

**Two earlier readings of this paper were wrong and are corrected here.** The first (mine) called it "our exact target on our exact network". The second (a domain challenge) called the target grab samples. Neither holds. Full accepted manuscript + SI obtained from the Iowa State Digital Repository mirror.

**Data is mixed and predominantly sensors.** 34 continuous optical-sensor sites — **22 IWQIS + 12 USGS**, 15-minute data averaged to daily — plus **8 grab-sample sites** from the Water Quality Portal, selected for having ≥25 samples across 2012–2019, i.e. roughly 3 lab samples a year. So it *is* largely our sensor network.

**But the evaluation is purely temporal, and that is what disqualifies it as our competitor.** Table S3 assigns *every one of the 42 sites* both a training and a testing period (typically train 2012–2017, test 2018–2019), and the model is "trained, including all the sites at once." **No site is spatially held out anywhere in the paper.** The Zhi et al. 2024 *Nature Water* review describes it in exactly those terms: *"reproduced and gap-filled daily NO₃ measurements at 42 **monitored** stream reaches in Iowa."*

Reported (Table 1, best of five input cases, median across sites, test period): **NSE 0.75, KGE 0.83, RMSE 1.53 mg/L, r 0.88, bias 0.15**; training NSE 0.89.

**The dominant predictor is a live neighbouring sensor.** `LocalNO3` — mean nitrate of neighbouring sensor sites **within 50 km**, excluding the prediction site — carries the model, and the paper's own conclusion is that it works "at nitrate data-limited regions **with a nearby sensor-based nitrate gauge**." That is our *network* variant with a hard 50 km donor requirement, and it is strong independent support for the donor block. It is not a model that can be deployed at a pin with no neighbour.

## 3. The field, verified

Full text obtained for Xia, Jain, Tso, Kratzert and the SPARROW report; Pandit and Gorski are Cloudflare-blocked (Pandit's PMC deposit is embargoed to **2026-08-09**, nine days out) so their headline numbers are verified from publisher abstracts and the rest is snippet-level.

Two columns do the discriminating work: whether the evaluated site's **own observations are in training**, and whether the model needs a **discharge gauge at the prediction location**. Most of this field quietly assumes both.

| study | target | resolution | n | modality | **own history?** | **needs Q?** | holdout | headline |
|---|---|---|---|---|---|---|---|---|
| **this project** | NO₃ **conc.** | **daily** | 116 | continuous sensors | **no** | **no** | spatial, basin-family | median NSE **0.27** ‡ |
| **Pandit 2025** | NO₃ **conc.** | **daily** | 95 (+659 CAMELS for Q; **50 in the MRB**) | continuous sensors | **both, separately** | probably **no** † | temporal + **12-fold spatial** | temporal KGE 0.60 CONUS / **0.69** MRB (**NSE 0.57**, R² 0.59) · **spatial KGE 0.18** CONUS / **0.34** MRB |
| **Xia 2025** | 20 vars incl. NO₃ | **daily** | 482 → 385/97 | grab (~138 obs/basin) | **both, separately** | **YES** | temporal + **spatial 80/20** | spatial: non-T/DO vars **KGE < 0.45** |
| Jain 2025 "XGBest" | TN/TP/TSS conc. | daily | 499 | grab (median 89/site) | **yes** | **YES** — a site-selection criterion | random 20% **rows**, ×20 | 88% TN sites NSE > 0; NSE ≥ 0.3 at 220/380 |
| Gorski 2024 | 7-day avg NO₃ conc. | daily | 46 | continuous sensors | **yes**, all 4 schemes | **YES** | temporal within site | single-site & cluster median KGE **0.66** |
| Tso 2023 | NO₃ conc. | **seasonal mean** | 2,343 → 200k reaches | grab | **yes** | **no** | random 25% **rows** | R² 0.71 (NSE 0.51, KGE 0.61), pooled |
| Saha 2023 | NO₃ conc. | daily | 42 (34 sensor + 8 grab) | mixed | **yes** | yes | temporal | median NSE 0.75, KGE 0.83 |
| SPARROW 2019 | TN **load/yield** | **mean-annual** | 1,334 → 1.36M catchments | mixed | **yes**; extrapolation unvalidated | **YES** | **none** | TN R² 0.950, **yield R² 0.913** |
| Kratzert 2019 | **streamflow** | daily | 531, 12 folds | gauges | **NO — fully held out** | n/a (Q is target) | **spatial k=12** | **median NSE 0.69** PUB |
| IIHR NFEWS | nitrate | 1 d – 8 wk | ~60 IIHR stations | sensors | not stated | not stated | not stated | **nothing published** |

‡ our `lofo_macro_r2` (median per-site NSE) for `full_feature_set`, run `20260731T055042Z` = **0.2679**; pooled `lofo_r2` is **0.4710** for the same run, and the `base` recipe in that run is 0.1809 / 0.4354. Absolute values pending re-measurement after today's D8 fix.
† inferred, not verified: Pandit trains a companion streamflow LSTM (which is what the 659 CAMELS sites are for) and predicts nitrate yield directly, which is what makes MRB-wide application possible.

### What full text changed

- **Pandit's spatial CV is genuine PUB.** 12-fold following Kratzert, ~8.3% of sites per fold **excluded entirely from training** with their full time series used for validation. Not a date holdout. The 0.18 is CONUS-wide; 0.34 is the MRB subset of the same CV.
- **Pandit publishes NSE, but only for the temporal regime.** Median KGE 0.69 / NSE **0.57** / R² 0.59 across the 50 MRB sites under *temporal* validation. The **spatial** results are reported as KGE only (0.18 CONUS, 0.34 MRB) — **no spatial NSE is published anywhere accessible**, which is precisely why KGE is not optional for us (§5.4). The one number in this literature that is both a median NSE and a real held-out-site result is Kratzert's 0.69, and its target is streamflow.
- **Xia's "ungauged" basins still have a flow gauge.** *"Runoff was derived as streamflow measured by the USGS divided by the basin area"*, and runoff is their top-ranked driver. So Xia answers "no water-quality history" — not "no gauge". Their exact NO₃ value appears only in supplementary Fig. S19; **only the "< 0.45" textual bound is verified.**
- **SPARROW reports no concentration accuracy at all.** The string "mg/L" does not appear anywhere in the 74-page report, and there is **no holdout of any kind** — the 1.36 M predicted catchments are pure unvalidated extrapolation. Its R² 0.950 is inflated by basin-size scaling; the fairer figures are **yield R² 0.913** and **unconditioned RMSE 60%**.
- **Tso's 200,000 reaches are never validated.** Genuine extrapolation, zero held-out-reach test. Its R² 0.71 comes from a random 25% *row* split where a station's other seasons and years sit in training.
- **Jain declines the experiment explicitly**: *"Future work could also explore the validity of the XGBest for making predictions in ungaged basins (PUB). This study did not explore such scenarios…"* Its split is random *rows* within each site, weaker even than a temporal block.
- **Gorski's conclusion is anti-scaling** — *"there is a threshold of dissimilarity beyond which more data does not improve the model"*, and the single-site scheme ties the pooled ones at median KGE 0.66. Directly relevant to our basin-family grouping, and to the Minnesota-expansion question.
- **Kratzert's PUB LSTM (0.69) beats SAC-SMA calibrated per basin (0.64)** and the National Water Model (0.58). That is the ceiling reference for what attribute-conditioned regionalization achieves when the target is well observed. The nutrient equivalent falls to 0.18.

### Verdict

**Exactly one published study predicts daily nutrient concentration at a site with no observation history of its own — Pandit 2025 — and it reports median KGE 0.18 CONUS-wide, 0.34 in the Mississippi.**

Six of the eight put the evaluated site's own observations in training. Only two run a real held-out-site experiment on nutrients, and **Xia's still consumes a measured discharge record**, so Pandit is the only candidate that plausibly clears both bars — and that result is a by-product of a paper whose main contribution is the MRB nitrogen budget.

**No tree-based model has ever been evaluated in this regime.** XGBoost and random forest appear in this literature only where the site is in training (Jain) or the target is a long-term mean (Tso's RF, Pandit's RF for spatial means). That is a specific, defensible gap.

**And most of the field assumes a discharge gauge at the prediction location.** Xia, Jain, Gorski and SPARROW all require one; Jain makes it a site-selection criterion. Our feature set — crops, surplus, weather, tile, basin geometry, distance buckets — contains **no discharge at all**, so we clear a bar most of this work does not attempt.

## 4. The competitor for the product slot is local

**IIHR NFEWS** ([announcement](https://iihr.uiowa.edu/all-news/2026/07/new-university-iowa-study-uses-nasa-earth-observations-advance-nitrate-forecasting), July 2026): 3-year NASA-funded pilot led by Jesus Gomez-Velez, **60 IIHR real-time stations**, NASA Earth observations, **integrating into IWQIS**, partnered with Des Moines Water Works, Cedar Rapids and Iowa City, explicitly extending to "areas with limited measurements."

Same sensor network. Will be a deployed product. Institutional partnerships we do not have. Architecture is not public. Runway is roughly a pilot's duration.

---

# 5. Metrics — what we have to beat, and what we cannot currently compare

## 5.1 Our numbers already map onto theirs, but not the one we headline

`cook._per_site_score` for regression is `r2_score(y, pred)` = 1 − SSE/SST, which **is** Nash–Sutcliffe Efficiency. `macro_r2` is the median of that across sites. Therefore:

| ours | literature equivalent | comparable? |
|---|---|---|
| **`lofo_macro_r2`** | **median NSE across sites** | **yes — this is the number to quote** |
| `lofo_r2` (our headline) | pooled R² over all rows | **no** — the denominator includes between-site variance, so it is systematically more flattering |
| `lofo_rmse` | RMSE (mg/L as N) | **no** — ours is pooled, Saha's 1.53 is a median across sites. Units match; the aggregation does not |
| `lofo_between_r2` | (no standard equivalent) | ours |
| `lofo_within_r2` | (no standard equivalent) | ours |
| — | **KGE** | **missing entirely** |

Verified in `src/eval/cook.py` on 2026-07-31: `_per_site_score` (reg) is `r2_score` on the site's own row slice, and sklearn's `r2_score` is `1 − Σ(y−ŷ)²/Σ(y−ȳ)²` against the *observed* mean — NSE's exact definition, with each site's own mean as the benchmark, which is the literature convention. `_decomposition` takes `np.nanmedian` of those, so `macro_r2` is a median NSE across sites. `lofo_r2` comes from `_score` on the whole pooled OOF vector.

**We have been headlining a number the field does not report.** Every study above quotes a median-across-sites figure; our `lofo_r2` pools all rows, which credits the model for getting between-site level differences right — real skill, but not what "NSE 0.75" means in Saha.

**Two aggregation hazards to fix before quoting `macro_r2` externally.** `nanmedian` silently drops sites that score NaN (a site with ≤1 usable row or zero variance), and **the count of sites actually entering the median is never recorded** — every paper above states its n. Worse, under `true_lofo=True` the `max_holdout_pct` cap (default 0.2) means rows in an oversized family never receive an OOF prediction at all, so `macro_r2` is a median over an *eligible subset* of the cohort; coverage is printed to stderr and never stored in the log record. A `macro_r2` quoted from a `true_lofo` run therefore describes an unrecorded fraction of 116 sites. Record `n_scored_sites` and OOF coverage as columns.

## 5.2 The evaluation-regime trap, and it runs against us

**Temporal holdout** (hold out dates at monitored sites) and **spatial holdout** (hold out whole sites) are different problems, and the gap between them is enormous — Pandit reports 0.60 temporal versus 0.18 spatial for the same model.

Saha's **NSE 0.75 is temporal**. Our LOFO is spatial. **Comparing our LOFO to Saha's 0.75 is apples-to-oranges in our own disfavour**, and it is the comparison anyone will reach for first because both are Iowa daily nitrate. Pandit's own temporal-vs-spatial MRB pair — KGE 0.69 → 0.34, on one model and one cohort — is the cleanest available measurement of how large that penalty is.

The correct comparator for our island model is **Pandit's spatial validation, median KGE ~0.34 in the MRB**. Against that bar a decent LOFO result is a genuine contribution rather than a disappointment.

**We have no temporal holdout at all, and an earlier draft of this report wrongly implied we did.** All three schemes in `cook.py` are spatial: `folds_grouped` on site (LOSO), `folds_lodo` on distance-family (LODO_d), `folds_grouped` on basin-family (LOFO). There is no date-based split anywhere in the module. `loso_macro_r2` is therefore **not** a Saha comparator — it is a leaky *spatial* median NSE, leaky because a site's nested neighbours stay in training. Reaching Saha, Jain or Gorski requires a new **fold generator** (`folds_temporal(dates, cut)`, every site in both splits, e.g. train 2012–2017 / test 2018–2019), which is a materially bigger change than the metric additions in §5.4 — those are post-processing on predictions we already compute; this one needs new fits.

**Every reported number should carry its regime.** Our log already records `true_lofo`; it should also record the holdout *type* in any external comparison.

## 5.3 The benchmark table, restated as targets

| what we would claim | comparator | their number | our current equivalent |
|---|---|---|---|
| daily nitrate, **spatial** holdout | Pandit 2025 (MRB) | median KGE 0.34 | **KGE not computed** — no spatial NSE exists on their side either |
| daily nitrate, **spatial** holdout | Xia 2025 (nutrients) | median KGE ≪ 0.4 | **KGE not computed** |
| PUB reference, **streamflow**, spatial k=12 | Kratzert 2019 | **median NSE 0.69** | **`lofo_macro_r2` = 0.27 — the one directly comparable pair we have** |
| daily nitrate, **temporal** holdout | Saha 2023 (Iowa, 42 sites) | median NSE 0.75 | **no temporal holdout exists** — needs a new fold generator, not a new metric |
| daily nitrate, **temporal** holdout | Pandit 2025 (MRB) | median NSE 0.57 | same — and this is the number our spatial 0.27 will be unfairly measured against |
| daily TN, temporal | Jain 2025 | NSE > 0.3 at 58% of sites | fraction of sites above a threshold — not reported, and temporal anyway |

**Exactly one row is comparable today: Kratzert.** Same statistic (median NSE), same regime (held-out sites, k-fold), different target — streamflow, which is the well-observed easy case and functions as a ceiling, not a peer. Every nutrient row fails on a metric we do not compute (KGE) or a regime we do not run (temporal), and in Pandit's case the spatial regime that *does* match ours is reported in KGE only, so **there is no published spatial median NSE for daily nitrate anywhere.** That asymmetry is the whole argument for adding KGE: without it we cannot compare to the only two studies in our own regime at all.

## 5.4 Proposed additions

**Do the enabling change first.** `_score_pool(..., return_oof=True)` already returns a per-row frame (`site`, `date`, `family`, `y`, `oof_site`, `oof_family`), and its docstring says outright that it exists so "a later metric change is a recomputation, not a refit"; `experiments/xgb-neighbor-count/run_exp.py::_per_site_rows` already consumes it that way. **But nothing persists it** — `experiments/feature-manifest/run_exp.py` does not even request it, so none of the 40 logged runs can be retro-scored, and every metric added below costs a fresh fit of every model we want to quote. **Writing `oof.parquet` beside `log.json` is the highest-leverage single change in this section** and should land before any of the five, because it makes all future metric work retroactive. Note also that `experiments/feature-manifest/METRICS.md` — referenced by both `CLAUDE.md` and `cook.py`'s `_cross_metrics` docstring — **does not exist**; it needs writing as these columns land.

**Then add, in priority order:**

1. **KGE and its three components.** `KGE = 1 − √((r−1)² + (α−1)² + (β−1)²)` where `r` is correlation, `α = σ_pred/σ_obs` (variability ratio) and `β = μ_pred/μ_obs` (bias ratio). Report per site, then the **median**. This is the current field standard and it is the only way to compare against Pandit or Xia at all. The decomposition is independently useful: it separates "wrong shape" from "wrong spread" from "wrong level", which our between/within split does differently and less conventionally.

   **Caveat that must be documented alongside it:** KGE and NSE are not on the same scale. KGE = 0 is *not* the "as good as the mean" benchmark that NSE = 0 is. Knoben, Freer & Woods (2019) show the mean-flow benchmark sits at **KGE ≈ −0.41**. Quoting KGE without that note invites misreading.

2. **Median-across-sites for every metric, reported alongside the pooled value.** We already compute `macro_r2` and `macro_auc`; extend the pattern to RMSE, KGE and bias, and make the macro figure the one quoted externally.

3. **PBIAS** — `100 × Σ(pred − obs) / Σ obs`. Standard in every hydrology paper, trivial to add, and it directly exposes the systematic under-prediction we would expect at treatment-outflow sites.

4. **A log-space error metric.** Nitrate is right-skewed and spans orders of magnitude; RMSE is dominated by the high tail. Report RMSE and NSE on `log1p` alongside the linear versions.

5. **Fraction of sites above a skill threshold** (e.g. % of sites with NSE > 0.5, > 0.3). Saha and Jain both report this shape, it is robust to a few catastrophic sites, and it is the most honest summary for a deployed product — it answers "at what fraction of locations is this usable".

6. **`n_scored_sites` and OOF coverage**, per §5.1. Not metrics, but without them a median is uninterpretable and a `true_lofo` median is unquotable.

All six are arithmetic on the OOF frame — none needs a refit once it is persisted. Per-site KGE needs the same degenerate-site guard `_per_site_score` already applies (`σ_obs > 0`, plus `μ_obs ≠ 0` for the β term).

**A temporal fold generator is the separate, larger item.** `folds_temporal(dates, cut)` is what unlocks Saha, Jain, Gorski and Pandit's 0.57 — and unlike the six above it requires new fits, so it is a decision about run budget rather than a post-processing addition. Worth doing once, on the shipped recipe only, purely to have the temporal-vs-spatial gap measured on *our* cohort the way Pandit measured it on theirs (0.69 → 0.34).

**Do not replace anything.** `lofo_prauc` and `lofo_r2` are load-bearing names selected by `run_exp.py` and `render_results.py`; renaming breaks historical rendering. Add the new columns beside them and change only which number we *quote externally*.

**Also worth recording per run:** the holdout type, the cohort size, and whether the number is pooled or median. Every external comparison in this report failed on one of those three.

---

## 6. What to do with this

1. **Reframe the claim.** Not "prediction at ungauged locations" (SPARROW) and not "daily nitrate ML" (Saha, Jain). The defensible claim is **daily concentration under a true spatial holdout, with the island/network contrast quantified**.
2. **Lead with the island model.** It is the part nobody has.
3. **Benchmark against Pandit's spatial KGE, not Saha's temporal NSE**, and say explicitly why the comparison is the fair one.
4. **Reproduce Saha's neighbour feature deliberately** and report island-vs-network as the headline result rather than an implementation detail.
5. **Take the sample-size problem seriously.** 116 sensors is small for PUB — Kratzert used 531 basins, Pandit used CONUS. The binding constraint on the island model is the number of *distinct basins*, not features, which makes pooling beyond Iowa higher-leverage than further feature engineering. This bears directly on the Minnesota expansion.

## Evidence quality

**Verified by fetching primary sources:** SPARROW's targets, spatial units, downloads and the national seasonal dynamic release; Tso 2023 in full (station count, R², reach count, web viewer); Jain 2025 in full; Xia 2025; the NFEWS announcement; Saha 2023 abstract plus the associated PSU thesis.

**Verified by reading this repo's source** (2026-07-31, `src/eval/cook.py`): the `_per_site_score` → NSE identity; `macro_r2` = `nanmedian` of per-site NSE; `lofo_r2` pooled over all rows; the absence of KGE; the absence of any temporal fold generator; the `true_lofo` / `max_holdout_pct` coverage gap; `return_oof` existing but never persisted. Run figures read from `experiments/feature-manifest/results_20260731T055042Z.md`.

**Search snippet only, lower confidence:** Pandit 2025's figures (paywalled — Wiley returns 402, PMC deposit embargoed to **2026-08-09**). Two independent summaries agree on temporal KGE 0.60 CONUS and spatial 0.18 CONUS / 0.34 MRB; two also agree on temporal MRB KGE 0.69 / NSE 0.57 / R² 0.59 over 50 MRB sites, and the regime attribution of that triple (temporal, not spatial) is stated explicitly in one of them and is consistent with 0.69 > 0.60 CONUS. **Confirm all of it from the PDF after 2026-08-09 before citing in writing** — the NSE 0.57 in particular, since §5.3 leans on it. SPARROW Midwest TN R² 0.95/0.91 (paywalled SIR). Kratzert 2019's NSE figures (well established, but snippet-sourced here).

**Not verified:** that NFEWS is LSTM-based — the architecture is not public. That no commercial pin-drop nutrient product exists — absence of evidence from targeted searching, not proof.

**Unchased lead:** a 2021 STOTEN paper on PLS models for **daily nitrate load at 34 unmonitored Iowa sites** ([S0048969721017113](https://www.sciencedirect.com/science/article/abs/pii/S0048969721017113)) returned 403. It is Iowa-specific ungauged prior art and should be read.
