"""Experiment engine: declare experiments as records, run them, log to ONE consolidated log.

An experiment is a QUESTION plus a dict of named recipes to compare against each other on the same sites and folds. You declare it with @experiment (in exps.py); this module resolves the site pool, runs the comparison through src.eval.cook, and appends one record per (experiment x task) run to logs/experiments.jsonl -- the single, git-tracked, append-only history. --summary and the rendered logs/experiments.md read that log back as Question -> Winner -> Results.

Modes select how many sites (ordered oldest-history-first):
    --test   sites[:2]   (default)     --med   sites[:20]     --full   the full filtered list
    --small  sites[:8]

--extra additionally runs each recipe's slow permutation-importance test (default off).

Usage:
    python run_exp.py                      # all registered experiments, test mode
    python run_exp.py --med --extra
    python run_exp.py --med 17c 18c 19c    # only these, in this order
    python run_exp.py --summary            # print the latest result per experiment
    python run_exp.py --render             # (re)write logs/experiments.md from the log
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

warnings.filterwarnings("ignore")

_HERE = Path(__file__).resolve().parent  # repo/experiments/specific-small-experiments
_ROOT = _HERE.parents[1]  # repo root -- NOT _HERE.parent, which is repo/experiments
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))  # so `import exps` works

from src.eval.cook import _STRUCTURAL, _pool_wide, compare_many, compare_masks
from src.data.access import get_site_ids
from src.features.features import daily_nitrate
from src.features.recipes import island_CLF, island_REG
from src.models.train import _tuned_iters, xgb_for
from logs.render_logs import format_run as _format_run, latest_per_exp as _latest_per_exp, load_experiments as _load_log, render_experiments

_LOG = _ROOT / "logs" / "experiments.jsonl"
_MD = _ROOT / "logs" / "experiments.md"

MODES = {"test": 2, "small": 8, "med": 20, "full": None}
_TOP_N = 8  # gain-importance features embedded per recipe in the log

# reg vs clf: (target column, headline metric the winner is chosen by; higher is better)
_TASK = {
    "reg": ("nitrate_con", "lofo_r2"),
    "clf": ("violation", "lofo_prauc"),
}

# ── declaration ───────────────────────────────────────────────────────────────

REGISTRY: dict[str, "Experiment"] = {}


@dataclass
class Experiment:
    id: str
    question: str  # REQUIRED: the question this experiment answers (shown first in every summary)
    build: Callable[[str], dict]  # build(task) -> {recipe_name: recipe_fn}, or a MaskedSweep for kind='masked'
    task: str = "reg"  # a run is EITHER 'reg' OR 'clf' -- one task per experiment
    kind: str = "many"  # "many" (a recipe per arm, re-pooled) | "masked" (one pool, arms are column subsets)
    sites: list | None = None  # optional fixed site pool (overrides the mode slice)


@dataclass
class MaskedSweep:
    """What a kind='masked' build(task) returns: ONE wide recipe plus the column subsets that define the arms.

    For a grid over feature GEOMETRY, `compare_many` is the wrong driver -- it re-pools every site for every arm, so an N-cell grid costs N full pooling passes. Because the shipping encodings make `edges` and `lam` nearly independent (edges drives the norm-bucketed block, lam drives the whole-basin expT block), one pooling pass can emit |E| + |L| column blocks and every cell of the |E| x |L| grid is then just a mask over them: pooling cost is additive while the grid stays multiplicative.

    `masks` is ordered -- the FIRST entry is the paired baseline that delta_vs_base is measured against, matching the recipe-dict convention. `base_cols` (usually the geometry-independent base features) scopes permutation importance to the columns an arm ADDS, so `--extra` stays affordable across a large grid.
    """

    wide: Callable  # site -> DataFrame carrying every arm's columns + the target
    masks: dict | Callable  # {arm_name: [column, ...]}, or pool -> that dict; order sets the baseline
    base_cols: list | Callable | None = None  # permutation-importance scope; None permutes everything


def experiment(id: str, question: str, task: str = "reg", kind: str = "many", sites=None):
    """Register a single-task experiment. `question` is REQUIRED and heads every summary. `task` is 'reg' or 'clf' -- one run is either regression or classification, never both. To test the same recipe logic under both, register two records (e.g. ids '20' and '20c') sharing one build fn.

    The decorated function takes the task and returns the {name: recipe} dict:  build(task) -> dict. Recipes are task-specific (a reg recipe targets nitrate_con; a clf recipe targets violation and drops continuous nitrate to avoid leakage), so build receives the experiment's task. It is called lazily, so grid factories only run when the experiment does. Returns the fn so one build can be registered under several ids."""
    if not question or not question.strip():
        raise ValueError(f"experiment {id!r} must have a non-empty question string.")
    if task not in ("reg", "clf"):
        raise ValueError(f"experiment {id!r}: task must be 'reg' or 'clf', got {task!r}")

    def deco(fn):
        if id in REGISTRY:
            raise ValueError(f"duplicate experiment id {id!r}")
        REGISTRY[id] = Experiment(id=id, question=question.strip(), build=fn, task=task, kind=kind, sites=sites)
        return fn

    return deco


