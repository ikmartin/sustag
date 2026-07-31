"""Persist each site's grid (the basin's cells over grid_global) as a build artifact.

A site's grid is a pure function of three already-built inputs -- the site's preferred basin polygon, the sensor location, and grid_global -- yet it was rebuilt from scratch in every process that touched a site. The expensive column is dist_to_sensor, which needs a breadth-first walk UP the D8 drainage network from the sensor's outlet (seconds for a large basin), and on a cold process is preceded by a ~38 s flow-accumulation pass over the whole raster. None of that changes between training runs, so it belongs here.

Output: src/data/processed/grids/{uid}_grid.parquet, in the exact schema access.get_grid returns:
    node_id, global_node_id, x, y, lat, lon, cell_area, dist_to_sensor, frac_cell_in_basin, geometry

Read back by access.get_grid, which falls back to computing live when a file is missing or stale -- so this step is an optimization, never a prerequisite. Virtual/ungauged sites (deploy/build_virtual_basin.py) delineate a basin from an arbitrary pin and can never be precomputed; they keep calling site_view.build_site_view directly and are unaffected.

Staleness is by mtime against both inputs: the site's basin parquet (the widget can rewrite basin4 in place under an unchanged name) and grid_global.parquet (a grown covariate_bbox renumbers global_node_id, invalidating every persisted grid at once).
"""

import argparse
import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent           # src/build
_SRC = _THIS_DIR.parent                               # src
sys.path.insert(0, str(_SRC.parent))                  # repo root on path

from src.data.access import (
    build_grid_live,
    get_basin_metadata,
    get_site_ids,
    grid_build_env,
    grid_env_path,
    site_grid_is_current,
    site_grid_path,
)

_GRID_DIR = _SRC / "data" / "processed" / "grids"


def _sites() -> list[str]:
    """The runtime cohort intersected with the sites that have a preferred basin -- exactly the set get_grid can be asked about.

    Both halves are needed and neither alone is the cohort. preferred_basin.csv lists every site the basin builder ever delineated, including the ones filter_sites later dropped from the water contract; those have no row in site_location_metadata.csv, so get_location -- and therefore build_grid_live -- cannot resolve a sensor for them. Building them is a guaranteed failure, and a single failure suppresses the _build_env.json stamp, which leaves the whole cache 'not current' and every site computing its grid live.
    """
    with_basin = set(get_basin_metadata()["site_uid"])
    return [s for s in get_site_ids() if s in with_basin]


def build_one(site_uid: str) -> int:
    """Compute and write one site's grid. Returns the cell count (0 = nothing written)."""
    grid = build_grid_live(site_uid)
    if grid.empty:
        # No grid_global cell intersects the basin. An empty frame round-trips with object dtypes instead of the real float64/int64 schema, so persist nothing and let get_grid rebuild live.
        return 0
    _GRID_DIR.mkdir(parents=True, exist_ok=True)
    grid.to_parquet(site_grid_path(site_uid), index=False)
    return len(grid)


def main(force: bool = False, site_uids: list[str] | None = None) -> None:
    """Write a per-site grid for every site whose cached copy is missing or older than its inputs."""
    sites = list(site_uids) if site_uids else _sites()
    if not site_uids:
        # A cohort site with no preferred basin is not fatal -- get_grid falls back to live, and live raises on get_basin -- but it is a hole in the basins build that the stamp would otherwise cover over.
        no_basin = [s for s in get_site_ids() if s not in set(sites)]
        if no_basin:
            print(f"    [warn] {len(no_basin)} cohort site(s) have no preferred basin, skipped: {', '.join(no_basin)}")
    todo = sites if force else [s for s in sites if not site_grid_is_current(s)]
    fresh = len(sites) - len(todo)
    if not todo:
        print(f"  site grids: all {len(sites)} current; use --force to rebuild.")
        return

    print(f"  site grids: {len(todo)} to build, {fresh} already current.")
    built = cells = 0
    failed, empty = [], []
    for i, uid in enumerate(todo, 1):
        print(f"\r  building grid {i}/{len(todo)} ({uid})" + " " * 20, end="", flush=True)
        try:
            n = build_one(uid)
        except Exception as e:
            failed.append((uid, f"{type(e).__name__}: {e}"))
            continue
        if n == 0:
            empty.append(uid)
            continue
        built += 1
        cells += n
    print(f"\r  site grids: wrote {built}/{len(todo)} ({cells:,} cells)" + " " * 20, flush=True)

    # Stamp the code + geo stack that produced these. site_grid_is_current compares it and rebuilds on a mismatch, which is the only thing that catches a change mtimes cannot see -- a PROJ pipeline shift moves the reprojected basin outline sub-metre and silently rewrites every boundary cell's frac_cell_in_basin. Written only after a successful pass, and NOT written when some sites failed, so a partial cache is never stamped as complete.
    if failed:
        print(f"    [warn] {len(failed)} site(s) failed -- not stamping {grid_env_path().name}; the cache stays 'not current'.")
    else:
        _GRID_DIR.mkdir(parents=True, exist_ok=True)
        grid_env_path().write_text(json.dumps(grid_build_env(), indent=2))
        print(f"    stamped {grid_env_path().name}")

    for uid in empty:
        print(f"    [skip] {uid}: no grid_global cell intersects the basin")
    for uid, err in failed:
        print(f"    [fail] {uid}: {err}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="rebuild every site, ignoring mtimes")
    ap.add_argument("--site", action="append", dest="sites", help="build only this site (repeatable)")
    a = ap.parse_args()
    main(force=a.force, site_uids=a.sites)
