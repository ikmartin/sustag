"""The human review artifact for series quality: one tall PNG, one row per flagged gauge.

THE LAST GATE, AND THE ONLY ONE ABOUT THE MEASUREMENT ITSELF. Chunk 3 asks what a site IS and which registrations are one gauge; chunk 4 asks which delineation to believe; this asks whether the nitrate is real. Nothing downstream can tell a broken sensor from unusual hydrology, so if it is not decided here it is not decided at all.

ONLY GAUGES THAT PASSED EVERY OTHER FILTER ARE REVIEWED. No well, no estuary, no short record, no far snap ever reaches this panel -- it is a review of series that would otherwise be trained on, which is what keeps it short. Reviewing a series that was already excluded for its type would be asking a person to judge data nobody will use.

A MERGED PAIR IS ONE ROW. The series are concatenated before anything is measured, so the seam between two registrations is not a defect and a fault spanning the join is visible. `quality.assess` does the merging; this only draws it.

TWO VIEWS OF THE SAME SERIES, because the failures live at different scales. The full record shows a level shift, a dead stretch or a drifting baseline; the worst-segment zoom shows the sampling-interval behaviour -- spikes, flatlines, noise -- that a decade-wide plot renders as a solid band. `seg_noise` is what nominates the window, so the reviewer is looking at the evidence the detector actually scored.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import _panel, config
from .._panel import A_COLOR, AXIS, INK, INK_2, MUTED, SURFACE, fmt
from . import quality

SIGNAL_WHAT = {
    "range_violation": "negative readings survived scrubbing",
    "dead_autocorr": "consecutive samples barely correlate -- noise, not a river",
    "extreme_noise": "step-to-step variation is a quarter of the series' own spread",
    "stuck_impossible": "a run of readings below -1 mg/L -- a failed sensor, not a river",
    "noisy": "noisier than the cohort by more than 3.5 robust-z",
    "scale_outlier": "its mean or spread is a cohort outlier",
    "flatline_heavy": "far more identical-value runs than its peers",
    "seg_bad": "one stretch is much worse than the rest of its own record",
    "aseasonal": "no seasonal signal, on a record long enough to have one",
}

SITE_COLUMN = "Gauge"
DECISION_COLUMNS = [SITE_COLUMN, "site_uid",
                    "decision", "decided_by", "note",
                    "signals", "score", "station_name", "n_samples", "lifespan_yr",
                    "n_nitrate_days", "mean", "std", "ac1", "noise", "spike_rate",
                    "flatline_frac", "longest_flat_days", "seg_noise", "seg_flat",
                    "seasonal_coherence", "n_clamped", "n_scrubbed", "impossible_run_days"]

VALID_DECISIONS = ("keep", "exclude")


def out_dir():
    return _panel.review_dir("corrupt-review", chunk="chunk6")


def signals_of(row) -> str:
    """The signals that fired, comma-joined. Empty when none did."""
    return ", ".join(s for s in quality.SIGNALS if bool(row.get(s)))


def queue(F: pd.DataFrame, meta: pd.DataFrame | None = None) -> pd.DataFrame:
    """The flagged gauges, worst score first. `F` is `quality.assess`'s table."""
    q = F[F.flagged].copy().reset_index()
    if not len(q):
        return q
    q["signals"] = [signals_of(r) for r in q.to_dict("records")]
    if meta is not None:
        keep = [c for c in ("station_name", "n_nitrate_days") if c in meta.columns]
        q = q.merge(meta[["site_uid"] + keep], on="site_uid", how="left")
    return q.sort_values("score", ascending=False, ignore_index=True)


def load_decisions() -> pd.DataFrame:
    """Human quality decisions, blank rows dropped. A blank `decision` means NOT YET REVIEWED, not `keep`."""
    p = config.CORRUPT_DECISIONS
    if not p.exists():
        return pd.DataFrame(columns=["site_uid", "decision"])
    d = pd.read_csv(p, dtype=str).fillna("")
    val = d.decision.str.strip().str.lower()
    bad = set(val[val != ""]) - set(VALID_DECISIONS)
    if bad:
        raise ValueError(f"{p.name} has unrecognised decisions {sorted(bad)}; "
                         f"expected one of {list(VALID_DECISIONS)}")
    out = d[val != ""].copy()
    out["decision"] = val[val != ""]
    return out[["site_uid", "decision"]].reset_index(drop=True)


