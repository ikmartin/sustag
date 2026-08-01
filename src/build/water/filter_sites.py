"""STEP 3 of the reworked water build: split candidates into good / flagged / short, with a human-review gate for quality.

Two kinds of filter, treated very differently:

  * QUANTITY (non-nan nitrate row count) -> `short_sites`. Applied automatically. A site is dropped if its number of non-nan nitrate observations does not exceed MIN_NITRATE_ROWS below -- a single, interpretable data-volume floor (see experiments/site-discovery/nitrate_row_counts.py for the cohort distribution used to pick it). This replaces the old sparsity + lifespan cutoffs.
  * QUALITY (sensor corruption) -> `flagged_sites`. A deliberately HIGH-false-positive detector: it is tuned for recall (catch every actually-bad site) and hands a human a review panel to confirm. A flagged site is NOT auto-dropped -- it is dropped only once a human promotes it into KNOWN_BAD below and reruns.
  * SIZE (too small) -> `tiny_sites`. Applied automatically, but only to sites whose basin AND grid_global are already built (this step precedes make_features, so on a first-ever build it is a no-op and takes effect on the next pass). The measure is CELL COVERAGE -- Sum(frac_cell_in_basin), the basin's area in units of covariate cells -- not raw km2, because cell size is not constant and the grid may be re-cut at another resolution; coverage is already in the units that matter. Below MIN_CELL_COVERAGE the basin owns less than half a cell, so every covariate it has is a weighted sliver of cells lying mostly outside it -- and when those cells' centroids fall outside the basin, dist_to_sensor comes back all-NaN and every multi-bucket recipe raises "No objects to concatenate".
  * SIZE (LOFO bridging) -> `oversized_sites`. Read from pipeline_config [site_filters].big_basin. These are hub basins that physically contain much of the fleet; because the conflict graph joins any two sites that are spatially nested AND temporally overlapping, one hub chains all its upstream sites into a single connected component -- i.e. one giant LOFO fold. Excluding four of them takes the largest family from 76 sites / 60% of all rows down to 23 / 25%, at a cost of 4.5% of rows. Selected on SITES CONTAINED, not basin area.

    good_sites = [all raw_site_list sites] - short_sites - KNOWN_BAD - oversized_sites - tiny_sites

Outputs `filtered_sites.json` (the three lists) + `review_panel.png` (one row per flagged site: a nitrate timeseries, a value histogram, and a stats table). The last CLI line prints `flagged_sites` as a copy-pasteable Python list.

HUMAN REVIEW LOOP: inspect review_panel.png, decide which flagged sites are truly corrupt, paste those uids into KNOWN_BAD below, and rerun this script. (A future item: do this review inside the Dash widget's basin_editor in a "data review mode" rather than a printed panel.)

    python -m src.build.water.filter_sites
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]  # repo root
sys.path.insert(0, str(_ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from src.build.config import get_config
from src.build.water._paths import (
    filtered_sites_path,
    interim_water_dir,
    raw_site_list_path,
    read_raw_series,
    review_panel_path,
)

# ── oversized "hub" basins, excluded for LOFO health (see module docstring) ────
# Unlike KNOWN_BAD this stays in pipeline_config.toml: it is a property of the basin geometry the build already knows about, not a human quality verdict, and the widget/basin tooling reads the same config. Read at call time so an edit takes effect without touching this file.
def _oversized() -> list[str]:
    return list(get_config()["site_filters"].get("big_basin", []))

# ── the human-curated bad-site list ───────────────────────────────────────────
# Edit this by hand after reviewing review_panel.png, then rerun. (Phase-1: seeded from the current pipeline_config.toml [site_filters].known_bad; Phase-2 removes the config key so this is the sole source of truth.)
KNOWN_BAD = [
    "USGS-05480603",
    "USGS-05480986",
    # "USGS-05483600", "USGS-06480000", "USGS-06899900", "USGS-06900050", "USGS-06901500",
    "WQS0019",
    # "WQS0025", "WQS0036", "WQS0042", "WQS0043", "WQS0044",
    "WQS0045",
    # "WQS0061", "WQS0064", "WQS0065", "WQS0066",
    "WQS0068",
    # "WQS0073",
    "WQS0075",
    # "WQS0076", "WQS0080",
    "WQS0085",
    "WQS0088",
    "WQS0090",
    # "WQS0091", "WQS0103", "WQS0111",
    "WQS0113",
    "WQS0114",
    "WQS9901",
    "WQS9902",
    "WQS9903",
    "WQS9904",
    "WQS9999",
]

# QUANTITY floor: a site is `short` (dropped) unless its non-nan nitrate row count EXCEEDS this. Tune it against the cohort distribution from experiments/site-discovery/nitrate_row_counts.py. Default 10,000 rows (~3.5 months at 15-min cadence) drops the empty + clearly-tiny sites and keeps the rest.
MIN_NITRATE_ROWS = 10_000

# COVERAGE floor: a basin with less than this many covariate CELLS' worth of area is dropped as `tiny_sites`. The measure is Sum(frac_cell_in_basin) over the grid_global cells the basin touches -- i.e. how much genuine cell area the basin actually owns, in units of cells.
#
# Thresholding coverage rather than km^2 is deliberate. Raw area has to be compared against a cell size that is not constant: a 1/24-deg cell is ~15.5 km^2 here but shrinks toward the poles, and the grid can be re-cut at a different resolution entirely. Coverage is already expressed in the units that matter -- if it is below 1, no single cell is fully inside the basin, and every covariate is a weighted average over cells that lie mostly OUTSIDE it. At 0.5 the basin owns less than half of one cell, so its "basin mean" is really its neighbours' land cover.
#
# The failure is not always graceful. WQS0109 (coverage 0.25) has two cells at 15% and 10% membership whose CENTROIDS fall outside the basin and off the sensor's upstream flow tree, so dist_to_sensor is NaN for every cell, every distance bucket is NaN, and any multi-bucket recipe dies in lag_buckets with "No objects to concatenate".
#
# At 0.5 this drops 7 of 123 sites (5.7%) but only 2.97% of daily-nitrate rows -- these basins are small AND short. The largest dropped is 0.49 cells; the two just above the line are 0.65 and 0.98, so even the survivors near the cut own less than one full cell.
MIN_CELL_COVERAGE = 0.5


def _coverage_basin(uid: str):
    """The polygon to measure coverage on: the site's PREFERRED basin, else its basin1 delineation straight off disk.

    The fallback is what makes this filter self-sustaining rather than self-defeating. preferred_basin.csv is rewritten wholesale from the WATER CONTRACT by _make_basins, so the moment this filter drops a site, that site loses its row on the next feature build -- and a filter that could only judge sites listed there would stop seeing exactly the sites it exists to remove. They come back on the following pass, the contract grows, the rows return, they are dropped again: the cohort oscillates (measured 2026-07-31: 116 <-> 123, readmitting WQS0109, whose 0.25 coverage is documented to break every multi-bucket recipe).

    Basin parquets are never deleted when a site leaves the contract, so reading one directly breaks the cycle. basin1 is the primary NLDI delineation and is what the auto rule prefers whenever the authoritative basin0 is absent; every currently-dropped site has one.
    """
    from src.data.access import get_basin

    try:
        return get_basin(uid, type=0)  # preferred, when preferred_basin.csv still carries the site
    except (KeyError, FileNotFoundError):
        return get_basin(uid, type=1)  # contract-independent fallback -- see above


def _cell_coverage(all_sites: list[str]) -> tuple[dict[str, float], dict[str, str]]:
    """({site_uid: Sum(frac_cell_in_basin)}, {site_uid: why_unjudged}) -- the basin's area in units of covariate CELLS.

    ORDERING: this runs in the WATER build, which precedes make_features -- so until basins AND grid_global exist it judges nothing and the filter is a no-op, taking effect on the next pass. Same shape as the big_basin list: a geometry-derived criterion that can only be applied once the geometry is built. It does NOT, however, depend on the water contract -- see _coverage_basin.

    Deliberately computed from the basin polygon intersected with grid_global, via site_view's own _grid_basin_fractions, rather than by calling get_grid: it reuses the exact function the pipeline will use (so the number cannot drift from the real frac_cell_in_basin), and it needs NO per-site grid and NO D8 flow field -- which matters because the D8 walk is precisely what fails on the sites this filter exists to remove.

    Sites that cannot be measured are returned SEPARATELY rather than silently dropped from the mapping. A site with no delineation at all and a site with corrupt geometry are both kept by the caller, but only one of them is a data problem, and previously neither was distinguishable from "measured and fine".
    """
    try:
        from src.data.crs import EQUAL_AREA_CRS
        from src.data.site_view import _grid_basin_fractions, _grid_global

        gg = _grid_global()
    except Exception as e:
        print(f"  [note] grid_global not built yet ({type(e).__name__}); tiny-basin filter is a no-op.")
        return {}, {}

    cover, unjudged = {}, {}
    for uid in all_sites:
        try:
            poly = _coverage_basin(uid).to_crs(EQUAL_AREA_CRS).geometry.union_all()
        except (KeyError, FileNotFoundError):
            unjudged[uid] = "no basin delineation on disk"
            continue
        except Exception as e:  # unreadable geometry -> unjudged, so kept (a filter must not drop on an error)
            unjudged[uid] = f"{type(e).__name__}: {e}"
            continue
        members = gg[gg.geometry.intersects(poly)]
        # a basin touching no cell at all scores 0 and is dropped -- there is nothing to aggregate
        cover[uid] = float(_grid_basin_fractions(poly, members).sum()) if len(members) else 0.0
    return cover, unjudged

# detector thresholds (physical / behavioral), mirrored from the site_stats.py study
_HI = 50.0  # mg/L as N: above this is implausible for a surface nitrate sensor
_SPIKE = 3.0  # mg/L jump between consecutive samples is non-physical
_FLAT_K = 20  # >= this many identical consecutive samples counts as a flatline run
_SEG_W = 2000  # ~3 weeks at 15-min cadence: window for the worst-segment scan
_ROBUST_Z = 3.5  # cohort robust-z (median/MAD) beyond which a feature is an outlier
_ZCLIP = 8.0  # cap robust-z so a near-zero-MAD feature can't dominate the score
_FLAT_GAP_SEC = (
    2 * 3600
)  # a flat run BREAKS across a time gap wider than this (2h), so identical readings that merely bracket a data gap don't count as one continuous stuck stretch

# scrubbing (data-correction): the ONLY localized defect removed is out-of-physical-range points (< -1 or > _HI); the point's nitrate is set to NaN. Flatline/stuck-sensor scrubbing was removed -- exact-value runs over-scrubbed coarsely-quantized (esp. USGS) series, deleting real stable low-flow periods rather than stuck sensors.


# ── gap-aware flat-run detection (shared by features + scrubbing) ─────────────
def _flat_same(v: np.ndarray, dt_v: np.ndarray) -> np.ndarray:
    """Boolean per sample: continues a gap-aware constant-value run (== previous value AND the time gap to it is <= _FLAT_GAP_SEC). same[0] is always False."""
    gap_sec = np.diff(dt_v).astype("timedelta64[s]").astype(float)
    return np.concatenate([[False], (v[1:] == v[:-1]) & (gap_sec <= _FLAT_GAP_SEC)])


def _runlen(same: np.ndarray) -> np.ndarray:
    """Per-sample run length: every member of a constant-value run carries the run's total length (forward pass accumulates, backward pass broadcasts the max)."""
    n = len(same)
    runlen = np.ones(n, dtype=int)
    for i in range(1, n):
        if same[i]:
            runlen[i] = runlen[i - 1] + 1
    for i in range(n - 2, -1, -1):
        if same[i + 1]:
            runlen[i] = max(runlen[i], runlen[i + 1])
    return runlen


