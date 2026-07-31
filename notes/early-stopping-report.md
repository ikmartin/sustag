# Early Stopping in cook.py / lean_cook.py: three defects

**Decision document. Read before spending compute on hyperparameter tuning.**
Date: 2026-07-28. Scope: the early-stopping machinery shared by `src/eval/cook.py` and `experiments/feature-manifest/lean_cook.py`, which every scored model in this repo is fitted through — `train.py`, `run_exp.py`, both `tune.py`s, `demo_model.py`.

Found while investigating why `mean_best_iter` came back pinned at its ceiling in the 2026-07-28 tuning sweep. Three separate defects, of which only one was the thing being looked for.

---

## 1. Bottom line

**None of this is leakage. No reported `loso_*` or `lofo_*` number is contaminated, and no historical result needs voiding.** The 85/15 early-stopping slice is carved out of each fold's *training* rows; the held-out sites and families never enter training or the stopping decision. What is wrong is that the stopping rule optimises the wrong objective, so every number in the repo is an honest measurement of a model stopped in the wrong place. The fixes should *raise* scores, which is the opposite of what a leakage fix does.

**The tuning work depends on this and should not go first.** Every config in the grid inherits `early_stopping_rounds=50`, so any optimum found now is optimal *conditional on* a rule that is about to change. Concretely: defect C shifts every score by handing back 15% of the training data; defect B, if fixed, makes `n_estimators` a real ceiling again and destroys the rationale for the `budget` axis entirely; defect A changes which CLF model every config produces. That is 12–14 h of fits across both tasks describing a configuration you intend to replace.

**Defect C is the one to act on regardless of how the rest resolves, and it is a one-line conditional.** `_fold_models` never inspects `early_stopping_rounds`, so it carves the 15% even when early stopping is switched off. For REG — where the rule never fires — that is ~21,673 rows per fold discarded for literally nothing. It also silently invalidates the tuning plan's `--fixed-trees` / `budget` arms, which were designed to measure "early stopping off" and currently measure "early stopping off **and** 15% of the data gone."

**The fix for all three is the same move, and it has been taken (§8a): early stopping is out of the screening CV path, and the tree count becomes an explicit tuned hyperparameter.** It dissolves A, B and C simultaneously, needs no new plumbing, and gives up almost nothing — the rule fired in 0 of 7 REG configs and 2 of 7 CLF configs.

**The cost of the old behaviour is now measured, and it is large.** On one LOFO fold, out-of-family R² peaked at **tree 175** while the watched in-distribution curve was still falling at tree 1498 of 1500. Running to the ceiling gave up **0.0324 R²** — about 4x the 0.0085 REG noise floor. Every screening number in the manifest was produced by a model trained roughly 8.5x longer than it should have been, on 85% of the rows available to it.

---

## 2. The numbers this report uses

| number | what it is | source |
|---|---|---|
| 180,611 | pooled rows in the REG/CLF training pool | `n_rows` in `log.json`'s score dict |
| 122 | sites in the cohort | `n_sites`, same place |
| 30 | basin families (LOFO groups) | `n_families`, same place |
| ~1,480 | pooled rows per site (180,611 / 122) | derived |
| 0.85 / 0.15 | train / early-stopping split inside each CV fold | `cut = int(len(v) * 0.85)`, cook.py:435 |
| 1500 | `n_estimators`, manifest screening config | `lean_cook._DEFAULT_XGB` |
| 3000 / 5000 | `n_estimators` shipped / in `train.REAL_XGB_*` | `cook._DEFAULT_XGB`, train.py |
| 50 | `early_stopping_rounds` — the patience | all three configs |
| 1450 | the firing threshold: `1500 − 50` | derived |
| 0.2328 | CLF base rate (share of rows that are violations) | `base` in the CLF score dict |

