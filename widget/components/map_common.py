"""Map panel: owns the Leaflet map and all region-of-interest selection.

Selection modes
---------------
Pin Drop : click anywhere on the map to pin a coordinate. Result is written to
           the `region-geom` store as a GeoJSON Point.
Point    : (default) select a monitoring site by clicking its marker. Map clicks
           do not drop a pin in this mode.
Area     : draw a rectangle or free-form polygon to bulk-select monitoring sites.
           Result is written to `region-geom` as a GeoJSON Polygon.

Shared state written by this module
------------------------------------
region-geom       : GeoJSON geometry of the current point or area selection.
active-graph-site : site_uid of the monitoring site whose timeseries is shown
                    in the info panel graph.  Set by clicking a site marker or
                    a row in the Sites Selected table.
selected-sites    : list of site_uids currently highlighted on the map.

Layer slots
-----------
The map contains several named `dl.LayerGroup` elements that act as render
slots.  This module owns their layout placement; other panels populate them:
  mapunit-layer  : reserved for future use
  forecast-layer : populated by forecast_panel
"""

import base64
import functools
import json
import random
from pathlib import Path

import pandas as pd
import geopandas as gpd
import plotly.graph_objects as go
import dash_leaflet as dl
from dash import Input, Output, State, html, dcc, no_update, ALL, ctx
from dash_extensions.javascript import assign
from shapely.geometry import shape, Point

from src.data import access, surplus_viz
from geo_utils import delineate_basin_for_pin, delineate_basin_v3_for_pin
from components import basin_editor
import colors

IOWA_CENTER = [42.0, -93.5]
IOWA_ZOOM = 7

# 1×1 transparent PNG used as the placeholder url for hidden ImageOverlays
_TRANSPARENT_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQAABjE+ibYAAAAASUVORK5CYII="
)
_IOWA_BOUNDS = [[40.3, -96.7], [43.5, -90.1]]

# Inverted mask: world polygon with Iowa bounding box (+ padding) cut as a hole.
# Dims everything outside the box at 50% opacity while leaving data layers on top unaffected.
_IOWA_MASK = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]],  # world
            [[-99.0, 39.0], [-87.5, 39.0], [-87.5, 45.0], [-99.0, 45.0], [-99.0, 39.0]],  # Iowa box
        ],
    },
    "properties": {},
}

_NHD_MIN_ORDER = 5
_NHD_SIMPLIFY_TOLERANCE = 0.005  # degrees; ~50% vertex reduction at state zoom

_ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
_FLOWLINES_ASSET = _ASSETS_DIR / "iowa_flowlines.geojson"
_WATERBODIES_ASSET = _ASSETS_DIR / "iowa_waterbodies.geojson"


def _ensure_nhd_assets():
    """Write simplified NHD GeoJSON to assets/ at startup if not already present."""
    if _FLOWLINES_ASSET.exists() and _WATERBODIES_ASSET.exists():
        return
    _ASSETS_DIR.mkdir(exist_ok=True)
    if not _FLOWLINES_ASSET.exists():
        fl = access.get_flowlines()
        fl = fl[fl["StreamOrde"] >= _NHD_MIN_ORDER][["geometry"]]
        fl["geometry"] = fl.geometry.simplify(_NHD_SIMPLIFY_TOLERANCE)
        _FLOWLINES_ASSET.write_text(fl.to_json())
    if not _WATERBODIES_ASSET.exists():
        wb = access.get_waterbodies()[["geometry"]]
        wb["geometry"] = wb.geometry.simplify(_NHD_SIMPLIFY_TOLERANCE)
        _WATERBODIES_ASSET.write_text(wb.to_json())


_ensure_nhd_assets()


TILE_URLS = {
    "street": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    "satellite": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "humanitarian": "https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
    "watercolor": "https://tiles.stadiamaps.com/tiles/stamen_watercolor/{z}/{x}/{y}.jpg",
}

# dl.EditControl's `draw` prop is only read once, at mount (dash-leaflet does
# not react to later changes to it), so the set of enabled draw tools is fixed
# here. Both rectangle and polygon are always available as separate buttons in
# the Leaflet draw toolbar (bottom-left of the map).
DRAW_TOOLS = {
    "rectangle": True,
    "polygon": True,
    "polyline": False,
    "circle": False,
    "circlemarker": False,
    "marker": False,
}


