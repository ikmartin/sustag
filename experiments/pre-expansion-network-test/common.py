"""Shared loaders for the R12 precondition test (see REPORT.md).

Question this whole folder answers: does one site's nitrate carry predictive signal about another's
as a function of HYDROLOGICAL relationship, ABOVE the shared seasonal/statewide driver the model
already captures? That gates the graph/GNN + donor-channel direction.

Everything here reads data already on disk (85-ish sensors' daily nitrate + the basin-containment
graph). No network, no new data.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
_ROOT = HERE.parents[1]  # repo root
sys.path.insert(0, str(_ROOT))

from src.data.access import get_site_ids, get_location, get_basin_graph, get_basin_area  # noqa: E402
from src.features.features import daily_nitrate  # noqa: E402

MIN_OVERLAP = 365  # a pair needs >= this many concurrent observed days for a trustworthy correlation
_WIDE_CACHE = HERE / "daily_nitrate_wide.parquet"


def build_wide(force: bool = False) -> pd.DataFrame:
    """date x site matrix of daily-max nitrate (the target), cached. One column per site."""
    if _WIDE_CACHE.exists() and not force:
        return pd.read_parquet(_WIDE_CACHE)
    series = {}
    for s in get_site_ids():
        try:
            d = daily_nitrate(site_uid=s).dropna()
        except Exception as e:  # a site with no water / unreadable record
            print(f"  skip {s}: {e}")
            continue
        if len(d):
            d.index = pd.DatetimeIndex(d.index).tz_localize(None).normalize()
            series[s] = d[~d.index.duplicated()]
    wide = pd.DataFrame(series).sort_index()
    wide.to_parquet(_WIDE_CACHE)
    return wide


def statewide_mean(wide: pd.DataFrame) -> pd.Series:
    """Cross-site daily mean nitrate -- the `rest_of_state` level the model ALREADY has. Removing it
    is how we test whether connected pairs co-move BEYOND the statewide signal."""
    return wide.mean(axis=1)


def residual_wide(wide: pd.DataFrame) -> pd.DataFrame:
    """Anomalies: each site minus the statewide daily mean (deseasonalized + de-leveled at once,
    since the statewide mean carries the spring-flush cycle every Iowa site shares)."""
    return wide.sub(statewide_mean(wide), axis=0)


def overlap_counts(wide: pd.DataFrame) -> pd.DataFrame:
    """site x site matrix of concurrent observed-day counts (both non-null)."""
    obs = wide.notna().astype(int)
    m = obs.T.values @ obs.values
    return pd.DataFrame(m, index=wide.columns, columns=wide.columns)


def haversine_km(lon1, lat1, lon2, lat2) -> float:
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return float(2 * r * np.arcsin(np.sqrt(a)))


def containment_info():
    """Return (undirected_graph, component_id_by_site, directed_graph). Edge child->parent in the
    directed graph means child's sensor lies inside parent's basin (child UPSTREAM of parent)."""
    import networkx as nx

    dg = get_basin_graph()  # DiGraph, child -> parent (child nested in parent)
    ug = dg.to_undirected()
    comp = {}
    for i, c in enumerate(nx.connected_components(ug)):
        for s in c:
            comp[s] = i
    return ug, comp, dg


def locations() -> dict:
    out = {}
    for s in get_site_ids():
        try:
            lon, lat = get_location(s)
            out[s] = (float(lon), float(lat))
        except Exception:
            pass
    return out


def basin_areas() -> dict:
    out = {}
    for s in get_site_ids():
        try:
            out[s] = float(get_basin_area(s))
        except Exception:
            out[s] = np.nan
    return out
