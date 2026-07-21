"""_runexps.py -- run every experiment in this directory, in order.

Builds one shared site list and passes a slice of it to each experiment's `main(sites=...)`:

    sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500]
            sorted by the site's EARLIEST data date (oldest first -> longest history first)

Three modes select how many of those sites to use:

    --test   sites[:3]               (default)
    --med    sites[:10]
    --full   the full filtered list

--extra=True additionally runs each experiment's slow permutation-importance test
(extra_importance_test); default False.

By default every experiment runs in numeric-then-suffix order (6, 6c, 7, 7c, 8, 8c, 9, 9c,
...). Pass experiment identifiers as positional args to run only those, IN THE ORDER GIVEN.
Each is preceded by a banner like  ======= exp 6 =======.

Usage:
    python _runexps.py                       # all, test mode (default), no perm importance
    python _runexps.py --med --extra=True
    python _runexps.py --full --extra=True
    python _runexps.py --med --extra=True 6 7c 9c 8 10   # only these, in this order
"""

import argparse
import importlib
import os
import re
import sys
from pathlib import Path

# Make `cook` (isaac/) and `data` (repo root) importable, and run from this dir so the
# experiments' relative paths ("../", "test_results/") resolve the way they expect.
_HERE = Path(__file__).resolve().parent  # isaac/experiments
_ISAAC = _HERE.parent  # isaac
_ROOT = _ISAAC.parent  # repo root
for _p in (str(_ROOT), str(_ISAAC), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(_HERE)

from data.features import daily_nitrate
from data import get_site_ids

MODES = {"test": 2, "small": 8, "med": 20, "full": None}  # how many sites each mode uses


def build_sites():
    """Filtered sites (>=1500 daily nitrate obs), sorted by earliest data date (oldest first).

    The earliest date is taken from each site's actual nitrate record
    (daily_nitrate(s).dropna().index.min()) -- the location metadata's deployed_at field is
    too sparse (NaN for many sites) to sort by.
    """
    dated = []
    for s in get_site_ids():
        d = daily_nitrate(s).dropna()
        if len(d) >= 1500:
            dated.append((d.index.min(), s))
    dated.sort()  # by earliest date, then uid
    return [s for _, s in dated]


def _experiment_files():
    """Every _experiment*.py here, ordered numerically then by suffix (6, 6c, 7, 7c, ...)."""

    def key(p):
        m = re.match(r"_experiment(\d+)([a-z]*)$", p.stem)
        return (int(m.group(1)), m.group(2)) if m else (10**9, p.stem)

    return sorted(_HERE.glob("_experiment*.py"), key=key)


def _as_bool(s):
    """Parse --extra=True/False (also 1/0, yes/no)."""
    return str(s).strip().lower() in ("1", "true", "yes", "y", "t")


def _select(ids):
    """Map experiment identifiers (e.g. '6', '7c', '10') to their files.

    With no ids, returns every experiment in natural order. With ids, returns exactly those in the ORDER GIVEN (so '6 7c 9c 8 10' runs in that sequence). Unknown ids raise.
    """
    by_label = {p.stem.replace("_experiment", ""): p for p in _experiment_files()}
    if not ids:
        return list(by_label.values())
    missing = [i for i in ids if i not in by_label]
    if missing:
        raise SystemExit(f"unknown experiment id(s): {', '.join(missing)}. available: {', '.join(by_label)}")
    return [by_label[i] for i in ids]


def main():
    ap = argparse.ArgumentParser(description="Run experiments in order.")
    g = ap.add_mutually_exclusive_group()
    for m in MODES:
        g.add_argument(f"--{m}", dest="mode", action="store_const", const=m)
    ap.add_argument(
        "--extra",
        type=_as_bool,
        default=False,
        help="run the slow permutation-importance test in each experiment (extra_importance_test). e.g. --extra=True",
    )
    ap.add_argument(
        "ids",
        nargs="*",
        help="experiment identifiers to run, in the order given (e.g. 6 7c 9c 8 10). Default: all, in natural order.",
    )
    ap.set_defaults(mode="test")
    args = ap.parse_args()

    paths = _select(args.ids)

    n = MODES[args.mode]
    sites = build_sites()
    sites = sites if n is None else sites[:n]
    print(f"mode={args.mode}: {len(sites)} sites (earliest-history first)  extra={args.extra}\n")

    for path in paths:
        label = path.stem.replace("_experiment", "")
        print(f"\n======= exp {label} =======", flush=True)
        mod = importlib.import_module(path.stem)
        mod.main(sites=sites, extra=args.extra)


if __name__ == "__main__":
    main()
