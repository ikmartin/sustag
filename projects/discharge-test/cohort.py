"""THE SINGLE BOUNDARY to `data.access`. Every read this project makes passes through here.

The point of the rule is that "does this experiment need an access-layer change?" is answerable by reading one file rather than grepping a project. `xgboost/cohort.py` takes the same stance; this is a sibling, not a fork -- the two projects deliberately do not import each other, because each is bound to its own cohort definition and coupling them would make either hard to change.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[2]))

from data.access import api                                        # noqa: E402
from data.access import config as acfg                             # noqa: E402
from data.access.machinery import accumulate as acc                # noqa: E402

import config                                                      # noqa: E402


@lru_cache(maxsize=1)
def sites() -> pd.DataFrame:
    """Every published record carrying a usable discharge series, with its snapped reach and drainage area.

    Discharge presence is read from each record's COLUMNS rather than from a channel registry, because the published file is the authority on what a record actually carries -- a channel declared and never fetched would otherwise enter the cohort and contribute an all-null target.
    """
    import pyarrow.parquet as pq

    s = api.get_sensors()
    meta = (s.dropna(subset=["publishes_under"])
             .drop_duplicates("publishes_under")
             .set_index("publishes_under"))
    vaa = api.get_network("vaa")
    vaa["comid"] = pd.to_numeric(vaa.comid, errors="coerce")
    area = vaa.dropna(subset=["comid"]).set_index(vaa.comid.dropna().astype("int64")).totdasqkm

    rows = []
    for p in sorted(acfg.PUB_WATER.glob("*.parquet")):
        pf = pq.ParquetFile(p)
        if not any(c.startswith("discharge_") for c in pf.schema_arrow.names):
            continue
        code = p.stem
        m = meta.loc[code] if code in meta.index else None
        comid = int(m.comid) if m is not None and pd.notna(m.get("comid")) else None
        rows.append(dict(code=code, n_days=pf.metadata.num_rows, comid=comid,
                         site_type=(m.get("site_type") if m is not None else None),
                         lat=(m.get("lat") if m is not None else None),
                         lon=(m.get("lon") if m is not None else None),
                         totdasqkm=(float(area.get(comid)) if comid in area.index else None)))
    d = pd.DataFrame(rows)
    d = d[d.comid.notna() & d.totdasqkm.notna() & (d.totdasqkm > 0)]
    d = d[d.n_days >= config.MIN_DISCHARGE_DAYS]
    d = d[d.totdasqkm <= config.MAX_BASIN_KM2]
    # SITE TYPE IS A PHYSICAL FILTER HERE, not a tidy-up. The published store carries a discharge channel
    # for groundwater wells, an atmosphere station, estuaries and tidal streams; a rainfall-runoff model
    # over a contributing basin is the wrong description of every one of them, and tidal reaches reverse
    # flow, so their series carries negative values a specific discharge cannot represent.
    d = d[d.site_type.isin(config.SITE_TYPES)]
    return d.reset_index(drop=True)


def record(code: str) -> pd.DataFrame:
    """One record's daily frame, date-indexed. Quality masks are already applied at publication."""
    d = api.get_record(code).copy()
    d["date"] = pd.to_datetime(d.date)
    return d.set_index("date").sort_index()


@lru_cache(maxsize=1)
def _network():
    return acc.Network.load()


def basin(code: str, comid: int, force: bool = False) -> pd.DataFrame:
    """The upstream COMID set, cached per code. Empty when the reach does not drain (coastline, closed basin).

    Cached because the walk is the project's dominant cost at cohort scale -- thousands of sites, and a rerun must not pay for it twice.
    """
    config.ensure_dirs()
    p = config.CACHE / "basins" / f"{code}.parquet"
    if p.exists() and not force:
        return pd.read_parquet(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    net = _network()
    out = (pd.DataFrame({"comid": sorted(net.upstream(int(comid)))})
           if net.drains(int(comid)) else pd.DataFrame(columns=["comid"]))
    out.to_parquet(p, index=False)
    return out


# The weather store's own span. `get_weather` REQUIRES a window -- it indexes `window[0]`, so None is a
# TypeError rather than "everything" -- and a project-wide default keeps every site on one calendar.
WINDOW = ("2007-01-01", "2026-12-31")


def weather(comids, window=None):
    """Area-weighted daily weather over a COMID set. One row per day, over `WINDOW` unless told otherwise.

    The window opens a year before the collection window so the longest rolling feature (90 days) and the longest lag are fully warmed by 2008-01-01 rather than starting on a truncated accumulation.
    """
    return api.get_weather(list(comids), window or WINDOW, variables=config.WEATHER_VARS)


def attributes(comids, columns=None):
    """StreamCat attributes for a COMID set."""
    return api.get_attributes(list(comids), columns=columns or list(config.STREAMCAT_COLS),
                              source="streamcat")


def vaa_statics(comids) -> pd.DataFrame:
    """Reach geometry from the network's own value-added attributes."""
    v = api.get_network("vaa")
    v["comid"] = pd.to_numeric(v.comid, errors="coerce")
    keep = [c for c in ("comid", "totdasqkm", "areasqkm", "slope", "streamorde", "maxelevsmo",
                        "minelevsmo", "lengthkm", "pathlength") if c in v.columns]
    return v.dropna(subset=["comid"]).astype({"comid": "int64"})[keep]
