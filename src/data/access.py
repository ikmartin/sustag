"""Single unified read surface for site data (merges the old per-subpackage access.py).

The runtime read path -- must NOT import src/build. Reads the global-grain tables in
src/data/interim (crops_global, surplus_global, weather_global, grid_global) and the per-site
water/metadata in src/data/processed, and (Phase 4) joins them to a thin per-site membership
view from site_view.py into a SiteData.

This file grows by section as the migration lands:
  * water   -- nitrate target + site metadata (this phase)
  * crops / surplus / weather live views + SiteData + get_data  (Phase 4)

Data dirs resolve to src/data/{processed,interim} when present, else fall back to the legacy
sustag/data tree, so accessors work at every stage of the migration.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

_DATA = Path(__file__).resolve().parent               # src/data
_INTERIM = _DATA / "interim"
_PROC_WATER = _DATA / "processed" / "water"
_PROC_BASINS = _DATA / "processed" / "basins"
_PROC_AUX = _DATA / "processed" / "aux"
_PROC_MAP = _DATA / "processed" / "map_overlays"


# ── water: dirs (new processed/ layout, else legacy) ──────────────────────────

def _water_data_dir() -> Path:
    """Per-site nitrate parquets."""
    return _PROC_WATER / "data"


def _water_meta_dir() -> Path:
    """Water metadata CSVs."""
    return _PROC_WATER / "meta"


# ── water: metadata + site list ───────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_metadata() -> pd.DataFrame:
    """Full site location metadata (site_uid, longitude, latitude, state, ...)."""
    return pd.read_csv(_water_meta_dir() / "site_location_metadata.csv")


def get_site_ids() -> list:
    """All site UIDs (WQS* = IWQIS, USGS-* = USGS-NWIS)."""
    return list(get_metadata()["site_uid"].unique())


def get_location(site_uid: str) -> list:
    """Sensor location as [lon, lat]."""
    df = get_metadata()
    return df[df["site_uid"] == site_uid][["longitude", "latitude"]].values[0].tolist()


# ── water: nitrate time series ────────────────────────────────────────────────

def get_water(site_uid: str) -> pd.DataFrame:
    """The site's full nitrate time series (datetime-indexed, UTC-aware)."""
    return pd.read_parquet(_water_data_dir() / f"{site_uid}_water.parquet")


def get_all_water() -> pd.DataFrame:
    """Every site's nitrate concatenated; a plain-index frame with a site_uid-less body
    (matches the legacy get_all_water shape)."""
    dfs = {uid: get_water(uid) for uid in get_site_ids()}
    return pd.concat(dfs, names=["uid", "datetime"]).reset_index(level=1).reset_index(drop=True)


def aggregate_by_interval(site_uid=None, df=None, value_col="nitrate_con", interval="1D", agg_func="mean"):
    """Resample a site's (or a given frame's) series by a pandas offset (e.g. '1D', '1W')."""
    if df is None:
        if site_uid is None:
            raise ValueError("provide either df or site_uid")
        df = get_water(site_uid)
    return df[value_col].resample(interval).agg(agg_func)


# ── water: statistics ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _stats_df() -> pd.DataFrame:
    path = _water_meta_dir() / "site_statistics.csv"
    if not path.exists():
        raise FileNotFoundError(f"Site statistics not found at {path}. Run the water builder.")
    return pd.read_csv(path)


def get_all_stats() -> pd.DataFrame:
    """Per-site nitrate stats (sparsity, start_date, last_date, lifespan)."""
    return _stats_df()


def get_stats(site_uid: str) -> pd.DataFrame:
    """One-row stats frame for a site."""
    return _stats_df()[_stats_df()["site_uid"] == site_uid]


# ── basins: dirs (new processed/ layout, else legacy) ─────────────────────────

def _basins_data_dir() -> Path:
    return _PROC_BASINS / "data"


def _basins_meta_dir() -> Path:
    return _PROC_BASINS / "meta"


# ── basins: read ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_basin_metadata() -> pd.DataFrame:
    """preferred_basin.csv -- the preferred basin choice + flags per site.

    Renamed from the legacy basins.get_metadata() to avoid colliding with the water
    get_metadata() (site locations) in this unified read surface.
    """
    path = _basins_meta_dir() / "preferred_basin.csv"
    if not path.exists():
        raise FileNotFoundError(f"preferred_basin.csv not found at {path}. Run the basins builder.")
    return pd.read_csv(path)


