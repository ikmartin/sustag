"""Runtime geo construction: (basin, sensor_lat, sensor_lon) -> the site's grid.

Unifies the old build_grid (real site) and build_grid_from_basin (virtual/deploy) into one
function over the canonical grid_global. A "site" is the set of grid_global cells whose polygon
intersects the basin, relabelled with a basin-local node_id, plus two basin-relative columns:
dist_to_sensor (D8 flow distance from the cell to the sensor's outlet) and frac_cell_in_basin
(fraction of the cell inside the basin).

The one runtime module carrying geo deps (the D8 flow raster via src.data.d8, the geopandas
intersection). access.py re-exports build_site_view so callers get it off the unified surface;
deploy/ imports it to build a virtual site's grid live from an arbitrary pin.

Output schema matches the old per-site grid parquet exactly:
    node_id, global_node_id, x, y, lat, lon, cell_area, dist_to_sensor, frac_cell_in_basin, geometry
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
from scipy.spatial import cKDTree

from src.data import d8
from src.data.crs import EQUAL_AREA_CRS

_INTERIM = Path(__file__).resolve().parent / "interim"
_GRID_GLOBAL_FILE = _INTERIM / "grid_global.parquet"

_GRID_COLS = [
    "node_id", "global_node_id", "x", "y", "lat", "lon",
    "cell_area", "dist_to_sensor", "frac_cell_in_basin", "geometry",
]

_GRID_GLOBAL: gpd.GeoDataFrame | None = None


def _grid_global() -> gpd.GeoDataFrame:
    """The canonical Voronoi tessellation (EPSG:5070), cached in-process."""
    global _GRID_GLOBAL
    if _GRID_GLOBAL is None:
        if not _GRID_GLOBAL_FILE.exists():
            raise FileNotFoundError(f"{_GRID_GLOBAL_FILE} missing — run `python -m src.build._make_grid`.")
        _GRID_GLOBAL = gpd.read_parquet(_GRID_GLOBAL_FILE)
    return _GRID_GLOBAL


def _grid_node_distances(grid: gpd.GeoDataFrame, field: np.ndarray) -> np.ndarray:
    """Flow distance (m) to the sensor for each cell. Pass 1 routes each node's centroid through
    the D8 field (with straddler recovery); pass 2 fills any still-NaN node from the nearest
    resolved node B: dist(A) = dist(B) + |centre(A) - centre(B)| (EPSG:5070 metres)."""
    dist = np.array([
        d8.sample_field(field, *d8.ll_to_image_pixel(lat, lon))
        for lat, lon in zip(grid["lat"].to_numpy(), grid["lon"].to_numpy())
    ])
    nan = np.isnan(dist)
    if nan.any() and (~nan).any():
        xy = grid[["x", "y"]].to_numpy()
        gap, idx = cKDTree(xy[~nan]).query(xy[nan])
        dist[nan] = dist[~nan][idx] + gap
    return dist


def _grid_basin_fractions(basin_poly, grid: gpd.GeoDataFrame) -> np.ndarray:
    """Fraction of each cell inside the basin, in [0, 1] (basin_poly assumed in EPSG:5070)."""
    cells = grid.geometry
    inside = cells.intersection(basin_poly).area
    return np.clip((inside / cells.area).to_numpy(), 0.0, 1.0)


def build_site_view(basin: gpd.GeoDataFrame, sensor_lat: float, sensor_lon: float, label: str = "") -> gpd.GeoDataFrame:
    """Build a site's grid from a basin + sensor location over grid_global.

    `basin` is any-CRS (reprojected to EPSG:5070 here). Works for a real site (basin from
    access.get_basin) or a virtual/ungauged site (NLDI basin from a drop point). Returns the
    per-site grid in the schema above, or an empty GeoDataFrame if no cell intersects the basin.
    """
    gg = _grid_global()
    basin_poly = basin.to_crs(EQUAL_AREA_CRS).geometry.union_all()

    members = gg[gg.geometry.intersects(basin_poly)].reset_index(drop=True).copy()
    if members.empty:
        return gpd.GeoDataFrame(columns=_GRID_COLS, crs=gg.crs)

    members.insert(0, "node_id", np.arange(len(members), dtype="int64"))
    try:
        field = d8.flow_distance_field_ll(sensor_lat, sensor_lon)
        members["dist_to_sensor"] = _grid_node_distances(members, field)
    except Exception as e:
        print(f"  [warn] {label}: dist_to_sensor unavailable ({e}); column set to NaN")
        members["dist_to_sensor"] = np.nan
    members["frac_cell_in_basin"] = _grid_basin_fractions(basin_poly, members)
    return members[_GRID_COLS]
