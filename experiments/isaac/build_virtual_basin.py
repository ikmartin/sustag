"""
From a lat,lon pair, build a virtual basin with complete dataset.

-> snap to nearest centroid of rain grid, make centroid new lat,lon reference point
-> create the basin gdf
-> get rain grid of basin, get rain data
-> get crop data, surplus data for basin
-> use recipe to generate
"""

import requests
import sys

sys.path.insert(0, "../")
from data.settings import get_equal_area_crs, get_region_bbox
import numpy as np
import pandas as pd
import geopandas as gpd

EQUAL_AREA_CRS = get_equal_area_crs()


# ── Basin 1: NLDI position snap ───────────────────────────────────────────────

_NLDI_BASE = "https://api.water.usgs.gov/nldi/linked-data"


def _compute_basin1(lat: float, lon: float, timeout: int = 60) -> gpd.GeoDataFrame:
    """NLDI position-snap basin for a single site."""
    pos = requests.get(
        f"{_NLDI_BASE}/comid/position",
        params={"coords": f"POINT({lon} {lat})", "f": "json"},
        timeout=timeout,
    )
    pos.raise_for_status()
    feats = pos.json().get("features", [])
    if not feats:
        raise ValueError(f"No NHDPlus catchment near ({lat}, {lon}).")
    comid = feats[0]["properties"]["comid"]

    resp = requests.get(
        f"{_NLDI_BASE}/comid/{comid}/basin",
        params={"f": "json", "simplified": "true"},
        timeout=timeout,
    )
    resp.raise_for_status()
    gdf = gpd.GeoDataFrame.from_features(resp.json()["features"], crs="EPSG:4326")
    if gdf.empty:
        raise ValueError(f"NLDI returned empty basin for COMID {comid} at lat,lon = ({lat}, {lon}).")

    gdf = gdf[["geometry"]].copy()
    gdf["comid"] = comid
    gdf["area_km2"] = gdf.to_crs(EQUAL_AREA_CRS).area / 1e6
    return gdf[["comid", "area_km2", "geometry"]]


from shapely.geometry import Polygon
from scipy.spatial import Voronoi, cKDTree
from datetime import date


