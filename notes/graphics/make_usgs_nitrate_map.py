"""Map every USGS continuous (in-situ) nitrate sensor in the continental US.

    python notes/graphics/make_usgs_nitrate_map.py

Enumerates USGS instantaneous-value stream sites carrying pcode 99133 across the 48 states + DC, clips to CONUS, and renders to usgs_nitrate_sensors_conus.png. Re-run to refresh; the site list is live and drifts as sensors are commissioned and retired.

DESIGN NOTES, so a later edit does not undo them:
  * ONE SERIES, so no legend -- the title names what the dots are. A legend box for a single category is noise.
  * Albers Equal Area (EPSG:5070), not lon/lat. On a plate-carree CONUS the northern states stretch and the Corn Belt cluster -- the actual story -- reads as less dense than it is.
  * Each marker carries a surface-coloured ring. Sites overlap heavily in Illinois and Iowa, and without the ring an eight-sensor cluster renders as one blob.
  * No lat/lon ticks or frame. They are chart furniture that a reference map does not need; the state outlines already carry the spatial anchor, drawn in the recessive axis grey rather than black.
"""

from __future__ import annotations

import io
import time
import urllib.request
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
SITES, STATES = HERE / "_usgs_nitrate_conus.parquet", HERE / "_us_states.geojson"
OUT = HERE / "usgs_nitrate_sensors_conus.png"

# dataviz reference palette, light mode. Validated: all six checks PASS at surface #fcfcfb.
SURFACE, INK, INK_2, MUTED, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#c3c2b7"
SERIES = "#2a78d6"  # categorical slot 1
ALBERS = "EPSG:5070"  # NAD83 / Conus Albers -- equal-area, so cluster density is honest

CONUS = ("al az ar ca co ct de fl ga id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny "
         "nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc").split()
_URL = ("https://waterservices.usgs.gov/nwis/site/?format=rdb&stateCd={st}&siteType=ST"
        "&hasDataTypeCd=iv&parameterCd=99133&siteStatus=all")


def fetch_sites() -> pd.DataFrame:
    rows = []
    for st in CONUS:
        try:
            raw = urllib.request.urlopen(_URL.format(st=st), timeout=90).read().decode(errors="replace")
        except Exception:
            continue
        lines = [l for l in raw.splitlines() if l and not l.startswith("#")]
        if len(lines) < 3:
            continue
        d = pd.read_csv(io.StringIO("\n".join([lines[0]] + lines[2:])), sep="\t", dtype=str)
        rows.append(d.dropna(subset=["dec_lat_va", "dec_long_va"]).assign(state=st))
        time.sleep(0.25)
    d = pd.concat(rows, ignore_index=True).drop_duplicates("site_no")
    d["lat"] = pd.to_numeric(d.dec_lat_va, errors="coerce")
    d["lon"] = pd.to_numeric(d.dec_long_va, errors="coerce")
    d = d.dropna(subset=["lat", "lon"])
    return d[(d.lon > -125) & (d.lon < -66) & (d.lat > 24) & (d.lat < 50)]


def main():
    d = pd.read_parquet(SITES) if SITES.exists() else fetch_sites()
    if not SITES.exists():
        d.to_parquet(SITES, index=False)
    states = gpd.read_file(STATES)
    states = states[~states.name.isin(["Alaska", "Hawaii", "Puerto Rico"])].to_crs(ALBERS)
    pts = gpd.GeoDataFrame(d, geometry=gpd.points_from_xy(d.lon, d.lat), crs="EPSG:4326").to_crs(ALBERS)

    mpl.rcParams.update({"font.family": "DejaVu Sans", "figure.dpi": 200})
    fig, ax = plt.subplots(figsize=(11, 6.6))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    states.plot(ax=ax, facecolor="#efeeea", edgecolor=AXIS, linewidth=0.7, zorder=1)
    # Size and ring are tuned TOGETHER: the ring must separate overlapping sensors without out-weighing the
    # fill. At markersize 26 / lw 1.6 the ring won, and dense clusters (CA, WA) rendered as pale smudges.
    pts.plot(ax=ax, color=SERIES, markersize=44, edgecolor=SURFACE, linewidth=1.1, zorder=3)

    ax.set_axis_off()
    ax.margins(0.01)

    top = d.state.str.upper().value_counts()
    fig.text(0.045, 0.955, "USGS continuous nitrate sensors, continental United States",
             fontsize=15, color=INK, weight="bold", va="top")
    fig.text(0.045, 0.900,
             f"{len(d)} in-situ stream sensors reporting nitrate (parameter 99133) across {d.state.nunique()} states.  "
             f"Densest: {', '.join(f'{s} {n}' for s, n in top.head(4).items())}.",
             fontsize=9.5, color=INK_2, va="top")
    fig.text(0.045, 0.045,
             "Source: USGS NWIS site service, hasDataTypeCd=iv, siteType=ST.  Retrieved 2026-07-30.  "
             "Equal-area projection (EPSG:5070).",
             fontsize=7.5, color=MUTED, va="bottom")

    fig.subplots_adjust(left=0.02, right=0.98, top=0.86, bottom=0.08)
    fig.savefig(OUT, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.25)
    print(f"wrote {OUT}  ({len(d)} sensors, {d.state.nunique()} states)")


if __name__ == "__main__":
    main()
