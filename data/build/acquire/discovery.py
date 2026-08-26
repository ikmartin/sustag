"""Shared discovery assembly: adapter frames -> conformed sensor registrations.

NOTHING IS FILTERED HERE. A registration returned by an adapter becomes a row whatever its record length, coverage or type -- non-surface sensors included: wells, groundwater and atmospheric stations register, fetch and publish exactly like stream sensors, and the only place the surface/subsurface distinction is ENFORCED is the identity layer's type gate. The only exclusion is geographic scope, applied inside each adapter. What this adds on top: the `SOURCE:native_id` uid, the recorded timezone, the nearest-registration distance (relational -- it depends on every other registration and cannot be recovered later), the window screen, and the canary flag that punches through every scope filter.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts
from .. import io as bio
from .sources import iwqis, mpca, usgs

ADAPTERS = {"USGS": usgs, "IWQIS": iwqis, "MPCA": mpca}

NO_SERIES = "no_declared_series"
ENDS_BEFORE_WINDOW = "declared_ends_before_window"


def canary_roster() -> pd.DataFrame:
    """The probe sensors every scope filter must admit. Empty when the roster file is absent."""
    p = config.PARAMETERS / "canary_roster.csv"
    if not p.exists():
        return pd.DataFrame(columns=["site_uid", "probe_class", "reason"])
    return pd.read_csv(p, dtype=str)


def _fetch_skip(a: pd.DataFrame) -> pd.Series:
    """Why A3 will not fetch a sensor's water. NULL means it will.

    THE WINDOW SCREEN IS THE ONLY CONTENT RULE, and it has ONE bound: a sensor is skipped when every declared series ENDS before `config.collection_start()`. There is deliberately no upper-bound test -- a declaration beginning "after the window" is a data-entry error or a station registered ahead of deployment, not a reason never to fetch, and any test against the current year silently starts excluding real stations as the calendar advances. The screen fires only for sources whose adapter sets `DECLARES_SPANS` (USGS); a source that declares nothing never has a fetch skipped by it, because absence of a declaration is not a declaration of absence. A registration that declares channels but no dates at all is the "registered but no series" catalogue entry, skipped as `no_declared_series`. Canaries punch through unconditionally -- the class nobody fetches is the class nobody tests.
    """
    out = pd.Series(None, index=a.index, dtype="object")
    if "declared_begin" not in a.columns:
        return out
    declares_spans = a.source.map({n: getattr(m, "DECLARES_SPANS", False) for n, m in ADAPTERS.items()})
    known = a.channels_declared.notna()
    begin = pd.to_datetime(a.declared_begin, errors="coerce")
    end = pd.to_datetime(a.declared_end, errors="coerce")
    lo = pd.Timestamp(config.collection_start())
    declares = declares_spans.fillna(False) & known
    dateless = declares & begin.isna() & end.isna()
    ended = declares & (end < lo)
    span_skip = (dateless | ended) & ~a.canary.fillna(False)
    out[span_skip & dateless] = NO_SERIES
    out[span_skip & ~dateless] = ENDS_BEFORE_WINDOW
    return out


def _nearest(df: pd.DataFrame) -> pd.DataFrame:
    """Nearest-other-registration distance in this discovery pass, metres, equal-area. Null for unlocated rows. k=2: every point's first neighbour is itself."""
    import geopandas as gpd
    from scipy.spatial import cKDTree

    df["nearest_registration_m"] = np.nan
    loc = df.lat.notna() & df.lon.notna()
    if loc.sum() < 2:
        return df
    g = gpd.GeoSeries(gpd.points_from_xy(df.lon[loc], df.lat[loc]), crs=4326).to_crs(config.EQUAL_AREA)
    xy = np.c_[g.x, g.y]
    d, _ = cKDTree(xy).query(xy, k=2)
    df.loc[loc, "nearest_registration_m"] = d[:, 1]
    return df


def assemble(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Adapter discovery frames -> one conformed sensors table (discovery block filled, later blocks null)."""
    a = pd.concat(frames, ignore_index=True)
    a["site_uid"] = [contracts.make_uid(s, i) for s, i in zip(a.source, a.source_site_id)]

    roster = set(canary_roster().site_uid)
    a["canary"] = a.site_uid.isin(roster)

    need = a.tz.isna() & a.lat.notna()
    if need.any():
        a.loc[need, "tz"] = [bio.resolve_tz(la, lo, st)
                             for la, lo, st in zip(a.loc[need, "lat"], a.loc[need, "lon"],
                                                   a.loc[need, "state"])]
    a = _nearest(a)
    a["fetch_skip_reason"] = _fetch_skip(a)

    out = contracts.conform_sensors(a)
    bad = contracts.validate_sensors(out)
    if bad:
        raise ValueError("sensors table violates the contract:\n  " + "\n  ".join(bad))
    return out.sort_values("site_uid", ignore_index=True)


def run(census: bool = True, sources: list[str] | None = None, cover=None, clip=None) -> pd.DataFrame:
    """Run every adapter and assemble. `census=False` is A1's nitrate-only bootstrap pass.

    `clip` forwards to EVERY adapter -- A2 passes the AOE-contains mask, because a seed-box default would silently cut the extent's arms off a census whose whole point is to measure them (it did, once: a census swept the AOE bounds and reported 0 arm sensors because this parameter stopped at one adapter).
    """
    frames = []
    for name in (sources or list(ADAPTERS)):
        mod = ADAPTERS[name]
        d = (mod.discover(census=census, cover=cover, clip=clip) if name == "USGS"
             else mod.discover(clip=clip))
        print(f"  {name:6} {len(d):>6,} registrations")
        if len(d):
            frames.append(d)
    if not frames:
        raise RuntimeError("no source returned any registrations")

    # THE ROSTER IS ADMITTED BY DIRECT LOOKUP where the sweep cannot see it. Discovery-by-parameter-code is
    # itself a scope filter, and a groundwater-level well or precip station is structurally invisible to it --
    # exactly the classes the canaries exist to keep exercised. A probe the API does not know is REPORTED,
    # never invented; fix the roster.
    have = {f"{f.source.iloc[0]}:{sid}" for f in frames for sid in f.source_site_id.astype(str)}
    for r in canary_roster().itertuples():
        src, native = str(r.site_uid).split(":", 1)
        if r.site_uid in have or src != "USGS":
            if r.site_uid not in have and src != "USGS":
                print(f"  canary {r.site_uid} not discovered and not USGS -- no direct-lookup path; check the roster")
            continue
        d = usgs.register_one(native)
        if len(d):
            print(f"  canary {r.site_uid} admitted by direct lookup ({r.probe_class})")
            frames.append(d)
        else:
            print(f"  canary {r.site_uid} UNKNOWN to the API -- roster needs a real probe id")
    return assemble(frames)
