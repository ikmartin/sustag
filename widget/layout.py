"""Top-level page layout.

The viewport is divided into two columns: map (80 %) on the left and the
tools panel (20 %) on the right.  Both fill the full screen height.
"""

from dash import html, dcc

from components import map_panel


def build_layout():
    return html.Div(
        style={"fontFamily": "sans-serif", "height": "100vh", "overflow": "hidden"},
        children=[
            dcc.Store(id="region-geom"),
            dcc.Store(id="selected-site"),
            dcc.Store(id="active-graph-site"),
            # region-info-panel is populated by info_panel callbacks; kept hidden here
            html.Div(id="region-info-panel", style={"display": "none"}),
            map_panel.layout(),
        ],
    )
