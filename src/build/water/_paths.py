"""Shared paths + a per-site raw-series reader for the reworked 4-step water build.

The new build separates RAW (per-source, native cadence) from PROCESSED (daily-max, the model product):

    src/data/raw/water/IWQIS/{uid}_water.parquet   native cadence, reassembled from the git-committed chunks/ (non-API source of truth)
    src/data/raw/water/USGS/{uid}_water.parquet    native cadence, pulled from the USGS API (refetchable)
    src/data/raw/water/raw_site_list.csv           fetch_sites.py output (candidate universe)
    src/data/raw/water/filtered_sites.json         filter_sites.py output (good/flagged/short)

    src/data/processed/water/{data,meta}           make_water.py output (daily-max + contract)

The existing IWQIS chunks live at src/data/raw/water/chunks/ (read via src.build.util.iwqis); this module does NOT move them (that is a Phase-2 git/layout change).
"""

from pathlib import Path

import pandas as pd

_SRC = Path(__file__).resolve().parents[2]  # src
_HERE = Path(__file__).resolve().parent  # src/build/water
_RAW = _SRC / "data" / "raw" / "water"
_LEGACY_PROC = _SRC / "data" / "processed" / "water" / "data"  # native parquets from the old build


def raw_iwqis_dir() -> Path:
    d = _RAW / "IWQIS"
    d.mkdir(parents=True, exist_ok=True)
    return d


def raw_usgs_dir() -> Path:
    d = _RAW / "USGS"
    d.mkdir(parents=True, exist_ok=True)
    return d


def raw_site_list_path() -> Path:
    _RAW.mkdir(parents=True, exist_ok=True)
    return _RAW / "raw_site_list.csv"


def filtered_sites_path() -> Path:
    _RAW.mkdir(parents=True, exist_ok=True)
    return _RAW / "filtered_sites.json"


def review_panel_path() -> Path:
    return _HERE / "review_panel.png"


def interim_water_dir() -> Path:
    """CLEANED (scrubbed) per-site series output dir, src/data/interim/water. filter_sites writes a cleaned {uid}_water.parquet here for any site whose localized defects (range/flatline) were scrubbed; make_water reads it in preference to raw. Gitignored (interim/* is ignored) and regenerated."""
    d = _SRC / "data" / "interim" / "water"
    d.mkdir(parents=True, exist_ok=True)
    return d


def raw_source_dir(uid: str) -> Path:
    """The per-source raw dir a uid belongs in (by uid prefix)."""
    return raw_usgs_dir() if str(uid).startswith("USGS-") else raw_iwqis_dir()


# ── per-site raw-series reader ────────────────────────────────────────────────
# Search order: the new raw/water/{USGS,IWQIS} dirs first, then (Phase-1 convenience) the old processed/water/data native parquets, so filter_sites/make_water can smoke-test on the existing 83-site dataset before the raw dirs are fully populated. NOTE the legacy fallback is native only; once make_water writes daily-max into processed/water (Phase 2 flips the source of truth), drop it.
_SEARCH_DIRS = [raw_usgs_dir, raw_iwqis_dir, lambda: _LEGACY_PROC]


def _load_series(path: Path) -> pd.DataFrame | None:
    """Standardize a per-site parquet into columns `datetime` (UTC) + `nitrate_con`, or None if it has no nitrate_con column."""
    df = pd.read_parquet(path)
    if "nitrate_con" not in df.columns:
        return None
    df = df.reset_index()
    # the index round-trips as a column named 'datetime' (sources set index.name='datetime'); fall back to any datetime-like column if a source named it differently.
    if "datetime" not in df.columns:
        dt_cols = [c for c in df.columns if "time" in c.lower() or "date" in c.lower()]
        if not dt_cols:
            return None
        df = df.rename(columns={dt_cols[0]: "datetime"})
    out = df[["datetime", "nitrate_con"]].copy()
    out["datetime"] = pd.to_datetime(out["datetime"], utc=True)
    out["nitrate_con"] = pd.to_numeric(out["nitrate_con"], errors="coerce")
    return out


def read_raw_series(uid: str) -> pd.DataFrame | None:
    """A site's native nitrate series as a frame with columns `datetime` (UTC) + `nitrate_con`. Returns None if no raw parquet exists for the uid or it carries no nitrate_con column. Reads RAW only (filter_sites must detect defects on the uncleaned series)."""
    for get_dir in _SEARCH_DIRS:
        p = get_dir() / f"{uid}_water.parquet"
        if p.exists():
            return _load_series(p)
    return None


def read_clean_or_raw_series(uid: str) -> pd.DataFrame | None:
    """The SCRUBBED series from interim/water if filter_sites wrote one for this site, else the raw series. This is make_water's reader, so cleaned sites aggregate their corrected data and everything else falls back to raw."""
    p = interim_water_dir() / f"{uid}_water.parquet"
    if p.exists():
        return _load_series(p)
    return read_raw_series(uid)
