"""This file does one thing: it finds valid candidate sites from various data sources, right now just USGS and IWQIS.

"Valid" = an in-situ (continuous, pcode 99133) nitrate site inside the bounding box that is SURFACE
water (not a well/spring). The ONLY filter applied is groundwater vs surface water -- the
sparsity/lifespan quality filter is deliberately NOT applied here (that lives in the build's
filter_sites.py, which decides the finally-kept set). So this returns the candidate universe.

Sources:
  * USGS  -- discovered fresh by querying the USGS OGC API for pcode 99133 within the bbox (so it
             surfaces new stations, unlike the build's curated USGS_SITE_LIST).
  * IWQIS -- the Iowa continuous-nitrate registry (site_clean.csv, WQS#### uids), filtered to the bbox.

    python fetch_site_list.py            # print the list (+ per-source summary), write site_list.csv
"""

import os
import sys
import tomllib
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]  # repo root
sys.path.insert(0, str(_ROOT))

from src.build.util.site_metadata import (
    CANONICAL,
    _groundwater_flag,
    _iwqis_candidate_ids,
    _iwqis_rows,
    _site_clean,
    _usgs_rows,
    fetch_monitoring_locations,
)

_BBOX = [-97.6, 39.8, -89.5, 44.5]
_USE_CONFIG_BBOX = False

_NITRATE_PCODE = "99133"  # USGS continuous in-situ nitrate sensor parameter
_KEYS_FILE = _ROOT / "src" / "build" / "api-keys.toml"


# helper for expanding BBOX easily
def get_bbox():
    if _USE_CONFIG_BBOX:
        from src.build.config import get_region_bbox

        return list(get_region_bbox())
    return _BBOX


def _set_usgs_pat():
    """Get the USGS personal access token"""
    with open(_KEYS_FILE, "rb") as f:
        os.environ["API_USGS_PAT"] = tomllib.load(f)["usgs"]


def _usgs_nitrate_sites_in_bbox(bbox) -> list[str]:
    """USGS monitoring_location_ids that report continuous nitrate (99133) inside the bbox."""
    from dataretrieval import waterdata

    res = waterdata.get_time_series_metadata(parameter_code=_NITRATE_PCODE, bbox=list(bbox), skip_geometry=True)
    df = res[0] if isinstance(res, tuple) else res
    return sorted(pd.Series(df["monitoring_location_id"]).astype(str).unique())


def discover(bbox=None) -> pd.DataFrame:
    """The in-situ surface-water nitrate candidate universe in the bbox, as a frame. USGS is
    discovered by bbox query; IWQIS is the WQS#### registry filtered to the bbox. Groundwater is the
    only exclusion. Adds a `source`."""
    bbox = bbox or get_bbox()
    minlon, minlat, maxlon, maxlat = bbox
    _set_usgs_pat()

    clean = _site_clean()
    usgs_ids = _usgs_nitrate_sites_in_bbox(bbox)
    iwqis_ids = _iwqis_candidate_ids(clean)
    ml = fetch_monitoring_locations(usgs_ids)  # cache-first; fetches the freshly-discovered ids

    df = pd.concat(
        [_usgs_rows(usgs_ids, ml, clean), _iwqis_rows(iwqis_ids, ml, clean)],
        ignore_index=True,
    ).reindex(columns=CANONICAL)
    df["is_groundwater"] = _groundwater_flag(df)
    df["source"] = df["site_uid"].astype(str).str.startswith("USGS-").map({True: "USGS", False: "IWQIS"})

    in_box = df["longitude"].between(minlon, maxlon) & df["latitude"].between(minlat, maxlat)
    keep = df[in_box & ~df["is_groundwater"]].sort_values("site_uid", ignore_index=True)
    return keep


def fetch_site_list(bbox=None) -> list[str]:
    """The deliverable: sorted site_uids of every in-situ surface-water nitrate site in the bbox."""
    return sorted(discover(bbox)["site_uid"])


if __name__ == "__main__":
    frame = discover()
    src = frame["source"].value_counts().to_dict()
    print(f"\n{len(frame)} in-situ surface-water nitrate sites in bbox {get_bbox()}")
    print(f"  by source: {src}")
    out = Path(__file__).resolve().parent / "site_list.csv"
    frame.to_csv(out, index=False)
    print(f"  wrote {out}")
    print("  sites:", ", ".join(frame["site_uid"]))
