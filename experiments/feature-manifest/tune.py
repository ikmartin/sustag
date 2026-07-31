"""tune.py -- grid-search XGBoost hyperparameters against the feature-manifest WIDE pool.

The manifest's screening configs (lean_cook._SCREEN_XGB_* / _FULL_XGB_*, one per task and regime) were hand-picked. This picks them instead, against the honest metric: every config is scored by LOFO -- lofo_r2 for regression, lofo_prauc for classification -- with LOSO reported alongside so the leakage gap is visible.

Same optimization as run_exp.py: the wide pool is the expensive part, so it is built ONCE per task and every config re-uses the same X / y / folds. A config therefore costs only its CV fits, and because all configs share one pool the comparison is exactly paired.

WHICH COLUMNS. The wide recipe emits every candidate column, most of which the model never ships. `--cols` picks the mask to tune on:
    full  (default)  the full_feature_set recipe -- base + the canonical encodings, i.e. the model whose hyperparameters actually matter
    base             the fixed 14-feature base alone
    all              every column the wide recipe emits (the stress case: maximum noise columns)

THE TREE COUNT IS NOT AN AXIS -- it is resolved per config. Early stopping used to choose it, and chose it badly (see below), so every config now fits each fold once at a `--ceiling` and scores every PREFIX of that fit on the holdout. Each config therefore reports the tree count it actually wanted (`best_k`), its score there, and `k_frac = best_k / ceiling` -- a k_frac near 1.0 means the ceiling is binding rather than chosen, and should be raised. This costs the same two CV passes scoring already paid, versus ~log2(ceiling) x n_splits fits for a bisection, and unlike a bisection it cannot be fooled by the measured curve, which is not unimodal.

WHICH AXES. `--search` names the axes crossed into the grid; every other axis takes its value from `--fix`, else from the inherited default. Staging is therefore a sequence of INVOCATIONS, not edits. Axes: depth, lr, lam, mcw, subsample, colsample.

    # Stage 1 — capacity x learning rate
    python tune.py --task reg --cols full --search depth,lr --ceiling 1500
        -> read best config AND k_frac. k_frac > ~0.8 means the ceiling is binding, not chosen: raise --ceiling to 3000 and repeat before trusting anything.

    # Stage 2 — the one genuine cartesian, at stage 1's lr
    python tune.py --task reg --cols full --search depth,lam --fix lr=0.02 --append

    # Stage 3 — coordinate, each pinning everything settled so far
    python tune.py --task reg --search mcw       --fix depth=4,lr=0.02,lam=0.1 --append
    python tune.py --task reg --search subsample --fix depth=4,lr=0.02,lam=0.1,mcw=0.02 --append
    python tune.py --task reg --search colsample --fix depth=4,lr=0.02,lam=0.1,mcw=0.02 --append

    # Stage 4 — confirm the neighbourhood, then de-bias with seeds
    python tune.py --task reg --search depth,lam --depths 3,4,5 --lams 0.02,0.1,0.3 --fix lr=0.02,mcw=0.02 --seeds 0,1,2 --append

WHY THE AXES ARE NOT ALL INDEPENDENT. Two regimes, and the grid design follows from them:

- `max_depth` x {`reg_lambda`, `min_child_weight`} are strongly coupled in RAW units, because both regularizers compete against a leaf's accumulated Hessian and that scales as N/2^depth. Fixing lambda at one depth and then optimizing depth would be a genuine mistake -- you would never visit (deep, large-lambda). Hence they are expressed RELATIVELY (below) and crossed with depth anyway.
- `min_child_weight` x `reg_lambda`, and the sampling fractions against everything else, are only weakly coupled -- one blocks splits, the other shrinks the leaves of splits that survive. Coordinate descent is sound for these.

`learning_rate` and `n_estimators` USED to be a third, totally-coupled regime -- with early stopping never firing, every config ran its full budget, so only the product `lr x n_trees` mattered and a `budget` axis was the honest way to sweep it. Resolving the tree count per config dissolves that: lr is a learning rate again and the optimal count simply adapts to it.

RELATIVE UNITS. `--lams` and `--mcws` are given as a FRACTION of the expected per-leaf Hessian, not in raw XGBoost units, and are converted per-config once depth is known. Leaf weight is -G/(H+lambda), so lambda = c x H yields a shrink of 1/(1+c) at EVERY depth: the axis becomes depth-invariant, which is what lets a modest cartesian cover the space. Two consequences worth knowing:

- It exposes how inert the historical values were. reg_lambda=5 at depth 4 is c=0.0009 -- a 0.09% shrink. min_child_weight=10 is 0.2% of a typical leaf. Nothing in the 2026-07-28 sweep was ever going to move.
- It unifies the REG and CLF ladders. REG's Hessian is 1/row; CLF's is p(1-p), ~0.179 at the observed base rate, so the same relative c produces the ~5.6x smaller absolute CLF value automatically. One ladder, both tasks. `--absolute` opts out and passes the numbers through untouched.

EARLY STOPPING IS GONE (2026-07-28), which is why the tree count needs choosing here. lean_cook carved its 15% stopping slice out of a PERMUTATION of the training rows, so the slice held the same sites as the training data: the rule was validated in-distribution while the score was out-of-family. It never fired -- `mean_best_iter` sat at the 1500 ceiling in all 7 configs of the REG sweep -- so the ceiling was silently doing the regularizing. Measured on one LOFO fold (45 sites, base recipe): the watched RMSE was still falling at tree 1498/1500 while out-of-family R2 peaked at tree 175, giving up 0.0324 R2 -- about 4x the 0.0085 REG noise floor. Folds now fit on 100% of their training rows with no eval_set, and this file picks the tree count against the holdout that is actually reported. See notes/early-stopping-report.md.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root/

import lean_cook
import run_exp
from src.data.access import get_site_ids

_HERE = Path(__file__).resolve().parent

# The metric each task is ranked by -- the same names run_exp/render_results select the winner by, so a config that wins here is a config that wins there. CLF moved to lofo_prauc on 2026-07-28.
_HEADLINE = {"reg": "lofo_r2", "clf": "lofo_prauc"}
# The same quantity as _HEADLINE, but under the name lean_cook._score returns it by. _score emits bare metric names (r2, prauc, auc, rmse); the `lofo_`/`loso_` prefixes are attached later by _cross_metrics, which is what the scoreboard reports. The tree-count scan calls _score directly -- one metric, thousands of times -- so it needs the bare name.
_CURVE_METRIC = {"reg": "r2", "clf": "prauc"}
# reported next to it, so a config that buys LOFO by widening the leakage gap is visible. lofo_between_r2 leads for REG on purpose: across the 2026-07-28 grid within_r2 moved 0.022 end to end while between_r2 moved 0.221, so depth is bought almost entirely in the between-site ranker -- the known bottleneck.
_ALSO = {
    "reg": ["lofo_between_r2", "lofo_within_r2", "loso_r2", "lofo_rmse"],
    "clf": ["loso_auc", "lofo_auc", "lofo_prauc_lift", "lofo_between_rate_r2"],
}
_INT_KEYS = {"max_depth", "n_estimators"}
# k_frac at or above this means the ceiling chose the tree count rather than the data, so the row is a truncated measurement. Lives HERE rather than in fulltune because tune.py stamps `ceiling_bound` onto every row and _edge_report warns off the same number; three copies of one threshold is how the report ended up telling you to raise a ceiling the retry logic had already accepted.
_BIND = 0.9
_COARSE, _FINE = (
    50,
    10,
)  # tree-count scan granularity: a peak narrower than _COARSE is invisible to it (see _score_pool_curve), and the measured peak spans >150 trees, so 50 leaves ~3x margin

# THE COST RULE. A config is only worth its price if every DOUBLING of fit cost buys more than this much headline metric. Set to the CLF headline noise floor: below it a "gain" is not distinguishable from a reseed, so paying 2x for it is paying for noise.
#
# Applied in three places, all through _penalized: ranking the grid, accepting a ceiling raise, and accepting an expansion step. Ranking on score alone is what let a 26,530-tree CLF winner beat a 12,000-tree one by 0.0024 -- 0.65 of a noise floor for 2.2x the compute -- and then take 22 hours doing it.
NOISE_CEILING = 0.003

_GROW = 1.5  # ceiling multiplier per raise, floored to a multiple of _COARSE
_MAX_CEILING = 6000  # default hard stop for automatic ceiling raises; --max-ceiling overrides
_MAX_EXPAND = 6  # steps --automatic-expansion may walk past a ladder edge on ONE axis before giving up and saying so

# axis name -> (xgb parameter it sets, default ladder, given in relative units?).
# `trees` is NOT an axis: crossing it would cost a refit per value, and one fit at a ceiling already yields every prefix for free (see _tree_curve). `budget` (lr x n_estimators) is gone with it -- it only existed because early stopping never fired, which welded lr and n_estimators into a single product. With the tree count resolved directly, lr is a learning rate again and the optimal count adapts to it; keeping a budget axis would re-confound exactly what removing early stopping decoupled.
_AXES = {
    "depth": ("max_depth", (3, 4, 5, 6), False),
    "lr": ("learning_rate", (0.01, 0.02, 0.05), False),
    "lam": ("reg_lambda", (0.002, 0.02, 0.1, 0.3), True),
    "mcw": ("min_child_weight", (0.002, 0.02, 0.1, 0.3), True),
    "subsample": ("subsample", (0.5, 0.7, 0.9), False),
    "colsample": ("colsample_bytree", (0.5, 0.7, 0.9), False),
}


def _leaf_hessian(depth: int, subsample: float, n_rows: int, train_frac: float, hess_per_row: float) -> float:
    """Expected sum-of-Hessian in one leaf of a depth-`depth` tree.

    (n_rows x train_frac x subsample) / 2^depth rows, each contributing `hess_per_row` -- 1 for squared error, p(1-p) for logistic. Approximate (real trees are unbalanced) but the right SCALE, which is what makes relative lam/mcw depth-invariant.
    """
    return (n_rows * train_frac * subsample) / (2**depth) * hess_per_row


def _materialize(axis_vals: dict, ctx: dict, absolute: bool) -> dict:
    """Axis point -> XGBoost override dict. Relative lam/mcw resolve against the leaf Hessian implied by THIS point's depth and subsample, not a grid-wide average."""
    cfg: dict = {}
    for name, v in axis_vals.items():
        param, _ladder, _rel = _AXES[name]
        if param is not None and name not in ("lam", "mcw"):
            cfg[param] = v

    depth = int(cfg.get("max_depth", ctx["default_depth"]))
    subsample = float(cfg.get("subsample", ctx["default_subsample"]))

    if "lam" in axis_vals or "mcw" in axis_vals:
        h = _leaf_hessian(depth, subsample, ctx["n_rows"], ctx["train_frac"], ctx["hess_per_row"])
        for name, param in (("lam", "reg_lambda"), ("mcw", "min_child_weight")):
            if name in axis_vals:
                cfg[param] = float(axis_vals[name]) if absolute else float(axis_vals[name]) * h

    return {k: (int(v) if k in _INT_KEYS else v) for k, v in cfg.items()}


