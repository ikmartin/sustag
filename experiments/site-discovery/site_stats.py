"""site_stats.py -- QA report + programmatic corruption detector for the fetch_site_list manifest.

Prints short CLI reports and saves graphs for the IWQIS nitrate candidate sites, and -- the point --
flags corrupted sites PROGRAMMATICALLY so the hand-curated `known_bad` list in pipeline_config.toml
can be replaced by inspection of a short shortlist instead of the whole cohort.

Two things learned from the data, which shape this tool:
  * Lifespan / sparsity are QUANTITY, not quality. A short but clean series is usable (XGBoost pools
    cross-site), so these are REPORTED, never used as a hard filter here.
  * Corruption is heterogeneous and `known_bad` is a lower bound, not ground truth. No single feature
    separates the bad sites: behavioral noise/autocorr catch the jittery ones (WQS9904/0075/0090/...),
    but some labeled sites look clean by those (WQS0113/0045/9901 -> caught by range/scale/flatline or
    the multivariate backstop), and some UNLABELED sites (WQS9931/9996/0068) look just as bad. So the
    detector is multi-signal + surfaces new suspects + ends in a human-confirmation panel.

Reasons to drop a site, BEYOND lifespan/sparsity (this tool automates the first; the rest are noted):
  * sensor corruption / behavioral anomaly (noise, no autocorr, steps)   <-- detected here
  * physical-range violations (negative, or absurd highs)                 <-- detected here
  * flatlined / stuck sensor / interpolation artifacts                    <-- detected here
  * wrong scale / units (cohort scale outlier)                            <-- detected here
  * absent / inverted seasonality (no spring-flush)                       <-- detected here
  * duplicate / aliased series (a copy of another site)
  * ungaugeable geography: big_basin (too large to delineate), groundwater (handled upstream)
  * co-location redundancy (a USGS and IWQIS uid for one physical site)
  * record entirely outside covariate coverage (surplus ends 2017)

Scope: IWQIS only (that is where the labels + full raw data live). USGS sites not already on disk
would need a dataretrieval pull -- out of scope for v1.

    python site_stats.py               # full report + plots + proposed_known_bad.csv
    python site_stats.py --recompute   # ignore the cached feature table
    python site_stats.py --no-plots
"""

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from src.build.config import get_config
from src.build.util import iwqis

_FEAT_CACHE = _HERE / "site_features.csv"
_MANIFEST_CSV = _HERE / "site_list.csv"

# physical / behavioral thresholds
_HI = 45.0  # mg/L as N: above this is implausible for a surface nitrate sensor
_SPIKE = 3.0  # mg/L jump between consecutive 15-min samples is non-physical
_FLAT_K = 20  # >= this many identical consecutive samples counts as a flatline run
_SEG_W = 2000  # ~3 weeks at 15-min cadence: window for the worst-segment scan
_ROBUST_Z = 3.5  # cohort robust-z (median/MAD) beyond which a feature is an outlier
_ZCLIP = 8.0  # cap robust-z so a near-zero-MAD feature can't dominate the score
_SHORTLIST_K = 25  # size of the ranked shortlist a human confirms (vs eyeballing all 160)


