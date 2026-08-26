"""Is this series fit to publish? Per-(site, channel) detectors: a battery for high-tier channels, rule checks for the rest.

THE UNIT IS (SITE, CHANNEL), NOT THE SITE. A site's discharge can be excluded while its nitrate is kept -- one verdict per series, recorded in the quality ledger keyed on the member set and the channel, so a re-snap or a re-mint never orphans an answer.

TWO TIERS, TWO DEPTHS, per the chapter. A HIGH-SCRUTINY channel gets the full battery -- shape features over the native-resolution record, cohort-relative outliers, an intrinsic seasonality test -- and a flagged series GATES on human review: nitrate is scarce, health-relevant, and a broken series is indistinguishable downstream from unusual hydrology. A STANDARD channel gets the absolute rule checks only, and the rule's verdict is recorded to the auto record, never asked about -- a queue holding every channel of every site is a queue nobody reads.

SCRUBBING IS NOT DONE HERE. The physical-range rule (`registry.lo/hi/clamp_negatives`) is applied at CANONICALISATION, where the record is made -- so the detector judges exactly the series the build publishes. What the scrub hides, `impossible_runs` measures on the RAW native before it: MPCA:32062001 spent six months pinned at -12.4, lost 9,490 samples to the scrub, and was never flagged, because by the time anything looked they were NaN.

FLATLINE SCRUBBING IS DELIBERATELY ABSENT. Exact-value runs over-scrub coarsely-quantised series -- USGS reports to two decimals, so a genuine stable low-flow week reads as a stuck sensor. Flatlines are DETECTED, and a human decides; they are never silently deleted.

THE DETECTOR IS HIGH-RECALL BY DESIGN. Any single signal flags a series; precision is the reviewer's job, and the panel exists to make rejecting a false positive cheap. What it must not do is stay silent about a broken series.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts, io as bio, ledger, registry
from ..derive import reduction

# A jump this large between consecutive samples is not a river changing, it is an instrument. Calibrated on nitrate (mg/L as N); for other channels the spike test is z-scored on the series' own diffs instead.
SPIKE = 3.0

# This many identical consecutive readings counts as a flat run. Gap-aware: identical values that merely bracket a hole in the record are not one continuous stuck stretch.
FLAT_K = 20

# Window for the worst-segment scan, in samples -- about three weeks at a 15-minute cadence. A bad MONTH inside a good decade is invisible to whole-series averages.
SEG_W = 2000

# Cohort robust-z (median / MAD) beyond which a feature counts as an outlier, and the cap that stops a near-zero-MAD feature dominating the composite score.
ROBUST_Z = 3.5
ZCLIP = 8.0

# Below this fraction of daily variance explained by an annual cycle, a multi-year series has no seasonality.
ASEASONAL_R2 = 0.05

# Below this many samples the shape features are noise about noise; the series is left unjudged by shape.
MIN_SAMPLES_FOR_FEATURES = 50

# A run of impossible values longer than this is a broken instrument rather than a glitch.
STUCK_DAYS = 1.0

SIGNALS = ("range_violation", "dead_autocorr", "extreme_noise", "stuck_impossible", "noisy",
           "scale_outlier", "flatline_heavy", "seg_bad", "aseasonal")

# A single exact value held this long is never a river; below it, quantised low-flow plateaus are common (a dry-summer week at the detection limit is real). This is the PROPOSAL threshold only -- a human confirms every span, and instrument railing (brief saturation touches at the sensor's ceiling) is deliberately not proposed, because "at least the ceiling" is information rather than a malfunction window.
PROPOSE_STUCK_DAYS = 7.0


# ---- span-scoped exclusions ---------------------------------------------------------------------------

def parse_spans(text) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """`'2021-03-01..2021-07-15; 2022-01-02..2022-01-09'` -> [(start, end), ...], inclusive both ends.

    STRICT: a malformed span raises rather than masking a guess -- the caller turns that into a halt naming the row. Empty or NaN is the common case and returns [].
    """
    if text is None or (isinstance(text, float) and np.isnan(text)) or pd.isna(text) or not str(text).strip():
        return []
    out = []
    for part in str(text).split(";"):
        part = part.strip()
        if not part:
            continue
        if ".." not in part:
            raise ValueError(f"span {part!r} is not 'YYYY-MM-DD..YYYY-MM-DD'")
        a, _, b = part.partition("..")
        try:
            lo, hi = pd.Timestamp(a.strip()), pd.Timestamp(b.strip())
        except (ValueError, TypeError) as e:
            raise ValueError(f"span {part!r}: {e}") from None
        if pd.isna(lo) or pd.isna(hi) or lo > hi:
            raise ValueError(f"span {part!r} is empty or reversed")
        out.append((lo.normalize(), hi.normalize()))
    return out


def span_days(spans) -> int:
    """Total days the spans cover, for the classification note."""
    return sum(int((b - a).days) + 1 for a, b in spans)


def in_spans(dates, spans) -> np.ndarray:
    """Boolean mask over `dates` (date-like) for membership in any span. Inclusive both ends."""
    d = pd.to_datetime(pd.Series(dates)).dt.normalize()
    m = np.zeros(len(d), dtype=bool)
    for a, b in spans:
        m |= ((d >= a) & (d <= b)).to_numpy()
    return m


def propose_spans(d: pd.DataFrame) -> str:
    """Stuck-window proposals for one native series: maximal identical-value runs >= PROPOSE_STUCK_DAYS.

    The same gap-aware grouping the flatline detector uses -- identical values bracketing a hole in the record are not one stuck stretch. Returned in the sheet's own span syntax so confirming a proposal is a cell copy, not a transcription off a plot.
    """
    v_all = d.value.to_numpy(dtype="float64")
    keep = ~np.isnan(v_all)
    v, t = v_all[keep], d.datetime.to_numpy()[keep]
    if len(v) < 2:
        return ""
    dt = np.diff(t).astype("timedelta64[s]").astype("float64")
    step = np.nanmedian(dt) if len(dt) else 0.0
    same = (v[1:] == v[:-1]) & (dt <= max(step * 3, 1.0))
    spans, start = [], 0
    for i in range(1, len(v) + 1):
        if i == len(v) or not same[i - 1]:
            days = (t[i - 1] - t[start]) / np.timedelta64(1, "D")
            if days >= PROPOSE_STUCK_DAYS:
                spans.append(f"{pd.Timestamp(t[start]).date()}..{pd.Timestamp(t[i - 1]).date()}")
            start = i
    return "; ".join(spans)


# ---- the native series ------------------------------------------------------------------------------

def native_series(members, channel: str) -> pd.DataFrame:
    """The merged native-resolution series for one site's channel: `datetime`, `value`, sorted, deduplicated.

    Column names differ per source and per pcode; `registry.extract` is the one mapping. Overlapping members publish the same instant twice; the channel's own combine rule decides which value survives, the same rule canonicalisation applies within a day, so the two never disagree.
    """
    frames = []
    for u in members:
        p = config.ACQ_WATER_NATIVE / f"{contracts.uid_to_filename(u)}.parquet"
        if not p.exists():
            continue
        source = contracts.split_uid(u)[0]
        obs = pd.read_parquet(p)
        s = registry.extract(obs, source, channel)
        if s is None or not len(s):
            continue
        frames.append(pd.DataFrame({"datetime": s.index, "value": s.to_numpy(dtype="float64")}))
    if not frames:
        return pd.DataFrame({"datetime": pd.Series(dtype="datetime64[ns, UTC]"),
                             "value": pd.Series(dtype="float64")})
    d = pd.concat(frames, ignore_index=True)
    agg = "max" if registry.CHANNELS[channel].combine == "max" else "mean"
    d = d.groupby("datetime", as_index=False).value.agg(agg)
    return d.sort_values("datetime", ignore_index=True)


def impossible_runs(d: pd.DataFrame, channel: str) -> tuple[int, float]:
    """`(count, longest run in days)` of readings outside the channel's physical range -- on the RAW native.

    Measured BEFORE the scrub the reduction applies, which is the whole reason this exists: the scrub nulls these values, so a detector run on the published record sees a gap rather than a fault.
    """
    c = registry.CHANNELS[channel]
    v = d.value.to_numpy(dtype="float64")
    bad = np.zeros(len(v), dtype=bool)
    fin = np.isfinite(v)
    if c.lo is not None:
        bad |= fin & (v < c.lo)
    if c.hi is not None:
        bad |= fin & (v > c.hi)
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


# ---- shape features ---------------------------------------------------------------------------------

def _flat_runs(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Run length of identical consecutive values, GAP-AWARE. Each element carries its run's length."""
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


