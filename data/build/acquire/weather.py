"""gridMET over the AOE, on its own 4 km grid.

STORED ON THE NATIVE GRID, NOT PER CATCHMENT. Catchments outnumber gridMET cells about 4.6 to 1 -- 309,680 against 67,080 over the old extent -- so keying daily weather by COMID multiplies the table by that factor. Chunk 5 stores the catchment x cell intersection WEIGHTS instead, static and small, and applies them at assembly time. The API downstream is COMID-keyed either way; only the storage differs, and changing the aggregation rule becomes a re-read rather than a rebuild.

`cell_id` is derived from the grid's own coordinates, not from row order. That is the `global_node_id` lesson: a bare row counter is renumbered by any extent change, and four tables keyed on it silently disagreed. A cell's id here is a function of where it is, so widening the AOE appends cells without moving existing ones.

`precip_in_1d` is deliberately absent. Its Iowa-only footprint made it a covert state indicator -- a feature that says "this row is from Iowa" rather than anything about rainfall.

WHY NOT `pygridmet`. Its `get_bygeom` retries any response containing a NaN, on the assumption that NaN means a corrupt download:

    nans = [p for p, v in clm.isnull().sum().any().items() if v.item()]
    if nans: ... unlink the files, re-download, loop

But gridMET is CONUS LAND ONLY, so NaN is the correct value over the Great Lakes, the ocean, and anything past the border. Every box we ask for contains water, so the loop deletes a perfectly good file, refetches it, sees the same NaNs and eventually raises "GridMET did NOT process your request successfully" -- while the identical URL returns 8.3 MB of valid netCDF to `curl` in two seconds. The repeated full-size refetches also present as connection timeouts, which is what sent me looking for an outage that was not there.

The endpoint underneath is simple and fast: one URL per variable per window, straight netCDF. Building it here removes the heuristic, and lets us TILE -- `pygridmet` sends `geometry.bounds`, and the AOE's bounding box is 8.4M km2 against a 4.28M km2 polygon, so more than half of every transfer would be masked away on arrival.
"""

from __future__ import annotations

import time
from functools import lru_cache

import pandas as pd

from .. import config

# gridMET short names. Kept explicit so a silent upstream rename is a KeyError rather than a missing column.
VARIABLES = ("pr", "tmmn", "tmmx", "rmin", "rmax", "sph", "vs", "srad", "vpd", "pet", "etr")

# NCSS variable names, and the aggregate file each lives in. Taken from `pygridmet.core.GridMET.long_names` so the mapping stays the library's rather than ours.
LONG_NAMES = {
    "pr": "precipitation_amount", "tmmn": "daily_minimum_temperature",
    "tmmx": "daily_maximum_temperature", "rmin": "daily_minimum_relative_humidity",
    "rmax": "daily_maximum_relative_humidity", "sph": "daily_mean_specific_humidity",
    "vs": "daily_mean_wind_speed", "srad": "daily_mean_shortwave_radiation_at_surface",
    "vpd": "daily_mean_vapor_pressure_deficit",
    "pet": "daily_mean_reference_evapotranspiration_grass",
    "etr": "daily_mean_reference_evapotranspiration_alfalfa",
}
NCSS = "http://thredds.northwestknowledge.net:8080/thredds/ncss"
_HTTP_TIMEOUT = 600

GRID_PATH_NAME = "grid.parquet"


# gridMET's own grid: 1/24 degree. A cell's id is its (row, col) ON THAT GRID, so it is a function of position and nothing else -- widening the AOE appends cells and renumbers none. That is the `global_node_id` lesson: a bare row counter is renumbered by any extent change, and four tables keyed on it silently disagreed.
_RES = 1.0 / 24.0
_LON0, _LAT0 = -124.76666666663333, 49.4
_COLS = 1386                       # gridMET CONUS width, so row*_COLS + col cannot collide


# Months at or after this are re-checked for completeness on every run; older ones are closed. Two months back covers gridMET's own publication lag with room to spare.
_RECHECK_MONTHS = 2


def _recheck_from() -> tuple[int, int]:
    """(year, month) of the oldest month still considered live. Uses the clock, so it advances on its own."""
    import datetime as _dt

    t = _dt.date.today()
    m = t.month - _RECHECK_MONTHS
    return (t.year, m) if m >= 1 else (t.year - 1, m + 12)