def get_basin(site_uid: str, type: int = 0) -> gpd.GeoDataFrame:
    """Basin polygon for site_uid. type=0 -> preferred (via preferred_basin.csv); 1/2/3/4 -> that version."""
    if type == 0:
        row = get_basin_metadata()
        row = row[row["site_uid"] == site_uid]
        if row.empty:
            raise KeyError(f"No preferred basin entry for {site_uid}.")
        fname = row.iloc[0]["basin_name"]
    else:
        fname = f"{site_uid}_basin{type}.parquet"
    path = _basins_data_dir() / fname
    if not path.exists():
        raise FileNotFoundError(f"No basin file: {path.name}")
    return gpd.read_parquet(path)


@lru_cache(maxsize=1)
def get_all_basins() -> gpd.GeoDataFrame:
    """The preferred basin for every site, concatenated (cached)."""
    meta = get_basin_metadata()
    gdfs = []
    for _, row in meta.iterrows():
        p = _basins_data_dir() / row["basin_name"]
        if p.exists():
            gdfs.append(gpd.read_parquet(p))
    if not gdfs:
        raise FileNotFoundError("No preferred basin files found.")
    return gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs)


@lru_cache(maxsize=1)
def get_all_basins_union() -> gpd.GeoDataFrame:
    """Dissolved union of all preferred basins as a single-row frame (+ area_km2), cached."""
    union = get_all_basins().dissolve()[["geometry"]].reset_index(drop=True)
    union["area_km2"] = union.to_crs("EPSG:5070").area / 1e6
    return union


def update_basin(site_uid: str, params: dict = None, basin_geom=None) -> None:
    """Update preferred_basin.csv for site_uid and optionally save a custom basin4 (widget use).

    Writes to the resolved basins meta/data dirs and clears the basin read caches.
    """
    params = dict(params or {})
    meta_path = _basins_meta_dir() / "preferred_basin.csv"
    if not meta_path.exists():
        raise FileNotFoundError("preferred_basin.csv not found. Run the basins builder.")

    df = pd.read_csv(meta_path)
    mask = df["site_uid"] == site_uid
    if not mask.any():
        raise KeyError(f"No entry for {site_uid} in preferred_basin.csv.")

    if basin_geom is not None:
        if isinstance(basin_geom, dict):
            if basin_geom.get("type") == "FeatureCollection":
                gdf = gpd.GeoDataFrame.from_features(basin_geom["features"], crs="EPSG:4326")
            else:
                from shapely.geometry import shape
                gdf = gpd.GeoDataFrame(geometry=[shape(basin_geom)], crs="EPSG:4326")
        else:
            gdf = basin_geom
        gdf.to_parquet(_basins_data_dir() / f"{site_uid}_basin4.parquet")
        params["basin_name"] = f"{site_uid}_basin4.parquet"
        params["basin_type"] = 4

    for col, val in params.items():
        if col not in df.columns:
            df[col] = None
        df.loc[mask, col] = val
    df.to_csv(meta_path, index=False)

    get_basin_metadata.cache_clear()
    get_all_basins.cache_clear()
    get_all_basins_union.cache_clear()


# ── aux: basin-containment graph ──────────────────────────────────────────────

def get_basin_graph(immediate_only: bool = False):
    """Basin-containment DiGraph: edge child -> parent means child's sensor lies inside parent's
    basin. Every site is a node; edges (with a parent_area attr) come from
    processed/aux/basin_containment_graph.parquet. immediate_only keeps only each child's smallest
    enclosing parent (an immediate-parent forest)."""
    import networkx as nx
    edges = pd.read_parquet(_PROC_AUX / "basin_containment_graph.parquet")
    if immediate_only and not edges.empty:
        edges = edges.loc[edges.groupby("child")["parent_area"].idxmin()]
    g = nx.DiGraph()
    g.add_nodes_from(get_site_ids())
    for child, parent, parent_area in edges.itertuples(index=False):
        g.add_edge(child, parent, parent_area=parent_area)
    return g


