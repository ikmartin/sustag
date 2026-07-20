"""Map panel: registers every map callback and re-exports layout() (so layout.py can still call
map_panel.layout()). Shared helpers/constants live in map_common; the UI builders in map_layout.
Split purely for navigability -- behavior is unchanged."""

from .map_common import *  # noqa: F401,F403
from .map_layout import layout, _crop_legend, _surplus_legend  # noqa: F401


def register_callbacks(app):
    _HIDDEN = {**_HELP_POPUP_STYLE, "display": "none"}

    def _latlon(center):
        # dash-leaflet reports center as [lat, lon] initially but {"lat":.., "lng":..} after a move.
        if isinstance(center, dict):
            return center.get("lat"), center.get("lng")
        return center[0], center[1]

    @app.callback(
        Output("map-view-readout", "children"),
        Input("map", "zoom"),
        Input("map", "center"),
    )
    def show_map_view(zoom, center):
        # Debug readout: current map zoom + center, live as the user pans/zooms.
        if zoom is None or not center:
            return "zoom —  ·  center —"
        lat, lon = _latlon(center)
        return f"zoom {zoom}  ·  center {lat:.4f}, {lon:.4f}"

    @app.callback(
        Output("map", "viewport"),
        Input("map-view-apply", "n_clicks"),
        State("map-zoom-input", "value"),
        State("map-center-input", "value"),
        State("map", "center"),
        State("map", "zoom"),
        prevent_initial_call=True,
    )
    def set_map_view(n_clicks, zoom_in, center_str, cur_center, cur_zoom):
        # Debug: fly the map to a directly-typed zoom / center. Blank fields keep the current value;
        # an unparseable center is ignored (no move).
        center = list(_latlon(cur_center)) if cur_center else None
        if center_str:
            try:
                lat, lon = (float(x) for x in center_str.replace(" ", "").split(","))
                center = [lat, lon]
            except (ValueError, TypeError):
                return no_update
        zoom = zoom_in if zoom_in is not None else cur_zoom
        if center is None and zoom is None:
            return no_update
        return {"center": center, "zoom": zoom, "transition": "flyTo"}

    for _popup_id, _btn_id, _close_id in [
        ("selection-help-popup", "selection-help-btn", "selection-help-close-btn"),
        ("graph-help-popup", "graph-help-btn", "graph-help-close-btn"),
        ("forecast-help-popup", "forecast-help-btn", "forecast-help-close-btn"),
        ("map-display-help-popup", "map-display-help-btn", "map-display-help-close-btn"),
        ("map-layers-help-popup", "map-layers-help-btn", "map-layers-help-close-btn"),
    ]:

        def _make_cb(close_id=_close_id, hidden=_HIDDEN):
            @app.callback(
                Output(_popup_id, "style"),
                Input(_btn_id, "n_clicks"),
                Input(_close_id, "n_clicks"),
                prevent_initial_call=True,
            )
            def _toggle(_, __, _cid=close_id, _h=hidden):
                return _h if ctx.triggered_id == _cid else _HELP_POPUP_STYLE

        _make_cb()

    _CLEAR_BTN_VISIBLE = {
        "fontSize": "11px",
        "color": "#aaa",
        "cursor": "pointer",
        "textDecoration": "underline",
        "marginTop": "6px",
        "display": "block",
    }
    _CLEAR_BTN_HIDDEN = {**_CLEAR_BTN_VISIBLE, "display": "none"}

    _TH = {
        "fontSize": "11px",
        "fontWeight": "600",
        "color": "#888",
        "textTransform": "uppercase",
        "letterSpacing": "0.05em",
        "padding": "2px 4px 5px 4px",
        "borderBottom": "2px dashed #ddd",
        "whiteSpace": "nowrap",
    }
    _TD = {"fontSize": "12px", "padding": "3px 4px", "verticalAlign": "middle"}

    @app.callback(
        Output("sites-selected-list", "children"),
        Output("clear-selection-btn", "style"),
        Output("debug-sites-table", "children"),
        Input("selected-site", "data"),
        Input("active-graph-site", "data"),
    )
    def update_sites_selected(selected_uids, active_uid):
        selected_uids = selected_uids or []

        header = html.Thead(
            html.Tr(
                [
                    html.Th("Site", style={**_TH, "textAlign": "left", "paddingLeft": "0"}),
                    html.Th("Sparsity (%)", style={**_TH, "textAlign": "center"}),
                    html.Th("Start", style={**_TH, "textAlign": "center"}),
                    html.Th("End", style={**_TH, "textAlign": "center"}),
                    html.Th("Lifespan", style={**_TH, "textAlign": "center"}),
                    html.Th("Basin Area (km²)", style={**_TH, "textAlign": "center"}),
                    html.Th("", style={**_TH, "borderBottom": "1px solid #ddd"}),
                ]
            )
        )

        if not selected_uids:
            empty = html.Table([header], style={"width": "100%", "borderCollapse": "collapse"})
            return empty, _CLEAR_BTN_HIDDEN, empty

        # one stats fetch for the whole table (indexed by site_uid), rather than get_stats per row
        try:
            stats_df = access.get_all_stats().set_index("site_uid")
        except Exception:
            stats_df = None

        rows = []
        for uid in selected_uids:
            if stats_df is not None and uid in stats_df.index:
                r = stats_df.loc[uid]
                sparsity = f"{r['nitrate_sparsity'] * 100:.1f}"
                start = str(r["start_date"])[:7]
                end = str(r["last_date"])[:7]
                lifespan = f"{r['lifespan']:.2f}"
            else:
                sparsity = start = end = lifespan = "—"

            try:
                basin_area = f"{access.get_basin_area(uid) / 1e6:,.0f}"  # get_basin_area is m²
            except Exception:
                basin_area = "—"

            rows.append(
                html.Tr(
                    [
                        html.Td(
                            html.Span(
                                uid,
                                id={"type": "graph-site-btn", "index": uid},
                                n_clicks=0,
                                style={
                                    "cursor": "pointer",
                                    "fontWeight": "bold" if uid == active_uid else "normal",
                                    "overflow": "hidden",
                                    "textOverflow": "ellipsis",
                                    "whiteSpace": "nowrap",
                                    "display": "block",
                                    "maxWidth": "90px",
                                },
                            ),
                            style={**_TD, "textAlign": "left", "paddingLeft": "0"},
                        ),
                        html.Td(sparsity, style={**_TD, "textAlign": "center"}),
                        html.Td(start, style={**_TD, "textAlign": "center"}),
                        html.Td(end, style={**_TD, "textAlign": "center"}),
                        html.Td(lifespan, style={**_TD, "textAlign": "center"}),
                        html.Td(basin_area, style={**_TD, "textAlign": "center"}),
                        html.Td(
                            html.Span(
                                "×",
                                id={"type": "remove-site-btn", "index": uid},
                                n_clicks=0,
                                style={"cursor": "pointer", "color": "#bbb", "fontWeight": "bold", "fontSize": "14px"},
                            ),
                            style={**_TD, "textAlign": "right", "paddingRight": "0"},
                        ),
                    ]
                )
            )

        table = html.Table([header, html.Tbody(rows)], style={"width": "100%", "borderCollapse": "collapse"})
        return table, _CLEAR_BTN_VISIBLE, table

    @app.callback(
        Output("selected-site", "data"),
        Input({"type": "iwqis-marker", "index": ALL}, "n_clicks"),
        Input({"type": "remove-site-btn", "index": ALL}, "n_clicks"),
        Input("clear-selection-btn", "n_clicks"),
        Input("edit-control", "geojson"),
        State("selected-site", "data"),
        State("selection-mode", "value"),
        State("active-menu", "data"),
        prevent_initial_call=True,
    )
    def update_selected_sites(marker_clicks, remove_clicks, _clear, edit_geojson, current, mode, active_menu):
        current = current or []
        triggered = ctx.triggered_id

        if triggered == "clear-selection-btn":
            return []

        if isinstance(triggered, dict) and triggered.get("type") == "iwqis-marker":
            if not any(marker_clicks):
                return no_update
            uid = triggered["index"]
            if active_menu == "debug":
                # Single selection: clicking the selected site deselects; clicking another replaces
                return [] if uid in current else [uid]
            return [s for s in current if s != uid] if uid in current else current + [uid]

        if isinstance(triggered, dict) and triggered.get("type") == "remove-site-btn":
            if not any(remove_clicks):
                return no_update
            uid = triggered["index"]
            return [s for s in current if s != uid]

        if triggered == "edit-control":
            if mode != "area" or not edit_geojson or not edit_geojson.get("features"):
                return no_update
            geom = edit_geojson["features"][-1]["geometry"]
            to_add = [s for s in _sites_in_polygon(geom) if s not in current]
            return current + to_add if to_add else no_update

        return no_update

    @app.callback(
        Output("active-graph-site", "data"),
        Input({"type": "graph-site-btn", "index": ALL}, "n_clicks"),
        Input("selected-site", "data"),
        State("active-graph-site", "data"),
        prevent_initial_call=True,
    )
    def update_active_graph_site(btn_clicks, selected_uids, current_active):
        selected_uids = selected_uids or []
        triggered = ctx.triggered_id

        if isinstance(triggered, dict) and triggered.get("type") == "graph-site-btn":
            if any(btn_clicks):
                return triggered["index"]

        # selected-site changed: switch to newly added site, or fall back to last
        if current_active not in selected_uids:
            return selected_uids[-1] if selected_uids else None
        # list grew — a new site was appended
        if selected_uids and selected_uids[-1] != current_active:
            return selected_uids[-1]
        return no_update

    @app.callback(
        Output("map-graph-overlay", "style"),
        Input("selected-site", "data"),
        Input("close-graph-btn", "n_clicks"),
        Input("graph-toggle", "value"),
        prevent_initial_call=True,
    )
    def toggle_graph_overlay(selected_uids, _, graph_toggle):
        if ctx.triggered_id == "close-graph-btn":
            return _GRAPH_OVERLAY_HIDDEN
        if "show" not in graph_toggle:
            return _GRAPH_OVERLAY_HIDDEN
        if selected_uids:
            return _GRAPH_OVERLAY_VISIBLE
        return no_update

    @app.callback(
        Output("tile-layer", "url"),
        Input("tile-selector", "value"),
    )
    def switch_tile_layer(value):
        return TILE_URLS[value]

    @app.callback(
        Output("iem-bbox-layer", "children"),
        Input("iem-bbox-toggle", "value"),
    )
    def render_iem_bbox(value):
        if "show" not in value:
            return []
        return [
            dl.Rectangle(
                bounds=[[38.8, -97.7], [45.3, -87.4]],
                pathOptions={"color": colors.IEM_BBOX["stroke"], "weight": 2, "dashArray": "6 4", "fillOpacity": 0},
            )
        ]

    @app.callback(
        Output("hydro-layer", "children"),
        Input("hydro-toggle", "value"),
    )
    def render_hydro(value):
        if "show" not in value:
            return []
        return [
            dl.GeoJSON(
                url="/assets/iowa_waterbodies.geojson",
                options={
                    "pane": "hydro-pane",
                    "style": {
                        "color": colors.HYDRO["stroke"],
                        "weight": 0.8,
                        "fillColor": colors.HYDRO["fill"],
                        "fillOpacity": 0.45,
                        "interactive": False,
                    },
                },
            ),
            dl.GeoJSON(
                url="/assets/iowa_flowlines.geojson",
                options={
                    "pane": "hydro-pane",
                    "style": {
                        "color": colors.HYDRO["stroke"],
                        "weight": 1.2,
                        "interactive": False,
                    },
                },
            ),
        ]

    @app.callback(
        Output("upstream-layer", "children"),
        Input("basin-preferred-toggle", "value"),
        Input("basin-all-toggle", "value"),
        Input("selected-site", "data"),
        Input("preferred-basin-version", "data"),
    )
    def render_upstream_area(preferred_toggle, all_toggle, selected_uids, _version):
        layers = []
        selected_uids = selected_uids or []

        if "show" in all_toggle:
            try:
                gdf = access.get_all_basins_union()
                layers.append(dl.GeoJSON(data=json.loads(gdf.to_json()), options={"style": colors.basin_style("preferred")}))
            except Exception:
                pass

        if "show" in preferred_toggle:
            for uid in selected_uids:
                try:
                    gdf = access.get_basin(uid)
                    layers.append(dl.GeoJSON(data=json.loads(gdf.to_json()), options={"style": colors.basin_style("preferred")}))
                except Exception:
                    pass

        return layers

    # basin1/2/3 render identically (differ only in basin type + colour) -> one factory, registered
    # once per type. basin_type=0 (preferred) is handled separately by render_upstream_area.
    def _register_basin_renderer(btype):
        @app.callback(
            Output(f"basin{btype}-layer", "children"),
            Input(f"basin{btype}-toggle", "value"),
            Input("selected-site", "data"),
        )
        def render_basin(toggle, selected_uids, _bt=btype):
            if "show" not in toggle:
                return []
            layers = []
            for uid in selected_uids or []:
                try:
                    gdf = access.get_basin(uid, type=_bt)
                    layers.append(dl.GeoJSON(data=json.loads(gdf.to_json()),
                                             options={"style": colors.basin_style(_bt)}))
                except FileNotFoundError:
                    pass
            return layers

    for _bt in (1, 2, 3):
        _register_basin_renderer(_bt)

    @app.callback(
        Output("pin-basin-layer", "children"),
        Input("pin-basin-v1-toggle", "value"),
        Input("region-geom", "data"),
    )
    def render_pin_basin_v1(toggle, region_geom):
        if "show" not in toggle:
            return []
        if not region_geom or region_geom.get("type") != "Point":
            return []
        lng, lat = region_geom["coordinates"]
        try:
            geojson = delineate_basin_for_pin(lat, lng)
            return [dl.GeoJSON(data=geojson, options={"style": colors.pin_basin_style("v1")})]
        except Exception:
            return []

    @app.callback(
        Output("pin-basin-v3-layer", "children"),
        Input("pin-basin-v3-toggle", "value"),
        Input("region-geom", "data"),
    )
    def render_pin_basin_v3(toggle, region_geom):
        if "show" not in toggle:
            return []
        if not region_geom or region_geom.get("type") != "Point":
            return []
        lng, lat = region_geom["coordinates"]
        try:
            geojson = delineate_basin_v3_for_pin(lat, lng)
            return [dl.GeoJSON(data=geojson, options={"style": colors.pin_basin_style("v3")})]
        except Exception:
            return []

    @app.callback(
        Output("grid-color-legend", "children"),
        Input("rain-grid-toggle", "value"),
        Input("grid-color-mode", "value"),
        Input("active-graph-site", "data"),
    )
    def render_grid_color_legend(rain_toggle, color_mode, active_uid):
        # only when the rain grid is actually showing (toggle on + a site to draw it for)
        if "show" not in (rain_toggle or []) or not active_uid:
            return []
        return _crop_legend() if color_mode == "crop" else _surplus_legend()

    @app.callback(
        Output("rain-grid-layer", "children"),
        Input("rain-grid-toggle", "value"),
        Input("active-graph-site", "data"),
        Input("surplus-year-slider", "value"),
        Input("grid-color-mode", "value"),
    )
    def render_rain_grid(toggle, active_uid, year, color_mode):
        if "show" not in toggle or not active_uid:
            return []
        try:
            features = _rain_grid_features(active_uid, year, color_mode or "surplus")
        except FileNotFoundError:
            return _rain_grid_dots(active_uid)  # no rain grid built yet → centroid dots
        return [
            dl.GeoJSON(
                data=features,
                style=_GRID_STYLE_JS,
                hoverStyle=_GRID_HOVER_JS,
                onEachFeature=_GRID_ONEACH_JS,
                zoomToBounds=False,
                pane="rain-grid-pane",
            )
        ]

    @app.callback(
        Output("iowa-surplus-image-overlay", "url"),
        Output("iowa-surplus-image-overlay", "bounds"),
        Output("iowa-surplus-image-overlay", "opacity"),
        Input("iowa-surplus-heatmap-toggle", "value"),
        Input("surplus-year-slider", "value"),
        Input("surplus-opacity-slider", "value"),
    )
    def render_iowa_surplus_heatmap(toggle, year, opacity):
        if "show" not in toggle:
            return _TRANSPARENT_PNG, _IOWA_BOUNDS, 0
        try:
            url, bounds = surplus_viz.get_iowa_surplus_image_buffer(year)
        except Exception as e:
            print(f"[iowa surplus heatmap] {type(e).__name__}: {e}")
            return _TRANSPARENT_PNG, _IOWA_BOUNDS, 0
        return url, bounds, opacity

    @app.callback(
        Output("iwqis-layer", "children"),
        Input("selected-site", "data"),
        Input("basin-review-flagged-only", "value"),
        Input("basin-review-unreviewed-only", "value"),
    )
    def render_iwqis_sites(selected_uids, flagged_only, unreviewed_only):
        visible_uids = None
        if "on" in (flagged_only or []) or "on" in (unreviewed_only or []):
            try:
                meta = access.get_basin_metadata()
                df = meta.copy()
                flag_cols = ["flag_area", "flag_river", "flag_not_contained", "flag_basin1_over_basin2"]
                if "on" in (flagged_only or []):
                    flag_df = df[flag_cols].fillna(False)
                    df = df[flag_df.any(axis=1)]
                if "on" in (unreviewed_only or []):
                    df = df[~df["reviewed"].fillna(False).astype(bool)]
                visible_uids = set(df["site_uid"])
            except FileNotFoundError:
                pass
        return make_iwqis_markers(selected_uids or [], visible_uids=visible_uids)

    @app.callback(
        Output("fake-sites-layer", "children"),
        Input("fake-sites-toggle", "value"),
    )
    def render_fake_bad_sites(toggle):
        # PRESENTATION-ONLY: 77 fake sensor dots along rivers (60/40 green/blue). Off by default.
        return make_fake_bad_site_markers() if "show" in (toggle or []) else []

    @app.callback(
        Output("area-tool-container", "style"),
        Input("selection-mode", "value"),
    )
    def update_selection_tool(mode):
        if mode == "area":
            return {"display": "flex", "gap": "4px", "alignItems": "center"}
        return {"display": "none"}

    @app.callback(
        Output("edit-control", "drawToolbar"),
        Output("edit-control", "editToolbar"),
        Input("draw-rect-btn", "n_clicks"),
        Input("draw-poly-btn", "n_clicks"),
        Input("draw-delete-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def trigger_draw_action(*_):
        triggered = ctx.triggered_id
        if triggered == "draw-rect-btn":
            return {"mode": "rectangle"}, no_update
        if triggered == "draw-poly-btn":
            return {"mode": "polygon"}, no_update
        if triggered == "draw-delete-btn":
            # "clear all" auto-clicks the Clear All action button after enabling
            # remove mode, immediately deleting all drawn shapes.
            return no_update, {"mode": "remove", "action": "clear all"}
        return no_update, no_update

    @app.callback(
        Output("region-geom", "data"),
        Output("marker-layer", "children"),
        Input("map", "clickData"),
        Input("edit-control", "geojson"),
        State("selection-mode", "value"),
        prevent_initial_call=True,
    )
    def update_region_geom(click_data, edit_geojson, mode):
        triggered = ctx.triggered_id

        if triggered == "map":
            if mode != "pin" or not click_data:
                return no_update, no_update
            latlng = click_data["latlng"]
            lat, lng = latlng["lat"], latlng["lng"]
            geom = {"type": "Point", "coordinates": [lng, lat]}
            marker = dl.Marker(
                position=[lat, lng],
                children=dl.Tooltip(f"{lat:.6f}, {lng:.6f}", permanent=True, direction="top"),
            )
            return geom, [marker]

        if triggered == "edit-control":
            if mode != "area" or not edit_geojson or not edit_geojson.get("features"):
                return no_update, no_update
            geom = edit_geojson["features"][-1]["geometry"]
            return geom, []

        return no_update, no_update

    @app.callback(
        Output("explore-menu-content", "style"),
        Output("forecast-menu-content", "style"),
        Output("debug-menu-content", "style"),
        Output("menu-tab-explore", "style"),
        Output("menu-tab-forecast", "style"),
        Output("menu-tab-debug", "style"),
        Output("active-menu", "data"),
        Output("selected-site", "data", allow_duplicate=True),
        Input("menu-tab-explore", "n_clicks"),
        Input("menu-tab-forecast", "n_clicks"),
        Input("menu-tab-debug", "n_clicks"),
        State("selected-site", "data"),
        State("active-graph-site", "data"),
        prevent_initial_call=True,
    )
    def switch_menu(_, __, ___, selected_sites, active_site):
        _show = {"display": "block"}
        _hide = {"display": "none"}
        if ctx.triggered_id == "menu-tab-debug":
            trimmed = [active_site] if active_site else (selected_sites[:1] if selected_sites else [])
            return _hide, _hide, _show, _MENU_TAB_INACTIVE, _MENU_TAB_INACTIVE, _MENU_TAB_ACTIVE, "debug", trimmed
        if ctx.triggered_id == "menu-tab-forecast":
            return _hide, _show, _hide, _MENU_TAB_INACTIVE, _MENU_TAB_ACTIVE, _MENU_TAB_INACTIVE, "forecast", no_update
        return _show, _hide, _hide, _MENU_TAB_ACTIVE, _MENU_TAB_INACTIVE, _MENU_TAB_INACTIVE, "explore", no_update

    @app.callback(
        Output("active-area-tool", "data"),
        Input("draw-rect-btn", "n_clicks"),
        Input("draw-poly-btn", "n_clicks"),
        Input("selection-mode", "value"),
        prevent_initial_call=True,
    )
    def update_active_area_tool(_rect, _poly, mode):
        if ctx.triggered_id == "selection-mode":
            return "rect" if mode == "area" else None
        if ctx.triggered_id == "draw-rect-btn":
            return "rect"
        if ctx.triggered_id == "draw-poly-btn":
            return "poly"
        return no_update

    @app.callback(
        Output("draw-rect-btn", "style"),
        Output("draw-poly-btn", "style"),
        Input("active-area-tool", "data"),
    )
    def update_area_btn_styles(active_tool):
        return (
            _DRAW_BTN_ACTIVE if active_tool == "rect" else _DRAW_BTN_INACTIVE,
            _DRAW_BTN_ACTIVE if active_tool == "poly" else _DRAW_BTN_INACTIVE,
        )

    basin_editor.register_callbacks(app)
