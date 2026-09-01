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

import datetime as dt
import time
from functools import lru_cache

import pandas as pd

from .. import config, verify

# gridMET short names. Kept explicit so a silent upstream rename is a KeyError rather than a missing column.
# Every DAILY gridMET aggregate the endpoint serves. The first eleven are the standard meteorology; the
# last five are the fire-weather block, restored 2026-08-26 after the rebuild had silently dropped them --
# `fuel_moisture_1000h` was the ONLY weather variable that survived feature curation in either shipped
# model ("the rest are perm-dead in every run"), and it was missing because the variable tuple was built
# from the standard long-name set rather than from the catalogue. Collect all of them; which ones a model
# uses is a modelling decision, made downstream.
#
# NOT HERE, deliberately: the 25 drought aggregates (spi/spei/eddi at eight windows each, pdsi, z) are
# PENTAD -- 3,404 timesteps since 1979, one per five days -- and this store is one row per (cell, DAY).
# Landing them here would either leave four of every five days NULL or forward-fill, and a forward-fill
# is a modelling preference the build has no business baking in. They need their own store.
# `snow` is absent from the aggregated catalogue: the name resolves and returns zero bytes.
VARIABLES = ("pr", "tmmn", "tmmx", "rmin", "rmax", "sph", "vs", "srad", "vpd", "pet", "etr",
             "fm1000", "fm100", "bi", "erc", "th")

# NCSS variable names, and the aggregate file each lives in. Taken from `pygridmet.core.GridMET.long_names` so the mapping stays the library's rather than ours.
LONG_NAMES = {
    "pr": "precipitation_amount", "tmmn": "daily_minimum_temperature",
    "tmmx": "daily_maximum_temperature", "rmin": "daily_minimum_relative_humidity",
    "rmax": "daily_maximum_relative_humidity", "sph": "daily_mean_specific_humidity",
    "vs": "daily_mean_wind_speed", "srad": "daily_mean_shortwave_radiation_at_surface",
    "vpd": "daily_mean_vapor_pressure_deficit",
    "pet": "daily_mean_reference_evapotranspiration_grass",
    "etr": "daily_mean_reference_evapotranspiration_alfalfa",
    "fm1000": "dead_fuel_moisture_1000hr", "fm100": "dead_fuel_moisture_100hr",
    "bi": "daily_mean_burning_index_g", "erc": "daily_mean_energy_release_component-g",
    "th": "daily_mean_wind_direction",
}
# THE DAY BOUNDARY, MEASURED -- and the measurement says the obvious repair is wrong.
#
# gridMET's daily precipitation accumulates over a window that is NOT our calendar day. Correlating
# gridMET `pr` against AORC hourly precipitation aggregated at all 24 possible offsets, three corn-belt
# sites over 2020, the peak sits at 13-15 h UTC and it is sharp: r = 0.969 at the peak against 0.474 at a
# UTC calendar day and 0.798 at ours. So gridMET's day runs approximately 14:00 UTC to 14:00 UTC.
#
# Our observation day is Central Standard year-round (`io.STANDARD_TZ`), i.e. 06:00 UTC to 06:00 UTC. The
# two windows are therefore EIGHT HOURS APART, and gridMET day D overlaps our day D by sixteen of its
# twenty-four hours and our day D+1 by the other eight.
#
# WHICH IS WHY RELABELLING IS NOT THE FIX. Sixteen of twenty-four is already the best integer-day
# assignment available, and shifting moves all of it rather than the misaligned third: measured against
# AORC on our own boundary, the current label scores r = 0.798, a +1-day shift 0.385, a -1-day shift 0.137.
# A `day_convention="local"` parameter that shifted the label would make the data materially worse.
#
# The residual misalignment is real and irreducible by any relabelling -- the mean absolute daily
# disagreement is 69% of total rainfall at Ames in 2020. The only true repair is to re-derive
# precipitation from AORC hourly on our own boundary; short of that, the honest move is to publish the
# offset so a feature can lag deliberately instead of assuming alignment.
#
# ONLY PRECIPITATION'S CONVENTION IS MEASURABLE THIS WAY. `tmmx` is insensitive to windowing (its
# correlation plateau spans offsets 4-17, all within 0.005) and `srad` more so (offsets 0-15 all at
# r = 1.000, because radiation is zero at night so any night boundary captures the same daylight).
# `tmmn`'s plateau is 10-14. None of them CONTRADICTS 14 h; none of them confirms it either.
GRIDMET_DAY_OFFSET_H = 14
GRIDMET_DAY_OFFSET_MEASURED = ("pr",)      # the variables the offset above is actually evidenced by

