# Island / Network Model Split — Report

**What happened:** the shipped recipe pair became four recipes and four models. `island_REG` / `island_CLF` carry only features computable at an arbitrary ungauged pin; `network_REG` / `network_CLF` add a reach-graph donor block that needs a resolvable up/downstream monitored neighbour. Written 2026-07-30. Companion to [future-work-report.md](future-work-report.md), [extra-in-situ-report.md](extra-in-situ-report.md) and [discrete-sample-report.md](discrete-sample-report.md).

**Most of this document is about what has NOT been done.** The code landed; the decisions it is waiting on did not.

---

## 1. Bottom line

- **The split is implemented and verified.** `network_*` emits a strict superset of `island_*` — REG +8 columns, CLF +14 — and both recipes share one aggregation pass rather than forking it.
- **Both network models are UNTUNED and cannot be fitted.** `_tuned_iters` raises `UntunedRecipe` rather than defaulting, so `train.py both` trains the two island models and stops loudly on the network pair. That is the intended end state of this work, not an oversight.
- **Six gates remain**, of which one is running, one is a one-line experiment, and one is a product decision nobody has made. §3 is the suggested order.
- **12% of the current cohort (14 of 116 sites) has no resolvable donor at all.** Whether that number holds for real map pins is unknown and is the single most load-bearing unmeasured quantity in this plan — it decides whether `network` is the primary model or the exception.

---

## 2. What changed, and the one idea behind it

**A feature is island-legal if it is computable at an arbitrary ungauged pin.** Everything below follows from that sentence.

Statewide aggregates qualify — `rest_of_state_nitrate_lag1` needs a monitoring network but no *nearby* gauge, and it is the strongest single feature in both tasks. A named up/downstream donor does not qualify: at a pin with no neighbour, the block is undefined. So it cannot simply join the shipped recipe, and the island/network gap becomes a second, useful number — what a nearby gauge is worth, which is a siting argument as much as a modelling one.

| | |
|---|---|
| `recipes.py` | `recipe_REG`/`recipe_CLF` → `island_REG`/`island_CLF`; new `network_REG`/`network_CLF`; `_NBR_*` constants per task |
| `features.py` | new `neighbor_nitrate(site, lags, select, immediate, encoding)`, promoted from `exps.py` with its six bug fixes intact |
| `train.py` | `_TASKS` → `_RECIPES` keyed on `(task, variant)`; `--recipe {island,network,both}`; `task` gains `both`; `--name` is now a PREFIX; filesystem-based collision numbering |
| `tune.py` / `fulltune.py` | `_TASK_SPEC` keyed on `(task, variant)`; `--variant` |
| `tune_threshold.py` | repurposed — solves for a target recall or FDR exactly, instead of only rebuilding a table `train.build` already writes |
| `cook.py` | `save_model` records `recipe` in the sidecar |

Three choices worth keeping in mind because they are not obvious from the diff:

**`lag0` lives in `features.py` but not in the recipes.** `neighbor_nitrate` will emit a same-day donor column if asked; `_NBR_LAGS_REG`/`_CLF` = `(1, 3)` is where that is declined. The capability and the policy are deliberately separate, because the policy depends on G3 (§3) and the policy is the part that will change.

**Collision numbering reads the filesystem, not the log.** A logged model may have been deleted; consulting `fulltrain_logs.json` would refuse to reuse a name whose artifact is gone. What must never be clobbered is a booster on disk.

**Encodings differ by task on measured grounds.** CLF uses both directions (exp 33c beat 34c by +0.0175 `lofo_prauc`, ~5× the 0.0037 floor); REG uses a single flagged neighbour, because exps 33/34/35 separated by less than the 0.0085 REG floor and one donor costs half the columns.

---

## 3. Suggested workflow for resolving the gates

Gates are neither independent nor equally urgent. Read this as a dependency order, not a checklist.

