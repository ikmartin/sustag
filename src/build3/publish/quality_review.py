"""The human review artifact for series quality: one tall PNG, one row per flagged (site, channel).

THE LAST GATE, AND THE ONLY ONE ABOUT THE MEASUREMENT ITSELF. D3 asks what a site IS; D4 which delineation to believe; this asks whether the water is real. Nothing downstream can tell a broken sensor from unusual hydrology, so if it is not decided here it is not decided at all.

ONLY HIGH-TIER FLAGS REACH THE PANEL. Standard channels take the rule's verdict, recorded; a queue holding every channel of every site is a queue nobody reads. A bundle's members are already ONE series here -- `quality.native_series` concatenates them, so the seam between two registrations is not a defect and a fault spanning the join is visible.

TWO VIEWS OF THE SAME SERIES, because the failures live at different scales. The full record shows a level shift, a dead stretch or a drifting baseline; the worst-segment zoom shows the sampling-interval behaviour -- spikes, flatlines, noise -- that a decade-wide plot renders as a solid band. `seg_noise` is what nominates the window, so the reviewer is looking at the evidence the detector actually scored.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, panel as _panel
from ..panel import A_COLOR, AXIS, INK, INK_2, MUTED, SURFACE, fmt
from . import quality

SIGNAL_WHAT = {
    "range_violation": "out-of-range readings survived scrubbing",
    "dead_autocorr": "consecutive samples barely correlate -- noise, not a river",
    "extreme_noise": "step-to-step variation is a quarter of the series' own spread",
    "stuck_impossible": "a run of physically impossible readings -- a failed sensor, not a river",
    "noisy": "noisier than the cohort by more than 3.5 robust-z",
    "scale_outlier": "its mean or spread is a cohort outlier",
    "flatline_heavy": "far more identical-value runs than its peers",
    "seg_bad": "one stretch is much worse than the rest of its own record",
    "aseasonal": "no seasonal signal, on a record long enough to have one",
}


def signals_of(row) -> str:
    """The signals that fired, comma-joined. Empty when none did."""
    return ", ".join(s for s in quality.SIGNALS if bool(row.get(s)))


def queue(F: pd.DataFrame) -> pd.DataFrame:
    """The flagged series, worst score first. `F` is `quality.assess_high_tier`'s table (may span channels)."""
    q = F[F.flagged].copy().reset_index()
    if not len(q):
        return q
    q["signals"] = [signals_of(r) for r in q.to_dict("records")]
    return q.sort_values("score", ascending=False, ignore_index=True)


# ---- drawing ----------------------------------------------------------------------------------------