NCSS = "http://thredds.northwestknowledge.net:8080/thredds/ncss"
_HTTP_TIMEOUT = 600

GRID_PATH_NAME = "grid.parquet"

# STORE LAYOUT: `weather/grid.parquet` is the shared cell registry; `weather/daily/` holds the daily
# quarters this module writes; `weather/pentad/` is reserved for the pentad drought aggregates (spi/
# spei/eddi/pdsi), which ride the SAME 585x1386 lattice (verified against the endpoint: identical
# corner coordinates) but a 5-day time axis that must never share files with daily rows.
DAILY_DIR_NAME = "daily"


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
    return config.ACQ_WEATHER / DAILY_DIR_NAME / "_pending" / f"gridmet_{year}-{month:02d}.parquet"


def _quarter_of(month: int) -> int:
    return (month - 1) // _QUARTER_MONTHS + 1


def _quarter_months(year: int, q: int) -> list[tuple[int, int]]:
    return [(year, m) for m in range((q - 1) * _QUARTER_MONTHS + 1, q * _QUARTER_MONTHS + 1)]


def _backfill_path(year: int, q: int):
    """Where a quarter's ADDED columns land before they are merged into it.

    `WORK`, quarter-grained -- never `_pending/`, month-grained. `compact` globs that directory and turns whatever it finds into a quarter, so a column slice sitting there is aimed squarely at the failure the carry-over fix just closed; and anything under `ACQUIRED` is manifested, so an interrupted run would register staging as `added` then `removed`, which is a drift conflict.
    """
    return config.WORK / "weather_backfill" / f"gridmet_{year}Q{q}.parquet"


def _quarter_path(year: int, q: int):
    return config.ACQ_WEATHER / DAILY_DIR_NAME / f"gridmet_{year}Q{q}.parquet"


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


def _to_frame(ds, variables=None) -> pd.DataFrame:
    """xarray dataset -> long observations. float32 throughout: gridMET's precision does not justify eight bytes.

    `variables` names which columns are values; it defaults to the whole declaration, which is right for a full fetch and wrong for a backfill -- a five-variable slice must drop rows null across ITS five, not across sixteen it never asked for.
    """
    df = ds.to_dataframe().reset_index()
    latc = next(c for c in df.columns if c.lower() in ("lat", "latitude", "y"))
    lonc = next(c for c in df.columns if c.lower() in ("lon", "longitude", "x"))
    timec = next(c for c in df.columns if c.lower() in ("time", "day", "date"))
    df = df.rename(columns={latc: "lat", lonc: "lon", timec: "date"})
    df["cell_id"] = cell_id(df.lon, df.lat)
    df["date"] = pd.to_datetime(df.date).dt.date
    want = frozenset(variables if variables is not None else VARIABLES)
    vals = [c for c in df.columns if c in want]
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
        cached = gpd.read_parquet(_grid_path())
        # THE CELL SET IS A FUNCTION OF THE AOE, so reusing it on a moved extent freezes the region every
        # later fetch is masked to: `main` derives `keep` from this frame and `_fetch_window` ends with
        # `obs[obs.cell_id.isin(keep)]`, so new cells would never be fetched AND never be missed -- the
        # lattice check passes precisely because a consistently truncated store IS consistent. The stamp
        # was already written here and never read.
        got = str(cached.aoe_stamp.iloc[0]) if "aoe_stamp" in cached.columns and len(cached) else None
        want = config.aoe_stamp()
        if got is None or got == want:
            return cached
        # COVERAGE, NOT EQUALITY -- and here the asymmetry has teeth. A cached grid cut against a LARGER
        # extent is a superset and stands; re-enumerating it would SHRINK the cell set below the weather
        # already stored against it, orphaning rows and breaking the weights-join check. A grid cut against
        # a smaller extent genuinely misses cells and must be rebuilt.
        from shapely.geometry import box as _box

        covers = _box(*config.aoe_geometry().bounds).buffer(-1e-9).within(_box(*cached.total_bounds))
        if covers:
            print(f"  weather grid: cut against AOE {got[:12]}, current is {want[:12]} -- but it covers "
                  f"the current extent, so it stands", flush=True)
            return cached
        print(f"  weather grid: cut against AOE {got[:12]} which does NOT cover the current extent "
              f"{want[:12]} -- re-enumerating", flush=True)

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