def excluded() -> set:
    """Uids a human marked `exclude`. This IS the replacement for the old hand-curated KNOWN_BAD list."""
    d = load_decisions()
    return set(d.site_uid[d.decision == "exclude"])


def sync_decisions_file(q: pd.DataFrame) -> tuple[int, int, int]:
    """Reconcile `corrupt_decisions.csv` with the current queue. Returns (added, kept, retired)."""
    if not len(q):
        return 0, 0, 0
    return _panel.sync_sheet(config.CORRUPT_DECISIONS, q, ["site_uid"], SITE_COLUMN, DECISION_COLUMNS)


def pending(q: pd.DataFrame) -> list[str]:
    """Flagged uids with no decision on file, in review order."""
    done = set(load_decisions().site_uid)
    return [u for u in q.site_uid if u not in done]


# ---- drawing ----------------------------------------------------------------------------------------

def _legend(axes_row, F: pd.DataFrame) -> None:
    """Row 0: what each signal means and how many gauges it fired on."""
    for ax in axes_row:
        ax.axis("off")
    ax = axes_row[1]
    ax.text(0, 1.0, "`decision` must be `keep` or `exclude`", fontsize=7.5, fontweight="bold",
            family="monospace", va="top", color=INK, transform=ax.transAxes)
    body = ("An `exclude` is durable: it is the replacement for the old hand-curated KNOWN_BAD list,\n"
            "and it is what keeps the series out of processed2. A `keep` records that a person looked.\n\n"
            "Any ONE signal flags a gauge -- the detector is high-recall on purpose, because a false\n"
            "positive costs a glance and a missed one enters the training data as if it were a river.")
    ax.text(0, 0.84, body, fontsize=6.8, va="top", family="monospace", color=INK_2,
            transform=ax.transAxes)

    ax = axes_row[2]
    ax.text(0, 1.0, "why a row is here", fontsize=7.5, fontweight="bold", family="monospace",
            va="top", color=INK, transform=ax.transAxes)
    lines = [f"{s:<17}{int(F[s].sum()) if s in F.columns else 0:>4}   {SIGNAL_WHAT[s]}"
             for s in quality.SIGNALS]
    ax.text(0, 0.88, "\n".join(lines), fontsize=6.4, va="top", family="monospace",
            color=INK_2, transform=ax.transAxes)


