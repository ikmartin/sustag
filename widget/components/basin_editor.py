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
from geo_utils import delineate_basin_for_pin, delineate_basin_v3_for_pin
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
            html.Div(id="basin-review-flags", style={"fontSize": "11px", "marginTop": "4px"}),
            # ── Two-column layout ─────────────────────────────────────────────
            html.Div(
                style={"display": "flex", "gap": "16px", "marginTop": "8px"},
                children=[
                    # Left: Set preferred basin (radio) + confirm
                    html.Div(
                        style={"flex": "1", "minWidth": "0"},
                        children=[
                            html.Div("Set preferred basin", style=_SUBSECTION_LABEL),
                            dcc.RadioItems(
                                id="basin-review-type",
                                options=[
                                    {"label": " v1 (NLDI)", "value": "1"},
                                    {"label": " v2 (auth)", "value": "2"},
                                    {"label": " v3 (D8)", "value": "3"},
                                    {"label": " Custom v1", "value": "custom_v1"},
                                    {"label": " Custom v3", "value": "custom_v3"},
                                ],
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
                    # Right: Basin display checkboxes (render callbacks live in map_panel)
                    html.Div(
                        style={"flex": "1", "minWidth": "0"},
                        children=[
                            html.Div("Basin display", style=_SUBSECTION_LABEL),
                            dcc.Checklist(
                                id="basin1-toggle",
                                options=[{"label": " v1", "value": "show"}],
                                value=[],
                                style=_CHECKBOX_STYLE,
                                labelStyle=_CHECKBOX_LABEL,
                            ),
                            dcc.Checklist(
                                id="basin2-toggle",
                                options=[{"label": " v2", "value": "show"}],
                                value=[],
                                style=_CHECKBOX_STYLE,
                                labelStyle=_CHECKBOX_LABEL,
                            ),
                            dcc.Checklist(
                                id="basin3-toggle",
                                options=[{"label": " v3", "value": "show"}],
                                value=[],
                                style=_CHECKBOX_STYLE,
                                labelStyle=_CHECKBOX_LABEL,
                            ),
                            dcc.Checklist(
                                id="pin-basin-v1-toggle",
                                options=[{"label": " v1 pin", "value": "show"}],
                                value=["show"],
                                style=_CHECKBOX_STYLE,
                                labelStyle=_CHECKBOX_LABEL,
                            ),
                            dcc.Checklist(
                                id="pin-basin-v3-toggle",
                                options=[{"label": " v3 pin", "value": "show"}],
                                value=[],
                                style=_CHECKBOX_STYLE,
                                labelStyle=_CHECKBOX_LABEL,
                            ),
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
        flag_cols = ["flag_area", "flag_river", "flag_not_contained", "flag_basin1_over_basin2"]
        if "on" in (flagged_only or []):
            flag_df = df[flag_cols].fillna(False)
            df = df[flag_df.any(axis=1)]
        if "on" in (unreviewed_only or []):
            df = df[~df["reviewed"].fillna(False).astype(bool)]

        opts = [{"label": r["site_uid"], "value": r["site_uid"]} for _, r in df.iterrows()]

        if ctx.triggered_id == "selected-site" and active_menu == "debug" and selected_sites and len(selected_sites) == 1:
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
        Output("basin-review-flags", "children"),
        Output("basin-review-type", "options"),
        Output("basin-review-type", "value"),
        Input("basin-review-site-dropdown", "value"),
    )
    def update_basin_review_info(site_uid):
        default_opts = [
            {"label": " v1 (NLDI)", "value": "1"},
            {"label": " v2 (auth)", "value": "2"},
            {"label": " v3 (D8)", "value": "3"},
            {"label": " Custom v1", "value": "custom_v1"},
            {"label": " Custom v3", "value": "custom_v3"},
        ]
        if not site_uid:
            return "", default_opts, None

        try:
            meta = access.get_basin_metadata()
        except FileNotFoundError:
            return "", default_opts, None

        row = meta[meta["site_uid"] == site_uid]
        if row.empty:
            return "", default_opts, None

        r = row.iloc[0]

        flag_labels = {
            "flag_area": "large area",
            "flag_river": "near river",
            "flag_not_contained": "not contained",
            "flag_basin1_over_basin2": "v1 over v2",
        }
        active_flags = [
            flag_labels[c]
            for c in flag_labels
            if not (isinstance(r.get(c), float) and math.isnan(r.get(c, float("nan")))) and bool(r.get(c, False))
        ]

        current_type = str(int(r["basin_type"])) if pd.notna(r.get("basin_type")) else "1"
        selection_mode = r.get("selection_mode", "auto")
        reviewed = bool(r.get("reviewed", False))

        flags_text = ", ".join(active_flags) if active_flags else "none"
        flags_div = html.Div([
            html.Span(
                f"v{current_type} · {selection_mode} · {'reviewed' if reviewed else 'unreviewed'}",
                style={"color": "#555", "marginRight": "8px"},
            ),
            html.Span(
                f"flags: {flags_text}",
                style={"color": "#dc2626" if active_flags else "#16a34a"},
            ),
        ])

        has_v2 = pd.notna(r.get("dist_to_2")) and r.get("dist_to_2") != ""
        opts = [
            {"label": " v1 (NLDI)", "value": "1"},
            {"label": " v2 (auth)", "value": "2", "disabled": not has_v2},
            {"label": " v3 (D8)", "value": "3"},
            {"label": " Custom v1", "value": "custom_v1"},
            {"label": " Custom v3", "value": "custom_v3"},
        ]
        return flags_div, opts, current_type

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

        # Lazy deployment safeguard (colors.DEBUG_MODE_ON): the confirm button is inert -- flash a
        # brief self-clearing message (via no-pin-timer, ~2s) and make no changes to the dataset.
        if colors.DEBUG_MODE_ON:
            return ["Debug Mode Off", html.Br(), "See 'colors.py'"], no_update, _NO_PIN_POPUP_HIDDEN, False, 0

        if not site_uid or not basin_type:
            return "Select a site and basin type first.", no_update, _NO_PIN_POPUP_HIDDEN, True, no_update

        if basin_type in ("custom_v1", "custom_v3"):
            if not region_geom or region_geom.get("type") != "Point":
                return "", no_update, _NO_PIN_POPUP_VISIBLE, False, 0
            lng, lat = region_geom["coordinates"]
            try:
                if basin_type == "custom_v1":
                    geojson = delineate_basin_for_pin(lat, lng)
                else:
                    geojson = delineate_basin_v3_for_pin(lat, lng)
                access.update_basin(site_uid, {"selection_mode": "manual", "reviewed": True}, basin_geom=geojson)
                return f"Saved {site_uid} → {basin_type}.", (version or 0) + 1, _NO_PIN_POPUP_HIDDEN, True, no_update
            except Exception as e:
                return f"Error: {e}", no_update, _NO_PIN_POPUP_HIDDEN, True, no_update
        else:
            btype = int(basin_type)
            try:
                access.update_basin(site_uid, {
                    "basin_type": btype,
                    "basin_name": f"{site_uid}_basin{btype}.parquet",
                    "selection_mode": "manual",
                    "reviewed": True,
                })
                return f"Saved {site_uid} → v{btype}.", (version or 0) + 1, _NO_PIN_POPUP_HIDDEN, True, no_update
            except Exception as e:
                return f"Error: {e}", no_update, _NO_PIN_POPUP_HIDDEN, True, no_update
