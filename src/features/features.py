"""Feature & target builders for the nitrate models.

Each function turns raw site data into a model-ready column (or small frame), keyed by
date and/or year so they compose through data.transforms.merge_on_date. Quick reference:

Targets
  daily_nitrate(site)              Regression target: nitrate concentration resampled to one value per day.
  nitrate_violations(site, thr)    Classification target: 1 if the day's nitrate is at/above a threshold, else 0.

Spatial covariate aggregates (per site, binned by each cell's distance to the sensor)
  agg_crops(site)                  Annual crop-area land use, aggregated to (year, distance-bucket).
  agg_surplus(site)                Annual nitrogen surplus, aggregated to (year, distance-bucket).
  agg_weather(site)                Daily weather (precip/temp/humidity/...), aggregated to (date, distance-bucket).
  agg_weather_w_lag(site)          agg_weather with each bucket shifted back by its water travel-time lag.
  agg_site_to_buckets(site)        Convenience bundle: (crops, surplus, weather) bucketed for one site.

Antecedent-weather integrators (basin-wide rolling sums; capture catchment wetness)
  rolling_precip(site, windows)    Antecedent precipitation index: trailing rolling sum of basin-mean daily precip.
  water_balance(site, windows)     Trailing rolling sum of basin-mean (precip - PET) in mm: a PDSI-lite wetness/drought integrator.

Static & neighbour features
  site_static(site)                Time-invariant site descriptors: sensor lat/lon, log basin area, cell-distance spread.
  lagged_sensor_nitrate(uids, k)   Past daily nitrate of the given site(s), shifted k days back (own history or neighbours).
  nitrate_avg_except_this(site, shift)  Cross-site daily-mean nitrate over all OTHER sites, lagged `shift` days (leakage-free neighbour level).

Cross-site nitrate climatology (cached; all built from the all-sites daily mean)
  nitrate_avg_calendar(freq)       Causal cross-site daily-mean nitrate, lagged one day (yesterday's basin-wide level).
  nitrate_avg_seasonal(freq)       Typical seasonal cycle: cross-site mean nitrate by day-of-year / week / month.
  nitrate_rolling(window)          Smoothed recent cross-site level: trailing rolling mean that excludes today.
  doy_climatology_pure_signal(s)   Leakage-free seasonality: sin/cos Fourier encoding of day-of-year (uses dates only).

Most cross-site series share _state_daily_base() (the all-sites daily-mean nitrate) and are cached in data/_cache.
"""

import pandas as pd
import numpy as np
from functools import lru_cache
from pathlib import Path

from src.data.access import get_data, get_water, get_weather, get_site_ids
from src.features.transformers import (
    _DEFAULT_DIST_EDGES_M,
    _VEL,
    _bucket_map,
    _standard_agg_dicts,
    _exp_decay_agg_dicts,
    agg_grid_to_buckets,
    bucket_lags,
    lag_buckets,
)

# Cross-site nitrate climatology cache -> src/data/cache (regenerable, gitignored).
_CACHE = Path(__file__).resolve().parents[1] / "data" / "cache"


def _resolve_data(*, site_data=None, site_uid=""):
    if site_data is None:
        if site_uid == "":
            raise ValueError("Either site_data or site_uid must be specified!")

        site_data = get_data(site_uid)
    return site_data


def agg_crops(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, lam=10_000, normalize=False, exp=False):
    """Annual crop areas aggregated to (year, bucket). `exp` -> distance-decay weights, else sum."""
    d = _resolve_data(site_data=site_data, site_uid=site_uid)

    if exp == False:
        c_dict, _, _ = _standard_agg_dicts(site_data=d)
    else:
        c_dict, _, _ = _exp_decay_agg_dicts(site_data=d, lam=lam, normalize=normalize)

    mapping = _bucket_map(site_data=d, edges=edges)
    crops_b = agg_grid_to_buckets(d.crops, mapping, keys=["year"], col_agg=c_dict)
    return crops_b


def agg_surplus(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, lam=10_000, normalize=False, exp=False):
    """Annual N surplus aggregated to (year, bucket). `exp` -> distance-decay weights, else sum."""
    d = _resolve_data(site_data=site_data, site_uid=site_uid)
    if exp == False:
        _, s_dict, _ = _standard_agg_dicts(site_data=d)
    else:
        _, s_dict, _ = _exp_decay_agg_dicts(site_data=d, lam=lam, normalize=normalize)

    mapping = _bucket_map(site_data=d, edges=edges)
    surplus_b = agg_grid_to_buckets(d.surplus, mapping, keys=["year"], col_agg=s_dict)
    return surplus_b


