"""The source-adapter contract.

Every source implements the same calls with the SAME signatures, so discovery and the fetch orchestrator never branch on source. The old builds routed on `uid.startswith("USGS-")` in five separate places; build2 then let USGS's `fetch` grow `tier=`/`channels=` keywords the other adapters lacked, which put the source-branching right back in the caller. Here the signature is uniform: a source that needs a fetch tier derives it INSIDE its own adapter from what it declared at discovery.

`discover()` returns one row per REGISTRATION with the columns below. It never filters -- a site with three observations is discovered exactly like a site with three million; scope filters are acquisition policy, applied by the census, and the canary roster punches through them there.

`fetch(uid, start, end, channels)` returns native-resolution observations: a UTC DatetimeIndex named `datetime`, one column per parameter the source reports. Every parameter, not a curated subset -- an 8-code allowlist is why turbidity was once never on disk despite being fetched. `channels` optionally narrows the request for sources that pay per parameter; a local-disk source ignores it and returns everything, which is always legal.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

# What discover() must supply. Everything else on the sensors table is derived later: uid and tz at
# registration, the record block by the fetch, the bundle block at D3.
DISCOVERY_COLUMNS = [
    "source_site_id", "station_name", "lat", "lon", "state", "county", "huc",
    "agency", "datum", "altitude_m", "source_drainage_area_km2", "site_type_raw",
    "regime", "pcodes", "reported_units", "detection_limit", "nitrate_begin", "nitrate_end",
    # Optional: a source that publishes the station's own timezone should pass it through, since that
    # beats inferring one. Left null otherwise and filled by io.resolve_tz.
    "tz",
    # WHAT THE SOURCE ADVERTISES, before anything is fetched. `channels_declared` is the canonical
    # channel set from the source's own series metadata; it decides whether a site is worth fetching
    # and, once fetched, `channels_present` is measured off the data and may be smaller. A site
    # declaring a series that returns nothing is a discontinued sensor, not a fetch failure.
    # `census_source` separates sites found by looking for the seed channel from those found by
    # looking for everything -- discovery provenance, kept because it is free.
    "channels_declared", "census_source",
    # The source's own ANY-CHANNEL series span, from the same metadata response that names the channels.
    # This is what makes "do not fetch what declares nothing" answerable without a request: a site whose
    # every declared series ends before the window (or that declares no dates at all) has nothing to pull.
    "declared_begin", "declared_end",
]


class Source(Protocol):
    NAME: str

    def discover(self) -> pd.DataFrame: ...

    def fetch(self, uid: str, start=None, end=None, channels=None) -> pd.DataFrame | None: ...


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

    Every ingest lands in UTC here; the conversion to a local calendar day happens once, in the daily reduction, so there is exactly one place that decides what a day is.
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
