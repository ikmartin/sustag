"""A3: fetch observations. Everything fetched, NOTHING DERIVED -- and every outcome ACCOUNTED.

ONE ARTIFACT PER SENSOR: `acquired/water/native/{uid}.parquet`, every parameter the source reports at native resolution, kept for ANY canonical channel. Only a sensor carrying nothing collectable is discarded.

THE FOUR-STATE ACCOUNTING. Every registration's fetch outcome lands in exactly one state -- `skipped` (a scope rule declined, reason on the sensors table), `cached` (a native holds the record), `source_empty` (the source was asked and affirmatively returned nothing), `attempt_failed` (the request errored; retried next pass) -- and the states partition: `registered = skipped + cached + source_empty + attempt_failed` after a full pass. `source_empty` is DURABLE: recorded once with a timestamp and not re-asked on incremental runs (a refresh or force re-asks, since a source can start publishing). `attempt_failed` is TRANSIENT: always retried. The two were once one `no_data` token, which made a rate-limited site indistinguishable from a genuinely empty one, forever.

STATUSES ACCUMULATE, PER SHARD. Each run writes its outcomes to its OWN file (`fetch_status.shard{i}.parquet` under a sharded run, `fetch_status.run.parquet` otherwise) and the parent merges them last-wins into the durable `fetch_status.parquet` -- under the old single-file scheme every shard wrote the same path at exit and the last one clobbered the other N-1's bookkeeping. The merged store then lands on the sensors table's fetch block, so one row answers "why does this sensor have no record".

THE TRAILING REVISION WINDOW. USGS revises provisional data for months before approving it, so a fetched-once native freezes the unrevised values. `refresh=True` re-pulls the trailing `REVISION_DAYS` for sensors already on disk and merges last-wins -- append-only acquisition is wrong for observations, and this is the declared mutation class the snapshot diff expects.

Fetching is idempotent: a sensor whose native exists is skipped unless refreshed or forced; every write is atomic, so an interrupted run resumes rather than leaving a truncated file the skip check mistakes for finished.
"""

from __future__ import annotations

import traceback

import pandas as pd

from .. import config, contracts, registry
from .. import io as bio
from .sources import iwqis, mpca, usgs

ADAPTERS = {"USGS": usgs, "IWQIS": iwqis, "MPCA": mpca}

# Re-pull this many trailing days for on-disk sensors when refreshing. Sized to USGS's provisional
# period -- data older than this is approved and stable; the A4 diff's revised_in_window class reads
# THIS constant, so the fetch and the drift classifier cannot disagree about what "recent" means.
REVISION_DAYS = 120

# How many daily-tier sites to prefetch before the loop consumes them. Large enough that a window is
# several 300-site batches, small enough that the waiting frames stay a fraction of a gigabyte --
# fetching a whole shard up front once held 3.9 GB across three workers.
DV_WINDOW = 1_200

STATUS_PATH = config.ACQUIRED / "water" / "fetch_status.parquet"

STATES = ("skipped", "cached", "source_empty", "attempt_failed")


def _any_canonical(obs: pd.DataFrame, source: str) -> bool:
    """Does this frame carry a value for ANY canonical channel? Measured on the data, not the column list."""
    for c in obs.columns:
        if c.endswith("_quality"):
            continue
        ch, _ = registry.resolve(source, c)
        if ch and obs[c].notna().any():
            return True
    return False


def _params_available(native_path) -> str | None:
    """'+'-joined every parameter column the archived native holds, from the footer alone."""
    import pyarrow.parquet as pq

    if not native_path.exists():
        return None
    try:
        names = pq.ParquetFile(native_path).schema_arrow.names
    except Exception:
        return None
    return "+".join(sorted(c for c in names if c != "datetime" and not c.endswith("_quality"))) or None


