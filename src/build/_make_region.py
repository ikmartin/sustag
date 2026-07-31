"""Compute `covariate_bbox` and write it to pipeline_config.toml [region].

Runs AFTER basins and BEFORE the covariate builders (grid/weather/crops/surplus/agtile/comid_attrs), so their extent grows to cover basins that reach outside Iowa. Two bboxes are kept separate to avoid a discover->bigger-basins feedback loop:

    bbox_wgs84      FIXED site-discovery box (fetch_sites only)
    covariate_bbox  DERIVED here = enclose(bbox_wgs84 + every delineated basin), padded outward

`make_region()` returns whether the box changed, so make_features can decide to rebuild the covariate layers (a grown box means the geometry/grid must be rebuilt to cover it).

Usage: python -m src.build._make_region
"""

import argparse
import sys
from pathlib import Path

from shapely.geometry import box

_THIS_DIR = Path(__file__).resolve().parent  # src/build
_SRC = _THIS_DIR.parent  # src
sys.path.insert(0, str(_SRC.parent))  # repo root on path

from src.build.config import get_config, get_region_bbox

_CONFIG_FILE = _THIS_DIR / "pipeline_config.toml"
_PAD_DEG = 0.05  # small outward pad so cells straddling a basin divide aren't clipped


def compute_covariate_bbox() -> tuple:
    """enclose(site box, every preferred basin) in WGS84, padded + rounded outward to a stable box."""
    minx, miny, maxx, maxy = box(*get_region_bbox()).bounds
    try:
        from src.data import access

        basins = access.get_all_basins()  # preferred basin per site, EPSG:5070
        if basins is not None and len(basins):
            bx = basins.to_crs("EPSG:4326").total_bounds  # [minx, miny, maxx, maxy]
            minx, miny = min(minx, bx[0]), min(miny, bx[1])
            maxx, maxy = max(maxx, bx[2]), max(maxy, bx[3])
    except Exception as e:
        print(f"  [WARN] could not read basins to enclose ({e}); covariate_bbox = site box.")
    return (
        round(minx - _PAD_DEG, 3),
        round(miny - _PAD_DEG, 3),
        round(maxx + _PAD_DEG, 3),
        round(maxy + _PAD_DEG, 3),
    )


def _write_covariate_bbox(bbox: tuple) -> bool:
    """Upsert `covariate_bbox = [...]` under [region] in pipeline_config.toml. Returns whether the written value differs from what was there (so a caller can rebuild the covariate layers)."""
    new_line = f"covariate_bbox = [{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}]"
    lines = _CONFIG_FILE.read_text().splitlines()
    has = any(ln.strip().startswith("covariate_bbox") for ln in lines)
    out, in_region, changed = [], False, True
    for ln in lines:
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            in_region = s == "[region]"
        if in_region and s.startswith("covariate_bbox"):
            changed = s != new_line
            out.append(new_line)
            continue
        out.append(ln)
        if in_region and not has and s.startswith("bbox_wgs84"):
            out.append(new_line)
            has = True  # inserted -> don't insert again
    _CONFIG_FILE.write_text("\n".join(out) + "\n")
    get_config.cache_clear()  # so get_covariate_bbox() sees the new value this run
    return changed


def make_region() -> bool:
    """Compute covariate_bbox from the current basins and write it to config. Returns changed?"""
    bbox = compute_covariate_bbox()
    changed = _write_covariate_bbox(bbox)
    site = get_region_bbox()
    grew = tuple(bbox) != tuple(site)
    print(
        f"covariate_bbox = [{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}] "
        f"({'changed' if changed else 'unchanged'}; {'extends past' if grew else 'equals'} the site box) "
        f"-> pipeline_config.toml"
    )
    return changed


def main(force: bool = False) -> None:
    make_region()


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    main()