# ── site pool ─────────────────────────────────────────────────────────────────


def build_sites(mode: str) -> list[str]:
    """Every contract site, ordered oldest-history-first, sliced by mode.

    NO row-count floor. src/build/water/filter_sites.py is the sole site filter -- MIN_NITRATE_ROWS there decides what reaches the contract, so a second threshold here would silently shrink the cohort below what the build published. The floor that used to live here (>= 1500 daily-nitrate obs) cut 123 sites to 45, which is the cohort every pre-2026-07-27 record in experiments.jsonl was scored on; `sites_hash` distinguishes those records from new ones.

    The ordering is still oldest-history-first, because the --test/--small/--med slices take a prefix and want the longest-running sites.
    """
    dated = []
    for s in get_site_ids():
        d = daily_nitrate(s).dropna()
        if len(d):
            dated.append((d.index.min(), s))
    dated.sort()  # earliest first, then uid
    ordered = [s for _, s in dated]
    n = MODES[mode]
    return ordered if n is None else ordered[:n]


def _sites_hash(sites) -> str:
    return hashlib.sha1(",".join(sorted(sites)).encode()).hexdigest()[:8]


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(_ROOT), "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


# ── run + log ─────────────────────────────────────────────────────────────────


def _jsonable(v):
    try:
        import numpy as np

        if isinstance(v, np.floating):
            return float(v)
        if isinstance(v, np.integer):
            return int(v)
    except Exception:
        pass
    return v


def _mask_diagnostics(pool, masks) -> dict:
    """Per-arm column bookkeeping, so a geometry's score can be read against how RAGGED its columns are.

    An unoccupied distance bucket emits no rows for a site (the aggregation groups with observed=True), so that column is simply ABSENT for it and the pool's concat fills NaN across all of that site's rows. Two things follow, and both would otherwise be invisible: n_feat varies across grid cells for reasons unrelated to model quality, and a tighter inner edge quietly turns a near-field feature into a mostly-missing column. Recorded per arm: how many requested columns materialized, and what fraction of sites actually carry an innermost-bucket value.
    """
    out = {}
    n_sites = pool["site"].nunique()
    for name, cols in masks.items():
        present = [c for c in cols if c in pool.columns]
        b0 = [c for c in present if c.endswith("_b0")]
        frac = None
        if b0 and n_sites:
            frac = round(float(pool[b0].notna().any(axis=1).groupby(pool["site"]).any().mean()), 4)
        out[name] = {
            "n_requested": len(cols),
            "n_present": len(present),
            "n_b0_cols": len(b0),
            "frac_sites_with_b0": frac,
        }
    return out


