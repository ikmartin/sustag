"""fulltune.py -- chain tune.py's six stages end to end and print the finished config.

`tune.py` searches ONE stage; this threads each stage's winner into the next as --fix and ends with a confirmation run at the settled config.

    python fulltune.py                            # all four: {reg,clf} x {base,full}
    python fulltune.py --task reg --cols full     # one block
    python fulltune.py --sites 25 --ceiling 400   # shakedown

FLAGS

    --task {reg,clf,both}         default both
    --cols {base,full,all,both}   default both. base -> lean_cook._SCREEN_XGB_* (add-one arms, ~14-40 cols); full -> _FULL_XGB_* (drop-one arms, ~80-105 cols). One config cannot be right for both.
    --ceiling N                   default 1500. STARTING tree ceiling for every stage; each config grows its own from here.
    --max-ceiling N               default 6000. Hard stop for that growth.
    --automatic-expansion         off. Walk one rung past a ladder edge and re-score, while the steps keep paying.
    --encodings                   tune against the pool including the bake-off encodings
    --sites N                     first N sites only (shakedown -- changes the family structure, so never pick values on it)
    --seeds a,b,c                 default 42; >1 ranks on the seed mean
    --true_lofo                   true leave-one-family-out folds instead of GroupKFold
    --max_holdout_pct F           default 0.2; with --true_lofo, a family above this share of rows never holds out

STAGES, ordered by axis coupling rather than importance (see tune.py):

    1  depth x lr      largest effects, and everything downstream needs a depth to sit at
    2  depth x lam     re-crosses depth: relative units are only APPROXIMATELY depth-invariant, and (deep, large-lambda) is the corner one-then-the-other misses
    3  mcw             weakly coupled to lam -> coordinate is sound
    4  subsample
    5  colsample
    6  confirmation    nothing searched: resolves best_k for a combination the coordinate stages may never have scored together

THE CEILING GROWS PER CONFIG, in tune._score_growing -- a bound config is refit alone while the rest of the grid keeps its scores. Growth stops at --max-ceiling or, sooner, when a raise stops buying more than tune.NOISE_CEILING per doubling of trees. That cost rule is the only thing that terminates an unregularized config: with lam and mcw at the bottom of their ladders, lr is the only shrinkage left and best_k is bounded solely by whatever ceiling it is handed. Ranking is cost-aware for the same reason, on `{headline}_adj`.

One pool is built and shared across every stage; pooling is the expensive part and doing it six times would dominate the run.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root/

import lean_cook
import run_exp
import tune as T
from src.data.access import get_site_ids

_STAGES = [["depth", "lam"], ["mcw"], ["subsample"], ["colsample"]]  # stage 1 is separate: it sets the ceiling


def _winner_axes(df: pd.DataFrame) -> dict:
    """Axis-space values of the top-ranked row, for threading into the next stage's --fix.

    Only axes the row actually carries. A DEFAULT (inherited) winner contributes nothing, which is right: it means the inherited value beat every ladder rung, and leaving it unpinned inherits exactly that.
    """
    best = df.iloc[0]
    return {a: float(best[f"ax_{a}"]) for a in T._AXES if f"ax_{a}" in df.columns and pd.notna(best.get(f"ax_{a}"))}


def _stage(label, task, sites, cols, search, fixed, ceiling, seeds, true_lofo, mhp, pool, append,
           encodings=False, resolved=None, max_ceiling=T._MAX_CEILING, expand=False):
    """Run one stage. Ceiling growth is NOT here -- tune._score_growing does it per config, immediately.

    This used to re-run the WHOLE stage at a larger ceiling whenever the winner came back bound, up to 8 times, each pass costing O(ceiling) per config. Growing per config instead means a bound config is refit alone while every finished row keeps its score, which is not an approximation: prefix scoring makes a config that chose k=400 out of 1500 pick the same 400 out of 6000. The rows in one stage therefore carry different `ceiling` values on purpose.

    EVERY argument to tune() goes by keyword. Passed positionally, this call used to bind `true_lofo` to `loso` and `mhp` (0.2, truthy) to `true_lofo` -- so every fulltune run silently used true-LOFO folds while --true_lofo toggled the LOSO pass and roughly doubled the runtime.
    """
    fixed = {k: v for k, v in fixed.items() if k not in search}  # search wins over fix; strip so tune() never sees both
    print(f"\n{'=' * 78}\n{label}  search={search or ['(none)']}  fix={fixed or '{}'}  "
          f"ceiling={ceiling}->{max_ceiling}\n{'=' * 78}")
    return T.tune(task=task, sites=sites, cols_mask=cols, search=search, fixed=fixed, ladders={}, absolute=False,
                  seeds=seeds, true_lofo=true_lofo, max_holdout_pct=mhp, pool=pool, encodings=encodings,
                  resolved_axes=resolved, ceiling=ceiling, append=append, max_ceiling=max_ceiling, expand=expand)


def full(task, sites, cols, ceiling0, seeds, true_lofo, mhp, n_splits=5, encodings=False,
         max_ceiling=T._MAX_CEILING, expand=False):
    g = run_exp.GEOM[task]
    wide = run_exp.make_wide_recipe(task, g["edges"], g["vel"], g["lam"], encodings)
    pool = lean_cook._pool_wide(wide, sites, g["target"], progress_label=f"{task} wide")

    t0 = time.time()
    # Every axis the PROTOCOL has searched, not just the current stage. An axis whose stage was won outright by the inherited value is left unpinned by _winner_axes, so it never reaches a later stage's `fixed` -- without this, stage 5 reported subsample as untuned even though stage 4 had settled it.
    resolved: set[str] = {"depth", "lr"}
    kw = dict(seeds=seeds, true_lofo=true_lofo, mhp=mhp, pool=pool, encodings=encodings,
              max_ceiling=max_ceiling, expand=expand)
    df = _stage("STAGE 1/6  capacity x learning rate", task, sites, cols, ["depth", "lr"], {},
                ceiling0, append=False, resolved=resolved, **kw)
    fixed = _winner_axes(df)
    print(f"\n  >> stage 1 best_k ranged {int(df.best_k.min())}-{int(df.best_k.max())}")

    # EVERY stage starts at ceiling0 and grows per config. Stages 2-5 used to start from stage 1's largest best_k, on the reasoning that nothing downstream needs to look above it -- but that was written when a bound ceiling could only be fixed by re-running the whole stage, so starting high was the cheap insurance. Now growth is per config and free to the configs that do not need it, and starting high is the expensive mistake: it makes a config that wants 100 trees pay for a scan to stage 1's outlier. On the 2026-07-29 CLF screen grid that meant every stage-2 config scanning to 12,000 because one stage-1 config wanted 11,990.
    for i, search in enumerate(_STAGES, start=2):
        resolved |= set(search)
        df = _stage(f"STAGE {i}/6", task, sites, cols, search, fixed, ceiling0, append=True, resolved=resolved, **kw)
        fixed.update(_winner_axes(df))

    # the settled combination may never have been scored as a unit by the coordinate stages -- resolve its own best_k, since it can want more trees than any single-axis winner did
    df = _stage("STAGE 6/6  confirmation at the settled config", task, sites, cols, [], fixed,
                ceiling0, append=True, resolved=resolved, **kw)

    print(f"\n{'=' * 78}\nFULLTUNE {task.upper()} done in {(time.time() - t0) / 60:.0f} min   settled axes: {fixed}")
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=["reg", "clf", "both"], default="both")
    ap.add_argument(
        "--cols",
        choices=["full", "base", "all", "both"],
        default="both",
        help="which feature-set regime to tune: 'base' -> lean_cook._SCREEN_XGB_* (the add-one arms), "
        "'full' -> _FULL_XGB_* (the drop-one arms), 'both' (default) runs each in turn so one invocation "
        "produces every config the manifest fits at",
    )
    ap.add_argument("--encodings", action="store_true", help="tune against the pool including the bake-off encodings")
    ap.add_argument("--ceiling", type=int, default=1500, help="starting ceiling for every stage; each config grows its own from here")
    ap.add_argument("--max-ceiling", type=int, default=T._MAX_CEILING,
                    help=f"hard stop for per-config ceiling growth (default {T._MAX_CEILING})")
    ap.add_argument("--automatic-expansion", action="store_true",
                    help="walk one rung past a ladder edge and re-score, while the steps keep paying under "
                         f"tune.NOISE_CEILING={T.NOISE_CEILING}. Off by default.")
    ap.add_argument("--sites", type=int, default=None, help="cap to the first N sites (shakedown only)")
    ap.add_argument("--seeds", default="42", help="comma-separated random_state values; >1 ranks on the seed mean")
    ap.add_argument("--true_lofo", action="store_true")
    ap.add_argument("--max_holdout_pct", type=float, default=0.2)
    a = ap.parse_args()

    sites = [str(s) for s in get_site_ids()]
    if a.sites:
        sites = sites[: a.sites]
    seeds = tuple(int(x) for x in a.seeds.split(","))
    masks = ["base", "full"] if a.cols == "both" else [a.cols]
    for t in ["reg", "clf"] if a.task == "both" else [a.task]:
        for cols in masks:
            print(f"\n\n{'#' * 78}\n# {t.upper()} / --cols {cols}  ->  lean_cook."
                  f"_{T._REGIME_OF_MASK.get(cols, 'full').upper()}_XGB_{t.upper()}\n{'#' * 78}")
            full(t, sites, cols, a.ceiling, seeds, a.true_lofo, a.max_holdout_pct, encodings=a.encodings,
                 max_ceiling=a.max_ceiling, expand=a.automatic_expansion)


if __name__ == "__main__":
    main()