```
G1  exp 36/36c ── RUNNING ──┐
                            ├─► add nbr_all_no_lag0 (DONE), re-run 33–36c ──► G2
                            │        │
                            │        ├─ no_lag0 ≈ nbr_all   ─► lag0 is free to drop. Ship (1,3). Gate closed.
                            │        └─ no_lag0 << nbr_all  ─► G3 becomes load-bearing: the deployment mode
                            │                                  must be decided BEFORE the lag tuple is fixed
                            └─► winner sets _NBR_SELECT_* / _NBR_IMMEDIATE_*

G4  seed replication ─ only for a between_r2 number you intend to quote. Not a blocker.
G5  in-situ E1       ─┐ orthogonal to the above; can run any time
G6  discrete E2      ─┘ G6 may belong in ISLAND too — the only candidate block that helps the fallback path
```

**G1 — which selection rule. STILL OPEN, and exp 36 as run does not answer it.** 36/36c repeat 33/33c with distance-based selection on the full containment graph instead of drainage-scale selection on the immediate graph. Exp 36 (reg) completed 2026-07-30T22:46Z; **36c never logged.**

Exp 36 is **not comparable to exp 33**: `RECIPE_XGB["island_REG"]` was updated with a fulltune winner between the two runs, so 33 fitted at `lr 0.05 / depth 5 / 480 trees / subsample 0.7` and 36 at `lr 0.01 / depth 6 / 2700 trees / subsample 0.5`. The proof is the `base` arm, which has byte-identical columns, rows and folds in both and must therefore score identically: it moved `lofo_r2` 0.4489 → 0.4547 and `lofo_between_r2` 0.4337 → 0.4740 (**1.6× the 0.0258 floor**) on the config change alone. Absolutes favour 36 (`lofo_r2` 0.5107 vs 0.5080) while every delta-vs-base favours 33 (+0.0591 vs +0.0561); both gaps are inside the 0.0085 headline floor, and neither reading is trustworthy because the delta itself shrinks as the base strengthens.

Note this affects **only exp 36**. 33/34/35 all ran at one shared config, as did 33c/34c/35c, so the encoding comparison between them stands.

**Next step:** re-run 36 and 36c with `RECIPE_XGB` pinned to the values 33/33c used, in the same pass as the `nbr_all_no_lag0` arm, so G1 and G2 close together. **Do not paste a fulltune winner into `RECIPE_XGB` before that run launches** — `run_exp._xgb_config` reads it at import, which is exactly how this confound arose. **Fallback if 36/36c stay inconclusive:** keep 33/33c's rule, which is what is coded today (`area`, `immediate=True`).

**G2 — what the deployable block actually scores.** The `nbr_all_no_lag0` arm is implemented (one line in `_neighbor_ablation.masks`, applying to all four variants) but has never been run. Nothing currently measures the column set a recipe could ship: `nbr_all` includes `lag0`, and `nbr_lagged` drops level/twohop/cover along with it. Read against `nbr_all` and `nbr_concurrent` it decomposes cleanly — total effect, same-day-only effect, and what survives without same-day. **Cost:** one re-run of eight experiments. Retuning first is unnecessary; the pre-fulltune config gap is too small to reorder the encodings.

**G3 — forecast or nowcast.** *This is a product decision, not an experiment, and it is not currently anyone's.* It determines whether `lag0` is legal:

