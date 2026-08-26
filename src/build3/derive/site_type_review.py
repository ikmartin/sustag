"""The human review artifact for site types: one tall PNG, one row per sensor under review.

WHY NOT EVERY REGISTRATION. Every registration is typed; a minority have evidence that disagrees or no evidence at all, and only the RECORDED ones among those matter to any downstream reader. `sites.type_review_set` selects, and the reason each row is here rides along in `ambiguity`.

A DECIDED ROW STAYS ON THE PANEL. Answering a sensor clears the conflict that raised it, so selecting on current evidence alone would drop each row the moment it was reviewed -- the reviewer's own answers would erase the rows they were looking at, and every row number below would shift.

WHAT THE MAP SETTLES. The decisive question is whether the sensor is IN a waterbody or on a river threading one, and that is a picture, not a number: `WBAreaType` says "the reach touches an area polygon" and is right half the time. The panel draws the polygon, the snapped reach and the sensor together, so a wide river reads as a wide river.

WHAT THE SERIES SETTLES. Type has a signature. A lake damps the spring flush a stream shows; a well is nearly flat; a tidal site carries a different rhythm than a free-flowing river. For a `conflict_tidal` row the record is the evidence the flowline attribute alone cannot supply.
"""

from __future__ import annotations

import pandas as pd

from .. import config, io as bio, panel as _panel
from ..panel import A_COLOR, INK, INK_2, MUTED, fmt
from . import site_type, sites

AMBIGUITY_COLOR = {"promote": "#2ca02c", "conflict_waterbody": "#7c3aed",
                   "conflict_tidal": "#e08214", "no_evidence": MUTED, "reviewed": INK_2}

# Evidence columns synced into the decisions sheet beside each question.
SHEET_EVIDENCE = ["question", "station_name", "site_type", "site_type_source",
                  "site_type_conflict", "ambiguity", "FTYPE", "WBAreaType", "Tidal", "snap_dist_m"]


def _legend(axes_row, counts: dict) -> None:
    """Row 0: the whole type vocabulary, so a reviewer never has to look it up elsewhere.

    The reference belongs IN the artifact. `decision` takes one of these and nothing else -- an unrecognised value raises rather than being ignored -- and what a type costs the site downstream is the consequence that makes the decision matter.
    """
    for ax in axes_row:
        ax.axis("off")
    ax = axes_row[2]
    lines = [f"{_marker(x)} {x:<18s}{counts.get(x, 0):>5}" for x in site_type.VALID_TYPES]
    ax.text(0, 1.0, "site type vocabulary — `decision` must be one of these",
            fontsize=7.5, fontweight="bold", family="monospace",
            va="top", color=INK, transform=ax.transAxes)
    # The footnote rides in the SAME text block, separated by a blank line, so matplotlib spaces it. Placing
    # it with a computed axes-fraction offset means guessing the line height, and guessing it low lands the
    # note on top of the last row.
    body = ("\n".join(lines)
            + "\n\n* = no upstream drainage: the site keeps its record but mints an OFF- id --"
            + "\n    no basin, no graph node"
            + "\n+ = drains far less than its catchment: keeps its network position, joins no graph"
            + "\n    both are published, typed; neither is dropped")
    ax.text(0, 0.90, body, fontsize=7, va="top", family="monospace",
            color=INK_2, transform=ax.transAxes)


def _marker(t: str) -> str:
    """Legend marker for a type: `*` no drainage, `+` sub-catchment, blank surface-network.

    TWO SYMBOLS, NOT ONE. A single marker meaning `NO_SURFACE_BASIN` reads `saturated_buffer` as an ordinary node, which it is not -- it leaves the graph for a different reason, and a reviewer choosing a type has to see both consequences.
    """
    if t in site_type.NO_SURFACE_BASIN:
        return "*"
    return "+" if t in site_type.SUB_CATCHMENT else " "


def _wrap(s: str, width: int) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(s, width))