def write_quarter(year: int, q: int, frames, allow_shrink: bool = False) -> int:
    """Z-sort a quarter's months into one file. Returns rows written.

    Written from whatever months exist and REWRITTEN as later ones land, so a reader never has to know that the current quarter is partial -- there is one layout, not two. Rewriting a 250 MB file up to three times per quarter is cheaper than making every consumer union a store with a pending directory in it.

    Peak memory is about 4.5 GB: three months is 21.5M rows and the sort holds them at once. Fine alone, not fine beside a chunk-4 pass holding the 2.6 GB catchment layer.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    t = pa.concat_tables([f for f in frames if f is not None], promote_options="default")
    if not t.num_rows:
        return 0
    t = t.append_column("zkey", pa.array(zkey(t.column("cell_id").to_numpy())))
    t = t.sort_by([("zkey", "ascending"), ("date", "ascending")])
    p = _quarter_path(year, q)

    # THE SHRINK TRIPWIRE. A quarter may gain days and may revise values, but a rewrite that DROPS a day
    # the store already held is data loss, and the drift gate cannot see it: `_parquet_probe` reads
    # `data_max_ts` only from timestamp columns, `date` here is `date32`, so a weather quarter presents no
    # temporal evidence at all and a 56% shrink classifies exactly like a routine trailing refresh. This is
    # the assertion that would have caught 2026-08-25.
    if p.exists() and not allow_shrink:
        had = set(pq.read_table(p, columns=["date"]).column("date").to_pylist())
        now = set(t.column("date").to_pylist())
        lost = sorted(had - now)
        if lost:
            raise RuntimeError(
                f"write_quarter({year}Q{q}) would drop {len(lost)} day(s) already on disk "
                f"({lost[0]} .. {lost[-1]}); pass allow_shrink=True only if the loss is intended")

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    try:
        pq.write_table(t, tmp, compression="snappy", row_group_size=_ROW_GROUP)
        tmp.replace(p)
    finally:
        tmp.unlink(missing_ok=True)      # `snapshot.manifest_rows` REFUSES a stray .tmp under the stores
    _forget_quarter()
    return t.num_rows


def compact(year: int, q: int, drop_pending: bool = True) -> int:
    """Fold every pending month of one quarter into its quarter file. Returns rows written.

    THE QUARTER ALREADY ON DISK IS AN INPUT, NOT A CASUALTY. This used to build the file from the pending months alone, which silently deleted every month that was already complete: on 2026-08-25 July was complete (31/31 days) so `fetch_month` skipped it and wrote no pending file, August was partial so it refetched, and the rewrite replaced a 152 MB two-month quarter with a 66 MB one holding August. Thirty-one days of gridMET left the store and the drift gate classified the 56% shrink as `revised_out_of_window`. Months that are NOT pending are now carried across from the existing file, and `write_quarter` refuses any replacement that would shrink the date set.

    The carried table is stripped of `zkey` because `write_quarter` appends it unconditionally, and the concat promotes: a carried 11-column quarter beside a 16-column pending month differs in schema, and the gap fills with nulls that `_covered_vars` then reports as an incomplete column.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    months = [(y, m) for y, m in _quarter_months(year, q) if _pending_path(y, m).exists()]
    if not months:
        return 0
    frames = [pq.read_table(_pending_path(y, m)) for y, m in months]

    p = _quarter_path(year, q)
    if p.exists():
        fresh = {(y, m) for y, m in months}
        keep = pq.read_table(p)
        d = pd.to_datetime(pd.Series(keep.column("date").to_pylist()))
        mask = ~pd.Series(list(zip(d.dt.year, d.dt.month))).isin(fresh)
        if mask.any():
            carried = keep.filter(pa.array(mask.to_numpy()))
            if "zkey" in carried.column_names:
                carried = carried.drop(["zkey"])
            frames.append(carried)

    n = write_quarter(year, q, frames)
    if drop_pending and n:
        for y, m in months:
            _pending_path(y, m).unlink()
    return n


