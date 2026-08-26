"""Scope and paths for the new pipeline: one declaration everything inherits.

TWO EXTENTS, AND THEY ARE NOT THE SAME. `seed_cover()` is the three boxes frozen in `src/build/pipeline_config.toml [region]`, each derived from the reach network within 250 km of FLOW distance of a nitrate sensor; it is used ONLY to discover the sensors that seed the AOE. `aoe_geometry()` is the real scope -- those boxes unioned with the drainage basin of every seed -- computed by chunk 0 and read back from `aoe.parquet`. Everything downstream is collected against the AOE, not the boxes.

The distinction is load-bearing because the AOE reaches far outside its seed boxes: it runs up the Missouri into the Dakotas and eastern Montana, 4.09M km2 against the boxes' 3.00M. Clipping a raster to the boxes would silently truncate the upstream half of the largest basins.

The AOE never re-expands. Sensors found later inside that arm are used but do not enlarge it, or each pass would admit sensors whose basins admit more and the extent would walk to CONUS.

cornbelt and mississippi OVERLAP around lon -92.16..-89.14, lat 34.92..35.88, so anything iterating the cover must union and dedupe rather than concatenate -- a site in the overlap is returned by two boxes.

Outputs go to a v2 subtree (`interim2/`, `processed2/`) sharing `raw/` with the old tree, so the existing read layer keeps working until chunk 6 repoints it. Per-REGISTRATION artifacts live in `interim2/` because identity is not settled until chunk 3; `processed2/` holds only identity-unified output.
"""

from __future__ import annotations

import functools
import tomllib
from pathlib import Path

from shapely.geometry import box
from shapely.ops import unary_union

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent
_ROOT = _SRC.parent

# The old build's config is still the single home of the region declaration.
_PIPELINE_CONFIG = _SRC / "build" / "pipeline_config.toml"
API_KEYS = _SRC / "build" / "api-keys.toml"

REGION_KEYS = ("cornbelt_bbox", "ne_bbox", "mississippi_bbox")

YEAR_START, YEAR_END = 2008, 2026

# The nitrogen surplus window. The national rasters on disk run 1930-2017 annually, and everything before 2000 is DELIBERATELY UNUSED -- 2000-2017 is what the old build aggregated, and the 88-year record is only worth carrying if legacy nitrogen is in scope, which it is not. Chunk 5 reads this rather than globbing the directory, so the pre-2000 tifs can stay on disk (3.7 GB, manually acquired, expensive to re-fetch) without silently entering the feature set.
#
# Widening it is not just a number change: SOHL's 1940-2005 land-cover backcast is the series a longer surplus record would pair with, and it is dropped from the Wieczorek pull for the same reason. See `chunk1.comid_attrs.WIECZOREK_DROP`.
SURPLUS_YEAR_START, SURPLUS_YEAR_END = 2000, 2017

DATA = _SRC / "data"
RAW = DATA / "raw"                       # shared with the old tree -- reuse, never re-download
RAW_WATER = RAW / "water"
INTERIM = DATA / "interim2"
PROCESSED = DATA / "processed2"

# Per-REGISTRATION, keyed on site_uid, which never changes. Identity only regroups these; it never rewrites them.
WATER_NATIVE = INTERIM / "water" / "native"    # native resolution, every parameter
WATER_DAILY = INTERIM / "water" / "daily"      # standard-time-day max nitrate; input to identity
WATER_CANONICAL = INTERIM / "water" / "canonical"  # chunk 2: daily statistics on the canonical channel set
WATER_IWQIS = INTERIM / "water" / "iwqis"      # the chunk export exploded per station; rebuilt from raw/water/chunks
NLDI_BASINS = INTERIM / "basins_nldi"          # chunk 0: one upstream basin per seed
NETWORK = INTERIM / "network"                  # chunk 1: flowlines, VAA, catchments
WEATHER = INTERIM / "weather"                  # chunk 1: gridMET over the AOE
COMID_ATTRS = INTERIM / "comid_attrs"          # chunk 1: StreamCat, Wieczorek, NID, NWM

