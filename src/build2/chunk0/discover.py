"""Run every source adapter and assemble the site table.

NOTHING IS FILTERED HERE. A registration returned by an adapter becomes a row, whatever its record length, coverage or type. The only exclusion is geographic, and that is the declared scope rather than a judgement about a site: `in_aoe` is applied inside each adapter.

What this module adds on top of the adapters: the `SOURCE:native_id` uid, the timezone used for local-day binning, and the two co-location distances. It does NOT classify site type -- that needs the snap, so it belongs to chunk 3. Those distances have to be computed here because they are relational -- they depend on every other registration -- and because they cannot be recovered afterwards. No holdout scheme can detect that a nitrate sensor sits 40 m from the discharge gauge feeding its features, so the distance is recorded at ingest and any exclusion radius becomes a modelling-time choice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, io as bio, schema
from ..sources import iwqis, mpca, usgs

ADAPTERS = {"USGS": usgs, "IWQIS": iwqis, "MPCA": mpca}


def _colocation(df: pd.DataFrame) -> pd.DataFrame:
    """Nearest-other-nitrate-registration distance, in metres, on an equal-area projection.

    `nearest_gauge_m` -- distance to the nearest non-nitrate continuous gauge -- is left null here: that gauge census belongs to chunk 4, which already walks the network. The column exists now so the contract does not change shape later. `site_type` is likewise left null; chunk 3 decides it.
    """
    import geopandas as gpd
    from scipy.spatial import cKDTree

    g = gpd.GeoSeries(gpd.points_from_xy(df.lon, df.lat), crs=4326).to_crs(config.EQUAL_AREA)
    xy = np.c_[g.x, g.y]
    if len(xy) < 2:
        df["nearest_nitrate_sensor_m"] = np.nan
        return df
    # k=2 because the first neighbour of every point is itself.
    d, _ = cKDTree(xy).query(xy, k=2)
    df["nearest_nitrate_sensor_m"] = d[:, 1]
    return df


# SITE TYPE IS NOT DECIDED HERE. It moved to `chunk3.site_type`, where the reach a site snaps to and the
# waterbody that reach threads are available -- evidence chunk 0 structurally cannot have, and which is what
# separates `Spirit Lake` from `Storm Lake`. Chunk 0 keeps only a metadata PRIOR, called by the basin guard.
# WHAT "NOT SURFACE WATER" LOOKS LIKE IN THE SOURCE'S OWN WORDS. This endpoint returns the type NAME, not
# the NWIS code -- `Well`, `Spring`, `Groundwater drain`, `Atmosphere`, never `GW`/`SP`/`AT` -- so the test
# matches words. An earlier version tested the code prefixes and skipped 73 sites when it should have skipped
# roughly 450: `SP` and `AT` happened to prefix `Spring` and `Atmosphere`, while `Well` (379 sites) matched
# nothing at all. Both forms are accepted now, since the code form appears in other USGS responses.
#
# LAKES AND ESTUARIES ARE NOT HERE, deliberately. They are surface water and their sensors are worth
# collecting; that they cannot be stream-network graph nodes is a separate decision made later against
# `NO_SURFACE_BASIN`. This rule is about what the water IS, not about what the graph can use.
_NON_SURFACE_WORDS = ("well", "spring", "groundwater", "atmosphere", "cistern", "test hole")
_NON_SURFACE_CODES = ("GW", "SP", "AT", "AS")


def _fetch_skip(a: pd.DataFrame) -> pd.Series:
    """Why chunk 1 will not fetch a site's water. NULL means it will.

    ONE RULE, AND IT NEEDS BOTH HALVES. A site is skipped when the source calls it non-surface AND it declares no nitrate. Neither alone is enough: a non-surface site WITH nitrate is a groundwater nitrate record worth having, and a surface site without nitrate is the covariate node the whole census exists to collect.

    THIS IS A SKIPPED FETCH, NEVER A DROPPED SITE. The registration keeps its row and carries the reason, so "why is there no water here" is answerable without re-running anything -- which is the same guarantee `excluded_reason` gives at the other end of the pipeline.

    Judged on the SOURCE's own type string, not on `site_type`, because the derived type needs the snap and the snap does not exist until chunk 3. A cheap prior is the only evidence available at fetch time, and it is wrong rarely enough to be worth the saving; a mistyped surface site would need a re-run to recover, which is why the test is deliberately narrow.
    """
    raw = a.site_type_raw.fillna("").astype(str).str.upper().str.strip()
    by_word = raw.str.contains("|".join(w.upper() for w in _NON_SURFACE_WORDS), regex=True)
    by_code = raw.str.fullmatch("|".join(f"{c}(-.*)?" for c in _NON_SURFACE_CODES))
    non_surface = by_word | by_code.fillna(False)

    # THE RULE ONLY FIRES WHERE THE CHANNELS ARE KNOWN. `_NON_SURFACE_PREFIX` is a USGS type-code test, and
    # a source whose `site_type_raw` is free text will collide with it -- an IWQIS station described as
    # "Spring at ..." upper-cases to SPRING and matches the SP prefix. Requiring a declared channel set means
    # the test can only apply to a source that publishes one, which today is USGS alone, and a null there
    # reads as "not known" rather than as "no nitrate". Absence of evidence never skips a fetch.
    known = a.channels_declared.notna()
    has_nitrate = (a.channels_declared.fillna("").astype(str)
                   .str.split("+").apply(lambda v: "nitrate" in v))
    skip = non_surface & known & ~has_nitrate
    return pd.Series(np.where(skip, "non_surface_no_nitrate", None), index=a.index, dtype="object")


def discover(sources: list[str] | None = None) -> pd.DataFrame:
    frames = []
    for name in (sources or list(ADAPTERS)):
        mod = ADAPTERS[name]
        d = mod.discover()
        print(f"  {name:6} {len(d):>6,} registrations")
        if len(d):
            frames.append(d)
    if not frames:
        return schema.empty_frame()

    a = pd.concat(frames, ignore_index=True)
    a["site_uid"] = [schema.make_uid(s, i) for s, i in zip(a.source, a.source_site_id)]

    # A source may publish its own zone; otherwise resolve from coordinates and state. Recorded either way, as metadata: binning uses the single STANDARD_TZ, not this column.
    need = a.tz.isna()
    if need.any():
        a.loc[need, "tz"] = [bio.resolve_tz(la, lo, st)
                             for la, lo, st in zip(a.loc[need, "lat"], a.loc[need, "lon"], a.loc[need, "state"])]

    a = _colocation(a)
    a["nearest_gauge_m"] = np.nan
    a["fetch_skip_reason"] = _fetch_skip(a)

    # The merge block stays NULL. Seeding it with singletons would make both halves of a duplicate pair claim to be canonical, which is a false assertion rather than a missing one; chunk 3 fills it from recorded decisions.

    out = schema.conform(a)
    bad = schema.validate(out)
    if bad:
        raise ValueError("site table violates the contract:\n  " + "\n  ".join(bad))
    return out.sort_values("site_uid", ignore_index=True)


def main(sources: list[str] | None = None) -> pd.DataFrame:
    config.ensure_dirs()
    print(config.describe())
    print("\ndiscovering:")
    d = discover(sources)
    bio.write_parquet(d, config.REGISTRATIONS_PATH)
    print(f"\n{len(d):,} registrations -> {config.REGISTRATIONS_PATH.relative_to(config.DATA.parents[1])}")
    print(f"  by source: {d.source.value_counts().to_dict()}")
    print(f"  by regime: {d.regime.value_counts().to_dict()}")
    print(f"  co-location: median {d.nearest_nitrate_sensor_m.median():,.0f} m, "
          f"{int((d.nearest_nitrate_sensor_m < 200).sum())} within 200 m of another registration")
    return d


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", nargs="*", default=None, choices=list(ADAPTERS))
    main(ap.parse_args().sources)
