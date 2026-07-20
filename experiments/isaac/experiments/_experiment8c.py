"""_experiment8c -- CLASSIFIER version of _experiment8.

A/D feature sets cropped to a fixed date window, but the TARGET is the binary 'violation'
(daily nitrate >= 10 mg/L). Continuous nitrate is never a feature; n_daily is only the spine.
Cross-site evaluation via compare_many (scored by AUC / PR-AUC / Brier).
"""

import sys

sys.path.insert(0, "../")

from data.features import (
    agg_crops,
    agg_surplus,
    agg_weather_w_lag,
    agg_weather,
    daily_nitrate,
    nitrate_violations,
    nitrate_avg_except_this,
    lagged_sensor_nitrate,
    nitrate_rolling,
    nitrate_avg_seasonal,
    nitrate_avg_calendar,
    doy_climatology_pure_signal,
    site_static,
)
from functools import lru_cache
from data.transforms import flatten_buckets, merge_on_date, match_seasonal
from data import get_site_ids

from cook import *


def all_but_this(site):
    s = set(get_site_ids())
    s.discard(site)
    return s


@lru_cache(maxsize=None)
def _covariates_cached(site, edges=()):
    """weather + crops/surplus (exp-decay buckets) + pure calendar. Cached (immutable tuple).
    n_daily (continuous) is returned only for use as the spine, never as a feature."""
    wb = flatten_buckets(agg_weather(site, edges=edges))
    cb = flatten_buckets(agg_crops(site, edges=edges, lam=5_000, exp=True))
    sb = flatten_buckets(agg_surplus(site, edges=edges, lam=5_000, exp=True))
    n_daily = daily_nitrate(site)
    doy = doy_climatology_pure_signal(n_daily)  # doy_sin/doy_cos
    return n_daily, (wb, cb, sb, doy)


def _covariates(site, edges=()):
    n_daily, parts = _covariates_cached(site, edges)
    return n_daily, list(parts)


def _add_static(site, d):
    for k, v in site_static(site).items():
        d[k] = v
    return d


def _violation(site):
    return nitrate_violations(site).rename("violation")  # binary target


# ----- recipes (target = 'violation') -------------------------------
def recipe_A(site, edges=()):
    """Covariates only: weather + land-use + pure calendar. No nitrate-derived features."""
    n_daily, parts = _covariates(site, edges=edges)
    return merge_on_date([_violation(site), *parts], spine=n_daily.index)


def recipe_A_static(site, edges=()):
    return _add_static(site, recipe_A(site, edges))


def recipe_D(site):
    """A + cross-site rolling avg + past nitrate of all other sensors with lag."""
    n_daily, parts = _covariates(site)
    dates = n_daily.index
    clim = [
        nitrate_rolling("3D", center=False).rename("nroll_3"),
        nitrate_rolling("7D", center=False).rename("nroll_7"),
        nitrate_rolling("14D", center=False).rename("nroll_14"),
        nitrate_rolling("30D", center=False).rename("nroll_30"),
    ]
    lagged_avgs = [
        nitrate_avg_except_this(site, shift=1),
        nitrate_avg_except_this(site, shift=2),
        nitrate_avg_except_this(site, shift=3),
        nitrate_avg_except_this(site, shift=7),
        nitrate_avg_except_this(site, shift=14),
    ]
    return merge_on_date([_violation(site), *parts, *clim, *lagged_avgs], spine=dates)


def recipe_D_static(site):
    return _add_static(site, recipe_D(site))


# ---- crop to a date window ---------------------
def _crop_to_date(recipe, max_date="2017-12-31"):
    def crop_recipe(site_uid):
        df = recipe(site_uid)
        return df[df.date <= max_date]

    return crop_recipe


recipes = {
    "A": _crop_to_date(recipe_A),
    "A_static": _crop_to_date(recipe_A_static),
    "D": _crop_to_date(recipe_D),
    "D_static": _crop_to_date(recipe_D_static),
}


def main(sites=None, extra=False):
    if sites is None:
        sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500]
    print(f"{len(recipes)} recipes x {len(sites)} sites\n")

    from pathlib import Path

    outname = f"test_results/{Path(__file__).stem}.csv"
    results = compare_many(
        recipes, sites, target_col="violation", task="clf", extra_importance_test=extra, **FAST_XGB
    )
    save_comparison(results, outname)


if __name__ == "__main__":
    main()
