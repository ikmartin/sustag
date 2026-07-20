"""Forecast panel: drop a pin -> predicted nitrate + P(violation) timeseries at that point.

The UI (year dropdown, Run button, results, graph) lives in map_panel._build_forecast_section; this
module owns the run_forecast callback and the figure. The model seam is model_interface
(forecast_virtual_site -> the deploy build_virtual_basin/virtual_recipe/predict chain).

Two renderers of the same graph:
  * `_forecast_figure`  -> interactive Plotly figure shown on screen.
  * `_forecast_png`     -> static matplotlib PNG for the "Download figure" button (server-side, no
                           kaleido/browser dependency), with presentation-tuned titles + baked text.
"""

import io

import dash_leaflet as dl
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, dcc, html, no_update
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from plotly.subplots import make_subplots

import model_interface

_RED = "#c1121f"
_BLUE = "#3a94fa"


def _coord_label(lat, lon) -> str:
    """A human 'site' label for a dropped pin (no site name exists), e.g. '42.03°N, 93.62°W'."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.2f}°{ns}, {abs(lon):.2f}°{ew}"


def _beta_text(tau, days_over, recall, fdr, sep) -> str:
    """The alarm/expected text baked below the classification plot in the downloaded figure:

        Raises alarm when violation probability ≥ 0.32 –– 108 days predicted ≥ 10 mg/L
        Expected: catches ~65% of violations, ~42% of alarms false

    `sep` joins the two lines ('\\n' for matplotlib, '<br>' for Plotly). Untuned model -> line 1 only.
    """
    if tau is None:
        return f"{days_over} days predicted ≥ 10 mg/L"
    return (
        f"Raises alarm when violation probability ≥ {tau:.2f} –– {days_over} days predicted ≥ 10 mg/L"
        f"{sep}Expected: catches ~{recall:.0%} of violations, ~{fdr:.0%} of alarms false"
    )


def _forecast_figure(vf, year) -> go.Figure:
    """Interactive on-screen figure (unchanged by the download path)."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.14,
        specs=[[{"secondary_y": True}], [{"secondary_y": True}]],
        subplot_titles=(f"Predicted nitrate (mg/L) — {year}", "P(violation ≥ 10 mg/L)"),
    )
    # top: predicted nitrate (red) + basin precip (blue, twin)
    fig.add_trace(go.Scatter(x=vf.reg.index, y=vf.reg.to_numpy(), name="nitrate", line={"color": _RED, "width": 1.5}),
                  row=1, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=vf.precip.index, y=vf.precip.to_numpy(), name="precip", line={"color": _BLUE, "width": 1},
                             opacity=0.5), row=1, col=1, secondary_y=True)
    fig.add_hline(y=10, line_dash="dash", line_color=_RED, opacity=0.4, row=1, col=1)
    # bottom: alarm-day shading (behind), P(violation) (red) + basin precip (blue, twin)
    if vf.tau is not None and vf.alarms is not None:
        fig.add_trace(
            go.Scatter(x=vf.alarms.index, y=vf.alarms.astype(float).to_numpy(),
                       line={"width": 0, "shape": "hv"}, fill="tozeroy",
                       fillcolor="rgba(193,18,31,0.13)", name="alarm", showlegend=False, hoverinfo="skip"),
            row=2, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=vf.clf.index, y=vf.clf.to_numpy(), name="P(viol)", line={"color": _RED, "width": 1.5}),
                  row=2, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=vf.precip.index, y=vf.precip.to_numpy(), name="precip", line={"color": _BLUE, "width": 1},
                             opacity=0.5, showlegend=False), row=2, col=1, secondary_y=True)
    if vf.tau is not None:  # the decision threshold: alarm where P(violation) crosses this line
        fig.add_hline(y=vf.tau, line_dash="dot", line_color="#555", line_width=1, row=2, col=1,
                      annotation_text=f"τ={vf.tau:.2f}", annotation_font_size=9)

    fig.update_yaxes(title_text="mg/L", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="precip (in)", row=1, col=1, secondary_y=True, showgrid=False)
    fig.update_yaxes(range=[0, 1], row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="precip (in)", row=2, col=1, secondary_y=True, showgrid=False)
    fig.update_layout(height=340, margin={"t": 30, "b": 30, "l": 44, "r": 44}, showlegend=False,
                      font={"size": 10})
    return fig


def _forecast_payload(vf, year, site_label) -> dict:
    """JSON-safe snapshot of a forecast (stashed in a dcc.Store so the download callback can rebuild
    the figure without re-running the several-second forecast)."""
    idx = vf.reg.index

    def _clean(s):  # align to the nitrate index; NaN -> None so the Store's JSON stays valid
        s = s.reindex(idx)
        return [None if pd.isna(v) else float(v) for v in s.to_numpy()]

    return {
        "dates": [d.isoformat() for d in idx],
        "reg": _clean(vf.reg),
        "clf": _clean(vf.clf),
        "precip": _clean(vf.precip),
        "tau": None if vf.tau is None else float(vf.tau),
        "recall": None if vf.recall is None else float(vf.recall),
        "fdr": None if vf.fdr is None else float(vf.fdr),
        "days_over": int(vf.days_over),
        "site_label": site_label,
        "year": int(year),
    }


