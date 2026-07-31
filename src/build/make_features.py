"""Feature pipeline orchestrator: run the _make_*.py covariate builders in dependency order.

This is the old make_data.py with the WATER step removed -- water (the per-site nitrate target + the site contract) is now built separately by src/build/water/ (fetch_sites -> download -> filter_sites -> make_water), which must run FIRST. make_features assumes the contract processed/water/meta/site_location_metadata.csv already exists and fails fast if it doesn't.

Order (basins now run BEFORE the covariate grid so their extent can drive it):
    map_overlays(site box)        NHD flowlines for basin snapping (outlets all inside the site box)
    basins        <- water        drainage polygons (national NLDI; may reach outside the site box)
    region        <- basins       covariate_bbox = enclose(site box + all basins) -> config
    map_overlays(covariate_bbox)  widen flowlines for display + the comid_attrs COMID universe
    comid_attrs   <- map_overlays  COMID-keyed static attrs, filtered to the flowlines COMID set
    grid_global   <- region        the national gridMET cell grid over covariate_bbox
    weather_global<- grid_global   gridMET (+ IEM precip over Iowa), keyed by global_node_id
    crops_global  <- grid_global   rasterize CDL onto grid_global
    agtile_global <- grid_global   tile-drainage raster (skips if source tif absent)
    surplus_global<- grid_global   Iowa N-surplus onto grid_global (NaN outside Iowa)
    site_grids    <- basins+grid   per-site cells + dist_to_sensor (the D8 walk, done once)
    aux           <- basins        basin containment graph (LOFO family splitter)

If basins reach outside the previously-built covariate_bbox, the covariate layers are rebuilt (the box grew -> the grid/weather/crops must cover it).

⚠ FULL rebuild path, HEAVY + NETWORK. Each builder is independently runnable (python -m src.build._make_<x>) and skips work already done unless force=True.

Usage
-----
    python -m src.build.make_features           # run all steps, skip existing
    python -m src.build.make_features --force   # rebuild everything
"""

import argparse
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent  # src/build
_SRC = _THIS_DIR.parent  # src
sys.path.insert(0, str(_SRC.parent))  # repo root on path

from src.build import (
    _make_agtile,
    _make_aux,
    _make_basins,
    _make_comid_attrs,
    _make_crops,
    _make_grid,
    _make_map_overlays,
    _make_region,
    _make_site_grids,
    _make_surplus,
    _make_weather,
)
from src.build.config import get_covariate_bbox, get_region_bbox

_CONTRACT = _SRC / "data" / "processed" / "water" / "meta" / "site_location_metadata.csv"


def _require_water_contract() -> None:
    """Fail fast if the water pipeline hasn't produced the site contract yet."""
    if not _CONTRACT.exists():
        raise SystemExit(
            f"make_features requires the water contract at {_CONTRACT}.\n"
            "Run the water pipeline first: src/build/water/{fetch_sites,download,filter_sites,make_water}.py"
        )


def main(force: bool = False, api_keys=None) -> None:
    _require_water_contract()

    # 1. Flowlines over the FIXED site box -- enough to snap every sensor outlet. Skips if a layer
    #    already exists (a prior run's wider layer covers the site box fine).
    print("── map_overlays (site box, for basin snapping) ──")
    _make_map_overlays.main(force=force, bbox=get_region_bbox())

    # 2. Basins (needs site coords + the snapping flowlines). National NLDI -> may reach outside box.
    print("\n── basins ──")
    _make_basins.main(api_keys=api_keys, force=force)

    # 3. Derive covariate_bbox = enclose(site box + all basins); write it to config.
    print("\n── region (covariate_bbox) ──")
    grew = _make_region.make_region()
    wide_force = force or grew  # a grown box means every covariate layer must be rebuilt to cover it

    # 4. Widen flowlines to covariate_bbox (display + COMID universe), then the COMID attributes.
    print("\n── map_overlays (covariate_bbox, widen for display + comid) ──")
    _make_map_overlays.main(force=wide_force, bbox=get_covariate_bbox())
    print("\n── comid_attrs (COMID-keyed static basin attributes) ──")
    _make_comid_attrs.main(force=wide_force)

    # 5. Covariate grid + fields over covariate_bbox.
    print("\n── grid_global (gridMET grid over covariate_bbox) ──")
    _make_grid.main(force=wide_force)
    print("\n── weather_global ──")
    _make_weather.main(force=wide_force)
    print("\n── crops_global ──")
    _make_crops.main(force=wide_force)
    print("\n── agtile_global (tile drainage raster; skips if source absent) ──")
    _make_agtile.main(force=wide_force)
    print("\n── surplus_global (NaN outside of bbox) ──")
    _make_surplus.main(force=wide_force)

    # 6. Per-site grids (basins x grid_global). Must precede aux: aux's get_basin_area calls
    #    get_grid for every parent site, so it reads these instead of recomputing the D8 walk.
    print("\n── site grids (per-site cells + dist_to_sensor) ──")
    _make_site_grids.main(force=wide_force)

    # 7. Basin containment graph (depends on basins, not on the covariate box).
    print("\n── aux (basin containment graph) ──")
    _make_aux.main(force=force)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Rebuild everything.")
    args = parser.parse_args()
    main(force=args.force)
