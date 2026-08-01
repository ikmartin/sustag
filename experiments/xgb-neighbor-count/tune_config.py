"""tune_config.py -- pick the ONE XGBoost config per task that every arm of the neighbour-count sweep fits at.

Writes xgb_config.json. run_exp.py reads it and fits all 27 arms of a task at that single config, which is the whole point: tuning per arm would make a variant's win indistinguishable from a better config, and that is what made exp 36 unreadable against exp 33. So exactly two configs come out of here, one for REG and one for CLF, and nothing downstream retunes.

TUNED AGAINST THE WIDEST ARM (`nearest_4_nbr_rednt_area`, 29 donor columns over the island base). A config picked on the island baseline underfits the donor arms, which penalises exactly the arms the experiment exists to measure; the reverse error -- a config with capacity the narrow arm cannot use -- is checked for explicitly (`--sanity`, and `_provenance.notes` in the written file).

METHOD, taken from experiments/feature-manifest/tune.py; see its module docstring for the derivations.

1. RELATIVE lam/mcw. `reg_lambda` and `min_child_weight` are given as a FRACTION of the expected per-leaf Hessian (`_leaf_hessian`) and converted per config once depth is known. Leaf weight is -G/(H+lambda), so lambda = c x H is a 1/(1+c) shrink at EVERY depth -- the axis becomes depth-invariant, and REG (Hessian 1/row) and CLF (p(1-p)/row) land on one ladder. `--absolute` opts out.

2. THE TREE COUNT IS NOT AN AXIS. Each fold is fit ONCE at a ceiling and every PREFIX is scored via iteration_range=(0,k), so a config reports the count it actually wanted (`best_k`) for the price of the fits it was already paying. Coarse scan at 50, refined at 10 around the top TWO coarse points -- the curve is not unimodal, so the argmax alone is not enough. A config whose best_k fills its ceiling is refit at a larger one, alone, while the rest of the grid keeps its scores.

3. THE COST RULE. Ranking is on `{headline}_adj` = headline - NOISE_CEILING x log2(fit_cost), i.e. a doubling of trees x 2^depth x subsample x colsample must buy more than one CLF noise floor. Without it an unregularized config absorbs trees forever, and the same rule gates ceiling raises and ladder expansion.

4. STAGED COORDINATE SEARCH: depth x lr, then depth x lam (re-crossing depth, because relative units are only approximately depth-invariant and (deep, large-lambda) is the corner one-then-the-other misses), then mcw, subsample, colsample, then a confirmation pass at the settled point -- which resolves its own best_k, since the combination the coordinate stages settled on may never have been scored as a unit.

ONE POOL PER TASK, built once and shared by every config in every stage; pooling is ~20 min and the fits are exactly paired because they are the same rows and the same folds. `--pool-cache` parks it as parquet so a re-run skips the build.

    python tune_config.py --task both --pool-cache /tmp/nbr-pools --write
    python tune_config.py --task reg --sites 20 --ceiling 300 --max-ceiling 600   # shakedown; never pick values on it
    python tune_config.py --task clf --sanity-only --pool-cache /tmp/nbr-pools    # re-run the narrow-arm check alone

OUTPUT. `tune_<task>.csv` is every config scored, one row each, rewritten after every config so a crash keeps what it had already paid for. `tune_<task>_curve.csv` is the score at every prefix tree count for every config -- free, it is the scan best_k was read off. `xgb_config.json` is written only under `--write`.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # the experiment dir, for arms/neighbors/run_exp

import arms as A
import run_exp
from src.data.access import get_site_ids
from src.eval import cook

_HERE = Path(__file__).resolve().parent
CONFIG_PATH = _HERE / "xgb_config.json"

# The arm every config is scored on. The widest one in the sweep: island base + 29 donor columns (4 slots x 7 + the shared count).
TUNE_ARM = "nearest_4_nbr_rednt_area"

# Load-bearing names -- run_exp.py and render_results.py select the winner by them.
_HEADLINE = {"reg": "lofo_r2", "clf": "lofo_prauc"}
# The same quantity under the name cook._score returns it by. _score emits bare names (r2, prauc); the lofo_/loso_ prefixes are attached later by _cross_metrics. The prefix scan calls _score directly, thousands of times, so it needs the bare one.
_CURVE_METRIC = {"reg": "r2", "clf": "prauc"}
_ALSO = {
    "reg": ["lofo_between_r2", "lofo_within_r2", "lofo_rmse", "lofo_macro_r2"],
    "clf": ["lofo_auc", "lofo_prauc_lift", "lofo_between_rate_r2", "lofo_brier"],
}
# Headline noise floor per task -- a reseed moves the metric by about this much. Used ONLY to judge the narrow-arm sanity check; ranking uses NOISE_CEILING.
NOISE_FLOOR = {"reg": 0.0085, "clf": 0.0037}

# THE COST RULE. A config is worth its price only if every DOUBLING of fit cost buys more than this much headline. Set to the CLF noise floor: below it a "gain" is not distinguishable from a reseed.
NOISE_CEILING = 0.003

_INT_KEYS = {"max_depth", "n_estimators"}
_BIND = 0.9  # k_frac at or above this means the ceiling chose the tree count, not the data
_COARSE, _FINE = 50, 10  # prefix-scan granularity; a peak narrower than _COARSE is invisible to it
_GROW = 1.5  # ceiling multiplier per raise, floored to a multiple of _COARSE
_MAX_CEILING = 6000
_MAX_EXPAND = 6  # rungs --automatic-expansion may walk past a ladder edge on ONE axis

# The config the file shipped with before this tuner ran: train.RECIPE_XGB's island_{REG,CLF} entries. It is the `DEFAULT (inherited)` reference row of stage 1 and the comparison arm of the narrow-arm sanity check, so it is pinned HERE rather than read back out of xgb_config.json -- read from the file, a second run would inherit its own first run's answer and the sanity check would compare a config to itself.
PROVISIONAL = {
    "reg": dict(learning_rate=0.05, max_depth=5, min_child_weight=10.0, subsample=0.7, colsample_bytree=0.5,
                reg_lambda=307.95275, reg_alpha=0.0, random_state=42, n_estimators=480),
    "clf": dict(learning_rate=0.05, max_depth=3, min_child_weight=4.395976, subsample=0.7, colsample_bytree=0.5,
                reg_lambda=659.396334, reg_alpha=0.0, random_state=42, n_estimators=580),
}

# axis -> (xgb parameter, default ladder, relative units?). `trees` is NOT an axis -- one fit at a ceiling already yields every prefix.
_AXES = {
    "depth": ("max_depth", (3, 4, 5, 6), False),
    "lr": ("learning_rate", (0.01, 0.02, 0.05), False),
    "lam": ("reg_lambda", (0.002, 0.02, 0.1, 0.3), True),
    "mcw": ("min_child_weight", (0.002, 0.02, 0.1, 0.3), True),
    "subsample": ("subsample", (0.5, 0.7, 0.9), False),
    "colsample": ("colsample_bytree", (0.5, 0.7, 0.9), False),
}
# axis -> (hard lower limit, hard upper limit, floor-is-already-inert). A winner on a HARD limit is an answer; a winner on a soft ladder end means the search was truncated.
_AXIS_LIMIT = {
    "depth": (1, None, False),
    "lr": (0.0, None, False),
    "lam": (0.0, None, True),  # c<=0.002 is a <0.2% leaf shrink -- indistinguishable from no L2
    "mcw": (0.0, None, True),
    "subsample": (0.0, 1.0, False),
    "colsample": (0.0, 1.0, False),
}

_STAGES = [["depth", "lam"], ["mcw"], ["subsample"], ["colsample"]]  # stage 1 (depth x lr) is separate


# ── pool ──────────────────────────────────────────────────────────────────────


def build_pool(task: str, sites, cache_dir: Path | None) -> pd.DataFrame:
    """The wide pool for `task`, from cache if `cache_dir` holds one for this exact site count.

    Keyed on task and len(sites) only -- the cohort is access.get_site_ids() and `--sites` only ever truncates it, so nothing else can vary between two runs that would change the frame.
    """
    cache = None if cache_dir is None else Path(cache_dir) / f"pool_{task}_{len(sites)}.parquet"
    if cache is not None and cache.exists():
        pool = pd.read_parquet(cache)
        print(f"  pool {task}: {pool.shape} from cache {cache}")
        return pool
    pool = cook._pool_wide(run_exp.make_wide_recipe(task), sites, run_exp.TARGET[task], progress_label=f"{task} wide")
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        pool.to_parquet(cache, index=False)
        print(f"  pool {task}: {pool.shape} -> {cache}")
    return pool


# ── relative units ────────────────────────────────────────────────────────────


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
        if name not in ("lam", "mcw"):
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

    The reference is the config this stage INHERITED -- `fixed` applied, no rung of the searched axis -- not the bare provisional config, so a stage that loses ground compares against what it walked in with rather than against something two stages back. Its axis point is empty on purpose: if it wins, `_winner_axes` threads nothing forward, which is exactly "no rung beat what I had".
    """
    ladders = {a: ladders.get(a) or _AXES[a][1] for a in search}
    out: list[tuple[dict, dict]] = [({}, _materialize(fixed, ctx, absolute))]
    if not search:  # confirmation stage: the product below would yield the reference again
        return out
    for combo in itertools.product(*(ladders[a] for a in search)):
        pt = {**fixed, **dict(zip(search, combo))}
        out.append((pt, _materialize(pt, ctx, absolute)))
    return out


