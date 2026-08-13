"""What is already downloaded and on disk, and thrown away at read time.

Every covariate column in raw/water/{USGS,IWQIS}/*.parquet is scored by DAYS OVERLAPPING THAT SITE'S OWN NITRATE RECORD, not by raw row count -- a 20-year turbidity series at a site whose nitrate sensor ran 2019-2023 is worth 4 years, not 20. `src/build/water/_paths.py:70,80` selects ['datetime','nitrate_con'] and drops the rest, so every column below is a download already paid for.

Writes data/held_but_discarded.csv (per column: sites offering it, sites with >=1yr nitrate overlap, median overlap years, total overlapping days).

    python experiments/research/conus-data-sources/01_held_but_discarded.py
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
_RAW = _ROOT / "src" / "data" / "raw" / "water"
_OUT = Path(__file__).resolve().parent / "data" / "held_but_discarded.csv"

_NOT_A_COVARIATE = {"site_uid", "datetime", "nitrate_con"}


def _overlap_days(df: pd.DataFrame, col: str) -> int:
    """Days where BOTH nitrate and `col` are observed at this site."""
    both = df[["nitrate_con", col]].notna().all(axis=1)
    if not both.any():
        return 0
    return int(df.loc[both].index.normalize().nunique())


def scan(source: str) -> pd.DataFrame:
    rows = []
    files = sorted(glob.glob(str(_RAW / source / "*.parquet")))
    for f in files:
        uid = Path(f).stem.replace("_water", "")
        df = pd.read_parquet(f)
        dt = next((c for c in df.columns if "datetime" in c.lower() or "date" in c.lower()), None)
        if dt is not None:
            df = df.set_index(pd.to_datetime(df[dt]))
        elif not isinstance(df.index, pd.DatetimeIndex):
            continue
        if "nitrate_con" not in df.columns:
            continue
        n_days = int(df.loc[df.nitrate_con.notna()].index.normalize().nunique())
        for col in df.columns:
            if col in _NOT_A_COVARIATE or col == dt:
                continue
            if df[col].notna().sum() == 0:
                continue
            rows.append(dict(source=source, site_uid=uid, column=col,
                             nitrate_days=n_days, overlap_days=_overlap_days(df, col)))
    return pd.DataFrame(rows)


def main() -> None:
    per_site = pd.concat([scan("USGS"), scan("IWQIS")], ignore_index=True)
    if per_site.empty:
        sys.exit("no raw water parquets found")
    per_site["overlap_yr"] = per_site.overlap_days / 365.25

    agg = (per_site.groupby(["source", "column"])
           .agg(sites_offering=("site_uid", "nunique"),
                sites_ge_1yr=("overlap_yr", lambda s: int((s >= 1.0).sum())),
                median_overlap_yr=("overlap_yr", "median"),
                max_overlap_yr=("overlap_yr", "max"),
                total_overlap_days=("overlap_days", "sum"))
           .reset_index()
           .sort_values(["source", "total_overlap_days"], ascending=[True, False]))
    agg["median_overlap_yr"] = agg.median_overlap_yr.round(2)
    agg["max_overlap_yr"] = agg.max_overlap_yr.round(2)

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    agg.to_csv(_OUT, index=False)

    for src in ("USGS", "IWQIS"):
        sub = agg[agg.source == src]
        print(f"\n=== {src} — held on disk, discarded at src/build/water/_paths.py:80 ===")
        print(f"{'column':22} {'sites':>6} {'>=1yr':>6} {'med_yr':>7} {'max_yr':>7} {'ovl_days':>9}")
        for _, r in sub.iterrows():
            print(f"{r.column:22} {r.sites_offering:6d} {r.sites_ge_1yr:6d} "
                  f"{r.median_overlap_yr:7.2f} {r.max_overlap_yr:7.2f} {r.total_overlap_days:9,d}")
    print(f"\nwrote {_OUT.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