# ── map_overlays: NHD flowlines + waterbodies (widget) ────────────────────────

@lru_cache(maxsize=1)
def get_flowlines() -> gpd.GeoDataFrame:
    """Iowa NHD flowlines (stream order >= 3), EPSG:4326."""
    return gpd.read_parquet(_PROC_MAP / "iowa_flowlines.parquet")


@lru_cache(maxsize=1)
def get_waterbodies() -> gpd.GeoDataFrame:
    """Iowa NHD waterbodies, EPSG:4326."""
    return gpd.read_parquet(_PROC_MAP / "iowa_waterbodies.parquet")


# ── global-grain tables (interim, cached) ─────────────────────────────────────

@lru_cache(maxsize=1)
def _crops_global() -> pd.DataFrame:
    return pd.read_parquet(_INTERIM / "crops_global.parquet")


@lru_cache(maxsize=1)
def _surplus_global() -> pd.DataFrame:
    return pd.read_parquet(_INTERIM / "surplus_global.parquet")


def _weather_global_files() -> dict:
    import re
    out = {}
    for p in _INTERIM.glob("weather_global_*.parquet"):
        m = re.search(r"weather_global_(\d{4})\.parquet$", p.name)
        if m:
            out[int(m.group(1))] = p
    return out


# ── per-site live views (grid_global + global tables joined to a site_view) ────

@lru_cache(maxsize=128)
def get_grid(site_uid: str) -> gpd.GeoDataFrame:
    """The site's grid, built live over grid_global (node_id, global_node_id, x, y, lat, lon,
    cell_area, dist_to_sensor, frac_cell_in_basin, geometry). Cached; treat as read-only."""
    from src.data import site_view
    lon, lat = get_location(site_uid)
    return site_view.build_site_view(get_basin(site_uid), lat, lon, label=site_uid)


def get_basin_area(site_uid: str) -> float:
    """Basin area in m^2 = Σ cell_area · frac_cell_in_basin over the site's grid."""
    g = get_grid(site_uid)
    return float(np.dot(g["cell_area"].to_numpy(), g["frac_cell_in_basin"].to_numpy()))


def _crops_for_grid(grid) -> pd.DataFrame:
    """crops_global sliced to a grid's cells (+ node_id). Grid-taking core shared by the real
    (get_crops) and virtual (build_virtual_site_data) paths."""
    return grid[["node_id", "global_node_id"]].merge(_crops_global(), on="global_node_id").sort_values(
        ["year", "node_id"], ignore_index=True)


def get_crops(site_uid: str) -> pd.DataFrame:
    """Annual crop pixel counts for the site: crops_global sliced to its cells + node_id."""
    return _crops_for_grid(get_grid(site_uid))


def _surplus_for_grid(grid) -> pd.DataFrame:
    """surplus_global sliced to a grid's cells (+ node_id). Grid-taking core."""
    return grid[["node_id", "global_node_id"]].merge(_surplus_global(), on="global_node_id").sort_values(
        ["year", "node_id"], ignore_index=True)


def get_surplus(site_uid: str) -> pd.DataFrame:
    """Annual N surplus for the site: surplus_global sliced to its cells + node_id."""
    return _surplus_for_grid(get_grid(site_uid))


_WEATHER_PAD = pd.Timedelta(days=60)  # legacy per-site weather padded the nitrate span by 60d
_WEATHER_COLS = [
    "date", "node_id", "global_node_id", "precip_in_1d", "max_temp", "min_temp",
    "max_rel_humidity", "min_rel_humidity", "vpd", "solar_rad", "evapotranspiration", "fuel_moisture_1000h",
]