def agg_weather(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, lam=10_000, normalize=False, exp=False):
    """Daily weather aggregated to (date, bucket) by area-weighted mean (or exp-decay if `exp`)."""
    d = _resolve_data(site_data=site_data, site_uid=site_uid)

    if exp == False:
        _, _, w_dict = _standard_agg_dicts(site_data=d)
    else:
        _, _, w_dict = _exp_decay_agg_dicts(site_data=d, lam=lam, normalize=normalize)

    mapping = _bucket_map(site_data=d, edges=edges)
    weather_b = agg_grid_to_buckets(d.weather, mapping, keys=["date"], col_agg=w_dict)
    return weather_b


def agg_weather_w_lag(
    site_uid="",
    site_data=None,
    edges=_DEFAULT_DIST_EDGES_M,
    lam=10_000,
    normalize=False,
    exp=False,
    water_velocity=_VEL,
):
    """`agg_weather` with each bucket shifted back by its travel-time lag (see bucket_lags)."""
    d = _resolve_data(site_data=site_data, site_uid=site_uid)
    wb = agg_weather(site_data=d, edges=edges, lam=lam, normalize=normalize, exp=exp)
    lags = bucket_lags(site_data=d, water_velocity=water_velocity, edges=edges)
    wb = lag_buckets(wb, lags, date_col="date", bucket_col="bucket")
    return wb


def agg_site_to_buckets(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, lam=10_000, normalize=False, mixed=True):
    """The default site-to-bucket aggregation. Crops and surplus are aggregated with exponential decay, weather is aggregated without decay."""
    cb = agg_crops(site_uid, site_data=site_data, edges=edges, lam=lam, normalize=normalize, exp=True)
    sb = agg_surplus(site_uid, site_data=site_data, edges=edges, lam=lam, normalize=normalize, exp=True)
    wb = agg_weather(site_uid, site_data=site_data, edges=edges, lam=lam, normalize=normalize, exp=False)
    return cb, sb, wb


# ── antecedent-weather integrators (basin-wide rolling sums) ──────────────────


def _basin_daily_weather_impl(w):
    daily = w.groupby("date").mean(numeric_only=True)
    daily.index = pd.DatetimeIndex(daily.index)
    return daily.sort_index()


@lru_cache(maxsize=256)
def _basin_daily_weather_uid(site_uid):
    return _basin_daily_weather_impl(get_weather(site_uid))  # weather only -> skip the full SiteData build


def _basin_daily_weather(site_uid="", site_data=None):
    """Basin-wide daily weather: simple mean over all cells per date, date-indexed.

    One row per day (tz-naive DatetimeIndex), columns = the raw weather variables. The
    site_uid path is memoized (via _basin_daily_weather_uid); the returned frame is shared,
    so treat it as read-only (copy before mutating). Pass site_data instead for a virtual
    site (computed fresh, not cached, since a SiteData is unhashable).
    """
    if site_data is None:
        return _basin_daily_weather_uid(site_uid)
    return _basin_daily_weather_impl(site_data.weather)


def rolling_precip(site_uid="", site_data=None, windows=(14, 30, 60)):
    """Antecedent precipitation index: trailing rolling SUM of basin-mean daily precip.

    For each window w (days) the value on date t is the total basin-mean precip over the
    `w` days ending at t (inches). Unlike experiment 6's lagged weather (the same daily value
    shifted), this ACCUMULATES rain over a window, capturing how wet the catchment has been
    -- the antecedent-moisture state that drives nitrate flushing.

    Precip is an exogenous covariate (not derived from the target), so including today is not
    leakage. Returns a date-indexed DataFrame, one column per window: 'precip_roll{w}d'.
    Accepts a site_uid or a site_data (the heavy basin-daily-weather step is cached on the
    site_uid path); treat the result as read-only.
    """
    p = _basin_daily_weather(site_uid=site_uid, site_data=site_data)["precip_in_1d"]
    cols = {f"precip_roll{w}d": p.rolling(f"{w}D", min_periods=1).sum() for w in windows}
    return pd.concat(cols, axis=1)


def rolling_nitrate_avg_except_this(site_to_exclude, windows=(14, 30, 60)):
    n = nitrate_avg_except_this(site_to_exclude=site_to_exclude)
    cols = {f"roll_n_avg_except_this{w}d": n.rolling(f"{w}D", min_periods=1).mean() for w in windows}
    return pd.concat(cols, axis=1)