# ── feature computation ───────────────────────────────────────────────────────
def _site_features(g: pd.DataFrame) -> dict:
    """Per-site quantity / physical / behavioral features on the native (15-min) nitrate series."""
    g = g.sort_values("datetime")
    dt = g["datetime"].to_numpy()
    v_all = pd.to_numeric(g["nitrate_con"], errors="coerce").to_numpy()
    v = v_all[~np.isnan(v_all)]
    n = len(v)
    span_yr = (dt.max() - dt.min()) / np.timedelta64(1, "s") / (365.25 * 24 * 3600) if len(dt) > 1 else 0.0
    out = dict(
        n=n,
        lifespan_yr=round(float(span_yr), 3),
        sparsity=round(n / len(g), 4) if len(g) else np.nan,  # nitrate density (== filter_sites data_count)
        mean=np.nan, med=np.nan, std=np.nan, vmin=np.nan, vmax=np.nan,
        pct_neg=np.nan, pct_out=np.nan, ac1=np.nan, noise=np.nan,
        roughness=np.nan, spike_rate=np.nan, flatline_frac=np.nan,
        seg_noise=np.nan, seg_flat=np.nan,
    )
    if n < 50:
        return out
    d1 = np.diff(v)
    # per-sample run length: every member of a run of identical values carries the run's total length
    same = np.concatenate([[False], v[1:] == v[:-1]])
    runlen = np.ones(n, dtype=int)
    for i in range(1, n):
        if same[i]:
            runlen[i] = runlen[i - 1] + 1
    for i in range(n - 2, -1, -1):
        if same[i + 1]:
            runlen[i] = max(runlen[i], runlen[i + 1])

    # segment-level WORST window: a bad PERIOD inside an otherwise-clean series -- catches the subtle
    # corruption (e.g. a stuck/glitched stretch) that whole-series averages wash out. Vectorized over
    # fixed windows (~3 weeks at 15-min cadence).
    seg_noise = seg_flat = np.nan
    if n >= 2 * _SEG_W:
        nseg = n // _SEG_W
        vt = v[: nseg * _SEG_W].reshape(nseg, _SEG_W)
        wstd = vt.std(axis=1)
        dstd = np.diff(vt, axis=1).std(axis=1)
        seg_noise = round(float(np.nanmax(np.where(wstd > 0, dstd / np.where(wstd > 0, wstd, 1), np.nan))), 4)
        rl = runlen[: nseg * _SEG_W].reshape(nseg, _SEG_W)
        seg_flat = round(float((rl.max(axis=1) / _SEG_W).max()), 4)  # worst window's flatline share

    out.update(
        mean=round(float(v.mean()), 3), med=round(float(np.median(v)), 3), std=round(float(v.std()), 3),
        vmin=round(float(v.min()), 3), vmax=round(float(v.max()), 3),
        pct_neg=round(100 * float(np.mean(v < 0)), 3),
        pct_out=round(100 * float(np.mean((v < 0) | (v > _HI))), 3),
        ac1=round(float(np.corrcoef(v[:-1], v[1:])[0, 1]), 4) if v.std() > 0 else np.nan,
        noise=round(float(d1.std() / v.std()), 4) if v.std() > 0 else np.nan,
        roughness=round(float(np.mean(np.abs(np.diff(d1)))), 4) if n > 2 else np.nan,
        spike_rate=round(100 * float(np.mean(np.abs(d1) > _SPIKE)), 3),
        flatline_frac=round(100 * float(np.mean(runlen >= _FLAT_K)), 2),
        seg_noise=seg_noise, seg_flat=seg_flat,
    )
    return out


def _seasonal_coherence(daily: pd.DataFrame) -> pd.Series:
    """Corr of each site's day-of-year mean profile with the cohort-mean profile. Low = no/odd
    seasonality. `daily` is long: site_uid, doy, val (site daily-mean nitrate)."""
    prof = daily.groupby(["site_uid", "doy"])["val"].mean().unstack("doy")  # site x doy
    cohort = prof.mean(axis=0)  # cohort seasonal profile
    return prof.apply(lambda r: r.corr(cohort), axis=1).rename("seasonal_coherence")