# ── per-site features (ported from experiments/site-discovery/site_stats.py) ──
def _site_features(g: pd.DataFrame) -> dict:
    """Per-site quantity / physical / behavioral features on the native nitrate series."""
    g = g.sort_values("datetime")
    dt = g["datetime"].to_numpy()
    v_all = pd.to_numeric(g["nitrate_con"], errors="coerce").to_numpy()
    keep = ~np.isnan(v_all)
    v = v_all[keep]
    dt_v = dt[keep]  # datetimes aligned to v, for measuring the longest stuck-flat run's DURATION
    n = len(v)
    span_yr = (dt.max() - dt.min()) / np.timedelta64(1, "s") / (365.25 * 24 * 3600) if len(dt) > 1 else 0.0
    out = dict(
        n=n,
        lifespan_yr=round(float(span_yr), 3),
        sparsity=round(n / len(g), 4) if len(g) else np.nan,  # nitrate density (== filter_sites data_count)
        mean=np.nan,
        med=np.nan,
        std=np.nan,
        vmin=np.nan,
        vmax=np.nan,
        pct_neg=np.nan,
        pct_out=np.nan,
        ac1=np.nan,
        noise=np.nan,
        roughness=np.nan,
        spike_rate=np.nan,
        flatline_frac=np.nan,
        longest_flat_days=np.nan,
        seg_noise=np.nan,
        seg_flat=np.nan,
    )
    if n < 50:
        return out
    d1 = np.diff(v)
    # gap-aware constant-value run lengths (see _flat_same/_runlen): identical readings that only bracket a data gap don't register as one continuous stuck stretch.
    runlen = _runlen(_flat_same(v, dt_v))

    # duration (days) of the longest run of one identical value -- a stuck sensor. runlen tags every member of the longest run with L, so argmax is its first index; measure end-start in real time.
    L = int(runlen.max())
    start = int(np.argmax(runlen))
    flat_days = float((dt_v[start + L - 1] - dt_v[start]) / np.timedelta64(1, "D")) if L > 1 else 0.0

    # segment-level WORST window: a bad PERIOD in an otherwise-clean series that whole-series averages wash out. Vectorized over fixed windows.
    seg_noise = seg_flat = np.nan
    if n >= 2 * _SEG_W:
        nseg = n // _SEG_W
        vt = v[: nseg * _SEG_W].reshape(nseg, _SEG_W)
        wstd = vt.std(axis=1)
        dstd = np.diff(vt, axis=1).std(axis=1)
        seg_noise = round(float(np.nanmax(np.where(wstd > 0, dstd / np.where(wstd > 0, wstd, 1), np.nan))), 4)
        rl = runlen[: nseg * _SEG_W].reshape(nseg, _SEG_W)
        seg_flat = round(float((rl.max(axis=1) / _SEG_W).max()), 4)

    out.update(
        mean=round(float(v.mean()), 3),
        med=round(float(np.median(v)), 3),
        std=round(float(v.std()), 3),
        vmin=round(float(v.min()), 3),
        vmax=round(float(v.max()), 3),
        pct_neg=round(100 * float(np.mean(v < 0)), 3),
        pct_out=round(100 * float(np.mean((v < 0) | (v > _HI))), 3),
        ac1=round(float(np.corrcoef(v[:-1], v[1:])[0, 1]), 4) if v.std() > 0 else np.nan,
        noise=round(float(d1.std() / v.std()), 4) if v.std() > 0 else np.nan,
        roughness=round(float(np.mean(np.abs(np.diff(d1)))), 4) if n > 2 else np.nan,
        spike_rate=round(100 * float(np.mean(np.abs(d1) > _SPIKE)), 3),
        flatline_frac=round(100 * float(np.mean(runlen >= _FLAT_K)), 2),
        longest_flat_days=round(flat_days, 2),
        seg_noise=seg_noise,
        seg_flat=seg_flat,
    )
    return out