def water_balance(site_uid="", site_data=None, windows=(30, 60)):
    """Antecedent water balance: trailing rolling SUM of basin-mean (precip - PET), in mm.

    Precip (inches) is converted to mm and reference ET (already mm) subtracted, so each day
    is net water input (+) or loss (-); the rolling sum over the `w` days ending at t is the
    running catchment surplus/deficit -- a hand-rolled drought/wetness integrator from the
    columns we already have (a PDSI-lite). Positive = net wetting, negative = net drying.

    Both inputs are exogenous, so today is not leakage. Returns a date-indexed DataFrame, one
    column per window: 'wbal_roll{w}d'. Accepts a site_uid or a site_data (the basin-daily-
    weather step is cached on the site_uid path); treat as read-only.
    """
    daily = _basin_daily_weather(site_uid=site_uid, site_data=site_data)
    bal = daily["precip_in_1d"] * 25.4 - daily["evapotranspiration"]  # inches -> mm, minus PET (mm)
    cols = {f"wbal_roll{w}d": bal.rolling(f"{w}D", min_periods=1).sum() for w in windows}
    return pd.concat(cols, axis=1)


def _daily_nitrate_impl(water, agg_meth):
    nitrate = water["nitrate_con"]
    if nitrate.index.tz is not None:
        nitrate = nitrate.tz_localize(None)  # new Series -- never mutate the cached frame
    return nitrate.resample("1D").agg(agg_meth)


@lru_cache(maxsize=256)
def _daily_nitrate_uid(site_uid, agg_meth="max"):
    # water only -> read the water parquet directly (get_data(uid).water == get_water(uid)) instead
    # of building the whole SiteData (basin/grid/crops/surplus/weather). Big speed-up everywhere.
    return _daily_nitrate_impl(get_water(site_uid), agg_meth)


def daily_nitrate(site_uid="", site_data=None, agg_meth="max"):
    """Target: nitrate_con resampled to one value per day (default daily max), tz-naive.

    The site_uid path is memoized per (site_uid, agg_meth); the returned Series is shared, so
    treat it as read-only (copy before mutating). Pass site_data instead for an in-memory site
    (computed fresh). Requires water -- undefined for a waterless virtual site.
    """
    if site_data is None:
        return _daily_nitrate_uid(site_uid, agg_meth=agg_meth)
    return _daily_nitrate_impl(site_data.water, agg_meth)


def nitrate_daily_rolling(site_uid="", site_data=None, window=7, min_obs=1, agg_meth="max"):
    """Regression target: the max daily nitrate over the trailing `window` days (today
    inclusive). A smoothed/peak-tracking version of daily_nitrate -- it shrugs off single
    missing days and emphasizes the recent worst case.

    NaN handling is just coverage, not the asymmetric OR of nitrate_violations_rolling:
    the rolling max ignores missing days and is a LOWER bound on the true window max (a gap
    could hide a higher day), so a window needs >= min_obs observed days to emit a value;
    fewer than that -> NaN, dropped before training like daily_nitrate itself. min_obs=1
    keeps the most rows but trusts a single day to stand in for the whole window; raise it
    (e.g. 3-4 of 7) for max estimates backed by more coverage.
    """
    daily = daily_nitrate(site_uid=site_uid, site_data=site_data, agg_meth=agg_meth).asfreq("D")  # contiguous daily index
    n_obs = daily.notna().rolling(window, min_periods=1).sum()
    rolled = daily.rolling(window, min_periods=1).max()  # max skips NaN days
    rolled[n_obs < min_obs] = np.nan  # too few observed days -> undecidable
    return rolled.rename("nitrate_daily_rolling")


def nitrate_violations(site_uid="", site_data=None, threshold=10, agg_meth="max"):
    """Binary target: 1 if the day's nitrate is at or above `threshold` (default 10
    mg/L, the drinking-water limit), else 0.

    Built from daily_nitrate (default daily max, so any exceedance during the day
    counts). Days with no observation stay NA (unknown, not 0) so they can be dropped
    before training just like the regression target.
    """
    daily = daily_nitrate(site_uid=site_uid, site_data=site_data, agg_meth=agg_meth)
    viol = (daily >= threshold).astype("Int8")
    viol[daily.isna()] = pd.NA
    return viol.rename("nitrate_violations")


