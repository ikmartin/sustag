"""D2 -- canonicalisation: native observations become the canonical channel schema, reduced to days.

ONE SHAPE FOR EVERY SENSOR. Three sources name the same quantity three ways; downstream the nodes are homogeneous, so a canonical series carries the same columns everywhere with real nulls for what a sensor does not measure. The mapping is `registry.CHANNELS`; the reduction is `reduction.summarise`; this stage applies them and reports what it found -- a channel silently missing everywhere is the failure this pass is most likely to have.

THE PER-SENSOR CANONICAL DAILIES ARE DERIVATION-INTERNAL INTERMEDIATES, NEVER PUBLISHED. D3's bundle evidence consumes them (a median-difference threshold is meaningless in native units -- the confirmed Cedar River duplicate agrees at ratio 1.0000 in canonical units and would be ruled distinct in native ones), site assembly merges them per channel, and they are the provenance behind every published channel-day. They are also the unit of incrementality: a sensor's canonical file rebuilds only when its native file changes.
"""

from __future__ import annotations

import pandas as pd

from .. import config, contracts, registry, verify
from .. import io as bio
from . import reduction

CANON_BLOCK = [c for c, _, blk, _ in contracts.SENSOR_COLUMNS if blk == "canon"]


def _native_path(uid: str):
    return config.ACQ_WATER_NATIVE / f"{contracts.uid_to_filename(uid)}.parquet"


def _canonical_path(uid: str):
    return config.WATER_CANONICAL / f"{contracts.uid_to_filename(uid)}.parquet"


def stale(uid: str) -> bool:
    """Rebuild when the canonical file is missing, older than its native, or CUT UNDER A DIFFERENT REGISTRY.

    The registry stamp is the cache key mtime cannot be: a registry edit (a new source column, a changed scale) changes the correct output of every canonical file while touching no native -- which left six of build2's canonicals measurably wrong with a passing staleness check.
    """
    n, c = _native_path(uid), _canonical_path(uid)
    if not n.exists():
        return False
    if (not c.exists()) or c.stat().st_mtime < n.stat().st_mtime:
        return True
    return bio.read_stamps(c).get("registry") != registry.fingerprint()


def process_sensor(uid: str, source: str, force: bool = False) -> tuple[dict, pd.DataFrame | None]:
    """Canonicalise one sensor. Returns (canon-block row, tally frame or None).

    A recordless sensor returns an EMPTY row rather than an absent one, so `channels_present` distinguishes "nothing observed" from "not looked at".
    """
    row: dict = {"site_uid": uid, "channels_present": pd.NA}
    for ch in registry.NAMES:
        row[f"n_days_{ch}"] = 0
    n = _native_path(uid)
    if not n.exists():
        return row, None
    obs = pd.read_parquet(n)
    frame, tallies = reduction.summarise(obs, source)
    cpath = _canonical_path(uid)
    if len(frame):
        bio.write_parquet(frame.reset_index().rename(columns={"index": "date"}), cpath,
                          stamps={"registry": registry.fingerprint()})
    else:
        cpath.unlink(missing_ok=True)

    present = []
    for ch in registry.NAMES:
        col = f"{ch}_mean"
        k = int(frame[col].notna().sum()) if col in frame.columns else 0
        row[f"n_days_{ch}"] = k
        if k:
            present.append(ch)
    row["channels_present"] = "+".join(present) if present else pd.NA
    return row, tallies


