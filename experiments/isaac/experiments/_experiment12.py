"""_experiment12 -- does broadcasting surplus forward past 2017 help the model?"""

import sys

sys.path.insert(0, "../")

from pathlib import Path

from data.features import (
    agg_crops,
    agg_surplus,
    agg_weather_w_lag,
    daily_nitrate,
    doy_climatology_pure_signal,
    site_static,
)
from data.transforms import flatten_buckets, merge_on_date
from data import get_site_ids

from cook import compare_many, save_comparison, FAST_XGB

EDGE = 20_000  # single inner/outer bucket boundary (m)
VEL = 0.8  # water velocity (m/s) -> weather travel-time lag
LAM = 5_000  # crop/surplus exp-decay length (m)


def extend_years_forward(annual, last_year):
    """Broadcast an annual frame's final-year values forward through `last_year`."""
    full = range(int(annual["year"].min()), int(last_year) + 1)
    return annual.set_index("year").reindex(full).ffill().reset_index()


def _add_static(site, d):
    for k, v in site_static(site).items():
        d[k] = v
    return d


def make_recipe(extend):
    """base covariates; if `extend`, surplus is broadcast forward past 2017."""

    def recipe(site):
        n = daily_nitrate(site).rename("nitrate_con")
        wb = flatten_buckets(agg_weather_w_lag(site, edges=[EDGE], exp=False, water_velocity=VEL))
        cb = flatten_buckets(agg_crops(site, edges=[EDGE], lam=LAM, exp=True))
        sb = flatten_buckets(agg_surplus(site, edges=[EDGE], lam=LAM, exp=True))
        if extend:
            sb = extend_years_forward(sb, n.index.year.max())
        doy = doy_climatology_pure_signal(n)
        return _add_static(site, merge_on_date([n, wb, cb, sb, doy], spine=n.index))

    return recipe


def recipe_none_surplus(site):
    n = daily_nitrate(site).rename("nitrate_con")
    wb = flatten_buckets(agg_weather_w_lag(site, edges=[EDGE], exp=False, water_velocity=VEL))
    cb = flatten_buckets(agg_crops(site, edges=[EDGE], lam=LAM, exp=True))
    # sb = flatten_buckets(agg_surplus(site, edges=[EDGE], lam=LAM, exp=True))
    doy = doy_climatology_pure_signal(n)
    return _add_static(site, merge_on_date([n, wb, cb, doy], spine=n.index))


recipes = {
    "none_surplus": recipe_none_surplus,
    "surplus_stop2017": make_recipe(False),
    # "surplus_extended": make_recipe(True),
}


def main(sites=None, extra=False):
    if sites is None:
        sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500]
    print(f"{len(recipes)} recipes x {len(sites)} sites\n")

    results = compare_many(
        recipes, sites, extra_importance_test=extra, **FAST_XGB
    )  # target_col defaults to 'nitrate_con', task='reg'
    print(results.round(3).to_string())

    Path("test_results").mkdir(exist_ok=True)
    # save_comparison(results, f"test_results/{Path(__file__).stem}.csv")


if __name__ == "__main__":
    main()
