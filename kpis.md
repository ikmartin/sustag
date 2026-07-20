# KPIs

Primary KPI: **can we flag a nitrate-violation day (≥ 10 mg/L) at a location with no sensor?** 

The key metric is the **LOFO classifier's discrimination on unseen basins** — it ranks violation days **2.4× better than chance** (PR-AUC, threshold-free) — turned into a decision by the deployed **β = 2 operating point: recall 0.86 at FDR 0.59** (catches ~86% of violation days; ~59% of alarms are false, at the ~26% base-rate prevalence). 

Secondary KPI: Same question but with a regression target, **can we predict nitrate concentration (mg/L) timeseries at unseen sites?** Everything below defines the supporting metrics.

Primary CV metric is always **LOFO** (leave-one-basin-family-out) — this is the honest generalization number since it's the most robust against data-leakage. LOSO (leave-one-site-out) is reported too but is optimistic because nested basins leak into one another (literally). CV outputs are scored as raw model probabilities/values; the 10 mg/L threshold defines the *target*, not a scoring cutoff. **Deployment** then layers one decision cutoff on top: a β-derived threshold τ picked post-hoc on the frozen classifier (see *Deployed operating point*). Definitions mirror `src/eval/cook.py` (`_score`, `_imbalance_suite`), and the β-table lives in `src/models/tune_threshold.py` + the shipped model's `<name>.meta.json`.

## Headline results (final deployed models)

LOFO unless noted. List results for `recipe_CLF2` and `recipe_REG2`, trained on 80 sites, 19 basin families, 160,074 site-days. See (`logs/fulltrain_logs.json`). These are the numbers in `notebooks/fulldemo.ipynb`.

**Classification — violation ≥ 10 mg/L** (base rate 0.263):

| ROC-AUC | PR-AUC lift | recall @10% FAR | F2 | macro-AUC | Brier | between-rate R² |
|---|---|---|---|---|---|---|
| **0.82** | **2.4×** | 0.52 | 0.71 | **0.90** | **0.137** | 0.24 |

LOSO ROC-AUC 0.84 (optimistic — nested basins leak).

**Regression — nitrate concentration (mg/L):**

| R² (LOFO) | R² (LOSO) | RMSE | within-site R² | between-site R² | macro-R² |
|---|---|---|---|---|---|
| **0.33** | 0.38 | **4.36** | 0.42 | 0.21 | 0.24 |

## Classification (violation ≥ 10 mg/L)

Headline pair is **`lofo_prauc_lift`** (base-rate-aware discrimination, threshold-free) + **`lofo_recall_at_far`** (recall you get under a fixed false-alarm budget) — both with the leaking-basin family held out. LOSO twins are reported but optimistic.

| KPI | Definition | Direction |
|---|---|---|
| `lofo_auc` / `loso_auc` | ROC-AUC on family / site OOF (LOFO honest, LOSO optimistic) | ↑ (0.5 = chance) |
| `lofo_prauc_lift` | PR-AUC ÷ base rate — × better than a random ranker at the rare class (imbalance-normalised, threshold-free) | ↑ (1 = chance) |
| `lofo_recall_at_far` | Recall achievable at a false-alarm rate ≤ `_FAR_BUDGET` (10%) | ↑ |
| `lofo_f2` | Best F2 over the threshold sweep (recall weighted 2× precision — false-negative-averse) | ↑ |
| `macro_auc` | Median per-site AUC (equal site weight) — timing *within* a basin | ↑ |
| `brier` | Mean squared error of P(violation); calibration | ↓ (0 best) |
| `between_rate_r2` | R² of per-site mean P(violation) vs per-site actual violation rate — ranks which sites are worst | ↑ |
| `base` | Violation base rate (random-guess floor; reference for lift/brier) | — |

### Deployed operating point (β-table)

What actually ships in the widget is a **recall-emphasis knob β**, not a fixed cutoff. For each β, `src/models/tune_threshold.py` sweeps the honest LOFO OOF, picks τ = argmax F_β, and records the recall/precision there; the table + base rate are written into the model's `meta.json` and read at serve time. The frozen booster is never retrained — this is pure cutoff selection.

| KPI | Definition | Direction |
|---|---|---|
| `tau` | Decision threshold on P(violation): alarm when P ≥ τ. τ = threshold that maximises F_β on the LOFO OOF | — (↓ as β ↑) |
| `recall` | Catch rate at τ(β): TP/(TP+FN) — share of true violation days flagged | ↑ |
| `fdr` | False-discovery rate at τ(β): FP/(TP+FP) = 1 − precision — share of alarms that are false | ↓; **prevalence-dependent**, quoted at `base_rate` |
| `base_rate` | Pooled violation prevalence (the reference at which `fdr` is stated) | — |

β weights recall β²× precision, so higher β ⇒ lower τ ⇒ more alarms (more caught, more false). Deployed β-table (`isaac_CLF2`, base rate 0.263), **default β = 2**:

| β | τ | recall | FDR |
|---|---|---|---|
| 0.5 | 0.56 | 0.50 | 0.34 |
| 1.0 | 0.32 | 0.65 | 0.43 |
| 1.5 | 0.11 | 0.80 | 0.53 |
| **2.0** | **0.06** | **0.86** | **0.59** |
| 2.5 | 0.02 | 0.94 | 0.65 |
| 3.0 | 0.01 | 0.96 | 0.67 |
| 4.0 | 0.01 | 0.99 | 0.70 |

## Regression (nitrate concentration, mg/L)

| KPI | Definition | Direction |
|---|---|---|
| `lofo_r2` / `loso_r2` | R² on family / site OOF (LOFO honest, LOSO optimistic) | ↑ |
| `rmse` | Root mean squared error (mg/L) | ↓ |
| `between_r2` | R² of predicted vs actual per-site means — ranks site levels | ↑ |
| `within_r2` | R² after removing each site's mean — tracks daily movement within a site | ↑ |
| `macro_r2` | Median per-site R² (equal site weight) | ↑ |
