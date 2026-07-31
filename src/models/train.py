import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))  # repo root on path

import src.eval.cook as cook  # for the runtime _FAR_BUDGET override
from src.eval.cook import compare_many, fit_full, save_model, _pool_wide
from src.features.recipes import island_REG, island_CLF, network_REG, network_CLF
from src.data.access import get_site_ids

# Task BASE config -- what every recipe inherits and tune.py's `DEFAULT (inherited)` reference row measures. NO n_estimators: a tree count is only meaningful for the config it was tuned WITH, so it lives in RECIPE_XGB beside the rest of that config. No early_stopping_rounds either -- cook's fold models carry no eval_set, so a non-None value raises there by design.
REAL_XGB_REG = dict(
    learning_rate=0.02,
    max_depth=4,
    min_child_weight=10,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=5.0,
    reg_alpha=0.0,
    random_state=42,
)

REAL_XGB_CLF = dict(
    learning_rate=0.01,
    max_depth=3,
    min_child_weight=10,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=5.0,
    reg_alpha=0.0,
    random_state=42,
)

# Per-RECIPE overrides, layered over the task base. THE WHOLE tuned config for a recipe lives here, tree count included: it is the one place a fit's hyperparameters come from, it is in version control, and a diff shows exactly what moved. Populate by pasting the block `python src/models/fulltune.py --task <task>` prints.
#
# A recipe with no `n_estimators` here is UNTUNED and cannot be fitted. That is deliberate: a tree count is only meaningful for the config it was tuned with, so taking it from anywhere else risks pairing one sweep's count with another sweep's depth. tune.py's models/tuning_logs.json is the tuner's own record, NOT an input to training.
RECIPE_XGB = {
    "island_REG": {
        "n_estimators": 480,
        "max_depth": 5,
        "learning_rate": 0.05,
        "reg_lambda": 307.95275,
        "min_child_weight": 10,
        "colsample_bytree": 0.5,
    },
    "island_CLF": {
        "n_estimators": 580,
        "max_depth": 3,
        "learning_rate": 0.05,
        "reg_lambda": 659.396334,
        "min_child_weight": 4.395976,
        "colsample_bytree": 0.5,
    },
}

OUT = Path(__file__).resolve().parent / "models"  # src/models/models -- anchored (CWD-independent, like _LOGFILE)
_LOGFILE = _ROOT / "logs" / "fulltrain_logs.json"


class UntunedRecipe(RuntimeError):
    """A recipe with no tuned tree count in RECIPE_XGB. Carries the command that produces one."""


def xgb_for(recipe, task):
    """Full XGBoost config for `recipe`: the task base with that recipe's RECIPE_XGB overrides layered on.

    The single source of a fit's hyperparameters -- CV, deployable fit and tune_threshold all go through it, so they cannot drift apart. Unknown recipe -> the bare task base, which _tuned_iters then rejects.
    """
    base = REAL_XGB_REG if task == "reg" else REAL_XGB_CLF
    return {**base, **RECIPE_XGB.get(getattr(recipe, "__name__", str(recipe)), {})}


def _tuned_iters(xgb, recipe_name, task):
    """Tree count out of an `xgb_for` config, or raise UntunedRecipe.

    With early stopping gone nothing here can derive a count, and inheriting cook's default ceiling over-trains by ~8x -- so a missing count is fatal rather than defaulted.
    """
    n = xgb.get("n_estimators")
    if n:
        return int(n)
    variant = recipe_name.split("_")[0] if recipe_name.split("_")[0] in ("island", "network") else "island"
    raise UntunedRecipe(
        f"{recipe_name!r} has no tuned tree count in train.RECIPE_XGB, so it cannot be fitted.\n"
        f"  Run:  python src/models/fulltune.py --task {task} --variant {variant}\n"
        f"  then paste the block it prints -- n_estimators included -- into RECIPE_XGB[{recipe_name!r}]."
    )


def _to_native(o):
    """json fallback for the numpy scalars in the metric / importance tables."""
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON-serializable: {type(o).__name__}")


