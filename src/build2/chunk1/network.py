"""NHDPlus V2 over the AOE: flowlines, value-added attributes, catchments.

THIS IS WHAT DEFINES THE COMID SET, so everything COMID-keyed waits on it -- StreamCat, Wieczorek, NID, NWM and the snap all take their key from here.

BULK DOWNLOAD, THEN CLIP LOCALLY. Not the REST service. `NHD().bygeom()` asks an ArcGIS endpoint for everything inside a polygon, and the AOE holds roughly 1.2 million flowlines -- one request times out, and the workaround is to tile the region into dozens of boxes and stitch the answers back together with a dedup on COMID. Measured at 29 requests for 4-degree tiles, or 466 at 1 degree.

`nhdplus_l48()` fetches the whole national dataset instead: 7.3 GB, once, cached under `./cache`, layers addressable by name. One artifact rather than dozens of queries, one consistent snapshot rather than a stitch, no tiling and no dedup. At an AOE covering half of CONUS east of the Rockies we would have been pulling most of the national dataset in pieces anyway, through an API built for interactive queries.

The archive is national and reusable at any extent, so it belongs with CDL and the gTREND rasters as a manually-acquired input rather than against the AOE storage budget. Only the AOE subset is written here; the extracted national layers are transient.

CATCHMENTS ARE NOT A NICE-TO-HAVE. They are the merge rule's unit -- two registrations are candidates when they share one -- and the aggregation unit that replaces `global_node_id` in chunk 5. Both rest on a property worth verifying rather than assuming, that catchments partition the surface, which `verify` checks.

VAA COMES FROM `nhdplus_vaa()`, already a national bulk parquet, and not the HydroShare path which 301-redirects to a 405.
"""

from __future__ import annotations

import pandas as pd

from .. import config

_LAYERS = ("flowlines", "vaa", "catchments")

# Layer names inside the national geodatabase, from `nhdplus_l48`'s own catalogue.
_L48_FLOWLINES, _L48_CATCHMENTS = "NHDFlowline_Network", "CatchmentSP"


def _path(name: str):
    return config.NETWORK / f"{name}.parquet"


def exists() -> bool:
    return all(_path(n).exists() for n in _LAYERS)


def _write(gdf, name: str) -> None:
    """Atomic write, stamped with the AOE it was cut against."""
    config.NETWORK.mkdir(parents=True, exist_ok=True)
    out = gdf.copy()
    out["aoe_stamp"] = config.aoe_stamp()
    p = _path(name)
    tmp = p.with_name(p.name + ".tmp")
    out.to_parquet(tmp)
    tmp.replace(p)


def comid_col(df) -> str | None:
    """The COMID column, whatever this layer chose to call it. Casing and name both vary by source."""
    return next((c for c in ("comid", "COMID", "featureid", "FEATUREID") if c in df.columns), None)


def _clip_to_aoe(gdf, label: str):
    """Rows intersecting the AOE. Spatial-index prefilter first, exact test only on the survivors.

    A national layer is millions of rows and an exact `intersects` against a polygon of this complexity on all of them is far slower than a bbox query that discards ~90% of them for free.
    """
    import geopandas as gpd

    geom = config.aoe_geometry()
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)
    idx = list(gdf.sindex.query(geom, predicate="intersects"))
    out = gdf.iloc[idx]
    print(f"    {label}: {len(gdf):,} national -> {len(out):,} in the AOE", flush=True)
    return gpd.GeoDataFrame(out, geometry="geometry", crs=4326).reset_index(drop=True)


