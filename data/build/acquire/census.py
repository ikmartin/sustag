"""A2 -- the census: every continuous sensor IN THE AOE, all channels, all sources, each minted its SNR id.

AOE-SCOPED: the census never feeds back into the extent (the AOE never re-expands), so the wider scope is not circular -- it is more sensors registered, and the arm count beyond the seed boxes is measured every pass. Runs AFTER D1 in the bootstrap (A1 -> D1 -> A2): the extent must exist to scope against. Every scope filter here admits the canary roster unconditionally.

THE CENSUS IS THE ROW AUTHORITY for the sensors table -- it may re-derive the full row set; no narrower pass may. It is also WHERE PUBLISHED IDS ARE BORN: every registration not yet in the id ledger is minted its `SNR-XXXX` code here, because minting belongs with registration and the ledger's append-only discipline makes re-censusing safe -- known uids keep their codes, new ones take the next free codes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts, ids
from .. import io as bio
from . import discovery
from . import snapshot as snap_store


def _aoe_clip(frame: pd.DataFrame) -> np.ndarray:
    """Point-in-AOE mask. Prepared geometry, one contains() per point -- cheap at census scale."""
    from shapely import points
    from shapely.prepared import prep

    geom = prep(config.aoe_geometry())
    pts = points(np.asarray(frame.lon, dtype=float), np.asarray(frame.lat, dtype=float))
    return np.fromiter((geom.contains(p) for p in pts), dtype=bool, count=len(pts))


def main(force: bool = False, snapshot: str | None = None) -> pd.DataFrame:
    # `snapshot` shadows the snapshot MODULE by design of the runner's kwarg contract; the module is imported as snap_store above.
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

    located = sensors.lat.notna()
    arms = sensors[located & ~config.in_seed_boxes(lon=sensors.lon.fillna(0).values,
                                                   lat=sensors.lat.fillna(0).values)]
    print(f"  AOE-arm sensors (outside every seed box): {len(arms):,}")

    # CARRY FORWARD, NEVER BLANK. The census re-discovers the row set, and every carried block written by
    # an earlier stage must survive onto the fresh rows -- the seed columns are A1's, the later blocks are
    # their stages'. Values are mapped by uid onto the new frame; a sensor the census no longer returns
    # keeps nothing (its row is gone from discovery, which is what `carried` means it must survive: a
    # re-discovery, not an exit from the source's registry).
    if config.SENSORS_PATH.exists():
        prev = pd.read_parquet(config.SENSORS_PATH)
        idx = prev.set_index("site_uid")
        cols = [c for c, _, blk, _ in contracts.SENSOR_COLUMNS
                if (contracts.SENSOR_OWNERS[blk].carried
                    or c in ("aoe_seed", "seed_exclusion_reason", "nldi_basin_km2",
                             "nldi_basin_flag", "nldi_basin_method"))
                and c in prev.columns and c != "site_uid"]
        for c in cols:
            sensors[c] = sensors.site_uid.map(idx[c])

    # MINT. Known uids keep their codes through the ledger; only never-seen registrations consume new ones.
    minted = ids.mint_new(sensors[["site_uid", "lat", "lon"]],
                          minted_under=snapshot or snap_store.head() or "pre-snapshot")
    if minted:
        print(f"  minted {len(minted):,} new SNR id(s)")
    sensors["snr_id"] = sensors.site_uid.map(ids.mapping())

    out = contracts.conform_sensors(sensors)
    bad = contracts.validate_sensors(out)
    if bad:
        raise ValueError("sensors table violates the contract:\n  " + "\n  ".join(bad))
    bio.write_parquet(out, config.SENSORS_PATH,
                      stamps={"snapshot": snapshot or snap_store.head() or "",
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


@verify.check("a2", "every canary is registered and un-skipped")
def _check_canaries():
    """The roster's whole purpose: every probe arrives, typed for its class, with no scope filter having eaten it. A missing canary is a broken filter, not a data gap."""
    if not config.SENSORS_PATH.exists():
        return None, "no sensors table yet"
    roster = discovery.canary_roster()
    if not len(roster):
        return None, "no roster"
    s = pd.read_parquet(config.SENSORS_PATH, columns=["site_uid", "canary", "fetch_skip_reason"])
    idx = s.set_index("site_uid")
    missing = [u for u in roster.site_uid if u not in idx.index]
    skipped = [u for u in roster.site_uid if u in idx.index and pd.notna(idx.loc[u, "fetch_skip_reason"])]
    ok = not missing and not skipped
    return ok, (f"missing: {missing} skipped: {skipped}" if not ok
                else f"{len(roster)} canaries registered and fetchable")


@verify.check("a2", "every registration carries exactly one SNR id and the ledger validates")
def _check_ids():
    if not config.SENSORS_PATH.exists():
        return None, "no sensors table yet"
    s = pd.read_parquet(config.SENSORS_PATH, columns=["site_uid", "snr_id"])
    unminted = int(s.snr_id.isna().sum())
    dup = s.snr_id.dropna()[s.snr_id.dropna().duplicated()].tolist()
    problems = ids.validate()
    ok = unminted == 0 and not dup and not problems
    return ok, (f"{unminted} unminted, dup codes {dup[:4]}, ledger: {problems[:2]}" if not ok
                else f"{len(s):,} registrations, all minted; ledger conforms")


@verify.check("a2", "the id ledger is append-only against its committed baseline")
def _check_ids_append_only():
    return ids.append_only_vs_git()
