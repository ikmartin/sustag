"""Build the IWQIS nitrate data (faithful port of data/water/make_iwqis_data.py).

PECULIAR SOURCE: IWQIS is NOT an API. The original download (iwqis_alldata.csv) was chunked with
split_csv.py into chunks/ (to fit on GitHub) and its site.csv hand-fixed to site_clean.csv (a
bad quote on row 63). This reassembles the chunked CSV via the manifest and writes one
{uid}_water.parquet per data-bearing WQS candidate + the metadata CSVs. Quality filtering
(sparsity/lifespan) was extracted to filter_sites.py (step 3); this step just materializes data.

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


_WQS_RE = r"WQS\d+"


def _data_uids(full_data) -> list[str]:
    """WQS-candidate uids that actually appear in the reassembled data. This is the pull set --
    quality filtering (sparsity/lifespan) is filter_sites.py, step 3."""
    uids = pd.Series(full_data["site_uid"].astype(str).unique())
    return sorted(uids[uids.str.match(_WQS_RE, na=False)])


def _precheck() -> bool:
    """True if the last build's outputs are all present, so we can skip the 3.1 GB reassembly.

    Manifest = iwqis_site_metadata.csv, which now lists the data-bearing WQS candidates (not the
    kept set -- keeping moved to filter_sites). Skipping is safe: the final kept set is a subset of
    what is on disk, so filter_sites still prunes to the correct result.
    """
    if not (_metadata_target().exists() and _measures_target().exists() and _params_target().exists()):
        return False
    manifest = pd.read_csv(_metadata_target(), dtype={"uid": str})["uid"].tolist()
    return all((data_dir() / f"{uid}_water.parquet").exists() for uid in manifest)


def main(api_keys=None):
    """Build the IWQIS dataset. STEP 2b. api_keys unused (no network).

    Writes a parquet for EVERY data-bearing WQS candidate (no keep-filter -- that is filter_sites).
    """
    if _precheck():
        print("IWQIS data already complete, skipping.")
        return

    print("---- Building IWQIS Water Data ----")
    full_data = _full_data()
    data_uids = _data_uids(full_data)
    current = {f.name[: -len("_water.parquet")] for f in data_dir().glob("WQ*.parquet")}
    missing = [u for u in data_uids if u not in current]
    print(f"{len(data_uids)} WQS candidates with data; {len(missing)} parquet(s) to write.")

    for uid in missing:
        file = data_dir() / f"{uid}_water.parquet"
        print(f"  Saving {file.name}...", end="", flush=True)
        site_df = full_data[full_data.site_uid == uid].copy()
        site_df["datetime"] = pd.to_datetime(site_df["datetime"], utc=True)
        site_df = site_df.set_index("datetime")
        site_df.to_parquet(file)
        print("done.")

    # Manifest = data-bearing WQS candidates (a build manifest, not the kept set).
    site_df = get_site_metadata()
    site_df[site_df.uid.isin(data_uids)].to_csv(_metadata_target(), index=False)
    pd.read_csv(_params_source()).to_csv(_params_target(), index=False)
    pd.read_csv(_measures_source()).to_csv(_measures_target(), index=False)
    print(f"\nIWQIS data build complete. {len(data_uids)} data-bearing WQS candidates.")


if __name__ == "__main__":
    main(None)
