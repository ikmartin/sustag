"""Atomic writes, and the local-calendar-day rule.

WHY ATOMIC. The old build wrote parquet directly and skipped on "file exists", so a Ctrl-C mid-write left a truncated artifact the next run treated as complete. Every write here goes to a sibling temp file and is renamed, which is atomic within a filesystem.

WHY A DAY IS CENTRAL STANDARD TIME, EVERYWHERE, ALL YEAR. The old build resampled a UTC-aware index, so a "daily max" ran 00:00-24:00 UTC = 18:00-18:00 local Central -- not what anyone means by a daily maximum, not aligned with gridMET's daily grid, and a grab sample carrying a date with no time landed at 00:00 UTC, the previous local evening.

The fix is ONE fixed offset for the whole AOE rather than each site's own civil zone. Per-site civil zones drag in DST: two days a year are 23 or 25 hours long, the spring-forward hour does not exist and the fall-back hour is ambiguous, and neighbouring sites in different zones get day boundaries an hour apart for no gain -- everything is aggregated to daily anyway. `Etc/GMT+6` is Central Standard year-round, so every day in every source is the same 24 hours. The cost is that an eastern-seaboard day runs 01:00-01:00 local; the benefit is that a day means one thing.

Each site's civil zone is still RECORDED on the site table as `tz`, so the choice stays auditable and a later stage can revisit it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

# Every state in or adjacent to the AOE. Used when `timezonefinder` is absent, which it is here. Exact for the AOE core (IA/MN/IL/MO Central, the NE box Eastern); the split-zone states are assigned their EASTERN-portion zone, which is where our sites in them actually sit.
STATE_TZ = {
    "ia": "America/Chicago", "mn": "America/Chicago", "il": "America/Chicago",
    "mo": "America/Chicago", "wi": "America/Chicago", "ne": "America/Chicago",
    "ks": "America/Chicago", "sd": "America/Chicago", "nd": "America/Chicago",
    "ar": "America/Chicago", "la": "America/Chicago", "ms": "America/Chicago",
    "ok": "America/Chicago", "tx": "America/Chicago", "al": "America/Chicago",
    "tn": "America/Chicago", "ky": "America/New_York", "in": "America/New_York",
    "oh": "America/New_York", "mi": "America/New_York", "pa": "America/New_York",
    "ny": "America/New_York", "va": "America/New_York", "wv": "America/New_York",
    "md": "America/New_York", "de": "America/New_York", "nj": "America/New_York",
    "ct": "America/New_York", "ma": "America/New_York", "ri": "America/New_York",
    "nh": "America/New_York", "vt": "America/New_York", "me": "America/New_York",
    "dc": "America/New_York", "nc": "America/New_York", "sc": "America/New_York",
    "ga": "America/New_York", "fl": "America/New_York",
}
DEFAULT_TZ = "America/Chicago"

# The single binning zone for the whole AOE: Central Standard year-round, no DST. Every day is 24 hours and every source shares the same boundaries.
STANDARD_TZ = "Etc/GMT+6"

try:                                      # optional; a real point-in-polygon beats the state map
    from timezonefinder import TimezoneFinder

    _TF = TimezoneFinder()
except Exception:
    _TF = None


def resolve_tz(lat: float, lon: float, state: str | None = None) -> str:
    """IANA zone for a site. Point-in-polygon when available, else the state map, else Central."""
    if _TF is not None:
        try:
            z = _TF.timezone_at(lat=float(lat), lng=float(lon))
            if z:
                return z
        except Exception:
            pass
    return STATE_TZ.get(str(state).strip().lower(), DEFAULT_TZ) if state else DEFAULT_TZ


def daily_max(ts, values, tz: str | None = None) -> pd.DataFrame:
    """Per-day maximum on STANDARD_TZ days. Returns columns `date` (python date) and `value`.

    `ts` may be tz-aware or naive; naive is ASSUMED UTC, matching every ingest path here. Rows with a null value are dropped first, so the result is one row per MEASURED day rather than a dense index -- downstream sparsity depends on that. `tz` is accepted and ignored: the binning zone is fixed for the whole AOE, and the argument stays so a caller cannot silently pass a per-site zone and believe it took effect.
    """
    s = pd.Series(pd.to_numeric(values, errors="coerce").to_numpy(),
                  index=pd.to_datetime(ts, errors="coerce", utc=True))
    s = s[s.index.notna()].dropna()
    if s.empty:
        return pd.DataFrame({"date": pd.Series(dtype="object"), "value": pd.Series(dtype="float64")})
    out = s.groupby(s.tz_convert(STANDARD_TZ).index.date).max()
    return pd.DataFrame({"date": list(out.index), "value": out.to_numpy()})


# Kept so existing callers keep working; the name now overstates what it does.
daily_max_local = daily_max


def gap_stats(dates) -> tuple[int, int]:
    """(n_days, longest_gap_days) over a set of local dates. Gap is the longest run with no observation."""
    d = pd.Series(pd.to_datetime(pd.Series(list(dates)), errors="coerce")).dropna().dt.normalize().drop_duplicates().sort_values()
    if d.empty:
        return 0, 0
    if len(d) == 1:
        return 1, 0
    return len(d), int(d.diff().dt.days.max() - 1)


def write_parquet(df: pd.DataFrame, path: Path, **kw) -> Path:
    """Atomic parquet write. A truncated file must never look complete to the next run's skip check."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, **kw)
    os.replace(tmp, path)
    return path


def write_csv(df: pd.DataFrame, path: Path, **kw) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=kw.pop("index", False), **kw)
    os.replace(tmp, path)
    return path
