"""Paths and scope for the build. Every store root is declared here and nowhere else.

SCOPE LITERALS LIVE IN `parameters/scope.toml`, not in code: the boxes and the collection window are build INPUTS that version with intent, and a build is `build(snapshots, parameters, decisions)`. This module only loads them.

STORE ROOTS, AND THE ONE INTERIM ASYMMETRY. The layout follows the chapter's layers -- `raw/` is adopted in place and never written by the build; `acquired/` is Layer A's output; `snapshots/` holds manifests naming states of raw+acquired; `derived/` and `published/` are Layers D and P. The heavy immovable archives (raw, acquired) currently sit under `src/data/` and every fresh store sits at its final home under `data/stores/`; the final migration moves the archives and flips RAW/ACQUIRED here, touching no other module. New acquisitions append into the EXISTING acquired root so the archive stays one thing.
"""

from __future__ import annotations

import datetime as _dt
import functools
import tomllib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parent.parent

PARAMETERS = _HERE / "parameters"
DECISIONS = _HERE / "decisions"

# The two adopted archives -- interim location, flipped to data/stores at the final migration.
RAW = REPO / "data" / "stores" / "raw"
ACQUIRED = REPO / "data" / "stores" / "acquired"

# Fresh stores, at their final home from day one.
STORES = REPO / "data" / "stores"
SNAPSHOTS = STORES / "snapshots"
DERIVED = STORES / "derived"
PUBLISHED = STORES / "published"

# Layer A substores
ACQ_WATER_NATIVE = ACQUIRED / "water" / "native"
ACQ_WATER_IWQIS = ACQUIRED / "water" / "iwqis"
ACQ_METADATA = ACQUIRED / "metadata"
ACQ_USGS_GEOJSON = ACQUIRED / "metadata" / "usgs_geojson"  # raw monitoring-locations responses -- the coord_dp source
ACQ_NETWORK = ACQUIRED / "network"
ACQ_WEATHER = ACQUIRED / "weather"
ACQ_ATTRIBUTES = ACQUIRED / "attributes"
ACQ_NWM = ACQUIRED / "nwm"
ACQ_SEED_BASINS = ACQUIRED / "seed_basins"
ACQ_FACILITIES = ACQUIRED / "facilities"  # ICIS-NPDES outfalls + DMR loads + CWNS attributes

# Layer D artifacts
AOE_PATH = DERIVED / "aoe.parquet"
SENSORS_PATH = DERIVED / "sensors.parquet"  # widened per stage, never loses a row
WATER_CANONICAL = DERIVED / "water" / "canonical"
WATER_MERGED = DERIVED / "water" / "merged"  # winner-per-span assembled records, SNR-keyed
SCRUB_TALLIES_PATH = DERIVED / "scrub_tallies.parquet"  # per-(sensor, channel) sentinel/null/clamp counts
PROXIMITY_PATH = DERIVED / "proximity.parquet"  # every located pair within PROXIMITY_RADIUS_M
COVARIATES_DIR = DERIVED / "covariates"
REVIEW_DIR = DERIVED / "review"  # generated evidence: candidates, stats, auto records

# Layer P artifacts
PUB_SENSORS = PUBLISHED / "sensors.parquet"
PUB_WATER = PUBLISHED / "water"  # one file per merged record, keyed by SNR id
PUB_PROXIMITY = PUBLISHED / "proximity.parquet"
PUB_NETWORK = PUBLISHED / "network"
PUB_COMID_FEATURES = PUBLISHED / "comid_features"

EQUAL_AREA = "EPSG:5070"  # degrees-squared is not an area

PROXIMITY_RADIUS_M = 2_000.0  # the published pair table's generous radius; the merge gate is far tighter


@functools.lru_cache(maxsize=1)
def scope() -> dict:
    with open(PARAMETERS / "scope.toml", "rb") as f:
        return tomllib.load(f)


def collection_start() -> _dt.date:
    """The one configured bound of the collection window -- a DATE, not a year. There is deliberately no upper bound: an upper test against the current year silently starts excluding real stations as the calendar advances."""
    return _dt.date.fromisoformat(str(scope()["window"]["collection_start"]))


def seed_boxes() -> dict[str, tuple[float, float, float, float]]:
    """Named WGS84 boxes `[min_lon, min_lat, max_lon, max_lat]`. Overlapping -- union, never concatenate."""
    return {k: tuple(float(x) for x in v) for k, v in scope()["seed_boxes"].items()}


def in_seed_boxes(*, lon, lat):
    """Boolean mask over parallel arrays: inside ANY seed box. Vectorised, no geometry objects.

    KEYWORD-ONLY ON PURPOSE. An ancestor took (lat, lon) positionally while the geographic x,y convention is (lon, lat), and a port once transposed them silently -- 148 stations became 0 with no error. A transposition must be impossible to write, not merely discouraged.
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
    print(
        "  WARNING: no AOE on disk -- falling back to the SEED BOXES. Only the bootstrap may run here; "
        "anything clipped against this extent truncates the largest basins."
    )
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


def usgs_pats() -> list[str]:
    """Every USGS token on file, in rotation order: `usgs`, then `usgs2`, `usgs3`, ...

    Quota is per token and recovers over time, so the adapter rotates on exhaustion rather than stopping; by the time the last token is spent the first has usually rolled over. Discovered by suffix so adding a key to the TOML is the only step needed to widen the rotation.
    """
    with open(API_KEYS, "rb") as f:
        keys = tomllib.load(f)
    names = ["usgs"] + sorted((k for k in keys if k.startswith("usgs") and k != "usgs"), key=lambda k: (len(k), k))
    return [str(keys[n]) for n in names if keys.get(n)]


def ensure_dirs() -> None:
    for p in (
        ACQ_WATER_NATIVE,
        ACQ_WATER_IWQIS,
        ACQ_METADATA,
        ACQ_USGS_GEOJSON,
        ACQ_NETWORK,
        ACQ_WEATHER,
        ACQ_ATTRIBUTES,
        ACQ_NWM,
        ACQ_SEED_BASINS,
        ACQ_FACILITIES,
        SNAPSHOTS,
        WATER_CANONICAL,
        WATER_MERGED,
        COVARIATES_DIR,
        REVIEW_DIR,
        PUB_WATER,
        PUB_NETWORK,
        PUB_COMID_FEATURES,
    ):
        p.mkdir(parents=True, exist_ok=True)
