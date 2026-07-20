"""Build crops_global.parquet: CDL pixel counts per (global_node_id, year), aggregated ONCE
over grid_global.

Phase 1 of the re-grain refactor. The old builder (data/crops/make_crops.py) rasterized each
site's cells over the site's own CDL window and wrote one {uid}_crops_grid.parquet per site --
recomputing the same cell's counts once per containing basin. Here we rasterize the whole
grid_global tessellation over the full CDL extent, once per year, keyed by global_node_id. A
site's crops are then just crops_global.merge(site_view, on="global_node_id").

Because a pixel is assigned to the Voronoi cell whose polygon contains its centre, and the
global Voronoi cells are identical to the old per-basin cells (Phase 0 verified cell_area to
machine precision), the per-cell counts match the old per-site outputs (parity-checked).

Output (src/data/interim/crops_global.parquet), one row per (global_node_id, year):
    global_node_id  int64
    year            int64
    <one integer pixel-count column per class from cdl_to_class>

Source: cdl_clip_{year}.tif under src/data/raw/crops/clipped/ (falls back to the legacy
data/crops/crops_raw/clipped/ during migration). Build clips with clip_crops.py first.

Usage
-----
    python -m src.build._make_crops                 # all years with a clip
    python -m src.build._make_crops --force
    python -m src.build._make_crops --year 2017     # one year (repeatable)
"""

import argparse
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds, intersection

_THIS_DIR = Path(__file__).resolve().parent           # src/build/
_SRC = _THIS_DIR.parent                               # src/
sys.path.insert(0, str(_SRC.parent))                  # repo root on path

from src.data.cdl_legend import cdl_to_class
from src.build.config import get_config

_INTERIM_DIR = _SRC / "data" / "interim"
_GRID_GLOBAL_FILE = _INTERIM_DIR / "grid_global.parquet"
_CROPS_GLOBAL_FILE = _INTERIM_DIR / "crops_global.parquet"

# CDL clips: cdl_clip_{year}.tif under src/data/raw/crops/clipped/ (built by clip_crops.py).
_CLIP_DIR = _SRC / "data" / "raw" / "crops" / "clipped"

_crops_cfg = get_config()["crops"]
YEARS = list(range(_crops_cfg["year_start"], _crops_cfg["year_end"] + 1))

_BINCOUNT_BLOCK = 2048  # raster rows per bincount block (caps transient memory on the 415M-px extent)


def _clip_path(year: int) -> Path:
    return _CLIP_DIR / f"cdl_clip_{year}.tif"


def _build_luts(remap) -> tuple[list[str], np.ndarray]:
    """From a code->class remap, build the sorted class list and a 256-entry code->class-index
    lookup (for vectorized counting)."""
    labels = [remap(c) for c in range(256)]
    classes = sorted(set(labels))
    idx = {c: i for i, c in enumerate(classes)}
    code_to_classidx = np.array([idx[labels[c]] for c in range(256)], dtype=np.int32)
    return classes, code_to_classidx


def _read_window(clip_path: Path, bounds):
    """Read the CDL band over `bounds` (in the clip CRS). Returns (band, transform, crs) or
    (None, None, None) if the bounds fall outside the clip."""
    minx, miny, maxx, maxy = bounds
    with rasterio.open(clip_path) as s:
        window = from_bounds(minx, miny, maxx, maxy, s.transform).round_offsets().round_lengths()
        window = intersection(window, Window(0, 0, s.width, s.height))
        if window.width <= 0 or window.height <= 0:
            return None, None, None
        return s.read(1, window=window), s.window_transform(window), s.crs


def _blocked_counts(node_raster: np.ndarray, band: np.ndarray, code_to_classidx: np.ndarray,
                    n_nodes: int, n_classes: int) -> np.ndarray:
    """Per-(cell, class) pixel counts, accumulated over row blocks to cap transient memory.

    node_raster holds cell-row-position+1 (0 = outside any cell); band holds CDL codes
    (0 = background). Returns an (n_nodes, n_classes) int64 count array.
    """
    flat = np.zeros(n_nodes * n_classes, dtype=np.int64)
    H = node_raster.shape[0]
    for r0 in range(0, H, _BINCOUNT_BLOCK):
        nr = node_raster[r0:r0 + _BINCOUNT_BLOCK]
        bb = band[r0:r0 + _BINCOUNT_BLOCK]
        m = (nr > 0) & (bb != 0)
        if not m.any():
            continue
        pos = (nr[m] - 1).astype(np.int64)
        cls = code_to_classidx[bb[m]]
        flat += np.bincount(pos * n_classes + cls, minlength=n_nodes * n_classes)
    return flat.reshape(n_nodes, n_classes)


