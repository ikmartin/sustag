"""The verify CLI -- every stage's done-when clause, executable against the real stores.

A CHECK REGISTRY, NOT A TEST SUITE. pytest owns the kernels (synthetic fixtures, no stores); this owns the stores: read-only assertions that the artifacts on disk satisfy the invariants the chapter states per stage. The two meet in `tests/storecheck/`, which simply asserts that `verify <stage>` passes.

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
        w = max((len(r["check"]) for r in rows), default=10)
        for r in rows:
            print(f"  {r['stage']:8s} {r['check']:{w}s}  {r['status']:4s}  {r['detail']}")
        print(f"\n  {len(rows)} check(s): {sum(r['status'] == 'ok' for r in rows)} ok, "
              f"{failed} failed, {sum(r['status'] == 'SKIP' for r in rows)} skipped")
    return 1 if failed else 0


# Stage modules register their checks on import; the registrations live beside the code they verify.
def _load_all() -> None:
    import importlib

    for mod in ("src.build3.acquire.snapshot", "src.build3.acquire.reconcile", "src.build3.acquire.census", "src.build3.derive.extent", "src.build3.derive.canonical",
                "src.build3.derive.sites",
                "src.build3.derive.basins_graphs", "src.build3.derive.products",
                "src.build3.publish.publish"):   # grows as stages land
        try:
            importlib.import_module(mod)
        except ImportError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", nargs="?", default="all", choices=(*STAGES, "all"))
    ap.add_argument("--deep", action="store_true", help="include expensive sweeps")
    ap.add_argument("--json", action="store_true", dest="as_json")
    a = ap.parse_args(argv)
    _load_all()
    return run(a.stage, deep=a.deep, as_json=a.as_json)


if __name__ == "__main__":
    sys.exit(main())