def log_metadata(name, recipe, target_col, task, xgb, scores, file=None, true_lofo=False):
    """Append one training-run record to _LOGFILE and return the integer key it was filed under.

    `true_lofo` records WHICH holdout produced the lofo_* entries of `score`. The true
    leave-one-family-out regime returns the IDENTICAL column set as the default GroupKFold one but a
    systematically different (higher) number, so without this field the log cannot tell two runs
    apart and a cross-regime comparison would read as a model change. Absent on pre-flag entries,
    which were all GroupKFold.
    """
    imp = scores.attrs.get("importance", {}).get(name)
    imp_perm = scores.attrs.get("importance_perm", {}).get(name)
    model_entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # when this run was logged (UTC)
        "name": name,
        "recipe": getattr(recipe, "__name__", str(recipe)),  # a recipe is a function -> store its name
        "features": list(imp.index) if imp is not None else [],  # full feature column list (gain-ranked)
        "target_col": target_col,
        "task": task,
        "true_lofo": bool(true_lofo),  # which family holdout produced the lofo_* scores
        "xgb": dict(xgb),
        "score": scores.loc[name].to_dict(),  # the scalar metric row (n_sites, lofo_r2, ...)
        "importance": imp.to_dict() if imp is not None else {},  # feature -> mean gain
        "importance_perm": imp_perm.to_dict() if imp_perm is not None else {},  # feature -> perm importance
    }
    # Decision thresholds, for classifiers. Derived from the LOFO out-of-fold vector the CV already produced, so every run carries a reproducible beta table without a second grouped CV. `base_rate` is prevalence in the SCORED rows -- not the pooled `base` in `score` when a capped true-LOFO run leaves part of the pool unpredicted -- and FDR is prevalence-dependent, so the two must not be read interchangeably.
    ops = (scores.attrs.get("operating_points") or {}).get(name)
    if ops:
        model_entry |= {
            "beta_table": ops["beta_table"],
            "base_rate": ops["base_rate"],
            "beta_table_coverage": ops["coverage"],
        }

    _LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    if _LOGFILE.exists() and _LOGFILE.stat().st_size > 0:
        with open(_LOGFILE) as f:
            log = json.load(f)
    else:
        log = {}
    key = max((int(k) for k in log), default=-1) + 1  # next integer not currently in the log
    log[str(key)] = model_entry
    with open(_LOGFILE, "w") as f:
        json.dump(log, f, indent=2, default=_to_native)
    return key


def build(name, recipe, target_col, task, xgb, final_iters=None, true_lofo=False, max_holdout_pct=0.2):
    # first check the directory exists before committing
    OUT.mkdir(exist_ok=True)

    # Resolve the tree count BEFORE anything expensive, and fold it into `xgb` so the CV below scores the model that actually ships. They used to differ -- the CV ran at whatever n_estimators the config carried while the deployable was fitted at the tuned count -- so the metrics in fulltrain_logs.json described a different model from the booster beside them, and the logged `xgb` dict did not say which. Raising here also means an untuned recipe fails in a second rather than after a full pooling pass.
    recipe_name = getattr(recipe, "__name__", str(recipe))
    if final_iters is None:
        final_iters = _tuned_iters(xgb, recipe_name, task)
    xgb = {**xgb, "n_estimators": int(final_iters)}

    # Pool ONCE and hand the same frame to both phases: compare_many and fit_full each pool internally, so a train used to build the identical frame twice.
    pool = _pool_wide(recipe, get_site_ids(), target_col, progress_label=recipe_name)

    # then do CV
    print(f"\n===== {name} ({task}) =====")
    print(f"[1/2] evaluating cross-site (LOSO/{'true LOFO' if true_lofo else 'LOFO'})...")
    scores = compare_many(
        {name: recipe},
        sites=None,
        target_col=target_col,
        task=task,
        extra_importance_test=True,
        true_lofo=true_lofo,
        max_holdout_pct=max_holdout_pct,
        pool=pool,
        **xgb,
    )
    print(scores.round(3).to_string())
    key = log_metadata(name, recipe, target_col, task, xgb, scores, true_lofo=true_lofo)
    print(f"  logged run #{key} -> {_LOGFILE}")

    # Then fit the deployable model on ALL rows -- same config the CV above just scored, tree count included.
    print(f"[2/2] fitting deployable model on all rows ({final_iters} trees, from RECIPE_XGB)...")
    model, feat, _imp = fit_full(recipe, sites=None, target_col=target_col, task=task, progress=True, pool=pool, **xgb)
    written = save_model(model, feat, str(OUT / f"{name}.json"), task=task, target_col=target_col, recipe=recipe_name)
    for f in written:
        print(f"  wrote {f}")

    # A classifier emits P(violation) and is useless without a cutoff, so ship the operating points beside it: the sidecar the widget reads carries both how to shape the input (feat/task/target) and how to turn a probability into an alarm. Patched, not rewritten, so save_model's fields survive. tune_threshold.py re-points a DEPLOYED booster later without retraining; this just means a fresh one is usable immediately.
    ops = (scores.attrs.get("operating_points") or {}).get(name)
    if ops:
        meta_path = Path(f"{OUT / f'{name}.json'}.meta.json")
        meta = json.loads(meta_path.read_text())
        meta |= {"beta_table": ops["beta_table"], "base_rate": ops["base_rate"], "beta_table_coverage": ops["coverage"]}
        meta_path.write_text(json.dumps(meta, indent=2))
        print(
            f"  beta table: {len(ops['beta_table'])} operating points, base rate {ops['base_rate']:.3f}, "
            f"from {ops['coverage']:.1%} of pooled rows -> {meta_path.name}"
        )


