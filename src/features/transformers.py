"""
A collection of transformations applied to features or targets in the data.
"""

import pandas as pd
import numpy as np

from src.data.access import get_data

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


def bucket_lags(site_uid="", site_data=None, water_velocity=_VEL, edges=_DEFAULT_DIST_EDGES_M):
    """Per-bucket travel-time lag in days.

    Parameters
    ----------
    site_uid : str, optional
        Site to resolve a grid for; ignored when `site_data` is given.
    site_data : SiteData, optional
        Prebuilt site data, to avoid re-resolving the grid.
    water_velocity : float, default _VEL
        Metres per second used to convert distance to travel time.
    edges : sequence, default _DEFAULT_DIST_EDGES_M
        Distance bucket edges in metres.

    Returns
    -------
    pd.Series
        bucket -> lag_days, the median cell distance in that bucket divided by `water_velocity`.
    """
    grid = _resolve_data(site_data=site_data, site_uid=site_uid).grid
    b = grid["node_id"].map(_bucket_map(site_uid=site_uid, site_data=site_data, edges=edges))
    med = grid["dist_to_sensor"].groupby(b).median()
    return (med / (water_velocity * 86400)).round().astype(int)  # days = metres / (m/s * s/day)


def lag_buckets(weather_b, lags, cols=None, date_col="date", bucket_col="bucket"):
    """Shift each bucket's weather columns back by that bucket's travel-time lag.

    Row t takes the value from t - lag, so a distant bucket's weather reaches the sensor on the day it would actually arrive.

    Parameters
    ----------
    weather_b : pd.DataFrame
        Long bucketed weather, one row per (date, bucket).
    lags : pd.Series or sequence
        bucket -> days (from bucket_lags); a plain sequence is indexed positionally.
    cols : sequence, optional
        Columns to shift; default every non-key column.
    date_col, bucket_col : str
        Key column names. With no bucket column (edges=[]) the whole frame is shifted by the single lag.

    Returns
    -------
    pd.DataFrame
        Same shape, values shifted per bucket.
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


def _weighted_group_agg(x, gkeys, cols, weights, kind):
    """Weighted sum (`kind="wsum"`) or weighted mean (`"wmean"`) of `cols` per `gkeys` group, vectorized.

    VALID ONLY FOR REDUCTIONS LINEAR IN THE VALUES -- Sum(v*w) and Sum(v*w)/Sum(w) -- because it scales each row by its cell weight once and then takes a plain groupby sum in Cython. A median or quantile tagged into this path would return wrong numbers silently, which is why aggregators opt in via `.agg_kind` rather than being routed by name. NaN handling is deliberate and load-bearing: a bare groupby sum SKIPS NaN while the per-group `np.dot`/`np.average` this replaced PROPAGATE it, so groups containing one are masked back to NaN explicitly. See notes/pooling-performance-report.md.
    """
    w = weights.reindex(x.index).to_numpy(dtype=float)
    num = x[cols].mul(w, axis=0)
    keyframe = x[gkeys]

    grouped = pd.concat([num, keyframe], axis=1).groupby(gkeys, observed=True)[cols]
    total = grouped.sum()
    saw_nan = pd.concat([num.isna(), keyframe], axis=1).groupby(gkeys, observed=True)[cols].sum() > 0

    if kind == "wmean":
        denom = pd.concat([pd.Series(w, index=x.index, name="_w"), keyframe], axis=1)
        total = total.div(denom.groupby(gkeys, observed=True)["_w"].sum(), axis=0)
    return total.mask(saw_nan)


def agg_grid_to_buckets(df, mapping, keys, col_agg):
    """Aggregate a per-cell frame to one row per (`keys`[, bucket]).

    Parameters
    ----------
    df : pd.DataFrame
        Per-cell values, indexed by node_id so weighted aggregators can pull per-cell weights via ``values.index``.
    mapping : pd.Series
        node_id -> bucket. The bucket grouping is dropped when the mapping defines a single bucket (edges=[]).
    keys : sequence of str
        Row keys to group by alongside the bucket, e.g. ``["date"]`` or ``["year"]``.
    col_agg : dict
        {column: how}. A `how` carrying ``.agg_kind`` / ``.cell_weights`` (the curried weighted aggregators) is routed through _weighted_group_agg and computed vectorized; anything else -- a builtin like ``sum``, or a caller's own callable -- goes through groupby.agg unchanged.

    Returns
    -------
    pd.DataFrame
        One row per (key[, bucket]).
    """
    # index by node_id so each group's value Series carries node_id as its index;
    # that lets a weighted aggregator (_area_mean_curry) pull the matching per-cell
    # weights via values.index.
    x = df.set_index("node_id")
    gkeys = list(keys)
    if mapping.cat.categories.size > 1:
        x["bucket"] = x.index.map(mapping)
        gkeys = [*keys, "bucket"]

    out = {}
    vectorizable, per_group = {}, {}
    for col, how in col_agg.items():
        (vectorizable if getattr(how, "agg_kind", None) else per_group)[col] = how

    if per_group:
        g = x.groupby(gkeys, observed=True)
        for col, how in per_group.items():
            out[col] = g[col].agg(how)

    # batch the vectorized columns by the weights+kind they share, so each batch is ONE groupby
    batches = {}
    for col, how in vectorizable.items():
        batches.setdefault((how.agg_kind, id(how.cell_weights)), (how, []))[1].append(col)
    for (kind, _), (how, cols) in batches.items():
        agg = _weighted_group_agg(x, gkeys, cols, how.cell_weights, kind)
        out.update({c: agg[c] for c in cols})

    return pd.DataFrame({c: out[c] for c in col_agg}).reset_index()


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
        "precip_gridmet": weather_func,
        "max_temp": weather_func,
        "min_temp": weather_func,
        "max_rel_humidity": weather_func,
        "min_rel_humidity": weather_func,
        "specific_humidity": weather_func,
        "vpd": weather_func,
        "solar_rad": weather_func,
        "wind_speed": weather_func,
        "wind_direction": weather_func,
        "evapotranspiration": weather_func,
        "ref_et_alfalfa": weather_func,
        "burning_index": weather_func,
        "energy_release": weather_func,
        "fuel_moisture_100h": weather_func,
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

    # Same reduction, declared so agg_grid_to_buckets can do it vectorized instead of calling _func
    # once per (group, column). _func stays the reference definition and the fallback.
    _func.cell_weights, _func.agg_kind = area_ha, "wmean"
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

    _exp_decay_weighting.cell_weights, _exp_decay_weighting.agg_kind = cell_w, "wsum"
    return _exp_decay_weighting


def _standard_agg_dicts(site_data):
    """Agg dicts: plain sum for land-use, basin-area-weighted mean for weather."""
    func1 = sum
    func2 = _area_mean_curry(site_data=site_data)

    return _agg_dicts(func1, func2)


def _exp_decay_agg_dicts(site_data, lam):
    """Agg dicts using exp distance-decay-weighted sums with decay length `lam`. Sum only -- a normalized-mean variant measured no better (exp 9/10)."""
    sumfunc = _exp_curry(site_data=site_data, lam=lam)
    return _agg_dicts(sumfunc, sumfunc)


def flatten_buckets(df, bucket_col="bucket", value_cols=None):
    """Pivot a long bucketed frame to wide, folding the bucket into column names.

    ``(value_cols, bucket) -> (value_cols)``: each ``col`` becomes ``col_b0`` / ``col_b1`` / ... one per bucket.

    Parameters
    ----------
    df : pd.DataFrame
        Long frame carrying a ``date`` (daily) or ``year`` (annual) row key plus `bucket_col`.
    bucket_col : str, default 'bucket'
        Cast to int before pivoting, so the result keys on plain integers -- a surviving categorical would spawn empty columns for unobserved bins.
    value_cols : sequence, optional
        Columns to spread; default every non-key column.

    Returns
    -------
    pd.DataFrame
        One row per key, with that key as a column. The input is not mutated.
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


