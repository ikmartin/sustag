"""
A collection of transformations applied to features or targets in the data.
"""

import pandas as pd
import numpy as np
from pathlib import Path

from src.data.access import get_data, get_site_ids, get_grid

_THIS_DIR = Path(__file__).resolve().parent
_DEFAULT_DIST_EDGES_M = []
_VEL = 1.0


def _resolve_data(*, site_data=None, site_uid=""):
    if site_data is None:
        if site_uid == "":
            raise ValueError("Either site_data or site_uid must be specified!")

        site_data = get_data(site_uid)
    return site_data


def _bucket_map(site_uid="", site_data=None, edges=_DEFAULT_DIST_EDGES_M):
    """f: node_id -> dist_bucket, from the grid's dist_to_sensor (fixed bins)."""

    grid = _resolve_data(site_data=site_data, site_uid=site_uid).grid
    e = [-np.inf, *edges, np.inf]
    b = pd.cut(grid["dist_to_sensor"], bins=e, labels=list(range(len(edges) + 1)))
    return pd.Series(b.values, index=grid["node_id"], name="bucket")


def bucket_lags(site_uid="", site_data=None, water_velocity=_VEL, edges=_DEFAULT_DIST_EDGES_M, max_lag_days=None):
    """Per-bucket travel-time lag in days (= median cell distance / water speed).

    Returns a Series: bucket -> lag_days, optionally capped at ``max_lag_days``.
    """
    grid = _resolve_data(site_data=site_data, site_uid=site_uid).grid
    b = grid["node_id"].map(_bucket_map(site_uid=site_uid, site_data=site_data, edges=edges))
    med = grid["dist_to_sensor"].groupby(b).median()
    lag = (med / (water_velocity * 86400)).round().astype(int)  # days = metres / (m/s * s/day)
    return lag.clip(upper=max_lag_days) if max_lag_days else lag  # Series: bucket -> lag_days


def lag_buckets(weather_b, lags, cols=None, date_col="date", bucket_col="bucket"):
    """Shift each bucket's weather columns back by its lag (row t <- value from t-lag).

    `lags` is a bucket->days Series (or list, indexed positionally). With no bucket
    column (edges=[]) the whole frame is lagged by the single lag value.
    """
    if cols is None:
        cols = [c for c in weather_b.columns if c not in (date_col, bucket_col)]

    if isinstance(lags, list):
        lags = pd.Series({i: n for i, n in enumerate(lags)})

    # single-bucket frames carry no bucket column (agg_grid_by_bucket drops it when
    # edges=[]); lag the whole frame by the sole lag value.
    if bucket_col not in weather_b.columns:
        sub = weather_b.sort_values(date_col).set_index(date_col).asfreq("D")
        sub[cols] = sub[cols].shift(int(lags.iloc[0]) if len(lags) else 0)
        return sub.reset_index()

    parts = []
    for b, sub in weather_b.groupby(bucket_col, observed=True):
        sub = sub.sort_values(date_col).set_index(date_col).asfreq("D")
        sub[cols] = sub[cols].shift(int(lags.get(b, 0)))  # row t <- value from t-lag
        sub[bucket_col] = b
        parts.append(sub.reset_index())
    return pd.concat(parts, ignore_index=True)


def agg_grid_to_buckets(df, mapping, keys, col_agg):
    """Aggregate per-cell `df` to one row per (`keys`[, bucket]) via `col_agg` {col: how}.

    `mapping` is node_id -> bucket. The frame is indexed by node_id so weighted
    aggregators can pull per-cell weights via ``values.index``; the bucket grouping is
    dropped when the mapping defines a single bucket (edges=[]).
    """
    # index by node_id so each group's value Series carries node_id as its index;
    # that lets a weighted aggregator (_area_mean_curry) pull the matching per-cell
    # weights via values.index.
    x = df.set_index("node_id")
    if mapping.cat.categories.size > 1:
        x["bucket"] = x.index.map(mapping)
        g = x.groupby([*keys, "bucket"], observed=True)

    else:
        g = x.groupby([*keys], observed=True)

    out = {}
    for col, how in col_agg.items():
        out[col] = g[col].agg(how)
    return pd.DataFrame(out).reset_index()


def _agg_dicts(land_use_func, weather_func):
    """Build the (crops, surplus, weather) {column: agg_func} dicts from two funcs."""
    c_dict = {
        # corn columns
        "Alfalfa": land_use_func,
        "Corn": land_use_func,
        "Fallow": land_use_func,
        "Hay_Pasture": land_use_func,
        "Nonag": land_use_func,
        "Other": land_use_func,
        "Small_Grains": land_use_func,
        "Soybeans": land_use_func,
    }
    s_dict = {
        # surplus columns
        "total_kg_N": land_use_func,
        "surplus_kgha": land_use_func,
    }
    w_dict = {
        # weather columns
        "precip_in_1d": weather_func,
        "max_temp": weather_func,
        "min_temp": weather_func,
        "max_rel_humidity": weather_func,
        "min_rel_humidity": weather_func,
        "vpd": weather_func,
        "solar_rad": weather_func,
        "evapotranspiration": weather_func,
        "fuel_moisture_1000h": weather_func,
    }

    return c_dict, s_dict, w_dict


