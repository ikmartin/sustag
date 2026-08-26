"""The human review artifact for merge candidates: one tall PNG, one row per pair.

WHY A FIGURE AND NOT A TABLE. `merge_candidates.csv` already carries the evidence, but deciding a pair from it means holding a map, two records and eight numbers in your head simultaneously. A merge changes the unit of analysis and a wrong one corrupts basins, graph edges and the merged site record at once, silently -- so the pairs are worth per-pair review.

BUILT AROUND THE SEQUENTIAL CASE. Pairs that overlap in time are easy: correlate them. Pairs that do NOT overlap are the hard ones, and they are exactly where correlation does not exist -- there are no shared days to compute it on. Telling "one gauge re-registered after a redeployment" from "two different sites that happen to share a reach" needs evidence that survives zero overlap, which is what `doy_r` and `ks` are here for:

  doy_r  monthly climatology of each record, correlated across the two 12-vectors. A re-registered gauge keeps its seasonal signature across the seam; a different site on a different tributary generally does not.
  ks     two-sample Kolmogorov-Smirnov on the value distributions. Distribution-level similarity needing no time alignment at all.

Neither needs a single shared day, and together they are what the `sequential` rows are actually judged on. Everything else -- separation, shared catchment, means, variances -- is corroboration.

EVERY SERIES IS THE PAIR'S DECIDING CHANNEL at its registry compare statistic, so the numbers on the panel are the numbers the rule saw -- a panel showing the mean while the rule judged the max would invite overriding decisions on evidence the rule never used.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, io as bio, ledger, panel as _panel
from ..panel import A_COLOR, B_COLOR, INK, INK_2, MUTED, fmt
from . import identity

VERDICT_COLOR = {"sequential": "#e08214", "dual_registration": "#2ca02c",
                 "complementary": "#7570b3", "weak_evidence": "#b8860b",
                 "distinct": "crimson", "unknown_no_records": MUTED}


# ---- data -----------------------------------------------------------------------------------------

def _series_for(cand: pd.DataFrame) -> dict[tuple[str, str], pd.Series]:
    """(uid, channel) -> the daily series identity judged: the pair's deciding channel at its compare stat.

    Registration-level on purpose. A merge review needs both halves of a pair as separate series -- comparing them IS the review -- so it cannot read the merged site record, where a confirmed pair has already been absorbed. Only the uids under review are opened.
    """
    out = {}
    for row in cand.itertuples():
        ch = row.on_channel if pd.notna(row.on_channel) else "nitrate"
        for u in (row.uid_a, row.uid_b):
            if (u, ch) not in out:
                s = identity._series(identity._canonical(u), ch)
                if s is not None:
                    out[(u, ch)] = s
    return out


# ---- statistics -----------------------------------------------------------------------------------

def _doy_r(sa: pd.Series | None, sb: pd.Series | None) -> float:
    """Correlation of the two monthly climatologies. NaN when either record cannot fill enough months.

    THE POINT: this needs no shared days. Each record is reduced to a 12-vector of month-of-year means over its whole life, and the two vectors are correlated. A gauge re-registered under a new id keeps the seasonal shape it had before the seam -- spring flush high, late-summer low -- while a different site on a different tributary usually does not. For a `sequential` pair it is the only agreement measure that exists.
    """
    if sa is None or sb is None or not len(sa) or not len(sb):
        return float("nan")
    ma = sa.groupby(sa.index.month).mean()
    mb = sb.groupby(sb.index.month).mean()
    both = pd.concat([ma.rename("a"), mb.rename("b")], axis=1, sort=True).dropna()
    if len(both) < 6:                     # fewer than six shared months is not a seasonal shape
        return float("nan")
    return float(both.a.corr(both.b))


def _ks(sa: pd.Series | None, sb: pd.Series | None) -> float:
    """Two-sample KS statistic on the value distributions; 0 identical, 1 disjoint. NaN if either is empty.

    Time-alignment-free, so like `_doy_r` it survives zero overlap. A re-registered gauge on the same water should show a small statistic; a genuinely different site usually does not.
    """
    if sa is None or sb is None or len(sa) < 10 or len(sb) < 10:
        return float("nan")
    from scipy.stats import ks_2samp

    return float(ks_2samp(sa.dropna().to_numpy(), sb.dropna().to_numpy()).statistic)


def _desc(s: pd.Series | None) -> dict:
    if s is None or not len(s):
        return dict(n=0, first=None, last=None, mean=np.nan, sd=np.nan,
                    median=np.nan, p10=np.nan, p90=np.nan)
    v = s.dropna()
    return dict(n=int(len(v)), first=str(s.index.min().date()), last=str(s.index.max().date()),
                mean=float(v.mean()), sd=float(v.std()), median=float(v.median()),
                p10=float(v.quantile(0.10)), p90=float(v.quantile(0.90)))


def pair_stats(row, sensors: pd.DataFrame, series: dict) -> dict:
    """Every number the review needs for one candidate pair. No plotting.

    Grouped as geometry / record / distribution / agreement. The agreement block is NaN for a non-overlapping pair by construction, which is precisely why `doy_r` and `ks` are computed for every pair rather than only the overlapping ones.
    """
    ua, ub = row.uid_a, row.uid_b
    ch = row.on_channel if pd.notna(row.on_channel) else "nitrate"
    si = sensors.set_index("site_uid")
    a, b = si.loc[ua], si.loc[ub]
    sa, sb = series.get((ua, ch)), series.get((ub, ch))
    da, db = _desc(sa), _desc(sb)

    shared = r = mdiff = mae = np.nan
    gap_d = np.nan
    if sa is not None and sb is not None:
        j = pd.concat([sa.rename("a"), sb.rename("b")], axis=1, sort=True).dropna()
        shared = len(j)
        if shared >= 10:
            r = float(j.a.corr(j.b))
        if shared:
            mdiff = float((j.a - j.b).median())
            mae = float((j.a - j.b).abs().mean())
        else:
            lo, hi = (sa, sb) if sa.index.max() <= sb.index.min() else (sb, sa)
            gap_d = int((hi.index.min() - lo.index.max()).days)

    return dict(
        uid_a=ua, uid_b=ub, name_a=a.station_name, name_b=b.station_name,
        proposed=row.proposed, channel=ch,
        # geometry
        sep_m=float(row.sep_m), comid=int(row.comid),
        comid_a=a.comid, comid_b=b.comid,
        catchment_comid_a=a.catchment_comid, catchment_comid_b=b.catchment_comid,
        same_catchment=bool(pd.notna(a.catchment_comid) and a.catchment_comid == b.catchment_comid),
        agree_a=a.comid_agree, agree_b=b.comid_agree,
        snap_dist_a=a.snap_dist_m, snap_dist_b=b.snap_dist_m,
        snap_status_a=a.snap_status, snap_status_b=b.snap_status,
        catch_km2=a.catchment_area_km2,
        area_a=a.source_drainage_area_km2, area_b=b.source_drainage_area_km2,
        # record
        n_a=da["n"], n_b=db["n"], first_a=da["first"], last_a=da["last"],
        first_b=db["first"], last_b=db["last"],
        shared_days=None if pd.isna(shared) else int(shared),
        seam_gap_d=None if pd.isna(gap_d) else int(gap_d),
        # distribution
        mean_a=da["mean"], mean_b=db["mean"], sd_a=da["sd"], sd_b=db["sd"],
        median_a=da["median"], median_b=db["median"],
        p10_a=da["p10"], p90_a=da["p90"], p10_b=db["p10"], p90_b=db["p90"],
        mean_diff=da["mean"] - db["mean"] if da["n"] and db["n"] else np.nan,
        sd_ratio=(da["sd"] / db["sd"]) if (db["n"] and db["sd"]) else np.nan,
        # agreement
        r=r, median_diff=mdiff, mae=mae,
        doy_r=_doy_r(sa, sb), ks=_ks(sa, sb),
    )


# ---- panels ---------------------------------------------------------------------------------------

def _legend(axes_row, cand) -> None:
    """Row 0: what a decision may be, and what each proposed branch means.

    `decision` takes `merge` or `distinct` and nothing else, and a reviewer should not have to remember which threshold produced a proposal. The thresholds are read from `identity` rather than retyped, so the legend cannot drift from the rule it describes.
    """
    for ax in axes_row:
        ax.axis("off")
    ax = axes_row[2]
    counts = cand.proposed.value_counts().to_dict() if len(cand) else {}
    n = lambda k: counts.get(k, 0)
    body = "\n".join([
        "  merge      one physical gauge, published twice; the records become one series",
        "  distinct   genuinely different sensors; never merged",
        "",
        "proposed branch, and the rule that produced it:",
        f"  sequential          {n('sequential'):>3}   no shared days -- a gauge re-registered after a redeployment",
        f"  complementary       {n('complementary'):>3}   no discriminating shared channel, within "
        f"{identity.COMPLEMENTARY_MAX_SEP_M:.0f} m -- one station, sensors measuring different things",
        f"  weak_evidence       {n('weak_evidence'):>3}   only a seasonal channel is shared; records cannot judge it",
        f"  dual_registration   {n('dual_registration'):>3}   overlap, r >= {identity.DUAL_MIN_R} AND level agreement per the",
        f"                            channel's registry spec (<= {identity.DUAL_MAX_MEDIAN_DIFF} mg/L high-scrutiny;",
        "                            agree_tol on interval scales; relative elsewhere)",
        f"  distinct            {n('distinct'):>3}   overlap, but they disagree on level or shape",
        f"  unknown_no_records  {n('unknown_no_records'):>3}   a record is missing; nothing to judge",
        "",
        "BOTH conditions gate dual_registration: high r alone only says two sensors",
        "move together, which any two on one river do. Agreeing on the VALUE is what",
        f"identifies one instrument. `r` needs >= {identity.MIN_SHARED_DAYS_FOR_R} shared days; seasonal r and KS",
        "need none, which is what the sequential rows are judged on.",
    ])
    ax.text(0, 1.0, "merge vocabulary — `decision` must be `merge` or `distinct`",
            fontsize=7.5, fontweight="bold", family="monospace",
            va="top", color=INK, transform=ax.transAxes)
    ax.text(0, 0.90, body, fontsize=7, va="top", family="monospace",
            color=INK_2, transform=ax.transAxes)


def _draw_series(ax, sa, sb, st):
    """Both records on one timeline, with the seam shaded when they do not overlap."""
    drawn = _panel.draw_series(ax, [sa, sb], [A_COLOR, B_COLOR])
    if drawn and st.get("seam_gap_d") is not None and sa is not None and sb is not None:
        lo, hi = (sa, sb) if sa.index.max() <= sb.index.min() else (sb, sa)
        c = VERDICT_COLOR["sequential"]
        ax.axvspan(lo.index.max(), hi.index.min(), color=c, alpha=0.16, zorder=0)
        mid = lo.index.max() + (hi.index.min() - lo.index.max()) / 2
        ax.annotate(f"seam {st['seam_gap_d']}d", (mid, 1.0), xycoords=("data", "axes fraction"),
                    fontsize=6, ha="center", va="top", color=c)


def _stats_text(st) -> str:
    """The monospace evidence block. Fixed-width so columns line up down the whole panel."""
    # pd.notna guards, because `NA == NA` is not False but AMBIGUOUS, and a member whose containing catchment is null reaches this line as pd.NA
    same_snap = ("?" if pd.isna(st["comid_a"]) or pd.isna(st["comid_b"])
                 else "SAME" if st["comid_a"] == st["comid_b"] else "DIFF")
    same_cat = "SAME" if bool(st["same_catchment"]) else "DIFF"
    ov = ("none" if pd.isna(st["shared_days"]) or st["shared_days"] in (0, None)
          else f"{int(st['shared_days']):,} d")
    flag = lambda v: {True: "y", False: "NEAR-DIVIDE"}.get(v, "?")
    far = lambda s: "  FAR" if pd.notna(s) and s != "ok" else ""      # NA == "ok" is ambiguous, not False
    return "\n".join([
        f"channel        {st['channel']:>9}",
        f"sep            {st['sep_m']:>9.1f} m",
        f"snapped reach  {same_snap:>9}  {st['comid']}",
        f"containment    {same_cat:>9}  {st['catchment_comid_a']} / {st['catchment_comid_b']}",
        f"catch agree    {flag(st['agree_a']):>9} / {flag(st['agree_b'])}",
        f"snap dist      {fmt(st['snap_dist_a'],'.1f'):>9} /{fmt(st['snap_dist_b'],'.1f'):>8} m"
        f"{far(st['snap_status_a'])}{far(st['snap_status_b'])}",
        f"catchment km2  {fmt(st['catch_km2'],'.2f'):>9}",
        f"src area km2   {fmt(st['area_a'],'.1f'):>9} /{fmt(st['area_b'],'.1f'):>8}",
        "",
        f"record A       {st['first_a'] or '--'} .. {st['last_a'] or '--'}  n={st['n_a']:,}",
        f"record B       {st['first_b'] or '--'} .. {st['last_b'] or '--'}  n={st['n_b']:,}",
        f"overlap        {ov:>9}"
        + (f"   seam {st['seam_gap_d']} d" if st["seam_gap_d"] is not None else ""),
        "",
        f"mean   A/B     {fmt(st['mean_a']):>9} /{fmt(st['mean_b']):>8}   d={fmt(st['mean_diff'])}",
        f"sd     A/B     {fmt(st['sd_a']):>9} /{fmt(st['sd_b']):>8}   x={fmt(st['sd_ratio'])}",
        f"median A/B     {fmt(st['median_a']):>9} /{fmt(st['median_b']):>8}",
        f"p10-p90 A      {fmt(st['p10_a']):>9} -{fmt(st['p90_a']):>8}",
        f"p10-p90 B      {fmt(st['p10_b']):>9} -{fmt(st['p90_b']):>8}",
        "",
        f"overlap r      {fmt(st['r'],'.3f'):>9}   med(a-b) {fmt(st['median_diff'])}",
        f"seasonal r     {fmt(st['doy_r'],'.3f'):>9}   <- needs no overlap",
        f"KS distance    {fmt(st['ks'],'.3f'):>9}   <- needs no overlap",
    ])


def _draw_stats(ax, st):
    """Proposed branch, then both uids in their panel colours, then the evidence."""
    _panel.draw_stats(ax, f"proposed: {st['proposed']}",
                      VERDICT_COLOR.get(st["proposed"], INK), _stats_text(st),
                      sublines=[(f"A  {st['uid_a']}", A_COLOR),
                                (f"B  {st['uid_b']}", B_COLOR)])


# ---- assembly -------------------------------------------------------------------------------------

def main() -> pd.DataFrame:
    """Render the review panel and the machine-readable stats beside it. Returns the stats frame."""
    if not identity.CANDIDATES_PATH.exists():
        print("  no candidate pairs on file -- either d3 has not run, or no two sensors share a reach")
        return pd.DataFrame()
    # The SAME decisions the sheet was synced against, or panel row N stops being sheet row N.
    cand = identity.order_for_review(pd.read_csv(identity.CANDIDATES_PATH), identity.load_decisions())
    sensors = bio.read_parquet(config.SENSORS_PATH)
    series = _series_for(cand)
    uids = set(cand.uid_a) | set(cand.uid_b)
    print(f"  {len(cand)} candidate pair(s); {len({u for u, _ in series})}/{len(uids)} have records")

    si = sensors.set_index("site_uid")
    need = set()
    for c in ("comid", "catchment_comid"):
        need |= {int(v) for v in si.loc[sorted(uids), c].dropna()}
    print(f"  loading geometry for {len(need)} COMID(s) ...", flush=True)
    catch, reaches = _panel.catchments(need), _panel.context_reaches(need)
    print(f"    {len(catch)} catchment(s), {len(reaches)} context reach(es)", flush=True)
    pts = _panel.project(sensors, key="site_uid")

    # +1 for the vocabulary row at the top. `cap` bounds the drawing only -- every candidate stays in the
    # sheet and in merge_candidates.csv, and the ordering means the rows kept are the outstanding ones.
    draw = _panel.cap(cand, "candidate pair(s)", ledger.MERGE.path)
    fig, axes = _panel.figure(len(draw) + 1)
    _legend(axes[0], cand)
    rows = []
    for i, row in enumerate(draw.itertuples(), start=1):
        st = pair_stats(row, sensors, series)
        rows.append(st)
        ax_n, ax_map, ax_ts, ax_txt = axes[i]
        _panel.draw_index(ax_n, i)
        cid = {st["comid_a"], st["comid_b"], st["catchment_comid_a"], st["catchment_comid_b"]}
        sub = catch[catch.comid.isin({int(c) for c in cid if pd.notna(c)})]
        ch = st["channel"]
        _panel.draw_map(ax_map, [pts[st["uid_a"]], pts[st["uid_b"]]], ["A", "B"],
                        [A_COLOR, B_COLOR], catch=sub, reaches=reaches, snapped=st["comid"])
        _draw_series(ax_ts, series.get((st["uid_a"], ch)), series.get((st["uid_b"], ch)), st)
        _draw_stats(ax_txt, st)
        ax_map.set_ylabel(f"{st['uid_a']}\n{st['uid_b']}", fontsize=6, color=INK)
        ax_ts.set_title(f"A {str(st['name_a'])[:38]}   |   B {str(st['name_b'])[:38]}",
                        fontsize=6.5, color=INK_2, loc="left")
    axes[1][1].set_title("catchments + reaches", fontsize=8, color=INK, loc="left")
    axes[1][3].set_title("evidence", fontsize=8, color=INK, loc="left")

    png = config.REVIEW_DIR / "merge_review.png"
    n_seq = int((cand.proposed == "sequential").sum())
    w, h = _panel.finish(fig, len(draw) + 1,
                         f"Merge review — {len(draw)} of {len(cand)} candidate pairs sharing a snapped reach "
                         f"({n_seq} sequential, decided in {ledger.MERGE.path.name})", png)

    stats = pd.DataFrame(rows)
    stats.to_csv(config.REVIEW_DIR / "merge_review_stats.csv", index=False)
    print(f"  wrote {png.name} ({w} x {h} px, {png.stat().st_size/1e6:.1f} MB) "
          f"and merge_review_stats.csv ({len(stats)} rows)")
    return stats


if __name__ == "__main__":
    main()
