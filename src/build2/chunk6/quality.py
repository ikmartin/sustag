"""Is this series fit to train on? Scrubbing, then per-gauge features, then a high-recall detector.

SCRUBBING IS DATA CORRECTION, NOT FILTERING, and it lives here rather than in chunk 1 because chunk 1's rule is "everything fetched, nothing derived". One defect is removed: a NEGATIVE reading becomes NaN. Nothing else is touched, and in particular there is no upper bound -- see `LO`.

FLATLINE SCRUBBING IS DELIBERATELY NOT CARRIED OVER. The old build had it and removed it, because exact-value runs over-scrubbed coarsely-quantised series -- USGS reports to two decimals, so a genuine stable low-flow week reads as a stuck sensor. Flatlines are still DETECTED, and a human decides; they are just never silently deleted.

THE UNIT IS THE GAUGE, NOT THE REGISTRATION. A merged pair is one instrument published twice, so its series are concatenated before anything is measured. The seam between two registrations is not a defect, and scoring the halves separately would either flag both for being short or miss a fault that only shows across the join.

THE DETECTOR IS HIGH-RECALL BY DESIGN. Any single signal flags a gauge; precision is the reviewer's job, and the panel exists to make rejecting a false positive cheap. What it must not do is stay silent about a broken series, because nothing downstream can distinguish bad nitrate from unusual hydrology.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, schema

# TWO KINDS OF NEGATIVE, AND THEY ARE NOT THE SAME THING. Measured across the cohort, 25,229 negative readings
# fall into three populations: MPCA's -99999 fill value (10,439, now mapped to null at ingest), failed sensors
# pinned at large negatives (10,718 -- MPCA:32062001 alone holds 9,490 at -12.4 across six months), and small
# negatives near zero (4,072, of which USGS:03336890 contributes 483 at exactly -0.10 over five weeks).
#
# The third population is REAL DATA. An optical nitrate sensor near its detection limit returns small negative
# values from calibration noise, and they mean "at or below detection" -- deleting them biases the low end of a
# record upward, which is precisely where a model's calibration at zero is decided.
#
# So: CLAMP the plausible, NULL the impossible. A reading in [-1, 0) becomes 0 and survives as the measurement
# it is; anything below -1 is not a concentration and becomes NaN. The two are counted separately, because
# "this gauge reads slightly below zero sometimes" and "this gauge spent six months at -12.4" are different
# facts about an instrument and one number would hide which.
CLAMP_LO = -1.0

# THERE IS NO CEILING. An upper bound would have to assert what a river cannot contain, and this cohort spans
# tile outlets and headwater ditches where genuinely extreme nitrate is the signal rather than the error.
# Removing the old 50 mg/L ceiling recovered about 10,700 real readings. A high value that IS a fault still
# shows up -- as a spike, as a scale outlier, on the panel -- where a person judges it.

# A jump this large between consecutive samples is not a river changing, it is an instrument.
SPIKE = 3.0

# This many identical consecutive readings counts as a flat run. Gap-aware: identical values that merely
# bracket a hole in the record are not one continuous stuck stretch.
FLAT_K = 20

# Window for the worst-segment scan, in samples -- about three weeks at a 15-minute cadence. A bad MONTH inside
# a good decade is invisible to whole-series averages, and it is the failure a per-site mean cannot see.
SEG_W = 2000

# Cohort robust-z (median / MAD) beyond which a feature counts as an outlier, and the cap that stops a
# near-zero-MAD feature dominating the composite score.
ROBUST_Z = 3.5
ZCLIP = 8.0

# Below this fraction of daily variance explained by an annual cycle, a multi-year series has no seasonality.
# Set from the cohort rather than from taste -- see the measurement in the run report.
ASEASONAL_R2 = 0.05

# Below this many samples the shape features are noise about noise, so they are left null and the gauge is
# judged on the record floors instead.
MIN_SAMPLES_FOR_FEATURES = 50

# A run of impossible values longer than this is a broken instrument rather than a glitch. One day, because
# nothing legitimately reads below -1 mg/L for a day, and MPCA:32062001 sat there for six months.
STUCK_DAYS = 1.0

SIGNALS = ("range_violation", "dead_autocorr", "extreme_noise", "stuck_impossible", "noisy",
           "scale_outlier", "flatline_heavy", "seg_bad", "aseasonal")


def native_series(uids) -> pd.DataFrame:
    """The merged native-resolution nitrate for one gauge: `datetime`, `nitrate_con`, sorted, deduplicated.

    THE COLUMN IS NOT ALWAYS CALLED THAT. USGS names columns by pcode and files continuous nitrate under three of them, IWQIS uses `nitrate_con`, MPCA `nitrate` -- so the mapping is `chunk1.records.NITRATE_COLUMNS` and this asks that module rather than guessing. Assuming one name silently found nothing for every USGS gauge in the cohort.

    Only the nitrate column is read: the native files carry up to 23 parameters and 1.2 GB across the cohort, and the detector needs one of them.
    """
    from ..chunk1.records import NITRATE_COLUMNS, nitrate_series

    frames = []
    for u in uids:
        p = config.WATER_NATIVE / f"{schema.uid_to_filename(u)}.parquet"
        if not p.exists():
            continue
        source = schema.split_uid(u)[0]
        import pyarrow.parquet as pq

        have = {f.name for f in pq.ParquetFile(p).schema_arrow}
        cols = [c for c in NITRATE_COLUMNS.get(source, []) if c in have]
        if not cols:
            continue
        d = pd.read_parquet(p, columns=cols)
        # Row-wise max where a source files nitrate under several codes -- the same rule `records` applies.
        s = nitrate_series(d, source)
        if s is None:
            continue
        d = pd.DataFrame({"nitrate_con": s})
        frames.append(d.reset_index().rename(columns={d.index.name or "index": "datetime"}))
    if not frames:
        return pd.DataFrame({"datetime": pd.Series(dtype="datetime64[ns, UTC]"),
                             "nitrate_con": pd.Series(dtype="float64")})
    d = pd.concat(frames, ignore_index=True)
    d["nitrate_con"] = pd.to_numeric(d.nitrate_con, errors="coerce")
    # An overlapping dual registration publishes the same instant twice; max matches the rule `io.daily_max`
    # applies within a day, so the two never disagree about which value survives.
    d = d.groupby("datetime", as_index=False).nitrate_con.max()
    return d.sort_values("datetime", ignore_index=True)


def scrub(d: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Clamp sub-detection negatives to zero, null the impossible ones. Returns (series, n_clamped, n_nulled).

    Nothing else is touched, and in particular nothing above zero is. See `CLAMP_LO` for why one rule would have been wrong in both directions.
    """
    v = d.nitrate_con
    clamp = v.notna() & (v < 0) & (v >= CLAMP_LO)
    null = v.notna() & (v < CLAMP_LO)
    if clamp.any() or null.any():
        d = d.copy()
        d.loc[clamp, "nitrate_con"] = 0.0
        d.loc[null, "nitrate_con"] = np.nan
    return d, int(clamp.sum()), int(null.sum())


