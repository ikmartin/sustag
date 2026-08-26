"""Which registrations get to define the AOE.

THIS IS NOT A COHORT FILTER. A non-seed keeps its row, its records and every column; the only thing it loses is a vote on how far the AOE reaches. The build never drops a site, and chunk 0 is not an exception -- it decides whose basin is unioned into the geometry, nothing else.

CONSERVATIVE BY CONSTRUCTION, because the only evidence here is metadata. The source advertises a SPAN, not a count, so a site claiming fifteen years might hold forty scattered observations. Everything here is therefore an upper bound: exclude only what CANNOT clear the bar. Unknown span means seed.

THE BAR IS ONE DAY, not sixty. Record length is no longer judged anywhere near here: chunk 1 archives anything with a nitrate observation and `chunk6.assemble.MIN_GAUGE_DAYS` decides trainability at the end, over the MERGED record. So a thin site may still merge into a real gauge, and its basin is then part of that gauge's drainage -- excluding it from the extent on span alone would truncate the AOE for a site that turns out to matter. Only a registration advertising no series at all, or a zero-length one, is excluded now.

Getting this backwards is expensive in one direction only. Wrongly seeding a thin site costs a slightly larger AOE. Wrongly excluding a real one truncates the upstream half of its basin, and nothing downstream reports that the covariates it trains on are missing.
"""

from __future__ import annotations

import pandas as pd

from .. import schema

# Advertised span, not observed days, and not the modelling floor -- see the module docstring. Measured when this dropped from 60: two more seeds (`03302060` at 1 day, `03432800` at 22), and BOTH basins already lie entirely inside the AOE, so the union and `aoe_stamp` are unchanged and no chunk-1 artifact was invalidated.
SEED_MIN_SPAN_DAYS = 1

NO_SERIES = "no_nitrate_series"
SHORT_SPAN = "span_under_min"


def _span_days(begin, end) -> pd.Series:
    b = pd.to_datetime(begin, errors="coerce", utc=True)
    e = pd.to_datetime(end, errors="coerce", utc=True)
    return (e - b).dt.days


def mark_seeds(sites: pd.DataFrame, min_span_days: int = SEED_MIN_SPAN_DAYS) -> pd.DataFrame:
    """Fill `aoe_seed` and `seed_exclusion_reason` for every row.

    Two exclusions, both decidable from metadata alone. A registration with null `begin` AND null `end` has no nitrate series at all -- `USGS:05374900` and `USGS:01161280` are registered under 99133 with `NaT` on both, and return nothing when fetched. A span under `min_span_days` cannot contain even one day of observation, which at the current floor means `begin == end`: `USGS:381704085491802`, a riverboat sampled once.

    Everything else seeds, including any row whose span is unknown. IWQIS and MPCA publish no span in their registries, so they seed by default; that is the intended bias, since their records are already on local disk and cost nothing to confirm later.
    """
    out = sites.copy()
    for c in ("nitrate_begin", "nitrate_end"):
        if c not in out.columns:
            out[c] = schema.null_column(c, out.index)
    span = _span_days(out.nitrate_begin, out.nitrate_end)

    # A null span means "no series" only where the source DID report spans for other sites. An absent column, or a source that publishes no spans at all, is missing evidence rather than evidence of absence -- and excluding on that would have dropped all 227 USGS seeds the first time this ran against a table built before the column existed.
    reports_span = out.groupby("source").nitrate_begin.transform(lambda s: s.notna().any())

    reason = pd.Series(pd.NA, index=out.index, dtype="object")
    reason[reports_span & out.nitrate_begin.isna() & out.nitrate_end.isna()] = NO_SERIES
    reason[span.notna() & (span < min_span_days) & reason.isna()] = SHORT_SPAN

    out["aoe_seed"] = reason.isna()
    out["seed_exclusion_reason"] = reason.astype("string")
    return out


def summarise(sites: pd.DataFrame) -> str:
    n = int(sites.aoe_seed.sum())
    by = sites[~sites.aoe_seed].seed_exclusion_reason.value_counts().to_dict()
    per = sites[sites.aoe_seed].source.value_counts().to_dict()
    return f"  seeds {n}/{len(sites)}  {per}\n  excluded: {by or 'none'}"
