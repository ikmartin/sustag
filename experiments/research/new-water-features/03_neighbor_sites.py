"""STEP 3 -- covariate-bearing USGS sites that sit NEAR one of our nitrate sites.

Two opportunities, worth very different things:

  CO-LOCATED (<= COLOC_KM)  a gauge effectively at the same place on the same river. Its covariate
      can be attached to OUR site as a local measurement. This is the only route by which the 92
      IWQIS sites -- which have no discharge at all in the delivered export -- can get one.

  NEARBY (up to --max-km)   a separate location. Its covariate is a NEIGHBOUR signal, usable only
      through the network features being designed in experiments/pre-expansion-network-test.
      Straight-line distance is a SCREEN, NOT a connectivity test: two sites 5 km apart on
      different tributaries are hydrologically unrelated, and Follow-up C of that report shows the
      reach graph -- not proximity -- is what carries signal. Anything surviving this screen must
      be re-tested on the reach graph before being treated as a donor.

Scoring matches script 02: each candidate series is scored by `overlap_yr` against the nitrate record of the nearest of OUR sites, because a donor covariate is only worth the period over which it co-observes the target. No absolute record-length cutoff (the retired 3.92 yr bar) is applied.

    python 03_neighbor_sites.py [--min-overlap 1.0] [--max-km 50]
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from shapely import wkb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from pcodes import CORE_PCODES, pcode_family, pcode_kind

ROOT = HERE.parents[2]
RAW = ROOT / "src/data/raw/water"
META = ROOT / "src/data/processed/water/meta/site_location_metadata.csv"
DATA = HERE / "data"
COLOC_KM = 5.0


def haversine_km(lat1, lon1, lat2, lon2):
    """Pairwise great-circle distance, (n,) x (m,) -> (n, m) km."""
    R = 6371.0088
    p1, p2 = np.radians(lat1)[:, None], np.radians(lat2)[None, :]
    dphi = p2 - p1
    dlam = np.radians(lon2)[None, :] - np.radians(lon1)[:, None]
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlam / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def nitrate_window(uid: str):
    src = "USGS" if uid.startswith("USGS-") else "IWQIS"
    f = RAW / src / f"{uid}_water.parquet"
    if not f.exists():
        return None
    d = pd.read_parquet(f, columns=["nitrate_con"])
    n = d["nitrate_con"].dropna()
    if n.empty:
        return None
    return n.index.min(), n.index.max()


def our_sites() -> pd.DataFrame:
    good = set(json.load(open(RAW / "filtered_sites.json"))["good_sites"])
    m = pd.read_csv(META, dtype={"site_uid": str})
    m = m[m.site_uid.isin(good)].dropna(subset=["latitude", "longitude"]).copy()
    m["source"] = np.where(m.site_uid.str.startswith("USGS-"), "USGS", "IWQIS")
    w = {u: nitrate_window(u) for u in m.site_uid}
    m["n_start"] = [w[u][0] if w[u] else pd.NaT for u in m.site_uid]
    m["n_end"] = [w[u][1] if w[u] else pd.NaT for u in m.site_uid]
    return m[["site_uid", "latitude", "longitude", "source", "n_start", "n_end"]].reset_index(drop=True)


def _point(g):
    if g is None:
        return None
    return wkb.loads(bytes(g)) if isinstance(g, (bytes, bytearray)) else g


def main(min_overlap=1.0, max_km=50.0):
    ours = our_sites()
    print(f"our good sites with coords: {len(ours)} ({(ours.source=='USGS').sum()} USGS, "
          f"{(ours.source=='IWQIS').sum()} IWQIS); nitrate window resolved for "
          f"{ours.n_start.notna().sum()}")

    ts = pd.read_parquet(DATA / "region_ts_metadata.parquet")
    ts["parameter_code"] = ts.parameter_code.astype(str)
    for c in ("begin", "end"):
        ts[c] = pd.to_datetime(ts[c], errors="coerce", utc=True)
    fam, kind = pcode_family(), pcode_kind()
    ts["family"] = ts.parameter_code.map(fam)
    ts["kind"] = ts.parameter_code.map(kind)
    ts = ts[(ts.kind == "insitu") & ts.family.notna()].copy()
    ts["pt"] = ts.geometry.map(_point)
    ts = ts[ts.pt.notna()].copy()
    ts["lon"] = ts.pt.map(lambda p: p.x)
    ts["lat"] = ts.pt.map(lambda p: p.y)

    # nearest of OUR sites for every candidate series
    D = haversine_km(ts.lat.values, ts.lon.values, ours.latitude.values, ours.longitude.values)
    j = D.argmin(axis=1)
    ts["dist_km"] = D.min(axis=1)
    ts["nearest_uid"] = ours.site_uid.values[j]
    ts["nearest_src"] = ours.source.values[j]
    ns, ne = ours.n_start.values[j], ours.n_end.values[j]
    lo = np.maximum(ts.begin.values, ns)
    hi = np.minimum(ts.end.values, ne)
    ts["overlap_yr"] = np.maximum((hi - lo) / np.timedelta64(1, "D"), 0) / 365.25
    ts["is_ours"] = ts.monitoring_location_id.isin(set(ours.site_uid))

    cand = ts[(~ts.is_ours) & (ts.overlap_yr >= min_overlap) & (ts.dist_km <= max_km)]
    print(f"external series within {max_km:g} km with >= {min_overlap:g} yr overlap: "
          f"{len(cand)} series at {cand.monitoring_location_id.nunique()} sites\n")

    site = (cand.groupby("monitoring_location_id")
                .agg(lat=("lat", "first"), lon=("lon", "first"), dist_km=("dist_km", "first"),
                     nearest_uid=("nearest_uid", "first"), nearest_src=("nearest_src", "first"),
                     overlap_yr=("overlap_yr", "max"),
                     families=("family", lambda s: "+".join(sorted(set(s)))))
                .reset_index().sort_values("dist_km"))
    site.to_csv(DATA / "neighbor_sites.csv", index=False)

    rows = []
    for f in sorted(cand.family.unique()):
        sub = site[[f in s.split("+") for s in site.families]]
        rows.append(dict(
            family=f,
            in_core=all(p in CORE_PCODES for p in cand[cand.family == f].parameter_code.unique()),
            coloc_5km=int((sub.dist_km <= COLOC_KM).sum()),
            within_10km=int((sub.dist_km <= 10).sum()),
            within_25km=int((sub.dist_km <= 25).sum()),
            within_50km=int((sub.dist_km <= 50).sum()),
            our_sites_gaining_coloc=sub[sub.dist_km <= COLOC_KM].nearest_uid.nunique(),
        ))
    r = pd.DataFrame(rows).sort_values("coloc_5km", ascending=False)
    print("=== EXTERNAL covariate gauges by distance to our nearest nitrate site ===")
    print(r.to_string(index=False))
    r.to_csv(DATA / "neighbor_family_bands.csv", index=False)

    co = site[site.dist_km <= COLOC_KM]
    print(f"\n=== CO-LOCATED external gauges (<= {COLOC_KM:g} km): {len(co)} gauges "
          f"attaching to {co.nearest_uid.nunique()} of our sites ===")
    print(co[["monitoring_location_id", "dist_km", "nearest_uid", "nearest_src",
              "overlap_yr", "families"]]
          .to_string(index=False, float_format=lambda x: f"{x:.2f}", max_colwidth=55))
    co.to_csv(DATA / "colocated_gauges.csv", index=False)

    print(f"\n--- co-location gain, split by which of OUR sources benefits ---")
    g = (co.groupby("nearest_src").nearest_uid.nunique()
           .rename("our_sites_gaining").reset_index())
    print(g.to_string(index=False))
    iw = co[co.nearest_src == "IWQIS"]
    print(f"\nIWQIS sites (which have NO discharge in the delivered export) gaining a co-located "
          f"gauge: {iw.nearest_uid.nunique()}")
    print(f"  ... of which the gauge carries discharge: "
          f"{iw[[('discharge' in s.split('+')) for s in iw.families]].nearest_uid.nunique()}")
    # ---- the number that matters for a NEIGHBOUR feature: what share of OUR sites is covered ----
    print(f"\n=== coverage of OUR {len(ours)} sites: share with >=1 external gauge within R km ===")
    print("(the analogue of the 47%/88% nitrate-neighbour coverage in "
          "experiments/pre-expansion-network-test)\n")
    cov = []
    for f in sorted(ts.family.unique()):
        sub = ts[(ts.family == f) & (~ts.is_ours)]
        g = sub.groupby("monitoring_location_id").agg(lat=("lat", "first"), lon=("lon", "first"))
        if not len(g):
            continue
        D2 = haversine_km(ours.latitude.values, ours.longitude.values, g.lat.values, g.lon.values)
        mn = D2.min(axis=1)
        cov.append(dict(family=f, n_gauges_region=len(g),
                        **{f"within_{r}km": f"{(mn <= r).mean():.0%}" for r in (5, 10, 25, 50)}))
    cv = pd.DataFrame(cov).sort_values("n_gauges_region", ascending=False)
    print(cv.to_string(index=False))
    cv.to_csv(DATA / "our_site_coverage.csv", index=False)
    print("\nCAVEAT: straight-line distance, not network connectivity. These are upper bounds on\n"
          "donor availability -- the reach-graph test is what decides whether a gauge is a donor.")

    print(f"\nwrote {DATA/'neighbor_sites.csv'}, {DATA/'neighbor_family_bands.csv'}, "
          f"{DATA/'colocated_gauges.csv'}, {DATA/'our_site_coverage.csv'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-overlap", type=float, default=1.0)
    ap.add_argument("--max-km", type=float, default=50.0)
    a = ap.parse_args()
    main(min_overlap=a.min_overlap, max_km=a.max_km)
