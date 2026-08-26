"""The project's access boundary: everything this project reads about the world comes through here.

WHY A BOUNDARY AT ALL. `data.access` is deliberately preference-free -- it publishes every registration the build admitted and refuses to decide which are worth modeling. This project models one slice (cornbelt surface watercourses with a year of nitrate), and that slice is a preference. Putting it in one module means the preference is stated once, auditable in one place, and -- the operative rule for this project -- **no other module here imports `data.access` directly**, so "does this project require an access-layer change?" is answerable by reading one file.

KEYED ON THE SNR CODE, NEVER THE SOURCE UID. The published record is SNR-keyed; a merged bundle publishes under its fronting member's code and the other members' uids resolve to it. Keying features on a source uid would silently pick one member of a bundle and quietly drop the rest of its record.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd

# APPEND, never insert: this directory is named `xgboost`, so putting the repo root at the
# FRONT of sys.path would shadow the real xgboost library with this project. Appending keeps
# `data.access` importable while `import xgboost` still finds site-packages.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from data.access import api                                        # noqa: E402
from data.access.machinery import aggregate as agg                 # noqa: E402

import basins                                                      # noqa: E402
import config                                                      # noqa: E402


@lru_cache(maxsize=1)
def sites() -> pd.DataFrame:
    """The cohort: cornbelt surface watercourses with at least MIN_NITRATE_DAYS of published nitrate.

    One row per PUBLISHED RECORD (not per registration), so a merged bundle is one row under its fronting code. Columns: snr_id, site_uid, station_name, site_type, lat, lon, comid, n_days_nitrate.
    """
    lo_lon, lo_lat, hi_lon, hi_lat = config.seed_box()
    s = api.get_sensors()
    keep = (s.lat.between(lo_lat, hi_lat) & s.lon.between(lo_lon, hi_lon)
            & s.site_type.isin(config.SITE_TYPES)
            & s.n_days_nitrate.fillna(0).ge(config.MIN_NITRATE_DAYS)
            & s.publishes_under.notna())
    d = s[keep].copy()
    # one row per published record: a bundle's members share a code, and the fronting member carries it
    d = d.sort_values("n_days_nitrate", ascending=False).drop_duplicates("publishes_under")
    d["snr_id"] = d.publishes_under
    # basin ceiling from VAA's own total drainage area -- a lookup, not a walk, so the filter is free
    vaa = api.get_network("vaa").set_index("comid")
    d["basin_km2"] = d.comid.map(vaa.totdasqkm)
    over = d.basin_km2 > config.MAX_BASIN_KM2
    if over.any():
        print(f"  cohort: dropped {int(over.sum())} record(s) above the "
              f"{config.MAX_BASIN_KM2:,.0f} km2 basin ceiling (largest {d.basin_km2.max():,.0f})")
    d = d[~over.fillna(False)]
    cols = ["snr_id", "site_uid", "station_name", "site_type", "lat", "lon", "comid",
            "n_days_nitrate", "basin_km2"]
    return d[[c for c in cols if c in d.columns]].reset_index(drop=True)


@lru_cache(maxsize=512)
def record(code: str) -> pd.DataFrame:
    """One published record's daily frame, date-indexed. Quality masks are already applied at publication."""
    d = api.get_record(code)
    d = d.copy()
    d["date"] = pd.to_datetime(d.date)
    return d.set_index("date").sort_index()


@lru_cache(maxsize=512)
def basin(code: str) -> pd.DataFrame:
    """The site's weighted COMID set with flow distances, built and cached by `basins`."""
    row = sites().set_index("snr_id").loc[code]
    return basins.build(code, int(row.comid)) if pd.notna(row.comid) else pd.DataFrame()


def covariates(code: str, products=None, years=None) -> dict:
    """Per-basin covariate reductions at CATCHMENT grain, one frame per product."""
    wset = basin(code)
    if not len(wset):
        return {}
    products = products or config.COVARIATE_PRODUCTS
    return api.get_covariates(wset.comid.tolist(), products=tuple(products), years=years,
                              remap=config.CROP_REMAP)


def covariates_by_comid(code: str, product: str, years=None) -> pd.DataFrame:
    """The same covariate, NOT collapsed: one row per (comid, year, ...), which is what bucketing needs.

    `api.get_covariates` reduces over the whole set; the bucketed features need the per-catchment rows to group by flow distance, so this reads the product's rows directly.

    THE READ IS PUSHED DOWN TO THE PARQUET, deliberately. The obvious implementation -- read the product, filter by COMID -- materialises 265 MILLION crop rows per site and takes an 8 GB process to its knees inside ten sites (measured; the OS kills it silently). A `comid IN (...)` filter lets pyarrow skip whole row groups, so a 400-reach basin reads a few thousand rows instead of the continent.
    """
    wset = basin(code)
    if not len(wset):
        return pd.DataFrame()
    path = agg.config.PUB_COMID_FEATURES / f"{product}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"published product {product!r} is not on disk ({path})")
    comids = [int(c) for c in wset.comid.tolist()]
    year_filter = [("year", "in", [int(y) for y in years])] if years is not None else []
    # CHUNKED: an `IN` predicate carrying tens of thousands of values costs more to evaluate than the
    # scan it saves, so large basins are read in slices and concatenated.
    step = 4_000
    parts = [pd.read_parquet(path, filters=[("comid", "in", comids[i:i + step])] + year_filter)
             for i in range(0, len(comids), step)]
    return pd.concat(parts, ignore_index=True) if len(parts) > 1 else (
        parts[0] if parts else pd.DataFrame())


def weather(code: str, window=None, variables=None) -> pd.DataFrame:
    """Daily basin-mean weather over the site's whole basin."""
    wset = basin(code)
    if not len(wset):
        return pd.DataFrame()
    return weather_for_comids(wset.comid.tolist(), window=window, variables=variables)


def weather_for_comids(comids, window=None, variables=None) -> pd.DataFrame:
    """Daily weather over an arbitrary COMID subset -- what per-bucket weather needs, since the access layer's reduction collapses whatever set it is handed to one row per day."""
    if not len(comids):
        return pd.DataFrame()
    window = window or (config.collection_start(), str(pd.Timestamp.now().date()))
    return api.get_weather(list(comids), window=window,
                           variables=tuple(variables or config.WEATHER_VARS))


@lru_cache(maxsize=1)
def pooled_nitrate() -> pd.DataFrame:
    """Every cohort site's daily nitrate as one wide frame (date x snr_id) -- the cross-site features' substrate.

    The old stack called this the state-daily-wide table and cached it; here it is the cohort's own pool, so a site's "everyone except me" average is over the modeled cohort rather than over whatever happened to be in the store.
    """
    cols = {}
    for code in sites().snr_id:
        try:
            r = record(code)
        except KeyError:
            continue
        if "nitrate_mean" in r.columns:
            cols[code] = pd.to_numeric(r["nitrate_mean"], errors="coerce")
    return pd.DataFrame(cols).sort_index() if cols else pd.DataFrame()


def clear_caches() -> None:
    for f in (sites, record, basin, pooled_nitrate):
        f.cache_clear()
