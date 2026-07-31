# Cross-validation design: what the field does, and what our data says

**Question.** Our LOFO holds out connected components of the basin conflict graph. Is that the right split, and should it be relaxed for basins that conflict only weakly?

Compiled 2026-07-27. Companion to [`src/splits/cv-notes.md`](../src/splits/cv-notes.md), which is the short version living next to the code; this is the measured version.

---

## 1. Bottom line

**Three findings, and the third reverses the obvious conclusion.**

**(a) The strict conflict graph was badly broken by hub basins, and that is now fixed.** A handful of large downstream basins physically contain most of the fleet, and because the conflict graph joins any pair that is spatially nested *and* temporally overlapping, one hub chains everything inside it into a single component. Before the fix: 21 families, the largest holding **76 sites / 60.2% of all rows** — one LOFO fold trained on 40% and tested on 60%. Excluding four hub sites (4.5% of rows) at the build level gives **31 families, largest 23 sites / 25.1%**. See [`notes/`](.) and `pipeline_config.toml [site_filters].big_basin`.

**(b) The "soft graph" idea is well-founded on the leakage axis, and it is standard practice in the literature.** Among conflicting pairs, residual nitrate correlation decays cleanly with separation and crosses zero between 50 and 100 km. Relaxing conflicts beyond ~100 km would take the largest fold from 25% to 15% of rows. This is exactly buffered leave-one-out CV, where the buffer is the autocorrelation range.

**(c) But by the criterion the current literature actually recommends, relaxing moves the wrong way — because our LOFO is already *optimistic* relative to deployment, not pessimistic.** A random pin in the basin union sits a median **116 km** from the nearest sensor. A LOFO held-out site sits a median **34 km** from the nearest *training* site. Holding out a whole basin family does not produce spatial isolation, because the fleet is clustered along rivers and other families remain close. Every relaxation widens that gap (100 km → 27 km, 50 km → 22 km).

So the conflict graph is doing its job (no shared-drainage leakage) while the evaluation still does not resemble deployment — and those are two different problems that tuning one knob cannot solve together.

---

## 2. What the field does

Our design has a name and a substantial literature.