def _label(pt: dict, cfg: dict) -> str:
    """Human-readable config name; lam/mcw print as relative(absolute). Load-bearing -- delta_vs_default keys on it."""
    if not pt:
        return "DEFAULT (inherited)"
    bits = []
    for k, v in pt.items():
        if k in ("lam", "mcw"):
            bits.append(f"{k}={v:g}({cfg[_AXES[k][0]]:.0f})")
        else:
            bits.append(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}")
    return ", ".join(bits)


# ── prefix scoring ────────────────────────────────────────────────────────────


def _base_margin(m, task):
    """The constant every `output_margin` prediction carries, in MARGIN space.

    `base_score` is stored in PROBABILITY space for binary:logistic, so it must be logit-ed before it can be subtracted from a margin -- raw, it shifts every CLF prefix by logit(p)-p, silently and with plausible-looking numbers. It also serialises as a bracketed string, hence the strip.
    """
    b = float(json.loads(m.get_booster().save_config())["learner"]["learner_model_param"]["base_score"].strip("[]"))
    return float(np.log(b / (1 - b))) if task == "clf" else b


def _prefix_oofs(models, X, task, ks, n):
    """{k: out-of-fold vector} for every prefix length in `ks`, from ONE pass of tree slices per fold.

    Boosting margins are additive and iteration_range=(a,b) costs O(b-a), so the margin at each k is the running sum of slice predictions rather than a fresh walk from tree 0 -- O(max k) per fold instead of O(sum k).
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


def _fold_specs(pool, n_splits, true_lofo, max_holdout_pct):
    """(family folds, family group vector) for the LOFO holdout every score here is computed on."""
    fam = cook.basin_groups(pool["site"])
    folds = list(cook.folds_true_lofo(fam, max_holdout_pct)) if true_lofo else list(cook.folds_grouped(fam, n_splits))
    return folds, fam


def _score_pool_curve(pool, feat, target, task, metric, ceiling, n_splits, true_lofo, max_holdout_pct,
                      coarse=_COARSE, fine=_FINE, loso=False, **cfg):
    """Score a config, choosing its tree count rather than being told it. -> (metrics_at_best_k, best_k, curve).

    Fit each fold once at `ceiling`, then score every PREFIX on the holdout -- no refits, so the whole curve plus the final metric set costs the one CV pass. Scanned rather than bisected because the curve is not unimodal; refined around the top TWO coarse points for the same reason. `metric` is a bare cook._score key (r2/prauc).
    """
    X, y = pool[feat], cook._target(pool, target, task)
    fam_folds, _fam = _fold_specs(pool, n_splits, true_lofo, max_holdout_pct)
    kw = dict(n_estimators=ceiling, **cfg)

    fam_models = [(m, te) for te, m in cook._fold_models(X, y, fam_folds, task, **kw)]

    def curve_over(ks):
        ks = [k for k in ks if k > 0]
        if not ks:  # the refine windows can land entirely inside the coarse grid; _prefix_oofs indexes ks[0]
            return {}
        got = _prefix_oofs(fam_models, X, task, ks, len(y))
        return {k: (cook._score(y, oof, task)[metric], oof) for k, oof in got.items()}

    scan = curve_over(range(coarse, ceiling + 1, coarse))
    ranked = sorted(scan, key=lambda k: scan[k][0], reverse=True)
    windows: set[int] = set()
    for k0 in ranked[:2]:
        windows.update(range(max(fine, k0 - coarse), min(ceiling, k0 + coarse) + 1, fine))
    scan.update(curve_over(sorted(windows - set(scan))))
    # SMALLEST k within the cost rule's tolerance of the peak, not the bare argmax. The prefix curve is
    # flat over most of its length on this pool, and an argmax over a plateau is a draw from noise -- it
    # pins nearly every config to its ceiling, which is how the feature-manifest tuner ended up settling
    # at 26,530 trees for +0.0023. The tolerance is the same rule that ranks the grid: giving up `d` is
    # free if the cheaper k would have to buy more than NOISE_CEILING per doubling to justify itself.
    # Depth and the sampling fractions are fixed within a config, so the cost ratio IS the tree ratio.
    k_peak = max(scan, key=lambda k: scan[k][0])
    peak = scan[k_peak][0]
    best_k = min(
        (k for k in scan if k <= k_peak and peak - scan[k][0] <= NOISE_CEILING * math.log2(k_peak / max(k, 1))),
        default=k_peak,
    )
    if best_k != k_peak:
        print(f"      k={best_k} chosen over the argmax k={k_peak} "
              f"({peak - scan[best_k][0]:+.4f} for {k_peak / max(best_k, 1):.1f}x fewer trees)", flush=True)
    oof_family = scan[best_k][1]

    # The LOSO pass is a FULL second CV whose only product is the leakage gap, and ranking is on LOFO alone -- so it is off per config. Off, oof_site stays all-NaN and _cross_metrics emits NaN loso_*, keeping the column set intact.
    oof_site = np.full(len(y), np.nan)
    if loso:
        site_models = [(m, te) for te, m in cook._fold_models(X, y, cook.folds_grouped(pool["site"], n_splits), task, **kw)]
        oof_site = _prefix_oofs(site_models, X, task, [best_k], len(y))[best_k]

    metrics = dict(
        n_sites=pool["site"].nunique(),
        n_rows=len(pool),
        n_feat=len(feat),
        **cook._cross_metrics(pool, np.asarray(y), oof_site, oof_family, task),
    )
    return metrics, best_k, {k: v[0] for k, v in sorted(scan.items())}


def _fit_cost(best_k, cfg: dict) -> float:
    """Relative cost of one fit: trees x leaves x the fraction of rows and columns each tree sees.

    Analytic rather than measured wall-clock ON PURPOSE -- the same grid must settle the same way regardless of what else the machine is doing, and this box is shared. Ignores feature count, constant within a run.
    """
    return float(best_k) * (2 ** int(cfg["max_depth"])) * float(cfg["subsample"]) * float(cfg["colsample_bytree"])


def _penalized(score: float, cost: float) -> float:
    """Headline discounted by NOISE_CEILING per doubling of fit cost: score - NC*log2(cost).

    Reference-free -- the log turns the ratio rule into a subtraction, so A beats B exactly when B fails to buy more than NOISE_CEILING per doubling over A, with no baseline nominated.
    """
    return float(score) - NOISE_CEILING * math.log2(max(float(cost), 1e-9))


def _grow(ceiling: int, max_ceiling: int) -> int:
    """Next ceiling up, floored to a multiple of _COARSE and capped. Aligned because the scan grid is range(_COARSE, ceiling+1, _COARSE)."""
    return min(max_ceiling, max(ceiling + _COARSE, int(ceiling * _GROW) // _COARSE * _COARSE))


def _score_growing(pool, feat, target, task, metric, ceiling, max_ceiling, n_splits, true_lofo, max_holdout_pct,
                   loso, **cfg):
    """Score a config, refitting at a larger ceiling while the tree count is chosen by the bound rather than the data. -> (metrics, best_k, curve, ceiling_used, note).

    PER CONFIG. A config whose best_k sits well under its ceiling is already finished -- prefix scoring means a larger ceiling would pick the same k off the same curve -- so only the bound row is redone. Growth stops when the count stops binding, when the raise stops paying under _penalized, or at max_ceiling; the middle case is the one that matters, since an unregularized config absorbs trees indefinitely and only a cost rule ever tells it to stop.
    """
    kw = dict(coarse=_COARSE, fine=_FINE, loso=loso, **cfg)
    best = _score_pool_curve(pool, feat, target, task, metric, ceiling, n_splits, true_lofo, max_holdout_pct, **kw)
    used, note = ceiling, ""
    while best[1] >= _BIND * used:
        if used >= max_ceiling:
            note = f"bound at the max-ceiling {max_ceiling}"
            break
        nxt = _grow(used, max_ceiling)
        trial = _score_pool_curve(pool, feat, target, task, metric, nxt, n_splits, true_lofo, max_holdout_pct, **kw)
        gain = trial[0][_HEADLINE[task]] - best[0][_HEADLINE[task]]
        # Same rule that ranks the grid. Depth and the sampling fractions are identical across the two fits, so the cost ratio is exactly the tree ratio.
        if gain <= NOISE_CEILING * math.log2(max(trial[1], 1) / max(best[1], 1)):
            note = f"raise to {nxt} rejected: {gain:+.4f} for {trial[1] / max(best[1], 1):.1f}x trees"
            break
        best, used = trial, nxt
    return (*best, used, note)


# ── ladder edges ──────────────────────────────────────────────────────────────


def _edge_of(ax: str, v: float, ladder) -> int:
    """-1 on the ladder's bottom rung, +1 on its top, 0 otherwise. A hard limit or an inert floor is NOT an edge -- the axis ran out of meaningful room rather than the search being truncated."""
    lad = sorted(ladder)
    if v is None or pd.isna(v) or len(lad) < 2:
        return 0
    lo_lim, hi_lim, inert_floor = _AXIS_LIMIT[ax]
    if v >= lad[-1]:
        return 0 if (hi_lim is not None and lad[-1] >= hi_lim) else 1
    if v <= lad[0]:
        return 0 if (inert_floor or lad[0] <= lo_lim) else -1
    return 0