def features(d: pd.DataFrame) -> dict:
    """Shape features for one scrubbed native series. Nulls where the record is too thin to describe."""
    out = dict(n_samples=0, lifespan_yr=np.nan, mean=np.nan, std=np.nan, vmax=np.nan,
               pct_out=np.nan, ac1=np.nan, noise=np.nan, spike_rate=np.nan,
               flatline_frac=np.nan, longest_flat_days=np.nan, seg_noise=np.nan, seg_flat=np.nan)
    if not len(d):
        return out
    t_all = d.datetime.to_numpy()
    v_all = d.value.to_numpy(dtype="float64")
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
    """Fraction of daily variance explained by an annual cycle. INTRINSIC, and that is the whole point.

    The old build measured seasonality as correlation with the COHORT MEAN profile, which is valid on an Iowa-only cohort and invalid beyond it: Mid-Atlantic gauges are ANTI-correlated with a Corn Belt mean because their nitrate cycle genuinely differs, and 72 of 123 flags measured geography rather than quality. Fitting `a*sin + b*cos + c` on day-of-year asks the question the signal was always meant to ask, and a gauge with an inverted cycle scores high, as it should.
    """
    v = d.dropna(subset=["value"])
    if len(v) < 365:
        return float("nan")
    day = pd.DatetimeIndex(v.datetime).dayofyear.to_numpy(dtype="float64")
    y = v.value.to_numpy(dtype="float64")
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
    """Correlation of each site's day-of-year profile with the cohort's. REPORTED, never flagged on -- on a cohort spanning regions it separates geography, not defects. `daily` is long: site_id, doy, val."""
    prof = daily.groupby(["site_id", "doy"]).val.mean().unstack("doy")
    cohort = prof.mean(axis=0)
    return prof.apply(lambda r: r.corr(cohort), axis=1).rename("seasonal_coherence")


