"""gridMET's pentad drought aggregates -- a SIBLING store to the daily one, at its own cadence.

WHY NOT IN `weather.py`'s STORE. These are PENTAD: one value per five days, 73 a year. Landing them beside the daily observations would leave four of every five days null, and forward-filling to daily is a modelling preference the build has no business baking in -- a consumer who wants a daily series can resample, but a consumer handed a forward-filled one cannot tell it was ever pentad. Hence `acquired/weather/pentad/` beside `acquired/weather/daily/`, and a reader that STATES the cadence in what it returns.

THEY RIDE THE SAME LATTICE, WHICH IS THE POINT. Verified against the endpoint: identical 1/24-degree grid and corner coordinates, so `grid.parquet` and `weights_gridmet.parquet` work unchanged and a catchment reduction needs nothing new. Only the file layout and the time column differ.

TWENTY-FIVE AGGREGATES, NOT TWENTY-SIX. `spei` and `eddi` publish all eight accumulation windows; `spi` publishes SEVEN -- there is no `spi270d`, confirmed against the catalogue. A loop over a hardcoded eight-window list fails on one request in twenty-five, most likely hours into a run.

`category` IS NOT STORED, and that is deliberate. Every drought file carries two grids -- its index and a `category` binning of that index -- so storing both would add 25 columns that are a deterministic function of 25 columns already present. That is the redundancy the thematic audit exists to find; there is no sense manufacturing it.
"""

from __future__ import annotations

import pandas as pd

from .. import config, verify
from .. import io as bio
from . import weather as W

PENTAD_DIR_NAME = "pentad"

# dataset stem -> the value grid inside it. Confirmed against the endpoint 2026-08-28.
AGGREGATES = {
    **{f"spi{w}": "spi" for w in ("14d", "30d", "90d", "180d", "1y", "2y", "5y")},
    **{f"spei{w}": "spei" for w in ("14d", "30d", "90d", "180d", "270d", "1y", "2y", "5y")},
    **{f"eddi{w}": "eddi" for w in ("14d", "30d", "90d", "180d", "270d", "1y", "2y", "5y")},
    "pdsi": "daily_mean_palmer_drought_severity_index",
    "z": "daily_mean_palmer_z_index",
}

# `spi270d` is published for spei and eddi but NOT for spi. Named rather than merely absent, so a future
# reader knows it was checked and does not "restore" it.
NOT_PUBLISHED = ("spi270d",)


def _dir():
    return config.ACQ_WEATHER / PENTAD_DIR_NAME


def _path(year: int):
    return _dir() / f"gridmet_pentad_{year}.parquet"


def _url(stem: str, bbox, start: str, end: str) -> str:
    w, s, e, n = bbox
    return (f"{W.NCSS}/agg_met_{stem}_1979_CurrentYear_CONUS.nc"
            f"?var={AGGREGATES[stem]}&north={n}&west={w}&east={e}&south={s}"
            f"&horizStride=1&time_start={start}T00:00:00Z&time_end={end}T00:00:00Z"
            f"&timeStride=1&accept=netcdf")


# A TRANSIENT TIMEOUT MUST NOT COST THE RUN. This fetch is 2,850 requests over ~1.5 hours against a
# public THREDDS server, so the probability that every one succeeds is not close to 1 -- the first
# attempt died on `[Errno 60] Operation timed out` at year sixteen of nineteen. The years already on
# disk survive (each is skipped when present), but a crash still needs a human to notice and restart,
# which is the papercut worth removing rather than the data loss.
_RETRIES = 4
_BACKOFF_S = 15


