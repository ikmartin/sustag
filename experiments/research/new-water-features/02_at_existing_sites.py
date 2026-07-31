"""STEP 2 -- which registry covariates can be pulled from the sites we ALREADY have?

This is the cheapest expansion available: no new sites, no new basins, no new LOFO families -- just a wider pcode list in src/build/util/usgs.py:CORE_PCODES and a refetch.

Scoring. The project's site bar (MIN_NITRATE_ROWS = 10_000 non-nan nitrate rows) governs the TARGET and does not transfer to a covariate. A covariate is useful in proportion to how much it
**overlaps the nitrate record at the same site** -- a 20-year turbidity series at a site whose
sensor ran 2019-2023 contributes exactly those 4 years. So every covariate here is scored by `overlap_yr` = |[covariate begin, end] intersect [that site's nitrate begin, end]|, taken from the site's real on-disk nitrate parquet. No absolute record-length cutoff is applied; the distribution is reported and MIN_OVERLAP_YR is only a display threshold.

Columns:
    offered   the site publishes a continuous series for the family (any of its pcodes)
    usable    ... with overlap_yr >= MIN_OVERLAP_YR against that site's own nitrate record
    on_disk   ... and we actually have the column in raw/water/USGS/{uid}_water.parquet
    gap       usable - on_disk  == free data currently left on the table

    python 02_at_existing_sites.py [--min-overlap 1.0]
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from pcodes import CORE_PCODES, FAMILIES, pcode_family, pcode_kind

ROOT = HERE.parents[2]
RAW = ROOT / "src/data/raw/water"
DATA = HERE / "data"


def good_usgs() -> list[str]:
    good = json.load(open(RAW / "filtered_sites.json"))["good_sites"]
    return sorted(u for u in good if u.startswith("USGS-"))


def nitrate_window_and_cols(uid: str):
    """((first, last) of the site's non-nan nitrate record, set of populated columns)."""
    f = RAW / "USGS" / f"{uid}_water.parquet"
    if not f.exists():
        return None, set()
    d = pd.read_parquet(f)
    cols = {c for c in d.columns if d[c].notna().any()}
    if "nitrate_con" not in d.columns:
        return None, cols
    n = d["nitrate_con"].dropna()
    if n.empty:
        return None, cols
    idx = n.index
    return (idx.min(), idx.max()), cols


def main(min_overlap=1.0):
    sites = good_usgs()
    ts = pd.read_parquet(DATA / "region_ts_metadata.parquet")
    ts["parameter_code"] = ts["parameter_code"].astype(str)
    for c in ("begin", "end"):
        ts[c] = pd.to_datetime(ts[c], errors="coerce", utc=True)
    fam, kind = pcode_family(), pcode_kind()
    ts["family"] = ts.parameter_code.map(fam)
    ts["kind"] = ts.parameter_code.map(kind)
    ours = ts[ts.monitoring_location_id.isin(sites) & (ts.kind == "insitu") & ts.family.notna()].copy()

    name2fam = {n: f for f, d in FAMILIES.items() for (n, _) in d.values()}
    win, disk = {}, {}
    for uid in sites:
        w, cols = nitrate_window_and_cols(uid)
        win[uid] = w
        disk[uid] = {name2fam[c] for c in cols if c in name2fam}

    # overlap of each covariate series with that site's own nitrate record
    def overlap_yr(row):
        w = win.get(row.monitoring_location_id)
        if w is None or pd.isna(row.begin) or pd.isna(row.end):
            return float("nan")
        lo, hi = max(row.begin, w[0]), min(row.end, w[1])
        return max((hi - lo).days, 0) / 365.25

    ours["overlap_yr"] = ours.apply(overlap_yr, axis=1)
    print(f"{len(sites)} USGS good sites; nitrate window resolved for "
          f"{sum(v is not None for v in win.values())}\n")

    rows = []
    for f, sub in ours.groupby("family"):
        offered = set(sub.monitoring_location_id)
        usable = set(sub[sub.overlap_yr >= min_overlap].monitoring_location_id)
        have = {u for u in sites if f in disk.get(u, set())}
        rows.append(dict(
            family=f, offered=len(offered), usable=len(usable), on_disk=len(have),
            gap=len(usable - have),
            med_overlap_yr=round(float(sub.overlap_yr.median()), 1),
            in_core=all(p in CORE_PCODES for p in sub.parameter_code.unique()),
            pcodes="+".join(sorted(sub.parameter_code.unique())),
        ))
    r = pd.DataFrame(rows).sort_values(["gap", "usable"], ascending=False)
    print(f"=== continuous covariates AT our existing USGS good sites "
          f"(usable = overlap >= {min_overlap:g} yr with that site's nitrate) ===\n")
    print(r.to_string(index=False))
    r.to_csv(DATA / "existing_site_availability.csv", index=False)

    act = r[(r.gap > 0) & (~r.in_core)].family.tolist()
    print(f"\n=== per-site detail, actionable families {act} (usable only) ===")
    det = ours[ours.family.isin(act) & (ours.overlap_yr >= min_overlap)]
    det = (det.groupby(["family", "monitoring_location_id"])
              .agg(pcode=("parameter_code", lambda s: "+".join(sorted(set(s)))),
                   overlap_yr=("overlap_yr", "max"), end=("end", "max"))
              .reset_index().sort_values(["family", "overlap_yr"], ascending=[True, False]))
    det["end"] = det["end"].dt.date
    det["on_disk"] = [f in disk.get(u, set()) for f, u in zip(det.family, det.monitoring_location_id)]
    det["overlap_yr"] = det.overlap_yr.round(1)
    print(det.to_string(index=False))
    det.to_csv(DATA / "existing_site_detail.csv", index=False)
    print(f"\nwrote {DATA/'existing_site_availability.csv'}, {DATA/'existing_site_detail.csv'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-overlap", type=float, default=1.0, help="display threshold, years")
    main(min_overlap=ap.parse_args().min_overlap)
