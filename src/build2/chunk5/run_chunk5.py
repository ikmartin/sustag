"""Chunk 5 — every raster becomes a COMID-keyed table.

    python -m src.build2.run chunk5
    python -m src.build2.run chunk5 --single --only crops     # one product
    python -m src.build2.run chunk5 --force                   # ignore every checkpoint

Five products over 1,485,343 catchments: crops, nutrients, agtile, gridMET weights, dams. Each is FRACTIONAL -- a pixel on a boundary contributes its area share to every catchment it touches -- and each is LOCAL, the value over that catchment's own polygon. Accumulating upstream is chunk 6's job, over `basin_reaches.parquet`.

NO HUMAN GATE. Chunks 2 and 3 stop for a reviewer because they decide identity and delineation, where a wrong answer is invisible downstream. Chunk 5 measures: every number here is checkable against the catchment's own area, and the checks run on every build rather than in front of a person.

ORDER IS SET BY THE DISK. CDL is read from a 50 GB expansion that is deleted afterwards, and AgTile's 15 GB expansion cannot be made until that space is back. So crops runs first and agtile last, with the products that need no archive in between -- and `archive.expanded` refuses rather than half-unzips when the space is not there. `--keep-archives` leaves both expansions in place, which is for debugging and costs 65 GB.

CHECKPOINTS ARE PER BATCH, under `interim2/comid_features/parts/`. The crops pass is about an hour and reads an archive that will not be on disk afterwards; a crash at minute fifty must not mean expanding 50 GB again. THE ARCHIVE IS OPENED LAZILY for the same reason: a step whose batches are all checkpointed reads them and never asks for the rasters, so a finished crops pass costs no unzip at all.
"""

from __future__ import annotations

if __name__ == "__main__" and __package__ in (None, ""):
    import pathlib as _pl
    import runpy as _rp
    import sys as _sys

    _root = _pl.Path(__file__).resolve().parents[3]
    _sys.path.insert(0, str(_root))
    _rp.run_module("src.build2.chunk5.run_chunk5", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

import argparse

import pandas as pd

from .. import config, io as bio
from . import agtile, archive, crops, dams, nutrients, weights, zonal

OUT = config.COMID_FEATURES
PARTS = config.INTERIM / "comid_features" / "parts"

PRODUCTS = ("crops", "nutrients", "weights", "dams", "agtile")


def _write(df: pd.DataFrame, name: str) -> None:
    """One product table, stamped with the AOE it was cut against so a join can refuse on mismatch."""
    df = df.copy()
    df["aoe_stamp"] = config.aoe_stamp()
    p = OUT / f"{name}.parquet"
    bio.write_parquet(df, p, index=False)
    print(f"  wrote {p.name}: {len(df):,} rows x {len(df.columns)} cols "
          f"({p.stat().st_size/1e6:.0f} MB)", flush=True)


def coverage(cat: zonal.Catchments, crops_df: pd.DataFrame | None) -> pd.DataFrame:
    """`comid, area_km2, n_px_30m, frac_covered` -- the denominator table, and the honesty check.

    A catchment smaller than a pixel can rasterise to nothing. Zero must reach chunk 6 as MISSING and not as a legitimate zero, which is what `frac_covered` is for: it is the fraction of the catchment's own area the raster actually saw.
    """
    out = zonal.coverage_table(cat)
    if crops_df is not None and len(crops_df):
        y = crops_df.year.max()
        px = crops_df[crops_df.year == y].set_index("comid").n_px
        out["n_px_30m"] = out.comid.map(px).astype("float32")
        out["frac_covered"] = (out.n_px_30m * 900.0 / 1e6 / out.area_km2).astype("float32")
    return out


def main(force: bool = False, only: str | None = None, limit: int | None = None,
         keep_archives: bool = False) -> None:
    want = PRODUCTS if only in (None, "", "all") else tuple(only.split(","))
    bad = [w for w in want if w not in PRODUCTS]
    if bad:
        raise SystemExit(f"{bad}: unknown product. Known: {list(PRODUCTS)}")

    for p in ("crops", "agtile", "surplus", "fertilizer"):
        ok, why = archive.present(p)
        print(f"  {p:11s} {'OK ' if ok else 'MISSING'} {why}")
        if not ok and ((p in ("crops",) and "crops" in want)
                       or (p == "agtile" and "agtile" in want)
                       or (p in ("surplus", "fertilizer") and "nutrients" in want)):
            raise SystemExit(f"{p} is required for the products requested and is not on disk")

    OUT.mkdir(parents=True, exist_ok=True)
    cat = zonal.Catchments()
    print(f"  {len(cat):,} catchments, {cat.area_km2.sum():,.0f} km2 total", flush=True)

    crops_df = None
    if "crops" in want:
        print("\n[5.1] crops  (CDL, fractional, all years in one coverage pass)")
        with archive.lazy("crops", keep=keep_archives) as d:
            crops_df = crops.build(cat, d, limit=limit, part_dir=PARTS, force=force)
        _write(crops_df, "crops")
        _write(coverage(cat, crops_df), "coverage")

    if "nutrients" in want:
        print("\n[5.2] nutrients  (surplus + fertilizer, 250 m, one pass)")
        _write(nutrients.build(cat, limit=limit, part_dir=PARTS, force=force), "nutrients")

    if "weights" in want:
        print("\n[5.3] gridMET weights  (catchment x cell overlap)")
        _write(weights.build(cat, limit=limit, part_dir=PARTS, force=force), "weights_gridmet")

    if "dams" in want:
        print("\n[5.4] dams  (NID points -> containing catchment)")
        _write(dams.build(), "dams")

    if "agtile" in want:
        print("\n[5.5] agtile  (tile drainage, ESRI:102003)")
        with archive.lazy("agtile", keep=keep_archives) as d:
            _write(agtile.build(cat, d, limit=limit, part_dir=PARTS, force=force), "agtile")

    print(f"\n  {config.COMID_FEATURES} now holds: "
          f"{sorted(p.name for p in OUT.glob('*.parquet'))}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="ignore checkpoints and rebuild every batch")
    ap.add_argument("--only", help=f"comma-separated subset of {list(PRODUCTS)}")
    ap.add_argument("--limit", type=int, help="stop after N catchments (smoke test, never a build)")
    ap.add_argument("--keep-archives", action="store_true",
                    help="do not delete the crops/agtile expansions afterwards (debugging; costs 65 GB)")
    a = ap.parse_args()
    main(force=a.force, only=a.only, limit=a.limit, keep_archives=a.keep_archives)