def _area_mean_curry(site_uid="", site_data=None):
    """Aggregator: basin-area-weighted mean over a group's cells (weights via values.index)."""
    grid = _resolve_data(site_data=site_data, site_uid=site_uid).grid
    area_ha = pd.Series((grid.cell_area * grid.frac_cell_in_basin / 1e4).values, index=grid.node_id)

    def _func(values):
        # values is one group's Series indexed by node_id -> weight by those cells
        return float(np.average(values, weights=area_ha.loc[values.index]))

    return _func


def _exp_curry(site_data, lam):
    """Aggregator: distance-decay-weighted sum, weight = frac_in_basin * exp(-dist/lam)."""
    grid = site_data.grid
    # per-cell weight (basin-area fraction * exponential distance decay) indexed by
    # node_id, so it can be subset + aligned to each group's cells via values.index.
    cell_w = pd.Series(
        grid.frac_cell_in_basin.values * np.exp(-grid.dist_to_sensor.values / lam),
        index=grid.node_id,
    )

    def _exp_decay_weighting(values):
        return float(np.dot(values, cell_w.loc[values.index]))

    return _exp_decay_weighting


def _exp_curry_norm(site_data, lam):
    """Aggregator: distance-decay-weighted mean (the normalised `_exp_curry`)."""
    grid = site_data.grid
    cell_w = pd.Series(
        grid.frac_cell_in_basin.values * np.exp(-grid.dist_to_sensor.values / lam),
        index=grid.node_id,
    )

    def _exp_decay_weighting(values):
        return float(np.average(values, weights=cell_w.loc[values.index]))

    return _exp_decay_weighting


def _standard_agg_dicts(site_data):
    """Agg dicts: plain sum for land-use, basin-area-weighted mean for weather."""
    func1 = sum
    func2 = _area_mean_curry(site_data=site_data)

    return _agg_dicts(func1, func2)


def _exp_decay_agg_dicts(site_data, lam, normalize=False):
    """Agg dicts using exp distance-decay weights (`lam`): normalize -> mean, else sum."""
    sumfunc = _exp_curry(site_data=site_data, lam=lam)
    avgfunc = _exp_curry_norm(site_data=site_data, lam=lam)

    if normalize:
        return _agg_dicts(avgfunc, avgfunc)
    else:
        return _agg_dicts(sumfunc, sumfunc)


def flatten_buckets(df, bucket_col="bucket", value_cols=None):
    """(value_cols, bucket) -> (value_cols)
    Pivot a long bucketed frame to wide, folding the bucket into column names.

    The row key is whichever of ``date`` (daily) or ``year`` (annual) the frame
    carries; the ``bucket`` dimension is spread into the columns, so each value
    column ``col`` becomes ``col_b0`` / ``col_b1`` / ... one per bucket. Returns one
    row per key, with that key (date or year) as a column.

    The bucket is cast to int first so the pivot keys on plain integers (a leftover
    categorical bucket would otherwise spawn empty columns for unobserved bins). The
    input frame is not mutated.
    """
    key = "date" if "date" in df.columns else "year"
    if value_cols is None:
        value_cols = [c for c in df.columns if c not in (key, bucket_col)]

    if bucket_col in df:
        df = df.assign(**{bucket_col: df[bucket_col].astype(int)})
        wide = df.pivot(index=key, columns=bucket_col, values=value_cols)
        wide.columns = [f"{col}_b{int(b)}" for col, b in wide.columns]
        return wide.reset_index()

    else:
        return df


def _with_key_column(d):
    """Return `d` with a 'date' or 'year' key as a column, resetting a date/year
    index into one when needed (and promoting a Series to a frame).

    Lets date-*indexed* inputs (the climatology Series/frames) be merged directly,
    without the caller having to reset_index first.
    """
    if isinstance(d, pd.Series):
        d = d.to_frame()
    if "date" in d.columns or "year" in d.columns:
        return d
    idx = d.index
    if isinstance(idx, pd.DatetimeIndex) or idx.name == "date":
        # a date index may be named anything ("date", "datetime", or None ->
        # "index" after reset); normalise whatever it lands on to "date".
        src = idx.name if idx.name is not None else "index"
        d = d.reset_index()
        return d.rename(columns={src: "date"}) if src != "date" else d
    if idx.name == "year":
        return d.reset_index()
    raise ValueError("frame has no 'date'/'year' column and its index is not a date/year index")


