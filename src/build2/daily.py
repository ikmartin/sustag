"""Native observations -> one row per standard-time day, on the canonical channel schema.

    summarise(obs, "IWQIS")   ->  date | nitrate_mean | nitrate_min | ... | ph_n_obs

THE ENGINE, NOT THE STAGE. Chunk 2 orchestrates -- reads natives, writes files, reports coverage -- and this does the transformation for one site. It lives beside `io.py` rather than inside a chunk because the read layer and the widget's virtual-pin path need the same reduction on data that was never on disk.

WHAT A DAY IS. `io.STANDARD_TZ`, Central Standard year-round, so every day in every source is the same 24 hours and no day is 23 or 25 hours long. Sparse by construction: a day with no observation of a channel has no row rather than a null one, because a dense index over eighteen years x fourteen channels would be mostly nothing.

THE STATISTICS ARE COLLECTED, NOT PREDICTED. Seven primitives per channel, from which `amplitude = max - min` and `iqr = p75 - p25` are derived at read time rather than stored -- storing a difference beside its inputs is two things that can disagree. Channels declared `stats="mean"` in the registry store the mean alone: discharge and gage height carry nothing in their spread that the model consumes, and they are the two channels driving most of the fetch.

A PRE-AGGREGATED DAY WRITES NULL, NOT ZERO. USGS `_dv` columns are already a daily mean, so there is no spread to measure. Filling `min` and `max` from the single value would record a measured diel amplitude of zero where nothing was measured at all, and `n_obs` would claim one observation where the true count is unknown. Both stay null, and `params.resolve` is what detects the case.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import io as bio, params

# The seven stored primitives, in column order. `amplitude` and `iqr` are deliberately absent -- see the module docstring.
STATS = ("mean", "min", "max", "p25", "median", "p75", "n_obs")
# What a `stats="mean"` channel stores. `n_obs` survives because 96 observations and 3 are different measurements of a day whatever the channel.
MEAN_STATS = ("mean", "n_obs")


def scrub(s: pd.Series, channel: str) -> tuple[pd.Series, int, int]:
    """Apply the channel's physical range. Returns `(series, n_clamped, n_nulled)`.

    Two rules, because a reading below zero means different things for different quantities. For a concentration a small negative is a real measurement of "at or below detection" and is CLAMPED to zero, which keeps the information and the non-negativity every log transform downstream assumes; anything below `lo` is not a measurement and is nulled. For a level or a flow `clamp_negatives` is false and a negative is simply kept -- USGS reports negative discharge at tidally reversed sites and gage height is datum-relative.
    """
    c = params.CHANNELS[channel]
    out = s.astype("float64")
    n_clamped = n_nulled = 0

    if c.lo is not None:
        below = out < c.lo
        n_nulled += int(below.sum())
        out = out.mask(below)
    if c.clamp_negatives:
        neg = out < 0.0
        n_clamped = int(neg.sum())
        out = out.mask(neg, 0.0)
    if c.hi is not None:
        above = out > c.hi
        n_nulled += int(above.sum())
        out = out.mask(above)
    return out, n_clamped, n_nulled


def _preaggregated(obs: pd.DataFrame, source: str, channel: str) -> bool:
    """True when every native column feeding this channel is already a daily statistic.

    Read off the columns actually present rather than assumed per source: a site can report one channel at native resolution and another as a daily value, and no USGS site carries both forms of the SAME pcode, so the answer is never ambiguous within a channel.
    """
    hits = [params.resolve(source, c) for c in obs.columns]
    flags = [pre for ch, pre in hits if ch == channel]
    return bool(flags) and all(flags)


def _local_date(index: pd.DatetimeIndex) -> np.ndarray:
    """Standard-time calendar date for a UTC-aware index. One fixed offset, so no day is 23 or 25 hours."""
    return index.tz_convert(bio.STANDARD_TZ).date


def channel_frame(obs: pd.DataFrame, source: str, channel: str) -> tuple[pd.DataFrame | None, dict]:
    """Daily statistics for one channel, plus a scrub tally. `(None, tally)` when the site does not carry it."""
    tally = dict(channel=channel, n_raw=0, n_clamped=0, n_nulled=0, n_days=0, preaggregated=False)
    s = params.extract(obs, source, channel)
    if s is None or s.empty:
        return None, tally

    tally["n_raw"] = len(s)
    s, n_clamped, n_nulled = scrub(s, channel)
    s = s.dropna()
    tally.update(n_clamped=n_clamped, n_nulled=n_nulled)
    if s.empty:
        return None, tally

    pre = _preaggregated(obs, source, channel)
    tally["preaggregated"] = pre
    g = s.groupby(_local_date(pd.DatetimeIndex(s.index)))

    out = pd.DataFrame({f"{channel}_mean": g.mean()})
    if pre:
        # Already a daily mean: there is no within-day spread to report and no honest observation count.
        for k in STATS[1:]:
            out[f"{channel}_{k}"] = np.nan
    elif params.full_stats(channel):
        out[f"{channel}_min"] = g.min()
        out[f"{channel}_max"] = g.max()
        out[f"{channel}_p25"] = g.quantile(0.25)
        out[f"{channel}_median"] = g.median()
        out[f"{channel}_p75"] = g.quantile(0.75)
        out[f"{channel}_n_obs"] = g.size()
    else:
        out[f"{channel}_n_obs"] = g.size()

    out.index.name = "date"
    tally["n_days"] = len(out)
    return out, tally


def summarise(obs: pd.DataFrame, source: str,
              channels=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every canonical channel this site carries, binned to standard-time days.

    Parameters
    ----------
    obs : pd.DataFrame
        A native frame, UTC-indexed, as `sources.*.fetch` returns it.
    source : str
        USGS | IWQIS | MPCA.
    channels : Sequence[str], optional
        Restrict to these; default every channel the source can supply.

    Returns
    -------
    (frame, tallies)
        `frame` has a `date` index and one column group per channel present, sparse -- a day appears only where something was observed. `tallies` is one row per attempted channel, carrying the scrub counts and whether the source pre-aggregated it.

    See Also
    --------
    params.extract : how a channel is assembled from native columns.
    scrub : the per-channel physical range.
    """
    want = list(channels) if channels is not None else list(params.channels_of(source))
    frames, tallies = [], []
    for ch in want:
        f, t = channel_frame(obs, source, ch)
        tallies.append(t)
        if f is not None:
            frames.append(f)
    tal = pd.DataFrame(tallies)
    if not frames:
        return pd.DataFrame(index=pd.Index([], name="date")), tal
    out = pd.concat(frames, axis=1).sort_index()
    out.index.name = "date"
    return out, tal


def derived(frame: pd.DataFrame, channel: str) -> pd.DataFrame:
    """`amplitude` and `iqr` for one channel, computed on demand from the stored primitives.

    Not stored, so they cannot drift from their inputs. Both are null wherever the source pre-aggregated the channel, which falls out of the arithmetic rather than needing a branch: `max` and `min` are already null there.
    """
    out = pd.DataFrame(index=frame.index)
    if f"{channel}_max" in frame and f"{channel}_min" in frame:
        out[f"{channel}_amplitude"] = frame[f"{channel}_max"] - frame[f"{channel}_min"]
    if f"{channel}_p75" in frame and f"{channel}_p25" in frame:
        out[f"{channel}_iqr"] = frame[f"{channel}_p75"] - frame[f"{channel}_p25"]
    return out
