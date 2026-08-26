"""Establish the registry's declared units by measurement rather than by documentation.

    python -m src.build2.units_check

WHY THIS EXISTS. `params.CHANNELS` declares what unit each source reports a channel in, and for USGS that is read off the NWIS parameter-code table and is trustworthy. IWQIS and MPCA document almost nothing: `params.unverified()` currently lists fifteen mappings whose units are an assumption, and an assumption that is wrong produces a series that looks entirely plausible and is off by a constant factor.

THE METHOD ALREADY WORKED ONCE. MPCA's nitrate was never documented as-N or as-NO3, and the question was settled by finding the same water in two systems: Cedar River is CSG 48020005 and USGS 05457000, and over 56,125 exactly-matching timestamps the median ratio is 1.0000 against 4.4269 for the as-NO3 alternative. That is the whole idea, generalised to every channel: two independent instruments on the same water, converted to canonical units, should agree.

WHAT A RESULT MEANS. `ratio` near 1.0 confirms the declared unit. A clean constant far from 1.0 is an undeclared unit and the scale factor is readable straight off it. Scatter with no central tendency means the two sites are not actually the same water, which is a co-location problem rather than a unit one -- `n` and the pair's separation are printed so that case is visible instead of being averaged into a verdict.

TURBIDITY IS THE ONE THAT MATTERS MOST. FNU, NTU, FNRU and FBU are different optical geometries rather than different spellings, so `turbidity_unknown` deliberately holds the IWQIS and MPCA sensors apart from both named channels until this check says which they are. A ratio near 1.0 against a 63680 neighbour is what promotes them.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from . import config, params, schema

# Two sensors this far apart on the same water are comparable for a unit check. Generous on purpose: this asks "is the scale right", not "is this one gauge", so admitting a pair that turns out to be two real sites costs a wide ratio spread that `n` and `sep_m` make legible, while a tight radius would leave most channels with no pair at all.
COLOCATION_M = 300.0

# Below this many matched timestamps a median ratio is not evidence of anything.
MIN_MATCHED = 30


def _native(uid: str) -> pd.DataFrame | None:
    p = config.WATER_NATIVE / f"{schema.uid_to_filename(uid)}.parquet"
    return pd.read_parquet(p) if p.exists() else None


def colocated_pairs(reg: pd.DataFrame) -> pd.DataFrame:
    """Cross-source registration pairs measuring the same water: `uid_a, uid_b, sep_m, how`.

    Two independent sources of truth, unioned. MPCA's site list names the USGS gage a station sits on, which is an assertion by the operator and better than any distance test; everything else is proximity under `COLOCATION_M`. Same-source pairs are excluded -- two IWQIS sensors agreeing tells you nothing about IWQIS's units.
    """
    import geopandas as gpd

    r = reg.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    g = gpd.GeoSeries(gpd.points_from_xy(r.lon, r.lat), crs=4326).to_crs(config.EQUAL_AREA)
    xy = np.c_[g.x, g.y]
    uid = r.site_uid.to_numpy()
    src = r.source.to_numpy()

    rows: dict[tuple[str, str], dict] = {}

    # 1. MPCA's declared USGS gage.
    try:
        m = pd.read_csv(config.RAW_WATER / "MPCA_CSG" / "mpca_csg_sites.csv", dtype=str)
        pos = {u: i for i, u in enumerate(uid)}
        for cs, us in m.dropna(subset=["usgs_id"])[["csg_id", "usgs_id"]].itertuples(index=False):
            a, b = f"MPCA:{cs}", f"USGS:{us}"
            if a in pos and b in pos:
                sep = float(np.hypot(*(xy[pos[a]] - xy[pos[b]])))
                rows[tuple(sorted((a, b)))] = dict(sep_m=sep, how="declared")
    except FileNotFoundError:
        pass

    # 1b. IWQIS's declared collocated gage. `colloc_uid` holds a braced NWIS number -- `{05451500}` on
    # WQP0010 -- which is the operator saying the two are the same structure. Same standing as MPCA's
    # `usgs_id` above and better than any distance test.
    try:
        r2 = pd.read_csv(config.RAW_WATER / "site_clean.csv", dtype=str)
        pos = {u: i for i, u in enumerate(uid)}
        cu = r2.get("colloc_uid")
        if cu is not None:
            for iw, us in zip(r2["uid"].astype(str).str.strip(), cu.fillna("").str.strip("{} ")):
                if not us:
                    continue
                a, b = f"IWQIS:{iw}", f"USGS:{us}"
                if a in pos and b in pos:
                    sep = float(np.hypot(*(xy[pos[a]] - xy[pos[b]])))
                    rows.setdefault(tuple(sorted((a, b))), dict(sep_m=sep, how="declared"))
    except FileNotFoundError:
        pass

    # 2. Proximity, cross-source only.
    from scipy.spatial import cKDTree

    tree = cKDTree(xy)
    for i, j in tree.query_pairs(COLOCATION_M):
        if src[i] == src[j]:
            continue
        key = tuple(sorted((uid[i], uid[j])))
        if key not in rows:
            rows[key] = dict(sep_m=float(np.hypot(*(xy[i] - xy[j]))), how="distance")

    if not rows:
        return pd.DataFrame(columns=["uid_a", "uid_b", "sep_m", "how"])
    out = pd.DataFrame([{"uid_a": a, "uid_b": b, **v} for (a, b), v in rows.items()])
    return out.sort_values("sep_m", ignore_index=True)


def compare(uid_a: str, uid_b: str, channel: str) -> dict | None:
    """Agreement between two sites on one channel, in canonical units. `None` unless both carry it.

    Joined on EXACT timestamps rather than resampled. Two continuous sensors on a shared 15-minute grid align exactly where they overlap, and a tolerance join would manufacture agreement by pairing readings hours apart on a series that varies within the day.
    """
    a, b = _native(uid_a), _native(uid_b)
    if a is None or b is None:
        return None
    sa = params.extract(a, schema.split_uid(uid_a)[0], channel)
    sb = params.extract(b, schema.split_uid(uid_b)[0], channel)
    if sa is None or sb is None:
        return None
    j = pd.concat([sa.rename("a"), sb.rename("b")], axis=1, join="inner").dropna()
    if len(j) < MIN_MATCHED:
        return None
    nz = j[j.b.abs() > 1e-9]
    return dict(uid_a=uid_a, uid_b=uid_b, channel=channel, n=len(j),
                ratio=float((nz.a / nz.b).median()) if len(nz) else np.nan,
                abs_diff=float((j.a - j.b).abs().median()),
                a_median=float(j.a.median()), b_median=float(j.b.median()))


def report(reg: pd.DataFrame | None = None) -> pd.DataFrame:
    """Every comparable (pair, channel), with the per-channel verdict printed."""
    reg = reg if reg is not None else pd.read_parquet(config.REGISTRATIONS_PATH)
    pairs = colocated_pairs(reg)
    print(f"  {len(pairs)} co-located cross-source pair(s) "
          f"({int((pairs.how == 'declared').sum())} declared, "
          f"{int((pairs.how == 'distance').sum())} within {COLOCATION_M:.0f} m)\n")

    rows = []
    for p in pairs.itertuples():
        for ch in params.NAMES:
            r = compare(p.uid_a, p.uid_b, ch)
            if r:
                rows.append({**r, "sep_m": p.sep_m, "how": p.how})
    out = pd.DataFrame(rows)
    if not len(out):
        print("  no channel is carried by both halves of any pair -- nothing to verify")
        return out

    print(f"{'channel':20s}{'pairs':>6s}{'matched':>10s}{'statistic':>22s}  verdict")
    for ch, g in out.groupby("channel", sort=False):
        c = params.CHANNELS[ch]
        if c.scale_kind == "interval":
            # An arbitrary origin makes a ratio meaningless; agreement is a difference within tolerance.
            # Two bands, because they mean different things: a wrong unit on an interval scale is GROSS
            # (Celsius read as Fahrenheit differs by 15-25 degrees), while a degree or so between two
            # agency thermistors at different depths is instrument disagreement and not a units finding.
            d = float(g.abs_diff.median())
            gross = 0.1 * (c.hi - c.lo) if c.hi is not None and c.lo is not None else float("inf")
            stat = f"median |diff| {d:>7.3f}"
            verdict = (f"confirms the declared unit (within {c.agree_tol:g} {c.unit})" if d <= c.agree_tol
                       else f"UNIT LOOKS WRONG -- {d:.3g} {c.unit} apart" if d > gross
                       else f"unit is right; instruments differ by {d:.3g} {c.unit}")
        else:
            med = float(g.ratio.median())
            spread = float(g.ratio.max() - g.ratio.min()) if len(g) > 1 else 0.0
            stat = f"median ratio {med:>8.4f}"
            verdict = ("confirms the declared unit" if abs(med - 1.0) <= 0.02
                       else f"SCALE MISMATCH -- out by about {med:.4g}x"
                       if np.isfinite(med) else "no usable ratio")
            if len(g) > 1 and spread > 0.5:
                verdict += f"; spread {spread:.2f}, check the pairs are the same water"
        print(f"{ch:20s}{len(g):>6d}{int(g.n.sum()):>10,}{stat:>22s}  {verdict}")

    unv = {(c, s) for c, s, _ in params.unverified()}
    covered = {(r.channel, schema.split_uid(r.uid_a)[0]) for r in out.itertuples()} | \
              {(r.channel, schema.split_uid(r.uid_b)[0]) for r in out.itertuples()}
    still = sorted(unv - covered)
    if still:
        print(f"\n  {len(still)} unverified mapping(s) with no co-located evidence, still assumed:")
        for c, s in still:
            print(f"    {c:20s} {s}")
    return out


def main() -> pd.DataFrame:
    print("\n[units] cross-source agreement on co-located sensors")
    out = report()
    if len(out):
        p = config.PROCESSED / "units_check.csv"
        from . import io as bio

        bio.write_csv(out, p)
        print(f"\n  wrote {p}")
    return out


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    main()