def merge_on_date(dfs, spine=None):
    """Merge frames keyed by `date` (daily) or `year` (annual) onto one daily timeline.

    Each frame must carry exactly one row-key — a ``date`` or ``year`` column, or a
    matching index (a DatetimeIndex / index named "date", or an index named "year"),
    which is reset into a column automatically. A Series is promoted to a frame.

    * ``date`` — a daily frame; aligned to the timeline on its date.
    * ``year`` — an annual frame; its one row per year is broadcast (copied) onto
      every date that falls in that year.

    The timeline is the union of every date-keyed frame's dates (unless `spine` is
    given); a year is derived from it, and each frame is left-joined on its own key —
    so an annual frame's one row per year repeats across all of that year's days.

    Parameters
    ----------
    dfs : list of DataFrame | Series
        Frames to merge; each is keyed by ``date`` or ``year`` (column or index) and
        is already unique on that key (one row per date / per year). Feature columns
        must be uniquely named across frames (no overlap besides the keys).
    spine : DatetimeIndex, optional
        If given, the timeline is exactly these dates and every frame is left-joined
        onto them, instead of the union of the date-keyed frames' dates. Use it to
        pin the rows to a chosen set (e.g. a site's nitrate dates) when some inputs
        (cross-site climatologies) span a much wider calendar that would otherwise
        balloon the row set. Dates with no match in a frame become NaN there.

    Returns
    -------
    DataFrame
        One row per date (sorted ascending), with a ``date`` column followed by
        every frame's feature columns.
    """
    date_frames, year_frames = [], []
    for d in dfs:
        d = _with_key_column(d)
        if "date" in d.columns:
            date_frames.append(d)
        else:  # _with_key_column guarantees a 'date' or 'year' key
            year_frames.append(d)

    if spine is not None:
        timeline = pd.DatetimeIndex(spine).rename("date")
    elif date_frames:
        # timeline = union of every daily frame's dates
        timeline = pd.Index(sorted(set().union(*(set(d["date"]) for d in date_frames))), name="date")
    else:
        raise ValueError("need at least one date-keyed frame (or a spine) to define the timeline")
    out = pd.DataFrame({"date": timeline})

    for d in date_frames:
        out = out.merge(d, on="date", how="left")

    if year_frames:
        # broadcast: derive the year of each date, then left-join the annual rows on
        # it so each year's single row lands on all its days. A private key (_yr)
        # avoids colliding with any real "year" feature a date frame might carry.
        out["_yr"] = out["date"].dt.year
        for d in year_frames:
            out = out.merge(d.rename(columns={"year": "_yr"}), on="_yr", how="left")
        out = out.drop(columns="_yr")

    dups = out.columns[out.columns.duplicated()].unique().tolist()
    if dups:
        raise ValueError(f"duplicate feature columns after merge: {dups} — rename before merging")
    return out


# seasonal-position extractors: how to turn a date into the index a seasonal
# series is keyed on (matches nitrate_avg_seasonal's "D"/"W"/"M").
_SEASON_POS = {
    "doy": lambda d: d.dt.dayofyear,
    "week": lambda d: d.dt.isocalendar().week.astype("int64"),
    "month": lambda d: d.dt.month,
}
_SEASON_ALIAS = {"D": "doy", "W": "week", "M": "month"}


def match_seasonal(dates, seasonal, freq=None):
    """Map a seasonal Series (indexed by doy / week / month) onto a column of dates.

    `seasonal` is a lookup keyed on a seasonal position -- day-of-year, ISO
    week-of-year, or month (e.g. from nitrate_avg_seasonal). This computes that
    position from each date and looks it up, returning one value per date.

    Parameters
    ----------
    dates : Series | array-like | DatetimeIndex
        Reference dates (e.g. a DataFrame's "date" column). If a Series, the
        result keeps its index so it can be assigned straight back onto the frame.
    seasonal : Series
        Seasonal lookup indexed by position. Its index name ("doy"/"week"/"month")
        selects the position automatically unless `freq` is given.
    freq : optional
        Override the position: "D"/"doy", "W"/"week", or "M"/"month". Needed only
        if `seasonal.index.name` is missing.

    Returns
    -------
    Series of matched values aligned to `dates` (named like `seasonal`).
    """
    key = _SEASON_ALIAS.get(freq, freq) or seasonal.index.name
    if key not in _SEASON_POS:
        raise ValueError(
            f"could not determine seasonal position (freq={freq!r}, "
            f"seasonal.index.name={seasonal.index.name!r}); pass freq as one of "
            f"D/W/M (or doy/week/month)"
        )
    dt = pd.to_datetime(dates)
    if isinstance(dt, pd.DatetimeIndex):
        dt = pd.Series(dt, index=dt)
    pos = _SEASON_POS[key](dt)
    return pos.map(seasonal).rename(seasonal.name)
