"""Basin Editor panel section and callbacks.

Owns the Basin Editor block that lives in the Debug menu: site selection,
flag display, preferred-basin radio, confirm button, and the basin display
checkboxes.  The map-layer callbacks that *render* basin polygons (basin1,
basin2, basin3) stay in map_panel because they output to map LayerGroups.
"""

import math
import pandas as pd
from dash import Input, Output, State, html, dcc, no_update, ctx

from src.data import access
from geo_utils import delineate_basin_v1_for_pin, delineate_basin_v2_for_pin
import colors

# ── Style constants ───────────────────────────────────────────────────────────
# Duplicated from map_panel to avoid a circular import.

_SECTION_LABEL_SUMMARY = {
    "fontSize": "11px",
    "fontWeight": "bold",
    "color": "#888",
    "textTransform": "uppercase",
    "letterSpacing": "0.5px",
    "cursor": "pointer",
    "userSelect": "none",
}
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
_CHECKBOX_STYLE = {"fontSize": "13px"}
_CHECKBOX_LABEL = {"fontSize": "13px", "display": "flex", "alignItems": "center", "cursor": "pointer"}

_NO_PIN_POPUP_HIDDEN = {"display": "none"}
_NO_PIN_POPUP_VISIBLE = {
    "display": "block",
    "marginTop": "4px",
    "padding": "4px 8px",
    "background": "#fef2f2",
    "border": "1px solid #fca5a5",
    "borderRadius": "4px",
    "fontSize": "11px",
    "color": "#dc2626",
}


_BASIN_METHODS = [
    ("v1", "snap"),  # nearest-flowline snap -- the shipped delineation
    ("v2", "catch"),  # containing catchment (/comid/position)
    ("v3", "D8"),  # raster flood-fill
]

# "Set preferred basin" radio. Values are the basin_type ints as strings, plus custom_v1 (a
# pin-drop snap). v0 (auth) is USGS-only and disabled elsewhere when the site has no basin0.
_RADIO_OPTIONS = [
    {"label": " v0 (auth)", "value": "0"},
    {"label": " v1 (snap)", "value": "1"},
    {"label": " v2 (catch)", "value": "2"},
    {"label": " Custom v1", "value": "custom_v1"},
    {"label": " Custom v2", "value": "custom_v2"},
]

_FLAG_LABELS = {
    "flag_area": "large area",
    "flag_river": "near major river",
    "flag_not_contained": "not contained",
    "flag_basin1_over_basin0": "snap over auth",
    "flag_area_mismatch_v2": "v1/v2 area mismatch",
    "flag_area_mismatch_v3": "v1/v3 area mismatch",
}


_AREA_LABEL_STYLE = {"fontSize": "10px", "color": "#888", "marginLeft": "5px", "minWidth": "48px"}


