"""Feature and target builders on the new access layer.

Each function turns one site's published record and basin into a model-ready column or small frame, keyed by date or year so they compose through `transforms.merge_on_date`.

Targets
  daily_nitrate(code)                 the published daily nitrate series
  nitrate_daily_rolling(code, ...)    REG target: trailing-window reduction of it
  nitrate_violations_rolling(...)     CLF target: 1 when the window's value is at or above the threshold

Basin covariate aggregates (per site, by FLOW-distance bucket -- see transforms)
  agg_crops / agg_crops_normalized    annual land cover: areas, and composition shares
  agg_nutrients / agg_nutrients_norm  gTREND N surplus and fertilizer: kilograms, and kg/ha intensity
  agg_weather / agg_weather_w_lag     daily gridMET, plain and travel-time shifted
  rolling_precip                      antecedent precipitation index: trailing sums of basin-mean precip

Static
  site_static(code)                   basin area, tile fraction, snap quality, position, network shape
  longrun_composition(code, ...)      the per-year shares reduced over ALL years -- characteristic land use

Cross-site (the pooled-neighbour block)
  nitrate_avg_except_this(code, ...)  cohort daily mean excluding this site, lagged
  rolling_nitrate_except_this(...)    the same, as trailing rolling means
  doy_climatology(spine)              leakage-free seasonality: sin/cos of day-of-year

DERIVED-DRYNESS NOTE. The old models kept exactly one weather prefix after curation -- `fuel_moisture_1000h`, a fire-weather index encoding ~40 days of moisture memory -- and the new pipeline does not carry gridMET's fire indices. `dryness_40d` here is the honest replacement built from what IS carried: a trailing 40-day integral of (reference ET - precipitation), the water-balance deficit that a 1000-hour fuel moisture is a proxy for. It is NOT the same number and is named so it cannot be mistaken for one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import cohort
import config
import transforms as T

CROP_CLASSES = ("alfalfa", "corn", "fallow", "hay_pasture", "nonag", "other", "small_grains", "soybeans")


# ---- targets ---------------------------------------------------------------------------------------

def daily_nitrate(code: str) -> pd.Series:
    """The published daily nitrate series (mg/L as N), date-indexed, NaN-free."""
    r = cohort.record(code)
    col = "nitrate_mean" if "nitrate_mean" in r.columns else None
    if col is None:
        return pd.Series(dtype="float64")
    return pd.to_numeric(r[col], errors="coerce").dropna().rename("nitrate")


def nitrate_daily_rolling(code: str, window: int = 1, min_obs: int = 1, agg: str = "max") -> pd.Series:
    """REG target: the trailing `window` days of nitrate reduced by `agg`, today inclusive."""
    s = daily_nitrate(code)
    if not len(s) or window <= 1:
        return s.rename("nitrate_con")
    full = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D"))
    out = full.rolling(f"{window}D", min_periods=min_obs).agg(agg)
    return out.dropna().rename("nitrate_con")


def nitrate_violations_rolling(code: str, window: int = 1, threshold: float = None,
                               min_obs: int = 1, agg: str = "max") -> pd.Series:
    """CLF target: 1 when the window's reduced nitrate is at or above `threshold`."""
    thr = config.CLF_THRESHOLD if threshold is None else threshold
    s = nitrate_daily_rolling(code, window=window, min_obs=min_obs, agg=agg)
    return (s >= thr).astype("int8").rename("violation")


# ---- basin covariate aggregates --------------------------------------------------------------------

def _geometry(code: str, edges, decay: float | None):
    wset = cohort.basin(code)
    if not len(wset):
        return None, None, None
    buckets = T.flow_buckets(wset, edges)
    weights = T.bucket_weights(wset, decay_lambda=decay)
    return wset, buckets, weights


def agg_crops(code: str, edges=(), decay: float | None = None) -> pd.DataFrame:
    """Annual crop AREA per (year, bucket): pixel areas summed, membership- and area-weighted."""
    d = cohort.covariates_by_comid(code, "crops")
    wset, buckets, weights = _geometry(code, edges, decay)
    if wset is None or not len(d):
        return pd.DataFrame()
    if "class" not in d.columns and "code" in d.columns:
        from data.access.machinery import remap
        d["class"] = [remap.code_to_class(int(c)) for c in d.code]
    val = "n_px" if "n_px" in d.columns else "frac"
    wide = d.pivot_table(index=["comid", "year"], columns="class", values=val,
                         aggfunc="sum", observed=True).reset_index()
    wide.columns = [str(c).lower() for c in wide.columns]
    vals = [c for c in wide.columns if c not in ("comid", "year")]
    return T.agg_to_buckets(wide, buckets, weights, keys=["year"], value_cols=vals, how="sum")


