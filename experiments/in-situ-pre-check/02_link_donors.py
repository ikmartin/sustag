"""Link each cohort site to a discharge donor at every exclusion radius, and report the coverage curve.

    python 02_link_donors.py                 # euclidean selection -> cache/donors.parquet
    python 02_link_donors.py --containment   # also test point-in-basin, the network-aware variant

THE POINT OF THE EXCLUSION RADIUS. 36 IWQIS cohort sites sit within 200 m of a USGS gauge, so the naive nearest-gauge feature reads the target's OWN discharge -- untrainable-then-undeployable, and LOFO cannot catch it because LOFO holds out sites, not instrument availability. Hiding every gauge within `r` and taking the nearest survivor simulates a pin that only reaches distant gauges.

Measured in the report and re-derived here: coverage stays at 100% of the cohort for every r up to 100 km, so the radius is a free design parameter rather than a coverage trade-off. The 0.1 -> 12.2 km jump between r=0 and r=1 is the co-location itself.

`up`/`down` here is BASIN CONTAINMENT, not elevation: a donor inside the cohort basin is upstream, a donor whose own basin contains the cohort sensor is downstream. Only the first is free -- the second needs the donor's delineation, which is why --containment resolves upstream only and leaves downstream to a later NLDI pass.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from common import CACHE, RADII_KM, cohort_lonlat, km, say

SITES = CACHE / "iv_sites.parquet"
OUT = CACHE / "donors.parquet"


def _pool(covariate: str) -> pd.DataFrame:
    if not SITES.exists():
        raise SystemExit(f"{SITES} missing -- run 01_enumerate_iv_sites.py first")
    d = pd.read_parquet(SITES)
    d = d[d.covariate == covariate]
    if d.empty:
        raise SystemExit(f"no {covariate} sites cached")
    return d.reset_index(drop=True)


def link_euclidean(covariate: str, topk: int = 3) -> pd.DataFrame:
    """One row per (cohort site, radius, rank): the `topk` nearest donors surviving that radius.

    TOP-K, NOT TOP-1, because a linked donor is not necessarily a USABLE one: 44 of the 215 rank-1 donors publish instantaneous values but no daily-mean product, which drops effective coverage from 100% to ~81%. Step 04 walks the ranks and takes the nearest donor that actually has a series, which restores it. k=3 is the cheap default; raise it if step 04 still reports gaps.
    """
    pool, own = _pool(covariate), cohort_lonlat()
    D = km(own.lon.values[:, None], own.lat.values[:, None], pool.lon.values[None, :], pool.lat.values[None, :])
    rows = []
    for r in RADII_KM:
        masked = np.where(D > r, D, np.inf)
        order = masked.argsort(axis=1)[:, :topk]
        for rank in range(order.shape[1]):
            j = order[:, rank]
            dist = masked[np.arange(len(own)), j]
            rows.append(pd.DataFrame({
                "site": own.index, "radius_km": r, "rank": rank + 1,
                "donor": np.where(np.isfinite(dist), pool.site_no.values[j], None),
                "donor_km": np.where(np.isfinite(dist), dist, np.nan),
            }))
    return pd.concat(rows, ignore_index=True)


def link_containment(covariate: str) -> pd.DataFrame:
    """Donors falling INSIDE each cohort basin -- the upstream half of the network relation, for free.

    A point-in-polygon test against the basin the pipeline already delineates. The downstream half needs each donor's own basin and is deliberately not attempted here; it is the expensive step the report defers to NLDI.
    """
    import geopandas as gpd
    from shapely.geometry import Point
    from src.data.access import get_basin

    pool = _pool(covariate)
    pts = gpd.GeoDataFrame(pool, geometry=[Point(x, y) for x, y in zip(pool.lon, pool.lat)], crs="EPSG:4326")
    rows = []
    for s in cohort_lonlat().index:
        try:
            b = get_basin(s)
        except Exception as e:
            say(f"    [warn] {s}: no basin ({type(e).__name__})")
            continue
        geom = b.geometry.union_all() if hasattr(b, "geometry") else b
        inside = pts[pts.within(geom)]
        rows.append({"site": s, "n_upstream_donors": len(inside),
                     "donors": ",".join(inside.site_no.head(20))})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--covariate", default="discharge", help="which enumerated covariate to link (default discharge)")
    ap.add_argument("--topk", type=int, default=3, help="nearest-N donors per radius, so step 04 can fall back past donors with no daily series")
    ap.add_argument("--containment", action="store_true", help="also run the point-in-basin upstream test (needs geopandas)")
    a = ap.parse_args()

    d = link_euclidean(a.covariate, a.topk)
    d.to_parquet(OUT, index=False)

    say(f"\nDONOR COVERAGE by exclusion radius -- {a.covariate}\n")
    say(f"{'r (km)':>8}{'with donor':>13}{'median km':>12}{'p90 km':>10}")
    for r, g in d[d["rank"] == 1].groupby("radius_km"):
        has = g.donor.notna()
        med = g.donor_km[has].median() if has.any() else float("nan")
        p90 = g.donor_km[has].quantile(0.9) if has.any() else float("nan")
        say(f"{r:>8.0f}{has.mean():>12.0%}{med:>11.1f}{p90:>10.1f}")
    say(f"\nwrote {OUT}  ({len(d)} rows)")
    say("\nREAD r=0 AS THE CONTROL, NOT A CANDIDATE: it is the co-located feature, and its gap to r=1 is the")
    say("skill that comes from reading the target's own gauge rather than a genuine donor.")

    if a.containment:
        c = link_containment(a.covariate)
        c.to_parquet(CACHE / "donors_containment.parquet", index=False)
        n = c.n_upstream_donors
        say(f"\nPOINT-IN-BASIN (upstream only): basins with >=1 donor {(n > 0).mean():.0%}, "
            f">=5 {(n >= 5).mean():.0%}, median {n.median():.0f}, max {n.max()}")
        say("Compare against the euclidean linking above before spending the NLDI pass: if the two")
        say("disagree materially, straight-line distance is selecting hydrologically unrelated donors.")


if __name__ == "__main__":
    main()