def _fetch_tile(stem: str, bbox, start: str, end: str):
    import os
    import socket
    import tempfile
    import time
    import urllib.error
    import urllib.request

    import xarray as xr

    url = _url(stem, bbox, start, end)
    for attempt in range(1, _RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=W._HTTP_TIMEOUT) as r:
                blob = r.read()
            break
        except urllib.error.HTTPError:
            raise                                    # 400 means "not published"; retrying will not help
        except (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError) as e:
            if attempt == _RETRIES:
                raise
            wait = _BACKOFF_S * attempt
            print(f"      {stem} {start[:4]}: {type(e).__name__}, retry {attempt}/{_RETRIES - 1} "
                  f"in {wait}s", flush=True)
            time.sleep(wait)
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
        f.write(blob)
        tmp = f.name
    try:
        ds = xr.load_dataset(tmp)
        # Drop the companion `category` grid before it reaches a frame -- see the module docstring.
        keep = [v for v in ds.data_vars if v == AGGREGATES[stem]]
        return ds[keep].rename({AGGREGATES[stem]: stem})
    finally:
        os.unlink(tmp)


def _horizon(year: int):
    """The latest pentad this year could plausibly hold: two cadence steps back from today, or the year end."""
    import datetime as _dt

    return min(_dt.date(year, 12, 31), _dt.date.today() - _dt.timedelta(days=10))


def _covers(year: int, p) -> bool:
    """Does the stored year reach the last pentad the endpoint can have published?

    EXISTENCE IS NOT COVERAGE, and the current year is where that bites. A year file written in August holds pentads to August and nothing later; a guard testing `p.exists()` skips it forever, so the newest year freezes at whatever the first run happened to fetch and no error is raised. Past years are unaffected -- they were complete when written -- which is what makes the failure invisible until someone asks why the store stops mid-2026.

    TWO pentads of slack, not one. The cadence is five days AND publication lags it, so a store holding data to the 23rd on the 31st is current rather than frozen -- a one-pentad horizon called that a freeze, which would both refetch the year on every pass and make the verify check cry wolf. A check that cries wolf gets ignored, which is worse than not having it.
    """
    import datetime as _dt

    import pyarrow.parquet as pq

    try:
        pf = pq.ParquetFile(p)
        i = pf.schema_arrow.names.index("date")
        md = pf.metadata
        best = None
        for rg in range(md.num_row_groups):
            st = md.row_group(rg).column(i).statistics
            if st is not None and st.has_min_max and st.max is not None:
                v = pd.Timestamp(st.max).date()
                best = v if best is None or v > best else best
    except Exception:                                    # noqa: BLE001 -- unreadable is not covered
        return False
    if best is None:
        return False
    return best >= _horizon(year)


def fetch_year(year: int, keep: set[int], force: bool = False) -> int:
    """Every aggregate over the AOE for one calendar year. Returns rows written, 0 when already present.

    YEAR GRANULARITY, and it is a cost decision rather than a preference: measured, one tile-year is 19.44 MB in 3.3 s, and the request carries seconds of fixed cost against a payload a fifth as dense as the daily store's. Twelve monthly requests would pay that fixed cost twelve times for identical data. The memory reason the daily store splits by month does not apply here -- a pentad year is 73 timesteps, not 365.
    """
    import urllib.error

    import xarray as xr

    p = _path(year)
    if p.exists() and not force and _covers(year, p):
        return 0
    start, end = f"{year}-01-01", f"{year}-12-31"
    frames = []
    for bx in W.tiles():
        try:
            ds = xr.merge([_fetch_tile(s, bx, start, end) for s in AGGREGATES])
        except urllib.error.HTTPError as e:
            if e.code == 400:
                raise W._NotPublished(f"{year}") from e
            raise
        frames.append(W._to_frame(ds, variables=tuple(AGGREGATES)))
    obs = pd.concat(frames, ignore_index=True).drop_duplicates(["cell_id", "date"])
    obs = obs[obs.cell_id.isin(keep)]
    if not len(obs):
        return 0
    obs = obs.assign(zkey=W.zkey(obs.cell_id.to_numpy())).sort_values(["zkey", "date"])
    _dir().mkdir(parents=True, exist_ok=True)
    bio.write_parquet(obs, p, stamps={"aoe": config.aoe_stamp(), "cadence": "pentad"})
    return len(obs)


