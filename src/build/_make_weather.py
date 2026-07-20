"""Build weather_global_{year}.parquet -- the global daily weather table at the IEM-cell grain.

Phase 3 of the re-grain refactor. Weather was already global in the old tree
(make_global_weather.py -> global_grid_weather_{year}.parquet); this ports it to the new
interim location and name. Per-site weather is now a *view* -- a slice of this table by
(global_node_id, date) with node_id attached -- served on the read side (see src/data/site_view
/ access), not materialized per site. Verified: a per-site slice of the global table equals the
old {uid}_weather.parquet byte for byte.

For every IEM cell in the region bbox and every day of a year, IEM precip is joined to bilinearly
interpolated gridMET meteorology on (date, global_node_id). global_node_id is the canonical IEM
grid row index, so vals[global_node_id] is a direct precip lookup and the per-site joins line up.

Output (src/data/interim/weather_global_{year}.parquet):
    date, global_node_id, precip_in_1d (in), max_temp (degC), min_temp (degC),
    max_rel_humidity (%), min_rel_humidity (%), vpd (kPa), solar_rad (W/m^2),
    evapotranspiration (mm), fuel_moisture_1000h (%).

NOTE: this is a heavy NETWORK build (gridMET + IEM downloads across all years). The existing
legacy files (data/weather/weather_global/global_grid_weather_{year}.parquet) are byte-identical
in schema, so migrating can be a copy+rename rather than a re-download:
    for f in .../global_grid_weather_YYYY.parquet -> src/data/interim/weather_global_YYYY.parquet

Usage
-----
    python -m src.build._make_weather                  # all years, skip existing
    python -m src.build._make_weather --force
    python -m src.build._make_weather --year 2017
"""

import argparse
import math
import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import xarray as xr

_THIS_DIR = Path(__file__).resolve().parent           # src/build/
_SRC = _THIS_DIR.parent                               # src/
sys.path.insert(0, str(_SRC.parent))                  # repo root on path

from src.build._make_grid import _download_shapefile, _parse_day_gdf, IEM_GRID_DATE
from src.build.config import get_region_bbox
from src.data.crs import EQUAL_AREA_CRS

_INTERIM_DIR = _SRC / "data" / "interim"
_GRIDMET_CACHE = _SRC / "data" / "raw" / "weather" / "gridMET_raw"

# Point pygridmet's NetCDF cache at raw/weather/gridMET_raw/ before importing pygridmet.
_GRIDMET_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HYRIVER_CACHE_NAME", str(_GRIDMET_CACHE / "hyriver.sqlite"))

import pygridmet  # noqa: E402
import pygridmet.pygridmet as _pgm  # noqa: E402

# gridMET is NaN over water / outside CONUS; pygridmet treats ANY NaN as a failed download and
# retries forever. We accept NaN (only land cells are sampled; near-shore cells nearest-filled).
_pgm._check_nans = lambda clm, urls, clm_files, long2abbr: (False, urls)

ALL_YEARS = list(range(2008, 2027))

_GRIDMET_RENAME = {
    "tmmx": "max_temp",
    "tmmn": "min_temp",
    "rmax": "max_rel_humidity",
    "rmin": "min_rel_humidity",
    "vpd": "vpd",
    "srad": "solar_rad",
    "pet": "evapotranspiration",
    "fm1000": "fuel_moisture_1000h",
}
_GRIDMET_VARS = list(_GRIDMET_RENAME)
_TEMP_COLS = ["max_temp", "min_temp"]  # K -> degC

_COLUMN_ORDER = [
    "date", "global_node_id", "precip_in_1d",
    "max_temp", "min_temp", "max_rel_humidity", "min_rel_humidity",
    "vpd", "solar_rad", "evapotranspiration", "fuel_moisture_1000h",
]

_MAX_TILE_DEG = 5.0
_TILE_BUFFER = 0.1  # deg, > 1 gridMET cell (~0.042 deg)


def _parse_day_values(zip_path: Path, expected_n: int) -> np.ndarray | None:
    """RAINFALL (inches) for every IEM cell, in record order (geometry-free read). Positionally
    aligned to the canonical IEM grid, so vals[global_node_id] is a cell's precip."""
    try:
        df = gpd.read_file(f"/vsizip/{zip_path}", ignore_geometry=True)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    df.columns = [c.lower() for c in df.columns]
    if "rainfall" not in df.columns or len(df) != expected_n:
        return None
    return df["rainfall"].to_numpy()


