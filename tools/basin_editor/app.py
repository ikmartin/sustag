"""The basin editor PoC: lat/lon in, four basins and the sensors inside them out.

    python tools/basin_editor/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dash_leaflet as dl
import pandas as pd
from dash import Dash, Input, Output, State, dcc, html

from basin_editor import delineate

COLORS = {"basin1": "#0d9488", "basin2": "#7c3aed", "basin3": "#b45309", "basin0": "#1f5fa8"}


def _poly_layer(result: dict):
    if result.get("polygon") is None:
        return None
    import shapely

    gj = shapely.geometry.mapping(shapely.simplify(result["polygon"], 0.001))
    return dl.GeoJSON(data={"type": "Feature", "geometry": gj},
                      style=dict(color=COLORS[result["method"]], weight=2, fillOpacity=0.06))


def main():
    app = Dash(__name__, title="basin editor")
    app.layout = html.Div([
        html.H3("basin editor — delineate at a point; basins live in YOUR project, not the data stores"),
        html.Div([
            dcc.Input(id="lat", placeholder="lat", type="number", value=41.606406),
            dcc.Input(id="lon", placeholder="lon", type="number", value=-91.615722),
            dcc.Input(id="usgs", placeholder="USGS id (optional, basin0 via live NLDI)", value=""),
            html.Button("delineate", id="go", n_clicks=0),
        ], style={"display": "flex", "gap": "8px"}),
        html.Pre(id="readout", style={"fontFamily": "monospace"}),
        dl.Map(id="map", children=[dl.TileLayer()], center=[41.5, -91.5], zoom=9,
               style={"height": "620px"}),
    ], style={"margin": "16px", "fontFamily": "system-ui"})

    @app.callback(Output("map", "children"), Output("map", "center"), Output("readout", "children"),
                  Input("go", "n_clicks"), State("lat", "value"), State("lon", "value"),
                  State("usgs", "value"), prevent_initial_call=True)
    def _run(_n, lat, lon, usgs):
        children = [dl.TileLayer()]
        lines = []
        results = [delineate.basin1(lat, lon), delineate.basin2(lat, lon)]
        results.append(delineate.basin3(lat, lon, expected_km2=results[0].get("area_km2")))
        if str(usgs or "").strip():
            results.append(delineate.basin0(str(usgs).strip()))
        inside = None
        for r in results:
            area = r.get("area_km2")
            vaa = r.get("vaa_km2")
            lines.append(f"{r['method']:7s} {r['status']:22s} "
                         f"{'' if area is None else f'{area:>12,.1f} km2'}"
                         f"{'' if vaa is None else f'   (VAA {vaa:,.1f})'}")
            layer = _poly_layer(r)
            if layer is not None:
                children.append(layer)
            if r["method"] == "basin1" and r.get("polygon") is not None:
                inside = delineate.sensors_within(r["polygon"])
        if inside is not None and len(inside):
            lines.append(f"sensors inside basin1: {len(inside)} "
                         f"{inside.site_type.value_counts().to_dict()}")
            for s in inside.itertuples():
                children.append(dl.CircleMarker(
                    center=[s.lat, s.lon], radius=5, color="#d62728", fill=True,
                    children=dl.Tooltip(f"{s.site_uid} | {s.site_type} | {s.station_name}")))
        children.append(dl.CircleMarker(center=[lat, lon], radius=7, color="#000", fill=True,
                                        children=dl.Tooltip("your point")))
        return children, [lat, lon], "\n".join(lines)

    app.run(debug=False, port=8062)


if __name__ == "__main__":
    main()
