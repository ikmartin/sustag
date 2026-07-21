import pandas as pd
import plotly.express as px

GRID_PATH = "iowa_grid_lookup.parquet"
PANEL_PATH = "nitrogen_surplus_iowa_grid_panel.parquet"


def plot_iowa_year(
    year,
    value_col="surplus_kgha",
    grid_path=GRID_PATH,
    panel_path=PANEL_PATH,
    downsample=1,
    clip_quantiles=(0.01, 0.99),
):
    """
    Map nitrogen surplus across Iowa's 250m grid for a single year.

    year:            the year to plot (matches the 'year' column in the panel parquet)
    value_col:       column to color by, e.g. "surplus_kgha" or "total_kg_N"
    downsample:      keep every Nth pixel for faster rendering (1 = full resolution,
                      ~2-2.5M points; try 3-5 for smoother interaction)
    clip_quantiles:  clip the color scale to these percentiles so a few extreme
                      pixels don't wash out the color range for everything else
    """
    grid = pd.read_parquet(grid_path)

    # Push the year filter down to the parquet reader so the whole panel
    # (all years) never has to be loaded into memory at once.
    panel = pd.read_parquet(panel_path, filters=[("year", "==", year)])
    if panel.empty:
        raise ValueError(f"No rows found for year={year} in {panel_path}")

    df = panel.merge(grid, on="pixel_id", how="inner")

    if downsample > 1:
        df = df.iloc[::downsample]

    lo, hi = df[value_col].quantile(clip_quantiles)

    fig = px.scatter_mapbox(
        df,
        lat="lat",
        lon="lon",
        color=value_col,
        color_continuous_scale="YlOrRd",
        range_color=(lo, hi),
        mapbox_style="open-street-map",  # free basemap, no API token needed
        zoom=6,
        center={"lat": 42.0, "lon": -93.5},  # roughly the center of Iowa
        height=750,
        opacity=0.8,
        title=f"Nitrogen surplus — Iowa, {year}",
        labels={value_col: "Surplus (kg N/ha)"},
    )
    fig.update_traces(marker=dict(size=4))
    fig.update_layout(margin=dict(l=0, r=0, t=40, b=0))
    return fig


if __name__ == "__main__":
    # Full resolution (downsample=1) is ~2-2.5M points, which can feel sluggish
    # to pan/zoom in a browser. Downsample while exploring, then drop to 1 for
    # a final high-res export.
    fig = plot_iowa_year(2005, downsample=500)
    fig.show()
    # fig.write_html("iowa_surplus_2005_map.html")