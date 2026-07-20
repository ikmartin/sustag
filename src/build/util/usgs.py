"""Pull and format USGS-NWIS continuous water data (faithful port of data/water/make_usgs_data.py).

Per-site collector for the curated USGS_SITE_LIST: a 15-minute snapped wide table per site with
one column per available core pcode (nitrate always present). NETWORK build via `dataretrieval`
(needs api-keys.toml -> {"usgs": PAT}). Incremental: only missing date ranges are fetched.

Source/keys: api-keys.toml. Output: processed/water/data/{uid}_water.parquet, processed/water/
meta/usgs_{site_metadata,units}.csv; QA long-format dumps -> raw/water/USGS_QA_DUMP/.
"""

import logging
import sys
import time as _time
from pathlib import Path

import pandas as pd
from dataretrieval import waterdata

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root on path
from src.build.util._water_paths import data_dir, meta_dir

_SRC = Path(__file__).resolve().parents[2]
_LONG_DIR = _SRC / "data" / "raw" / "water" / "USGS_QA_DUMP"
_LONG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("collector")

GRID = "15min"

CORE_PCODES = {
    "00010": "temp_water",
    "99133": "nitrate_con",
    "00300": "diss_oxy_con",
    "00301": "diss_oxy_sat",
    "00400": "ph",
    "00095": "spec_cond",
    "00060": "discharge",
    "00065": "stage",
}

# Curated USGS sites to pull -- tied to this file's logic (pcodes, QA, coverage), so it lives
# here rather than in the shared pipeline config.
USGS_SITE_LIST = [
    "USGS-05418400", "USGS-05474500", "USGS-06604440", "USGS-05455100", "USGS-05418110",
    "USGS-415959091441301", "USGS-05484000", "USGS-05480986", "USGS-05412500", "USGS-05480603",
    "USGS-06817000", "USGS-05420500", "USGS-05418720", "USGS-05464500", "USGS-05464420",
    "USGS-06808500", "USGS-05451210", "USGS-06603750", "USGS-05484500", "USGS-05482500",
    "USGS-05482300", "USGS-05481000", "USGS-05465500", "USGS-05482000", "USGS-05483600",
    "USGS-05464475",
]


def _units_file():
    return meta_dir() / "usgs_units.csv"


def _metadata_file():
    return meta_dir() / "usgs_site_metadata.csv"


def _load_usgs_site_list() -> list:
    return USGS_SITE_LIST


MAX_ROWS_PER_REQUEST = 50_000
ROWS_PER_DAY_15MIN = 96
THREE_YEAR_CAP = pd.Timedelta(days=3 * 365)


