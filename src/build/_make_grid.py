"""Build grid_global.parquet -- the ONE canonical Voronoi tessellation over ALL Iowa IEM cells.

Phase 0 of the re-grain refactor. This replaces the old per-basin *local* Voronoi
(data/weather/make_grid.py) with a single *global* tessellation, computed once over every cell
of the static IEM ~4 km precipitation grid (2018-06-15 reference day). Every downstream global
aggregate (crops_global, surplus_global, weather_global) and every per-site view keys off the
`global_node_id` produced here.

Basin-independent by design: no basin, no sensor, no D8 raster enter this builder -- those are
per-site concerns that move to src/data/site_view.py. That is the whole point of the re-grain:
the cell geometry a value lives on is a function of the IEM cell alone, not of any basin.

Output (src/data/interim/grid_global.parquet), one row per IEM cell:
    global_node_id  int64    canonical IEM cell index (0..N-1, the reference-day row ordering)
    x, y            float64  cell centroid, EPSG:5070 metres
    lon, lat        float64  cell centroid, WGS84
    cell_area       float64  Voronoi cell area, m^2 (EPSG:5070)
    geometry        polygon  Voronoi cell, EPSG:5070

`global_node_id` = the row index into the IEM reference-day ordering -- identical to the
convention the old per-basin grids used (`reset_index(names="global_node_id")` over the same
`day` frame), so the re-grain's joins stay consistent with any legacy artifact.

Unlike the old per-basin halo Voronoi, the global tessellation resolves every interior cell
canonically: no halo pad tuning, no edge-ambiguity. Only true IEM-domain-boundary cells (whose
Voronoi region is infinite) are dropped.

Usage
-----
    python -m src.build._make_grid            # build if missing
    python -m src.build._make_grid --force    # rebuild
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import requests
from scipy.spatial import Voronoi
from shapely.geometry import Polygon

_THIS_DIR = Path(__file__).resolve().parent          # src/build/
_SRC = _THIS_DIR.parent                              # src/
sys.path.insert(0, str(_SRC.parent))                 # repo root on path -> `import src.data.crs`

from src.data.crs import EQUAL_AREA_CRS

_RAW_DIR = _SRC / "data" / "raw" / "weather"          # cached IEM reference-day zip
_INTERIM_DIR = _SRC / "data" / "interim"
_GRID_GLOBAL_FILE = _INTERIM_DIR / "grid_global.parquet"

# A single IEM day supplies the cell polygons; the choice of day is irrelevant (geometry only).
IEM_GRID_DATE = date(2018, 6, 15)

_SHP_URL = (
    "https://mesonet.agron.iastate.edu/rainfall/dshape.php?"
    "month={month}&day={day}&year={year}"
    "&geometry=polygon&duration=day&epsg=4326"
)


def _download_shapefile(d: date) -> Path | None:
    """Fetch (and cache) the IEM daily precipitation shapefile zip for date `d`.

    Returns the local zip path, or None on failure. Skips the network request if already cached
    under src/data/raw/weather/. IEM serves HTML on no-data days (content not starting with PK).
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

    We use the geometry only. The API serves EPSG:4269 (NAD83) despite epsg=4326; the datums
    are sub-metre apart, so we re-label to 4326.
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


def _finite_voronoi(points: np.ndarray) -> dict[int, Polygon]:
    """Map point index -> its Voronoi cell polygon, skipping infinite/degenerate (hull) cells."""
    vor = Voronoi(points)
    polys: dict[int, Polygon] = {}
    for pidx, ridx in enumerate(vor.point_region):
        verts = vor.regions[ridx]
        if not verts or -1 in verts:
            continue  # infinite (domain-boundary) or degenerate cell
        pts = vor.vertices[verts]
        c = pts.mean(axis=0)  # order vertices CCW so the polygon is valid
        pts = pts[np.argsort(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))]
        polys[pidx] = Polygon(pts)
    return polys


def build_global_grid(force: bool = False) -> gpd.GeoDataFrame:
    """Build (or load) grid_global.parquet: one canonical Voronoi cell per IEM grid cell."""
    if _GRID_GLOBAL_FILE.exists() and not force:
        print(f"{_GRID_GLOBAL_FILE.name} exists; use --force to rebuild.")
        return gpd.read_parquet(_GRID_GLOBAL_FILE)

    zip_path = _download_shapefile(IEM_GRID_DATE)
    if zip_path is None:
        raise RuntimeError(f"Could not obtain the IEM reference-day shapefile for {IEM_GRID_DATE}.")
    day = _parse_day_gdf(zip_path, IEM_GRID_DATE)
    if day is None or day.empty:
        raise RuntimeError("IEM reference-day parse returned no cells.")

    day = day.to_crs(EQUAL_AREA_CRS)
    centroids = day.geometry.centroid
    lonlat = centroids.to_crs("EPSG:4326")
    x = centroids.x.to_numpy()
    y = centroids.y.to_numpy()
    lon = lonlat.x.to_numpy()
    lat = lonlat.y.to_numpy()

    polys = _finite_voronoi(np.column_stack([x, y]))  # {row index -> Voronoi polygon}

    keep = [i for i in range(len(day)) if i in polys]
    grid = gpd.GeoDataFrame(
        {
            "global_node_id": np.asarray(keep, dtype=np.int64),
            "x": x[keep],
            "y": y[keep],
            "lon": lon[keep],
            "lat": lat[keep],
            "cell_area": np.array([polys[i].area for i in keep], dtype=float),
        },
        geometry=[polys[i] for i in keep],
        crs=EQUAL_AREA_CRS,
    )

    _INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    grid.to_parquet(_GRID_GLOBAL_FILE)
    dropped = len(day) - len(grid)
    print(
        f"grid_global: {len(grid):,} cells ({dropped} domain-boundary cells dropped) "
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
