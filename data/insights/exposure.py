"""Tier 0 -- what is on disk against what `data.access` exposes.

THIS EXISTS BECAUSE THE ANSWER WAS A SURPRISE. Roughly 1,150 per-COMID attributes across StreamCat and the Wieczorek release are acquired, snapshotted, verified by the build's own checks -- and readable by no model. 6.1 GB of native sub-daily water observations sat behind no accessor until 2026-08-26. None of that is visible from either side alone: the build sees files it wrote, the access layer sees functions it offers, and nobody was comparing the two.

Three populations, and the distinction matters because they cost different things to fix:

  acquired but UNEXPOSED       on disk, no reader at all -- the largest category, and the cheapest to fix
  acquired but UNDER-REQUESTED reachable, but a request-shaping decision hides most of it
  exposed but UNREAD           reachable and used by nothing -- not a defect, but worth knowing
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pandas as pd

from data.access import api
from data.build import config as bcfg

# Store roots this tier walks, and the layer each belongs to. `snapshot.manifest_rows` walks raw and
# acquired already; derived and published have never had a walker, which is itself a small finding.
ROOTS = {
    "raw": bcfg.RAW,
    "acquired": bcfg.ACQUIRED,
    "derived": bcfg.DERIVED,
    "published": bcfg.PUBLISHED,
}

# What each access function reads, declared rather than inferred. A path reachable through NO entry here is
# unexposed by definition; keeping this beside the walk means adding a reader without updating this shows up
# as a finding rather than silently passing.
EXPOSED_BY = {
    "get_sensors": ("published/sensors.parquet",),
    "get_record": ("published/water",),
    "get_quality": ("published/quality.parquet",),
    "get_proximity": ("published/proximity.parquet",),
    "get_network": ("acquired/network",),
    "get_covariates": ("published/comid_features",),
    "get_weather": ("acquired/weather/daily", "published/comid_features/weights_gridmet.parquet"),
    "get_native": ("acquired/water/native",),
    "snap": ("acquired/network",),
    "accumulate": ("acquired/network",),
    "get_attributes": ("acquired/attributes",),
    "attribute_columns": ("acquired/attributes",),
    "get_drought": ("acquired/weather/pentad",),
    "drought_columns": ("acquired/weather/pentad",),
    "get_discrete_nitrate": ("acquired/wqx",),
    "discrete_nitrate_states": ("acquired/wqx",),
}

SKIP_NAMES = ("fetch_status", ".DS_Store", "__pycache__")


def _walk(root: Path, label: str) -> pd.DataFrame:
    """One row per file under a store root: relpath, size, and its top-level substore."""
    rows = []
    if not root.exists():
        return pd.DataFrame(columns=["layer", "substore", "relpath", "size", "n_files"])
    for p in root.rglob("*"):
        if not p.is_file() or p.name.startswith(".") or any(s in p.parts for s in SKIP_NAMES):
            continue
        if any(p.name.startswith(s) for s in SKIP_NAMES):
            continue
        rel = p.relative_to(root)
        rows.append(dict(layer=label, substore=rel.parts[0] if len(rel.parts) > 1 else rel.stem,
                         relpath=f"{label}/{rel}", size=p.stat().st_size, n_files=1))
    return pd.DataFrame(rows)


def inventory() -> pd.DataFrame:
    """Every file in every store, one row each."""
    return pd.concat([_walk(r, name) for name, r in ROOTS.items()], ignore_index=True)


def _exposed_prefixes() -> set[str]:
    return {pre for paths in EXPOSED_BY.values() for pre in paths}


def substores() -> pd.DataFrame:
    """One row per (layer, substore) with its size, file count, and whether anything can read it.

    Matching is by PATH PREFIX against `EXPOSED_BY`, so a substore counts as exposed when some access function names it or a parent of it. Coarse on purpose: the question at this tier is "can a model reach this at all", not "can it reach every column".
    """
    inv = inventory()
    if not len(inv):
        return inv
    g = (inv.groupby(["layer", "substore"], as_index=False)
         .agg(n_files=("n_files", "sum"), bytes=("size", "sum")))
    pre = _exposed_prefixes()
    g["reachable"] = [any(f"{l}/{s}".startswith(p) or p.startswith(f"{l}/{s}") for p in pre)
                      for l, s in zip(g.layer, g.substore)]
    g["readers"] = [", ".join(sorted(fn for fn, paths in EXPOSED_BY.items()
                                     if any(f"{l}/{s}".startswith(p) or p.startswith(f"{l}/{s}")
                                            for p in paths))) or ""
                    for l, s in zip(g.layer, g.substore)]
    consumed = _consumed_substores()
    g["consumed_by_build"] = [s in consumed for s in g.substore]
    # THREE STATES, NOT TWO. "No access reader" is not the finding on its own: `acquired/facilities` has
    # none and is read by `derive/structures`, whose product IS published -- flagging it sends a reader to
    # fix something that works. The finding is data reachable by NOBODY, and separating the two is what
    # makes the rest of the column worth trusting.
    g["status"] = np.where(g.reachable, "reachable",
                  np.where(g.consumed_by_build, "intermediate (consumed by the build)", "ORPHANED"))
    return g.sort_values("bytes", ascending=False).reset_index(drop=True)


def _consumed_substores() -> set:
    """Substore names referenced anywhere in the live `data/` source. A source scan, no imports.

    Deliberately name-based and generous: a substore mentioned in `data/build` or `data/access` is being used by something, and the cost of a false "consumed" is a missed finding while the cost of a false "orphaned" is a reader sent to fix working code. `src/` is excluded on purpose -- it is the retired predecessor, and `authority_basins` is referenced ONLY there, which is exactly the case this is built to surface.
    """
    root = Path(__file__).resolve().parents[1]
    text = []
    for sub in ("build", "access"):
        d = root / sub
        if not d.exists():
            continue
        for q in d.rglob("*.py"):
            if "__pycache__" in q.parts:
                continue
            text.append(q.read_text())
    blob = "\n".join(text)
    out = set()
    for layer, base in ROOTS.items():
        if not base.exists():
            continue
        for child in base.iterdir():
            name = child.name.replace(".parquet", "")
            if name in blob or name.upper() in blob:
                out.add(child.name)
                out.add(name)
    return out


def access_surface() -> pd.DataFrame:
    """The access layer's public functions and their signatures -- the other half of the reconciliation."""
    rows = []
    for name in sorted(n for n in dir(api) if not n.startswith("_")):
        fn = getattr(api, name)
        if not callable(fn) or not getattr(fn, "__module__", "").startswith("data.access"):
            continue
        rows.append(dict(function=name, signature=str(inspect.signature(fn)),
                         declared_paths=", ".join(EXPOSED_BY.get(name, ())) or "(undeclared)"))
    return pd.DataFrame(rows)


def findings() -> list[str]:
    """Prose lines naming what is unreachable, largest first. The work list this tier exists to produce."""
    g = substores()
    out = []
    dark = g[~g.reachable & (g.layer != "raw")].sort_values("bytes", ascending=False)
    for r in dark.itertuples():
        out.append(f"**{r.layer}/{r.substore}** — {r.bytes / 1e9:.2f} GB across {r.n_files:,} file(s), "
                   f"no access-layer reader")
    undeclared = [r.function for r in access_surface().itertuples()
                  if r.declared_paths == "(undeclared)"]
    if undeclared:
        out.append(f"access functions with no declared store path (so this tier cannot audit them): "
                   f"{', '.join(undeclared)}")
    return out