**How the stopping rule is assembled.** There is no hand-written stopping logic anywhere — it is XGBoost's built-in, spread across four points: `early_stopping_rounds=50` in `_DEFAULT_XGB`; the watched metric in `_model` (cook.py:135-136); `eval_set=[(Xval, yval)]` in `_fit` (cook.py:149); and what goes *into* `Xval`, in `_fold_models` (cook.py:434). The rule is: add rounds one at a time, score `eval_metric` on `eval_set[-1]`, and stop when it has not improved for 50 consecutive rounds. `best_iteration` records the best round, 0-indexed, so trees used is `best_iteration + 1`.

---

## 3. Defect A — the watched metric is not the graded metric (CLF only)

`_model` hardcodes `eval_metric="logloss"` for CLF and `eval_metric="rmse"` for REG. Neither is overridden anywhere in the repo. The graded metrics are `lofo_r2` and `lofo_prauc`, where `prauc = average_precision_score(y, pred)` (cook.py:204, 241).

**REG is fine, and this matters for the diagnosis.** `R² = 1 − MSE/Var(y)`; on a fixed set of rows `Var(y)` is constant, so the tree count minimising RMSE is exactly the one maximising R². Watched and graded rank tree counts identically. There is nothing to fix on the REG side.

**CLF is not.** Logloss is a proper scoring rule averaged over every row, and at a base rate of 0.2328, 76.7% of rows are negatives — so logloss is dominated by them. Average precision is a property of the ranking of the positives alone and is invariant to any monotone rescaling of the scores. A model can lower logloss materially by sharpening already-correct negatives while leaving the positive ranking untouched or worse. The round logloss prefers is not the round average precision prefers.

**Why it is a defect.** The stopping rule selects the model, on a quantity the project has explicitly decided is not the one that matters — `lofo_prauc` was made the primary CLF headline on 2026-07-28 precisely because average precision is what counts for a rare-event flag.

**Fix.** `eval_metric="aucpr"` on the CLF branch. One line; REG untouched. Honest caveat: XGBoost's `aucpr` is an interpolated area under the PR curve and is **not** numerically identical to sklearn's step-wise `average_precision_score`. They move together and it is a large improvement on logloss, but it is a proxy, not the same number.

---

## 4. Defect B — the early-stopping slice is drawn from the same sites as training

Inside each CV fold, `_fold_models` permutes the fold's training rows uniformly and takes the last 15% as the early-stopping set (cook.py:433-438):

```python
v = rng.permutation(np.asarray(train))
cut = int(len(v) * 0.85)
m = _fit(task, X.iloc[v[:cut]], y.iloc[v[:cut]], X.iloc[v[cut:]], y.iloc[v[cut:]], **xgb_kw)
yield test, m
```

The permutation is over **rows**, with no reference to site or family. With ~1,480 rows per site, the chance a given site contributes *no* rows to the 15% slice is `0.85^1480 ≈ 10^-105`. Every site is on both sides, with certainty.

### The mechanism is site memorisation, not temporal interpolation

The obvious story — the model interpolates day *d* from day *d−1* — is wrong, and the feature list disproves it. **The 14-feature base recipe contains no own-site lagged nitrate.** Its only temporal inputs are `rest_of_state_nitrate_lag1/lag2`, which are the statewide mean *excluding* the site and therefore near-identical across sites on a given date, plus `doy_sin/doy_cos`. Ranked by gain:

```
rest_of_state_nitrate_lag1  16.4%      tile_frac_ag           6.2%
tile_frac_basin             13.8%      max_dist_to_sensor     5.7%
tot_tiles92                 11.9%      rest_of_state_lag2     5.5%
mean_dist_to_sensor          9.6%      tot_contact            5.1%
lat                          8.0%      lon                    4.7%
tot_bfi                      6.4%      log_basin_area         3.7%
                                       doy_sin + doy_cos      3.0%
```

Ten of the fourteen are **constant within a site**, and together they are **75.1% of total gain**. Ten continuous statics uniquely fingerprint all 122 sites, so a tree can partition on that fingerprint and fit each site's own nitrate level directly.

That is precisely what an in-distribution slice rewards. The slice holds the same sites, so a memorised site→level mapping scores well on it and the watched metric keeps improving. On a held-out *family* the fingerprint values are new, the memorised partition contributes nothing, and it may actively mislead. This is consistent with the decomposition already reported: `lofo_within_r2` sits at 0.37–0.39 across the whole tuning grid while `lofo_between_r2` — the cross-site level ranking that memorisation fakes — spans −0.07 to +0.15.

