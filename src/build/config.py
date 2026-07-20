"""Build-time configuration: parse pipeline_config.toml.

Build-only -- the runtime read path (src/data) must NOT import this module. The one
cross-cutting invariant the read path needs (the equal-area CRS) lives in src/data/crs.py
as a constant instead; everything here is a build knob (region, year spans, site filters,
thresholds, crop-aggregation schema).
"""

import tomllib
from functools import lru_cache
from pathlib import Path

_CONFIG_FILE = Path(__file__).resolve().parent / "pipeline_config.toml"


@lru_cache(maxsize=1)
def get_config() -> dict:
    """Full parsed pipeline config, cached for the process. Uses an absolute path off this
    file, so it is unaffected by os.chdir() (the pipeline runner chdir's per builder)."""
    with open(_CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def get_region_bbox(albers: bool = False) -> tuple:
    """Shared area of interest as (min_lon, min_lat, max_lon, max_lat), or EPSG:5070 metres
    if albers=True. The Albers path reuses the invariant reprojection from src.data.crs
    (build may depend on data-side constants; data must never depend on build)."""
    bbox = tuple(get_config()["region"]["bbox_wgs84"])
    if albers:
        from src.data.crs import wgs84_to_albers
        return tuple(wgs84_to_albers(*bbox))
    return bbox