def nitrate_violations_rolling(site_uid="", site_data=None, window=7, threshold=10, min_obs=1, agg_meth="max"):
    """Binary target: 1 if ANY day in the trailing `window` days (today inclusive) was a
    violation (daily nitrate >= threshold), 0 if the window was OBSERVED-and-clean, <NA>
    when there isn't enough data to decide.

    NaN handling is asymmetric because "any violation in the window" is an OR: one confirmed
    violation day forces 1 (missing days can't undo a real exceedance), but a 0 requires
    >= min_obs observed, non-violating days (a window can't be certified clean from gaps).
    Everything else stays <NA> and is dropped before training, exactly like daily_nitrate
    and the instantaneous nitrate_violations target.

    `min_obs` trades data retention against label trust: 1 mirrors the instantaneous target
    (a single clean day certifies the window); raise it (e.g. 3-4 of 7) for stricter
    negatives at the cost of dropping sparse windows. Build via daily max (agg_meth) so any
    intraday exceedance counts, same as nitrate_violations.

    Note: (daily >= threshold) maps NaN days to False, so missing days are masked via
    notna() and never silently counted as non-violations.
    """
    daily = daily_nitrate(site_uid=site_uid, site_data=site_data, agg_meth=agg_meth).asfreq("D")  # contiguous daily index
    obs = daily.notna()
    viol_day = (daily >= threshold) & obs  # True only on OBSERVED violation days
    n_obs = obs.rolling(window, min_periods=1).sum()
    n_viol = viol_day.rolling(window, min_periods=1).sum()

    out = pd.Series(pd.NA, index=daily.index, dtype="Int8")
    out[n_obs >= min_obs] = 0  # enough clean evidence -> 0
    out[n_viol >= 1] = 1  # a confirmed exceedance always wins -> 1 (set last)
    return out.rename("nitrate_violations_rolling")


def nitrate_anomaly_z(site_uid="", site_data=None, window=21, min_obs=5, agg_meth="max"):
    """Standardized anomaly z(t) = (x(t) - mean) / std, where mean/std are over the TRAILING
    `window` days ending yesterday (today excluded -> no leakage). A site-relative measure of how
    far today's nitrate sits from its own recent baseline, in baseline-std units -- a 'spike' is a
    large positive z. Because it's relative to each site's own mean and scale, it targets the
    within-site dynamics rather than the between-site level the absolute targets struggle with.

    The trailing window skips missing days and needs >= min_obs observed days to define the
    baseline. z(t) is NaN where today is unobserved, the baseline has < min_obs days, or the
    baseline std is 0 (a flat baseline -> no scale to standardize against); those rows drop before
    training like the other targets. Continuous -> the regression spike target; threshold it for
    the classification one (see nitrate_spike)."""
    daily = daily_nitrate(site_uid=site_uid, site_data=site_data, agg_meth=agg_meth).asfreq("D")  # contiguous daily grid
    prev = daily.shift(1)  # baseline ends yesterday -> today never enters its own baseline
    mean = prev.rolling(window, min_periods=min_obs).mean()
    std = prev.rolling(window, min_periods=min_obs).std()
    z = (daily - mean) / std.where(std > 0)  # std == 0 / NaN -> NaN (undefined anomaly)
    return z.rename("nitrate_anomaly_z")


def nitrate_spike(site_uid="", site_data=None, window=21, k=2.0, min_obs=5, agg_meth="max"):
    """Binary spike target: 1 if today's standardized anomaly z(t) >= k, else 0; NaN where z is
    undecidable (see nitrate_anomaly_z). A 'spike' = today's nitrate sits at least k baseline-std
    above the site's own trailing mean -- a site-relative, event-like deviation rather than an
    absolute level. Built from nitrate_anomaly_z so the regression (z) and classification (spike)
    targets stay consistent."""
    z = nitrate_anomaly_z(site_uid=site_uid, site_data=site_data, window=window, min_obs=min_obs, agg_meth=agg_meth)
    spike = (z >= k).astype("Int8")
    spike[z.isna()] = pd.NA  # NaN >= k is False, so restore NaN where the anomaly is undecidable
    return spike.rename("nitrate_spike")


