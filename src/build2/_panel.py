"""Shared machinery for the human review panels -- the long PNGs a person decides from.

THREE REVIEWS, ONE SHAPE. `merge_review` asks "are these two registrations one gauge"; `site_type_review` asks "is this site really a stream"; `basin_review` asks "is this the right delineation". Each is one tall figure, one row per case, `map | series | evidence`, and all three are read the same way. Everything here is what they share; each module supplies only its own rows, its middle panel and its evidence text.

It sits at the top of `build2` rather than inside a chunk because the reviews do: chunk 3 owns two of them and chunk 4 owns the third, and a panel that lived in whichever chunk happened to need it first would be imported sideways by the others.

THE FIGURE MATH IS THE PART WORTH SHARING. It took iteration and every piece of it fixes a specific failure: the title band is reserved in INCHES because a fixed FRACTION collapses on a tall figure and drops the suptitle inside row 0; markers are ringed in the surface colour because sites metres apart otherwise render as one dot; the series is reindexed to a full daily calendar so real gaps BREAK the line instead of drawing a straight segment that reads as interpolation; the map floors its span so a 4 m separation does not zoom to a scale with no landmark in it.

STYLE FOLLOWS `src/build/water/filter_sites.py::render_panel`, the repo's existing long review panel.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config, schema

# Reserved so a map never zooms to a scale where the subject fills the panel and no landmark is visible.
MIN_SPAN_M = 900.0
PAD_FRAC = 0.08

# Hops up and down the flow network to gather context reaches around a site.
CONTEXT_HOPS = 3

# The repo's light-mode figure palette, declared identically in the three `notes/graphics` map scripts.
SURFACE, INK, INK_2, MUTED, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#c3c2b7"
A_COLOR, B_COLOR = "#2a78d6", "#eb6834"
CATCH, REACH, WATERBODY = "#0d9488", "#2563eb", "#7c3aed"

ROW_IN, FIG_W, DPI = 2.75, 17, 110


def review_dir(name: str, chunk: str = "chunk3"):
    """`{chunk}/{name}/`. The hyphen in these names is deliberate -- they are OUTPUT directories, not importable packages, which is why the modules that write them sit beside them rather than inside."""
    d = config._HERE / chunk / name
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- geometry ---------------------------------------------------------------------------------------

def catchments(comids) -> "object":
    """Catchment polygons for the given COMIDs, EPSG:5070.

    Column-projected pushdown rather than a full read: the layer is 854 MB over 1.49M rows and a review needs a handful. `gpd.read_parquet(bbox=...)` is not an option -- the file is GeoParquet 1.0.0 with no bbox covering column and raises.
    """
    import geopandas as gpd
    import pyarrow.parquet as pq
    import shapely

    from .chunk1 import network

    ids = [int(c) for c in comids if pd.notna(c)]
    if not ids:
        return gpd.GeoDataFrame({"comid": []}, geometry=[], crs=config.EQUAL_AREA)
    t = pq.read_table(network._path("catchments"), columns=["FEATUREID", "geometry"],
                      filters=[("FEATUREID", "in", ids)])
    df = t.to_pandas()
    g = gpd.GeoDataFrame({"comid": df.FEATUREID.astype("int64")},
                         geometry=shapely.from_wkb(df.geometry), crs=4326)
    return g.to_crs(config.EQUAL_AREA)


def context_reaches(comids, hops: int = CONTEXT_HOPS) -> "object":
    """Flowlines within `hops` of the given COMIDs on the flow network, EPSG:5070.

    WALKED, NOT SPATIALLY QUERIED. The flowline layer is 1.25 GB with no usable spatial index here, so a bbox query would mean loading every geometry. `vaa.parquet` is 90 MB and holds the topology, so the neighbourhood is found in attribute space and only the resulting geometries are read.

    `uphydroseq`/`dnhydroseq` hold HYDROSEQ values, not COMIDs -- following them as COMIDs silently returns unrelated reaches elsewhere in the network. The hydroseq->comid map is built off the same table.
    """
    import geopandas as gpd
    import pyarrow.parquet as pq
    import shapely

    from .chunk1 import network

    v = pd.read_parquet(network._path("vaa"),
                        columns=["comid", "hydroseq", "uphydroseq", "dnhydroseq"])
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

    t = pq.read_table(network._path("flowlines"),
                      columns=["COMID", "GNIS_NAME", "StreamOrde", "geometry"],
                      filters=[("COMID", "in", sorted(keep))])
    df = t.to_pandas()
    g = gpd.GeoDataFrame({"comid": df.COMID.astype("int64"),
                          "name": df.GNIS_NAME, "order": df.StreamOrde},
                         geometry=shapely.from_wkb(df.geometry), crs=4326)
    return g.to_crs(config.EQUAL_AREA)


def project(sites: pd.DataFrame) -> dict:
    """site_uid -> shapely Point on EPSG:5070, so metres are metres."""
    import geopandas as gpd
    from shapely.geometry import Point

    si = sites.set_index("site_uid")
    proj = gpd.GeoSeries(gpd.points_from_xy(si.lon, si.lat), crs=4326).to_crs(config.EQUAL_AREA)
    return {u: Point(p.x, p.y) for u, p in zip(si.index, proj)}


# ---- panels -----------------------------------------------------------------------------------------

def _extent(shapes, pts):
    """Square-ish bounds around some geometries and some points, in metres.

    The floor matters: a subject metres across inside a small catchment would otherwise zoom to a scale with no landmark in it. The geometry sets the frame, the floor stops a tiny one dominating, the pad keeps markers off the edge.
    """
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
    """A bar at a round fraction of the view, labelled. The map has no ticks, so this is the only scale cue."""
    span = x1 - x0
    L = min([50, 100, 250, 500, 1000, 2000, 5000, 10000, 25000],
            key=lambda v: abs(v - span * 0.25))
    xs = x0 + span * 0.06
    ax.plot([xs, xs + L], [y0, y0], lw=2.2, c=INK, solid_capstyle="butt", zorder=9)
    ax.text(xs + L / 2, y0 + span * 0.02, f"{L/1000:g} km" if L >= 1000 else f"{L:g} m",
            fontsize=6, ha="center", va="bottom", color=INK, zorder=9)


def draw_map(ax, pts, labels, colors, catch=None, reaches=None, snapped=None, water=None):
    """Catchments outlined, the snapped reach highlighted, a waterbody if given, context flowlines behind, sites on top.

    Labels are offset in OPPOSITE directions because two sites metres apart on a 900 m panel are half a percent of the width apart, so identically-placed labels would sit on top of each other and neither would be readable.
    """
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
        # Reindexed to a full daily calendar so a real gap becomes NaN and the line BREAKS there, instead of a straight segment that reads as interpolation.
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
    """Verdict first, then the evidence beneath it.

    BOTH ANCHORED TO THE TOP. Pinning the headline to `(0, 0)` with `va="bottom"` reads well until the body is taller than the row -- then they overlap and the headline lands in the middle of the numbers. Stacking from the top means a long body collides with nothing but empty space.
    """
    ax.axis("off")
    ax.text(0, 1.0, headline, fontsize=8, fontweight="bold", family="monospace",
            va="top", color=headline_color, transform=ax.transAxes)
    # The body starts BELOW the headline however many lines it runs to. A fixed offset works for a one-line verdict and collides the moment the headline wraps -- which it does as soon as the headline is a sentence rather than a label.
    n = headline.count("\n") + 1
    y = 1.0 - 0.055 * n - 0.03
    # `sublines` are (text, colour) pairs drawn between the headline and the body -- the identities of the things being compared, in the SAME colours the map markers and the series use, so a reader carries one A/B mapping across all three panels instead of re-deriving it per panel.
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
    """The row's number, large, in its own narrow column.

    A reviewer works between the panel and the decisions sheet, and the number is the only thing that links a row to the line they type in. Small text in a corner makes that a search; a large number in a dedicated column makes it a glance.
    """
    ax.axis("off")
    if label is not None:
        ax.text(0.5, 0.5, str(label), fontsize=22, fontweight="bold", family="monospace",
                ha="center", va="center", color=AXIS, transform=ax.transAxes)


# A PANEL IS A THING SOMEBODY OPENS, so its size is bounded by what a person can read rather than by how
# many rows the upstream query happened to return. At ~302 px per row a 736-row panel is 222,625 px tall --
# 416 megapixels, a 1.7 GB buffer, and half an hour of matplotlib producing a file no viewer will open. That
# is not an error anything reports; it looks exactly like a hang. The cap turns it into a printed sentence.
#
# TRUNCATION IS FROM THE TOP, AND SAFE BECAUSE THE ORDER MEANS SOMETHING. Both queues sort unanswered first,
# so the rows a panel drops are the ones already answered or furthest down the branch order -- and panel row
# N is still sheet row N, which is the invariant the whole numbering rests on.
MAX_PANEL_ROWS = 120


def cap(frame: pd.DataFrame, what: str, sheet) -> pd.DataFrame:
    """The leading `MAX_PANEL_ROWS` of a review frame, saying so when it truncates. Order must be meaningful."""
    if len(frame) <= MAX_PANEL_ROWS:
        return frame
    print(f"  {len(frame)} {what} exceeds the {MAX_PANEL_ROWS}-row panel cap -- drawing the first "
          f"{MAX_PANEL_ROWS} (unanswered first); all {len(frame)} are in {getattr(sheet, 'name', sheet)}")
    return frame.head(MAX_PANEL_ROWS).reset_index(drop=True)


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
    """Reserve the title band in INCHES, save, report the pixel size.

    A fixed FRACTION collapses on a tall figure and drops the suptitle inside row 0 -- the trap `filter_sites.render_panel` documents and the same rule three other figure scripts here follow.
    """
    import matplotlib.pyplot as plt

    if nrow > MAX_PANEL_ROWS + 1:
        # Reached only if a caller skipped `cap`. Refuse rather than spend half an hour on an unopenable file.
        import matplotlib.pyplot as plt

        plt.close(fig)
        raise ValueError(f"panel of {nrow} rows exceeds MAX_PANEL_ROWS={MAX_PANEL_ROWS}; "
                         f"pass the frame through `_panel.cap` first")
    fig_h = ROW_IN * nrow
    fig.tight_layout(rect=[0, 0, 1, 1 - 0.9 / fig_h])
    fig.suptitle(title, fontsize=12, color=INK, y=1 - 0.42 / fig_h)
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)

    from PIL import Image

    w, h = Image.open(path).size
    return w, h


# ---- decision sheets --------------------------------------------------------------------------------

EDITABLE = ("decision", "decided_by", "note")


def sync_sheet(path, ordered: pd.DataFrame, key_cols, number_col: str,
               columns) -> tuple[int, int, int]:
    """Reconcile a decisions sheet with the current candidate set. Returns (added, kept, retired).

    ADDED TO, NEVER REWRITTEN. Creating the file only when it is absent leaves no way for a candidate that appears later to reach the reviewer: the gate reports it pending, and the sheet has no row to fill in. Rewriting it wholesale is worse -- it discards decisions that took real work.

    So: every current candidate gets a row, carrying forward whatever was already decided for it; genuinely new candidates arrive blank; and a row whose candidate has DISAPPEARED is kept, unnumbered, at the bottom -- IF IT WAS DECIDED. A cohort change can retire a case and a later one can bring it back, and re-deciding it because the row was deleted would be a self-inflicted cost. Retired rows are inert; nothing reads them until their key returns.

    A RETIRED ROW THAT WAS NEVER DECIDED IS DROPPED, because it holds nothing to preserve. Keeping it is not caution, it is clutter that reads as outstanding work: chunk 4 accumulated 24 such rows, among them the wells and estuaries that had been queued before the build learned they can have no basin at all. Nobody will ever answer those, and a sheet that shows them invites someone to try.

    The number column is rewritten on every sync so row N of the sheet is row N of the panel. It is a position, not an identity: decisions are keyed on `key_cols`, which is what survives a reordering.
    """
    key = lambda df: list(zip(*[df[c].astype(str) for c in key_cols]))
    cur = ordered.copy()
    cur_keys = key(cur)

    prior, retired = {}, None
    if path.exists():
        old = pd.read_csv(path, dtype=str).fillna("")
        if all(c in old.columns for c in key_cols):
            for k, row in zip(key(old), old.to_dict("records")):
                prior[k] = {c: row.get(c, "") for c in EDITABLE}
            gone = old[[k not in set(cur_keys) for k in key(old)]]
            gone = gone[gone.get("decision", pd.Series("", index=gone.index)).astype(str).str.strip() != ""]
            retired = gone if len(gone) else None

    for c in EDITABLE:
        cur[c] = [prior.get(k, {}).get(c, "") for k in cur_keys]
    cur.insert(0, number_col, range(1, len(cur) + 1))
    out = cur[[c for c in columns if c in cur.columns]]

    if retired is not None:
        r = retired.copy()
        r[number_col] = ""                      # unnumbered: not on the panel, not part of the ordering
        out = pd.concat([out, r[[c for c in columns if c in r.columns]]], ignore_index=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    out.to_csv(tmp, index=False)
    tmp.replace(path)
    added = sum(1 for k in cur_keys if k not in prior)
    return added, len(cur_keys) - added, 0 if retired is None else len(retired)


def daily_record(uid: str) -> pd.Series | None:
    """One registration's daily nitrate, indexed on a DatetimeIndex. `None` when it has no daily file.

    LIVES HERE BECAUSE BOTH PANELS WANT IT. It used to be `identity._daily`, and when that function was tidied away the reference outlived it in TWO review modules -- each breaking only at the moment a reviewer needed the panel, which is the worst time to discover it. `identity` reads canonical frames it can correlate; a panel wants the archived record as a line. Different question, different file, and one implementation rather than two.

    The daily store rather than the canonical one, deliberately: this is the record as chunk 1 archived it, which is what a human is being asked to look at. A pair whose evidence channel is not nitrate will therefore show a nitrate line beside a correlation computed on something else -- the sheet names the channel.
    """
    p = config.WATER_DAILY / f"{schema.uid_to_filename(uid)}.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    col = next((c for c in d.columns if c.startswith("nitrate")), None)
    if col is None or "date" not in d.columns:
        return None
    s = pd.Series(pd.to_numeric(d[col], errors="coerce").to_numpy(),
                  index=pd.DatetimeIndex(pd.to_datetime(d.date))).dropna().sort_index()
    return s if len(s) else None
