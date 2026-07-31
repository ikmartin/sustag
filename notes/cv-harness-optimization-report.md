# CV harness and tuner optimizations (A–E)

**Record document.** Date: 2026-07-28. Scope: `src/eval/cook.py`, `experiments/feature-manifest/lean_cook.py`, and both `tune.py` scripts.

Tuning had become the wall-clock bottleneck — ~270 s per config on the 123-site pool, so a 16-config stage runs over an hour. Five changes were made; none alters what any metric means, and each is either exactly equivalent or was gated on proving so.

---

## 1. Bottom line

**Nothing measured changes.** A, B, D and E are equivalence-preserving; C changes output only when explicitly switched off, and then only the `loso_*` columns, which become NaN rather than disappearing.

**The one number I cannot yet report honestly is the headline speedup.** The prefix-scan rewrite (A) measured **6.3x/6.7x** in isolation but **1.6x/2.8x** end-to-end — the end-to-end runs were taken while two tuning jobs saturated the CPU, and on a grid with fewer prefixes. The true figure is somewhere between and needs a quiet machine. See §4.

**One candidate was ruled out**: DMatrix reuse via the native API. Building a DMatrix from a 120k x 103 frame costs 150 ms against a 10.2 s fit, so fitting dominates and the refactor would buy ~1%.

---

## 2. What changed

### A — accumulate margins instead of re-predicting each prefix

`predict(iteration_range=(0, k))` walks trees 0..k every call, so scanning ~40 prefixes cost O(sum k). Boosting margins are additive and slices are genuinely cheap — measured, `predict(1080, 1200)` costs 0.88x an equally wide `predict(0, 120)`, confirming `iteration_range` is O(b - a) and not O(b). So each slice is predicted once and cumulative-summed, with the link function applied at the end.

**`base_score` is the trap.** Every `output_margin` prediction carries it, so it must be subtracted once per slice — and for `binary:logistic` it is stored in PROBABILITY space and must be logit-ed first. Using it raw shifts every CLF prefix by exactly `0.25 - logit(0.25) = 1.349`. My first attempt did exactly that and produced plausible-looking, wrong numbers; it was caught only by comparing against the old path. A second trap: `iteration_range=(0, 0)` means "all trees", not "no trees", so it cannot be used to read the base off.

New helpers `_base_margin` / `_prefix_oofs` in both tuners.

### B — hoist the fold slice out of the k loop

Both scanners called `X.iloc[te]` INSIDE the per-k loop: ~40 prefixes x 5 folds = 200 slices where 5 suffice, at 5 ms each on a 180k x 103 frame. Fell out of A's restructuring.

### C — the LOSO pass is now optional, and off in the tuners

`_score_pool` fitted BOTH holdouts on every call, but the site-grouped pass produces only `loso_r2` / `loso_auc` — the leakage gap — while ranking is on LOFO alone. In `src/models/tune.py` it was refit at the full ceiling for every config.

Both `_score_pool` forks take `loso: bool = True`; both tuners default it off per config and run ONE site pass for the winner, fitted at the winner's own `best_k` rather than the ceiling. `--loso` restores per-config behaviour. The manifest screen is unaffected (it keeps the default).

Roughly halves a tuning sweep.

### D — float32 pool

`_pool_wide` now casts float64 feature columns to float32 (the target keeps its dtype, so metric arithmetic is untouched). **Gated on a test, and the test passed exactly**: fold predictions from float64 and float32 pools are bit-identical.

### E — stop pooling twice per train

`train.build` called `compare_many` (pools) then `fit_full` (pools again), building the identical frame twice. `cook_many` and `fit_full` now accept `pool=`, and train pools once.

A guard came with it: `compare_many` **refuses** a prebuilt pool when given more than one recipe, since it forwards `pool` through `**kw` and every recipe would silently be scored on the first recipe's columns.

---

## 3. Measurements

| | measured | conditions |
|---|---|---|
| A speedup, isolated | 6.3x (reg), 6.7x (clf) | 24 prefixes, ceiling 1200, 40k x 12 |
| A speedup, end-to-end | 1.6x (reg), 2.8x (clf) | 16 prefixes, ceiling 800, 30k x 20, **CPU contended** |
| A correctness | max abs diff 1.29e-05 (reg), 1.01e-06 (clf); NaN masks identical | every k in a real scan grid, both tasks |
| B | 5 ms per slice; 200 slices -> 5 | 180k x 103 |
| C | one full CV pass removed per config | verified same column set, only `loso_r2` -> NaN |
| D | fits 10.23 s -> 9.16 s (~11%); pool 99 MB -> 49 MB | 120k x 103, 200 trees |
| D correctness | **max abs diff exactly 0.0** | fold predictions, both tasks |
| ruled out | DMatrix build 150 ms vs 10.2 s fit | 120k x 103 |

Post-change snapshot: 16 keys, no NaNs, `loso_*` present and populated under the default `loso=True`.

---

## 4. Evidence quality

**Measured directly:** every row of the table above. A's correctness was checked per-k against the previous implementation on real fold models, including the CLF case where the base-margin trap bites. D was gated on its test rather than assumed, and passed with exact equality.

**Not established — the end-to-end speedup.** The 1.6x/2.8x figures were taken while a `fulltune` run and a feature-manifest `tune` run were both saturating the machine, on a smaller grid than the isolated benchmark. Neither the 6.5x nor the 2x should be quoted as *the* number. A clean per-config timing on an idle machine is the outstanding measurement.

**Not established — an end-to-end A/B of the harness.** The available `cook_snapshot` baseline predates the whole cook.py rework, so its diff conflates the rework's intended changes (`mean_best_iter` removed, gain moved to the LOFO models, early stopping gone, the `at_f2` rename) with these optimizations. Equivalence here rests on the per-change unit tests above, not on a snapshot pairing. A clean pairing would mean restoring the pre-optimization `cook.py` from backup and snapshotting it.

**Process note.** `py_compile` passed on every one of these changes while real defects remained — a missing `numpy` import, an uninitialised `ops` dict, a stale `early_stopping_rounds`, a dangling `_predict_prefix`. Only importing and running caught them. Separately, edit scripts twice reported success while silently no-opping, because a formatter had reflowed the anchors they matched on; re-grepping the file afterwards is what caught it, not the script's own output.
