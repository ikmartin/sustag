# xgb-neighbor-count — how many donors, encoded how, ranked how

## The question

Exps 33–36c established that the reach-graph donor block is worth **+0.061 `lofo_r2`** and **+0.083 `lofo_prauc`**, and that the `both` encoding beats `flag`/`signed`. But every one of those arms carried **exactly one donor per direction**, and they confounded the similarity rule with the graph (33 = area + immediate, 36 = distance + full). Nothing has measured whether a 2nd, 3rd or 4th donor adds anything.

This sweep crosses the three axes independently over one candidate set:

- **count** — 1, 2, 3, 4 donors (`nearest_1` is the control that makes "does a 2nd donor help" a clean paired delta)
- **indicator** — `sign` (signed distance, no flag), `flag` (absolute distance + `is_up`), `rednt` (both, redundant on purpose)
- **similarity** — `area` (closest in drainage scale) vs `dist` (nearest along the containment edge)

27 arms per task: `island_<task>` + `nbr_all_no_lag0_{area,dist}` (exps 33/36 unchanged, for continuity) + 24 `nearest_{n}_nbr_{ind}_{rule}`.

## How to run it

```bash
python run_exp.py --task both                       # the real run, 27 arms per task
python run_exp.py --task clf --sites 14 --limit 4   # smoke
python render_results.py latest
```

`xgb_config.json` must exist first — see *One config per task* below.

## Design decisions worth knowing before reading results

**Candidates are the full containment graph's adjacency.** That is not an approximation of "everything reachable": the graph is transitively closed (measured — 0 of 116 sites have a directed-reachable site that is not already a direct edge), so ancestors ∪ descendants at any depth *is* the neighbour list. It also means every candidate pair carries its own D8 flow distance, so no path-summing is needed.

**Siblings are excluded.** Two basins nested in the same parent share no water, so any correlation between them travels through shared weather or land use — both already carried by the crop/surplus/weather blocks. Admitting them would let the donor block act as a land-use proxy dressed as a transport signal. The cost is coverage (see below); the pre-expansion report measured sibling residual Spearman at +0.130 against disconnected +0.006, so this is a deliberate conservative call, not a free one.

**The high slots are sparse, by construction.** Fraction of the 116-site cohort with at least *k* donors:

| ≥1 | ≥2 | ≥3 | ≥4 |
|---|---|---|---|
| 87.9% | 74.1% | 59.5% | **44.0%** |

Median 3, max 19, and **14 sites (12.1%) have no donor at any depth**. So `nearest_4` is over half NaN columns. `num_neighbors` is in every arm so the tree can condition on it, and `eval_<task>.csv` carries per-site `num_neighbors` — **split `nearest_4` by that before concluding anything about a 4th donor.**

**One config per task, shared by every arm.** This is the difference between a readable result and exp 36's. Tuning each arm separately would make a variant's win indistinguishable from a better config, which is exactly what made exp 36 unreadable against exp 33 until it was re-run. `xgb_config.json` is keyed by task, and `run_exp.py` refuses to run without it.

## Files

| file | role |
|---|---|
| `neighbors.py` | the N-nearest donor block; the only new feature code |
| `arms.py` | the 27 column masks per task, derived from the pool |
| `run_exp.py` | pool once → score every arm → `log.json` + `eval_<task>.csv` |
| `render_results.py` | `log.json` → `results_<run_id>.md` |
| `xgb_config.json` | `{"reg": {...}, "clf": {...}}`, written once by the tuner |

## Two things the pool has to work around

`flag` and `rednt` both want a column meaning "distance to donor k" but holding **different values** (absolute vs signed), and the `area` and `dist` rankings put **different sites** in slot k. Neither can share a name in one frame, so the pool carries `{rule}_nbr{k}_dist_abs` *and* `{rule}_nbr{k}_dist_signed` under both rule prefixes, and each arm selects the form it wants. XGBoost never sees names, only the selection.

`arms.build_masks` hard-fails if any named donor column is absent from the built pool. That check is the whole safety margin: `cook` drops unknown columns silently, so a drift between `neighbors.py` and `arms.py` would collapse arms onto `base` and log a winner with a 0.0000 delta and no error anywhere.

## eval_<task>.csv

One row per (arm, site): per-site score (R²/AUC via `cook._per_site_score`, plus rmse/brier/base-rate/n_rows), `basin_area`, `num_neighbors`, `family_n_sites`, `family_area`, `rank_by_nitrate`, `rank_by_area`, `num_sites_training`, `num_sites_testing`, `eval_method`.

`family_area` is the basin area of the family's **max-area member**, verified to strictly enclose every other member in all 11 non-singleton families. Per-site scores are recomputed from the returned OOF vectors at **zero extra fits** — `cook` computes them inside its between/within decomposition and throws them away.

`--eval-methods` defaults to `lofo`. Adding `loso` costs a full second CV pass.

## Caveats

- **Every score predating 2026-07-31 is incomparable**, island and network alike. A D8 pour-point snapping bug corrupted `dist_to_sensor` (a base feature in every recipe) for 28 of 116 sites and `flow_dist_m` for 9 of 54 parents. See [../../notes/D8_bug_fix_explained.md](../../notes/D8_bug_fix_explained.md).
- **`dist_to_nbr` mixes two units on 6.9% of pairs** — D8 flow distance where available, straight-line otherwise. Kept as-is so these arms stay comparable to the 33/36 bases on that channel.
- **20 of 149 containment chains still violate the triangle inequality**, bounded at 1.6% error. Distances to different outlets come from independently-rooted fields. An all-pairs rebuild does *not* fix this — see the notes doc for why — and 1.6% will not flip a donor ranking that a noise floor of 0.003 could detect.
- **`WQS0066`'s outlet is still 4.93× its basin area**, the one site that needed the widened snap band. Treat its donor relationships with suspicion.
