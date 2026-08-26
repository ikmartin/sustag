"""Compute areas of interest over a sweep of `max_dist_from_sensor_km`.

One traversal per sensor at the LARGEST distance; smaller distances are a filter on the recorded distance, so the sweep costs one pass rather than one per value.

    python experiments/research/aoe-boxes/01_compute_aoe.py

Writes data/aoe_clusters.csv (one row per cluster per distance) and, to the scratchpad, the per-distance reach sets the figure script draws.
"""

from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import aoe  # noqa: E402

_ROOT = _HERE.parents[2]
_SCRATCH = Path("/private/tmp/claude-501/-Users-isaac-dev-sustag/"
                "4b2640b0-7d62-4647-b7b9-5138f4af4269/scratchpad/aoe")
_OUT = _HERE / "data"

DISTANCES_KM = (25.0, 50.0, 100.0, 250.0, 350.0, 500.0)
MIN_STREAM_ORDER = 1     # 1 = every reach is a valid pin; raise to restrict deployment targets
MAX_BOXES = None         # None = keep every cluster; an int selects the largest N
SNAP_MAX_KM = 5.0


def box_area_km2(b) -> float:
    latm = (b[1] + b[3]) / 2
    return (b[2] - b[0]) * 111.32 * np.cos(np.radians(latm)) * (b[3] - b[1]) * 110.57


def load_seeds() -> pd.DataFrame:
    """Every in-situ nitrate sensor we know of -- USGS, the IWQIS cohort, and the MPCA network.

    The AOE is seeded by SENSORS, so a network absent from the seed set is absent from the region. MPCA matters here specifically: 30 of its 32 stations report nitrate nowhere in NWIS or WQP, so a USGS-only seeding silently omits them and the corn-belt region stops short of Minnesota's nitrate network.
    """
    parts = []
    u = pd.read_parquet(_ROOT / "notes" / "graphics" / "_usgs_nitrate_conus.parquet")
    parts.append(u.rename(columns={"site_no": "seed_id", "station_nm": "name"})
                  .assign(source="USGS")[["seed_id", "name", "lat", "lon", "state", "source"]])

    sl = pd.read_csv(_ROOT / "src/data/raw/water/raw_site_list.csv")
    c = {x.lower(): x for x in sl.columns}
    uid, la, lo = c.get("site_uid") or c.get("uid"), c.get("lat") or c.get("latitude"), c.get("lon") or c.get("longitude")
    iw = sl.dropna(subset=[la, lo]).drop_duplicates(subset=[uid])
    parts.append(pd.DataFrame(dict(seed_id=iw[uid].astype(str), name=iw[uid].astype(str),
                                   lat=iw[la].astype(float), lon=iw[lo].astype(float),
                                   state="ia", source="IWQIS")))

    mp = _ROOT / "src/data/raw/water/MPCA_CSG/mpca_csg_sites.csv"
    if mp.exists():
        m = pd.read_csv(mp, dtype={"csg_id": str, "usgs_id": str})
        parts.append(pd.DataFrame(dict(seed_id="MPCA-" + m.csg_id, name=m.name,
                                       lat=m.lat.astype(float), lon=m.lon.astype(float),
                                       state="mn", source="MPCA")))
    else:
        print("  [WARN] MPCA site list absent -- seeds are USGS + IWQIS only")

    d = pd.concat(parts, ignore_index=True)
    d = d[(d.lon > -125) & (d.lon < -66) & (d.lat > 24) & (d.lat < 50)]
    # A station registered by two programs is one seed; co-located duplicates would only
    # inflate counts, not change the union, but the counts are reported.
    d["_k"] = list(zip(d.lat.round(4), d.lon.round(4)))
    d = d.drop_duplicates("_k").drop(columns="_k").reset_index(drop=True)
    print(f"seeds: {len(d)}  " + "  ".join(f"{k} {v}" for k, v in d.source.value_counts().items()))
    return d


def main() -> None:
    net = aoe.build_network(_SCRATCH / "nhdplusVAA.parquet", _SCRATCH / "comid_xy.parquet")
    sensors = load_seeds()
    idx, snap_km = aoe.snap(net, sensors.lat.values, sensors.lon.values, SNAP_MAX_KM)
    sensors = sensors.assign(reach=idx, snap_km=snap_km)
    ok = sensors[sensors.reach >= 0].reset_index(drop=True)
    print(f"sensors {len(sensors)}; snapped {len(ok)} (median {np.median(snap_km):.3f} km, "
          f"max {snap_km.max():.1f} km)")
    for _, r in sensors[sensors.reach < 0].iterrows():
        print(f"  UNSNAPPED  [{r.source}] {r.seed_id}  {r.name}")

    dmax = max(DISTANCES_KM)
    print(f"\ntraversing to {dmax:.0f} km ...")
    t = time.time()
    full = []
    for k, seed in enumerate(ok.reach.tolist()):
        r, dd = net.reachable(int(seed), dmax, MIN_STREAM_ORDER)
        full.append((r, dd))
        if (k + 1) % 50 == 0:
            print(f"  {k+1}/{len(ok)}  {time.time()-t:.0f}s")
    print(f"  done in {time.time()-t:.0f}s; median {np.median([len(r) for r, _ in full]):.0f} reaches/sensor")

    rows, per_distance = [], {}
    for D in DISTANCES_KM:
        sets = [r[dd <= D] for r, dd in full]
        groups = aoe.clusters(sets, MAX_BOXES)
        reach_union_all = set()
        cl_rows = []
        for gi, members in enumerate(groups):
            reaches = np.unique(np.concatenate([sets[m] for m in members]))
            reach_union_all |= set(reaches.tolist())
            lat, lon = net.lat[reaches], net.lon[reaches]
            bbox = [float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())]
            area = box_area_km2(bbox)
            cl_rows.append(dict(dist_km=D, cluster=gi, n_sensors=len(members), n_reaches=len(reaches),
                                bbox=[round(v, 3) for v in bbox], box_area_km2=round(area),
                                km2_per_sensor=round(area / len(members))))
        per_distance[D] = dict(groups=groups, sets=sets)
        rows.extend(cl_rows)
        singles = sum(1 for g in groups if len(g) == 1)
        print(f"\nD={D:6.0f} km   {len(groups):3d} clusters ({singles} singletons)   "
              f"{len(reach_union_all):>9,} reaches   total box area "
              f"{sum(r['box_area_km2'] for r in cl_rows)/1e6:.2f}M km2")
        for r in cl_rows[:6]:
            print(f"    c{r['cluster']:<2d} n={r['n_sensors']:3d} reaches={r['n_reaches']:>8,} "
                  f"{r['box_area_km2']/1e3:7.0f}k km2  {r['bbox']}")

    _OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(_OUT / "aoe_clusters.csv", index=False)
    with open(_SCRATCH / "per_distance.pkl", "wb") as f:
        pickle.dump(dict(per_distance=per_distance, lat=net.lat, lon=net.lon,
                         sensors=ok[["seed_id", "name", "lat", "lon", "state", "source"]]), f)
    print(f"\nwrote {(_OUT / 'aoe_clusters.csv').relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
