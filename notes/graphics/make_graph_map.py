"""The sensor graph chunk 4 builds: every valid node, and every edge between them.

    python notes/graphics/make_graph_map.py

LEFT is the whole AOE. RIGHT is Iowa and its neighbours, where the network is dense enough that the left panel can only show a smudge -- the same data, no filtering, just an extent.

WHAT AN EDGE MEANS. M -> N because water leaving M reaches N without passing another node. It is a FLOW relation, not a distance one: the line is drawn straight between two gauges but the water follows the river, so a single edge can span several hundred kilometres of the Mississippi with nothing instrumented in between. Read the lines as "these two are hydrologically adjacent", never as a river.

ISOLATED DOTS ARE THE MINORITY, 24 of 350. A node with no edge means no other sensor lies anywhere on its flow path, up or down. Most nodes do connect, and 204 of them have nothing above them -- they are the headwaters of the instrumented network, which is why the fans all point one way.

THE FANS ARE THE POINT, AND THEY ARE REAL. Fourteen nodes flow directly into the Mississippi at Winona, thirteen into the Missouri at St. Joseph, thirteen into the Ohio at Olmsted. A mainstem gauge is where many tributary gauges first meet, so its in-degree is high by construction -- that is a river network, not an artifact.

DESIGN NOTES, so a later edit does not undo them:
  * Albers Equal Area (EPSG:5070), matching `make_aoe_expansion_map.py`. On plate-carree the Missouri arm reads far larger than it is.
  * Colour carries ONE distinction -- does this node observe nitrate -- because that is the split that decides what can be trained on. Everything else about a node is size or position. Palette #2a78d6 / #eb6834 reused from the AOE figure, where all-pairs CVD dE measured 9.2 at this surface.
  * Identity is never colour-alone: the legend names both classes and the counts sit in the subtitle.
  * Edges are drawn UNDER the nodes at low alpha. Above them they read as a road network and the nodes disappear into the joins.
  * Node area is proportional to log10 basin area, so a headwater and the Mississippi are on one scale without the latter swallowing the panel.
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.build2 import config  # noqa: E402
from src.build2.chunk3 import gauges  # noqa: E402
from src.build2.chunk3.site_type import NO_SURFACE_BASIN  # noqa: E402
from src.build2.chunk4 import graph  # noqa: E402

HERE = Path(__file__).resolve().parent
STATES = HERE / "_us_states.geojson"
OUT = HERE / "sensor_graph.png"

SURFACE, INK, INK_2, MUTED, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#c3c2b7"
NITRATE, COVARIATE = "#2a78d6", "#eb6834"
ALBERS = "EPSG:5070"

# Iowa and its neighbours, in degrees; the right panel's extent.
ZOOM = (-97.0, 39.5, -89.0, 44.5)


def build():
    """Nodes with coordinates, and the edge list, in Albers."""
    reg = pd.read_parquet(config.REGISTRATIONS_PATH)
    g = gauges.collapse(reg)
    valid = g[g.comid.notna() & ~g.site_type.isin(NO_SURFACE_BASIN)].copy()

    net = graph.Downstream.load()
    on_reach = graph.node_reaches(valid)
    _, below = graph.label(net, on_reach)

    bas = pd.read_parquet(config.BASINS, columns=["site_uid", "basin_km2"])
    valid = valid.merge(bas, on="site_uid", how="left", suffixes=("", "_b"))
    valid["km2"] = pd.to_numeric(valid.basin_km2, errors="coerce")
    valid["has_nitrate"] = pd.to_numeric(valid.n_nitrate_days, errors="coerce").fillna(0) > 0

    gdf = gpd.GeoDataFrame(valid, geometry=gpd.points_from_xy(valid.lon, valid.lat),
                           crs=4326).to_crs(ALBERS)
    xy = {r.site_uid: (r.geometry.x, r.geometry.y) for r in gdf.itertuples()}
    edges = [(xy[a], xy[b]) for a, b in below.items() if a in xy and b in xy]
    return gdf, edges, below


def panel(ax, gdf, edges, states, extent=None, title=""):
    ax.set_facecolor(SURFACE)
    states.boundary.plot(ax=ax, color=AXIS, linewidth=0.4, zorder=1)

    from matplotlib.collections import LineCollection
    ax.add_collection(LineCollection(edges, colors=INK_2, linewidths=0.7, alpha=0.55, zorder=2))

    size = 10 + 70 * (np.log10(gdf.km2.clip(lower=1).fillna(1)) / 6.5)
    for flag, colour, label in ((True, NITRATE, "observes nitrate"),
                                (False, COVARIATE, "other channels only")):
        s = gdf[gdf.has_nitrate == flag]
        ax.scatter(s.geometry.x, s.geometry.y, s=size[s.index], c=colour,
                   edgecolors=SURFACE, linewidths=0.5, zorder=3, label=label)

    if extent:
        b = gpd.GeoSeries.from_wkt(
            [f"POINT({extent[0]} {extent[1]})", f"POINT({extent[2]} {extent[3]})"],
            crs=4326).to_crs(ALBERS)
        ax.set_xlim(b.x.min(), b.x.max())
        ax.set_ylim(b.y.min(), b.y.max())
    ax.set_title(title, fontsize=10.5, color=INK_2, loc="left", pad=4)
    ax.set_aspect("equal")
    ax.axis("off")


def main():
    gdf, edges, below = build()
    states = gpd.read_file(STATES).to_crs(ALBERS)

    n_lab = int(gdf.has_nitrate.sum())
    linked = len({a for a in below} | {b for b in below.values()})

    mpl.rcParams["figure.facecolor"] = SURFACE
    fig, axes = plt.subplots(1, 2, figsize=(17, 8.2))
    b = gdf.total_bounds
    pad = 0.04 * max(b[2] - b[0], b[3] - b[1])
    panel(axes[0], gdf, edges, states, title="the whole AOE")
    axes[0].set_xlim(b[0] - pad, b[2] + pad)
    axes[0].set_ylim(b[1] - pad, b[3] + pad)
    panel(axes[1], gdf, edges, states, extent=ZOOM, title="Iowa and its neighbours")

    axes[0].legend(loc="lower left", frameon=False, fontsize=9, labelcolor=INK_2,
                   handles=[Line2D([], [], marker="o", ls="", color=NITRATE,
                                   label=f"observes nitrate  ({n_lab})"),
                            Line2D([], [], marker="o", ls="", color=COVARIATE,
                                   label=f"other channels only  ({len(gdf) - n_lab})"),
                            Line2D([], [], color=INK_2, alpha=0.55, lw=0.9,
                                   label=f"flow edge  ({len(edges)})")])

    # subplots_adjust, NOT tight_layout: tight_layout repositions a suptitle and drops it onto the
    # subtitle, which is exactly what it did here.
    fig.subplots_adjust(top=0.845, left=0.02, right=0.98, bottom=0.02, wspace=0.04)
    fig.suptitle("The sensor graph — nodes and hydrologic edges", fontsize=15, color=INK,
                 x=0.055, ha="left", y=0.980)
    fig.text(0.055, 0.912,
             f"{len(gdf)} valid nodes (surface type, snapped to a reach) · {len(edges)} edges · "
             f"{linked} on at least one edge, {len(gdf) - linked} isolated · dot area ∝ log10 basin km²\n"
             f"A line is a FLOW relation, not a river: the two gauges are hydrologically adjacent, "
             f"with the water following the channel between them.",
             fontsize=9.5, color=INK_2, ha="left")
    fig.savefig(OUT, dpi=170, facecolor=SURFACE)
    print(f"wrote {OUT}  ({len(gdf)} nodes, {len(edges)} edges, {linked} linked)")


if __name__ == "__main__":
    main()
