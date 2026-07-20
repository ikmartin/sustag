"""Build surplus_global.parquet: area-weighted nitrogen surplus per (global_node_id, year),
aggregated ONCE over grid_global.

Phase 2 of the re-grain refactor. The old builder (data/surplus/make_surplus.py) overlaid the
250 m surplus squares with each site's Voronoi cells and wrote one {uid}_surplus_grid.parquet
per site. Here we overlay against the whole grid_global tessellation once, keyed by
global_node_id; a site's surplus is then surplus_global.merge(site_view, on="global_node_id").

Each 250 m surplus cell (a square) is area-weighted by its overlap with each Voronoi cell:
    total_kg_N   = Σ area(square ∩ cell) · surplus_kgha / 1e4     (kg, the cell's N sum)
    surplus_kgha = total_kg_N / cell_area_ha                       (intensive mean over the cell)
Because grid_global's cells are identical to the old per-basin cells (Phase 0 verified cell_area
to machine precision) and a cell's overlapping squares are the same, this reproduces the old
per-site values up to float summation order (parity-checked, float-tolerant).

Output (src/data/interim/surplus_global.parquet), one row per (global_node_id, year):
    global_node_id  int64
    year            int64/int16
    surplus_kgha    float64
    total_kg_N      float64

Source (src/data/raw/surplus/, falls back to legacy data/surplus/surplus_source/):
    iowa_grid_lookup.parquet   pixel_id -> x, y (EPSG:5070), lon, lat
    surplus[1..N].parquet      pixel_id, year, surplus_kgha, total_kg_N  (one year per chunk)

COST: the statewide overlay is ~6M 250 m squares × ~23k cells. This is the heavy build in the
pipeline (minutes, several GB). If memory is tight it can be tiled over sub-regions of
grid_global (each tile is exact and independent); not done here to keep the port faithful.

Usage
-----
    python -m src.build._make_surplus                # all years
    python -m src.build._make_surplus --force
    python -m src.build._make_surplus --year 2017    # one year (repeatable)
"""

import argparse
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

_THIS_DIR = Path(__file__).resolve().parent           # src/build/
_SRC = _THIS_DIR.parent                               # src/
sys.path.insert(0, str(_SRC.parent))                  # repo root on path

from src.build.config import get_config

_INTERIM_DIR = _SRC / "data" / "interim"
_GRID_GLOBAL_FILE = _INTERIM_DIR / "grid_global.parquet"
_SURPLUS_GLOBAL_FILE = _INTERIM_DIR / "surplus_global.parquet"

# Source: iowa_grid_lookup.parquet + surplus[1..N].parquet under src/data/raw/surplus/.
_SOURCE_DIR = _SRC / "data" / "raw" / "surplus"

_SURPLUS_M = 250  # surplus pixel size (EPSG:5070 metres)

_surplus_cfg = get_config()["surplus"]
YEARS = list(range(_surplus_cfg["year_start"], _surplus_cfg["year_end"] + 1))


def load_surplus_source(bbox=None, years=None) -> pd.DataFrame:
    """Load surplus pixels filtered to a bbox and/or years (no 100M-row full merge).

    Columns: pixel_id, year, surplus_kgha, total_kg_N, x, y, lon, lat. The chunks are
    one-year-per-file, so a `years` filter reads only the matching chunk(s) via row-group
    pushdown; `bbox` (EPSG:5070, e.g. a grid's .total_bounds) keeps only pixels near that extent.
    """
    src = _SOURCE_DIR
    lookup = pd.read_parquet(src / "iowa_grid_lookup.parquet")
    pid_filter = None
    if bbox is not None:
        minx, miny, maxx, maxy = bbox
        h = _SURPLUS_M / 2  # keep edge pixels whose 250 m square can still overlap the bbox
        lookup = lookup[lookup["x"].between(minx - h, maxx + h) & lookup["y"].between(miny - h, maxy + h)]
        pid_filter = set(lookup["pixel_id"].to_numpy().tolist())

    yrs = list(years) if years is not None else None
    frames = []
    for f in sorted(src.glob("surplus[0-9]*.parquet")):
        df = pd.read_parquet(f, filters=[("year", "in", yrs)] if yrs is not None else None)
        if df.empty:
            continue
        if pid_filter is not None:
            df = df[df["pixel_id"].isin(pid_filter)]
        if not df.empty:
            frames.append(df)

    cols = ["pixel_id", "year", "surplus_kgha", "total_kg_N"]
    surplus = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    return surplus.merge(lookup, on="pixel_id")