def _iem_cells() -> pd.DataFrame:
    """The entire IEM grid: global_node_id (row index), lon, lat (centroid, WGS84)."""
    day = _parse_day_gdf(_download_shapefile(IEM_GRID_DATE), IEM_GRID_DATE).to_crs(EQUAL_AREA_CRS)
    ll = day.geometry.centroid.to_crs("EPSG:4326")
    return pd.DataFrame(
        {
            "global_node_id": np.arange(len(day), dtype="int64"),
            "lon": ll.x.to_numpy(),
            "lat": ll.y.to_numpy(),
        }
    )


def _get_bygeom_retry(bbox: tuple, dates: tuple, attempts: int = 6) -> xr.Dataset:
    """get_bygeom with patient exponential-backoff retries (the gridMET NCSS server intermittently
    rejects valid requests under load)."""
    import time
    from pygridmet.exceptions import InputRangeError

    for k in range(attempts):
        try:
            return pygridmet.get_bygeom(bbox, dates, variables=_GRIDMET_VARS)
        except InputRangeError:
            raise  # dates outside gridMET's range -- retrying can't help
        except Exception:
            if k == attempts - 1:
                raise
            wait = min(30 * 2**k, 300)
            print(f"    gridMET fetch failed; retry {k + 1}/{attempts - 1} in {wait}s")
            time.sleep(wait)


def _sample_gridmet(ds: xr.Dataset, cells: pd.DataFrame) -> pd.DataFrame:
    """Bilinearly sample a gridMET dataset at the given cell centroids -> long df."""
    ds = ds.sortby("lat").sortby("lon")
    lon_da = xr.DataArray(cells["lon"].to_numpy(), dims="cell")
    lat_da = xr.DataArray(cells["lat"].to_numpy(), dims="cell")
    samp = ds.interp(lon=lon_da, lat=lat_da, method="linear")
    samp = samp.fillna(ds.interp(lon=lon_da, lat=lat_da, method="nearest"))
    samp = samp.assign_coords(cell=("cell", cells["global_node_id"].to_numpy()))

    gm = samp.to_dataframe().reset_index()
    gm = gm.drop(columns=[c for c in ("spatial_ref", "lat", "lon") if c in gm.columns])
    gm = gm.rename(columns={"time": "date", "cell": "global_node_id", **_GRIDMET_RENAME})
    gm["date"] = pd.to_datetime(gm["date"]).dt.normalize()
    for c in _TEMP_COLS:
        gm[c] = gm[c] - 273.15
    return gm


def _gridmet_range(cells: pd.DataFrame, bbox: tuple, start: str, end: str) -> pd.DataFrame:
    """gridMET vars for every (day, cell) in [start, end], tiled to stay under the NCSS size cap."""
    minx, miny, maxx, maxy = bbox
    nx = max(1, math.ceil((maxx - minx) / _MAX_TILE_DEG))
    ny = max(1, math.ceil((maxy - miny) / _MAX_TILE_DEG))
    xs = np.linspace(minx, maxx, nx + 1)
    ys = np.linspace(miny, maxy, ny + 1)
    ix = np.clip(np.searchsorted(xs, cells["lon"].to_numpy(), side="right") - 1, 0, nx - 1)
    iy = np.clip(np.searchsorted(ys, cells["lat"].to_numpy(), side="right") - 1, 0, ny - 1)
    tile = ix * ny + iy

    out = []
    for t in np.unique(tile):
        i, j = divmod(int(t), ny)
        sub = cells[tile == t]
        fetch = (xs[i] - _TILE_BUFFER, ys[j] - _TILE_BUFFER, xs[i + 1] + _TILE_BUFFER, ys[j + 1] + _TILE_BUFFER)
        ds = _get_bygeom_retry(fetch, (start, end))
        out.append(_sample_gridmet(ds, sub))
    return pd.concat(out, ignore_index=True)


def _assemble(gm: pd.DataFrame, prec: pd.DataFrame) -> pd.DataFrame:
    """Join gridMET + IEM precip, order columns, shrink to float32/int32."""
    df = gm.merge(prec, on=["date", "global_node_id"], how="left")
    df["date"] = df["date"].astype("datetime64[ms]")
    df = df[_COLUMN_ORDER].sort_values(["date", "global_node_id"], ignore_index=True)
    float_cols = df.select_dtypes("float64").columns
    df[float_cols] = df[float_cols].astype("float32")
    df["global_node_id"] = df["global_node_id"].astype("int32")
    return df


