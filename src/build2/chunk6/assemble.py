"""Turn the surviving registrations into the gauge table, and write the water under one uid per gauge.

THE PREDICATES LIVE IN `filter`, not here. This module does the two things that happen either side of them: collapsing merged registrations into one row, and writing what a consumer reads. Keeping every exclusion in one file is what makes the funnel auditable; keeping them out of this one is what makes the collapse readable.

WHAT A CONSUMER SEES. One row per physical gauge, keyed on the canonical uid. Merged registrations are already collapsed, so nothing downstream learns that `Middle Raccoon River at Panora` was published twice; non-surface types are gone, so nothing asks for the upstream basin of a well. `get_water(uid)` is a direct read of `{uid}.parquet`, which holds the merged series concatenated -- the merge is a fact on disk rather than a convention a caller has to honour.

AN UNDECIDED IDENTITY IS NOT VALID FOR MODELLING. A registration sitting in a pending merge pair is excluded, not admitted as a singleton. Admitting it is the double-count the merge block's null semantics exist to prevent: if the pair turns out to be one gauge, both halves are in the cohort claiming to be separate, and every pooled fit sees the same water twice. Excluding it costs coverage until the review is done, which is the pressure the human gate is supposed to apply.
"""

from __future__ import annotations

import pandas as pd

from .. import config, io as bio, schema


# THE COLLAPSE LIVES IN CHUNK 3, with the identity decisions that make it possible -- the graph needs the
# merged-gauge unit before this chunk runs, so deriving it here would mean deriving it twice. Imported rather
# than reimplemented: two copies of "which registrations are one gauge" is the corruption the review gate exists
# to prevent.
from ..chunk3.gauges import collapse, combined_series      # noqa: F401 -- re-exported for callers


def record_columns(g: pd.DataFrame, series: dict) -> pd.DataFrame:
    """Recompute the record span from the SCRUBBED series, so the table describes what was published.

    `collapse` derives these from chunk 1's daily files because the floor has to be answerable before the scrub runs. What lands in `sites.parquet` has to describe the series actually written, or `n_nitrate_days` counts days that the parquet beside it does not contain.
    """
    out = g.copy()
    first, last, ndays, gap = [], [], [], []
    for u in out.site_uid:
        d = daily_from_native(series[u]) if u in series else pd.DataFrame(columns=["date"])
        n, gp = bio.gap_stats(d.date) if len(d) else (0, 0)
        first.append(str(min(d.date)) if len(d) else pd.NA)
        last.append(str(max(d.date)) if len(d) else pd.NA)
        ndays.append(n)
        gap.append(gp)
    out["first_day"], out["last_day"] = first, last
    out["n_nitrate_days"], out["longest_gap_d"] = ndays, gap
    return out


def basin_columns(g: pd.DataFrame) -> pd.DataFrame:
    """Carry chunk 4's selection onto the gauge table, and rank the cohort by basin size.

    A consumer should not open a second table to learn how big a watershed is or whether anything corroborates its delineation. `area_rank` is COMPETITION ranking over the FILTERED cohort -- 1 is the largest, ties share a rank and the next one skips, so a two-way tie for second gives 1, 2, 2, 4. It is computed here, after every predicate has run, because a rank over a set that later shrinks is a rank of nothing.
    """
    import numpy as np

    out = g.copy()
    if config.BASINS.exists():
        b = pd.read_parquet(config.BASINS, columns=[
            "site_uid", "basin_method", "basin_km2", "n_reaches",
            "n_corroborations", "corroborated_by"])
        out = out.drop(columns=[c for c in b.columns if c != "site_uid"], errors="ignore")
        out = out.merge(b, on="site_uid", how="left")
    for c in ("basin_method", "basin_km2", "n_reaches", "n_corroborations", "corroborated_by"):
        if c not in out.columns:
            out[c] = schema.null_column(c, out.index, schema.GAUGE_DTYPES)
    area = pd.to_numeric(out.basin_km2, errors="coerce")
    out["log_basin_km2"] = np.log10(area.where(area > 0))
    out["area_rank"] = area.rank(ascending=False, method="min").astype("Int64")
    return out


def daily_from_native(d: pd.DataFrame) -> pd.DataFrame:
    """Standard-time-day max from a SCRUBBED native series. `date`, `nitrate_mgl_as_n`.

    Re-derived rather than read from `interim2/water/daily/`, because those files were binned in chunk 1 from the UNSCRUBBED native and would silently keep the readings this chunk removed. The binning rule is `io.daily_max`, the same one chunk 1 used, so a gauge with nothing to scrub reproduces its chunk-1 file exactly.
    """
    out = bio.daily_max(d.datetime, d.nitrate_con)
    return out.rename(columns={"value": "nitrate_mgl_as_n"})


