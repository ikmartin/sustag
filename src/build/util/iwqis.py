"""Build the IWQIS nitrate data (faithful port of data/water/make_iwqis_data.py).

PECULIAR SOURCE: IWQIS is NOT an API. The original download (iwqis_alldata.csv) was chunked with
split_csv.py into chunks/ (to fit on GitHub) and its site.csv hand-fixed to site_clean.csv (a
bad quote on row 63). This reassembles the chunked CSV via the manifest, filters garbage sites
by sparsity/lifespan, and writes one {uid}_water.parquet per keeper site + the metadata CSVs.

Because the raw chunks are 3.1 GB and not re-fetchable, the per-site parquets (processed/water)
are the durable source of truth; this builder only needs to run when reconstructing from chunks.

Source (raw/water, else legacy water_raw): chunks/, site_clean.csv, measures.csv, params.csv.
Output: processed/water/data/{uid}_water.parquet, processed/water/meta/iwqis_{site_metadata,
measures,params}.csv.
"""

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root on path
from src.build.config import get_config
from src.build.util._water_paths import data_dir, meta_dir, raw_dir

FULL_DATA = None


def _measures_source():
    return raw_dir() / "measures.csv"


def _metadata_source():
    return raw_dir() / "site_clean.csv"


def _params_source():
    return raw_dir() / "params.csv"


def _measures_target():
    return meta_dir() / "iwqis_measures.csv"


def _metadata_target():
    return meta_dir() / "iwqis_site_metadata.csv"


def _params_target():
    return meta_dir() / "iwqis_params.csv"


def _full_data():
    global FULL_DATA
    if FULL_DATA is None:
        FULL_DATA = _get_full_data()
    return FULL_DATA


def get_site_metadata():
    return pd.read_csv(_metadata_source(), engine="python", on_bad_lines="warn")


def _get_full_data():
    manifest_path = raw_dir() / "chunks" / "iwqis_alldata_manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)

    chunks_dir = Path(manifest_path).parent
    print(f"Reassembling '{manifest['original_filename']}'")
    print(f"Expected: {manifest['total_rows']:,} rows across {manifest['num_chunks']} chunks")
    files = [chunks_dir / Path(chunk["filename"]) for chunk in manifest["chunks"]]
    print(f"Found {len(files)} chunks to merge (order per the manifest). Proceeding...")

    dfs = []
    for i, f in enumerate(files):
        print(f"  Reading chunk {i + 1}/{len(files)}: {f}", end="\r")
        dfs.append(pd.read_csv(f))
    print(" ")

    full_data = pd.concat(dfs, ignore_index=True)
    actual, expected = full_data.shape[0], int(manifest["total_rows"])
    if actual != expected:
        raise ValueError(
            f"Row count mismatch reassembling '{manifest['original_filename']}': "
            f"expected {expected:,}, got {actual:,} (difference {actual - expected:+,})"
        )
    print(f"Reassembled {len(full_data):,} rows.")
    return full_data


def filter_sites(sparsity_cutoff, lifespan_cutoff, source):
    source = source.copy()
    source["datetime"] = pd.to_datetime(source["datetime"], utc=True)
    grouped = source.groupby("site_uid", sort=False)
    data_count = grouped["nitrate_con"].count() / grouped.size()
    span = grouped["datetime"].max() - grouped["datetime"].min()
    lifespan = span.dt.total_seconds() / (365.25 * 24 * 3600)
    vals = pd.DataFrame({"data_count": data_count, "lifespan": lifespan}).reset_index()

    keep_mask = (vals.data_count >= sparsity_cutoff) & (vals.lifespan >= lifespan_cutoff)
    keep, remove = vals[keep_mask], vals[~keep_mask]
    print(f"{keep.shape[0]} (keep) + {remove.shape[0]} (remove) = {vals.shape[0]}")
    assert keep.shape[0] + remove.shape[0] == vals.shape[0]
    return keep, remove


def _precheck(extra_filter=()) -> bool:
    """True if all expected outputs already exist (uses iwqis_site_metadata.csv as a manifest)."""
    if not _metadata_target().exists():
        return False
    keeper_uids = [u for u in pd.read_csv(_metadata_target())["uid"].tolist() if u not in extra_filter]
    all_parquets = all((data_dir() / f"{uid}_water.parquet").exists() for uid in keeper_uids)
    return all_parquets and _measures_target().exists() and _params_target().exists()


def evaluate_uids(sparsity_cutoff, lifespan_cutoff, extra_filter=()):
    """Return (all keeper uids, uids whose parquet still needs writing)."""
    current_uids = {f.name[: -len("_water.parquet")] for f in data_dir().glob("WQ*.parquet")}
    print("Filtering the sites, this may take a minute...", end="", flush=True)
    keep, _ = filter_sites(sparsity_cutoff, lifespan_cutoff, source=_full_data())
    keep = keep[keep.site_uid.isin(extra_filter) == False]
    keep_uids = set(keep.site_uid.unique())
    missing_uids = list(keep_uids.difference(current_uids))
    print("done.")
    return list(keep_uids), missing_uids


def main(api_keys=None, extra_filter=()):
    """Build the IWQIS dataset (per-site parquets + metadata). api_keys unused (no network)."""
    extra_filter = list(extra_filter)
    if _precheck(extra_filter):
        print("IWQIS data already complete, skipping.")
        return

    _cfg = get_config()["iwqis"]
    SPARSITY_CUTOFF, LIFESPAN_CUTOFF = _cfg["sparsity_cutoff"], _cfg["lifespan_cutoff"]
    print("---- Building IWQIS Water Data ----")

    keep_uids, missing_uids = evaluate_uids(SPARSITY_CUTOFF, LIFESPAN_CUTOFF, extra_filter=extra_filter)
    if not missing_uids:
        print("No data missing. Writing metadata.")
    else:
        full_data = _full_data()
        for uid in missing_uids:
            file = data_dir() / f"{str(uid).strip()}_water.parquet"
            print(f"  Saving {file}...", end="", flush=True)
            site_df = full_data[full_data.site_uid == uid].copy()
            site_df["datetime"] = pd.to_datetime(site_df["datetime"], utc=True)
            site_df = site_df.set_index("datetime")
            site_df.to_parquet(file)
            print("done.")

    site_df = get_site_metadata()
    site_df[site_df.uid.isin(keep_uids)].to_csv(_metadata_target(), index=False)
    pd.read_csv(_params_source()).to_csv(_params_target(), index=False)
    pd.read_csv(_measures_source()).to_csv(_measures_target(), index=False)
    print(f"\nIWQIS data build complete. Kept {len(keep_uids)} sites.")


if __name__ == "__main__":
    main(None)