def fetch(force: bool = False) -> dict:
    """Download the national dataset once, clip each layer to the AOE, write. Returns row counts."""
    import geopandas as gpd
    from pynhd import nhdplus_l48, nhdplus_vaa

    if exists() and not force:
        # Row counts from parquet METADATA, not by reading the layers. `gpd.read_parquet` raises "Missing geo metadata" on `vaa`, which is a plain attribute table with no geometry, and reading 2.25 GB of flowlines to call `len()` on them is wasteful even where it works.
        import pyarrow.parquet as pq

        counts = {n: pq.read_metadata(_path(n)).num_rows for n in _LAYERS}
        print(f"  network already on disk: {counts}")
        return counts

    counts = {}

    print(f"  flowlines: reading {_L48_FLOWLINES} from the national dataset "
          f"(7.3 GB on first call, then cached) ...", flush=True)
    fl = _clip_to_aoe(nhdplus_l48(layer=_L48_FLOWLINES), "flowlines")
    _write(fl, "flowlines")
    counts["flowlines"] = len(fl)

    print(f"  catchments: reading {_L48_CATCHMENTS} ...", flush=True)
    cat = _clip_to_aoe(nhdplus_l48(layer=_L48_CATCHMENTS), "catchments")
    _write(cat, "catchments")
    counts["catchments"] = len(cat)

    print("  vaa ...", flush=True)
    vaa = nhdplus_vaa()
    fl_key = comid_col(fl)
    if fl_key is None:
        raise KeyError(f"no COMID column in the flowline layer; got {list(fl.columns)[:12]}")
    comids = set(pd.to_numeric(fl[fl_key], errors="coerce").dropna().astype("int64"))
    vaa_key = comid_col(vaa)
    if vaa_key is None:
        raise KeyError(f"no COMID column in VAA; got {list(vaa.columns)[:12]}")
    v = vaa[vaa[vaa_key].isin(comids)].copy()
    print(f"    vaa: {len(vaa):,} national -> {len(v):,} matching the AOE flowlines", flush=True)
    v["aoe_stamp"] = config.aoe_stamp()
    config.NETWORK.mkdir(parents=True, exist_ok=True)
    tmp = _path("vaa").with_name("vaa.parquet.tmp")
    v.to_parquet(tmp)
    tmp.replace(_path("vaa"))
    counts["vaa"] = len(v)
    return counts


def verify() -> dict:
    """The catchment properties the merge rule and chunk 5 depend on.

    MEASURED BY SET DIFFERENCE, BOTH WAYS, because neither layer contains the other. Subtracting the row counts nets two populations against each other and understates both: on this AOE it gives 25,752 where the truth is 32,411 reaches with no catchment AND 6,659 catchments with no reach.

    A reach without a catchment is a MISSING ROW here, not a zero -- `CatchmentSP` holds only catchments that exist -- so testing `AreaSqKM == 0` on this layer reports a clean 0.00% and hides the entire population.

    WHAT THEY ACTUALLY ARE, measured rather than assumed. 56% are `ArtificialPath`: synthetic lines threaded through lakes and wide rivers to keep the network connected, which no land drains to directly. 30% `StreamRiver`, 10% `CanalDitch`, then Connector, Coastline and Pipeline. Only ~29% are divergences (`Divergence == 2`, the minor branch where flow splits and one path owns the catchment) -- the explanation the old build gives, which turns out to cover a minority. 81% carry `Divergence == 0`.

    Catchments with no reach are sinks: closed depressions that drain somewhere but have no channel.

    Both matter downstream. A reach with no catchment is a graph node that divides by zero in any per-area feature; a catchment with no reach is area that the network cannot route.
    """
    import pyarrow.parquet as pq

    # Column reads only -- these layers are ~1 GB each and this needs two id columns and one area.
    fl_names = list(pq.read_schema(_path("flowlines")).names)
    cat_names = list(pq.read_schema(_path("catchments")).names)
    fl_key = next(c for c in fl_names if c.lower() in ("comid", "featureid"))
    cat_key = next(c for c in cat_names if c.lower() in ("featureid", "comid"))
    acol = next((c for c in cat_names if c.lower() == "areasqkm"), None)

    fl_ids = pq.read_table(_path("flowlines"), columns=[fl_key]).column(fl_key).to_pandas()
    cat_tbl = pq.read_table(_path("catchments"), columns=[cat_key] + ([acol] if acol else []))
    cat_ids = cat_tbl.column(cat_key).to_pandas()
    a = pd.to_numeric(cat_tbl.column(acol).to_pandas(), errors="coerce") if acol else pd.Series(dtype=float)

    fl_set = set(pd.to_numeric(fl_ids, errors="coerce").dropna().astype("int64"))
    cat_set = set(pd.to_numeric(cat_ids, errors="coerce").dropna().astype("int64"))
    no_cat, no_reach = len(fl_set - cat_set), len(cat_set - fl_set)
    return dict(n_flowlines=len(fl_ids), n_catchments=len(cat_ids),
                reaches_without_catchment=no_cat,
                without_catchment_frac=no_cat / len(fl_set) if fl_set else float("nan"),
                catchments_without_reach=no_reach,
                zero_area=int((a == 0).sum()) if len(a) else 0,
                median_km2=float(a.median()) if len(a) else float("nan"))


def main(force: bool = False) -> dict:
    counts = fetch(force=force)
    v = verify()
    print(f"  {counts}")
    print(f"  catchments: median {v['median_km2']:.3f} km2; "
          f"{v['reaches_without_catchment']:,} reach(es) with no catchment "
          f"({v['without_catchment_frac']*100:.2f}%), "
          f"{v['catchments_without_reach']:,} catchment(s) with no reach")
    return counts
