"""Clip the CONUS AgTile-US tile-drainage raster to the region bbox (analogue of clip_crops.py).

AgTile-US (Valayamkunnath et al. 2020, Sci Data 7:257; Figshare DOI 10.6084/m9.figshare.11825742) is a 30 m binary tile-drained-cropland raster: 1 = tile-drained, 0 = undrained cropland, and NON-AGRICULTURAL land is MASKED (nodata), NOT coded 0. Single vintage 2017. CONUS uncompressed is ~14.9 GB, so -- exactly like the national CDL tifs in clip_crops -- the national file is downloaded MANUALLY and this tool windows it down to the region once.

LICENCE: CC-BY 4.0 -- attribute Valayamkunnath, Barlage, Chen, Gochis & Franz (2020). Self-host the Figshare TIFF; do not use the Google Earth Engine mirror for a deployed product.

Manual pre-step:
  1. Download AgTile-US-TIFF.zip from Figshare (DOI above), unzip.
  2. Put the CONUS GeoTIFF at src/data/raw/agtile/national/AgTile-US.tif
Then:
    python -m src.build.util.clip_agtile        # writes raw/agtile/clipped/agtile_clip.tif
"""

import sys
from pathlib import Path

import rasterio
from rasterio.windows import Window, from_bounds, intersection
from rasterio.warp import transform_bounds

_THIS_DIR = Path(__file__).resolve().parent  # src/build/util
_SRC = _THIS_DIR.parents[1]  # src
_RAW_DIR = _SRC / "data" / "raw" / "agtile"
_NATIONAL_DIR = _RAW_DIR / "national"  # manually-downloaded CONUS AgTile-US.tif here
_CLIP_DIR = _RAW_DIR / "clipped"  # agtile_clip.tif (what _make_agtile reads)

sys.path.insert(0, str(_THIS_DIR.parents[2]))  # repo root -> import src.build.config
from src.build.config import get_covariate_bbox

_NATIONAL_TIF = _NATIONAL_DIR / "AgTile-US.tif"
_CLIP_TIF = _CLIP_DIR / "agtile_clip.tif"


def clip_agtile(force: bool = False) -> Path:
    if _CLIP_TIF.exists() and not force:
        print(f"{_CLIP_TIF.name} exists; use --force to rebuild.")
        return _CLIP_TIF
    if not _NATIONAL_TIF.exists():
        raise FileNotFoundError(
            f"{_NATIONAL_TIF} missing. Download AgTile-US-TIFF.zip from Figshare "
            "(DOI 10.6084/m9.figshare.11825742), unzip, and place the CONUS tif there."
        )
    _CLIP_DIR.mkdir(parents=True, exist_ok=True)
    minlon, minlat, maxlon, maxlat = get_covariate_bbox()  # WGS84
    with rasterio.open(_NATIONAL_TIF) as s:
        # AgTile is Albers NAD83; densify the WGS84 bbox edges before reprojecting to raster CRS.
        b = transform_bounds("EPSG:4326", s.crs, minlon, minlat, maxlon, maxlat, densify_pts=21)
        window = from_bounds(*b, s.transform).round_offsets().round_lengths()
        window = intersection(window, Window(0, 0, s.width, s.height))
        if window.width <= 0 or window.height <= 0:
            raise ValueError("Region bbox does not overlap the AgTile raster.")
        band = s.read(1, window=window)
        profile = s.profile.copy()
        profile.update(
            height=int(window.height), width=int(window.width),
            transform=s.window_transform(window), compress="lzw",
        )
        with rasterio.open(_CLIP_TIF, "w", **profile) as d:
            d.write(band, 1)
    print(f"Wrote {_CLIP_TIF} ({band.shape[1]}x{band.shape[0]} px)")
    return _CLIP_TIF


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true")
    clip_agtile(force=ap.parse_args().force)