def _next_rung(ax: str, ladder, direction: int) -> float | None:
    """One rung past the edge of `ladder`, arithmetic in the ladder's own units, clipped to the axis's hard limits. None when the limit is already reached."""
    lad = sorted(ladder)
    lo_lim, hi_lim, _ = _AXIS_LIMIT[ax]
    if direction > 0:
        nxt = round(lad[-1] + (lad[-1] - lad[-2]), 6)
        if hi_lim is not None:
            nxt = min(nxt, hi_lim)
        return None if nxt <= lad[-1] else nxt
    nxt = round(lad[0] - (lad[1] - lad[0]), 6)
    return None if nxt <= lo_lim else nxt


def _expand_edges(score_point, rows, search, ladders, fixed, ctx, absolute, head) -> None:
    """Walk each searched axis outward past a ladder edge, one rung at a time, while the steps keep paying under _penalized. Scores in place via `score_point`.

    An edge winner means the search was TRUNCATED, not that an optimum was found. Axes are walked SEQUENTIALLY from the running best, not as a joint product: stage 2 crosses depth x lam and both can be at an edge, and a product costs O(steps^2) fits to answer what two independent walks answer in O(steps).
    """
    for ax in search:
        lad = list(sorted(ladders.get(ax) or _AXES[ax][1]))
        best = max(rows, key=lambda r: _penalized(r[head], r["fit_cost"]))  # re-read AFTER any earlier axis moved
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
                print(f"  [expand] {ax}={nxt:g} rejected: {gain:+.4f} for {cost / max(cur_cost, 1e-9):.1f}x cost")
                break
            lad = sorted(lad + [nxt])
            cur_score, cur_cost = score, cost
            if step == _MAX_EXPAND:
                print(f"  [expand] {ax} still improving after {_MAX_EXPAND} steps -- the ladder was badly truncated")