def _seasonal_coherence(daily: pd.DataFrame) -> pd.Series:
    """Corr of each site's day-of-year mean profile with the cohort-mean profile. Low = no/odd seasonality. `daily` is long: site_uid, doy, val (site daily-mean nitrate)."""
    prof = daily.groupby(["site_uid", "doy"])["val"].mean().unstack("doy")
    cohort = prof.mean(axis=0)
    return prof.apply(lambda r: r.corr(cohort), axis=1).rename("seasonal_coherence")


# ── scrubbing (data correction) ───────────────────────────────────────────────
def _scrub_series(s: pd.DataFrame):
    """Remove localized defects from a raw series: out-of-physical-range points (< -1 or > _HI) have their nitrate_con set to NaN. (Flatline/stuck-sensor scrubbing was removed -- exact-value runs over-scrubbed coarsely-quantized series, deleting real stable low-flow periods rather than stuck sensors.) Returns (cleaned frame sorted by datetime, report) or (None, None) if nothing was removed. The datetime timeline is preserved; make_water's daily-max simply drops the NaNs."""
    d = s.sort_values("datetime").reset_index(drop=True)
    v_all = pd.to_numeric(d["nitrate_con"], errors="coerce").to_numpy()
    idx = np.where(~np.isnan(v_all))[0]
    if len(idx) < 2:
        return None, None
    v = v_all[idx]
    range_local = (v < -1) | (v > _HI)
    n_range = int(range_local.sum())
    if n_range == 0:
        return None, None
    remove = np.zeros(len(d), dtype=bool)
    remove[idx[range_local]] = True
    cleaned = d.copy()
    cleaned.loc[remove, "nitrate_con"] = np.nan
    return cleaned, {"n_range": n_range}


