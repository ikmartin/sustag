"""National Inventory of Dams, assigned to catchments.

THE ONLY CHUNK-1 TABLE THAT IS NOT ALREADY COMID-KEYED. StreamCat and Wieczorek arrive keyed on COMID; NID arrives as 61,766 points with latitude and longitude and nothing else to join on. So a dam belongs to the catchment its coordinate falls inside, by point-in-polygon, and the reduction is a count and two storage sums.

A POINT, NOT AN AREA, so there is no fractional coverage here and nothing to weight -- a dam is in one catchment or none. Dams outside the AOE are dropped, which is most of them.

`yearCompleted` IS KEPT AS `first_year`, THE EARLIEST IN THE CATCHMENT, because a dam does not exist before it is built. A basin's dam count is a function of the year being modelled, and a table that hides the dates makes 1975's basin look like 2020's. Chunk 6 decides whether to use it; chunk 5's job is to not throw it away.

STORAGE IS IN ACRE-FEET, NID's own unit, unconverted. Converting here would bury the unit in a column name; the read layer converts once, where the unit is documented.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config

STORAGE_COLS = {"nidStorage": "nid_storage", "normalStorage": "normal_storage",
                "maxStorage": "max_storage"}

# HEIGHT IS THE BEST AVAILABLE PROXY FOR RESIDENCE. Storage says how much water a structure holds; height
# says how much head it imposes, which is what decides whether a reach behaves as a pool. Taken as the
# MAXIMUM in the catchment rather than the sum -- four farm ponds are not one forty-foot dam. NID publishes
# four height fields and they disagree; `damHeight` is the one populated most consistently.
HEIGHT_COL = "damHeight"


def _path():
    from ..chunk1 import comid_attrs

    return comid_attrs._path("nid")


# A dam is built ON a river, so the reach it impounds is the one it sits closest to. Beyond this it is a
# structure on something else and claiming it would attribute a farm pond to the mainstem.
REACH_SNAP_M = 500.0

_ON_REACH = "dams_on_reach.parquet"


def on_reach(force: bool = False) -> pd.DataFrame | None:
    """Dams keyed on the reach they IMPOUND -- nearest flowline within `REACH_SNAP_M` -- not on the catchment holding them.

    WHY THIS EXISTS BESIDE `build`. A catchment is bounded by drainage divides and a dam is built across one, so point-in-polygon puts it in whichever catchment happens to contain the coordinate. Saylorville Dam is the case that proves it: 641,000 acre-feet on the Des Moines River, and its point falls inside COMID 6597708 -- an order-1, 0.487 km tributary draining 2.29 km2 that joins the river just below the outlet. Counting dams over the reaches an edge crosses therefore found NONE across 22 reaches of reservoir, which is the one thing the column exists to report.

    `build`'s catchment keying stays and is still correct for its own question -- how many dams a BASIN contains, summed over `basin_reaches`. This answers a different one: which reach a structure sits on.
    """
    return _cached(_ON_REACH, build_on_reach, force)


def _cached(name: str, make, force: bool):
    """A stamped product from disk, rebuilt when it is missing, forced, or cut against a DIFFERENT AOE.

    THE STAMP IS THE CACHE KEY, not the filename. `run_chunk5._write` stamps every product with the AOE it was cut against precisely so a join can refuse on mismatch, and an accessor that returns a table on `exists()` alone hands the caller an extent it did not ask for -- silently, and after chunk 0 has already reported the extent as changed. Same discipline as `sources.iwqis._stale`: a derived artifact records the rule it was derived under, and the reader checks it.

    Returns None when the NID pull has not run, so the caller can tell "no dam table" from "no dams here".
    """
    p = config.COMID_FEATURES / name
    if p.exists() and not force:
        d = pd.read_parquet(p)
        if "aoe_stamp" not in d.columns or (d.aoe_stamp == config.aoe_stamp()).all():
            return d
        print(f"  {name} was cut against a different AOE -- rebuilding")
    if not _path().exists():
        return None
    out = make()
    out = out.copy()
    out["aoe_stamp"] = config.aoe_stamp()
    from .. import io as bio

    bio.write_parquet(out, p, index=False)
    return out


def build_on_reach() -> pd.DataFrame:
    """Same aggregate as `build`, keyed on the nearest flowline instead of the containing catchment."""
    import geopandas as gpd
    from shapely import STRtree

    from ..chunk1 import network

    pts = _dam_points()
    fl = gpd.read_parquet(network._path("flowlines"), columns=["COMID", "geometry"]).to_crs(config.EQUAL_AREA)
    p5 = pts.to_crs(config.EQUAL_AREA)
    tree = STRtree(fl.geometry.values)
    idx = tree.nearest(p5.geometry.values)
    dist = p5.geometry.values.distance(fl.geometry.values[idx])
    keep = dist <= REACH_SNAP_M
    print(f"  {int(keep.sum()):,} of {len(p5):,} dam(s) within {REACH_SNAP_M:.0f} m of a reach", flush=True)
    j = p5[keep].copy()
    j["comid"] = pd.to_numeric(fl.COMID.to_numpy()[idx[keep]], errors="coerce").astype("int64")
    j["snap_m"] = dist[keep]
    return _aggregate(j)


def table(force: bool = False) -> pd.DataFrame | None:
    """The catchment-keyed dam table, built on first use. `None` when its chunk-1 input is not on disk yet.

    CHUNK 4 NEEDS THIS AND RUNS FIRST, which is not the forward reach chunk 6's filter warns about: this product's inputs are `comid_attrs/nid.parquet` and `network/catchments.parquet`, both chunk 1's. It lives in chunk 5 because chunk 5 is where a source becomes a COMID-keyed table, not because it depends on anything chunk 5 computes. Building on demand keeps ONE implementation and keeps the edge table from reading an artifact that may not exist.

    Returns None rather than raising, because an edge with null dam columns is honest -- see `graph._dam_index` for why that is different from zero.
    """
    return _cached("dams.parquet", build, force)


def build(cat=None) -> pd.DataFrame:
    """`comid, n_dams, nid_storage, normal_storage, max_storage, max_height, first_year`, one row per catchment WITH a dam.

    Catchments with no dam are absent rather than zero-filled: 1.49M rows of zeros to record 61,766 dams is a table that is 99.9% padding, and chunk 6's join fills the absence with the zero it means.
    """
    import geopandas as gpd

    from ..chunk1 import network

    p = _path()
    if not p.exists():
        raise FileNotFoundError(f"{p} is missing -- run chunk 1's NID branch first")
    pts = _dam_points()

    cats = gpd.read_parquet(network._path("catchments"), columns=["FEATUREID", "geometry"])
    print(f"  {len(pts):,} dam(s) with coordinates against {len(cats):,} catchments ...", flush=True)
    j = pts.sjoin(cats.rename(columns={"FEATUREID": "comid"}), predicate="within", how="inner")
    print(f"  {len(j):,} inside the AOE ({len(j)/max(len(pts),1)*100:.0f}%)", flush=True)
    if not len(j):
        return pd.DataFrame({"comid": pd.Series(dtype="int64"), "n_dams": pd.Series(dtype="int32")})

    return _aggregate(j)


def _dam_points():
    """Every NID dam with a usable coordinate, carrying the storage, height and year columns. EPSG:4326."""
    import geopandas as gpd

    p = _path()
    if not p.exists():
        raise FileNotFoundError(f"{p} is missing -- run chunk 1's NID branch first")
    nid = pd.read_parquet(p)
    lat = pd.to_numeric(nid.latitude, errors="coerce")
    lon = pd.to_numeric(nid.longitude, errors="coerce")
    ok = lat.notna() & lon.notna()
    return gpd.GeoDataFrame(
        {c: pd.to_numeric(nid[c], errors="coerce")[ok]
         for c in (*STORAGE_COLS, HEIGHT_COL) if c in nid.columns}
        # NID writes 0 for an unknown completion year, and a zero would make the dam predate every basin it sits in. Anything before the first US dam is not a date, it is a blank.
        | {"yearCompleted": pd.to_numeric(nid.get("yearCompleted"), errors="coerce")
           .where(lambda s: s > 1700)[ok]},
        geometry=gpd.points_from_xy(lon[ok], lat[ok]), crs=4326, index=nid.index[ok])


def _aggregate(j) -> pd.DataFrame:
    """Per-comid count, storage sums, tallest dam and earliest year. One reduction, two keyings."""
    agg = {"n_dams": ("comid", "size"), "first_year": ("yearCompleted", "min")}
    for src, dst in STORAGE_COLS.items():
        if src in j.columns:
            agg[dst] = (src, "sum")
    if HEIGHT_COL in j.columns:
        agg["max_height"] = (HEIGHT_COL, "max")
    out = j.groupby("comid", as_index=False).agg(**agg)
    out["comid"] = out.comid.astype("int64")
    out["n_dams"] = out.n_dams.astype("int32")
    return out
