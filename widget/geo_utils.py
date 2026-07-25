"""Geometry helpers shared between the map, info, and forecast panels.

The map panel writes the user's current selection ("region of interest") to
the `region-geom` store as a GeoJSON geometry — either a Point (point-select
mode) or a Polygon (rectangle/polygon draw mode). The functions here turn that
selection into whatever shape downstream consumers need.
"""

import json
import sys
from pathlib import Path

from shapely.geometry import shape, mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root/, for src.* imports


_flowlines = None
_direction_array = None


def _get_flowlines():
    """NHD flowlines layer, loaded once and cached for the nearest-flowline snap."""
    global _flowlines
    if _flowlines is None:
        from src.build._make_basins import _load_flowlines

        _flowlines = _load_flowlines()
        if _flowlines is None:
            raise RuntimeError("NHD flowlines layer missing; run `python -m src.build._make_map_overlays`.")
    return _flowlines


def delineate_basin_v1_for_pin(lat: float, lon: float, site_uid: str = "pin", timeout: int = 60) -> dict:
    """v1 -- NEAREST-FLOWLINE SNAP basin for a pin. The delineation the product ships.

    Delegates to the builder's _compute_basin1, so a pin basin is the same artifact as a batch-built
    basin1: same NLDI basin call, same ``[site_uid, comid, area_km2, geometry]`` schema, same
    AUTHORITATIVE COMID resolved from the snapped reach. No reported_area is passed -- a map pin has
    none -- so this is the pure-nearest rule, identical to how a new deploy site would be delineated.

    A COMID must never be inherited between delineations; its TOT_/CAT_ attributes describe its own
    reach's catchment. Pass the real ``site_uid`` when saving a correction; "pin" is for live preview.

    Returns a GeoJSON FeatureCollection dict (EPSG:4326). Raises ValueError outside the flowlines
    extent or off the NHD network.
    """
    from src.build._make_basins import _compute_basin1

    gdf = _compute_basin1(site_uid, lat, lon, flowlines=_get_flowlines(), timeout=timeout)
    return json.loads(gdf.to_json())


# Back-compat alias: the confirm handler and map preview import this name.
delineate_basin_for_pin = delineate_basin_v1_for_pin


def delineate_basin_v2_for_pin(lat: float, lon: float, site_uid: str = "pin", timeout: int = 60) -> dict:
    """v2 -- CONTAINING-CATCHMENT basin for a pin (NLDI /comid/position). Cross-check only.

    Returns whichever catchment polygon the point falls inside, which is NOT necessarily the reach
    the pin sits on (see _make_basins for the mainstem-divergence failure). Shown alongside v1 so a
    reviewer can see when the two methods disagree.
    """
    from src.build._make_basins import _compute_basin2

    gdf = _compute_basin2(site_uid, lat, lon, timeout=timeout)
    return json.loads(gdf.to_json())


def delineate_basin_v3_for_pin(lat: float, lon: float) -> dict:
    """v3 -- D8 raster basin for a pin. Cross-check only; carries no COMID.

    Same 500 m flow-direction raster and BFS flood-fill as the builder's basin3. The direction array
    is loaded once and cached. Raises ValueError outside the Iowa raster domain.
    """
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