# TWO TABLES, NAMED BY THE UNIT THEY HOLD. `registrations` is one row per registration and never loses one; every chunk reads and widens it, and a row excluded from modelling keeps its place and its reason there, which is what makes an exclusion judgeable at all. `sites` is one row per TRAINABLE PHYSICAL GAUGE -- merged registrations collapsed to a single row, non-surface types and record-less sites gone -- and it is the only table a consumer should open.
#
# The split does not contradict "the build never drops a site". That rule exists because a build-time filter reaching forward for a later stage's artifact caused the old 116<->123 oscillation and made dropped sites unjudgeable. Neither happens here: nothing is lost from `registrations`, and `sites` is written LAST, when every predicate it applies already has its evidence. Filtering early on partial evidence and projecting late on complete evidence are different operations.
REGISTRATIONS_PATH = PROCESSED / "registrations.parquet"   # chunks 0-4, internal
SITES_PATH = PROCESSED / "sites.parquet"                   # chunk 6 only; the read layer's table
GAUGES_PATH = PROCESSED / "gauges.parquet"                 # chunk 3: one row per PHYSICAL GAUGE, every type
NODES_PATH = PROCESSED / "nodes.parquet"                   # chunk 4: valid graph nodes + DAG + basin
NODE_EDGES = PROCESSED / "node_edges.parquet"              # chunk 4: the edge list, one row per M -> N
# The upstream reach set behind each gauge's selected basin: one row per (gauge, reach). Written by chunk 4,
# read by chunks 5 and 6 -- hence here rather than in either.
BASIN_REACHES = PROCESSED / "basin_reaches.parquet"
AOE_PATH = PROCESSED / "aoe.parquet"

# COMPLETION MARKERS for the chunks that are not written yet. Declared here so chunk 6 can refuse to run on
# an incomplete build by checking a path rather than by trusting the order it was called in -- a gauge with no
# delineated basin has no covariates, so a `sites.parquet` assembled before chunk 4 is a cohort that looks
# finished and is not. Each becomes real when its chunk lands; until then their absence IS the gate.
BASINS = PROCESSED / "basins.parquet"        # chunk 4: one delineated basin per physical gauge
COMID_FEATURES = PROCESSED / "comid_features"  # chunk 5: covariates aggregated to catchments
NITRATE_DAILY = PROCESSED / "water" / "nitrate_daily.parquet"
GAUGE_WATER = PROCESSED / "water"                          # chunk 6: {gauge_uid}.parquet, merged series
# The merge review pair. BOTH live beside the code rather than in the data tree: they are human-facing, one
# is hand-edited, and both belong to chunk 3 rather than to any dataset. `merge_decisions.csv` is TRACKED --
# losing it means re-reviewing every pair -- while the candidate list regenerates on every run.
MERGE_REVIEW = _HERE / "chunk3" / "merge-review"
MERGE_CANDIDATES = MERGE_REVIEW / "merge_candidates.csv"      # only the pairs a person must answer
MERGE_AUTO = MERGE_REVIEW / "merge_auto.csv"                  # what the rule settled without asking
MERGE_DECISIONS = MERGE_REVIEW / "merge_decisions.csv"        # TRACKED

TYPE_REVIEW = _HERE / "chunk3" / "site-type-review"
TYPE_CANDIDATES = TYPE_REVIEW / "site_type_candidates.csv"    # only the sites a person must answer
TYPE_AUTO = TYPE_REVIEW / "site_type_auto.csv"                # ambiguous, but the classifier's answer stands
TYPE_DECISIONS = TYPE_REVIEW / "site_type_decisions.csv"      # TRACKED

BASIN_REVIEW = _HERE / "chunk4" / "basin-review"
BASIN_CANDIDATES = BASIN_REVIEW / "basin_candidates.csv"
BASIN_DECISIONS = BASIN_REVIEW / "basin_decisions.csv"        # TRACKED

# The quality review. `corrupt_decisions.csv` replaces the old build's hand-curated KNOWN_BAD list: an
# `exclude` here is what keeps a series out of processed2, and it is TRACKED for the same reason -- losing it
# means re-judging every flagged series.
CORRUPT_REVIEW = _HERE / "chunk6" / "corrupt-review"
CORRUPT_CANDIDATES = CORRUPT_REVIEW / "corrupt_candidates.csv"
CORRUPT_DECISIONS = CORRUPT_REVIEW / "corrupt_decisions.csv"  # TRACKED

EQUAL_AREA = "EPSG:5070"


@functools.lru_cache(maxsize=1)
def _config() -> dict:
    with open(_PIPELINE_CONFIG, "rb") as f:
        return tomllib.load(f)


@functools.lru_cache(maxsize=1)
def seed_cover() -> tuple[tuple[float, float, float, float], ...]:
    """The three SEED boxes, (min_lon, min_lat, max_lon, max_lat). A cover, not a partition -- two of them overlap.

    Sensor discovery runs against these and nothing else, which is what keeps the AOE non-circular: seeds define the AOE, and the AOE never redefines the seed set.
    """
    r = _config()["region"]
    missing = [k for k in REGION_KEYS if k not in r]
    if missing:
        raise KeyError(f"{_PIPELINE_CONFIG.name} [region] is missing {missing}")
    return tuple(tuple(float(v) for v in r[k]) for k in REGION_KEYS)


bbox_cover = seed_cover     # old name; the boxes are a seed cover, not the AOE


@functools.lru_cache(maxsize=1)
def seed_geometry():
    """The three seed boxes as one geometry. Overlaps dissolve, so area is not double counted."""
    return unary_union([box(*b) for b in seed_cover()])