def _legend(axes_row, F: pd.DataFrame) -> None:
    """Row 0: what each signal means and how many series it fired on."""
    for ax in axes_row:
        ax.axis("off")
    ax = axes_row[1]
    ax.text(0, 1.0, "`decision` must be `keep` or `exclude`", fontsize=7.5, fontweight="bold",
            family="monospace", va="top", color=INK, transform=ax.transAxes)
    body = ("An `exclude` is durable: it is what withholds the series from `published/`, keyed on the\n"
            "member set and channel so it survives every rebuild. A `keep` records that a person looked.\n\n"
            "A `keep` may carry `exclude_spans`: day windows nulled from the PUBLISHED series while the\n"
            "rest is kept -- for a record that is fine except for a stuck or railed stretch. Write them as\n"
            "`2021-03-01..2021-07-15; 2022-01-02..2022-01-09` (inclusive both ends, `;`-separated).\n"
            "`proposed_spans` beside it holds the detector's measured stuck windows (>= 7 days pinned at\n"
            "one value, gap-aware); copy or edit them into `exclude_spans` -- they are never applied alone.\n\n"
            "A `keep` may also carry `max_threshold`: a number in the channel's canonical units, a ceiling\n"
            "for THIS sensor alone. Every published day with a reading above it is nulled -- whole days,\n"
            "because the day's mean was computed from the offending samples too. Use it where a sensor\n"
            "rails or spikes to values credible for the channel but not for this water; the registry's\n"
            "`hi` (nitrate: 100) already removes the physically impossible at canonicalisation.\n\n"
            "Any ONE signal flags a series -- the detector is high-recall on purpose, because a false\n"
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
    """The `width`-sample window with the highest step-to-step noise -- the same statistic `seg_noise` scores, so the reviewer sees the stretch that produced the number rather than a window chosen for looking bad."""
    v = d.dropna(subset=["value"])
    if len(v) < 2 * width:
        return v
    n = len(v) // width
    vt = v.value.to_numpy()[: n * width].reshape(n, width)
    sd = vt.std(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        score = np.where(sd > 0, np.diff(vt, axis=1).std(axis=1) / np.where(sd > 0, sd, 1), np.nan)
    k = int(np.nanargmax(score)) if np.isfinite(score).any() else 0
    return v.iloc[k * width:(k + 1) * width]


def _draw_series(ax, d: pd.DataFrame, title: str, gapped: bool = True) -> None:
    """One series on a date axis. Gaps BREAK the line rather than being bridged by a straight segment."""
    ax.set_facecolor(SURFACE)
    v = d.dropna(subset=["value"])
    if not len(v):
        ax.text(0.5, 0.5, "no samples", fontsize=8, color=MUTED, ha="center", va="center",
                transform=ax.transAxes)
    else:
        s = pd.Series(v.value.to_numpy(), index=pd.DatetimeIndex(v.datetime))
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
        f"seasonality    {fmt(r.get('seasonal_strength'), '.3f'):>14}",
        "",
        f"score          {fmt(r.get('score'), '.2f'):>14}",
    ])