def aggregate_surplus_global(source: pd.DataFrame, grid: gpd.GeoDataFrame) -> pd.DataFrame | None:
    """Area-weighted surplus per (global_node_id, year) over grid_global (see module docstring)."""
    minx, miny, maxx, maxy = grid.total_bounds
    h = _SURPLUS_M / 2
    m = source[source["x"].between(minx - h, maxx + h) & source["y"].between(miny - h, maxy + h)]
    if m.empty:
        return None

    # 250 m square per unique surplus pixel (geometry is static across years).
    upx = m.drop_duplicates("pixel_id")[["pixel_id", "x", "y"]].reset_index(drop=True)
    squares = gpd.GeoDataFrame(
        {"pixel_id": upx["pixel_id"]},
        geometry=[shapely.box(x - h, y - h, x + h, y + h) for x, y in zip(upx["x"], upx["y"])],
        crs=grid.crs,
    )

    inter = gpd.overlay(squares, grid[["global_node_id", "geometry"]], how="intersection")
    if inter.empty:
        return None
    inter["area_DR"] = inter.geometry.area

    j = inter.merge(m[["pixel_id", "year", "surplus_kgha"]], on="pixel_id")
    j["w_density"] = j["area_DR"] * j["surplus_kgha"]  # area-weighted surplus per overlap
    g = (
        j.groupby(["global_node_id", "year"])
        .agg(w_density=("w_density", "sum"))
        .reset_index()
        .merge(grid[["global_node_id", "cell_area"]], on="global_node_id")
    )
    g["total_kg_N"] = g["w_density"] / 1e4  # Σ area·density (m²·kg/ha) -> kg
    g["surplus_kgha"] = g["w_density"] / g["cell_area"]  # mean over the (full) cell
    return g[["global_node_id", "year", "surplus_kgha", "total_kg_N"]]


def build_surplus_global(force: bool = False, years=None) -> pd.DataFrame:
    """Build (or load) surplus_global.parquet over grid_global."""
    if _SURPLUS_GLOBAL_FILE.exists() and not force:
        print(f"{_SURPLUS_GLOBAL_FILE.name} exists; use --force to rebuild.")
        return pd.read_parquet(_SURPLUS_GLOBAL_FILE)

    if not _GRID_GLOBAL_FILE.exists():
        raise FileNotFoundError(f"{_GRID_GLOBAL_FILE} missing — run `python -m src.build._make_grid` first.")

    grid = gpd.read_parquet(_GRID_GLOBAL_FILE)
    yrs = list(years) if years is not None else list(YEARS)
    print(f"Loading surplus source over grid_global extent, {len(yrs)} year(s)...")
    source = load_surplus_source(bbox=tuple(grid.total_bounds), years=yrs)
    print(f"  {len(source):,} pixel-year rows, {source['pixel_id'].nunique():,} unique pixels")

    t0 = time.perf_counter()
    print(f"Overlaying ~{source['pixel_id'].nunique():,} squares × {len(grid):,} cells (this is the slow step)...")
    agg = aggregate_surplus_global(source, grid)
    if agg is None:
        raise RuntimeError("No surplus overlaps grid_global for the requested years.")

    _INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    agg.to_parquet(_SURPLUS_GLOBAL_FILE, index=False)
    print(
        f"surplus_global: {len(agg):,} rows, {agg['global_node_id'].nunique()} cells, "
        f"{agg['year'].nunique()} years  ({time.perf_counter() - t0:.0f}s) -> "
        f"{_SURPLUS_GLOBAL_FILE.relative_to(_SRC.parent)}"
    )
    return agg


def main(force: bool = False, years=None) -> None:
    # build_surplus_global rebuilds iff force or the output is missing; capture that BEFORE the call
    # so we only regenerate the widget colour-scale stats when we actually read the raw chunks (a
    # committed-file short-circuit has no raw chunks, so re-deriving stats there would fail).
    rebuilt = force or not _SURPLUS_GLOBAL_FILE.exists()
    build_surplus_global(force=force, years=years)
    if rebuilt:
        from src.build.util.gen_surplus_statistics import gen_surplus_statistics

        gen_surplus_statistics()  # -> processed/surplus/meta/surplus_stats.csv (surplus_viz colour scale)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Rebuild surplus_global.parquet.")
    parser.add_argument("--year", action="append", type=int, help="Limit to specific year(s) (repeatable).")
    args = parser.parse_args()
    main(force=args.force, years=args.year)
