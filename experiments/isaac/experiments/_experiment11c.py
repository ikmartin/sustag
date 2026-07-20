"""_experiment11c, classifier version

Same four recipes (base / +rolling_precip / +water_balance / +both) testing whether explicit antecedent-moisture integrators help and whether they displace fuel_moisture_1000h -- but the TARGET is the binary 'violation' (daily nitrate >= 10 mg/L). Continuous nitrate is never a feature; n_daily is only the spine + calendar. Scored as classification (AUC). Run with --extra=True for the held-out permutation cross-check on fm1000.

Run:  python isaac/experiments/_experiment11c.py
"""

import sys

sys.path.insert(0, "../")

from pathlib import Path

from data.features import (
    agg_crops,
    agg_surplus,
    agg_weather_w_lag,
    daily_nitrate,
    nitrate_violations,
    rolling_precip,
    water_balance,
    doy_climatology_pure_signal,
    site_static,
)
from data.transforms import flatten_buckets, merge_on_date
from data import get_site_ids

from cook import compare_many, save_comparison, FAST_XGB

# Fixed feature config (recipe_REG defaults); only the integrator added varies across recipes.
EDGE = 20_000  # single inner/outer bucket boundary (m)
VEL = 0.8  # water velocity (m/s) -> weather travel-time lag
LAM = 5_000  # crop/surplus exp-decay length (m)


def _add_static(site, d):
    """Broadcast time-invariant site descriptors (lat/lon, basin size, ...) onto every row."""
    for k, v in site_static(site).items():
        d[k] = v
    return d


def make_recipe(use_precip, use_wbal):
    """base covariates (target = 'violation'), optionally + rolling_precip and/or water_balance."""

    def recipe(site):
        n = daily_nitrate(site)  # spine + calendar only (NOT a feature)
        wb = flatten_buckets(agg_weather_w_lag(site, edges=[EDGE], exp=False, water_velocity=VEL))
        cb = flatten_buckets(agg_crops(site, edges=[EDGE], lam=LAM, exp=True))
        sb = flatten_buckets(agg_surplus(site, edges=[EDGE], lam=LAM, exp=True))
        doy = doy_climatology_pure_signal(n)
        v = nitrate_violations(site).rename("violation")
        integ = []
        if use_precip:
            integ.append(rolling_precip(site))
        if use_wbal:
            integ.append(water_balance(site))
        return _add_static(site, merge_on_date([v, wb, cb, sb, doy, *integ], spine=n.index))

    return recipe


recipes = {
    "base": make_recipe(False, False),
    "precip": make_recipe(True, False),
    "wbal": make_recipe(False, True),
    "both": make_recipe(True, True),
}


def main(sites=None, extra=False):
    if sites is None:
        sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500]
    print(f"{len(recipes)} recipes x {len(sites)} sites\n")

    results = compare_many(recipes, sites, target_col="violation", task="clf", extra_importance_test=extra, **FAST_XGB)
    print(results.round(3).to_string())

    Path("test_results").mkdir(exist_ok=True)
    save_comparison(results, f"test_results/{Path(__file__).stem}.csv")


if __name__ == "__main__":
    main()
