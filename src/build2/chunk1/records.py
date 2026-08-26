"""Fetch observations and bin nitrate onto standard-time days.

TWO ARTIFACTS PER SITE, AND THEY HAVE DIFFERENT ADMISSION RULES. `interim2/water/native/{uid}.parquet` holds every parameter the source reports at native resolution and is kept for ANY canonical channel in `params.CHANNELS`, nitrate or not -- a discharge or turbidity sensor is a graph node in its own right, so the native is the point of fetching it. `interim2/water/daily/{uid}.parquet` holds the daily nitrate maximum and exists only where there is nitrate to bin.

Only a site carrying no canonical channel at all is discarded, because nothing about it is collectable.

A DAY IS CENTRAL STANDARD TIME EVERYWHERE. The old build resampled a UTC-aware index, so its "day" ran 18:00-18:00 Central. Here every series is converted to one fixed offset, `io.STANDARD_TZ`, so every day in every source is the same 24 hours with no DST discontinuity. The output is keyed on a plain date, not a timestamp, so the question cannot be reopened downstream.

Fetching is idempotent: a site whose native parquet already exists is skipped unless forced, and every write is atomic, so an interrupted run resumes rather than leaving a truncated file that the skip check mistakes for finished.
"""

from __future__ import annotations

import traceback

import pandas as pd

from .. import config, io as bio, params, schema
from ..sources import iwqis, mpca, usgs

ADAPTERS = {"USGS": usgs, "IWQIS": iwqis, "MPCA": mpca}

# COLLECT ANYTHING WITH A NITRATE OBSERVATION. This is a storage floor, not the modelling one: the real "is this enough record to train on" test is `chunk6.assemble.MIN_GAUGE_DAYS`, applied at the end to the MERGED record.
#
# It used to be 60 here, which was wrong once chunk 6 existed. A registration holding 22 days is worthless alone but not worthless: if it merges into a gauge that already clears the bar, those days extend a real record, and discarding them at fetch time made that impossible to discover later. Measured on IWQIS, the old floor withheld 9 sites and 132 site-days, 4 of which merge into an existing gauge. The sites that do NOT merge cost nothing now -- chunk 6 drops them from `sites.parquet`, so they never reach a model.
#
# 1 rather than 0 because a site with no nitrate at all yields an empty frame, reported as `covariate_only`. This floor governs the DAILY NITRATE FILE alone -- it has never decided whether the native is archived, and now that non-nitrate channels are collected it decides nothing about them either.
MIN_NITRATE_DAYS = 1

# How many daily-tier sites to prefetch before the loop consumes them. Large enough that a window is several
# 300-site batches, small enough that the frames waiting to be used stay a fraction of a gigabyte.
DV_WINDOW = 1_200

# Which column in a source's native frame carries nitrate. USGS names columns by pcode, so all three nitrate codes are candidates and whichever is present wins.
NITRATE_COLUMNS = {
    "USGS": list(usgs.NITRATE_PCODES),
    "IWQIS": ["nitrate_con"],
    "MPCA": ["nitrate"],
}


def nitrate_series(obs: pd.DataFrame, source: str) -> pd.Series | None:
    """The site's nitrate series. Where a source reports it under several codes they are combined by row-wise max, which is the same rule used inside a day."""
    cols = [c for c in NITRATE_COLUMNS[source] if c in obs.columns]
    if not cols:
        return None
    s = obs[cols].astype("float64").max(axis=1)
    return s.dropna() if s.notna().any() else None


def _discard(*paths) -> None:
    """Remove archived records for a site the floor rejects.

    Needed because the floor is applied on every run, not only at first fetch: a site archived before the threshold existed, or one whose record shrinks after a re-fetch, would otherwise keep artifacts that its own status says are not there.
    """
    for p in paths:
        p.unlink(missing_ok=True)


