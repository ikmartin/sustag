"""What might be wrong with a delineation. Advisory only -- flags drive the review queue, never the selection.

FIVE INTERNAL CHECKS AND ONE EXTERNAL. The internal ones compare a basin against another measurement of itself: our accumulation against NHDPlus's own, the authority's polygon against the snap's, the raster against the vector, the site against its own basin. They are cheap and they catch a delineation that is inconsistent.

They cannot catch the failure that matters most, because it is consistent. A sensor on a small inlet running into a large river, whose basin snaps to the river, agrees with every other method that also snapped to the river and with the river's own drainage area. Every self-check passes while the answer is wrong by three orders of magnitude. `flag_river_capture` is the only check that looks outside the delineation for evidence.

WHY IT IS NOT THE OLD PROXIMITY FLAG. `_make_basins.flag_river` fired when a site sat within 3 km of a reach draining over 10,000 km2 -- 20 of 116 sites, and its own comment conceded "expect legitimate mainstem-monitoring sites to flag too; that is the intended trade." Asking "is this site near a big river" flags the Mississippi gauges, which are supposed to be on the Mississippi.

The question worth asking is whether the site was CAPTURED by the big river: is its basin the river's when it should have been something smaller beside it. Two signals were measured on this cohort and rejected before the third was kept:

  SNAP DISTANCE does not discriminate. A large river is wide, so its gauge sits 100-430 m from the digitised centreline; all 26 sites beyond 100 m are genuinely named for their river.

  REPORTED DRAINAGE AREA is unusable. Of six sites whose assigned basin dwarfs the reported one, five report exactly 2.589988 km2 -- one square mile -- or the known-bad 145.04 for the Des Moines at Boone. They are placeholders, which is why `chunk0.basins` already deleted a ratio test built on them.

  THE NAME is what carries it. 56 of 62 large-river sites share a word between their station name and their reach's GNIS name. A site named for a different watercourse than the reach it sits on is the signature of a capture.

AND THE CAUSE IS ALREADY GONE, which is why this is a safety net rather than an alarm. The old build snapped against flowlines filtered to `streamorde >= 3`, so small inlets were invisible and the mainstem was the only candidate; `chunk3.snap` snaps to the nearest line of any order. WQS0065 and WQS0066 -- the two worked examples, given the Missouri at 765,000 km2 by the old build -- now resolve to the Little Sioux at 9,220 and the Monona-Harrison Ditch at 2,359.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

# A reach this large is a river whose basin dwarfs anything a capture would have taken instead.
RIVER_DA_KM2 = 10_000.0

# How far to look for a reach the site might really belong to, and how much smaller it has to be. 100x is not a tuning knob -- it is the difference between a tributary and the river it joins, and anything closer than that is a judgement about hydrology rather than a detectable error.
ALT_RADIUS_M = 1_500.0
ALT_DA_FACTOR = 100.0

# Tolerances. `area_vs_vaa` is tight because it compares our accumulation against NHDPlus's own over the same topology -- it agrees to 0.01% on this cohort, so anything past 5% is a broken walk, not a rounding error. `d8` is wide because a 15 arc-second raster against NHDPlus vector will never agree closely: the old build used 4% and its flag fired on 61 of 116 sites, which is not a flag, it is noise.
AREA_VS_VAA_TOL = 0.05
BASIN0_TOL = 0.10
D8_TOL = 0.35

MAX_CONUS_KM2 = 3.5e6

# How far outside its own basin a site may sit before that means something. NOT zero: a gauge sits ON the outlet reach, so the boundary runs through it and the sign of a few metres is coordinate precision.
#
# 250 m IS THE RESOLUTION OF THE CLAIM, NOT A TUNING KNOB. Measured on this cohort the distances form two populations with a gap between them. Eight basin0 selections sit 50-165 m outside NLDI's polygon, which is a GENERALISED rendering of a basin -- its boundary is not placed to 50 m, so a site just outside it says nothing about the delineation. The other population is 0.2-9.1 km and is structural: the snapped reach carries no catchment of its own (the divergence case chunk 3 records as `comid_agree == False`), so the dissolve has no polygon for the basin's own outlet and the boundary stops short of the sensor by the length of that reach. WQS0065 at 5.4 km and WQS0066 at 9.1 km are exactly the sites chunk 3 flagged.
#
# THE SECOND KIND IS NOT AN AREA ERROR. Those basins still reproduce `vaa.totdasqkm` to four decimals, because a divergence reach carries no drainage area to lose, and chunk 5's covariates come from the REACH SET rather than from the polygon. What it costs is a polygon that contains its own gauge -- which matters for a map, for a point-in-basin test, and for the containment graph. A reviewer who needs that can select basin3.
NOT_CONTAINED_TOL_KM = 0.25

_STOP = {"river", "creek", "at", "near", "nr", "the", "of", "ditch", "abv", "above", "below",
         "blw", "and", "trib", "tributary", "fork", "branch", "run", "lake", "pond"}


def _words(s) -> set[str]:
    """Content words of a name, lowercased. Watercourse nouns are stripped -- every name has 'river' in it."""
    if s is None or (isinstance(s, float) and np.isnan(s)) or pd.isna(s):
        return set()
    return {w for w in re.sub(r"[^a-z ]", " ", str(s).lower()).split()
            if len(w) > 2 and w not in _STOP}


def informative(name, uid=None) -> bool:
    """Does this name say anything about WHERE the site is?

    A station called `WQS9903` does not, and must not be tested against a reach name -- it would mismatch every time and flag a site whose delineation is fine. Stripping non-letters is not enough to catch it: `WQS9903` leaves the token `wqs`, which passes a naive length test. The reliable test is against the uid itself, because an uninformative station name is almost always the registration id repeated.
    """
    w = _words(name)
    if not w:
        return False
    if uid is not None:
        tag = re.sub(r"[^a-z]", "", str(uid).lower())
        if all(x in tag for x in w):        # every "word" is a fragment of the id -- it names nothing
            return False
    return True


def river_capture(site, reach_name, reach_da, alternatives, uid=None) -> bool:
    """True when a site's basin looks like it was captured by a large river beside it.

    `alternatives` is `[(gnis_name, totdasqkm, distance_m), ...]` for reaches near the site other than the one it snapped to. All three conditions must hold, and each rules out a different kind of false positive:

      the assigned reach is large       -- a capture only matters when the wrong answer is huge
      a much smaller reach is close     -- there has to be something it should have been instead
      the names point at the smaller one -- which is what separates capture from a real mainstem gauge
    """
    if reach_da is None or pd.isna(reach_da) or reach_da < RIVER_DA_KM2:
        return False
    near = [(n, da, d) for n, da, d in alternatives
            if d <= ALT_RADIUS_M and pd.notna(da) and da > 0 and reach_da / da >= ALT_DA_FACTOR]
    if not near:
        return False
    if not informative(site, uid):
        return False                       # a uid-only station name cannot be compared to anything
    sw = _words(site)

    # THE ASSIGNED REACH GETS THE FIRST SAY. If the station is named for the river it snapped to, it is on that river and nothing else matters -- checking the neighbours first fires on every big-river gauge with a same-named tributary beside it, which is most of them: Delaware at Chester, Potomac near Washington and Wabash at New Harmony all have a smaller `Delaware`/`Potomac`/`Wabash` something within 1.5 km.
    if informative(reach_name) and (sw & _words(reach_name)):
        return False
    # Otherwise: named for one of the smaller neighbours, or named for something the big reach is not.
    return bool(any(sw & _words(n) for n, _, _ in near)) or informative(reach_name)


def capture_evidence(sites: pd.DataFrame, net, radius_m: float = ALT_RADIUS_M) -> pd.DataFrame:
    """What `river_capture` needs, per site: the assigned reach's name and drainage area, and its neighbours'.

    One pass over the flowline layer for the whole cohort. The layer is 1.25 GB and has no usable spatial index on disk, so the neighbourhood search is an in-memory STRtree over every reach -- the same read `chunk3.snap` already does, and the reason this is a single function rather than a per-site lookup.

    Returns `site_uid, reach_name, reach_da_km2, alt_name, alt_da_km2, alt_dist_m, n_alternatives, flag_river_capture`, where the `alt_*` columns describe the SMALLEST nearby reach -- the one a captured site most likely belongs to, and the one a reviewer wants named.
    """
    import geopandas as gpd
    from shapely import STRtree

    from ..chunk1 import network

    fl = gpd.read_parquet(network._path("flowlines"), columns=["COMID", "GNIS_NAME", "geometry"])
    fl = fl.to_crs("EPSG:5070")
    comids = pd.to_numeric(fl.COMID, errors="coerce").to_numpy()
    names = fl.GNIS_NAME.to_numpy()
    geoms = fl.geometry.values
    tree = STRtree(geoms)

    pts = gpd.GeoSeries(gpd.points_from_xy(sites.lon, sites.lat), crs=4326).to_crs("EPSG:5070").values
    rows = []
    for site, pt in zip(sites.itertuples(), pts):
        cid = None if pd.isna(site.comid) else int(site.comid)
        reach_name = None
        reach_da = np.nan if cid is None else float(net.totda.get(cid, np.nan))
        hits = tree.query(pt.buffer(radius_m))
        alts = []
        for j in hits:
            c = int(comids[j])
            d = float(pt.distance(geoms[j]))
            if c == cid:
                reach_name = names[j]
                continue
            alts.append((names[j], float(net.totda.get(c, np.nan)), d))
        if reach_name is None and cid is not None:
            k = np.where(comids == cid)[0]
            reach_name = names[k[0]] if len(k) else None
        best = min((a for a in alts if pd.notna(a[1]) and a[1] > 0), key=lambda a: a[1], default=None)
        rows.append({
            "site_uid": site.site_uid,
            "reach_name": reach_name,
            "reach_da_km2": reach_da,
            "alt_name": None if best is None else best[0],
            "alt_da_km2": np.nan if best is None else best[1],
            "alt_dist_m": np.nan if best is None else best[2],
            "n_alternatives": len(alts),
            "flag_river_capture": river_capture(site.station_name, reach_name, reach_da, alts,
                                                uid=site.site_uid),
        })
    return pd.DataFrame(rows)


def _outside(d, tol_km: float) -> bool:
    """Is the site outside its own basin by more than `tol_km`? NaN reads as "no basin", not as "outside"."""
    return bool(pd.notna(d) and float(d) > tol_km)


def _ratio_off(a, b, tol: float) -> bool:
    """|a-b|/b beyond `tol`. False whenever either side is missing or the base is zero -- an absent comparison is not a disagreement."""
    if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
        return False
    return abs(a - b) / abs(b) > tol


def evaluate(row) -> dict:
    """Every flag for one gauge, as a dict of booleans. `row` carries the areas and the capture evidence.

    A GAUGE WITH NO BASIN GETS NO FLAGS. Every one of these is a claim about a delineation -- that it disagrees with the authority, that it does not contain its own gauge, that a river captured it -- and where nothing was delineated the claim has no subject. Without this the wells came back into the review by the side door: `IWQIS:WQS0029` has no basin because it is a well, and it was still queued as `river_capture` because its snapped reach is the Des Moines River. The queue then asked a reviewer to choose among four methods that had all correctly declined to run.
    """
    if not (row.get("basin_method") if isinstance(row.get("basin_method"), str) else None):
        return {c: False for c in FLAG_COLS}
    return {
        # Our accumulation against NHDPlus's, over the same topology. A disagreement means the walk is wrong.
        "flag_area_vs_vaa": _ratio_off(row.get("basin1_km2"), row.get("vaa_totda_km2"), AREA_VS_VAA_TOL),
        # The authority's own delineation against the snap's.
        "flag_basin0_disagrees": _ratio_off(row.get("basin0_km2"), row.get("basin1_km2"), BASIN0_TOL),
        # An independent method sharing no code or data with NHDPlus.
        "flag_d8_disagrees": _ratio_off(row.get("basin3_km2"), row.get("basin1_km2"), D8_TOL),
        # NaN is NOT outside. A gauge with no basin has no distance to one, and reading that absence as a containment failure files it under the wrong question -- `selection_mode == 'none'` is what queues it.
        "flag_not_contained": _outside(row.get("dist_to_basin_km"), NOT_CONTAINED_TOL_KM),
        "flag_oversized": bool((row.get("basin_km2") or 0) > MAX_CONUS_KM2),
        "flag_river_capture": bool(row.get("flag_river_capture", False)),
    }


FLAG_COLS = ["flag_area_vs_vaa", "flag_basin0_disagrees", "flag_d8_disagrees",
             "flag_not_contained", "flag_oversized", "flag_river_capture"]


def corroboration(row) -> tuple[int, str]:
    """How many INDEPENDENT delineations confirm this basin, and which. Returns `(n, "basin0,basin3")`.

    THE POINT IS THE SITES WITH NO AUTHORITY. 198 of 388 gauges have a `basin0` to be checked against; the other 190 are IWQIS, MPCA and the USGS registrations NLDI's index omits, and for those the snap is trusted rather than verified. They are also the majority of the cohort, so "basin1 agrees with basin0" is a statement about the half of the data that needed checking least. This column is what the other half carries instead.

    CORROBORATION IS THE FLAGS READ POSITIVELY. A method confirms when it EXISTS and its disagreement flag did NOT fire, so the thresholds are the ones already calibrated per method rather than a new number invented here.

    WHAT COUNTS AS INDEPENDENT, and this is the whole substance of the column:

      basin0  the authority's own polygon. Independent of everything we compute.
      basin3  a D8 raster flood-fill sharing no code and no data with NHDPlus.
      basin2  the same accumulation from the CONTAINING catchment -- and it counts ONLY where `comid_agree` is False. Where the two ids match, basin2 is bit-for-bit the same computation on the same reach, so counting it would be counting basin1 twice and would hand every unremarkable gauge a free corroboration.

    A gauge with `n == 0` is not wrong; it is unverified, which is a different thing and worth being able to select on.
    """
    ok = []
    if pd.notna(row.get("basin0_km2")) and not _ratio_off(row.get("basin0_km2"), row.get("basin1_km2"), BASIN0_TOL):
        ok.append("basin0")
    if pd.notna(row.get("basin3_km2")) and not _ratio_off(row.get("basin3_km2"), row.get("basin1_km2"), D8_TOL):
        ok.append("basin3")
    if (row.get("comid_agree") is False and pd.notna(row.get("basin2_km2"))
            and not _ratio_off(row.get("basin2_km2"), row.get("basin1_km2"), BASIN0_TOL)):
        ok.append("basin2")
    return len(ok), ",".join(ok)
