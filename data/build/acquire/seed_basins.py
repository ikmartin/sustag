"""One upstream drainage basin per seed, from NLDI.

WHY A WEB SERVICE AND NOT THE LOCAL NETWORK. Chunk 0 runs before anything is downloaded, and its whole job is to decide how much to download. Accumulating catchments locally would need the NHDPlus network, which is the thing being scoped -- so the basin comes from NLDI, which returns a polygon per site with nothing on disk. Chunk 4 rebuilds basins properly by accumulating catchments; these are only for setting the extent.

TWO METHODS, MATCHING THE OLD BUILD'S TWO GOOD ONES. A USGS site is addressed by its own site number and NLDI returns the AUTHORITY's delineation -- `basin0`, preferred over anything inferred from coordinates. Everything else is snapped to the NEAREST REACH and delineated from that COMID -- `basin1`, the old build's default for sites without an NWIS id.

NOT `/comid/position`, which is the demoted `basin2`. It returns the catchment CONTAINING the point rather than the nearest reach, and a mainstem through a divergence carries no catchment of its own, so a sensor sitting on the mainstem falls inside a neighbouring tributary's catchment and the service returns the tributary. WQS0061 lost a 1,057 km2 order-5 river 4 m away to an unnamed order-2 reach 510 m off -- 98.5% understated, reproduced here to the decimal at 15.6 km2. Two other sites came back 83x and 324x too LARGE. An understatement like WQS0061's is the dangerous kind: nothing downstream can detect it, and it quietly shrinks the AOE.

The snap needs flowlines near the sensor, NOT the whole network -- a 0.01-degree box returns a handful of reaches in about 1.5 s, so it costs one small request per non-USGS seed and no local layer. Verified against the old build on WQS0061, WQS0065, WQS0066 and WQS0032: four exact COMID matches at 4.1, 36.4, 3.2 and 18.8 m.

CACHED PER SITE, because the pass is slow and idempotent and an interrupted run should resume rather than restart. A cached basin is trusted without revalidation; `--force` is the way to refetch.
"""

from __future__ import annotations

import warnings
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .. import config, contracts as schema

_WORKERS = 8

# THE ONLY CHECK CHUNK 0 MAKES, and it guards the AOE UNION rather than judging a site.
#
# NLDI occasionally returns a basin that is wrong by orders of magnitude: a coordinate-named site on Cape Cod (414224070452001) came back with 5,063,593 km2 spanning -113.9 to -70.7, having been snapped onto the Mississippi. Two such polygons inflated the AOE from 1.43x the seed boxes to 1.94x -- a 36% larger raster download bought by a delineation no site in scope could have. Anything above the Mississippi at Natchez, ~2.97M km2, is impossible here whatever site it belongs to.
#
# There was also a ratio test against the source's reported drainage area. It is GONE. Judging whether a basin is right is chunk 4's job, where basins are delineated by accumulating catchments and a human reviews the selection -- and the reported areas it trusted are unreliable enough to have been wrong every time it fired. `WQS0028` is `Des Moines River at Boone`: IWQIS reports 145 km2, NLDI returned 14,289, and the old build's basin1 (14,289.2 on the same COMID), the NWIS authority (14,298) and IWQIS's own neighbouring Stratford site (14,120) all agree the river drains about 14,290. `WQS0052` matched the old build to the decimal at 70.4 km2 and was rejected against a 2-square-mile placeholder.
MAX_CONUS_KM2 = 3.5e6


# The snap window, in degrees of half-width -- about 1.1 km. THERE IS NO WIDER SECOND PASS. If no reach is found here the sensor is off the NHDPlus medium-resolution network, and widening until something turns up returns a stream that is not the one being measured: `BRADLEY CREEK AT LOUISVILLE` has zero reaches within 1.1 km, and the widened search handed it an unnamed reach 2,142 m away carrying 67 km2 of somebody else's drainage. Off-network is a fact about the site, recorded as such, not a search to try harder at.
_SNAP_HALF_DEG = 0.01


def _nldi():
    from pynhd import NLDI

    return NLDI()


def snap_comid(lon: float, lat: float) -> tuple[int | None, float]:
    """COMID of the flowline NEAREST a coordinate, by fetching the local reaches and measuring.

    This is the old build's `basin1`, and the reason it is worth the extra request is that the alternative is wrong in a way nothing catches. `/comid/position` returns the catchment CONTAINING the point, and a mainstem reach through a divergence carries no catchment of its own -- so a sensor sitting ON the mainstem falls inside a neighbouring tributary's catchment and the service hands back the tributary. WQS0061 lost a 1,057 km2 order-5 river 4 m away to an unnamed order-2 reach 510 m off, understating by 98.5%.

    Only the neighbourhood is fetched, not the network: a 0.01-degree box returns a handful of reaches in about 1.5 s. Verified against the old build's snap on WQS0061, WQS0065, WQS0066 and WQS0032 -- four exact COMID matches, at 4.1, 36.4, 3.2 and 18.8 m.
    """
    import geopandas as gpd
    from pynhd import NHD
    from shapely.geometry import Point, box

    client = NHD("flowline_mr")
    pt = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(config.EQUAL_AREA).iloc[0]
    half = _SNAP_HALF_DEG
    try:
        fl = client.bygeom(box(lon - half, lat - half, lon + half, lat + half), geo_crs=4326)
    except Exception:
        return None, float("nan")        # ZeroMatchedError included: no reaches in the window
    if not len(fl):
        return None, float("nan")
    d = fl.to_crs(config.EQUAL_AREA).geometry.distance(pt)
    i = int(d.idxmin())
    col = "comid" if "comid" in fl.columns else "COMID"
    return int(fl.loc[i, col]), float(d.loc[i])


