"""Build the basin-containment graph (child -> parent nesting edges).

Faithful port of data/aux/make_aux.py. Adds a directed edge A -> B when site A's sensor location
falls inside site B's preferred basin (A is nested in B). Persisted as an edge list with each
edge's parent_area, so the full relation can be reduced to an immediate-parent forest later.

Output: src/data/processed/aux/basin_containment_graph.parquet (child, parent, parent_area).
Consumed at runtime by access.get_basin_graph (used by src/splits).
"""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

_THIS_DIR = Path(__file__).resolve().parent           # src/build
_SRC = _THIS_DIR.parent                               # src
sys.path.insert(0, str(_SRC.parent))                  # repo root on path

from src.data.access import get_basin, get_basin_area, get_location, get_site_ids

_AUX_DATA_DIR = _SRC / "data" / "processed" / "aux"
_GRAPH_FILE = _AUX_DATA_DIR / "basin_containment_graph.parquet"


def build_basin_graph() -> dict:
    """graph[A] = sorted sites B whose (preferred) basin contains A's sensor (A nested in B).

    Sensor coords and basin polygons are both WGS84, so point-in-polygon is a single vectorized
    spatial join. Self-edges dropped; a sensor in no other basin maps to an empty list.
    """
    sites = get_site_ids()
    points, basins = [], []
    for s in sites:
        try:
            lon, lat = get_location(s)
            points.append({"site_uid": s, "geometry": Point(lon, lat)})
        except (IndexError, KeyError):
            pass
        try:
            basin = get_basin(s).to_crs("EPSG:4326")
            basins.append({"site_uid": s, "geometry": basin.union_all()})
        except (FileNotFoundError, KeyError):
            pass

    pts_gdf = gpd.GeoDataFrame(points, crs="EPSG:4326")
    basin_gdf = gpd.GeoDataFrame(basins, crs="EPSG:4326")
    joined = gpd.sjoin(pts_gdf, basin_gdf, predicate="within", how="left")

    graph = {s: [] for s in sites}
    for a, b in zip(joined["site_uid_left"], joined["site_uid_right"]):
        if pd.isna(b) or a == b:
            continue
        graph[a].append(b)
    return {s: sorted(set(v)) for s, v in graph.items()}


def save_basin_graph() -> pd.DataFrame:
    """Flatten build_basin_graph() to one (child, parent, parent_area) row per edge and write it."""
    graph = build_basin_graph()
    area = {s: get_basin_area(s) for s in {p for ps in graph.values() for p in ps}}
    rows = [
        {"child": child, "parent": parent, "parent_area": area[parent]}
        for child, parents in graph.items()
        for parent in parents
    ]
    edges = pd.DataFrame(rows, columns=["child", "parent", "parent_area"])
    _AUX_DATA_DIR.mkdir(parents=True, exist_ok=True)
    edges.to_parquet(_GRAPH_FILE, index=False)
    print(f"wrote {len(edges)} containment edges -> {_GRAPH_FILE}")
    return edges


def main(api_keys=None, force: bool = False) -> None:
    save_basin_graph()


if __name__ == "__main__":
    main()
