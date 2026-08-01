# Industry KPIs

Metrics the hydrology and water-quality ML literature actually reports, in the format of `kpis.md`. Written 2026-07-31 after a prior-art review — see `competing-research-report.md` for the studies.

**The headline problem this file solves:** three of the five results we would want to compare against are stated in metrics we do not compute, and the number we currently headline is not the number the field reports. Adding four metrics makes us directly comparable without changing anything we already track.

---

## The conventions that matter more than any single metric

**Report the metric you optimize.** Everything below is about *reporting*. None of it changes what the model is fitted to, and today that is pooled row-wise squared error — which weights sites by row count and, being MSE-optimal, produces predictions shrunk toward the conditional mean. KGE penalises exactly that shrinkage through $\alpha = \sigma_p/\sigma_y$. **Adding KGE to the report while still training on pooled MSE means training for the failure the headline metric punishes.** See `aug-01.md` T1.2b; the fix is row weights of $1/n_{\text{rows}}(s)$ plus a change to the tuner's selection rule, and it must follow the metric additions here.

**Median across sites, not pooled over rows.** Every study below reports the *median of a per-site score*. Our headline `lofo_r2` is computed on the pooled row vector, so its denominator carries between-site variance and it is systematically higher. On run `20260731T055042Z`, pooled `lofo_r2` was **0.479** while median-per-site `lofo_macro_r2` was **0.264** — nearly double. We already compute the comparable number; we just do not lead with it.

**The holdout regime is part of the metric.** A temporal holdout (hold out dates at a monitored site) and a spatial holdout (hold out whole sites) are different problems, and the gap is enormous — Pandit reports median KGE 0.60 temporal versus 0.18 spatial for the same model. Any number quoted without its regime is meaningless.

---

## Recommended headline metrics

##### `kge` –– "is the shape, the spread, and the level all right at once?"
Kling–Gupta Efficiency decomposes skill into correlation, variability ratio and bias ratio, and penalises all three. It is the current field standard and the metric three of our five comparators report; without it we cannot compare to them at all.
$$\mathrm{KGE} = 1 - \sqrt{(r-1)^2 + (\alpha-1)^2 + (\beta-1)^2}, \qquad \alpha = \frac{\sigma_p}{\sigma_y}, \quad \beta = \frac{\mu_p}{\mu_y}$$

##### `kge_r`, `kge_alpha`, `kge_beta` –– "which of the three is broken?"
The components, reported alongside the scalar. `r` is timing, `α` is whether the prediction is too smooth or too volatile, `β` is systematic level bias — and a model that under-predicts every treatment-outflow site will show it in `β` specifically.
$$r = \mathrm{corr}(y, p), \qquad \alpha = \sigma_p/\sigma_y, \qquad \beta = \mu_p/\mu_y$$

##### `macro_nse` –– "at a typical site, how much variance do we explain?"
The median across sites of per-site NSE. **This is our existing `macro_r2` renamed to what the field calls it** — `sklearn.r2_score` computed per site is exactly Nash–Sutcliffe. It is the number to quote externally.
$$\widetilde{\mathrm{NSE}} = \operatorname*{median}_{s\in\mathcal S} \mathrm{NSE}_s, \qquad \mathrm{NSE}_s = 1 - \frac{\sum_{i: s(i)=s}(y_i - p_i)^2}{\sum_{i: s(i)=s}(y_i - \bar y_s)^2}$$

##### `macro_kge` –– "the same, in the metric everyone else uses"
Median across sites of per-site KGE. Pandit, Xia and Gorski all report exactly this quantity, so it is the single number that makes us commensurable with the held-out-site literature.
$$\widetilde{\mathrm{KGE}} = \operatorname*{median}_{s\in\mathcal S} \mathrm{KGE}_s$$

##### `frac_nse_above` –– "at what fraction of locations is this actually usable?"
The share of sites clearing a skill threshold, conventionally $\tau \in \{0, 0.3, 0.5\}$. Robust to a few catastrophic sites in a way a median is not, and it is the honest summary for a deployed product because it answers the deployment question directly.
$$F_\tau = \frac{1}{|\mathcal S|}\Big|\{\, s \in \mathcal S : \mathrm{NSE}_s > \tau \,\}\Big|$$

---

## Supporting metrics

##### `pbias` –– "are we systematically high or low?"
Percent bias over the pooled rows, reported in every hydrology paper and trivial to add. It exposes exactly the failure mode we expect at treatment-outflow sites, where a watershed-covariate model must over-predict.
$$\mathrm{PBIAS} = 100 \times \frac{\sum_i (p_i - y_i)}{\sum_i y_i}$$

##### `log_nse`, `log_rmse` –– "do we get the low end right too?"
The same statistics on $\log(1+y)$. Nitrate is right-skewed and spans orders of magnitude, so linear RMSE is dominated by the high tail — and the low tail is precisely where wetland outflows and diluted sites live.
$$\mathrm{NSE}_{\log} = 1 - \frac{\sum_i \big(\log(1+y_i) - \log(1+p_i)\big)^2}{\sum_i \big(\log(1+y_i) - \overline{\log(1+y)}\big)^2}$$