def _grid(search: list[str], fixed: dict, ladders: dict, ctx: dict, absolute: bool) -> list[tuple[dict, dict]]:
    """(axis point, xgb override) per cell of the cartesian over `search`, with `fixed` merged in, led by the reference row.

    The reference is the config this stage INHERITED -- `fixed` applied, no rung of the searched axis -- not the bare screening config. Against the bare config, a stage-3 delta_vs_default compares to something the chain abandoned two stages ago, so it reads as a large gain even when the stage lost ground, and the stage can never decline to move. Measured on a shakedown: stage 2 settled at 0.5373, stage 3's mcw ladder topped out at 0.5363, and that -0.0010 was threaded forward as a win because the only "no change" option on offer scored 0.4747.

    Its axis point is empty ON PURPOSE. If the reference wins, fulltune's _winner_axes reads no ax_* off it and threads nothing, which is exactly "no rung beat what I walked in with" -- while the row's effective config, and so the paste block, still carries every settled value.
    """
    ladders = {a: ladders.get(a) or _AXES[a][1] for a in search}
    out: list[tuple[dict, dict]] = [({}, _materialize(fixed, ctx, absolute))]
    if not search:  # confirmation stage: the product below yields exactly the reference again, so scoring it would be a duplicate fit
        return out
    for combo in itertools.product(*(ladders[a] for a in search)):
        pt = {**fixed, **dict(zip(search, combo))}
        out.append((pt, _materialize(pt, ctx, absolute)))
    return out


def _label(pt: dict, cfg: dict) -> str:
    """Human-readable config name; lam/mcw print as relative(absolute). An empty axis point is the reference row -- under a staged run that is the config the stage inherited, not the bare screening config. The name is load-bearing: delta_vs_default keys on it."""
    if not pt:
        return "DEFAULT (inherited)"
    bits = []
    for k, v in pt.items():
        if k in ("lam", "mcw"):
            param = _AXES[k][0]
            bits.append(f"{k}={v:g}({cfg[param]:.0f})")  # relative(absolute)
        else:
            bits.append(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}")
    return ", ".join(bits)


