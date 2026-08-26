"""Build-side entry to the snap machinery: the pure `snap()` lives in `data.access.machinery.snap` (the access layer exposes the same machinery a modeler delineates with, and access never imports the build -- so the shared function lives on the access side and the build imports it, which is the allowed direction). This module keeps the ACQUIRED-store loader `main()` the D-stages call.
"""

from __future__ import annotations

import pandas as pd

from data.access.machinery.snap import (  # noqa: F401 -- the build's stages import these from here
    MAX_SNAP_M, SNAP_AREA_MATCH_MAX_LOGERR, SNAP_AREA_MATCH_RADIUS_M, SNAP_AREA_RESCUE_LOGERR,
    SNAP_SEARCH_RADII_M, SNAP_TIE_TOLERANCE_M, snap, usable_area,
)

def main(sites: pd.DataFrame, force: bool = False) -> pd.DataFrame:
    import geopandas as gpd

    from ..acquire import network

    if not network.exists():
        raise FileNotFoundError("network layers are missing -- run the a3 network branch first")
    fl = gpd.read_parquet(network._path("flowlines"))
    cat = gpd.read_parquet(network._path("catchments"))
    vaa = pd.read_parquet(network._path("vaa"))

    block = snap(sites, fl, cat, vaa)
    n_ok = int((block.snap_status == "ok").sum())
    print(f"  snapped {n_ok}/{len(block)} within {MAX_SNAP_M:.0f} m; "
          f"median {block.snap_dist_m.median():.1f} m")
    far = block[block.snap_status == "far"]
    if len(far):
        print(f"  {len(far)} beyond {MAX_SNAP_M:.0f} m (kept, flagged): "
              f"{far.site_uid.head(5).tolist()}")
    noloc = block[block.snap_status == "no_location"]
    if len(noloc):
        print(f"  {len(noloc)} with no coordinates at all (coordinate-sentinel class): "
              f"{noloc.site_uid.head(5).tolist()}")
    if "comid_agree" in block.columns:
        n_ag = int((block.comid_agree == True).sum())          # noqa: E712 -- Boolean dtype, `is True` fails
        dis = block[block.comid_agree == False]                # noqa: E712
        print(f"  containing catchment agrees with the snap on {n_ag}/{int(block.comid_agree.notna().sum())}"
              f"; {int(block.catchment_comid.isna().sum())} site(s) in no catchment at all")
        if len(dis):
            near = dis[dis.snap_dist_m <= 50]
            # NAMED, not counted. A disagreement means the sensor sits near a divide, and the ones that matter are the CLOSE snaps -- a reach 4 m away that the point does not fall inside is the mainstem-through-a-divergence case, where the containing catchment belongs to a neighbouring tributary. A far snap disagreeing is just a far snap.
            print(f"  {len(dis)} disagree ({len(near)} of them snapped within 50 m -- near a divide):")
            nm = sites.set_index("site_uid").station_name
            for r in dis.sort_values("snap_dist_m").itertuples():
                print(f"    {r.site_uid:22s} comid {r.comid} vs catchment {r.catchment_comid} "
                      f"at {r.snap_dist_m:>8.2f} m  {str(nm.get(r.site_uid, ''))[:38]}")
    return block