**Why it is a defect.** The stopping rule faithfully answers the question it was asked: *"am I still improving at predicting more days from sites I have already fingerprinted?"* The honest answer is yes, effectively forever. The question the score asks is *"am I still improving at predicting an unseen basin?"*, which turns negative far earlier. Same code, same metric, different population, opposite answer.

### The evidence that it never fires

Stopping halts only after 50 consecutive rounds without improvement, so against a 1500 ceiling it beats the cap only when `best_iteration + 1 ≤ 1450`. Measured `mean_best_iter` — the mean over the 5 LOFO fold-models of `best_iteration + 1`:

| task | values | fired |
|---|---|---|
| REG | 1500.0 in all 7 configs | **0 of 7** |
| CLF | 1338.4 (depth 3, lr 0.05), 1040.6 (depth 4, lr 0.05) | fired |
| CLF | 1499.8, 1499.4, 1496.2, 1492.8, 1488.0 | **did not** — best round was near the end, but 50 rounds of patience never elapsed |

**REG isolates the cause.** REG has no metric mismatch at all, and still never stopped in any config. A correct metric measured on the wrong population does not stop. Fixing defect A would therefore not fix the pinning; only B does.

**Consequence.** `n_estimators` is not a safe ceiling — it is the operative regulariser. And because every config runs the full tree budget, `learning_rate × n_estimators` collapses into a single axis: what looked like a learning-rate effect in the REG sweep (lr 0.02 beating 0.05 at every depth) is a *total shrinkage budget* effect, monotone in `lr × 1500`.

---

## 5. Defect C — the 15% is withheld even when early stopping is switched off

`_fold_models` never inspects `early_stopping_rounds`. The carve runs unconditionally, so a config with `early_stopping_rounds=None` still trains on 85% of its fold's rows and passes an `eval_set` that nothing consumes. Confirmed by fitting both ways and observing identical behaviour.

**Scope: the CV path only.** `fit_full` already gets this right — `need_val = has_es or extra_importance_test` (cook.py:1031) — so the deployable-model path carves only when something will use the slice.

**Why it is a defect.** Each of the 5 GroupKFold folds trains on roughly `180,611 × 0.8 = 144,489` rows, of which `144,489 × 0.15 ≈ 21,673` are discarded. For REG, where the rule never fires, those rows buy nothing whatsoever. Worse, it invalidates the comparison the tuning plan was built on: the `--fixed-trees` / `budget` arms exist to measure "what happens with early stopping off", and today they measure "early stopping off **and** 15% of the data gone".

**Fix.** Carve only when `early_stopping_rounds` is truthy; otherwise fit on all of the fold's training rows with no `eval_set`. This mirrors what `fit_full` already does.

---

## 6. Recommendation

**Primary — remove early stopping from the CV path and tune the tree count.** `early_stopping_rounds=None` with a tuned `n_estimators` dissolves all three defects at once: no watched metric to mismatch (A), no slice drawn from the wrong population (B), and no rows withheld once C is fixed. It needs no new plumbing, it is deterministic and reproducible, and the tuning plan already carries the `budget` / `trees` axis that supplies the count. The evidence says little is given up: the rule fired in 0 of 7 REG configs and 2 of 7 CLF configs.

**Alternative, to measure rather than assume — a group-aware slice.** If adaptive per-fold stopping is still wanted, the slice must be carved by whole **sites** or whole **families**, not by row. Site-grouping leaves ~98 groups in a fold to draw ~15 from, giving a stable stopping curve. Family-grouping matches the LOFO holdout the headline is computed on, but a fold's training set holds only ~24 families, so a 15% carve is ~4 of them — and a stopping metric measured on 4 families with patience 50 may stop on noise. Real trade-off; decide it on evidence.

