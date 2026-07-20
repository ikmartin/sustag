import sys

sys.path.insert(0, "../")

from data.features import (
    agg_crops,
    agg_surplus,
    agg_weather_w_lag,
    agg_weather,
    daily_nitrate,
    nitrate_violations,
    lagged_sensor_nitrate,
    nitrate_rolling,
    nitrate_avg_except_this,
    nitrate_avg_seasonal,
    nitrate_avg_calendar,
    doy_climatology_pure_signal,
    site_static,
    rolling_nitrate_avg_except_this,
)
from functools import lru_cache
from data.transforms import flatten_buckets, merge_on_date, match_seasonal
from data import get_site_ids


@lru_cache(maxsize=None)
def _covariates_cached(site, edges=(), new_name_of_target="nitrate_con"):
    """weather + crops/surplus (exp-decay buckets) + pure calendar. Cached; the parts are
    returned as an IMMUTABLE tuple so the cached value can't be mutated in place."""
    wb = flatten_buckets(agg_weather(site, edges=edges))
    cb = flatten_buckets(agg_crops(site, edges=edges, lam=5_000, exp=True))
    sb = flatten_buckets(agg_surplus(site, edges=edges, lam=5_000, exp=True))
    n_daily = daily_nitrate(site).rename(new_name_of_target)
    doy = doy_climatology_pure_signal(n_daily)  # doy_sin/doy_cos
    return n_daily, (wb, cb, sb, doy)


def _covariates(site, edges=(), new_name_of_target="nitrate_con"):
    """Cached covariates, but returns a FRESH parts LIST each call -- so callers may safely
    do `parts += [...]` (or otherwise mutate the list) without corrupting the cache."""
    n_daily, parts = _covariates_cached(site, edges, new_name_of_target)
    return n_daily, list(parts)


def _add_static(site, d):
    for k, v in site_static(site).items():
        d[k] = v
    return d


def _extend_years_forward(annual, last_year):
    """Use this for broadcasting the surplus values forward from 2017 onward."""
    full = range(int(annual["year"].min()), int(last_year) + 1)
    return annual.set_index("year").reindex(full).ffill().reset_index()


# current default general-location regressor
def recipe_REG(site, edge=20_000, vel=0.8, lam=5_000, new_name_of_target="nitrate_con"):
    wb = flatten_buckets(agg_weather_w_lag(site, edges=[edge], exp=False, water_velocity=vel))
    cb = flatten_buckets(agg_crops(site, edges=[edge], lam=lam, exp=True))
    sb = flatten_buckets(agg_surplus(site, edges=[edge], lam=lam, exp=True))
    n = daily_nitrate(site).rename("nitrate_con")
    sb = _extend_years_forward(sb, n.index.year.max())  # 2017 held constant for 2018+
    doy = doy_climatology_pure_signal(n)
    lagged_avgs = [
        nitrate_avg_except_this(site, shift=1),
        nitrate_avg_except_this(site, shift=2),
        nitrate_avg_except_this(site, shift=3),
        nitrate_avg_except_this(site, shift=5),
    ]
    roll_except_this = rolling_nitrate_avg_except_this(site, windows=(7, 14, 30, 60))
    return _add_static(site, merge_on_date([n, wb, cb, sb, doy, *lagged_avgs, roll_except_this], spine=n.index))


# current default general-location classifier
def recipe_CLF(site, edge=20_000, vel=0.8, lam=5_000, thresh=10, new_name_of_target="nitrate_con"):
    wb = flatten_buckets(agg_weather_w_lag(site, edges=[edge], exp=False, water_velocity=vel))
    cb = flatten_buckets(agg_crops(site, edges=[edge], lam=lam, exp=True))
    sb = flatten_buckets(agg_surplus(site, edges=[edge], lam=lam, exp=True))
    n = daily_nitrate(site).rename("nitrate_con")
    sb = _extend_years_forward(sb, n.index.year.max())  # 2017 held constant for 2018+
    doy = doy_climatology_pure_signal(n)
    lagged_avgs = [
        nitrate_avg_except_this(site, shift=1),
        nitrate_avg_except_this(site, shift=2),
        nitrate_avg_except_this(site, shift=3),
        nitrate_avg_except_this(site, shift=5),
    ]
    roll_except_this = rolling_nitrate_avg_except_this(site, windows=(7, 14, 30, 60))
    v = nitrate_violations(site, threshold=thresh).rename("violation")
    return _add_static(site, merge_on_date([v, wb, cb, sb, doy, *lagged_avgs, roll_except_this], spine=n.index))


