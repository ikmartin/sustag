"""Shared paths, unit harmonisation and cohort geometry for the discrete-sample pre-check.

Pre-check for notes/discrete-sample-report.md. Nothing here writes to src/data/ or the pipeline -- every artifact lands in ./cache/.

THE UNIT TRAP, which is why harmonisation lives in the shared module rather than in one script. The Water Quality Portal returns the same USGS measurements twice, under `mg/l as N` and `mg/l asNO3`, differing by the stoichiometric factor 62.00/14.01 = 4.4269. Measured on the USGS Iowa WSC subset: 3,816 results under each code, ratio of medians 4.43. Pooled unconverted the median nitrate reads 24.05 instead of 5.44 and the >=10 mg/L exceedance rate reads 76% instead of 19%. It does not look like an error in any summary statistic -- it looks like a dirtier state.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1]))  # repo root
CACHE = _HERE / "cache"
CACHE.mkdir(exist_ok=True)

from src.data.access import get_site_ids  # noqa: E402

NO3_TO_N = 14.01 / 62.00  # multiply an as-NO3 value by this to get as-N

# Unit strings seen in Iowa WQP nitrate results -> multiplier onto mg/L as N. Anything absent is DROPPED rather than guessed: a mystery unit silently treated as as-N is exactly the failure this table exists to prevent.
UNIT_FACTOR = {
    "mg/l as n": 1.0,
    "mg/l": 1.0,  # WQP's bare mg/l on nitrate is as-N by USGS convention; flagged in the audit
    "mg/l asno3": NO3_TO_N,
    "mg/l as no3": NO3_TO_N,
    "ug/l as n": 0.001,
    "ppm": 1.0,
}

VIOLATION_MGL = 10.0  # the drinking-water standard the CLF target is built on, as N


def cohort() -> list[str]:
    return [str(s) for s in get_site_ids()]


def harmonise(df: pd.DataFrame, value_col="ResultMeasureValue", unit_col="ResultMeasure/MeasureUnitCode") -> pd.DataFrame:
    """Add `n_mgl` (nitrate as N, mg/L) and `unit_ok`. Rows in an unrecognised unit get NaN, never a guess."""
    v = pd.to_numeric(df[value_col], errors="coerce")
    u = df[unit_col].astype(str).str.strip().str.lower()
    f = u.map(UNIT_FACTOR)
    return df.assign(n_mgl=v * f, unit_norm=u, unit_ok=f.notna())


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse the as-N / as-NO3 duplication.

    Keyed on (station, datetime, harmonised value) rather than on the raw value, because the duplicate pair differs by 4.43 in raw units and is IDENTICAL only after conversion -- which is what makes the conversion self-checking. Rounded to 4dp so floating-point conversion noise does not defeat the match.
    """
    d = df.dropna(subset=["n_mgl"]).copy()
    d["_k"] = d.n_mgl.round(4)
    return d.drop_duplicates(subset=["MonitoringLocationIdentifier", "ActivityStartDate", "_k"]).drop(columns="_k")


def is_volunteer(org: pd.Series) -> pd.Series:
    """The Iowa Volunteer Water Monitoring Program is 73% of all nitrate stations, and a single methodological population. Not disqualifying, but every coverage number is reported both ways so the decision is explicit."""
    return org.astype(str).str.contains("Volunteer", case=False, na=False)


def say(msg: str) -> None:
    print(msg, flush=True)