# Per-(task, variant) training config. Keyed on both because the split is real: ISLAND carries only features computable at an arbitrary ungauged pin, NETWORK adds the reach-graph donor block and is undefined without a resolvable up/downstream monitored neighbour. No `xgb` here -- it comes from xgb_for(recipe, task), keyed on the RECIPE, so each of the four gets its own tuned config rather than a shared one.
#
# The model NAME is the recipe's own __name__. With four models, two of them REG, the filename is the only thing that tells them apart.
_TARGETS = {"reg": "nitrate_con", "clf": "violation"}
_RECIPES = {
    ("reg", "island"): island_REG,
    ("clf", "island"): island_CLF,
    ("reg", "network"): network_REG,
    ("clf", "network"): network_CLF,
}


def _next_free_name(stem, out=None):
    """`stem`, or stem2/stem3/... -- the first for which no <name>.json exists in `out`.

    Keyed on the FILESYSTEM, deliberately not on fulltrain_logs.json: a logged model may have been deleted, and reading the log would resurrect a name whose artifact is gone and refuse to reuse it. What must not be clobbered is a booster on disk.
    """
    out = OUT if out is None else out
    if not (out / f"{stem}.json").exists():
        return stem
    n = 2
    while (out / f"{stem}{n}.json").exists():
        n += 1
    return f"{stem}{n}"


def main():
    parser = argparse.ArgumentParser(description="Train + log the shipped recipes for one or both tasks.")
    parser.add_argument(
        "task",
        type=str.lower,
        choices=["reg", "clf", "both"],
        help="Which task to train (REQUIRED): 'reg' (-> nitrate_con), 'clf' (-> violation), or 'both'. "
        "Case-insensitive, so REG/CLF also work. With the default --recipe both, `train.py both` trains "
        "all four models.",
    )
    parser.add_argument(
        "--recipe",
        type=str.lower,
        choices=["island", "network", "both"],
        default="both",
        help="Which variant(s): 'island' is deployable at any pin, 'network' adds the reach-graph donor "
        "block and needs a resolvable up/downstream monitored neighbour. Default 'both'.",
    )
    parser.add_argument(
        "--false-alarm-rate",
        type=float,
        default=None,
        metavar="FAR",
        help="False-alarm-rate (FPR) budget for the recall_at_far metric, in (0, 1) -- e.g. 0.2 for "
        f"20%%. clf only. Default: cook._FAR_BUDGET = {cook._FAR_BUDGET:.2f}.",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="PREFIX for the trained model names, not a replacement: --name testy gives testy_island_REG, "
        "testy_network_CLF, and so on. Bare, the names are island_REG / island_CLF / network_REG / "
        "network_CLF. If <name>.json already exists in models/, a number is appended (island_REG2, then "
        "island_REG3) so an existing booster is never overwritten.",
    )
    parser.add_argument(
        "--true_lofo",
        action="store_true",
        help="evaluate with a TRUE leave-one-family-out holdout (one fold per basin family) instead "
        "of the default GroupKFold LOFO. Same lofo_* column names, so the run is logged with a "
        "true_lofo flag -- the two regimes are NOT comparable to each other.",
    )
    parser.add_argument(
        "--max_holdout_pct",
        type=float,
        default=0.2,
        help="with --true_lofo: a basin family above this share of pooled rows is never held out "
        "(it still trains). Default 0.2, which on the 123-site cohort bars one 25%%-of-rows family.",
    )
    args = parser.parse_args()

    tasks = ["reg", "clf"] if args.task == "both" else [args.task]
    variants = ["island", "network"] if args.recipe == "both" else [args.recipe]

    if args.false_alarm_rate is not None:
        if "clf" not in tasks:
            parser.error("--false-alarm-rate applies to clf only (recall_at_far is a classification metric)")
        if not 0.0 < args.false_alarm_rate < 1.0:
            parser.error("--false-alarm-rate must be in (0, 1)")
        cook._FAR_BUDGET = args.false_alarm_rate  # resolved at call time by _imbalance_suite
        print(f"[cfg] recall_at_far false-alarm budget set to {cook._FAR_BUDGET:.2%}")

    # Resolve every name BEFORE fitting anything, so a collision or a typo'd prefix surfaces in a second rather than after the first model's CV. Resolved in order, and _next_free_name reads the directory each time, so two variants of one task cannot claim the same stem.
    plan = []
    for task in tasks:
        for variant in variants:
            recipe = _RECIPES[(task, variant)]
            stem = f"{args.name}_{recipe.__name__}" if args.name else recipe.__name__
            plan.append((_next_free_name(stem), recipe, task))
    print(f"[plan] {len(plan)} model(s): " + ", ".join(n for n, _, _ in plan))

    # The whole config -- tree count included -- comes from RECIPE_XGB via xgb_for. An untuned recipe stops here rather than falling back to anything, so a network_* model that has never been tuned fails loudly instead of shipping some other recipe's tree count.
    for name, recipe, task in plan:
        try:
            build(
                name,
                recipe,
                target_col=_TARGETS[task],
                task=task,
                xgb=xgb_for(recipe, task),
                true_lofo=args.true_lofo,
                max_holdout_pct=args.max_holdout_pct,
            )
        except UntunedRecipe as e:
            raise SystemExit(f"\n{e}")


if __name__ == "__main__":
    main()
