"""Pipeline orchestrator: run the _make_*.py builders in dependency order.

Dependency order (only edges that matter):
    water                         (foundational: site list, coords, target)
    grid_global                   (independent: one Voronoi over all IEM cells)
    weather_global                (independent: IEM + gridMET, keyed by global_node_id)
    basins        <- water        (needs site coords)
    crops_global  <- grid_global  (rasterize CDL onto grid_global)
    surplus_global<- grid_global  (area-weight surplus onto grid_global)
    aux           <- basins       (basin containment graph, used by the LOFO family splitter)
    map_overlays                  (NHD flowlines/waterbodies, widget basemap overlays)

⚠ This is the FULL rebuild path and is HEAVY + NETWORK: water (IWQIS 3.1GB reassembly + USGS
API), basins (NLDI/KMZ), weather (gridMET/IEM download), plus the crops/surplus aggregations.
Each builder is independently runnable (python -m src.build._make_<x>) and skips work already
done unless force=True, so you rarely run the whole thing at once. During migration the built
data is copied in, so this exists for reproducibility rather than routine use.

Usage
-----
    python -m src.build.make_data              # run all ported steps, skip existing
    python -m src.build.make_data --force      # rebuild everything
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root on path

from src.build import (
    _make_aux,
    _make_basins,
    _make_crops,
    _make_grid,
    _make_map_overlays,
    _make_surplus,
    _make_water,
    _make_weather,
)


def main(force: bool = False, api_keys=None) -> None:
    print("── water ──")
    _make_water.main(api_keys=api_keys, force=force)

    print("\n── grid_global ──")
    _make_grid.main(force=force)

    print("\n── weather_global ──")
    _make_weather.main(force=force)

    print("\n── basins ──")
    _make_basins.main(api_keys=api_keys, force=force)

    print("\n── crops_global ──")
    _make_crops.main(force=force)

    print("\n── surplus_global ──")
    _make_surplus.main(force=force)

    print("\n── aux (basin containment graph) ──")
    _make_aux.main(force=force)

    print("\n── map_overlays (NHD flowlines/waterbodies) ──")
    _make_map_overlays.main(force=force)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Rebuild everything.")
    args = parser.parse_args()
    main(force=args.force)
