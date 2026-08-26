"""The source-adapter contract.

Every source implements the same two calls, so `discover.py` and `records.py` never branch on source. That is deliberate: the old build routed on `uid.startswith("USGS-")` and treated everything else as IWQIS, in five separate places, which is why adding a source meant editing all of them.

`discover()` returns one row per REGISTRATION with the columns below. It never filters -- a site with three observations is discovered exactly like a site with three million, and whether it is useful is a modelling-time query.

`fetch(uid)` returns native-resolution observations for one registration: a DatetimeIndex named `datetime` in UTC, plus one column per parameter the source reports. Every parameter, not a curated subset -- the old build's 8-code `CORE_PCODES` is why turbidity was never on disk despite being fetched.

`fetch` also takes `min_nitrate_days`, which is a COST HINT and not a filter. A source that pays per request may use it to skip the covariate passes for a site it can already tell is too thin; a source reading from local disk ignores it. Either way the frame is returned and `records.MIN_NITRATE_DAYS` makes the actual keep-or-drop decision, in one place, identically for every source.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

# What discover() must supply. Everything else in the site table is derived later: uid and tz in discover.py, the record block in records.py, the merge block in identity.py.
DISCOVERY_COLUMNS = [
    "source_site_id", "station_name", "lat", "lon", "state", "county", "huc",
    "agency", "datum", "altitude_m", "source_drainage_area_km2", "site_type_raw",
    "regime", "pcodes", "reported_units", "detection_limit", "nitrate_begin", "nitrate_end",
    # Optional: a source that publishes the station's own timezone should pass it through, since that beats inferring one. Left null otherwise and filled by io.resolve_tz.
    "tz",
    # WHAT THE SOURCE ADVERTISES, before anything is fetched. `channels_declared` is the canonical channel set from the source's own series metadata; it is what decides whether a site is worth fetching at all and, once fetched, `channels_present` is measured off the data and may be smaller. `census_source` separates the sites found by looking for nitrate from those found by looking for everything.
    "channels_declared", "census_source",
]


class Source(Protocol):
    NAME: str

    def discover(self) -> pd.DataFrame: ...

    def fetch(self, uid: str, start=None, end=None, min_nitrate_days: int = 0) -> pd.DataFrame | None: ...


def conform_discovery(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """Reindex an adapter's output to DISCOVERY_COLUMNS, erroring on unknown columns.

    Unknown columns are an error rather than a silent drop: a typo'd name would otherwise vanish and the column it was meant to fill would be null for that whole source, which is exactly the kind of thing nobody notices for months.
    """
    extra = [c for c in df.columns if c not in DISCOVERY_COLUMNS]
    if extra:
        raise ValueError(f"{name}.discover() returned unknown columns: {extra}")
    out = df.reindex(columns=DISCOVERY_COLUMNS).copy()
    out["source"] = name
    for c in ("source_site_id", "station_name", "state", "county", "huc", "agency",
              "datum", "site_type_raw", "regime", "pcodes", "reported_units", "tz",
              "nitrate_begin", "nitrate_end", "channels_declared", "census_source"):
        out[c] = out[c].astype("string")
    for c in ("lat", "lon", "altitude_m", "source_drainage_area_km2", "detection_limit"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out["state"] = out.state.str.strip().str.lower()
    return out


def normalise_obs(df: pd.DataFrame, tcol: str = "datetime") -> pd.DataFrame:
    """UTC-aware DatetimeIndex named `datetime`, sorted, duplicate timestamps resolved by last-wins.

    Every ingest lands in UTC here; the conversion to the site's LOCAL calendar day happens once, in `io.daily_max_local`, so there is exactly one place that decides what a day is.
    """
    d = df.copy()
    if tcol in d.columns:
        d[tcol] = pd.to_datetime(d[tcol], errors="coerce", utc=True)
        d = d[d[tcol].notna()].set_index(tcol)
    else:
        d.index = pd.to_datetime(d.index, errors="coerce", utc=True)
        d = d[d.index.notna()]
    d.index.name = "datetime"
    return d[~d.index.duplicated(keep="last")].sort_index()