def html(q: pd.DataFrame, series: dict) -> "object":
    """The INTERACTIVE companion to the PNG: hover reads the exact date and value off the record.

    Same rows as the panel, two hoverable plots each. The full record is shown at DAILY resolution -- exclusion spans are day-granular, so the day is the unit a reviewer is actually choosing, and it keeps a 700k-sample IWQIS series at a few thousand points; hover carries the day's mean, min, max and sample count. The worst segment stays at NATIVE resolution (it is at most `SEG_W` samples), because sampling-interval behaviour is what it exists to show. The detector's `proposed_spans` are drawn as shaded windows on the full record, so a boundary can be checked against the data before the dates are copied into `exclude_spans`.

    Self-contained: plotly.js is EMBEDDED (the file is ~4 MB and opens offline), matching the rule that review evidence must not depend on a network.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    n = len(q)
    fig = make_subplots(
        rows=n, cols=2, column_widths=[0.68, 0.32], horizontal_spacing=0.05,
        vertical_spacing=min(0.5 / max(n, 1), 0.06), shared_xaxes=False,
        subplot_titles=[t for r in q.to_dict("records") for t in
                        (f"{r['site_id']}  ({r.get('channel', 'nitrate')})  —  {r['signals'] or 'flagged'}"
                         f"  —  score {r.get('score')}", "worst segment (native)")])
    for i, r in enumerate(q.to_dict("records"), start=1):
        sid, ch = r["site_id"], r.get("channel", "nitrate")
        d = series.get((sid, ch), pd.DataFrame(columns=["datetime", "value"]))
        v = d.dropna(subset=["value"])
        if len(v):
            day = (v.set_index(pd.DatetimeIndex(v.datetime)).value
                   .resample("D").agg(["mean", "min", "max", "count"]).dropna(subset=["mean"]))
            fig.add_trace(go.Scatter(
                x=day.index, y=day["mean"], mode="lines", line=dict(width=0.8, color=A_COLOR),
                customdata=day[["min", "max", "count"]].to_numpy(),
                hovertemplate=("%{x|%Y-%m-%d}<br>mean %{y:.3f}<br>min %{customdata[0]:.3f}"
                               "  max %{customdata[1]:.3f}<br>%{customdata[2]:.0f} sample(s)"
                               "<extra></extra>"),
                showlegend=False), row=i, col=1)
            for a, b in quality.parse_spans(r.get("proposed_spans") or ""):
                fig.add_vrect(x0=a, x1=b + pd.Timedelta(days=1), row=i, col=1,
                              fillcolor="#e08214", opacity=0.18, line_width=0)
        seg = worst_segment(d)
        if len(seg):
            fig.add_trace(go.Scattergl(
                x=seg.datetime, y=seg.value, mode="lines", line=dict(width=0.7, color=INK_2),
                hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.3f}<extra></extra>",
                showlegend=False), row=i, col=2)
    led = quality.quality_ledger()
    fig.update_layout(
        height=max(320 * n, 400), template="plotly_white",
        title=dict(text=(f"Series quality — {n} flagged.  Hover reads the exact date; shaded windows are "
                         f"the detector's proposed_spans (copy the dates you agree with into "
                         f"exclude_spans in {led.path.name}; a max_threshold there nulls every day "
                         f"reading above it for this sensor alone)."), font=dict(size=13)),
        margin=dict(t=70, l=50, r=20, b=30))
    fig.update_annotations(font_size=10)
    out = config.REVIEW_DIR / "quality_review.html"
    fig.write_html(out, include_plotlyjs="inline")
    print(f"  wrote {out.name} ({out.stat().st_size/1e6:.1f} MB, self-contained -- open in a browser)")
    return out


def main(F: pd.DataFrame | None = None, series: dict | None = None) -> pd.DataFrame:
    """Render the flagged-series panel. Recomputes the assessment when not handed one -- the standalone route."""
    from .. import io as bio

    if F is None or series is None:
        from .. import registry

        sites = bio.read_parquet(config.SITES_PATH)
        frames, series = [], {}
        for ch in registry.high_scrutiny():
            Fc, s = quality.assess_high_tier(sites, ch)
            if len(Fc):
                frames.append(Fc.assign(channel=ch))
            series.update({(sid, ch): d for sid, d in s.items()})
        F = pd.concat(frames) if frames else pd.DataFrame()
    q = queue(F)
    if not len(q):
        print("  nothing flagged; no quality review needed")
        return q

    led = quality.quality_ledger()
    draw = _panel.cap(q, "series flagged", led.path)

    fig, axes = _panel.figure(len(draw) + 1, width_ratios=(0.42, 4.4, 2.6, 2.9))
    _legend(axes[0], F)
    for i, r in enumerate(draw.to_dict("records"), start=1):
        sid, ch = r["site_id"], r.get("channel", "nitrate")
        ax_n, ax_full, ax_seg, ax_txt = axes[i]
        _panel.draw_index(ax_n, i)
        d = series.get((sid, ch), pd.DataFrame(columns=["datetime", "value"]))
        _draw_series(ax_full, d, f"{sid}  ({ch})")
        _draw_series(ax_seg, worst_segment(d), "worst segment", gapped=False)
        _panel.draw_stats(ax_txt, r["signals"] or "flagged", INK, _stats_text(r),
                          sublines=[(str(r.get("members")), A_COLOR)])
        ax_full.set_ylabel(sid, fontsize=6, color=INK)
    for ax, t in zip(axes[0][1:], ("the full record, canonical units", "worst segment", "evidence")):
        ax.set_title(t, fontsize=8, color=INK, loc="left")

    png = config.REVIEW_DIR / "quality_review.png"
    w, h = _panel.finish(fig, len(draw) + 1,
                         f"Series quality — {len(q)} flagged of {len(F)} high-tier series that reached "
                         f"publication.  `exclude` withholds the series from published/; "
                         f"`keep` records that it was judged.  Decide in {led.path.name}", png)
    print(f"  wrote {png.name} ({w} x {h} px, {png.stat().st_size/1e6:.1f} MB)")
    html(q, series)
    return q


if __name__ == "__main__":
    main()