def _quarter_span(year: int, q: int):
    """(first, last) date in a quarter file from row-group statistics, or None. Footer only."""
    import pyarrow.parquet as pq

    path = _quarter_path(year, q)
    if not path.exists():
        return None
    md = pq.ParquetFile(path).metadata
    names = [md.schema.column(i).name for i in range(md.num_columns)]
    if "date" not in names:
        return None
    di, lo, hi = names.index("date"), None, None
    for rg in range(md.num_row_groups):
        st = md.row_group(rg).column(di).statistics
        if st is None or not st.has_min_max:
            continue
        lo = st.min if lo is None or st.min < lo else lo
        hi = st.max if hi is None or st.max > hi else hi
    return None if lo is None else (lo, hi)


def fetch_quarter_vars(year: int, q: int, variables, keep: set[int], force: bool = False) -> int:
    """Fetch `variables` over a quarter's OWN date span into staging. Returns rows staged.

    The span comes from the file rather than the calendar, so a partial trailing quarter asks only for the days it holds -- which keeps the `400 not published yet` response a genuine error here instead of an expected one.
    """
    out = _backfill_path(year, q)
    if out.exists() and not force:
        return 0
    span = _quarter_span(year, q)
    if span is None:
        return 0
    start, end = span[0].isoformat(), span[1].isoformat()
    obs = _fetch_window(list(variables), keep, start, end)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    try:
        obs.to_parquet(tmp, index=False)
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return len(obs)


_KEY_SHIFT = 20          # cell_id < 2**20 (the 585x1386 lattice caps it at 810,809) and day-of-epoch too


def _as_i64(col):
    """An arrow column as int64, without building Python objects.

    `date32` IS days-since-epoch as int32, so casting is free and the join key needs no datetime round trip. Going through `to_pylist` instead cost 21 million Python objects per quarter -- measured at 102 s and 4.3 GB peak against 21 s and under a gigabyte this way.
    """
    import pyarrow as pa

    c = col.combine_chunks()
    if pa.types.is_date32(c.type):
        c = c.cast(pa.int32())          # arrow refuses date32 -> int64 directly; via int32 it is free
    elif pa.types.is_date64(c.type):
        c = c.cast(pa.int64())          # milliseconds; the caller divides
    return c.cast(pa.int64()).to_numpy(zero_copy_only=False)


def _as_f32(col):
    """An arrow float column as a float32 numpy array, zero-copy where arrow allows it."""
    return col.combine_chunks().to_numpy(zero_copy_only=False).astype("float32", copy=False)


def _pack_key(cell_id, date_days):
    """(cell_id, days-since-epoch) -> one int64. Both fields are statically bounded below 2**20."""
    import numpy as np

    c = np.asarray(cell_id, dtype="int64")
    d = np.asarray(date_days, dtype="int64")
    if c.size and (c.max() >= 1 << _KEY_SHIFT or c.min() < 0):
        raise ValueError(f"cell_id out of packing range: max {c.max()}")
    if d.size and (d.max() >= 1 << _KEY_SHIFT or d.min() < 0):
        raise ValueError(f"date out of packing range: max {d.max()}")
    return (c << _KEY_SHIFT) | d


