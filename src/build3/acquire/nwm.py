"""A3-late -- NWM v3.0 retrospective daily aggregates for the site reaches, COMID-partitioned.

`feature_id` IS THE COMID: the National Water Model inherits NHDPlus's reach identifiers, so its modelled streamflow joins everything else here with no crosswalk. The retrospective zarr is chunked [672 hours, 30000 features]; the site COMIDs occupy a fraction of the feature chunks (measured 22 of 93 on the calibration cohort), so pulling only them is severalfold cheaper than a full sweep.

THE INPUT IS A PARAMETER FILE, NOT THE SITE TABLE. `parameters/site_comids.txt` is written by D3 -- acquisition never imports derivation, and the fetch is reproducible from tracked inputs alone. Partitioned `acquired/nwm/comid={comid}/` so widening to more reaches later is an APPEND, never a rewrite; the retrospective is a FIXED product (1979-02 to 2023-01), so a comid already on disk is final and is never re-pulled -- there is no revision window to honour.

DAILY AGGREGATES ONLY -- the sanctioned daily-resolution assumption applies to modelled series too. Days are cut in `Etc/GMT+6` standard time like every observed record, so an NWM day and a sensor day describe the same water. Mean, min and max per day: the diel spread survives, and a pre-aggregated modelled mean beside a measured `_dv` mean stays comparable.

THE RETROSPECTIVE ENDS EARLY 2023; recent years need the operational archives, and the virtual-pin route (NWPS API vs per-reach zarr range reads) is under investigation -- `published/` promises no NWM columns until that closes. This store is an acquired product, consumed by nothing downstream yet.

Extra dependencies: `zarr`, `s3fs` (anonymous S3). Named loudly at run time rather than imported at module load, so the rest of A3 never pays for them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, io as bio

RETRO_ZARR = "s3://noaa-nwm-retrospective-3-0-pds/CONUS/zarr/chrtout.zarr"
COMIDS_PATH = config.PARAMETERS / "site_comids.txt"

# Load-and-aggregate window. One year of hourly flow for a few dozen reaches is a few MB; the bound exists so a widened cohort cannot silently turn one .load() into the whole store.
_YEARS_PER_LOAD = 1


def site_comids() -> list[int]:
    """The reaches to pull, from the parameter file D3 writes. Empty (with a note) before any D3 run."""
    if not COMIDS_PATH.exists():
        print(f"  {COMIDS_PATH.name} not written yet -- run d3 first (first-build no-op)")
        return []
    out = []
    for line in COMIDS_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(int(line))
    return sorted(set(out))


def _partition(comid: int):
    return config.ACQ_NWM / f"comid={int(comid)}" / "streamflow_daily.parquet"


def _open_retro():
    try:
        import s3fs  # noqa: F401
        import xarray as xr
        import zarr  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            f"the NWM pull needs `zarr` and `s3fs` ({e.name} is missing) -- "
            f"`pip install zarr s3fs` in the sustag env and rerun") from None
    return xr.open_zarr(RETRO_ZARR, storage_options={"anon": True}, consolidated=True)


def _daily(hourly: pd.Series) -> pd.DataFrame:
    """Hourly UTC flow -> one row per standard-time day: mean, min, max, n_hours.

    The shift is the same `Etc/GMT+6` rule every observed record uses (`io.STANDARD_TZ`), applied as a fixed -6 h offset -- NWM timestamps are UTC and the standard zone never observes DST, so the offset IS the rule.
    """
    idx = hourly.index - pd.Timedelta(hours=6)
    g = hourly.groupby(idx.date)
    out = pd.DataFrame({"nwm_flow_mean": g.mean(), "nwm_flow_min": g.min(),
                        "nwm_flow_max": g.max(), "nwm_n_hours": g.size().astype("int16")})
    out.index = pd.to_datetime(out.index)
    out.index.name = "date"
    return out.reset_index()


def fetch(comids: list[int], force: bool = False) -> dict[str, int]:
    """Pull and aggregate the retrospective for `comids`, one partition per reach. Returns outcome counts.

    LOADED YEAR BY YEAR for all wanted reaches together: the store's chunks are [672 h, 30000 features], so one `.load()` per window touches each needed chunk once, while a per-reach loop would re-fetch every shared chunk per reach. Aggregates accumulate per comid and each partition is written ONCE, whole -- an interrupted run leaves finished partitions and no torn ones.
    """
    todo = [c for c in comids if force or not _partition(c).exists()]
    print(f"  nwm: {len(comids) - len(todo)} partition(s) on disk, {len(todo)} to fetch")
    if not todo:
        return {"cached": len(comids)}

    ds = _open_retro()
    have = np.intersect1d(ds.feature_id.values, np.asarray(todo, dtype="int64"))
    missing = sorted(set(todo) - set(have.tolist()))
    if missing:
        # Named, not dropped silently: a reach absent from NWM is a fact about the reach (usually a
        # divergence artifact) that the consumer needs to see as an absence with a reason.
        print(f"  {len(missing)} reach(es) not in the NWM retrospective: {missing[:8]}")
    if not len(have):
        return {"absent": len(missing)}

    flow = ds.streamflow.sel(feature_id=have)
    t0, t1 = pd.Timestamp(ds.time.values[0]), pd.Timestamp(ds.time.values[-1])
    print(f"  retrospective {t0.date()} .. {t1.date()}, {len(have)} reach(es); loading by year", flush=True)

    parts: dict[int, list[pd.DataFrame]] = {int(c): [] for c in have}
    for y in range(t0.year, t1.year + 1, _YEARS_PER_LOAD):
        sl = flow.sel(time=slice(f"{y}-01-01", f"{y + _YEARS_PER_LOAD - 1}-12-31"))
        if not sl.time.size:
            continue
        arr = sl.load()
        idx = pd.DatetimeIndex(arr.time.values)
        for j, c in enumerate(arr.feature_id.values):
            s = pd.Series(arr.values[:, j], index=idx).dropna()
            if len(s):
                parts[int(c)].append(_daily(s))
        print(f"    {y}: {sl.time.size:,} hour(s) aggregated", flush=True)

    n_ok = 0
    for c, frames in parts.items():
        if not frames:
            continue
        df = pd.concat(frames, ignore_index=True).groupby("date", as_index=False).agg(
            nwm_flow_mean=("nwm_flow_mean", "mean"), nwm_flow_min=("nwm_flow_min", "min"),
            nwm_flow_max=("nwm_flow_max", "max"), nwm_n_hours=("nwm_n_hours", "sum"))
        bio.write_parquet(df, _partition(c), stamps={"source": "nwm-retrospective-3.0"})
        n_ok += 1
    print(f"  wrote {n_ok} partition(s) under {config.ACQ_NWM}")
    return {"fetched": n_ok, "absent": len(missing), "cached": len(comids) - len(todo)}


def main(force: bool = False, snapshot: str | None = None) -> None:
    comids = site_comids()
    if comids:
        fetch(comids, force=force)