def _edge_report(df: pd.DataFrame, search: list[str], ladders: dict, ceiling: int) -> None:
    """Warn when the winner sits on the edge of a searched ladder, or on the tree ceiling. Extending is left to the caller."""
    best = df.iloc[0]
    notes = []
    used = int(best.get("ceiling", ceiling))
    if pd.notna(best.get("best_k")) and best["best_k"] >= _BIND * used:
        why = str(best.get("ceiling_note") or "")
        if why.startswith("raise to"):
            notes.append(f"best_k={int(best['best_k'])} fills the {used} ceiling, but growth was declined on cost ({why}) "
                         f"-- the count is capped by NOISE_CEILING={NOISE_CEILING}, not by the scan bound.")
        else:
            notes.append(f"best_k={int(best['best_k'])} is >={_BIND:.0%} of the {used} ceiling -- the ceiling is binding, "
                         f"not chosen." + (f" [{why}]" if why else ""))
    for ax in search:
        v = best.get(f"ax_{ax}")
        lad = sorted(ladders.get(ax) or _AXES[ax][1])
        if v is None or pd.isna(v) or len(lad) < 2:
            continue
        lo_lim, hi_lim, inert_floor = _AXIS_LIMIT[ax]
        if v >= lad[-1] and (hi_lim is None or lad[-1] < hi_lim):
            notes.append(f"{ax}={v:g} is the TOP of its ladder {lad} -- untruncated optimum unknown.")
        elif v <= lad[0]:
            if inert_floor:
                notes.append(f"{ax}={v:g} is the bottom of its ladder, and that low it is already inert -- "
                             f"read as 'no {ax} helps', not as a truncated search.")
            elif lad[0] > lo_lim:
                notes.append(f"{ax}={v:g} is the BOTTOM of its ladder {lad} -- untruncated optimum unknown.")
    for n in notes:
        print(f"  [edge] {n}")


