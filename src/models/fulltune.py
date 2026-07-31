"""fulltune.py -- run tune.py's staged protocol end to end and print the config to paste into train.RECIPE_XGB.

`python tune.py` sweeps ONE stage and stops. This chains all six, threading each stage's winner into the next as --fix, and ends at the settled config with its own resolved tree count.

    python src/models/fulltune.py                  # both tasks
    python src/models/fulltune.py --task reg       # one task
    python src/models/fulltune.py --sites 25 --ceiling 400   # fast shakedown

STAGE ORDER follows axis coupling, not importance -- see tune.py's module docstring:

    1  depth x lr      largest effects, and everything downstream needs a depth to sit at
    2  depth x lam     re-crosses depth on purpose: this is the pair where settling one and then the other would miss (deep, large-lambda), which is measurably where the optimum lives
    3  mcw             weakly coupled to lam (one blocks splits, the other shrinks the leaves of splits that survive) -> coordinate is sound
    4  subsample
    5  colsample
    final              nothing searched, everything pinned: resolves best_k for a combination the coordinate stages may never have scored together

THE CEILING ADAPTS, AND CLIMBS IF IT HAS TO. Stage 1 runs at `--ceiling` and every config reports the count it actually wanted; stages 2-5 then start from the largest best_k stage 1 produced, since scanning above it is pure prediction cost. The FINAL stage returns to at least the original ceiling, because the settled combination is new and may want more trees than any single-axis winner did.

A stage whose winner lands at >=80% of its ceiling is re-run at DOUBLE, repeatedly, up to `_MAX_DOUBLINGS` times -- a binding ceiling is not a result, it is the tree count being chosen by the scan bound rather than by the data. This matters most at stage 2: stage 1 sets the starting ceiling with the regularizers at their inherited (inert) values, where models peak early, while stage 2 is exactly where the deep-and-regularized regime first appears and that regime wants MORE trees. Expect one doubling there, and read the log -- a stage that doubles more than twice is telling you the LADDER is truncated rather than the ceiling, almost certainly lam, since heavier lambda is what drives the tree count up. Widen --lams rather than letting the ceiling climb.

One pool per task is built and shared across every stage (tune.tune(pool=...)); building every site's recipe frame is the slow part and repeating it six times would dominate the run.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root on path

import src.models.tune as T
from src.eval.cook import _pool_wide
from src.data.access import get_site_ids

_STAGES = [["depth", "lam"], ["mcw"], ["subsample"], ["colsample"]]  # stage 1 is separate: it sets the ceiling
_BIND = T._BIND  # k_frac at or above this means the ceiling chose the tree count, not the data. Defined in tune.py, which stamps the same threshold onto every row as `ceiling_bound`, so the flag and the trigger cannot disagree.
_GROW = 1.5  # ceiling multiplier per retry, floored to an int
_MAX_RETRIES = 8  # backstop only. Each retry re-runs the WHOLE stage and a fit costs O(ceiling), so all 8 growths cost ~75x the first pass; it should stop after one or two.


def _winner_axes(df: pd.DataFrame) -> dict:
    """Axis-space values of the top-ranked row, for threading into the next stage's --fix.

    Only axes the row actually carries. A DEFAULT (inherited) winner contributes nothing, which is right: it means the inherited value beat every ladder rung, and leaving it unpinned inherits exactly that.
    """
    best = df.iloc[0]
    return {a: float(best[f"ax_{a}"]) for a in T._AXES if f"ax_{a}" in df.columns and pd.notna(best.get(f"ax_{a}"))}


def _stage(label, task, recipe, target_col, sites, search, fixed, ceiling, seeds, true_lofo, mhp, pool, append, splits, resolved=None):
    """Run one stage, re-running at a larger ceiling for as long as its winner comes back ceiling-bound. -> (result frame, the ceiling it settled at).

    Only the ceiling moves across retries: the ladder and the pinned axes are unchanged, so a retry is the same grid measured with room to breathe. The trigger reads the WINNER's k_frac, not the grid maximum -- a losing config that wanted more trees does not drag the whole stage up.

    Every retry reuses this stage's own `append`, rather than forcing True. Forcing it meant a ceiling-bound stage-1 attempt survived in the CSV that stage 1 had just overwritten, and df_out sorts by headline -- so a row known to be a truncated measurement could top the file while the frame threaded onward was the retry's. Reusing `append` lets stage 1's retry overwrite its own invalid attempt; later stages keep theirs, now flagged `ceiling_bound`.
    """
    fixed = {k: v for k, v in fixed.items() if k not in search}  # search wins over fix; strip BEFORE the header, so a re-crossed axis is not reported as pinned
    print(f"\n{'=' * 78}\n{label}  search={search or ['(none)']}  fix={fixed or '{}'}  ceiling={ceiling}\n{'=' * 78}")
    kw = dict(task=task, recipe=recipe, target_col=target_col, sites=sites, search=search, fixed=fixed,
              ladders={}, seeds=seeds, true_lofo=true_lofo, max_holdout_pct=mhp, n_splits=splits, pool=pool,
              resolved_axes=resolved)
    df = T.tune(ceiling=ceiling, append=append, **kw)

    for n in range(1, _MAX_RETRIES + 1):
        if float(df.iloc[0].get("k_frac", 0)) < _BIND:
            return df, ceiling
        # Floored to a multiple of _COARSE, because the scan grid is range(_COARSE, ceiling+1, _COARSE): an unaligned ceiling leaves the top of the scan up to _COARSE-1 trees short of the fit's own ceiling, so `at_ceiling` would report a score from a shorter prefix than its name claims.
        grown = max(ceiling + T._COARSE, int(ceiling * _GROW) // T._COARSE * T._COARSE)
        print(f"\n  [retry {n}/{_MAX_RETRIES}] winner used {int(df.iloc[0]['best_k'])} of {ceiling} trees -- ceiling-bound, redoing at {grown}")
        ceiling = grown
        df = T.tune(ceiling=ceiling, append=append, **kw)

    if float(df.iloc[0].get("k_frac", 0)) >= _BIND:
        print(f"\n  [warn] still ceiling-bound after {_MAX_RETRIES} growths (ceiling {ceiling}). The LADDER is the likelier problem at this point -- widen --lams before trusting this stage.")
    return df, ceiling


def full(task, recipe, target_col, sites, ceiling0, seeds, true_lofo, mhp, splits=5):
    name = getattr(recipe, "__name__", str(recipe))
    pool = _pool_wide(recipe, sites, target_col, progress_label=name)

    t0 = time.time()
    # Every axis the PROTOCOL has searched, not just the current stage. An axis whose stage was won outright by the inherited value is left unpinned by _winner_axes, so it never reaches a later stage's `fixed` -- without this the final stage reported axes it had already searched as untuned.
    resolved: set[str] = {"depth", "lr"}
    df, _ = _stage("STAGE 1/6  capacity x learning rate", task, recipe, target_col, sites, ["depth", "lr"], {},
                   ceiling0, seeds, true_lofo, mhp, pool, False, splits, resolved)
    fixed = _winner_axes(df)

    # every stage-1 config reported the tree count it wanted; nothing downstream STARTS above the largest of them, and _stage doubles from there if a later stage outgrows it
    ceiling = int(np.ceil(df["best_k"].max() / T._COARSE) * T._COARSE)
    print(f"\n  >> stage 1 best_k ranged {int(df.best_k.min())}-{int(df.best_k.max())}; stages 2-5 start at {ceiling} (was {ceiling0})")

    for i, search in enumerate(_STAGES, start=2):
        resolved |= set(search)
        df, ceiling = _stage(f"STAGE {i}/6", task, recipe, target_col, sites, search, fixed, ceiling, seeds,
                             true_lofo, mhp, pool, True, splits, resolved)
        fixed.update(_winner_axes(df))

    # the settled combination may never have been scored as a unit by the coordinate stages -- resolve its own best_k, at a ceiling no lower than the original since it can want more trees than any single-axis winner did
    df, _ = _stage("STAGE 6/6  confirmation at the settled config", task, recipe, target_col, sites, [], fixed,
                   max(ceiling0, ceiling), seeds, true_lofo, mhp, pool, True, splits, resolved)

    print(f"\n{'=' * 78}\nFULLTUNE {name} done in {(time.time() - t0) / 60:.0f} min   settled axes: {fixed}")
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=["reg", "clf", "both"], default="both")
    ap.add_argument("--variant", choices=["island", "network", "both"], default="island",
                    help="which recipe variant to tune: 'island' (deployable at any pin) or 'network' (adds the "
                         "reach-graph donor block). Defaults to island; --task both --variant both tunes all four.")
    ap.add_argument("--ceiling", type=int, default=1500,
                    help="stage-1 and confirmation ceiling; stages 2-5 start from stage 1's max best_k and double from there if bound")
    ap.add_argument("--sites", type=int, default=None,
                    help="cap to the first N sites (SHAKEDOWN ONLY -- it changes the family structure and so the LOFO difficulty; never pick values on a subset)")
    ap.add_argument("--seeds", default="42", help="comma-separated random_state values; >1 ranks on the seed mean")
    ap.add_argument("--true-lofo", action="store_true")
    ap.add_argument("--max-holdout-pct", type=float, default=0.2)
    ap.add_argument("--splits", type=int, default=5)
    a = ap.parse_args()

    import src.features.recipes as recipes

    sites = [str(s) for s in get_site_ids()]
    if a.sites:
        sites = sites[: a.sites]
    seeds = tuple(int(x) for x in a.seeds.split(","))

    tasks = ["reg", "clf"] if a.task == "both" else [a.task]
    variants = ["island", "network"] if a.variant == "both" else [a.variant]
    specs = [(t, *T._TASK_SPEC[(t, v)]) for t in tasks for v in variants]
    print(f"fulltune {len(specs)} recipe(s) on {len(sites)} sites: " + ", ".join(s[1] for s in specs))
    for task, recipe_attr, target_col in specs:
        full(task, getattr(recipes, recipe_attr), target_col, sites, a.ceiling, seeds,
             a.true_lofo, a.max_holdout_pct, a.splits)


if __name__ == "__main__":
    main()