def main(sensors: pd.DataFrame | None = None, force: bool = False,
         limit: int | None = None, snapshot: str | None = None) -> pd.DataFrame:
    config.ensure_dirs()
    reg = sensors if sensors is not None else pd.read_parquet(config.SENSORS_PATH)
    unv = registry.units_report()
    print(f"  {len(registry.CHANNELS)} channels; {len(unv)} mapping(s) not yet verified by measurement",
          flush=True)

    todo = [r for r in reg.itertuples() if force or stale(r.site_uid)]
    if limit:
        todo = todo[:limit]
    print(f"  {len(todo)} sensor(s) to canonicalise ({len(reg) - len(todo)} current)", flush=True)

    rows, tallies = [], []
    for k, r in enumerate(todo, 1):
        row, t = process_sensor(r.site_uid, r.source, force)
        rows.append(row)
        if t is not None and len(t):
            tallies.append(t.assign(site_uid=r.site_uid))
        if k % 200 == 0:
            print(f"    {k}/{len(todo)}", flush=True)

    # THE TALLIES ARE AN ARTIFACT, NOT A PRINTOUT. Accumulated per (sensor, channel), last-wins for the
    # sensors this pass touched -- the verify invariant and the review panel read them, and "every removal
    # counted" is unfalsifiable from a scrolled-away console line.
    if tallies:
        fresh = pd.concat(tallies, ignore_index=True)
        if config.SCRUB_TALLIES_PATH.exists():
            prior = pd.read_parquet(config.SCRUB_TALLIES_PATH)
            prior = prior[~prior.site_uid.isin(set(fresh.site_uid))]
            fresh = pd.concat([prior, fresh], ignore_index=True)
        bio.write_parquet(fresh.sort_values(["site_uid", "channel"], ignore_index=True),
                          config.SCRUB_TALLIES_PATH,
                          stamps={"registry": registry.fingerprint()})

    if rows and sensors is None:
        block = pd.DataFrame(rows)
        out = contracts.conform_sensors(contracts.update_block(reg, block, CANON_BLOCK))
        bad = contracts.validate_sensors(out)
        if bad:
            raise ValueError("sensors table violates the contract:\n  " + "\n  ".join(bad))
        bio.write_parquet(out, config.SENSORS_PATH,
                          stamps={"snapshot": snapshot or "", "aoe": config.aoe_stamp(),
                                  "schema": contracts.schema_fingerprint(contracts.SENSOR_DTYPES)})
        print(f"  canon block updated on {len(block)} row(s)")

    if tallies:
        t = pd.concat(tallies, ignore_index=True)
        agg = t.groupby("channel").agg(sensors=("site_uid", "nunique"), days=("n_days", "sum"),
                                       sentinel=("n_sentinel", "sum"), clamped=("n_clamped", "sum"),
                                       below=("n_below_lo", "sum"), above=("n_above_hi", "sum"))
        print(f"\n  {'channel':20s}{'sensors':>8s}{'site-days':>12s}{'sentinel':>10s}"
              f"{'below_lo':>9s}{'clamped':>9s}{'above_hi':>9s}")
        for ch, r in agg.iterrows():
            print(f"  {ch:20s}{r.sensors:>8d}{r.days:>12,}{r.sentinel:>10,}"
                  f"{r.below:>9,}{r.clamped:>9,}{r.above:>9,}")
        empty = sorted(set(registry.NAMES) - set(agg.index[agg.days > 0]))
        if empty:
            print(f"\n  channels with NO observation anywhere in this pass: {empty}")
    return pd.DataFrame(rows)


# ---- verify ---------------------------------------------------------------------------------------

@verify.check("d2", "no _dv-sourced channel-day carries a fabricated spread statistic")
def _check_dv_null_rule():
    import random

    files = sorted(config.WATER_CANONICAL.glob("*.parquet"))
    if not files:
        return None, "no canonical store yet"
    rng = random.Random(0)
    bad = 0
    checked = 0
    for p in rng.sample(files, min(50, len(files))):
        d = pd.read_parquet(p)
        for ch in registry.NAMES:
            m, mx = f"{ch}_mean", f"{ch}_max"
            if m in d.columns and mx in d.columns and f"{ch}_n_obs" in d.columns:
                # a pre-aggregated day: n_obs null but mean present -> max must be null too
                pre = d[m].notna() & d[f"{ch}_n_obs"].isna()
                checked += int(pre.sum())
                bad += int((pre & d[mx].notna()).sum())
    return bad == 0, f"{checked} pre-aggregated channel-day(s) sampled, {bad} with fabricated spread"


@verify.check("d2", "scrub tallies close at the cell grain and match the live registry")
def _check_tally_closure():
    if not config.SCRUB_TALLIES_PATH.exists():
        return None, "no tally artifact yet"
    if bio.read_stamps(config.SCRUB_TALLIES_PATH).get("registry") != registry.fingerprint():
        return False, "tallies were cut under a different registry -- rerun d2"
    t = pd.read_parquet(config.SCRUB_TALLIES_PATH)
    lhs = t.n_kept
    rhs = t.n_cells - t.n_sentinel - t.n_below_lo - t.n_above_hi
    bad = int((lhs != rhs).sum())
    return bad == 0, f"{len(t):,} (sensor, channel) tallies; {bad} violate n_kept = n_cells - n_sentinel - n_below_lo - n_above_hi"


@verify.check("d2", "every canonical file's registry stamp matches the live registry", deep=True)
def _check_canonical_stamps():
    files = sorted(config.WATER_CANONICAL.glob("*.parquet"))
    if not files:
        return None, "no canonical store yet"
    want = registry.fingerprint()
    stale_files = [p.name for p in files if bio.read_stamps(p).get("registry") != want]
    return not stale_files, (f"{len(stale_files)} of {len(files)} stale, e.g. {stale_files[:3]}"
                             if stale_files else f"{len(files):,} canonical files, all current")
