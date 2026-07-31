"""STEP 5 -- demo: actually pull a non-CORE covariate and align it to the nitrate target.

Proves the recommended change is a one-line edit plus a refetch. Pulls the turbidity family (63680 + 63675 + 72213 -- all three, because a single-pcode query silently drops sites) for one site, coalesces the variants into one column, aggregates to the daily grain the model uses, and reports how many of that site's nitrate-days it actually covers.

    python 05_demo_pull.py --site USGS-05412500 --days 900

USGS-05412500 is a deliberate choice: it carries turbidity under 72213, NOT the common 63680, so it is exactly the site a naive `CORE_PCODES["63680"]` addition would miss.
"""

import argparse
import os
import sys
import tomllib
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from pcodes import FAMILIES

ROOT = HERE.parents[2]
RAW = ROOT / "src/data/raw/water"
KEYS = ROOT / "src/build/api-keys.toml"


def set_pat():
    if KEYS.exists():
        with open(KEYS, "rb") as f:
            os.environ["API_USGS_PAT"] = tomllib.load(f)["usgs"]


def main(site, family="turbidity", days=900):
    from dataretrieval import waterdata

    set_pat()
    pcs = [p for p, (_, k) in FAMILIES[family].items() if k == "insitu"]
    names = {p: n for p, (n, k) in FAMILIES[family].items() if k == "insitu"}
    print(f"site={site}  family={family}  pcodes={pcs}")

    nit = pd.read_parquet(RAW / "USGS" / f"{site}_water.parquet", columns=["nitrate_con"])
    nit = nit["nitrate_con"].dropna()
    end = nit.index.max()
    start = end - pd.Timedelta(days=days)
    print(f"nitrate record {nit.index.min().date()} -> {end.date()}; "
          f"demo window {start.date()} -> {end.date()}")

    df, _ = waterdata.get_continuous(
        monitoring_location_id=site, parameter_code=pcs,
        time=f"{start.date()}/{end.date()}",
    )
    if df is None or df.empty:
        print("no data returned"); return
    print(f"\nraw response: {len(df)} rows, pcodes present: "
          f"{sorted(df.parameter_code.astype(str).unique())}")

    wide = (df.assign(parameter_code=df.parameter_code.astype(str))
              .pivot_table(index="time", columns="parameter_code", values="value", aggfunc="last")
              .rename(columns=names))
    wide.index = pd.to_datetime(wide.index, utc=True)
    # coalesce the variants -- they measure the same quantity with different optics
    cols = [c for c in wide.columns]
    wide[family] = wide[cols].bfill(axis=1).iloc[:, 0]
    print(f"variant columns: {cols} -> coalesced into `{family}`")

    daily = wide[[family]].resample("1D").agg(["max", "mean"])
    daily.columns = [f"{family}_{s}" for s in ("max", "mean")]
    # restrict the target to the SAME window, else the coverage ratio is against the whole record
    nd = nit[(nit.index >= start) & (nit.index <= end)].resample("1D").max().dropna()
    joined = daily.join(nd.rename("nitrate_con"), how="inner")
    cov = joined[f"{family}_max"].notna().sum()
    print(f"\ndaily rows: {len(daily)};  nitrate-days in window: {len(nd)};  "
          f"co-covered days: {cov} ({cov/max(len(nd),1):.0%} of in-window nitrate-days)")
    print(f"\ncorrelation with daily-max nitrate over the window:")
    print(joined.corr(method="spearman")["nitrate_con"].round(3).to_string())
    print(f"\nhead:\n{joined.dropna().head(8).to_string()}")
    out = HERE / "data" / f"demo_{family}_{site}.csv"
    joined.to_csv(out)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="USGS-05412500")
    ap.add_argument("--family", default="turbidity")
    ap.add_argument("--days", type=int, default=900)
    a = ap.parse_args()
    main(a.site, a.family, a.days)