# THE STORE IS QUARTERLY AND Z-ORDERED; THE FETCH IS STILL MONTHLY. Those are separate decisions and both are measured.
#
# Fetching stays monthly because a year is ~98M rows and concatenating then deduplicating one needs two full copies resident -- that filled 28 GB of swap and stalled at 4% CPU without ever writing a byte. See `fetch_month`.
#
# Storing three months per file is worth ~2x on every read: one-month files answer a gauge's full 224-month span in 2.2 s, three-month files in 1.2 s, and the difference is not data volume but per-file fixed cost -- an open plus footer parse is about 7 ms and 224 of them dominate. Twelve-month files are worse on every axis (1.4 s, and 19.9 GB against 18.6) because each open then parses proportionally more row-group metadata. Compression is unchanged either way, being governed by `row_group_size` rather than by file size.
_QUARTER_MONTHS = 3

# ~8,000 cells per row group at three months of dates, matching what the one-month layout had at 250k.
_ROW_GROUP = 750_000


def _pending_path(year: int, month: int):
    """Where a freshly fetched month lands before it is folded into its quarter."""
    return config.ACQ_WEATHER / "_pending" / f"gridmet_{year}-{month:02d}.parquet"


def _quarter_of(month: int) -> int:
    return (month - 1) // _QUARTER_MONTHS + 1


def _quarter_months(year: int, q: int) -> list[tuple[int, int]]:
    return [(year, m) for m in range((q - 1) * _QUARTER_MONTHS + 1, q * _QUARTER_MONTHS + 1)]


def _quarter_path(year: int, q: int):
    return config.ACQ_WEATHER / f"gridmet_{year}Q{q}.parquet"


