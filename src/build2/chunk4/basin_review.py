"""The basin selection sheet, and an optional panel for looking at the flagged ones.

TWO ARTIFACTS WITH DIFFERENT SCOPES. `basin_decisions.csv` carries EVERY delineated gauge (`sheet_rows`), because it is the override table and a gauge with no row cannot be overruled. The PNG carries only the flagged ones (`queue`), because it is for reading and a picture of 366 unremarkable basins is not. The sheet is written on every run; the panel renders only under `--review`, since the real instrument for this is the map widget and this is a stopgap until the read layer exists.

WHY THE PANEL SHOWS ONLY THE FLAGGED ONES. 388 gauges are delineated and most are unremarkable -- the local accumulation reproduces NHDPlus's own drainage area to four decimal places, and there is nothing for a person to add. A row appears here only when a check fired or when a delineation FAILED, which is the same rule the type review uses and for the same reason: a queue that contains everything contains nothing.

AND NOT THE ONES WITH NO BASIN TO FIND. Four gauges sit on `Coastline` reaches, which carry no upstream drainage, and sixteen are wells or groundwater, which have no overland catchment. That is an answer, not a question: asking a person to choose among four correctly empty methods would be asking them to invent a watershed for a patch of sea or for a hole in the ground. See `_needs_review`.

TWO MAPS, NOT A MAP AND A SERIES. The other two panels put the daily record in the middle column because type and identity both have a signature in the nitrate. A delineation does not -- the record cannot tell you which watershed the water came from. So the middle column is a WIDE map carrying the candidate basins at basin scale, and the left column is the SITE at reach scale, showing what it snapped to and what ran nearby. Those two questions -- "are these four answers the same shape" and "is this the right reach" -- are the whole review, and each needs its own zoom.

WHAT THE WIDE MAP SETTLES. Four polygons in four colours. Agreement is visible as overlap and disagreement as geometry, which is a stronger statement than the area ratio beside it: two basins can share an area to a percent and lie in different valleys, and only the picture says so.

WHAT THE SITE MAP SETTLES. `flag_river_capture` claims the gauge belongs on a smaller reach beside the one it snapped to. That claim is decidable by eye and by nothing else -- the snapped reach is drawn heavy, the neighbours thin, and the named alternative labelled.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import _panel, config
from .._panel import AXIS, INK, INK_2, MUTED, SURFACE, fmt
from . import basins, flags

# One colour per method, used identically in the map, the legend and the evidence block, so a reader carries one mapping across the row. basin1 keeps the teal the other panels give a catchment, because it IS one.
METHOD_COLOR = {"basin0": "#b45309", "basin1": "#0d9488", "basin2": "#7c3aed", "basin3": "#db2777"}
METHOD_WHAT = {
    "basin0": "NLDI nwissite -- the authority's own polygon (USGS only)",
    "basin1": "accumulated upstream from the SNAPPED reach  [the default]",
    "basin2": "accumulated from the CONTAINING catchment  [cross-check]",
    "basin3": "D8 raster flood-fill, HydroSHEDS 15s  [cross-check]",
}

FLAG_WHAT = {
    "flag_area_vs_vaa": "basin1 disagrees with NHDPlus's own accumulated area -- the walk is wrong",
    "flag_basin0_disagrees": "the authority's polygon and the snap's differ by more than 10%",
    "flag_d8_disagrees": "the raster and the vector differ by more than 35%",
    "flag_not_contained": "the site sits outside its own basin -- usually a reach with no catchment",
    "flag_oversized": "larger than any basin in scope can be",
    "flag_river_capture": "looks captured by a large river it sits beside",
}

SITE_COLUMN = "Basin"
DECISION_COLUMNS = [SITE_COLUMN, "site_uid",
                    "decision", "decided_by", "note",
                    "auto_method", "reason", "station_name", "site_type", "selection_mode",
                    "basin0_km2", "basin1_km2", "basin2_km2", "basin3_km2",
                    "n_corroborations", "corroborated_by",
                    "vaa_totda_km2", "reach_name", "reach_da_km2",
                    "alt_name", "alt_da_km2", "alt_dist_m", "snap_dist_m", "comid_agree"]


def out_dir():
    return _panel.review_dir("basin-review", chunk="chunk4")


def reason(row) -> str:
    """Why this row is in the queue: the flags that fired, or a delineation that failed."""
    fired = [c for c in flags.FLAG_COLS if bool(row.get(c))]
    if not fired:
        return "every method failed"
    return ", ".join(f.replace("flag_", "") for f in fired)


def _needs_review(r) -> bool:
    """Anything flagged, plus a gauge whose delineation FAILED -- but not one that has no basin to find.

    A basin that does not physically exist is an answer, not a question. Four estuaries snap to `Coastline` reaches, which carry no upstream drainage; sixteen sites are wells or groundwater, which have no overland catchment at all. In neither case is there a watershed to delineate or anything a reviewer could pick, so putting them in the queue would ask a person to choose between four correctly empty methods. They carry a `no_basin_reason` in `basins.NOT_DELINEABLE` and are left alone.
    """
    if any(bool(r.get(c)) for c in flags.FLAG_COLS):
        return True
    return (r.get("selection_mode") == "none"
            and r.get("no_basin_reason") not in basins.NOT_DELINEABLE)


def queue(ev: pd.DataFrame) -> pd.DataFrame:
    """The gauges a person has to look at, worst first.

    Ordered by how badly the delineation is in question rather than by uid -- `river_capture` and a failed delineation are wrong answers, while a wide D8 disagreement is usually just a 15 arc-second raster being a raster.
    """
    q = ev[[_needs_review(r) for r in ev.to_dict("records")]].copy()
    if not len(q):
        return q
    rank = {c: i for i, c in enumerate(
        ["selection_failed", "flag_river_capture", "flag_oversized", "flag_not_contained",
         "flag_area_vs_vaa", "flag_basin0_disagrees", "flag_d8_disagrees"])}
    q["_rank"] = [min([rank["selection_failed"] if r.get("selection_mode") == "none" else 99]
                      + [rank[c] for c in flags.FLAG_COLS if bool(r.get(c))])
                  for r in q.to_dict("records")]
    q["reason"] = [reason(r) for r in q.to_dict("records")]
    return q.sort_values(["_rank", "site_uid"]).drop(columns="_rank").reset_index(drop=True)


def sheet_rows(ev: pd.DataFrame) -> pd.DataFrame:
    """Every DELINEATED gauge, flagged ones first. The override table's contents.

    One row per gauge that has a basin, because the sheet is where a selection is overruled and a gauge with no row cannot be overruled. Gauges with no basin at all are excluded -- a well or a coastline estuary has nothing to choose between, and a blank row asking which of four empty methods to prefer is the clutter this build keeps having to sweep up.

    Ordered flagged-first so the rows most worth a second look sit at the top of the file, which is the only affordance a CSV has.
    """
    d = ev[ev.selection_mode != "none"].copy()
    if not len(d):
        return d
    d["reason"] = [reason(r) if any(bool(r.get(c)) for c in flags.FLAG_COLS) else ""
                   for r in d.to_dict("records")]
    d["auto_method"] = [basins.auto_choice(r) for r in d.to_dict("records")]
    d["_flagged"] = (d.reason != "").astype(int)
    return d.sort_values(["_flagged", "site_uid"], ascending=[False, True]).drop(
        columns="_flagged").reset_index(drop=True)


def sync_decisions_file(q: pd.DataFrame) -> tuple[int, int, int]:
    """Reconcile `basin_decisions.csv` with the current selection set. Returns (added, kept, retired).

    `decision` is BLANK unless a human wrote one, and is never seeded from the auto rule -- a pre-filled decision is indistinguishable from a reviewed one, and `selection_mode` would then report every gauge as manually chosen. The auto rule's answer rides alongside in `auto_method`, so the sheet shows what was picked without claiming anybody picked it.
    """
    if not len(q):
        return 0, 0, 0
    return _panel.sync_sheet(config.BASIN_DECISIONS, q, ["site_uid"], SITE_COLUMN, DECISION_COLUMNS)


def pending(q: pd.DataFrame) -> list[str]:
    """Uids with no decision on file, in sheet order. Nothing blocks on this -- chunk 4 does not gate."""
    done = set(basins.load_decisions().site_uid)
    return [u for u in q.site_uid if u not in done]


# ---- drawing ----------------------------------------------------------------------------------------

def _legend(axes_row, ev: pd.DataFrame) -> None:
    """Row 0: the four methods and the six flags, so a reviewer never has to look either up elsewhere."""
    for ax in axes_row:
        ax.axis("off")

    ax = axes_row[1]
    ax.text(0, 1.0, "`decision` must be one of these four", fontsize=7.5, fontweight="bold",
            family="monospace", va="top", color=INK, transform=ax.transAxes)
    y = 0.86
    for m in basins.METHODS:
        n = int((ev.basin_method == m).sum()) if "basin_method" in ev.columns else 0
        ax.text(0, y, f"{m}   {METHOD_WHAT[m]}", fontsize=6.6, family="monospace", va="top",
                color=METHOD_COLOR[m], transform=ax.transAxes)
        ax.text(0, y - 0.075, f"        selected for {n} gauge(s)", fontsize=6.2, family="monospace",
                va="top", color=MUTED, transform=ax.transAxes)
        y -= 0.20

    ax = axes_row[2]
    ax.text(0, 1.0, "why a row is here", fontsize=7.5, fontweight="bold", family="monospace",
            va="top", color=INK, transform=ax.transAxes)
    lines = [f"{c.replace('flag_', ''):<18s}{int(ev[c].sum()) if c in ev.columns else 0:>4}   {FLAG_WHAT[c]}"
             for c in flags.FLAG_COLS]
    body = "\n".join(lines) + "\n\nFlags are ADVISORY -- they queue a gauge, they never change the selection."
    ax.text(0, 0.88, body, fontsize=6.4, va="top", family="monospace", color=INK_2, transform=ax.transAxes)


def _draw_basins(ax, uid: str, pt, chosen: str | None) -> None:
    """The candidate polygons, largest behind, the selected one heavy. Basin scale, not site scale."""
    import geopandas as gpd

    ax.set_facecolor(SURFACE)
    drawn = []
    for m in basins.METHODS:
        g = basins.geometry_for(uid, m)
        if g is not None:
            drawn.append((m, gpd.GeoSeries([g], crs=4326).to_crs(config.EQUAL_AREA)))
    if not drawn:
        ax.text(0.5, 0.5, "no basin from any method", fontsize=8, color=MUTED,
                ha="center", va="center", transform=ax.transAxes)
        ax.set_xticks([]); ax.set_yticks([])
        return

    drawn.sort(key=lambda t: -float(t[1].area.iloc[0]))
    for m, g in drawn:
        sel = (m == chosen)
        g.plot(ax=ax, facecolor=METHOD_COLOR[m], alpha=0.16 if sel else 0.07,
               edgecolor=METHOD_COLOR[m], lw=2.0 if sel else 1.0, zorder=2 + int(sel))
    ax.scatter([pt.x], [pt.y], s=46, c=INK, edgecolors=SURFACE, linewidths=1.3, zorder=9)

    b = drawn[0][1].total_bounds
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    span = max(b[2] - b[0], b[3] - b[1], 2000.0) * 1.1
    ax.set_xlim(cx - span / 2, cx + span / 2)
    ax.set_ylim(cy - span / 2, cy + span / 2)
    ax.set_aspect("equal")
    _panel.scale_bar(ax, cx - span / 2, cx + span / 2, cy - span / 2 + span * 0.06)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(AXIS)


def _evidence_text(r) -> str:
    lines = []
    for m in basins.METHODS:
        st = str(r.get(f"{m}_status", "missing"))
        km = fmt(r.get(f"{m}_km2"), ",.1f")
        mark = "<-" if r.get("basin_method") == m else "  "
        lines.append(f"{mark} {m}  {km:>14}  {st}")
    lines += [
        "",
        f"   corroborated   {str(r.get('corroborated_by') or 'NOTHING'):>14}",
        f"   NHDPlus totda  {fmt(r.get('vaa_totda_km2'), ',.1f'):>14}",
        f"   reaches walked {int(r['n_reaches']) if pd.notna(r.get('n_reaches')) else '--':>14}",
        f"   site to basin  {fmt(r.get('dist_to_basin_km'), '.2f'):>14} km",
        "",
        f"   snapped reach  {str(r.get('reach_name') or '--')[:30]}",
        f"   its drainage   {fmt(r.get('reach_da_km2'), ',.1f'):>14} km2",
        f"   nearest other  {str(r.get('alt_name') or '--')[:30]}",
        f"   its drainage   {fmt(r.get('alt_da_km2'), ',.1f'):>14} km2 "
        f"at {fmt(r.get('alt_dist_m'), '.0f')} m",
        "",
        f"   snap dist      {fmt(r.get('snap_dist_m'), '.1f'):>14} m",
        f"   comid agree    {str(r.get('comid_agree')):>14}",
        f"   site type      {str(r.get('site_type')):>14}",
    ]
    return "\n".join(lines)


def main(ev: pd.DataFrame | None = None, force: bool = False) -> pd.DataFrame:
    """Render the flagged-basin panel. `ev` is the evidence table from `run_chunk4`; read from disk if absent."""
    if ev is None:
        if not config.BASIN_CANDIDATES.exists():
            raise FileNotFoundError(f"{config.BASIN_CANDIDATES} missing -- run chunk 4 first")
        ev = pd.read_csv(config.BASIN_CANDIDATES)
    q = queue(ev)
    if not len(q):
        print("  no flagged basins; nothing to review")
        return q
    print(f"  {len(q)} gauge(s) queued: {q.reason.value_counts().to_dict()}", flush=True)

    reg = pd.read_parquet(config.REGISTRATIONS_PATH)
    pts = _panel.project(reg)
    need = {int(c) for c in reg[reg.site_uid.isin(q.site_uid)].comid.dropna()}
    print(f"  loading geometry for {len(need)} COMID(s) ...", flush=True)
    catch, reaches = _panel.catchments(need), _panel.context_reaches(need)

    draw = _panel.cap(q, "gauge(s) queued", config.BASIN_DECISIONS)

    fig, axes = _panel.figure(len(draw) + 1, width_ratios=(0.42, 3.4, 3.4, 2.9))
    _legend(axes[0], ev)
    for i, r in enumerate(q.to_dict("records"), start=1):
        uid = r["site_uid"]
        ax_n, ax_site, ax_bas, ax_txt = axes[i]
        _panel.draw_index(ax_n, i)
        cid = reg.loc[reg.site_uid == uid, "comid"]
        cid = None if not len(cid) or pd.isna(cid.iloc[0]) else int(cid.iloc[0])
        _panel.draw_map(ax_site, [pts[uid]], [""], [INK],
                        catch=catch[catch.comid == cid] if cid is not None else None,
                        reaches=reaches, snapped=cid)
        chosen = basins.method_of(r)
        _draw_basins(ax_bas, uid, pts[uid], chosen)
        _panel.draw_stats(ax_txt, r["reason"],
                          METHOD_COLOR.get(chosen, INK), _evidence_text(r),
                          sublines=[(uid, INK_2)])
        ax_site.set_ylabel(uid, fontsize=6, color=INK)
        ax_bas.set_title(str(r.get("station_name"))[:70], fontsize=6.5, color=INK_2, loc="left")
    # Column headers ride on the LEGEND row, not on row 1. A title on row 1 sits in the same slot as that row's station name and silently replaces it, so the first gauge in the queue loses the one label that says what it is.
    for ax, t in zip(axes[0][1:], ("site: snapped reach + neighbours", "candidate basins", "evidence")):
        ax.set_title(t, fontsize=8, color=INK, loc="left")

    d = out_dir()
    png = d / "basin_review.png"
    w, h = _panel.finish(fig, len(draw) + 1,
                         f"Basin review — {len(q)} flagged of {len(ev)} gauges.  "
                         f"The auto rule is basin0 if the authority has one, else basin1.  "
                         f"Overrule it in basin_decisions.csv", png)
    print(f"  wrote {png.name} ({w} x {h} px, {png.stat().st_size/1e6:.1f} MB)")
    return q


if __name__ == "__main__":
    main()
