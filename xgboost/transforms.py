"""Frame plumbing and the geometry primitive: flow-distance bucketing over a basin's COMID set.

THE ONE REAL CHANGE FROM THE OLD STACK. The predecessor bucketed raster CELLS by EUCLIDEAN distance from the sensor; this buckets CATCHMENTS by FLOW distance along the channel network -- `pathlength(reach) - pathlength(outlet)` from the NHDPlus value-added attributes, the distance water actually travels. Two cells equidistant from a sensor can be 3 km and 40 km of channel apart when a basin wraps a divide, and it is the channel distance that sets travel time, dilution and in-stream processing. The bucket columns are therefore NOT comparable to the old `_b{k}` columns: they are a different quantity on a different grain, and every ablation result inherited from the old recipes is a hypothesis to re-measure rather than a settled finding.

WEIGHTS. A catchment contributes `w * areasqkm` -- membership times area, the catchment-grain heir of `frac_cell_in_basin * cell_area`. A plain accumulate has w = 1.0 throughout; a polygon-cut set carries partial membership, and partial catchments count partially, as they did per-cell before.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def flow_buckets(wset: pd.DataFrame, edges) -> pd.Series:
    """comid -> bucket index (0 = nearest), from `flow_dist_m`. `edges=()` collapses to a single bucket."""
    edges = list(edges)
    if not len(wset):
        return pd.Series(dtype="int64")
    if not edges:
        return pd.Series(0, index=wset.comid.to_numpy(), dtype="int64")
    idx = np.digitize(wset.flow_dist_m.fillna(np.inf).to_numpy(), edges, right=False)
    return pd.Series(idx.astype("int64"), index=wset.comid.to_numpy())


def bucket_weights(wset: pd.DataFrame, decay_lambda: float | None = None) -> pd.Series:
    """comid -> weight: membership x area, optionally x exp(-flow_dist / lambda).

    The decay is the old stack's `exp` arm on the new axis. Its case is unchanged: near-field land contributes more per hectare than far-field land, and BOTH the decayed and undecayed surplus blocks earned their place in the old curation, so the pair is worth carrying until measured here.
    """
    if not len(wset):
        return pd.Series(dtype="float64")
    w = wset.w.fillna(0.0).to_numpy() * wset.areasqkm.fillna(0.0).to_numpy()
    if decay_lambda:
        w = w * np.exp(-wset.flow_dist_m.fillna(0.0).to_numpy() / float(decay_lambda))
    return pd.Series(w, index=wset.comid.to_numpy())


def agg_to_buckets(frame: pd.DataFrame, buckets: pd.Series, weights: pd.Series, keys, value_cols,
                   how: str = "sum") -> pd.DataFrame:
    """Aggregate a per-COMID frame to (keys..., bucket).

    `how='sum'` accumulates a MASS (kilograms, pixel areas) weighted by membership; `how='mean'` takes the weight-weighted MEAN of an INTENSITY (kg/ha, degrees, mm) -- summing an intensity would make it scale with basin size, which is the single easiest way to build a feature that only measures how big the basin is.
    """
    if not len(frame):
        return pd.DataFrame(columns=[*keys, "bucket", *value_cols])
    d = frame[frame.comid.isin(buckets.index)].copy()
    if not len(d):
        return pd.DataFrame(columns=[*keys, "bucket", *value_cols])
    d["bucket"] = buckets.reindex(d.comid.to_numpy()).to_numpy()
    w = weights.reindex(d.comid.to_numpy()).fillna(0.0).to_numpy()
    gkeys = [*keys, "bucket"]
    if how == "sum":
        for c in value_cols:
            d[c] = pd.to_numeric(d[c], errors="coerce") * w
        return d.groupby(gkeys, observed=True)[list(value_cols)].sum().reset_index()
    out = {}
    d["_w"] = w
    for c in value_cols:
        d[f"_v_{c}"] = pd.to_numeric(d[c], errors="coerce") * w
    g = d.groupby(gkeys, observed=True)
    num = g[[f"_v_{c}" for c in value_cols]].sum()
    den = g["_w"].sum()
    for c in value_cols:
        out[c] = num[f"_v_{c}"] / den.where(den > 0)
    return pd.DataFrame(out).reset_index()


def bucket_lags(wset: pd.DataFrame, buckets: pd.Series, velocity_mps: float,
                use_pathtime: bool = True) -> dict:
    """bucket -> travel-time lag in whole days.

    Prefers NHDPlus's own modeled path time (`pathtimema`, days) where present -- the authority's hydraulics rather than a hand-set velocity -- and falls back to `flow_dist / velocity` where it is absent. A bucket's lag is the weight-free MEDIAN over its catchments, so one very distant reach does not drag the whole ring's shift.
    """
    if not len(wset):
        return {}
    d = wset.copy()
    d["bucket"] = buckets.reindex(d.comid.to_numpy()).to_numpy()
    days_from_dist = d.flow_dist_m.fillna(0.0) / (float(velocity_mps) * 86_400.0)
    if use_pathtime and "pathtimema" in d.columns:
        pt = pd.to_numeric(d.pathtimema, errors="coerce")
        # pathtimema is time to the TERMINAL outlet; the differential to this basin's outlet is what
        # matters, and its minimum over the set is the outlet's own value
        pt = pt - pt.min() if pt.notna().any() else pt
        days = pt.fillna(days_from_dist)
    else:
        days = days_from_dist
    d["_lag"] = days
    return {int(b): int(round(float(v))) for b, v in d.groupby("bucket", observed=True)._lag.median().items()}


def lag_buckets(frame: pd.DataFrame, lags: dict, date_col: str = "date",
                bucket_col: str = "bucket") -> pd.DataFrame:
    """Shift each bucket's rows FORWARD by its travel-time lag: the day distant water falls is the day it is credited to the sensor."""
    if not len(frame) or not lags:
        return frame
    out = frame.copy()
    out[date_col] = pd.to_datetime(out[date_col])
    shift = out[bucket_col].map(lambda b: lags.get(int(b), 0)).fillna(0).astype(int)
    out[date_col] = out[date_col] + pd.to_timedelta(shift, unit="D")
    return out


