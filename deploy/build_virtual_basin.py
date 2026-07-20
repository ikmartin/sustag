"""Build a virtual SiteData for an arbitrary (lat, lon) in Iowa."""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path
from src.data import access
from src.data.access import SiteData

TARGET_YEAR = 2017
_BUFFER = pd.DateOffset(months=2)  # weather lead-in/out for trailing rolling/lag features
_NLDI_BASE = "https://api.water.usgs.gov/nldi/linked-data"


def delineate_basin_for_pin(lat: float, lon: float, timeout: int = 60) -> gpd.GeoDataFrame:
    """Upstream NLDI basin polygon for an arbitrary (lat, lon). Two requests: resolve the pin to the enclosing NHDPlus COMID, then fetch its upstream basin. Raises ValueError off-network."""
    pos = requests.get(
        f"{_NLDI_BASE}/comid/position", params={"coords": f"POINT({lon} {lat})", "f": "json"}, timeout=timeout
    )
    pos.raise_for_status()
    feats = pos.json().get("features", [])
    if not feats:
        raise ValueError(f"No NHDPlus catchment near ({lat}, {lon}); off-network or outside CONUS.")
    comid = feats[0]["properties"]["comid"]
    resp = requests.get(
        f"{_NLDI_BASE}/comid/{comid}/basin", params={"f": "json", "simplified": "true"}, timeout=timeout
    )
    resp.raise_for_status()
    gdf = gpd.GeoDataFrame.from_features(resp.json()["features"], crs="EPSG:4326")
    if gdf.empty:
        raise ValueError(f"NLDI returned an empty basin for COMID {comid}.")
    return gdf


def build_virtual_basin(lat: float, lon: float, target_year: int = TARGET_YEAR, timeout: int = 60) -> SiteData:
    """Assemble a SiteData (water=None) for a virtual sensor at (lat, lon) for `target_year`.

    Weather spans target_year ±2 months so trailing rolling/lag features have lead-in. Everything else is a grid_global intersection + joins (see access.build_virtual_site_data).
    """
    basin = delineate_basin_for_pin(lat, lon, timeout=timeout)
    ystart = pd.Timestamp(f"{target_year}-01-01")
    yend = pd.Timestamp(f"{target_year}-12-31")
    return access.build_virtual_site_data(basin, lat, lon, weather_start=ystart - _BUFFER, weather_end=yend + _BUFFER)