**Implementation shape.** Add an `es_split` parameter to `_fold_models` (and lean_cook's `_grouped_models`) taking `"row"` (current behaviour, **the default**) / `"site"` / `"family"` / `"none"`, plus the group labels it needs. Defaulting to `"row"` means no historical number moves until we choose to move it — which is what makes a before/after honest, and is required by CLAUDE.md's rule that a baseline must be snapshotted from the working tree rather than recovered from HEAD. `folds_grouped` and `basin_groups` already exist for the labels; `sklearn.model_selection.GroupShuffleSplit` gives the carve.

**Independently:** `tune._ceiling_report` tests `mean_best_iter ≥ 0.98 × n_estimators` (=1470 at a 1500 ceiling). That threshold is arbitrary. The exact test is `mean_best_iter > n_estimators − early_stopping_rounds` (=1450) — the condition under which the patience cannot have elapsed.

---

## 7. The measurement that decides it

One pool, the shipped recipe, full cohort, arms differing only in the early-stopping treatment. Report `lofo_r2` / `lofo_prauc`, `lofo_between_r2`, `lofo_within_r2` and `mean_best_iter` per arm.

| arm | `es_split` | early stopping | isolates |
|---|---|---|---|
| 1 | `row` | on | today's baseline |
| 2 | `none` | off, `n_estimators=1500` | defect C alone — the +15% training data |
| 3 | `site` | on | defect B at site granularity |
| 4 | `family` | on | defect B at family granularity |
| 5 (CLF) | `family` | on, `eval_metric="aucpr"` | defects A+B together |

Arm 2 vs 1 gives what C alone buys. Arms 3/4 vs 1 give whether a group-aware slice makes stopping fire, and at which granularity. Arm 5 vs 4 gives what the metric fix adds on top. **If arms 3/4 do not beat arm 2, the primary recommendation stands and the group-aware machinery can be dropped entirely.** Cost: 5 arms × 1 config on the full cohort, roughly 1.5–2 h per task.

---

## 8. The divergence test — RUN, and it confirms §4

One LOFO fold, 45 sites, 21 families, base recipe, lr 0.03, ceiling 1500. All **38 of 38** training sites appeared on both sides of the row-wise slice, as predicted. Early stopping disabled so the model ran the full budget and every prefix could be scored via `iteration_range=(0, k)`.

**Test 1 — base recipe, 14 features.** The two curves diverge sharply:

| curve | behaviour |
|---|---|
| in-distribution RMSE (what the rule watches) | best at tree **1498 of 1500** — still falling at the ceiling |
| out-of-family R² (what we grade on) | **peaks at tree 175 (+0.3497)**, then degrades to +0.3173 |

```
  25: +0.088    175: +0.350    325: +0.313    475: +0.300    625: +0.301
 775: +0.305    925: +0.313   1075: +0.313   1225: +0.313   1375: +0.317
```

**0.0324 R² is given up by running to the ceiling** — roughly 4x the 0.0085 REG noise floor — and the model runs ~8.5x longer than it should. §4's claim is confirmed: a stopping rule validated in-distribution cannot see the point where transfer starts degrading, because on its own slice nothing degrades.

Note the curve is **not unimodal** — it falls to +0.300 by k=475 and recovers to +0.317 by k=1375. That rules out bisection as a way to pick the tree count.

**Test 2 — static fingerprint removed. Weaker than expected.** Dropping the ten site-constant features cut the absolute divergence from 0.0324 to 0.0099, which looks like strong support. But it also cut peak R² from 0.3497 to 0.1335. **As a fraction of peak performance the divergence only moves 9.3% → 7.4%** — a modest reduction, not the collapse the memorisation story predicts.

So: the divergence is real, large, and measured. The *attribution* to static-fingerprint memorisation specifically is **not** established by this test, and §4's mechanism should be read as a plausible account rather than a demonstrated one. Distinguishing it properly needs the permutation variant (shuffle the static block across sites, holding feature count fixed) so that overall signal strength is not confounded with the channel being tested.

**Caveats:** one fold, 45 sites, and one hyperparameter setting. `k*` will move with depth and learning rate — which is precisely why the tuner must resolve it per config rather than adopt 175 as a constant.

## 8a. Decision taken (2026-07-28)

Early stopping is **removed** from the feature-manifest screening path: `lean_cook._DEFAULT_XGB` sets `early_stopping_rounds=None`, and `es_split` defaults to `"none"` so each fold fits on 100% of its training rows with no `eval_set`. This resolves A, B and C at once.

The group-aware slice of §6 was **rejected without being measured**, on the grounds that basin families vary enormously in size (one is ~25% of pooled rows), so a stopping signal computed on 2–4 unequal families would be unreliable. The five-arm measurement of §7 was dropped with it.

The tree count instead becomes an explicit tuned hyperparameter, resolved per config by a prefix scan in `tune.py` — one fit per fold at a ceiling, then score every prefix. `src/eval/cook.py` is unchanged and still carries `early_stopping_rounds=50` and the row-wise carve on the shipped path.

## 9. Evidence quality

**Measured directly, in this repo, this session:**

- `mean_best_iter` per config — REG 1500.0 in all 7; CLF 1499.8 / 1499.4 / 1496.2 / 1492.8 / 1488.0 / 1338.4 / 1040.6. Read from `tune_reg.csv` and `tune_clf.csv`, produced by Isaac's own runs.
- The unconditional carve (defect C) — fitted at `early_stopping_rounds=50` and `=None` and confirmed identical 85/15 behaviour.
- `_mean_best_iter` equivalence to the helper it replaced — matched to 1e-9 on synthetic folds. Early stopping *does* fire on easy synthetic data (89.4 trees of a 400 ceiling), so the pinning on real data is a property of the data, not a code artifact.
- The base recipe's 14 features and their gain shares, including the 75.1% static total — from `log.json`.
- 180,611 rows / 122 sites / 30 families / 0.2328 base rate — from `log.json`'s score dict.

**Derived arithmetic, not measured:**

- `0.85^1480 ≈ 10^-105` — a consequence of the uniform draw given ~1,480 rows/site. That rows/site figure is itself an average (180,611 / 122); per-site counts were not inspected, so a genuinely short site would have a larger (still negligible) absence probability.
- ~21,673 discarded rows per fold — `180,611 × 0.8 × 0.15`, where 0.8 is GroupKFold(5)'s nominal train share. Folds are balanced by group, not by row, so this is approximate.
- The 1450 firing threshold — exact given `n_estimators` and `early_stopping_rounds`, but applied to a *mean* over 5 folds, so a config near the boundary could mix fired and non-fired folds.

**Reasoned, and explicitly NOT yet tested:**

- **That site memorisation via the STATIC FINGERPRINT specifically is the channel.** §8 test 2 was supposed to establish this and did not: dropping the ten site-constant features cut absolute divergence 0.0324 → 0.0099, but also cut peak R² 0.3497 → 0.1335, so as a share of peak the divergence barely moved (9.3% → 7.4%). The mechanism in §4 remains a plausible account, not a demonstrated one. The clean test is the permutation variant — shuffle the static block across sites so the fingerprint is uninformative but the feature count and overall signal are held fixed.

**Promoted to measured by §8 (was reasoned):**

- **That the watched curve and out-of-family performance diverge.** Directly measured: in-distribution RMSE still falling at tree 1498/1500 while out-of-family R² peaked at 175 and gave back 0.0324. One fold, 45 sites, one hyperparameter setting — the direction and rough magnitude are solid, the exact `k*` is not, and it will move with depth and learning rate.
- **That the out-of-family curve is not unimodal**, which is why the tree count is chosen by a full prefix scan rather than by bisection.
- That group-aware stopping on ~4 families would be noisy. Plausible from the group count; unmeasured.
- That XGBoost's `aucpr` tracks sklearn's `average_precision_score` closely enough to serve as a proxy. Directionally safe, not verified numerically.

**Superseded within this investigation:** an earlier explanation attributed defect B to day-to-day nitrate autocorrelation exploited through a lagged feature. That is wrong — the base recipe has no own-site lagged nitrate — and is replaced by the static-fingerprint mechanism above.
