"""Direct cell-by-cell comparison of the two D8 flow-direction rasters over Iowa.

Legacy = IWQIS `direction500m.png` (numeric-keypad encoding, ~15 arcsec).
New    = HydroSHEDS `flowdir_15s.tif` (ESRI powers-of-2 encoding, 15 arcsec).

Both are 15-arcsec grids aligned to the global 1/240-degree grid, so cells co-register and we can compare downstream directions directly. Reports: coverage, flow-direction agreement, a SHIFT TEST (agreement under a +/-1 pixel offset -- a peak off zero would reveal a georef/encoding error in the new handling rather than a genuine source difference), and where they disagree.

    python experiments/raster-investigation/compare_rasters.py
"""

import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.windows import from_bounds

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_CACHE = _ROOT / "src" / "data" / "raw" / "basins" / "cache"
_LEGACY = _CACHE / "direction500m.png"
_HS = _CACHE / "flowdir_15s.tif"

# legacy IWQIS georef (verbatim from src/data/d8.py)
_RES = 0.004167
_LON0, _LAT0 = -97.154167, 44.53785  # ll_to_pixel: col=(lon-_LON0)/RES+0.5, row=(_LAT0-lat)/RES+0.5

# encodings -> downstream (dcol, drow); 0 elsewhere. validity masks separate.
_IWQIS = {1: (-1, 1), 2: (0, 1), 3: (1, 1), 4: (-1, 0), 6: (1, 0), 7: (-1, -1), 8: (0, -1), 9: (1, -1)}
_ESRI = {1: (1, 0), 2: (1, 1), 4: (0, 1), 8: (-1, 1), 16: (-1, 0), 32: (-1, -1), 64: (0, -1), 128: (1, -1)}


def _step_arrays(mapping):
    """code(0..255) -> (dcol, drow) lookup + a validity mask over 0..255."""
    dc = np.zeros(256, np.int8)
    dr = np.zeros(256, np.int8)
    valid = np.zeros(256, bool)
    for code, (c, r) in mapping.items():
        dc[code], dr[code], valid[code] = c, r, True
    return dc, dr, valid


def main():
    if not (_LEGACY.exists() and _HS.exists()):
        raise SystemExit(f"need both rasters: {_LEGACY.exists()=} {_HS.exists()=}")

    legacy = np.array(Image.open(_LEGACY).convert("RGB"))[:, :, 0].astype(np.uint8)
    lh, lw = legacy.shape
    print(f"legacy IWQIS: {lw}x{lh} @ {_RES} deg (~15 arcsec); codes present: {sorted(np.unique(legacy).tolist())}")

    # Iowa comparison window (WGS84)
    W = (-96.7, 40.3, -90.0, 43.6)
    with rasterio.open(_HS) as ds:
        print(f"HydroSHEDS: {ds.width}x{ds.height} @ {ds.res} deg; crs {ds.crs}; bounds {[round(b,2) for b in ds.bounds]}")
        win = from_bounds(*W, ds.transform).round_offsets().round_lengths()
        hs = ds.read(1, window=win)
        wt = ds.window_transform(win)
        hs_codes_present = sorted(np.unique(hs).tolist())
    hh, hw = hs.shape
    print(f"HS window over Iowa {W}: {hw}x{hh}; codes present: {hs_codes_present}")

    # cell-center lon/lat of every HS window cell
    rows, cols = np.mgrid[0:hh, 0:hw]
    lon, lat = rasterio.transform.xy(wt, rows.ravel(), cols.ravel())  # centers
    lon = np.asarray(lon).reshape(hh, hw)
    lat = np.asarray(lat).reshape(hh, hw)

    # sample legacy at those same cell centers (legacy georef)
    lcol = np.floor((lon - _LON0) / _RES + 0.5).astype(int)
    lrow = np.floor((_LAT0 - lat) / _RES + 0.5).astype(int)
    inb = (lcol >= 0) & (lcol < lw) & (lrow >= 0) & (lrow < lh)
    leg = np.zeros((hh, hw), np.uint8)
    leg[inb] = legacy[lrow[inb], lcol[inb]]

    ldc, ldr, lval = _step_arrays(_IWQIS)
    hdc, hdr, hval = _step_arrays(_ESRI)
    leg_valid = lval[leg] & inb
    hs_valid = hval[hs]
    both = leg_valid & hs_valid
    print(f"\ncells in window: {hh * hw:,}")
    print(f"  legacy valid-flow: {leg_valid.mean():.1%} | HS valid-flow: {hs_valid.mean():.1%} | both: {both.mean():.1%}")

    # direction agreement on cells valid in both
    agree = (ldc[leg] == hdc[hs]) & (ldr[leg] == hdr[hs])
    print(f"  flow-direction AGREEMENT on both-valid cells: {agree[both].mean():.1%} ({int(agree[both].sum()):,}/{int(both.sum()):,})")

    # SHIFT TEST: does agreement peak at a non-zero offset? (would mean georef misalignment)
    print("\nshift test (legacy sampled at cell-center offset by d(lon,lat) = 15 arcsec):")
    for dlat in (-1, 0, 1):
        line = []
        for dlon in (-1, 0, 1):
            lc = np.floor((lon + dlon * _RES - _LON0) / _RES + 0.5).astype(int)
            lr = np.floor((_LAT0 - (lat + dlat * _RES)) / _RES + 0.5).astype(int)
            ib = (lc >= 0) & (lc < lw) & (lr >= 0) & (lr < lh)
            lg = np.zeros((hh, hw), np.uint8)
            lg[ib] = legacy[lr[ib], lc[ib]]
            lv = lval[lg] & ib
            b = lv & hs_valid
            ag = ((ldc[lg] == hdc[hs]) & (ldr[lg] == hdr[hs]))[b].mean() if b.any() else float("nan")
            line.append(f"{ag:5.1%}")
        print(f"   dlat={dlat:+d}: " + "  ".join(f"dlon={d:+d}:{v}" for d, v in zip((-1, 0, 1), line)))
    print("(if the center [dlat=0,dlon=0] is the max, the grids are aligned and my HS handling is correct)")


if __name__ == "__main__":
    main()