def main(force: bool = False, years: range | None = None) -> None:
    """Year by year, so an interrupted run resumes at the next year rather than restarting.

    The span matches `weather.main`'s: from the configured collection start to the current year. There is deliberately no configured upper bound -- one tested against the calendar silently stops collecting as time passes -- so the loop runs forward and stops on the first year the endpoint says is unpublished.
    """
    import datetime as _dt

    grid = W.aoe_cells()
    keep = set(grid.cell_id.astype("int64"))
    years = years or range(config.collection_start().year, _dt.date.today().year + 1)
    lo, hi = years.start, years.stop - 1
    print(f"  drought pentad: {len(AGGREGATES)} aggregate(s), {lo}-{hi}, {len(keep):,} AOE cells", flush=True)
    total = done = 0
    for y in years:
        try:
            n = fetch_year(y, keep, force=force)
        except W._NotPublished:
            print(f"    {y}: not published yet -- stopping", flush=True)
            break
        total += n
        done += 1
        print(f"    {y}: {n:,} rows" if n else f"    {y}: cached", flush=True)
    print(f"  drought pentad: {total:,} rows over {done} year(s)", flush=True)


# ---- verify checks --------------------------------------------------------------------------------

@verify.check("a3", "the pentad store carries every declared aggregate, non-null throughout")
def _check_pentad_variables():
    import pyarrow.parquet as pq

    ps = sorted(_dir().glob("gridmet_pentad_*.parquet")) if _dir().exists() else []
    if not ps:
        return None, "no pentad store yet"
    missing = {}
    for p in ps:
        pf = pq.ParquetFile(p)
        names = set(pf.schema_arrow.names)
        gap = sorted(set(AGGREGATES) - names)
        if gap:
            missing[p.name] = gap
    return not missing, (f"{len(ps)} year file(s), all {len(AGGREGATES)} aggregates present"
                         if not missing else f"{len(missing)} file(s) short, e.g. {list(missing.items())[:1]}")


@verify.check("a3", "the pentad store shares the daily store's lattice")
def _check_pentad_lattice():
    import pyarrow.parquet as pq

    ps = sorted(_dir().glob("gridmet_pentad_*.parquet")) if _dir().exists() else []
    if not ps:
        return None, "no pentad store yet"
    cells = set(pq.read_table(ps[0], columns=["cell_id"]).column("cell_id").to_pylist())
    grid = set(W.aoe_cells().cell_id.astype("int64"))
    extra = cells - grid
    return not extra, (f"{len(cells):,} cells, all on the gridMET lattice"
                       if not extra else f"{len(extra)} cell(s) absent from grid.parquet")


@verify.check("a3", "the pentad store is pentad, not daily")
def _check_pentad_cadence():
    """The store's whole reason for existing separately. A daily-spaced file here means the wrong endpoint was read."""
    import numpy as np
    import pyarrow.parquet as pq

    ps = sorted(_dir().glob("gridmet_pentad_*.parquet")) if _dir().exists() else []
    if not ps:
        return None, "no pentad store yet"
    d = pq.read_table(ps[0], columns=["date"]).to_pandas()
    days = np.sort(pd.to_datetime(d.date).dt.normalize().unique())
    if len(days) < 3:
        return None, f"only {len(days)} distinct date(s)"
    gaps = np.diff(days).astype("timedelta64[D]").astype(int)
    med = int(np.median(gaps))
    return med == 5, f"{len(days)} timesteps in {ps[0].name}, median spacing {med} day(s)"


@verify.check("a3", "the pentad store reaches the present")
def _check_pentad_current():
    """The standing guard for the freeze this store actually had: `fetch_year` skipped on existence, so the partial current year would never have been refreshed and every other check would have stayed green."""
    import datetime as _dt

    ps = sorted(_dir().glob("gridmet_pentad_*.parquet")) if _dir().exists() else []
    if not ps:
        return None, "no pentad store yet"
    year = max(int(p.stem.rsplit("_", 1)[1]) for p in ps)
    covered = _covers(year, _path(year))
    horizon = _horizon(year)
    return covered, f"newest year {year}, reaches its horizon {horizon}" if covered else \
        f"newest year {year} stops short of {horizon} -- the store has frozen"