def build_features(recompute: bool = False) -> pd.DataFrame:
    """Feature table over every IWQIS site in _full_data (cached to site_features.csv). Tags manifest
    membership and known_bad. The _full_data load is a slow 42M-row reassembly, so cache aggressively."""
    if _FEAT_CACHE.exists() and not recompute:
        return pd.read_csv(_FEAT_CACHE, dtype={"site_uid": str}).set_index("site_uid")

    print("Loading IWQIS full data (slow: 42M-row reassembly)...")
    d = iwqis._full_data().copy()
    d["nitrate_con"] = pd.to_numeric(d["nitrate_con"], errors="coerce")
    d["datetime"] = pd.to_datetime(d["datetime"], utc=True)

    print("Computing per-site features...")
    feats = {uid: _site_features(g) for uid, g in d.groupby("site_uid", sort=False)}
    F = pd.DataFrame(feats).T
    F.index.name = "site_uid"

    # seasonal coherence from daily-mean series
    valid = d.dropna(subset=["nitrate_con"]).copy()
    valid["day"] = valid["datetime"].dt.floor("D")
    daily = valid.groupby(["site_uid", "day"])["nitrate_con"].mean().reset_index(name="val")
    daily["doy"] = daily["day"].dt.dayofyear
    F = F.join(_seasonal_coherence(daily))

    # cohort robust-z (median/MAD), CLIPPED so a near-zero-MAD feature can't blow up the score
    for col in ["mean", "std", "noise", "ac1", "seasonal_coherence", "flatline_frac", "seg_noise", "seg_flat"]:
        x = pd.to_numeric(F[col], errors="coerce")
        mad = (x - x.median()).abs().median() or 1e-9
        F[f"z_{col}"] = ((x - x.median()) / (1.4826 * mad)).clip(-_ZCLIP, _ZCLIP).round(2)

    kb = set(get_config()["site_filters"]["known_bad"])
    F["known_bad"] = F.index.isin(kb)
    manifest = set(pd.read_csv(_MANIFEST_CSV, dtype={"site_uid": str})["site_uid"]) if _MANIFEST_CSV.exists() else set(F.index)
    F["in_manifest"] = F.index.isin(manifest)

    F.reset_index().to_csv(_FEAT_CACHE, index=False)
    print(f"  cached {len(F)} sites -> {_FEAT_CACHE.name}")
    return F


# ── corruption detector ───────────────────────────────────────────────────────
def detect(F: pd.DataFrame) -> pd.DataFrame:
    """Corruption signals + a composite ranked SCORE. Returns per-signal booleans, `score`, `rank`,
    and `flagged`. Whole-series stats can't cleanly separate the subtle bad sites, so the primary
    output is a ranked shortlist (score) plus high-confidence auto-flags for the obvious cases."""
    z = lambda c: pd.to_numeric(F.get(f"z_{c}"), errors="coerce").fillna(0)
    num = lambda c: pd.to_numeric(F[c], errors="coerce")
    pos = lambda c, s=1: (s * z(c)).clip(lower=0)  # only anomalies in the CORRUPT direction
    sig = pd.DataFrame(index=F.index)

    # High-confidence signals -- auto-flag regardless of rank (unambiguously broken).
    sig["range_violation"] = num("pct_out") > 0.5  # negatives / implausible highs
    sig["dead_autocorr"] = num("ac1") < 0.9  # nitrate is highly persistent; this low = noise, not signal
    sig["extreme_noise"] = num("noise") > 0.25  # gross jitter
    # Graded signals -- contribute to the score, fire when a cohort-outlier.
    sig["noisy"] = z("noise") > _ROBUST_Z
    sig["scale_outlier"] = (z("mean").abs() > _ROBUST_Z) | (z("std").abs() > _ROBUST_Z)
    sig["flatline_heavy"] = z("flatline_frac") > _ROBUST_Z + 1
    sig["seg_bad"] = (z("seg_noise") > _ROBUST_Z + 1) | (z("seg_flat") > _ROBUST_Z + 1)  # bad segment
    sig["aseasonal"] = (num("seasonal_coherence") < 0.2) & (num("lifespan_yr") > 2)  # guard short records

    # Composite corruption score: clipped robust-z summed over corrupt-direction features + a bump for
    # the hard signals. Ranks the whole cohort; NaN-feature (very short) series get score 0.
    F_score = (
        pos("noise") + pos("ac1", -1) + pos("flatline_frac") + pos("seasonal_coherence", -1)
        + pos("seg_noise") + pos("seg_flat") + z("mean").abs().clip(upper=_ZCLIP) * 0.5
        + 5 * sig["range_violation"].astype(float) + 5 * sig["dead_autocorr"].astype(float)
    )
    sig["score"] = F_score.round(2)
    sig["rank"] = sig["score"].rank(ascending=False, method="min")

    # Shortlist for human review = the top-K most-corrupt by score PLUS any strong-signal auto-flag.
    # (A fixed top-K, not a score-outlier cutoff: whole-series stats don't cleanly separate the subtle
    # bad sites, so this is a triage ranking to confirm on the panel, not a precise classifier.)
    strong = sig["range_violation"] | sig["dead_autocorr"] | sig["extreme_noise"]
    sig["flagged"] = strong | (sig["rank"] <= _SHORTLIST_K)
    return sig