def agg_crops_normalized(code: str, edges=()) -> pd.DataFrame:
    """Crop COMPOSITION shares per (year, bucket) -- size-invariant, which is what lets one model rank basins of different areas."""
    frame = agg_crops(code, edges=edges, decay=None)
    if not len(frame):
        return frame
    vals = [c for c in frame.columns if c not in ("year", "bucket")]
    total = frame[vals].sum(axis=1)
    out = frame.copy()
    out[vals] = frame[vals].div(total.where(total > 0), axis=0)
    return out.rename(columns={c: f"pct_{c}" for c in vals})


def agg_nutrients(code: str, edges=(), decay: float | None = None) -> pd.DataFrame:
    """N inputs per (year, bucket) in KILOGRAMS -- a mass, so it accumulates."""
    d = cohort.covariates_by_comid(code, "nutrients")
    wset, buckets, weights = _geometry(code, edges, decay)
    if wset is None or not len(d):
        return pd.DataFrame()
    vals = [c for c in d.columns if c not in ("comid", "year", "product")]
    if "product" in d.columns:
        d = d.pivot_table(index=["comid", "year"], columns="product", values=vals[0],
                          aggfunc="sum", observed=True).reset_index()
        d.columns = [str(c) for c in d.columns]
        vals = [c for c in d.columns if c not in ("comid", "year")]
    return T.agg_to_buckets(d, buckets, weights, keys=["year"], value_cols=vals, how="sum")


def agg_nutrients_normalized(code: str, edges=()) -> pd.DataFrame:
    """N inputs per (year, bucket) as an INTENSITY (kg/ha): the weighted MEAN, never a sum."""
    frame = agg_nutrients(code, edges=edges, decay=None)
    wset = cohort.basin(code)
    if not len(frame) or not len(wset):
        return pd.DataFrame()
    buckets = T.flow_buckets(wset, edges)
    area_ha = (pd.Series(wset.areasqkm.to_numpy() * 100.0, index=wset.comid.to_numpy())
               .groupby(buckets.reindex(wset.comid.to_numpy()).to_numpy()).sum())
    out = frame.copy()
    vals = [c for c in out.columns if c not in ("year", "bucket")]
    denom = out.bucket.map(area_ha)
    for c in vals:
        out[c] = out[c] / denom.where(denom > 0)
    return out.rename(columns={c: f"{c}_kgha" for c in vals})


def agg_weather(code: str, edges=(), variables=None, window=None) -> pd.DataFrame:
    """Daily basin weather per (date, bucket).

    NOTE ON GRAIN: the access layer's weather reduction collapses a basin to weighted gridMET cells and returns ONE row per day, so per-bucket weather requires one reduction per bucket. Buckets here are therefore built by reducing each bucket's COMID subset separately.
    """
    wset = cohort.basin(code)
    if not len(wset):
        return pd.DataFrame()
    edges = list(edges)
    buckets = T.flow_buckets(wset, edges)
    variables = tuple(variables or config.WEATHER_VARS)
    window = window or (config.collection_start(), str(pd.Timestamp.now().date()))
    frames = []
    for b in sorted(set(buckets.to_numpy().tolist())):
        comids = buckets.index[buckets.to_numpy() == b].tolist()
        if not comids:
            continue
        w = cohort.weather_for_comids(comids, window=window, variables=variables)
        if not len(w):
            continue
        w = w.copy()
        w["bucket"] = int(b)
        frames.append(w)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def agg_weather_w_lag(code: str, edges=(), variables=None, window=None,
                      velocity_mps: float = None) -> pd.DataFrame:
    """`agg_weather` with each bucket shifted forward by its travel-time lag."""
    wb = agg_weather(code, edges=edges, variables=variables, window=window)
    wset = cohort.basin(code)
    if not len(wb) or not len(wset):
        return wb
    buckets = T.flow_buckets(wset, edges)
    lags = T.bucket_lags(wset, buckets, velocity_mps or config.VELOCITY_MPS)
    return T.lag_buckets(wb, lags)


def dryness_index(code: str, window_days: int = 40, window=None) -> pd.Series:
    """Trailing water-balance deficit: sum(reference ET - precipitation) over `window_days`, basin-mean.

    The honest stand-in for the old `fuel_moisture_1000h` -- see the module docstring. Positive means the basin has lost more water than it received over the window.
    """
    w = cohort.weather(code, window=window, variables=("pr", "pet"))
    if not len(w) or "pet" not in w.columns or "pr" not in w.columns:
        return pd.Series(dtype="float64")
    d = w.copy()
    d["date"] = pd.to_datetime(d.date)
    d = d.set_index("date").sort_index()
    deficit = pd.to_numeric(d.pet, errors="coerce") - pd.to_numeric(d.pr, errors="coerce")
    return deficit.rolling(f"{window_days}D", min_periods=1).sum().rename(f"dryness_{window_days}d")