# ----- recipes ------------------------------
def recipe_A(site, edges=(), new_name_of_target="nitrate_con"):
    """Covariates only: weather + land-use + pure calendar. No nitrate-derived features."""
    n_daily, parts = _covariates(site, edges=edges, new_name_of_target=new_name_of_target)
    return merge_on_date([n_daily, *parts], spine=n_daily.index)


def recipe_A_static(site, edges=(), new_name_of_target="nitrate_con"):
    return _add_static(site, recipe_A(site, edges, new_name_of_target))


def recipe_B(site):
    """A + the site's OWN past nitrate (autoregressive; sensor sees its own history)."""
    n_daily, parts = _covariates(site)
    own = [lagged_sensor_nitrate([site], shift=k) for k in (1, 2, 3, 7, 14, 30)]
    return merge_on_date([n_daily, *parts, *own], spine=n_daily.index)


def recipe_B_static(site):
    return _add_static(site, recipe_B(site))


def recipe_C(site):
    """A + cross-site climatology (causal) + past nitrate of all other sensors."""
    n_daily, parts = _covariates(site)
    dates = n_daily.index
    clim = [
        nitrate_rolling("3D", center=False).rename("nroll_3"),
        nitrate_rolling("7D", center=False).rename("nroll_7"),
        nitrate_rolling("14D", center=False).rename("nroll_14"),
        nitrate_rolling("30D", center=False).rename("nroll_30"),
        nitrate_avg_calendar("D").rename("ncal_d"),
        match_seasonal(dates, nitrate_avg_seasonal("D")).rename("ndoy"),
        match_seasonal(dates, nitrate_avg_seasonal("W")).rename("nwoy"),
        match_seasonal(dates, nitrate_avg_seasonal("M")).rename("nmoy"),
    ]
    neigh = [lagged_sensor_nitrate(get_site_ids(), shift=k) for k in (1, 3, 7)]
    return merge_on_date([n_daily, *parts, *clim, *neigh], spine=dates)


def recipe_C_static(site):
    return _add_static(site, recipe_C(site))


# ── best performers ─────────────────────
# Measured under leakage-aware CV. Headline winners:
# * INDIVIDUAL site, daily regression      -> recipe_B  (own autoregression)
# cook_one R2 ~ 0.79 (vs ~0.23 covariates-only); just edges out persistence.
# * CROSS-SITE regression, unseen basin    -> recipe_A_static  (whole-basin covariates
# + static descriptors).  LOSO R2 ~ 0.45, honest LOFO ~ 0.33.
# (distance buckets and neighbour AR did NOT help cross-site.)
# * CROSS-SITE classification (best result)-> recipe_violation_static
# "will the day exceed the limit?"  LOSO AUC ~ 0.85, LOFO ~ 0.82 -- transfers best.
# Feature-construction tuning (exp4): edges=[] (no buckets), lam=5000, vel ~ 0.3-0.8.


def recipe_violation(site, threshold=10):
    """Best classification base: covariates + a binary 'violation' target (nitrate >=
    threshold). The continuous nitrate is NOT included (no leakage). Run via cook with
    target='violation', task='clf'."""
    n_daily, parts = _covariates(site)
    v = nitrate_violations(site, threshold=threshold).rename("violation")
    return merge_on_date([v, *parts], spine=n_daily.index)


def recipe_violation_static(site, threshold=10):
    """recipe_violation + static site descriptors -- the best cross-site model overall
    (LOSO AUC ~0.85 / LOFO ~0.82). Run via cook with target='violation', task='clf'."""
    return _add_static(site, recipe_violation(site, threshold))
