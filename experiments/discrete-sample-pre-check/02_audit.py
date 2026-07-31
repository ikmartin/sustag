"""E1 -- the credibility audit. Decides whether the volunteer pool is usable, and whether the block is measurable at all.

    python 02_audit.py                 # -> cache/audit_basins.parquet + a printed verdict
    python 02_audit.py --flow-bias     # also test low-flow sampling bias (needs the in-situ pre-check's discharge cache)

SIX QUESTIONS, in the order they can kill the idea:

  1 COVERAGE   how many cohort basins contain any discrete station at all, with and without the volunteer pool
  2 DENSITY    how many samples per basin -- four samples is a level estimate, one sample is noise
  3 AGREEMENT  where a basin holds both populations, do their basin means agree?
  4 ERA        which agricultural regime the samples describe
  5 PREDICTION does a basin aggregate predict the site's OWN sensor level? -- THE DECISION NUMBER
  6 FLOW BIAS  grab campaigns oversample accessible conditions; a low-flow-biased mean is a biased level

(5) SUPERSEDES (3) AS THE STOP CONDITION. The report's original rule was "if the pools disagree AND agency-only coverage is thin, do not run the CV". Measured, both limbs hold -- and (5) still says the agency aggregate carries real between-site signal. Agreement between two pools is a proxy for usefulness; correlation against the target quantity is the thing itself, so when they conflict, (5) wins. Kill the block only if (5) is near zero.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from common import CACHE, VIOLATION_MGL, cohort, is_volunteer, say

SAMPLES = CACHE / "wqp_nitrate.parquet"
OUT = CACHE / "audit_basins.parquet"


def basin_membership(d: pd.DataFrame) -> pd.DataFrame:
    """Which discrete stations fall inside which cohort basin. One point-in-polygon pass, cached, because it is the expensive step and every later question is a groupby over it."""
    import geopandas as gpd
    from shapely.geometry import Point
    from src.data.access import get_basin

    st = (d.groupby("MonitoringLocationIdentifier")
            .agg(lon=("lon", "first"), lat=("lat", "first")).reset_index())
    pts = gpd.GeoDataFrame(st, geometry=[Point(x, y) for x, y in zip(st.lon, st.lat)], crs="EPSG:4326")
    rows = []
    for s in cohort():
        try:
            b = get_basin(s)
        except Exception:
            continue
        geom = b.geometry.union_all() if hasattr(b, "geometry") else b
        for sid in pts[pts.within(geom)].MonitoringLocationIdentifier:
            rows.append({"site": s, "station": sid})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flow-bias", action="store_true")
    a = ap.parse_args()

    if not SAMPLES.exists():
        raise SystemExit(f"{SAMPLES} missing -- run 01_fetch_wqp.py first")
    d = pd.read_parquet(SAMPLES)
    d["vol"] = is_volunteer(d.OrganizationFormalName)
    d["dt"] = pd.to_datetime(d.ActivityStartDate, errors="coerce")

    # station coordinates come from the Station service, not Result -- fetched here rather than in 01 so the
    # big Result pull stays a single request
    import io, urllib.parse, urllib.request
    p = {"statecode": "US:19", "siteType": "Stream", "characteristicName": "Nitrate", "mimeType": "csv", "zip": "no"}
    raw = urllib.request.urlopen("https://www.waterqualitydata.us/data/Station/search?" + urllib.parse.urlencode(p), timeout=600).read().decode(errors="replace")
    stn = pd.read_csv(io.StringIO(raw), dtype=str, low_memory=False)
    stn["lon"] = pd.to_numeric(stn.LongitudeMeasure, errors="coerce")
    stn["lat"] = pd.to_numeric(stn.LatitudeMeasure, errors="coerce")
    d = d.merge(stn[["MonitoringLocationIdentifier", "lon", "lat"]], on="MonitoringLocationIdentifier", how="left").dropna(subset=["lon", "lat"])

    mem = basin_membership(d)
    mem.to_parquet(OUT, index=False)
    j = mem.merge(d, left_on="station", right_on="MonitoringLocationIdentifier")

    say("\n1. COVERAGE -- cohort basins containing >=N discrete nitrate stations")
    say(f"   {'pool':18s}{'>=1':>8}{'>=5':>8}{'>=20':>8}{'median':>9}")
    for lab, sub in (("all orgs", j), ("agency only", j[~j.vol])):
        c = sub.groupby("site").station.nunique().reindex(cohort()).fillna(0)
        say(f"   {lab:18s}{(c >= 1).mean():>7.0%}{(c >= 5).mean():>8.0%}{(c >= 20).mean():>8.0%}{c.median():>9.0f}")

    say("\n2. DENSITY -- samples per basin (a basin mean over <5 samples is noise, not a level)")
    for lab, sub in (("all orgs", j), ("agency only", j[~j.vol])):
        n = sub.groupby("site").size().reindex(cohort()).fillna(0)
        say(f"   {lab:18s} median {n.median():>6.0f}   p25 {n.quantile(.25):>6.0f}   basins with <5 samples: {(n < 5).mean():.0%}")

    say("\n3. AGREEMENT -- basins holding BOTH populations: volunteer mean vs agency mean")
    piv = j.groupby(["site", "vol"]).n_mgl.agg(["mean", "size"]).unstack("vol")
    both = piv.dropna(subset=[("mean", True), ("mean", False)])
    if len(both) >= 5:
        vm, am = both[("mean", True)], both[("mean", False)]
        diff = vm - am
        say(f"   {len(both)} basins hold both. volunteer - agency: median {diff.median():+.2f} mg/L, "
            f"IQR [{diff.quantile(.25):+.2f}, {diff.quantile(.75):+.2f}]")
        say(f"   rank correlation between the two basin means: {vm.corr(am, method='spearman'):+.2f}")
        say("   VERDICT: " + ("populations agree -- use the full pool and take the wider coverage"
                              if abs(diff.median()) < 1.0 and vm.corr(am, method="spearman") > 0.5
                              else "populations DISAGREE -- use agency only, and re-read coverage above"))
    else:
        say(f"   only {len(both)} basins hold both -- cannot compare; default to agency only")

    say("\n4. ERA -- a 1998-sampled basin and a 2022-sampled basin describe different agricultural regimes")
    yr = j.groupby("site").dt.median()
    say(f"   median sample year per basin: p10 {yr.quantile(.1).year}, median {yr.median().year}, p90 {yr.quantile(.9).year}")

    say("\n5. DIRECT PREDICTIVE CHECK -- does a basin grab-aggregate predict the site's OWN sensor level?")
    say("   This supersedes (3) as the decision number. (3) asks whether two pools agree with each other;")
    say("   this asks whether either predicts the quantity the feature exists to supply. A pool can fail (3)")
    say("   and still pass this, and only this one bears on whether the block is worth a CV run.")
    from src.features.features import _state_daily_wide
    sensor = _state_daily_wide().mean()
    say(f"   {'pool':16s}{'basins':>8}{'spearman':>10}{'pearson':>9}{'median |err|':>14}")
    for lab, sub in (("all orgs", j), ("volunteer only", j[j.vol]), ("agency only", j[~j.vol])):
        gm = sub.groupby("site").n_mgl.mean()
        common = [x for x in gm.index if x in sensor.index and pd.notna(sensor[x])]
        if len(common) < 10:
            say(f"   {lab:16s}{len(common):>8}{'--':>10}{'--':>9}{'--':>14}")
            continue
        aa, bb = gm[common], sensor[common]
        say(f"   {lab:16s}{len(common):>8}{aa.corr(bb, method='spearman'):>+10.2f}{aa.corr(bb):>+9.2f}"
            f"{(aa - bb).abs().median():>14.2f}")
    say("   A pool whose aggregate anti-correlates or sits near zero cannot help the between-site channel,")
    say("   and no amount of CV will rescue it. Pooling that DILUTES the best single pool is evidence against")
    say("   pooling, not against the data.")

    if a.flow_bias:
        say("\n6. FLOW BIAS -- discharge on sample days vs all days, where a co-located gauge exists")
        qf = Path(__file__).resolve().parents[1] / "in-situ-pre-check" / "cache" / "discharge_daily.parquet"
        if not qf.exists():
            say("   skipped: run the in-situ pre-check's 03_fetch_discharge.py first")
        else:
            q = pd.read_parquet(qf)
            allq = q.groupby("donor").discharge.median()
            days = set(zip(j.site, j.dt.dt.normalize()))
            say(f"   {len(allq)} gauges cached; {len(days):,} distinct basin-sample-days available to join")
            say("   (join gauge->basin via the in-situ pre-check's donors.parquet at radius 0 to complete this)")

    say(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
