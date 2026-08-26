"""The runner. Layers are the architecture; stages are the targets.

    python -m data.build.run acquire            # Layer A alone -- the daily-cadence entry point
    python -m data.build.run build              # full A -> D -> P against the pinned snapshot
    python -m data.build.run d3 [--single]      # from a stage onward, or exactly that stage
    python -m data.build.run verify [stage] [--deep]

A gate halting the build is a SystemExit naming outstanding ledger rows -- the build did what it could and is waiting on a person; rerunning after the sheet is filled continues from the same inputs. `--snapshot ID` pins the raw state (default: the HEAD pointer); every derived artifact stamps it.
"""

from __future__ import annotations

import argparse
import importlib
import sys

# stage -> (label, module, layer). Filled in as milestones land; a named-but-unlanded stage stops loudly.
STAGES: dict[str, tuple[str, str, str]] = {
    "a1": ("seed discovery + seed basins", "data.build.acquire.seed", "A"),
    "a2": ("census (AOE-scoped) + id mint", "data.build.acquire.census", "A"),
    "a3": ("bulk fetch + snapshot cut", "data.build.acquire.bulk", "A"),
    "a4": ("verification + drift", "data.build.acquire.reconcile", "A"),
    "d1": ("extent", "data.build.derive.extent", "D"),
    "d2": ("canonicalisation", "data.build.derive.canonical", "D"),
    "d3": ("geometry: snap + precision + proximity", "data.build.derive.geometry", "D"),
    "d4": ("identity: gate + evidence + assembly", "data.build.derive.assemble", "D"),
    "d5": ("covariates + structures", "data.build.derive.products", "D"),
    "p1": ("publication", "data.build.publish.publish", "P"),
}
ORDER = tuple(STAGES)


def _run_stage(name: str, **kw) -> object:
    label, module, _ = STAGES[name]
    print(f"\n{'=' * 78}\n{name}  —  {label}\n{'=' * 78}")
    try:
        m = importlib.import_module(module)
    except ImportError as e:
        raise SystemExit(f"{name} ({label}) is not implemented yet -- the build stops here.\n  ({e})")
    accepted = m.main.__code__.co_varnames[: m.main.__code__.co_argcount]
    return m.main(**{k: v for k, v in kw.items() if k in accepted})


def resolve(target: str, single: bool) -> tuple[str, ...]:
    if target == "build":
        return ORDER
    if target == "acquire":
        return tuple(s for s in ORDER if STAGES[s][2] == "A")
    if target not in STAGES:
        raise SystemExit(f"unknown target {target!r}; stages: {', '.join(ORDER)}, or build/acquire/verify")
    return (target,) if single else ORDER[ORDER.index(target):]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", default="build")
    ap.add_argument("--single", action="store_true", help="run only the named stage, not the ones after")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--snapshot", default=None, help="pin a snapshot id (default: HEAD)")
    ap.add_argument("--list", action="store_true")
    # parse_known_args, NOT a REMAINDER positional: REMAINDER swallows every flag after the target, so
    # `run d3 --single` silently ran d4 onward once. Unknowns belong to `verify` alone; anywhere else they are a mistake.
    a, extra = ap.parse_known_args(argv)

    if a.target == "verify":
        from . import verify

        return verify.main(extra)
    if extra:
        raise SystemExit(f"unrecognised argument(s) {extra} for target {a.target!r}")
    if a.list:
        for s in ORDER:
            label, module, layer = STAGES[s]
            try:
                importlib.import_module(module)
                state = ""
            except ImportError:
                state = "  [NOT IMPLEMENTED]"
            print(f"  {s}  [{layer}]  {label}{state}")
        return 0

    for s in resolve(a.target, a.single):
        _run_stage(s, force=a.force, snapshot=a.snapshot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