def build_features(all_sites: list[str]) -> tuple[pd.DataFrame, dict, set, dict, dict]:
    """Per-site feature table over every candidate with raw data on disk, PLUS a scrubbing pass in the same read loop. Returns (F, daily_series, nodata, cleaned, removed_days): daily_series maps uid -> daily-mean nitrate Series (raw, for the panel); nodata is candidates with no usable raw series; cleaned maps uid -> removal-count report for sites a cleaned interim file was written for; removed_days maps those uids -> the day-timestamps that had a scrubbed sample (for the panel overlay)."""
    feats, daily_rows, daily_series, nodata = {}, [], {}, set()
    cleaned, removed_days = {}, {}
    for uid in all_sites:
        s = read_raw_series(uid)
        if s is None or s["nitrate_con"].notna().sum() == 0:
            nodata.add(uid)
            continue
        feats[uid] = _site_features(s)
        d = s.dropna(subset=["nitrate_con"]).copy()
        d["day"] = d["datetime"].dt.floor("D")
        dm = d.groupby("day")["nitrate_con"].mean()
        daily_series[uid] = dm
        prof = pd.DataFrame({"site_uid": uid, "doy": dm.index.dayofyear, "val": dm.values})
        daily_rows.append(prof)

        # SCRUB localized defects -> cleaned interim file (write when something is removed, else clear any stale cleaned file so make_water doesn't read outdated data on a rerun).
        interim = interim_water_dir() / f"{uid}_water.parquet"
        cln, report = _scrub_series(s)
        if report is not None:
            cln.set_index("datetime")[["nitrate_con"]].to_parquet(interim)
            cleaned[uid] = report
            raw_sorted = s.sort_values("datetime").reset_index(drop=True)
            gone = raw_sorted["nitrate_con"].notna().to_numpy() & cln["nitrate_con"].isna().to_numpy()
            removed_days[uid] = pd.DatetimeIndex(raw_sorted.loc[gone, "datetime"]).floor("D").unique()
        elif interim.exists():
            interim.unlink()

    F = pd.DataFrame(feats).T
    F.index.name = "site_uid"
    if daily_rows:
        F = F.join(_seasonal_coherence(pd.concat(daily_rows, ignore_index=True)))
    if "seasonal_coherence" not in F.columns:
        F["seasonal_coherence"] = np.nan

    # cohort robust-z (median/MAD), CLIPPED so a near-zero-MAD feature can't blow up the score
    for col in ["mean", "std", "noise", "ac1", "seasonal_coherence", "flatline_frac", "seg_noise", "seg_flat"]:
        x = pd.to_numeric(F[col], errors="coerce")
        mad = (x - x.median()).abs().median() or 1e-9
        F[f"z_{col}"] = ((x - x.median()) / (1.4826 * mad)).clip(-_ZCLIP, _ZCLIP).round(2)
    return F, daily_series, nodata, cleaned, removed_days