@functools.lru_cache(maxsize=1)
def aoe_geometry():
    """The AOE as one shapely geometry in EPSG:4326, read from `aoe.parquet`.

    Falls back to the seed boxes with a LOUD warning when chunk 0 has not run. Silently returning the boxes would clip every raster to an extent that truncates the upstream half of the largest basins, and nothing downstream would notice.
    """
    import warnings

    if AOE_PATH.exists():
        import geopandas as gpd

        return gpd.read_parquet(AOE_PATH).geometry.iloc[0]
    warnings.warn("aoe.parquet is missing -- chunk 0 has not run. Falling back to the SEED BOXES, "
                  "which are smaller than the AOE and will truncate upstream basins.", stacklevel=2)
    return seed_geometry()


@functools.lru_cache(maxsize=1)
def aoe_stamp() -> str:
    """Short hash identifying this AOE. Every artifact cut against it records the stamp; joins refuse on mismatch.

    The replacement for `global_node_id`'s missing version tag. Changing the seed set or the geometry changes the stamp, so a stale clip is detected rather than silently mixed with a fresh one.
    """
    import hashlib

    return hashlib.sha256(aoe_geometry().wkb).hexdigest()[:12]


def in_seed_boxes(lat, lon):
    """Elementwise membership in the SEED boxes. Accepts scalars or arrays; returns bool or ndarray.

    A vectorised coordinate comparison rather than point-in-polygon: the seed cover is rectangles, so this is exact and ~1000x faster on the arrays discovery produces.
    """
    import numpy as np

    lat_a, lon_a = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    hit = np.zeros(lat_a.shape, dtype=bool)
    for x0, y0, x1, y1 in seed_cover():
        hit |= (lon_a >= x0) & (lon_a <= x1) & (lat_a >= y0) & (lat_a <= y1)
    return bool(hit) if hit.ndim == 0 else hit


def in_aoe(lat, lon):
    """Elementwise membership in the real AOE. Accepts scalars or arrays; returns bool or ndarray.

    Point-in-polygon, not a rectangle test: once chunk 0 runs the AOE is a drainage-shaped region and the box shortcut stops being exact. Uses a prepared geometry, so the per-point cost is a fraction of a plain `contains`.
    """
    import numpy as np
    from shapely import points
    from shapely.prepared import prep

    lat_a, lon_a = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    geom = prep(aoe_geometry())
    pts = points(np.atleast_1d(lon_a), np.atleast_1d(lat_a))
    hit = np.fromiter((geom.contains(p) for p in pts), dtype=bool, count=len(pts))
    return bool(hit[0]) if lat_a.ndim == 0 else hit.reshape(lat_a.shape)


def usgs_pat() -> str:
    with open(API_KEYS, "rb") as f:
        return tomllib.load(f)["usgs"]


def usgs_pats() -> list[str]:
    """Every USGS token on file, in rotation order: `usgs`, then `usgs2`, `usgs3`, ...

    Quota is per token and recovers over time, so the adapter rotates on exhaustion rather than stopping; by the time the last token is spent the first has usually rolled over. Discovered by suffix so adding a key to the TOML is the only step needed to widen the rotation.
    """
    with open(API_KEYS, "rb") as f:
        keys = tomllib.load(f)
    names = ["usgs"] + sorted((k for k in keys if k.startswith("usgs") and k != "usgs"),
                              key=lambda k: (len(k), k))
    return [str(keys[n]) for n in names if keys.get(n)]


def ensure_dirs() -> None:
    for p in (WATER_NATIVE, WATER_DAILY, WATER_CANONICAL, NLDI_BASINS, NETWORK, WEATHER,
              COMID_ATTRS, PROCESSED):
        p.mkdir(parents=True, exist_ok=True)


def equal_area_km2(geom) -> float:
    """Area of a lon/lat geometry in km2, via EPSG:5070. Degrees-squared is not an area."""
    import geopandas as gpd

    return float(gpd.GeoSeries([geom], crs=4326).to_crs(EQUAL_AREA).area.iloc[0] / 1e6)


def describe() -> str:
    parts = [f"  {k:18} {list(b)}" for k, b in zip(REGION_KEYS, seed_cover())]
    seed_km2 = equal_area_km2(seed_geometry())
    head = f"seed boxes: {len(seed_cover())}, {seed_km2:,.0f} km2, years {YEAR_START}-{YEAR_END}"
    if AOE_PATH.exists():
        aoe_km2 = equal_area_km2(aoe_geometry())
        head += f"\nAOE:        {aoe_km2:,.0f} km2 ({aoe_km2/seed_km2:.2f}x seed), stamp {aoe_stamp()}"
    else:
        head += "\nAOE:        NOT BUILT -- run chunk 0"
    return head + "\n" + "\n".join(parts)


if __name__ == "__main__":
    print(describe())
