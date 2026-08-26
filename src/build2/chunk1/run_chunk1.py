"""Chunk 1 — every raw download, nothing derived.

    python -m src.build2.run chunk1
    python -m src.build2.run chunk1 --only records      # one branch
    python -m src.build2.run chunk1 --serial            # no thread pool

THREE INDEPENDENT BRANCHES, THEN TWO DEPENDENT STEPS.

    A records   water observations, every parameter        | concurrent
    B network   NHDPlus flowlines, VAA, catchments         | concurrent
    C weather   gridMET over the AOE                       | concurrent
      D attrs   StreamCat, Wieczorek, NID                  needs B

B gates branch D because it is what defines the COMID set. The SNAP moved to chunk 3: it derives rather than fetches, and its consumer is the merge rule, which lives there.

WHY THE POOL IS THREE AND NOT MORE. Branch A talks to `waterdata.usgs.gov`, B and D to `hydro.usgs.gov` and NLDI -- different services on shared USGS infrastructure. An earlier design multiplied two concurrency knobs, six site workers over a library default of 32, reached about 190 simultaneous requests and lost 194 sites to HTTP 429. A's internal fan-out (`sources.usgs._CONCURRENT`) is the only API concurrency knob; B, C and D do not fan out. If 429s appear, `--serial`.

The national rasters (CDL, gTREND, AgTile) are NOT handled here -- see the deferral note in PLAN.md.
"""

from __future__ import annotations

# Running this file directly (`python run_chunkN.py`) fails on the relative imports below with "attempted relative import with no known parent package", which says nothing about the fix. Catch it and say what to type.
# Running this file directly leaves the repo root off sys.path, so the relative imports below fail with "attempted relative import with no known parent package". Re-exec as a module instead of explaining the fix.
if __name__ == "__main__" and __package__ in (None, ""):
    import pathlib as _pl
    import runpy as _rp
    import sys as _sys

    _root = _pl.Path(__file__).resolve().parents[3]
    _sys.path.insert(0, str(_root))
    _rp.run_module("src.build2.chunk1.run_chunk1", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

import argparse
import traceback

import pandas as pd

from .. import config, io as bio, schema

BRANCHES = ("records", "network", "weather", "attrs")

# What records.py contributes to the site table.
RECORD_BLOCK = ["params_available", "n_nitrate_days", "first_day", "last_day", "longest_gap_d"]
# The block-update rule lives on the contract, not here -- every chunk that owns a block needs it.
_join = schema.update_block


def main(force: bool = False, only: str | None = None, serial: bool = False,
         sources: list[str] | None = None) -> pd.DataFrame:
    from concurrent.futures import ThreadPoolExecutor

    from . import comid_attrs, network, records, weather

    config.ensure_dirs()
    if not config.AOE_PATH.exists():
        raise FileNotFoundError(
            "aoe.parquet is missing -- run chunk 0 first. Without it every clip here would "
            "silently use the seed boxes, which truncate the largest basins.")
    print(config.describe())

    sites = pd.read_parquet(config.REGISTRATIONS_PATH)
    print(f"\n{len(sites):,} registrations, {int(sites.aoe_seed.sum())} seeds")

    want = set(BRANCHES if only is None else [only])
    results: dict[str, object] = {}

    def branch_records():
        print("\n[A] records", flush=True)
        return records.main(sites=sites, force=force, sources=sources)

    def branch_network():
        print("\n[B] network", flush=True)
        return network.main(force=force)

    def branch_weather():
        print("\n[C] weather", flush=True)
        return weather.main(force=force)

    jobs = [(n, fn) for n, fn in (("records", branch_records), ("network", branch_network),
                                  ("weather", branch_weather)) if n in want]
    if serial or len(jobs) <= 1:
        for n, fn in jobs:
            try:
                results[n] = fn()
            except Exception:
                traceback.print_exc()
                results[n] = None
    else:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futs = {pool.submit(fn): n for n, fn in jobs}
            for f, n in futs.items():
                try:
                    results[n] = f.result()
                except Exception:
                    traceback.print_exc()
                    results[n] = None

    if "records" in results and isinstance(results["records"], pd.DataFrame):
        sites = _join(sites, results["records"], RECORD_BLOCK)

    if "attrs" in want:
        print("\n[D] comid attributes", flush=True)
        try:
            results["attrs"] = comid_attrs.main(force=force)
        except Exception:
            traceback.print_exc()

    out = schema.conform(sites)
    bad = schema.validate(out)
    if bad:
        raise ValueError("site table violates the contract:\n  " + "\n  ".join(bad))
    bio.write_parquet(out, config.REGISTRATIONS_PATH)
    n_rec = int(out.n_nitrate_days.notna().sum()) if "n_nitrate_days" in out else 0
    print(f"\nwrote {config.REGISTRATIONS_PATH.name}: {len(out):,} registrations, {n_rec:,} with records")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="refetch everything, ignoring what is on disk")
    ap.add_argument("--only", choices=BRANCHES, help="run a single branch")
    ap.add_argument("--serial", action="store_true", help="disable the thread pool")
    ap.add_argument("--sources", nargs="*", default=None, choices=["USGS", "IWQIS", "MPCA"])
    a = ap.parse_args()
    main(force=a.force, only=a.only, serial=a.serial, sources=a.sources)