def _svg_img(inner):
    svg = '<svg viewBox="0 0 16 16" width="16" height="16"' ' xmlns="http://www.w3.org/2000/svg">' + inner + "</svg>"
    src = "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
    return html.Img(src=src, width=16, height=16, style={"display": "block"})


def _draw_btn(icon, btn_id, title):
    return html.Button(
        icon,
        id=btn_id,
        n_clicks=0,
        title=title,
        style={
            "background": "white",
            "border": "1px solid #bbb",
            "borderRadius": "3px",
            "padding": "5px",
            "cursor": "pointer",
            "display": "flex",
            "alignItems": "center",
            "justifyContent": "center",
            "width": "30px",
            "height": "30px",
            "boxSizing": "border-box",
        },
    )


def _rect_icon():
    return _svg_img('<rect x="1" y="4" width="14" height="8"' ' fill="none" stroke="#555" stroke-width="1.5"/>')


def _poly_icon():
    return _svg_img('<path d="M8,1 L15,6 L12,14 L4,14 L1,6 Z"' ' fill="none" stroke="#555" stroke-width="1.5"/>')


def _trash_icon():
    return _svg_img(
        '<line x1="1" y1="4" x2="15" y2="4" stroke="#555" stroke-width="1.5"/>'
        '<path d="M6 4V2H10V4" fill="none" stroke="#555" stroke-width="1.5"/>'
        '<path d="M3 4L4 14H12L13 4" fill="none" stroke="#555" stroke-width="1.5"/>'
        '<line x1="6" y1="7" x2="6" y2="11" stroke="#555" stroke-width="1.5"/>'
        '<line x1="10" y1="7" x2="10" y2="11" stroke="#555" stroke-width="1.5"/>'
    )


_SECTION_LABEL = {
    "fontSize": "11px",
    "fontWeight": "bold",
    "color": "#888",
    "textTransform": "uppercase",
    "letterSpacing": "0.5px",
    "marginBottom": "6px",
}
_SECTION_LABEL_SUMMARY = {**_SECTION_LABEL, "cursor": "pointer", "userSelect": "none"}
_SUBSECTION_LABEL = {
    "fontSize": "10px",
    "fontWeight": "600",
    "color": "#bbb",
    "textTransform": "uppercase",
    "letterSpacing": "0.5px",
    "marginTop": "8px",
    "marginBottom": "3px",
    "borderBottom": "1px solid #eee",
    "paddingBottom": "2px",
}
_CHECKBOX_ROW = {"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginTop": "4px"}
_CHECKBOX_STYLE = {"fontSize": "13px"}
_CHECKBOX_LABEL = {"fontSize": "13px", "display": "flex", "alignItems": "center", "cursor": "pointer"}

# Permanent side panel — always visible, 20 % of viewport width
_PANEL_STYLE = {
    "flex": "0 0 32%",
    "width": "32%",
    "height": "100vh",
    "background": "rgba(255,255,255,0.97)",
    "borderLeft": "1px solid rgba(0,0,0,0.15)",
    "boxShadow": "-3px 0 8px rgba(0,0,0,0.10)",
    "overflowY": "auto",
    "padding": "0 12px 12px 12px",
    "boxSizing": "border-box",
}

# Area-select draw button styles
_DRAW_BTN_BASE = {
    "borderRadius": "3px",
    "padding": "5px",
    "cursor": "pointer",
    "display": "flex",
    "alignItems": "center",
    "justifyContent": "center",
    "width": "30px",
    "height": "30px",
    "boxSizing": "border-box",
}
_DRAW_BTN_INACTIVE = {**_DRAW_BTN_BASE, "background": "white", "border": "1px solid #bbb"}
_DRAW_BTN_ACTIVE = {**_DRAW_BTN_BASE, "background": "#dbeafe", "border": "1.5px solid #3b82f6"}

# Menu selector tab styles
_MENU_TAB_ACTIVE = {
    "flex": "1",
    "padding": "8px 0",
    "fontSize": "11px",
    "fontWeight": "700",
    "background": "#1e3a8a",
    "color": "white",
    "border": "none",
    "cursor": "pointer",
    "letterSpacing": "0.4px",
    "textTransform": "uppercase",
}
_MENU_TAB_INACTIVE = {**_MENU_TAB_ACTIVE, "background": "#f1f5f9", "color": "#64748b", "fontWeight": "600"}