def merge_quarter_vars(year: int, q: int, max_missing: float = 0.0, drop_staging: bool = True) -> int:
    """Add the staged columns to an existing quarter, row group by row group. Returns rows written.

    A LEFT JOIN ON (cell_id, date) AGAINST THE FILE'S OWN ROW ORDER, never a re-sort. The quarter is sorted `(zkey, date)` and `read_cells` prunes on the row groups' `zkey` statistics, so re-sorting to add a column would rebuild that index for nothing. Each row group is read alone (~45 MB), gains its columns by `searchsorted` into the staged table, and is written straight out; the 1.28 GB quarter is never resident.

    IT REFUSES A PARTIAL COLUMN, AND DECIDES BEFORE WRITING A BYTE. Pass one reads only `cell_id` and `date` and counts rows the staging cannot answer, because a column that is null for part of the record is indistinguishable in the footer's NAMES from a complete one and would be read downstream as data. `max_missing` is the escape hatch and defaults to none.
    """
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    src, dst = _backfill_path(year, q), _quarter_path(year, q)
    if not src.exists() or not dst.exists():
        return 0

    stage = pq.read_table(src)
    new_cols = [c for c in stage.column_names if c in VARIABLES]
    if not new_cols:
        return 0
    s_key = _pack_key(_as_i64(stage.column("cell_id")), _as_i64(stage.column("date")))
    if len(np.unique(s_key)) != len(s_key):
        raise ValueError(f"staged {year}Q{q} has duplicate (cell_id, date) rows")
    order = np.argsort(s_key)
    s_key = s_key[order]
    s_vals = {c: _as_f32(stage.column(c))[order] for c in new_cols}

    pf = pq.ParquetFile(dst)
    n_rows = pf.metadata.num_rows

    def _lookup(tbl):
        key = _pack_key(_as_i64(tbl.column("cell_id")), _as_i64(tbl.column("date")))
        pos = np.searchsorted(s_key, key)
        pos_c = np.clip(pos, 0, len(s_key) - 1)
        hit = s_key[pos_c] == key
        return pos_c, hit

    misses = 0
    for rg in range(pf.metadata.num_row_groups):
        _, hit = _lookup(pf.read_row_group(rg, columns=["cell_id", "date"]))
        misses += int((~hit).sum())
    if misses > max_missing * n_rows:
        raise RuntimeError(
            f"{year}Q{q}: staging cannot answer {misses:,} of {n_rows:,} rows "
            f"({misses / n_rows:.2%}); refusing to write a partly-null column")

    keep_names = ["cell_id", "date"] + [v for v in VARIABLES
                                        if v in set(pf.schema_arrow.names) | set(new_cols)] + ["zkey"]
    tmp = dst.with_name(dst.name + ".tmp")
    writer = None
    try:
        for rg in range(pf.metadata.num_row_groups):
            tbl = pf.read_row_group(rg)
            pos, hit = _lookup(tbl)
            for c in new_cols:
                arr = pa.array(np.where(hit, s_vals[c][pos], np.float32(0.0)), mask=~hit)
                tbl = (tbl.set_column(tbl.column_names.index(c), c, arr)
                       if c in tbl.column_names else tbl.append_column(c, arr))
            tbl = tbl.select([c for c in keep_names if c in tbl.column_names])
            if writer is None:
                writer = pq.ParquetWriter(tmp, tbl.schema, compression="snappy")
            writer.write_table(tbl)
        if writer is not None:
            writer.close()
            writer = None
            tmp.replace(dst)
    finally:
        if writer is not None:
            writer.close()
        tmp.unlink(missing_ok=True)          # a stray .tmp under the stores REFUSES the next snapshot
    _forget_quarter()
    if drop_staging:
        src.unlink(missing_ok=True)
    return n_rows


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
    for p in sorted((config.ACQ_WEATHER / DAILY_DIR_NAME).glob("gridmet_*Q*.parquet")):
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


def _covered_vars(path) -> frozenset:
    """Which VARIABLES this quarter carries with EVERY row populated. Footer only -- no row is read.

    NAMES ARE NOT COVERAGE. Once `compact` carries an 11-column quarter across a 16-column pending month the concat promotes and fills the gap with NULLS, so the footer lists `fm1000` while two thirds of the file is empty; a name check calls that done. Null is the right predicate because gridMET's over-water NaN is a float VALUE whose null count is zero -- measured across all 75 quarters, every column reports 0 nulls -- so a null in a value column means one thing: no row was ever fetched there. Cost over the whole store is 0.09 s.
    """
    import pyarrow.parquet as pq

    if not path.exists():
        return frozenset()
    try:
        md = pq.ParquetFile(path).metadata
    except Exception:                                  # noqa: BLE001 -- an unreadable file covers nothing
        return frozenset()
    names = [md.schema.column(i).name for i in range(md.num_columns)]
    nulls = {n: 0 for n in names}
    for rg in range(md.num_row_groups):
        g = md.row_group(rg)
        for i in range(md.num_columns):
            nulls[names[i]] += g.column(i).statistics.null_count or 0
    return frozenset(v for v in VARIABLES if v in nulls and nulls[v] == 0)


@lru_cache(maxsize=8)
def _quarter_vars(year: int, q: int) -> frozenset:
    """`_covered_vars` for a quarter, cached beside `_quarter_dates` and cleared with it."""
    return _covered_vars(_quarter_path(year, q))


def _forget_quarter() -> None:
    """Drop every cached read of the quarter files. One call site for both caches, because a cache the writer forgets to clear reports the state before the write."""
    _quarter_dates.cache_clear()
    _quarter_vars.cache_clear()


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


