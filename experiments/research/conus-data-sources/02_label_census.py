"""G3 -- how many nitrate-labelled sites exist CONUS-wide, under the CORRECTED pcode set.

The build discovers on 99133 alone (`src/build/water/fetch_sites.py:34`). USGS codes continuous nitrate under three pcodes and the choice is a network-by-network convention, not a data property: 99133 returns ZERO Nebraska sites and 00630 returns five. Querying one code silently deletes whole state networks.

Also resolves site TYPE, because a raw count is not a label count -- the endpoint returns lakes, canals, wells and ditches alongside streams, and Pandit 2025 dropped 155 -> 95 on exactly this filter.

Writes data/label_census.csv (per site: pcodes, span, state, type) and prints the census.

    python experiments/research/conus-data-sources/02_label_census.py
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
_OUT = Path(__file__).resolve().parent / "data" / "label_census.csv"

CONUS_BBOX = [-125.0, 24.0, -66.5, 49.5]
NITRATE_PCODES = ["99133", "99137", "00630"]  # continuous. 83554 is a LOAD -- excluded on purpose.
CORN_BELT = ["Iowa", "Illinois", "Minnesota", "Indiana", "Ohio", "Missouri",
             "Nebraska", "South Dakota", "Wisconsin", "Kansas", "Michigan", "North Dakota"]
_RIVERINE_PREFIX = ("ST",)  # USGS site_type_code: ST, ST-CA, ST-DCH, ST-TS


def _auth() -> None:
    with open(_ROOT / "src" / "build" / "api-keys.toml", "rb") as f:
        os.environ["API_USGS_PAT"] = tomllib.load(f)["usgs"]


def census() -> pd.DataFrame:
    from dataretrieval import waterdata

    frames = []
    for pc in NITRATE_PCODES:
        res = waterdata.get_time_series_metadata(parameter_code=pc, bbox=CONUS_BBOX, skip_geometry=True)
        df = res[0] if isinstance(res, tuple) else res
        df["pcode"] = pc
        frames.append(df)
        print(f"  pcode {pc}: {len(df):>5} series, {df.monitoring_location_id.nunique():>4} sites")
    a = pd.concat(frames, ignore_index=True)
    a["begin"] = pd.to_datetime(a["begin"], errors="coerce", utc=True)
    a["end"] = pd.to_datetime(a["end"], errors="coerce", utc=True)
    a["yrs"] = (a["end"] - a["begin"]).dt.days / 365.25

    per = (a.groupby("monitoring_location_id")
           .agg(yrs=("yrs", "max"), first=("begin", "min"), last=("end", "max"),
                state=("state_name", "first"),
                pcodes=("pcode", lambda s: "+".join(sorted(set(s)))))
           .reset_index())

    # site type -- the endpoint mixes streams with lakes, wells, canals and ditches
    ml = waterdata.get_monitoring_locations(monitoring_location_id=per.monitoring_location_id.tolist())
    ml = ml[0] if isinstance(ml, tuple) else ml
    tcol = next((c for c in ml.columns if "site_type" in c.lower()), None)
    if tcol:
        per = per.merge(ml[["monitoring_location_id", tcol]].rename(columns={tcol: "site_type"}),
                        on="monitoring_location_id", how="left")
    else:
        per["site_type"] = None
    per["riverine"] = per.site_type.fillna("").str.upper().str.startswith(_RIVERINE_PREFIX)
    return per


def main() -> None:
    _auth()
    print("=== USGS continuous nitrate, CONUS ===")
    per = census()

    only_99133 = set(per[per.pcodes.str.contains("99133")].monitoring_location_id)
    print(f"\n  99133 alone           : {len(only_99133):4d} sites")
    print(f"  corrected pcode union : {len(per):4d} sites  (+{len(per) - len(only_99133)} recovered)")
    ne = per[per.state == "Nebraska"]
    print(f"  Nebraska: {len(ne)} sites, all under {sorted(set(ne.pcodes))} -- 99133 finds none")

    riv = per[per.riverine]
    print(f"\n  riverine only         : {len(riv):4d} sites  ({len(per) - len(riv)} lakes/wells/canals dropped)")
    print(f"  site_type breakdown   : {per.site_type.value_counts().head(8).to_dict()}")

    print("\n  record span, riverine:")
    for t in (1, 2, 3, 5, 10):
        print(f"    >= {t:2d} yr: {int((riv.yrs >= t).sum()):4d}")
    print(f"    median {riv.yrs.median():.2f} yr; still reporting {int((riv['last'] >= pd.Timestamp('2025-01-01', tz='UTC')).sum())}")

    cb = riv[riv.state.isin(CORN_BELT)]
    print(f"\n  corn belt riverine    : {len(cb):4d} sites  (>= 3 yr: {int((cb.yrs >= 3).sum())})")
    print(f"  top states: {riv.state.value_counts().head(10).to_dict()}")

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    per.to_csv(_OUT, index=False)
    print(f"\nwrote {_OUT.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
