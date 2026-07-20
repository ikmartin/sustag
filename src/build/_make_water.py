"""Build the water dataset: nitrate target + site metadata (faithful port of make_water.py).

NOT a re-grain -- water is the prediction target (per-site nitrate time series) plus the
canonical site metadata (the 85-site list, locations, date ranges), so it stays per-site. This
orchestrates the two sources and the combiner:

    usgs   (network, dataretrieval)     -> processed/water/data/USGS-*.parquet + usgs metadata
    iwqis  (offline chunk reassembly)   -> processed/water/data/WQS*.parquet  + iwqis metadata
    site_locations (combine)            -> processed/water/meta/site_location_metadata.csv
    gen_statistics                      -> processed/water/meta/site_statistics.csv

The per-site parquets under processed/water are the DURABLE source of truth (IWQIS is not
re-fetchable) and are tracked in git; see .gitignore. api-keys.toml (for USGS) is read from
api-keys.toml (repo root), else the legacy sustag/data/api-keys.toml.

Usage
-----
    python -m src.build._make_water
"""

import sys
import tomllib
from pathlib import Path

import pandas as pd

_THIS_DIR = Path(__file__).resolve().parent  # src/build/
_SRC = _THIS_DIR.parent  # src/
sys.path.insert(0, str(_SRC.parent))  # repo root on path

from src.build.config import get_config
from src.build.util import iwqis, site_locations, usgs
from src.build.util._water_paths import meta_dir


def get_api_keys() -> dict:
    """Load api-keys.toml. Needed for the USGS pull."""
    p = _THIS_DIR / "api-keys.toml"
    if not p.exists():
        raise FileNotFoundError(f"api-keys.toml not found at {p} (needed for the USGS pull).")
    with open(p, "rb") as f:
        return tomllib.load(f)


def gen_statistics() -> pd.DataFrame:
    """Per-site nitrate stats from the built per-site parquets."""
    from src.data.access import get_all_water

    df = get_all_water()
    grouped = df.groupby("site_uid", sort=False)
    sparsity = grouped["nitrate_con"].count() / grouped.size()
    first_date = grouped["datetime"].min()
    last_date = grouped["datetime"].max()
    lifespan = (last_date - first_date).dt.total_seconds() / (365.25 * 24 * 3600)
    return pd.DataFrame(
        {"nitrate_sparsity": sparsity, "start_date": first_date, "last_date": last_date, "lifespan": lifespan}
    ).reset_index()


def main(api_keys=None, force: bool = False) -> None:
    if api_keys is None:
        api_keys = get_api_keys()

    cfg = get_config()
    all_filtered = cfg["site_filters"]["known_bad"] + cfg["site_filters"]["big_basin"]
    usgs_filter = [uid for uid in all_filtered if uid.startswith("USGS")]
    iwqis_filter = [uid for uid in all_filtered if uid.startswith("WQ")]

    usgs.main(api_keys, extra_filter=usgs_filter)
    iwqis.main(api_keys, extra_filter=iwqis_filter)
    site_locations.create_site_locations()

    stats = gen_statistics()
    stats_path = meta_dir() / "site_statistics.csv"
    stats.to_csv(stats_path, index=False)
    print(f"Saved site statistics to {stats_path}")


if __name__ == "__main__":
    main()