def _complete(year: int, month: int, variables=None) -> bool:
    """Is this month fully on disk, in its pending file or already folded into its quarter?

    THE TRAILING MONTH IS PARTIAL AND MUST NOT BE TRUSTED. gridMET publishes the current month as it goes, so a file written on the 14th holds twelve days -- and an exists-check then treats it as done forever, leaving a permanent hole at the end of the record that nothing downstream can see. Only the date column is read, and the quarter's dates are cached so asking about three months costs one read.
    """
    import calendar

    import pyarrow.parquet as pq

    need = calendar.monthrange(year, month)[1]
    q = _quarter_of(month)
    have = {d for d in _quarter_dates(year, q) if d.year == year and d.month == month}
    if len(have) >= need:
        # DATES ARE ONLY HALF OF DONE. A month can hold every day and still be missing a column the
        # declaration names -- which is exactly the state the store was in when `fm1000` and its four
        # siblings were added to VARIABLES and every month reported complete, so nothing was ever fetched.
        want = frozenset(variables if variables is not None else VARIABLES)
        return want <= _quarter_vars(year, q)
    p = _pending_path(year, month)
    if not p.exists():
        return False
    try:
        return pq.read_table(p, columns=["date"]).column("date").to_pandas().nunique() >= need
    except Exception:
        return False


class _NotPublished(Exception):
    """NCSS answered 400: the requested window runs past the end of the published record."""


def _fetch_window(variables, keep: set[int], start: str, end: str):
    """Every requested variable over the AOE for [start, end], one NCSS request per (variable, tile).

    THE WINDOW IS A PARAMETER, NOT A MONTH. Measured on the cornbelt tile, one month costs 4.5 s and one quarter 6.2 s -- the request carries ~2.6 s of fixed cost against ~0.6 s per month of payload, so three monthly requests cost 13.5 s for what one quarterly request delivers in 6.2 s. The monthly split exists for MEMORY (a year of sixteen variables is 98M rows and needed two resident copies), which a five-variable quarter does not approach; a caller that knows its width can therefore ask for a wider window.
    """
    import urllib.error

    import xarray as xr

    frames = []
    for bx in tiles():
        try:
            ds = xr.merge([_fetch_tile(v, bx, start, end) for v in variables])
        except urllib.error.HTTPError as e:
            # NCSS answers 400 for a window past the end of the record. That is "not published yet", not a
            # failure -- the loop runs to the current year, and the last months of it do not exist yet.
            if e.code == 400:
                raise _NotPublished(f"{start}..{end}") from e
            raise
        frames.append(_to_frame(ds, variables))
    # The seed boxes overlap, so a cell in the overlap arrives twice; `cell_id` is positional, which makes
    # the duplicate exact.
    obs = pd.concat(frames, ignore_index=True).drop_duplicates(["cell_id", "date"])
    return obs[obs.cell_id.isin(keep)]


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

    try:
        obs = _fetch_window(VARIABLES, keep, start, end)
    except _NotPublished:
        return -1

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    obs.to_parquet(tmp, index=False)
    tmp.replace(p)
    _forget_quarter()
    return len(obs)


def backfill(variables=None, force: bool = False, limit: int | None = None) -> dict:
    """Add missing declared variables to every quarter already on disk. Returns {quarter: rows}.

    THE UNIT IS THE QUARTER, and it is resumable at that grain: staging and merge are per file, each merge is atomic, and a quarter that already carries everything is skipped by the same footer scan the verify check uses. An interrupted run resumes exactly where it stopped.

    This exists because `VARIABLES` can grow. Before it, adding one meant `force=True` -- redownloading every variable for every month, fourteen hours with no checkpoint -- because `_complete` counted dates and never noticed a missing column.
    """
    grid = aoe_cells()
    keep = set(grid.cell_id.tolist())
    want = frozenset(variables if variables is not None else VARIABLES)
    todo = []
    for y, q, path in _quarters_on_disk():
        gap = sorted(want - _covered_vars(path))
        if gap or force:
            todo.append((y, q, gap or sorted(want)))
    if limit is not None:
        todo = todo[:limit]
    if not todo:
        print("  weather backfill: every quarter already carries every declared variable", flush=True)
        return {}
    names = sorted({v for _, _, g in todo for v in g})
    print(f"  weather backfill: {len(todo)} quarter(s) missing {names}", flush=True)

    written = {}
    for i, (y, q, gap) in enumerate(todo, 1):
        t0 = time.time()
        staged = fetch_quarter_vars(y, q, gap, keep, force=force)
        rows = merge_quarter_vars(y, q)
        written[f"{y}Q{q}"] = rows
        print(f"    {y}Q{q}: staged {staged:,}, merged {rows:,} ({time.time() - t0:.0f}s) "
              f"[{i}/{len(todo)}]", flush=True)
    return written