def lagged_sensor_nitrate(site_uids, shift, agg_meth="max"):
    """Daily nitrate of each site in `site_uids`, shifted `shift` days into the past.

    For each uid, daily_nitrate is put on a regular daily index and shifted so the
    value on date t is that sensor's reading from `shift` days earlier (t - shift) --
    only past values are exposed. Returns a date-indexed DataFrame with one column
    per site, named "{uid}_lag{shift}".

    Use it for a sensor's own history (the sensor sees its past values) or for
    neighbouring sensors. Sites are aligned on the union of dates (missing -> NaN).
    """
    cols = {}
    for uid in site_uids:
        s = daily_nitrate(uid, agg_meth=agg_meth).asfreq("D").shift(shift)
        cols[f"{uid}_lag{shift}"] = s
    return pd.concat(cols, axis=1)


def _site_static_impl(d):
    lon, lat = d.sensor_location
    dist = d.grid["dist_to_sensor"]
    return {
        "lat": float(lat),
        "lon": float(lon),
        "log_basin_area": float(np.log10(d.basin_area)),
        "mean_dist_to_sensor": float(dist.mean()),
        "max_dist_to_sensor": float(dist.max()),
    }


@lru_cache(maxsize=None)
def _site_static_uid(site_uid):
    return _site_static_impl(get_data(site_uid=site_uid))


def site_static(site_uid="", site_data=None):
    """Time-invariant site descriptors derived from existing data (sensor location,
    basin size, grid geometry).

    The site_uid path is memoized; the returned dict is shared, so treat it as read-only.
    Pass site_data instead for a virtual site (requires its sensor_location, basin_area and
    grid to be set). Constant within a site -- they add nothing to a single-site model, but
    across sites they vary and let a pooled model place a site (ungauged-basin transfer).
    Returns a dict of scalars:
        lat, lon            -- sensor coordinates (climate / spatial gradients)
        log_basin_area      -- log10 of basin area in m^2 (spans orders of magnitude)
        mean_dist_to_sensor -- mean cell distance (basin size/travel proxy, metres)
        max_dist_to_sensor  -- basin span (metres)
    """
    if site_data is None:
        return _site_static_uid(site_uid)
    return _site_static_impl(site_data)


# ── cross-site nitrate climatology (cached in data/_cache) ────────────────────


def _cache_series(name, compute, force=False):
    """Load a cached Series from data/_cache, computing + storing it if absent."""
    _CACHE.mkdir(parents=True, exist_ok=True)
    path = _CACHE / f"{name}.parquet"
    if path.exists() and not force:
        return pd.read_parquet(path).iloc[:, 0]
    s = compute()
    s.to_frame().to_parquet(path)
    return s


def _state_daily_wide(force=False):
    """Per-site daily-max nitrate_con aligned into one wide frame (columns = site_uids).

    The shared base for the cross-site climatologies: each site's nitrate resampled to a
    daily mean, aligned on the union of dates (missing -> NaN). Naive daily DatetimeIndex.
    Cached as a frame so the all-sites load happens once.
    """
    _CACHE.mkdir(parents=True, exist_ok=True)
    path = _CACHE / "nitrate_state_daily_wide.parquet"
    if path.exists() and not force:
        return pd.read_parquet(path)
    frames = []
    for s in get_site_ids():
        try:
            n = get_water(s)["nitrate_con"]  # water only -> skip building a full SiteData per site
        except (FileNotFoundError, KeyError):
            continue
        if n is None or n.dropna().empty:
            continue
        frames.append(n.resample("1D").max().rename(s))
    wide = pd.concat(frames, axis=1)
    if wide.index.tz is not None:
        wide.index = wide.index.tz_localize(None)
    wide.index.name = "date"
    wide.to_parquet(path)
    return wide


def _state_daily_base(force=False):
    """Cross-site mean nitrate_con per calendar day -- the base for all climatologies.

    The per-site daily series (from `_state_daily_wide`) averaged across sites (skipping
    missing). Naive daily DatetimeIndex. Cached so the all-sites load happens once.
    """
    return _cache_series(
        "nitrate_state_daily",
        lambda: _state_daily_wide(force=force).mean(axis=1).rename("nitrate_con_state_avg"),
        force=force,
    )