def _parse_day_gdf(zip_path: Path, d: date) -> gpd.GeoDataFrame | None:
    """Parse an IEM daily zip into precipitation polygons (EPSG:4326).

    Columns: date, precip_in_1d, geometry. The API serves EPSG:4269 (NAD83)
    despite epsg=4326; the datums are sub-metre apart so we re-label.
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
    gdf = gdf.rename(columns={"rainfall": "precip_in_1d"})
    gdf["date"] = d
    return gdf[["date", "precip_in_1d", "geometry"]].copy()


def _finite_voronoi(points: np.ndarray) -> dict[int, Polygon]:
    """Map point index -> its Voronoi cell polygon, skipping infinite/empty cells."""
    vor = Voronoi(points)
    polys = {}
    for pidx, ridx in enumerate(vor.point_region):
        verts = vor.regions[ridx]
        if not verts or -1 in verts:
            continue  # hull (infinite) or degenerate cell
        pts = vor.vertices[verts]
        c = pts.mean(axis=0)  # order vertices CCW so the polygon is valid
        pts = pts[np.argsort(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))]
        polys[pidx] = Polygon(pts)
    return polys


_ALBERS = get_equal_area_crs()  # equal-area CRS for the Voronoi/area math (EPSG:5070)
# Geometry of the grid only — a single IEM day supplies the cell polygons.
IEM_GRID_DATE = date(2018, 6, 15)


def _download_shapefile(d: date) -> Path | None:
    """Fetch the IEM daily precipitation shapefile for date d into weather_raw/.

    Returns the local zip path, or None on failure. Skips the network request if
    the file is already cached (here, or in the sibling rain/rain_raw/ cache, so
    a prior rain build's downloads are reused).
    """
    path = _RAW_DIR / f"iowa_rain_{d.isoformat()}.zip"
    if path.exists():
        return path

    url = _SHP_URL.format(month=d.month, day=d.day, year=d.year)
    try:
        resp = requests.get(url, timeout=(10, 60))
        resp.raise_for_status()
        if not resp.content.startswith(b"PK"):  # IEM serves HTML on no-data days
            return None
        _RAW_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return path
    except Exception as e:
        print(f"  [WARN] {d}: {e}")
        return None


def build_grid(basin_gdf) -> gpd.GeoDataFrame:
    """Build the Voronoi target-cell grid for one basin (see module docstring)."""
    basin = basin_gdf.to_crs(_ALBERS)
    basin_poly = basin.geometry.union_all()
    minx, miny, maxx, maxy = basin.total_bounds

    # A single IEM day supplies the cell polygons; centroids give the node coords.
    day = _parse_day_gdf(_download_shapefile(IEM_GRID_DATE), IEM_GRID_DATE).to_crs(_ALBERS)
    centroids = day.geometry.centroid
    lonlat = centroids.to_crs("EPSG:4326")
    grid = gpd.GeoDataFrame(
        {
            "x": centroids.x.to_numpy(),
            "y": centroids.y.to_numpy(),
            "lon": lonlat.x.to_numpy(),
            "lat": lonlat.y.to_numpy(),
        },
        geometry=day.geometry.values,
        crs=_ALBERS,
    )

    # Nodes whose cell touches the basin are the targets; max spacing sets the pad.
    grid["is_target"] = grid.geometry.intersects(basin_poly)
    tgt_xy = grid.loc[grid["is_target"], ["x", "y"]].to_numpy()
    if len(tgt_xy) < 2:
        all_xy = grid[["x", "y"]].to_numpy()
        pad = 2 * float(np.median(cKDTree(all_xy).query(all_xy, k=2)[0][:, 1]))
    else:
        pad = 2 * float(cKDTree(tgt_xy).query(tgt_xy, k=2)[0][:, 1].max())

    # Halo = every node within the padded bbox; keep the original IEM row index
    # (global_node_id) so cells line up across basins.
    px0, py0, px1, py1 = minx - pad, miny - pad, maxx + pad, maxy + pad
    halo = grid[grid["x"].between(px0, px1) & grid["y"].between(py0, py1)].reset_index(names="global_node_id")
    polys = _finite_voronoi(halo[["x", "y"]].to_numpy())

    rows = []
    for i in halo.index[halo["is_target"]]:
        if i not in polys:
            print(f"  [warn] {site_uid}: a target node has an infinite cell (pad too small) — skipped")
            continue
        rows.append(
            {
                "node_id": len(rows),
                "global_node_id": int(halo.at[i, "global_node_id"]),
                "x": halo.at[i, "x"],
                "y": halo.at[i, "y"],
                "lat": halo.at[i, "lat"],
                "lon": halo.at[i, "lon"],
                "cell_area": polys[i].area,
                "geometry": polys[i],
            }
        )
    grid = gpd.GeoDataFrame(rows, crs=_ALBERS)

    # Per-node columns from the in-memory grid (no parquet yet): flow distance to
    # the sensor and the fraction of each cell inside the basin.
    if len(grid):
        try:
            grid["dist_to_sensor"] = _grid_node_distances(site_uid, grid)
        except Exception as e:
            print(f"  [warn] {site_uid}: dist_to_sensor unavailable ({e}); column set to NaN")
            grid["dist_to_sensor"] = np.nan
        grid["frac_cell_in_basin"] = _grid_basin_fractions(site_uid, grid)
    return grid


import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
from pathlib import Path

# ── Region to keep, in WGS84 (lon/lat) ───────────────────────────────────────
# Shared region from pipeline_config.toml [region].bbox_wgs84 as
# (min_lon, min_lat, max_lon, max_lat). Edit that file to adjust the clip margin.
BBOX_WGS84 = get_region_bbox()


def clip_to_bbox(src_path: Path, out_path: Path) -> tuple[int, int]:
    """Window a national CDL raster down to BBOX_WGS84 and write it out.

    Returns (width, height) of the clip. Preserves CRS, class codes, nodata and
    the CDL colormap; compresses output with LZW.
    """
    with rasterio.open(src_path) as src:
        # Reproject the WGS84 box into the raster's CRS (EPSG:5070). densify_pts
        # follows the curved Albers edges so the envelope safely encloses the box.
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *BBOX_WGS84, densify_pts=21)
        window = from_bounds(left, bottom, right, top, src.transform).round_offsets().round_lengths()

        # The source must fully contain the clip region. A smaller source (e.g. a
        # partial download) would otherwise be silently clamped to a too-small
        # clip, breaking the uniform extent every clip is meant to share.
        if (
            window.col_off < 0
            or window.row_off < 0
            or window.col_off + window.width > src.width
            or window.row_off + window.height > src.height
        ):
            raise ValueError(
                f"Clip region is not fully contained in source '{src_path.name}'. "
                f"The source raster is smaller than the region {BBOX_WGS84} — "
                f"re-download the full region before clipping."
            )

        data = src.read(window=window)
        transform = src.window_transform(window)
        profile = src.profile.copy()
        profile.update(
            height=int(window.height),
            width=int(window.width),
            transform=transform,
            compress="lzw",
        )
        try:
            colormap = src.colormap(1)
        except ValueError:
            colormap = None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(data)
        if colormap:
            dst.write_colormap(1, colormap)
    return int(window.width), int(window.height)


lat, lon = 35, -90
gdf = _compute_basin1(lat, lon)
print(gdf.columns)
print(gdf.info())
print(gdf.head())
