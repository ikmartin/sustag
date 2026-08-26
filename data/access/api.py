"""The access surface: published primitives one join away, read-only, stamps checked, no build imports.

Every reader takes the published store as it is; durable changes enter through the build's decision ledgers and arrive at the next build. Caches (the pooled read) are optimizations with live fallbacks, never prerequisites. The flow-network layers are read from the ACQUIRED archive in place -- it is immutable and snapshot-manifested, and copying gigabytes into `published/` would duplicate an archive to no reader's benefit.
"""

from __future__ import annotations

import pandas as pd

from . import config
from .machinery import accumulate as acc_mod
from .machinery import aggregate as agg_mod
from .machinery import snap as snap_mod


def get_sensors(columns=None) -> pd.DataFrame:
    """The published sensors metadata table: every registration's full story, one row each."""
    return pd.read_parquet(config.PUB_SENSORS, columns=columns)


def get_record(code_or_uid: str, channels=None, window=None) -> pd.DataFrame:
    """One published record's daily series with per-day provenance, by SNR code (source uids resolve through the metadata as a convenience)."""
    code = str(code_or_uid)
    if not code.startswith("SNR-"):
        s = get_sensors(columns=["site_uid", "publishes_under"]).set_index("site_uid")
        if code not in s.index or pd.isna(s.loc[code, "publishes_under"]):
            raise KeyError(f"{code_or_uid!r} resolves to no published record")
        code = str(s.loc[code, "publishes_under"])
    p = config.PUB_WATER / f"{code}.parquet"
    if not p.exists():
        raise KeyError(f"no published record for {code}")
    d = pd.read_parquet(p)
    if channels is not None:
        keep = ["date"] + [c for c in d.columns
                           if any(c.startswith(f"{ch}_") for ch in channels)]
        d = d[keep]
    if window is not None:
        lo, hi = window
        dt = pd.to_datetime(d.date)
        d = d[(dt >= pd.Timestamp(lo)) & (dt <= pd.Timestamp(hi))]
    return d.reset_index(drop=True)


def get_quality() -> pd.DataFrame:
    """Every (record, channel) quality verdict with its masks -- why any masked day is absent."""
    return pd.read_parquet(config.PUB_QUALITY)


def get_proximity(max_sep_m: float | None = None) -> pd.DataFrame:
    """The published pair table: every located pair within 2 km of STATED coordinates, with rectangle bounds."""
    d = pd.read_parquet(config.PUB_PROXIMITY)
    return d[d.sep_m <= max_sep_m].reset_index(drop=True) if max_sep_m is not None else d


def get_network(layer: str = "flowlines"):
    """A flow-network layer as the authority provides it: flowlines | vaa | catchments | waterbodies."""
    p = config.ACQ_NETWORK / f"{layer}.parquet"
    if not p.exists():
        raise FileNotFoundError(f"network layer {layer!r} is not on disk ({p})")
    if layer == "vaa":
        return pd.read_parquet(p)
    import geopandas as gpd

    return gpd.read_parquet(p)


def get_covariates(comids, products=("crops", "nutrients", "agtile"), years=None, remap=None) -> dict:
    """Catchment-grain covariates aggregated over a plain COMID set (weight 1). For polygons or custom weights, build the set with `machinery.aggregate` and call its functions directly."""
    wset = agg_mod.from_comids(comids)
    out = {}
    for prod in products:
        if prod == "crops":
            out["crops"] = agg_mod.crops(wset, years=years, remap=remap)
        elif prod == "nutrients":
            out["nutrients"] = agg_mod.nutrients(wset, years=years)
        elif prod == "agtile":
            out["agtile"] = agg_mod.agtile(wset)
        else:
            out[prod] = pd.read_parquet(config.PUB_COMID_FEATURES / f"{prod}.parquet") \
                .query("comid in @comids")
    out["coverage"] = agg_mod.coverage_gap(wset)
    return out


def get_weather(wset_or_comids, window, variables=("pr", "tmmx", "tmmn", "pet", "srad")) -> pd.DataFrame:
    """Daily weather over any basin: collapse to (cell_id, w_km2) FIRST, then read only the touched cells' days and take the weighted mean. One row per day."""
    wset = (wset_or_comids if isinstance(wset_or_comids, pd.DataFrame)
            else agg_mod.from_comids(wset_or_comids))
    cells = agg_mod.weather_weights(wset)
    if not len(cells):
        return pd.DataFrame(columns=["date", *variables])
    lo, hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    frames = []
    for year in range(lo.year, hi.year + 1):
        for q in (1, 2, 3, 4):
            p = config.ACQ_WEATHER / f"gridmet_{year}Q{q}.parquet"
            if not p.exists():
                continue
            d = pd.read_parquet(p, columns=["cell_id", "date", *variables],
                                filters=[("cell_id", "in", cells.cell_id.tolist())])
            if len(d):
                frames.append(d)
    if not frames:
        return pd.DataFrame(columns=["date", *variables])
    d = pd.concat(frames, ignore_index=True)
    d["date"] = pd.to_datetime(d.date)
    d = d[(d.date >= lo) & (d.date <= hi)]
    d = d.merge(cells, on="cell_id")
    w = d.pop("w_km2").to_numpy()
    out = {}
    g = d.groupby("date")
    for v in variables:
        num = (d[v] * w).groupby(d.date).sum()
        den = pd.Series(w, index=d.index).groupby(d.date).sum()
        out[v] = num / den
    return pd.DataFrame(out).reset_index().rename(columns={"index": "date"})


def snap(lat: float, lon: float) -> dict:
    """One point onto the network: the same machinery, constants and outputs as the build's sensor snap."""
    import geopandas as gpd

    fl = get_network("flowlines")
    cat = get_network("catchments")
    vaa = get_network("vaa")
    frame = pd.DataFrame([dict(site_uid="pin", lat=lat, lon=lon,
                               source_drainage_area_km2=pd.NA)])
    out = snap_mod.snap(frame, fl, cat, vaa)
    return out.iloc[0].to_dict()


def accumulate(comid: int):
    """The upstream COMID set and its areas, by walking the published topology. See `machinery.accumulate`."""
    net = acc_mod.Network.load()
    return net.accumulate(int(comid))