# ── reports + plots ───────────────────────────────────────────────────────────
def _hist(x, cutoff, title, xlabel, path):
    x = pd.to_numeric(x, errors="coerce").dropna()
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(x, bins=30, color="#4477aa", edgecolor="white")
    ax.axvline(cutoff, color="crimson", ls="--", lw=1.5, label=f"current cutoff = {cutoff}")
    ax.set_title(title); ax.set_xlabel(xlabel); ax.set_ylabel("sites"); ax.legend()
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def report_quantity(F: pd.DataFrame, cfg, plots: bool):
    m = F[F["in_manifest"]]
    spars_cut, life_cut = cfg["iwqis"]["sparsity_cutoff"], cfg["iwqis"]["lifespan_cutoff"]
    life, spars = pd.to_numeric(m["lifespan_yr"], errors="coerce"), pd.to_numeric(m["sparsity"], errors="coerce")
    print("\n== LIFESPAN & SPARSITY (quantity, not quality) ==")
    print(f"  {len(m)} manifest IWQIS sites | lifespan yr: median {life.median():.1f} (min {life.min():.1f}, max {life.max():.1f})")
    print(f"  sparsity (nitrate density): median {spars.median():.2f} (min {spars.min():.2f}, max {spars.max():.2f})")
    print(f"  current cutoffs would drop {(life < life_cut).sum()} on lifespan<{life_cut}, {(spars < spars_cut).sum()} on sparsity<{spars_cut} "
          f"({((life < life_cut) | (spars < spars_cut)).sum()} total) -- reported, NOT applied here")
    if plots:
        _hist(life, life_cut, "IWQIS site lifespans", "lifespan (years)", _HERE / "lifespan_hist.png")
        _hist(spars, spars_cut, "IWQIS nitrate sparsity (density)", "fraction of rows with nitrate", _HERE / "sparsity_hist.png")
        print(f"  wrote lifespan_hist.png, sparsity_hist.png")


def report_corruption(F: pd.DataFrame, sig: pd.DataFrame, plots: bool):
    kb = list(F.index[F["known_bad"]])
    flagged = list(sig.index[sig["flagged"]])
    signals = [c for c in sig.columns if c not in ("flagged", "score", "rank")]

    print("\n== CORRUPTION DETECTOR (ranked shortlist) ==")
    print(f"  flagged {len(flagged)} of {len(F)} IWQIS sites  (per-signal: {sig[signals].sum().astype(int).to_dict()})")
    caught = [s for s in kb if s in flagged]
    print(f"  recall on config known_bad: {len(caught)}/{len(kb)}  (ranks shown below; lower = more corrupt)")
    print("  known_bad site | rank/160 | signals fired:")
    for s in sorted(kb, key=lambda s: sig.loc[s, "rank"] if s in sig.index else 1e9):
        if s not in sig.index:
            print(f"    {s:<9}   <no data>"); continue
        fired = [c for c in signals if sig.loc[s, c]]
        tag = "" if s in flagged else "   <-- NOT flagged (statistically ~ a clean site; needs manual/segment)"
        print(f"    {s:<9}  #{int(sig.loc[s,'rank']):>3}  {', '.join(fired) or '(none)'}{tag}")

    new = [s for s in flagged if s not in set(kb) and F.loc[s, "in_manifest"]]
    print(f"  NEW suspects (flagged, not in known_bad): {len(new)}  -- top by rank:")
    for s in sorted(new, key=lambda s: sig.loc[s, "rank"])[:20]:
        fired = [c for c in signals if sig.loc[s, c]]
        print(f"    {s:<9}  #{int(sig.loc[s,'rank']):>3}  noise={F.loc[s,'noise']} ac1={F.loc[s,'ac1']} seg_flat={F.loc[s,'seg_flat']}  <- {', '.join(fired)}")

    proposed = sorted(set(flagged) & set(F.index[F["in_manifest"]]), key=lambda s: sig.loc[s, "rank"])
    prop = pd.DataFrame({"site_uid": proposed})
    prop["rank"] = [int(sig.loc[s, "rank"]) for s in proposed]
    prop["in_config_known_bad"] = prop["site_uid"].isin(set(kb))
    prop.sort_values("rank").to_csv(_HERE / "proposed_known_bad.csv", index=False)
    missed = sorted(set(kb) - set(flagged))
    print(f"  wrote proposed_known_bad.csv ({len(proposed)} sites, rank-sorted).")
    print(f"  config known_bad NOT re-flagged: {missed or 'none'} "
          f"(whole-series stats can't see these -> the confirmation panel / a segment review is the backstop)")

    if plots:
        _scatter(F, sig, _HERE / "behavioral_scatter.png")
        _confirm_panel(sorted(set(flagged) | set(kb)), _HERE / "confirm_panel.png")
        print("  wrote behavioral_scatter.png, confirm_panel.png")


