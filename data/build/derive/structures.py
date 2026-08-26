"""Structures on the network: point layers with an exact station along their reach.

POINTS ARE PUBLISHED AS POINTS. Aggregating a structure's location away into "exists somewhere in this catchment" discards exactly what a flow-path question needs -- which of two sensors is the structure between? Every structure point carries `(comid, snap_pos_m, snap_dist_m)` from the same convention as the sensor snap (position in metres along the flowline from its upstream end), so structures and sensors ORDER TOGETHER along a reach: sensor A at 300 m, dam at 850 m, sensor B at 1,400 m is a query, not a drawing exercise.

TWO POINT LAYERS: dam points (from `dams._dam_points`) and treatment-facility outfalls (from the ICIS-NPDES pull, already AOE-clipped, joined to CWNS POTW attributes on the permit number). The raw nitrogen DMR rows stay in the acquired store at their native grain; turning them into honest annual loads is a documented-methodology aggregation that belongs where its assumptions can be chosen, not baked here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, io as bio
from ..acquire import facilities as fac_mod
from ..acquire import network, snapshot

# A structure further than this from any flowline is a structure on something unmapped; its point row
# keeps null reach columns rather than claiming a river it is not on. Same constant family as the dam
# reach keying.
SNAP_MAX_M = 500.0


def snap_points(gdf) -> pd.DataFrame:
    """(comid, snap_pos_m, snap_dist_m) per point, nearest flowline, EQUAL_AREA metres. Null beyond SNAP_MAX_M."""
    import geopandas as gpd
    from shapely import STRtree

    fl = gpd.read_parquet(network._path("flowlines"), columns=["COMID", "geometry"]).to_crs(config.EQUAL_AREA)
    p = gdf.to_crs(config.EQUAL_AREA)
    tree = STRtree(fl.geometry.values)
    idx = tree.nearest(p.geometry.values)
    lines = fl.geometry.values[idx]
    dist = p.geometry.values.distance(lines)
    pos = np.array([float(ln.project(pt)) for ln, pt in zip(lines, p.geometry.values)])
    comid = pd.to_numeric(fl.COMID.to_numpy()[idx], errors="coerce")
    far = dist > SNAP_MAX_M
    out = pd.DataFrame({
        "comid": pd.array(np.where(far, np.nan, comid), dtype="Int64"),
        "snap_pos_m": np.where(far, np.nan, np.round(pos, 1)),
        "snap_dist_m": np.round(dist, 1),
    }, index=gdf.index)
    return out


def build_dam_points(force: bool = False) -> pd.DataFrame | None:
    """The dam POINT layer: exact coordinates + NID attributes + the reach station. None before the NID pull."""
    from . import dams

    out_p = config.COVARIATES_DIR / "dam_points.parquet"
    if out_p.exists() and not force:
        try:
            return bio.read_parquet(out_p, expect={"aoe": config.aoe_stamp()})
        except bio.StaleArtifactError:
            print("  dam_points was cut against a different AOE -- rebuilding")
    if not (config.ACQ_ATTRIBUTES / "nid.parquet").exists():
        return None
    pts = dams._dam_points()
    # AOE clip via the catchment join dams.build already trusts -- a point in no AOE catchment is outside.
    import geopandas as gpd

    cats = gpd.read_parquet(network._path("catchments"), columns=["FEATUREID", "geometry"])
    j = pts.sjoin(cats.rename(columns={"FEATUREID": "containing_comid"}), predicate="within", how="inner")
    j = j.drop(columns=[c for c in ("index_right",) if c in j.columns])
    snapped = snap_points(j)
    out = pd.DataFrame({
        "lat": j.geometry.y, "lon": j.geometry.x,
        "containing_comid": pd.to_numeric(j.containing_comid, errors="coerce").astype("Int64"),
        **{c: j[c] for c in j.columns if c not in ("geometry", "containing_comid")},
    }).join(snapped)
    bio.write_parquet(out.reset_index(drop=True), out_p, stamps={"aoe": config.aoe_stamp()})
    on_reach = int(out.comid.notna().sum())
    print(f"  dam_points.parquet: {len(out):,} dam(s) in the AOE, {on_reach:,} stationed on a reach")
    return out


def build_facilities(force: bool = False) -> pd.DataFrame | None:
    """The facility layer: ICIS outfalls + CWNS attributes + the reach station. None before the facilities pull."""
    out_p = config.COVARIATES_DIR / "facilities.parquet"
    if out_p.exists() and not force:
        try:
            return bio.read_parquet(out_p, expect={"aoe": config.aoe_stamp()})
        except bio.StaleArtifactError:
            print("  facilities was cut against a different AOE -- rebuilding")
    if not fac_mod.OUTFALLS_PATH.exists():
        return None
    import geopandas as gpd

    o = pd.read_parquet(fac_mod.OUTFALLS_PATH)
    g = gpd.GeoDataFrame(o, geometry=gpd.points_from_xy(o.lon, o.lat), crs=4326)
    snapped = snap_points(g)
    out = pd.concat([o.reset_index(drop=True), snapped.reset_index(drop=True)], axis=1)

    if fac_mod.CWNS_PATH.exists():
        c = pd.read_parquet(fac_mod.CWNS_PATH).drop_duplicates("npdes_id")
        c = c.set_index(c.npdes_id.astype(str).str.strip())
        for x in ("treatment_level", "design_flow_mgd", "population_2022"):
            if x in c.columns:
                out[f"cwns_{x}"] = out.npdes_id.astype(str).str.strip().map(c[x])
        n = int(out.get("cwns_treatment_level", pd.Series(dtype=object)).notna().sum())
        print(f"  CWNS attributes joined on the permit number ({n:,} outfall(s) matched)")
    bio.write_parquet(out, out_p, stamps={"aoe": config.aoe_stamp()})
    on_reach = int(out.comid.notna().sum())
    print(f"  facilities.parquet: {len(out):,} outfall(s), {on_reach:,} stationed on a reach")
    return out


def main(force: bool = False) -> None:
    d = build_dam_points(force=force)
    if d is None:
        print("  NID not on disk; dam points skipped (run the a3 attributes branch)")
    f = build_facilities(force=force)
    if f is None:
        print("  outfall layer not on disk; facilities skipped (run the a3 facilities branch)")
