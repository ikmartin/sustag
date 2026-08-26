"""dash-leaflet layer builders: stated positions, uncertainty rectangles, reaches, neighbors.

EVERY POSITION DRAWN IS A STATED COORDINATE. The uncertainty rectangle is the declared-precision half-width per axis -- a 2 dp latitude draws a tall sliver, which tells the reviewer at a glance which axis not to trust. The snap residual is CONTEXT, drawn as a thin connector from the point to its reach, never folded into the rectangle.
"""

from __future__ import annotations

import dash_leaflet as dl
import numpy as np
import pandas as pd

from .. import config
from ..derive.geometry import half_widths_m

M_PER_DEG_LAT = 111_320.0


def _deg(lat, half_lat_m, half_lon_m):
    dlat = half_lat_m / M_PER_DEG_LAT
    dlon = half_lon_m / (M_PER_DEG_LAT * np.cos(np.radians(lat)))
    return dlat, dlon


def sensor_layers(rows: pd.DataFrame, colors=None):
    """[(marker, rectangle|None, tooltip)] per located sensor row from the sensors table."""
    from .plots import MEMBER_COLORS

    colors = colors or list(MEMBER_COLORS)
    out = []
    loc = rows[rows.lat.notna() & rows.lon.notna()]
    hla, hlo = half_widths_m(loc.lat, loc.coord_dp_lat, loc.coord_dp_lon)
    for i, (r, ha, ho) in enumerate(zip(loc.itertuples(), hla, hlo)):
        color = colors[i % len(colors)]
        tip = (f"{r.site_uid} | {r.station_name} | type {r.site_type} | "
               f"dp ({r.coord_dp_lat}, {r.coord_dp_lon}) | snap {r.snap_dist_m:.0f} m -> {r.comid}"
               if pd.notna(r.snap_dist_m) else f"{r.site_uid} | {r.station_name}")
        marker = dl.CircleMarker(center=[r.lat, r.lon], radius=6, color=color, fill=True,
                                 fillOpacity=0.9, children=dl.Tooltip(tip))
        rect = None
        if ha > 0 or ho > 0:
            dlat, dlon = _deg(r.lat, max(ha, 1.0), max(ho, 1.0))
            rect = dl.Rectangle(bounds=[[r.lat - dlat, r.lon - dlon], [r.lat + dlat, r.lon + dlon]],
                                color=color, weight=1, fill=True, fillOpacity=0.08, dashArray="4")
        out.append((marker, rect))
    return out


def reach_geojson(comids, hops: int = 3):
    """Context flowlines around the given reaches as a dash-leaflet GeoJSON layer (EPSG:4326)."""
    import geopandas as gpd
    import pyarrow.parquet as pq
    import shapely

    from ..acquire import network

    v = pd.read_parquet(network._path("vaa"), columns=["comid", "hydroseq", "uphydroseq", "dnhydroseq"])
    seq_to_comid = dict(zip(v.hydroseq.to_numpy(), v.comid.to_numpy()))
    by_comid = v.set_index("comid")
    keep = {int(c) for c in comids if pd.notna(c)}
    frontier = set(keep)
    for _ in range(hops):
        rows = by_comid.reindex([c for c in frontier if c in by_comid.index])
        nxt = set()
        for col in ("uphydroseq", "dnhydroseq"):
            for s in rows[col].dropna().astype("int64"):
                c = seq_to_comid.get(s)
                if c is not None and c not in keep:
                    nxt.add(int(c))
        if not nxt:
            break
        keep |= nxt
        frontier = nxt
    t = pq.read_table(network._path("flowlines"), columns=["COMID", "GNIS_NAME", "geometry"],
                      filters=[("COMID", "in", sorted(keep))])
    df = t.to_pandas()
    g = gpd.GeoDataFrame({"comid": df.COMID.astype("int64"), "name": df.GNIS_NAME},
                         geometry=shapely.from_wkb(df.geometry), crs=4326)
    snapped = g[g.comid.isin({int(c) for c in comids if pd.notna(c)})]
    context = g[~g.comid.isin(set(snapped.comid))]
    layers = []
    # catchment outlines UNDER the flowlines: their own pane below the overlay pane (400), so the
    # async GeoJSON loads cannot shuffle them above the streams
    ct = pq.read_table(network._path("catchments"), columns=["FEATUREID", "geometry"],
                       filters=[("FEATUREID", "in", sorted(keep))])
    if len(ct):
        cg = gpd.GeoDataFrame({"comid": ct.column("FEATUREID").to_pandas().astype("int64")},
                              geometry=shapely.from_wkb(ct.column("geometry").to_pandas()), crs=4326)
        layers.append(dl.Pane(dl.GeoJSON(data=cg.__geo_interface__,
                                         style=dict(color="#000000", weight=1.6, opacity=0.6,
                                                    fill=False)),
                              name="catchments", style={"zIndex": 390}))
    if len(context):
        layers.append(dl.GeoJSON(data=context.__geo_interface__,
                                 style=dict(color="#9db8d2", weight=1.5)))
    if len(snapped):
        layers.append(dl.GeoJSON(data=snapped.__geo_interface__,
                                 style=dict(color="#1f5fa8", weight=3.5)))
    return layers


def pair_map(rows: pd.DataFrame, neighbors: pd.DataFrame | None = None, height="420px"):
    """One evidence map: the sensors, their rectangles, their reaches, and the 2 km neighborhood."""
    children = [dl.TileLayer(), dl.ScaleControl(metric=True, imperial=False, position="bottomleft")]
    try:
        children += reach_geojson(rows.comid.dropna().unique())
    except Exception as e:                                   # noqa: BLE001 -- a map layer must not sink the tab
        print(f"  reach layer unavailable ({type(e).__name__}: {e})")
    # every dot lives in its own pane ABOVE the flowline overlay: GeoJSON layers load async and
    # would otherwise paint over markers regardless of children order
    dots = []
    if neighbors is not None and len(neighbors):
        near = pd.concat([neighbors.uid_a, neighbors.uid_b]).unique()
        from . import backend

        extra = backend.sensor_rows([u for u in near if u not in set(rows.site_uid)])
        for marker, _rect in sensor_layers(extra, colors=["#999999"]):
            dots.append(marker)
    for marker, rect in sensor_layers(rows):
        if rect is not None:
            dots.append(rect)
        dots.append(marker)
    children.append(dl.Pane(dots, name="sensor-dots", style={"zIndex": 620}))
    loc = rows[rows.lat.notna()]
    center = [float(loc.lat.mean()), float(loc.lon.mean())] if len(loc) else [42.0, -93.5]
    # position:relative + zIndex:0 makes the map its own stacking context, trapping leaflet's
    # controls (zoom buttons live at z-index ~1000 INSIDE it) below the page's dropdown menus
    return dl.Map(children=children, center=center, zoom=14,
                  style={"height": height, "position": "relative", "zIndex": 0})
