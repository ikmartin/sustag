"""STEP 4 of the reworked water build: build the per-site DAILY-MAX nitrate product for the good sites and write the downstream contract.

Reads `good_sites` from filtered_sites.json (step 3) and the native raw series from raw/water/{USGS,IWQIS} (step 2). For each good site it aggregates the native nitrate series to DAILY MAX and writes one parquet under processed/water/data. It also writes the contract file site_location_metadata.csv (site_uid + coords + descriptive fields) that the make_features pipeline (basins, aux) and the runtime access layer read, plus site_statistics.csv.

Daily-max is the stored resolution ON PURPOSE: the modeling target becomes daily-max nitrate. Wiring recipes/the nitrate cache to consume it is a Phase-2 change outside this directory.

    python -m src.build.water.make_water
    python -m src.build.water.make_water --out /tmp/water_stage --limit 5   # safe smoke, no clobber
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]  # repo root
sys.path.insert(0, str(_ROOT))

from src.build.util._water_paths import data_dir as _proc_data_dir
from src.build.util._water_paths import meta_dir as _proc_meta_dir
from src.build.util.site_metadata import CANONICAL, STR_COLS
from src.build.water._paths import filtered_sites_path, raw_site_list_path, read_clean_or_raw_series


def _out_dirs(out_root: str | None) -> tuple[Path, Path]:
    """(<data dir>, <meta dir>). Default = the real processed/water; --out redirects for a safe smoke that never touches the tracked dataset."""
    if out_root is None:
        return _proc_data_dir(), _proc_meta_dir()
    root = Path(out_root)
    data, meta = root / "data", root / "meta"
    data.mkdir(parents=True, exist_ok=True)
    meta.mkdir(parents=True, exist_ok=True)
    return data, meta


def _daily_max(uid: str) -> pd.DataFrame | None:
    """The site's native nitrate series aggregated to one daily-max value per measured day. Uses the SCRUBBED interim series where filter_sites wrote one (removed points are NaN, so daily-max skips them), raw otherwise."""
    s = read_clean_or_raw_series(uid)
    if s is None or s["nitrate_con"].notna().sum() == 0:
        return None
    daily = (
        s.dropna(subset=["nitrate_con"])
        .set_index("datetime")["nitrate_con"]
        .resample("1D").max()
        .dropna()
    )
    out = daily.to_frame("nitrate_con")
    out.insert(0, "site_uid", uid)
    out.index.name = "datetime"
    return out


def _write_contract(good: list[str], meta_dir: Path) -> pd.DataFrame:
    """Filter raw_site_list.csv to the good set, reindex to CANONICAL, and write the contract file."""
    cand = pd.read_csv(raw_site_list_path(), dtype={c: str for c in STR_COLS})
    final = (
        cand[cand["site_uid"].isin(set(good))]
        .reindex(columns=CANONICAL)  # drop is_groundwater/source; final schema == CANONICAL
        .sort_values("site_uid", ignore_index=True)  # load-bearing: holdout determinism
    )
    for c in STR_COLS:
        if c in final.columns:
            final[c] = final[c].astype("object").where(final[c].notna(), None)
    missing = final[final[["latitude", "longitude"]].isna().any(axis=1)]
    if len(missing):
        raise RuntimeError("Good sites with no coordinates: " + ", ".join(missing["site_uid"]))
    final.to_csv(meta_dir / "site_location_metadata.csv", index=False)
    return final


def _write_statistics(built: dict[str, pd.DataFrame], meta_dir: Path) -> pd.DataFrame:
    """Per-site date-range/sparsity stats over the built daily-max frames (schema matches the old gen_statistics: nitrate_sparsity, start_date, last_date, lifespan)."""
    rows = []
    for uid, df in built.items():
        idx = df.index
        first, last = idx.min(), idx.max()
        lifespan = (last - first).total_seconds() / (365.25 * 24 * 3600) if len(idx) > 1 else 0.0
        # daily-max product has one row per measured day; sparsity = measured days / calendar days.
        cal_days = (last - first).days + 1 if len(idx) > 1 else len(idx)
        rows.append({
            "site_uid": uid,
            "nitrate_sparsity": round(len(idx) / cal_days, 4) if cal_days else float("nan"),
            "start_date": first,
            "last_date": last,
            "lifespan": round(lifespan, 4),
        })
    stats = pd.DataFrame(rows).sort_values("site_uid", ignore_index=True)
    stats.to_csv(meta_dir / "site_statistics.csv", index=False)
    return stats


def make_water(out_root: str | None = None, limit: int | None = None) -> None:
    import json

    with open(filtered_sites_path()) as f:
        good = sorted(json.load(f)["good_sites"])
    if limit:
        good = good[:limit]
    data_dir, meta_dir = _out_dirs(out_root)
    print(f"Building daily-max water for {len(good)} good site(s) -> {data_dir}")

    built, missing = {}, []
    for uid in good:
        df = _daily_max(uid)
        if df is None:
            missing.append(uid)
            continue
        df.to_parquet(data_dir / f"{uid}_water.parquet")
        built[uid] = df
    if missing:
        print(f"  WARNING: {len(missing)} good site(s) had no raw data, skipped: {missing}")

    final = _write_contract(sorted(built), meta_dir)
    stats = _write_statistics(built, meta_dir)
    print(f"  wrote {len(built)} daily-max parquet(s)")
    print(f"  wrote {meta_dir / 'site_location_metadata.csv'}  ({len(final)} sites)")
    print(f"  wrote {meta_dir / 'site_statistics.csv'}  (median lifespan {stats['lifespan'].median():.1f}y)")

    if out_root is None:
        # Only the real contract write invalidates the runtime metadata cache.
        try:
            from src.data import access

            access.get_metadata.cache_clear()
        except Exception:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="redirect output root (default: processed/water); use for a safe smoke")
    ap.add_argument("--limit", type=int, help="build only the first N good sites")
    args = ap.parse_args()
    make_water(out_root=args.out, limit=args.limit)
