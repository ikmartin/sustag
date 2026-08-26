"""Chunk 0 — discover the seed sensors and compute the AOE.

    python -m src.build2.run chunk0
    python -m src.build2.run chunk0 --force        # refetch NLDI basins, recompute the geometry

Four steps: discover every registration in the seed boxes, mark which of them may define the extent, fetch one upstream basin per seed from NLDI, and union those with the boxes into `aoe.parquet`.

NOTHING DOWNSTREAM CAN RUN WITHOUT THIS. Every later chunk clips against `aoe_geometry()`, which falls back to the seed boxes with a warning when this has not run -- and the boxes are 3.00M km2 against the AOE's 4.09M, so the fallback truncates the upstream half of the largest basins.

`--force` recomputes the geometry, which changes `aoe_stamp()`, which invalidates every chunk-1 artifact cut against the old one.
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
    _rp.run_module("src.build2.chunk0.run_chunk0", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

import argparse

import pandas as pd

from .. import config, io as bio, schema
from . import aoe, basins, discover, seed


def _current_stamp() -> str | None:
    """The stamp of the AOE on disk BEFORE this run rebuilds it, or None if there is none."""
    if not config.AOE_PATH.exists():
        return None
    try:
        import geopandas as gpd

        return str(gpd.read_parquet(config.AOE_PATH).stamp.iloc[0])
    except Exception:
        return None


def _carry_forward(sites: pd.DataFrame) -> pd.DataFrame:
    """Re-attach columns owned by later chunks, matched on `site_uid`.

    THE COLUMN LIST COMES FROM THE CONTRACT, never from a list kept here. This used to be two hand-maintained name lists, and three columns were missing from them -- each silently nulled on every run, with no error to attribute it to. `schema.columns_in(carried=True)` cannot fall out of step, because declaring a column's block is the only way to add one.

    Carries everything; `_invalidate_stale` drops what the new AOE invalidates once the stamp is known. A registration that has vanished from the source keeps nothing; a new one gets nulls. This restores what a later chunk established, never invents it.
    """
    if not config.REGISTRATIONS_PATH.exists():
        return sites
    prev = pd.read_parquet(config.REGISTRATIONS_PATH)
    cols = [c for c in schema.columns_in(carried=True)
            if c in prev.columns and prev[c].notna().any()]
    if not cols:
        return sites
    out = sites.drop(columns=[c for c in cols if c in sites.columns], errors="ignore")
    out = out.merge(prev[["site_uid"] + cols], on="site_uid", how="left")
    print(f"  carried forward {len(cols)} column(s) from the previous table")
    return out


def _invalidate_stale(sites: pd.DataFrame, prev_stamp: str | None, new_stamp: str) -> pd.DataFrame:
    """Null the extent-derived columns when the AOE changed. Runs after the new stamp exists.

    Extent-dependence is a property of the column's block: the snap comes off a flowline layer cut to the AOE, so carrying it across a new stamp would preserve an answer derived from a different extent. The records block is not -- it comes from per-site files keyed on `site_uid`, which no AOE change touches.
    """
    if prev_stamp is None or prev_stamp == new_stamp:
        return sites
    cols = [c for c in schema.columns_in(aoe_dependent=True)
            if c in sites.columns and sites[c].notna().any()]
    if not cols:
        return sites
    out = sites.copy()
    for c in cols:
        out[c] = schema.null_column(c, out.index)
    print(f"  AOE changed ({prev_stamp} -> {new_stamp}): invalidated {len(cols)} "
          f"extent-dependent column(s); chunk 1 will re-derive them")
    return out


def main(sources: list[str] | None = None, force: bool = False, skip_discover: bool = False) -> pd.DataFrame:
    config.ensure_dirs()
    print(config.describe())
    prev_stamp = _current_stamp()

    print("\n[0.1] discover  (seed boxes)")
    if skip_discover and config.REGISTRATIONS_PATH.exists():
        sites = pd.read_parquet(config.REGISTRATIONS_PATH)
        print(f"  reusing {config.REGISTRATIONS_PATH.name}: {len(sites):,} registrations")
    else:
        sites = discover.discover(sources)
        print(f"  {len(sites):,} registrations  {sites.source.value_counts().to_dict()}")
        sites = _carry_forward(sites)

    print("\n[0.2] seed filter")
    sites = seed.mark_seeds(sites)
    print(seed.summarise(sites))

    print("\n[0.3] seed basins  (NLDI)")
    g = basins.fetch_seed_basins(sites, force=force)
    if len(g):
        idx = g.set_index("site_uid")
        sites["nldi_basin_km2"] = sites.site_uid.map(idx.nldi_basin_km2)
        for c in ("nldi_basin_flag", "nldi_basin_method"):
            if c in idx.columns:
                sites[c] = sites.site_uid.map(idx[c]).astype("string")
        area = idx.nldi_basin_km2
        print(f"  {len(g)} basins, median {area.median():,.0f} km2, largest {area.max():,.0f} km2")

    print("\n[0.4] AOE")
    summary = aoe.write(aoe.build(g), n_seeds=int(len(g)))
    print(aoe.summarise(summary))
    sites = _invalidate_stale(sites, prev_stamp, summary["stamp"])

    out = schema.conform(sites)
    bad = schema.validate(out)
    if bad:
        raise ValueError("site table violates the contract:\n  " + "\n  ".join(bad))
    bio.write_parquet(out, config.REGISTRATIONS_PATH)
    print(f"\nwrote {config.REGISTRATIONS_PATH.name}: {len(out):,} registrations, "
          f"{int(out.aoe_seed.sum())} seeds")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="refetch NLDI basins and recompute the AOE")
    ap.add_argument("--sources", nargs="*", default=None, choices=["USGS", "IWQIS", "MPCA"])
    ap.add_argument("--skip-discover", action="store_true", help="reuse the existing registrations.parquet")
    a = ap.parse_args()
    main(sources=a.sources, force=a.force, skip_discover=a.skip_discover)
