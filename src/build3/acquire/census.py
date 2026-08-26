"""A2 -- the census: every continuous sensor IN THE AOE, all channels, all sources.

AOE-SCOPED, A CHANGE FROM THE ANCESTOR, which swept the seed boxes and left the extent's arms with basins and no sensors. The census never feeds back into the extent (the AOE never re-expands), so the wider scope is not circular -- it is more sensors fetched, and the first AOE-scoped run measures what the arms cost before any cadence is committed.

Runs AFTER D1 in the bootstrap (A1 -> D1 -> A2): the extent must exist to scope against. Every scope filter here admits the canary roster unconditionally -- the probes exist precisely to keep unfetched classes exercised.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts
from .. import io as bio
from . import discovery


def _aoe_clip(frame: pd.DataFrame) -> np.ndarray:
    """Point-in-AOE mask. Prepared geometry, one contains() per point -- cheap at census scale."""
    from shapely import points
    from shapely.prepared import prep

    geom = prep(config.aoe_geometry())
    pts = points(np.asarray(frame.lon, dtype=float), np.asarray(frame.lat, dtype=float))
    return np.fromiter((geom.contains(p) for p in pts), dtype=bool, count=len(pts))


def main(force: bool = False, snapshot: str | None = None) -> pd.DataFrame:
    config.ensure_dirs()
    if not config.AOE_PATH.exists():
        raise SystemExit("the census is AOE-scoped and no AOE exists -- run a1 then d1 first "
                         "(the bootstrap order is the design, not an accident)")

    # One cover box: the AOE's own bounds. The metadata endpoints are queried per pcode per box, so a
    # single generous box costs less than tiling, and the contains-clip does the honest restriction.
    b = config.aoe_geometry().bounds
    cover = [(b[0], b[1], b[2], b[3])]
    print(f"  census cover: AOE bounds {tuple(round(x, 2) for x in b)}")
    sensors = discovery.run(census=True, cover=cover, clip=_aoe_clip)

    # The non-USGS adapters clip to seed boxes internally (their networks are box-local anyway); the
    # USGS census used the AOE clip above. Measure and report the arms -- the chapter's open question.
    in_aoe = _aoe_clip(sensors)
    outside = int((~in_aoe).sum())
    if outside:
        print(f"  {outside} sensor(s) inside a seed box but outside the AOE -- kept; the extent is the "
              f"authority and these sit in box corners the drainage never reached")
    arms = sensors[in_aoe & ~config.in_seed_boxes(lon=sensors.lon.values, lat=sensors.lat.values)]
    print(f"  AOE-arm sensors (outside every seed box): {len(arms):,} -- the measured cost of the "
          f"AOE-scoped census")

    # CARRY FORWARD, NEVER BLANK. The census re-discovers the row set, and every carried block written by
    # an earlier stage must survive onto the fresh rows -- the seed columns are A1's, the later blocks are
    # their stages'. Values are mapped by uid onto the new frame; a sensor the census no longer returns
    # keeps nothing (its row is gone from discovery, which is what `carried` means it must survive: a
    # re-discovery, not an exit from the source's registry).
    if config.SENSORS_PATH.exists():
        prev = pd.read_parquet(config.SENSORS_PATH)
        idx = prev.set_index("site_uid")
        cols = [(c, blk) for c, _, blk, _ in contracts.SENSOR_COLUMNS
                if (contracts.SENSOR_OWNERS[blk].carried
                    or c in ("aoe_seed", "seed_exclusion_reason", "nldi_basin_km2",
                             "nldi_basin_flag", "nldi_basin_method"))
                and c in prev.columns and c != "site_uid"]
        for c, _ in cols:
            carried_vals = sensors.site_uid.map(idx[c])
            sensors[c] = carried_vals
    out = contracts.conform_sensors(sensors)
    bad = contracts.validate_sensors(out)
    if bad:
        raise ValueError("sensors table violates the contract:\n  " + "\n  ".join(bad))
    bio.write_parquet(out, config.SENSORS_PATH,
                      stamps={"snapshot": snapshot or "",
                              "aoe": config.aoe_stamp(),
                              "schema": contracts.schema_fingerprint(contracts.SENSOR_DTYPES)})
    print(f"  wrote sensors table: {len(out):,} registrations "
          f"{out.source.value_counts().to_dict()}")
    print(f"  fetch-skip: {out.fetch_skip_reason.value_counts(dropna=False).to_dict()}")
    return out


# ---- verify ----------------------------------------------------------------------------------------

from .. import verify


@verify.check("a2", "the census is AOE-scoped: sensors exist beyond every seed box")
def _check_arms_exist():
    """A box-scoped clip regression presents as exactly zero arm sensors -- the extent's arms hold thousands, so zero after a census pass means the clip is wrong, not the world."""
    if not config.SENSORS_PATH.exists():
        return None, "no sensors table yet"
    s = pd.read_parquet(config.SENSORS_PATH, columns=["site_uid", "lat", "lon", "census_source"])
    if not (s.census_source.fillna("") == "census").any():
        return None, "the census has not run"
    ok = s.lat.notna() & s.lon.notna()
    arms = int((~config.in_seed_boxes(lon=s.lon[ok].values, lat=s.lat[ok].values)).sum())
    return arms > 0, f"{arms:,} sensor(s) outside every seed box"