# ── one stage ─────────────────────────────────────────────────────────────────


def _ctx(pool, task, n_splits, true_lofo, max_holdout_pct, base):
    """The constants the relative ladders resolve against: row count, training fraction and per-row Hessian.

    Hessian per row is exactly 1 for squared error and p(1-p) for logistic at the pool's own base rate -- the entire reason one relative ladder yields different absolute values per task.
    """
    y = cook._target(pool, run_exp.TARGET[task], task)
    folds, fam = _fold_specs(pool, n_splits, true_lofo, max_holdout_pct)
    n_fam = int(pd.Series(fam).nunique())
    if n_fam < 2:
        raise SystemExit(f"{task}: only {n_fam} basin family in the pool -- every LOFO fold is empty.")
    p = float(np.mean(y)) if task == "clf" else float("nan")
    train_frac = float(np.mean([len(tr) for tr, _ in folds])) / len(pool) if folds else 0.8
    return dict(
        n_rows=len(pool),
        n_families=n_fam,
        train_frac=train_frac,
        hess_per_row=(p * (1 - p) if task == "clf" else 1.0),
        default_depth=base["max_depth"],
        default_subsample=base["subsample"],
    )


def tune(task, pool, feat, search, fixed, ladders, absolute, ceiling, max_ceiling, seeds, n_splits,
         true_lofo, max_holdout_pct, append, expand, csv_path, curve_path, base, ctx, stage_label=""):
    """Grid-search ONE stage over the shared pool. -> the stage's ranked frame, best row first.

    Every row is written to `csv_path` as it completes, so a crash keeps what it has already paid for.
    """
    head = _HEADLINE[task]
    target = run_exp.TARGET[task]
    grid = _grid(search, fixed, ladders, ctx, absolute)

    print(f"\n=== {task.upper()} {stage_label}  pool {pool.shape} -> {len(feat)} features  |  "
          f"{pool['site'].nunique()} sites, {ctx['n_families']} families  |  {len(grid)} configs x {len(seeds)} seed(s) ===")
    print(f"    searching {search or ['(none)']}" + (f", fixed {fixed}" if fixed else "") + f"  |  ranking on {head}_adj")
    if not absolute:
        scale = "  ".join(f"d{d}:{_leaf_hessian(d, ctx['default_subsample'], ctx['n_rows'], ctx['train_frac'], ctx['hess_per_row']):,.0f}"
                          for d in (3, 4, 5, 6))
        print(f"    leaf Hessian ({ctx['n_rows'] * ctx['train_frac'] * ctx['default_subsample']:,.0f} rows/tree, "
              f"{ctx['hess_per_row']:.4f}/row) -> {scale}   [relative units; c=0.1 is a 9% leaf shrink]")
    print(f"    trees: scanned to a {ceiling} ceiling per config, grown per config up to {max_ceiling}, "
          f"coarse {_COARSE} then fine {_FINE}")
    print(f"    cost rule: a doubling of trees x leaves x samples must buy > {NOISE_CEILING} {head}", flush=True)

    rows, curves, t0 = [], [], time.time()
    # Snapshot the earlier stages ONCE. Re-reading the file on every flush would concatenate this stage's growing row list onto a file that already holds its own earlier rows, duplicating every config as many times as there are configs after it.
    prior = pd.read_csv(csv_path) if (append and csv_path.exists()) else None

    def flush(frame: pd.DataFrame | None = None):
        df = pd.DataFrame(rows) if frame is None else frame
        if prior is not None:
            df = pd.concat([prior, df], ignore_index=True)
        df.to_csv(csv_path, index=False)

    def score_point(pt: dict, cfg: dict, tag: str) -> tuple[float, float]:
        """Score one axis point across every seed: append its rows and curves, print its line. -> (mean headline, mean cost)."""
        t1 = time.time()
        for seed in seeds:
            kw = {**cfg, "random_state": seed}
            r, best_k, curve, used, note = _score_growing(
                pool, feat, target, task, _CURVE_METRIC[task], ceiling, max_ceiling, n_splits, true_lofo,
                max_holdout_pct, False, **kw,
            )
            eff = {**base, **kw, "n_estimators": best_k}
            rows.append({
                "config": _label(pt, cfg),
                "stage": stage_label,
                "searched": ",".join(search) or "none",
                "arm": TUNE_ARM,
                "seed": seed,
                head: r[head],
                **{k: r.get(k) for k in _ALSO[task]},
                "best_k": best_k,
                "fit_cost": round(_fit_cost(best_k, eff), 1),
                "ceiling": used,
                "k_frac": round(best_k / used, 3),
                "ceiling_bound": bool(best_k / used >= _BIND),
                "ceiling_note": note,
                "at_ceiling": round(curve[max(curve)], 4),
                "n_feat": r["n_feat"],
                "secs": round(time.time() - t1, 1),
                **{f"ax_{k}": v for k, v in pt.items()},
                **eff,  # the EFFECTIVE config, not just the overrides
            })
            curves.extend({"config": _label(pt, cfg), "stage": stage_label, "seed": seed, "k": k, head: v}
                          for k, v in curve.items())
        mine = rows[-len(seeds):]
        got, ks = [x[head] for x in mine], [x["best_k"] for x in mine]
        seed_note = f"  [{len(seeds)} seeds, sd {np.std(got):.4f}]" if len(seeds) > 1 else ""
        note = mine[-1]["ceiling_note"]
        print(f"  [{tag}] {_label(pt, cfg):<48.48} {head}={np.mean(got):.4f}  k={int(np.mean(ks)):>5}"
              f"  (vs {np.mean([x['at_ceiling'] for x in mine]):+.4f} at {mine[-1]['ceiling']})"
              f"{seed_note}  ({time.time() - t1:.0f}s)" + (f"\n        [ceiling] {note}" if note else ""), flush=True)
        flush()
        return float(np.mean(got)), float(np.mean([x["fit_cost"] for x in mine]))

    for i, (pt, cfg) in enumerate(grid, 1):
        score_point(pt, cfg, f"{i}/{len(grid)}")

    if expand and search:
        _expand_edges(score_point, rows, search, ladders, fixed, ctx, absolute, head)

    df = pd.DataFrame(rows)
    if len(seeds) > 1:
        # Rank on the seed MEAN: the max over a grid of noisy single-seed scores is optimistically biased, and subsample<1 makes every fit seed-dependent. Only numeric columns are averaged -- a None/str config column would make .mean() raise AFTER every fit is paid for.
        num = [c for c in df.columns if c != "config" and pd.api.types.is_numeric_dtype(df[c])]
        rest = [c for c in df.columns if c != "config" and c not in num]
        df = df.groupby("config", as_index=False).agg({**{c: "mean" for c in num}, **{c: "first" for c in rest}})
        df["n_seeds"] = len(seeds)
    df.insert(2, f"{head}_adj", (df[head] - NOISE_CEILING * np.log2(df["fit_cost"].clip(lower=1e-9))).round(4))
    # DEFAULT LOSES TIES: the inherited config is usually also a ladder rung, and when the tie went to DEFAULT it carried no ax_* values -- so nothing was threaded forward, the edge report skipped the axis, and a searched axis was reported as untuned.
    df["_default_last"] = (df["config"] == "DEFAULT (inherited)").astype(int)
    df = df.sort_values([f"{head}_adj", "_default_last"], ascending=[False, True]).drop(columns="_default_last")
    df = df.reset_index(drop=True)
    ref = df.loc[df["config"] == "DEFAULT (inherited)", head]
    if len(ref):
        df.insert(3, "delta_vs_inherited", (df[head] - float(ref.iloc[0])).round(4))

    flush(df)  # the RANKED frame, so the audit CSV carries _adj and the delta alongside the raw rows
    if curves:
        cdf = pd.DataFrame(curves)
        if append and curve_path.exists():
            cdf = pd.concat([pd.read_csv(curve_path), cdf], ignore_index=True)
        cdf.to_csv(curve_path, index=False)

    print(f"\n  best: {df.iloc[0]['config']}  ({head}={df.iloc[0][head]:.4f}, adj={df.iloc[0][f'{head}_adj']:.4f}, "
          f"k={int(df.iloc[0]['best_k'])})")
    _edge_report(df, search, ladders, ceiling)
    print(f"  stage done in {time.time() - t0:.0f}s -> {csv_path.name}", flush=True)
    return df


