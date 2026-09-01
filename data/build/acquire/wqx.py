"""WQX discrete nitrate results -- COLLECTED AND SURFACED, and deliberately not sensors.

WHAT THIS IS NOT, and the constraint is the design (Isaac, 2026-08-30). WQX stations are **not nodes**. They do not enter the census, they never become registrations, and they must not appear anywhere in the proximity or identity machinery. They are a distinct CLASS of observation from our instrumented sensors -- discrete samples rather than a monitored series -- and treating them as candidate nodes would have generated **42,209 merge candidates** against a review queue that has handled 136 ledger masks in its life. The store is collected, exposed through `data.access`, and stops there.

WHY IT IS STILL WORTH COLLECTING. 75,599 stations carry nitrate results across the 41 AOE states against our ~20,000 sensors. As a spatial field of discrete observations that is an order-of-magnitude change in coverage; as a set of monitored nodes it is a review burden nobody can pay. Collecting without registering keeps the first and refuses the second.

**Name it WQX, not WQP.** IWQIS already owns a `WQP\\d+` namespace -- IIHR's *Water Quality Project* programme -- so a `WQP:` prefix would put two unrelated WQPs in one store forever.

TWO ENDPOINT HAZARDS, both measured rather than anticipated:

- **`zip=yes` is ignored.** `wqx3` answers `text/plain` CSV whatever the parameter says.
- **A truncated response arrives as HTTP 200 with a valid header row and no error marker.** The same Iowa query returned 11.4 MB once and 21.6 MB the next time; the short one parsed as perfectly good CSV. `_complete` therefore checks the marker AND that the final line is a whole record, and the fetch retries rather than accepting a short read. There is no count header to compare against -- `Total-Result-Count` does not exist on this endpoint, whatever the plan assumed.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd

from .. import config, verify
from .. import io as bio

BASE = "https://www.waterqualitydata.us/wqx3/Result/search"
UA = "sustag-build/1.0"

# Nitrate forms only. Kjeldahl, ammonia and total-N are different quantities and no factor makes them
# nitrate; refusing them here is cheaper than explaining a bad join later.
CHARACTERISTICS = ("Nitrate", "Nitrate + Nitrite", "Inorganic nitrogen (nitrate and nitrite)")

# Kept because they distinguish measurement processes a model may reasonably separate: a routine grab is
# an instant, a composite is an average over time, a cross-sectional profile an average across the width.
# QC activities are kept in the STORE and excluded by the READER -- the build records what the source
# said, and "a field blank is not the river" is a judgement the access layer applies visibly.
KEEP = ["Org_Identifier", "Org_FormalName", "Location_Identifier", "Location_Name", "Location_Type",
        "Location_State", "Location_Latitude", "Location_Longitude", "Activity_TypeCode",
        "Activity_Media", "Activity_StartDate", "Result_Characteristic", "Result_Measure",
        "Result_MeasureUnit", "Result_MeasureType", "Result_DetectionCondition",
        "Result_MethodSpeciationName", "Result_SampleFraction"]

STATE_FIPS = {
    "AL": "01", "AR": "05", "CO": "08", "CT": "09", "DC": "11", "DE": "10", "GA": "13", "IA": "19",
    "IL": "17", "IN": "18", "KS": "20", "KY": "21", "LA": "22", "MA": "25", "MD": "24", "ME": "23",
    "MI": "26", "MN": "27", "MO": "29", "MS": "28", "MT": "30", "NC": "37", "ND": "38", "NE": "31",
    "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "OH": "39", "OK": "40", "PA": "42", "RI": "44",
    "SC": "45", "SD": "46", "TN": "47", "TX": "48", "VA": "51", "VT": "50", "WI": "55", "WV": "54",
    "WY": "56",
}
_RETRIES = 4


def _dir():
    return config.ACQUIRED / "wqx"


def _path(state: str):
    return _dir() / f"nitrate_{state}.parquet"


def _complete(text: str) -> bool:
    """True when the payload is a whole CSV. See the module docstring for why this cannot be assumed."""
    if not text or "ERROR" in text[:400].upper():
        return False
    lines = text.rstrip("\n").split("\n")
    if len(lines) < 2:
        return False
    # PARSED, not counted. A truncated stream ends mid-record, but counting commas cannot tell a short
    # row from a quoted field that contains one -- and a tolerance loose enough to survive quoting is
    # loose enough to accept `4,` against a three-field header, which is exactly the silent truncation
    # this exists to catch. The csv module knows the quoting rules; use them.
    import csv as _csv

    try:
        head = next(_csv.reader([lines[0]]))
        last = next(_csv.reader([lines[-1]]))
    except Exception:                                    # noqa: BLE001 -- unparseable is incomplete
        return False
    return len(last) == len(head)


# How stale a state may be before it is refetched. WQX contributors submit on their own schedules and a
# result can appear months after the sample, so a tight horizon would refetch two million rows forever;
# a quarter is long enough to be quiet and short enough that the store does not silently stop.
STALE_DAYS = 90


def _current(p) -> bool:
    """Does the stored state reach recently enough, or has it frozen at its first fetch?

    EXISTENCE IS NOT CURRENCY. The request window runs to today, so a file written in August holds samples to August; a guard testing `p.exists()` skips it on every later run and the state freezes at whatever the first pass happened to collect — silently, because a frozen file is a perfectly valid file. Same shape as the drought store's year guard and as the skip-logic family generally: the question is not *have I fetched this state* but *have I fetched it recently enough*.
    """
    import datetime as _dt

    import pyarrow.parquet as pq

    try:
        d = pq.read_table(p, columns=["Activity_StartDate"]).to_pandas()
        latest = pd.to_datetime(d.Activity_StartDate, errors="coerce").max()
    except Exception:                                    # noqa: BLE001 -- unreadable is not current
        return False
    if pd.isna(latest):
        return False
    return (_dt.date.today() - latest.date()).days <= STALE_DAYS


def _window(state: str, lo: str, hi: str) -> str | None:
    """One request over [lo, hi], retried. None when every attempt came back truncated."""
    import urllib.parse
    import urllib.request

    q = [("statecode", f"US:{STATE_FIPS[state]}"), ("dataProfile", "narrow"), ("mimeType", "csv"),
         ("startDateLo", lo), ("startDateHi", hi)]
    q += [("characteristicName", c) for c in CHARACTERISTICS]
    url = BASE + "?" + urllib.parse.urlencode(q)
    for attempt in range(1, _RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=1800) as r:
                body = r.read().decode("utf8", "replace")
        except Exception as e:                            # noqa: BLE001 -- a dropped read is a retry
            print(f"    {state} {lo[-4:]}-{hi[-4:]}: {type(e).__name__}, retry {attempt}", flush=True)
            continue
        if _complete(body):
            return body
        print(f"    {state} {lo[-4:]}-{hi[-4:]}: truncated at {len(body)/1e6:.1f} MB, "
              f"retry {attempt}", flush=True)
    return None


def fetch_state(state: str, force: bool = False) -> int:
    """One state's discrete nitrate results. Returns rows written, 0 when cached."""
    import io as _io
    import urllib.parse
    import urllib.request

    p = _path(state)
    if p.exists() and not force and _current(p):
        return 0
    lo_y, hi_y = config.collection_start().year, _dt.date.today().year
    text = _window(state, f"01-01-{lo_y}", f"12-31-{hi_y}")
    if text is None:
        # ADAPTIVE, because the truncation is intermittent rather than a size limit. The same 19-year
        # Alabama query failed three times at 3.7, 9.5 and 14.8 MB and then succeeded at 16.7 -- so
        # retrying is valid, but a state that keeps failing needs smaller asks. Cost is per REQUEST
        # (one year 19 s, nineteen years 28 s), so chunking is the fallback and never the default.
        print(f"    {state}: falling back to 3-year windows", flush=True)
        parts = []
        for y in range(lo_y, hi_y + 1, 3):
            t = _window(state, f"01-01-{y}", f"12-31-{min(y + 2, hi_y)}")
            if t is None:
                raise RuntimeError(f"{state}: {y}-{y+2} incomplete after {_RETRIES} attempts")
            parts.append(t if not parts else t.split("\n", 1)[1])   # drop repeated header
        text = "\n".join(parts)
    d = pd.read_csv(_io.StringIO(text), low_memory=False)
    cols = [c for c in KEEP if c in d.columns]
    d = d[cols].rename(columns={"Location_Latitude": "lat", "Location_Longitude": "lon"})
    d["state"] = state
    _dir().mkdir(parents=True, exist_ok=True)
    bio.write_parquet(d, p, stamps={"source": "wqx", "not_a_sensor": "true"})
    return len(d)


