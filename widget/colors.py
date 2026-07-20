"""Map layer color palette.

Each entry is a dict with 'stroke' and/or 'fill' keys (CSS color strings).
Surplus gradient is stored as (hue, saturation, lightness) HSL tuples so the
interpolation in map_panel stays in the same color space.
"""

# Lazy deployment safeguard. When True, the Debug tab's "confirm" button is INERT -- it makes no
# changes and only flashes a brief, self-clearing "Debug Mode Off" message. Set to False to
# re-enable the real basin-review confirm action (writes preferred_basin). Default True so a
# deployed instance can't mutate the dataset from the UI.
DEBUG_MODE_ON = True

HYDRO = {"stroke": "#2563eb", "fill": "#3b82f6"}

BASIN = {"stroke": "#0d9488", "fill": "#0d9488"}
BASIN_V2 = {"stroke": "purple", "fill": "purple"}
BASIN_V3 = {"stroke": "#7c3aed", "fill": "#7c3aed"}

PIN_BASIN_V1 = {"stroke": "#f97316", "fill": "#f97316"}
PIN_BASIN_V3 = {"stroke": "#0891b2", "fill": "#0891b2"}

RAIN_GRID = {"stroke": "#0284c7", "fill": "#38bdf8"}
IEM_BBOX = {"stroke": "#f97316"}

SITE_DEFAULT = {"stroke": "darkgreen", "fill": "limegreen"}
SITE_USGS = {"stroke": "#6b21a8", "fill": "#55a3f7"}
SITE_SELECTED = {"stroke": "darkred", "fill": "red"}

SURPLUS_LOW = (120, 80, 45)  # HSL green (low surplus)
SURPLUS_HIGH = (0, 80, 45)  # HSL red   (high surplus)


# ── Leaflet layer styles (centralised so map_panel stays styling-free) ────────


def basin_style(kind):
    """Leaflet style for a monitoring-site basin layer.

    kind: 'preferred' or 1 -> teal, 2 -> purple, 3 -> violet (matches the basin_type palette).
    """
    palette = {"preferred": (BASIN, 0.15), 1: (BASIN, 0.15), 2: (BASIN_V2, 0.12), 3: (BASIN_V3, 0.12)}
    c, fill_opacity = palette[kind]
    return {
        "pane": "basin-pane",
        "color": c["stroke"],
        "weight": 2,
        "fillOpacity": fill_opacity,
        "fillColor": c["fill"],
        "interactive": False,
    }


def pin_basin_style(method):
    """Leaflet style for a dropped-pin delineated basin. method: 'v1' (NLDI) or 'v3' (D8)."""
    c = {"v1": PIN_BASIN_V1, "v3": PIN_BASIN_V3}[method]
    return {
        "pane": "basin-pane",
        "color": c["stroke"],
        "weight": 2,
        "fillOpacity": 0.15,
        "fillColor": c["fill"],
        "interactive": False,
    }


# ── Crop palette (dominant-crop grid colouring; mirrors the presentation figure) ──────────────
# CDL categories the aggregation keeps (see recipes/crop reconciliation). Cells with no crop data
# fall back to _GRID_NODATA_COLOR in map_common.
CROP_COLORS = {
    "Corn": "#f2c14e",
    "Soybeans": "#2f7d32",
    "Hay_Pasture": "#8bc34a",
    "Alfalfa": "#7e57c2",
    "Small_Grains": "#c8a15a",
    "Fallow": "#a1887f",
    "Nonag": "#9e9e9e",
    "Other": "#d7ccc8",
}


# ── Explore-tab control defaults ──────────────────────────────────────────────────────────────
# Single source of truth for the initial value of every Explore-tab control. Change a value here
# to change what the widget shows on load; map_layout's section builders read these via default().
EXPLORE_DEFAULTS = {
    # selection
    "selection-mode": "point",
    # graph display
    "graph-toggle": ["show"],
    "aggregate-interval": "1D",
    "agg-func-water": "mean",
    "agg-func-rain": "sum",
    # map display overlays
    "hydro-toggle": ["show"],
    "basin-preferred-toggle": [],
    "basin-all-toggle": [],
    "rain-grid-toggle": [],
    "iowa-surplus-heatmap-toggle": [],
    "surplus-year-slider": 2017,
    "surplus-opacity-slider": 0.8,
    # presentation display options
    "grid-color-mode": "surplus",
    # map layers / debug
    "tile-selector": "street",
    "iem-bbox-toggle": [],
}


def default(key, fallback=None):
    """Initial value for an Explore-tab control id (see EXPLORE_DEFAULTS). Lists are copied so a
    Dash component can never mutate the shared default in place."""
    v = EXPLORE_DEFAULTS.get(key, fallback)
    return list(v) if isinstance(v, list) else v