def _flat_runs(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Run length of identical consecutive values, GAP-AWARE. Each element carries its run's length.

    Gap-aware because two identical readings either side of a three-month hole are not a stuck sensor; the old build's comment on this is the one that motivated dropping flatline SCRUBBING while keeping the detection.
    """
    if len(v) < 2:
        return np.ones(len(v), dtype="int64")
    dt = np.diff(t).astype("timedelta64[s]").astype("float64")
    step = np.nanmedian(dt) if len(dt) else 0.0
    same = (v[1:] == v[:-1]) & (dt <= max(step * 3, 1.0))
    out = np.ones(len(v), dtype="int64")
    start = 0
    for i in range(1, len(v)):
        if not same[i - 1]:
            out[start:i] = i - start
            start = i
    out[start:] = len(v) - start
    return out


def impossible_runs(d: pd.DataFrame) -> tuple[int, float]:
    """`(count, longest run in days)` of readings below `CLAMP_LO`. Measured BEFORE scrubbing.

    Order matters and this is the whole reason the function exists. Scrubbing nulls these values, so a detector run afterwards sees a gap rather than a fault -- `MPCA:32062001` lost 9,490 samples to six months pinned at -12.4 and was never flagged for it, because by the time anything looked they were NaN. Counting first turns a silent deletion into a signal a person judges.
    """
    v = d.nitrate_con.to_numpy(dtype="float64")
    bad = np.isfinite(v) & (v < CLAMP_LO)
    if not bad.any():
        return 0, 0.0
    t = d.datetime.to_numpy()
    longest, run_start = 0.0, None
    for i, flag in enumerate(bad):
        if flag and run_start is None:
            run_start = i
        elif not flag and run_start is not None:
            longest = max(longest, (t[i - 1] - t[run_start]) / np.timedelta64(1, "D"))
            run_start = None
    if run_start is not None:
        longest = max(longest, (t[-1] - t[run_start]) / np.timedelta64(1, "D"))
    return int(bad.sum()), round(float(longest), 2)


def features(d: pd.DataFrame) -> dict:
    """Shape features for one gauge's scrubbed native series. Nulls where the record is too thin to describe."""
    out = dict(n_samples=0, lifespan_yr=np.nan, mean=np.nan, std=np.nan, vmax=np.nan,
               pct_out=np.nan, ac1=np.nan, noise=np.nan, spike_rate=np.nan,
               flatline_frac=np.nan, longest_flat_days=np.nan, seg_noise=np.nan, seg_flat=np.nan)
    if not len(d):
        return out
    t_all = d.datetime.to_numpy()
    v_all = d.nitrate_con.to_numpy(dtype="float64")
    keep = ~np.isnan(v_all)
    v, t = v_all[keep], t_all[keep]
    out["n_samples"] = int(len(v))
    if len(t_all) > 1:
        span = (t_all.max() - t_all.min()) / np.timedelta64(1, "s")
        out["lifespan_yr"] = round(float(span / (365.25 * 24 * 3600)), 3)
    if len(v) < MIN_SAMPLES_FOR_FEATURES:
        return out

    d1 = np.diff(v)
    runs = _flat_runs(v, t)
    L, start = int(runs.max()), int(np.argmax(runs))
    flat_days = float((t[start + L - 1] - t[start]) / np.timedelta64(1, "D")) if L > 1 else 0.0

    seg_noise = seg_flat = np.nan
    if len(v) >= 2 * SEG_W:
        nseg = len(v) // SEG_W
        vt = v[: nseg * SEG_W].reshape(nseg, SEG_W)
        wstd, dstd = vt.std(axis=1), np.diff(vt, axis=1).std(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            seg_noise = float(np.nanmax(np.where(wstd > 0, dstd / np.where(wstd > 0, wstd, 1), np.nan)))
        seg_flat = float((runs[: nseg * SEG_W].reshape(nseg, SEG_W).max(axis=1) / SEG_W).max())

    sd = float(v.std())
    out.update(
        mean=round(float(v.mean()), 3), std=round(sd, 3), vmax=round(float(v.max()), 3),
        # Measured on the scrubbed series, so this is what SURVIVED scrubbing -- always 0 unless `LO` itself
        # moved. The count that matters is reported separately by `scrub`.
        pct_out=round(100 * float(np.mean(v < 0)), 3),
        ac1=round(float(np.corrcoef(v[:-1], v[1:])[0, 1]), 4) if sd > 0 else np.nan,
        noise=round(float(d1.std() / sd), 4) if sd > 0 else np.nan,
        spike_rate=round(100 * float(np.mean(np.abs(d1) > SPIKE)), 3),
        flatline_frac=round(100 * float(np.mean(runs >= FLAT_K)), 2),
        longest_flat_days=round(flat_days, 2),
        seg_noise=None if seg_noise != seg_noise else round(seg_noise, 4),
        seg_flat=None if seg_flat != seg_flat else round(seg_flat, 4),
    )
    return out


def seasonal_strength(d: pd.DataFrame) -> float:
    """Fraction of daily variance explained by an annual cycle. 0 means no seasonality; ~1 means a clean one.

    INTRINSIC, AND THAT IS THE WHOLE POINT. The old build measured seasonality as the correlation of a gauge's day-of-year profile with the COHORT MEAN profile, which is valid on the Iowa-only cohort it was written for and invalid here. Measured across this cohort, the median coherence by state runs il 0.706, ne 0.554, mo 0.525, ia 0.330 -- and va -0.129, pa -0.172, md -0.246. The Mid-Atlantic gauges are ANTI-correlated with the cohort mean because the cohort mean is a Corn Belt profile and their nitrate cycle genuinely differs. Flagging them measured geography, not quality: 72 of 123 flags came from that one signal.

    Fitting `a*sin(2*pi*doy/365.25) + b*cos(...) + c` asks the question the signal was always meant to ask -- does this series have an annual cycle at all -- and a gauge with an inverted cycle scores high, as it should.
    """
    v = d.dropna(subset=["nitrate_con"])
    if len(v) < 365:
        return float("nan")
    day = pd.DatetimeIndex(v.datetime).dayofyear.to_numpy(dtype="float64")
    y = v.nitrate_con.to_numpy(dtype="float64")
    w = 2 * np.pi / 365.25
    X = np.column_stack([np.sin(w * day), np.cos(w * day), np.ones(len(day))])
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return float("nan")
    resid = y - X @ beta
    var = y.var()
    return float(1 - resid.var() / var) if var > 0 else float("nan")


def seasonal_coherence(daily: pd.DataFrame) -> pd.Series:
    """Correlation of each gauge's day-of-year profile with the cohort's. REPORTED, never flagged on.

    Kept because it is genuinely informative -- "this gauge's season is unlike its peers'" is worth seeing on the panel -- and demoted out of the signal set because on a cohort spanning Iowa to Virginia it separates regions rather than defects. `daily` is long: site_uid, doy, val.
    """
    prof = daily.groupby(["site_uid", "doy"]).val.mean().unstack("doy")
    cohort = prof.mean(axis=0)
    return prof.apply(lambda r: r.corr(cohort), axis=1).rename("seasonal_coherence")


def _robust_z(s: pd.Series) -> pd.Series:
    """Median/MAD z-score. MAD rather than std, so one broken gauge cannot flatten the scale everyone else is judged on."""
    x = pd.to_numeric(s, errors="coerce")
    med = x.median()
    mad = (x - med).abs().median()
    if not mad or not np.isfinite(mad):
        return pd.Series(0.0, index=s.index)
    return (x - med) / (1.4826 * mad)


def detect(F: pd.DataFrame) -> pd.DataFrame:
    """Per-signal flags and a composite score over the cohort's feature table.

    HIGH RECALL ON PURPOSE: any single signal flags the gauge. Two kinds of signal, and the distinction is the point -- the first three are absolute and say the series is broken on its own terms, while the rest are cohort-relative and say it is unlike its peers. A cohort-relative signal cannot fire on a cohort of one, which is why the absolute ones exist.
    """
    z = {c: _robust_z(F[c]) for c in
         ("noise", "mean", "std", "flatline_frac", "seg_noise", "seg_flat", "seasonal_strength")}
    num = lambda c: pd.to_numeric(F[c], errors="coerce")                                  # noqa: E731
    pos = lambda c, s=1: (s * z[c]).clip(lower=0)                                         # noqa: E731

    sig = pd.DataFrame(index=F.index)
    # Absolute -- unambiguously broken, whatever the rest of the cohort looks like.
    sig["range_violation"] = num("pct_out") > 0.5
    sig["dead_autocorr"] = num("ac1") < 0.9        # nitrate is highly persistent; this low is noise, not signal
    sig["extreme_noise"] = num("noise") > 0.25
    # Absolute by nature: a fortnight below -1 mg/L is broken whatever the cohort looks like, so this survives
    # any decision to gate on absolute signals only.
    sig["stuck_impossible"] = num("impossible_run_days") > STUCK_DAYS
    # Cohort-relative -- fires on an outlier.
    sig["noisy"] = z["noise"] > ROBUST_Z
    sig["scale_outlier"] = (z["mean"].abs() > ROBUST_Z) | (z["std"].abs() > ROBUST_Z)
    sig["flatline_heavy"] = z["flatline_frac"] > ROBUST_Z + 1
    sig["seg_bad"] = (z["seg_noise"] > ROBUST_Z + 1) | (z["seg_flat"] > ROBUST_Z + 1)
    # Guarded on lifespan: a record under two years has no annual cycle to be missing.
    sig["aseasonal"] = (num("seasonal_strength") < ASEASONAL_R2) & (num("lifespan_yr") > 2)

    sig["score"] = (pos("noise") + pos("flatline_frac") + pos("seg_noise") + pos("seg_flat")
                    + pos("seasonal_strength", -1)
                    + z["mean"].abs().clip(upper=ZCLIP) * 0.5
                    + 5 * sig.range_violation.astype(float)
                    + 5 * sig.dead_autocorr.astype(float)).round(2)
    sig["flagged"] = sig[list(SIGNALS)].any(axis=1)
    return sig


def assess(gauges: pd.DataFrame, members: dict) -> tuple[pd.DataFrame, dict]:
    """Scrub and score every gauge. Returns (feature+signal table, {uid: scrubbed native series}).

    The series are handed back rather than re-read, because the panel draws them and `assemble` writes them -- three passes over 1.2 GB of native parquet to compute the same thing twice would be the expensive part of this chunk.
    """
    rows, series, doy = [], {}, []
    for u in gauges.site_uid:
        d = native_series(members.get(u, [u]))
        # BEFORE the scrub, which is about to hide exactly what this measures.
        n_imp, imp_days = impossible_runs(d)
        d, n_clamped, n_scrubbed = scrub(d)
        series[u] = d
        f = features(d)
        f.update(site_uid=u, n_clamped=n_clamped, n_scrubbed=n_scrubbed,
                 n_impossible=n_imp, impossible_run_days=imp_days,
                 seasonal_strength=seasonal_strength(d))
        rows.append(f)
        if len(d):
            v = d.dropna(subset=["nitrate_con"])
            if len(v):
                doy.append(pd.DataFrame({"site_uid": u,
                                         "doy": pd.DatetimeIndex(v.datetime).dayofyear,
                                         "val": v.nitrate_con.to_numpy()}))
    F = pd.DataFrame(rows).set_index("site_uid")
    F["seasonal_coherence"] = (seasonal_coherence(pd.concat(doy, ignore_index=True))
                               if doy else np.nan)
    return F.join(detect(F)), series
