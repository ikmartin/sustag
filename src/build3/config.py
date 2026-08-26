"""Paths and scope for build3. Every store root is declared here and nowhere else.

SCOPE LITERALS LIVE IN `parameters/scope.toml`, not in code: the boxes and the window are build INPUTS that version with intent, and a build is `build(snapshots, parameters, decisions)`. This module only loads them.

Store roots follow the chapter's layers: `raw/` is adopted in place and never written by build3; `acquired/` is Layer A's output; `snapshots/` holds manifests naming states of raw+acquired; `derived/` and `published/` are Layers D and P. The old `interim2/`/`processed2/` trees belong to frozen build2 and are cross-validation diff targets only.
"""

from __future__ import annotations

import functools
import tomllib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parent.parent

PARAMETERS = _HERE / "parameters"
DECISIONS = _HERE / "decisions"

DATA = REPO / "src" / "data"
RAW = DATA / "raw"                      # adopted in place; build3 never writes here
ACQUIRED = DATA / "acquired"            # Layer A output store
SNAPSHOTS = DATA / "snapshots"          # manifests + HEAD pointer
DERIVED = DATA / "derived"              # Layer D store
PUBLISHED = DATA / "published"          # Layer P store

# Layer A substores
ACQ_WATER_NATIVE = ACQUIRED / "water" / "native"
ACQ_WATER_IWQIS = ACQUIRED / "water" / "iwqis"
ACQ_METADATA = ACQUIRED / "metadata"
ACQ_NETWORK = ACQUIRED / "network"
ACQ_WEATHER = ACQUIRED / "weather"
ACQ_ATTRIBUTES = ACQUIRED / "attributes"
ACQ_NWM = ACQUIRED / "nwm"
ACQ_SEED_BASINS = ACQUIRED / "seed_basins"
ACQ_AUTHORITY_BASINS = ACQUIRED / "authority_basins"   # NLDI basin0 per USGS sensor, fetched at a3

# Layer D artifacts
AOE_PATH = DERIVED / "aoe.parquet"
SENSORS_PATH = DERIVED / "sensors.parquet"     # widened per stage, never loses a row
WATER_CANONICAL = DERIVED / "water" / "canonical"
WATER_SITES = DERIVED / "water" / "sites"       # merged per-site series, winner-per-span, provenance per day
SITES_PATH = DERIVED / "sites.parquet"
BASINS_DIR = DERIVED / "basins"
BASINS_PATH = BASINS_DIR / "basins.parquet"          # selected polygon per site
BASIN_REACHES = BASINS_DIR / "basin_reaches.parquet"  # one row per (site, upstream reach)
GRAPH_DIR = DERIVED / "graph"
NODES_PATH = GRAPH_DIR / "nodes.parquet"
EDGES_PATH = GRAPH_DIR / "edges.parquet"
COVARIATES_DIR = DERIVED / "covariates"
REVIEW_DIR = DERIVED / "review"                # generated evidence: candidates, panels, auto records

# Layer P artifacts
PUB_SITES = PUBLISHED / "sites.parquet"
PUB_SENSORS = PUBLISHED / "sensors.parquet"
PUB_WATER = PUBLISHED / "water"
PUB_NITRATE_DAILY = PUBLISHED / "nitrate_daily.parquet"
PUB_NODES = PUBLISHED / "nodes.parquet"
PUB_EDGES = PUBLISHED / "edges.parquet"
PUB_BASINS = PUBLISHED / "basins.parquet"
PUB_BASIN_REACHES = PUBLISHED / "basin_reaches.parquet"
PUB_COMID_FEATURES = PUBLISHED / "comid_features"

EQUAL_AREA = "EPSG:5070"                # degrees-squared is not an area

# Ergonomic mirrors of the scope window; the toml is the authority.
def _window_consts():
    import tomllib as _toml

    with open(PARAMETERS / "scope.toml", "rb") as f:
        w = _toml.load(f)["window"]
    return int(w["year_start"]), int(w["year_end"])


YEAR_START, YEAR_END = _window_consts()


@functools.lru_cache(maxsize=1)
def scope() -> dict:
    with open(PARAMETERS / "scope.toml", "rb") as f:
        return tomllib.load(f)