def channels_present(obs: pd.DataFrame, source: str) -> list[str]:
    """Canonical channels this frame actually carries a value for, in registry order.

    Presence is measured on the DATA, not on the column list: an adapter returning a declared-but-empty column would otherwise make a site look instrumented for something it never recorded, which is the same failure `params_available` already has and the reason that column is not used for this.
    """
    live = []
    for c in obs.columns:
        if c.endswith("_quality"):
            continue
        ch, _ = params.resolve(source, c)
        if ch and ch not in live and obs[c].notna().any():
            live.append(ch)
    return [n for n in params.NAMES if n in live]


def process_site(uid: str, source: str, native_id: str, tz: str, force: bool = False,
                 min_days: int = MIN_NITRATE_DAYS, declared: str | None = None) -> dict:
    """Fetch, persist, and return the record-block columns for one registration.

    Writes nothing for a site under `min_days`, and removes anything an earlier run left, so the archive and the reported status can never disagree.
    """
    native_path = config.WATER_NATIVE / f"{schema.uid_to_filename(uid)}.parquet"
    daily_path = config.WATER_DAILY / f"{schema.uid_to_filename(uid)}.parquet"

    if native_path.exists() and not force:
        obs = pd.read_parquet(native_path)
    else:
        kw = {}
        if source == "USGS":
            # THE TIER IS DECIDED FROM WHAT THE SOURCE DECLARED, not from what comes back -- the point is to
            # avoid paying for a sub-daily pull at the ~13,700 flow-and-stage sites whose spread nothing reads.
            kw = dict(tier=params.fetch_tier(declared), channels=declared)
        obs = ADAPTERS[source].fetch(native_id, start=f"{config.YEAR_START}-01-01",
                                     end=f"{config.YEAR_END}-12-31", min_nitrate_days=min_days, **kw)
        if obs is None or obs.empty:
            _discard(native_path, daily_path)
            return dict(site_uid=uid, params_available=pd.NA, n_nitrate_days=pd.NA,
                        first_day=pd.NA, last_day=pd.NA, longest_gap_d=pd.NA, status="no_data")

    avail = [c for c in obs.columns if not c.endswith("_quality")]
    channels = channels_present(obs, source)
    nit = nitrate_series(obs, source)

    def _keep_native() -> None:
        """Archive the native for its non-nitrate channels; drop the nitrate-only daily file."""
        daily_path.unlink(missing_ok=True)
        if not native_path.exists() or force:
            bio.write_parquet(obs, native_path)

    # THE NATIVE SURVIVES A MISSING NITRATE RECORD, because nitrate is no longer the only thing collected. A turbidity or discharge sensor is a graph node whose series is the point of fetching it, and discarding here destroyed exactly that: 16 IWQIS turbidity stations holding ~12M rows were deleted on every run, which is why 110 natives existed against 135 raw parquets. Only a site carrying NO canonical channel at all is discarded, since nothing about it is collectable.
    if nit is None or nit.empty:
        if not channels:
            _discard(native_path, daily_path)
            return dict(site_uid=uid, params_available="+".join(sorted(avail)), n_nitrate_days=0,
                        first_day=pd.NA, last_day=pd.NA, longest_gap_d=pd.NA, status="no_channels")
        _keep_native()
        return dict(site_uid=uid, params_available="+".join(sorted(avail)), n_nitrate_days=0,
                    first_day=pd.NA, last_day=pd.NA, longest_gap_d=pd.NA, status="covariate_only")

    daily = bio.daily_max_local(nit.index, nit, tz)
    daily = daily.rename(columns={"value": "nitrate_mgl_as_n"}).assign(site_uid=uid)
    n_days, gap = bio.gap_stats(daily.date)
    # The floor is on NITRATE DAYS and governs the nitrate daily file only. A site under it keeps its native for the same reason as above -- a thin nitrate record does not make its discharge series worthless, and chunk 6 decides trainability over the MERGED record anyway.
    if n_days < min_days:
        _keep_native()
        return dict(site_uid=uid, params_available="+".join(sorted(avail)), n_nitrate_days=n_days,
                    first_day=str(min(daily.date)), last_day=str(max(daily.date)),
                    longest_gap_d=gap, status="too_few_days")

    if not native_path.exists() or force:
        bio.write_parquet(obs, native_path)
    bio.write_parquet(daily, daily_path)
    return dict(site_uid=uid, params_available="+".join(sorted(avail)), n_nitrate_days=n_days,
                first_day=str(min(daily.date)), last_day=str(max(daily.date)),
                longest_gap_d=gap, status="ok")


