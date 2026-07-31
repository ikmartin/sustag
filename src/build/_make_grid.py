"""Build grid_global.parquet -- the ONE canonical cell grid over covariate_bbox, on gridMET's native ~4 km (1/24 deg) grid.

Re-based from the old Iowa-only IEM Voronoi onto gridMET's national grid so the covariate grid can cover basins that reach outside Iowa (the IEM product only serves Iowa cells). Every downstream global aggregate (crops_global, surplus_global, weather_global) and every per-site view keys off the `global_node_id` produced here.

The cells are the gridMET grid points inside covariate_bbox, each a 1/24-deg square. gridMET meteorology maps onto them directly (weather_global samples gridMET at these centroids). IEM precip (precip_in_1d) is joined per-cell where a cell falls inside the Iowa IEM footprint, NaN elsewhere.

Basin-independent by design: no basin/sensor/D8 raster enter here. Runs AFTER _make_region (so get_covariate_bbox() is the basin-enclosing box), BEFORE weather/crops/surplus.

Output (src/data/interim/grid_global.parquet), one row per gridMET cell in covariate_bbox:
    global_node_id  int64    canonical cell index (0..N-1, row-major over the in-box gridMET grid)
    x, y            float64  cell centroid, EPSG:5070 metres
    lon, lat        float64  cell centroid, WGS84 (gridMET native grid point)
    cell_area       float64  cell area, m^2 (EPSG:5070)
    geometry        polygon  1/24-deg cell, EPSG:5070

The IEM helpers (_download_shapefile / _parse_day_gdf / IEM_GRID_DATE) remain here because _make_weather still needs the IEM polygons to join precip_in_1d onto the new grid.

Usage
-----
    python -m src.build._make_grid            # build if missing
    python -m src.build._make_grid --force    # rebuild
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import requests
from shapely.geometry import box

_THIS_DIR = Path(__file__).resolve().parent          # src/build/
_SRC = _THIS_DIR.parent                              # src/
sys.path.insert(0, str(_SRC.parent))                 # repo root on path -> `import src.data.crs`

from src.build.config import get_covariate_bbox
from src.data.crs import EQUAL_AREA_CRS

_RAW_DIR = _SRC / "data" / "raw" / "weather"          # cached IEM reference-day zip
_INTERIM_DIR = _SRC / "data" / "interim"
_GRID_GLOBAL_FILE = _INTERIM_DIR / "grid_global.parquet"

# Point pygridmet's cache at raw/weather/gridMET_raw/ before importing it (mirrors _make_weather).
_GRIDMET_CACHE = _SRC / "data" / "raw" / "weather" / "gridMET_raw"
_GRIDMET_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HYRIVER_CACHE_NAME", str(_GRIDMET_CACHE / "hyriver.sqlite"))

GRIDMET_RES = 1.0 / 24.0  # gridMET native resolution, ~0.041667 deg (~4 km)

# A single IEM day supplies the IEM polygons (for the precip join in _make_weather); day irrelevant.
IEM_GRID_DATE = date(2018, 6, 15)

_SHP_URL = (
    "https://mesonet.agron.iastate.edu/rainfall/dshape.php?"
    "month={month}&day={day}&year={year}"
    "&geometry=polygon&duration=day&epsg=4326"
)


def _download_shapefile(d: date) -> Path | None:
    """Fetch (and cache) the IEM daily precipitation shapefile zip for date `d`.

    Returns the local zip path, or None on failure. Skips the network request if already cached under src/data/raw/weather/. IEM serves HTML on no-data days (content not starting with PK).
    """
    path = _RAW_DIR / f"iem_grid_{d.isoformat()}.zip"
    if path.exists():
        return path
    url = _SHP_URL.format(month=d.month, day=d.day, year=d.year)
    try:
        resp = requests.get(url, timeout=(10, 60))
        resp.raise_for_status()
        if not resp.content.startswith(b"PK"):
            print(f"  [WARN] {d}: IEM returned non-zip content (no-data day?).")
            return None
        _RAW_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return path
    except Exception as e:
        print(f"  [WARN] {d}: {e}")
        return None


def _parse_day_gdf(zip_path: Path, d: date) -> gpd.GeoDataFrame | None:
    """Parse an IEM daily zip into precipitation polygons (EPSG:4326).

    We use the geometry only. The API serves EPSG:4269 (NAD83) despite epsg=4326; the datums are sub-metre apart, so we re-label to 4326.
    """
    try:
        gdf = gpd.read_file(f"/vsizip/{zip_path}")
    except Exception as e:
        print(f"  [WARN] parse {zip_path.name}: {e}")
        return None
    if gdf is None or gdf.empty:
        return None
    gdf.columns = [c.lower() for c in gdf.columns]
    if "rainfall" not in gdf.columns:
        print(f"  [WARN] unexpected columns in {zip_path.name}: {list(gdf.columns)}")
        return None
    gdf = gdf.set_crs("EPSG:4326", allow_override=True)
    return gdf[["geometry"]].copy()


def _gridmet_grid_coords(bbox: tuple) -> tuple[np.ndarray, np.ndarray]:
    """gridMET's native 1D (lon, lat) coordinate arrays covering bbox (one tiny 1-day fetch).

    Reading the coords straight off a gridMET response guarantees the grid aligns exactly with the gridMET pixels weather_global samples, so no interpolation offset is introduced."""
    import pygridmet

    d = IEM_GRID_DATE.isoformat()
    ds = pygridmet.get_bygeom(tuple(bbox), (d, d), variables=["pr"])
    return np.sort(np.unique(ds["lon"].to_numpy())), np.sort(np.unique(ds["lat"].to_numpy()))


def build_global_grid(force: bool = False) -> gpd.GeoDataFrame:
    """Build (or load) grid_global.parquet: one 1/24-deg cell per gridMET grid point in covariate_bbox."""
    if _GRID_GLOBAL_FILE.exists() and not force:
        print(f"{_GRID_GLOBAL_FILE.name} exists; use --force to rebuild.")
        return gpd.read_parquet(_GRID_GLOBAL_FILE)

    bbox = get_covariate_bbox()
    lons, lats = _gridmet_grid_coords(bbox)
    LON, LAT = np.meshgrid(lons, lats)  # (nlat, nlon), row-major
    lon_f, lat_f = LON.ravel(), LAT.ravel()

    # keep gridMET points whose centre falls inside covariate_bbox (the fetch pads slightly)
    minx, miny, maxx, maxy = bbox
    keep = (lon_f >= minx) & (lon_f <= maxx) & (lat_f >= miny) & (lat_f <= maxy)
    lon_f, lat_f = lon_f[keep], lat_f[keep]
    if len(lon_f) == 0:
        raise RuntimeError(f"No gridMET cells inside covariate_bbox {bbox}.")

    # each cell = a 1/24-deg WGS84 square about its gridMET centre, reprojected to the equal-area CRS
    half = GRIDMET_RES / 2.0
    cells_wgs = gpd.GeoSeries(
        [box(x - half, y - half, x + half, y + half) for x, y in zip(lon_f, lat_f)], crs="EPSG:4326"
    )
    cells = cells_wgs.to_crs(EQUAL_AREA_CRS)
    cent = cells.centroid

    grid = gpd.GeoDataFrame(
        {
            "global_node_id": np.arange(len(lon_f), dtype=np.int64),
            "x": cent.x.to_numpy(),
            "y": cent.y.to_numpy(),
            "lon": lon_f,
            "lat": lat_f,
            "cell_area": cells.area.to_numpy(),
        },
        geometry=cells.values,
        crs=EQUAL_AREA_CRS,
    )

    _INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    grid.to_parquet(_GRID_GLOBAL_FILE)
    print(
        f"grid_global: {len(grid):,} gridMET cells over covariate_bbox {tuple(round(b, 2) for b in bbox)} "
        f"-> {_GRID_GLOBAL_FILE.relative_to(_SRC.parent)}"
    )
    return grid


def main(force: bool = False) -> None:
    build_global_grid(force=force)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Rebuild grid_global.parquet.")
    args = parser.parse_args()
    main(force=args.force)
