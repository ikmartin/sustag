"""Feature & target builders for the nitrate models.

Each function turns raw site data into a model-ready column (or small frame), keyed by date and/or year so they compose through data.transforms.merge_on_date. Quick reference:

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

Static & neighbour features
  site_static(site)                Time-invariant site descriptors: sensor lat/lon, log basin area, cell-distance spread.
  lagged_sensor_nitrate(uids, k)   Past daily nitrate of the given site(s), shifted k days back (own history or neighbours).
  nitrate_avg_except_this(site, shift)  Cross-site daily-mean nitrate over all OTHER sites, lagged `shift` days (leakage-free neighbour level).
  neighbor_nitrate(site, ...)      Nitrate at the nearest monitored up/downstream neighbour on the reach graph -- needs a resolvable donor, unlike the pooled average above.
  doy_climatology_pure_signal(s)   Leakage-free seasonality: sin/cos Fourier encoding of day-of-year (uses dates only).

The all-sites daily-max nitrate (_state_daily_wide) backs nitrate_avg_except_this and is cached in data/cache.
"""

import pandas as pd
import numpy as np
from functools import lru_cache
from pathlib import Path

import sys

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))  # repo root on path

from src.data.access import get_data, get_water, get_weather, get_site_ids, get_basin_graph, get_basin_area
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
# Several accessors compute from either a site_uid (memoized) or an in-memory site_data (fresh, since a SiteData is unhashable). This one helper replaces the repeated _impl / @lru_cache _uid / dispatch trios: `impl` takes the resolved data, `loader(uid)` loads it for the cached path, `pick(site_data)` extracts the same piece for the fresh path.
@lru_cache(maxsize=None)
def _uid_cache(impl, loader, site_uid, kwitems):
    """Memoize `impl` per (site_uid, kwargs), loading the site via `loader` on a miss. The returned object is SHARED -- callers must treat it as read-only."""
    return impl(loader(site_uid), **dict(kwitems))


def _uid_or_data(impl, loader, pick, site_uid, site_data, **kw):
    """Dispatch a feature builder: use `site_data` when given, else resolve+memoize on `site_uid`. `pick` selects the attribute `impl` needs, so a uid path can load only that slice rather than the whole SiteData."""
    if site_data is not None:
        return impl(pick(site_data), **kw)
    return _uid_cache(impl, loader, site_uid, tuple(sorted(kw.items())))


def _agg_covariate(slot, attr, keys, site_uid, site_data, edges, lam, exp):
    """Aggregate one covariate frame (`d.<attr>`) to (`keys`, bucket). `slot` selects the crops/surplus/weather column dict; `exp` -> distance-decay weights, else the standard (sum / area-mean)."""
    d = _resolve_data(site_data=site_data, site_uid=site_uid)
    dicts = _exp_decay_agg_dicts(site_data=d, lam=lam) if exp else _standard_agg_dicts(site_data=d)
    mapping = _bucket_map(site_data=d, edges=edges)
    return agg_grid_to_buckets(getattr(d, attr), mapping, keys=keys, col_agg=dicts[slot])


def _bucket_mean_intensity(site_uid, site_data, edges):
    """Per-(year, bucket) basin-membership-weighted MEAN of surplus_kgha (kg N/ha): Sum(surplus_kgha * frac) / Sum(frac) over the cells mapped to that bucket. surplus_kgha is already an intensity, so its size-invariant aggregation is a weighted MEAN of it, NOT a sum (a sum scales with basin size). Each cell is weighted by its basin-membership fraction (partial edge cells count partially). Returns [year(, bucket), surplus_kgha]; a single bucket (edges=()) drops the bucket column to match agg_surplus. Empty bucket -> NaN."""
    d = _resolve_data(site_data=site_data, site_uid=site_uid)
    mapping = _bucket_map(site_data=d, edges=edges)  # node_id -> bucket (categorical)
    frac = pd.Series(d.grid.frac_cell_in_basin.values, index=d.grid.node_id)
    s = d.surplus[["node_id", "year", "surplus_kgha"]]
    nid = s["node_id"].to_numpy()
    w = frac.loc[nid].to_numpy()  # basin-membership weight per (cell, year) row
    agg = pd.DataFrame(
        {
            "year": s["year"].to_numpy(),
            "_wsum": s["surplus_kgha"].to_numpy() * w,  # intensity weighted by membership
            "_w": w,
        }
    )
    keys = ["year"]
    if mapping.cat.categories.size > 1:
        agg["bucket"] = mapping.loc[nid].to_numpy()
        keys = ["year", "bucket"]
    g = agg.groupby(keys, observed=True)[["_wsum", "_w"]].sum()
    g["surplus_kgha"] = g["_wsum"] / g["_w"].where(g["_w"] > 0)
    return g.reset_index()[keys + ["surplus_kgha"]]


def agg_crops(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, lam=10_000, exp=True):
    """Annual crop areas aggregated to (year, bucket).

    Parameters
    ----------
    site_uid : str, optional
        Site to resolve; memoized. Ignored when `site_data` is given.
    site_data : SiteData, optional
        Prebuilt site data -- required for a virtual site, which has no uid to resolve.
    edges : sequence, default _DEFAULT_DIST_EDGES_M
        Distance bucket edges in metres; ``edges=[]`` collapses to a single bucket.
    lam : float, default 10_000
        Exp-decay length in metres; used only when `exp`.
    exp : bool
        True weights each cell by frac_in_basin * exp(-dist/lam); False takes a plain sum.

    Returns
    -------
    pd.DataFrame
        One row per (year, bucket), one column per crop class.

    See Also
    --------
    agg_crops_normalized : the same as size-invariant composition shares.
    """
    return _agg_covariate(0, "crops", ["year"], site_uid, site_data, edges, lam, exp)


