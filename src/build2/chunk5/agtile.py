"""AgTile-US: subsurface tile drainage, per catchment.

A BARE BINARY RASTER WITH NO MASK. 1 means tile-drained, 0 means everything else -- undrained cropland and downtown alike. It carries no nodata and no cropland layer, which an earlier design assumed and was wrong about. So the raster supplies a NUMERATOR only: `tile_px`. The denominators come from elsewhere, and which one is right depends on the question -- basin area for "how much of this watershed is drained", CDL cultivated pixels (`cdl_classes.CULTIVATED`) for "how much of its cropland is drained". Dividing tile pixels by the raster's own covered pixels answers neither and is the mistake this docstring exists to prevent.

ESRI:102003, NOT EPSG:5070. USA Contiguous Albers, a different projection from every other raster here, so the catchments are reprojected into it for this pass alone. The raster is never reprojected: resampling a categorical layer to move it into 5070 would invent tile drainage at the boundaries.

ONE VINTAGE, 2017. There is no year dimension, and the table is one row per catchment.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import zonal

CRS = "ESRI:102003"
TIF = "AgTile-US.tif"


def raster(directory):
    """The national tif inside the expanded archive."""
    p = directory / TIF
    if not p.exists():
        hits = list(directory.rglob(TIF))
        if not hits:
            raise FileNotFoundError(f"{TIF} not under {directory}")
        p = hits[0]
    return p


def _reduce(comids: np.ndarray, res: pd.DataFrame) -> pd.DataFrame:
    """One batch -> (comid, tile_px, n_px). `frac`/`unique` give the share of each value present."""
    n_px = res["agtile_count"].to_numpy(dtype="float64")
    tile = np.zeros(len(comids), dtype="float64")
    for i, (vals, frac) in enumerate(zip(res["agtile_unique"], res["agtile_frac"])):
        if vals is None or not len(vals):
            continue
        v = np.asarray(vals, dtype="int64")
        f = np.asarray(frac, dtype="float64")
        tile[i] = f[v == 1].sum() * n_px[i]
    return pd.DataFrame({"comid": comids, "tile_px": tile.astype("float32"),
                         "n_px": n_px.astype("float32")})


def cached(part_dir, n_items: int, size: int = zonal.BATCH):
    """The finished frame if every batch is checkpointed, else None. Opens no raster.

    Simpler than the crops version and for the same reason: one raster means one part directory and no group structure to remember, so completeness is `ceil(n / size)` files being present. AgTile's archive is 15 GB expanded, which is 15 GB not spent when there is nothing to compute.
    """
    import math

    d = part_dir / "agtile"
    want = [d / f"part_{i:05d}.parquet" for i in range(math.ceil(n_items / size))]
    if not want or not all(f.exists() for f in want):
        return None
    print(f"  agtile: every batch checkpointed ({len(want)} parts); the archive is not needed", flush=True)
    return pd.concat([pd.read_parquet(f) for f in want], ignore_index=True)


def build(cat: zonal.Catchments, directory, limit: int | None = None, part_dir=None,
          force: bool = False) -> pd.DataFrame:
    """Tile-drained pixel area per catchment, in the raster's own projection.

    `directory` may be a path or an `archive.Lazy`; the archive is expanded only if a batch has to be computed.
    """
    if part_dir is not None and not force and limit is None:
        done = cached(part_dir, len(cat))
        if done is not None:
            return done
    r = raster(getattr(directory, "path", directory))
    print(f"  agtile: {r.name} in {CRS}", flush=True)
    return zonal.extract(cat, {"agtile": r}, CRS, ops=zonal.CATEGORICAL_OPS,
                         reduce=_reduce, limit=limit, force=force,
                         part_dir=None if part_dir is None else part_dir / "agtile",
                         label="agtile ")