def nitrate_avg_except_this(site_to_exclude, shift=0, force=False):
    """Daily cross-site mean nitrate over ALL sites except `site_to_exclude`, optionally
    shifted `shift` days into the past.

    Leave-one-site-out version of `nitrate_state_daily`: the average of every other sensor,
    with the target site's own readings removed -- a leakage-free "what are the neighbours
    doing" feature when modelling `site_to_exclude`. With shift>0 the value on date t is the
    neighbour average from t-shift (only past info), so it is safe to use causally.

    Returns a date-indexed Series named "nbr_nitrate_lag{shift}" -- NOT "nitrate_con", so it
    never collides with the target, and DISTINCT per shift, so several lags can be merged
    together. Pass several shifts for a lagged neighbour set, e.g.
    ``[nitrate_avg_except_this(site, shift=k) for k in (0, 1, 3, 7)]``.
    """
    others = _state_daily_wide(force=force).drop(columns=site_to_exclude, errors="ignore")
    avg = others.mean(axis=1)
    if shift:
        avg = avg.asfreq("D").shift(shift)  # regular daily grid so shift is exactly `shift` days
    return avg.rename(f"rest_of_state_nitrate_lag{shift}")


def nitrate_avg_calendar(freq="D", force=False):
    """Cross-site average nitrate_con on the PREVIOUS day (causal daily nowcast).

    The daily cross-site mean (nitrate_state_daily), lagged one day so the value on
    date t reflects only data through t-1 -- no same-day leakage. Indexed by day.

    Only freq="D" is supported: the weekly/monthly variants were retired (a causal
    calendar-bucket average is either a per-period sawtooth or just duplicates the
    trailing `nitrate_rolling`). For a smoothed recent level use `nitrate_rolling`;
    for the seasonal cycle use `nitrate_avg_seasonal` or the doy Fourier terms.
    """
    if freq != "D":
        raise ValueError(
            f"nitrate_avg_calendar only supports freq='D' (got {freq!r}); the W/M "
            f"variants were retired -- use nitrate_rolling (recent level) or "
            f"nitrate_avg_seasonal (seasonal cycle) instead."
        )

    def compute():
        # asfreq -> regular daily index so shift(1) is exactly one calendar day
        base = _state_daily_base(force=force).asfreq("D")
        return base.shift(1).rename("nitrate_con")

    return _cache_series("nitrate_calendar_D_lag1", compute, force=force)


_SEASON_KEY = {
    "D": ("doy", lambda idx: idx.dayofyear),
    "W": ("week", lambda idx: idx.isocalendar().week.to_numpy()),
    "M": ("month", lambda idx: idx.month),
}


def nitrate_avg_seasonal(freq="D", force=False):
    """Cross-site average nitrate_con by seasonal position (collapsing years).

    freq: "D" day-of-year (1-366), "W" week-of-year (1-53), "M" month (1-12).
    Returns a Series indexed by that position -- the typical seasonal cycle of the
    cross-site average. Cached per freq.
    """
    name, keyfn = _SEASON_KEY[freq]

    def compute():
        base = _state_daily_base(force=force)
        s = base.groupby(keyfn(base.index)).mean().rename("nitrate_con")
        s.index.name = name
        return s

    return _cache_series(f"nitrate_seasonal_{freq}", compute, force=force)


def nitrate_rolling(window="31D", center=True, force=False):
    """Rolling average of the cross-site daily nitrate (nitrate_state_daily), excluding today.

    Smooths the daily cross-site mean over the calendar timeline, giving a
    slow-varying nitrate baseline that keeps seasonal and year-to-year movement but
    removes day-to-day noise. The base is put on a complete daily index first, so an
    offset like "31D" is exactly `window` calendar days. The result is lagged one day
    so the value on date t uses only data through t-1 (the window never includes
    today). Indexed by date -- join on date. Cached per (window, center).
    """

    def compute():
        base = _state_daily_base(force=force).asfreq("D")  # regular daily index
        w = pd.Timedelta(window).days if isinstance(window, str) else window
        roll = base.rolling(w, center=center, min_periods=1).mean()
        return roll.shift(1).rename("nitrate_con")  # exclude today: value at t uses <= t-1

    return _cache_series(f"nitrate_rolling_{window}_c{int(center)}_lag1", compute, force=force)


def doy_climatology_pure_signal(s):
    """Cyclical (Fourier) calendar encodings of day-of-year.

    Returns sin/cos of the day-of-year angle, which encode where in the annual
    cycle each timestamp falls in a smooth, wrap-around way (Dec 31 ~ Jan 1).
    Unlike `doy_climatology`, this uses *only the dates* -- never the nitrate
    values -- so it is completely leakage-free; the model learns the seasonal
    shape from these two features itself.
    """
    doy = s.index.dayofyear
    return pd.DataFrame(
        {
            "doy_sin": np.sin(2 * np.pi * doy / 365.25),
            "doy_cos": np.cos(2 * np.pi * doy / 365.25),
        },
        index=s.index,
    )
