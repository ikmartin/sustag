"""tune.py -- grid-search XGBoost hyperparameters for the shipped recipes against the HONEST LOFO metric.

Every config is ranked by its leave-one-family-out score -- `lofo_r2` for regression, `lofo_prauc` for classification -- the honest end of the bracket (lofo <= real generalisation <= loso). The recipe's pool is the expensive part, so it is built ONCE and every config re-uses the same X / y / folds: a config costs only its CV fits, and because all configs share one pool the comparison is exactly paired.

THE TREE COUNT IS NOT AN AXIS -- it is resolved per config. Early stopping used to choose it, and chose it badly (see src/eval/cook.py's module docstring), so every config now fits each fold once at a `--ceiling` and scores every PREFIX of that fit on the holdout. Each config reports the tree count it actually wanted (`best_k`), its score there, and `k_frac = best_k / ceiling` -- a k_frac near 1.0 means the ceiling is binding rather than chosen, and should be raised. This costs the same CV pass scoring already paid, versus ~log2(ceiling) x n_splits fits for a bisection, and unlike a bisection it cannot be fooled by the measured curve, which is not unimodal.

WHICH AXES. `--search` names the axes crossed into the grid; every other axis takes its value from `--fix`, else from the recipe's inherited config. Staging is therefore a sequence of INVOCATIONS, not edits. Axes: depth, lr, lam, mcw, subsample, colsample.

    # Stage 1 -- capacity x learning rate
    python src/models/tune.py --task reg --search depth,lr --ceiling 1500
        -> read the best config AND its k_frac. k_frac > ~0.8 means the ceiling is binding, not chosen: raise --ceiling and repeat before trusting anything.

    # Stage 2 -- the one genuine cartesian, at stage 1's lr
    python src/models/tune.py --task reg --search depth,lam --fix lr=0.02 --append

    # Stage 3 -- coordinate, each pinning everything settled so far
    python src/models/tune.py --task reg --search mcw --fix depth=4,lr=0.02,lam=0.1 --append

Or run the whole protocol with `python src/models/fulltune.py --task reg`.

WHY THE AXES ARE NOT ALL INDEPENDENT. Two regimes, and the grid design follows from them:

- `max_depth` x {`reg_lambda`, `min_child_weight`} are strongly coupled in RAW units, because both regularizers compete against a leaf's accumulated Hessian and that scales as N/2^depth. Fixing lambda at one depth and then optimizing depth would be a genuine mistake -- you would never visit (deep, large-lambda), which is measurably where the optimum lives: at c=0.002 (regularization off) depths 3/4/5/6 score 0.3926/0.3960/0.3967/0.3915, flat, while at c=0.3 they score 0.3861/0.4095/0.4194/0.4222. Depth is worth nothing until lambda is real. Hence lam/mcw are expressed RELATIVELY (below) and crossed with depth anyway.
- `min_child_weight` x `reg_lambda`, and the sampling fractions against everything else, are only weakly coupled -- one blocks splits, the other shrinks the leaves of splits that survive. Coordinate descent is sound for these.

`learning_rate` and `n_estimators` USED to be a third, totally-coupled regime -- with early stopping never firing, every config ran its full budget, so only the product `lr x n_trees` mattered. Resolving the tree count per config dissolves that: lr is a learning rate again and the optimal count simply adapts to it.

RELATIVE UNITS. `--lams` and `--mcws` are given as a FRACTION of the expected per-leaf Hessian, not in raw XGBoost units, and are converted per-config once that config's depth and subsample are known. Leaf weight is -G/(H+lambda), so lambda = c x H yields a shrink of 1/(1+c) at EVERY depth: the axis becomes depth-invariant, which is what lets a modest cartesian cover the space. Two consequences worth knowing:

- It exposes how inert the historical values were. reg_lambda=5 at depth 4 is c=0.0009 -- a 0.09% shrink. min_child_weight=10 is 0.2% of a typical leaf. Nothing in a sweep over those was ever going to move.
- It unifies the REG and CLF ladders. REG's Hessian is exactly 1/row; CLF's is p(1-p), ~0.17 at the observed base rate, so the same relative c produces the ~5.7x smaller absolute CLF value automatically. One ladder, both tasks.

OUTPUT. Two files, neither of them an input to training. `models/tune_<recipe>.csv` is the ranked grid (`--append` accumulates stages); every row carries the `ceiling` it was measured at and a `ceiling_bound` flag, so an accumulated file stays readable across stages and ceiling retries -- a bound row is a truncated measurement, not a result. `models/tuning_logs.jsonl` records EVERY config scored, one JSON object per line appended as the sweep runs, so a crash keeps what it had already paid for and two concurrent sweeps cannot lose each other's entries. A winner reaches a fitted model only by being pasted into `train.RECIPE_XGB`, which every run prints ready to copy, tree count included.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root on path

import src.eval.cook as cook
from src.data.access import get_site_ids

# ANCHORED to this file, not the CWD. train.OUT uses the same anchoring; a relative Path("models") wrote wherever tune.py happened to be invoked from, so the artifacts only landed correctly when run from src/models/ and silently went nowhere otherwise.
_HERE = Path(__file__).resolve().parent
OUT = _HERE / "models"
LOG_FILE = OUT / "tuning_logs.jsonl"  # every config ever scored, one JSON object per line, oldest first

# (task, variant) -> (recipe attr on src.features.recipes, target column). Keyed on the pair because island and network are separate models with separate tuned configs, not one model with an option.
#
# The recipe is an attribute NAME resolved by getattr at run time, so a stale entry here is an AttributeError hours into a sweep rather than at import. Keep it in step with train._RECIPES.
_TASK_SPEC = {
    ("reg", "island"): ("island_REG", "nitrate_con"),
    ("clf", "island"): ("island_CLF", "violation"),
    ("reg", "network"): ("network_REG", "nitrate_con"),
    ("clf", "network"): ("network_CLF", "violation"),
}

# The metric each task is ranked by -- the same names train.py reports as its headline, so a config that wins here is a config that wins there. CLF ranks on prauc rather than auc: at a ~26% violation base rate the two disagree about what "best" means, and average precision is the quantity a rare-event flag is judged on.
_HEADLINE = {"reg": "lofo_r2", "clf": "lofo_prauc"}
# The same quantity under the name cook._score returns it by. _score emits bare metric names (r2, prauc, auc, rmse); the lofo_/loso_ prefixes are attached later by _cross_metrics, which is what the scoreboard reports. The tree-count scan calls _score directly -- one metric, thousands of times -- so it needs the bare name.
_CURVE_METRIC = {"reg": "r2", "clf": "prauc"}
# The metric the surviving loso_* column carries, which is NOT _CURVE_METRIC for CLF: _cross_metrics emits loso_auc there while the ranking is on prauc. Writing the winner's leakage gap under f"loso_{_CURVE_METRIC[task]}" would invent a loso_prauc column no other row has and leave the loso_auc every consumer reads empty.
_LOSO_METRIC = {"reg": "r2", "clf": "auc"}
# Reported next to the headline, so a config that buys LOFO by widening the leakage gap is visible. lofo_between_r2 leads for REG on purpose: depth is bought largely in the between-site ranker, which is the known bottleneck.
_ALSO = {
    "reg": ["lofo_between_r2", "lofo_within_r2", "loso_r2", "lofo_rmse"],
    "clf": ["loso_auc", "lofo_auc", "lofo_prauc_lift", "lofo_between_rate_r2"],
}
_INT_KEYS = {"max_depth", "n_estimators"}
# k_frac at or above this means the ceiling chose the tree count rather than the data, so the row is a truncated measurement. Lives HERE rather than in fulltune because tune.py stamps `ceiling_bound` onto every row; fulltune reads the same constant to decide whether to re-run, so the flag in the CSV and the retry trigger can never disagree.
_BIND = 0.9
_COARSE, _FINE = 50, 10  # tree-count scan granularity: a peak narrower than _COARSE is invisible to it (see _score_pool_curve), and the measured peak spans >150 trees, so 50 leaves ~3x margin

# axis name -> (xgb parameter it sets, default ladder, given in relative units?).
# `trees` is NOT an axis: crossing it would cost a refit per value, and one fit at a ceiling already yields every prefix for free (see _score_pool_curve).
_AXES = {
    "depth": ("max_depth", (3, 4, 5, 6), False),
    "lr": ("learning_rate", (0.01, 0.02, 0.05), False),
    "lam": ("reg_lambda", (0.002, 0.02, 0.1, 0.3), True),
    "mcw": ("min_child_weight", (0.002, 0.02, 0.1, 0.3), True),
    "subsample": ("subsample", (0.5, 0.7, 0.9), False),
    "colsample": ("colsample_bytree", (0.5, 0.7, 0.9), False),
}

# axis -> (hard lower limit, hard upper limit, floor-is-already-inert). A winner sitting on a HARD limit is a real answer; a winner sitting on a ladder end that is not a hard limit means the search was truncated.
_AXIS_LIMIT = {
    "depth": (1, None, False),
    "lr": (0.0, None, False),
    "lam": (0.0, None, True),  # c<=0.002 is a <0.2% leaf shrink -- indistinguishable from no L2 at all
    "mcw": (0.0, None, True),  # likewise: a fraction this small never blocks a split
    "subsample": (0.0, 1.0, False),
    "colsample": (0.0, 1.0, False),
}


# ── the relative-units core ───────────────────────────────────────────────────


def _leaf_hessian(depth: int, subsample: float, n_rows: int, train_frac: float, hess_per_row: float) -> float:
    """Expected sum-of-Hessian in one leaf of a depth-`depth` tree.

    (n_rows x train_frac x subsample) / 2^depth rows, each contributing `hess_per_row` -- exactly 1 for squared error, p(1-p) for logistic. Approximate (real trees are unbalanced) but the right SCALE, which is what makes relative lam/mcw depth-invariant and what makes one ladder serve both tasks.
    """
    return (n_rows * train_frac * subsample) / (2**depth) * hess_per_row


def _materialize(axis_vals: dict, ctx: dict) -> dict:
    """Axis point -> XGBoost override dict. Relative lam/mcw resolve against the leaf Hessian implied by THIS point's depth and subsample, not a grid-wide average -- which is what makes a depth x lam cartesian coherent."""
    cfg: dict = {}
    for name, v in axis_vals.items():
        param, _ladder, relative = _AXES[name]
        if not relative:
            cfg[param] = v

    depth = int(cfg.get("max_depth", ctx["default_depth"]))
    subsample = float(cfg.get("subsample", ctx["default_subsample"]))

    if "lam" in axis_vals or "mcw" in axis_vals:
        h = _leaf_hessian(depth, subsample, ctx["n_rows"], ctx["train_frac"], ctx["hess_per_row"])
        for name, param in (("lam", "reg_lambda"), ("mcw", "min_child_weight")):
            if name in axis_vals:
                cfg[param] = float(axis_vals[name]) * h

    return {k: (int(v) if k in _INT_KEYS else v) for k, v in cfg.items()}


