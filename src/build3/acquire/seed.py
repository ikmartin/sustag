"""A1 -- seed discovery and seed basins: the bootstrap that lets the extent exist.

THE ONE ACQUISITION STAGE SCOPED BY THE BOXES rather than the AOE, because the AOE does not exist yet: D1 derives the extent from this pass's basins, and the extent then pins acquisition's scope. The AOE never re-expands on its own, so the bootstrap runs once -- staged initialization, not a cycle.

Seed marking IS NOT A COHORT FILTER: a non-seed keeps its row and every column; it only loses a vote on how far the AOE reaches. Conservative by construction -- the only evidence is advertised metadata, a SPAN not a count, so exclude only what CANNOT clear the bar and unknown span seeds. Wrongly seeding a thin site costs a slightly larger AOE; wrongly excluding a real one truncates the upstream half of its basin, and nothing downstream reports that the covariates it trains on are missing.
"""

from __future__ import annotations

import pandas as pd

from .. import config, contracts
from .. import io as bio
from . import discovery, seed_basins

# Advertised span, not observed days, and not any modelling floor. Measured when this dropped from 60:
# two more seeds, and BOTH basins already lay inside the AOE -- the union and stamp were unchanged.
SEED_MIN_SPAN_DAYS = 1

NO_SERIES = "no_nitrate_series"
SHORT_SPAN = "span_under_min"


def _span_days(begin, end) -> pd.Series:
    b = pd.to_datetime(begin, errors="coerce", utc=True)
    e = pd.to_datetime(end, errors="coerce", utc=True)
    return (e - b).dt.days


def mark_seeds(sensors: pd.DataFrame, min_span_days: int = SEED_MIN_SPAN_DAYS) -> pd.DataFrame:
    """Fill `aoe_seed` and `seed_exclusion_reason` for every row.

    Two exclusions, both decidable from metadata alone: null begin AND end where the source reports spans at all (a registration with no series, different from a short one), and a span that cannot contain one observation day. A null span where the source publishes no spans is missing evidence, not evidence of absence -- excluding on it would have dropped all 227 USGS seeds the first time this ran against a table built before the column existed. IWQIS and MPCA publish no spans and seed by default; their records are on local disk and cost nothing to confirm.
    """
    out = sensors.copy()
    span = _span_days(out.nitrate_begin, out.nitrate_end)
    reports_span = out.groupby("source").nitrate_begin.transform(lambda s: s.notna().any())

    reason = pd.Series(pd.NA, index=out.index, dtype="object")
    reason[reports_span & out.nitrate_begin.isna() & out.nitrate_end.isna()] = NO_SERIES
    reason[span.notna() & (span < min_span_days) & reason.isna()] = SHORT_SPAN

    out["aoe_seed"] = reason.isna()
    out["seed_exclusion_reason"] = reason.astype("string")
    return out


def main(force: bool = False, snapshot: str | None = None) -> pd.DataFrame:
    config.ensure_dirs()
    print("  discovering (nitrate pass, seed boxes):")
    sensors = discovery.run(census=False)
    sensors = mark_seeds(sensors)
    n = int(sensors.aoe_seed.sum())
    by = sensors[sensors.aoe_seed].source.value_counts().to_dict()
    print(f"  seeds {n}/{len(sensors)}  {by}")
    print(f"  excluded: {sensors[~sensors.aoe_seed].seed_exclusion_reason.value_counts().to_dict() or 'none'}")

    g = seed_basins.fetch_seed_basins(sensors, force=force)
    if len(g):
        idx = g.set_index("site_uid")
        for col in ("nldi_basin_km2", "nldi_basin_flag", "nldi_basin_method"):
            if col in idx.columns:
                sensors[col] = sensors.site_uid.map(idx[col])
        area = pd.to_numeric(idx.nldi_basin_km2, errors="coerce")
        print(f"  {len(g)} basins, median {area.median():,.0f} km2, largest {area.max():,.0f} km2")

    # A1 IS A SUBSET PASS AND MUST NOT DEFINE THE ROW SET. The census (a2) legitimately re-derives the
    # whole table -- a full re-discovery is the row authority. This nitrate-only pass discovers a few
    # hundred of 20,000+ rows, and writing them as THE table once gutted a censused cohort to 407 rows and
    # blanked every derived block behind it (the census then carried blocks forward from the gutted table,
    # so the loss looked like a2's). Rows the seed pass discovered get their discovery+seed columns
    # updated; every other row, and every carried block, passes through untouched.
    if config.SENSORS_PATH.exists():
        prev = pd.read_parquet(config.SENSORS_PATH)
        new_uids = set(sensors.site_uid) - set(prev.site_uid)
        # dict.fromkeys, because the nldi columns are already discovery-block members and a duplicated
        # column name makes DataFrame.update's internal mask a frame -- "truth value ambiguous", far away
        seed_cols = list(dict.fromkeys(
            [c for c, _, blk, _ in contracts.SENSOR_COLUMNS if blk == "discovery" and c != "site_uid"]
            + ["nldi_basin_km2", "nldi_basin_flag", "nldi_basin_method"]))
        seed_cols = [c for c in seed_cols if c in sensors.columns]
        merged = pd.concat([prev, contracts.conform_sensors(
            sensors[sensors.site_uid.isin(new_uids)])], ignore_index=True)
        merged = contracts.update_block(merged, sensors, seed_cols)
        out = contracts.conform_sensors(merged)
        print(f"  updated {len(sensors):,} row(s) in place ({len(new_uids)} new); "
              f"{len(out):,} registrations total, derived blocks untouched")
    else:
        out = contracts.conform_sensors(sensors)
        print(f"  wrote sensors table: {len(out):,} registrations")
    bad = contracts.validate_sensors(out)
    if bad:
        raise ValueError("sensors table violates the contract:\n  " + "\n  ".join(bad))
    bio.write_parquet(out, config.SENSORS_PATH,
                      stamps={"snapshot": snapshot or "",
                              "schema": contracts.schema_fingerprint(contracts.SENSOR_DTYPES)})
    return out