def agg_crops_normalized(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M):
    """Crop areas as land-cover COMPOSITION shares, aggregated to (year, bucket).

    Each class is divided by the total over all classes in its (year, bucket), so a column reads as "what fraction of this ring is corn" rather than "how many hectares" -- size-invariant, which is what lets one model rank basins of different areas.

    Parameters
    ----------
    site_uid : str, optional
        Site to resolve; memoized. Ignored when `site_data` is given.
    site_data : SiteData, optional
        Prebuilt site data -- required for a virtual site, which has no uid to resolve.
    edges : sequence, default _DEFAULT_DIST_EDGES_M
        Distance bucket edges in metres; ``edges=[]`` collapses to a single bucket.

    Returns
    -------
    pd.DataFrame
        One row per (year, bucket); columns are per-class shares summing to 1 within a row.
    """
    frame = agg_crops(site_uid, site_data, edges, exp=False)  # plain pixel-count sum, no exp weighting
    val_cols = [c for c in frame.columns if c not in ("year", "bucket")]
    total = frame[val_cols].sum(axis=1)
    out = frame.copy()
    out[val_cols] = frame[val_cols].div(total.where(total != 0), axis=0)  # NaN where the bucket is crop-empty
    return out.rename(columns={c: f"pct_{c.lower()}" for c in val_cols})


def agg_surplus(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, lam=10_000, exp=True):
    """Annual N surplus aggregated to (year, bucket).

    Parameters
    ----------
    site_uid : str, optional
        Site to resolve; memoized. Ignored when `site_data` is given.
    site_data : SiteData, optional
        Prebuilt site data -- required for a virtual site, which has no uid to resolve.
    edges : sequence, default _DEFAULT_DIST_EDGES_M
        Distance bucket edges in metres; ``edges=[]`` collapses to a single bucket.
    lam : float, default 10_000
        Exp-decay length in metres; used only when `exp`.
    exp : bool
        True weights each cell by frac_in_basin * exp(-dist/lam); False takes a plain sum.

    Returns
    -------
    pd.DataFrame
        One row per (year, bucket).

    See Also
    --------
    agg_surplus_normalized : the same as a per-hectare intensity.
    """
    return _agg_covariate(1, "surplus", ["year"], site_uid, site_data, edges, lam, exp)


def agg_surplus_normalized(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M):
    """Per-bucket area-weighted MEAN N-surplus intensity in kg N/ha, to (year, bucket).

    Each distance ring's total N mass divided by its area, so the value is an intensity rather than a mass and does not scale with basin size.

    Parameters
    ----------
    site_uid : str, optional
        Site to resolve; memoized. Ignored when `site_data` is given.
    site_data : SiteData, optional
        Prebuilt site data -- required for a virtual site, which has no uid to resolve.
    edges : sequence, default _DEFAULT_DIST_EDGES_M
        Distance bucket edges in metres; ``edges=[]`` collapses to a single bucket.

    Returns
    -------
    pd.DataFrame
        One row per (year, bucket).
    """
    return _bucket_mean_intensity(site_uid, site_data, edges)


# ── long-run composition: the per-year shares reduced OVER YEARS ──────────────
# Crops and surplus are joined per YEAR, so the model only ever sees one year's land cover -- a snapshot carrying that year's weather, rotation phase and price response. What separates two basins over the long run is their CHARACTERISTIC land use and how variable it is, and nothing else in the feature set is a function of the TIME SERIES of the CDL raster. These are static per site (and per COMID), so they stay precomputable for the widget: extra columns on a row already shipped, no new per-day storage.
#
# Aimed squarely at the between-site metrics; being constant within a site they cannot move within_r2 at all. See notes/plan_long_run_composition.md for the case, and exps 32/32c for the measurement.

# The CDL class set agg_crops emits, in the order transformers._agg_dicts declares it. Distinct from _CULTIVATED_CROPS above, which is the tile-drainage denominator (row crops only).
_CROP_CLASSES = ("Alfalfa", "Corn", "Fallow", "Hay_Pasture", "Nonag", "Other", "Small_Grains", "Soybeans")


def _longrun_impl(d, edges, stats):
    """{column: scalar} of the per-year shares reduced over ALL available years, per distance bucket.

    `stats` selects which reductions to emit -- "mean", "sd", "rotation" -- so a caller can take the block that earns its place without paying for the rest. std() over a single year is NaN, which XGBoost consumes natively; that is the honest encoding of "this site has no multi-year record to reduce".
    """
    e = list(edges)
    pct = [f"pct_{c.lower()}" for c in _CROP_CLASSES]
    out = {}
    for frame, vals in (
        (agg_crops_normalized(site_data=d, edges=e), pct),
        (agg_surplus_normalized(site_data=d, edges=e), ["surplus_kgha"]),
    ):
        vals = [c for c in vals if c in frame.columns]
        groups = (
            [(int(b), g) for b, g in frame.groupby("bucket", observed=True)]
            if "bucket" in frame.columns
            else [(None, frame)]
        )
        for b, g in groups:
            sfx = "" if b is None else f"_b{b}"
            g = g.sort_values("year")
            for c in vals:
                if "mean" in stats:
                    out[f"{c}_mean{sfx}"] = float(g[c].mean())
                if "sd" in stats:
                    out[f"{c}_sd{sfx}"] = float(g[c].std())
            if "rotation" in stats and "pct_corn" in vals:
                out[f"rotation_index{sfx}"] = float(g["pct_corn"].diff().abs().mean())
    return out