def _covers_declared(native_path, source: str, declared: str | None) -> bool:
    """Does the archived native carry a column for every channel the registration declares?

    CACHED MUST MEAN "CACHED FOR THIS REQUEST". The fetch is channel-scoped -- adapters restrict the call to the pcodes the declaration names -- so a native on disk holds only what was declared WHEN IT WAS FETCHED. Adding a channel to the registry widens `channels_declared`, `canonical.stale` correctly rebuilds every canonical file from those natives, and the new channel is all-null everywhere while A3 reports every sensor `cached`. Nothing downstream can see the difference between "this sensor does not measure it" and "we never asked".

    The evidence was already being computed: `_params_available` reads the native's footer on every branch of `process_sensor` and lands on the sensors table, where it was used only for display.

    A native missing a declared channel's columns is NOT proof the sensor lacks it -- the source may simply have returned nothing. So this returns False (refetch) only when a declared channel has NO column present; the fetch then settles it, and a genuinely absent channel costs one request per registry widening rather than a permanent hole.
    """
    if not declared:
        return True
    have = _params_available(native_path)
    if have is None:
        return False
    cols = {c.split("_")[0] for c in have.split("+")}
    for ch in (x.strip() for x in str(declared).split("+")):
        c = registry.CHANNELS.get(ch)
        if c is None:
            continue
        sm = c.sources.get(source)
        if sm is None or not getattr(sm, "cols", None):
            continue                                   # this source does not advertise it; nothing owed
        if not (set(sm.cols) & cols):
            return False
    return True


def process_sensor(uid: str, source: str, native_id: str, force: bool = False,
                   refresh: bool = False, declared: str | None = None) -> dict:
    """Fetch and persist one sensor's native record. Returns an accounting row; derives nothing.

    `status` is one of the four partition states; `detail` carries the finer mechanism (fetched / refreshed / no_channels / the error class) for diagnosis without breaking the partition.
    """
    now = pd.Timestamp.now().isoformat(timespec="seconds")
    native_path = config.ACQ_WATER_NATIVE / f"{contracts.uid_to_filename(uid)}.parquet"
    start, end = config.collection_start().isoformat(), None

    if native_path.exists() and not force and not _covers_declared(native_path, source, declared):
        # A widened declaration is a different request, not a cache hit. Fall through to the fetch.
        print(f"    {uid}: native predates a widened channel declaration -- refetching", flush=True)
    elif native_path.exists() and not force:
        if not refresh:
            return dict(site_uid=uid, status="cached", detail="on_disk",
                        fetch_last_attempt=now, fetch_last_success=now,
                        params_available=_params_available(native_path))
        old = pd.read_parquet(native_path)
        if len(old):
            cut = old.index.max() - pd.Timedelta(days=REVISION_DAYS)
            fresh = ADAPTERS[source].fetch(native_id, start=str(cut.date()), end=end, channels=declared)
            if fresh is not None and len(fresh):
                merged = pd.concat([old[old.index < cut], fresh])
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                bio.write_parquet(merged, native_path, index=True)
                return dict(site_uid=uid, status="cached", detail=f"refreshed+{len(merged) - len(old)}",
                            fetch_last_attempt=now, fetch_last_success=now,
                            params_available=_params_available(native_path))
        return dict(site_uid=uid, status="cached", detail="on_disk",
                    fetch_last_attempt=now, fetch_last_success=now,
                    params_available=_params_available(native_path))

    obs = ADAPTERS[source].fetch(native_id, start=start, end=end, channels=declared)
    if obs is None or obs.empty:
        native_path.unlink(missing_ok=True)
        return dict(site_uid=uid, status="source_empty", detail="no_data",
                    fetch_last_attempt=now, fetch_last_success=None, params_available=None)
    # Only a sensor carrying NO canonical channel is discarded -- nothing about it is collectable. A
    # nitrate-less turbidity station stays: 16 of them holding ~12M rows were once deleted on every run.
    if not _any_canonical(obs, source):
        native_path.unlink(missing_ok=True)
        return dict(site_uid=uid, status="source_empty", detail="no_channels",
                    fetch_last_attempt=now, fetch_last_success=None, params_available=None)
    bio.write_parquet(obs, native_path, index=True)
    return dict(site_uid=uid, status="cached", detail=f"fetched+{len(obs)}",
                fetch_last_attempt=now, fetch_last_success=now,
                params_available=_params_available(native_path))


ACCOUNTING_COLS = ["site_uid", "status", "detail", "fetch_last_attempt", "fetch_last_success",
                   "params_available"]


