"""STEP 3 of the water build: filter candidates to the kept set and write the contract file.

The last step of `metadata -> usgs+iwqis -> filter`. Reads the candidate table (site_candidates.csv) and the pulled data, applies every quality/exclusion filter that used to live inside usgs.py and iwqis.py, and writes the final `site_location_metadata.csv` -- which is, by construction, the kept set that access.get_metadata() reads. It also prunes non-kept parquets so the data dir equals the kept set, and appends the filter funnel to the build log.

Filtering rules (all here except the one big_basin USGS exclusion kept at fetch in usgs.py):
    IWQIS keep: data_count >= sparsity_cutoff AND lifespan >= lifespan_cutoff  ([iwqis] config)
    exclude:    known_bad + big_basin (config [site_filters]); groundwater (is_groundwater flag)
    USGS keep:  candidate has a {uid}_water.parquet on disk (== the pull succeeded / "has data")

Current funnel: 166 candidates -> 23 USGS + 60 IWQIS = 83 kept.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root on path
from src.build.config import get_config
from src.build.util import iwqis
from src.build.util._water_paths import build_log_path, data_dir, meta_dir
from src.build.util.site_metadata import CANONICAL, STR_COLS


def _candidates_path() -> Path:
    return meta_dir() / "site_candidates.csv"


def _final_path() -> Path:
    return meta_dir() / "site_location_metadata.csv"


def _read_candidates() -> pd.DataFrame:
    df = pd.read_csv(_candidates_path(), dtype={c: str for c in STR_COLS})
    # is_groundwater round-trips through CSV as the strings "True"/"False"; coerce back to bool.
    df["is_groundwater"] = df["is_groundwater"].map(
        lambda v: str(v).strip().lower() in ("true", "1")
    )
    return df


def _iwqis_quality(sparsity_cutoff: float, lifespan_cutoff: float) -> set[str]:
    """WQS uids passing the sparsity + lifespan cutoffs, computed from the reassembled data.

    Same arithmetic as the retired iwqis.filter_sites: fraction of rows with a nitrate value, and span between first and last sample in years.
    """
    src = iwqis._full_data().copy()
    src["datetime"] = pd.to_datetime(src["datetime"], utc=True)
    g = src.groupby("site_uid", sort=False)
    data_count = g["nitrate_con"].count() / g.size()
    lifespan = (g["datetime"].max() - g["datetime"].min()).dt.total_seconds() / (365.25 * 24 * 3600)
    vals = pd.DataFrame({"data_count": data_count, "lifespan": lifespan})
    passing = vals[(vals.data_count >= sparsity_cutoff) & (vals.lifespan >= lifespan_cutoff)]
    return set(passing.index.astype(str))


def filter_final() -> pd.DataFrame:
    cand = _read_candidates()
    cfg = get_config()
    sparsity, lifespan = cfg["iwqis"]["sparsity_cutoff"], cfg["iwqis"]["lifespan_cutoff"]
    # NOTE this module is retired with _make_water; known_bad moved to src/build/water/filter_sites.py
    # (KNOWN_BAD) and is no longer in config, so read it defensively.
    excluded = set(cfg["site_filters"].get("known_bad", [])) | set(cfg["site_filters"]["big_basin"])

    is_usgs = cand.site_uid.str.startswith("USGS-")
    gw = set(cand.loc[cand.is_groundwater, "site_uid"])

    # USGS keep = candidate has a parquet on disk (the pull succeeded). big_basin USGS sites were
    # already excluded at fetch, so they have no parquet and drop out here naturally.
    usgs_cand = set(cand.loc[is_usgs, "site_uid"])
    usgs_on_disk = {u for u in usgs_cand if (data_dir() / f"{u}_water.parquet").exists()}
    usgs_keep = usgs_on_disk - excluded - gw

    # IWQIS keep = candidate passing sparsity/lifespan, minus exclusions and groundwater.
    wqs_cand = set(cand.loc[~is_usgs, "site_uid"])
    wqs_pass = _iwqis_quality(sparsity, lifespan) & wqs_cand
    wqs_keep = wqs_pass - excluded - gw

    kept = usgs_keep | wqs_keep
    final = (
        cand[cand.site_uid.isin(kept)]
        .reindex(columns=CANONICAL)  # drop is_groundwater; final schema == CANONICAL
        .sort_values("site_uid", ignore_index=True)  # load-bearing: holdout determinism
    )
    for c in STR_COLS:
        final[c] = final[c].astype("object").where(final[c].notna(), None)

    missing_coords = final[final[["latitude", "longitude"]].isna().any(axis=1)]
    if len(missing_coords):
        raise RuntimeError("Kept sites with no coordinates: " + ", ".join(missing_coords.site_uid))

    final.to_csv(_final_path(), index=False)

    # Prune parquets for sites not in the kept set, so the data dir == kept set.
    pruned = []
    for f in data_dir().glob("*_water.parquet"):
        if f.name[: -len("_water.parquet")] not in kept:
            f.unlink()
            pruned.append(f.name)

    _write_log(cand, is_usgs, usgs_cand, usgs_on_disk, wqs_cand, wqs_pass, excluded, gw, usgs_keep, wqs_keep, pruned)

    print(
        f"site_location_metadata.csv: {len(final)} kept "
        f"({len(usgs_keep)} USGS + {len(wqs_keep)} IWQIS); pruned {len(pruned)} parquet(s)."
    )

    # Only place the contract file is (re)written, so the single correct cache invalidation point.
    from src.data import access

    access.get_metadata.cache_clear()
    return final


def _write_log(cand, is_usgs, usgs_cand, usgs_on_disk, wqs_cand, wqs_pass, excluded, gw, usgs_keep, wqs_keep, pruned):
    usgs_no_data = usgs_cand - usgs_on_disk
    gw_excluded = sorted(g for g in gw if g in (usgs_on_disk | wqs_pass))
    with open(build_log_path(), "a") as f:
        f.write("[3] Filter\n")
        f.write(f"  USGS candidates: {len(usgs_cand)}; with data (on disk): {len(usgs_on_disk)}")
        f.write(f"; no data: {sorted(usgs_no_data)}\n" if usgs_no_data else "\n")
        f.write(f"  IWQIS candidates: {len(wqs_cand)}; passing sparsity/lifespan: {len(wqs_pass)}\n")
        f.write(f"  excluded (known_bad/big_basin): {sorted(excluded & (usgs_on_disk | wqs_pass))}\n")
        f.write(f"  excluded (groundwater): {gw_excluded}\n")
        f.write(f"  pruned parquets: {len(pruned)}\n")
        f.write(f"  KEPT: {len(usgs_keep) + len(wqs_keep)} = {len(usgs_keep)} USGS + {len(wqs_keep)} IWQIS\n")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    filter_final()
    print(f"Wrote {_final_path()}")
