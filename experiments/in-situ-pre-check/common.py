"""Shared paths, the covariate footprint, and the cohort geometry every step here needs.

Pre-check for notes/extra-in-situ-report.md. Nothing in this directory writes to src/data/ or to the pipeline -- every artifact lands in ./cache/, so the whole experiment can be deleted without touching the build.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1]))  # repo root
CACHE = _HERE / "cache"
CACHE.mkdir(exist_ok=True)

from src.data.access import get_site_ids, get_location  # noqa: E402

# The states that intersect the covariate footprint. USGS's site service caps a bbox query, so the pool is assembled per state and clipped afterwards.
STATES = ["ia", "mn", "wi", "il", "mo", "ne", "sd", "ks"]

# The covariate footprint, read from the grid rather than restated: a donor outside it has no weather/crops/surplus coverage, so it could never be joined even if it were nearby.
_GRID = _HERE.parents[1] / "src" / "data" / "interim" / "grid_global.parquet"

# The pcodes worth enumerating, and what the report concluded about each. Only discharge is sequenced for the CV step; the rest are enumerated so the counts can be re-checked cheaply as the network changes.
PCODES = {
    "00060": "discharge",  # 1,192 sites in footprint -- the only one dense enough to survive an exclusion radius
    "00065": "stage",  # 1,012, near-redundant with discharge (same rating curve)
    "00010": "temp_water",  # 198, marginal
    "00095": "spec_cond",  # 97, too sparse
    "63680": "turbidity",  # 73, too sparse
    "00300": "diss_oxy_con",  # 65, too sparse
    "00400": "ph",  # 52, too sparse
    "99133": "nitrate_con",  # 58, the donor channel exps 33-36c already use
}

# Exclusion radii for the distance-stratified design. r=0 is the NAIVE co-located feature and is kept on purpose: the gap between r=0 and r=1 is the skill that comes from reading the target's own gauge, i.e. the train/serve skew the deployable version avoids.
RADII_KM = (0.0, 1.0, 5.0, 10.0, 20.0, 50.0)


def footprint() -> tuple[float, float, float, float]:
    """(lon_min, lat_min, lon_max, lat_max) of the covariate grid."""
    g = pd.read_parquet(_GRID, columns=["lon", "lat"])
    return float(g.lon.min()), float(g.lat.min()), float(g.lon.max()), float(g.lat.max())


def cohort() -> list[str]:
    """The contract cohort, as strings. The runtime cohort comes from the water contract, NOT filtered_sites.json -- see CLAUDE.md."""
    return [str(s) for s in get_site_ids()]


def cohort_lonlat() -> pd.DataFrame:
    """site -> (lon, lat). get_location's tuple order is not guaranteed across sources, so it is normalised by magnitude: Iowa longitudes are ~-90 and latitudes ~+42, so |first| > 50 identifies a (lon, lat) pair."""
    rows = []
    for s in cohort():
        p = get_location(s)
        lon, lat = (p[0], p[1]) if abs(p[0]) > 50 else (p[1], p[0])
        rows.append({"site": s, "lon": float(lon), "lat": float(lat)})
    return pd.DataFrame(rows).set_index("site")


def km(lon1, lat1, lon2, lat2):
    """Great-circle distance in km, equirectangular approximation. Fine at Iowa scale and vectorises; a LOWER BOUND on hydrologic separation, since two points 12 km apart may sit across a divide -- step 02's containment linking is what replaces it."""
    lat0 = np.radians((np.asarray(lat1) + np.asarray(lat2)) / 2)
    return 111.32 * np.hypot((np.asarray(lon1) - np.asarray(lon2)) * np.cos(lat0), np.asarray(lat1) - np.asarray(lat2))


def say(msg: str) -> None:
    print(msg, flush=True)
