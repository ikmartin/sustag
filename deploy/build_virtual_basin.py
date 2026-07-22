"""Build a virtual SiteData for an arbitrary (lat, lon) in Iowa."""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path
from src.data import access
from src.data.access import SiteData

TARGET_YEAR = 2017
_BUFFER = pd.DateOffset(months=2)  # weather lead-in/out for trailing rolling/lag features

_FLOWLINES = None


def _flowlines():
    global _FLOWLINES
    if _FLOWLINES is None:
        from src.build._make_basins import _load_flowlines

        _FLOWLINES = _load_flowlines()
        if _FLOWLINES is None:
            raise RuntimeError("NHD flowlines layer missing; run `python -m src.build._make_map_overlays`.")
    return _FLOWLINES


def delineate_basin_for_pin(lat: float, lon: float, timeout: int = 60) -> gpd.GeoDataFrame:
    """Upstream basin for an arbitrary (lat, lon), delineated by the SAME nearest-flowline snap as
    training sites (not /comid/position, which mis-snaps mainstem pins to a neighbouring tributary).

    Returns `[site_uid, comid, area_km2, geometry]` carrying the authoritative COMID -- required for
    the COMID-keyed static features (features._basin_comid). No reported area at a pin, so pure
    nearest. Raises ValueError outside the flowlines extent (no silent lateral-snap fallback).
    """
    from src.build._make_basins import _compute_basin1

    return _compute_basin1("pin", lat, lon, flowlines=_flowlines(), timeout=timeout)


def build_virtual_basin(lat: float, lon: float, target_year: int = TARGET_YEAR, timeout: int = 60) -> SiteData:
    """Assemble a SiteData (water=None) for a virtual sensor at (lat, lon) for `target_year`.

    Weather spans target_year ±2 months so trailing rolling/lag features have lead-in. Everything else is a grid_global intersection + joins (see access.build_virtual_site_data).
    """
    basin = delineate_basin_for_pin(lat, lon, timeout=timeout)
    ystart = pd.Timestamp(f"{target_year}-01-01")
    yend = pd.Timestamp(f"{target_year}-12-31")
    return access.build_virtual_site_data(basin, lat, lon, weather_start=ystart - _BUFFER, weather_end=yend + _BUFFER)
