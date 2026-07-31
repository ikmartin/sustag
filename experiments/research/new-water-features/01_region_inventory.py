"""STEP 1 -- what continuous covariate series exist anywhere inside the covariate bbox?

Pulls every continuous time-series' metadata in [region].covariate_bbox from the USGS OGC API (metadata needs no API key), joins the national parameter-code dictionary, and reports coverage per pcode and per covariate family. This is the denominator for everything downstream: it says what the network *could* give us in-region, before asking who is near one of our sites.

    python 01_region_inventory.py            # cached after first run
    python 01_region_inventory.py --refresh
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))  # repo root

from pcodes import CORE_PCODES, RATIONALE, pcode_family, pcode_kind, pcode_name
from src.build.config import get_covariate_bbox

DATA = HERE / "data"
DATA.mkdir(exist_ok=True)
TS_META = DATA / "region_ts_metadata.parquet"
PC_REF = DATA / "usgs_parameter_codes.csv"

# NOTE on thresholds: the project's site bar is now MIN_NITRATE_ROWS = 10_000 non-nan nitrate
# observations (src/build/water/filter_sites.py) -- a data-VOLUME floor that replaced the old
# 3.92 yr lifespan + 0.5 sparsity cutoffs. That bar governs the TARGET series and does not
# transfer to a covariate: what makes a covariate usable is how much it OVERLAPS the nitrate
# record at the same site (scripts 02/03 measure exactly that). Region-wide we have only
# begin/end metadata, so this script reports scale descriptively -- total sites and still-active
# sites -- and applies no record-length filter at all.
ACTIVE_YEAR = 2020  # "still reporting", matching notes/new-site-report.md's funnel


def fetch(refresh=False) -> tuple[pd.DataFrame, pd.DataFrame]:
    from dataretrieval import waterdata

    bbox = list(get_covariate_bbox())
    if refresh or not TS_META.exists():
        print(f"fetching continuous time-series metadata for bbox {bbox} ...")
        df, _ = waterdata.get_time_series_metadata(bbox=bbox, skip_geometry=False, limit=10000)
        df.to_parquet(TS_META)
        print(f"  {len(df)} series -> {TS_META.name}")
    if refresh or not PC_REF.exists():
        print("fetching USGS parameter-code dictionary ...")
        pc, _ = waterdata.get_reference_table("parameter-codes", max_rows=50000)
        pc.to_csv(PC_REF, index=False)
        print(f"  {len(pc)} pcodes -> {PC_REF.name}")
    ts = pd.read_parquet(TS_META)
    pc = pd.read_csv(PC_REF, dtype={"parameter_code": str})
    ts["parameter_code"] = ts["parameter_code"].astype(str)
    for c in ("begin", "end"):
        ts[c] = pd.to_datetime(ts[c], errors="coerce", utc=True)
    ts["span_yr"] = (ts["end"] - ts["begin"]).dt.days / 365.25
    return ts, pc


def main(refresh=False):
    ts, pc = fetch(refresh)
    fam, kind, name = pcode_family(), pcode_kind(), pcode_name()
    print(f"\nregion: {len(ts)} series · {ts.monitoring_location_id.nunique()} sites · "
          f"{ts.parameter_code.nunique()} distinct pcodes\n")

    g = ts.groupby("parameter_code").agg(
        n_sites=("monitoring_location_id", "nunique"),
        param=("parameter_name", "first"),
    )
    act = ts[ts.end.dt.year >= ACTIVE_YEAR].groupby("parameter_code").monitoring_location_id.nunique()
    g["n_sites_active"] = act.reindex(g.index).fillna(0).astype(int)
    g["family"] = [fam.get(p, "-") for p in g.index]
    g["in_core"] = [p in CORE_PCODES for p in g.index]
    g = g.sort_values("n_sites", ascending=False)
    g.to_csv(DATA / "region_pcode_inventory.csv")

    known = g[g.family != "-"]
    print(f"=== registry pcodes present in-region (n_sites_active = still reporting >= {ACTIVE_YEAR}) ===")
    print(known[["family", "param", "n_sites", "n_sites_active", "in_core"]]
          .to_string(max_colwidth=32))

    print("\n=== by FAMILY (union across that family's pcodes -- the point of the registry) ===")
    ts["family"] = ts.parameter_code.map(fam)
    ts["kind"] = ts.parameter_code.map(kind)
    fam_rows = []
    for f, sub in ts[ts.family.notna()].groupby("family"):
        ins = sub[sub.kind == "insitu"]  # continuous only -- union and best-single must agree on scope
        best = ins.groupby("parameter_code").monitoring_location_id.nunique().sort_values(ascending=False)
        fam_rows.append(dict(
            family=f,
            n_pcodes_insitu=ins.parameter_code.nunique(),
            sites_union=ins.monitoring_location_id.nunique(),
            sites_best_single=int(best.iloc[0]) if len(best) else 0,
            sites_union_active=ins[ins.end.dt.year >= ACTIVE_YEAR].monitoring_location_id.nunique(),
            all_in_core=all(p in CORE_PCODES for p in ins.parameter_code.unique()) if len(ins) else False,
        ))
    fr = pd.DataFrame(fam_rows).sort_values("sites_union", ascending=False)
    fr["union_gain"] = fr.sites_union - fr.sites_best_single
    print(fr.to_string(index=False))
    fr.to_csv(DATA / "region_family_inventory.csv", index=False)
    print("\n`union_gain` = extra in-region sites found by querying the whole family instead of\n"
          "the single most common pcode -- i.e. what a CORE-style single-pcode query misses.")
    print(f"\nwrote {DATA/'region_pcode_inventory.csv'} and {DATA/'region_family_inventory.csv'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    main(**vars(ap.parse_args()))