def _fmt_area_km2(v) -> str:
    """Format a basin area (km2) for a display label; blank for missing."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return f"{float(v):,.0f} km²"


def _display_cell(toggle_id: str, area_id: str) -> html.Div:
    """A display checkbox with an area label to its right (filled by callback)."""
    return html.Div(
        [
            dcc.Checklist(
                id=toggle_id,
                options=[{"label": "", "value": "show"}],
                value=[],
                style={"fontSize": "13px", "margin": "0"},
                labelStyle={"margin": "0"},
            ),
            html.Span("", id=area_id, style=_AREA_LABEL_STYLE),
        ],
        style={"display": "flex", "alignItems": "center", "justifyContent": "flex-start"},
    )


def _basin_display_table() -> html.Table:
    """Rows v1/v2/v3 x columns site/pin. Site cells reuse basin{1,2,3}-toggle (map_panel renders
    the stored parquets); pin cells drive the live pin-drop overlays. Each checkbox shows the area
    of its basin (km2) to the right -- site areas from preferred_basin.csv, pin areas computed on
    drop."""
    hdr_style = {"fontSize": "10px", "color": "#888", "fontWeight": "600", "padding": "2px 8px", "textAlign": "center"}
    row_label_style = {"fontSize": "11px", "color": "#555", "padding": "2px 8px", "whiteSpace": "nowrap"}
    header = html.Tr(
        [
            html.Th("", style=hdr_style),
            html.Th("site", style=hdr_style),
            html.Th("pin", style=hdr_style),
        ]
    )
    body = [
        html.Tr(
            [
                html.Td(f"{v} ({label})", style=row_label_style),
                html.Td(_display_cell(f"basin{i}-toggle", f"basin{i}-area"), style={"padding": "2px 8px"}),
                html.Td(_display_cell(f"pin-basin-{v}-toggle", f"pin-basin-{v}-area"), style={"padding": "2px 8px"}),
            ]
        )
        for i, (v, label) in enumerate(_BASIN_METHODS, start=1)
    ]
    return html.Table([html.Thead(header), html.Tbody(body)], style={"borderCollapse": "collapse"})


def layout():
    return html.Details(
        [
            html.Summary("Basin Editor", style=_SECTION_LABEL_SUMMARY),
            # ── Site filter + dropdown + flags ────────────────────────────────
            html.Div(
                style={"display": "flex", "gap": "12px", "marginTop": "6px"},
                children=[
                    dcc.Checklist(
                        id="basin-review-flagged-only",
                        options=[{"label": " Flagged sites only", "value": "on"}],
                        value=[],
                        style=_CHECKBOX_STYLE,
                        labelStyle=_CHECKBOX_LABEL,
                    ),
                    dcc.Checklist(
                        id="basin-review-unreviewed-only",
                        options=[{"label": " Unreviewed only", "value": "on"}],
                        value=[],
                        style=_CHECKBOX_STYLE,
                        labelStyle=_CHECKBOX_LABEL,
                    ),
                ],
            ),
            dcc.Dropdown(
                id="basin-review-site-dropdown",
                placeholder="Select site...",
                clearable=True,
                style={"fontSize": "12px", "marginTop": "4px"},
            ),
            html.Div(id="basin-review-site-meta", style={"fontSize": "11px", "marginTop": "4px"}),
            html.Div(id="basin-review-flags", style={"fontSize": "11px", "marginTop": "4px"}),
            # ── Two-column layout ─────────────────────────────────────────────
            html.Div(
                style={"display": "flex", "gap": "16px", "marginTop": "8px"},
                children=[
                    # Left: Set preferred basin (radio) + confirm -- 1/3 width
                    html.Div(
                        style={"flex": "1", "minWidth": "0"},
                        children=[
                            html.Div("Set preferred basin", style=_SUBSECTION_LABEL),
                            dcc.RadioItems(
                                id="basin-review-type",
                                options=_RADIO_OPTIONS,
                                value=None,
                                style={"fontSize": "12px"},
                                labelStyle={
                                    "display": "flex",
                                    "alignItems": "center",
                                    "gap": "4px",
                                    "marginBottom": "2px",
                                },
                            ),
                            html.Div(
                                style={"display": "flex", "alignItems": "center", "gap": "6px", "marginTop": "8px"},
                                children=[
                                    html.Button(
                                        "Confirm",
                                        id="basin-review-confirm-btn",
                                        n_clicks=0,
                                        style={
                                            "fontSize": "11px",
                                            "padding": "4px 10px",
                                            "cursor": "pointer",
                                            "background": "#1e3a8a",
                                            "color": "white",
                                            "border": "none",
                                            "borderRadius": "3px",
                                        },
                                    ),
                                    html.Span(
                                        id="basin-review-confirm-status",
                                        style={"fontSize": "11px", "color": "#888"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                    # Right: Basin display table -- rows v1/v2/v3 x columns site/pin -- 2/3 width.
                    # (render callbacks live in map_panel). v0 (auth) is omitted: it is a uid lookup
                    # with no pin-drop equivalent.
                    html.Div(
                        style={"flex": "2", "minWidth": "0"},
                        children=[
                            html.Div("Basin display", style=_SUBSECTION_LABEL),
                            _basin_display_table(),
                        ],
                    ),
                ],
            ),
            html.Div(
                id="no-pin-popup",
                style={"display": "none"},
                children="No pin dropped on map!",
            ),
            dcc.Interval(id="no-pin-timer", interval=2000, n_intervals=0, disabled=True, max_intervals=1),
        ],
        open=True,
    )


def register_callbacks(app):
    @app.callback(
        Output("basin-review-site-dropdown", "options"),
        Output("basin-review-site-dropdown", "value"),
        Input("basin-review-flagged-only", "value"),
        Input("basin-review-unreviewed-only", "value"),
        Input("preferred-basin-version", "data"),
        Input("selected-site", "data"),
        State("active-menu", "data"),
    )
    def update_basin_review_dropdown(flagged_only, unreviewed_only, _version, selected_sites, active_menu):
        try:
            meta = access.get_basin_metadata()
        except FileNotFoundError:
            return [], no_update

        df = meta.copy()
        # Derived from the column names, so a new flag in _make_basins.FLAG_COLS surfaces here
        # automatically instead of being silently dropped.
        flag_cols = [c for c in df.columns if c.startswith("flag_")]
        if "on" in (flagged_only or []):
            flag_df = df[flag_cols].fillna(False)
            df = df[flag_df.any(axis=1)]
        if "on" in (unreviewed_only or []):
            df = df[~df["reviewed"].fillna(False).astype(bool)]

        opts = [{"label": r["site_uid"], "value": r["site_uid"]} for _, r in df.iterrows()]

        if (
            ctx.triggered_id == "selected-site"
            and active_menu == "debug"
            and selected_sites
            and len(selected_sites) == 1
        ):
            return opts, selected_sites[0]

        return opts, no_update

    @app.callback(
        Output("selected-site", "data", allow_duplicate=True),
        Input("basin-review-site-dropdown", "value"),
        prevent_initial_call=True,
    )
    def sync_selected_site_from_dropdown(site_uid):
        if ctx.triggered_id != "basin-review-site-dropdown":
            return no_update
        return [site_uid] if site_uid else []

    @app.callback(
        Output("basin-review-site-meta", "children"),
        Input("basin-review-site-dropdown", "value"),
    )
    def update_basin_review_site_meta(site_uid):
        """Site metadata line, styled to match the basin status line directly below it."""
        if not site_uid:
            return ""
        try:
            desc = access.get_site_desc(site_uid)
        except (KeyError, FileNotFoundError):
            return ""

        location = desc.pop("location", None)
        parts = [
            html.Span(
                location or "unnamed location",
                style={"color": "#555" if location else "#999", "marginRight": "8px"},
            )
        ]

        def _fmt(v):
            return f"{v:,.0f}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)

        detail = " · ".join(f"{k}: {_fmt(v)}" for k, v in desc.items() if v not in (None, ""))
        if detail:
            parts.append(html.Span(detail, style={"color": "#888"}))
        return html.Div(parts)

    @app.callback(
        Output("basin-review-flags", "children"),
        Output("basin-review-type", "options"),
        Output("basin-review-type", "value"),
        Output("basin1-area", "children"),
        Output("basin2-area", "children"),
        Output("basin3-area", "children"),
        Input("basin-review-site-dropdown", "value"),
    )
    def update_basin_review_info(site_uid):
        blank_areas = ("", "", "")
        if not site_uid:
            return "", _RADIO_OPTIONS, None, *blank_areas

        try:
            meta = access.get_basin_metadata()
        except FileNotFoundError:
            return "", _RADIO_OPTIONS, None, *blank_areas

        row = meta[meta["site_uid"] == site_uid]
        if row.empty:
            return "", _RADIO_OPTIONS, None, *blank_areas

        r = row.iloc[0]

        # Site column areas: v1/v2/v3 rows -> area1/area2/area3 from preferred_basin.csv (km2).
        site_areas = tuple(_fmt_area_km2(r.get(f"area{i}")) for i in (1, 2, 3))

        active_flags = [
            _FLAG_LABELS.get(c, c.removeprefix("flag_").replace("_", " "))
            for c in r.index
            if c.startswith("flag_")
            and not (isinstance(r.get(c), float) and math.isnan(r.get(c, float("nan"))))
            and bool(r.get(c, False))
        ]

        current_type = str(int(r["basin_type"])) if pd.notna(r.get("basin_type")) else "1"
        selection_mode = r.get("selection_mode", "auto")
        reviewed = bool(r.get("reviewed", False))

        flags_text = ", ".join(active_flags) if active_flags else "none"
        flags_div = html.Div(
            [
                html.Span(
                    f"v{current_type} · {selection_mode} · {'reviewed' if reviewed else 'unreviewed'}",
                    style={"color": "#555", "marginRight": "8px"},
                ),
                html.Span(
                    f"flags: {flags_text}",
                    style={"color": "#dc2626" if active_flags else "#16a34a"},
                ),
            ]
        )

        # v0 (auth) exists only where basin0 was delineated -- USGS sites. Disable it otherwise.
        has_v0 = pd.notna(r.get("dist_to_0")) and r.get("dist_to_0") != ""
        opts = [{**o, "disabled": o["value"] == "0" and not has_v0} for o in _RADIO_OPTIONS]
        return flags_div, opts, current_type, *site_areas

    @app.callback(
        Output("basin-review-confirm-status", "children"),
        Output("preferred-basin-version", "data"),
        Output("no-pin-popup", "style"),
        Output("no-pin-timer", "disabled"),
        Output("no-pin-timer", "n_intervals"),
        Input("basin-review-confirm-btn", "n_clicks"),
        Input("no-pin-timer", "n_intervals"),
        State("basin-review-site-dropdown", "value"),
        State("basin-review-type", "value"),
        State("region-geom", "data"),
        State("preferred-basin-version", "data"),
        prevent_initial_call=True,
    )
    def confirm_basin_review(_, timer_n, site_uid, basin_type, region_geom, version):
        # no-pin-timer also clears the status text, so the DEBUG "Debug Mode Off" flash auto-vanishes.
        if ctx.triggered_id == "no-pin-timer":
            return "", no_update, _NO_PIN_POPUP_HIDDEN, True, no_update

        # Lazy deployment safeguard (colors.DEBUG_MODE_OFF): the confirm button is inert -- flash a
        # brief self-clearing message (via no-pin-timer, ~2s) and make no changes to the dataset.
        if colors.DEBUG_MODE_OFF:
            return ["Debug Mode Off", html.Br(), "See 'colors.py'"], no_update, _NO_PIN_POPUP_HIDDEN, False, 0

        if not site_uid or not basin_type:
            return "Select a site and basin type first.", no_update, _NO_PIN_POPUP_HIDDEN, True, no_update

        # Custom v1 (snap) and Custom v2 (containing catchment) can be saved from a pin; both carry
        # an authoritative NLDI COMID. The D8 raster (v3) has no COMID -- a COMID must come from NLDI
        # rather than be inherited -- so it has no pin-save option and could never join the
        # COMID-keyed attribute tables.
        if basin_type in ("custom_v1", "custom_v2"):
            if not region_geom or region_geom.get("type") != "Point":
                return "", no_update, _NO_PIN_POPUP_VISIBLE, False, 0
            lng, lat = region_geom["coordinates"]
            try:
                # Pass the real uid so the saved basin carries site_uid + the AUTHORITATIVE comid
                # NLDI resolved from the corrected pin.
                delineate = delineate_basin_v1_for_pin if basin_type == "custom_v1" else delineate_basin_v2_for_pin
                geojson = delineate(lat, lng, site_uid=site_uid)
                access.update_basin(site_uid, {"selection_mode": "manual", "reviewed": True}, basin_geom=geojson)
                return f"Saved {site_uid} → {basin_type}.", (version or 0) + 1, _NO_PIN_POPUP_HIDDEN, True, no_update
            except Exception as e:
                return f"Error: {e}", no_update, _NO_PIN_POPUP_HIDDEN, True, no_update
        else:
            btype = int(basin_type)
            try:
                access.update_basin(
                    site_uid,
                    {
                        "basin_type": btype,
                        "basin_name": f"{site_uid}_basin{btype}.parquet",
                        "selection_mode": "manual",
                        "reviewed": True,
                    },
                )
                return f"Saved {site_uid} → v{btype}.", (version or 0) + 1, _NO_PIN_POPUP_HIDDEN, True, no_update
            except Exception as e:
                return f"Error: {e}", no_update, _NO_PIN_POPUP_HIDDEN, True, no_update