def _grid(search: list[str], fixed: dict, ladders: dict, ctx: dict) -> list[tuple[dict, dict]]:
    """(axis point, xgb override) per cell of the cartesian over `search`, with `fixed` merged in, led by the reference row.

    The reference is the config this stage INHERITED -- `fixed` applied, no rung of the searched axis -- not the bare task base. Against the bare base, a stage-3 delta_vs_default compares to a config the chain abandoned two stages ago, so it reads as a large gain even when the stage lost ground, and the stage can never decline to move. Measured on a shakedown: stage 2 settled at 0.5373, stage 3's mcw ladder topped out at 0.5363, and that -0.0010 was threaded forward as a win because the only "no change" option on offer scored 0.4747.

    Its axis point is empty ON PURPOSE. If the reference wins, fulltune's _winner_axes reads no ax_* off it and threads nothing, which is exactly "no rung beat what I walked in with" -- while the row's effective config, and so the paste block, still carries every settled value.
    """
    ladders = {a: ladders.get(a) or _AXES[a][1] for a in search}
    out: list[tuple[dict, dict]] = [({}, _materialize(fixed, ctx))]
    if not search:  # confirmation stage: the product below yields exactly the reference again, so scoring it would be a duplicate fit
        return out
    for combo in itertools.product(*(ladders[a] for a in search)):
        pt = {**fixed, **dict(zip(search, combo))}
        out.append((pt, _materialize(pt, ctx)))
    return out