# ── corruption detector (high-recall) ─────────────────────────────────────────
_SIGNALS = [
    "range_violation",
    "dead_autocorr",
    "extreme_noise",
    "noisy",
    "scale_outlier",
    "flatline_heavy",
    "seg_bad",
    "aseasonal",
]


def detect(F: pd.DataFrame) -> pd.DataFrame:
    """Per-signal corruption flags + a composite score. `flagged` is deliberately HIGH-recall: any single signal firing flags the site (the human prunes false positives on the panel)."""
    z = lambda c: pd.to_numeric(F.get(f"z_{c}"), errors="coerce").fillna(0)
    num = lambda c: pd.to_numeric(F[c], errors="coerce")
    pos = lambda c, s=1: (s * z(c)).clip(lower=0)
    sig = pd.DataFrame(index=F.index)

    # High-confidence signals -- unambiguously broken.
    sig["range_violation"] = num("pct_out") > 0.5
    sig["dead_autocorr"] = num("ac1") < 0.9  # nitrate is highly persistent; this low = noise, not signal
    sig["extreme_noise"] = num("noise") > 0.25
    # Graded signals -- fire when a cohort-outlier.
    sig["noisy"] = z("noise") > _ROBUST_Z
    sig["scale_outlier"] = (z("mean").abs() > _ROBUST_Z) | (z("std").abs() > _ROBUST_Z)
    sig["flatline_heavy"] = z("flatline_frac") > _ROBUST_Z + 1
    sig["seg_bad"] = (z("seg_noise") > _ROBUST_Z + 1) | (z("seg_flat") > _ROBUST_Z + 1)
    sig["aseasonal"] = (num("seasonal_coherence") < 0.2) & (num("lifespan_yr") > 2)  # guard short records

    F_score = (
        pos("noise")
        + pos("ac1", -1)
        + pos("flatline_frac")
        + pos("seasonal_coherence", -1)
        + pos("seg_noise")
        + pos("seg_flat")
        + z("mean").abs().clip(upper=_ZCLIP) * 0.5
        + 5 * sig["range_violation"].astype(float)
        + 5 * sig["dead_autocorr"].astype(float)
    )
    sig["score"] = F_score.round(2)
    sig["rank"] = sig["score"].rank(ascending=False, method="min")
    # HIGH-RECALL flag: any signal fires. (Precision is the human's job via the panel.)
    sig["flagged"] = sig[_SIGNALS].any(axis=1)
    return sig