def shard_of(sites: pd.DataFrame, shard: int, shards: int) -> pd.DataFrame:
    """One shard's slice, ROUND-ROBIN over the site order rather than contiguous blocks.

    Cost per site is wildly uneven -- 2.6 s at a dead gauge against 103.7 s at an instrumented one -- and the expensive sites cluster geographically, because a well-instrumented river has several of them. Contiguous blocks would hand one worker the Mississippi mainstem and another a run of empty headwaters, so the slowest shard would set the wall clock. Round-robin over a sorted list interleaves them.
    """
    return sites.sort_values("site_uid").iloc[shard::shards]


def run_sharded(shards: int, force: bool = False, sources: list[str] | None = None) -> int:
    """Spawn one child process per shard, each pinned to its own API key. Returns the failure count.

    SEPARATE PROCESSES, NOT THREADS, and that is forced rather than chosen: `dataretrieval` reads the key from `os.environ`, one value per process, so threads cannot hold different keys at once. Each child is a plain `--shard i --shards n` invocation, which means anything the driver can run you can also run by hand to debug one slice.

    SAFE TO INTERRUPT. `process_site` skips a site whose native is already on disk, so a killed shard is re-run rather than repaired, and nothing coordinates between shards because they touch disjoint sites.
    """
    import subprocess
    import sys

    procs = []
    for i in range(shards):
        cmd = [sys.executable, "-m", "src.build2.chunk1.records",
               "--shard", str(i), "--shards", str(shards)]
        if force:
            cmd.append("--force")
        if sources:
            cmd += ["--sources", *sources]
        procs.append((i, subprocess.Popen(cmd, cwd=str(config.DATA.parents[1]))))
        print(f"  shard {i}/{shards} started (pid {procs[-1][1].pid}, key {i})", flush=True)

    failed = 0
    for i, p in procs:
        rc = p.wait()
        print(f"  shard {i} exited {rc}", flush=True)
        failed += rc != 0
    return failed


