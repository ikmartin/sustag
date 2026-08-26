"""The human review artifact for COMPOSED sites: one row per multi-member bundle, seen whole.

WHY THIS EXISTS BESIDE THE MERGE PANEL. The merge panel shows EDGES -- two records compared, one pair at a time -- and every answer there can be locally sound while the composed node is still wrong: three pairwise merges fuse a trio nobody ever looked at, and the mint moves the site to whichever member wins authority. This panel shows the NODE: every member's record on one timeline, who wins authority and why, what each contributes to the assembled series. `decision` takes `confirm` and nothing else -- rejecting a composition is not a token, it is editing the pairs in `merge.csv`, which is what the pairwise ledger is for.

MEMBERSHIP IS THE KEY. The sheet is keyed on the sorted member set, so re-answering a pair re-mints the key and the changed site re-queues by construction. A confirmed composition never re-asks.
"""

from __future__ import annotations

import pandas as pd

from .. import config, io as bio, ledger, panel as _panel
from ..panel import A_COLOR, B_COLOR, INK, INK_2, fmt

# One colour per member position within a row; three members is already rare.
MEMBER_COLORS = (A_COLOR, B_COLOR, "#2ca02c", "#7570b3", "#b8860b")


def _stats_text(r, day_map: dict) -> str:
    lines = [
        f"members        {int(r.n_members):>10}",
        f"canonical      {str(r.canonical_uid):>24}",
        f"rule(s)        {str(r.rule):>24}",
        f"shared reach   {str(r.comid):>24}",
        f"channels       {str(r.channels)[:34]}",
        "",
        "nitrate days by member:",
    ]
    for part in str(r.nitrate_days_by_member).split("; "):
        lines.append(f"  {part}")
    return "\n".join(lines)


def main() -> pd.DataFrame:
    """Render the composed-site panel from the sensors table's decided multi-member bundles."""
    from . import sites as sites_mod

    sensors = bio.read_parquet(config.SENSORS_PATH)
    q = sites_mod.site_review_queue(sensors)
    if not len(q):
        print("  no multi-member sites to confirm")
        return q

    led = ledger.SITE_REVIEW
    draw = _panel.cap(q, "composed site(s)", led.path)
    fig, axes = _panel.figure(len(draw) + 1, width_ratios=(0.42, 6.0, 0.1, 3.2))
    for ax in axes[0]:
        ax.axis("off")
    axes[0][1].text(0, 1.0, "`decision` must be `confirm`", fontsize=7.5, fontweight="bold",
                    family="monospace", va="top", color=INK, transform=axes[0][1].transAxes)
    axes[0][1].text(0, 0.82,
                    "Each row is ONE PROPOSED SITE -- the node your pairwise merge answers compose.\n"
                    "Confirming accepts the composition; to REJECT it, edit the pairs in merge.csv\n"
                    "(this sheet never overrides a pair). Membership is the key: change the pairs and\n"
                    "the new composition re-queues on its own row.",
                    fontsize=6.8, va="top", family="monospace", color=INK_2,
                    transform=axes[0][1].transAxes)

    for i, r in enumerate(draw.itertuples(), start=1):
        ax_n, ax_ts, _spacer, ax_txt = axes[i]
        _spacer.axis("off")
        _panel.draw_index(ax_n, i)
        members = str(r.members).split("+")
        series = [_panel.daily_record(u) for u in members]
        colors = [MEMBER_COLORS[j % len(MEMBER_COLORS)] for j in range(len(members))]
        _panel.draw_series(ax_ts, series, colors)
        _panel.draw_stats(ax_txt, "composed site", INK,
                          _stats_text(r, {}),
                          sublines=[(u, c) for u, c in zip(members, colors)])
        ax_ts.set_ylabel(str(r.members)[:28], fontsize=6, color=INK)
        ax_ts.set_title(str(r.station_names)[:100], fontsize=6.5, color=INK_2, loc="left")
    axes[1][1].set_title("every member's nitrate record, one timeline", fontsize=8, color=INK, loc="left")
    axes[1][3].set_title("the composition", fontsize=8, color=INK, loc="left")

    png = config.REVIEW_DIR / "site_review.png"
    w, h = _panel.finish(fig, len(draw) + 1,
                         f"Composed sites — {len(q)} multi-member bundle(s).  Confirm the NODE your "
                         f"pairwise answers built; edit membership in merge.csv.  Decide in {led.path.name}",
                         png)
    print(f"  wrote {png.name} ({w} x {h} px, {png.stat().st_size/1e6:.1f} MB)")
    return q


if __name__ == "__main__":
    main()