# ── review panel ──────────────────────────────────────────────────────────────
def _stats_text(uid, F, sig, short_set, cleaned) -> str:
    r = F.loc[uid]
    fired = [c for c in _SIGNALS if sig.loc[uid, c]] if uid in sig.index else []
    tags = []
    if uid in KNOWN_BAD:
        tags.append("KNOWN_BAD")
    if uid in short_set:
        tags.append("short")
    lines = [
        uid + ("  [" + ", ".join(tags) + "]" if tags else ""),
        f"n={int(r['n']):>7}   lifespan={r['lifespan_yr']}y",
        f"sparsity={r['sparsity']}   mean={r['mean']}  std={r['std']}",
        f"noise={r['noise']}   ac1={r['ac1']}",
        f"flatline%={r['flatline_frac']}   longest_flat={r['longest_flat_days']}d",
        f"seas_coh={round(float(r['seasonal_coherence']), 3) if pd.notna(r['seasonal_coherence']) else 'nan'}",
        f"seg_noise={r['seg_noise']}  seg_flat={r['seg_flat']}",
        "signals: " + (", ".join(fired) or "(none)"),
    ]
    if uid in cleaned:
        c = cleaned[uid]
        lines.append(f"SCRUBBED: {c['n_range']} out-of-range")
    return "\n".join(lines)


# signals no amount of row-scrubbing can fix -- a whole-series/shape verdict (see the scrubbable taxonomy)
_UNRECOVERABLE = {"dead_autocorr", "extreme_noise", "noisy", "scale_outlier", "aseasonal"}
_REC_COLOR = {"keep as is": "#2ca02c", "clean and keep": "#e08214", "record as bad": "crimson"}


def _recommendation(uid, fired: set, cleaned: dict) -> str:
    """Per-site verdict for the review panel: 'record as bad' if any unrecoverable whole-series signal fired (scrubbing can't help -> promote to KNOWN_BAD); else 'clean and keep' if localized defects were scrubbed into interim/water; else 'keep as is'."""
    if fired & _UNRECOVERABLE:
        return "record as bad"
    if uid in cleaned:
        return "clean and keep"
    return "keep as is"


def _longest_flat_span(s: pd.Series):
    """Index bounds (start, end) of the longest run of equal consecutive non-nan values in a daily series -- the visible stuck-sensor stretch, for highlighting. NaN (gap) days break a run. None if no run of length >= 2."""
    vals = s.to_numpy()
    best = (0, 0)
    cur = None
    for i in range(1, len(vals)):
        if not (np.isnan(vals[i]) or np.isnan(vals[i - 1])) and vals[i] == vals[i - 1]:
            cur = i - 1 if cur is None else cur
            if i - cur > best[1] - best[0]:
                best = (cur, i)
        else:
            cur = None
    return best if best[1] > best[0] else None