def _winner_axes(df: pd.DataFrame) -> dict:
    """Axis-space values of the top-ranked row, for threading into the next stage's `fixed`.

    Only axes the row actually carries. A DEFAULT (inherited) winner contributes nothing, which is right: it means the inherited value beat every rung, and leaving it unpinned inherits exactly that.
    """
    best = df.iloc[0]
    return {a: float(best[f"ax_{a}"]) for a in _AXES if f"ax_{a}" in df.columns and pd.notna(best.get(f"ax_{a}"))}


_JSON_INT = {"max_depth", "n_estimators", "random_state"}  # written as ints, not 5.0 / 42.0


def _effective(df: pd.DataFrame, base: dict) -> dict:
    """The winning row as a complete, JSON-ready XGBoost config, tree count included.

    Values come off a pandas row, so they are numpy scalars -- cast on the way out or json.dumps raises on np.float64 and XGBoost is handed max_depth=5.0.
    """
    best = df.iloc[0]
    out = dict(base)
    for k in ("learning_rate", "max_depth", "min_child_weight", "subsample", "colsample_bytree", "reg_lambda",
              "reg_alpha", "random_state"):
        if k in df.columns and pd.notna(best.get(k)):
            out[k] = best[k]
    out["n_estimators"] = int(best["best_k"])
    return {k: (int(v) if k in _JSON_INT else round(float(v), 6)) for k, v in out.items()}