def flatten_buckets(frame: pd.DataFrame, bucket_col: str = "bucket") -> pd.DataFrame:
    """(keys, bucket) long -> one row per key with `{value}_f{bucket}` columns. `_f` for FLOW distance, deliberately not the old `_b`, so a column name can never be mistaken for the old euclidean geometry."""
    if not len(frame) or bucket_col not in frame.columns:
        return frame
    keys = [c for c in frame.columns if c in ("date", "year")]
    vals = [c for c in frame.columns if c not in keys + [bucket_col]]
    wide = frame.pivot_table(index=keys, columns=bucket_col, values=vals, observed=True)
    wide.columns = [f"{v}_f{int(b)}" for v, b in wide.columns]
    return wide.reset_index()


def tag_values(frame: pd.DataFrame, suffix: str, keep=()) -> pd.DataFrame:
    """Suffix every value column (keys and `keep` untouched) so two variants of one quantity can coexist."""
    keys = {"date", "year", "bucket", *keep}
    return frame.rename(columns={c: f"{c}{suffix}" for c in frame.columns if c not in keys})


def merge_on_date(frames, spine: pd.DatetimeIndex) -> pd.DataFrame:
    """Left-join every frame onto a daily spine: date-keyed frames on the date, year-keyed frames on the year (broadcast across that year's days), scalars assigned as columns."""
    out = pd.DataFrame(index=pd.DatetimeIndex(spine).sort_values())
    out.index.name = "date"
    out["year"] = out.index.year
    for f in frames:
        if f is None or (hasattr(f, "empty") and f.empty):
            continue
        if isinstance(f, pd.Series):
            out[f.name or "value"] = f.reindex(out.index)
            continue
        g = f.copy()
        if "date" in g.columns:
            g["date"] = pd.to_datetime(g["date"])
            g = g.drop_duplicates("date").set_index("date")
            out = out.join(g, how="left", rsuffix="_dup")
        elif "year" in g.columns:
            g = g.drop_duplicates("year").set_index("year")
            out = out.join(g, on="year", how="left", rsuffix="_dup")
    return out.drop(columns=[c for c in out.columns if c.endswith("_dup")])