def _label(pt: dict, cfg: dict) -> str:
    """Human-readable config name. lam/mcw print as relative(absolute) -- the relative value is what was asked for, the absolute is what XGBoost was handed, and reading the output needs both.

    An empty axis point is the reference row, which under a staged run is the config the stage inherited rather than the bare task base. The name is load-bearing: delta_vs_default keys on it.
    """
    if not pt:
        return "DEFAULT (inherited)"
    bits = []
    for k, v in pt.items():
        if k in ("lam", "mcw"):
            bits.append(f"{k}={v:g}({cfg[_AXES[k][0]]:.0f})")
        else:
            bits.append(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}")
    return ", ".join(bits)


def _flag(axis: str) -> str:
    """CLI flag for an axis ladder: the axis name, pluralised."""
    return f"{axis}s"


# ── the tree-count scan ───────────────────────────────────────────────────────


def _base_margin(m, task: str) -> float:
    """The constant every `output_margin` prediction carries, in MARGIN space.

    `base_score` is stored in PROBABILITY space for binary:logistic, so it must be logit-ed before it can be subtracted from a margin -- using it raw shifts every CLF prefix by logit(p) - p, silently, and the resulting curve still looks entirely plausible. It also serialises as a bracketed string, hence the strip.
    """
    b = float(json.loads(m.get_booster().save_config())["learner"]["learner_model_param"]["base_score"].strip("[]"))
    return float(np.log(b / (1 - b))) if task == "clf" else b


