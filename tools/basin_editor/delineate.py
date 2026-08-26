"""Delineation for the basin editor: four methods, one representation, ACCESS-LAYER IMPORTS ONLY.

Every method returns the same shape -- `dict(method, status, comids | None, area_km2, vaa_km2, polygon)` -- because every basin is a weighted COMID set and the polygon is a rendering of it, not the authority. basin1 walks upstream from the snapped reach; basin2 from the catchment CONTAINING the point (the two disagree exactly near divides); basin3 is the HydroSHEDS D8 flood-fill, the one NHDPlus-independent answer; basin0 is the authority's own polygon via a LIVE NLDI call -- the one network request this tool makes, clearly labeled, a modeler utility fetching a public polygon and never the build touching the network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from data.access import api, config
from data.access.machinery import accumulate as acc_mod
from data.access.machinery import aggregate as agg_mod

_NET = None


def _network():
    global _NET
    if _NET is None:
        _NET = acc_mod.Network.load()
    return _NET


def dissolve(comids) -> "object | None":
    """One lon/lat polygon for a COMID set, via the catchment layer. None when nothing joins."""
    import geopandas as gpd
    import pyarrow.parquet as pq
    import shapely

    ids = sorted(int(c) for c in comids)
    if not ids:
        return None
    t = pq.read_table(config.ACQ_NETWORK / "catchments.parquet",
                      columns=["FEATUREID", "geometry"], filters=[("FEATUREID", "in", ids)])
    if not t.num_rows:
        return None
    geom = shapely.union_all(shapely.from_wkb(t.column("geometry").to_pylist()))
    return shapely.make_valid(geom)


def _walk(start_comid: int, method: str) -> dict:
    net = _network()
    comids, area, vaa_km2, status = net.accumulate(int(start_comid))
    poly = dissolve(comids) if comids and len(comids) <= 400_000 else None
    return dict(method=method, status=status, comids=comids,
                area_km2=None if area is None else float(area),
                vaa_km2=None if vaa_km2 is None else float(vaa_km2), polygon=poly)


def basin1(lat: float, lon: float) -> dict:
    """Upstream of the SNAPPED reach -- the default delineation."""
    s = api.snap(lat, lon)
    if pd.isna(s.get("comid")):
        return dict(method="basin1", status=str(s.get("snap_status", "no_reach")),
                    comids=None, area_km2=None, vaa_km2=None, polygon=None)
    out = _walk(int(s["comid"]), "basin1")
    out["snap"] = {k: s.get(k) for k in ("comid", "snap_dist_m", "snap_status", "snap_rule")}
    return out


def basin2(lat: float, lon: float) -> dict:
    """Upstream of the CONTAINING catchment -- differs from basin1 exactly near divides."""
    s = api.snap(lat, lon)
    c = s.get("catchment_comid")
    if pd.isna(c):
        return dict(method="basin2", status="no_containing_catchment",
                    comids=None, area_km2=None, vaa_km2=None, polygon=None)
    return _walk(int(c), "basin2")


def basin3(lat: float, lon: float, expected_km2: float | None = None) -> dict:
    """The HydroSHEDS D8 flood-fill -- NHDPlus-independent. Returns a polygon and a pixel area, no COMIDs."""
    import collections

    from rasterio.features import shapes as rio_shapes
    from shapely.geometry import shape as sh_shape
    from shapely.ops import unary_union

    from data.access.machinery import d8

    direction = d8.load_direction_array()
    col, row = d8.ll_to_image_pixel(float(lat), float(lon))
    if not (0 <= col < d8.W and 0 <= row < d8.H):
        return dict(method="basin3", status="outside_raster", comids=None,
                    area_km2=None, vaa_km2=None, polygon=None)
    try:
        col, row = d8._snap_outlet(direction, col, row,
                                   None if expected_km2 is None else expected_km2 * 1e6)
    except ValueError:
        return dict(method="basin3", status="no_compatible_outlet", comids=None,
                    area_km2=None, vaa_km2=None, polygon=None)
    mask = np.zeros((d8.H, d8.W), dtype=np.uint8)
    mask[row, col] = 1
    q = collections.deque([(col, row)])
    while q:
        cx, cy = q.popleft()
        for dx, dy, expected in d8.NEIGHBOR_CHECKS:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < d8.W and 0 <= ny < d8.H and not mask[ny, nx] and direction[ny, nx] == expected:
                mask[ny, nx] = 1
                q.append((nx, ny))
    if not mask.any():
        return dict(method="basin3", status="empty", comids=None, area_km2=None,
                    vaa_km2=None, polygon=None)
    edge = bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())
    # cell area is per ROW (an (H,) vector, latitude-dependent); weight each row's masked cells by it
    area_km2 = float((mask.sum(axis=1) * d8.cell_area_m2()).sum() / 1e6)
    polys = [sh_shape(s) for s, v in rio_shapes(mask, mask=mask.astype(bool), transform=d8.TRANSFORM)
             if v == 1]
    return dict(method="basin3", status="edge_truncated" if edge else "ok", comids=None,
                area_km2=area_km2, vaa_km2=None, polygon=unary_union(polys))


def basin0(usgs_id: str) -> dict:
    """The authority's own polygon, via a LIVE NLDI request -- network access, clearly labeled."""
    from pynhd import NLDI

    print(f"  [live NLDI call] fetching the authority basin for USGS-{usgs_id} ...")
    try:
        g = NLDI().get_basins(str(usgs_id))
    except Exception as e:                                   # noqa: BLE001 -- a remote service; degrade with the reason
        return dict(method="basin0", status=f"nldi_failed:{type(e).__name__}", comids=None,
                    area_km2=None, vaa_km2=None, polygon=None)
    if g is None or not len(g):
        return dict(method="basin0", status="nldi_empty", comids=None, area_km2=None,
                    vaa_km2=None, polygon=None)
    poly = g.geometry.iloc[0]
    import geopandas as gpd

    area = float(gpd.GeoSeries([poly], crs=4326).to_crs(config.EQUAL_AREA).area.iloc[0] / 1e6)
    return dict(method="basin0", status="ok", comids=None, area_km2=area, vaa_km2=None, polygon=poly)


def sensors_within(polygon) -> pd.DataFrame:
    """Published sensors inside a lon/lat polygon, with type and record columns -- the display that replaces any medium judgment: an arbitrary point HAS no medium, so the editor shows what is there and lets the modeler read the situation."""
    import shapely

    s = api.get_sensors(columns=["site_uid", "snr_id", "station_name", "lat", "lon",
                                 "site_type", "channels_present", "publishes_under", "published_as"])
    loc = s[s.lat.notna() & s.lon.notna()]
    pts = shapely.points(loc.lon.to_numpy(), loc.lat.to_numpy())
    inside = shapely.contains(polygon, pts)
    return loc[inside].reset_index(drop=True)


def to_weighted_set(result: dict) -> pd.DataFrame:
    """A delineation result as the weighted COMID set a modeler stores IN THEIR PROJECT -- never in the data stores."""
    if result.get("comids"):
        return agg_mod.from_comids(result["comids"])
    if result.get("polygon") is not None:
        return agg_mod.from_polygon(result["polygon"])
    raise ValueError(f"{result.get('method')}: nothing to normalise ({result.get('status')})")
