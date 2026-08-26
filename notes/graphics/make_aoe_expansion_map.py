"""Two maps: the AOE as declared, and the AOE required for full basin containment.

    python notes/graphics/make_aoe_expansion_map.py

LEFT is the scope frozen in `pipeline_config.toml [region]` -- three boxes derived from the reach network within 250 km of flow distance of a nitrate sensor -- with every discovered sensor on top. RIGHT is what those boxes become under the rule "every sensor's drainage basin lies inside the AOE", with the basins that force the expansion drawn underneath.

The right panel is the argument against the rule, not for it. Six sensors sit on the Mississippi and Missouri mainstems with reported drainage areas above 1,000,000 km2 -- Natchez alone is 2,966,572 km2, about 41% of CONUS -- so containment does not nudge the boxes outward, it pulls in the entire Missouri headwaters to the Continental Divide.

Basins come from NLDI for the 31 largest-drainage sensors. That is enough to fix the envelope: every smaller basin nests inside one of them or inside the declared boxes already, so adding the remaining sites cannot move the outer bound.

DESIGN NOTES, so a later edit does not undo them:
  * Albers Equal Area (EPSG:5070). On plate-carree the northern expansion reads as larger than it is, which would overstate the very point the figure makes.
  * Both panels share one extent, set by the RIGHT panel. Independent extents would let the left panel fill its axes and hide the difference the figure exists to show.
  * Three sources, so a legend is mandatory -- identity is never carried by colour alone. Palette #2a78d6 / #eb6834 / #1baf7a validated at surface #fcfcfb, all-pairs: worst CVD dE 9.2, worst normal dE 24.0. Aqua sits at 2.74:1 contrast, below 3:1, so it ships with a labelled legend as its relief.
  * Basins are drawn as one dissolved union, not 31 overlapping polygons, so the shaded area reads as "land that must be covered" rather than as a pile of translucent shapes whose overlaps look like a heatmap.
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.build2 import config  # noqa: E402

HERE = Path(__file__).resolve().parent
STATES = HERE / "_us_states.geojson"
BASINS = Path("/private/tmp/claude-501/-Users-isaac-dev-sustag/"
              "4b2640b0-7d62-4647-b7b9-5138f4af4269/scratchpad/big_basins.parquet")
OUT = HERE / "aoe_expansion.png"

SURFACE, INK, INK_2, MUTED, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#c3c2b7"
SRC_COLOR = {"USGS": "#2a78d6", "IWQIS": "#eb6834", "MPCA": "#1baf7a"}
ALBERS = "EPSG:5070"


def _expanded_boxes(boxes, basins, sites):
    """Grow each declared box to contain the basins of the sensors inside it.

    Assignment is by outlet: a basin belongs to whichever box holds its sensor, so a box only ever grows for water it is actually responsible for. A basin whose sensor falls in no box (none here) would be dropped rather than silently widening an arbitrary box.
    """
    out = []
    for bx in boxes:
        geom = box(*bx)
        inside = sites[sites.geometry.within(geom)].site_uid
        mine = basins[basins.site_uid.isin(inside)]
        if not len(mine):
            out.append(bx)
            continue
        b = mine.total_bounds
        out.append((min(bx[0], b[0]), min(bx[1], b[1]), max(bx[2], b[2]), max(bx[3], b[3])))
    return out


def _panel(ax, states, sites, boxes, title, region=None):
    """`boxes` are always drawn dashed for reference; `region` is the actual AOE outline when it is no longer a set of boxes."""
    states.plot(ax=ax, facecolor="none", edgecolor=AXIS, linewidth=0.6, zorder=2)
    if region is not None:
        region.plot(ax=ax, facecolor=SRC_COLOR["USGS"], alpha=0.15, edgecolor="none", zorder=1)
        region.boundary.plot(ax=ax, color=SRC_COLOR["USGS"], linewidth=1.4, zorder=4)
    for bx in boxes:
        g = gpd.GeoSeries([box(*bx)], crs=4326).to_crs(ALBERS)
        g.plot(ax=ax, facecolor=INK_2, alpha=0.07, edgecolor="none", zorder=3)
        g.boundary.plot(ax=ax, color=INK_2, linewidth=1.5, linestyle=(0, (5, 3)), zorder=4)
    for src, col in SRC_COLOR.items():
        d = sites[sites.source == src]
        if len(d):
            d.plot(ax=ax, color=col, markersize=17, edgecolor=SURFACE, linewidth=0.55, zorder=5)
    ax.set_title(title, fontsize=12.5, color=INK, pad=12, loc="left")
    ax.set_axis_off()


def main() -> Path:
    mpl.rcParams["font.family"] = "DejaVu Sans"
    states = gpd.read_file(STATES).to_crs(ALBERS)

    s = pd.read_parquet(config.SITES_PATH)
    sites = gpd.GeoDataFrame(s, geometry=gpd.points_from_xy(s.lon, s.lat), crs=4326)
    basins = gpd.read_parquet(BASINS).to_crs(4326)

    cur = list(config.bbox_cover())

    sites_a = sites.to_crs(ALBERS)
    # NLDI returns at least one self-intersecting basin, which makes the union throw a side-location conflict. Repair before dissolving; a zero-width fix is harmless at this scale.
    fixed = basins.geometry.make_valid().buffer(0)
    # The AOE as a GEOMETRY, not a cover: declared boxes unioned with every upstream basin. This is the honest shape of full containment -- it follows drainage divides instead of squaring them off, which is what makes it affordable where the bounding-box version is not.
    region = gpd.GeoDataFrame(
        geometry=[fixed.union_all().union(config.aoe_geometry())], crs=4326).to_crs(ALBERS)

    aoe_km2 = gpd.GeoDataFrame(geometry=[config.aoe_geometry()], crs=4326).to_crs(ALBERS).area.iloc[0] / 1e6
    reg_km2 = region.area.iloc[0] / 1e6

    fig, axes = plt.subplots(1, 2, figsize=(17.5, 7.4), facecolor=SURFACE)
    for ax in axes:
        ax.set_facecolor(SURFACE)

    n = len(sites)
    _panel(axes[0], states, sites_a, cur, f"Declared AOE — three boxes, {n} sensors")
    _panel(axes[1], states, sites_a, cur,
           "Declared AOE ∪ every upstream basin", region=region)

    # One shared extent, taken from the wider panel, so the growth is legible.
    b = region.total_bounds
    pad = 60_000
    axes[0].set_xlim(b[0] - pad, b[2] + pad)
    axes[0].set_ylim(b[1] - pad, b[3] + pad)
    axes[1].set_xlim(*axes[0].get_xlim())
    axes[1].set_ylim(*axes[0].get_ylim())

    handles = [Line2D([], [], marker="o", linestyle="none", markersize=7.5, markerfacecolor=c,
                      markeredgecolor=SURFACE, markeredgewidth=0.6,
                      label=f"{k}  ({int((sites.source == k).sum())})")
               for k, c in SRC_COLOR.items()]
    handles += [Line2D([], [], color=INK_2, linewidth=1.5, linestyle=(0, (5, 3)), label="declared box"),
                Rectangle((0, 0), 1, 1, facecolor=SRC_COLOR["USGS"], alpha=0.15,
                          edgecolor=SRC_COLOR["USGS"], linewidth=1.2, label="AOE ∪ basins")]
    leg = fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False,
                     fontsize=10.5, handletextpad=0.6, columnspacing=2.2,
                     bbox_to_anchor=(0.5, 0.012))
    for t in leg.get_texts():
        t.set_color(INK_2)

    fig.text(0.5, 0.955, "Following drainage divides instead of squaring them off: "
                         f"{reg_km2 / aoe_km2:.2f}x the declared area, not 4.3x",
             ha="center", fontsize=13.5, color=INK)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.90, bottom=0.075, wspace=0.02)
    fig.savefig(OUT, dpi=200, facecolor=SURFACE)
    print(f"wrote {OUT}")
    print(f"  declared AOE        {aoe_km2:>12,.0f} km2")
    print(f"  AOE union basins    {reg_km2:>12,.0f} km2   ({reg_km2 / aoe_km2:.2f}x)")
    print(f"  bounding box of it  {(region.total_bounds[2]-region.total_bounds[0])*(region.total_bounds[3]-region.total_bounds[1])/1e6:>12,.0f} km2")
    return OUT


if __name__ == "__main__":
    main()
