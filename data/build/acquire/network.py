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

from .. import config, io as bio

_LAYERS = ("flowlines", "vaa", "catchments")   # waterbodies is fetched separately with its own existence check

# Layer names inside the national geodatabase, from `nhdplus_l48`'s own catalogue.
_L48_FLOWLINES, _L48_CATCHMENTS, _L48_WATERBODIES = "NHDFlowline_Network", "CatchmentSP", "NHDWaterbody"


def _path(name: str):
    return config.ACQ_NETWORK / f"{name}.parquet"


def waterbodies_path():
    """The waterbody store's location; existence and coverage are the CALLER's questions -- the layer lands after the reach layers and may be the predecessor's partial subset until the full fetch reruns."""
    return _path("waterbodies")


def _layer_stamp(name: str) -> str | None:
    """The AOE a layer was cut against, from its own `aoe_stamp` column. `None` when unreadable.

    Footer-free but cheap: one row group, one column. `_write` has always stamped this and nothing has ever read it.
    """
    import pyarrow.parquet as pq

    p = _path(name)
    if not p.exists():
        return None
    try:
        f = pq.ParquetFile(p)
        if "aoe_stamp" not in f.schema_arrow.names:
            return None
        t = f.read_row_group(0, columns=["aoe_stamp"])
        return str(t.column("aoe_stamp")[0]) if t.num_rows else None
    except Exception:                                  # noqa: BLE001 -- unreadable is not current
        return None


def exists(aoe_stamp: str | None = None) -> bool:
    """Are all layers on disk AND cut against `aoe_stamp` (default: the current extent)?

    EXISTENCE WAS NEVER THE QUESTION. These layers are CLIPPED TO THE AOE, so a file cut against a retired extent is not a cached answer to the current request -- it is a different answer to a different question. Guarding on existence alone meant that widening the extent left `flowlines`, `catchments` and `vaa` clipped to the old one, and because `zonal.Catchments` reads the catchment layer, every D5 product was then computed over the retired cohort and written with the CURRENT stamp -- so the AOE-stamp check passed on artifacts describing the wrong region. The correct key was inside the file the guard declined to open.
    """
    want = aoe_stamp or config.aoe_stamp()
    stale = []
    for n in _LAYERS:
        if not _path(n).exists():
            return False
        got = _layer_stamp(n)
        if got is not None and got != want:
            stale.append((n, got))
    if not stale:
        return True

    # A DIFFERENT STAMP IS NOT AUTOMATICALLY THE WRONG DATA. These layers are CLIPPED, so what matters is
    # whether the stored clip still COVERS the current extent -- a layer cut against a larger, earlier AOE
    # is a superset and perfectly usable, while one cut against a smaller AOE is missing reaches the current
    # request needs. Equality would force a multi-gigabyte refetch every time the extent shrank, which is
    # both wasteful and the kind of cost that gets a check disabled.
    if _covers_aoe(want):
        print(f"  network: cut against AOE {stale[0][1][:12]}, current is {want[:12]} -- but the stored "
              f"clip COVERS the current extent, so it stands", flush=True)
        return True
    print(f"  network: cut against AOE {stale[0][1][:12]} which does NOT cover the current extent "
          f"{want[:12]} -- refetching {len(stale)} layer(s)", flush=True)
    return False


def _covers_aoe(want: str) -> bool:
    """Does the stored catchment layer's footprint contain the current AOE? Bounds only -- no geometry union.

    A bounding-box test is deliberately loose: it can pass for a clip that is missing interior reaches. It is used only to avoid a needless refetch when the extent SHRANK, which is the common case; a grown extent fails the box test and refetches, which is the outcome that matters.
    """
    try:
        import geopandas as gpd
        from shapely.geometry import box

        p = _path("catchments")
        if not p.exists():
            return False
        stored = box(*gpd.read_parquet(p, columns=["geometry"]).total_bounds)
        return box(*config.aoe_geometry().bounds).buffer(-1e-9).within(stored)
    except Exception:                                  # noqa: BLE001 -- cannot prove coverage
        return False


def _write(gdf, name: str) -> None:
    """Atomic write, stamped with the AOE it was cut against."""
    config.ACQ_NETWORK.mkdir(parents=True, exist_ok=True)
    out = gdf.copy()
    out["aoe_stamp"] = config.aoe_stamp()
    p = _path(name)
    tmp = p.with_name(p.name + ".tmp")
    out.to_parquet(tmp)
    tmp.replace(p)
    # a predecessor-format external sidecar beside the target describes the file it replaced; left in
    # place it re-triggers the legacy-store detection on every pass
    p.with_name(p.name + ".stamps.json").unlink(missing_ok=True)


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
    config.ACQ_NETWORK.mkdir(parents=True, exist_ok=True)
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
    fetch_waterbodies(force=force)
    return counts


# ---- waterbodies -----------------------------------------------------------------------------------

# Properties of the NHDPlus VAA DATA, declared once here because this layer owns the store -- build2 kept
# two copies of each (accumulate.py and graph.py) and copies drift. A Coastline carries no upstream
# drainage: walking from one is a category error, not an approximation (it collects every river reaching
# the sea along it -- 124,184 reaches for a site whose own reach drains 20 km2). NHDPlus writes -9998/-9999
# where a value is UNKNOWN; it is not a small number, and differencing two of them yields a confident zero.
NON_DRAINAGE_FTYPE = {"Coastline"}
VAA_NODATA = -9998.0

def fetch_waterbodies(force: bool = False):
    """The FULL NHDWaterbody layer over the AOE, from the national geodatabase -- the authority's own layer, as provided.

    Bulk, not by-id: a by-id pull keyed on the reaches sensors happened to snap to bakes one cohort's question into the store, and a graph-builder asking "does this flow path cross a reservoir" needs polygons for reaches no sensor sits on. The national archive is the same 7.3 GB download the other layers already share, so the marginal cost is one more layer clipped from it. A `WBAREACOMI` that references an `NHDArea` (a wide-river polygon) rather than an `NHDWaterbody` still resolves to nothing -- that is the layer's own semantics, recorded here so the absence reads as expected rather than as loss.
    """
    from pynhd import nhdplus_l48

    p = _path("waterbodies")
    legacy_sidecar = p.with_name(p.name + ".stamps.json")
    if p.exists() and legacy_sidecar.exists() and "requested" in legacy_sidecar.read_text():
        # the predecessor's BY-ID store: ~78 polygons keyed to the reaches one cohort's sensors snapped
        # to -- it satisfies an existence check while being a different product entirely
        print("  waterbody store is the legacy by-id subset -- rebuilding as the full AOE layer")
        force = True
    if p.exists() and not force:
        import pyarrow.parquet as pq

        print(f"  waterbodies already on disk: {pq.read_metadata(p).num_rows:,} polygons")
        return None
    print(f"  waterbodies: reading {_L48_WATERBODIES} from the national dataset ...", flush=True)
    wb = _clip_to_aoe(nhdplus_l48(layer=_L48_WATERBODIES), "waterbodies")
    cc = comid_col(wb)
    if cc and cc != "comid":
        wb = wb.rename(columns={cc: "comid"})
    _write(wb, "waterbodies")
    print(f"    {len(wb):,} waterbody polygon(s) in the AOE")
    return wb
