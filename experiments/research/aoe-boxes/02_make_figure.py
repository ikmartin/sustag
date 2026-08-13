"""Render the area-of-interest map: sensors, reachable network, and cluster boxes, over a sweep of max_dist_from_sensor.

    python experiments/research/aoe-boxes/02_make_figure.py

DESIGN NOTES, carried from notes/graphics/make_usgs_nitrate_map.py and extended:
  * ONE SERIES, still. The reach tracery and the sensor dots are the same entity class -- the network we care about, and the points on it where somebody measures -- so they share the series hue and are separated by MARK SIZE plus the surface ring, not by a second colour. A second categorical hue here would fail the chroma floor and would also be a lie about the data.
  * The dotted rectangles are CHROME, not a series: they are the bounding box a pipeline would actually download, drawn in the recessive muted ink. The gap between the tracery and its rectangle is the point of the figure -- it shows how much of each box is empty.
  * Albers Equal Area (EPSG:5070). On plate-carree the northern clusters stretch and the Corn Belt reads as less dense than it is.
  * Small multiples rather than nested rings on one map: by 250 km the regions overlap heavily and stacked outlines are unreadable.
  * Marker size is re-tuned for the quarter-size panels; the size/ring pairing was deliberately tuned in the parent script and does not survive a naive copy.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]
_SCRATCH = Path("/private/tmp/claude-501/-Users-isaac-dev-sustag/"
                "4b2640b0-7d62-4647-b7b9-5138f4af4269/scratchpad/aoe")
_GFX = _ROOT / "notes" / "graphics"
OUT = _GFX / "aoe_boundaries_conus.png"

# dataviz reference palette, light mode.
SURFACE, INK, INK_2, MUTED, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#c3c2b7"
SERIES = "#2a78d6"  # categorical slot 1 -- the only series on the figure
ALBERS = "EPSG:5070"


def main() -> None:
    with open(_SCRATCH / "per_distance.pkl", "rb") as f:
        blob = pickle.load(f)
    per, lat, lon, sensors = blob["per_distance"], blob["lat"], blob["lon"], blob["sensors"]
    clusters = pd.read_csv(_HERE / "data" / "aoe_clusters.csv")

    states = gpd.read_file(_GFX / "_us_states.geojson")
    states = states[~states.name.isin(["Alaska", "Hawaii", "Puerto Rico"])].to_crs(ALBERS)
    pts = gpd.GeoDataFrame(sensors, geometry=gpd.points_from_xy(sensors.lon, sensors.lat),
                           crs=4326).to_crs(ALBERS)

    mpl.rcParams.update({"font.family": "DejaVu Sans", "figure.dpi": 200})
    n_panels = len(per)
    nrows = (n_panels + 1) // 2
    fig, axes = plt.subplots(nrows, 2, figsize=(12.4, 1.0 + 4.0 * nrows))
    fig.patch.set_facecolor(SURFACE)
    for extra in axes.ravel()[n_panels:]:
        extra.set_axis_off()

    for ax, D in zip(axes.ravel(), sorted(per)):
        ax.set_facecolor(SURFACE)
        states.plot(ax=ax, facecolor="#efeeea", edgecolor=AXIS, linewidth=0.45, zorder=1)

        reaches = np.unique(np.concatenate(per[D]["sets"])) if per[D]["sets"] else np.array([], int)
        if len(reaches):
            rg = gpd.GeoSeries(gpd.points_from_xy(lon[reaches], lat[reaches]), crs=4326).to_crs(ALBERS)
            # Alpha falls as the reach count rises: at 250 km a fixed alpha saturates into a solid
            # blob that swallows the sensor dots, and the density stops being readable.
            a = float(np.clip(9_000 / max(len(reaches), 1), 0.10, 0.42))
            ax.scatter(rg.x, rg.y, s=0.18, c=SERIES, alpha=a, linewidths=0, zorder=2, rasterized=True)

        sub = clusters[clusters.dist_km == D]
        boxes = gpd.GeoDataFrame(
            sub, geometry=gpd.GeoSeries.from_wkt(
                sub.bbox.map(lambda b: (lambda v: f"POLYGON(({v[0]} {v[1]},{v[2]} {v[1]},"
                                                  f"{v[2]} {v[3]},{v[0]} {v[3]},{v[0]} {v[1]}))")(eval(b)))),
            crs=4326).to_crs(ALBERS)
        boxes.plot(ax=ax, facecolor=MUTED, alpha=0.055, edgecolor="none", zorder=3)
        boxes.boundary.plot(ax=ax, edgecolor=MUTED, linewidth=0.85, linestyle=(0, (2.2, 1.8)), zorder=4)

        # Sensors last and ringed, so they stay findable inside the densest tracery.
        ax.scatter(pts.geometry.x, pts.geometry.y, s=11, c=SERIES,
                   edgecolor=SURFACE, linewidths=0.55, zorder=5)

        n_cl, n_single = len(sub), int((sub.n_sensors == 1).sum())
        # Titles ABOVE the axes, not inside them -- panel-internal text collided with
        # Washington state and with the dotted boxes at every distance.
        ax.set_title(f"max_dist_from_sensor = {D:.0f} km", loc="left", pad=13,
                     fontsize=10.5, color=INK, weight="bold")
        ax.text(0.0, 1.006,
                f"{n_cl} regions · {n_single} single-sensor · "
                f"{sub.box_area_km2.sum()/1e6:.2f}M km² of box",
                transform=ax.transAxes, fontsize=8.2, color=INK_2, va="bottom")
        ax.set_axis_off()
        ax.margins(0.01)

    # Header/footer margins are fixed in INCHES and converted to figure fraction, so adding panel
    # rows grows the plot area rather than squeezing the title block.
    H = fig.get_figheight()
    fig.text(0.028, 1 - 0.30 / H, "Areas of interest by flow distance from a nitrate sensor",
             fontsize=15.5, color=INK, weight="bold", va="top")
    fig.text(0.028, 1 - 0.62 / H,
             "Two sensors share a region when their reachable stretches of river meet. Fine tracery is the "
             "reach network within the stated flow distance of a sensor;\nsolid dots are the "
             f"{len(sensors)} sensors themselves; dotted rectangles are the bounding box a pipeline would "
             "download — the space between tracery and rectangle is area paid for and unused.",
             fontsize=8.8, color=INK_2, va="top", linespacing=1.5)
    fig.text(0.028, 0.16 / H,
             "Distance is measured along the NHDPlus V2 network, upstream and downstream, not straight-line. "
             "Reach coordinates from the NWM v3.0 retrospective; topology and lengths from NHDPlus VAA.  "
             "Equal-area projection (EPSG:5070).",
             fontsize=7.2, color=MUTED, va="bottom")

    # 1.85in of header clears the two-line subtitle AND the first row's panel title + its
    # annotation line, which together stand ~0.5in above the axes. hspace clears the later rows'.
    fig.subplots_adjust(left=0.012, right=0.988, top=1 - 1.85 / H, bottom=0.36 / H,
                        wspace=0.01, hspace=0.15)
    fig.savefig(OUT, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.22)
    print(f"wrote {OUT.relative_to(_ROOT)}")
    for D in sorted(per):
        sub = clusters[clusters.dist_km == D]
        print(f"  {D:6.0f} km  {len(sub):3d} regions  {int((sub.n_sensors==1).sum()):3d} singletons  "
              f"{sub.box_area_km2.sum()/1e6:.2f}M km²")


if __name__ == "__main__":
    main()