- as a **forecast** (predict tomorrow), the donor's day-*T* reading does not exist yet — `lag0` is unavailable outright;
- as a **nowcast** (estimate today's nitrate at an ungauged pin off the live network), it is available.

A softer caution holds either way: `lag0` is contemporaneous with the target, so it can be carried by one storm falling on both basins rather than by transport down the reach — which makes it hostage to donor telemetry arriving with no latency, an operational assumption nobody has checked. G3 only becomes urgent if G2 shows `lag0` is expensive to give up.

**G4 — seed replication.** The headline effects (+0.0591 REG `lofo_r2`, +0.0790 CLF `lofo_prauc`) are 7× and 21× their noise floors and need no replication. The between-channel claims (+0.0735 `between_r2`, +0.1320 `between_rate_r2`) are 2–3× the 0.0258 REG `between_r2` floor — replicate before quoting either in writing.

**G5 / G6 — the preliminary blocks.** Both have built, run pre-checks and unrun CVs. **Read the coverage-only arm first in each** (`q_coverage`, `grab_coverage`): station density tracks population, road access and project funding, all correlated with land use, so a coverage arm that carries the effect means the block is a sampling-effort fingerprint rather than a measurement. See §5.

---

## 4. Future work

### 4.1 Retune all four models

Both `network_*` recipes are untuned. `RECIPE_XGB` currently holds only the island pair, on the cheaper configs (REG 480 trees / depth 5, CLF 580 / depth 3).

Widen the ladders before trusting a winner: the last REG fulltune settled at the **top of both the depth and the lam ladder**, which means the search was truncated, not that an optimum was found. `experiments/feature-manifest/tune.py` now has `--automatic-expansion` for exactly this (walks one rung past an edge and re-scores while the steps keep paying); the same treatment has not been ported to `src/models/tune.py`.

### 4.2 The deploy problem

**There is no model-selection seam anywhere.** `deploy/predict.py:22-23` hardcodes one model name per task and `widget/model_interface.py:47-48` calls `load_model(task=...)` unconditionally. Four models cannot be served through that.

**Per-pin neighbour resolution does not exist.** `get_basin_graph` is a build-time edge list over `get_site_ids()`, and a pin is not a node in it; `widget/geo_utils.get_downstream_sites` is a stub returning `[]`. Serving `network_*` at a pin requires, per request:

1. point-in-polygon of monitored sensors inside the pin's delineated basin (upstream — cheap);
2. which monitored basins contain the pin (downstream — **nothing does this today**);
3. transitive reduction, so "nearest" means nearest-*enclosing* rather than any enclosing;
4. a live D8 flow distance — seconds per call, and already NaN on ~7% of gauged edges.

Two smaller items belong with that work: `feature_mismatch` is defined at `predict.py:64` and **called nowhere**, so `predict`'s `reindex` silently NaN-fills any missing column — harmless with one feature set, dangerous with two. And `save_model` now records `recipe`, which makes wiring a real check trivial.

### 4.3 The sequence that precedes deploy

1. expand the bounding box to its final position
2. rebuild all data on it
3. rework pin dropping so pins snap to **stream order ≥ 4**
4. **run statistics over valid pin locations.** Two numbers come out, both load-bearing: *what fraction of legal pins has a resolvable donor* (decides whether network is the primary model or the fallback), and *forecast vs nowcast* (this is **G3**)
5. adjust the recipes to what that shows — including `_NBR_LAGS_*`
6. retune all four
7. build the deploy, including the model-selection seam

Step 4 is the hinge. Everything about the split is currently justified by a 12% figure measured on **gauged cohort sites**, which are not a random sample of map pins — they sit where someone chose to instrument, which correlates with being on a monitored reach.

### 4.4 The per-site grid cache needs one rebuild pass

`src/data/processed/grids/` is currently **not being used** — every site falls back to live computation, which is correct but slower. This is deliberate: the cached artifacts written 2026-07-27 disagreed with a live rebuild on `frac_cell_in_basin` for every *boundary* cell (interior cells matched exactly), a sub-metre shift in the reprojected basin outline that the mtime-based staleness check could not see. `site_grid_is_current` now also compares a `_build_env.json` provenance stamp, which is absent, so the whole cache reads as stale.

**Run `python -m src.build._make_site_grids --force` when the machine is free.** It rewrites the grids and stamps the provenance, after which the cache is used again. Nothing is wrong in the meantime; live computation is the ground truth the cache is supposed to reproduce, and the discrepancy was 9.4e-7 relative on `basin_area` — far under any noise floor.

### 4.5 Deferred documentation

The rename left stale text behind, deliberately, to be swept once the project stabilises: `src/README.md` (9 code blocks), notebook markdown in `fulldemo.ipynb`, `kpis.md`, and comment/docstring mentions in `virtual_recipes.py:30`, `demo_model.py:120`, `demo_recipes.py:94`, `exps.py:188/209/233`. Historical log keys (`recipe_REG2` … `recipe_CLF4`) are left alone permanently — they are real entries in `fulltrain_logs.json` and `model_scores("recipe_REG2")` still resolves.

Separately: **`notebooks/demo_model.py:23` is broken** and predates this work — it imports `_grouped_models` from `src.eval.cook`, which is now `_fold_models`.

### 4.6 Two measurement cautions

**The island/network comparison is not apples-to-apples on the full cohort.** Network can only be scored where a donor exists (~88% of sites). Comparing pooled scores across different row sets conflates "the feature helps" with "sites with neighbours are easier". Report the gap **on the donor-having subset, with island recomputed on that same subset.**

**Network's LOFO answers an easier question than island's.** Reach neighbours and basin families are both built from containment, so a held-out site's donor is usually held out too. This is *not* training leakage — the donor's nitrate is observed, never predicted — but it does mean the two models' LOFO numbers are not strictly comparable. Say so wherever they appear together.

---

## 5. Preliminary blocks, not implemented

**In-situ covariates — network only (G5).** A donor gauge is precisely what island lacks by definition, so this block can never help the fallback path. `experiments/in-situ-pre-check/` is built and its data steps have run: 1,192 instantaneous-value discharge gauges in footprint, **97% donor coverage at every exclusion radius** after rank fallback, 235 donors at a median 18.6-year span. Hold the prior from `future-work-report.md`: *perfect* specific discharge measured +0.001, and a C–Q exponent of b = +0.61 means Iowa nitrate mobilizes rather than dilutes, so discharge is not the missing variable it looks like. Water chemistry is not a candidate at 12–16% coverage within 10 km.

**Discrete samples — possibly BOTH models (G6).** `experiments/discrete-sample-pre-check/` is built and audited. Agency-only basin aggregates correlate **ρ = +0.53** with each site's own long-run sensor level. The volunteer pool must be excluded: it anti-correlates at **−0.42** once both sides have ≥50 samples, and pooling drags +0.53 → +0.39. Coverage is 57% of basins.

This block **could be island-legal** — a static aggregate over third-party historical samples is computable at any delineated pin without the pin being instrumented. If E2 moves `between_r2`, it belongs in island as well as network, which would make it the only new block that improves the no-donor fallback. That is worth more than its effect size suggests.

Open question: ρ = +0.53 is against the sensor level **in isolation**, not against what crops, surplus and tile drainage already explain. Only E2's paired `base` arm answers whether it adds anything.

---

## 6. Evidence quality

**Measured directly, this session:**
- `network_*` is a strict superset of `island_*`: REG 83 → 91 columns, CLF 118 → 132. All four experiment variants' `nbr_all_no_lag0` arm equals `nbr_all` minus the concurrent block exactly.
- 14 of 116 cohort sites (12%) resolve no donor in either direction on the immediate graph. On a donor-less site every donor column is NaN except the neighbour *count*, which is legitimately 0.
- Mutating `_NBR_ENCODING_REG` or `_NBR_LAGS_REG` mid-process changes the emitted columns rather than serving a stale cache.
- `xgb_for(network_REG, "reg")` raises `UntunedRecipe`; `island_REG` resolves to 480 trees.
- Collision numbering reclaims a freed name after its `.json` is deleted, with the log entry still present.
- `src/selftest.py`: 5 pass, 2 fail. **Both failures (`virtual_equals_real`, `splits`) are pre-existing and outside this work** — they exercise `build_virtual_site_data`/`get_data` and `conflict_graph`, none of which was touched, and both are consistent with the in-progress data rework. The one test covering this work, `features`, passes.

**Measured in earlier experiments (33–35c), at the PRE-fulltune config:**
- +0.0591 REG `lofo_r2`, +0.0790 CLF `lofo_prauc`; `between_r2` +0.0735, `between_rate_r2` +0.1320.
- 33c beat 34c by +0.0175 `lofo_prauc`; 33 ≈ 34 on REG, inside the floor; 34 ≈ 35.
- Noise floors: REG headline 0.0085, CLF headline 0.0037, REG `between_r2` 0.0258.

**Not measured — assumed, and the assumption matters:**
- That the 12% donor-less rate on gauged sites transfers to arbitrary map pins. It probably does not; gauges sit where someone chose to instrument. §4.3 step 4 is what settles it.
- That the pre-fulltune config the encodings were ranked at does not reorder them after retuning.
- That `nbr_all_no_lag0` retains most of `nbr_all`'s effect. This is the entire premise of shipping a causal-lag-only recipe and **it has never been run.**