def write_water(gauges: pd.DataFrame, series: dict) -> int:
    """Per-gauge merged series, plus the pooled table. Returns total rows.

    BOTH, because they answer different questions. `{uid}.parquet` makes a per-site read direct and makes a merge invisible by construction; `nitrate_daily.parquet` makes a pooled fit one read instead of 300. The whole cohort is under 2 MB, so holding both costs nothing and they regenerate together.

    `series` is the SCRUBBED native from `quality.assess`, so what lands on disk is what the detector judged. Writing anything else would mean the quality gate approved one series and the build published another.
    """
    config.GAUGE_WATER.mkdir(parents=True, exist_ok=True)
    # SWEEP FIRST. A gauge that drops out of the cohort -- merged away, or its record fell under the floor -- leaves its parquet behind, and a stale file is worse than a missing one: it reads cleanly and describes a site `sites.parquet` no longer lists. The pooled table is written here too and must survive the sweep.
    keep = {f"{schema.uid_to_filename(u)}.parquet" for u in gauges.site_uid} | {config.NITRATE_DAILY.name}
    stale = [p for p in config.GAUGE_WATER.glob("*.parquet") if p.name not in keep]
    for p in stale:
        p.unlink()
    if stale:
        print(f"  swept {len(stale)} stale per-gauge parquet(s)")

    pooled = []
    for r in gauges.itertuples():
        ser = daily_from_native(series[r.site_uid]).assign(site_uid=r.site_uid)
        bio.write_parquet(ser, config.GAUGE_WATER / f"{schema.uid_to_filename(r.site_uid)}.parquet")
        pooled.append(ser)
    out = pd.concat(pooled, ignore_index=True) if pooled else pd.DataFrame()
    bio.write_parquet(out[["site_uid", "date", "nitrate_mgl_as_n"]], config.NITRATE_DAILY)
    return len(out)


def gridmet_cells_eff(site_uids=None) -> pd.Series:
    """Effective gridMET cells per gauge: `(sum w)^2 / sum(w^2)` over its basin's cell weights.

    WHY NOT A COVERAGE THRESHOLD. The old build filtered on `MIN_CELL_COVERAGE`, sites owning under half a cell, and the reasoning does not survive `chunk5.weights`: the overlap is measured fractionally by the same engine that counts corn, converted to km2 rather than fractional cell counts -- because a gridMET cell is 18.7 km2 at 29.5N and 13.9 at 49.7N -- and the reduction is an area-weighted mean. There is no bookkeeping error for a small basin and nothing to correct.

    WHAT DOES SURVIVE IS RESOLUTION, and it is worth recording rather than enforcing. A basin smaller than one cell reads that cell's value, so two such basins inside the same cell receive IDENTICAL weather covariates and different nitrate -- indistinguishable in feature space by construction. The inverse participation ratio is the honest statistic: cells touched counts a sliver the same as a whole cell, this does not. It affects gridMET alone; CDL at 30 m gives a 5 km2 basin ~5,600 pixels and surplus at 250 m ~80.

    Left as a column for modelling to threshold, deliberately. No predicate reads it.
    """
    w_path = config.COMID_FEATURES / "weights_gridmet.parquet"
    if not w_path.exists() or not config.BASIN_REACHES.exists():
        return pd.Series(dtype="float64")
    reaches = pd.read_parquet(config.BASIN_REACHES, columns=["site_uid", "comid"])
    if site_uids is not None:
        reaches = reaches[reaches.site_uid.isin(set(site_uids))]
    w = pd.read_parquet(w_path, columns=["comid", "cell_id", "w_km2"])
    j = reaches.merge(w, on="comid", how="inner")
    if not len(j):
        return pd.Series(dtype="float64")
    # Collapse to the GAUGE before squaring: a cell touched by twenty catchments of one basin is one cell.
    per_cell = j.groupby(["site_uid", "cell_id"], sort=False).w_km2.sum()
    g = per_cell.groupby("site_uid")
    return (g.sum() ** 2 / g.apply(lambda v: float((v ** 2).sum()))).rename("gridmet_cells_eff")


def finalise(gauges: pd.DataFrame, series: dict) -> pd.DataFrame:
    """Record span from the scrubbed series, basin columns, the cohort ranking, and the contract check."""
    out = basin_columns(record_columns(gauges, series))
    out["assembly_status"] = pd.array(["ok"] * len(out), dtype="string")
    out["covariate_years"] = pd.array([_covariate_years()] * len(out), dtype="string")
    out = schema.conform_gauges(out)
    bad = schema.validate_gauges(out)
    if bad:
        raise ValueError("gauge table violates the contract:\n  " + "\n  ".join(bad))
    return out


def _covariate_years() -> str:
    """The span the per-COMID covariates actually cover, which is not one number.

    CDL runs 2008-2025 while surplus and fertilizer end at 2017, so a single "years covered" figure would be wrong for one product or the other. Recorded as text per product rather than as a range nobody can act on.
    """
    from .. import config as _c

    return f"crops 2008-2025; surplus/fertilizer {_c.SURPLUS_YEAR_START}-{_c.SURPLUS_YEAR_END}"