def _record(exp, task, sites, result, elapsed, extra, mode, xgb) -> dict:
    """Build one log record from a compare_* result DataFrame.

    `xgb` is the effective config every arm was fitted at, stored because the harness's config is no longer a constant: it follows train.RECIPE_XGB, so two records at the same sites_hash are only comparable when this block matches.
    """
    _, headline = _TASK[task]
    metrics = {r: {k: _jsonable(v) for k, v in row.items()} for r, row in result.to_dict("index").items()}
    recipes = list(metrics)
    # NaN must not win: the headline is all-NaN whenever LOFO could not split (e.g. a --test slice landing in one basin family), and max() over NaN would silently crown whichever arm came first.
    scored = [
        (r, v) for r in recipes if (v := metrics[r].get(headline)) is not None and not (isinstance(v, float) and v != v)
    ]
    winner = max(scored, key=lambda x: x[1]) if scored else (None, None)
    base = recipes[0]  # first recipe is the baseline; deltas are measured against it (paired)
    delta = {
        r: {headline: round(metrics[r][headline] - metrics[base][headline], 4)}
        for r in recipes
        if metrics[r].get(headline) is not None and metrics[base].get(headline) is not None
    }
    top_gain = {}
    for r, imp in (result.attrs.get("importance") or {}).items():
        try:
            top_gain[r] = list(imp.sort_values(ascending=False).head(_TOP_N).index)
        except Exception:
            pass
    diagnostics = result.attrs.get("mask_diagnostics")
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "exp": exp.id,
        "question": exp.question,
        "task": task,
        "kind": exp.kind,
        "mode": mode,
        "n_sites": len(sites),
        "sites_hash": _sites_hash(sites),
        "git_sha": _git_sha(),
        "elapsed_s": round(elapsed, 1),
        "extra": extra,
        "headline_metric": headline,
        "xgb": {k: _jsonable(v) for k, v in xgb.items()},
        "baseline": base,
        "winner": {"recipe": winner[0], "by": headline, "value": _jsonable(winner[1])},
        "delta_vs_base": delta,
        "metrics": metrics,
        "top_gain": top_gain,
        **({"mask_diagnostics": diagnostics} if diagnostics else {}),
    }


def _log(record: dict) -> None:
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


_SHIPPED_RECIPE = {"reg": island_REG, "clf": island_CLF}


def _xgb_config(task: str, seed: int | None = None) -> dict:
    """The SHIPPED recipe's tuned XGBoost config for `task`, with `seed` overriding random_state.

    Resolved from the TASK, never from the experiment's own recipe: train.xgb_for keys RECIPE_XGB on recipe.__name__, and every experiment arm is a recipe_maker closure named "recipe" -- an unknown key, so xgb_for would hand back the bare task base with no n_estimators and cook's _DEFAULT_XGB would quietly supply 800 trees. _tuned_iters is called only to raise: an un-pasted future tuning then fails in a second rather than after a full run at the wrong tree count. Carries no early_stopping_rounds, so cook's None survives the merge, which _fold_models requires.
    """
    recipe = _SHIPPED_RECIPE[task]
    cfg = xgb_for(recipe, task)
    _tuned_iters(cfg, recipe.__name__, task)
    return {**cfg, **({"random_state": seed} if seed is not None else {})}