def worst_segment(d: pd.DataFrame, width: int = quality.SEG_W):
    """The `width`-sample window with the highest step-to-step noise. Returns a slice of `d`.

    The same statistic `seg_noise` scores, so the reviewer sees the stretch that produced the number rather than a window chosen for looking bad.
    """
    v = d.dropna(subset=["nitrate_con"])
    if len(v) < 2 * width:
        return v
    n = len(v) // width
    vt = v.nitrate_con.to_numpy()[: n * width].reshape(n, width)
    sd = vt.std(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        score = np.where(sd > 0, np.diff(vt, axis=1).std(axis=1) / np.where(sd > 0, sd, 1), np.nan)
    k = int(np.nanargmax(score)) if np.isfinite(score).any() else 0
    return v.iloc[k * width:(k + 1) * width]


def _draw_series(ax, d: pd.DataFrame, title: str, gapped: bool = True) -> None:
    """One series on a date axis. Gaps BREAK the line rather than being bridged by a straight segment."""
    ax.set_facecolor(SURFACE)
    v = d.dropna(subset=["nitrate_con"])
    if not len(v):
        ax.text(0.5, 0.5, "no samples", fontsize=8, color=MUTED, ha="center", va="center",
                transform=ax.transAxes)
    else:
        s = pd.Series(v.nitrate_con.to_numpy(), index=pd.DatetimeIndex(v.datetime))
        if gapped and len(s) > 1:
            step = pd.Series(s.index).diff().median()
            brk = pd.Series(s.index).diff() > max(step * 5, pd.Timedelta("1D"))
            s = s.copy()
            s[brk.to_numpy()] = np.nan
        ax.plot(s.index, s.to_numpy(), lw=0.4, c=A_COLOR)
    ax.set_title(title, fontsize=6.5, color=INK_2, loc="left")
    ax.tick_params(labelsize=6, colors=INK_2)
    for sp in ax.spines.values():
        sp.set_color(AXIS)


def _stats_text(r) -> str:
    return "\n".join([
        f"samples        {int(r['n_samples']):>14,}",
        f"nitrate days   {int(r['n_nitrate_days']) if pd.notna(r.get('n_nitrate_days')) else '--':>14}",
        f"lifespan       {fmt(r.get('lifespan_yr'), '.1f'):>14} yr",
        f"clamped / null {int(r.get('n_clamped') or 0):>7} {int(r.get('n_scrubbed') or 0):>6}",
        f"impossible run {fmt(r.get('impossible_run_days'), '.1f'):>14} d",
        "",
        f"mean / std     {fmt(r.get('mean'), '.2f'):>7} {fmt(r.get('std'), '.2f'):>6}",
        f"autocorr(1)    {fmt(r.get('ac1'), '.4f'):>14}",
        f"noise          {fmt(r.get('noise'), '.4f'):>14}",
        f"spike rate     {fmt(r.get('spike_rate'), '.3f'):>14} %",
        f"flatline       {fmt(r.get('flatline_frac'), '.2f'):>14} %",
        f"longest flat   {fmt(r.get('longest_flat_days'), '.1f'):>14} d",
        f"worst segment  {fmt(r.get('seg_noise'), '.3f'):>7} {fmt(r.get('seg_flat'), '.3f'):>6}",
        f"seasonality    {fmt(r.get('seasonal_coherence'), '.3f'):>14}",
        "",
        f"score          {fmt(r.get('score'), '.2f'):>14}",
    ])


def main(F: pd.DataFrame, series: dict, meta: pd.DataFrame | None = None) -> pd.DataFrame:
    """Render the flagged-series panel. Returns the queue."""
    q = queue(F, meta)
    if not len(q):
        print("  nothing flagged; no quality review needed")
        return q

    draw = _panel.cap(q, "gauge(s) flagged", config.CORRUPT_DECISIONS)

    fig, axes = _panel.figure(len(draw) + 1, width_ratios=(0.42, 4.4, 2.6, 2.9))
    _legend(axes[0], F)
    for i, r in enumerate(q.to_dict("records"), start=1):
        uid = r["site_uid"]
        ax_n, ax_full, ax_seg, ax_txt = axes[i]
        _panel.draw_index(ax_n, i)
        d = series.get(uid, pd.DataFrame(columns=["datetime", "nitrate_con"]))
        _draw_series(ax_full, d, str(r.get("station_name") or uid)[:70])
        _draw_series(ax_seg, worst_segment(d), "worst segment", gapped=False)
        _panel.draw_stats(ax_txt, r["signals"] or "flagged", INK, _stats_text(r),
                          sublines=[(uid, A_COLOR)])
        ax_full.set_ylabel(uid, fontsize=6, color=INK)
    for ax, t in zip(axes[0][1:], ("the full record, mg/L as N", "worst segment", "evidence")):
        ax.set_title(t, fontsize=8, color=INK, loc="left")

    png = out_dir() / "corrupt_review.png"
    w, h = _panel.finish(fig, len(draw) + 1,
                         f"Series quality — {len(q)} flagged of {len(F)} gauges that passed every other "
                         f"filter.  `exclude` keeps a series out of processed2 permanently; "
                         f"`keep` records that it was judged.", png)
    print(f"  wrote {png.name} ({w} x {h} px, {png.stat().st_size/1e6:.1f} MB)")
    return q
