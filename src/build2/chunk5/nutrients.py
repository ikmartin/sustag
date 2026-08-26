"""Nitrogen surplus and applied fertilizer, per catchment per year.

TWO PRODUCTS, ONE GRID, ONE PASS. `Surplus_N_{year}.tif` and `Fertilizer_Ag_{year}.tif` are published on the identical 250 m EPSG:5070 lattice (18458 x 11613) for 2000-2017, so all thirty-six rasters go through the engine together and the coverage fractions are computed once.

WHY BOTH, WHEN SURPLUS IS THE ESTABLISHED FEATURE. Fertilizer is not a copy of surplus, it is a COMPONENT of it: surplus nets out crop removal, so a basin with heavy application and heavy uptake reads high on one and low on the other. Storing both lets a model separate how much went on from how much was left over. Two caveats travel with it -- it is an input to gTREND, so its signal is partly embedded there if gTREND features are ever used, and like surplus it is a modelled product rather than a measurement.

MEAN AND AREA, NOT A TOTAL FROM THE RASTER. The band is an intensity (kg N/ha), so the catchment's total is `mean x area` and the mean is the coverage-weighted mean the engine already returns. Both are stored: the mean is what a person reads, and the total is what ACCUMULATES -- summing means up a river network is meaningless, while summing kilograms is exactly right, and the accumulated intensity comes back by dividing at the end.

NO 250 m WEIGHT MATRIX. The plan called for one, on the reasoning that a stored matrix makes a future 250 m product a re-read. With the engine running at about 1 ms per catchment on this grid, that future re-read is a few minutes -- so a 60M-row matrix would exist only to avoid a cost smaller than reading it. The gridMET matrix is kept because its consumer is the READ path at request time, which is a different argument.

2000-2017 DOES NOT COVER THE WATER RECORD, which runs to 2026. What happens past 2017 -- carry the last year forward, or go NaN -- is a feature-assembly decision and is deliberately not resolved here. Chunk 5 stores the years that exist.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from . import zonal

CRS = "EPSG:5070"

# `{product: (directory, filename stem)}`. Both stay expanded on disk -- see `archive.EPHEMERAL`.
SOURCES = {
    "surplus": ("surplus", "Surplus_N_"),
    "fertilizer": ("fertilizer", "Fertilizer_Ag_"),
}

# The band's unit. kg N per hectare, so a catchment's total kilograms is mean x hectares.
HA_PER_PIXEL = 250.0 * 250.0 / 10_000.0


def rasters() -> dict[str, object]:
    """`{'{product}_{year}': path}` for every year of both products found on disk."""
    out = {}
    for product, (sub, stem) in SOURCES.items():
        d = config.RAW / sub / "national"
        for p in sorted(d.glob(f"{stem}*.tif")):
            try:
                y = int(p.stem.replace(stem, ""))
            except ValueError:
                continue
            out[f"{product}_{y}"] = p
    return out


def _reduce(comids: np.ndarray, res: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    """One batch -> long rows of (comid, product, year, kg_per_ha, kg, n_px)."""
    frames = []
    for n in names:
        product, year = n.rsplit("_", 1)
        mean = res[f"{n}_mean"].to_numpy(dtype="float64")
        n_px = res[f"{n}_count"].to_numpy(dtype="float64")
        frames.append(pd.DataFrame({
            "comid": comids,
            "product": product,
            "year": np.int16(int(year)),
            "kg_per_ha": mean.astype("float32"),
            "kg": (mean * n_px * HA_PER_PIXEL).astype("float32"),
            "n_px": n_px.astype("float32"),
        }))
    return pd.concat(frames, ignore_index=True)


def build(cat: zonal.Catchments, limit: int | None = None, part_dir=None,
          force: bool = False) -> pd.DataFrame:
    """Surplus and fertilizer over every catchment, long by (product, year)."""
    paths = rasters()
    if not paths:
        raise FileNotFoundError(f"no Surplus_N/Fertilizer_Ag rasters under {config.RAW}")
    years = sorted({int(k.rsplit("_", 1)[1]) for k in paths})
    print(f"  {len(paths)} raster(s): {sorted({k.rsplit('_', 1)[0] for k in paths})} "
          f"{years[0]}-{years[-1]}", flush=True)
    names = list(paths)
    return zonal.extract(cat, paths, CRS, ops=zonal.CONTINUOUS_OPS,
                         reduce=lambda c, r: _reduce(c, r, names),
                         limit=limit, force=force,
                         part_dir=None if part_dir is None else part_dir / "nutrients",
                         label="nutrients ")
