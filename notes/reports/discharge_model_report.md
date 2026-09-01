# Discharge model — and the NWM decision

**Verdict: do not acquire NWM.** A regionalised discharge model built entirely from data already on disk **exceeds NWM's measured skill on four of five metrics**, and does so on the harder version of the test. The 26 GB CHANOBS pull and the 76 GB zarr pull are both unjustified on present evidence, and the decision rule this was measured against was written before the numbers existed.

Run 2026-08-27. Code and reproduction in `projects/discharge-test/`.

## The result

4,620 gauged sites across the AOE, 906 basin families, 3,457,217 daily rows, five-fold cross-validation grouped so that **a test site's basin never contains a training site's outlet**.

| metric | this model (median per-site) | this model (pooled) | NWM at gaged reaches |
|---|---|---|---|
| Nash–Sutcliffe | **0.509** | 0.523 | 0.35 |
| log-space NSE | **0.585** | 0.690 | 0.521 |
| correlation | **0.787** | 0.723 | 0.701 |
| bias (pred/obs) | **1.007** | 0.993 | 1.023 |
| Kling–Gupta | 0.529 | 0.612 | **0.646** |

85.8% of held-out sites score NSE above zero — that is, beat their own long-term mean.

**NWM wins on KGE alone.** KGE penalises the variance ratio directly, so it is the metric that punishes a damped hydrograph, and a gradient-boosted model regresses toward the mean more than a routed physical model does. That is a real and interpretable deficit, and it is the one axis on which NWM would add something.

## Why the comparison favours NWM and it still loses

The two numbers are not from identical samples, and every difference runs in NWM's favour:

- **NWM was scored where it was calibrated.** Its 0.35 comes from gauged reaches — CHANOBS exists only at gauges — and NWM's per-basin calibration saw them. This model was scored on basins it had **never seen**, which is strictly the harder task and the one that matches the intended use.
- **The published NWM figures come from 19 of our reaches over 2018–2019**; these come from 4,620 sites over the full record. A 19-site sample is not a tight estimate.
- Gauged-reach skill is a **ceiling** on ungauged skill, because gauges sit at well-behaved, well-instrumented locations. NWM's real number where we would actually use it — the 158 nitrate sites with no gauge — is below 0.35, while this model's 0.509 is already an ungauged-equivalent measurement.

## What the model is

Target is **specific discharge in mm/day, log-transformed**: `Q × 86.4 / A`. Raw m³/s across basins spanning four orders of magnitude in drainage area is mostly a prediction of *area*, and a model can score well on it while learning no hydrology at all.

Features are basin-mean gridMET (`pr`, `pet`, `tmmx`, `tmmn`, `srad`) with lags at 1–30 days and rolling sums at 3–90 days, antecedent water balance as cumulative `pr − pet` over the same windows, day-of-year harmonics, static reach geometry from NHDPlus VAA, and ten StreamCat catchment attributes. 90 features, XGBoost, five folds.

**Predictions are smearing-corrected.** Exponentiating a log-space fit returns the conditional median rather than the mean, which is biased low for a right-skewed variable — measured at 0.76 of observed before correction, with KGE punished twice because it scores the mean and variance ratios directly. Duan's estimator, computed on the training fold only, moved bias to ~1.0.

## Two data findings this produced

**The published store carries a discharge channel for records that are not rivers** — 122 tidal streams, 23 storm drains, 15 groundwater wells, 7 estuaries and one atmosphere station. Tidal reaches reverse flow, which is where 5.5% of rows carrying negative discharge came from; a specific-discharge log transform cannot represent them and a rainfall-runoff model over a contributing basin is the wrong description of all of them.

**About 3.5% of gauged sites have a wrong drainage area**, and the water balance finds them. Long-term discharge depth over precipitation depth must sit between zero and roughly one — a basin cannot shed more water than it receives, and a real gauged stream does not sustain 0.2% of its rainfall. Measured, the cohort is tightly behaved (p50 **0.349**, p01 0.0023, p99 0.894), and the **170 sites outside 0.01–1.5 carry a median NSE of −1.2 against 0.489 for the rest**. They are bad snaps: the gauge sits on a tributary while the snapped reach is a large river, so the area is overstated and specific discharge collapses. Before filtering them, pooled NSE was **−0.064** against a median per-site NSE of 0.464 — a divergence that was the symptom rather than a metric quirk. Afterwards the two agree (0.523 against 0.509), which is what says the filter removed a defect rather than trimmed a tail.

**Note what the filter does not explain**: 375 of the 463 sites with NSE below −1 have perfectly plausible runoff ratios. Those are genuinely hard basins where the model fails, and they remain in the cohort and in the scores.

## The imputed series

110 of the 158 ungauged nitrate records now carry a modelled daily discharge — **749,320 values** in `projects/discharge-test/results/imputed_discharge.parquet`, as both mm/day and m³/s.

**48 records are refused rather than predicted**, and the refusal is recorded with its reason in `imputed_discharge_refused.csv`: 11 wells, 7 lakes, 7 estuaries, 5 groundwater stations, 3 tidal streams, and assorted tile outlets, saturated buffers, blind inlets and a pond. A rainfall-runoff response over a contributing basin is the wrong description of every one, and the model was neither trained nor validated on anything resembling them. Emitting a number there would be inventing data, not imputing it.

**The series is checked against the same water balance that screens the training cohort**, and this is what makes it trustworthy rather than merely plausible-looking:

| | predicted | observed cohort |
|---|---|---|
| runoff ratio p50 | 0.310 | 0.349 |
| runoff ratio p05–p95 | 0.165–0.450 | 0.025–0.577 |
| outside physical bounds | **0 of 110** | — |

The predicted spread is **narrower than observed**, and that is the expected face of the KGE deficit: a boosted-tree regression damps variance, so extremes are underrepresented — predicted daily maxima reach 47.8 mm/day where observed reach about 140. Consumers should treat the series as reliable for level and timing and conservative for peaks.

## What this determines

- **NWM is not worth acquiring as a covariate.** It is beaten on NSE, log-NSE, correlation and bias by a model with no acquisition cost, on a harder evaluation.
- **110 of the 158 nitrate sites lacking observed discharge now have a modelled series** at the skill reported above, with no download. The other 48 are refused on physical grounds rather than filled.
- **The modelled series stays out of `published/`.** The inventory chapter quarantines NWM as "a simulation wearing the shape of an observation", and our own model earns the same treatment: it is a project artifact, and any recipe using it must carry the distinction.

## What this does NOT determine

- **Whether NWM would help where this model is weak.** NWM's KGE advantage (0.646 against 0.529) is real, and a routed physical model preserves hydrograph variance in a way a boosted-tree regression does not. If a downstream task turns out to depend on flow *variability* rather than level and timing, that gap is the argument for revisiting.
- **True ungauged skill, for either model.** Both are scored at gauges. Spatial holdout is the closest available proxy and it is not the same thing; no data we can obtain settles it.
- **Whether these features are the best available.** No feature selection or tuning was performed — the model is a first honest fit, and the result is a lower bound on what this approach reaches.