def seed_boxes() -> dict[str, tuple[float, float, float, float]]:
    """Named WGS84 boxes `[min_lon, min_lat, max_lon, max_lat]`. Overlapping -- union, never concatenate."""
    return {k: tuple(float(x) for x in v) for k, v in scope()["seed_boxes"].items()}


def window() -> tuple[int, int]:
    w = scope()["window"]
    return int(w["year_start"]), int(w["year_end"])


def in_seed_boxes(*, lon, lat):
    """Boolean mask over parallel arrays: inside ANY seed box. Vectorised, no geometry objects.

    KEYWORD-ONLY ON PURPOSE. Its build2 ancestor took (lat, lon) positionally while the geographic x,y convention is (lon, lat), and the first port transposed them silently -- 148 stations became 0 with no error. A transposition must be impossible to write, not merely discouraged.
    """
    import numpy as np

    lon = np.asarray(lon, dtype="float64")
    lat = np.asarray(lat, dtype="float64")
    hit = np.zeros(lon.shape, dtype=bool)
    for x0, y0, x1, y1 in seed_boxes().values():
        hit |= (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)
    return hit


def seed_geometry():
    """Union of the seed boxes as one shapely geometry, lon/lat."""
    from shapely.geometry import box
    from shapely.ops import unary_union

    return unary_union([box(x0, y0, x1, y1) for x0, y0, x1, y1 in seed_boxes().values()])


@functools.lru_cache(maxsize=1)
def aoe_geometry():
    """The AOE polygon from D1's artifact, or -- LOUDLY -- the seed boxes when it does not exist yet.

    Silently returning the boxes would clip every raster to an extent that truncates the upstream half of the largest basins, and nothing downstream would notice. The fallback exists only for the bootstrap, and it says so every time it fires.
    """
    if AOE_PATH.exists():
        import geopandas as gpd

        return gpd.read_parquet(AOE_PATH).geometry.iloc[0]
    print("  WARNING: no AOE on disk -- falling back to the SEED BOXES. Only the bootstrap may run here; "
          "anything clipped against this extent truncates the largest basins.")
    return seed_geometry()


@functools.lru_cache(maxsize=1)
def aoe_stamp() -> str:
    """The extent stamp: the stored one when D1 has run, else a hash of the fallback geometry."""
    if AOE_PATH.exists():
        import geopandas as gpd

        g = gpd.read_parquet(AOE_PATH)
        if "stamp" in g.columns:
            return str(g.stamp.iloc[0])
    import hashlib

    return hashlib.sha256(aoe_geometry().wkb).hexdigest()[:12]


def equal_area_km2(geom) -> float:
    """Area of a lon/lat geometry in km2 via EPSG:5070."""
    import geopandas as gpd

    return float(gpd.GeoSeries([geom], crs=4326).to_crs(EQUAL_AREA).area.iloc[0] / 1e6)


API_KEYS = REPO / "src" / "build" / "api-keys.toml"


def usgs_pat() -> str:
    import tomllib as _toml

    with open(API_KEYS, "rb") as f:
        return _toml.load(f)["usgs"]


def usgs_pats() -> list[str]:
    """Every USGS token on file, in rotation order: `usgs`, then `usgs2`, `usgs3`, ...

    Quota is per token and recovers over time, so the adapter rotates on exhaustion rather than stopping; by the time the last token is spent the first has usually rolled over. Discovered by suffix so adding a key to the TOML is the only step needed to widen the rotation.
    """
    import tomllib as _toml

    with open(API_KEYS, "rb") as f:
        keys = _toml.load(f)
    names = ["usgs"] + sorted((k for k in keys if k.startswith("usgs") and k != "usgs"),
                              key=lambda k: (len(k), k))
    return [str(keys[n]) for n in names if keys.get(n)]


def ensure_dirs() -> None:
    for p in (ACQ_WATER_NATIVE, ACQ_WATER_IWQIS, ACQ_METADATA, ACQ_NETWORK, ACQ_WEATHER,
              ACQ_ATTRIBUTES, ACQ_NWM, ACQ_SEED_BASINS, ACQ_AUTHORITY_BASINS, SNAPSHOTS, WATER_CANONICAL, WATER_SITES, BASINS_DIR, GRAPH_DIR,
              COVARIATES_DIR, REVIEW_DIR, PUB_WATER, PUB_COMID_FEATURES):
        p.mkdir(parents=True, exist_ok=True)