def _prefix_oofs(models, X, task: str, ks, n: int) -> dict:
    """{k: out-of-fold vector} for every prefix length in `ks`, from ONE pass of tree slices per fold.

    Boosting margins are additive and `iteration_range=(a, b)` costs O(b - a), not O(b), so the margin at each k is the running SUM of slice predictions rather than a fresh walk from tree 0 -- O(max k) per fold instead of O(sum k). Each slice carries the base margin, so it is subtracted once per slice; the link function is applied at the end.

    Two traps, both of which produce plausible-looking wrong numbers rather than an error: `base_score` is in probability space for binary:logistic (see _base_margin), and `iteration_range=(0, 0)` means ALL trees, not none, so it cannot be used to read the base off. The fold slice X.iloc[te] is taken once per FOLD here rather than once per (fold, k).
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


def _fam_folds(fam, n_splits: int, true_lofo: bool, max_holdout_pct: float):
    """The family folds to score on -- GroupKFold by default, one-family-per-fold under --true-lofo. Materialised per call because folds_grouped returns a generator a previous config has already consumed."""
    if true_lofo:
        return list(cook.folds_true_lofo(fam, max_holdout_pct))
    return list(cook.folds_grouped(fam, n_splits))


def _score_pool_curve(
    pool, feat, target, task, metric, ceiling, n_splits, true_lofo, max_holdout_pct,
    coarse=_COARSE, fine=_FINE, loso=False, **cfg
):
    """Score a config, choosing its tree count rather than being told it. -> (metrics_at_best_k, best_k, curve).

    Fit each fold once at `ceiling`, then score every PREFIX on the holdout via iteration_range=(0, k) -- no refits, so the whole curve plus final metrics cost the same CV pass a plain score already paid. Scanned rather than bisected because the curve is not unimodal. Coarse-then-fine since a prefix costs O(k); `metric` is a bare cook._score key (r2/prauc), not a lofo_-prefixed one.
    """
    X, y = pool[feat], cook._target(pool, target, task)
    fam = cook.basin_groups(pool["site"])
    kw = dict(n_estimators=ceiling, **cfg)

    folds = _fam_folds(fam, n_splits, true_lofo, max_holdout_pct)
    fam_models = [(m, te) for te, m in cook._fold_models(X, y, folds, task, **kw)]

    def curve_over(ks):
        ks = [k for k in ks if k > 0]  # iteration_range=(0, 0) means ALL trees, not none
        got = _prefix_oofs(fam_models, X, task, ks, len(y))
        return {k: (cook._score(y, oof, task)[metric], oof) for k, oof in got.items()}

    scan = curve_over(range(coarse, ceiling + 1, coarse))
    # Refine around the top TWO coarse points rather than only the argmax. The curve is bimodal -- on the measured fold it peaked +0.350 at 175 then recovered to a +0.317 plateau by 1375, a margin of only ~0.03 -- so when both basins are visible to the coarse grid but rank in the wrong order, the second window recovers the true peak.
    #
    # What this does NOT fix: a peak NARROWER than `coarse` is invisible to the coarse grid entirely (both flanking samples read the base level), so the top-2 coarse points both sit in the other basin and neither window covers it. The only real protection there is `coarse` itself, which is why it is 50 rather than 100: the observed peak spans >150 trees, so 50 leaves a 3x margin.
    ranked = sorted(scan, key=lambda k: scan[k][0], reverse=True)
    windows = set()
    for k0 in ranked[:2]:
        windows.update(range(max(fine, k0 - coarse), min(ceiling, k0 + coarse) + 1, fine))
    scan.update(curve_over(sorted(windows - set(scan))))
    best_k = max(scan, key=lambda k: scan[k][0])
    oof_family = scan[best_k][1]

    # The LOSO pass is a FULL second CV whose only product is the leakage gap, and ranking is on LOFO alone -- so it is OFF per config here and run once for the winner instead (see _winner_loso). Off, oof_site stays all-NaN and _cross_metrics emits a NaN loso_*, leaving the column set intact. Prefixed at the SAME best_k when it does run, so the gap describes the model actually chosen rather than one at the ceiling.
    oof_site = np.full(len(y), np.nan)
    if loso:
        site_folds = list(cook.folds_grouped(pool["site"], n_splits))
        site_models = [(m, te) for te, m in cook._fold_models(X, y, site_folds, task, **kw)]
        oof_site = _prefix_oofs(site_models, X, task, [best_k], len(y))[best_k]

    metrics = dict(
        n_sites=pool["site"].nunique(),
        n_families=int(pd.Series(fam).nunique()),
        n_rows=len(pool),
        n_feat=len(feat),
        **cook._cross_metrics(pool, np.asarray(y), oof_site, oof_family, task),
    )
    return metrics, best_k, {k: v[0] for k, v in sorted(scan.items())}


def _winner_loso(pool, feat, target, task, metric, k, n_splits, **cfg):
    """Site-grouped score at a FIXED tree count -- the leakage-gap companion, run once for the winning config.

    Fits at `k` rather than the ceiling: the winner's tree count is already known, so there is nothing to scan. This is what makes turning the per-config LOSO pass off cheap rather than lossy -- the gap is still reported, just once instead of once per grid cell.
    """
    X, y = pool[feat], cook._target(pool, target, task)
    folds = list(cook.folds_grouped(pool["site"], n_splits))
    models = [(m, te) for te, m in cook._fold_models(X, y, folds, task, n_estimators=int(k), **cfg)]
    if not models:
        return float("nan")
    return cook._score(y, _prefix_oofs(models, X, task, [int(k)], len(y))[int(k)], task)[metric]


# ── CLI parsing and reporting ─────────────────────────────────────────────────


def _parse_pairs(s: str | None) -> dict:
    """'depth=4,lam=0.1' -> {'depth': 4.0, 'lam': 0.1}. Axis names only -- a raw XGBoost name like reg_lambda=0.1 is rejected, since it would mean thousands of times less than the axis of the same name."""
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


def _native(v):
    """numpy scalar -> python scalar, so json.dumps and repr() do not emit np.float64(...)."""
    return v.item() if hasattr(v, "item") else v


def _log_config(task: str, recipe_name: str, searched: str, score, config: dict) -> None:
    """Append one scored config to models/tuning_logs.jsonl, oldest first.

    Called after EVERY config rather than once per sweep: a full sweep is hours, and a crash used to take the whole record with it. `config` is the effective XGB dict with n_estimators set to that config's own best_k -- i.e. exactly what would be pasted into train.RECIPE_XGB. Read by nothing; it is the tuner's record of what it has fitted, not an input to training.

    ONE LINE APPENDED, not a whole file rewritten. As a JSON array this read-insert-write the entire log per config, which is O(n^2) in bytes and unbounded across sweeps -- 195 entries was already 80 KB, so a 500-config sweep wrote ~52 MB and every later sweep paid more. Nothing reads it, so the format was free to change; a truncated final line after a hard kill costs one entry instead of the file.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    score = _native(score)
    entry = {
        "task": task,
        "recipe_name": recipe_name,
        "timestamp": datetime.now(timezone.utc).strftime("%y%m%d%H%M%S"),  # UTC, matching train.log_metadata
        "searched": searched,
        "score": None if score is None or not np.isfinite(score) else round(float(score), 6),
        "config": {k: (int(_native(v)) if k in _INT_KEYS else _native(v)) for k, v in config.items()},
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_tuning_log() -> list[dict]:
    """The tuning log, oldest first. Malformed lines are skipped rather than fatal -- a hard kill mid-write can leave a partial last line, and one lost entry should not make the record unreadable."""
    if not LOG_FILE.exists():
        return []
    out = []
    for line in LOG_FILE.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _emit_config(df: pd.DataFrame, search: list[str], recipe_name: str, task_base: dict, fixed: dict | None = None, resolved_axes: set | None = None) -> None:
    """Print the winner as a paste-ready RECIPE_XGB entry, plus which axes are still at inherited values.

    Diffed against the TASK BASE, not the recipe's current config, so the printed entry is always the recipe's COMPLETE override set and can be pasted over the existing one wholesale. Diffing against the current config instead would omit any axis a previous stage had already settled -- it matches, so it "did not change" -- and pasting that block would silently revert it.

    An axis counts as RESOLVED if this run searched it OR `fixed` pins it, because fulltune.py threads each stage's winner into the next as --fix: without that, every stage after the first would report the axes it had already settled as untuned, and the final confirmation stage -- which pins all six -- would report the completed config as tuning nothing at all.

    `resolved_axes` overrides that with the set fulltune has searched ACROSS the whole protocol. It is needed because an axis whose stage was won outright by the inherited value is deliberately left unpinned (see _winner_axes), so it never reaches a later stage's `fixed` and the local view reports it as untuned -- which is how a CLF run that searched lam, mcw, subsample and colsample ended by claiming none of them were resolved.
    """
    resolved = set(resolved_axes) if resolved_axes is not None else set(search) | set(fixed or {})
    best = df.iloc[0]
    changed = {}
    for axis in _AXES:
        param = _AXES[axis][0]
        if param not in best or pd.isna(best.get(param)):
            continue
        v = _native(best[param])
        v = int(v) if param in _INT_KEYS else round(float(v), 6)
        if task_base.get(param) != v:
            changed[param] = v

    # n_estimators leads the block: it is the one key train.py cannot proceed without, and pasting the rest without it leaves the recipe untuned.
    entry = {"n_estimators": int(best["best_k"]), **changed}
    print(f"\n  To adopt this winner, put it in train.RECIPE_XGB[{recipe_name!r}] -- the tree count belongs WITH the config it was tuned for:")
    print(f'      "{recipe_name}": {{' + ", ".join(f"{k!r}: {v!r}" for k, v in entry.items()) + "},")
    if not changed:
        print("  (the swept parameters all match the task base; only the tree count moved)")

    untouched = [a for a in _AXES if a not in resolved]
    if untouched:
        print(f"  [partial] NOT resolved: {', '.join(untouched)} -- still at inherited values. lam/mcw in particular are inert at the inherited reg_lambda=5 / min_child_weight=10. `python src/models/fulltune.py` resolves all six.")


def _edge_report(df: pd.DataFrame, search: list[str], ladders: dict, ceiling: int) -> None:
    """Warn when the winning config sits on the edge of a searched ladder, or on the tree ceiling.

    An edge winner means the search was TRUNCATED, not that an optimum was found -- unless the edge is a hard limit (subsample/colsample at 1.0) or an already-inert floor (lam/mcw at ~0, which is indistinguishable from no regularization). Prints the ladder to try next; extending is left to the caller, since auto-widening can run away.
    """
    best = df.iloc[0]
    notes = []
    # _BIND, not a literal: this was hardcoded 0.8 while the retry trigger and the `ceiling_bound` column read _BIND, so at k_frac 0.885 the report said "the ceiling is binding, re-run larger" about a stage fulltune had already decided was fine. Three copies of one threshold, two of them disagreeing.
    if pd.notna(best.get("best_k")) and best["best_k"] >= _BIND * ceiling:
        notes.append(f"best_k={int(best['best_k'])} is >={_BIND:.0%} of the {ceiling} ceiling -- the ceiling is binding, not chosen. Re-run with a larger --ceiling before trusting this winner.")
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
            notes.append(f"{ax}={v:g} is the TOP of its ladder {lad} -- untruncated optimum unknown. Try --{_flag(ax)} {','.join(f'{x:g}' for x in lad[-2:] + nxt)}")
        elif v <= lad[0]:
            if inert_floor:
                notes.append(f"{ax}={v:g} is the bottom of its ladder, and that low it is already inert -- read as 'no {ax} helps', not as a truncated search.")
            elif lad[0] > lo_lim:
                step = lad[1] - lad[0]
                nxt = [round(lad[0] - step * i, 6) for i in (1, 2)]
                nxt = [x for x in nxt if x > lo_lim]
                if nxt:
                    notes.append(f"{ax}={v:g} is the BOTTOM of its ladder {lad} -- untruncated optimum unknown. Try --{_flag(ax)} {','.join(f'{x:g}' for x in sorted(nxt) + lad[:2])}")
    for n in notes:
        print(f"  [edge] {n}")


# ── the sweep ─────────────────────────────────────────────────────────────────


def tune(
    task: str,
    recipe,
    target_col: str,
    sites,
    *,
    search: list[str],
    fixed: dict,
    ladders: dict,
    ceiling: int,
    seeds: tuple[int, ...],
    append: bool,
    true_lofo: bool = False,
    max_holdout_pct: float = 0.2,
    n_splits: int = 5,
    pool: pd.DataFrame | None = None,
    loso: bool = False,
    resolved_axes: set | None = None,
):
    """Sweep one recipe. `pool` lets fulltune.py share one pooling pass across all six stages.

    Everything after `sites` is KEYWORD-ONLY. The sibling copy of this function in experiments/feature-manifest/ took thirteen positional arguments, and when `loso` sat before `true_lofo` its driver silently bound `true_lofo` to `loso` and `max_holdout_pct` (0.2, truthy) to `true_lofo` -- so every run used a holdout regime it did not report, with no error anywhere. The `*` makes that class of bug unrepresentable rather than merely avoided by convention.

    `loso` runs the site-grouped pass for EVERY config. It is off by default because that is a full second CV per grid cell whose only product is the leakage-gap column, while ranking is on LOFO alone -- roughly halving a sweep. The gap is still reported: one site pass is run for the winner, at the winner's own tree count (see _winner_loso).
    """
    from src.models.train import xgb_for, REAL_XGB_REG, REAL_XGB_CLF  # local: train imports cook, and cook is heavy

    name = getattr(recipe, "__name__", str(recipe))
    head = _HEADLINE[task]
    base = xgb_for(recipe, task)  # what the DEFAULT row measures and every unswept axis inherits
    task_base = REAL_XGB_REG if task == "reg" else REAL_XGB_CLF  # what _emit_config diffs against

    if pool is None:
        pool = cook._pool_wide(recipe, sites, target_col, progress_label=name)
    # sorted on purpose: cook._score_pool sorts before selecting, so an unsorted column order here would give the tuner different colsample_bytree draws from the CV that later validates its winner.
    feat = sorted(cook._features(pool, target_col))
    y = cook._target(pool, target_col, task)
    fam = cook.basin_groups(pool["site"])
    n_fam = int(pd.Series(fam).nunique())
    if n_fam < 2:
        raise SystemExit(f"only {n_fam} basin family across these sites -- LOFO is undefined, and every score would come back NaN with a best_k picked off an all-NaN curve. Widen --sites.")

    # Hessian per row: exactly 1 for squared error; p(1-p) for logistic, at the pool's own base rate. This is the entire reason the same relative ladder yields different absolute values per task.
    p = float(np.mean(y)) if task == "clf" else float("nan")
    hess_per_row = p * (1 - p) if task == "clf" else 1.0
    # one family held out per fold under true LOFO, else GroupKFold's (k-1)/k
    train_frac = (1 - 1 / n_fam) if true_lofo else (1 - 1 / min(n_splits, n_fam))
    ctx = dict(
        n_rows=len(pool),
        train_frac=train_frac,
        hess_per_row=hess_per_row,
        default_depth=base["max_depth"],
        default_subsample=base["subsample"],
    )

    grid = _grid(search, fixed, ladders, ctx)
    print(
        f"\n=== {name} ({task.upper()})  pool {pool.shape}  |  {len(feat)} features  |  "
        f"{pool['site'].nunique()} sites, {n_fam} families  |  {len(grid)} configs x {len(seeds)} seed(s) ==="
    )
    print(f"    ranking on {head}" + (f"   [true LOFO, cap {max_holdout_pct:.0%}]" if true_lofo else ""))
    print(f"    searching {search or ['(none)']}" + (f", fixed {fixed}" if fixed else ""))
    eff = ctx["n_rows"] * train_frac * ctx["default_subsample"]
    scale = "  ".join(
        f"d{d}:{_leaf_hessian(d, ctx['default_subsample'], ctx['n_rows'], train_frac, hess_per_row):,.0f}"
        for d in (3, 4, 5, 6)
    )
    print(f"    leaf Hessian ({eff:,.0f} rows/tree, {hess_per_row:.4f}/row) -> {scale}   [relative units; c=0.1 means a 9% leaf shrink]")
    print(f"    tree count: scanned to a {ceiling} ceiling per config, coarse {_COARSE} then fine {_FINE}")
    print()

    searched = ",".join(search) or "none"
    rows, t0 = [], time.time()
    for i, (pt, cfg) in enumerate(grid, 1):
        t1 = time.time()
        for seed in seeds:
            kw = {**base, **cfg, "random_state": seed}
            kw.pop("n_estimators", None)  # the scan supplies the ceiling; a base value would fight it
            r, best_k, curve = _score_pool_curve(
                pool, feat, target_col, task, _CURVE_METRIC[task], ceiling, n_splits,
                true_lofo, max_holdout_pct, loso=loso, **kw,
            )
            effective = {**kw, "n_estimators": best_k}
            rows.append(
                {
                    "config": _label(pt, cfg),
                    # `searched` identifies which run a row came from. Under --append the file accumulates stages and sorting by headline interleaves them, so without it a stage-1 row and a stage-3 row are distinguishable only by guessing from which ax_* happen to be non-NaN.
                    "searched": searched,
                    "seed": seed,
                    head: r[head],
                    **{k: r.get(k) for k in _ALSO[task]},
                    "best_k": best_k,  # trees the config actually wanted
                    # The ceiling this row was measured at, and whether it bound. Under --append the file accumulates stages AND ceiling-raise retries, so without these a reader cannot tell a config that chose 400 trees from one that was cut off at 400 -- recoverable only as best_k/k_frac, and k_frac is rounded. A bound row is a truncated measurement, not a result.
                    "ceiling": ceiling,
                    "k_frac": round(best_k / ceiling, 3),  # ~1.0 means the ceiling is binding -- raise it
                    "ceiling_bound": bool(best_k / ceiling >= _BIND),
                    "at_ceiling": round(curve[max(curve)], 4),
                    # the EFFECTIVE config, not just the overrides, so each row says what was fit rather than what differed
                    **{f"ax_{k}": v for k, v in pt.items()},
                    **effective,
                }
            )
            _log_config(task, name, searched, r[head], effective)
        got = [x[head] for x in rows[-len(seeds):]]
        ks = [x["best_k"] for x in rows[-len(seeds):]]
        seed_note = f"  [{len(seeds)} seeds, sd {np.std(got):.4f}]" if len(seeds) > 1 else ""
        print(
            f"  [{i}/{len(grid)}] {_label(pt, cfg):<50.50} {head}={np.mean(got):.4f}  k={int(np.mean(ks)):>5}"
            f"  (vs {np.mean([x['at_ceiling'] for x in rows[-len(seeds):]]):+.4f} at {max(curve)})"
            f"{seed_note}  ({time.time() - t1:.0f}s)",
            flush=True,
        )

    df = pd.DataFrame(rows)
    if len(seeds) > 1:
        # Rank on the seed MEAN: picking the max over a grid of noisy single-seed scores is optimistically biased, and subsample<1 makes every fit seed-dependent. Averaging only the numeric columns on purpose -- a config column that is None/str would make .mean() raise here, i.e. AFTER every fit in the sweep has been paid for.
        num = [c for c in df.columns if c != "config" and pd.api.types.is_numeric_dtype(df[c])]
        rest = [c for c in df.columns if c != "config" and c not in num]
        df = df.groupby("config", as_index=False).agg({**{c: "mean" for c in num}, **{c: "first" for c in rest}})
        df["n_seeds"] = len(seeds)
    # DEFAULT LOSES TIES. The inherited config is usually also a ladder rung -- CLF's inherited reg_lambda is exactly lam=0.3(659) -- so the two score identically and an arbitrary tie-break decided which row won. When DEFAULT won, it carried no ax_* values, and three things silently followed: fulltune._winner_axes threaded nothing forward, _edge_report skipped the axis so a top-of-ladder winner raised no warning, and _emit_config reported an axis that HAD been searched as "not resolved". Preferring the explicit rung on a tie names the same model and keeps its axis values attached.
    df["_default_last"] = (df["config"] == "DEFAULT (inherited)").astype(int)
    df = df.sort_values([head, "_default_last"], ascending=[False, True]).drop(columns="_default_last")
    df = df.reset_index(drop=True)
    ref = df.loc[df["config"] == "DEFAULT (inherited)", head]
    if len(ref):
        df.insert(2, "delta_vs_default", (df[head] - float(ref.iloc[0])).round(4))

    # The leakage gap, once, for the winner only -- at its own tree count rather than the ceiling. Per-config this was a full second CV for a column nothing ranks on; here it is one extra fit set per sweep, and it still answers the question the column exists for.
    loso_col, lofo_col = f"loso_{_LOSO_METRIC[task]}", f"lofo_{_LOSO_METRIC[task]}"
    if not loso and len(df):
        b = df.iloc[0]
        wkw = {k: b[k] for k in base if k in b.index and pd.notna(b[k])}
        wkw.pop("n_estimators", None)
        wkw = {k: (int(_native(v)) if k in _INT_KEYS else _native(v)) for k, v in wkw.items()}
        t_l = time.time()
        val = _winner_loso(pool, feat, target_col, task, _LOSO_METRIC[task], b["best_k"], n_splits, **wkw)
        df.loc[0, loso_col] = val
        # against its OWN twin, not the headline: for CLF the pair is auc/auc while the ranking is prauc, and a prauc-vs-auc subtraction is not a leakage gap.
        twin = float(b[lofo_col]) if lofo_col in b.index and pd.notna(b.get(lofo_col)) else float("nan")
        print(f"  leakage gap for the winner: {loso_col}={val:.4f} vs {lofo_col}={twin:.4f} ({val - twin:+.4f})   ({time.time() - t_l:.0f}s)")

    OUT.mkdir(parents=True, exist_ok=True)
    # Keyed on the RECIPE, not the task: two recipes on the same task would otherwise silently overwrite each other's grid.
    grid_fp = OUT / f"tune_{name}.csv"
    if append and grid_fp.exists():
        df_out = pd.concat([pd.read_csv(grid_fp), df], ignore_index=True).sort_values(head, ascending=False)
    else:
        df_out = df
    df_out.to_csv(grid_fp, index=False)

    best = df.iloc[0]
    print(f"\n  best: {best['config']}  ({head}={best[head]:.4f}, {int(best['best_k'])} trees)")
    _edge_report(df, search, ladders, ceiling)
    _emit_config(df, search, name, task_base, fixed, resolved_axes)
    print(f"  wrote {grid_fp}  ({len(df_out)} rows)  and {len(grid) * len(seeds)} entries -> {LOG_FILE.name}  ({time.time() - t0:.0f}s)")
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=["reg", "clf", "both"], default="both")
    ap.add_argument(
        "--variant",
        choices=["island", "network", "both"],
        default="island",
        help="which recipe variant to tune: 'island' (deployable at any pin) or 'network' (adds the "
        "reach-graph donor block). Defaults to island, so a bare invocation keeps tuning what it always did.",
    )
    ap.add_argument("--search", default="depth,lr", help=f"axes to cross: {', '.join(_AXES)}")
    ap.add_argument("--fix", default=None, help="pin axes not searched, e.g. 'depth=4,lam=0.1'")
    ap.add_argument(
        "--ceiling",
        type=int,
        default=1500,
        help="upper bound on trees; each config is scanned to it and reports the count it actually wanted",
    )
    ap.add_argument("--seeds", default="42", help="comma-separated random_state values; >1 ranks on the seed mean")
    ap.add_argument(
        "--append", action="store_true", help="append to tune_<recipe>.csv instead of overwriting, so stages accumulate"
    )
    ap.add_argument(
        "--loso",
        action="store_true",
        help="run the site-grouped pass for EVERY config (roughly doubles the sweep). Off by default: it is a full "
        "second CV whose only product is the leakage-gap column, which nothing ranks on -- the winner still gets "
        "one, at its own tree count.",
    )
    ap.add_argument("--true-lofo", action="store_true", help="score with a TRUE leave-one-family-out holdout")
    ap.add_argument(
        "--max-holdout-pct",
        type=float,
        default=0.2,
        help="with --true-lofo, a family above this share of pooled rows is never held out (it still trains). "
        "cook._score_pool prints the coverage each run actually achieved -- read it before trusting the scores.",
    )
    ap.add_argument(
        "--sites",
        type=int,
        default=None,
        help="cap to the first N sites (SMOKE ONLY -- it changes the family structure and so the LOFO "
        "difficulty; never pick values on a subset)",
    )
    ap.add_argument("--splits", type=int, default=5, help="GroupKFold splits for LOSO/LOFO (default 5)")
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

    import src.features.recipes as recipes

    sites = [str(s) for s in get_site_ids()]
    if a.sites:
        sites = sites[: a.sites]

    tasks = ["reg", "clf"] if a.task == "both" else [a.task]
    variants = ["island", "network"] if a.variant == "both" else [a.variant]
    specs = [(t, v, *_TASK_SPEC[(t, v)]) for t in tasks for v in variants]
    print(f"tuning {len(specs)} recipe(s) on {len(sites)} sites: " + ", ".join(s[2] for s in specs))
    for task, _variant, recipe_attr, target_col in specs:
        tune(
            task, getattr(recipes, recipe_attr), target_col, sites,
            search=search, fixed=fixed, ladders=ladders, ceiling=a.ceiling, seeds=seeds, append=a.append,
            true_lofo=a.true_lofo, max_holdout_pct=a.max_holdout_pct, n_splits=a.splits, loso=a.loso,
        )


if __name__ == "__main__":
    main()