# ── the driver ────────────────────────────────────────────────────────────────


def full(task, pool, ceiling, max_ceiling, seeds, n_splits, true_lofo, max_holdout_pct, absolute, expand, ladders):
    """Run the six stages for one task over one pool. -> (settled config, final stage frame, settled axes)."""
    masks = A.build_masks(pool, task)
    if TUNE_ARM not in masks:
        raise SystemExit(f"{TUNE_ARM!r} is not an arm ({sorted(masks)[:4]} ...)")
    feat = sorted(masks[TUNE_ARM])
    base = {k: v for k, v in PROVISIONAL[task].items() if k != "n_estimators"}
    ctx = _ctx(pool, task, n_splits, true_lofo, max_holdout_pct, base)
    csv_path, curve_path = _HERE / f"tune_{task}.csv", _HERE / f"tune_{task}_curve.csv"

    kw = dict(ladders=ladders, absolute=absolute, ceiling=ceiling, max_ceiling=max_ceiling, seeds=seeds,
              n_splits=n_splits, true_lofo=true_lofo, max_holdout_pct=max_holdout_pct, expand=expand,
              csv_path=csv_path, curve_path=curve_path, base=base, ctx=ctx)

    t0 = time.time()
    df = tune(task, pool, feat, ["depth", "lr"], {}, append=False, stage_label="STAGE 1/6 depth x lr", **kw)
    fixed = _winner_axes(df)
    print(f"  >> stage 1 best_k ranged {int(df.best_k.min())}-{int(df.best_k.max())}", flush=True)

    for i, search in enumerate(_STAGES, start=2):
        # EVERY stage starts at the SAME ceiling and grows per config. Starting from stage 1's largest best_k would make a config that wants 100 trees pay for a scan to an outlier's 6000.
        fx = {k: v for k, v in fixed.items() if k not in search}
        df = tune(task, pool, feat, search, fx, append=True, stage_label=f"STAGE {i}/6 {'+'.join(search)}", **kw)
        fixed.update(_winner_axes(df))

    # The settled combination may never have been scored as a unit by the coordinate stages, and it can want more trees than any single-axis winner did.
    df = tune(task, pool, feat, [], fixed, append=True, stage_label="STAGE 6/6 confirmation", **kw)
    settled = _effective(df, base)
    print(f"\n{'=' * 78}\n{task.upper()} settled in {(time.time() - t0) / 60:.0f} min   axes: {fixed}\n  {settled}\n{'=' * 78}", flush=True)
    return settled, df, fixed


# ── the narrow-arm sanity check ───────────────────────────────────────────────


def sanity(task, pool, settled, n_splits, true_lofo, max_holdout_pct):
    """Score the NARROWEST arm at the settled config AND at the provisional island one. -> a dict for `_provenance`.

    One config is shared by all 27 arms, so a config picked on the widest arm could be buying donor-arm accuracy with island-arm accuracy. This is the measurement that says whether it did: both configs at their OWN tree count, on `island_<TASK>`, same rows and same folds. A loss larger than the task's noise floor is a finding, not a rounding error, and is reported rather than absorbed.
    """
    head, arm = _HEADLINE[task], f"island_{task.upper()}"
    feat = sorted(A.build_masks(pool, task)[arm])
    out = {"arm": arm, "n_feat": len(feat), "headline": head, "noise_floor": NOISE_FLOOR[task]}
    for name, cfg in (("settled", settled), ("provisional", PROVISIONAL[task])):
        t0 = time.time()
        r = cook._score_pool(pool, feat, run_exp.TARGET[task], task, n_splits, loso=False, true_lofo=true_lofo,
                             max_holdout_pct=max_holdout_pct, extra_importance_test=False,
                             **{k: v for k, v in cfg.items()})
        out[name] = round(float(r[head]), 6)
        out[f"{name}_n_estimators"] = int(cfg["n_estimators"])
        print(f"  [sanity] {arm} @ {name:<11} {head}={r[head]:.4f}  (n_estimators={cfg['n_estimators']}, {time.time() - t0:.0f}s)", flush=True)
    out["delta"] = round(out["settled"] - out["provisional"], 6)
    out["costs_narrow_arm"] = bool(out["delta"] < -NOISE_FLOOR[task])
    return out


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True, cwd=_HERE).strip()
    except Exception:
        return "unknown"