def run(exp: Experiment, mode: str = "test", extra: bool = False, seed: int | None = None):
    """Run one experiment (registered OR ad-hoc) and RETURN its metrics DataFrame.

    Resolves the site pool (exp.sites, else the `mode` slice), runs the compare_* driver, prints Question -> Winner, appends a record to experiments.jsonl, and refreshes experiments.md. The full log record is stashed on result.attrs['record']. Called both by the CLI (via REGISTRY) and directly from a notebook (see run_experiment).

    Every arm is fitted at the SHIPPED recipe's tuned config for `exp.task` (see _xgb_config), so experiments track whatever train.RECIPE_XGB currently holds rather than a hand-set screening config. RECORDS PREDATING THAT ARE NOT COMPARABLE, even at the same sites_hash: the old FAST_XGB path fitted 800 trees at depth 4 and reg_lambda=5, which is c~=0.0009 of a leaf's Hessian, i.e. no regularization at all. The record's `xgb` field distinguishes the two.

    `seed` overrides XGBoost's random_state, so the same experiment can be replicated and its spread reported instead of a point estimate. Note what that does and does not buy: with subsample=0.7/colsample_bytree=0.5 the fit genuinely changes, so it measures MODEL variance. It does NOT measure the site-sampling variance that sets the noise floor on between-site metrics -- that one is a property of the cohort size and cannot be reseeded away. Because every arm is scored on the same pool and folds, judge an effect by the spread of the PAIRED delta across seeds, not by the spread of either arm's absolute score.
    """
    sites = exp.sites if exp.sites is not None else build_sites(mode)
    mode_label = mode if exp.sites is None else "custom"  # explicit sites aren't a named mode slice
    if getattr(exp, "kind", None) == "fleet":
        # the per-site fleet path (cook_one/cook_fleet/compare_fleet) was retired from cook.py -- the project moved off per-site modelling long ago.
        raise NotImplementedError(f"exp {exp.id}: kind='fleet' is no longer supported")
    target, _ = _TASK[exp.task]
    xgb_kw = _xgb_config(exp.task, seed)
    built = exp.build(exp.task)  # task-specific recipe set (reg vs clf targets differ)
    print(f"\n======= exp {exp.id} [{exp.task}] : {exp.question} =======", flush=True)
    t0 = time.time()

    if isinstance(built, MaskedSweep):
        # One pooling pass, then every arm is a column mask over it. Beyond the speed, this is the PAIRED path: all arms score the same rows, the same site vector and the same fold assignments, so an arm-vs-baseline delta differences out cohort and fold variance instead of mixing in a cohort effect from re-pooling.
        pool = _pool_wide(built.wide, sites, target, progress_label=f"exp {exp.id} [{exp.task}]")
        # masks/base_cols may be callables of the pool, for an ablation defined as "the shipped recipe, whatever it currently contains, plus/minus a block" -- those column names are not knowable before the frame is built.
        masks = built.masks(pool) if callable(built.masks) else built.masks
        base_cols = built.base_cols(pool) if callable(built.base_cols) else built.base_cols
        # Drop cook.py's bookkeeping columns from every mask. A mask derived from pool.columns (the natural way to say "the shipped recipe, whatever it contains") otherwise sweeps up `date`, and XGBoost rejects a datetime64 feature outright. compare_many never hits this because cook_many builds its feature list with _features(); the masked path takes the caller's list verbatim, so the guard belongs here.
        masks = {k: [c for c in v if c not in _STRUCTURAL] for k, v in masks.items()}
        if base_cols is not None:
            base_cols = [c for c in base_cols if c not in _STRUCTURAL]
        result = compare_masks(
            None,
            masks,
            target_col=target,
            task=exp.task,
            extra_importance_test=extra,
            pool=pool,
            base_cols=base_cols,
            **xgb_kw,
        )
        result.attrs["mask_diagnostics"] = _mask_diagnostics(pool, masks)
    else:
        result = compare_many(
            built, sites, target_col=target, task=exp.task, extra_importance_test=extra, **xgb_kw
        )
    rec = _record(exp, exp.task, sites, result, time.time() - t0, extra, mode_label, xgb_kw)
    rec["seed"] = seed
    _log(rec)
    w = rec["winner"]
    print(f"  WINNER: {w['recipe']}  ({w['by']}={w['value']})  ->  logged to {_LOG.name}")
    render_md()
    result.attrs["record"] = rec
    return result