def main(sites: pd.DataFrame | None = None, force: bool = False,
         sources: list[str] | None = None,
         shard: int | None = None, shards: int | None = None) -> pd.DataFrame:
    """Fetch every registration, one at a time.

    THE SITE LOOP IS SERIAL ON PURPOSE. dataretrieval already parallelises within a call, so threading here multiplied a concurrency setting rather than adding one: six workers over a library default of 32 reached about 190 simultaneous requests and lost 194 sites to HTTP 429. `usgs._CONCURRENT` is now the only knob, and a serial loop keeps ordering deterministic, error attribution per-site, and progress honest.
    """
    config.ensure_dirs()
    sites = sites if sites is not None else pd.read_parquet(config.REGISTRATIONS_PATH)
    if sources:
        sites = sites[sites.source.isin(sources)]

    # PIN THE KEY BEFORE ANY REQUEST. Each shard owns one key for its whole run; see `usgs.use_token`.
    if shards:
        usgs.use_token(shard or 0)
        sites = shard_of(sites, shard or 0, shards)
        print(f"  shard {shard}/{shards}: {len(sites):,} site(s), API key {shard % usgs.n_tokens()}")

    # CHUNK 0 ALREADY DECIDED THIS ONE. A non-surface site declaring no nitrate has nothing the build
    # collects, and at census scale those are thousands of wells; skipping them here is the difference
    # between a fetch that is worth running and one that spends most of its time on groundwater monitoring.
    # The registration keeps its row and its reason -- nothing is dropped, only not asked for.
    skip = (sites.fetch_skip_reason.notna() if "fetch_skip_reason" in sites.columns
            else pd.Series(False, index=sites.index))
    if skip.any():
        print(f"  skipping {int(skip.sum())} site(s) chunk 0 marked non-surface with no nitrate")
        sites = sites[~skip]

    # BATCH THE DAILY TIER FIRST. The serial loop below stays exactly as it was -- it just finds the data
    # already in hand for a dv site. One request per site costs 0.43 s and 300 per request costs 0.235 s a
    # site, which over 13,331 sites is the difference between 1.6 hours and about 50 minutes.
    dv_ids = [r.source_site_id for r in sites.itertuples()
              if r.source == "USGS"
              and params.fetch_tier(getattr(r, "channels_declared", None)) == "dv"
              and not (config.WATER_NATIVE / f"{schema.uid_to_filename(r.site_uid)}.parquet").exists()]
    # PREFETCH IN WINDOWS, INTERLEAVED WITH THE LOOP. Fetching a whole shard's daily tier up front is what
    # made three workers hold 3.9 GB between them: nothing is consumed until the loop starts, so every site's
    # frame is live at once. A window bounds the resident set to what is about to be used, and the batching
    # still pays off because one window is several batches wide.
    dv_queue = list(dv_ids)
    if dv_queue:
        print(f"  {len(dv_queue):,} daily-tier site(s) to prefetch, {DV_WINDOW:,} at a time", flush=True)

    def _ensure_dv(native_id: str) -> None:
        """Fill the next window when the loop reaches a site nobody has fetched yet."""
        if native_id in usgs._DV_FETCHED or not dv_queue:
            return
        window = dv_queue[:DV_WINDOW]
        del dv_queue[:DV_WINDOW]
        usgs.prefetch_dv(window, f"{config.YEAR_START}-01-01", f"{config.YEAR_END}-12-31")

    rows, failed = [], []
    for k, r in enumerate(sites.itertuples(), 1):
        try:
            if r.source == "USGS":
                _ensure_dv(r.source_site_id)
            rows.append(process_site(r.site_uid, r.source, r.source_site_id, r.tz, force,
                                     declared=getattr(r, "channels_declared", None)))
        except Exception as e:
            failed.append((r.site_uid, f"{type(e).__name__}: {e}"))
            rows.append(dict(site_uid=r.site_uid, status="error"))
        if k % 25 == 0:
            print(f"  {k}/{len(sites)}", flush=True)

    out = pd.DataFrame(rows)
    ok = out[out.status == "ok"]
    kept = out[out.status.isin(("ok", "covariate_only", "too_few_days"))]
    print(f"\n{len(ok)}/{len(out)} sites archived with a nitrate record; "
          f"{len(kept) - len(ok)} more kept for their other channels "
          f"({int((out.status == 'covariate_only').sum())} with no nitrate at all, "
          f"{int((out.status == 'too_few_days').sum())} under the {MIN_NITRATE_DAYS}-day floor); "
          f"{int((out.status == 'no_channels').sum())} carry nothing collectable, "
          f"{int((out.status == 'no_data').sum())} returned nothing, {len(failed)} errored")
    if len(ok):
        print(f"  local-day nitrate records: {int(ok.n_nitrate_days.sum()):,} site-days, "
              f"median {ok.n_nitrate_days.median():,.0f} days/site")
    for uid, err in failed[:10]:
        print(f"  ERROR {uid}: {err}")
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sources", nargs="*", default=None, choices=list(ADAPTERS))
    ap.add_argument("--shard", type=int, default=None, help="this worker's index; requires --shards")
    ap.add_argument("--shards", type=int, default=None,
                    help="split across N processes, one API key each. Without --shard, spawns them.")
    a = ap.parse_args()
    try:
        if a.shards and a.shard is None:
            raise SystemExit(run_sharded(a.shards, force=a.force, sources=a.sources))
        main(force=a.force, sources=a.sources, shard=a.shard, shards=a.shards)
    except Exception:
        traceback.print_exc()
        raise