**Block / spatial CV.** [Roberts et al. 2017, *Ecography* 40:913–929](https://nsojournals.onlinelibrary.wiley.com/doi/abs/10.1111/ecog.02881) is the canonical reference: when temporal, spatial or hierarchical dependence exists, random CV seriously underestimates predictive error, and data should be split strategically. Their recommendation is to block *even when* no correlation is visible in fitted residuals. Our LOFO is a hierarchical block CV.

**Buffered LOO / autocorrelation-range buffers — i.e. the "soft graph".** [Le Rest et al. 2014, *Global Ecology and Biogeography*](https://onlinelibrary.wiley.com/doi/10.1111/geb.12161) proposes spatial leave-one-out where a buffer, set to the range of residual spatial autocorrelation, is excluded around each test point. The [`blockCV`](https://besjournals.onlinelibrary.wiley.com/doi/full/10.1111/2041-210X.13107) R package (Valavi et al. 2019) automates the same logic: `cv_spatial_autocor` fits a variogram and uses the estimated autocorrelation range as the block size.

**This resolves a methodological worry worth recording.** Using measured nitrate correlation to decide splits sounds like selection on the outcome — pairs whose correlation is low *by chance* would get separated, and those folds would score well for reasons that will not replicate. The literature's practice avoids this: the response is used to estimate **one global range from all pairs at once**, never as a per-pair gate. Calibrate a single threshold offline; apply it structurally (distance) at split time. Distance is target-independent and immune to the problem.

**The counter-argument.** [Wadoux et al. 2021, *Ecological Modelling* 457:109692](https://www.sciencedirect.com/science/article/abs/pii/S0304380021002489) — "Spatial cross-validation is not the right way to evaluate map accuracy" — argues that spatial CV manufactures artificial extrapolation and can be *overly pessimistic*, and that unbiased map accuracy requires probability sampling and design-based inference. "More separation is safer" is therefore not a settled principle.

**The modern reconciliation, and the one that matters for us.** NNDM ([Milà et al. 2022, *MEE* 13:1304–1316](https://besjournals.onlinelibrary.wiley.com/doi/10.1111/2041-210X.13851)) and kNNDM ([Linnenbrink et al. 2024, *GMD* 17:5897](https://gmd.copernicus.org/articles/17/5897/2024/)) abandon independence as the goal. Instead they construct folds whose **nearest-neighbour distance distribution between test and training points matches the distribution between prediction locations and training points**. A [2026 synthesis](https://arxiv.org/html/2605.13689v1) generalizes this as "prediction-domain adaptive evaluation": sampling sits on an extrapolation continuum, and the aim is to resemble the prediction situation *in difficulty*, not to maximize separation. For clustered samples this behaves like spatial CV; for random samples, like random CV. It pairs with Meyer & Pebesma's "area of applicability" — accuracy estimates are only meaningful for prediction situations resembling the folds.

This is directly computable for us, because our prediction domain is well defined: an arbitrary pin in the basin union.

---

## 3. Measurements

### 3.1 Does residual correlation decay with distance among conflicting pairs?

From `experiments/pre-expansion-network-test/pairs.parquet` (487 conflicting pairs with basin areas, **83-site pre-expansion cohort**):

| axis | Spearman vs residual ρ |
|---|---|
| **euclidean distance** | **−0.416** |
| area ratio (dilution) | −0.104 |

| distance band | n | mean residual ρ |
|---|---|---|
| 0–25 km | 51 | **+0.380** |
| 25–50 km | 62 | +0.223 |
| 50–100 km | 130 | +0.066 |
| 100–200 km | 213 | **−0.049** |
| 200+ km | 31 | −0.003 |

Distance is the right axis; **dilution is not**, despite the network report's Spearman ≈ −0.65 for area ratio — that was measured on reach-graph nearest neighbours, a different population.

This does **not** contradict the network report's "euclidean distance is the wrong axis." That claim compared *connected vs disconnected* pairs at matched distance, arguing the graph edge matters more than mere proximity. The question here is different: *given* an edge, does distance modulate its strength? It does. Both hold.

### 3.2 What relaxation would buy (current 123-site fleet, 187 conflict edges, median length 76 km)

| threshold | edges kept | families | largest family | largest % rows |
|---|---|---|---|---|
| none (current) | 187 | 31 | 23 sites | 25.1% |
| ≤ 150 km | 129 | 42 | 22 | 24.4% |
| **≤ 100 km** | **103** | **54** | **9** | **14.7%** |
| ≤ 75 km | 92 | 60 | 9 | 14.7% |
| ≤ 50 km | 74 | 72 | 12 | 12.0% |
| ≤ 25 km | 42 | 92 | 3 | 6.1% |

100 km is where the measured residual ρ crosses zero *and* where fold balance sharply improves.

### 3.3 The NNDM diagnostic — the decisive one

Distances from 3,000 random pins inside the basin union, versus LOFO held-out-site → nearest-training-site distances:

| | median | 10 / 25 / 75 / 90 pct (km) |
|---|---|---|
| **Deployment**: random pin → nearest sensor | **116** | 15 / 33 / 211 / 302 |
| **LOFO strict (current)** | **34** | 6 / 21 / 46 / 59 |
| relax > 150 km | 29 | 3 / 16 / 41 / 53 |
| relax > 100 km | 27 | 3 / 14 / 35 / 48 |
| relax > 50 km | 22 | 3 / 12 / 32 / 43 |

**LOFO is optimistic relative to deployment by ~82 km at the median, and every relaxation widens the gap.**

> **Correction (2026-07-27).** The 34 km row assumes LOFO is *leave-one-family-out* — each fold holding out a single family. It is not. `cook._grouped_models` runs `GroupKFold(min(n_splits, n_groups))` with `n_splits = 5`, so a fold holds out roughly a fifth of the families (~4 of 21) at once. Holding out more families at a time puts the held-out sites **further** from the surviving training set, so the true LOFO figure is larger than 34 km and the gap to 116 km is correspondingly **smaller** than stated. The direction of the argument is unchanged — CV is still nearer-field than deployment, and relaxing still widens the gap — but the 82 km magnitude is an overestimate and should be recomputed against the real fold construction before being quoted. The same misreading appeared in `experiments/feature-manifest/METRICS.md`, corrected there too.

The 116 km median is itself a headline fact that does not appear anywhere else in the project's documentation: **the widget routinely predicts far outside the spatial neighbourhood any CV fold ever tests.**

---

## 4. Recommendations

**Keep the strict conflict graph as the leakage control.** It prevents shared-drainage leakage, which is real and is not what the distance evidence addresses. Section 3.3 is not an argument that it is too strict.

**Add distance-stratified reporting as the honesty control.** Score LOFO predictions binned by each held-out site's distance to its nearest training site, so performance at 30 km and at 100 km+ can be read separately instead of collapsed into one number. This is the "area of applicability" idea and it answers the question the product actually raises. Cheap: the OOF predictions and site coordinates already exist. **This is the highest-value item here.**

**Treat the soft graph as a fold-balance fix, not a bias correction — and make it opt-in.** Add `max_conflict_km: float | None = None` to `build_conflict_graph` / `split_groups`, defaulting to today's behaviour, and report strict and soft side by side. The gap between them is informative. Do not silently replace the strict number.

**Do not gate on per-pair measured correlation.** Use one globally-calibrated distance threshold (§2). If a second axis is wanted, prefer structural ones: along-network distance rather than euclidean, or fraction of shared drainage area.

**Recompute the decay curve on the current fleet before fixing a threshold.** The −0.416 and the 50–100 km crossing come from the 83-site pre-expansion cohort. The fleet now reaches well outside Iowa and the decay length may differ.

---

## 5. Open questions

- **Can any fold construction over this fleet reproduce a 116 km prediction distance?** Probably not — the sensors are clustered along rivers. If not, the honest conclusion is that LOFO measures a *nearer-field* problem than the product solves, and distance-stratified reporting is the only way to bound the deployment case.
- **Should the evaluation target change?** If CV cannot reach the deployment regime, an explicit far-field holdout (train on one region, test on another) would at least bracket it — though at 31 families the sample is thin.
- **What does the distance-stratified curve look like?** If performance is flat out to 100 km+, the 82 km gap does not matter much. If it decays sharply, every headline number in the project is optimistic for the deployed use case. This is the measurement that decides how much of the rest matters.
- **Does kNNDM fold construction help or just relabel?** Worth prototyping once the stratified curve exists.

---

## 6. Evidence quality

- **Verified by direct measurement, reproducible against on-disk data:** every number in §1 and §3 — family structure, the distance-decay table, the relaxation sweep, and the NNDM comparison.
- **Read directly:** the 2026 prediction-domain-adaptive synthesis (arXiv 2605.13689).
- **Assessed from abstracts and search results, not full text:** Roberts et al. 2017, Le Rest et al. 2014, Valavi et al. 2019, Wadoux et al. 2021, Milà et al. 2022, Linnenbrink et al. 2024. The characterizations above are consistent across multiple sources, but verify specific claims before quoting them.
- **Cohort caveat:** §3.1 is the 83-site pre-expansion fleet; §3.2 and §3.3 are the current 123-site fleet.