_GRAPH_OVERLAY_BASE = {
    "position": "absolute",
    "top": "10px",
    "left": "10px",
    "width": "48%",
    "height": "35vh",
    "background": "rgba(255,255,255,0.95)",
    "borderRadius": "6px",
    "boxShadow": "0 2px 8px rgba(0,0,0,0.2)",
    "zIndex": 700,
    "boxSizing": "border-box",
}
_GRAPH_OVERLAY_HIDDEN = {**_GRAPH_OVERLAY_BASE, "display": "none"}
_GRAPH_OVERLAY_VISIBLE = {**_GRAPH_OVERLAY_BASE, "display": "block"}

_HELP_BTN_STYLE = {
    "position": "absolute",
    "top": "0",
    "right": "0",
    "background": "none",
    "border": "1.5px solid #aaa",
    "borderRadius": "50%",
    "width": "16px",
    "height": "16px",
    "fontSize": "10px",
    "lineHeight": "14px",
    "cursor": "pointer",
    "color": "#888",
    "padding": "0",
    "fontWeight": "bold",
    "textAlign": "center",
}
_HELP_POPUP_STYLE = {
    "position": "absolute",
    "top": "20px",
    "right": "0",
    "width": "230px",
    "background": "white",
    "border": "1px solid #ddd",
    "borderRadius": "6px",
    "boxShadow": "0 4px 12px rgba(0,0,0,0.15)",
    "padding": "10px 12px",
    "zIndex": 2000,
    "lineHeight": "1.5",
}
_HP = {"margin": "2px 0 8px 0", "color": "#555", "fontSize": "11px"}


def load_iowa_geojson():
    states = gpd.read_file("https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_state_20m.zip")
    return states[states["NAME"] == "Iowa"].__geo_interface__


iowa_geojson = load_iowa_geojson()
IWQIS_SITES = access.get_metadata()[["site_uid", "latitude", "longitude"]]


def _sites_in_polygon(geojson_geom):
    """Return site_uids whose location falls inside a GeoJSON geometry."""
    poly = shape(geojson_geom)
    return [
        row.site_uid for row in IWQIS_SITES.itertuples(index=False) if poly.contains(Point(row.longitude, row.latitude))
    ]


def make_iwqis_markers(selected_uids=None, visible_uids=None):
    """Build small clickable circle markers for the IWQIS sites.

    Each marker uses bubblingMouseEvents=False so clicking it does not also
    trigger the map's click handler. Each marker has a pattern-matching id
    so on_iwqis_marker_click can tell which site was clicked.

    visible_uids : set or None
        If provided, only markers whose site_uid is in this set are rendered.
    """
    selected = set(selected_uids or [])
    sites = list(IWQIS_SITES[["site_uid", "latitude", "longitude"]].itertuples(index=False, name=None))
    if visible_uids is not None:
        sites = [(uid, lat, lon) for uid, lat, lon in sites if uid in visible_uids]
    return [
        dl.CircleMarker(
            id={"type": "iwqis-marker", "index": site_uid},
            center=[lat, lon],
            radius=7 if site_uid in selected else 5,
            color=(
                colors.SITE_SELECTED["stroke"]
                if site_uid in selected
                else (colors.SITE_USGS["stroke"] if site_uid.startswith("USGS") else colors.SITE_DEFAULT["stroke"])
            ),
            fillColor=(
                colors.SITE_SELECTED["fill"]
                if site_uid in selected
                else (colors.SITE_USGS["fill"] if site_uid.startswith("USGS") else colors.SITE_DEFAULT["fill"])
            ),
            fillOpacity=0.8,
            weight=1,
            pane="sites-pane",
            bubblingMouseEvents=False,
        )
        for (site_uid, lat, lon) in sites
    ]


@functools.lru_cache(maxsize=1)
def _fake_bad_site_points(n=77, seed=1234):
    """`n` deterministic points sampled from Iowa river flowline vertices (so they sit ON rivers).
    PRESENTATION-ONLY. Cached once (the geojson read is the only cost)."""
    data = json.loads(_FLOWLINES_ASSET.read_text())
    coords = []  # every vertex across all flowlines, as [lon, lat]
    for feat in data.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") == "LineString":
            coords.extend(geom["coordinates"])
        elif geom.get("type") == "MultiLineString":
            for line in geom["coordinates"]:
                coords.extend(line)
    picks = random.Random(seed).sample(coords, min(n, len(coords)))
    return tuple((lat, lon) for lon, lat in picks)  # -> (lat, lon) for Leaflet


