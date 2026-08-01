"""Temporal decomposition of the basin-containment graph: one entry per interval over which the active-site set is constant.

The containment graph in `processed/aux/basin_containment_graph.parquet` is static -- it carries every edge that has EVER existed across the cohort's whole 2008-2026 record. On any given day the real graph is the subgraph induced by the sites actually deployed that day, and nothing outside `src/splits/conflict_graph.py` currently accounts for that.

DEPLOYMENT, NOT REPORTING. A site is introduced on the first day it reports nitrate and retired the day AFTER its last. Gaps inside that span -- NaNs, sensor outages, winter shutdowns -- are NOT node deletions. So the active set changes at most twice per site, which bounds the number of distinct graphs at 2 x cohort and makes the decomposition finite and small (159 intervals over 116 sites, against a 232 ceiling).

Intervals with an empty active set are dropped, so consecutive entries may not be contiguous in time -- a gap means no site was reporting at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from src.data.access import get_all_water, get_site_ids  # noqa: E402

HERE = Path(__file__).resolve().parent
INTERVALS_JSON = HERE / "intervals.json"


def site_spans() -> pd.DataFrame:
    """First and last day each cohort site reports a non-NaN nitrate value. Index site_uid, columns first/last (datetime64, tz-naive, floored to the day)."""
    cohort = {str(s) for s in get_site_ids()}
    w = get_all_water()
    w = w[w.nitrate_con.notna() & w.site_uid.isin(cohort)].copy()
    w["day"] = pd.to_datetime(w.datetime, utc=True).dt.tz_localize(None).dt.floor("D")
    return w.groupby("site_uid").day.agg(first="min", last="max")


def build_intervals(spans: pd.DataFrame | None = None) -> list[dict]:
    """List of {start_date, end_date, active_sites} over which the active set is constant, earliest start first.

    Dates are 'YYYY-MM-DD' strings and both ends are INCLUSIVE. active_sites is sorted, so two runs of this function produce byte-identical output.
    """
    spans = site_spans() if spans is None else spans

    events: dict[pd.Timestamp, list[tuple[str, int]]] = {}
    for uid, r in spans.iterrows():
        events.setdefault(r["first"], []).append((uid, +1))
        events.setdefault(r["last"] + pd.Timedelta(days=1), []).append((uid, -1))

    dates = sorted(events)
    last_day = spans["last"].max()
    active: set[str] = set()
    out: list[dict] = []
    for i, day in enumerate(dates):
        for uid, delta in events[day]:
            active.add(uid) if delta > 0 else active.discard(uid)
        end = dates[i + 1] - pd.Timedelta(days=1) if i + 1 < len(dates) else last_day
        if active and end >= day:
            out.append(
                {
                    "start_date": day.strftime("%Y-%m-%d"),
                    "end_date": end.strftime("%Y-%m-%d"),
                    "active_sites": sorted(active),
                }
            )
    return out


def load() -> list[dict]:
    """Read intervals.json, building and caching it first if absent."""
    if not INTERVALS_JSON.exists():
        main()
    return json.loads(INTERVALS_JSON.read_text())


def main() -> None:
    iv = build_intervals()
    INTERVALS_JSON.write_text(json.dumps(iv, indent=2) + "\n")
    n = [len(e["active_sites"]) for e in iv]
    print(f"{len(iv)} intervals -> {INTERVALS_JSON}")
    print(f"  {iv[0]['start_date']} .. {iv[-1]['end_date']}   active sites: min {min(n)}, max {max(n)}")


if __name__ == "__main__":
    main()
