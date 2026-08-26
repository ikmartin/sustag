"""Covariates over ANY basin: every basin is a weighted COMID set, every aggregation a weighted sum.

THE UNIFICATION. An exact upstream walk is the special case where every weight is 1; a polygon that partially covers boundary catchments carries fractional weights; and every aggregation question reduces to a weighted sum over the catchment-grain products. Nothing about a basin needs to be STORED to be usable -- what a modeler keeps lives in their project files, never in the data stores.

A weighted set is a DataFrame `(comid, weight)`, weight in [0, 1] = the share of that catchment's area inside the basin. COMIDs with no catchment row (32k reaches carry none) contribute nothing and are reported, never silently absorbed: the walk's area and the aggregate's denominator can legitimately disagree by exactly those reaches.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config


def from_comids(comids, weight: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame({"comid": pd.array(sorted({int(c) for c in comids}), dtype="int64"),
                         "weight": float(weight)})


def from_polygon(geom, catchments=None) -> pd.DataFrame:
    """A lon/lat polygon -> weighted COMID set: each intersected catchment weighted by its inside share."""
    import geopandas as gpd

    if catchments is None:
        catchments = gpd.read_parquet(config.ACQ_NETWORK / "catchments.parquet",
                                      columns=["FEATUREID", "geometry"])
    g = gpd.GeoSeries([geom], crs=4326).to_crs(config.EQUAL_AREA).iloc[0]
    cat = catchments.to_crs(config.EQUAL_AREA) if catchments.crs and catchments.crs.to_epsg() != 5070 \
        else catchments
    idx = list(cat.sindex.query(g, predicate="intersects"))
    hit = cat.iloc[idx]
    inter = hit.geometry.intersection(g)
    w = (inter.area / hit.geometry.area).clip(0, 1)
    key = "FEATUREID" if "FEATUREID" in hit.columns else "comid"
    out = pd.DataFrame({"comid": pd.to_numeric(hit[key], errors="coerce").astype("int64"),
                        "weight": w.to_numpy(dtype="float64")})
    return out[out.weight > 1e-9].reset_index(drop=True)


def _product(name: str) -> pd.DataFrame:
    p = config.PUB_COMID_FEATURES / f"{name}.parquet"
    if not p.exists():
        raise FileNotFoundError(f"published product {name!r} is not on disk ({p})")
    return pd.read_parquet(p)


def crops(wset: pd.DataFrame, years=None, remap=None) -> pd.DataFrame:
    """Weighted crop-area composition: (year, code|class, area_px) over the basin.

    `remap=None` returns RAW CDL codes -- the default, because the code is the fact. `remap="default"` applies the project's 8-class agronomic map; any {code: class} mapping applies verbatim. The result records which map produced it in `.attrs['remap']`, so two experiments cannot both report "corn" meaning different things.
    """
    d = _product("crops").merge(wset, on="comid", how="inner")
    if years is not None:
        d = d[d.year.isin(set(int(y) for y in years))]
    d["area_px"] = d.frac * d.n_px * d.weight
    label = "code"
    if remap is not None:
        from . import remap as remap_mod

        mapping = remap_mod.CODE_TO_CLASS if remap == "default" else dict(remap)
        d["cls"] = d.code.map(lambda c: mapping.get(int(c), remap_mod.DEFAULT
                                                    if remap == "default" else "Other"))
        label = "cls"
    out = (d.groupby(["year", label], as_index=False).area_px.sum()
           .rename(columns={label: "code" if remap is None else "class"}))
    out.attrs["remap"] = "none" if remap is None else ("default" if remap == "default" else "custom")
    return out


def nutrients(wset: pd.DataFrame, years=None) -> pd.DataFrame:
    """Weighted N inputs: (product, year, kg) -- kilograms accumulate; means do not."""
    d = _product("nutrients").merge(wset, on="comid", how="inner")
    if years is not None:
        d = d[d.year.isin(set(int(y) for y in years))]
    d["kg_w"] = d.kg * d.weight
    return d.groupby(["product", "year"], as_index=False).kg_w.sum().rename(columns={"kg_w": "kg"})


def agtile(wset: pd.DataFrame) -> dict:
    """Weighted tile-drained pixel area and the coverage denominator."""
    d = _product("agtile").merge(wset, on="comid", how="inner")
    return dict(tile_px=float((d.tile_px * d.weight).sum()),
                n_px=float((d.n_px * d.weight).sum()))


def weather_weights(wset: pd.DataFrame) -> pd.DataFrame:
    """Collapse the basin to (cell_id, w_km2): the geometry half of any weather reduction, dates untouched."""
    w = _product("weights_gridmet").merge(wset, on="comid", how="inner")
    w["wk"] = w.w_km2 * w.weight
    return w.groupby("cell_id", as_index=False).wk.sum().rename(columns={"wk": "w_km2"})


def coverage_gap(wset: pd.DataFrame) -> dict:
    """How much of the set the products can actually see: comids with no coverage row are ABSENT AREA, reported."""
    cov_p = config.PUB_COMID_FEATURES / "coverage.parquet"
    if not cov_p.exists():
        return dict(comids=len(wset), covered=None)
    cov = pd.read_parquet(cov_p, columns=["comid"])
    missing = wset[~wset.comid.isin(set(cov.comid))]
    return dict(comids=len(wset), covered=len(wset) - len(missing),
                missing_comids=missing.comid.tolist()[:20])
