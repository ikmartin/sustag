"""_tune.py -- grid-search XGBoost hyperparameters against the HONEST LOFO metric.

build_model.py's REAL_XGB config is hand-picked. This picks it instead: it sweeps a small
grid of the hyperparameters that matter most for cross-site transfer on this dataset (tree
depth, learning rate, optionally the regularizers) and ranks every config by its
leave-one-family-out score -- lofo_r2 for regression, lofo_auc for classification -- the
honest end of the bracket (lofo <= real generalisation <= loso). LOSO is reported too so you
can watch the leakage gap; a big LOSO->LOFO drop is the cue to lean harder on reg_lambda /
min_child_weight (try --regular).

Recipes: by default recipe_REG / recipe_CLF from recipes3 (point --module elsewhere, e.g.
recipes2, to tune a variant). The best config per recipe is upserted -- one row keyed by
recipe name -- into models/lofo_tune.csv: tune the same recipe again and its row is replaced;
tune a differently-named recipe and a row is added. The full per-run grid also lands in
models/tune_<task>.csv for inspection.

NOTE: recipes2.recipe_REG and recipes3.recipe_REG share the name "recipe_REG", so tuning one
overwrites the other's row in lofo_tune.csv.

Speed: building+merging every site's recipe frame (the "pool") is the slow part, so we build
it ONCE per task and reuse the same X / y / groups for every config -- each config then only
costs its CV fits.

Run from isaac/ (same as build_model.py):
    python _tune.py                 # tune BOTH reg + clf on all sites, default depth x lr grid
    python _tune.py reg             # just regression
    python _tune.py clf --regular   # classification, also sweep reg_lambda + min_child_weight
    python _tune.py --module recipes2   # tune the recipes2 variants instead
    python _tune.py --fast          # 12 sites + small grid + low ceiling (smoke test)
    python _tune.py reg --depths 3,4,5 --lrs 0.01,0.02,0.05 --sites 40
"""

from __future__ import annotations

import argparse
import importlib
import itertools
import time
from pathlib import Path

import pandas as pd

from cook import _pool, _features, _target, basin_groups, _grouped_oof, _score
from data.access import get_site_ids

OUT = Path("models")
LOFO_FILE = OUT / "lofo_tune.csv"  # shared summary: one upserted best-row per recipe

# Everything NOT swept. The swept keys (max_depth, learning_rate, and with --regular also
# reg_lambda + min_child_weight) are layered on top per config. early_stopping stays ON with a
# high n_estimators ceiling so each config picks its own tree count -- the only fair way to
# compare learning rates. Mirrors build_model.REAL_XGB so the winner drops straight in.
BASE_XGB = dict(
    n_estimators=5000,
    learning_rate=0.02,
    max_depth=4,
    min_child_weight=10,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=5.0,
    reg_alpha=0.0,
    early_stopping_rounds=50,
    random_state=42,
)

# task -> (recipe attr on the module, target column, higher-is-better _score key)
_TASK_SPEC = {
    "reg": ("recipe_REG", "nitrate_con", "r2"),
    "clf": ("recipe_CLF", "violation", "auc"),
}
_INT_KEYS = ("max_depth", "min_child_weight")  # cast back to int when echoing the winner
# identity/metric columns that precede the hyperparameter columns in lofo_tune.csv
_LEAD_COLS = ("recipe", "task", "metric", "lofo", "loso", "gap")


def _grid(depths, lrs, regular):
    """Cartesian product of the swept hyperparameters -> list of override dicts."""
    lambdas = [1.0, 5.0, 10.0] if regular else [BASE_XGB["reg_lambda"]]
    mcws = [5, 10, 20] if regular else [BASE_XGB["min_child_weight"]]
    return [
        dict(max_depth=d, learning_rate=lr, reg_lambda=lam, min_child_weight=mcw)
        for d, lr, lam, mcw in itertools.product(depths, lrs, lambdas, mcws)
    ]


def _upsert_lofo(row):
    """Write `row` into models/lofo_tune.csv, replacing any existing row for the same recipe.

    Keyed on the 'recipe' column: tuning a recipe again overwrites its line; a new recipe name
    appends a line. Columns are ordered identity/metrics first, then the hyperparameters."""
    OUT.mkdir(exist_ok=True)
    new = pd.DataFrame([row])
    if LOFO_FILE.exists():
        old = pd.read_csv(LOFO_FILE)
        old = old[old["recipe"] != row["recipe"]]  # drop the prior row for this recipe
        out = pd.concat([old, new], ignore_index=True)
        cols = list(row) + [c for c in old.columns if c not in row]  # keep any legacy extra cols
        out = out.reindex(columns=cols)
    else:
        out = new
    out = out.sort_values(["task", "recipe"]).reset_index(drop=True)
    out.to_csv(LOFO_FILE, index=False)
    return out


