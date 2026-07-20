import sys
import json
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))  # repo root on path

import src.eval.cook as cook  # for the runtime _FAR_BUDGET override
from src.eval.cook import compare_many, fit_full, save_model
from src.features.recipes import recipe_REG, recipe_CLF

REAL_XGB_old = dict(
    n_estimators=5000,  # high ceiling; early stopping picks the real count
    learning_rate=0.02,  # low -> more trees, better generalization
    max_depth=4,  # shallow; cross-site signal is broad, deep trees memorize sites
    min_child_weight=10,  # leaves need real support -> resists site-specific noise
    subsample=0.7,  # row bagging -> variance reduction
    colsample_bytree=0.7,  # feature bagging -> avoids over-leaning on fm1000-type proxies
    reg_lambda=5.0,  # L2 on leaf weights
    reg_alpha=0.0,  # add L1 (~0.1-1) if you want feature sparsity
    early_stopping_rounds=50,  # needs the val holdout fit_full / cook_many carve
    random_state=42,
)

# current best for regression (from `python _tune.py reg`: lofo_r2 0.343)
REAL_XGB_REG = dict(
    n_estimators=5000,
    learning_rate=0.01,
    max_depth=3,
    min_child_weight=10,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=5.0,
    reg_alpha=0.0,
    early_stopping_rounds=50,
    random_state=42,
)

# current best for classification, but all the results in _tune.py were nearly identical.
REAL_XGB_CLF = dict(
    n_estimators=5000,
    learning_rate=0.01,
    max_depth=3,
    min_child_weight=10,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=5.0,
    reg_alpha=0.0,
    early_stopping_rounds=50,
    random_state=42,
)

OUT = Path(__file__).resolve().parent / "models"  # src/models/models -- anchored (CWD-independent, like _LOGFILE)
_LOGFILE = _ROOT / "logs" / "fulltrain_logs.json"
_LOFO_FILE = OUT / "lofo_tune.csv"  # tune.py's per-recipe winner table (holds best_iteration)


def _tuned_iters(recipe_name):
    """best_iteration recorded for `recipe_name` by tune.py (models/lofo_tune.csv), or None if the file / row / column is absent. None -> the full train falls back to fit_full(final_fit=True), which learns the tree count from its own holdout instead of the tuning run."""
    if not _LOFO_FILE.exists():
        return None
    df = pd.read_csv(_LOFO_FILE)
    if "best_iteration" not in df.columns:
        return None
    row = df[df["recipe"] == recipe_name]
    if row.empty or pd.isna(row["best_iteration"].iloc[0]):
        return None
    return int(row["best_iteration"].iloc[0])


def _to_native(o):
    """json fallback for the numpy scalars in the metric / importance tables."""
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON-serializable: {type(o).__name__}")


def log_metadata(name, recipe, target_col, task, xgb, scores, file=None):
    """Append one training-run record to _LOGFILE and return the integer key it was filed under."""
    imp = scores.attrs.get("importance", {}).get(name)
    imp_perm = scores.attrs.get("importance_perm", {}).get(name)
    model_entry = {
        "name": name,
        "recipe": getattr(recipe, "__name__", str(recipe)),  # a recipe is a function -> store its name
        "features": list(imp.index) if imp is not None else [],  # full feature column list (gain-ranked)
        "target_col": target_col,
        "task": task,
        "xgb": dict(xgb),
        "score": scores.loc[name].to_dict(),  # the scalar metric row (n_sites, lofo_r2, ...)
        "importance": imp.to_dict() if imp is not None else {},  # feature -> mean gain
        "importance_perm": imp_perm.to_dict() if imp_perm is not None else {},  # feature -> perm importance
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


def build(name, recipe, target_col, task, xgb, final_iters=None, min_rows=500):
    # first check the directory exists before committing
    OUT.mkdir(exist_ok=True)

    # then do CV
    print(f"\n===== {name} ({task}) =====")
    print("[1/2] evaluating cross-site (LOSO/LOFO)...")
    scores = compare_many(
        {name: recipe},
        sites=None,
        target_col=target_col,
        task=task,
        extra_importance_test=True,
        min_rows=min_rows,
        **xgb,
    )
    print(scores.round(3).to_string())
    key = log_metadata(name, recipe, target_col, task, xgb, scores)
    print(f"  logged run #{key} -> {_LOGFILE}")

    # then fit the deployable model on ALL rows. Prefer the tuning-run tree count when available:
    # fix n_estimators at best_iteration and disable early stopping, so the shipped model uses the
    # tuned count on 100% of the rows. Otherwise final_fit=True lets fit_full learn the count from
    # its own holdout, then refit on all rows.
    if final_iters is None:
        final_iters = _tuned_iters(getattr(recipe, "__name__", ""))
    fit_kw = dict(xgb)
    if final_iters is not None:
        fit_kw["n_estimators"] = int(final_iters)
        fit_kw["early_stopping_rounds"] = None
        print(f"[2/2] fitting deployable model on all rows ({final_iters} trees, from tuning run)...")
    else:
        print("[2/2] fitting deployable model on all rows (best_iteration from fit_full holdout)...")
    model, feat, _imp = fit_full(
        recipe, sites=None, target_col=target_col, task=task, final_fit=True, progress=True, min_rows=min_rows, **fit_kw
    )
    for f in save_model(model, feat, str(OUT / f"{name}.json"), task=task, target_col=target_col):
        print(f"  wrote {f}")


def main():
    parser = argparse.ArgumentParser(description="Train + log the shipped recipe(s).")
    parser.add_argument(
        "--false-alarm-rate",
        type=float,
        default=None,
        metavar="FAR",
        help="False-alarm-rate (FPR) budget for the recall_at_far metric, in (0, 1) -- e.g. 0.2 for "
        f"20%%. Default: cook._FAR_BUDGET = {cook._FAR_BUDGET:.2f}.",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="recipe_CLF2",
        help="Name for the trained model: the booster is saved to models/<name>.json and the run is "
        "logged under this name in fulltrain_logs.json. Default: recipe_CLF2.",
    )
    args = parser.parse_args()
    if args.false_alarm_rate is not None:
        if not 0.0 < args.false_alarm_rate < 1.0:
            parser.error("--false-alarm-rate must be in (0, 1)")
        cook._FAR_BUDGET = args.false_alarm_rate  # resolved at call time by _imbalance_suite
        print(f"[cfg] recall_at_far false-alarm budget set to {cook._FAR_BUDGET:.2%}")

    # final_iters defaults to None -> build() auto-reads best_iteration from models/lofo_tune.csv
    # (whatever `python tune.py` last recorded for recipe_REG / recipe_CLF).
    # build("recipe_REG2", recipe_REG, target_col="nitrate_con", task="reg", xgb=REAL_XGB_REG)
    build(args.name, recipe_CLF, target_col="violation", task="clf", xgb=REAL_XGB_CLF)


if __name__ == "__main__":
    main()