def longrun_composition(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, stats=("mean",)):
    """Long-run basin composition: per-year crop/surplus shares reduced over ALL years, per bucket.

    Static per site, so the caller broadcasts across the daily spine rather than merging on date. `stats` picks the reductions:

        "mean"      pct_<class>_mean_b{k}, surplus_kgha_mean_b{k}  -- characteristic land use, year-to-year noise averaged out
        "sd"        pct_<class>_sd_b{k}, surplus_kgha_sd_b{k}      -- how variable that composition is
        "rotation"  rotation_index_b{k}                            -- mean |year-over-year change| in the corn share

    `rotation_index` is OFF by default and should stay off: it measured worse on both tasks, and the mechanism is dead independently -- differencing the BASIN MEAN cancels the field-scale rotation it is meant to catch, since half the fields flipping corn->soy while the other half flip back leaves the mean almost unmoved. Use longrun_cell_rotation for a per-cell measure that does not cancel. See notes/plan_long_run_composition.md for the measurements.

    Parameters
    ----------
    site_uid : str, optional
        Memoized; the returned dict is SHARED, so treat it as read-only.
    site_data : SiteData, optional
    edges : sequence, default _DEFAULT_DIST_EDGES_M
    stats : tuple of str, default ('mean',)
        Any of "mean", "sd", "rotation".

    Returns
    -------
    dict
        Scalars, one per (class, stat, bucket).
    """
    return _uid_or_data(
        _longrun_impl, get_data, lambda d: d, site_uid, site_data, edges=tuple(edges), stats=tuple(stats)
    )


def _longrun_cell_rotation_impl(d, edges):
    """{rotation_cell[_b{k}]: scalar} -- rotation measured PER CELL, then averaged, rather than as the year-over-year change of the basin's mean share.

    A basin holds thousands of fields; when half flip corn->soy while the other half flip back, the basin MEAN barely moves even though every field rotated, so differencing that mean cancels most of the signal. Measured across the 123-site cohort the per-cell value runs 1.3x-10x the basin-level one and the ratio varies by site, so the two definitions RE-RANK basins rather than differing by a constant.

    Each cell's corn share is differenced over years, absolute-valued and averaged; cells are combined per bucket weighted by frac_cell_in_basin, so a partial edge cell counts partially. UNTESTED as a model feature -- exp 32 ran before this arm existed.
    """
    crops, grid = d.crops, d.grid
    classes = [c for c in _CROP_CLASSES if c in crops.columns]
    total = crops[classes].sum(axis=1)
    share = pd.DataFrame(
        {
            "node_id": crops["node_id"].to_numpy(),
            "year": crops["year"].to_numpy(),
            "share": np.where(total > 0, crops["Corn"] / total.where(total > 0), np.nan),
        }
    )
    per_cell = share.sort_values("year").groupby("node_id")["share"].apply(lambda s: s.diff().abs().mean())
    weight = pd.Series(grid["frac_cell_in_basin"].to_numpy(), index=grid["node_id"].to_numpy())
    bucket = _bucket_map(site_data=d, edges=list(edges)).reindex(per_cell.index)

    out, n_buckets = {}, len(edges) + 1
    for k in range(n_buckets):
        sel = per_cell.index if n_buckets == 1 else per_cell.index[(bucket == k).to_numpy()]
        v, w = per_cell.reindex(sel), weight.reindex(sel)
        ok = v.notna() & w.notna()
        if not ok.any() or w[ok].sum() == 0:
            continue  # unoccupied bucket -> no column, matching every other bucketed block
        out[f"rotation_cell{'' if n_buckets == 1 else f'_b{k}'}"] = float((v[ok] * w[ok]).sum() / w[ok].sum())
    return out


def longrun_cell_rotation(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M):
    """Rotation intensity measured per CELL and then averaged -- the definition that survives basin aggregation.

    Differencing the BASIN MEAN cancels field-scale rotation (half the fields flipping corn->soy while the other half flip back leaves the mean almost unmoved), so this differences each cell's own series first and averages the magnitudes afterwards.

    Parameters
    ----------
    site_uid : str, optional
        Site to resolve; memoized. Ignored when `site_data` is given.
    site_data : SiteData, optional
        Prebuilt site data -- required for a virtual site, which has no uid to resolve.
    edges : sequence, default _DEFAULT_DIST_EDGES_M
        Distance bucket edges in metres; ``edges=[]`` collapses to a single bucket.

    Returns
    -------
    dict
        ``rotation_cell[_b{k}]`` scalars. Static per site, so the caller broadcasts across the daily spine. The site_uid path is memoized and the dict is SHARED -- read-only.
    """
    return _uid_or_data(_longrun_cell_rotation_impl, get_data, lambda d: d, site_uid, site_data, edges=tuple(edges))


def agg_weather(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, lam=10_000, exp=True):
    """Daily weather aggregated to (date, bucket).

    Parameters
    ----------
    site_uid : str, optional
        Site to resolve; memoized. Ignored when `site_data` is given.
    site_data : SiteData, optional
        Prebuilt site data -- required for a virtual site, which has no uid to resolve.
    edges : sequence, default _DEFAULT_DIST_EDGES_M
        Distance bucket edges in metres; ``edges=[]`` collapses to a single bucket.
    lam : float, default 10_000
        Exp-decay length in metres; used only when `exp`.
    exp : bool
        True weights each cell by frac_in_basin * exp(-dist/lam); False takes a plain sum.
        Without `exp`, cells are combined by basin-area-weighted mean.

    Returns
    -------
    pd.DataFrame
        One row per (date, bucket).

    See Also
    --------
    agg_weather_w_lag : the same, shifted by each bucket's travel time.
    """
    return _agg_covariate(2, "weather", ["date"], site_uid, site_data, edges, lam, exp)