def _compact_year(year: int) -> None:
    """Fold whatever this year has pending into its quarter files. Quiet when there is nothing to do."""
    for q in range(1, 5):
        if any(_pending_path(y, m).exists() for y, m in _quarter_months(year, q)):
            n = compact(year, q)
            if n:
                print(f"    {year}Q{q}: compacted to {n:,} rows "
                      f"({_quarter_path(year, q).stat().st_size / 1e6:.0f} MB)", flush=True)
    _forget_quarter()


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

    # WIDENING `VARIABLES` MUST NOT NEED A HUMAN TO REMEMBER. `fetch_month` short-circuits any month older
    # than the recheck window before `_complete` -- the function carrying the variable test -- is ever
    # called, so a declared-but-unfetched column is invisible to the ordinary pass. Backfill closes that,
    # and closes it HERE rather than in a REPL: a fix reachable only by hand is a fix that runs once.
    backfill()
    return written


# ---- verification ------------------------------------------------------------------------------------
# The weather store is the largest in the build and registered NO checks until 2026-08-26, which is how a
# quarter lost a month unnoticed. All three read footers only -- the whole store scans in well under a second.

def _quarters_on_disk():
    """(year, quarter, path) for every quarter file, ascending."""
    out = []
    d = config.ACQ_WEATHER / DAILY_DIR_NAME
    for p in sorted(d.glob("gridmet_*Q*.parquet")):
        stem = p.stem.replace("gridmet_", "")
        try:
            out.append((int(stem[:4]), int(stem[-1]), p))
        except ValueError:
            continue
    return out


@verify.check("a3", "every weather quarter carries every variable the registry declares")
def _check_weather_variables():
    """The completeness invariant made executable: a declared variable that is absent, or present but null for part of the record, is not carried.

    This is the check that would have reported the five fire-weather variables as missing the moment they were declared, instead of a re-run silently doing nothing.
    """
    qs = _quarters_on_disk()
    if not qs:
        return None, "no weather store yet"
    want, missing = frozenset(VARIABLES), {}
    for y, q, p in qs:
        gap = want - _covered_vars(p)
        if gap:
            missing[f"{y}Q{q}"] = sorted(gap)
    if not missing:
        return True, f"{len(qs)} quarter(s), all carrying {len(want)} variable(s)"
    names = sorted({v for g in missing.values() for v in g})
    return False, f"{len(missing)} of {len(qs)} quarter(s) missing {names}; e.g. {sorted(missing)[:3]}"


@verify.check("a3", "no weather quarter starts late -- a quarter begins on its first calendar day")
def _check_weather_quarter_starts():
    """Every quarter except the record's first begins on its own first calendar day.

    THIS IS THE ONE THAT SEES A DELETED MONTH. The lattice check cannot: dropping July whole left 24 days x
    231,080 cells, which is a perfectly valid smaller lattice. What a deletion DOES leave is a quarter that
    starts late -- 2026Q3 beginning on 2026-08-01 instead of 2026-07-01 -- and that is detectable from one
    footer statistic. A quarter may legitimately END early (the current one is partial), so only the start
    is asserted, and the earliest quarter on disk is exempt because the record has to start somewhere.
    """
    import pyarrow.parquet as pq

    qs = _quarters_on_disk()
    if len(qs) < 2:
        return None, "no weather store yet" if not qs else "one quarter; nothing to compare"
    late = {}
    for y, q, p_ in qs[1:]:                       # qs is ascending; the first quarter is exempt
        md = pq.ParquetFile(p_).metadata
        names = [md.schema.column(i).name for i in range(md.num_columns)]
        if "date" not in names:
            continue
        di = names.index("date")
        lo = None
        for rg in range(md.num_row_groups):
            st = md.row_group(rg).column(di).statistics
            if st is not None and st.has_min_max:
                lo = st.min if lo is None or st.min < lo else lo
        if lo is None:
            continue
        want = dt.date(y, (q - 1) * _QUARTER_MONTHS + 1, 1)
        if lo > want:
            late[f"{y}Q{q}"] = f"starts {lo}, expected {want} ({(lo - want).days} day(s) missing)"
    if not late:
        return True, f"{len(qs)} quarter(s), none starting late"
    return False, f"{len(late)} quarter(s) start late: {dict(list(late.items())[:3])}"


