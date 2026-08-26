"""Shared discovery assembly: adapter frames -> conformed sensor registrations.

NOTHING IS FILTERED HERE. A registration returned by an adapter becomes a row whatever its record length, coverage or type; the only exclusion is geographic scope, applied inside each adapter. What this adds on top: the `SOURCE:native_id` uid, the recorded timezone, the co-location distance (relational -- it depends on every other registration and cannot be recovered later; no holdout scheme can detect that a nitrate sensor sits 40 m from the discharge gauge feeding its features), the fetch-skip scope filter, and the canary flag that punches through it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts
from .. import io as bio
from .sources import iwqis, mpca, usgs

ADAPTERS = {"USGS": usgs, "IWQIS": iwqis, "MPCA": mpca}

# WHAT "NOT SURFACE WATER" LOOKS LIKE IN THE SOURCE'S OWN WORDS. The USGS endpoint returns the type NAME
# (`Well`, `Spring`, `Atmosphere`), never the code -- an earlier version tested code prefixes and skipped 73
# sites when it should have skipped ~450: `SP`/`AT` happened to prefix `Spring`/`Atmosphere` while `Well`
# (379 sites) matched nothing. Both forms accepted, since the code form appears in other USGS responses.
# Lakes and estuaries are deliberately absent: they are surface water worth collecting; that they cannot be
# graph nodes is a later decision against `NO_SURFACE_BASIN`.
_NON_SURFACE_WORDS = ("well", "spring", "groundwater", "atmosphere", "cistern", "test hole")
_NON_SURFACE_CODES = ("GW", "SP", "AT", "AS")


def canary_roster() -> pd.DataFrame:
    """The probe sensors every scope filter must admit. Empty when the roster file is absent."""
    p = config.PARAMETERS / "canary_roster.csv"
    if not p.exists():
        return pd.DataFrame(columns=["site_uid", "probe_class", "reason"])
    return pd.read_csv(p, dtype=str)


def _fetch_skip(a: pd.DataFrame) -> pd.Series:
    """Why A3 will not fetch a sensor's water. NULL means it will.

    ONE RULE, BOTH HALVES REQUIRED: non-surface AND no declared nitrate. A non-surface site WITH nitrate is a groundwater nitrate record worth having; a surface site without nitrate is the covariate node the census exists to collect. A SKIPPED FETCH, NEVER A DROPPED SENSOR -- the row keeps its reason.

    The rule fires only where channels are declared: free-text `site_type_raw` collides with the code test (an IWQIS "Spring at ..." upper-cases into SP), and a null declaration reads as "not known", never as "no nitrate". Absence of evidence never skips a fetch. Canaries punch through unconditionally -- the class nobody fetches is the class nobody tests.
    """
    raw = a.site_type_raw.fillna("").astype(str).str.upper().str.strip()
    by_word = raw.str.contains("|".join(w.upper() for w in _NON_SURFACE_WORDS), regex=True)
    by_code = raw.str.fullmatch("|".join(f"{c}(-.*)?" for c in _NON_SURFACE_CODES))
    non_surface = by_word | by_code.fillna(False)

    known = a.channels_declared.notna()
    has_nitrate = (a.channels_declared.fillna("").astype(str)
                   .str.split("+").apply(lambda v: "nitrate" in v))
    skip = non_surface & known & ~has_nitrate & ~a.canary.fillna(False)
    out = pd.Series(np.where(skip, "non_surface_no_nitrate", None), index=a.index, dtype="object")

    # THE DECLARED-SPAN SCREEN. The census metadata already says when every declared series begins and
    # ends, so a sensor whose whole declared record misses the collection window -- or that declares
    # channels with NO dates at all, the "registered but no series" catalogue entry -- has nothing to
    # fetch, knowable without a request. Applies only where the source DECLARES spans (USGS); IWQIS and
    # MPCA declare nothing and absence of evidence never skips a fetch. Canaries punch through, as
    # everywhere: the class nobody fetches is the class nobody tests.
    if "declared_begin" in a.columns:
        begin = pd.to_datetime(a.declared_begin, errors="coerce")
        end = pd.to_datetime(a.declared_end, errors="coerce")
        lo, hi = pd.Timestamp(f"{config.YEAR_START}-01-01"), pd.Timestamp(f"{config.YEAR_END}-12-31")
        declares = a.source.eq("USGS") & known
        dateless = declares & begin.isna() & end.isna()
        outside = declares & ((end < lo) | (begin > hi))
        span_skip = (dateless | outside) & out.isna() & ~a.canary.fillna(False)
        out[span_skip & dateless] = "no_declared_series"
        out[span_skip & ~dateless] = "declared_span_outside_window"
    return out


def _colocation(df: pd.DataFrame) -> pd.DataFrame:
    """Nearest-other-registration distance, metres, equal-area. k=2: every point's first neighbour is itself."""
    import geopandas as gpd
    from scipy.spatial import cKDTree

    g = gpd.GeoSeries(gpd.points_from_xy(df.lon, df.lat), crs=4326).to_crs(config.EQUAL_AREA)
    xy = np.c_[g.x, g.y]
    if len(xy) < 2:
        df["nearest_nitrate_sensor_m"] = np.nan
        return df
    d, _ = cKDTree(xy).query(xy, k=2)
    df["nearest_nitrate_sensor_m"] = d[:, 1]
    return df


def assemble(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Adapter discovery frames -> one conformed sensors table (discovery block filled, later blocks null)."""
    a = pd.concat(frames, ignore_index=True)
    a["site_uid"] = [contracts.make_uid(s, i) for s, i in zip(a.source, a.source_site_id)]

    roster = set(canary_roster().site_uid)
    a["canary"] = a.site_uid.isin(roster)

    need = a.tz.isna()
    if need.any():
        a.loc[need, "tz"] = [bio.resolve_tz(la, lo, st)
                             for la, lo, st in zip(a.loc[need, "lat"], a.loc[need, "lon"],
                                                   a.loc[need, "state"])]
    a = _colocation(a)
    a["fetch_skip_reason"] = _fetch_skip(a)

    out = contracts.conform_sensors(a)
    bad = contracts.validate_sensors(out)
    if bad:
        raise ValueError("sensors table violates the contract:\n  " + "\n  ".join(bad))
    return out.sort_values("site_uid", ignore_index=True)


def run(census: bool = True, sources: list[str] | None = None, cover=None, clip=None) -> pd.DataFrame:
    """Run every adapter and assemble. `census=False` is A1's nitrate-only bootstrap pass.

    `clip` forwards to the USGS adapter -- A2 passes the AOE-contains mask, because the adapter's seed-box default would silently cut the extent's arms off a census whose whole point is to measure them (it did, once: a census swept the AOE bounds and reported 0 arm sensors because this parameter stopped here).
    """
    frames = []
    for name in (sources or list(ADAPTERS)):
        mod = ADAPTERS[name]
        d = mod.discover(census=census, cover=cover, clip=clip) if name == "USGS" else mod.discover()
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
        from .sources import usgs as _usgs

        d = _usgs.register_one(native)
        if len(d):
            print(f"  canary {r.site_uid} admitted by direct lookup ({r.probe_class})")
            frames.append(d)
        else:
            print(f"  canary {r.site_uid} UNKNOWN to the API -- roster needs a real probe id")
    return assemble(frames)