def agg_weather_w_lag(
    site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M, lam=10_000, exp=False, water_velocity=_VEL
):
    """`agg_weather` with each bucket shifted back by its travel-time lag.

    A distant ring's weather reaches the sensor days after it falls, so its columns are shifted to line up with the day the water would arrive.

    Parameters
    ----------
    site_uid : str, optional
        Site to resolve; memoized. Ignored when `site_data` is given.
    site_data : SiteData, optional
        Prebuilt site data -- required for a virtual site, which has no uid to resolve.
    edges : sequence, default _DEFAULT_DIST_EDGES_M
        Distance bucket edges in metres; ``edges=[]`` collapses to a single bucket.
    lam : float, default 10_000
        Exp-decay length in metres; used only when `exp`.
    exp : bool
        True weights each cell by frac_in_basin * exp(-dist/lam); False takes a plain sum.
    water_velocity : float
        Metres per second used to turn distance into travel time; see bucket_lags.

    Returns
    -------
    pd.DataFrame
        One row per (date, bucket), each bucket shifted by its own lag.
    """
    d = _resolve_data(site_data=site_data, site_uid=site_uid)
    wb = agg_weather(site_data=d, edges=edges, lam=lam, exp=exp)
    lags = bucket_lags(site_data=d, water_velocity=water_velocity, edges=edges)
    wb = lag_buckets(wb, lags, date_col="date", bucket_col="bucket")
    return wb


def agg_weather_normalized(site_uid="", site_data=None, wcols=None, edges=_DEFAULT_DIST_EDGES_M):
    """Daily weather aggregated to (date, bucket) as basin-membership-weighted means.

    Parameters
    ----------
    site_uid : str, optional
        Site to resolve; memoized. Ignored when `site_data` is given.
    site_data : SiteData, optional
        Prebuilt site data -- required for a virtual site, which has no uid to resolve.
    wcols : sequence, optional
        Weather columns to aggregate; default all of them.
    edges : sequence, default _DEFAULT_DIST_EDGES_M
        Distance bucket edges in metres; ``edges=[]`` collapses to a single bucket.

    Returns
    -------
    pd.DataFrame
        One row per (date, bucket).
    """
    if wcols is None:
        raise ValueError("You must pass a specified list of weather value columns to wcols!")

    # get the data and the mapping
    d = _resolve_data(site_data=site_data, site_uid=site_uid)
    mapping = _bucket_map(site_data=d, edges=edges)
    frac = pd.Series(d.grid["frac_cell_in_basin"].values, index=d.grid["node_id"])
    area = pd.Series(d.grid["cell_area"].values / 1e4, index=d.grid["node_id"]) * frac  # area of cell in basin in ha
    nid = d.weather["node_id"].to_numpy()

    # ['date', 'node_id', 'global_node_id', 'precip_in_1d', 'precip_gridmet', 'max_temp', 'min_temp', 'max_rel_humidity', 'min_rel_humidity', 'specific_humidity', 'vpd', 'solar_rad', 'wind_speed', 'wind_direction', 'evapotranspiration', 'ref_et_alfalfa', 'burning_index', 'energy_release', 'fuel_moisture_100h', 'fuel_moisture_1000h'] build the intermediate aggregating dataframe

    dct = {col + "_mass": d.weather[col].to_numpy() * frac.loc[nid].to_numpy() for col in wcols}
    dct["date"] = d.weather["date"]
    agg = pd.DataFrame(dct)
    temp_cols = [col + "_mass" for col in wcols]

    keys = ["date"]
    if mapping.cat.categories.size > 1:
        agg["bucket"] = mapping.loc[nid].to_numpy()  # get bucket ids
        keys += ["bucket"]

    g = agg.groupby(keys, observed=True)[temp_cols].mean()
    for col, temp in zip(wcols, temp_cols):
        print(g.area)
        g[col] = g[temp] / g["area"].where(g["area"] > 0)
    return g.reset_index()[keys + wcols]


# ── antecedent-weather integrators (basin-wide rolling sums) ──────────────────


def _basin_daily_weather_impl(w):
    """Mean over all basin cells per date -- the un-bucketed daily weather `w`, date-indexed."""
    daily = w.groupby("date").mean(numeric_only=True)
    daily.index = pd.DatetimeIndex(daily.index)
    return daily.sort_index()


def _basin_daily_weather(site_uid="", site_data=None):
    """Basin-wide daily weather: simple mean over all cells per date, date-indexed.

    One row per day (tz-naive DatetimeIndex), columns = the raw weather variables. The site_uid path is memoized (weather only -> skips the full SiteData build); the returned frame is shared, so treat it as read-only. Pass site_data instead for a virtual site (computed fresh).
    """
    return _uid_or_data(_basin_daily_weather_impl, get_weather, lambda d: d.weather, site_uid, site_data)


def rolling_precip(site_uid="", site_data=None, windows=(14, 30, 60)):
    """Antecedent precipitation index: trailing rolling SUM of basin-mean daily precip.

    Sum, not mean, because what drives transport is total water delivered over the window.

    Parameters
    ----------
    site_uid : str, optional
    site_data : SiteData, optional
    windows : tuple of int, default (14, 30, 60)
        Trailing day counts.

    Returns
    -------
    pd.DataFrame
        Date-indexed, one column per window.
    """
    p = _basin_daily_weather(site_uid=site_uid, site_data=site_data)["precip_in_1d"]
    cols = {f"precip_roll{w}d": p.rolling(f"{w}D", min_periods=1).sum() for w in windows}
    return pd.concat(cols, axis=1)


