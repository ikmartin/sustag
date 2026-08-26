"""A3: fetch observations. Everything fetched, NOTHING DERIVED.

ONE ARTIFACT PER SENSOR: `acquired/water/native/{uid}.parquet`, every parameter the source reports at native resolution, kept for ANY canonical channel -- a discharge or turbidity sensor is a graph node in its own right, and only a sensor carrying nothing collectable is discarded. The build2 ancestor also binned nitrate to daily files and computed the record block here; both were DERIVATION smuggled into the fetch, and both now belong to D2 -- which is what makes this layer rerunnable on its own cadence without touching a derived artifact.

THE ADAPTER CALL IS UNIFORM. `fetch(native_id, start, end, channels=declared)` for every source; USGS decides its own dv-vs-native tier from the declared channels INSIDE the adapter. The build2 caller branched on `source == "USGS"` to pass tier keywords -- the exact routing the adapter contract exists to prevent.

THE TRAILING REVISION WINDOW. USGS revises provisional data for months before approving it, so a fetched-once native freezes the unrevised values. `refresh=True` re-pulls the trailing `REVISION_DAYS` for sensors already on disk and merges last-wins -- append-only acquisition is wrong for observations, and this is the declared mutation class the snapshot diff expects.

Fetching is idempotent: a sensor whose native exists is skipped unless refreshed or forced; every write is atomic, so an interrupted run resumes rather than leaving a truncated file the skip check mistakes for finished. The per-run fetch statuses go to `acquired/water/fetch_status.parquet` -- acquisition bookkeeping the A4 reconciliation reads, not a derived artifact.
"""

from __future__ import annotations

import traceback

import pandas as pd

from .. import config, contracts, registry
from .. import io as bio
from .sources import iwqis, mpca, usgs

ADAPTERS = {"USGS": usgs, "IWQIS": iwqis, "MPCA": mpca}

# Re-pull this many trailing days for on-disk sensors when refreshing. Sized to USGS's provisional
# period -- data older than this is approved and stable; see the A4 diff's revised_in_window class.
REVISION_DAYS = 120

# How many daily-tier sites to prefetch before the loop consumes them. Large enough that a window is
# several 300-site batches, small enough that the waiting frames stay a fraction of a gigabyte --
# fetching a whole shard up front once held 3.9 GB across three workers.
DV_WINDOW = 1_200

STATUS_PATH = config.ACQUIRED / "water" / "fetch_status.parquet"


def _any_canonical(obs: pd.DataFrame, source: str) -> bool:
    """Does this frame carry a value for ANY canonical channel? Measured on the data, not the column list."""
    for c in obs.columns:
        if c.endswith("_quality"):
            continue
        ch, _ = registry.resolve(source, c)
        if ch and obs[c].notna().any():
            return True
    return False


def process_sensor(uid: str, source: str, native_id: str, force: bool = False,
                   refresh: bool = False, declared: str | None = None) -> dict:
    """Fetch and persist one sensor's native record. Returns a status row; derives nothing.

    Statuses: `fetched` (written), `refreshed` (trailing window merged), `cached` (on disk, untouched), `no_data` (source returned nothing), `no_channels` (nothing collectable -- discarded), `error`.
    """
    native_path = config.ACQ_WATER_NATIVE / f"{contracts.uid_to_filename(uid)}.parquet"
    start, end = f"{config.YEAR_START}-01-01", f"{config.YEAR_END}-12-31"

    if native_path.exists() and not force:
        if not refresh:
            return dict(site_uid=uid, status="cached")
        old = pd.read_parquet(native_path)
        if len(old):
            cut = old.index.max() - pd.Timedelta(days=REVISION_DAYS)
            fresh = ADAPTERS[source].fetch(native_id, start=str(cut.date()), end=end, channels=declared)
            if fresh is not None and len(fresh):
                merged = pd.concat([old[old.index < cut], fresh])
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                bio.write_parquet(merged, native_path, index=True)
                return dict(site_uid=uid, status="refreshed",
                            n_new=int(len(merged) - len(old)))
        return dict(site_uid=uid, status="cached")

    obs = ADAPTERS[source].fetch(native_id, start=start, end=end, channels=declared)
    if obs is None or obs.empty:
        native_path.unlink(missing_ok=True)
        return dict(site_uid=uid, status="no_data")
    # Only a sensor carrying NO canonical channel is discarded -- nothing about it is collectable. A
    # nitrate-less turbidity station stays: 16 of them holding ~12M rows were once deleted on every run.
    if not _any_canonical(obs, source):
        native_path.unlink(missing_ok=True)
        return dict(site_uid=uid, status="no_channels")
    bio.write_parquet(obs, native_path, index=True)
    return dict(site_uid=uid, status="fetched", n_rows=len(obs))