##### `nse` –– "the field's name for what we already compute"
Nash–Sutcliffe Efficiency is **algebraically identical** to R² taken against the observed mean, which is what `sklearn.r2_score` computes. Our `lofo_r2` is already NSE; the only change needed is to say so, and to report the per-site median beside the pooled value.
$$\mathrm{NSE} = 1 - \frac{\sum_i (y_i - p_i)^2}{\sum_i (y_i - \bar y)^2} \;\equiv\; R^2$$

---

## The scale trap: KGE and NSE are not interchangeable

$\mathrm{NSE} = 0$ means "no better than predicting the observed mean". **$\mathrm{KGE} = 0$ does not.** The mean-benchmark equivalent sits at

$$\mathrm{KGE}_{\text{mean benchmark}} = 1 - \sqrt{2} \approx -0.41$$

so a model at KGE 0.18 is meaningfully better than climatology, not marginally worse than useless. Quoting KGE without this note invites misreading — usually in our favour, which is worse. (Knoben, Freer & Woods 2019.)

---

# Metrics to beat

Every row is the *median across sites* unless noted. The two columns that decide whether a number is a fair comparison are **whether the evaluated site's own observations were in training** and **whether the model was handed a discharge gauge at the prediction location**.

### Our regime — daily concentration, site fully held out, no discharge input

| study | target | holdout | metric | value |
|---|---|---|---|---|
| **Pandit 2025** (CONUS) | daily NO₃ conc. | **12-fold spatial**, sites excluded entirely | median KGE | **0.18** |
| **Pandit 2025** (Mississippi) | daily NO₃ conc. | same | median KGE | **0.34** |
| **Xia 2025** | daily nutrients | **spatial 385/97**, unseen basins | median KGE | **< 0.45** † |

† Xia's held-out basins still consume a measured USGS discharge record, so it clears "no water-quality history" but not "no gauge". Its exact NO₃ value appears only in a plotted supplementary figure; only the `< 0.45` bound is textual.

**`0.34` is the number to beat.** It is the closest published result in our exact regime and in the same river basin. Nothing else in this literature both predicts daily concentration and holds the site out entirely.

### Ceiling reference — what regionalization achieves on an easier target

| study | target | holdout | metric | value |
|---|---|---|---|---|
| **Kratzert 2019** | daily **streamflow** | **spatial k=12** | median NSE | **0.69** |
| " — calibrated SAC-SMA, per basin | " | " | median NSE | 0.64 |
| " — NOAA National Water Model | " | " | median NSE | 0.58 |

Attribute-conditioned regionalization reaches 0.69 when the target is well observed and physically constrained. The nutrient equivalent collapses to 0.18. That gap is the difficulty of our problem, stated precisely.

### Easier regimes — quoted so nobody compares us to them by accident

| study | target | holdout | metric | value |
|---|---|---|---|---|
| Saha 2023 (Iowa, 42 sites) | daily NO₃ conc. | **temporal**, site in training, sensor within 50 km required | median NSE / KGE | 0.75 / **0.83** |
| Gorski 2024 (46 ag sites) | 7-day avg NO₃ conc. | **temporal within site** | median KGE | 0.66 |
| Jain 2025 "XGBest" (499 sites) | daily TN conc. | **random 20% of rows**, per site | % sites NSE > 0 | 88% |
| " | " | " | % sites NSE ≥ 0.3 | 58% (220/380) |
| Tso 2023 (2,343 GB stations) | **seasonal mean** NO₃ | random rows | pooled R² / NSE / KGE | 0.71 / 0.51 / 0.61 |
| SPARROW Midwest 2019 | **mean-annual TN load** | **none** | yield R² | 0.913 ‡ |

‡ SPARROW reports no concentration accuracy anywhere — the string "mg/L" does not appear in the 74-page report — and performs no holdout of any kind. Its headline load R² of 0.950 is inflated by basin-size scaling; yield R² 0.913 and unconditioned RMSE 60% are the fairer figures. Not commensurable with a concentration metric.

### Why our task is harder than several of these

- **No discharge input.** Xia, Jain, Gorski and SPARROW all require a flow gauge at the prediction location; Jain makes it a site-selection criterion. Our feature set contains no discharge at all.
- **Family-grouped CV, not random site holdout.** LOFO keeps hydrologically connected sites together across the split, so a held-out site cannot borrow from a nested neighbour. Pandit's 12-fold and Xia's stratified split are both *random* over sites and do not control for connectivity.
- **Daily concentration, not load or seasonal mean.** Load correlates with drainage area and is far easier to fit; SPARROW's R² 0.95 is mostly basin size.

If we clear 0.34 median KGE under family-grouped LOFO with no discharge, that is a stronger result than the number alone suggests.

---

## Current position

| | pooled | median per site |
|---|---|---|
| REG base (island) | `lofo_r2` 0.435 | **`macro_nse` 0.181** |
| REG full | `lofo_r2` 0.479 | **`macro_nse` 0.264** |

From run `20260731T055042Z`; absolute values pending re-measurement after the 2026-07-31 D8 fix. **KGE is not yet computed**, so the comparison against 0.34 cannot be made today — that is the single blocking gap.

For CLF there is no established external benchmark: the exceedance-flag framing is ours, and no comparator reports PR-AUC on a nitrate violation threshold. `lofo_prauc` remains the right internal headline, but note `macro_auc` (median per-site AUC) and `lofo_prauc` (pooled) are different metrics, not a pooled/median pair — a per-site PR-AUC is needed before CLF has a comparable macro figure.