def zkey(cell_id):
    """Morton (Z-order) key for a cell, from its position on the grid rather than from its id.

    SORTING BY `cell_id` DOES NOT CLUSTER A BASIN. The id is `row * 1386 + col`, row-major over a CONUS-wide grid, so a compact basin scatters into one narrow id run per grid ROW, 1386 apart -- WQS0065's 687 cells span 60,979 ids. Interleaving the bits of (col, row) puts neighbours in two dimensions next to each other instead. Measured on a 250k-row layout, row groups a basin has to touch: sorted by `cell_id`, 2/29, 3/29 and 5/29 for a small, medium and large basin; sorted by this key, 1/29 for all three.
    """
    import numpy as np

    c = np.asarray(cell_id, dtype="int64")
    x, y = (c % _COLS).astype("uint64"), (c // _COLS).astype("uint64")

    def spread(v):
        v = v & 0x1FFFFF
        v = (v | (v << 32)) & 0x1F00000000FFFF
        v = (v | (v << 16)) & 0x1F0000FF0000FF
        v = (v | (v << 8)) & 0x100F00F00F00F00F
        v = (v | (v << 4)) & 0x10C30C30C30C30C3
        v = (v | (v << 2)) & 0x1249249249249249
        return v

    return (spread(x) | (spread(y) << 1)).astype("int64")


def _grid_path():
    return config.ACQ_WEATHER / GRID_PATH_NAME


def cell_id(lon, lat):
    """Stable int32 id from position on gridMET's grid.

    An int, not the `"41.775_-91.68333"` string this used to build. The old build stored 17 float columns at 12.9 bytes per row; a 17-byte text key would have consumed that entire budget on its own.
    """
    import numpy as np

    col = np.rint((np.asarray(lon, dtype="float64") - _LON0) / _RES).astype("int64")
    row = np.rint((_LAT0 - np.asarray(lat, dtype="float64")) / _RES).astype("int64")
    return (row * _COLS + col).astype("int32")


def _to_frame(ds) -> pd.DataFrame:
    """xarray dataset -> long observations. float32 throughout: gridMET's precision does not justify eight bytes."""
    df = ds.to_dataframe().reset_index()
    latc = next(c for c in df.columns if c.lower() in ("lat", "latitude", "y"))
    lonc = next(c for c in df.columns if c.lower() in ("lon", "longitude", "x"))
    timec = next(c for c in df.columns if c.lower() in ("time", "day", "date"))
    df = df.rename(columns={latc: "lat", lonc: "lon", timec: "date"})
    df["cell_id"] = cell_id(df.lon, df.lat)
    df["date"] = pd.to_datetime(df.date).dt.date
    vals = [c for c in df.columns if c in VARIABLES]
    for c in vals:
        df[c] = df[c].astype("float32")
    return df[["cell_id", "date"] + vals].dropna(how="all", subset=vals)


def tiles():
    """Boxes covering the AOE, each hugging it more tightly than one bounding box would.

    The seed boxes plus the bbox of whatever the AOE holds outside them -- the Missouri arm. NCSS only takes a rectangle, so some overfetch is unavoidable; the point is to pay 2.8x once on a narrow arm rather than on the whole region. Cells outside the AOE are dropped after the read.
    """
    from shapely.geometry import box

    aoe = config.aoe_geometry()
    out = [tuple(b) for b in list(config.seed_boxes().values())]
    rest = aoe.difference(box(*config.seed_geometry().bounds))
    if not rest.is_empty:
        parts = list(rest.geoms) if rest.geom_type.startswith("Multi") else [rest]
        out += [p.bounds for p in parts if config.equal_area_km2(p) > 100]
    return out


def _ncss_url(var: str, bbox, start: str, end: str) -> str:
    w, s, e, n = bbox
    return (f"{NCSS}/agg_met_{var}_1979_CurrentYear_CONUS.nc"
            f"?var={LONG_NAMES[var]}&north={n}&west={w}&east={e}&south={s}"
            f"&horizStride=1&time_start={start}T00:00:00Z&time_end={end}T00:00:00Z"
            f"&timeStride=1&accept=netcdf")


def _fetch_tile(var: str, bbox, start: str, end: str):
    """One variable over one box, straight from NCSS. NaN over water is DATA, never a retry trigger."""
    import tempfile
    import urllib.request

    import xarray as xr

    url = _ncss_url(var, bbox, start, end)
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as r:
        blob = r.read()
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
        f.write(blob)
        tmp = f.name
    try:
        return xr.load_dataset(tmp).rename({LONG_NAMES[var]: var})
    finally:
        import os

        os.unlink(tmp)


def aoe_cells(force: bool = False):
    """Every gridMET cell whose FOOTPRINT intersects the AOE. Written once to `grid.parquet`.

    INTERSECTS, not centre-inside. A cell straddling the boundary still contributes area to a catchment on the inside, and chunk 5 weights by area of overlap -- dropping it would silently under-weight every edge catchment.

    Enumerated analytically from gridMET's grid definition rather than from a download: the grid is a fixed 1/24-degree lattice, so the cells covering a region are known without asking anyone.
    """
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import box

    if _grid_path().exists() and not force:
        return gpd.read_parquet(_grid_path())

    aoe = config.aoe_geometry()
    w, s, e, n = aoe.bounds
    cols = np.arange(int(np.floor((w - _LON0) / _RES)), int(np.ceil((e - _LON0) / _RES)) + 1)
    rows = np.arange(int(np.floor((_LAT0 - n) / _RES)), int(np.ceil((_LAT0 - s) / _RES)) + 1)
    cc, rr = np.meshgrid(cols, rows)
    lon = _LON0 + cc.ravel() * _RES
    lat = _LAT0 - rr.ravel() * _RES
    h = _RES / 2
    g = gpd.GeoDataFrame(
        {"cell_id": (rr.ravel() * _COLS + cc.ravel()).astype("int32"),
         "lon": lon, "lat": lat},
        geometry=[box(x - h, y - h, x + h, y + h) for x, y in zip(lon, lat)], crs=4326)
    hit = g.iloc[list(g.sindex.query(aoe, predicate="intersects"))].reset_index(drop=True)
    hit["aoe_stamp"] = config.aoe_stamp()
    config.ACQ_WEATHER.mkdir(parents=True, exist_ok=True)
    tmp = _grid_path().with_name(GRID_PATH_NAME + ".tmp")
    hit.to_parquet(tmp)
    tmp.replace(_grid_path())
    print(f"  grid: {len(g):,} candidate cells -> {len(hit):,} intersecting the AOE", flush=True)
    return hit


def write_quarter(year: int, q: int, frames) -> int:
    """Z-sort a quarter's months into one file. Returns rows written.

    Written from whatever months exist and REWRITTEN as later ones land, so a reader never has to know that the current quarter is partial -- there is one layout, not two. Rewriting a 250 MB file up to three times per quarter is cheaper than making every consumer union a store with a pending directory in it.

    Peak memory is about 4.5 GB: three months is 21.5M rows and the sort holds them at once. Fine alone, not fine beside a chunk-4 pass holding the 2.6 GB catchment layer.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    t = pa.concat_tables([f for f in frames if f is not None])
    if not t.num_rows:
        return 0
    t = t.append_column("zkey", pa.array(zkey(t.column("cell_id").to_numpy())))
    t = t.sort_by([("zkey", "ascending"), ("date", "ascending")])
    p = _quarter_path(year, q)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    pq.write_table(t, tmp, compression="snappy", row_group_size=_ROW_GROUP)
    tmp.replace(p)
    return t.num_rows


def compact(year: int, q: int, drop_pending: bool = True) -> int:
    """Fold every pending month of one quarter into its quarter file. Returns rows written."""
    import pyarrow.parquet as pq

    months = [(y, m) for y, m in _quarter_months(year, q) if _pending_path(y, m).exists()]
    if not months:
        return 0
    n = write_quarter(year, q, [pq.read_table(_pending_path(y, m)) for y, m in months])
    if drop_pending and n:
        for y, m in months:
            _pending_path(y, m).unlink()
    return n


def read_cells(cells, start=None, end=None):
    """Every observation for `cells` over [start, end]. The reader that makes the Z-order layout pay.

    ROW GROUPS ARE SELECTED HERE, FROM THE FOOTER STATISTICS, rather than left to `pq.read_table(filters=...)`. Measured on one quarter file and one basin: an explicit `read_row_groups` of the single matching group takes 2.2 ms, the equivalent `filters=` expression 41.4 ms, and reading the whole file 42.6 ms -- and for a 9-cell basin `filters=` took 385 ms, NINE TIMES SLOWER than reading everything. The layout is worth ~20x and none of it is reachable through the convenient API.

    The `zkey` bounds test is a range check, so it admits row groups holding no requested cell; the exact `cell_id` filter afterwards is what makes the result correct rather than merely small.
    """
    import numpy as np
    import pandas as pd
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    want = np.unique(np.asarray(list(cells), dtype="int64"))
    keys = np.unique(zkey(want))
    lo, hi = int(keys.min()), int(keys.max())
    out = []
    for p in sorted(config.ACQ_WEATHER.glob("gridmet_*Q*.parquet")):
        f = pq.ParquetFile(p)
        md = f.metadata
        names = [md.schema.column(i).name for i in range(md.num_columns)]
        if "zkey" not in names:
            raise RuntimeError(f"{p.name} predates the Z-ordered layout; re-run the weather compaction")
        zi = names.index("zkey")
        groups = []
        for r in range(md.num_row_groups):
            st = md.row_group(r).column(zi).statistics
            if st.max < lo or st.min > hi:
                continue
            if keys[np.searchsorted(keys, st.min):np.searchsorted(keys, st.max, "right")].size:
                groups.append(r)
        if not groups:
            continue
        t = f.read_row_groups(groups)
        t = t.filter(pc.is_in(t.column("cell_id"), value_set=pa.array(want)))
        if t.num_rows:
            out.append(t.drop_columns(["zkey"]).to_pandas())
    if not out:
        return pd.DataFrame(columns=["cell_id", "date"] + list(VARIABLES))
    d = pd.concat(out, ignore_index=True)
    if start is not None:
        d = d[pd.to_datetime(d.date) >= pd.Timestamp(start)]
    if end is not None:
        d = d[pd.to_datetime(d.date) <= pd.Timestamp(end)]
    return d.sort_values(["cell_id", "date"], ignore_index=True)


@lru_cache(maxsize=8)
def _quarter_dates(year: int, q: int) -> frozenset:
    """Every date present in a quarter file. Cached, because the completeness check asks month by month."""
    import pyarrow.parquet as pq

    p = _quarter_path(year, q)
    if not p.exists():
        return frozenset()
    try:
        return frozenset(pq.read_table(p, columns=["date"]).column("date").to_pylist())
    except Exception:
        return frozenset()


def _complete(year: int, month: int) -> bool:
    """Is this month fully on disk, in its pending file or already folded into its quarter?

    THE TRAILING MONTH IS PARTIAL AND MUST NOT BE TRUSTED. gridMET publishes the current month as it goes, so a file written on the 14th holds twelve days -- and an exists-check then treats it as done forever, leaving a permanent hole at the end of the record that nothing downstream can see. Only the date column is read, and the quarter's dates are cached so asking about three months costs one read.
    """
    import calendar

    import pyarrow.parquet as pq

    need = calendar.monthrange(year, month)[1]
    have = {d for d in _quarter_dates(year, _quarter_of(month)) if d.year == year and d.month == month}
    if len(have) >= need:
        return True
    p = _pending_path(year, month)
    if not p.exists():
        return False
    try:
        return pq.read_table(p, columns=["date"]).column("date").to_pandas().nunique() >= need
    except Exception:
        return False


def fetch_month(year: int, month: int, keep: set[int], force: bool = False) -> int:
    """One month over the AOE. Returns rows written; 0 when skipped, -1 when the month is not published yet.

    ONE FILE PER MONTH. A year is ~98M rows, and concatenating a year's tiles then deduplicating needs two full copies resident -- that filled 28 GB of swap and stalled at 4% CPU without ever writing a byte. A month is ~8M rows and fits.
    """
    import calendar
    import urllib.error

    import xarray as xr

    p = _pending_path(year, month)
    on_disk = p.exists() or bool(_quarter_dates(year, _quarter_of(month)))
    if on_disk and not force:
        # Re-check only the last few months; everything older is closed and immutable.
        if (year, month) < _recheck_from() or _complete(year, month):
            return 0
        print(f"    {year}-{month:02d}: partial on disk, refetching", flush=True)
    last = calendar.monthrange(year, month)[1]
    start, end = f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last:02d}"

    frames = []
    for bx in tiles():
        try:
            ds = xr.merge([_fetch_tile(v, bx, start, end) for v in VARIABLES])
        except urllib.error.HTTPError as e:
            # NCSS answers 400 for a window past the end of the record. That is "not published yet", not a failure -- the loop runs to the current year, and the last months of the final year do not exist until they happen.
            if e.code == 400:
                return -1
            raise
        frames.append(_to_frame(ds))
    # The seed boxes overlap, so a cell in the overlap arrives twice; `cell_id` is positional, which makes the duplicate exact.
    obs = pd.concat(frames, ignore_index=True).drop_duplicates(["cell_id", "date"])
    obs = obs[obs.cell_id.isin(keep)]

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    obs.to_parquet(tmp, index=False)
    tmp.replace(p)
    _quarter_dates.cache_clear()
    return len(obs)


def _compact_year(year: int) -> None:
    """Fold whatever this year has pending into its quarter files. Quiet when there is nothing to do."""
    for q in range(1, 5):
        if any(_pending_path(y, m).exists() for y, m in _quarter_months(year, q)):
            n = compact(year, q)
            if n:
                print(f"    {year}Q{q}: compacted to {n:,} rows "
                      f"({_quarter_path(year, q).stat().st_size / 1e6:.0f} MB)", flush=True)
    _quarter_dates.cache_clear()


def main(force: bool = False, years: range | None = None) -> dict:
    """Month by month, so an interrupted run resumes at the next month rather than restarting.

    STOPS AT THE END OF THE RECORD rather than at the current year's end. The declared span runs to 2026, but only part of 2026 has happened; asking NCSS for a future month earns a `400 Bad Request` that used to take the whole branch down after every real month had already been fetched.
    """
    grid = aoe_cells(force=force)
    keep = set(grid.cell_id.tolist())
    print(f"  {len(keep):,} gridMET cells intersect the AOE", flush=True)

    years = years or range(config.collection_start().year, __import__('datetime').date.today().year + 1)
    written = {}
    for y in years:
        for m in range(1, 13):
            t0 = time.time()
            n = fetch_month(y, m, keep, force=force)
            if n < 0:
                print(f"    {y}-{m:02d}: not published yet -- end of the record", flush=True)
                _compact_year(y)
                total = sum(written.values())
                print(f"  gridMET: {total:,} rows across "
                      f"{len([v for v in written.values() if v])} new month(s)")
                return written
            written[(y, m)] = n
            # PER MONTH, not per year. A year is twelve fetches of about three minutes each, so a per-year line means over half an hour of silence on a run that takes eight hours -- long enough to look hung, which is exactly how it was read.
            print(f"    {y}-{m:02d}: {'skipped' if n == 0 else f'{n:,} rows'}"
                  f"{'' if n == 0 else f' in {time.time() - t0:.0f}s'}", flush=True)
        _compact_year(y)
        done = sum(v for (yy, _), v in written.items() if yy == y)
        print(f"  {y}: {done:,} rows total", flush=True)
    total = sum(written.values())
    print(f"  gridMET: {total:,} rows across {len([v for v in written.values() if v])} new month(s)")
    return written
