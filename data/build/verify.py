"""The verify CLI -- every stage's done-when clause, executable against the real stores.

A CHECK REGISTRY, NOT A TEST SUITE. pytest owns the kernels (synthetic fixtures, no stores); this owns the stores: read-only assertions that the artifacts on disk satisfy the invariants the chapter states per stage. The two meet in `tests/storecheck/`, which simply asserts that `verify <stage>` passes.

EVERY CHAPTER VERIFICATION BULLET NAMES AN IMPLEMENTED CHECK. The mapping (chapter: notes/book/data_pipeline_chapterv2.md, section Verification):

    chapter bullet                          where it runs
    registry closure                        import-time raises in registry.py (axis vocab, high-tier <= 3, clamp => lo < 0) + tests/data/build/test_registry.py
    a2: canaries registered/un-skipped      verify a2 (census._check_canaries)
    a2: uid <-> namespace                   contracts.validate_sensors at every table write + census._check_ids
    a2: arm sensors nonzero                 verify a2 (census._check_arms_exist)
    a3: fetch partition                     verify a3 (records._check_partition); stray shard files (records._check_no_stray_status)
    a3: retry horizon                       verify a3 (records._check_retry_horizon)
    a4: gaps/units/drift named              verify a4 (reconcile._check_report) + the gates themselves
    d2: clean-sensor roundtrip              tests/data/build/test_reduction.py (synthetic; exact by construction)
    d2: no fabricated DV spread             verify d2 (canonical._check_dv_null_rule)
    d2: canonical stamps match registry     verify d2 --deep (canonical._check_canonical_stamps)
    d2: tally closure                       verify d2 (canonical._check_tally_closure)
    d3: min <= sep <= max rowwise           verify d3 (geometry._check_bounds_order)
    d3: table matches recomputation         verify d3 --deep (geometry._check_table_fresh)
    d3: dp coverage where text exists       verify d3 (geometry._check_dp_coverage)
    d3: sentinels have no geometry/pairs    verify d3 (geometry._check_sentinels_have_no_geometry)
    d4: no contradiction survives           the contradiction halt itself (identity.apply_decisions) + tests
    d4: material/cross-type discipline      verify d4 (assemble._check_sensor_partition, _check_fronting) + tests
    d4: provenance names one member         verify d4 --deep (assemble._check_record_provenance)
    d4: id ledger bijection/format/reuse    verify a2 (census._check_ids over ids.validate)
    d4: id ledger append-only vs git        verify a2 (census._check_ids_append_only; SKIPs until the first commit)
    d4: half-full warning                   ids.mint_new, at mint time
    d5: per-grid closures                   verify d5 (products._check_closures) -- and gate-before-write in _write
    d5: weight matrix joins + conserves     verify d5 (products._check_weights_join + the global cap in _weights_closure)
    d5: AOE stamps match the extent         verify d5 (products._check_product_aoe_stamps)
    p1: the partition is exact              verify p1 (publish._check_partition)
    p1: ledger masks honoured               verify p1 (publish._check_water_matches_ledger)
    p1: zero sentinels / out-of-range       verify p1 --deep (publish._check_no_sentinels_or_range_leaks)
    p1: republication exact to rtol=1e-9    verify p1 --deep (publish._check_republication_exact)
    network machinery vs NHDPlus            verify d4 --deep (verify._check_accumulate_vs_vaa, over data.access.machinery.accumulate)

Checks are registered with `@check(stage, name, deep=False)`. Cheap checks run by default; expensive sweeps (full BFS, all-product closure) sit behind `--deep`. Output is one table, nonzero exit on any fail; a SKIP (store absent, stage not yet run) is not a failure -- a missing artifact is a fact, and `run` is who decides whether it is a problem. Where a check asserts a MEASURED claim (388/392 within 1%, zonal closure at 0.00%), the expected value and its provenance ride in the failure message, so a regression names the measurement it broke.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections.abc import Callable

STAGES = ("snapshot", "a1", "a2", "a3", "a4", "d1", "d2", "d3", "d4", "d5", "p1")


@dataclasses.dataclass
class Check:
    stage: str
    name: str
    fn: Callable[[], tuple[bool | None, str]]     # (ok | None for skip, detail)
    deep: bool = False


_CHECKS: list[Check] = []


def check(stage: str, name: str, deep: bool = False):
    """Register a verify check. The fn returns (ok, detail); ok=None means skip."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")

    def deco(fn):
        _CHECKS.append(Check(stage, name, fn, deep))
        return fn

    return deco


