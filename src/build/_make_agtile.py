"""Build interim/agtile_global.parquet: AgTile tile-drainage pixel counts over grid_global.

Pipeline B of the static-data additions -- the raster/areal path, mirroring _make_crops. Rasterizes grid_global onto the AgTile clip transform once and bincounts, per cell:
  tile_cells = pixels == 1 (tile-drained)
  cell_px    = every pixel the cell covers within the raster (coverage denominator)

AgTile-US is a PLAIN BINARY raster: 1 = tile-drained, 0 = everything else (undrained land, ag OR non-ag alike). It carries NO nodata / cropland mask (verified: the CONUS tif is nodata=None, values {0,1} only). An earlier design assumed non-ag land was masked as nodata -- it is NOT -- so there is no "agricultural pixel" denominator inside this raster. The two downstream features get their denominators elsewhere: tile_frac_basin from the basin AREA (geometric), and tile_frac_ag from CDL cultivated-cropland pixel counts (crops_global) -- see features._agtile_fractions. cell_px is stored only as a coverage diagnostic; a cell may legitimately have tile_cells=0 (covered but untiled).

Single vintage 2017 -> no year dimension (unlike crops_global).

Input:  src/data/raw/agtile/clipped/agtile_clip.tif (built by util/clip_agtile.py)
Output: src/data/interim/agtile_global.parquet  [global_node_id, tile_cells, cell_px] (committed)

Usage
-----
    python -m src.build._make_agtile [--force]
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds, intersection

_THIS_DIR = Path(__file__).resolve().parent  # src/build
_SRC = _THIS_DIR.parent  # src
sys.path.insert(0, str(_SRC.parent))  # repo root on path

_GRID_GLOBAL_FILE = _SRC / "data" / "interim" / "grid_global.parquet"
_OUT_FILE = _SRC / "data" / "interim" / "agtile_global.parquet"
_CLIP_TIF = _SRC / "data" / "raw" / "agtile" / "clipped" / "agtile_clip.tif"

_BINCOUNT_BLOCK = 2048  # raster rows per block, caps transient memory (as in _make_crops)


def _read_clip_window(src, bounds):
    """Read the AgTile clip band over `bounds` (in the clip CRS, `src.crs`). Returns (band, transform, nodata), or (None, None, None) if the bounds miss the raster."""
    window = from_bounds(*bounds, src.transform).round_offsets().round_lengths()
    window = intersection(window, Window(0, 0, src.width, src.height))
    if window.width <= 0 or window.height <= 0:
        return None, None, None
    return src.read(1, window=window), src.window_transform(window), src.nodata


def build_agtile_global(force: bool = False) -> pd.DataFrame:
    if _OUT_FILE.exists() and not force:
        print(f"{_OUT_FILE.name} exists; use --force to rebuild.")
        return pd.read_parquet(_OUT_FILE)
    if not _GRID_GLOBAL_FILE.exists():
        raise FileNotFoundError(f"{_GRID_GLOBAL_FILE} missing -- run `python -m src.build._make_grid` first.")
    if not _CLIP_TIF.exists():
        raise FileNotFoundError(f"{_CLIP_TIF} missing -- run `python -m src.build.util.clip_agtile` first.")

    grid = gpd.read_parquet(_GRID_GLOBAL_FILE)
    with rasterio.open(_CLIP_TIF) as s:
        # AgTile's CRS (Albers ESRI:102003) differs from grid_global's (NAD83/Conus Albers 5070), so
        # -- unlike _make_crops, whose clip shares the grid CRS -- we must rasterize in the CLIP CRS.
        # Reproject the grid up front so both the window bounds and the cell geometries live there.
        gproj = grid.to_crs(s.crs)
        # grid_global carries a few degenerate Voronoi edge cells whose native coords are ~1e8 m out;
        # they reproject to NON-FINITE geometry that would blanket the whole raster and starve every
        # real cell (crops avoids this only because it never reprojects). Drop them before rasterizing.
        finite = np.isfinite(gproj.geometry.bounds.to_numpy()).all(axis=1)
        gproj = gproj[finite].reset_index(drop=True)
        gids = gproj["global_node_id"].to_numpy()
        n = len(gproj)
        band, transform, _ = _read_clip_window(s, tuple(gproj.total_bounds))
        if band is None:
            raise ValueError("grid_global does not overlap the AgTile clip.")

    node_raster = rasterize(
        ((g, int(p) + 1) for p, g in enumerate(gproj.geometry)),  # cell-position+1; 0 = outside any cell
        out_shape=band.shape, transform=transform, fill=0, dtype=np.int32,
    )

    # tile = pixels == 1 (tile-drained); covered = every pixel the cell owns (no nodata mask exists on
    # AgTile, so "covered" is just cell membership). Count per cell in row blocks to cap memory.
    tile = np.zeros(n, dtype=np.int64)
    covered = np.zeros(n, dtype=np.int64)
    for r0 in range(0, band.shape[0], _BINCOUNT_BLOCK):
        nr = node_raster[r0:r0 + _BINCOUNT_BLOCK]
        bb = band[r0:r0 + _BINCOUNT_BLOCK]
        cell = nr > 0
        t = cell & (bb == 1)
        if cell.any():
            covered += np.bincount((nr[cell] - 1).astype(np.int64), minlength=n)
        if t.any():
            tile += np.bincount((nr[t] - 1).astype(np.int64), minlength=n)

    keep = covered > 0  # cells the raster covers; tile_cells may legitimately be 0 (covered, untiled)
    out = pd.DataFrame({"global_node_id": gids[keep], "tile_cells": tile[keep], "cell_px": covered[keep]})
    _OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(_OUT_FILE, index=False)
    dropped = len(grid) - n
    print(
        f"agtile_global.parquet: {len(out):,} cells with raster coverage ({int(keep.sum())}/{n}); "
        f"dropped {dropped} degenerate edge cells before rasterizing"
    )
    return out


def main(force: bool = False) -> None:
    """Clip the national AgTile tif then aggregate onto grid_global. Skips with a warning (does not fail the pipeline) if the manually-downloaded national raster is absent."""
    from src.build.util.clip_agtile import clip_agtile

    try:
        clip_agtile(force=force)
    except FileNotFoundError as e:
        print(f"  SKIP agtile: {e}")
        return
    build_agtile_global(force=force)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    main(force=ap.parse_args().force)
    print(f"Wrote {_OUT_FILE}")