def _base_margin(m, task):
    """The constant every `output_margin` prediction carries, in MARGIN space.

    `base_score` is stored in PROBABILITY space for binary:logistic, so it must be logit-ed before it can be subtracted from a margin -- using it raw shifts every CLF prefix by logit(p) - p (1.349 at the 0.25 default), silently and with plausible-looking numbers. It also serialises as a bracketed string, hence the strip.
    """
    b = float(json.loads(m.get_booster().save_config())["learner"]["learner_model_param"]["base_score"].strip("[]"))
    return float(np.log(b / (1 - b))) if task == "clf" else b


def _prefix_oofs(models, X, task, ks, n):
    """{k: out-of-fold vector} for every prefix length in `ks`, from ONE pass of tree slices per fold.

    Boosting margins are additive and `iteration_range=(a, b)` costs O(b - a), so the margin at each k is the running sum of slice predictions rather than a fresh walk from tree 0 -- O(max k) per fold instead of O(sum k). Measured ~6.5x faster than re-predicting each prefix, agreeing to float32 noise. The fold slice is taken once per fold rather than once per (fold, k).
    """
    ks = sorted(ks)
    oofs = {k: np.full(n, np.nan) for k in ks}
    for m, te in models:
        Xte = X.iloc[te]
        base = _base_margin(m, task)
        acc = m.predict(Xte, iteration_range=(0, ks[0]), output_margin=True)
        oofs[ks[0]][te] = acc
        for a, b in zip(ks, ks[1:]):
            acc = acc + m.predict(Xte, iteration_range=(a, b), output_margin=True) - base
            oofs[b][te] = acc
    if task == "clf":  # margins -> P(positive), matching predict_proba
        for k in ks:
            oofs[k] = 1.0 / (1.0 + np.exp(-oofs[k]))
    return oofs


def _score_pool_curve(
    pool, feat, target, task, metric, ceiling, n_splits, true_lofo, max_holdout_pct, coarse=100, fine=10,
    loso=False, **cfg
):
    """Score a config, choosing its tree count rather than being told it. -> (metrics_at_best_k, best_k, curve).

    Fit each fold once at `ceiling`, then score every PREFIX on the holdout via iteration_range=(0, k) -- no refits, so the whole curve plus final metrics cost the same two CV passes _score_pool already paid. Scanned rather than bisected because the curve is not unimodal. Coarse-then-fine since a prefix costs O(k); `metric` is a bare _score key (r2/prauc), not a lofo_-prefixed one.
    """
    X, y = pool[feat], lean_cook._target(pool, target, task)
    fam = lean_cook.basin_groups(pool["site"])
    fam_folds = (
        list(lean_cook.folds_true_lofo(fam, max_holdout_pct)) if true_lofo else lean_cook.folds_grouped(fam, n_splits)
    )
    kw = dict(n_estimators=ceiling, **cfg)

    fam_models = [(m, te) for te, m in lean_cook._fold_models(X, y, fam_folds, task, **kw)]

    def curve_over(ks):
        ks = [k for k in ks if k > 0]
        got = _prefix_oofs(fam_models, X, task, ks, len(y))
        return {k: (lean_cook._score(y, oof, task)[metric], oof) for k, oof in got.items()}

    scan = curve_over(range(coarse, ceiling + 1, coarse))
    # Refine around the top TWO coarse points rather than only the argmax. The curve is bimodal -- on the measured fold it peaked +0.350 at 175 then recovered to a +0.317 plateau by 1375, a margin of only ~0.03 -- so when both basins are visible to the coarse grid but rank in the wrong order, the second window recovers the true peak.
    #
    # What this does NOT fix, measured: a peak NARROWER than `coarse` is invisible to the coarse grid entirely (both flanking samples read the base level), so the top-2 coarse points both sit in the other basin and neither window covers it. Simulated against the measured curve shape, top-1 and top-2 both find the peak at widths 150/80/40 and both miss it at 25/15. The only real protection there is `coarse` itself, which is why it is 50 rather than 100: the observed peak spans >150 trees, so 50 leaves a 3x margin.
    ranked = sorted(scan, key=lambda k: scan[k][0], reverse=True)
    windows = set()
    for k0 in ranked[:2]:
        windows.update(range(max(fine, k0 - coarse), min(ceiling, k0 + coarse) + 1, fine))
    scan.update(curve_over(sorted(windows - set(scan))))
    best_k = max(scan, key=lambda k: scan[k][0])
    oof_family = scan[best_k][1]

    # The LOSO pass is a FULL second CV pass whose only product is the leakage gap, and ranking is on LOFO alone -- so it is off per config and run once for the winner (see _winner_loso). Off, oof_site stays all-NaN and _cross_metrics emits NaN loso_*, keeping the column set intact.
    oof_site = np.full(len(y), np.nan)
    if loso:
        site_models = [
            (m, te)
            for te, m in lean_cook._fold_models(X, y, lean_cook.folds_grouped(pool["site"], n_splits), task, **kw)
        ]
        oof_site = _prefix_oofs(site_models, X, task, [best_k], len(y))[best_k]

    metrics = dict(
        n_sites=pool["site"].nunique(),
        n_families=int(pd.Series(fam).nunique()),
        n_rows=len(pool),
        n_feat=len(feat),
        **lean_cook._cross_metrics(pool, np.asarray(y), oof_site, oof_family, task),
    )
    return metrics, best_k, {k: v[0] for k, v in sorted(scan.items())}


def _fit_cost(best_k, cfg: dict) -> float:
    """Relative cost of one fit: trees x leaves x the fraction of rows and columns each tree sees.

    Analytic rather than measured wall-clock ON PURPOSE -- the same grid must settle the same way regardless of what else the machine is doing, and two tuning runs sharing a box inflate each other's timings enough to flip a decision. Ignores feature count, which is constant within a stage, so it cancels out of every comparison made here.
    """
    return float(best_k) * (2 ** int(cfg["max_depth"])) * float(cfg["subsample"]) * float(cfg["colsample_bytree"])


def _penalized(score: float, cost: float) -> float:
    """Headline metric discounted by NOISE_CEILING per doubling of fit cost.

    score - NC*log2(cost). Reference-free: the log turns the ratio rule into a subtraction, so A beats B exactly when B fails to buy more than NOISE_CEILING per doubling over A, and no baseline config has to be nominated for that comparison to hold.
    """
    return float(score) - NOISE_CEILING * math.log2(max(float(cost), 1e-9))