def auto_chunk_size(n_params, cadence_per_day=ROWS_PER_DAY_15MIN, row_budget=MAX_ROWS_PER_REQUEST, safety=0.85):
    """Largest time window under the row budget for this site (capped at the 3-year API limit)."""
    n_params = max(1, int(n_params))
    days = max(1, int((row_budget * safety) // (cadence_per_day * n_params)))
    return min(pd.Timedelta(days=days), THREE_YEAR_CAP)


def make_chunks(start, end, chunk):
    offset = pd.tseries.frequencies.to_offset(chunk) if isinstance(chunk, str) else chunk
    cur = start
    while cur < end:
        nxt = min(cur + offset, end)
        yield cur, nxt
        cur = nxt


def _fmt(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def pull_chunk(site_id, pcodes, c_start, c_end, retries=2, pause=2.0):
    rng = [_fmt(c_start), _fmt(c_end)]
    for attempt in range(retries + 1):
        try:
            df, _ = waterdata.get_continuous(monitoring_location_id=site_id, parameter_code=list(pcodes), time=rng)
            if df is None or len(df) == 0:
                log.info(f"  {rng}: empty")
                return None
            got_end = pd.to_datetime(df["time"], utc=True).max()
            c_end_utc = c_end if c_end.tzinfo else c_end.tz_localize("UTC")
            if got_end < c_end_utc - pd.Timedelta(days=1):
                log.warning(f"  {rng}: data ends {got_end}, short of {c_end} -- possible truncation")
            return df
        except Exception as e:
            if attempt < retries:
                log.warning(f"  {rng}: {e} -- retry {attempt + 1}")
                _time.sleep(pause)
            else:
                log.error(f"  {rng}: FAILED after {retries} retries -- skipping ({e})")
                return None


def build_site(site_id, start=None, end=None, chunk=None, pcodes=CORE_PCODES, grid=GRID):
    """start,end: ISO strings or Timestamps. chunk: window per request (<=3y). -> (wide, long_keep)."""
    if chunk is None:
        raise ValueError("Provide a chunk size (use auto_chunk_size to compute one).")
    if start is None or end is None:
        raise ValueError("Provide start and end.")
    start = pd.Timestamp(start)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = pd.Timestamp(end)
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")

    raw_parts = []
    for c_start, c_end in make_chunks(start, end, chunk):
        df = pull_chunk(site_id, pcodes, c_start, c_end)
        if df is not None:
            raw_parts.append(df)
    if not raw_parts:
        log.warning(f"{site_id}: no data in any chunk.")
        return None, None

    raw = pd.concat(raw_parts, ignore_index=True)
    raw["time"] = pd.to_datetime(raw["time"], utc=True)
    raw = raw.drop_duplicates(subset=["parameter_code", "time"])

    long_keep = raw[
        ["monitoring_location_id", "parameter_code", "time", "value", "unit_of_measure", "approval_status"]
    ].copy()

    raw["time"] = raw["time"].dt.floor(grid)
    wide = raw.pivot_table(index="time", columns="parameter_code", values="value", aggfunc="first")
    wide = wide.rename(columns=pcodes)
    wide = wide[[pcodes[pc] for pc in pcodes if pcodes[pc] in wide.columns]]
    wide.insert(0, "site_id", site_id)
    return wide, long_keep


def generate_metadata(generate_relevant_sites=False):
    if generate_relevant_sites:
        nitrogen_pcodes = ["99133", "99137", "83554", "00631"]
        timeseries_df, _ = waterdata.get_time_series_metadata(
            state_name="Iowa", parameter_code=",".join(nitrogen_pcodes), skip_geometry=False
        )
        monitoring_location_ids = timeseries_df["monitoring_location_id"].unique().tolist()
    else:
        monitoring_location_ids = _load_usgs_site_list()
    all_meta, _ = waterdata.get_time_series_metadata(monitoring_location_id=monitoring_location_ids, skip_geometry=False)
    return all_meta


def qa_summary(long_keep):
    return (
        long_keep.groupby(["parameter_code", "approval_status"])
        .agg(begin=("time", "min"), end=("time", "max"), n=("time", "size"))
        .reset_index()
        .sort_values(["parameter_code", "begin"])
    )


def update_measures(long_keep, pcodes=CORE_PCODES, path=None):
    """Maintain a parameter_code -> unit lookup across all sites; warn on unit conflicts."""
    path = path or _units_file()
    seen = long_keep[["parameter_code", "unit_of_measure"]].dropna().drop_duplicates().copy()
    seen["name"] = seen["parameter_code"].map(pcodes)
    existing = pd.read_csv(path, dtype={"parameter_code": str}) if path.exists() else \
        pd.DataFrame(columns=["parameter_code", "name", "unit_of_measure"])
    seen["parameter_code"] = seen["parameter_code"].astype(str)
    seen = seen[["parameter_code", "name", "unit_of_measure"]]
    combined = pd.concat([existing, seen], ignore_index=True).drop_duplicates(subset=["parameter_code", "unit_of_measure"])
    dupes = combined[combined.duplicated("parameter_code", keep=False)]
    if not dupes.empty:
        for pc, g in dupes.groupby("parameter_code"):
            log.warning(f"unit conflict for {pc} ({pcodes.get(pc, '?')}): {', '.join(g['unit_of_measure'].astype(str))}")
    combined.sort_values("parameter_code").to_csv(path, index=False)
    return combined


def filename(site_id: str) -> Path:
    return data_dir() / f"{site_id}_water.parquet"


def get_lifespan(site_id: str, meta):
    mask = (meta["monitoring_location_id"] == site_id) & (meta["parameter_code"].astype(str) == "99133")
    start = pd.to_datetime(meta[mask]["begin"], utc=True, errors="coerce").min()
    end = pd.to_datetime(meta[mask]["end"], utc=True, errors="coerce").max()
    return start, end


def get_update_ranges(site_id: str, meta) -> list:
    """[(start, end)] gaps that need fetching; [] if up to date."""
    _TOL = pd.Timedelta(days=2)
    path = filename(site_id)
    meta_start, meta_end = get_lifespan(site_id, meta)
    if pd.isna(meta_start) or pd.isna(meta_end):
        log.warning(f"{site_id}: no nitrate lifespan in metadata, skipping")
        return []
    if not path.exists():
        return [(meta_start, meta_end)]
    try:
        idx = pd.read_parquet(path, columns=[]).index
    except Exception:
        print(f"File {path} unreadable, will re-fetch full range.")
        return [(meta_start, meta_end)]
    if idx.empty:
        return [(meta_start, meta_end)]

    def _utc(ts):
        ts = pd.Timestamp(ts)
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

    file_start, file_end = _utc(idx.min()), _utc(idx.max())
    ranges = []
    if file_start - meta_start > _TOL:
        print(f"{site_id}: missing at start -> {meta_start:%Y-%m-%d}..{file_start:%Y-%m-%d}")
        ranges.append((meta_start, file_start))
    if meta_end - file_end > _TOL:
        print(f"{site_id}: missing at end -> {file_end:%Y-%m-%d}..{meta_end:%Y-%m-%d}")
        ranges.append((file_end, meta_end))
    return ranges


def params_from_meta(site_id: str, meta) -> int:
    sub = meta[(meta["monitoring_location_id"] == site_id) & meta["parameter_code"].astype(str).isin(CORE_PCODES)]
    return max(1, sub["parameter_code"].nunique())


def main(api_keys, extra_filter=None):
    """Build the USGS data site by site (incremental). api_keys must contain 'usgs' (a PAT)."""
    import os

    extra_filter = extra_filter or []
    os.environ["API_USGS_PAT"] = api_keys["usgs"]

    site_list = _load_usgs_site_list()
    try:
        meta = pd.read_csv(_metadata_file(), dtype={"parameter_code": str})
        if set(meta["monitoring_location_id"].unique()) != set(site_list):
            meta = generate_metadata()
    except FileNotFoundError:
        meta = generate_metadata()

    print(f"\n{meta.monitoring_location_id.nunique()} sites in metadata\n")
    site_ids = (
        meta[meta["monitoring_location_id"].isin(site_list) & ~meta["monitoring_location_id"].isin(extra_filter)]
        ["monitoring_location_id"].unique().tolist()
    )

    completed = []
    for site_id in site_ids:
        path = filename(site_id)
        update_ranges = get_update_ranges(site_id, meta)
        if not update_ranges:
            print(f"Site {site_id} up to date, skipping")
            completed.append(site_id)
            continue

        n_params = params_from_meta(site_id, meta)
        chunk = auto_chunk_size(n_params)

        existing_wide = None
        if path.exists():
            try:
                existing_wide = pd.read_parquet(path)
            except Exception:
                log.warning(f"{site_id}: could not read existing file, re-fetching full range")
                update_ranges = [get_lifespan(site_id, meta)]

        wide_parts = [existing_wide] if existing_wide is not None else []
        long_parts = []
        for r_start, r_end in update_ranges:
            print(f"  {site_id}: fetching {r_start:%Y-%m-%d}..{r_end:%Y-%m-%d} ({n_params} params, chunk {chunk.days}d)")
            wide_chunk, long_chunk = build_site(site_id, start=r_start, end=r_end, chunk=chunk)
            if wide_chunk is not None:
                wide_parts.append(wide_chunk.rename(columns={"site_id": "site_uid"}))
            if long_chunk is not None:
                long_parts.append(long_chunk)

        if not wide_parts:
            print(f"  {site_id}: no data returned, skipping")
            continue

        wide = pd.concat(wide_parts)
        wide = wide[~wide.index.duplicated(keep="last")].sort_index()
        wide.index.name = "datetime"
        wide.to_parquet(path)
        print(f"Wrote {path}  shape={wide.shape}")

        if long_parts:
            long_keep = pd.concat(long_parts, ignore_index=True)
            qa_summary(long_keep).to_csv(_LONG_DIR / f"{site_id}_qa.csv", index=False)
            update_measures(long_keep)
        completed.append(site_id)

    meta = meta[meta.monitoring_location_id.isin(completed)]
    meta.to_csv(_metadata_file())
    print(f"Saved metadata for {len(completed)} sites to {_metadata_file()}")


if __name__ == "__main__":
    import tomllib

    with open(Path(__file__).resolve().parents[3] / "api-keys.toml", "rb") as f:  # api-keys.toml (repo root)
        main(tomllib.load(f))
