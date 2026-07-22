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

Antecedent-weather integrators (basin-wide rolling sums; capture catchment wetness)
  rolling_precip(site, windows)    Antecedent precipitation index: trailing rolling sum of basin-mean daily precip.
  water_balance(site, windows)     Trailing rolling sum of basin-mean (precip - PET) in mm: a PDSI-lite wetness/drought integrator.

Static & neighbour features
  site_static(site)                Time-invariant site descriptors: sensor lat/lon, log basin area, cell-distance spread.
  lagged_sensor_nitrate(uids, k)   Past daily nitrate of the given site(s), shifted k days back (own history or neighbours).
  nitrate_avg_except_this(site, shift)  Cross-site daily-mean nitrate over all OTHER sites, lagged `shift` days (leakage-free neighbour level).
  doy_climatology_pure_signal(s)   Leakage-free seasonality: sin/cos Fourier encoding of day-of-year (uses dates only).

The all-sites daily-mean nitrate (_state_daily_wide) backs nitrate_avg_except_this and is cached in data/cache.
"""

import pandas as pd
import numpy as np
from functools import lru_cache
from pathlib import Path

from src.data.access import get_data, get_water, get_weather, get_site_ids
from src.features.transformers import (
    _DEFAULT_DIST_EDGES_M,
    _VEL,
    _resolve_data,
    _bucket_map,
    _standard_agg_dicts,
    _exp_decay_agg_dicts,
    agg_grid_to_buckets,
    bucket_lags,
    lag_buckets,
)

# Cross-site nitrate climatology cache -> src/data/cache (regenerable, gitignored).
_CACHE = Path(__file__).resolve().parents[1] / "data" / "cache"


# ── uid-cached accessor dispatch ──────────────────────────────────────────────
# Several accessors compute from either a site_uid (memoized) or an in-memory site_data (fresh, since
# a SiteData is unhashable). This one helper replaces the repeated _impl / @lru_cache _uid / dispatch
# trios: `impl` takes the resolved data, `loader(uid)` loads it for the cached path, `pick(site_data)`
# extracts the same piece for the fresh path.
@lru_cache(maxsize=None)
def _uid_cache(impl, loader, site_uid, kwitems):
    return impl(loader(site_uid), **dict(kwitems))


def _uid_or_data(impl, loader, pick, site_uid, site_data, **kw):
    if site_data is not None:
        return impl(pick(site_data), **kw)
    return _uid_cache(impl, loader, site_uid, tuple(sorted(kw.items())))


def _agg_covariate(slot, attr, keys, site_uid, site_data, edges, lam, normalize, exp):
    """Aggregate one covariate frame (`d.<attr>`) to (`keys`, bucket). `slot` selects the crops/
    surplus/weather column dict; `exp` -> distance-decay weights, else the standard (sum / area-mean)."""
    d = _resolve_data(site_data=site_data, site_uid=site_uid)
    dicts = _exp_decay_agg_dicts(site_data=d, lam=lam, normalize=normalize) if exp else _standard_agg_dicts(site_data=d)
    mapping = _bucket_map(site_data=d, edges=edges)
    return agg_grid_to_buckets(getattr(d, attr), mapping, keys=keys, col_agg=dicts[slot])


def agg_crops(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, lam=10_000, normalize=False, exp=False):
    """Annual crop areas aggregated to (year, bucket). `exp` -> distance-decay weights, else sum."""
    return _agg_covariate(0, "crops", ["year"], site_uid, site_data, edges, lam, normalize, exp)


def agg_surplus(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, lam=10_000, normalize=False, exp=False):
    """Annual N surplus aggregated to (year, bucket). `exp` -> distance-decay weights, else sum."""
    return _agg_covariate(1, "surplus", ["year"], site_uid, site_data, edges, lam, normalize, exp)


def agg_weather(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, lam=10_000, normalize=False, exp=False):
    """Daily weather aggregated to (date, bucket) by area-weighted mean (or exp-decay if `exp`)."""
    return _agg_covariate(2, "weather", ["date"], site_uid, site_data, edges, lam, normalize, exp)


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


# ── antecedent-weather integrators (basin-wide rolling sums) ──────────────────


def _basin_daily_weather_impl(w):
    daily = w.groupby("date").mean(numeric_only=True)
    daily.index = pd.DatetimeIndex(daily.index)
    return daily.sort_index()


def _basin_daily_weather(site_uid="", site_data=None):
    """Basin-wide daily weather: simple mean over all cells per date, date-indexed.

    One row per day (tz-naive DatetimeIndex), columns = the raw weather variables. The site_uid path
    is memoized (weather only -> skips the full SiteData build); the returned frame is shared, so
    treat it as read-only. Pass site_data instead for a virtual site (computed fresh).
    """
    return _uid_or_data(_basin_daily_weather_impl, get_weather, lambda d: d.weather, site_uid, site_data)


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


def _daily_nitrate_impl(water, agg_meth="max"):
    # water only -> read the water parquet directly (get_data(uid).water == get_water(uid)) instead of
    # building the whole SiteData (basin/grid/crops/surplus/weather). Big speed-up everywhere.
    nitrate = water["nitrate_con"]
    if nitrate.index.tz is not None:
        nitrate = nitrate.tz_localize(None)  # new Series -- never mutate the cached frame
    return nitrate.resample("1D").agg(agg_meth)


def daily_nitrate(site_uid="", site_data=None, agg_meth="max"):
    """Target: nitrate_con resampled to one value per day (default daily max), tz-naive.

    The site_uid path is memoized per (site_uid, agg_meth); the returned Series is shared, so
    treat it as read-only (copy before mutating). Pass site_data instead for an in-memory site
    (computed fresh). Requires water -- undefined for a waterless virtual site.
    """
    return _uid_or_data(_daily_nitrate_impl, get_water, lambda d: d.water, site_uid, site_data, agg_meth=agg_meth)


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
    out = {
        "lat": float(lat),
        "lon": float(lon),
        "log_basin_area": float(np.log10(d.basin_area)),
        "mean_dist_to_sensor": float(dist.mean()),
        "max_dist_to_sensor": float(dist.max()),
    }
    # COMID-keyed static basin attributes (tot_* modelled, cat_* carried), assembled into SiteData
    # by access.get_data/build_virtual_site_data. NaN dict if no/unknown COMID.
    out.update(getattr(d, "comid_attrs", None) or {})
    # AgTile tile-drainage fractions (raster/areal). NaN if agtile not built or no ag coverage.
    out.update(_agtile_fractions(d))
    return out


_PIXEL_M2 = 900.0  # 30 m AgTile / CDL pixel (both rasters are 30 m)
_AGTILE_CDL_YEAR = 2017  # AgTile-US vintage; the CDL year used for the cropland denominator
# Cultivated cropland (USDA "cultivated layer" sense): the row/annual crops tile drainage is
# installed on. Excludes perennial Hay_Pasture and non-ag land (Nonag/Other). See recipes/build.
_CULTIVATED_CROPS = ("Corn", "Soybeans", "Small_Grains", "Fallow", "Alfalfa")


def _agtile_fractions(d):
    """basin-integrated tile-drainage fractions, weighted by frac_cell_in_basin. AgTile-US is a plain
    binary 1=tile-drained raster with NO cropland mask, so the two denominators come from ELSEWHERE:
      tile_frac_basin = tile-drained area / basin area          (geometric, from basin_area)
      tile_frac_ag    = tile-drained px / cultivated-cropland px (CDL 2017, _CULTIVATED_CROPS)
    tile_frac_ag can slightly exceed 1 where AgTile tiles land CDL didn't call cultivated.
    """
    nan = {"tile_frac_ag": float("nan"), "tile_frac_basin": float("nan")}
    ag = getattr(d, "agtile", None)
    if ag is None or "tile_cells" not in getattr(ag, "columns", []):
        return nan
    w = d.grid.set_index("node_id")["frac_cell_in_basin"]
    weight = ag.set_index("node_id")["tile_cells"]
    tile = float(np.nansum(weight.to_numpy() * w.reindex(weight.index).fillna(0.0).to_numpy()))
    frac_basin = (tile * _PIXEL_M2) / d.basin_area if d.basin_area else float("nan")
    cult = _cultivated_cropland_px(d)
    frac_ag = tile / cult if cult > 0 else float("nan")
    return {"tile_frac_ag": frac_ag, "tile_frac_basin": frac_basin}


def _cultivated_cropland_px(d):
    """Basin-weighted cultivated-cropland pixel count from CDL year _AGTILE_CDL_YEAR -- the ag
    denominator for tile_frac_ag. 0 if crops absent or that year has no cells (-> NaN fraction)."""
    crops = getattr(d, "crops", None)
    if crops is None or "year" not in getattr(crops, "columns", []):
        return 0.0
    yr = crops[crops["year"] == _AGTILE_CDL_YEAR]
    cols = [c for c in _CULTIVATED_CROPS if c in yr.columns]
    if yr.empty or not cols:
        return 0.0
    cult = yr.set_index("node_id")[cols].sum(axis=1)
    w = d.grid.set_index("node_id")["frac_cell_in_basin"]
    return float(np.nansum(cult.to_numpy() * w.reindex(cult.index).fillna(0.0).to_numpy()))


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
    return _uid_or_data(_site_static_impl, get_data, lambda d: d, site_uid, site_data)


# ── cross-site nitrate climatology (cached in data/_cache) ────────────────────


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


def doy_climatology_pure_signal(s):
    """Cyclical (Fourier) calendar encodings of day-of-year.

    Returns sin/cos of the day-of-year angle, which encode where in the annual
    cycle each timestamp falls in a smooth, wrap-around way (Dec 31 ~ Jan 1). Uses
    *only the dates* -- never the nitrate values -- so it is completely leakage-free;
    the model learns the seasonal shape from these two features itself.
    """
    doy = s.index.dayofyear
    return pd.DataFrame(
        {
            "doy_sin": np.sin(2 * np.pi * doy / 365.25),
            "doy_cos": np.cos(2 * np.pi * doy / 365.25),
        },
        index=s.index,
    )