def _run_status_path(shard: int | None) -> object:
    name = f"fetch_status.shard{shard}.parquet" if shard is not None else "fetch_status.run.parquet"
    return STATUS_PATH.with_name(name)


def load_accounting() -> pd.DataFrame:
    """The durable accumulated accounting; empty frame with the contract columns when absent.

    SCHEMA-TOLERANT ON PURPOSE: the adopted archive carried a predecessor bookkeeping file with other columns and another vocabulary, and rows outside this build's partition states are dropped rather than propagated -- they answer a question this accounting no longer asks.
    """
    if not STATUS_PATH.exists():
        return pd.DataFrame(columns=ACCOUNTING_COLS)
    d = pd.read_parquet(STATUS_PATH).reindex(columns=ACCOUNTING_COLS)
    legacy = ~d.status.isin(STATES)
    if legacy.any():
        print(f"  dropping {int(legacy.sum()):,} legacy accounting row(s) outside the partition vocabulary")
        d = d[~legacy]
    return d.reset_index(drop=True)


def merge_accounting(run_files: list) -> pd.DataFrame:
    """Fold per-run outcome files into the durable store, last-wins per uid, preserving last-success timestamps across runs; then push the fetch block onto the sensors table."""
    frames = [pd.read_parquet(p) for p in run_files if p.exists()]
    if not frames:
        return load_accounting()
    fresh = pd.concat(frames, ignore_index=True).drop_duplicates("site_uid", keep="last")
    prior = load_accounting()
    if len(prior):
        prev_success = prior.set_index("site_uid").fetch_last_success
        carry = fresh.fetch_last_success.isna() & fresh.site_uid.isin(prev_success.index)
        fresh.loc[carry, "fetch_last_success"] = fresh.site_uid[carry].map(prev_success)
        merged = pd.concat([prior[~prior.site_uid.isin(set(fresh.site_uid))], fresh],
                           ignore_index=True)
    else:
        merged = fresh
    merged = merged[ACCOUNTING_COLS].sort_values("site_uid", ignore_index=True)
    bio.write_parquet(merged, STATUS_PATH)
    for p in run_files:
        p.unlink(missing_ok=True)

    if config.SENSORS_PATH.exists():
        sensors = pd.read_parquet(config.SENSORS_PATH)
        block = merged.rename(columns={"status": "fetch_status"})
        sensors = contracts.update_block(
            sensors, block, ["fetch_status", "fetch_last_attempt", "fetch_last_success",
                             "params_available"])
        # skipped sensors carry their state from the discovery reason, not from an attempt
        skip = sensors.fetch_skip_reason.notna()
        sensors.loc[skip, "fetch_status"] = "skipped"
        out = contracts.conform_sensors(sensors)
        bio.write_parquet(out, config.SENSORS_PATH,
                          stamps=bio.read_stamps(config.SENSORS_PATH))
        counts = out.fetch_status.value_counts(dropna=False).to_dict()
        print(f"  sensors table fetch block updated: {counts}")
    return merged


def shard_of(sensors: pd.DataFrame, shard: int, shards: int) -> pd.DataFrame:
    """One shard's slice, ROUND-ROBIN over sorted uids rather than contiguous blocks.

    Cost per sensor is wildly uneven -- 2.6 s at a dead gauge against 103.7 s at an instrumented one -- and the expensive ones cluster geographically. Contiguous blocks would hand one worker the Mississippi mainstem and another a run of empty headwaters; round-robin interleaves them.
    """
    return sensors.sort_values("site_uid").iloc[shard::shards]


