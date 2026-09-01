"""Per-catchment karst fractions, by BURIAL DEPTH class -- the axis `pctcarbresid` cannot express.

The Weary & Doctor map classes carbonate and evaporite units by `Exposure`: `E` at or near the surface, `B1`/`B2`/`B3` buried at increasing depth. A carbonate aquifer under forty feet of till is `B2` or `B3` and scores ZERO carbonate-residual, while being exactly the cover-collapse setting that routes nitrate to groundwater with almost no attenuation. Our AOE straddles the glacial margin, so the buried classes are not a rounding error here: they cover 490,609 km2 against the exposed class's 813,435.

FRACTIONS, NOT FLAGS. A catchment is rarely wholly karst, and the share of its area over each class is what a model can weight; a boolean would discard the difference between a catchment 5% underlain and one 95% underlain. Areas are computed in the map's own equal-area projection rather than reprojecting it to ours -- the source is authoritative about its own geometry, and a reprojection would introduce error into the very quantity being measured.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, verify
from .. import io as bio
from ..acquire import karst as acq
from ..acquire import network, weather as W

LAYERS = {"carbonate": "Carbonates48.shp", "evaporite": "Evaporites48.shp"}
CLASSES = ("E", "B1", "B2", "B3")
BATCH = 200_000


def _zip_path(layer: str) -> str:
    p = (config.RAW / "karst" / "USKarstMap.zip").resolve()
    return f"/vsizip/{p}/Shapefiles/Continguous48/{LAYERS[layer]}"


def _out():
    return config.COVARIATES_DIR / "karst.parquet"


def build(force: bool = False) -> pd.DataFrame | None:
    """`(comid, {layer}_{class}_frac)` for every catchment. None when the release is not downloaded."""
    import geopandas as gpd
    import pyarrow.parquet as pq
    import pyogrio
    from shapely import from_wkb
    from shapely.geometry import box

    p = _out()
    if p.exists() and not force:
        return bio.read_parquet(p, expect={"aoe": config.aoe_stamp()})
    if not (config.RAW / "karst" / "USKarstMap.zip").exists():
        return None

    crs = pyogrio.read_info(_zip_path("carbonate"))["crs"]
    t = list(W.tiles())
    bb = gpd.GeoSeries([box(min(x[0] for x in t), min(x[1] for x in t),
                            max(x[2] for x in t), max(x[3] for x in t))],
                       crs=4326).to_crs(crs).total_bounds
    polys = {}
    for name in LAYERS:
        g = gpd.read_file(_zip_path(name), bbox=tuple(bb))[["Exposure", "geometry"]]
        polys[name] = g[g.geometry.is_valid & ~g.geometry.is_empty].reset_index(drop=True)
        print(f"    {name}: {len(polys[name]):,} polygon(s) in the AOE bbox", flush=True)

    pf = pq.ParquetFile(network._path("catchments"))
    frames = []
    for rg in range(pf.metadata.num_row_groups):
        tb = pf.read_row_group(rg, columns=["FEATUREID", "geometry"])
        cat = gpd.GeoDataFrame(
            {"comid": tb.column("FEATUREID").to_numpy()},
            geometry=from_wkb(np.asarray(tb.column("geometry").to_pylist(), dtype=object)),
            crs=4326).to_crs(crs)
        cat = cat[cat.geometry.is_valid & ~cat.geometry.is_empty]
        if not len(cat):
            continue
        out = pd.DataFrame({"comid": cat.comid.to_numpy(),
                            "cat_km2": cat.geometry.area.to_numpy() / 1e6}).set_index("comid")
        for name, g in polys.items():
            j = gpd.sjoin(cat, g, how="inner", predicate="intersects")
            if not len(j):
                continue
            j = j.merge(g.geometry.rename("kgeom"), left_on="index_right", right_index=True)
            j["km2"] = j.geometry.intersection(
                gpd.GeoSeries(j.kgeom, crs=crs, index=j.index)).area / 1e6
            agg = j.groupby(["comid", "Exposure"]).km2.sum().unstack(fill_value=0.0)
            for c in CLASSES:
                col = f"{name}_{c}"
                out[col] = agg[c].reindex(out.index).fillna(0.0) if c in agg.columns else 0.0
        frames.append(out.reset_index())
        if (rg + 1) % 5 == 0:
            print(f"    row group {rg+1}/{pf.metadata.num_row_groups}", flush=True)

    d = pd.concat(frames, ignore_index=True)
    for name in LAYERS:
        for c in CLASSES:
            col = f"{name}_{c}"
            if col not in d.columns:
                d[col] = 0.0
            # FRACTION OF THE CATCHMENT, clipped at 1: a sliver of numerical overshoot on a boundary
            # intersection is not evidence that a catchment is 101% carbonate.
            d[f"{col}_frac"] = (d[col] / d.cat_km2.replace(0, np.nan)).clip(upper=1.0).fillna(0.0)
    keep = ["comid", "cat_km2"] + [f"{n}_{c}_frac" for n in LAYERS for c in CLASSES]
    d = d[keep]
    bio.write_parquet(d, p, stamps={"aoe": config.aoe_stamp(), "source": "weary_doctor_2014"})
    return d


def main(force: bool = False) -> None:
    d = build(force=force)
    if d is None:
        print("  karst: release not downloaded", flush=True)
        return
    cols = [c for c in d.columns if c.endswith("_frac")]
    any_karst = (d[cols].sum(axis=1) > 0).sum()
    print(f"  karst: {len(d):,} catchments, {any_karst:,} with any karst "
          f"({any_karst/max(len(d),1):.1%})", flush=True)


@verify.check("d5", "karst fractions are shares of a catchment, and the buried classes are present")
def _check_karst():
    """The buried classes are the entire reason for this source; a file with only `E` reproduces `pctcarbresid`."""
    if not _out().exists():
        return None, "karst not built"
    d = bio.read_parquet(_out())
    fr = [c for c in d.columns if c.endswith("_frac")]
    bad = int((d[fr] < 0).sum().sum() + (d[fr] > 1.0001).sum().sum())
    buried = [c for c in fr if any(f"_{b}_" in c for b in ("B1", "B2", "B3"))]
    have_buried = float(d[buried].to_numpy().sum()) > 0
    return (not bad and have_buried), (
        f"{len(d):,} catchments, {len(fr)} fraction column(s), buried classes populated"
        if not bad and have_buried else
        f"{bad} out-of-range fraction(s); buried populated={have_buried}")