def make_fake_bad_site_markers(n=77, green_frac=0.6, seed=1234):
    """PRESENTATION-ONLY fake sensor dots scattered along Iowa rivers: `n` circle markers, ~green_frac
    green (SITE_DEFAULT) and the rest blue (SITE_USGS), styled to match the real IWQIS/USGS markers
    but with NO id -> non-interactive (they never trigger the site-select callback). Deterministic."""
    pts = _fake_bad_site_points(n, seed)
    n_green = int(round(len(pts) * green_frac))  # 60% green, 40% blue by default
    return [
        dl.CircleMarker(
            center=[lat, lon],
            radius=5,
            color=(colors.SITE_DEFAULT if i < n_green else colors.SITE_USGS)["stroke"],
            fillColor=(colors.SITE_DEFAULT if i < n_green else colors.SITE_USGS)["fill"],
            fillOpacity=0.8,
            weight=1,
            pane="sites-pane",
            bubblingMouseEvents=False,
        )
        for i, (lat, lon) in enumerate(pts)
    ]


def _hr():
    return html.Hr(style={"margin": "10px 0", "borderColor": "#eee"})


def _help_btn(btn_id):
    return html.Button("?", id=btn_id, n_clicks=0, style=_HELP_BTN_STYLE)


def _help_popup(popup_id, close_id, heading, body):
    return html.Div(
        id=popup_id,
        style={"display": "none"},
        children=[
            html.Div(
                [
                    html.Strong(heading, style={"fontSize": "12px"}),
                    html.Button(
                        "×",
                        id=close_id,
                        n_clicks=0,
                        style={
                            "float": "right",
                            "background": "none",
                            "border": "none",
                            "cursor": "pointer",
                            "fontSize": "15px",
                            "color": "#999",
                            "padding": "0",
                            "lineHeight": "1",
                        },
                    ),
                ],
                style={"marginBottom": "6px", "overflow": "hidden"},
            ),
            html.Hr(style={"margin": "0 0 8px 0", "borderColor": "#eee"}),
            html.Div(body),
        ],
    )


def _wrap_with_help(details_el, btn_id, close_id, popup_id, heading, body):
    return html.Div(
        style={"position": "relative", "marginBottom": "10px"},
        children=[
            details_el,
            _help_btn(btn_id),
            _help_popup(popup_id, close_id, heading, body),
        ],
    )


_GRID_NODATA_COLOR = "#cccccc"  # cells with no surplus value for the year (e.g. outside Iowa)

_GRID_STYLE_JS = assign(
    """function(feature, context){
        return {color: '#0284c7', weight: 1, fillColor: feature.properties.color, fillOpacity: 0.55};
    }"""
)
_GRID_HOVER_JS = assign(
    """function(feature, context){
        return {weight: 3, color: '#0c4a6e', fillOpacity: 0.8};
    }"""
)
_GRID_ONEACH_JS = assign(
    """function(feature, layer, context){
        if(feature.properties && feature.properties.tooltip){
            // Click opens a popup — reliable even when Leaflet's vector hover
            // hit-testing goes stale after a map pan/zoom (clicks are hit-tested
            // by coordinate, so they always reach the cell).
            layer.bindPopup(feature.properties.tooltip);
            // Hover tooltip too, for the (intermittent) cases where mouseover fires.
            layer.bindTooltip(feature.properties.tooltip, {sticky: true});
            layer.on('mouseover', function(e){ layer.openTooltip(e.latlng); });
            layer.on('mousemove', function(e){ layer.openTooltip(e.latlng); });
            layer.on('mouseout', function(){ layer.closeTooltip(); });
        }
    }"""
)


