"""Build the basin-containment graph (child -> parent nesting edges).

Faithful port of data/aux/make_aux.py. Adds a directed edge A -> B when site A's sensor location falls inside site B's preferred basin (A is nested in B). Persisted as an edge list with each edge's parent_area, so the full relation can be reduced to an immediate-parent forest later.

Output: src/data/processed/aux/basin_containment_graph.parquet (child, parent, parent_area). Consumed at runtime by access.get_basin_graph (used by src/splits).
"""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import Geod
from shapely.geometry import Point

_THIS_DIR = Path(__file__).resolve().parent           # src/build
_SRC = _THIS_DIR.parent                               # src
sys.path.insert(0, str(_SRC.parent))                  # repo root on path

from src.data.access import get_basin, get_basin_area, get_location, get_site_ids

_AUX_DATA_DIR = _SRC / "data" / "processed" / "aux"
_GRAPH_FILE = _AUX_DATA_DIR / "basin_containment_graph.parquet"
_GEOD = Geod(ellps="WGS84")


def build_basin_graph() -> dict:
    """graph[A] = sorted sites B whose (preferred) basin contains A's sensor (A nested in B).

    Sensor coords and basin polygons are both WGS84, so point-in-polygon is a single vectorized spatial join. Self-edges dropped; a sensor in no other basin maps to an empty list.
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


def _euclid_m(a: str, b: str) -> float:
    """Geodesic (WGS84 ellipsoid) sensor-to-sensor distance in metres. Same Geod as d8 uses for its per-step segment lengths, so euc_dist_m and flow_dist_m are on a comparable footing."""
    try:
        lon_a, lat_a = get_location(a)
        lon_b, lat_b = get_location(b)
    except (IndexError, KeyError):
        return float("nan")
    return float(_GEOD.inv(lon_a, lat_a, lon_b, lat_b)[2])


def _flow_dists_for_parent(parent: str, children: list[str], parent_area_m2: float | None = None) -> dict[str, float]:
    """Along-network distance (m) from each child's sensor down to `parent`'s outlet.

    One D8 flow field per PARENT, reused across its children -- the BFS is the expensive step and d8 caches it per rounded (lat, lon), so edges are grouped by parent to pay it once. Returns NaN where the parent is outside the raster extent or the child's pixel is unreachable (the latter means the D8 network disagrees with the point-in-polygon containment, which is worth knowing rather than silently filling).

    `parent_area_m2` constrains pour-point snapping so the parent's outlet cannot land on a larger neighbouring river -- see d8._snap_outlet. It must be passed in already materialised, never looked up here: get_basin_area reads the site's grid, and building that grid calls back into this same flow-field function.
    """
    from src.data import d8

    try:
        lon_p, lat_p = get_location(parent)
        field = d8.flow_distance_field_ll(lat_p, lon_p, parent_area_m2)
    except (ValueError, IndexError, KeyError):
        return {c: float("nan") for c in children}

    out = {}
    for c in children:
        try:
            lon_c, lat_c = get_location(c)
            col, row = d8.ll_to_image_pixel(lat_c, lon_c)
            out[c] = float(d8.sample_field(field, col, row))
        except (ValueError, IndexError, KeyError):
            out[c] = float("nan")
    return out


def _daily_nitrate_series() -> dict:
    """{site_uid -> daily nitrate Series}. processed/water is already daily-max, so this is a read."""
    from src.data.access import get_water

    out = {}
    for s in get_site_ids():
        try:
            w = get_water(s)
        except (FileNotFoundError, KeyError):
            continue
        if "nitrate_con" in w.columns:
            ser = w["nitrate_con"].dropna()
            if len(ser):
                out[s] = ser
    return out


def _statewide_daily_mean(series: dict) -> pd.Series:
    """Cross-site daily mean nitrate -- the common signal `corr_resid` removes.

    Every Iowa site rides the same spring-flush cycle, so RAW correlation between any two sites is dominated by shared seasonality that does not decay with separation. Subtracting this mean leaves each site's ANOMALY, and it is the anomaly co-movement that actually indicates a leakage risk. Measured effect: residual rho vs distance is about -0.42 where raw rho manages only -0.19 (see notes/future-cv-work-report.md)."""
    if not series:
        return pd.Series(dtype=float)
    return pd.concat(series.values(), axis=1).mean(axis=1, skipna=True)


