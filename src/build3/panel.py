"""Shared machinery for the human review panels -- the long PNGs a person decides from.

ONE SHAPE FOR EVERY REVIEW. Each panel is one tall figure, one row per case, `index | map | series | evidence`, and all are read the same way: the number links a row to the sheet line the reviewer types in, the map and the series share one A/B colour mapping, and the panel is drawn from the SAME ordering as the sheet so row N is row N. Decision-sheet mechanics live in `ledger`; this module only draws.

THE FIGURE MATH IS THE PART WORTH SHARING, and every piece fixes a specific failure: the title band is reserved in INCHES because a fixed fraction collapses on a tall figure and drops the suptitle inside row 0; markers are ringed in the surface colour because sites metres apart otherwise render as one dot; the series is reindexed to a full daily calendar so real gaps BREAK the line instead of drawing a segment that reads as interpolation; the map floors its span so a 4 m separation does not zoom to a scale with no landmark; and the row count is CAPPED, because a 736-row panel is 222,625 px tall, a 1.7 GB buffer, and half an hour of matplotlib producing a file no viewer opens -- which is not an error anything reports; it looks exactly like a hang.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

# Reserved so a map never zooms to a scale where the subject fills the panel and no landmark is visible.
MIN_SPAN_M = 900.0
PAD_FRAC = 0.08

# Hops up and down the flow network to gather context reaches around a site.
CONTEXT_HOPS = 3

# The repo's light-mode figure palette.
SURFACE, INK, INK_2, MUTED, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#c3c2b7"
A_COLOR, B_COLOR = "#2a78d6", "#eb6834"
CATCH, REACH, WATERBODY = "#0d9488", "#2563eb", "#7c3aed"

ROW_IN, FIG_W, DPI = 2.75, 17, 110

# TRUNCATION IS FROM THE TOP AND SAFE BECAUSE THE ORDER MEANS SOMETHING: every queue sorts unanswered
# first, so the rows a capped panel drops are answered or furthest down the branch order -- and panel
# row N is still sheet row N, the invariant the numbering rests on.
MAX_PANEL_ROWS = 120


def cap(frame: pd.DataFrame, what: str, sheet) -> pd.DataFrame:
    """The leading `MAX_PANEL_ROWS` of a review frame, saying so when it truncates."""
    if len(frame) <= MAX_PANEL_ROWS:
        return frame
    print(f"  {len(frame)} {what} exceeds the {MAX_PANEL_ROWS}-row panel cap -- drawing the first "
          f"{MAX_PANEL_ROWS} (unanswered first); all {len(frame)} are in {getattr(sheet, 'name', sheet)}")
    return frame.head(MAX_PANEL_ROWS).reset_index(drop=True)


# ---- geometry ---------------------------------------------------------------------------------------

def _network_path(name: str):
    return config.ACQ_NETWORK / f"{name}.parquet"


def catchments(comids):
    """Catchment polygons for the given COMIDs, EPSG:5070.

    Column-projected pushdown rather than a full read: the layer is 854 MB over 1.49M rows and a review needs a handful. `gpd.read_parquet(bbox=...)` is not an option -- the file is GeoParquet 1.0.0 with no bbox covering column and raises.
    """
    import geopandas as gpd
    import pyarrow.parquet as pq
    import shapely

    ids = [int(c) for c in comids if pd.notna(c)]
    if not ids:
        return gpd.GeoDataFrame({"comid": []}, geometry=[], crs=config.EQUAL_AREA)
    t = pq.read_table(_network_path("catchments"), columns=["FEATUREID", "geometry"],
                      filters=[("FEATUREID", "in", ids)])
    df = t.to_pandas()
    g = gpd.GeoDataFrame({"comid": df.FEATUREID.astype("int64")},
                         geometry=shapely.from_wkb(df.geometry), crs=4326)
    return g.to_crs(config.EQUAL_AREA)


def context_reaches(comids, hops: int = CONTEXT_HOPS):
    """Flowlines within `hops` of the given COMIDs on the flow network, EPSG:5070.

    WALKED, NOT SPATIALLY QUERIED: the flowline layer is 1.25 GB with no usable spatial index, while the VAA holds the topology in 90 MB, so the neighbourhood is found in attribute space and only the resulting geometries are read. `uphydroseq`/`dnhydroseq` hold HYDROSEQ values, not COMIDs -- following them as COMIDs silently returns unrelated reaches elsewhere in the network.
    """
    import geopandas as gpd
    import pyarrow.parquet as pq
    import shapely

    v = pd.read_parquet(_network_path("vaa"), columns=["comid", "hydroseq", "uphydroseq", "dnhydroseq"])
    seq_to_comid = dict(zip(v.hydroseq.to_numpy(), v.comid.to_numpy()))
    by_comid = v.set_index("comid")

    keep = {int(c) for c in comids if pd.notna(c)}
    frontier = set(keep)
    for _ in range(hops):
        rows = by_comid.reindex([c for c in frontier if c in by_comid.index])
        nxt = set()
        for col in ("uphydroseq", "dnhydroseq"):
            for s in rows[col].dropna().astype("int64"):
                c = seq_to_comid.get(s)
                if c is not None and c not in keep:
                    nxt.add(int(c))
        if not nxt:
            break
        keep |= nxt
        frontier = nxt

    t = pq.read_table(_network_path("flowlines"),
                      columns=["COMID", "GNIS_NAME", "StreamOrde", "geometry"],
                      filters=[("COMID", "in", sorted(keep))])
    df = t.to_pandas()
    g = gpd.GeoDataFrame({"comid": df.COMID.astype("int64"),
                          "name": df.GNIS_NAME, "order": df.StreamOrde},
                         geometry=shapely.from_wkb(df.geometry), crs=4326)
    return g.to_crs(config.EQUAL_AREA)


def project(frame: pd.DataFrame, key: str = "uid") -> dict:
    """key -> shapely Point on EPSG:5070, so metres are metres."""
    import geopandas as gpd
    from shapely.geometry import Point

    si = frame.set_index(key)
    proj = gpd.GeoSeries(gpd.points_from_xy(si.lon, si.lat), crs=4326).to_crs(config.EQUAL_AREA)
    return {u: Point(p.x, p.y) for u, p in zip(si.index, proj)}


def daily_record(uid: str, channel: str = "nitrate") -> pd.Series | None:
    """One sensor's canonical daily series for a channel, for plotting. None when nothing is on disk.

    Channel-parametric on purpose: the build2 ancestor hardcoded nitrate, which made a shared helper chunk-specific in substance. Reads the derivation store's canonical dailies -- the record as the pipeline actually reduced it.
    """
    from . import contracts

    p = config.WATER_CANONICAL / f"{contracts.uid_to_filename(uid)}.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    col = f"{channel}_mean" if f"{channel}_mean" in d.columns else None
    if col is None or "date" not in d.columns:
        return None
    s = pd.Series(pd.to_numeric(d[col], errors="coerce").to_numpy(),
                  index=pd.DatetimeIndex(pd.to_datetime(d.date))).dropna().sort_index()
    return s if len(s) else None


# ---- panels -----------------------------------------------------------------------------------------

def _extent(shapes, pts):
    """Square-ish bounds around geometries and points, in metres. The geometry sets the frame, the floor stops a tiny subject dominating, the pad keeps markers off the edge."""
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    for g in shapes:
        if g is not None and len(g):
            b = g.total_bounds
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    span = max(x1 - x0, y1 - y0, MIN_SPAN_M) * (1 + 2 * PAD_FRAC)
    return cx - span / 2, cx + span / 2, cy - span / 2, cy + span / 2


def scale_bar(ax, x0, x1, y0):
    """A bar at a round fraction of the view. The map has no ticks, so this is the only scale cue."""
    span = x1 - x0
    L = min([50, 100, 250, 500, 1000, 2000, 5000, 10000, 25000],
            key=lambda v: abs(v - span * 0.25))
    xs = x0 + span * 0.06
    ax.plot([xs, xs + L], [y0, y0], lw=2.2, c=INK, solid_capstyle="butt", zorder=9)
    ax.text(xs + L / 2, y0 + span * 0.02, f"{L/1000:g} km" if L >= 1000 else f"{L:g} m",
            fontsize=6, ha="center", va="bottom", color=INK, zorder=9)


def draw_map(ax, pts, labels, colors, catch=None, reaches=None, snapped=None, water=None):
    """Catchments outlined, the snapped reach highlighted, a waterbody if given, context flowlines behind, sites on top. Labels offset in OPPOSITE directions: two sites metres apart on a 900 m panel are half a percent of the width apart, and identically-placed labels would overprint."""
    x0, x1, y0, y1 = _extent([catch, water], pts)
    ax.set_facecolor(SURFACE)

    if reaches is not None and len(reaches):
        sub = reaches.cx[x0:x1, y0:y1]
        if len(sub):
            sub.plot(ax=ax, color=AXIS, lw=0.7, zorder=1)
            if snapped is not None:
                hit = sub[sub.comid == snapped]
                if len(hit):
                    hit.plot(ax=ax, color=REACH, lw=2.0, zorder=3)
    if water is not None and len(water):
        water.plot(ax=ax, facecolor=WATERBODY, alpha=0.16, edgecolor=WATERBODY, lw=1.4, zorder=2)
    if catch is not None and len(catch):
        catch.plot(ax=ax, facecolor=CATCH, alpha=0.10, edgecolor=CATCH, lw=1.2, zorder=2)

    offs = [(7, 6), (-13, -12)]
    for i, (p, lbl, c) in enumerate(zip(pts, labels, colors)):
        ax.scatter([p.x], [p.y], s=52, c=c, edgecolors=SURFACE, linewidths=1.4, zorder=8)
        if lbl:
            ax.annotate(lbl, (p.x, p.y), textcoords="offset points", xytext=offs[i % 2],
                        fontsize=7, fontweight="bold", color=c, zorder=9)

    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    scale_bar(ax, x0, x1, y0 + (y1 - y0) * 0.06)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(AXIS)


def draw_series(ax, series, colors, empty_msg="no records on disk yet"):
    """One or more daily records on a shared timeline. Returns True if anything was drawn."""
    ax.set_facecolor(SURFACE)
    drawn = False
    for s, c in zip(series, colors):
        if s is None or not len(s):
            continue
        drawn = True
        # Reindexed to a full daily calendar so a real gap becomes NaN and the line BREAKS there.
        full = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D"))
        ax.plot(full.index, full.to_numpy(), lw=0.55, c=c)
    if not drawn:
        ax.text(0.5, 0.5, empty_msg, fontsize=8, color=MUTED,
                ha="center", va="center", transform=ax.transAxes)
    ax.tick_params(labelsize=6, colors=INK_2)
    for s in ax.spines.values():
        s.set_color(AXIS)
    return drawn


def draw_stats(ax, headline, headline_color, body, sublines=None):
    """Verdict first, then the evidence beneath it. BOTH anchored to the top: a long body then collides with nothing but empty space, and the body starts below however many lines the headline wraps to."""
    ax.axis("off")
    ax.text(0, 1.0, headline, fontsize=8, fontweight="bold", family="monospace",
            va="top", color=headline_color, transform=ax.transAxes)
    n = headline.count("\n") + 1
    y = 1.0 - 0.055 * n - 0.03
    # `sublines` are (text, colour) pairs -- the identities being compared, in the SAME colours the map
    # markers and the series use, so a reader carries one A/B mapping across all three panels.
    for text, colour in (sublines or []):
        ax.text(0, y, text, fontsize=6.8, va="top", family="monospace",
                fontweight="bold", color=colour, transform=ax.transAxes)
        y -= 0.047
    if sublines:
        y -= 0.02
    ax.text(0, y, body, fontsize=6.8, va="top", family="monospace",
            color=INK, transform=ax.transAxes)


def fmt(v, spec=".2f", dash="--"):
    return dash if v is None or (isinstance(v, float) and np.isnan(v)) else format(v, spec)


# ---- figure -----------------------------------------------------------------------------------------

def draw_index(ax, label) -> None:
    """The row's number, large, in its own narrow column -- the only link between a panel row and the sheet line the reviewer types in. Small text in a corner makes that a search; this makes it a glance."""
    ax.axis("off")
    if label is not None:
        ax.text(0.5, 0.5, str(label), fontsize=22, fontweight="bold", family="monospace",
                ha="center", va="center", color=AXIS, transform=ax.transAxes)


def figure(nrow: int, width_ratios=(0.42, 2.6, 4.2, 3.0)):
    """A tall figure sized in inches per row: index | map | series | evidence. Returns (fig, axes)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(nrow, len(width_ratios), figsize=(FIG_W, ROW_IN * nrow), squeeze=False,
                             gridspec_kw={"width_ratios": list(width_ratios)})
    fig.patch.set_facecolor(SURFACE)
    return fig, axes


def finish(fig, nrow: int, title: str, path):
    """Reserve the title band in INCHES, save, report the pixel size. Refuses an uncapped figure."""
    import matplotlib.pyplot as plt

    if nrow > MAX_PANEL_ROWS + 1:
        plt.close(fig)
        raise ValueError(f"panel of {nrow} rows exceeds MAX_PANEL_ROWS={MAX_PANEL_ROWS}; "
                         f"pass the frame through `panel.cap` first")
    fig_h = ROW_IN * nrow
    fig.tight_layout(rect=[0, 0, 1, 1 - 0.9 / fig_h])
    fig.suptitle(title, fontsize=12, color=INK, y=1 - 0.42 / fig_h)
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)

    from PIL import Image

    w, h = Image.open(path).size
    return w, h
