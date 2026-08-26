"""The human review artifact for site types: one tall PNG, one row per site under review.

WHY NOT ALL 392. Every registration is typed; a minority have evidence that disagrees or no evidence at all. Putting all 392 in front of a reviewer would bury those among 118 fall-through defaults the NHD already corroborated. `site_type.ambiguous` selects, and the reason each row is here rides along in `ambiguity`.

BUT A DECIDED SITE STAYS ON THE PANEL. Answering a site clears the conflict that raised it, so selecting on current evidence alone would drop each row the moment it was reviewed -- the reviewer's own answers would erase the rows they were looking at, and every row number below would shift. `review_set` is ambiguous-now PLUS everything already decided, tagged `reviewed`, and it feeds both the panel and the decisions sheet so the two can never disagree about which row is number 7.

WHAT THE MAP SETTLES. The decisive question is whether the site is IN a waterbody or on a river threading one, and that is a picture, not a number: `WBAreaType` says "the reach touches an area polygon" and is right half the time. The panel draws the polygon, the snapped reach and the site together, so a wide river reads as a wide river.

WHAT THE SERIES SETTLES. Type has a signature. A lake damps the spring flush a stream shows; a well is nearly flat; a tidal site carries a different rhythm than a free-flowing river. For the eleven `conflict_tidal` rows -- Delaware at Trenton, Schuylkill at Philadelphia, Mississippi at Baton Rouge -- the record is the evidence that the flowline attribute alone cannot supply.
"""

from __future__ import annotations

import pandas as pd

from .. import config, io as bio, schema
from .. import _panel
from . import identity, site_type
from .._panel import A_COLOR, INK, INK_2, MUTED, WATERBODY, fmt

AMBIGUITY_COLOR = {"promote": "#2ca02c", "conflict_waterbody": "#7c3aed",
                   "conflict_tidal": "#e08214", "no_evidence": MUTED, "reviewed": INK_2}

# The reason codes are the classifier's vocabulary, not a reviewer's. Each row states its question in words
# instead, because "conflict_waterbody" does not tell you that the type was LEFT ALONE and you are being asked
# to overrule a source, while "promote" does not tell you that it was CHANGED and you are being asked to
# confirm. That asymmetry is the whole of what a reviewer needs and none of what the label conveys.
QUESTION = {
    "promote": "CHANGED to {t}: nothing was known, and the site falls inside a waterbody polygon. Right?",
    "conflict_waterbody": "KEPT as {t} ({src}), but the site falls inside a waterbody polygon. Which is right?",
    "conflict_tidal": "KEPT as {t} ({src}), but this reach is marked tidal. Tidally influenced?",
    "no_evidence": "KEPT as {t} by default: no evidence either way. What is it?",
    "reviewed": "DECIDED as {t} by review. The evidence that raised it is settled; change it here to revisit.",
}


def question(r) -> str:
    """The row's decision, phrased as the question being asked rather than as the rule that fired."""
    src = str(r.site_type_source).replace("_", " ")
    return QUESTION[r.ambiguity].format(t=r.site_type, src=src)

SITE_COLUMN = "Site"
DECISION_COLUMNS = [SITE_COLUMN, "site_uid",
                    "decision", "decided_by", "note",
                    "question", "station_name", "site_type", "site_type_source",
                    "site_type_conflict", "ambiguity", "FTYPE", "WBAreaType", "Tidal", "snap_dist_m"]


def out_dir():
    return _panel.review_dir("site-type-review")


def sync_decisions_file(amb: pd.DataFrame) -> tuple[int, int, int]:
    """Reconcile `site_type_decisions.csv` with the current review set. Returns (added, kept, retired).

    `decision` is BLANK for a new row, never seeded from the current type, and takes a value from `site_type.VALID_TYPES`. A site that stops being ambiguous -- because its evidence changed, or because deciding it cleared the conflict -- keeps its row at the bottom rather than losing the decision.
    """

    if not len(amb):
        return 0, 0, 0
    a = amb.copy()
    a["question"] = [question(r) for r in a.itertuples()]
    return _panel.sync_sheet(config.TYPE_DECISIONS, a, ["site_uid"], SITE_COLUMN, DECISION_COLUMNS)


load_decisions = site_type.load_decisions      # single definition; classify() applies them


def review_set(sites: pd.DataFrame) -> pd.DataFrame:
    """Sites the review covers: ambiguous AND carrying a record, plus everything already decided that still does.

    ONE FRAME FOR THE PANEL AND THE SHEET, which is the whole point. Answering a site clears the conflict that raised it, so a set computed from the current evidence alone shrinks as the review progresses -- the panel would drop each row as it was answered, the sheet would retire it, and the numbers a reviewer works from would shift under them. Deciding something is not a reason to stop showing it.

    A RECORD IS WHAT MAKES A TYPE MATTER. Typing decides whether a site can hold a surface basin and enter the cohort; a registration with no nitrate record leaves under `no_record` first, so its type changes nothing that is ever read. Once the census was snapped, 711 of 735 outstanding rows were exactly that -- 628 of them one tidal-flag conflict -- and NONE carried a single nitrate day. A queue that long is not read at all, so asking about them costs the answers to the ones that matter.

    Not asking is not deciding: the classifier's type stands, `site_type_auto.csv` records what it chose, and a site that later gains a record arrives in this queue on the next build because the test is re-evaluated every run.
    """
    amb = site_type.ambiguous(sites, include=set(load_decisions().site_uid))
    return amb[blocks(amb)].reset_index(drop=True) if len(amb) else amb