@verify.check("a3", "every weather quarter is an exact cell x day lattice")
def _check_weather_lattice():
    """Rows == (cells carrying data) x (days in the span), asserted without reading a row.

    THE EXPECTED CELL COUNT COMES FROM THE DATA, NOT FROM `grid.parquet`. The grid holds every cell whose
    FOOTPRINT intersects the AOE -- 267,906 -- while only 231,080 ever carry values: gridMET returns all-NaN
    over ocean and beyond CONUS, and `_to_frame` drops rows null across every variable. Checking against the
    grid's count therefore fails on a perfectly healthy store, which is what it did on first writing.

    Two properties, and neither subsumes the other. Divisibility by the day span catches rows that were
    dropped or duplicated inside a quarter; agreement of the implied cell count ACROSS quarters catches a
    quarter built against a different lattice, which is what a partial rewrite produces. A quarter may be
    short at the ends -- the record starts, the current one is partial -- so the span is taken from the file
    rather than from the calendar.
    """
    import pyarrow.parquet as pq

    qs = _quarters_on_disk()
    if not qs:
        return None, "no weather store yet"
    bad, implied = {}, {}
    for y, q, p_ in qs:
        md = pq.ParquetFile(p_).metadata
        names = [md.schema.column(i).name for i in range(md.num_columns)]
        if "date" not in names:
            continue
        di = names.index("date")
        lo = hi = None
        for rg in range(md.num_row_groups):
            st = md.row_group(rg).column(di).statistics
            if st is None or not st.has_min_max:
                continue
            lo = st.min if lo is None or st.min < lo else lo
            hi = st.max if hi is None or st.max > hi else hi
        if lo is None:
            continue
        span = (hi - lo).days + 1
        if md.num_rows % span:
            bad[f"{y}Q{q}"] = f"{md.num_rows:,} rows over a {span}-day span ({lo}..{hi}) is not a lattice"
        else:
            implied[f"{y}Q{q}"] = md.num_rows // span
    counts = set(implied.values())
    if len(counts) > 1:
        common = max(counts, key=lambda c: sum(v == c for v in implied.values()))
        odd = {k: v for k, v in implied.items() if v != common}
        bad.update({k: f"{v:,} cells against {common:,} everywhere else" for k, v in list(odd.items())[:3]})
    if not bad:
        return True, f"{len(qs)} quarter(s), exact against {counts.pop():,} cells" if counts else "no quarters"
    return False, f"{len(bad)} quarter(s) not a consistent lattice: {dict(list(bad.items())[:2])}"


# How far behind the present a time-partitioned store may fall before it is reported frozen. GENEROUS on
# purpose: gridMET publishes with a few days' lag and a check that cries wolf gets ignored, which is worse
# than not having it. The failure being guarded is a store stuck MONTHS back, not one a week behind.
STALE_DAYS = 21


@verify.check("a3", "the daily weather store reaches the present")
def _check_weather_current():
    """A store frozen at an old partition is valid in every other respect, which is why this exists.

    The other three checks all pass on a frozen store: its variables are complete, its quarters start on time, and its lattice is exact -- for the partitions it has. Nothing else asks whether the newest one is recent, so a fetch guard that skipped on existence alone could stop the store in 2026Q3 forever and every check would stay green. That is not hypothetical: the same shape was found and fixed in the pentad and WQX stores on 2026-08-31.
    """
    import datetime as _dt

    import pyarrow.parquet as pq

    ps = sorted((config.ACQ_WEATHER / DAILY_DIR_NAME).glob("gridmet_*Q*.parquet"))
    if not ps:
        return None, "no daily store yet"
    best = None
    for p in ps[-2:]:
        pf = pq.ParquetFile(p)
        i = pf.schema_arrow.names.index("date")
        md = pf.metadata
        for rg in range(md.num_row_groups):
            st = md.row_group(rg).column(i).statistics
            if st is not None and st.has_min_max and st.max is not None:
                v = pd.Timestamp(st.max).date()
                best = v if best is None or v > best else best
    if best is None:
        return None, "no readable dates"
    lag = (_dt.date.today() - best).days
    return lag <= STALE_DAYS, f"newest observation {best} ({lag} day(s) behind; limit {STALE_DAYS})"