def rolling_nitrate_avg_except_this(site_to_exclude, windows=(14, 30, 60)):
    """Trailing rolling means of the leave-one-out neighbour average, one column per window.

    Parameters
    ----------
    site_to_exclude : str
        The modelled site, whose own readings are removed -- that exclusion is what keeps this leakage-free.
    windows : tuple of int, default (14, 30, 60)
        Trailing day counts.

    Returns
    -------
    pd.DataFrame
        Date-indexed, one column per window.
    """
    n = nitrate_avg_except_this(site_to_exclude=site_to_exclude)
    cols = {f"roll_n_avg_except_this{w}d": n.rolling(f"{w}D", min_periods=1).mean() for w in windows}
    return pd.concat(cols, axis=1)


def _daily_nitrate_impl(water, agg_meth="max"):
    """Resample raw water rows to one nitrate_con per day via `agg_meth`, tz-naive.

    Takes the water frame alone, not a SiteData: get_data(uid).water == get_water(uid), so the uid path can skip building basin/grid/crops/surplus/weather entirely.
    """
    # water only -> read the water parquet directly (get_data(uid).water == get_water(uid)) instead of
    # building the whole SiteData (basin/grid/crops/surplus/weather). Big speed-up everywhere.
    nitrate = water["nitrate_con"]
    if nitrate.index.tz is not None:
        nitrate = nitrate.tz_localize(None)  # new Series -- never mutate the cached frame
    return nitrate.resample("1D").agg(agg_meth)


def daily_nitrate(site_uid="", site_data=None, agg_meth="max"):
    """Target: nitrate_con resampled to one value per day, tz-naive.

    Parameters
    ----------
    site_uid : str, optional
    site_data : SiteData, optional
    agg_meth : str, default 'max'
        Intraday reduction; max so any intraday peak is what the day reports.

    Returns
    -------
    pd.DataFrame
        date + nitrate_con, one row per observed day.
    """
    return _uid_or_data(_daily_nitrate_impl, get_water, lambda d: d.water, site_uid, site_data, agg_meth=agg_meth)


def nitrate_daily_rolling(site_uid="", site_data=None, window=7, min_obs=1, agg_meth="max"):
    """Regression target: max daily nitrate over the trailing `window` days, today inclusive.

    A smoothed, peak-preserving target -- it tracks whether the recent period contained a high day rather than what today happened to read, which is the quantity an exceedance warning is about.

    Parameters
    ----------
    site_uid : str, optional
    site_data : SiteData, optional
    window : int, default 7
        Trailing days, today inclusive.
    min_obs : int, default 1
        Observed days required before a window gets a value; below it the row is <NA> and dropped before training.
    agg_meth : str, default 'max'
        Intraday reduction, so any intraday peak counts.

    Returns
    -------
    pd.DataFrame
        date + the rolling target.
    """
    daily = daily_nitrate(site_uid=site_uid, site_data=site_data, agg_meth=agg_meth).asfreq(
        "D"
    )  # contiguous daily index
    n_obs = daily.notna().rolling(window, min_periods=1).sum()
    rolled = daily.rolling(window, min_periods=1).max()  # max skips NaN days
    rolled[n_obs < min_obs] = np.nan  # too few observed days -> undecidable
    return rolled.rename("nitrate_daily_rolling")


def nitrate_violations(site_uid="", site_data=None, threshold=10, agg_meth="max"):
    """Binary target: 1 when the day's nitrate is at or above `threshold`.

    Parameters
    ----------
    site_uid : str, optional
    site_data : SiteData, optional
    threshold : float, default 10
        mg/L; 10 is the drinking-water limit.
    agg_meth : str, default 'max'
        Intraday reduction, so any intraday exceedance counts.

    Returns
    -------
    pd.DataFrame
        date + violation. Days with no reading are <NA> and dropped before training, never counted as non-violations.
    """
    daily = daily_nitrate(site_uid=site_uid, site_data=site_data, agg_meth=agg_meth)
    viol = (daily >= threshold).astype("Int8")
    viol[daily.isna()] = pd.NA
    return viol.rename("nitrate_violations")


def nitrate_violations_rolling(site_uid="", site_data=None, window=7, threshold=10, min_obs=1, agg_meth="max"):
    """Binary target: 1 when ANY day in the trailing `window` was a violation.

    NaN handling is ASYMMETRIC because the window is an OR, and this is the contract to respect when changing it: one confirmed violation forces 1 (missing days cannot undo a real exceedance), but a 0 requires `min_obs` observed non-violating days (a window cannot be certified clean from gaps). Everything else stays <NA> and is dropped before training. `(daily >= threshold)` maps NaN to False, so missing days are masked via notna() rather than silently counted clean.

    Parameters
    ----------
    site_uid : str, optional
    site_data : SiteData, optional
    window : int, default 7
        Trailing days, today inclusive.
    threshold : float, default 10
        mg/L.
    min_obs : int, default 1
        Observed clean days needed to certify a 0. Trades retention against label trust: 1 mirrors the instantaneous target, 3-4 of 7 gives stricter negatives and drops sparse windows.
    agg_meth : str, default 'max'

    Returns
    -------
    pd.DataFrame
        date + the rolling binary target.
    """
    daily = daily_nitrate(site_uid=site_uid, site_data=site_data, agg_meth=agg_meth).asfreq(
        "D"
    )  # contiguous daily index
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

    Parameters
    ----------
    site_uids : sequence of str
        Sites to read; one column each.
    shift : int
        Days into the past. shift>0 makes the value on date t come from t-shift, so it is causally safe.
    agg_meth : str, default 'max'
        Intraday reduction.

    Returns
    -------
    pd.DataFrame
        Date-indexed, one column per site, named per site and shift so several lags can be merged without collision.
    """
    cols = {}
    for uid in site_uids:
        s = daily_nitrate(uid, agg_meth=agg_meth).asfreq("D").shift(shift)
        cols[f"{uid}_lag{shift}"] = s
    return pd.concat(cols, axis=1)


def _site_static_impl(d):
    """Time-invariant descriptors from an already-built SiteData `d` -- see site_static for the keys."""
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
# Cultivated cropland (USDA "cultivated layer" sense): the row/annual crops tile drainage is installed on. Excludes perennial Hay_Pasture and non-ag land (Nonag/Other). See recipes/build.
_CULTIVATED_CROPS = ("Corn", "Soybeans", "Small_Grains", "Fallow", "Alfalfa")


def _agtile_fractions(d):
    """basin-integrated tile-drainage fractions, weighted by frac_cell_in_basin. AgTile-US is a plain binary 1=tile-drained raster with NO cropland mask, so the two denominators come from ELSEWHERE:
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
    """Basin-weighted cultivated-cropland pixel count from CDL year _AGTILE_CDL_YEAR -- the ag denominator for tile_frac_ag. 0 if crops absent or that year has no cells (-> NaN fraction)."""
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
    """Time-invariant site descriptors: sensor location, basin size, grid geometry.

    Constant within a site, so they add nothing to a single-site model; across sites they vary and let a pooled model place a basin it has never seen.

    Parameters
    ----------
    site_uid : str, optional
        Memoized; the returned dict is SHARED, so treat it as read-only.
    site_data : SiteData, optional
        Required for a virtual site, and must carry sensor_location, basin_area and grid.

    Returns
    -------
    dict
        lat, lon            -- sensor coordinates (climate / spatial gradients)
        log_basin_area      -- log10 basin area in m^2 (spans orders of magnitude)
        mean_dist_to_sensor -- mean cell distance in metres (size / travel proxy)
        max_dist_to_sensor  -- basin span in metres
    """
    return _uid_or_data(_site_static_impl, get_data, lambda d: d, site_uid, site_data)


