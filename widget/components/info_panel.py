"""Info panel: displays data about the currently selected site or region.

Reacts to state written by the map panel:

- `active-graph-site`: the site_uid of a monitoring site selected on the map
  or from the Sites Selected table.  Drives the timeseries graph via
  `_build_timeseries_figure`, which plots nitrate concentration on the primary
  y-axis with a secondary y-axis reserved for rainfall (placeholder, uncomment
  once rain parquets are available).

- `region-geom`: the selected point or area geometry.  Currently displays the
  coordinates of a point selection.  Area-based information is not yet
  implemented.

Neither callback knows anything about the forecast model — this panel is
purely descriptive.
"""

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, html, dash_table, no_update

from src.data import access


def _render_tables(pairs):
    """pairs: list of (title, df). Returns Dash elements, or a placeholder."""
    sections = []
    for title, df in pairs:
        if df is None or df.empty:
            continue
        if title:
            sections.append(html.H3(title, style={"marginTop": "20px", "marginBottom": "4px"}))
        sections.append(
            dash_table.DataTable(
                data=df.to_dict("records"),
                columns=[{"name": c, "id": c} for c in df.columns],
                style_table={"overflowX": "auto", "marginTop": "8px"},
                style_cell={"textAlign": "left", "padding": "6px 12px", "fontSize": "13px"},
                style_header={"fontWeight": "bold", "borderBottom": "2px solid #ddd"},
                page_size=20,
            )
        )
    if sections:
        return html.Div(sections)
    return html.P("No data for this location.", style={"color": "#888"})


_SEASON_MONTHS_DAYS = [(3, 21), (6, 21), (9, 21), (12, 21)]
_BAR_THRESH = 200  # if there are more than this many bars, do a scatter for rain


def _build_timeseries_figure(
    site_uid: str, interval: str, agg_func_water: str, agg_func_rain: str, show_seasons: bool = False
) -> go.Figure:
    """Build the site timeseries figure with nitrate and rain traces.

    Nitrate is plotted on the primary y-axis.  A secondary y-axis is reserved
    for rainfall — uncomment the rain block below once the rain parquets are
    ready for the sites you care about.
    """
    fig = go.Figure()
    try:
        precip_col = f"precip_{interval.lower()}"
        # Basin-mean daily precip, then resample to the interval. (Basin-mean-then-resample
        # == the old per-cell-resample-then-spatial-mean for the linear sum/mean aggregators.)
        w = access.get_weather(site_uid)
        daily = w.groupby("date")["precip_in_1d"].mean()
        daily.index = pd.DatetimeIndex(daily.index)
        rain = daily.resample(interval).agg(agg_func_rain).rename(precip_col).reset_index()
        if rain.shape[0] < _BAR_THRESH:
            fig.add_trace(
                go.Bar(
                    x=rain["date"],
                    y=rain[precip_col],
                    name="Precip (in)",
                    yaxis="y2",
                    marker_color="#3a94fa",
                    opacity=0.5,
                )
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=rain["date"],
                    y=rain[precip_col],
                    name="Precip (in)",
                    yaxis="y2",
                    opacity=0.5,
                    line={"color": "#3a94fa", "width": 1},
                )
            )
        fig.update_layout(
            yaxis2=dict(
                title="Precipitation (in)",
                overlaying="y",
                side="right",
                showgrid=False,
            ),
        )
    except FileNotFoundError:
        pass  # no rain data yet for this site — silently omit the trace

    # ── nitrate trace ─────────────────────────────────────────────────────────
    nitrate = access.aggregate_by_interval(site_uid=site_uid, interval=interval, agg_func=agg_func_water)
    fig.add_trace(
        go.Scatter(
            x=nitrate.index,
            y=nitrate.values,
            name="Nitrate (mg/L)",
            yaxis="y1",
            line={"color": "#6b21a8", "width": 1.5},
        )
    )

    fig.update_layout(
        yaxis={"title": "Nitrate (mg/L)"},
        xaxis={"title": None},
        legend={"orientation": "h", "y": -0.15},
        margin={"t": 20, "b": 40, "l": 50, "r": 50},
        xaxis_rangeselector=dict(
            buttons=[
                dict(count=1, label="1M", step="month", stepmode="backward"),
                dict(count=3, label="3M", step="month", stepmode="backward"),
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(count=3, label="3Y", step="year", stepmode="backward"),
                dict(step="all", label="All"),
            ]
        ),
    )
    if show_seasons:
        start_year = nitrate.index.min().year
        end_year = nitrate.index.max().year
        for year in range(start_year, end_year + 1):
            for month, day in _SEASON_MONTHS_DAYS:
                fig.add_vline(
                    x=pd.Timestamp(year, month, day, tz="UTC").isoformat(),
                    line_width=1,
                    line_color="black",
                    opacity=0.3,
                )

    fig.update_yaxes(fixedrange=True)
    fig.add_annotation(
        text=site_uid,
        xref="paper",
        yref="paper",
        x=1,
        y=1,
        xanchor="right",
        yanchor="bottom",
        showarrow=False,
        font={"size": 11, "color": "#888"},
    )

    return fig


def region_info_layout():
    return html.Div(id="region-info-panel", style={"padding": "16px 0"})


def register_callbacks(app):
    @app.callback(
        Output("timeseries-graph", "figure"),
        Input("active-graph-site", "data"),
        Input("aggregate-interval", "value"),
        Input("agg-func-water", "value"),
        Input("agg-func-rain", "value"),
        Input("graph-toggle", "value"),
        prevent_initial_call=True,
    )
    def update_timeseries(active_uid, interval, agg_func_water, agg_func_rain, graph_toggle):
        if not active_uid:
            return go.Figure()
        return _build_timeseries_figure(
            active_uid,
            interval=interval or "1D",
            agg_func_water=agg_func_water or "mean",
            agg_func_rain=agg_func_rain or "sum",
            show_seasons="seasons" in (graph_toggle or []),
        )

    @app.callback(
        Output("mapunit-layer", "children"),
        Output("region-info-panel", "children"),
        Input("region-geom", "data"),
        prevent_initial_call=True,
    )
    def update_region_info(region_geom):
        if not region_geom:
            return no_update, no_update

        geom_type = region_geom.get("type")

        if geom_type == "Point":
            lng, lat = region_geom["coordinates"]

            coord_row = html.Div(
                [html.Strong("Selected point: "), html.Code(f"{lat:.6f}, {lng:.6f}")],
                style={"marginBottom": "12px"},
            )

            return [], html.Div([coord_row])

        # Area selection (Polygon/MultiPolygon from rectangle or polygon draw)
        coord_row = html.Div(
            [html.Strong("Selected area")],
            style={"marginBottom": "12px"},
        )
        placeholder = html.P(
            "Area-based information is not yet implemented.",
            style={"color": "#888"},
        )
        return [], html.Div([coord_row, placeholder])