def tune_task(task, recipe, target_col, metric, sites, depths, lrs, regular, n_splits, ceiling):
    """Pool once, score every grid config by LOFO (and LOSO), upsert the winner to lofo_tune.csv."""
    print(f"\n===== tuning {task} ({recipe.__name__}, target={target_col!r}) =====")

    # build the mega-frame a SINGLE time; reused for every config below
    pool = _pool(recipe, sites, target_col, progress_label=recipe.__name__)
    feat = _features(pool, target_col)
    X, y = pool[feat], _target(pool, target_col, task)
    site_grp, fam_grp = pool["site"], basin_groups(pool["site"])
    n_fam = fam_grp.nunique()
    print(f"  pooled {site_grp.nunique()} sites, {len(pool)} rows, {len(feat)} feats, {n_fam} families")
    if n_fam < 2:
        print("  [warn] <2 basin families -> LOFO undefined; ranking on LOSO instead")

    grid = _grid(depths, lrs, regular)
    print(f"  sweeping {len(grid)} configs (lofo_{metric} = honest score)\n")

    rows, t0 = [], time.time()
    for i, override in enumerate(grid, 1):
        cfg = {**BASE_XGB, **override, "n_estimators": ceiling}
        loso = _score(y, _grouped_oof(X, y, site_grp, task, n_splits, **cfg), task)[metric]
        lofo = (
            _score(y, _grouped_oof(X, y, fam_grp, task, n_splits, **cfg), task)[metric]
            if n_fam >= 2
            else float("nan")
        )
        rows.append({**override, "lofo": lofo, "loso": loso, "gap": loso - lofo})
        tag = ", ".join(f"{k}={override[k]}" for k in override)
        print(
            f"  [{i:>2}/{len(grid)}] {tag:<52.52s} "
            f"lofo_{metric}={lofo:+.4f}  loso_{metric}={loso:+.4f}  ({time.time() - t0:4.0f}s)",
            flush=True,
        )

    rank_col = "lofo" if n_fam >= 2 else "loso"
    res = pd.DataFrame(rows).sort_values(rank_col, ascending=False).reset_index(drop=True)

    print(f"\n  --- {task} ranked by {rank_col}_{metric} (best first) ---")
    print(res.round(4).to_string(index=False))

    OUT.mkdir(exist_ok=True)
    grid_fp = OUT / f"tune_{task}.csv"
    res.to_csv(grid_fp, index=False)
    print(f"  wrote full grid -> {grid_fp}")

    best = res.iloc[0]
    winner = {**BASE_XGB, **{k: (int(best[k]) if k in _INT_KEYS else float(best[k])) for k in grid[0]}}

    # one self-describing best-row per recipe, upserted into the shared summary file
    summary = {
        "recipe": recipe.__name__,
        "task": task,
        "metric": metric,
        "lofo": round(float(best["lofo"]), 6),
        "loso": round(float(best["loso"]), 6),
        "gap": round(float(best["gap"]), 6),
        **winner,
    }
    _upsert_lofo(summary)
    print(f"  upserted best row (recipe={recipe.__name__!r}) -> {LOFO_FILE}")

    print(f"\n  BEST {task}: {rank_col}_{metric}={best[rank_col]:+.4f}  (loso gap {best['gap']:+.4f})")
    print("  paste into build_model.REAL_XGB:")
    print("  REAL_XGB = dict(")
    for k, v in winner.items():
        print(f"      {k}={v!r},")
    print("  )")
    return res


def _csv_ints(s):
    return [int(x) for x in s.split(",")]


def _csv_floats(s):
    return [float(x) for x in s.split(",")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", nargs="?", default="both", choices=["reg", "clf", "both"],
                    help="which target to tune (default: both)")
    ap.add_argument("--module", default="recipes3",
                    help="module to import recipe_REG/recipe_CLF from (default: recipes3)")
    ap.add_argument("--depths", type=_csv_ints, default=[3, 4, 5, 6], help="max_depth values (default 3,4,5,6)")
    ap.add_argument("--lrs", type=_csv_floats, default=[0.01, 0.02, 0.05],
                    help="learning_rate values (default 0.01,0.02,0.05)")
    ap.add_argument("--regular", action="store_true",
                    help="ALSO sweep reg_lambda (1,5,10) x min_child_weight (5,10,20) -- 9x the grid")
    ap.add_argument("--sites", type=int, default=None, help="limit to the first N sites (default: all)")
    ap.add_argument("--splits", type=int, default=5, help="GroupKFold splits for LOSO/LOFO (default 5)")
    ap.add_argument("--fast", action="store_true",
                    help="smoke-test: 12 sites, depths 3,4, lrs 0.02,0.05, n_estimators ceiling 800")
    args = ap.parse_args()

    mod = importlib.import_module(args.module)

    depths, lrs, ceiling = args.depths, args.lrs, BASE_XGB["n_estimators"]
    sites = get_site_ids() if args.sites is None else get_site_ids()[: args.sites]
    if args.fast:
        depths, lrs, ceiling = [3, 4], [0.02, 0.05], 800
        sites = get_site_ids()[:12]

    for task in (["reg", "clf"] if args.task == "both" else [args.task]):
        recipe_attr, target_col, metric = _TASK_SPEC[task]
        recipe = getattr(mod, recipe_attr)
        tune_task(task, recipe, target_col, metric, sites, depths, lrs, args.regular, args.splits, ceiling)


if __name__ == "__main__":
    main()