def _corr(series: dict, a: str, b: str, state_mean: pd.Series | None = None) -> tuple[float, int]:
    """(Spearman correlation, n overlapping days) of two sites' daily nitrate.

    With `state_mean`, both series are residualized against it first -- that is the `corr_resid` variant. Note the residual is computed on each site's OWN dates before the inner join, so a site's anomaly does not depend on which partner it is being compared to.

    SPEARMAN, not Pearson: it is what experiments/pre-expansion-network-test used to establish the correlation-vs-distance decay curve, so a threshold calibrated there transfers directly. Rank correlation is also the robust choice against nitrate's skew and spikes. `n_overlap_days` is stored alongside because a correlation over a handful of shared days is noise -- without it, a spurious high `corr` could relax a genuine conflict."""
    sa, sb = series.get(a), series.get(b)
    if sa is None or sb is None:
        return float("nan"), 0
    if state_mean is not None and len(state_mean):
        sa = (sa - state_mean.reindex(sa.index)).dropna()
        sb = (sb - state_mean.reindex(sb.index)).dropna()
    j = pd.concat([sa.rename("a"), sb.rename("b")], axis=1, join="inner").dropna()
    if len(j) < 2:
        return float("nan"), len(j)
    return float(j["a"].corr(j["b"], method="spearman")), len(j)


def save_basin_graph() -> pd.DataFrame:
    """Flatten build_basin_graph() to one row per containment edge, with its metrics, and write it.

    Columns: child, parent, parent_area, euc_dist_m, flow_dist_m, corr, corr_resid, n_overlap_days.

    These metrics turn a purely topological relation into a weighted one: an edge no longer just says "A nests in B", it says how far apart and how strongly co-varying they are. That is what a distance- or correlation-relaxed ("soft") conflict graph needs -- see notes/future-cv-work-report.md.

    Prefer `corr_resid` over `corr` for that purpose: raw correlation is dominated by the shared spring-flush seasonality every Iowa site rides, which does not decay with separation."""
    graph = build_basin_graph()
    parents = {p for ps in graph.values() for p in ps}
    area = {s: get_basin_area(s) for s in parents}

    # invert to parent -> children so each parent's D8 flow field is built exactly once
    by_parent: dict[str, list[str]] = {}
    for child, ps in graph.items():
        for p in ps:
            by_parent.setdefault(p, []).append(child)

    print(f"computing flow distances over {len(by_parent)} parent basins (one D8 field each)...")
    flow: dict[tuple[str, str], float] = {}
    for i, (p, kids) in enumerate(sorted(by_parent.items()), 1):
        # area[p], NOT area.get(p): a miss must raise here. Passing None falls through to _snap_outlet's unconstrained window-relative branch -- the exact rule that mis-snapped 28 of 116 sites -- so a lookup hole would silently reinstate the bug instead of failing. `area` is built from `parents` two lines up, so a miss is a refactor error, and the next loop already reads area[parent] directly.
        for c, d in _flow_dists_for_parent(p, kids, area[p]).items():
            flow[(c, p)] = d
        if i % 10 == 0 or i == len(by_parent):
            print(f"  {i}/{len(by_parent)} parents")

    series = _daily_nitrate_series()
    state_mean = _statewide_daily_mean(series)
    rows = []
    for child, ps in graph.items():
        for parent in ps:
            c, n = _corr(series, child, parent)
            cr, _ = _corr(series, child, parent, state_mean=state_mean)
            rows.append(
                {
                    "child": child,
                    "parent": parent,
                    "parent_area": area[parent],
                    "euc_dist_m": _euclid_m(child, parent),
                    "flow_dist_m": flow.get((child, parent), float("nan")),
                    "corr": c,
                    "corr_resid": cr,
                    "n_overlap_days": n,
                }
            )
    cols = ["child", "parent", "parent_area", "euc_dist_m", "flow_dist_m", "corr", "corr_resid", "n_overlap_days"]
    edges = pd.DataFrame(rows, columns=cols)
    _AUX_DATA_DIR.mkdir(parents=True, exist_ok=True)
    edges.to_parquet(_GRAPH_FILE, index=False)

    n = len(edges)
    print(f"wrote {n} containment edges -> {_GRAPH_FILE}")
    print(f"  euc_dist_m  : {int(edges['euc_dist_m'].notna().sum())}/{n} resolved")
    print(f"  flow_dist_m : {int(edges['flow_dist_m'].notna().sum())}/{n} resolved  "
          f"(NaN = parent off-raster or child unreachable)")
    print(f"  corr        : {int(edges['corr'].notna().sum())}/{n} resolved  "
          f"(median overlap {edges['n_overlap_days'].median():.0f} d)")
    print(f"  corr_resid  : {int(edges['corr_resid'].notna().sum())}/{n} resolved  "
          f"(statewide daily mean removed; prefer this for thresholding)")
    return edges


def main(api_keys=None, force: bool = False) -> None:
    save_basin_graph()


if __name__ == "__main__":
    main()
