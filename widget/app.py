import sys
from pathlib import Path

from widget.components import forecast_panel, info_panel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dash import Dash

from widget.layout import build_layout
from widget.components import map_panel

# suppress_callback_exceptions: map_panel's region-selection callback targets
# "edit-control", which only exists in the layout once area-selection mode
# has been chosen (it's created dynamically by map_panel's own callback).
app = Dash(__name__, suppress_callback_exceptions=True)
app.layout = build_layout()

map_panel.register_callbacks(app)
info_panel.register_callbacks(app)
forecast_panel.register_callbacks(app)


if __name__ == "__main__":
    app.run(debug=True)
