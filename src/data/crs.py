"""Project-wide coordinate-reference constant + geo helper.

Runtime-safe: imported by the read path (src/data/access, src/data/site_view) *and* by
src/build. The equal-area CRS is an invariant, not a tunable -- so it lives here as a
constant, not in src/build/pipeline_config.toml (which holds build knobs). Keeping it here
is what lets the read path do geometry without importing any build config.
"""

from pyproj import Transformer

EQUAL_AREA_CRS = "EPSG:5070"  # CONUS Albers; all area / point-in-polygon / Voronoi math runs here


def wgs84_to_albers(min_lon, min_lat, max_lon, max_lat):
    """Reproject a WGS84 bbox to CONUS Albers (EPSG:5070).

    densify_pts samples the curved Albers edges so the returned box fully encloses the WGS84
    region (transforming only the two corners undershoots the east/south edges). Returns
    (min_x, min_y, max_x, max_y) in Albers metres.
    """
    transformer = Transformer.from_crs("epsg:4326", EQUAL_AREA_CRS, always_xy=True)
    return transformer.transform_bounds(min_lon, min_lat, max_lon, max_lat, densify_pts=21)
