"""_experiment8c -- CLASSIFIER version of _experiment8.

A/D feature sets cropped to a fixed date window, but the TARGET is the binary 'violation'
(daily nitrate >= 10 mg/L). Continuous nitrate is never a feature; n_daily is only the spine.
Cross-site evaluation via compare_many (scored by AUC / PR-AUC / Brier).
"""

import sys

sys.path.insert(0, "../")

from src.features.features import (
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
    daily_nitrate,
    agg_weather,
    agg_crops,
    agg_surplus,
    doy_climatology_pure_signal,
    nitrate_violations_rolling,
    site_static,
)

from src.features.recipes import _rolling_weather, _weather_windows
from src.data.access import get_site_ids

from functools import lru_cache
from src.features.transformers import flatten_buckets, merge_on_date, match_seasonal
from src.data.access import get_site_ids


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


# Preet's classifier recipe: whole-basin distance-decay geometry, breach target
PREET_LAM = 10_000  # Preet's single exp-decay length (~10 km) for crops + surplus
PREET_ROLL_WINDOW = 60  # rolling-rain ladder up to 60d (Preet used 3/7/14/30/60/90; 90 is past our ladder)


def preet_recipe(site, lam: int = PREET_LAM, roll_window: int = PREET_ROLL_WINDOW):
    """Preet's BREACH-CLASSIFIER feature geometry from **testing_stuff.ipynb** (her later, classifier
    notebook -- NOT the EDA_and_modeling regression notebook), rebuilt on the refactored data layer
    for `plot_pr_curves(preet_recipe)` / `run_cv(preet_recipe, task="clf")`. Mirrors testing_stuff's
    choices:

      * ONE whole-basin bucket (edges=()), no riparian/distance bucketing -- crops & surplus are
        exp-decay weighted at a SINGLE scale (lam~10km), matching Preet's w_{crop}=crop*exp(-d/lam);
      * the FULL gridMET weather block (precip, temp, humidity, vpd, solar, ET, fuel_moisture),
        un-lagged, plus the rolling ladder out to `roll_window` days on EVERY weather variable;
      * lat / lon and the static site descriptors (testing_stuff added site_lat / site_lon);
      * calendar (day-of-year) signal;
      * NO cross-site nitrate neighbour features (recipe_CLF's rest-of-state lags) -- Preet didn't use them;
      * target = the plain daily EPA breach (`violation`, nitrate_max > 10, window=1).

    Faithful to the *approach* in testing_stuff.ipynb, not a byte-exact port (its multi-scale
    _short/_med/_long variants, seasonal interactions, leaching_risk, etc. are not reproduced). For
    Preet's EDA_and_modeling *regression* baseline instead, see demo_model.preet_xgb_baseline."""
    kw = {"site_uid": site} if isinstance(site, str) else {"site_data": site}
    n = daily_nitrate(**kw).rename("nitrate_con")
    wb = flatten_buckets(agg_weather(**kw, edges=(), exp=False))  # single basin bucket, un-lagged
    cb = flatten_buckets(agg_crops(**kw, edges=(), lam=lam, exp=True))
    sb = flatten_buckets(agg_surplus(**kw, edges=(), lam=lam, exp=True))
    doy = doy_climatology_pure_signal(n)
    feats = [wb, cb, sb, doy, *_rolling_weather(wb, _weather_windows(roll_window))]
    target = nitrate_violations_rolling(**kw, window=1, min_obs=1).rename("violation")
    frame = merge_on_date([target, *feats], spine=n.index)
    for k, v in site_static(**kw).items():
        frame[k] = v
    return frame