def run_experiment(
    question,
    recipes: "dict | Callable[[str], dict]",
    task: str = "reg",
    *,
    sites=None,
    mode: str = "test",
    kind: str = "many",
    extra: bool = False,
    id: str = "adhoc",
    seed: int | None = None,
):
    """Run a ONE-OFF experiment directly -- no @experiment, no REGISTRY, no CLI. For a notebook or a scratch experiment file: build a {name: recipe} dict, call this, inspect the returned DataFrame. Always logs to experiments.jsonl and refreshes experiments.md, exactly like a registered run.

    `recipes` is either a plain {name: recipe_fn} dict (the common one-off case) or a build(task)->dict callable (registry style, if you want the recipe set to depend on the task). Each recipe is site_uid -> DataFrame INCLUDING the task's target column (nitrate_con for reg, violation for clf) -- the same contract the registered recipes satisfy. The FIRST recipe is the paired baseline.

    Pass sites=[...] to pin the pool (mode is then ignored, logged as 'custom'); otherwise `mode` slices the default oldest-first pool ('test'/'small'/'med'/'full')."""
    if task not in ("reg", "clf"):
        raise ValueError(f"task must be 'reg' or 'clf', got {task!r}")
    if not question or not question.strip():
        raise ValueError("run_experiment needs a non-empty question string.")
    build: "Callable[[str], dict]" = recipes if callable(recipes) else (lambda _task: dict(recipes))
    exp = Experiment(id=id, question=question.strip(), build=build, task=task, kind=kind, sites=sites)
    return run(exp, mode=mode, extra=extra, seed=seed)


# ── read the log back ─────────────────────────────────────────────────────────


def summary() -> None:
    records = _latest_per_exp(_load_log())
    if not records:
        print("No experiment runs logged yet.")
        return
    for key in records:
        print("\n" + _format_run(records[key], md=False))


def render_md() -> None:
    """Refresh logs/experiments.md. The rendering itself lives in logs/render_logs.py -- that module owns every log in logs/, and a second copy here would drift from it silently."""
    render_experiments()


# ── CLI ───────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    for m in MODES:
        g.add_argument(f"--{m}", dest="mode", action="store_const", const=m)
    ap.add_argument("--extra", action="store_true", help="also run slow permutation importance")
    ap.add_argument("--seed", type=int, default=None,
                    help="override XGBoost random_state; replicate a run to report a spread instead of a point estimate")
    ap.add_argument("--summary", action="store_true", help="print latest result per experiment and exit")
    ap.add_argument("--render", action="store_true", help="(re)write logs/experiments.md and exit")
    ap.add_argument("ids", nargs="*", help="experiment id(s) to run, in order (REQUIRED; omit to list available ids)")
    args = ap.parse_args()

    import exps  # noqa: F401  -- populates REGISTRY via @experiment

    if args.summary:
        summary()
        return
    if args.render:
        render_md()
        return

    if not args.ids:  # running requires an explicit id -- never run the whole registry implicitly
        print("No experiment id(s) specified. Available experiments:")
        for i, exp in REGISTRY.items():
            print(f"  {i:6s} [{exp.task}]  {exp.question}")
        print("\nSpecify one or more ids to run, e.g.:  python run_exp.py --med 26 26c")
        return

    mode = args.mode or "test"
    ids = args.ids
    unknown = [i for i in ids if i not in REGISTRY]
    if unknown:
        raise SystemExit(f"unknown experiment id(s): {unknown}. Registered: {list(REGISTRY)}")
    print(f"Running {len(ids)} experiment(s) in {mode} mode: {ids}")
    for i in ids:
        run(REGISTRY[i], mode, args.extra, seed=args.seed)  # each run logs + refreshes experiments.md


if __name__ == "__main__":
    # Re-dispatch through the real module name so `import exps` (which does `from run_exp import experiment`) registers into the SAME module object main() reads,
    # not a second copy under __main__.
    import run_exp

    run_exp.main()
