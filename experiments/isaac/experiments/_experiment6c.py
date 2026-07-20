"""_experiment6c -- CLASSIFIER version of _experiment6.

Same feature sets (weather-lag sweep + a no-weather recipe), but the TARGET is the binary
'violation' (daily nitrate >= 10 mg/L) instead of continuous nitrate. The continuous nitrate
is NEVER a feature (it would leak the label); n_daily is used only for the spine and the
day-of-year calendar terms. Scored as classification (AUC / PR-AUC / Brier).
"""

import sys

sys.path.insert(0, "../")

from data.features import (
    agg_crops,
    agg_surplus,
    agg_weather,
    agg_weather_w_lag,
    daily_nitrate,
    nitrate_violations,
    lagged_sensor_nitrate,
    nitrate_rolling,
    nitrate_avg_seasonal,
    nitrate_avg_calendar,
    doy_climatology_pure_signal,
)
from data.transforms import flatten_buckets, merge_on_date, match_seasonal
from data import get_site_ids

from cook import *
from isaac.recipes2 import _covariates


def recipe_lagger(lags=[]):
    def recipe(site_uid):
        def lagged(lag):
            wdf = (
                agg_weather(site_uid, edges=[])
                .sort_values("date")
                .set_index("date")
                .asfreq("D")  # regular daily index
                .shift(lag)
            )  # actually lag by `lag` days
            wdf.columns = [f"{c}_lag{lag}" for c in wdf.columns]  # suffix the value columns
            return wdf.reset_index()

        n_daily, parts = _covariates(site_uid)  # n_daily used only for the spine (NOT a feature)
        parts += [lagged(i) for i in lags]
        v = nitrate_violations(site_uid).rename("violation")  # binary target
        out = merge_on_date([v, *parts], spine=n_daily.index)
        return out.dropna(subset=["violation"]).reset_index(drop=True)

    return recipe


def no_weather(site):
    cb = flatten_buckets(agg_crops(site, lam=5_000, exp=True))
    sb = flatten_buckets(agg_surplus(site, lam=5_000, exp=True))
    n_daily = daily_nitrate(site)  # spine + calendar only
    doy = doy_climatology_pure_signal(n_daily)  # doy_sin/doy_cos
    v = nitrate_violations(site).rename("violation")
    return merge_on_date([v, cb, sb, doy], spine=n_daily.index)


lags = [1, 3, 7, 14, 30]
recipes = {f"{len(lags[:i])}_Lags": recipe_lagger(lags[:i]) for i in range(len(lags))}
recipes["no_weather"] = no_weather


def main(sites=None, extra=False):
    if sites is None:
        sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500]
    print(f"{len(recipes)} recipes x {len(sites)} sites\n")

    from pathlib import Path

    outname = f"test_results/{Path(__file__).stem}.csv"
    results = compare_many(recipes, sites, target_col="violation", task="clf", extra_importance_test=extra, **FAST_XGB)
    save_comparison(results, outname)


if __name__ == "__main__":
    main()