def rolling_precip(code: str, windows=(14, 30, 60), window=None) -> pd.DataFrame:
    """Antecedent precipitation index: trailing rolling sums of basin-mean daily precipitation."""
    w = cohort.weather(code, window=window, variables=("pr",))
    if not len(w) or "pr" not in w.columns:
        return pd.DataFrame()
    d = w.copy()
    d["date"] = pd.to_datetime(d.date)
    d = d.set_index("date").sort_index()
    pr = pd.to_numeric(d.pr, errors="coerce")
    out = pd.DataFrame({f"precip_{k}d": pr.rolling(f"{k}D", min_periods=1).sum() for k in windows})
    return out.reset_index()


# ---- long-run composition --------------------------------------------------------------------------

def longrun_composition(code: str, edges=(), stats=("mean",)) -> dict:
    """Per-year crop/nutrient shares reduced over ALL years, per bucket -- static per site.

    What separates two basins over the long run is their CHARACTERISTIC land use and how variable it is; every per-year column carries one year's weather, rotation phase and price response. `sd` over a single year is NaN, which XGBoost consumes natively -- the honest encoding of "no multi-year record to reduce".
    """
    out = {}
    for frame, vals in ((agg_crops_normalized(code, edges=edges), None),
                        (agg_nutrients_normalized(code, edges=edges), None)):
        if not len(frame):
            continue
        vals = [c for c in frame.columns if c not in ("year", "bucket")]
        groups = ([(int(b), g) for b, g in frame.groupby("bucket", observed=True)]
                  if "bucket" in frame.columns else [(None, frame)])
        for b, g in groups:
            sfx = "" if b is None else f"_f{b}"
            g = g.sort_values("year")
            for c in vals:
                if "mean" in stats:
                    out[f"{c}_mean{sfx}"] = float(g[c].mean())
                if "sd" in stats:
                    out[f"{c}_sd{sfx}"] = float(g[c].std())
    return out


# ---- static descriptors ----------------------------------------------------------------------------

def site_static(code: str) -> dict:
    """Time-invariant descriptors: position, basin size and shape, tile drainage, snap quality."""
    s = cohort.sites().set_index("snr_id")
    row = s.loc[code] if code in s.index else None
    wset = cohort.basin(code)
    out = {}
    if row is not None:
        out["lat"] = float(row.lat) if pd.notna(row.lat) else np.nan
        out["lon"] = float(row.lon) if pd.notna(row.lon) else np.nan
    if len(wset):
        km2 = float(wset.areasqkm.sum())
        out["basin_km2"] = km2
        out["log_basin_km2"] = float(np.log10(km2)) if km2 > 0 else np.nan
        out["n_reaches"] = int(len(wset))
        out["flow_dist_max_m"] = float(wset.flow_dist_m.max())
        out["flow_dist_mean_m"] = float(wset.flow_dist_m.mean())
    tile = cohort.covariates(code, products=("agtile",)).get("agtile")
    if isinstance(tile, dict):
        drained, denom = tile.get("tile_px_area"), tile.get("cultivated_px_area")
        if drained is not None and denom:
            out["tile_frac"] = float(drained) / float(denom) if float(denom) > 0 else np.nan
    return out


# ---- cross-site ------------------------------------------------------------------------------------

def nitrate_avg_except_this(code: str, lags=(1, 3)) -> list:
    """The cohort's daily mean nitrate EXCLUDING this site, lagged -- a leakage-free neighbour level.

    Excluding the site is what makes it leakage-free: the mean must not contain the target it predicts.
    """
    pool = cohort.pooled_nitrate()
    if not len(pool) or code not in pool.columns:
        return []
    others = pool.drop(columns=[code])
    mean = others.mean(axis=1)
    return [mean.shift(k).rename(f"cohort_nitrate_lag{k}") for k in lags]


def rolling_nitrate_except_this(code: str, windows=(7, 14, 60)) -> pd.DataFrame:
    """Trailing rolling means of the cohort-except-this daily nitrate."""
    pool = cohort.pooled_nitrate()
    if not len(pool) or code not in pool.columns:
        return pd.DataFrame()
    mean = pool.drop(columns=[code]).mean(axis=1)
    out = pd.DataFrame({f"roll_cohort_{k}d": mean.rolling(f"{k}D", min_periods=1).mean()
                        for k in windows})
    return out.reset_index().rename(columns={"index": "date"})


def doy_climatology(spine) -> pd.DataFrame:
    """Leakage-free seasonality: sin/cos Fourier encoding of day-of-year. Dates only, no values."""
    idx = pd.DatetimeIndex(spine)
    doy = idx.dayofyear.to_numpy()
    ang = 2 * np.pi * doy / 365.25
    return pd.DataFrame({"date": idx, "doy_sin": np.sin(ang), "doy_cos": np.cos(ang),
                         "doy_sin2": np.sin(2 * ang), "doy_cos2": np.cos(2 * ang)})