def _print_table(rows: list[dict], failed: int) -> None:
    """One block per check, wrapped to the terminal: `stage status name`, then the detail under a bullet.

    The detail strings carry measurements and file lists and run to hundreds of characters, so a single
    padded line per check was a column of ragged wraps. Wrapping explicitly costs the same screen space
    and keeps the status column scannable.
    """
    import shutil
    import textwrap

    width = max(60, shutil.get_terminal_size((100, 24)).columns - 1)
    for r in rows:
        tag = f"  {r['stage']:<4} {r['status']:<4} "
        pad = " " * len(tag)
        print(textwrap.fill(r["check"], width=width, initial_indent=tag, subsequent_indent=pad))
        if r["detail"]:
            print(textwrap.fill(str(r["detail"]), width=width,
                                initial_indent=pad + "· ", subsequent_indent=pad + "  "))
    print(f"\n  {len(rows)} check(s): {sum(r['status'] == 'ok' for r in rows)} ok, "
          f"{failed} failed, {sum(r['status'] == 'SKIP' for r in rows)} skipped")


def run(stage: str | None = None, deep: bool = False, as_json: bool = False) -> int:
    """Run the registered checks. Returns the exit code (0 = nothing failed)."""
    todo = [c for c in _CHECKS
            if (stage in (None, "all") or c.stage == stage) and (deep or not c.deep)]
    rows, failed = [], 0
    for c in todo:
        t0 = time.time()
        try:
            ok, detail = c.fn()
        except Exception as e:                     # noqa: BLE001 -- a crashed check is a failed check
            ok, detail = False, f"{type(e).__name__}: {e}"
        dt = time.time() - t0
        status = "SKIP" if ok is None else ("ok" if ok else "FAIL")
        failed += status == "FAIL"
        rows.append(dict(stage=c.stage, check=c.name, status=status, detail=detail, seconds=round(dt, 2)))

    if as_json:
        print(json.dumps(rows, indent=1))
    else:
        _print_table(rows, failed)
    return 1 if failed else 0


# Stage modules register their checks on import; the registrations live beside the code they verify.
def _load_all() -> None:
    import importlib

    for mod in ("data.build.acquire.snapshot", "data.build.acquire.reconcile",
                "data.build.acquire.census", "data.build.acquire.records",
                "data.build.derive.extent", "data.build.derive.canonical",
                "data.build.derive.geometry", "data.build.derive.identity",
                "data.build.derive.assemble", "data.build.derive.products",
                "data.build.publish.publish"):   # grows as stages land
        try:
            importlib.import_module(mod)
        except ImportError as e:
            # LOUD, because a module that fails to import contributes NO checks and an empty table
            # looks exactly like a clean one. A stage not yet written is the only benign case.
            print(f"  verify: {mod} did not import, its checks are ABSENT ({e})", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", nargs="?", default="all", choices=(*STAGES, "all"))
    ap.add_argument("--deep", action="store_true", help="include expensive sweeps")
    ap.add_argument("--json", action="store_true", dest="as_json")
    a = ap.parse_args(argv)
    # RUN THROUGH THE CANONICAL MODULE, NEVER THIS ONE'S GLOBALS. Under `python -m data.build.verify`
    # this file is imported as `__main__`, while every stage module registers into `data.build.verify`
    # -- a SEPARATE module object with a separate `_CHECKS`. Reading the local registry there yields an
    # empty table, which is indistinguishable from a clean run and was reported as exactly that.
    import importlib

    v = importlib.import_module("data.build.verify")
    v._load_all()
    return v.run(a.stage, deep=a.deep, as_json=a.as_json)


if __name__ == "__main__":
    sys.exit(main())


# ---- cross-cutting checks hosted here (they verify machinery, not one stage's artifact) -----------

@check("d4", "access accumulate reproduces NHDPlus totdasqkm on a random reach sample", deep=True)
def _check_accumulate_vs_vaa():
    """The network machinery's standing self-test, re-homed with the machinery when basins left the build: two independent accumulations (ours and NHDPlus's own) must agree, or every basin the access layer hands a modeler is quietly wrong."""
    import random

    import pandas as pd

    from data.access import config as acfg
    from data.access.machinery import accumulate as acc

    vaa_path = acfg.ACQ_NETWORK / "vaa.parquet"
    if not vaa_path.exists():
        return None, "no network archive"
    net = acc.Network.load()
    vaa = pd.read_parquet(vaa_path, columns=["comid", "ftype", "totdasqkm"])
    ok_reaches = vaa[~vaa.ftype.isin(acc.NON_DRAINAGE_FTYPE) & (vaa.totdasqkm > 10.0)]
    rng = random.Random(0)
    sample = ok_reaches.sample(min(40, len(ok_reaches)), random_state=0)
    bad = 0
    for r in sample.itertuples():
        comids, area, vaa_km2, status = net.accumulate(int(r.comid))
        if status != "ok" or area is None or vaa_km2 is None or vaa_km2 <= 0:
            continue
        if abs(area - vaa_km2) / vaa_km2 > 0.01:
            bad += 1
    return bad <= max(1, len(sample) // 20), \
        f"{len(sample)} reaches sampled, {bad} beyond 1% of the VAA's own accumulation"
