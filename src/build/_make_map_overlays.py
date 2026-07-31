"""Download NHD medium-resolution flowlines + waterbodies over the region bbox (widget map overlays + basin snapping + the comid_attrs COMID universe).

One-off network build (pynhd / USGS NHD). The NHD MR service is national; the extent is set by the `bbox` argument (a WGS84 box), which defaults to get_covariate_bbox() -- so the layer widens with the basins instead of being clamped to Iowa. The app reads the saved GeoParquet at startup.

Output filenames keep the historical `iowa_` prefix (6 readers depend on them) but now hold the bbox-wide layer: src/data/processed/map_overlays/iowa_{flowlines,waterbodies}.parquet (EPSG:4326).
"""

import sys
from pathlib import Path

from shapely.geometry import box

_THIS_DIR = Path(__file__).resolve().parent           # src/build
_SRC = _THIS_DIR.parent                               # src
sys.path.insert(0, str(_SRC.parent))                  # repo root on path

from src.build.config import get_config, get_covariate_bbox

_SAVE_DIR = _SRC / "data" / "processed" / "map_overlays"

# Minimum Strahler stream order to include in the flowlines layer (3 = a good balance).
MIN_STREAM_ORDER = get_config()["map_overlays"]["min_stream_order"]


def region_geometry(bbox=None):
    """The WGS84 clip geometry for the NHD queries -- a box over `bbox` (default covariate_bbox)."""
    return box(*(bbox or get_covariate_bbox()))


def download_flowlines(geom):
    from pynhd import NHD
    print("Downloading NHD flowlines (medium resolution) ...")
    flowlines = NHD("flowline_mr").bygeom(geom, geo_crs="EPSG:4326")
    if "streamorde" in flowlines.columns:
        flowlines = flowlines[flowlines["streamorde"] >= MIN_STREAM_ORDER].copy()
        print(f"  {len(flowlines):,} features (streamorde >= {MIN_STREAM_ORDER})")
    return flowlines


def download_waterbodies(geom):
    from pynhd import NHD
    print("Downloading NHD waterbodies (medium resolution) ...")
    return NHD("waterbody_mr").bygeom(geom, geo_crs="EPSG:4326")


def main(api_keys=None, force: bool = False, bbox=None) -> None:
    """Build the flowline/waterbody overlays over `bbox` (default get_covariate_bbox()). make_features calls this twice: first over the fixed site box (for basin snapping), then force=True over the wider covariate_bbox after basins/_make_region (for display + the comid_attrs COMID set)."""
    _SAVE_DIR.mkdir(parents=True, exist_ok=True)
    flowlines_path = _SAVE_DIR / "iowa_flowlines.parquet"
    waterbodies_path = _SAVE_DIR / "iowa_waterbodies.parquet"

    if flowlines_path.exists() and waterbodies_path.exists() and not force:
        print("Overlay files already exist; skipping download (pass force=True to overwrite).")
        return

    geom = region_geometry(bbox)
    if not flowlines_path.exists() or force:
        download_flowlines(geom).to_parquet(flowlines_path)
        print(f"Saved flowlines -> {flowlines_path}")
    if not waterbodies_path.exists() or force:
        download_waterbodies(geom).to_parquet(waterbodies_path)
        print(f"Saved waterbodies -> {waterbodies_path}")


if __name__ == "__main__":
    main()