def shard_of(sensors: pd.DataFrame, shard: int, shards: int) -> pd.DataFrame:
    """One shard's slice, ROUND-ROBIN over sorted uids rather than contiguous blocks.

    Cost per sensor is wildly uneven -- 2.6 s at a dead gauge against 103.7 s at an instrumented one -- and the expensive ones cluster geographically. Contiguous blocks would hand one worker the Mississippi mainstem and another a run of empty headwaters; round-robin interleaves them.
    """
    return sensors.sort_values("site_uid").iloc[shard::shards]


def run_sharded(shards: int, force: bool = False, refresh: bool = False,
                sources: list[str] | None = None) -> int:
    """One child process per shard, each pinned to its own API key. Returns the failure count.

    SEPARATE PROCESSES, NOT THREADS, forced rather than chosen: `dataretrieval` reads the key from `os.environ`, one value per process. Each child is a plain `--shard i --shards n` invocation, runnable by hand to debug one slice; shards touch disjoint sensors, so nothing coordinates and a killed shard is re-run, not repaired.
    """
    import subprocess
    import sys

    procs = []
    for i in range(shards):
        cmd = [sys.executable, "-m", "src.build3.acquire.records",
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

    # A named scope filter, decided at discovery: non-surface with no declared nitrate is not fetched.
    # The registration keeps its row and its reason -- nothing dropped, only not asked for. The canary
    # roster punches through this in the census, so the class stays exercised.
    skip = (sensors.fetch_skip_reason.notna() if "fetch_skip_reason" in sensors.columns
            else pd.Series(False, index=sensors.index))
    if skip.any():
        print(f"  skipping {int(skip.sum())} sensor(s) marked non-surface with no declared nitrate")
        sensors = sensors[~skip]

    # BATCH THE DAILY TIER, WINDOWED. One request per site costs 0.43 s and 300 per request 0.085 s/site.
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
        usgs.prefetch_dv(window, f"{config.YEAR_START}-01-01", f"{config.YEAR_END}-12-31")

    rows, failed = [], []
    for k, r in enumerate(sensors.itertuples(), 1):
        try:
            if r.source == "USGS":
                _ensure_dv(r.source_site_id)
            rows.append(process_sensor(r.site_uid, r.source, r.source_site_id, force=force,
                                       refresh=refresh,
                                       declared=getattr(r, "channels_declared", None)))
        except Exception as e:                          # noqa: BLE001 -- per-sensor attribution
            failed.append((r.site_uid, f"{type(e).__name__}: {e}"))
            rows.append(dict(site_uid=r.site_uid, status="error"))
        if k % 25 == 0:
            print(f"  {k}/{len(sensors)}", flush=True)

    out = pd.DataFrame(rows)
    if len(out):
        counts = out.status.value_counts().to_dict()
        print(f"\n  fetch statuses: {counts}; {len(failed)} errored")
        bio.write_parquet(out.assign(run_at=pd.Timestamp.now().isoformat()), STATUS_PATH)
    for uid, err in failed[:10]:
        print(f"  ERROR {uid}: {err}")
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-pull the trailing revision window")
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
