"""Geometry helpers shared between the map, info, and forecast panels.

The map panel writes the user's current selection ("region of interest") to
the `region-geom` store as a GeoJSON geometry — either a Point (point-select
mode) or a Polygon (rectangle/polygon draw mode). The functions here turn that
selection into whatever shape downstream consumers need.
"""

import json
import requests
import geopandas as gpd
from shapely.geometry import shape, mapping

_NLDI_BASE = "https://api.water.usgs.gov/nldi/linked-data"


def delineate_basin_for_pin(lat: float, lon: float, timeout: int = 60) -> dict:
    """Fetch the upstream drainage basin for an arbitrary (lat, lon) pin.

    Makes two NLDI requests:
      1. Resolve the pin to the nearest NHDPlus COMID.
      2. Fetch the upstream basin polygon for that COMID.

    Returns a GeoJSON FeatureCollection dict (EPSG:4326) suitable for passing
    directly to ``dl.GeoJSON(data=...)``.

    Raises ValueError if the point is outside CONUS or off the NHD network.
    """
    pos = requests.get(
        f"{_NLDI_BASE}/comid/position",
        params={"coords": f"POINT({lon} {lat})", "f": "json"},
        timeout=timeout,
    )
    pos.raise_for_status()
    feats = pos.json().get("features", [])
    if not feats:
        raise ValueError(
            f"No NHDPlus catchment for (lat={lat}, lon={lon}); "
            "point may be outside CONUS or off-network."
        )
    comid = feats[0]["properties"]["comid"]

    resp = requests.get(
        f"{_NLDI_BASE}/comid/{comid}/basin",
        params={"f": "json", "simplified": "true"},
        timeout=timeout,
    )
    resp.raise_for_status()
    gdf = gpd.GeoDataFrame.from_features(resp.json()["features"], crs="EPSG:4326")
    if gdf.empty:
        raise ValueError(f"NLDI returned an empty basin for COMID {comid}.")
    return json.loads(gdf.to_json())


_direction_array = None


def delineate_basin_v3_for_pin(lat: float, lon: float) -> dict:
    """Compute the D8 raster basin for an arbitrary (lat, lon) pin.

    Uses the same 500 m IWQIS flow-direction raster and BFS flood-fill as
    make_basins.py's basin3 method.  The direction array is loaded once and
    cached in memory for subsequent calls.

    Returns a GeoJSON FeatureCollection dict (EPSG:4326) suitable for passing
    directly to ``dl.GeoJSON(data=...)``.

    Raises ValueError if the point is outside the Iowa raster domain.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root/

    from src.data.d8 import load_direction_array
    from src.build._make_basins import _compute_basin3

    global _direction_array
    if _direction_array is None:
        _direction_array = load_direction_array()

    gdf = _compute_basin3("pin", lat, lon, _direction_array)
    if gdf is None:
        raise ValueError(f"Point ({lat}, {lon}) is outside the Iowa raster domain.")
    return json.loads(gdf.to_json())


# Rough placeholder: ~1km, used to turn a clicked point into a small area for
# forecast purposes. Not geodesically accurate — revisit once the model
# defines what source-area size it expects.
DEFAULT_POINT_BUFFER_DEG = 0.01


def normalize_for_forecast(region_geojson, buffer_deg=DEFAULT_POINT_BUFFER_DEG):
    """Return a Polygon GeoJSON geometry suitable for forecast queries.

    Points (from point-select mode) are buffered into a small disk so the
    model always receives an area. Polygons (from rectangle/polygon draw
    mode) are passed through unchanged.
    """
    geom = shape(region_geojson)
    if geom.geom_type == "Point":
        geom = geom.buffer(buffer_deg)
    return mapping(geom)


def get_downstream_sites(region_geojson, sites_df):
    """Return uids of monitoring sites downstream of the given region.

    Placeholder: identifying downstream sites requires a watershed/flow-
    network dataset that isn't integrated yet. Returns an empty list so
    callers can be written against the final interface now.

    Args:
        region_geojson: Polygon GeoJSON geometry (e.g. from
            normalize_for_forecast) of the source region.
        sites_df: DataFrame of candidate monitoring sites, as returned by
            iwqis_utils.get_iwqis_sites().

    Returns:
        List of site uids downstream of region_geojson.
    """
    return []