# ── cross-site nitrate climatology (cached in data/_cache) ────────────────────


def _state_daily_wide(force=False):
    """Per-site daily-max nitrate_con aligned into one wide frame (columns = site_uids).

    The shared base for the cross-site climatologies: each site's nitrate resampled to a daily max, aligned on the union of dates (missing -> NaN). Naive daily DatetimeIndex. Cached as a frame so the all-sites load happens once.
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
    """Daily cross-site mean nitrate over every site EXCEPT `site_to_exclude`.

    Leaving the modelled site out is what makes this leakage-free, and `shift` > 0 makes date t carry the neighbour average from t-shift, so it is causally safe.

    Parameters
    ----------
    site_to_exclude : str
        The modelled site, removed from the average.
    shift : int, default 0
        Days into the past.
    force : bool, default False
        Rebuild the cached wide nitrate frame.

    Returns
    -------
    pd.Series
        Date-indexed, named ``nbr_nitrate_lag{shift}`` -- deliberately NOT "nitrate_con", so it cannot collide with the target, and distinct per shift so several lags merge together.
    """
    others = _state_daily_wide(force=force).drop(columns=site_to_exclude, errors="ignore")
    avg = others.mean(axis=1)
    if shift:
        avg = avg.asfreq("D").shift(shift)  # regular daily grid so shift is exactly `shift` days
    return avg.rename(f"rest_of_state_nitrate_lag{shift}")


# ── reach-graph neighbour nitrate ─────────────────────────────────────────────
# A named up/down monitored donor, resolved on the basin-containment graph. Distinct from nitrate_avg_except_this, which pools every OTHER site into one statewide number and so needs no donor at all: this block is undefined at a pin with no resolvable neighbour, which is exactly what separates the network_* recipes from the island_* ones.
#
# EVERY column is emitted on every path, NaN-filled where a direction has no monitored neighbour. Omitting them instead leaves the pool ragged, and on a slice where NO site has a neighbour the columns vanish entirely -- a masked sweep then filters every arm down to `base` and reports a 0.0000 delta with no error anywhere.


def _edge_dist_m(graph, child, parent):
    """Metres along the containment edge child -> parent: flow distance, else straight-line, else NaN.

    NaN-SAFE, which an `or` chain is not: _make_aux writes float('nan') on the ~7 of 98 immediate-only edges D8 cannot route, and NaN is TRUTHY in Python, so `flow or euc or nan` short-circuits on the NaN and never reaches the fallback -- leaving the distance missing on exactly the edges the fallback exists for. Same idiom as src/splits/conflict_graph.py.
    """
    e = graph.edges[child, parent]
    for k in ("flow_dist_m", "euc_dist_m"):
        v = e.get(k)
        if v is not None and np.isfinite(v):
            return float(v)
    return np.nan


def _basin_area_or(areas, uid, missing=np.nan):
    """Positive finite basin area (m^2) for `uid`, else `missing`.

    Guards both the log10 ratio and the min/max selection keys: max(nan, x) returns NAN because every NaN comparison is False, so an unguarded missing area survives as a sort key and silently reorders the candidates. Pass -inf / +inf to sort a missing area last under max / min.
    """
    a = areas.get(uid)
    return float(a) if a is not None and np.isfinite(a) and a > 0 else missing


def _edge_dist_or(graph, child, parent, missing=np.inf):
    """_edge_dist_m, with a non-finite result sorted LAST rather than acting as a wildcard -- the same guard _basin_area_or applies, for the same reason: every NaN comparison is False, so an unrouted edge would otherwise win a min()."""
    d = _edge_dist_m(graph, child, parent)
    return d if np.isfinite(d) else missing


def _best_neighbors(site_uid, graph, areas, select="area"):
    """(best_up, best_down, n_up, n_down) on the reach graph, picked by `select`.

    `select="area"` takes the candidate closest in drainage scale; `select="dist"` takes the nearest in metres -- along-network where D8 could route it, straight-line where it could not. Direction assignment and the mutual-containment tie-break stay on area under both rules, because those decide WHICH WAY a neighbour lies, not which of several to take.

    DOWNSTREAM is a graph successor (a basin enclosing this one), UPSTREAM the LARGEST nested sub-basin -- one neighbour measured at +0.465 against +0.470 for an area-weighted aggregate of every upstream site, so a single donor beats an aggregate.

    Deliberately does NOT use the graph's corr / corr_resid / n_overlap_days edge attributes. They are precomputed and tempting as a dilution weight, but each is computed FROM THIS SITE'S OWN nitrate series -- selecting or weighting a neighbour by them would fit the feature on the label. Drainage area is the honest weight.
    """
    if site_uid not in graph:
        return None, None, 0, 0
    own = _basin_area_or(areas, site_uid)
    up = set(graph.predecessors(site_uid))  # basins nested inside this one
    down = set(graph.successors(site_uid))  # enclosing basin(s)
    # MUTUAL CONTAINMENT -- 4 pairs on this graph (8 of 123 sites) land in both sets: two near-coincident gauges each enclosing the other's sensor, so the containment relation carries no direction. Left alone they resolve as best_up == best_down and the same series ships under two column names. Direction goes to drainage area: area <= own is upstream, and every such pair here is EXACTLY equal-area, so the tie deliberately breaks upstream -- an equal-area donor is the undiluted extreme, and upstream is the stronger channel (+0.503 vs +0.361). Making the sets disjoint also makes len(up) + len(down) the true neighbour count.
    for u in up & down:
        (down if _basin_area_or(areas, u, np.inf) <= own else up).discard(u)
    if select == "dist":
        best_up = min(up, key=lambda u: _edge_dist_or(graph, u, site_uid)) if up else None
        best_down = min(down, key=lambda d: _edge_dist_or(graph, site_uid, d)) if down else None
    else:
        best_up = max(up, key=lambda u: _basin_area_or(areas, u, -np.inf)) if up else None
        best_down = min(down, key=lambda d: _basin_area_or(areas, d, np.inf)) if down else None
    return best_up, best_down, len(up), len(down)


def _best_either(site_uid, best_up, best_down, areas):
    """Whichever neighbour is closest to this basin in drainage scale -> (uid, is_up), or (None, None).

    The dilution law (Spearman(area_ratio, rho) = -0.67): a downstream gauge scores lower on average (+0.361 vs +0.503) only because the nearest one is often a much bigger, more diluted river. With no usable area the tie breaks to upstream, the stronger predictor -- unreachable while `areas` covers every graph node, and explicit so it is a decision rather than NaN ordering.
    """
    own = _basin_area_or(areas, site_uid)
    cands = [(u, True) for u in (best_up,) if u] + [(d, False) for d in (best_down,) if d]
    scored = [(c, abs(np.log10(_basin_area_or(areas, c[0]) / own))) for c in cands]
    scored = [(c, s) for c, s in scored if np.isfinite(s)]
    if scored:
        return min(scored, key=lambda t: t[1])[0]
    return next(iter(cands), (None, None))


def _two_hop(best, is_up, graph, areas, exclude, select="area"):
    """One more step in the SAME direction from `best` (A -> M -> B), skipping `exclude`.

    `exclude` carries the origin AND the other direction's neighbour: in a diamond topology the second hop can land on a gauge already emitted as the opposite direction's first hop, shipping one series under two column names.
    """
    if best is None:
        return None
    nxt = [n for n in (graph.predecessors(best) if is_up else graph.successors(best)) if n not in exclude]
    if not nxt:
        return None
    if select == "dist":  # the second hop follows the same rule as the first, or the chain mixes criteria
        return min(nxt, key=lambda n: _edge_dist_or(graph, n, best) if is_up else _edge_dist_or(graph, best, n))
    return max(nxt, key=lambda u: _basin_area_or(areas, u, -np.inf)) if is_up else min(nxt, key=lambda d: _basin_area_or(areas, d, np.inf))


def _except_this_mean(wide, sums, counts, site_uid):
    """Statewide daily mean EXCLUDING `site_uid` -- the anomaly baseline.

    Excluding the target is load-bearing: without it the target's own nitrate re-enters the feature through the baseline, a silent label leak. It does NOT exclude the neighbour, mirroring nitrate_avg_except_this, so an anomaly carries 1/(n-1) of its own signal -- and under encoding='both' the _up and _down anomalies share a baseline containing both, giving them a small shared negative dependence. Harmless, recorded so it is not rediscovered as a bug.

    `sums`/`counts` are the whole-frame row sum and non-null count: subtracting the site's own column reproduces wide.drop(columns=site).mean(axis=1) exactly, NaN-skipping included, at O(1) per site instead of O(n_sites^2 x n_dates) -- ~120M float ops per pooling pass at this cohort size.
    """
    if site_uid not in wide.columns:
        return sums / counts.where(counts > 0)
    own = wide[site_uid]
    n = counts - own.notna()
    return (sums - own.fillna(0.0)) / n.where(n > 0)


@lru_cache(maxsize=None)
def _neighbor_context(immediate=True):
    """(graph, areas, wide, sums, counts, spine) -- the whole-cohort setup every per-site neighbour column needs.

    Memoized on `immediate` alone: the graph and the state-wide nitrate frame are cohort-level, so rebuilding them per site would dominate a pooling pass. The returned objects are SHARED -- treat as read-only.
    """
    graph = get_basin_graph(immediate_only=immediate)
    areas = {s: get_basin_area(s) for s in graph.nodes}
    wide = _state_daily_wide()
    spine = pd.date_range(wide.index.min(), wide.index.max(), freq="D")
    return graph, areas, wide, wide.sum(axis=1), wide.notna().sum(axis=1), spine


def neighbor_nitrate(site_uid, lags=(1, 3), select="area", immediate=True, encoding="both"):
    """Nitrate at the nearest monitored up/downstream neighbour on the reach graph.

    Parameters
    ----------
    site_uid : str
        The modelled site. A uid absent from the graph (a virtual/ungauged pin) yields the full column set, all NaN.
    lags : tuple of int, default (1, 3)
        Days back for the neighbour ANOMALY columns. **0 is legal and emits a same-day column** -- whether that is deployable depends on the product being a nowcast rather than a forecast, which is a recipes.py decision, not one made here.
    select : {'area', 'dist'}, default 'area'
        Which candidate wins in each direction: closest in drainage scale, or nearest in metres.
    immediate : bool, default True
        True walks each child's smallest enclosing parent only; False the full containment graph, where a direction can have several candidates to choose between.
    encoding : {'both', 'flag', 'signed'}, default 'both'
        'both' emits an _up and a _down column set. 'flag' emits ONE neighbour (whichever is closest in drainage scale) plus a `neighbor_up` indicator; 'signed' drops the indicator and signs `dist_to_neighbor` negative upstream instead.

    Returns
    -------
    (pd.DataFrame, dict)
        A date-keyed frame of the time-varying columns, and the static per-site scalars to broadcast.

    See Also
    --------
    nitrate_avg_except_this : the statewide pooled neighbour level, which needs no resolvable donor and is therefore island-legal.
    """
    graph, areas, wide, sums, counts, spine = _neighbor_context(immediate)
    best_up, best_down, n_up, n_down = _best_neighbors(site_uid, graph, areas, select)
    baseline = _except_this_mean(wide, sums, counts, site_uid)
    own = _basin_area_or(areas, site_uid)
    nan_col = pd.Series(np.nan, index=spine)
    others = {u for u in (site_uid, best_up, best_down) if u is not None}

    def anom(uid, k):
        """The neighbour's deviation from the statewide daily mean, shifted `k` days. NaN column if absent."""
        if uid is None or uid not in wide.columns:
            return nan_col
        return (wide[uid] - baseline).asfreq("D").shift(k)

    def level(uid):
        """The neighbour's raw LEVEL at lag 1 -- the only column that can move the between-site channel, since an anomaly is mean-zero per site by construction."""
        return wide[uid].asfreq("D").shift(1) if uid is not None and uid in wide.columns else nan_col

    def ratio(uid):
        """log10 of the neighbour's drainage area over this site's -- the dilution proxy."""
        a = _basin_area_or(areas, uid)
        return float(np.log10(a / own)) if np.isfinite(a) and np.isfinite(own) else np.nan

    def dist(uid, is_up):
        """Metres to `uid`. Edge direction is child -> parent, so an upstream neighbour is the CHILD of this site and a downstream one is its parent."""
        if uid is None:
            return np.nan
        return _edge_dist_m(graph, uid, site_uid) if is_up else _edge_dist_m(graph, site_uid, uid)

    cols, stats = {}, {}
    if encoding == "both":
        for uid, is_up, sfx in ((best_up, True, "_up"), (best_down, False, "_down")):
            two = _two_hop(uid, is_up, graph, areas, others, select)
            for k in lags:
                cols[f"nbr_anom_lag{k}{sfx}"] = anom(uid, k)
            cols[f"nbr_level_lag1{sfx}"] = level(uid)
            cols[f"nbr2_anom_lag1{sfx}"] = anom(two, 1)
            stats[f"distance{sfx}"] = dist(uid, is_up)
            stats[f"nbr_area_ratio{sfx}"] = ratio(uid)
        stats["nbr_n_up"], stats["nbr_n_down"] = float(n_up), float(n_down)
    elif encoding in ("flag", "signed"):
        best, is_up = _best_either(site_uid, best_up, best_down, areas)
        two = _two_hop(best, is_up, graph, areas, others, select)
        for k in lags:
            cols[f"nbr_anom_lag{k}"] = anom(best, k)
        cols["nbr_level_lag1"] = level(best)
        cols["nbr2_anom_lag1"] = anom(two, 1)
        d = dist(best, is_up)
        stats["nbr_n_neighbors"] = float(n_up + n_down)
        stats["nbr_area_ratio"] = ratio(best)
        # 'signed' folds direction into the sign and drops the flag; 'flag' keeps magnitude and flag apart. A tree can recover the flag from the sign with one split at 0; the question is whether that split costs anything.
        stats["dist_to_neighbor"] = (-d if is_up else d) if encoding == "signed" else d
        if encoding == "flag":
            stats["neighbor_up"] = float(is_up) if best is not None else np.nan
    else:
        raise ValueError(f"Expected encoding 'both', 'flag' or 'signed', got {encoding!r}")

    return pd.concat(cols, axis=1).rename_axis("date").reset_index(), stats


def doy_climatology_pure_signal(s):
    """Cyclical (Fourier) calendar encodings of day-of-year.

    sin/cos pairs rather than a raw day number, so 31 Dec and 1 Jan are adjacent instead of maximally distant.

    Parameters
    ----------
    s : pd.Series or pd.DatetimeIndex
        Dates to encode.

    Returns
    -------
    pd.DataFrame
        The harmonic columns, aligned to `s`.
    """
    doy = s.index.dayofyear
    ang = 2 * np.pi * doy / 365.25
    return pd.DataFrame(
        {
            "doy_sin": np.sin(ang),
            "doy_cos": np.cos(ang),
            "doy_sin2": np.sin(2 * ang),  # first harmonic (2x frequency)
            "doy_cos2": np.cos(2 * ang),
        },
        index=s.index,
    )
