"""The pipeline entry point. One chunk per phase, in order.

    python -m src.build2.run                  # every implemented chunk, in order
    python -m src.build2.run chunk3           # chunk 3 AND everything after it
    python -m src.build2.run chunk1 --single  # only chunk 1
    python -m src.build2.run --list

NAMING A CHUNK MEANS "FROM HERE ONWARD". Each chunk consumes the one before, so a partial rebuild leaves later artifacts describing inputs that have moved -- present, well-formed and stale. It also makes the resume after a merge-review stop a single obvious command.

    chunk0  seed filter and AOE definition        -> aoe.parquet, registrations.parquet
    chunk1  every raw download                    -> interim2/**
    chunk2  canonicalisation                      -> interim2/water/canonical/**
    chunk3  snap and site identity                -> the merge block, nitrate_daily.parquet
    chunk4  the sensor graph and basins           -> basins.parquet, basin_reaches.parquet
    chunk5  covariate aggregation to catchments   -> processed2/comid_features/**
    chunk6  assembly and the read layer           -> sites.parquet, per-gauge water

Each chunk owns its own orchestrator (`chunkN/run_chunkN.py`) and is runnable on its own; this only sequences them and forwards flags. Chunks are ordered by data dependency, not convenience: chunk 1 clips against the AOE chunk 0 computes, and chunk 3's merge rule needs the flowlines chunk 1 downloads.

A chunk holds work that is not internally sequential, which is why basins are chunk 4 rather than the back half of chunk 3 -- they are delineated per physical gauge, so they wait on the merge.
"""

from __future__ import annotations

# `python run.py` from inside src/build2 leaves the repo root off sys.path, so `src.build2.*` does not resolve and the failure surfaces as `ModuleNotFoundError: No module named 'src'` from deep inside importlib. Put the root on the path rather than lecturing about `-m`: the invocation is a reasonable thing to type, and there is no reason for it not to work.
if __package__ in (None, ""):
    import pathlib as _pl
    import sys as _sys

    _root = _pl.Path(__file__).resolve().parents[2]
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))


import argparse
import time

CHUNKS = {
    "chunk0": ("seed filter and AOE definition", "src.build2.chunk0.run_chunk0"),
    "chunk1": ("every raw download", "src.build2.chunk1.run_chunk1"),
    "chunk2": ("canonicalisation -- naming, units, daily statistics", "src.build2.chunk2.run_chunk2"),
    "chunk3": ("snap and site identity", "src.build2.chunk3.run_chunk3"),
    "chunk4": ("basin delineation and selection", "src.build2.chunk4.run_chunk4"),
    "chunk5": ("covariate aggregation to catchments", "src.build2.chunk5.run_chunk5"),
    "chunk6": ("assembly and the read layer", "src.build2.chunk6.run_chunk6"),
}

# Not written yet, but part of the ORDER: the sequence stops here rather than skipping to chunk 6, because
# chunk 6 consumes what these produce and assembling without them yields a cohort that looks finished and is
# not. Moving one into CHUNKS is all it takes to open the gate.
PLANNED: dict[str, str] = {}

# Written, but not part of the default sequence. See the module docstring. These measure rather than build:
# they read what is on disk, print, and never raise, so nothing downstream can depend on having run them --
# which is exactly why they must be discoverable from `--list` instead of only from the source tree.
UNWIRED: dict[str, str] = {
    "units_check": "cross-source agreement on co-located sensors -- verifies declared units by measurement",
    "wqp_admission": "what admitting the IWQIS WQP namespace would change, measured before it is changed",
}

DEFAULT_ORDER = ("chunk0", "chunk1", "chunk2", "chunk3", "chunk4", "chunk5", "chunk6")


def _run(name: str, **kw):
    import importlib

    label, mod = CHUNKS[name]
    print(f"\n{'=' * 72}\n{name}  —  {label}\n{'=' * 72}")
    m = importlib.import_module(mod)
    accepted = m.main.__code__.co_varnames[: m.main.__code__.co_argcount]
    return m.main(**{k: v for k, v in kw.items() if k in accepted})


def resolve(chunks) -> tuple[str, ...]:
    """Named chunks -> the sequence to actually run: the earliest one named, and everything after it.

    NAMING A CHUNK MEANS "FROM HERE", not "only this". Every chunk consumes the one before, so re-running chunk 3 without re-running chunk 6 leaves `sites.parquet` describing a merge decision that is no longer current -- stale in a way nothing reports, because the file is present and well-formed. Cascading makes the resume after a merge-review stop one obvious command, and makes a partial rebuild the thing you have to ask for rather than the thing you get by accident.

    `--single` runs exactly the named chunks, for debugging a stage in isolation.
    """
    if not chunks:
        return DEFAULT_ORDER
    unknown = [c for c in chunks if c not in CHUNKS and c not in PLANNED]
    if unknown:
        raise SystemExit(f"{unknown}: not runnable. Known: {sorted(CHUNKS)}"
                         + (f"; planned: {sorted(PLANNED)}" if PLANNED else "")
                         + (f"; unwired: {sorted(UNWIRED)}" if UNWIRED else ""))
    first = min(DEFAULT_ORDER.index(c) for c in chunks)
    return DEFAULT_ORDER[first:]


def main(chunks=None, single: bool = False, **kw):
    t0 = time.time()
    order = tuple(chunks) if (single and chunks) else resolve(chunks)
    if chunks and not single and len(order) > len(chunks):
        print(f"running {' -> '.join(order)}  (naming a chunk runs it and everything after; "
              f"--single for just the one)")
    for name in order:
        if name in PLANNED:
            raise SystemExit(
                f"\n{name} ({PLANNED[name]}) is not written yet -- the build stops here.\n"
                f"Everything up to {name} is current. Chunk 6 consumes what {name} produces, so it is\n"
                f"deliberately not skipped to: assembling without it yields a cohort that looks finished\n"
                f"and is missing the covariates every gauge needs.\n")
        if name not in CHUNKS:
            raise SystemExit(f"{name}: not runnable. Known: {sorted(CHUNKS)}")
        _run(name, **kw)
    print(f"\ndone in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("chunks", nargs="*", default=None,
                    help="chunk name; runs THAT chunk and every later one. Default: all.")
    ap.add_argument("--single", action="store_true",
                    help="run only the named chunk(s), not the ones after")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", help="chunk1: a single branch; chunk5: a comma-separated product subset")
    ap.add_argument("--serial", action="store_true", help="chunk1 only: disable the thread pool")
    ap.add_argument("--sources", nargs="*", default=None, choices=["USGS", "IWQIS", "MPCA"])
    ap.add_argument("--skip-discover", action="store_true", help="chunk0 only: reuse registrations.parquet")
    ap.add_argument("--review", action="store_true",
                    help="chunk3/chunk4: force the review panels to re-render")
    ap.add_argument("--list", action="store_true", help="list chunks and exit")
    a = ap.parse_args()
    if a.list:
        for n in DEFAULT_ORDER:                      # pipeline order, not dict order
            if n in CHUNKS:
                print(f"  {n}  {CHUNKS[n][0]}")
            elif n in PLANNED:
                print(f"  {n}  {PLANNED[n]}  [NOT WRITTEN]")
        for n, label in UNWIRED.items():
            print(f"  {n}  {label}  [NOT WIRED]")
        raise SystemExit(0)
    main(a.chunks or None, single=a.single, force=a.force, only=a.only, serial=a.serial,
         sources=a.sources, skip_discover=a.skip_discover, review=a.review)