def _stats_text(r) -> str:
    return "\n".join([
        f"current type   {str(r.site_type):>16}",
        f"decided by     {str(r.site_type_source):>16}",
        f"disagreement   {str(r.site_type_conflict if pd.notna(r.site_type_conflict) else '--'):>16}",
        "",
        f"source string  {str(r.site_type_raw)[:34] if pd.notna(r.site_type_raw) else '--'}",
        "",
        f"reach FTYPE    {str(r.FTYPE):>16}",
        f"WBAreaType     {str(r.WBAreaType).strip() or '--':>16}",
        f"waterbody id   {int(r.WBAREACOMI) if pd.notna(r.WBAREACOMI) and r.WBAREACOMI > 0 else '--':>16}",
        f"Tidal          {'yes' if r.Tidal == 1 else 'no':>16}",
        f"stream order   {str(r.StreamOrde):>16}",
        f"snap dist      {fmt(r.snap_dist_m, '.1f'):>16} m  {r.snap_status}",
        "",
        f"drainage km2   {fmt(r.source_drainage_area_km2, '.1f'):>16}",
        f"catchment km2  {fmt(r.catchment_area_km2, '.2f'):>16}",
    ])


def main() -> pd.DataFrame:
    """Render the site-type panel and its stats. Returns the review frame."""
    sensors = bio.read_parquet(config.SENSORS_PATH)
    amb = sites.type_review_set(sensors)
    if not len(amb):
        print("  no site types under review")
        return amb
    print(f"  {len(amb)} sensor(s) under review: {amb.ambiguity.value_counts().to_dict()}", flush=True)

    need = {int(c) for c in amb.comid.dropna()}
    catch, reaches = _panel.catchments(need), _panel.context_reaches(need)
    water = site_type.waterbodies(amb.WBAREACOMI)
    pts = _panel.project(sensors, key="site_uid")
    series = {u: _panel.daily_record(u) for u in amb.site_uid}

    counts = sensors.site_type.value_counts().to_dict()
    # +1 for the vocabulary row at the top. `cap` bounds the drawing only -- the sheet holds the full frame.
    tled = site_type.type_ledger()
    draw = _panel.cap(amb, "sensor(s) under review", tled.path)
    fig, axes = _panel.figure(len(draw) + 1)
    _legend(axes[0], counts)
    for i, r in enumerate(draw.itertuples(), start=1):
        ax_n, ax_map, ax_ts, ax_txt = axes[i]
        _panel.draw_index(ax_n, i)
        sub = catch[catch.comid == r.comid] if pd.notna(r.comid) else None
        wb = water[water.comid == int(r.WBAREACOMI)] if (
            len(water) and pd.notna(r.WBAREACOMI) and r.WBAREACOMI > 0) else None
        _panel.draw_map(ax_map, [pts[r.site_uid]], [""], [A_COLOR],
                        catch=sub, reaches=reaches, snapped=r.comid, water=wb)
        _panel.draw_series(ax_ts, [series.get(r.site_uid)], [A_COLOR])
        _panel.draw_stats(ax_txt, _wrap(site_type.question(r), 46),
                          AMBIGUITY_COLOR.get(r.ambiguity, INK), _stats_text(r),
                          sublines=[(r.site_uid, A_COLOR)])
        ax_map.set_ylabel(r.site_uid, fontsize=6, color=INK)
        ax_ts.set_title(str(r.station_name)[:70], fontsize=6.5, color=INK_2, loc="left")
    axes[1][1].set_title("catchment + reach + waterbody", fontsize=8, color=INK, loc="left")
    axes[1][3].set_title("evidence", fontsize=8, color=INK, loc="left")

    png = config.REVIEW_DIR / "site_type_review.png"
    w, h = _panel.finish(fig, len(draw) + 1,
                         f"Site type review — {len(draw)} of {len(amb)} under review.  "
                         f"CHANGED = the type was altered, confirm it.  KEPT = it was left alone, "
                         f"overrule it if the evidence is better.  Decide in {tled.path.name}", png)
    cols = [c for c in ("site_uid", *SHEET_EVIDENCE) if c in amb.columns or c == "question"]
    stats = amb.copy()
    stats["question"] = [site_type.question(r) for r in stats.itertuples()]
    stats[[c for c in cols if c in stats.columns]].to_csv(
        config.REVIEW_DIR / "site_type_review_stats.csv", index=False)
    print(f"  wrote {png.name} ({w} x {h} px, {png.stat().st_size/1e6:.1f} MB) "
          f"and site_type_review_stats.csv ({len(amb)} rows)")
    return amb


if __name__ == "__main__":
    main()
