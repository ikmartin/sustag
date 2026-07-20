"""Build site_location_metadata.csv from USGS and IWQIS metadata.

Combines USGS site coordinates (authoritative, parsed from the metadata geometry) with the
filtered IWQIS site list to produce the single location table the access layer reads. Faithful
port of data/water/make_site_locations.py with new paths.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root on path
from src.build.util._water_paths import meta_dir, raw_dir


def create_site_locations() -> None:
    md = meta_dir()
    usgs_metadata = pd.read_csv(md / "usgs_site_metadata.csv")
    site_clean = pd.read_csv(raw_dir() / "site_clean.csv")

    usgs_metadata = usgs_metadata.rename(columns={"state_name": "state", "monitoring_location_id": "uid"})

    coords = usgs_metadata["geometry"].str.extract(r"POINT \(([-\d.]+) ([-\d.]+)\)").astype(float)
    usgs_metadata["longitude"] = coords[0]
    usgs_metadata["latitude"] = coords[1]
    usgs_metadata = usgs_metadata.drop(columns=["geometry"])

    uid_overlap = [
        uid for uid in usgs_metadata.uid.unique()
        if (site_clean.uid == str(uid).replace("USGS-", "")).any()
    ]
    uid_diff = set(usgs_metadata.uid.unique()).difference(set(uid_overlap))

    for uid in uid_overlap:
        site_clean.loc[site_clean.uid == str(uid).replace("USGS-", ""), "uid"] = uid
        lat = float(usgs_metadata.loc[usgs_metadata.uid == uid, "latitude"].iloc[0])
        lon = float(usgs_metadata.loc[usgs_metadata.uid == uid, "longitude"].iloc[0])
        site_clean.loc[site_clean.uid == uid, "latitude"] = lat
        site_clean.loc[site_clean.uid == uid, "longitude"] = lon

    keeper_ids = pd.read_csv(md / "iwqis_site_metadata.csv").uid.unique().tolist()
    site_clean = site_clean[site_clean.uid.isin(keeper_ids + uid_overlap)]

    new_rows = usgs_metadata[usgs_metadata.uid.isin(uid_diff)][
        ["state", "uid", "latitude", "longitude"]
    ].drop_duplicates()

    new_df = pd.concat([site_clean, new_rows], ignore_index=True)
    new_df = new_df.rename(columns={"uid": "site_uid"})
    new_df.to_csv(md / "site_location_metadata.csv", index=False)


if __name__ == "__main__":
    create_site_locations()
    print(f"Wrote {meta_dir() / 'site_location_metadata.csv'}")
