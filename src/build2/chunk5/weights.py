"""The catchment x gridMET-cell weight matrix -- how daily weather reaches a gauge.

DAILY WEATHER IS NEVER STORED PER COMID. 1.49M catchments x ~6,600 days is ten billion rows. What is stored instead is the geometry: for each catchment, which gridMET cells it overlaps and by how much. That is 4M rows, static, and it turns a gauge's daily weather into

    reach set -> weights -> collapse to (cell_id, w_km2) at the GAUGE level -> read the cells' days -> weighted mean

with the collapse happening BEFORE any date is touched. A basin spans hundreds to thousands of cells, so the per-day work is over cells, never over catchment x cell x day.

THE MATRIX IS BUILT BY THE SAME ENGINE AS EVERY OTHER PRODUCT, using one trick: a raster whose pixel VALUE is a gridMET cell id. Running fractional zonal statistics against it returns, per catchment, the ids it touches and the share of its area in each -- which is the overlap, measured rather than assumed, by the same code that counts corn. A cell half inside a catchment weighs half.

IN 4326, gridMET's OWN CRS. The cells are 1/24-degree squares in lon/lat, so an id raster on that lattice is exact and tiny (about 1,000 x 500 pixels for the whole AOE). Projecting them to 5070 first would turn exact squares into approximated quadrilaterals for no gain.

THE WEIGHT IS AN AREA IN KM2, NOT A COUNT OF CELLS. The engine returns coverage in units of the value raster's own cells, so the raw number says "this catchment covers 0.35 of a cell" -- and on a lat/lon lattice that is not an area, because the cells are not equal-area. One twenty-fourth of a degree of longitude is 4.03 km at 29.5N and 3.00 km at 49.7N, so cell ground area runs from 18.7 km2 down to 13.9 km2 across this AOE, a 34% spread. Weighting a basin's cells by fractional counts therefore over-weights its northern cells systematically -- small within one basin, around 7% across three degrees of latitude, and wrong for nothing. `cell_area_km2` converts, and every consumer gets an area it can reason about.

THE MATRIX IS KEPT, NOT CONSUMED. Chunk 6 uses it once per gauge, but the widget's virtual-pin path delineates an arbitrary basin at request time and needs to run the same reduction with no build in sight.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from . import zonal

# gridMET's native lattice, as declared by `chunk1.weather`. Repeated rather than imported so a change there fails loudly here instead of silently re-keying the matrix.
RES = 1.0 / 24.0
COLS = 1386

ID_RASTER = config.INTERIM / "comid_features" / "gridmet_cell_ids.tif"


# Mean Earth radius, km. The cells are small enough that a sphere and an ellipsoid disagree by well under the
# precision anything here needs.
_EARTH_R_KM = 6371.0088


def cell_area_km2(cell_id) -> np.ndarray:
    """Ground area of each gridMET cell, km2. A function of LATITUDE alone on this lattice.

    Exact for a spherical Earth rather than the small-angle `21.47 * cos(lat)` approximation: the area between two parallels is `R^2 * dlon * (sin(lat2) - sin(lat1))`, which costs one extra sine and removes the question of where the approximation stops holding. `cell_id = row * 1386 + col` and `id_raster` places row 0 at the pole, so the row alone gives the cell's latitude band.
    """
    row = np.asarray(cell_id, dtype="float64") // COLS
    lat_top = np.radians(90.0 - row * RES)
    lat_bot = np.radians(90.0 - (row + 1) * RES)
    return _EARTH_R_KM ** 2 * np.radians(RES) * (np.sin(lat_top) - np.sin(lat_bot))


def _grid():
    """The AOE's gridMET cells, from chunk 1's own `grid.parquet`. Never re-derived."""
    import geopandas as gpd

    from ..chunk1 import weather

    p = weather._grid_path()
    if not p.exists():
        raise FileNotFoundError(f"{p} is missing -- run chunk 1's weather branch first")
    return gpd.read_parquet(p)


def id_raster(force: bool = False):
    """Write (or reuse) the raster whose value is `cell_id`, on gridMET's own lattice in EPSG:4326.

    `cell_id` is `row * 1386 + col` on gridMET's CONUS grid, exactly as `chunk1.weather.cell_id` computes it -- a function of position, so widening the AOE appends cells and renumbers none. Cells outside the AOE are left at -1 and a catchment overlapping one records no weight there, which is the honest answer: there is no weather on file for them.
    """
    if ID_RASTER.exists() and not force:
        return ID_RASTER

    g = _grid()
    lon, lat = g.lon.to_numpy(), g.lat.to_numpy()
    col = np.rint((lon + 180.0) / RES).astype("int64")
    row = np.rint((90.0 - lat) / RES).astype("int64")
    c0, c1, r0, r1 = col.min(), col.max(), row.min(), row.max()
    ids = np.full((r1 - r0 + 1, c1 - c0 + 1), -1, dtype="int32")
    ids[row - r0, col - c0] = (row * COLS + col).astype("int32")

    # Pixel edges, not centres: the cell whose centre is at `lon` spans half a step either side.
    west = (c0 * RES) - 180.0 - RES / 2
    north = 90.0 - (r0 * RES) + RES / 2
    print(f"  gridmet id raster: {ids.shape[1]}x{ids.shape[0]} cells, "
          f"{int((ids >= 0).sum()):,} in the AOE", flush=True)
    return zonal.cell_id_raster(ID_RASTER, west, north, RES, "EPSG:4326", ids)


def _reduce(comids: np.ndarray, res: pd.DataFrame) -> pd.DataFrame:
    """One batch -> long (comid, cell_id, w_km2) rows, dropping the outside-AOE sentinel.

    `frac * count` is the coverage attributable to one cell IN CELL UNITS -- "this catchment covers 0.35 of that cell" -- so it is multiplied by that cell's own ground area to become km2. See the module docstring for why a fractional-cell weight is not an area.
    """
    out = []
    n_cells = res["gridmet_count"].to_numpy(dtype="float64")
    for i, (cells, frac) in enumerate(zip(res["gridmet_unique"], res["gridmet_frac"])):
        if cells is None or not len(cells):
            continue
        cells = np.asarray(cells, dtype="int64")
        keep = cells >= 0
        if not keep.any():
            continue
        cells = cells[keep]
        share = np.asarray(frac, dtype="float64")[keep] * n_cells[i]
        out.append(pd.DataFrame({"comid": np.int64(comids[i]),
                                 "cell_id": cells.astype("int32"),
                                 "w_km2": (share * cell_area_km2(cells)).astype("float32")}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        {"comid": pd.Series(dtype="int64"), "cell_id": pd.Series(dtype="int32"),
         "w_km2": pd.Series(dtype="float32")})


def build(cat: zonal.Catchments, limit: int | None = None, part_dir=None,
          force: bool = False) -> pd.DataFrame:
    """The weight matrix, one row per (catchment, overlapped gridMET cell), weighted in km2."""
    r = id_raster(force=force)
    return zonal.extract(cat, {"gridmet": r}, "EPSG:4326", ops=zonal.CATEGORICAL_OPS,
                         reduce=_reduce, limit=limit, force=force,
                         part_dir=None if part_dir is None else part_dir / "weights_gridmet",
                         label="gridmet weights ")
