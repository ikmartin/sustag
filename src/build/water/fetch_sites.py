"""STEP 1 of the reworked water build: fetch the candidate site universe from every source.

Produces `raw_site_list.csv` -- the union of in-situ SURFACE-water nitrate sites across sources (USGS + IWQIS today), with only the groundwater-vs-surface filter applied. It does NOT apply the sparsity/lifespan or quality filters (that is filter_sites.py, step 3). Modeled on the standalone experiments/site-discovery/fetch_site_list.py, promoted into the build.

Sources:
  * USGS  -- discovered fresh by querying the USGS OGC API for pcode 99133 within [region].bbox_wgs84 (so it surfaces stations beyond the curated USGS_SITE_LIST).
  * IWQIS -- the Iowa continuous-nitrate registry (site_clean.csv, WQS#### uids), filtered to the bbox.

    python -m src.build.water.fetch_sites        # print the list (+ per-source summary), write raw_site_list.csv
"""

import os
import sys
import tomllib
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]  # repo root
sys.path.insert(0, str(_ROOT))

from src.build.config import get_region_bbox
from src.build.util.site_metadata import (
    CANONICAL,
    _groundwater_flag,
    _iwqis_candidate_ids,
    _iwqis_rows,
    _site_clean,
    _usgs_rows,
    fetch_monitoring_locations,
)
from src.build.water._paths import raw_site_list_path

_NITRATE_PCODE = "99133"  # USGS continuous in-situ nitrate sensor parameter
_KEYS_FILE = _ROOT / "src" / "build" / "api-keys.toml"


def get_bbox() -> list:
    """The shared area of interest, (min_lon, min_lat, max_lon, max_lat), from pipeline config."""
    return list(get_region_bbox())


def _set_usgs_pat() -> None:
    """Put the USGS personal access token on the environment for the OGC-API query."""
    with open(_KEYS_FILE, "rb") as f:
        os.environ["API_USGS_PAT"] = tomllib.load(f)["usgs"]


def _usgs_nitrate_sites_in_bbox(bbox) -> list[str]:
    """USGS monitoring_location_ids that report continuous nitrate (99133) inside the bbox."""
    from dataretrieval import waterdata

    res = waterdata.get_time_series_metadata(parameter_code=_NITRATE_PCODE, bbox=list(bbox), skip_geometry=True)
    df = res[0] if isinstance(res, tuple) else res
    return sorted(pd.Series(df["monitoring_location_id"]).astype(str).unique())


def discover(bbox=None) -> pd.DataFrame:
    """The in-situ surface-water nitrate candidate universe in the bbox, as a frame. USGS is discovered by bbox query; IWQIS is the WQS#### registry filtered to the bbox. Groundwater is the only exclusion. Carries `is_groundwater` and a `source` column beyond CANONICAL."""
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


def fetch_sites(bbox=None) -> pd.DataFrame:
    """STEP 1: discover the candidate universe and write raw_site_list.csv. Returns the frame."""
    frame = discover(bbox)
    out = raw_site_list_path()
    frame.to_csv(out, index=False)
    src = frame["source"].value_counts().to_dict()
    print(f"\n{len(frame)} in-situ surface-water nitrate candidate sites in bbox {get_bbox()}")
    print(f"  by source: {src}")
    print(f"  wrote {out}")
    return frame


if __name__ == "__main__":
    fetch_sites()
