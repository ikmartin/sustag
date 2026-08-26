"""An optional panel for looking at the flagged basins. The override SHEET lives in the basin ledger (`basins_graphs.sync_ledger`); this renders the queue.

WHY THE PANEL SHOWS ONLY THE FLAGGED ONES. Most delineations are unremarkable -- the local accumulation reproduces NHDPlus's own drainage area to four decimal places, and there is nothing for a person to add. A row appears here only when a check fired or when a delineation FAILED, which is the same rule the type review uses: a queue that contains everything contains nothing.

AND NOT THE ONES WITH NO BASIN TO FIND. Off-network sites, sub-catchment sites and coastline reaches carry a `no_basin_reason` that is an ANSWER, not a question: asking a person to choose among four correctly empty methods would be asking them to invent a watershed for a hole in the ground or a patch of sea. Only `all_methods_failed` queues.

TWO MAPS, NOT A MAP AND A SERIES. The record cannot tell you which watershed the water came from, so the middle column is a WIDE map carrying the candidate basins at basin scale, and the left column is the SITE at reach scale. Those two questions -- "are these four answers the same shape" and "is this the right reach" -- are the whole review, and each needs its own zoom. Four polygons in four colours: agreement is visible as overlap, and two basins can share an area to a percent and lie in different valleys -- only the picture says so.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, io as bio, panel as _panel
from ..panel import AXIS, INK, INK_2, MUTED, SURFACE, fmt
from . import basins

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

# no_basin_reason values that are ANSWERS; only the absent fourth kind queues.
_ANSWERS = {basins.NO_BASIN_OFF_NETWORK, basins.NO_BASIN_SUB_CATCHMENT, basins.NO_BASIN_SURFACE}


def reason(row) -> str:
    """Why this row is in the queue: the flags that fired, or a delineation that failed."""
    fired = [c for c in basins.FLAG_COLS if bool(row.get(c))]
    if not fired:
        return "every method failed"
    return ", ".join(f.replace("flag_", "") for f in fired)


def _needs_review(r) -> bool:
    if any(bool(r.get(c)) for c in basins.FLAG_COLS):
        return True
    return r.get("selection_mode") == "none" and r.get("no_basin_reason") not in _ANSWERS


def queue(ev: pd.DataFrame) -> pd.DataFrame:
    """The sites a person has to look at, worst first.

    Ordered by how badly the delineation is in question rather than by id -- `river_capture` and a failed delineation are wrong answers, while a wide D8 disagreement is usually just a 15 arc-second raster being a raster.
    """
    q = ev[[_needs_review(r) for r in ev.to_dict("records")]].copy()
    if not len(q):
        return q
    rank = {c: i for i, c in enumerate(
        ["selection_failed", "flag_river_capture", "flag_oversized", "flag_not_contained",
         "flag_area_vs_vaa", "flag_basin0_disagrees", "flag_d8_disagrees"])}
    q["_rank"] = [min([rank["selection_failed"] if r.get("selection_mode") == "none" else 99]
                      + [rank[c] for c in basins.FLAG_COLS if bool(r.get(c))])
                  for r in q.to_dict("records")]
    q["reason"] = [reason(r) for r in q.to_dict("records")]
    return q.sort_values(["_rank", "site_id"]).drop(columns="_rank").reset_index(drop=True)


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
        ax.text(0, y - 0.075, f"        selected for {n} site(s)", fontsize=6.2, family="monospace",
                va="top", color=MUTED, transform=ax.transAxes)
        y -= 0.20

    ax = axes_row[2]
    ax.text(0, 1.0, "why a row is here", fontsize=7.5, fontweight="bold", family="monospace",
            va="top", color=INK, transform=ax.transAxes)
    lines = [f"{c.replace('flag_', ''):<18s}{int(ev[c].sum()) if c in ev.columns else 0:>4}   {FLAG_WHAT[c]}"
             for c in basins.FLAG_COLS]
    body = "\n".join(lines) + "\n\nFlags are ADVISORY -- they queue a site, they never change the selection."
    ax.text(0, 0.88, body, fontsize=6.4, va="top", family="monospace", color=INK_2, transform=ax.transAxes)


def _draw_basins(ax, site_id: str, pt, chosen: str | None) -> None:
    """The candidate polygons, largest behind, the selected one heavy. Basin scale, not site scale."""
    import geopandas as gpd

    ax.set_facecolor(SURFACE)
    drawn = []
    for m in basins.METHODS:
        g = basins.geometry_for(site_id, m)
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


def main(ev: pd.DataFrame | None = None) -> pd.DataFrame:
    """Render the flagged-basin panel. `ev` is the evidence table from `basins_graphs`; read from disk if absent."""
    src = config.REVIEW_DIR / "basin_candidates.csv"
    if ev is None:
        if not src.exists():
            raise FileNotFoundError(f"{src} missing -- run d4 first")
        ev = pd.read_csv(src)
    q = queue(ev)
    if not len(q):
        print("  no flagged basins; nothing to review")
        return q
    print(f"  {len(q)} site(s) queued: {q.reason.value_counts().to_dict()}", flush=True)

    sites = bio.read_parquet(config.SITES_PATH)
    pts = _panel.project(sites, key="site_id")
    si = sites.set_index("site_id")
    need = {int(c) for c in si.loc[[u for u in q.site_id if u in si.index], "comid"].dropna()}
    print(f"  loading geometry for {len(need)} COMID(s) ...", flush=True)
    catch, reaches = _panel.catchments(need), _panel.context_reaches(need)

    led = basins.basin_ledger()
    draw = _panel.cap(q, "site(s) queued", led.path)

    fig, axes = _panel.figure(len(draw) + 1, width_ratios=(0.42, 3.4, 3.4, 2.9))
    _legend(axes[0], ev)
    for i, r in enumerate(draw.to_dict("records"), start=1):
        sid = r["site_id"]
        ax_n, ax_site, ax_bas, ax_txt = axes[i]
        _panel.draw_index(ax_n, i)
        cid = si.loc[sid, "comid"] if sid in si.index else None
        cid = None if cid is None or pd.isna(cid) else int(cid)
        _panel.draw_map(ax_site, [pts[sid]], [""], [INK],
                        catch=catch[catch.comid == cid] if cid is not None else None,
                        reaches=reaches, snapped=cid)
        chosen = basins.method_of(r)
        _draw_basins(ax_bas, sid, pts[sid], chosen)
        _panel.draw_stats(ax_txt, r["reason"],
                          METHOD_COLOR.get(chosen, INK), _evidence_text(r),
                          sublines=[(sid, INK_2)])
        ax_site.set_ylabel(sid, fontsize=6, color=INK)
        ax_bas.set_title(str(r.get("station_name"))[:70], fontsize=6.5, color=INK_2, loc="left")
    # Column headers ride on the LEGEND row, not on row 1. A title on row 1 sits in the same slot as that row's station name and silently replaces it, so the first site in the queue loses the one label that says what it is.
    for ax, t in zip(axes[0][1:], ("site: snapped reach + neighbours", "candidate basins", "evidence")):
        ax.set_title(t, fontsize=8, color=INK, loc="left")

    png = config.REVIEW_DIR / "basin_review.png"
    w, h = _panel.finish(fig, len(draw) + 1,
                         f"Basin review — {len(q)} flagged of {len(ev)} sites.  "
                         f"The auto rule is basin0 if the authority has one, else basin1.  "
                         f"Overrule it in {led.path.name}", png)
    print(f"  wrote {png.name} ({w} x {h} px, {png.stat().st_size/1e6:.1f} MB)")
    return q


if __name__ == "__main__":
    main()