def _scatter(F, sig, path):
    x, y = pd.to_numeric(F["ac1"], errors="coerce"), pd.to_numeric(F["noise"], errors="coerce")
    kb, fl = F["known_bad"], sig["flagged"] & ~F["known_bad"]
    normal = ~kb & ~fl
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(x[normal], y[normal], s=14, c="#bbbbbb", label="normal")
    ax.scatter(x[fl], y[fl], s=34, c="#ee6677", label="flagged (new)", edgecolor="k", lw=0.3)
    ax.scatter(x[kb], y[kb], s=44, c="#4477aa", marker="D", label="known_bad", edgecolor="k", lw=0.3)
    ax.set_xlabel("lag-1 autocorrelation"); ax.set_ylabel("noise = std(Δ)/std")
    ax.set_title("IWQIS site behavior (corruption ↗ up-left)"); ax.legend()
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def _confirm_panel(sites, path):
    """Small-multiples daily-mean nitrate for the shortlist (+ 3 normals), for human confirmation."""
    d = iwqis._full_data()
    d = d[d["site_uid"].isin(set(sites) | {"WQS0001", "WQS0003", "WQS0005"})].copy()
    d["nitrate_con"] = pd.to_numeric(d["nitrate_con"], errors="coerce")
    d["day"] = pd.to_datetime(d["datetime"], utc=True).dt.floor("D")
    daily = d.dropna(subset=["nitrate_con"]).groupby(["site_uid", "day"])["nitrate_con"].mean()
    panel = list(sites) + ["WQS0001", "WQS0003", "WQS0005"]
    ncol = 5
    nrow = -(-len(panel) // ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(3 * ncol, 1.8 * nrow), squeeze=False)
    for ax, uid in zip(axes.ravel(), panel):
        s = daily.loc[uid] if uid in daily.index.get_level_values(0) else pd.Series(dtype=float)
        ax.plot(s.index, s.values, lw=0.5, c="crimson" if uid in set(sites) else "#4477aa")
        ax.set_title(uid, fontsize=8); ax.tick_params(labelsize=6)
    for ax in axes.ravel()[len(panel):]:
        ax.axis("off")
    fig.suptitle("Daily-mean nitrate — red = flagged/known_bad, blue = normal reference", fontsize=10)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recompute", action="store_true", help="ignore the cached feature table")
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args()

    F = build_features(recompute=args.recompute)
    cfg = get_config()
    report_quantity(F, cfg, plots=not args.no_plots)
    sig = detect(F)
    report_corruption(F, sig, plots=not args.no_plots)


if __name__ == "__main__":
    main()