def tag_values(frame, suffix, keep=None):
    """Append `suffix` to every value column, so one aggregation's variants coexist in a single frame.

    Value columns are everything but the date/year/bucket keys; `keep` first restricts them to that list (used to drop total_kg_N, keeping only surplus_kgha). `date` MUST stay protected -- weather-with-lag frames are daily, and a suffixed date column leaves flatten_buckets unable to find its row key.
    """
    struct = [c for c in ("date", "year", "bucket") if c in frame.columns]
    val = [c for c in frame.columns if c not in struct]
    if keep is not None:
        val = [c for c in val if c in keep]
        frame = frame[struct + val]
    return frame.rename(columns={c: f"{c}{suffix}" for c in val})


def _with_key_column(d):
    """Return `d` with a 'date' or 'year' key as a column, resetting a date/year index into one when needed (and promoting a Series to a frame).

    Lets date-*indexed* inputs (the climatology Series/frames) be merged directly, without the caller having to reset_index first.
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

    Each frame must carry exactly one row-key — a ``date`` or ``year`` column, or a matching index (a DatetimeIndex / index named "date", or an index named "year"), which is reset into a column automatically. A Series is promoted to a frame.

    * ``date`` — a daily frame; aligned to the timeline on its date.
    * ``year`` — an annual frame; its one row per year is broadcast (copied) onto
      every date that falls in that year.

    The timeline is the union of every date-keyed frame's dates (unless `spine` is given); a year is derived from it, and each frame is left-joined on its own key — so an annual frame's one row per year repeats across all of that year's days.

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