def write_config(configs: dict, prov: dict) -> None:
    """Write xgb_config.json: the two settled configs plus a `_provenance` block naming the arm, cohort and sanity result."""
    out = {"_provenance": prov}
    out.update({t: configs[t] for t in ("reg", "clf") if t in configs})
    CONFIG_PATH.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwrote {CONFIG_PATH}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=["reg", "clf", "both"], default="both")
    ap.add_argument("--sites", type=int, default=None, help="cap to the first N sites (shakedown only -- it changes the family structure and so the LOFO difficulty)")
    ap.add_argument("--ceiling", type=int, default=1500, help="STARTING tree ceiling for every stage; each config grows its own from here")
    ap.add_argument("--max-ceiling", type=int, default=_MAX_CEILING, help=f"hard stop for that growth (default {_MAX_CEILING})")
    ap.add_argument("--seeds", default="42", help="comma-separated random_state values; >1 ranks on the seed mean")
    ap.add_argument("--n-splits", type=int, default=5)
    ap.add_argument("--true_lofo", action="store_true", help="one family per fold instead of GroupKFold")
    ap.add_argument("--max_holdout_pct", type=float, default=0.2)
    ap.add_argument("--absolute", action="store_true", help="read the lam/mcw ladders as raw XGBoost units instead of fractions of the per-leaf Hessian")
    ap.add_argument("--automatic-expansion", action="store_true", help="walk one rung past a ladder edge and re-score while the steps keep paying")
    ap.add_argument("--pool-cache", type=Path, default=None, help="directory to park the built pool in as parquet")
    ap.add_argument("--write", action="store_true", help="write xgb_config.json (needs --task both, so both configs exist)")
    ap.add_argument("--sanity-only", action="store_true", help="skip the search; re-run the narrow-arm check for the config already in xgb_config.json")
    ap.add_argument("--notes", default="", help="extra text for _provenance.notes")
    for axis in _AXES:
        ap.add_argument(f"--{axis}s", default=None, help=f"ladder for the {axis} axis (default {_AXES[axis][1]})")
    a = ap.parse_args()

    ladders = {ax: (None if getattr(a, f"{ax}s") is None else
                    tuple((int if ax == "depth" else float)(x) for x in getattr(a, f"{ax}s").split(",")))
               for ax in _AXES}
    seeds = tuple(int(x) for x in a.seeds.split(","))
    sites = [str(s) for s in get_site_ids()]
    if a.sites:
        sites = sites[: a.sites]
    tasks = ["reg", "clf"] if a.task == "both" else [a.task]

    print(f"tuning {tasks} on {len(sites)} sites against arm {TUNE_ARM!r}  (seeds={seeds}, ceiling={a.ceiling}->{a.max_ceiling})")
    t0 = time.time()
    configs, checks = {}, {}
    for task in tasks:
        pool = build_pool(task, sites, a.pool_cache)
        if a.sanity_only:
            cfg = {k: v for k, v in json.loads(CONFIG_PATH.read_text())[task].items() if not k.startswith("_")}
            checks[task] = sanity(task, pool, cfg, a.n_splits, a.true_lofo, a.max_holdout_pct)
            configs[task] = cfg
            continue
        cfg, _df, _axes = full(task, pool, a.ceiling, a.max_ceiling, seeds, a.n_splits, a.true_lofo,
                               a.max_holdout_pct, a.absolute, a.automatic_expansion, ladders)
        configs[task] = cfg
        checks[task] = sanity(task, pool, cfg, a.n_splits, a.true_lofo, a.max_holdout_pct)

    mins = (time.time() - t0) / 60
    print(f"\nall tasks done in {mins:.0f} min")
    print(json.dumps({"configs": configs, "sanity": checks}, indent=2, default=float))

    if a.write:
        hurt = [t for t, c in checks.items() if c.get("costs_narrow_arm")]
        note = (f"Tuned on the widest arm {TUNE_ARM} (one config per task, shared by all 27 arms, so every delta "
                f"isolates the feature set). Staged coordinate search (depth x lr, depth x lam, mcw, subsample, "
                f"colsample, confirmation) with relative lam/mcw ladders; tree count resolved by prefix scan, not "
                f"searched; ranked on {{'reg': 'lofo_r2', 'clf': 'lofo_prauc'}} discounted by NOISE_CEILING="
                f"{NOISE_CEILING} per doubling of fit cost. Narrow-arm check (island arm, settled vs the previous "
                f"provisional config): "
                + "; ".join(f"{t} {c['delta']:+.4f} {c['headline']} (floor {c['noise_floor']})" for t, c in checks.items())
                + (f". WARNING -- {', '.join(hurt)} loses more than one noise floor on the narrow arm at the shared "
                   f"config: a single config is genuinely costing that end of the ladder." if hurt else
                   ". No task loses more than one noise floor on the narrow arm.")
                + (f" {a.notes}" if a.notes else ""))
        write_config(configs, {
            "status": "tuned",
            "arm": TUNE_ARM,
            "n_sites": len(sites),
            "git_sha": _git_sha(),
            "written": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tuner": "tune_config.py",
            "search_minutes": round(mins, 1),
            "sanity": checks,
            "notes": note,
        })


if __name__ == "__main__":
    main()