def _robust_z(s: pd.Series) -> pd.Series:
    """Median/MAD z-score. MAD rather than std, so one broken series cannot flatten the scale everyone else is judged on."""
    x = pd.to_numeric(s, errors="coerce")
    med = x.median()
    mad = (x - med).abs().median()
    if not mad or not np.isfinite(mad):
        return pd.Series(0.0, index=s.index)
    return (x - med) / (1.4826 * mad)


def detect(F: pd.DataFrame) -> pd.DataFrame:
    """Per-signal flags and a composite score over one channel's cohort feature table.

    HIGH RECALL ON PURPOSE: any single signal flags the series. Two kinds of signal, and the distinction is the point -- the absolute ones say the series is broken on its own terms, the cohort-relative ones that it is unlike its peers. A cohort-relative signal cannot fire on a cohort of one, which is why the absolute ones exist.
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


# ---- the two tiers ----------------------------------------------------------------------------------

def assess_high_tier(sites: pd.DataFrame, channel: str) -> tuple[pd.DataFrame, dict]:
    """Full battery for one high-scrutiny channel over every site carrying it. Returns (features+signals, {site_id: native frame}).

    The series are handed back rather than re-read: the review panel draws them, and re-reading a gigabyte of native parquet to plot what was just measured would be the expensive half of this stage.
    """
    rows, series, doy = [], {}, []
    carry = sites[sites.channels_present.fillna("").str.split("+").apply(lambda v: channel in v)]
    for r in carry.itertuples():
        members = str(r.bundle).split("+")
        d = native_series(members, channel)
        n_imp, imp_days = impossible_runs(d, channel)         # BEFORE the scrub hides exactly this
        s, n_clamped, n_nulled = reduction.scrub(d.value, channel)
        d = d.assign(value=s)
        series[r.site_id] = d
        f = features(d)
        f.update(site_id=r.site_id, members=r.bundle, channel=channel,
                 n_clamped=n_clamped, n_scrubbed=n_nulled,
                 n_impossible=n_imp, impossible_run_days=imp_days,
                 seasonal_strength=seasonal_strength(d),
                 proposed_spans=propose_spans(d))
        rows.append(f)
        v = d.dropna(subset=["value"])
        if len(v):
            doy.append(pd.DataFrame({"site_id": r.site_id,
                                     "doy": pd.DatetimeIndex(v.datetime).dayofyear,
                                     "val": v.value.to_numpy()}))
    if not rows:
        return pd.DataFrame(), series
    F = pd.DataFrame(rows).set_index("site_id")
    F["seasonal_coherence"] = (seasonal_coherence(pd.concat(doy, ignore_index=True))
                               if doy else np.nan)
    return F.join(detect(F)), series


def assess_standard(rec: pd.DataFrame, channel: str) -> tuple[str, str]:
    """Rule verdict for one standard channel's MERGED DAILY record: `(verdict, reason)`.

    Absolute checks only -- a dead series (no variance over a long record) is broken whatever its peers look like; everything subtler waits for a person to care about the channel. The rule's verdict is RECORDED, never asked about.
    """
    col = f"{channel}_mean"
    if col not in rec.columns:
        return "keep", "no record"
    v = pd.to_numeric(rec[col], errors="coerce").dropna()
    if len(v) >= 90 and float(v.std()) == 0.0:
        return "exclude", f"dead series: {len(v)} days without variance"
    return "keep", "rule checks passed"


# ---- the ledger --------------------------------------------------------------------------------------

def quality_ledger():
    return ledger.QUALITY


def parse_threshold(text) -> float | None:
    """`max_threshold` cell -> float or None. STRICT: a non-numeric cell raises rather than masking a guess."""
    if text is None or (isinstance(text, float) and np.isnan(text)) or pd.isna(text) or not str(text).strip():
        return None
    try:
        v = float(str(text).strip())
    except ValueError:
        raise ValueError(f"max_threshold {text!r} is not a number") from None
    if not np.isfinite(v):
        raise ValueError(f"max_threshold {text!r} is not finite")
    return v


def verdicts(sites: pd.DataFrame, records: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Every (site, channel) verdict: the battery + ledger for high-tier, the rule for the rest.

    Returns `(verdict table, high-tier feature table, native series)`. Verdict columns: site_id, members, channel, verdict (keep|exclude|pending), decided_by (rule|human|pending), reason, exclude_spans, max_threshold. A `keep` with `exclude_spans` keeps the series minus those day windows; a `keep` with `max_threshold` keeps it minus every day whose readings exceed the threshold -- a SENSOR-specific ceiling in canonical units, where the registry's `hi` is the channel-wide physical one. Publication applies both. The PENDING state is what the publication gate reads -- a flagged high-tier series with no decision on file withholds its site from publication rather than publishing unjudged data.
    """
    led = quality_ledger()
    high: dict[str, tuple[pd.DataFrame, dict]] = {}
    for ch in registry.high_scrutiny():
        high[ch] = assess_high_tier(sites, ch)

    d = led.load()
    decisions = {}          # (members, channel) -> (verdict, exclude_spans text, max_threshold or None)
    for r in d.itertuples():
        spans_text = str(getattr(r, "exclude_spans", "") or "").strip()
        try:
            if spans_text:
                parse_spans(spans_text)
            thr = parse_threshold(getattr(r, "max_threshold", ""))
        except ValueError as e:
            raise SystemExit(f"{led.path.name} row ({r.members}, {r.channel}): {e}")
        decisions[(str(r.members), str(r.channel))] = (str(r.decision).strip(), spans_text, thr)
    out, feats = [], []
    all_series: dict = {}
    for ch in registry.NAMES:
        if ch in registry.high_scrutiny():
            F, series = high[ch]
            all_series.update({(sid, ch): s for sid, s in series.items()})
            if len(F):
                feats.append(F.assign(channel=ch))
            for sid, row in F.iterrows():
                key = (str(row.members), ch)
                human, spans_text, thr = decisions.get(key, (None, "", None))
                if human:
                    v, by, why = human, "human", "quality review"
                elif bool(row.flagged):
                    v, by, spans_text, thr = "pending", "pending", "", None
                    why = ",".join(s for s in SIGNALS if bool(row.get(s)))
                else:
                    v, by, why, spans_text, thr = "keep", "rule", "no signal fired", "", None
                out.append(dict(site_id=sid, members=row.members, channel=ch,
                                verdict=v, decided_by=by, reason=why, exclude_spans=spans_text,
                                max_threshold=thr, score=row.get("score"), flagged=bool(row.flagged)))
        else:
            for r in sites.itertuples():
                chans = str(r.channels_present or "").split("+")
                if ch not in chans:
                    continue
                key = (str(r.bundle), ch)
                human, spans_text, thr = decisions.get(key, (None, "", None))
                if human:
                    v, by, why = human, "human", "quality review"
                else:
                    v, why = assess_standard(records.get(r.site_id, pd.DataFrame()), ch)
                    by, spans_text, thr = "rule", "", None
                out.append(dict(site_id=r.site_id, members=r.bundle, channel=ch,
                                verdict=v, decided_by=by, reason=why, exclude_spans=spans_text,
                                max_threshold=thr, score=np.nan, flagged=v == "exclude"))
    V = pd.DataFrame(out, columns=["site_id", "members", "channel", "verdict", "decided_by",
                                   "reason", "exclude_spans", "max_threshold", "score", "flagged"])
    Fs = pd.concat(feats) if feats else pd.DataFrame()
    return V, Fs, all_series
