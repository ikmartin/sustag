"""STEP 4 -- discrete (lab / grab) sample coverage at our USGS sites and their co-located gauges.

Continuous sensors are the primary focus, but several literature-backed covariates have NO in-situ pcode at all in our region -- chloride and the reduced-N pool (ammonium, TKN) are lab-only (script 01: `sites_union = 0` for both). Those can only arrive as discrete samples, on a different API (the Samples service, not the time-series service).

Discrete data cannot form a daily series, so it is NOT a drop-in feature for the daily model. Its realistic uses are (a) a per-site static summary -- e.g. a long-term median Cl:NO3 ratio as a static basin descriptor, the same shape as the tot_* COMID attributes that already work, and (b) the site-level exceedance-RATE track that future-work R4/R8 describes. Both are stated in REPORT.md section 5.3; this script only measures what exists.

    python 04_lab_samples.py [--refresh]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

ROOT = HERE.parents[2]
RAW = ROOT / "src/data/raw/water"
DATA = HERE / "data"
CACHE = DATA / "samples_summary.parquet"

# characteristics worth pulling, keyed to the families in pcodes.py that are lab-only or lab-mostly
WANTED = {
    "Chloride": "chloride",
    "Ammonia": "reduced_n",
    "Ammonia and ammonium": "reduced_n",
    "Kjeldahl nitrogen": "reduced_n",
    "Organic Nitrogen": "reduced_n",
    "Inorganic nitrogen (nitrate and nitrite)": "nitrate_grab",
    "Nitrate": "nitrate_grab",
    "Total Nitrogen, mixed forms": "total_n",
    "Phosphorus": "phosphorus",
    "Orthophosphate": "phosphorus",
    "Suspended Sediment Concentration (SSC)": "sediment",
    "Total suspended solids": "sediment",
    "Organic carbon": "doc",
    "Specific conductance": "spec_cond",
    "pH": "ph",
    "Turbidity": "turbidity",
    "Silica": "silica",
}


def target_sites() -> list[str]:
    good = json.load(open(RAW / "filtered_sites.json"))["good_sites"]
    ours = sorted(u for u in good if u.startswith("USGS-"))
    extra = []
    f = DATA / "colocated_gauges.csv"
    if f.exists():
        extra = sorted(set(pd.read_csv(f).monitoring_location_id) - set(ours))
    return ours, extra


def fetch(sites, refresh=False) -> pd.DataFrame:
    from dataretrieval import waterdata

    if CACHE.exists() and not refresh:
        df = pd.read_parquet(CACHE)
        if set(sites) <= set(df.monitoringLocationIdentifier.unique()) | set(df.attrs.get("empty", [])):
            return df
    frames, empty = [], []
    for i, uid in enumerate(sites, 1):
        try:
            d, _ = waterdata.get_samples_summary(uid)
            if d is not None and len(d):
                frames.append(d)
            else:
                empty.append(uid)
        except Exception as e:
            empty.append(uid)
            print(f"  [warn] {uid}: {type(e).__name__}")
        if i % 20 == 0:
            print(f"  ... {i}/{len(sites)}")
        time.sleep(0.15)
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    df.attrs["empty"] = empty
    df.to_parquet(CACHE)
    return df


def main(refresh=False):
    ours, extra = target_sites()
    sites = ours + extra
    print(f"querying Samples summary for {len(sites)} sites "
          f"({len(ours)} of ours + {len(extra)} co-located gauges)")
    df = fetch(sites, refresh)
    if df.empty:
        print("no discrete samples returned"); return
    df["resultCount"] = pd.to_numeric(df.resultCount, errors="coerce").fillna(0)
    df["family"] = df.characteristic.map(WANTED)
    df["is_ours"] = df.monitoringLocationIdentifier.isin(set(ours))
    keep = df[df.family.notna()].copy()

    print(f"\n{len(df)} summary rows; {keep.characteristic.nunique()} of the wanted "
          f"characteristics present\n")

    agg = (keep.groupby(["family", "is_ours"])
               .agg(n_sites=("monitoringLocationIdentifier", "nunique"),
                    median_results=("resultCount", "median"),
                    total_results=("resultCount", "sum"))
               .reset_index()
               .pivot(index="family", columns="is_ours",
                      values=["n_sites", "median_results"]))
    agg.columns = [f"{a}_{'ours' if b else 'coloc'}" for a, b in agg.columns]
    agg = agg.fillna(0).astype(int).sort_values("n_sites_ours", ascending=False)
    print("=== discrete-sample coverage by covariate family ===")
    print("n_sites_ours = our USGS good sites with any sample; _coloc = co-located gauges\n")
    print(agg.to_string())
    agg.to_csv(DATA / "lab_sample_coverage.csv")

    # per-site richness for the lab-only families
    print("\n=== sites with >=30 samples, lab-only families (chloride / reduced_n / doc) ===")
    lab_only = keep[keep.family.isin(["chloride", "reduced_n", "doc"]) & (keep.resultCount >= 30)]
    t = (lab_only.groupby(["family", "monitoringLocationIdentifier"])
                 .agg(results=("resultCount", "sum"),
                      first=("firstActivity", "min"), last=("mostRecentActivity", "max"),
                      ours=("is_ours", "first"))
                 .reset_index().sort_values(["family", "results"], ascending=[True, False]))
    print(t.head(40).to_string(index=False))
    t.to_csv(DATA / "lab_sample_sites.csv", index=False)
    print(f"\nwrote {DATA/'lab_sample_coverage.csv'}, {DATA/'lab_sample_sites.csv'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    main(**vars(ap.parse_args()))
