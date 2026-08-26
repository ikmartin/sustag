"""Registrations collapse into PHYSICAL GAUGES — the unit everything after identity is keyed on.

A registration is how a source publishes a sensor; a gauge is the sensor. `Middle Raccoon River at Panora` was published twice and is one gauge, and until that is resolved every downstream count is wrong: two basins for one place, two nodes on one reach, one river's water in the training set twice.

THE COLLAPSE BELONGS HERE, WITH THE DECISIONS THAT MAKE IT POSSIBLE. It used to live in the assembly chunk, which meant the graph had no unit to build nodes from without re-deriving identity for itself. Identity is decided in this chunk; the table it implies is written in this chunk.

EVERY TYPE, NOT ONLY SURFACE ONES. `gauges.parquet` holds a row for each physical gauge whatever it is -- a well is a gauge. Deciding that a well cannot be a graph node, or cannot be trained on, is a modelling judgement made later against different evidence; making it here would leave no table that simply says what exists. This is the same reason `registrations.parquet` never loses a row.

A PENDING IDENTITY IS NOT A GAUGE YET. A registration in an undecided merge pair carries a null `physical_gauge_group` and is skipped, because admitting it as a singleton is exactly the double-count the review gate exists to prevent -- if the pair turns out to be one gauge, both halves are already in the table claiming to be separate.
"""

from __future__ import annotations

import pandas as pd

from .. import config, io as bio, schema


def combined_series(uids: list[str]) -> pd.DataFrame:
    """The merged daily nitrate record across a gauge's members: `date`, `nitrate_mgl_as_n`.

    UNSCRUBBED, and used only to describe the record. Chunk 6 recomputes these columns from the scrubbed series before publishing, so what `sites.parquet` reports always describes the series actually written; here the question is only how long the gauge has observed, which has to be answerable before any scrub has run.

    OVERLAP RESOLVES BY MAX, the rule `io.daily_max` already applies within a day. It only bites on a `dual_registration` merge; a sequential merge has no shared days by definition.
    """
    frames = []
    for u in uids:
        p = config.WATER_DAILY / f"{schema.uid_to_filename(u)}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p)[["date", "nitrate_mgl_as_n"]])
    if not frames:
        return pd.DataFrame({"date": pd.Series(dtype="object"),
                             "nitrate_mgl_as_n": pd.Series(dtype="float64")})
    d = pd.concat(frames, ignore_index=True)
    return d.groupby("date", as_index=False).nitrate_mgl_as_n.max().sort_values(
        "date", ignore_index=True)


def collapse(sites: pd.DataFrame) -> pd.DataFrame:
    """One row per physical gauge, on the gauge contract.

    Descriptive columns come from the canonical member; the record columns are recomputed over the COMBINED series, because a sequential merge spans both halves and copying the canonical member's span would understate it by however long the other registration ran.

    Columns the later chunks own -- the basin, the graph, the assembly verdict -- are absent here and `conform_gauges` writes them as real nulls, which is the contract's way of saying "not decided yet" rather than "not applicable".
    """
    elig = sites[sites.physical_gauge_group.notna()]
    rows = []
    for gid, grp in elig.groupby("physical_gauge_group", sort=True):
        canon = grp[grp.is_group_canonical == True]                        # noqa: E712 -- Boolean dtype
        canon = (canon if len(canon) else grp).iloc[0]
        # Canonical first, so `member_uids` reads as "this gauge, then what merged into it".
        members = [canon.site_uid] + sorted(set(grp.site_uid) - {canon.site_uid})
        ser = combined_series(members)
        n_days, gap = bio.gap_stats(ser.date) if len(ser) else (0, 0)
        r = {c: canon.get(c) for c in schema.GAUGE_COLS
             if c in elig.columns and c not in ("first_day", "last_day",
                                                "n_nitrate_days", "longest_gap_d")}
        r.update(site_uid=canon.site_uid, member_uids="+".join(members),
                 n_registrations=len(members),
                 first_day=str(min(ser.date)) if len(ser) else pd.NA,
                 last_day=str(max(ser.date)) if len(ser) else pd.NA,
                 n_nitrate_days=n_days, longest_gap_d=gap)
        rows.append(r)
    return schema.conform_gauges(pd.DataFrame(rows))


def main(sites: pd.DataFrame, write: bool = True) -> pd.DataFrame:
    """Collapse and persist `gauges.parquet`. Returns the gauge table."""
    g = collapse(sites)
    bad = schema.validate_gauges(g, surface_only=False)
    if bad:
        raise ValueError("gauge table violates the contract:\n  " + "\n  ".join(bad))
    pending = int(sites.physical_gauge_group.isna().sum())
    merged = int((g.n_registrations > 1).sum())
    print(f"  {len(sites) - pending} decided registration(s) -> {len(g)} physical gauge(s) "
          f"({merged} merged from more than one)"
          + (f"; {pending} held back on a pending identity" if pending else ""))
    if write:
        bio.write_parquet(g, config.GAUGES_PATH)
        print(f"  wrote {config.GAUGES_PATH.name}")
    return g
