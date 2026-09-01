"""Tier 2's decisive test, and the one that generalises: does a source vary WITHIN a catchment?

A source whose native cell is larger than the catchment it is reduced to delivers a basin-level constant, whatever resolution it advertises. That is answerable from geometry alone -- no data need be read, and the answer holds for every gridded product the project will ever consider.

Measured over the real network: median catchment 1.38 km2, and the share of catchments smaller than one source cell runs 40.7% for AORC (1 km2), 98.4% for TerraClimate (16 km2), 98.8% for gridMET (19 km2) and 100.0% for NLDAS-2 (146 km2). The practical consequence is blunt: **do not pay for spatial resolution in a gridded covariate at this grain.** It is why OpenET's 30 m advantage over TerraClimate's 4 km is worth nothing here, and why comparing those two on resolution compares a quantity neither delivers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Approximate cell AREA in km2 for each gridded product the project holds or has considered.
CELL_AREA_KM2 = {
    "AORC (1 km)": 1.0,
    "SNODAS (1 km)": 1.0,
    "TerraClimate (~4 km)": 16.0,
    "gridMET (1/24 deg)": 19.0,
    "CDL (30 m)": 0.0009,
    "AgTile (30 m)": 0.0009,
    "gTREND (250 m)": 0.0625,
    "NLDAS-2 (1/8 deg)": 146.0,
    "GRACE (~300 km)": 90_000.0,
}


def catchment_areas() -> pd.Series:
    """Every catchment's area in km2, from the network's own value-added attributes."""
    from data.access import api

    vaa = api.get_network("vaa")
    a = pd.to_numeric(vaa.areasqkm, errors="coerce").dropna()
    return a[a > 0]


def resolution_table(areas: pd.Series | None = None) -> pd.DataFrame:
    """Per product: cell area, and the share of catchments too small to contain within-cell variation.

    `frac_smaller_than_cell` is an upper bound on how uninformative the product is spatially: a catchment smaller than one cell straddles at most two to four cells and usually sits inside one, so its within-catchment variance is at best the difference between a handful of neighbours.
    """
    a = catchment_areas() if areas is None else areas
    rows = []
    for name, cell in sorted(CELL_AREA_KM2.items(), key=lambda kv: kv[1]):
        rows.append(dict(product=name, cell_km2=cell,
                         frac_smaller_than_cell=round(float((a < cell).mean()), 4),
                         verdict=("resolves within catchments" if float((a < cell).mean()) < 0.5
                                  else "catchment-level constant")))
    return pd.DataFrame(rows)


# ---- the measured version of the same question -------------------------------------------------------

BASIN_SAMPLE = 60
_SEED = 20260827


def _huc8_map():
    """comid -> HUC8, free: NHDPlus `reachcode` IS the HUC8 followed by a sequence number."""
    import pyarrow.parquet as pq

    from data.build.acquire import network

    v = pq.read_table(network._path("vaa"), columns=["comid", "reachcode"]).to_pandas()
    v["huc8"] = v.reachcode.astype(str).str[:8]
    v = v[v.huc8.str.len() == 8]
    v["comid"] = pd.to_numeric(v.comid, errors="coerce")
    return v.dropna(subset=["comid"]).astype({"comid": "int64"})[["comid", "huc8"]]


def decompose(values, basin) -> dict:
    """Split a catchment-grain series' variance into between-basin and within-basin parts.

    `within` is the share a basin-level aggregate DESTROYS -- so a source at ~0 within is already a basin constant and its native resolution buys nothing after catchment aggregation, whatever it advertises. This is the measured form of `resolution_table`, which answers the same question from cell geometry alone and cannot see a source whose values are smooth for reasons other than cell size.
    """
    d = pd.DataFrame({"x": pd.to_numeric(values, errors="coerce"), "b": np.asarray(basin)}).dropna()
    d = d.groupby("b").filter(lambda g: len(g) > 1)
    if d.b.nunique() < 2:
        return {"n": len(d), "basins": int(d.b.nunique()), "within": np.nan, "between": np.nan}
    grand = d.x.mean()
    bm = d.groupby("b").x.agg(["mean", "size"])
    ss_between = float((bm["size"] * (bm["mean"] - grand) ** 2).sum())
    ss_within = float(((d.x - d.b.map(bm["mean"])) ** 2).sum())
    tot = ss_between + ss_within
    if tot <= 0:
        return {"n": len(d), "basins": int(d.b.nunique()), "within": np.nan, "between": np.nan}
    return {"n": len(d), "basins": int(d.b.nunique()),
            "within": round(ss_within / tot, 4), "between": round(ss_between / tot, 4)}


def sample_basins(n: int = BASIN_SAMPLE, seed: int = _SEED):
    """A seeded HUC8 sample. WHOLE basins, not scattered catchments -- a random catchment sample would have almost no two catchments sharing a basin and the within-basin term would be undefined."""
    m = _huc8_map()
    rng = np.random.default_rng(seed)
    hucs = np.sort(m.huc8.unique())
    keep = set(rng.choice(hucs, size=min(n, len(hucs)), replace=False))
    return m[m.huc8.isin(keep)]


def variance_table(n_basins: int = BASIN_SAMPLE) -> pd.DataFrame:
    """Per source: the share of catchment-grain variance that survives basin aggregation."""
    import pyarrow.parquet as pq

    from data.access import config as acfg

    m = sample_basins(n_basins)
    comids = set(m.comid.tolist())
    b = m.set_index("comid").huc8
    rows = []

    p = acfg.PUB_COMID_FEATURES / "nutrients.parquet"
    if p.exists():
        t = pq.read_table(p, columns=["comid", "year", "product", "kg_per_ha"],
                          filters=[("comid", "in", comids), ("year", "=", 2015)]).to_pandas()
        for prod, g in t.groupby("product"):
            r = decompose(g.kg_per_ha, g.comid.map(b))
            rows.append(dict(source=f"gTREND {prod} (250 m)", **r))

    p = acfg.PUB_COMID_FEATURES / "agtile.parquet"
    if p.exists():
        t = pq.read_table(p, columns=["comid", "tile_px", "n_px"],
                          filters=[("comid", "in", comids)]).to_pandas()
        frac = t.tile_px / t.n_px.replace(0, np.nan)     # the product stores counts, not the share
        rows.append(dict(source="AgTile tile fraction (30 m)", **decompose(frac, t.comid.map(b))))

    try:
        from data.access import api

        for col in ("pctagdrainagews", "pctagdrainagecat"):
            a = api.get_attributes(sorted(comids), columns=[col], source="streamcat")
            if col in a.columns:
                rows.append(dict(source=f"StreamCat {col}", **decompose(a[col], a.comid.map(b))))
    except Exception:                                    # noqa: BLE001 -- store absent is a real answer
        pass

    out = pd.DataFrame(rows)
    if len(out):
        out["verdict"] = np.where(out.within < 0.10, "basin constant after aggregation",
                                  np.where(out.within < 0.40, "mostly basin-level", "resolves within basins"))
        out = out.sort_values("within").reset_index(drop=True)
    return out