def _weather_for_grid(grid, start=None, end=None) -> pd.DataFrame:
    """weather_global sliced to a grid's cells over [start, end] (+ node_id). Grid-taking core
    shared by get_weather (real) and build_virtual_site_data (virtual). With no dates, reads all
    available years."""
    g = grid[["node_id", "global_node_id"]]
    cells = g["global_node_id"].tolist()
    files = _weather_global_files()
    if start is not None and end is not None:
        years = [y for y in files if pd.Timestamp(start).year <= y <= pd.Timestamp(end).year]
    else:
        years = sorted(files)

    frames = [pd.read_parquet(files[y], filters=[("global_node_id", "in", cells)]) for y in sorted(years)]
    if not frames:
        return pd.DataFrame(columns=_WEATHER_COLS)
    w = pd.concat(frames, ignore_index=True)
    if start is not None:
        w = w[w["date"] >= pd.Timestamp(start)]
    if end is not None:
        w = w[w["date"] <= pd.Timestamp(end)]
    w = w.merge(g, on="global_node_id")
    w[["node_id", "global_node_id"]] = w[["node_id", "global_node_id"]].astype("int32")
    return w[_WEATHER_COLS].sort_values(["date", "node_id"], ignore_index=True)


def get_weather(site_uid: str, start=None, end=None) -> pd.DataFrame:
    """Daily weather for the site: weather_global sliced to its cells (+ node_id).

    Default date window = the site's nitrate span ±60 days (matching the legacy per-site weather);
    pass start/end to override.
    """
    if start is None or end is None:
        try:
            st = get_stats(site_uid).iloc[0]
            start = start or pd.to_datetime(st["start_date"]).tz_localize(None).normalize() - _WEATHER_PAD
            end = end or pd.to_datetime(st["last_date"]).tz_localize(None).normalize() + _WEATHER_PAD
        except (IndexError, KeyError):
            pass
    return _weather_for_grid(get_grid(site_uid), start, end)


# ── SiteData + get_data ───────────────────────────────────────────────────────

@dataclass
class SiteData:
    """All available data for a site, same schema as the legacy get_data() result."""
    site_uid: str
    basin: object = None
    crops: object = None
    grid: object = None
    surplus: object = None
    water: object = None
    weather: object = None
    basin_area: float = None
    sensor_location: tuple = None

    def has(self, key: str) -> bool:
        return getattr(self, key, None) is not None

    def available(self) -> list:
        return [f for f in ("basin", "crops", "grid", "surplus", "water", "weather") if self.has(f)]


def get_data(site_uid: str) -> SiteData:
    """Assemble a SiteData for a real site: live views joined to the global tables. Fields that
    fail to build (missing basin, no water, etc.) come back None rather than raising."""
    def _try(fn, *a):
        try:
            return fn(*a)
        except Exception:
            return None

    loc = _try(get_location, site_uid)
    return SiteData(
        site_uid=site_uid,
        basin=_try(get_basin, site_uid),
        crops=_try(get_crops, site_uid),
        grid=_try(get_grid, site_uid),
        surplus=_try(get_surplus, site_uid),
        water=_try(get_water, site_uid),
        weather=_try(get_weather, site_uid),
        basin_area=_try(get_basin_area, site_uid),
        sensor_location=tuple(loc) if loc is not None else None,
    )


def build_virtual_site_data(basin, sensor_lat, sensor_lon, *, weather_start=None, weather_end=None,
                            site_uid="VIRTUAL") -> SiteData:
    """Assemble a SiteData for a virtual (ungauged) site from an in-memory basin + pin location.

    The re-grained build_virtual_basin: build_site_view over grid_global, then join the SAME global
    tables (crops/surplus/weather) a real site uses -- an intersection + joins, no per-pin raster or
    overlay. water is None (ungauged). Pass weather_start/end for the target window (e.g. TARGET_YEAR
    ±2 months). Identical to get_data for a real site when handed that site's stored basin + sensor.
    """
    grid = build_site_view(basin, sensor_lat, sensor_lon, label=site_uid)
    if grid.empty:
        raise ValueError(f"No grid_global cells intersect the basin at ({sensor_lat}, {sensor_lon}).")
    basin_area = float(np.dot(grid["cell_area"].to_numpy(), grid["frac_cell_in_basin"].to_numpy()))
    return SiteData(
        site_uid=site_uid,
        basin=basin,
        crops=_crops_for_grid(grid),
        grid=grid,
        surplus=_surplus_for_grid(grid),
        water=None,
        weather=_weather_for_grid(grid, weather_start, weather_end),
        basin_area=basin_area,
        sensor_location=(sensor_lon, sensor_lat),
    )


# runtime construction of a virtual/real site grid, re-exported off the unified surface
from src.data.site_view import build_site_view  # noqa: E402,F401