def blocks(amb: pd.DataFrame) -> pd.Series:
    """Which ambiguous sites must be answered: the ones carrying a nitrate record."""
    if not len(amb):
        return pd.Series(dtype="bool")
    return pd.to_numeric(amb.get("n_nitrate_days"), errors="coerce").fillna(0) > 0


def auto_set(sites: pd.DataFrame) -> pd.DataFrame:
    """Ambiguous sites the classifier settles without asking. Written out so the silence is inspectable."""
    amb = site_type.ambiguous(sites, include=set(load_decisions().site_uid))
    return amb[~blocks(amb)].reset_index(drop=True) if len(amb) else amb


def pending(amb: pd.DataFrame) -> list[str]:
    """Uids under review with no decision on file, in review order. A `reviewed` row is never pending."""
    dec = load_decisions()
    done = set(dec.site_uid) if len(dec) else set()
    return [u for u in amb.site_uid if u not in done]


def _legend(axes_row, counts: dict) -> None:
    """Row 0: the whole type vocabulary, so a reviewer never has to look it up elsewhere.

    The reference belongs IN the artifact. `decision` takes one of these and nothing else -- an unrecognised value raises rather than being ignored -- and which of them are excluded from the cohort is the consequence that makes a type decision matter.

    ONE COLUMN, in the middle. All 14 fit in a single row's height, and a single list reads as a list; splitting it across the three panels turned a short reference into three fragments a reader has to reassemble.
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
            + "\n\n* = no upstream drainage at all"
            + "\n+ = drains an area far smaller than its catchment"
            + "\n    both are dropped from sites.parquet; neither is dropped from the build")
    ax.text(0, 0.90, body, fontsize=7, va="top", family="monospace",
            color=INK_2, transform=ax.transAxes)


def _marker(t: str) -> str:
    """Legend marker for a type: `*` no drainage, `+` sub-catchment, blank trainable.

    TWO SYMBOLS, NOT ONE. A single marker meaning `NO_SURFACE_BASIN` reads `saturated_buffer` as trainable, which it is not -- it leaves the cohort for a different reason, and a reviewer choosing a type has to see both consequences.
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


def main(force: bool = False) -> pd.DataFrame:
    """Render the site-type panel and its stats. Returns the review frame."""
    reg = pd.read_parquet(config.REGISTRATIONS_PATH)
    amb = review_set(reg)
    if not len(amb):
        print("  no site types under review")
        return amb
    print(f"  {len(amb)} site(s) under review: {amb.ambiguity.value_counts().to_dict()}", flush=True)

    need = {int(c) for c in amb.comid.dropna()}
    catch, reaches = _panel.catchments(need), _panel.context_reaches(need)
    water = site_type.waterbodies(amb.WBAREACOMI)
    pts = _panel.project(reg)
    series = {u: _panel.daily_record(u) for u in amb.site_uid}

    counts = reg.site_type.value_counts().to_dict()
    # +1 for the vocabulary row at the top. `cap` bounds the drawing only -- `site_type_candidates.csv`
    # below is written from the full frame.
    draw = _panel.cap(amb, "site(s) under review", config.TYPE_DECISIONS)
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
        _panel.draw_stats(ax_txt, _wrap(question(r), 46),
                          AMBIGUITY_COLOR.get(r.ambiguity, INK), _stats_text(r),
                          sublines=[(r.site_uid, A_COLOR)])
        ax_map.set_ylabel(r.site_uid, fontsize=6, color=INK)
        ax_ts.set_title(str(r.station_name)[:70], fontsize=6.5, color=INK_2, loc="left")
    axes[1][1].set_title("catchment + reach + waterbody", fontsize=8, color=INK, loc="left")
    axes[1][3].set_title("evidence", fontsize=8, color=INK, loc="left")

    d = out_dir()
    png = d / "site_type_review.png"
    w, h = _panel.finish(fig, len(draw) + 1,
                         f"Site type review — {len(draw)} of {len(amb)} under review.  "
                         f"CHANGED = the type was altered, confirm it.  KEPT = it was left alone, "
                         f"overrule it if the evidence is better.  Decide in site_type_decisions.csv", png)
    amb.to_csv(config.TYPE_CANDIDATES, index=False)
    cols = [c for c in DECISION_COLUMNS if c in amb.columns and c not in ("decision", "decided_by", "note")]
    amb[cols].to_csv(d / "site_type_review_stats.csv", index=False)
    print(f"  wrote {png.name} ({w} x {h} px, {png.stat().st_size/1e6:.1f} MB) "
          f"and site_type_candidates.csv ({len(amb)} rows)")
    return amb


if __name__ == "__main__":
    main()