def render_panel(review, F, sig, daily_series, short_set, cleaned, removed_days, path) -> None:
    """One row per reviewed site: [daily nitrate timeseries | value histogram | stats table]. Scrubbed days (removed_days) are marked with grey x's on the timeseries."""
    if not review:
        print("  (nothing to review -- no flagged sites; panel skipped.)")
        return
    nrow = len(review)
    fig, axes = plt.subplots(
        nrow, 3, figsize=(13, 1.7 * nrow), squeeze=False, gridspec_kw={"width_ratios": [3, 1.4, 2.2]}
    )
    for i, uid in enumerate(review):
        ax_ts, ax_hist, ax_txt = axes[i]
        s = daily_series.get(uid, pd.Series(dtype=float))
        # reindex to a full daily calendar so genuine gaps become NaN and matplotlib BREAKS the line there, rather than drawing a straight segment across the hole (which reads as interpolation).
        if len(s):
            s = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D"))
        color = "crimson" if uid in KNOWN_BAD else ("#ee6677" if sig.loc[uid, "flagged"] else "#4477aa")
        ax_ts.plot(s.index, s.values, lw=0.5, c=color)
        # highlight the longest stuck-flat stretch so a short flat at a low value can't hide in a multi-year trace (the number in the stats block is longest_flat_days).
        flat = _longest_flat_span(s)
        if flat is not None:
            fs = s.iloc[flat[0] : flat[1] + 1]
            ax_ts.plot(fs.index, fs.values, lw=3, c="orange", solid_capstyle="butt", zorder=5)
        # mark days a scrub removed samples on, so a human can see where correction acted
        rd = removed_days.get(uid)
        if rd is not None and len(rd) and len(s):
            marks = s.reindex(rd).dropna()
            ax_ts.scatter(marks.index, marks.values, s=9, c="#888888", marker="x", lw=0.6, zorder=6)
        ax_ts.set_ylabel(uid, fontsize=7)
        ax_ts.tick_params(labelsize=6)
        vals = s.values[~np.isnan(s.values)]
        if len(vals):
            ax_hist.hist(vals, bins=25, color=color, edgecolor="white")
        ax_hist.tick_params(labelsize=6)
        ax_hist.set_title("value dist.", fontsize=6)
        ax_txt.axis("off")
        ax_txt.text(
            0,
            1,
            _stats_text(uid, F, sig, short_set, cleaned),
            fontsize=6.5,
            va="top",
            family="monospace",
            transform=ax_txt.transAxes,
        )
        fired = {c for c in _SIGNALS if uid in sig.index and sig.loc[uid, c]}
        rec = _recommendation(uid, fired, cleaned)
        ax_txt.text(
            0,
            0.0,
            f"Recommend: {rec}",
            fontsize=8,
            fontweight="bold",
            va="bottom",
            family="monospace",
            color=_REC_COLOR[rec],
            transform=ax_txt.transAxes,
        )
    axes[0][0].set_title("daily-mean nitrate (mg/L as N)", fontsize=8)
    # reserve a fixed ~0.9in band at the top for the title (a fixed FRACTION collapses on tall panels, letting the suptitle land inside the first row); place the title inside that band.
    fig_h = 1.7 * nrow
    fig.tight_layout(rect=[0, 0, 1, 1 - 0.9 / fig_h])
    fig.suptitle(
        f"Water quality review — {nrow} sites (red=known_bad, pink=flagged, blue=reference)",
        fontsize=11,
        y=1 - 0.45 / fig_h,
    )
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ── driver ────────────────────────────────────────────────────────────────────
def filter_sites(plots: bool = True) -> dict:
    site_list = pd.read_csv(raw_site_list_path(), dtype={"site_uid": str})
    all_sites = sorted(site_list["site_uid"].astype(str))
    print(f"Filtering {len(all_sites)} candidate sites from {raw_site_list_path().name}...")

    F, daily_series, nodata, cleaned, removed_days = build_features(all_sites)

    # SHORT (quantity): too few non-nan nitrate rows to keep. `n` is exactly that count; nodata sites (no raw parquet, absent from F) count as 0 and so are always short.
    n_rows = pd.to_numeric(F["n"], errors="coerce")
    short = set(F.index[~(n_rows > MIN_NITRATE_ROWS)]) | set(nodata)

    # FLAGGED (quality): high-recall corruption detector.
    sig = detect(F)
    flagged = set(sig.index[sig["flagged"]])

    # OVERSIZED (size): hub basins excluded for LOFO health. Intersected with the candidate universe so a stale config entry can't silently claim to have excluded a site that isn't here.
    oversized = set(_oversized()) & set(all_sites)

    # TINY (size): basins owning less than MIN_CELL_COVERAGE of one covariate cell, where the aggregation has nothing real to measure. Judged from the basin polygon on disk, NOT from the water contract, so a site this filter drops stays judgeable on the next pass -- see _coverage_basin. Sites with no delineation at all are unjudged and therefore kept, but they are reported rather than silently indistinguishable from measured-and-fine.
    coverage, unjudged = _cell_coverage(all_sites)
    tiny = {u for u, c in coverage.items() if c < MIN_CELL_COVERAGE}
    if unjudged:
        print(f"  [warn] {len(unjudged)} site(s) could not be size-judged and are KEPT: "
              f"{', '.join(sorted(unjudged)[:6])}{' ...' if len(unjudged) > 6 else ''}")

    # GOOD = all - short - KNOWN_BAD - oversized - tiny  (flagged is advisory, NOT subtracted here).
    good = set(all_sites) - short - set(KNOWN_BAD) - oversized - tiny

    # recommended KNOWN_BAD = flagged sites the review verdict marks 'record as bad' (an unrecoverable whole-series signal fired, so scrubbing can't fix them), excluding ones already in KNOWN_BAD.
    fired_of = lambda u: {c for c in _SIGNALS if u in sig.index and sig.loc[u, c]}
    rec_bad = sorted(
        u for u in flagged if u not in KNOWN_BAD and _recommendation(u, fired_of(u), cleaned) == "record as bad"
    )

    result = {
        "good_sites": sorted(good),
        "flagged_sites": sorted(flagged),
        "short_sites": sorted(short),
        # hub basins excluded for LOFO health (pipeline_config [site_filters].big_basin). Their raw per-site parquets are still downloaded and retained -- only the processed/derived products are skipped -- so re-including one is a filter/make_water/make_features cycle, no refetch.
        "oversized_sites": sorted(oversized),
        # basins below MIN_CELL_COVERAGE -- they own less than half a covariate cell, so their aggregated covariates are slivers of cells lying mostly outside the basin. uid -> cell coverage, so the reason a site was dropped is legible without recomputing geometry.
        "tiny_sites": {u: round(coverage[u], 3) for u in sorted(tiny)},
        # sites the size filter could NOT measure (no delineation on disk, or unreadable geometry) -- kept by default, but recorded so "unjudged" is never mistaken for "judged and fine". An entry here means the basin build has a hole.
        "unjudged_sites": {u: unjudged[u] for u in sorted(unjudged)},
        # sites whose localized defects were auto-scrubbed into interim/water (uid -> removal counts); make_water reads the cleaned file for these.
        "cleaned_sites": {u: cleaned[u] for u in sorted(cleaned)},
        # flagged sites the review recommends ADDING to KNOWN_BAD (unrecoverable corruption; sites already in KNOWN_BAD are excluded, hence "additions")
        "recommended_bad_additions": rec_bad,
    }
    with open(filtered_sites_path(), "w") as f:
        json.dump(result, f, indent=2)

    print("\n== FILTER SUMMARY ==")
    print(f"  candidates:      {len(all_sites)}")
    print(
        f"  short (quantity): {len(short)}  (<= {MIN_NITRATE_ROWS:,} non-nan nitrate rows, incl. {len(nodata)} with no raw data)"
    )
    print(
        f"  flagged (quality): {len(flagged)}  (high-recall; per-signal: {sig[_SIGNALS].sum().astype(int).to_dict()})"
    )
    print(f"  KNOWN_BAD:        {len(KNOWN_BAD)}")
    print(f"  oversized (size): {len(oversized)}  (hub basins, config [site_filters].big_basin: {sorted(oversized)})")
    if coverage:
        print(
            f"  tiny (coverage):  {len(tiny)}  (< {MIN_CELL_COVERAGE:g} cells of basin area, of "
            f"{len(coverage)} basins measured: { {u: round(coverage[u], 3) for u in sorted(tiny)} })"
        )
    else:
        print("  tiny (coverage):  0  (basins/grid_global not built yet -- filter is a no-op this pass)")
    stale = set(_oversized()) - set(all_sites)
    if stale:
        print(f"    [note] {len(stale)} big_basin entries are not in the candidate list (no-ops): {sorted(stale)}")
    if cleaned:
        tot_range = sum(c["n_range"] for c in cleaned.values())
        print(
            f"  scrubbed:         {len(cleaned)} site(s) cleaned -> interim/water ({tot_range:,} out-of-range samples removed)"
        )
    print(f"  GOOD:             {len(good)}  = candidates - short - KNOWN_BAD - oversized - tiny")
    kb_caught = sorted(set(KNOWN_BAD) & flagged)
    print(f"  detector recall on KNOWN_BAD: {len(kb_caught)}/{len(KNOWN_BAD)}")
    print(f"  wrote {filtered_sites_path()}")

    if plots:
        # review = flagged sites (most-corrupt first) + a few clean references for calibration
        review = sorted(flagged, key=lambda u: sig.loc[u, "rank"])
        refs = [
            u for u in sorted(good - flagged, key=lambda u: sig.loc[u, "rank"] if u in sig.index else 0, reverse=True)
        ][:3]
        render_panel(review + refs, F, sig, daily_series, short, cleaned, removed_days, review_panel_path())
        print(f"  wrote {review_panel_path()}  ({len(review)} flagged + {len(refs)} reference rows)")

    # per-site scrub detail: what was removed and why (largest first)
    if cleaned:
        print("\n== SCRUBBED SITES (out-of-range samples removed -> interim/water) ==")
        for u in sorted(cleaned, key=lambda u: -cleaned[u]["n_range"]):
            c = cleaned[u]
            print(f"  {u:<14} {c['n_range']:>9,} out-of-range rows deleted")

    # copy-pasteable hand-offs: the advisory flagged set, then the sites to actually record as bad
    print("\n" + "=" * 78)
    print("REVIEW review_panel.png. Two lists follow (copy-paste).")
    print("\n# every site tripping a quality signal (advisory -- includes fixable/scrubbed ones):")
    print(f"flagged_sites = {sorted(flagged)}")
    print("\n# RECOMMENDED additions to KNOWN_BAD: unrecoverable corruption the panel marks 'record as")
    print("# bad' (sites already in KNOWN_BAD are excluded). Paste these into KNOWN_BAD, then rerun:")
    print(f"recommended_bad_additions = {rec_bad}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args()
    filter_sites(plots=not args.no_plots)
