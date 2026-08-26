"""Put every registration on a reach: nearest-flowline snap, and the catchment it lands in.

NEAREST FLOWLINE, NEVER `/comid/position`. The containing-catchment endpoint answers a different question -- which polygon holds this point -- and near a confluence that is the wrong reach: the old build recorded a case where it returned an unnamed order-2 tributary 510 m away while the mainstem ran 4 m from the sensor (`src/build/_make_basins.py:12-17`). Snapping to the nearest line gets the reach the sensor is actually measuring.

BOTH ANSWERS ARE KEPT, and `comid_agree` records whether they match. On this cohort they agree on 363 sites, disagree on 25, and 4 points fall in no catchment at all. Grouping on the containment id yields EXACTLY the same 27 merge groups over the same 58 sites as grouping on the snap -- it finds nothing the snap misses and misses nothing the snap finds -- so it is useless as a second candidate source and valuable as an independent corroboration of the partition.

The 25 disagreements are the payload. Twelve are snapped within 50 m (`WQS0061` at 4.75 m, `WQS0066` at 4.20 m, `WQS0073` at 3.19 m): the mainstem carries no catchment of its own, so the point falls inside a neighbour's polygon. Four are `snap_status = far`, where the disagreement says only that neither id is trustworthy. So `comid_agree == False` reads as NEAR A DIVIDE -- the flag worth having before delineating a basin or believing a candidate pair, and the reason chunk 4 delineates `basin2` from `catchment_comid` alongside `basin1` and compares the two.

TWO GAUGES CAN SHARE A REACH, and `snap_pos_m` is what orders them. Every other positional attribute -- `pathlength_km`, `totma_d`, `stream_order` -- belongs to the REACH, so two sites on one COMID carry identical values and nothing downstream can tell which is upstream. That matters most for the graph: an edge between two gauges has to point downhill, and a pair with no defined order can be given an edge in either direction, which is how a cycle enters a graph that is supposed to be a DAG. 58 sites sit on 27 shared reaches here.

A FAILED SNAP KEEPS ITS ROW. Engineered channels are missing from NHDPlus entirely; three Florida canals failed in the old build. `snap_status` records it and the site stays in the table.

WHY THIS IS CHUNK 2 AND NOT CHUNK 1. It is a DERIVATION, not a download -- it reads the flowline layer chunk 1 fetched and computes a nearest-line join. Chunk 1's rule is "everything fetched, nothing derived", and its consumer is the merge rule; basins accumulate upstream from `comid` in chunk 4. The COMID it assigns is the site's ORIGIN -- its anchor in the river network, and the join key for every COMID-keyed attribute table.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config

# Beyond this a "nearest" reach is not plausibly the one being measured; the row is kept and flagged.
MAX_SNAP_M = 500.0

# NEAREST IS NOT ENOUGH, AND THE OLD BUILD KNEW IT. `_make_basins.snap_comid` carried three rules that a bare nearest-line join does not, and dropping them cost 13 gauges: `Missouri River at Napoleon, MO` snapped to an unnamed order-1 reach draining 3.8 km2, with the Missouri's own centreline 5.9 m further away, and `SUSQUEHANNA RIVER NEAR DARLINGTON` snapped to Deer Creek at 318 m while the Susquehanna ran at 475 m. Both are the same geometry problem: a wide river's digitised centreline is far from its bank, and the gauge is on the bank. Each rule is ported with the constant it was proven at.
#
# TIE TOLERANCE. Among reaches within this many metres of the closest, prefer the largest upstream drainage area. Resolves a confluence toward the mainstem without dragging a genuine tributary sensor onto it.
SNAP_TIE_TOLERANCE_M = 25.0

# AREA REFINEMENT. Where the operator reports a drainage area, match it in log space among reaches within this radius rather than taking the strictly nearest. This is the rule for a sensor a few hundred metres from the mainstem it monitors and a few metres from a parallel tributary.
SNAP_AREA_MATCH_RADIUS_M = 600.0

# A reported area this far (log-ratio) from every nearby reach is unmatchable -- the coordinates or the area are wrong -- so the snap falls back to the base rule rather than confidently snapping to the wrong reach.
SNAP_AREA_MATCH_MAX_LOGERR = 0.69          # ~2x

# THE REPORTED AREA MAY RESCUE A BAD SNAP; IT MAY NOT SECOND-GUESS A GOOD ONE. The area rule only runs when the base choice is already off by more than this. Without the guard it fires on sites that were correct: `EMBARRAS RIVER AT LAWRENCEVILLE` matched the authority's basin to 0.01% from a reach 68 m away, and area matching moved it 333 m to a reach that fitted the REPORTED figure 0.3% better -- a figure itself only good to about a percent. Twelve gauges moved that way, all of them for a margin smaller than the reported area's own precision. The old build had no such guard and did not need one, because its flowline layer was clipped at stream order 3 and there was usually nothing else nearby to move to.
SNAP_AREA_RESCUE_LOGERR = 0.22             # ~1.25x

# Search radii, widened only until something is found. Beyond the first, `snap_status` will read `far` anyway.
SNAP_SEARCH_RADII_M = (500.0, 2_000.0, 10_000.0)

# REPORTED AREAS THAT ARE NOT AREAS. IWQIS writes one square mile as a placeholder on sites whose basin it does not know, and 145.04 is the known-bad figure for the Des Moines at Boone (the river drains ~14,290). Feeding either to the area refinement snaps a mainstem gauge onto whatever ditch is 2.6 km2. Compared in km2 with a tolerance, because the value arrives as a float converted from square miles.
_PLACEHOLDER_AREA_KM2 = (2.589988, 145.04)
_PLACEHOLDER_TOL = 0.01

# Reaches below this Strahler order are never snap targets. `None` disables the filter, which is the current setting: the old build clipped its whole flowline layer at order 3 and that is what made small reaches invisible, but it also made a genuine order-1 or order-2 sensor unsnappable. The three rules above fix the mis-snaps without it, so the filter stays available and off. Raise it only with evidence that a specific failure needs it, and expect it to cost the smallest headwater sites.
MIN_STREAM_ORDER = None

# Keyed on the LOWERCASED source name, matched case-insensitively. The national geodatabase returns `COMID`, `StreamOrde`, `Pathlength`, `TOTMA`, `AreaSqKM` -- mixed and inconsistent casing, with `TOTMA` fully upper where its neighbours are not. An exact-case lookup silently matches nothing and leaves the whole snap block null, which is precisely the bug the old build shipped: `download_flowlines` filtered on `"streamorde"` while the source returned `StreamOrde`, so its `min_stream_order` never once fired.
_VAA_COLS = {
    "streamorde": "stream_order", "streamleve": "stream_level", "terminalpa": "terminal_path",
    "pathlength": "pathlength_km", "totma": "totma_d", "areasqkm": "catchment_area_km2",
}


def _rename_vaa(df):
    """Map source columns onto contract names, case-insensitively. Returns (frame, comid_column)."""
    ren, low = {}, {c.lower(): c for c in df.columns}
    for src, dst in _VAA_COLS.items():
        if src in low:
            ren[low[src]] = dst
    key = next((low[c] for c in ("comid", "featureid") if c in low), None)
    return df.rename(columns=ren), key


def _containing_catchment(pts, catchments, uids) -> pd.Series:
    """COMID of the catchment each point falls inside, aligned to `pts` by INDEX. Int64, null where none.

    ALIGNED, NOT TRUNCATED. `sjoin(how="left")` emits one row per match, so a point inside two overlapping catchments adds a row and pushes every site after it onto somebody else's id -- and the previous `to_numpy()[: len(out)]` slice made that a silent positional shift rather than an error. Catchments are supposed to partition the surface and on this cohort they do (392 points, 392 rows, no multi-hits), which is exactly why the failure would have gone unnoticed.

    A multi-hit resolves to the LOWEST comid and prints the sites, because "first" out of a spatial index is not stable across runs and this column feeds a basin delineation.
    """
    import geopandas as gpd

    cat = catchments.to_crs(config.EQUAL_AREA)
    ccol = next((c for c in ("featureid", "FEATUREID", "comid", "COMID") if c in cat.columns), None)
    if ccol is None:
        print(f"  WARNING: no COMID column in the catchment layer; got {list(cat.columns)[:8]}")
        return pd.Series(pd.array([None] * len(pts), dtype="Int64"), index=pts.index)

    left = gpd.GeoDataFrame(geometry=pts.values, crs=config.EQUAL_AREA, index=pts.index)
    j = left.sjoin(cat[[ccol, "geometry"]], predicate="within", how="left")
    hits = pd.to_numeric(j[ccol], errors="coerce").groupby(level=0)
    if j.index.has_duplicates:
        multi = hits.count()
        names = [str(u) for u in pd.Series(list(uids), index=pts.index)[multi > 1]]
        print(f"  WARNING: {len(names)} site(s) inside more than one catchment, "
              f"taking the lowest comid: {names[:5]}")
    return hits.min().reindex(pts.index).astype("Int64")


def usable_area(value) -> float | None:
    """A reported drainage area the snap may trust, in km2 -- or None.

    Rejects nulls, zeros and the two known placeholders. A placeholder is worse than a missing value: absent, the snap falls back to the base rule, while `2.589988 km2` actively steers a mainstem gauge onto whatever ditch happens to drain one square mile.
    """
    if value is None or pd.isna(value):
        return None
    v = float(value)
    if v <= 0:
        return None
    if any(abs(v - p) <= _PLACEHOLDER_TOL for p in _PLACEHOLDER_AREA_KM2):
        return None
    return v


def _choose(cands, dist, totda, reported) -> tuple[int, str]:
    """Pick one reach from the candidates near a point. Returns (position in `cands`, rule that decided it).

    THE BASE RULE DECIDES FIRST and usually decides alone:

      mainstem  among reaches within `SNAP_TIE_TOLERANCE_M` of the closest, the largest drainage area
      nearest   only one reach is that close

    The reported area is then allowed to OVERRULE that choice, but only as a rescue: the base choice has to be off by more than `SNAP_AREA_RESCUE_LOGERR` before the area is consulted at all, and the replacement has to fit better and to within `SNAP_AREA_MATCH_MAX_LOGERR`. Running the area rule first instead moves gauges that were already right, because a reported area is good to about a percent and the rule would happily trade 265 m of distance for a 0.3% better fit.

    Zero-drainage reaches are dropped before any of it -- NHDPlus divergence artifacts carry no catchment, and they are precisely what `/comid/position` kept mis-snapping to.
    """
    live = np.where(np.isfinite(totda) & (totda > 0))[0]
    if not len(live):
        return int(np.argmin(dist)), "nearest"

    dmin = dist[live].min()
    tie = live[dist[live] <= dmin + SNAP_TIE_TOLERANCE_M]
    base = int(tie[np.argmax(totda[tie])])
    rule = "mainstem" if len(tie) > 1 else "nearest"

    if reported is None:
        return base, rule
    base_err = abs(np.log(totda[base] / reported))
    if base_err <= SNAP_AREA_RESCUE_LOGERR:
        return base, rule

    near = live[dist[live] <= SNAP_AREA_MATCH_RADIUS_M]
    if not len(near):
        return base, rule
    err = np.abs(np.log(totda[near] / reported))
    k = int(np.argmin(err))
    if err[k] <= SNAP_AREA_MATCH_MAX_LOGERR and err[k] < base_err:
        return int(near[k]), "area"
    return base, rule


def snap(sites: pd.DataFrame, flowlines, catchments=None, vaa=None) -> pd.DataFrame:
    """Return the snap block, one row per site: comid, snap_dist_m, snap_status, plus VAA columns.

    Distances are computed on EPSG:5070, so metres are metres rather than degrees pretending to be. Reach choice is `_choose`; `snap_rule` records which of its three rules decided each site, so a surprising COMID can be traced to the rule that produced it rather than guessed at.
    """
    import geopandas as gpd
    from shapely import STRtree

    pts = gpd.GeoSeries(gpd.points_from_xy(sites.lon, sites.lat), crs=4326).to_crs(config.EQUAL_AREA)
    fl = flowlines.to_crs(config.EQUAL_AREA)
    fl_comid = pd.to_numeric(fl.get("comid", fl.get("COMID")), errors="coerce").to_numpy()
    geoms = fl.geometry.values

    # Drainage area per reach, and stream order if the filter is on. Both come from the VAA rather than from the flowline attributes, so one table is the authority for what a reach drains.
    da = np.full(len(fl), np.nan)
    if vaa is not None and len(vaa):
        low = {c.lower(): c for c in vaa.columns}
        key = next((low[c] for c in ("comid", "featureid") if c in low), None)
        if key is not None and "totdasqkm" in low:
            m = dict(zip(pd.to_numeric(vaa[key], errors="coerce").to_numpy(),
                         pd.to_numeric(vaa[low["totdasqkm"]], errors="coerce").to_numpy()))
            da = np.array([m.get(c, np.nan) for c in fl_comid])
    if not np.isfinite(da).any():
        print("  WARNING: no TotDASqKM available; the snap falls back to NEAREST for every site, "
              "which is the rule that mis-snapped 13 gauges onto reaches beside the ones they measure.")

    order = None
    if MIN_STREAM_ORDER is not None:
        oc = next((c for c in ("streamorde", "StreamOrde", "STREAMORDE") if c in fl.columns), None)
        if oc is not None:
            order = pd.to_numeric(fl[oc], errors="coerce").to_numpy()

    reported = [usable_area(v) for v in sites.get("source_drainage_area_km2", pd.Series(index=sites.index))]

    tree = STRtree(geoms)
    chosen, dist, pos, rules = [], [], [], []
    for i, pt in enumerate(pts.values):
        cands = np.array([], dtype="int64")
        for radius in SNAP_SEARCH_RADII_M:
            cands = tree.query(pt.buffer(radius))
            if order is not None and len(cands):
                keep = cands[np.nan_to_num(order[cands], nan=0) >= MIN_STREAM_ORDER]
                cands = keep if len(keep) else cands
            if len(cands):
                break
        if not len(cands):
            j = int(tree.nearest(pt))
            cands = np.array([j], dtype="int64")
        d = np.array([pt.distance(geoms[j]) for j in cands])
        k, rule = _choose(cands, d, da[cands], reported[i])
        j = int(cands[k])
        chosen.append(j)
        dist.append(float(d[k]))
        rules.append(rule)
        # WHERE ON THE REACH, not just which reach. `LineString.project` returns distance from the line's first vertex, and NHDPlus digitises in the direction of flow -- verified on this layer, where a reach's LAST vertex coincides exactly with its `dnhydroseq` neighbour's FIRST -- so a larger value is further downstream. See the column's contract entry for why nothing else can supply this.
        pos.append(float(geoms[j].project(pt)))

    idx = np.array(chosen, dtype="int64")
    dist = np.array(dist)

    out = pd.DataFrame({
        "site_uid": sites.site_uid.to_numpy(),
        "comid": pd.array(fl_comid[idx], dtype="Int64"),
        "snap_dist_m": dist.round(2),
        "snap_pos_m": np.array(pos).round(2),
        "snap_rule": pd.array(rules, dtype="string"),
    })
    out["snap_status"] = np.where(dist <= MAX_SNAP_M, "ok", "far")

    if catchments is not None and len(catchments):
        out["catchment_comid"] = _containing_catchment(pts, catchments, sites.site_uid)
        both = out.comid.notna() & out.catchment_comid.notna()
        out["comid_agree"] = pd.array(
            np.where(both, out.comid == out.catchment_comid, None), dtype="boolean")

    if vaa is not None and len(vaa):
        v, key = _rename_vaa(vaa)
        if key is None:
            print("  WARNING: no COMID column in VAA; snap block will lack the VAA fields")
        else:
            keep = [key] + [c for c in _VAA_COLS.values() if c in v.columns]
            out = out.merge(v[keep].rename(columns={key: "comid"}).drop_duplicates("comid"),
                            on="comid", how="left")
    return out


def main(sites: pd.DataFrame, force: bool = False) -> pd.DataFrame:
    import geopandas as gpd

    from ..chunk1 import network

    if not network.exists():
        raise FileNotFoundError("network layers are missing -- run chunk 1 branch B (network) first")
    fl = gpd.read_parquet(network._path("flowlines"))
    cat = gpd.read_parquet(network._path("catchments"))
    vaa = pd.read_parquet(network._path("vaa"))

    block = snap(sites, fl, cat, vaa)
    n_ok = int((block.snap_status == "ok").sum())
    print(f"  snapped {n_ok}/{len(block)} within {MAX_SNAP_M:.0f} m; "
          f"median {block.snap_dist_m.median():.1f} m")
    far = block[block.snap_status != "ok"]
    if len(far):
        print(f"  {len(far)} beyond {MAX_SNAP_M:.0f} m (kept, flagged): "
              f"{far.site_uid.head(5).tolist()}")
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
