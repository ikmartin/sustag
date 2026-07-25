"""STEP 2 of the reworked water build: ensure raw nitrate for every candidate is on disk.

Reads raw_site_list.csv (step 1). For each site: if a raw per-site parquet is already present it is left alone; otherwise it is fetched (USGS) or reassembled (IWQIS). Anything that cannot be materialized is REPORTED (non-fatal) so coverage gaps are visible. Raw is stored per-source at NATIVE cadence -- daily-max aggregation is make_water.py's job (step 4).

    raw/water/USGS/{uid}_water.parquet   pulled from the USGS API (dataretrieval; refetchable)
    raw/water/IWQIS/{uid}_water.parquet  split from the git-committed chunks/ (the non-API source)

Phase-1 note: this drives the raw/water/{USGS,IWQIS} output path locally, reusing the low-level pieces of src.build.util.{usgs,iwqis} without editing those modules (they still write to processed/water). Repointing them is deferred to Phase 2.

    python -m src.build.water.download                       # all sources, all missing sites
    python -m src.build.water.download --sources usgs        # one source
    python -m src.build.water.download --only USGS-05412500  # restrict to specific uids
    python -m src.build.water.download --force               # refetch even if present
"""

import argparse
import os
import sys
import tomllib
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]  # repo root
sys.path.insert(0, str(_ROOT))

from src.build.util import iwqis, usgs
from src.build.water._paths import raw_iwqis_dir, raw_site_list_path, raw_usgs_dir

_KEYS_FILE = _ROOT / "src" / "build" / "api-keys.toml"


def _set_usgs_pat() -> None:
    with open(_KEYS_FILE, "rb") as f:
        os.environ["API_USGS_PAT"] = tomllib.load(f)["usgs"]


def _read_site_list(only=None, sources=None) -> pd.DataFrame:
    df = pd.read_csv(raw_site_list_path(), dtype={"site_uid": str})
    if "source" not in df.columns:
        df["source"] = df["site_uid"].str.startswith("USGS-").map({True: "USGS", False: "IWQIS"})
    if sources:
        df = df[df["source"].isin({s.upper() for s in sources})]
    if only:
        df = df[df["site_uid"].isin(set(only))]
    return df


# ── USGS ──────────────────────────────────────────────────────────────────────
def _usgs_metadata(uids: list[str]) -> pd.DataFrame:
    """Time-series metadata (nitrate begin/end per site) for arbitrary USGS uids, via the OGC API."""
    from dataretrieval import waterdata

    res = waterdata.get_time_series_metadata(monitoring_location_id=uids, skip_geometry=True)
    meta = res[0] if isinstance(res, tuple) else res
    return pd.DataFrame(meta)


def _fetch_usgs(uids: list[str], force: bool) -> list[dict]:
    """Fetch missing USGS sites into raw/water/USGS. Returns a per-site status record list."""
    out_dir = raw_usgs_dir()
    todo = [u for u in uids if force or not (out_dir / f"{u}_water.parquet").exists()]
    records = [{"site_uid": u, "source": "USGS", "status": "present", "reason": ""}
               for u in uids if u not in todo]
    if not todo:
        return records

    _set_usgs_pat()
    print(f"  USGS: {len(todo)} site(s) to fetch...")
    meta = _usgs_metadata(todo)
    for uid in todo:
        start, end = usgs.get_lifespan(uid, meta)
        if pd.isna(start) or pd.isna(end):
            records.append({"site_uid": uid, "source": "USGS", "status": "MISSING",
                            "reason": "no nitrate (99133) lifespan in metadata"})
            continue
        chunk = usgs.auto_chunk_size(usgs.params_from_meta(uid, meta))
        wide, _ = usgs.build_site(uid, start=start, end=end, chunk=chunk)
        if wide is None or len(wide) == 0:
            records.append({"site_uid": uid, "source": "USGS", "status": "MISSING",
                            "reason": "API returned no data over the reported lifespan"})
            continue
        wide = wide.rename(columns={"site_id": "site_uid"})
        wide = wide[~wide.index.duplicated(keep="last")].sort_index()
        wide.index.name = "datetime"
        wide.to_parquet(out_dir / f"{uid}_water.parquet")
        records.append({"site_uid": uid, "source": "USGS", "status": "fetched",
                        "reason": f"{wide.shape[0]:,} rows"})
        print(f"    {uid}: wrote {wide.shape[0]:,} rows")
    return records


# ── IWQIS ─────────────────────────────────────────────────────────────────────
def _fetch_iwqis(uids: list[str], force: bool) -> list[dict]:
    """Split the reassembled IWQIS chunks into raw/water/IWQIS for missing sites. Reassembly (a ~42M-row, ~2 min load) runs only if at least one site is missing."""
    out_dir = raw_iwqis_dir()
    todo = [u for u in uids if force or not (out_dir / f"{u}_water.parquet").exists()]
    records = [{"site_uid": u, "source": "IWQIS", "status": "present", "reason": ""}
               for u in uids if u not in todo]
    if not todo:
        return records

    print(f"  IWQIS: {len(todo)} site(s) to reassemble (loading the chunked source)...")
    full = iwqis._full_data()
    have = set(full["site_uid"].astype(str).unique())
    for uid in todo:
        if uid not in have:
            records.append({"site_uid": uid, "source": "IWQIS", "status": "MISSING",
                            "reason": "no rows in the reassembled chunks"})
            continue
        site_df = full[full["site_uid"].astype(str) == uid].copy()
        site_df["datetime"] = pd.to_datetime(site_df["datetime"], utc=True)
        site_df = site_df.set_index("datetime").sort_index()
        site_df.to_parquet(out_dir / f"{uid}_water.parquet")
        records.append({"site_uid": uid, "source": "IWQIS", "status": "fetched",
                        "reason": f"{len(site_df):,} rows"})
        print(f"    {uid}: wrote {len(site_df):,} rows")
    return records


# ── report ──────────────────────────────────────────────────────────────────
def _report(records: list[dict]) -> None:
    rep = pd.DataFrame(records)
    print("\n== DOWNLOAD COVERAGE ==")
    counts = rep.groupby(["source", "status"]).size().unstack(fill_value=0)
    print(counts.to_string())
    missing = rep[rep["status"] == "MISSING"]
    if len(missing):
        print(f"\n  {len(missing)} site(s) MISSING (no raw data available):")
        for _, r in missing.iterrows():
            print(f"    {r.site_uid:<18} [{r.source}] {r.reason}")
    else:
        print("\n  all candidate sites have raw data on disk.")


def download(only=None, sources=None, force=False) -> pd.DataFrame:
    sites = _read_site_list(only=only, sources=sources)
    usgs_ids = sorted(sites.loc[sites["source"] == "USGS", "site_uid"])
    iwqis_ids = sorted(sites.loc[sites["source"] == "IWQIS", "site_uid"])
    print(f"Checking raw coverage for {len(sites)} site(s): {len(usgs_ids)} USGS + {len(iwqis_ids)} IWQIS")

    records = []
    if usgs_ids:
        records += _fetch_usgs(usgs_ids, force)
    if iwqis_ids:
        records += _fetch_iwqis(iwqis_ids, force)
    _report(records)
    return pd.DataFrame(records)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", help="comma-separated subset: usgs,iwqis (default both)")
    ap.add_argument("--only", help="comma-separated site_uids to restrict to")
    ap.add_argument("--force", action="store_true", help="refetch even if a raw parquet is present")
    args = ap.parse_args()
    download(
        only=args.only.split(",") if args.only else None,
        sources=args.sources.split(",") if args.sources else None,
        force=args.force,
    )
