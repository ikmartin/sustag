"""Which raster's derived river network matches reality (NHD flowlines) better?

For each raster we compute D8 flow accumulation, threshold it to a derived stream network at a fixed drainage-area cutoff, and score that network against the NHD medium-res flowlines (ground truth):
  precision = fraction of derived-stream cells lying on/adjacent to an NHD flowline
  recall    = fraction of NHD-flowline cells that have a derived-stream cell on/adjacent
Higher = the raster's flow directions reproduce the true channel network better. Also doubles as a correctness check on the HydroSHEDS handling in src/data/d8.py: garbled directions (bad encoding / georef) would score near random.

    python experiments/raster-investigation/validate_vs_nhd.py
"""

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import from_bounds
from scipy.ndimage import binary_dilation

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from src.data import access, d8

_IOWA = (-96.7, 40.3, -90.0, 43.6)  # WGS84 comparison window
_KM2_PER_CELL = 0.463 * 0.344  # ~15 arcsec cell area at ~42N (approx, both rasters ~same)
_THRESH_KM2 = (200.0, 1000.0)  # drainage-area cutoffs defining "a stream"


_REAL_HS = d8._RASTER_HS  # capture the real HydroSHEDS path before any monkeypatching


def _accumulation_for(source: str):
    """(acc, transform, (H,W)) for 'hydrosheds' or 'legacy' by driving src/data/d8 at each source."""
    d8._DIRECTION = d8._ACCUM = None
    d8._FLOW_FIELD_CACHE = {}
    d8._SOURCE = None
    d8._RASTER_HS = _REAL_HS if source == "hydrosheds" else Path("/nonexistent_force_legacy.tif")
    direction = d8.load_direction_array()
    assert d8._SOURCE == source, f"wanted {source}, got {d8._SOURCE}"
    acc = d8._flow_accumulation(direction)
    return acc, d8.TRANSFORM, direction.shape


def _score(acc, transform, shape, nhd_geoms):
    """precision/recall of the derived stream net vs NHD, at each drainage threshold, on the Iowa window."""
    H, W = shape
    win = from_bounds(*_IOWA, transform).round_offsets().round_lengths()
    r0, c0 = int(win.row_off), int(win.col_off)
    r1, c1 = min(H, r0 + int(win.height)), min(W, c0 + int(win.width))
    r0, c0 = max(0, r0), max(0, c0)
    sub_acc = acc[r0:r1, c0:c1]
    sub_tf = rasterio.transform.from_origin(
        transform.c + c0 * transform.a, transform.f + r0 * transform.e, transform.a, -transform.e
    )
    nhd = rasterize(nhd_geoms, out_shape=sub_acc.shape, transform=sub_tf, fill=0, default_value=1, all_touched=True).astype(bool)
    nhd_tol = binary_dilation(nhd, iterations=1)  # +/-1 cell tolerance (absorbs the ~1px registration offset)
    out = {}
    for km2 in _THRESH_KM2:
        stream = sub_acc * _KM2_PER_CELL >= km2
        stream_tol = binary_dilation(stream, iterations=1)
        prec = (stream & nhd_tol).sum() / max(1, stream.sum())
        rec = (nhd & stream_tol).sum() / max(1, nhd.sum())
        out[km2] = (prec, rec, int(stream.sum()), int(nhd.sum()))
    return out


def main():
    fl = access.get_flowlines()
    geoms = [g for g in fl.to_crs("EPSG:4326").geometry if g is not None]
    print(f"NHD flowlines: {len(geoms)} features (streamorde>=3, Iowa)\n")

    for source in ("legacy", "hydrosheds"):
        acc, tf, shape = _accumulation_for(source)
        sc = _score(acc, tf, shape, geoms)
        print(f"== {source} ==  raster {shape[1]}x{shape[0]}")
        for km2, (p, r, ns, nn) in sc.items():
            print(f"   >= {km2:>5.0f} km2: precision {p:5.1%} | recall {r:5.1%}  ({ns:,} stream cells vs {nn:,} NHD cells)")
        print()
    print("precision = derived-stream cells that sit on a real NHD channel; recall = NHD channels the")
    print("derived network recovers. Higher on BOTH = the more faithful flow raster for Iowa.")


if __name__ == "__main__":
    main()