def _cell_tooltip(row, crop_cols):
    """HTML tooltip string for one Voronoi cell: area + surplus + total N + crop list."""
    lines = [f"<b>Cell {int(row['node_id'])}</b>"]
    if pd.notna(row.get("cell_area")):
        area_km2 = row["cell_area"] / 1e6  # cell_area is m² (EPSG:5070)
        lines.append(f"Cell area: {area_km2:.2f} km²")
        if pd.notna(row.get("frac_cell_in_basin")):
            lines.append(f"Cell area in basin: {area_km2 * row['frac_cell_in_basin']:.2f} km²")
    if pd.notna(row.get("dist_to_sensor")):
        lines.append(f"Dist to sensor: {row['dist_to_sensor'] / 1e3:.1f} km")  # dist_to_sensor is m
    if pd.notna(row.get("surplus_kgha")):
        lines.append(f"Surplus: {row['surplus_kgha']:.0f} kg/ha")
        lines.append(f"Total N: {row['total_kg_N']:,.0f} kg")
    else:
        lines.append("Surplus: n/a (outside data)")
    counts = {c: row[c] for c in crop_cols if pd.notna(row.get(c)) and row[c] > 0}
    total = sum(counts.values())
    if total:
        lines.append("Crops:")
        for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"&nbsp;&nbsp;{k}: {v / total:.0%}")
    return "<br>".join(lines)


@functools.lru_cache(maxsize=64)
def _rain_grid_features(uid, year, mode="surplus"):
    """GeoJSON (lon/lat) of a site's rain cells, each with a per-cell colour and a tooltip joining
    surplus + crop stats for `year`. `mode` picks the colour: 'surplus' (nitrogen-surplus gradient,
    the default) or 'crop' (dominant CDL crop, colors.CROP_COLORS). Raises FileNotFoundError if the
    rain grid hasn't been built. Cached per (uid, year, mode) -- the result is passed read-only to
    dl.GeoJSON, so callers must not mutate it."""
    grid = access.get_grid(uid).to_crs("EPSG:4326")
    cells = grid[["node_id", "geometry"] + [c for c in ("cell_area", "frac_cell_in_basin", "dist_to_sensor") if c in grid.columns]]

    try:
        s = access.get_surplus(uid)
        s = s[s["year"] == year][["node_id", "surplus_kgha", "total_kg_N"]]
    except FileNotFoundError:
        s = pd.DataFrame(columns=["node_id", "surplus_kgha", "total_kg_N"])

    try:
        c = access.get_crops(uid)
        c = c[c["year"] == year].drop(columns=["year"])
    except FileNotFoundError:
        c = pd.DataFrame(columns=["node_id"])
    crop_cols = [col for col in c.columns if col not in ("node_id", "global_node_id")]

    cells = cells.merge(s, on="node_id", how="left").merge(c, on="node_id", how="left")
    if mode == "crop":  # colour each cell by its dominant crop for the year
        present = [col for col in crop_cols if col in cells.columns]
        if present:
            filled = cells[present].fillna(0)
            dom = filled.idxmax(axis=1).where(filled.sum(axis=1) > 0)  # NaN where the cell has no crop data
            cells["color"] = dom.map(colors.CROP_COLORS).fillna(_GRID_NODATA_COLOR)
        else:
            cells["color"] = _GRID_NODATA_COLOR
    else:  # nitrogen-surplus gradient (default)
        cells["color"] = cells["surplus_kgha"].map(
            lambda v: surplus_viz.surplus_to_hex(v) if pd.notna(v) else _GRID_NODATA_COLOR
        )
    cells["tooltip"] = cells.apply(lambda r: _cell_tooltip(r, crop_cols), axis=1)
    return json.loads(cells[["node_id", "color", "tooltip", "geometry"]].to_json())


def _rain_grid_dots(uid):
    """Fallback: grid-cell centroids as dots, for sites without grid geometry yet."""
    try:
        grid = access.get_grid(uid)
    except FileNotFoundError:
        return []
    cells = grid[["lon", "lat"]].drop_duplicates()
    return [
        dl.CircleMarker(
            center=[row.lat, row.lon],
            radius=4,
            color=colors.RAIN_GRID["stroke"],
            fillColor=colors.RAIN_GRID["fill"],
            fillOpacity=0.5,
            weight=1,
            pane="rain-grid-pane",
            bubblingMouseEvents=False,
        )
        for row in cells.itertuples()
    ]




# Export everything (incl. single-underscore helpers/constants) for `from map_common import *`.
__all__ = [n for n in list(globals()) if not n.startswith("__")]