def run_sharded(shards: int, force: bool = False, refresh: bool = False,
                sources: list[str] | None = None) -> int:
    """One child process per shard, each pinned to its own API key, each writing its OWN status file; the parent merges them once every shard exits. Returns the failure count.

    SEPARATE PROCESSES, NOT THREADS, forced rather than chosen: `dataretrieval` reads the key from `os.environ`, one value per process. Each child is a plain `--shard i --shards n` invocation, runnable by hand to debug one slice; shards touch disjoint sensors, so nothing coordinates and a killed shard is re-run, not repaired.
    """
    import subprocess
    import sys

    procs = []
    for i in range(shards):
        cmd = [sys.executable, "-m", "data.build.acquire.records",
               "--shard", str(i), "--shards", str(shards)]
        if force:
            cmd.append("--force")
        if refresh:
            cmd.append("--refresh")
        if sources:
            cmd += ["--sources", *sources]
        procs.append((i, subprocess.Popen(cmd, cwd=str(config.REPO))))
        print(f"  shard {i}/{shards} started (pid {procs[-1][1].pid}, key {i})", flush=True)

    failed = 0
    for i, p in procs:
        rc = p.wait()
        print(f"  shard {i} exited {rc}", flush=True)
        failed += rc != 0
    merge_accounting([_run_status_path(i) for i in range(shards)])
    return failed


def main(sensors: pd.DataFrame | None = None, force: bool = False, refresh: bool = False,
         sources: list[str] | None = None,
         shard: int | None = None, shards: int | None = None,
         snapshot: str | None = None) -> pd.DataFrame:
    """Fetch every registered sensor, one at a time.

    THE LOOP IS SERIAL ON PURPOSE. dataretrieval already parallelises within a call; threading here multiplied a concurrency setting (six workers over a 32-deep library default reached ~190 simultaneous requests and lost 194 sites to HTTP 429). `usgs._CONCURRENT` is the only knob, and a serial loop keeps error attribution per-sensor and progress honest.
    """
    config.ensure_dirs()
    if sensors is None:
        sensors = pd.read_parquet(config.SENSORS_PATH)
    if sources:
        sensors = sensors[sensors.source.isin(sources)]

    # PIN THE KEY BEFORE ANY REQUEST. Each shard owns one key for its whole run.
    if shards:
        usgs.use_token(shard or 0)
        sensors = shard_of(sensors, shard or 0, shards)
        print(f"  shard {shard}/{shards}: {len(sensors):,} sensor(s), API key {(shard or 0) % usgs.n_tokens()}")

    rows = []
    # Scope-skipped sensors are ACCOUNTED, not silently filtered: their state is `skipped`, reason on the table.
    skip = (sensors.fetch_skip_reason.notna() if "fetch_skip_reason" in sensors.columns
            else pd.Series(False, index=sensors.index))
    for r in sensors[skip].itertuples():
        rows.append(dict(site_uid=r.site_uid, status="skipped", detail=str(r.fetch_skip_reason),
                         fetch_last_attempt=None, fetch_last_success=None, params_available=None))
    if skip.any():
        print(f"  {int(skip.sum())} sensor(s) scope-skipped (reasons on the sensors table)")
    sensors = sensors[~skip]

    # DURABLE source_empty: not re-asked on incremental runs. A refresh or force re-asks -- sources
    # can start publishing -- and a sensor never asked before is always asked.
    prior = load_accounting()
    if len(prior) and not force and not refresh:
        empty = set(prior.site_uid[prior.status == "source_empty"])
        hold = sensors.site_uid.isin(empty)
        if hold.any():
            print(f"  {int(hold.sum())} sensor(s) held at durable source_empty (use --refresh to re-ask)")
            rows += prior[prior.site_uid.isin(set(sensors.site_uid[hold]))].to_dict("records")
            sensors = sensors[~hold]

    # BATCH THE DAILY TIER, WINDOWED. One request per site costs 0.43 s and 300 per request 0.085 s/site.
    end_iso = pd.Timestamp.now().normalize().date().isoformat()
    dv_ids = [r.source_site_id for r in sensors.itertuples()
              if r.source == "USGS"
              and registry.fetch_tier(getattr(r, "channels_declared", None)) == "dv"
              and not (config.ACQ_WATER_NATIVE / f"{contracts.uid_to_filename(r.site_uid)}.parquet").exists()]
    dv_queue = list(dv_ids)
    if dv_queue:
        print(f"  {len(dv_queue):,} daily-tier sensor(s) to prefetch, {DV_WINDOW:,} at a time", flush=True)

    def _ensure_dv(native_id: str) -> None:
        if native_id in usgs._DV_FETCHED or not dv_queue:
            return
        window = dv_queue[:DV_WINDOW]
        del dv_queue[:DV_WINDOW]
        usgs.prefetch_dv(window, config.collection_start().isoformat(), end_iso)

    failed = []
    for k, r in enumerate(sensors.itertuples(), 1):
        try:
            if r.source == "USGS":
                _ensure_dv(r.source_site_id)
            rows.append(process_sensor(r.site_uid, r.source, r.source_site_id, force=force,
                                       refresh=refresh,
                                       declared=getattr(r, "channels_declared", None)))
        except Exception as e:                          # noqa: BLE001 -- per-sensor attribution
            failed.append((r.site_uid, f"{type(e).__name__}: {e}"))
            rows.append(dict(site_uid=r.site_uid, status="attempt_failed",
                             detail=f"{type(e).__name__}: {e}"[:200],
                             fetch_last_attempt=pd.Timestamp.now().isoformat(timespec="seconds"),
                             fetch_last_success=None, params_available=None))
        if k % 25 == 0:
            print(f"  {k}/{len(sensors)}", flush=True)

    out = pd.DataFrame(rows, columns=ACCOUNTING_COLS)
    if len(out):
        print(f"\n  fetch outcomes: {out.status.value_counts().to_dict()}; {len(failed)} errored")
        bio.write_parquet(out, _run_status_path(shard))
        if shard is None:
            merge_accounting([_run_status_path(None)])
    for uid, err in failed[:10]:
        print(f"  ERROR {uid}: {err}")
    return out


