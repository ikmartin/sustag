"""_experiment18 -- does a finer INNER spatial bucket (a riparian 0-2 km zone) help?
(regression + classification)

exp10 swept the spatial geometry but only as a SINGLE edge (two buckets: inner / outer). Preet's EDA
found the immediate 0-2 km land cover (`w_Corn_short`) to be the single dominant spatial predictor
(15.2% importance), with medium/long-range corn falling out entirely -- the classic riparian-buffer /
tile-drainage-proximity effect. The shipped REG geometry uses edge=50k, so its inner bucket is a very
coarse 0-50 km; the riparian signal is smeared into it.

This tests adding a fine inner boundary. For each task, hold vel/lam at the shipped values and vary
only the bucket edges:

  single      shipped edges = [EDGE]            -> buckets: 0-EDGE, >EDGE
  riparian    edges = [2000, EDGE]              -> buckets: 0-2k, 2k-EDGE, >EDGE
  multiscale  edges = [2000, 10000, EDGE]       -> 0-2k, 2-10k, 10k-EDGE, >EDGE   (reg only)

A lift on lofo_r2 / lofo_auc from `single` -> `riparian` would justify a multi-edge geometry in the
shipped recipe (which currently exposes only one edge per task).

NOTE (refactor): targets the REFACTORED codebase (src.* imports); the older _experiment*.py files are
left on their pre-refactor imports and are NOT updated here.

Run:  python experiments/isaac/experiments/_experiment18.py
"""

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # sustag2/ on path

import pandas as pd

from src.eval.cook import compare_many, save_comparison, FAST_XGB
from src.features.recipes import (
    _site_kwargs,
    _cross_site_nitrate,
    _add_static,
    REG_EDGE,
    REG_VEL,
    REG_LAM,
    CLF_EDGE,
    CLF_VEL,
    CLF_LAM,
)
from src.features.features import (
    agg_crops,
    agg_surplus,
    agg_weather_w_lag,
    doy_climatology_pure_signal,
    daily_nitrate,
    nitrate_daily_rolling,
    nitrate_violations_rolling,
)
from src.features.transformers import flatten_buckets, merge_on_date
from src.data.access import get_site_ids


def make_edges_recipe(task, edges, vel, lam):
    """Faithful rebuild of the shipped recipe_REG / recipe_CLF covariate block (window=1, min_obs=1,
    reg keeps roll_nitrate {7d}, clf omits it) but with the bucket `edges` list exposed."""

    def recipe(site):
        kw = _site_kwargs(site)
        n = daily_nitrate(**kw).rename("nitrate_con")
        wb = flatten_buckets(agg_weather_w_lag(**kw, edges=edges, exp=False, water_velocity=vel))
        cb = flatten_buckets(agg_crops(**kw, edges=edges, lam=lam, exp=True))
        sb = flatten_buckets(agg_surplus(**kw, edges=edges, lam=lam, exp=True))
        doy = doy_climatology_pure_signal(pd.Series(index=n.index, dtype="float64"))
        lagged_avgs, roll_n_all = _cross_site_nitrate(site)
        feats = [wb, cb, sb, doy, *lagged_avgs]
        if task == "reg":
            feats.append(roll_n_all[["roll_n_avg_except_this7d"]])
            target = nitrate_daily_rolling(**kw, window=1, min_obs=1).rename("nitrate_con")
        else:
            target = nitrate_violations_rolling(**kw, window=1, min_obs=1).rename("violation")
        return _add_static(site, merge_on_date([target, *feats], spine=n.index))

    return recipe


def _recipes(task):
    if task == "reg":
        e, vel, lam = REG_EDGE, REG_VEL, REG_LAM
        specs = {"single": [e], "riparian": [2000, e], "multiscale": [2000, 10000, e]}
    else:
        e, vel, lam = CLF_EDGE, CLF_VEL, CLF_LAM
        specs = {"single": [e], "riparian": [2000, e]}
    return {name: make_edges_recipe(task, edges, vel, lam) for name, edges in specs.items()}


def _run(sites, task, extra):
    target_col, suffix, kw = (
        ("nitrate_con", "", {}) if task == "reg"
        else ("violation", "c", dict(target_col="violation", task="clf"))
    )
    recipes = _recipes(task)
    print(f"\n[{task}] {len(recipes)} recipes x {len(sites)} sites")
    results = compare_many(recipes, sites, extra_importance_test=extra, **kw, **FAST_XGB)
    print(results.round(4).to_string())
    Path("test_results").mkdir(exist_ok=True)
    save_comparison(results, f"test_results/{Path(__file__).stem}{suffix}.csv")


def main(sites=None, extra=False):
    if sites is None:
        sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500]
    _run(sites, "reg", extra)
    _run(sites, "clf", extra)


if __name__ == "__main__":
    main()