def _grow(ceiling: int, max_ceiling: int) -> int:
    """Next ceiling up, floored to a multiple of _COARSE and capped at max_ceiling.

    Aligned because the scan grid is range(_COARSE, ceiling+1, _COARSE): an unaligned ceiling leaves the top of the scan up to _COARSE-1 trees short of the fit's own ceiling, so `at_ceiling` reports a shorter prefix than its name claims. max() keeps growth monotonic at small ceilings, where a bare floor can move backwards.
    """
    return min(max_ceiling, max(ceiling + _COARSE, int(ceiling * _GROW) // _COARSE * _COARSE))


def _score_growing(
    pool, feat, target, task, metric, ceiling, max_ceiling, n_splits, true_lofo, max_holdout_pct, loso, **cfg
):
    """Score a config, refitting at a larger ceiling for as long as the tree count is being chosen by the bound rather than by the data. -> (metrics, best_k, curve, ceiling_used, note).

    PER CONFIG, not per stage. A config whose best_k sits well under its ceiling is already a finished measurement -- prefix scoring means a larger ceiling would have picked the same k off the same curve -- so only the bound row needs redoing and the rest of the grid keeps its scores at whatever ceiling they were measured at. That is why every row carries its own `ceiling`/`k_frac`/`ceiling_bound`.

    Growth stops on whichever comes first: the count stops binding, the raise stops paying under _penalized, or max_ceiling. The middle case is the one that matters -- an unregularized config absorbs trees indefinitely, gaining a few ten-thousandths per doubling, and only a cost rule ever tells it to stop.
    """
    kw = dict(coarse=_COARSE, fine=_FINE, loso=loso, **cfg)
    best = _score_pool_curve(pool, feat, target, task, metric, ceiling, n_splits, true_lofo, max_holdout_pct, **kw)
    used, note = ceiling, ""
    while best[1] >= _BIND * used:
        if used >= max_ceiling:
            note = f"bound at the --max-ceiling {max_ceiling}"
            break
        nxt = _grow(used, max_ceiling)
        trial = _score_pool_curve(pool, feat, target, task, metric, nxt, n_splits, true_lofo, max_holdout_pct, **kw)
        gain = trial[0][_HEADLINE[task]] - best[0][_HEADLINE[task]]
        # Same rule that ranks the grid: the raise must buy more than NOISE_CEILING per doubling of trees. Depth and the sampling fractions are identical across the two fits, so the cost ratio is exactly the tree ratio.
        if gain <= NOISE_CEILING * math.log2(max(trial[1], 1) / max(best[1], 1)):
            note = f"raise to {nxt} rejected: {gain:+.4f} for {trial[1] / max(best[1], 1):.1f}x trees"
            break
        best, used = trial, nxt
    return (*best, used, note)


def _winner_loso(pool, feat, target, task, metric, k, n_splits, **cfg):
    """Site-grouped score at a FIXED tree count -- the leakage-gap companion, run once for the winning config.

    Fits at `k` rather than the ceiling: the winner's tree count is already known, so there is nothing to scan.
    """
    X, y = pool[feat], lean_cook._target(pool, target, task)
    folds = lean_cook.folds_grouped(pool["site"], n_splits)
    models = [(m, te) for te, m in lean_cook._fold_models(X, y, folds, task, n_estimators=int(k), **cfg)]
    return lean_cook._score(y, _prefix_oofs(models, X, task, [int(k)], len(y))[int(k)], task)[metric]


def _columns(task: str, mask: str, pool: pd.DataFrame, encodings: bool = False) -> list[str]:
    """Resolve `--cols` to an explicit feature list against the pool actually built.

    The candidate list comes from run_exp.candidates, NOT a local copy. It used to be restated here, and a divergence tuned one feature set while the screen measured another -- with no error, just an incomparable number.
    """
    if mask == "all":
        target = run_exp.GEOM[task]["target"]
        return [c for c in pool.columns if c != target and c not in lean_cook._STRUCTURAL]
    g = run_exp.GEOM[task]
    n_buckets = len(g["edges"]) + 1
    bucketed = set(run_exp._weather_singles(g["lam"]))
    testing = run_exp.candidates(task, encodings)
    recipes = run_exp.build_recipes(run_exp.BASE_FEATURES, testing, n_buckets, bucketed, task)
    cols = recipes["base"] if mask == "base" else recipes[run_exp.FULL_NAME]
    return sorted(c for c in cols if c in pool.columns)  # present-only; over-expansion is harmless


def _parse_pairs(s: str | None) -> dict:
    """'depth=4,lam=0.1' -> {'depth': 4.0, 'lam': 0.1}. Axis names only -- a raw XGBoost name like reg_lambda=0.1 is rejected, since it would mean ~5000x less than the axis of the same name."""
    if not s:
        return {}
    out = {}
    for part in s.split(","):
        k, _, v = part.partition("=")
        k = k.strip()
        if k not in _AXES:
            raise SystemExit(f"--fix: {k!r} is not an axis. Axes: {', '.join(_AXES)}")
        out[k] = float(v)
    return out


def _parse_list(s: str | None, cast=float):
    return None if not s else tuple(cast(x) for x in s.split(","))


# axis -> (hard lower limit, hard upper limit, floor-is-already-inert). A winner sitting on a HARD limit is a real answer; a winner sitting on a ladder end that is not a hard limit means the search was truncated.
_AXIS_LIMIT = {
    "depth": (1, None, False),
    "lr": (0.0, None, False),
    "lam": (0.0, None, True),  # c<=0.002 is a <0.2% leaf shrink -- indistinguishable from no L2 at all
    "mcw": (0.0, None, True),  # likewise: a fraction this small never blocks a split
    "subsample": (0.0, 1.0, False),
    "colsample": (0.0, 1.0, False),
}


_REGIME_OF_MASK = {"base": "screen", "full": "full", "all": "full"}  # --cols -> the lean_cook regime it tunes


def _emit_config(df: pd.DataFrame, search: list[str], cols_mask: str, task: str, fixed: dict | None = None, resolved_axes: set | None = None) -> None:
    """Print the winner as a paste-ready lean_cook config block, plus which axes this run did NOT touch.

    The block is named for BOTH the regime and the task. `--cols base` tunes the add-one arms (_SCREEN_XGB_*), `--cols full` the drop-one arms (_FULL_XGB_*); pasting one into the other's dict silently rescales the whole screen. The task half matters for the same reason within a regime: reg_lambda and min_child_weight are absolute values scaled to the task's own per-leaf Hessian, so a REG block in the CLF dict means something ~5.6x stronger than it says. Nothing here writes the dict automatically.

    `fixed` counts as resolved alongside `search` -- an axis pinned by an earlier stage IS tuned, and treating it as untouched made the final confirmation stage (which searches nothing) print every axis as untuned.
    """
    best = df.iloc[0]
    name = f"_{_REGIME_OF_MASK.get(cols_mask, 'full').upper()}_XGB_{task.upper()}"
    print(f"\n  paste into lean_cook.{name}  (tuned on --cols {cols_mask}, axes {','.join(search) or '(none: confirmation)'}):")
    print(f"  {name} = dict(")
    for k in lean_cook.default_xgb(task, _REGIME_OF_MASK.get(cols_mask, "full")):
        v = best.get(k)
        v = v.item() if hasattr(v, "item") else v  # numpy scalar -> native, else repr() prints np.float64(0.02)
        if k == "early_stopping_rounds" or v is None or (isinstance(v, float) and np.isnan(v)):
            v = None
        elif k in _INT_KEYS:
            v = int(v)
        elif isinstance(v, float):
            v = round(v, 6)
        print(f"      {k}={v!r},")
    print("  )")
    resolved = set(resolved_axes) if resolved_axes is not None else set(search) | set(fixed or {})
    untouched = [a for a in _AXES if a not in resolved]
    if untouched:
        print(f"  [partial] NOT tuned in this run: {', '.join(untouched)} -- still at inherited values. "
              f"lam/mcw in particular are measured to be inert at the inherited reg_lambda=5 / min_child_weight=10.")


def _expand_edges(score_point, grid, rows, search, ladders, fixed, ctx, absolute, head) -> None:
    """Walk each searched axis outward past a ladder edge, one rung at a time, while the steps keep paying. Scores in place via `score_point`; adds rows, returns nothing.

    An edge winner means the search was TRUNCATED, not that an optimum was found -- depth=6 winning against [3,4,5,6] says nothing about depth 9. Axes are walked SEQUENTIALLY from the running best rather than as a joint product: stage 2 crosses depth x lam and both can be at an edge, and a product would cost O(steps^2) fits to answer a question two independent walks answer in O(steps).

    Each step must clear the same _penalized bar the grid is ranked by, so an expansion that buys a ten-thousandth for twice the trees is refused exactly like a ceiling raise is.
    """
    for ax in search:
        lad = list(sorted(ladders.get(ax) or _AXES[ax][1]))
        # re-read the best AFTER any earlier axis moved -- expansion is sequential, so the point being expanded from is the current best, not the one the grid ended on
        best = max(rows, key=lambda r: _penalized(r[head], r["fit_cost"]))
        v = best.get(f"ax_{ax}")
        direction = _edge_of(ax, v, lad)
        if not direction:
            continue
        pt = {a: best[f"ax_{a}"] for a in _AXES if best.get(f"ax_{a}") is not None and not pd.isna(best.get(f"ax_{a}"))}
        pt = {**fixed, **pt}
        cur_score, cur_cost = best[head], best["fit_cost"]
        print(f"\n  [expand] {ax}={v:g} sits at the {'top' if direction > 0 else 'bottom'} of {lad} -- walking outward")
        for step in range(1, _MAX_EXPAND + 1):
            nxt = _next_rung(ax, lad, direction)
            if nxt is None:
                print(f"  [expand] {ax} is at its hard limit -- stopping")
                break
            pt = {**pt, ax: nxt}
            score, cost = score_point(pt, _materialize(pt, ctx, absolute), f"expand {ax}={nxt:g}")
            gain = score - cur_score
            if gain <= NOISE_CEILING * math.log2(max(cost, 1e-9) / max(cur_cost, 1e-9)):
                print(f"  [expand] {ax}={nxt:g} rejected: {gain:+.4f} for {cost / max(cur_cost, 1e-9):.1f}x cost -- settling at {ax}={lad[-1] if direction > 0 else lad[0]:g}")
                break
            lad = sorted(lad + [nxt])
            cur_score, cur_cost = score, cost
            if step == _MAX_EXPAND:
                print(f"  [expand] {ax} still improving after {_MAX_EXPAND} steps -- the ladder was badly truncated; widen --{_flag(ax)} and re-run")


def _edge_of(ax: str, v: float, ladder) -> int:
    """-1 if `v` sits on the ladder's bottom rung, +1 on its top, 0 otherwise. A hard limit or an inert floor is NOT an edge -- the search was not truncated there, the axis simply ran out of meaningful room."""
    lad = sorted(ladder)
    if pd.isna(v) or len(lad) < 2:
        return 0
    lo_lim, hi_lim, inert_floor = _AXIS_LIMIT[ax]
    if v >= lad[-1]:
        return 0 if (hi_lim is not None and lad[-1] >= hi_lim) else 1
    if v <= lad[0]:
        return 0 if (inert_floor or lad[0] <= lo_lim) else -1
    return 0


def _next_rung(ax: str, ladder, direction: int) -> float | None:
    """One rung past the edge of `ladder`, arithmetic in the ladder's own units, clipped to the axis's hard limits. None when the limit is already reached.

    Arithmetic on purpose: it is the same step _edge_report prints as "Try --lrs 0.02,0.05,0.08,0.11", so the suggestion and what --automatic-expansion actually runs are the same number by construction.
    """
    lad = sorted(ladder)
    lo_lim, hi_lim, _ = _AXIS_LIMIT[ax]
    if direction > 0:
        nxt = round(lad[-1] + (lad[-1] - lad[-2]), 6)
        if hi_lim is not None:
            nxt = min(nxt, hi_lim)
        return None if nxt <= lad[-1] else nxt
    nxt = round(lad[0] - (lad[1] - lad[0]), 6)
    return None if nxt <= lo_lim else nxt


def _edge_report(df: pd.DataFrame, search: list[str], ladders: dict, ceiling: int) -> None:
    """Warn when the winning config sits on the edge of a searched ladder, or on the tree ceiling.

    An edge winner means the search was TRUNCATED, not that an optimum was found -- unless the edge is a hard limit (subsample/colsample at 1.0) or an already-inert floor (lam/mcw at ~0, which is indistinguishable from no regularization). Prints the ladder to try next; extending is left to the caller, since auto-widening can run away.
    """
    best = df.iloc[0]
    notes = []
    used = int(best.get("ceiling", ceiling))
    # Only a ceiling that bound WITHOUT the growth loop having deliberately stopped is a truncated measurement. When `ceiling_note` says the raise was refused by the cost rule, the count was chosen -- by economics rather than by the curve, but chosen -- and repeating the old "re-run with a larger --ceiling" would be telling you to undo a decision this run just made on purpose.
    if pd.notna(best.get("best_k")) and best["best_k"] >= _BIND * used:
        why = str(best.get("ceiling_note") or "")
        if why.startswith("raise to"):
            notes.append(f"best_k={int(best['best_k'])} fills the {used} ceiling, but growth was declined on cost ({why}). "
                         f"The count is capped by NOISE_CEILING={NOISE_CEILING}, not by the scan bound.")
        else:
            notes.append(f"best_k={int(best['best_k'])} is >={_BIND:.0%} of the {used} ceiling -- the ceiling is binding, "
                         f"not chosen. Raise --max-ceiling before trusting this winner." + (f" [{why}]" if why else ""))
    for ax in search:
        v = best.get(f"ax_{ax}")
        lad = sorted(ladders.get(ax) or _AXES[ax][1])
        if pd.isna(v) or len(lad) < 2:
            continue
        lo_lim, hi_lim, inert_floor = _AXIS_LIMIT[ax]
        if v >= lad[-1] and (hi_lim is None or lad[-1] < hi_lim):
            step = lad[-1] - lad[-2]
            nxt = [round(lad[-1] + step * i, 6) for i in (1, 2)]
            if hi_lim is not None:
                nxt = [x for x in nxt if x <= hi_lim] or [hi_lim]
            notes.append(f"{ax}={v:g} is the TOP of its ladder {lad} -- untruncated optimum unknown. "
                         f"Try --{_flag(ax)} {','.join(f'{x:g}' for x in lad[-2:] + nxt)}")
        elif v <= lad[0]:
            if inert_floor:
                notes.append(f"{ax}={v:g} is the bottom of its ladder, and that low it is already inert -- "
                             f"read as 'no {ax} helps', not as a truncated search.")
            elif lad[0] > lo_lim:
                step = lad[1] - lad[0]
                nxt = [round(lad[0] - step * i, 6) for i in (1, 2)]
                nxt = [x for x in nxt if x > lo_lim]
                if nxt:
                    notes.append(f"{ax}={v:g} is the BOTTOM of its ladder {lad} -- untruncated optimum unknown. "
                                 f"Try --{_flag(ax)} {','.join(f'{x:g}' for x in sorted(nxt) + lad[:2])}")
    for n in notes:
        print(f"  [edge] {n}")


def _flag(axis: str) -> str:
    """CLI flag for an axis ladder: the axis name, pluralised."""
    return f"{axis}s"


def tune(
    task: str,
    sites,
    cols_mask: str,
    search: list[str],
    fixed: dict,
    ladders: dict,
    absolute: bool,
    ceiling: int,
    seeds: tuple[int, ...],
    append: bool,
    true_lofo: bool = False,
    max_holdout_pct: float = 0.2,
    n_splits: int = 5,
    pool: pd.DataFrame | None = None,
    encodings: bool = False,
    loso: bool = False,
    resolved_axes: set | None = None,
    dump_curve: bool = False,
    max_ceiling: int = _MAX_CEILING,
    expand: bool = False,
):
    """Grid-search one stage. `loso` is LAST and keyword-only in practice on purpose: it used to sit before `true_lofo`, and fulltune.py's positional call bound its `true_lofo` to `loso` and its max_holdout_pct (0.2, truthy) to `true_lofo` -- so every fulltune run silently used true-LOFO folds while `--true_lofo` toggled the LOSO pass instead. Pass everything from a driver by keyword."""
    g = run_exp.GEOM[task]
    head = _HEADLINE[task]
    regime = _REGIME_OF_MASK.get(cols_mask, "full")
    base = lean_cook.default_xgb(task, regime)  # the inherited config the DEFAULT row measures -- per (task, regime), since lam/mcw are scaled to the task's own Hessian
    if pool is None:  # fulltune.py passes one prebuilt pool so a multi-stage workflow pools once, not once per stage
        wide = run_exp.make_wide_recipe(task, g["edges"], g["vel"], g["lam"], encodings)
        pool = lean_cook._pool_wide(wide, sites, g["target"], progress_label=f"{task} wide")
    feat = _columns(task, cols_mask, pool, encodings)
    y = lean_cook._target(pool, g["target"], task)
    fam = lean_cook.basin_groups(pool["site"])
    n_fam = int(pd.Series(fam).nunique())
    if n_fam < 2:
        raise SystemExit(
            f"{task}: only {n_fam} basin family in the pool -- every LOFO fold would be empty, so every score "
            f"is NaN and best_k would be read off an all-NaN curve. Widen the site set."
        )

    # Hessian per row: exactly 1 for squared error; p(1-p) for logistic, at the pool's own base rate. This is the entire reason the same relative ladder yields different absolute values per task.
    p = float(np.mean(y)) if task == "clf" else float("nan")
    hess_per_row = p * (1 - p) if task == "clf" else 1.0
    # one family held out per fold under true LOFO, else GroupKFold's (k-1)/k
    train_frac = (1 - 1 / n_fam) if true_lofo else (1 - 1 / min(n_splits, n_fam))
    ctx = dict(
        n_rows=len(pool),
        train_frac=train_frac,
        hess_per_row=hess_per_row,
        ceiling=ceiling,
        default_depth=base["max_depth"],
        default_subsample=base["subsample"],
    )

    grid = _grid(search, fixed, ladders, ctx, absolute)
    print(
        f"\n=== {task.upper()}  pool {pool.shape}  |  --cols {cols_mask} -> {len(feat)} features  |  "
        f"{pool['site'].nunique()} sites, {n_fam} families  |  {len(grid)} configs x {len(seeds)} seed(s) ==="
    )
    print(f"    geometry: edges={g['edges']} vel={g['vel']} lam={g['lam']}  |  ranking on {head}")
    print(f"    searching {search or ['(none)']}" + (f", fixed {fixed}" if fixed else ""))
    if not absolute:
        eff = ctx["n_rows"] * train_frac * ctx["default_subsample"]
        scale = "  ".join(
            f"d{d}:{_leaf_hessian(d, ctx['default_subsample'], ctx['n_rows'], train_frac, hess_per_row):,.0f}"
            for d in (3, 4, 5, 6)
        )
        print(
            f"    leaf Hessian ({eff:,.0f} rows/tree, {hess_per_row:.4f}/row) -> {scale}   [relative units; c=0.1 means a 9% leaf shrink]"
        )
    print(f"    tree count: scanned to a {ceiling} ceiling per config, grown per config up to {max_ceiling}, coarse {_COARSE} then fine {_FINE}")
    print(f"    cost rule: a doubling of trees x leaves x samples must buy > {NOISE_CEILING} {head}"
          + ("  |  --automatic-expansion ON" if expand else ""))
    print()

    rows, curves, t0 = [], [], time.time()

    def score_point(pt: dict, cfg: dict, tag: str) -> tuple[float, float]:
        """Score one axis point across every seed: append its rows (and curves), print its line. -> (mean headline, mean cost).

        A closure rather than a loop body so --automatic-expansion scores its off-ladder points through exactly the same path as the grid -- same ceiling growth, same columns, same CSV.
        """
        t1 = time.time()
        for seed in seeds:
            kw = {**cfg, "random_state": seed} if len(seeds) > 1 or seed != 42 else dict(cfg)
            r, best_k, curve, used, note = _score_growing(
                pool, feat, g["target"], task, _CURVE_METRIC[task], ceiling, max_ceiling,
                n_splits, true_lofo, max_holdout_pct, loso, **kw,
            )
            eff = {**base, **kw, "n_estimators": best_k}
            rows.append(
                {
                    "config": _label(pt, cfg),
                    # `searched`/`cols` identify which run a row came from. Under --append the file accumulates stages and sorting by headline interleaves them, so without these a stage-1 row and a stage-3 row are distinguishable only by guessing from which ax_* happen to be non-NaN.
                    "searched": ",".join(search) or "none",
                    "cols": cols_mask,
                    "seed": seed,
                    head: r[head],
                    **{k: r.get(k) for k in _ALSO[task]},
                    "best_k": best_k,  # trees the config actually wanted
                    "fit_cost": round(_fit_cost(best_k, eff), 1),
                    # The ceiling this row was measured at, and whether it bound. Rows within ONE stage now carry different ceilings on purpose -- growth is per config, so an un-bound row is left at whatever ceiling already finished it. `ceiling_note` says why growth stopped when it stopped short.
                    "ceiling": used,
                    "k_frac": round(best_k / used, 3),  # ~1.0 means the ceiling is binding, not chosen
                    "ceiling_bound": bool(best_k / used >= _BIND),
                    "ceiling_note": note,
                    "at_ceiling": round(curve[max(curve)], 4),
                    # the EFFECTIVE config, not just the overrides, so each row says what was fit rather than what differed
                    **{f"ax_{k}": v for k, v in pt.items()},
                    **eff,
                }
            )
            if dump_curve:
                # The prefix curve is what best_k was read off, and it is free -- one fit per fold already scored every k. Persisted long-format so a screening run can ask "how few trees still separates the arms" from fits it has already paid for, instead of inferring it from best_k and at_ceiling alone.
                # .extend, NOT `curves +=`: score_point is a closure, and an augmented assignment REBINDS, which makes `curves` local to it and raises UnboundLocalError on the first --dump-curve row. `rows.append` above is safe for the same reason this is -- it mutates rather than rebinds.
                curves.extend(
                    {"config": _label(pt, cfg), "cols": cols_mask, "seed": seed, "k": k, head: v}
                    for k, v in curve.items()
                )
        mine = rows[-len(seeds):]
        got, ks = [x[head] for x in mine], [x["best_k"] for x in mine]
        seed_note = f"  [{len(seeds)} seeds, sd {np.std(got):.4f}]" if len(seeds) > 1 else ""
        note = mine[-1]["ceiling_note"]
        print(
            f"  [{tag}] {_label(pt, cfg):<50.50} {head}={np.mean(got):.4f}  k={int(np.mean(ks)):>5}"
            f"  (vs {np.mean([x['at_ceiling'] for x in mine]):+.4f} at {mine[-1]['ceiling']})"
            f"{seed_note}  ({time.time() - t1:.0f}s)" + (f"\n        [ceiling] {note}" if note else ""),
            flush=True,
        )
        return float(np.mean(got)), float(np.mean([x["fit_cost"] for x in mine]))

    for i, (pt, cfg) in enumerate(grid, 1):
        score_point(pt, cfg, f"{i}/{len(grid)}")

    if expand and search:
        _expand_edges(score_point, grid, rows, search, ladders, fixed, ctx, absolute, head)

    df = pd.DataFrame(rows)
    if len(seeds) > 1:
        # Rank on the seed MEAN: picking the max over a grid of noisy single-seed scores is optimistically biased, and subsample<1 makes every fit seed-dependent. Averaging only the numeric columns on purpose -- a config column that is None/str (early_stopping_rounds=None, say) would make .mean() raise here, i.e. AFTER every fit in the sweep has been paid for. `first` keeps those constant-within-config values intact.
        num = [c for c in df.columns if c != "config" and pd.api.types.is_numeric_dtype(df[c])]
        rest = [c for c in df.columns if c != "config" and c not in num]
        df = df.groupby("config", as_index=False).agg({**{c: "mean" for c in num}, **{c: "first" for c in rest}})
        df["n_seeds"] = len(seeds)
    # DEFAULT LOSES TIES. The inherited config is usually also a ladder rung, so the two score identically and an arbitrary tie-break decided which row won. When DEFAULT won it carried no ax_* values, and three things silently followed: fulltune._winner_axes threaded nothing forward (stage 5 above shows `fix=` with no subsample, though stage 4 settled it at 0.7), _edge_report skipped the axis so a top-of-ladder winner raised no warning, and _emit_config reported an axis that HAD been searched as untuned. Preferring the explicit rung on a tie names the same model and keeps its axis values attached.
    # RANKED COST-AWARE, not on the headline alone. `{head}_adj` is the headline discounted by NOISE_CEILING per doubling of fit cost, so a config only outranks a cheaper one by buying more than a noise floor per doubling. On the 2026-07-29 CLF grid this is the difference between settling at 26,530 trees and settling at 12,000 for 0.0024 less. The raw headline stays in its own column and is what every delta and every report still quotes -- this reorders the frame, it does not rewrite the measurement.
    df.insert(2, f"{head}_adj", (df[head] - NOISE_CEILING * np.log2(df["fit_cost"].clip(lower=1e-9))).round(4))
    # DEFAULT LOSES TIES. The inherited config is usually also a ladder rung, so the two score identically and an arbitrary tie-break decided which row won. When DEFAULT won it carried no ax_* values, and three things silently followed: fulltune._winner_axes threaded nothing forward (stage 5 above shows `fix=` with no subsample, though stage 4 settled it at 0.7), _edge_report skipped the axis so a top-of-ladder winner raised no warning, and _emit_config reported an axis that HAD been searched as untuned. Preferring the explicit rung on a tie names the same model and keeps its axis values attached.
    df["_default_last"] = (df["config"] == "DEFAULT (inherited)").astype(int)
    df = df.sort_values([f"{head}_adj", "_default_last"], ascending=[False, True]).drop(columns="_default_last")
    df = df.reset_index(drop=True)
    ref = df.loc[df["config"] == "DEFAULT (inherited)", head]
    if len(ref):
        df.insert(3, "delta_vs_default", (df[head] - float(ref.iloc[0])).round(4))

    # `df` must keep describing THIS stage. Reassigning it to the concatenation made the winner, the LOSO pass, _edge_report, _emit_config and the frame fulltune._winner_axes threads forward all describe the best row across every appended stage plus whatever stale rows the CSV already held -- so a stage that lost ground silently re-exported an earlier stage's axes as its own result.
    out = _HERE / f"tune_{task}_{regime}.csv"
    df_out = pd.concat([pd.read_csv(out), df], ignore_index=True).sort_values(head, ascending=False) if (append and out.exists()) else df
    df_out.to_csv(out, index=False)

    if curves:
        cout = _HERE / f"tune_{task}_{regime}_curve.csv"
        cdf = pd.DataFrame(curves)
        cdf = pd.concat([pd.read_csv(cout), cdf], ignore_index=True) if (append and cout.exists()) else cdf
        cdf.to_csv(cout, index=False)
        print(f"  wrote {cout}  ({len(cdf)} prefix scores)")

    if not loso:  # one site pass, for the winner only, so the leakage gap is still reported
        w = df.iloc[0]
        wcfg = {k: v for k, v in base.items() if k != "n_estimators"}
        wcfg.update({k: w[k] for k in ("max_depth", "learning_rate", "reg_lambda", "min_child_weight",
                                        "subsample", "colsample_bytree") if k in df.columns and pd.notna(w.get(k))})
        wcfg["max_depth"] = int(wcfg["max_depth"])
        loso_col = "loso_r2" if task == "reg" else "loso_auc"
        df.loc[df.index[0], loso_col] = _winner_loso(
            pool, feat, g["target"], task, _CURVE_METRIC[task] if task == "reg" else "auc",
            w["best_k"], n_splits, **wcfg
        )

    print(f"\n  best: {df.iloc[0]['config']}  ({head}={df.iloc[0][head]:.4f})")
    _edge_report(df, search, ladders, ceiling)
    _emit_config(df, search, cols_mask, task, fixed, resolved_axes)
    print(f"  wrote {out}  ({len(df_out)} rows, this stage {len(df)}, {time.time() - t0:.0f}s)")
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=["reg", "clf", "both"], default="both")
    ap.add_argument(
        "--cols",
        choices=["full", "base", "all"],
        default="full",
        help="which column mask to tune on -- 'base' produces lean_cook._SCREEN_XGB_* (the add-one arms), "
        "'full' produces _FULL_XGB_* (the drop-one arms). The winner is emitted named for the matching dict.",
    )
    ap.add_argument("--encodings", action="store_true", help="build the pool including the bake-off encodings")
    ap.add_argument(
        "--sites",
        type=int,
        default=None,
        help="cap to the first N sites (SMOKE ONLY -- it changes the family structure and so the LOFO difficulty; never pick values on a subset)",
    )
    ap.add_argument("--search", default="depth,lr", help=f"axes to cross: {', '.join(_AXES)}")
    ap.add_argument("--fix", default=None, help="pin axes not searched, e.g. 'depth=4,lam=0.1'")
    ap.add_argument(
        "--absolute",
        action="store_true",
        help="read --lams/--mcws as raw XGBoost units instead of fractions of the per-leaf Hessian",
    )
    ap.add_argument(
        "--ceiling",
        type=int,
        default=1500,
        help="upper bound on trees; each config is scanned to it and reports the count it actually wanted",
    )
    ap.add_argument(
        "--max-ceiling",
        type=int,
        default=_MAX_CEILING,
        help=f"hard stop for the automatic per-config ceiling growth (default {_MAX_CEILING}). A bound config is "
        f"refit at a larger ceiling immediately, on its own, while the raise keeps paying under NOISE_CEILING; "
        f"this is where that stops regardless.",
    )
    ap.add_argument(
        "--automatic-expansion",
        action="store_true",
        help="when the winner lands on a ladder edge, keep stepping one rung outward and re-scoring that exact "
        "config until a step stops paying under NOISE_CEILING. Off by default: an edge winner means the search "
        "was truncated, and walking out of it costs fits.",
    )
    ap.add_argument("--seeds", default="42", help="comma-separated random_state values; >1 ranks on the seed mean")
    ap.add_argument(
        "--loso",
        action="store_true",
        help="compute the LOSO leakage gap for EVERY config (default: winner only -- the site pass is a full second "
        "CV and ranking is on LOFO alone)",
    )
    ap.add_argument(
        "--append", action="store_true", help="append to tune_{task}.csv instead of overwriting, so stages accumulate"
    )
    ap.add_argument(
        "--dump-curve",
        action="store_true",
        help="also write tune_{task}_{regime}_curve.csv: the score at EVERY prefix tree count, per config. Free -- it is the scan best_k was already read off. Use it to pick a cheaper n_estimators, and to check that arm deltas survive there.",
    )
    ap.add_argument("--true_lofo", action="store_true", help="score with a TRUE leave-one-family-out holdout")
    ap.add_argument(
        "--max_holdout_pct",
        type=float,
        default=0.2,
        help="with --true_lofo, a family above this share of pooled rows is never held out (it still trains)",
    )
    for axis in _AXES:
        ap.add_argument(f"--{_flag(axis)}", default=None, help=f"ladder for the {axis} axis (default {_AXES[axis][1]})")
    a = ap.parse_args()

    search = [s.strip() for s in a.search.split(",") if s.strip()]
    for s in search:
        if s not in _AXES:
            raise SystemExit(f"--search: {s!r} is not an axis. Axes: {', '.join(_AXES)}")

    fixed = _parse_pairs(a.fix)
    if set(fixed) & set(search):
        raise SystemExit(f"--fix and --search overlap on {sorted(set(fixed) & set(search))}")
    ladders = {axis: _parse_list(getattr(a, _flag(axis)), int if axis == "depth" else float) for axis in _AXES}
    seeds = _parse_list(a.seeds, int)

    sites = [str(s) for s in get_site_ids()]
    if a.sites:
        sites = sites[: a.sites]
    tasks = ["reg", "clf"] if a.task == "both" else [a.task]
    print(f"tuning {tasks} on {len(sites)} sites (cols={a.cols}, search={search}, absolute={a.absolute})")
    for t in tasks:
        # BY KEYWORD. Bound positionally, this call and fulltune's silently mapped --true_lofo onto `loso` and max_holdout_pct onto `true_lofo` when the signature moved.
        tune(
            task=t,
            sites=sites,
            cols_mask=a.cols,
            search=search,
            fixed=fixed,
            ladders=ladders,
            absolute=a.absolute,
            ceiling=a.ceiling,
            seeds=seeds,
            append=a.append,
            true_lofo=a.true_lofo,
            max_holdout_pct=a.max_holdout_pct,
            encodings=a.encodings,
            loso=a.loso,
            dump_curve=a.dump_curve,
            max_ceiling=a.max_ceiling,
            expand=a.automatic_expansion,
        )


if __name__ == "__main__":
    main()