def _iem_precip_year(year: int, cells: pd.DataFrame, dates: pd.DatetimeIndex, n_ref: int) -> pd.DataFrame:
    """IEM precip (inches) for every (day, cell) in `year` via vals[global_node_id]."""
    gids = cells["global_node_id"].to_numpy()
    precip = np.full((len(dates), len(gids)), np.nan)
    for i, d in enumerate(dates):
        zp = _download_shapefile(d.date())
        if zp is None:
            continue
        vals = _parse_day_values(zp, n_ref)
        if vals is None:
            continue
        precip[i] = vals[gids]
    return pd.DataFrame(
        {
            "date": np.repeat(dates.values, len(gids)),
            "global_node_id": np.tile(gids, len(dates)),
            "precip_in_1d": precip.ravel(),
        }
    )


def build_year(year: int, cells: pd.DataFrame, bbox: tuple, n_ref: int) -> None:
    """Build weather_global_{year}.parquet, streaming one month at a time (peak memory ~1 month)."""
    cutoff = pd.Timestamp.today().normalize() - pd.DateOffset(months=1)  # gridMET publishes with a lag
    out = _INTERIM_DIR / f"weather_global_{year}.parquet"
    tmp = out.with_suffix(".tmp.parquet")
    writer = None
    total = 0
    try:
        for month in range(1, 13):
            m_start = pd.Timestamp(year=year, month=month, day=1)
            if m_start > cutoff:
                break
            m_end = min(m_start + pd.offsets.MonthEnd(0), cutoff)
            gm = _gridmet_range(cells, bbox, m_start.strftime("%Y-%m-%d"), m_end.strftime("%Y-%m-%d"))
            if gm.empty:
                continue
            dates = pd.DatetimeIndex(sorted(gm["date"].unique()))
            prec = _iem_precip_year(year, cells, dates, n_ref)
            df = _assemble(gm, prec)
            table = pa.Table.from_pandas(df, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(tmp, table.schema, compression="zstd")
            writer.write_table(table)
            total += len(df)
            print(f"    {year}-{month:02d}: {len(df):,} rows (running {total:,})")
        if writer is not None:
            writer.close()
            writer = None
    except BaseException:
        if writer is not None:
            writer.close()
        if tmp.exists():
            tmp.unlink()
        raise

    if total == 0:
        if tmp.exists():
            tmp.unlink()
        print(f"  {year}: no data")
        return
    tmp.replace(out)
    print(f"  {year}: {total:,} rows ({len(cells):,} cells x {total // len(cells)} days) -> {out.name}")


def build_weather_global(years: list[int] | None = None, force: bool = False) -> None:
    """Build/refresh the yearly global weather files (IEM cells in the region bbox)."""
    _INTERIM_DIR.mkdir(parents=True, exist_ok=True)

    all_cells = _iem_cells()  # full IEM grid (global_node_id == IEM row index)
    n_ref = len(all_cells)

    min_lon, min_lat, max_lon, max_lat = get_region_bbox()
    in_region = all_cells.lon.between(min_lon, max_lon) & all_cells.lat.between(min_lat, max_lat)
    cells = all_cells[in_region].reset_index(drop=True)

    buf = 0.1  # deg, > 1 gridMET cell so edge cells interpolate cleanly
    bbox = (min_lon - buf, min_lat - buf, max_lon + buf, max_lat + buf)
    print(f"Global weather: {len(cells):,} of {n_ref:,} IEM cells in region | bbox {tuple(round(b, 2) for b in bbox)}")

    years = years or ALL_YEARS
    failed = []
    for year in years:
        out = _INTERIM_DIR / f"weather_global_{year}.parquet"
        if out.exists() and not force:
            print(f"  {year}: exists, skipping")
            continue
        try:
            build_year(year, cells, bbox, n_ref)
        except Exception as e:
            failed.append(year)
            print(f"  {year}: FAILED ({type(e).__name__}: {str(e)[:90]}) — re-run to fill it in")
    if failed:
        print(f"\n{len(failed)} year(s) failed (likely gridMET outages): {failed}. Re-run to complete them.")


def main(force: bool = False, years: list[int] | None = None) -> None:
    build_weather_global(years=years, force=force)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Rebuild existing years.")
    parser.add_argument("--year", action="append", type=int, help="Limit to these years (repeatable).")
    args = parser.parse_args()
    main(force=args.force, years=args.year)