def aggregate_crops_global(grid, years, classes, code_to_classidx) -> pd.DataFrame | None:
    """Pixel counts per (global_node_id, year), one column per class, over grid_global.

    Rasterizes the grid cells once per unique clip transform (two exist -- 30 m and 56 m CDL
    eras) and reuses the burned raster for every year sharing it.
    """
    n_nodes, n_classes = len(grid), len(classes)
    gids = grid["global_node_id"].to_numpy()
    positions = np.arange(n_nodes)
    bounds = grid.total_bounds

    raster_cache: dict[tuple, np.ndarray] = {}  # transform key -> node_raster (kept small)
    frames = []
    for year in years:
        clip = _clip_path(year)
        if not clip.exists():
            continue
        band, transform, crs = _read_window(clip, bounds)
        if band is None:
            continue

        key = (str(crs), tuple(np.round(np.asarray(transform)[:6], 3)), band.shape)
        node_raster = raster_cache.get(key)
        if node_raster is None:
            geoms = grid.geometry if (crs is None or crs == grid.crs) else grid.to_crs(crs).geometry
            node_raster = rasterize(
                ((geom, int(pos) + 1) for geom, pos in zip(geoms, positions)),
                out_shape=band.shape,
                transform=transform,
                fill=0,
                dtype=np.int32,
            )
            raster_cache.clear()  # years are sorted -> same transform is contiguous; keep 1 raster
            raster_cache[key] = node_raster

        counts = _blocked_counts(node_raster, band, code_to_classidx, n_nodes, n_classes)
        nz = counts.sum(axis=1) > 0
        if not nz.any():
            continue
        df = pd.DataFrame(counts[nz], columns=classes)
        df.insert(0, "year", year)
        df.insert(0, "global_node_id", gids[nz])
        frames.append(df)
        print(f"  {year}: {int(nz.sum()):,} cells")

    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def build_crops_global(force: bool = False, years=None, remap=cdl_to_class) -> pd.DataFrame:
    """Build (or load) crops_global.parquet over grid_global."""
    if _CROPS_GLOBAL_FILE.exists() and not force:
        print(f"{_CROPS_GLOBAL_FILE.name} exists; use --force to rebuild.")
        return pd.read_parquet(_CROPS_GLOBAL_FILE)

    if not _GRID_GLOBAL_FILE.exists():
        raise FileNotFoundError(f"{_GRID_GLOBAL_FILE} missing — run `python -m src.build._make_grid` first.")

    grid = gpd.read_parquet(_GRID_GLOBAL_FILE)
    classes, code_to_classidx = _build_luts(remap)
    yrs = list(years) if years is not None else list(YEARS)
    print(f"Aggregating crops over grid_global ({len(grid):,} cells), {len(yrs)} year(s); classes: {classes}")

    t0 = time.perf_counter()
    agg = aggregate_crops_global(grid, yrs, classes, code_to_classidx)
    if agg is None:
        raise RuntimeError("No crop data produced for any requested year.")

    _INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    agg.to_parquet(_CROPS_GLOBAL_FILE, index=False)
    print(
        f"crops_global: {len(agg):,} rows, {agg['global_node_id'].nunique()} cells, "
        f"{agg['year'].nunique()} years  ({time.perf_counter() - t0:.0f}s) -> "
        f"{_CROPS_GLOBAL_FILE.relative_to(_SRC.parent)}"
    )
    return agg


def main(force: bool = False, years=None) -> None:
    build_crops_global(force=force, years=years)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Rebuild crops_global.parquet.")
    parser.add_argument("--year", action="append", type=int, help="Limit to specific year(s) (repeatable).")
    args = parser.parse_args()
    main(force=args.force, years=args.year)
