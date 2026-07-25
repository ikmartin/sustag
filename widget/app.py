from components import forecast_panel, info_panel, map_panel
from layout import build_layout
from dash import Dash

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