# The non-surface guard now asks chunk 3's classifier, which owns the type vocabulary. Chunk 0 gets a PRIOR
# from metadata alone -- it runs three steps before the AOE union and cannot wait for a snap that does not
# exist yet. See `chunk3.site_type.prior_no_surface` for why a prior is enough here.
from ..derive.site_type import prior_no_surface


def _fetch_one(nldi, source: str, native_id: str, lon: float, lat: float,
               site_type_raw=None, station_name=None):
    """`(geometry, method)` for one seed, or `(None, reason)`. Never raises -- a failure is a missing seed, not a dead run.

    BASIN0 IF PRESENT, ELSE BASIN1, which is the old build's own preference order. A USGS site number gets the authority's delineation; when NLDI has no COMID for it the site falls back to the coordinate snap rather than going without.

    The fallback is not rare and not exotic: NLDI's `nwissite` index simply omits some registrations, answering `404 No comid for feature USGS-01477050 in source nwissite` for the Delaware at Chester, the Susquehanna at Columbia and Bradley Creek at Louisville. Snapping recovers the first two convincingly -- Delaware at 26,755 km2 against 26,677 reported, 0.3% apart.
    """
    if prior_no_surface(source, site_type_raw, station_name):
        return None, "no_surface_basin"
    try:
        if source == "USGS":
            try:
                return nldi.get_basins(native_id).geometry.iloc[0], "basin0"
            except Exception:
                pass                      # not in the nwissite index; snap instead
        comid, dist = snap_comid(float(lon), float(lat))
        if comid is None:
            return None, "off_network"
        g = nldi.get_basins(str(comid), fsource="comid").geometry.iloc[0]
        return g, "basin1"
    except Exception:
        return None, "fetch_failed"


def fetch_seed_basins(sites: pd.DataFrame, force: bool = False, workers: int = _WORKERS) -> pd.DataFrame:
    """Basin geometry for every `aoe_seed` row. Returns site_uid, geometry, nldi_basin_km2.

    Threaded because the calls are independent and network-bound. NLDI is a different service from the waterdata API the records fetch hammers, so this concurrency does not stack with `sources.usgs._CONCURRENT`.
    """
    import geopandas as gpd

    config.ACQ_SEED_BASINS.mkdir(parents=True, exist_ok=True)
    seeds = sites[sites.aoe_seed.fillna(False)]
    todo, cached = [], []
    for r in seeds.itertuples():
        p = config.ACQ_SEED_BASINS / f"{schema.uid_to_filename(r.site_uid)}.parquet"
        if p.exists() and not force:
            cached.append(p)
        else:
            todo.append((r.site_uid, r.source, r.source_site_id, r.lon, r.lat,
                         getattr(r, "site_type_raw", None), getattr(r, "station_name", None), p))

    print(f"  basins: {len(cached)} cached, {len(todo)} to fetch", flush=True)
    outcomes: dict[str, str] = {}
    if todo:
        nldi = _nldi()

        def one(args):
            uid, src, nid, lon, lat, raw, name, path = args
            g, method = _fetch_one(nldi, src, nid, lon, lat, raw, name)
            if g is None:
                return uid, None, method
            gpd.GeoDataFrame({"site_uid": [uid], "nldi_basin_method": [method]},
                             geometry=[g], crs=4326).to_parquet(path)
            return uid, path, method

        done = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for uid, path, method in pool.map(one, todo):
                done += 1
                outcomes[uid] = method
                if path is not None:
                    cached.append(path)
                if done % 25 == 0:
                    print(f"    {done}/{len(todo)}", flush=True)
        if outcomes:
            print(f"  methods: {pd.Series(list(outcomes.values())).value_counts().to_dict()}")
        for uid, m in outcomes.items():
            if m in ("off_network", "no_surface_basin"):
                print(f"  NO BASIN {uid}: {m}")

    if not cached:
        return gpd.GeoDataFrame({"site_uid": pd.Series(dtype="string")}, geometry=[], crs=4326)

    g = pd.concat([gpd.read_parquet(p) for p in cached], ignore_index=True)
    g = gpd.GeoDataFrame(g, geometry="geometry", crs=4326)
    # NLDI returns at least one self-intersecting polygon; leaving it unrepaired makes the union raise a side-location conflict rather than return a wrong answer, so this is a hard requirement not a tidy-up.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        g["geometry"] = g.geometry.make_valid().buffer(0)
    g["nldi_basin_km2"] = g.to_crs(config.EQUAL_AREA).area / 1e6

    g = _flag_oversized(g)
    missing = set(seeds.site_uid) - set(g.site_uid)
    if missing:
        by = pd.Series([u.split(":")[0] for u in missing]).value_counts().to_dict()
        print(f"  {len(missing)} seed(s) without a basin: {by}")
    return g


def _flag_oversized(g) -> "object":
    """Mark basins too large to be any in-scope site's. Records; `aoe.build` is what excludes them."""
    out = g.copy()
    out["nldi_basin_flag"] = pd.Series(
        ["over_conus_cap" if a > MAX_CONUS_KM2 else pd.NA for a in out.nldi_basin_km2],
        index=out.index, dtype="string")
    for r in out[out.nldi_basin_flag.notna()].itertuples():
        print(f"  OVERSIZED {r.site_uid}: {r.nldi_basin_km2:,.0f} km2 "
              f"-- kept on the table, excluded from the AOE union")
    return out


# ---- authority basins (D4's basin0) ----------------------------------------------------------------