# ---- verify ----------------------------------------------------------------------------------------

from .. import verify


@verify.check("a3", "the fetch partition holds: registered = skipped + cached + source_empty + attempt_failed")
def _check_partition():
    if not config.SENSORS_PATH.exists():
        return None, "no sensors table yet"
    s = pd.read_parquet(config.SENSORS_PATH, columns=["site_uid", "fetch_status"])
    counts = s.fetch_status.value_counts(dropna=False).to_dict()
    unknown = {k: v for k, v in counts.items() if pd.notna(k) and k not in STATES}
    unaccounted = int(s.fetch_status.isna().sum())
    if unknown:
        return False, f"states outside the partition: {unknown}"
    if unaccounted:
        return None, f"{unaccounted:,} registration(s) not yet attempted (partition holds after a full a3)"
    return True, f"{len(s):,} registrations partitioned: {counts}"


@verify.check("a3", "no stale per-run status files await a merge")
def _check_no_stray_status():
    stray = sorted(STATUS_PATH.parent.glob("fetch_status.shard*.parquet")) + \
        sorted(STATUS_PATH.parent.glob("fetch_status.run.parquet"))
    return not stray, (f"{len(stray)} unmerged run file(s) -- a sharded run died before its merge"
                       if stray else "clean")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-pull the trailing revision window and re-ask source_empty sensors")
    ap.add_argument("--sources", nargs="*", default=None, choices=list(ADAPTERS))
    ap.add_argument("--shard", type=int, default=None)
    ap.add_argument("--shards", type=int, default=None)
    a = ap.parse_args()
    try:
        if a.shards and a.shard is None:
            raise SystemExit(run_sharded(a.shards, force=a.force, refresh=a.refresh, sources=a.sources))
        main(force=a.force, refresh=a.refresh, sources=a.sources, shard=a.shard, shards=a.shards)
    except Exception:
        traceback.print_exc()
        raise


@verify.check("a3", "no attempt_failed row is older than the retry horizon")
def _check_retry_horizon():
    """attempt_failed is the TRANSIENT state -- a row stuck there across many days means the retry loop is not actually retrying (or a sensor fails deterministically and deserves eyes)."""
    if not STATUS_PATH.exists():
        return None, "no accounting store yet"
    d = pd.read_parquet(STATUS_PATH)
    failed = d[d.status == "attempt_failed"]
    if not len(failed):
        return True, "no attempt_failed rows"
    age = (pd.Timestamp.now() - pd.to_datetime(failed.fetch_last_attempt, errors="coerce")).dt.days
    old = failed[age > 14]
    return len(old) == 0, (f"{len(failed)} attempt_failed row(s); {len(old)} older than 14 days: "
                           f"{old.site_uid.head(4).tolist()}" if len(old)
                           else f"{len(failed)} attempt_failed row(s), all within the horizon")