def main(force: bool = False, states=None) -> None:
    import time

    states = states or sorted(STATE_FIPS)
    t0 = time.time()
    total = 0
    for i, st in enumerate(states, 1):
        try:
            n = fetch_state(st, force=force)
        except Exception as e:                            # noqa: BLE001 -- one state must not end the pass
            print(f"  {i:2d}/{len(states)} {st}: FAILED {type(e).__name__} {str(e)[:70]}", flush=True)
            continue
        total += n
        print(f"  {i:2d}/{len(states)} {st}: {n:,} rows" if n else
              f"  {i:2d}/{len(states)} {st}: cached", flush=True)
    print(f"  wqx: {total:,} results in {(time.time()-t0)/60:.1f} min", flush=True)


@verify.check("a3", "WQX never enters the sensor population")
def _check_wqx_is_not_a_sensor():
    """THE constraint this source is collected under. A WQX identifier reaching the sensors table would put 55,210 registrations into the identity machinery and 42,209 pairs into a review queue built for hundreds."""
    import pyarrow.parquet as pq

    if not _dir().exists() or not any(_dir().glob("nitrate_*.parquet")):
        return None, "wqx not collected"
    sp = config.PUBLISHED / "sensors.parquet"
    if not sp.exists():
        return None, "no published sensors yet"
    s = pq.read_table(sp, columns=["source"]).column("source").to_pylist()
    leaked = sorted({x for x in s if x and str(x).upper().startswith("WQX")})
    n = sum(pq.ParquetFile(p).metadata.num_rows for p in _dir().glob("nitrate_*.parquet"))
    return not leaked, (f"{n:,} WQX result(s) held; no WQX source in the sensors table"
                        if not leaked else f"WQX LEAKED INTO SENSORS: {leaked}")


@verify.check("a3", "the WQX store reaches the present")
def _check_wqx_current():
    """Guards the freeze this store had: `fetch_state` skipped on existence while its request window ran to today, so a state collected once would never refresh. The tolerance is loose because contributors submit months after sampling."""
    import datetime as _dt

    ps = sorted(_dir().glob("nitrate_*.parquet")) if _dir().exists() else []
    if not ps:
        return None, "wqx not collected"
    stale = [p.stem.rsplit("_", 1)[1] for p in ps if not _current(p)]
    return not stale, (f"{len(ps)} state(s), all within {STALE_DAYS} days"
                       if not stale else f"{len(stale)} state(s) frozen: {sorted(stale)[:6]}")