def _forecast_png(payload: dict) -> bytes:
    """Render the download figure with matplotlib at 2500x2000 px (4:5 height:width, high-res)."""
    dates = pd.to_datetime(payload["dates"])
    arr = lambda k: np.array([np.nan if v is None else v for v in payload[k]], dtype=float)  # noqa: E731
    reg, clf, precip = arr("reg"), arr("clf"), arr("precip")
    tau = payload["tau"]

    fig = Figure(figsize=(12.5, 10), dpi=200)  # 12.5*200 x 10*200 = 2500 x 2000 px
    FigureCanvasAgg(fig)
    ax1, ax2 = fig.subplots(2, 1, sharex=True)

    # top: predicted nitrate (red) + 10 mg/L line + basin precip (blue, twin)
    ax1.plot(dates, reg, color=_RED, lw=1.6)
    ax1.axhline(10, ls="--", color=_RED, alpha=0.4, lw=1.2)
    ax1.set_ylabel("mg/L")
    ax1.set_title("Predicted nitrate (mg/L)", fontsize=13)  # year suppressed (it's in the suptitle)
    ax1b = ax1.twinx()
    ax1b.plot(dates, precip, color=_BLUE, lw=1, alpha=0.5)
    ax1b.set_ylabel("precip (in)")

    # bottom: alarm shading (behind) + P(violation) (red) + tau line + basin precip (blue, twin)
    if tau is not None:
        ax2.fill_between(dates, 0, 1, where=(clf >= tau), step="post", color=_RED, alpha=0.13, lw=0)
        ax2.axhline(tau, ls=":", color="#555", lw=1.2)
        ax2.text(dates[-1], tau, f" τ={tau:.2f} ", fontsize=9, color="#555", va="bottom", ha="right")
    ax2.plot(dates, clf, color=_RED, lw=1.6)
    ax2.set_ylim(0, 1)
    ax2.set_ylabel("P(violation)")
    ax2.set_title("P(violation ≥ 10 mg/L)", fontsize=13)
    ax2b = ax2.twinx()
    ax2b.plot(dates, precip, color=_BLUE, lw=1, alpha=0.5)
    ax2b.set_ylabel("precip (in)")

    site = payload.get("site_label")
    title = f"Predictions for {site} using {payload['year']} example data" if site \
        else f"Predictions using {payload['year']} example data"
    fig.suptitle(title, fontsize=16, y=0.98)
    fig.text(0.5, 0.03,
             _beta_text(tau, payload["days_over"], payload["recall"], payload["fdr"], sep="\n"),
             ha="center", va="bottom", fontsize=11, color="#333")
    fig.subplots_adjust(top=0.91, bottom=0.135, hspace=0.28, left=0.07, right=0.93)

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


def register_callbacks(app):
    _HIDE_DL = {"display": "none"}

    @app.callback(
        Output("forecast-graph", "figure"),
        Output("forecast-graph", "style"),
        Output("forecast-results", "children"),
        Output("forecast-layer", "children"),
        Output("forecast-download-fig", "data"),
        Output("download-forecast-row", "style"),
        Input("run-forecast-button", "n_clicks"),
        State("region-geom", "data"),
        State("forecast-year", "value"),
        State("forecast-beta", "value"),
        prevent_initial_call=True,
    )
    def run_forecast(n_clicks, region_geom, year, beta):
        if not region_geom or region_geom.get("type") != "Point":
            return no_update, {"display": "none"}, \
                html.P("Drop a pin first (Pin drop selection mode).", style={"color": "#888"}), [], None, _HIDE_DL

        lng, lat = region_geom["coordinates"]
        try:
            vf = model_interface.forecast_virtual_site(lat, lng, int(year), beta=float(beta))
        except Exception as e:
            return no_update, {"display": "none"}, \
                html.P(f"Forecast failed: {type(e).__name__}: {e}", style={"color": "#c00", "fontSize": "12px"}), \
                [], None, _HIDE_DL

        peak_line = html.P(
            f"Peak P(violation): {vf.peak_prob:.0%} · {vf.days_over} days predicted ≥ 10 mg/L",
            style={"fontSize": "12px", "marginTop": "6px", "marginBottom": "2px"},
        )
        if vf.tau is not None:  # β operating point from the tuned classifier
            op_line = html.P(
                f"β={vf.beta:g} → alarm at P ≥ {vf.tau:.2f} · {int(vf.alarms.sum())} alarm days. "
                f"Expected: catches ~{vf.recall:.0%} of violations · ~{vf.fdr:.0%} of alarms false "
                f"(at ~{vf.base_rate:.0%} base-rate prevalence).",
                style={"fontSize": "12px", "marginTop": "0", "color": "#444"},
            )
            summary = html.Div([peak_line, op_line])
        else:
            summary = peak_line
        basin_layer = [dl.GeoJSON(data=vf.basin_geojson,
                                  options={"style": {"color": "#c026d3", "weight": 2, "fillOpacity": 0.05}})]
        # Stash a JSON-safe snapshot for the download; the on-screen figure returned below is unchanged.
        payload = _forecast_payload(vf, year, _coord_label(lat, lng))
        return (_forecast_figure(vf, year), {"display": "block", "height": "340px"}, summary, basin_layer,
                payload, {"display": "block", "marginTop": "6px"})

    @app.callback(
        Output("forecast-download", "data"),
        Input("download-forecast-button", "n_clicks"),
        State("forecast-download-fig", "data"),
        prevent_initial_call=True,
    )
    def download_forecast(n_clicks, payload):
        if not payload:
            return no_update
        png = _forecast_png(payload)
        return dcc.send_bytes(png, f"nitrate_forecast_{payload['year']}.png")
