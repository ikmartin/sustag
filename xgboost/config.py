"""Project constants for the xgboost cohort models: who is in the cohort, what geometry the features use, where the caches live.

THE COHORT IS A MODELING PREFERENCE, NOT A PIPELINE FACT. The published stores carry every registration the build admitted -- wells, estuaries, storm drains, four-day deployments, the Atlantic and Gulf arms. This project models one slice of that: cornbelt surface watercourses with enough record to train on. Nothing here edits or constrains `data.access`; it filters what the access layer returns.

THE SEED BOX IS READ FROM THE PIPELINE, NEVER COPIED. `scope.toml` is the build's own geographic input; duplicating its numbers here would let the two drift apart silently, and the drift would show up as a cohort that quietly stopped matching the region the build collected.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
RESULTS = HERE / "results"

# ---- cohort ----------------------------------------------------------------------------------------

SEED_BOX = "cornbelt"

# Surface WATERCOURSES only. Excluded on purpose: wells/groundwater (different water), lake/estuary/tidal
# (standing or tidal water has no contributing basin to accumulate), and the sub-catchment practice types
# (tile_outlet, saturated_buffer/waterway, blind_inlet, bioreactor, field) whose basin is a field-scale
# footprint, not the reach's drainage -- the same two exclusion axes the pipeline's site_type module draws,
# applied here as a project choice rather than inherited as a fact.
SITE_TYPES = ("stream", "ditch", "canal")

MIN_NITRATE_DAYS = 365

# BASIN SIZE CEILING, km2 -- a modeling preference, and the one that keeps the features meaning what they
# claim. Cohort basins are median 1,157 km2, but the mainstem sites run to 1.77M (Mississippi at Cape
# Girardeau, 640,172 reaches): a catchment-mean corn share over half a continent is not a local covariate,
# it is a national average wearing a site's name, and one such basin also dominates every pooled statistic
# it enters. 50,000 km2 keeps 171 of 188 records and drops the 17 mainstem giants. Measured against VAA's
# own `totdasqkm` so the filter costs a lookup rather than a walk.
MAX_BASIN_KM2 = 50_000.0

# ---- target ----------------------------------------------------------------------------------------

CLF_THRESHOLD = 10.0        # mg/L as N -- the drinking-water MCL, the old models' threshold
TARGET_WINDOW = 1           # trailing days the target reduces over, today inclusive (exp13's best geometry)
TARGET_MIN_OBS = 1
TARGET_AGG = "max"          # the day's value: max of the day's readings

# ---- geometry --------------------------------------------------------------------------------------

# Flow-distance bucket edges in METRES along the channel network, replacing the old euclidean rings.
# Starting values mirror the old REG/CLF edges so the first runs are comparable in spirit; they are
# hypotheses on the new axis, to be swept once a baseline exists.
REG_EDGES = (8_000.0, 30_000.0)
CLF_EDGES = (5_000.0, 20_000.0)
DECAY_LAMBDA = 20_000.0     # exp(-flow_dist / lambda) weighting length, metres

# Fallback water velocity for travel-time lags where VAA's own path time is absent, m/s.
VELOCITY_MPS = 0.5

# ---- covariates ------------------------------------------------------------------------------------

CROP_REMAP = "default"      # the access layer's 8-class map; None would give raw CDL codes
WEATHER_VARS = ("pr", "tmmx", "tmmn", "pet", "srad", "vpd", "rmin")
COVARIATE_PRODUCTS = ("crops", "nutrients", "agtile")

# ---- cross validation ------------------------------------------------------------------------------

N_SPLITS = 5
LODO_BUFFER_KM = 50.0
MAX_HOLDOUT_PCT = 0.2


def seed_box() -> tuple[float, float, float, float]:
    """The cohort's bounding box, read live from the pipeline's scope.toml."""
    scope = tomllib.loads((ROOT / "data" / "build" / "parameters" / "scope.toml").read_text())
    return tuple(scope["seed_boxes"][SEED_BOX])


def collection_start() -> str:
    scope = tomllib.loads((ROOT / "data" / "build" / "parameters" / "scope.toml").read_text())
    return scope["window"]["collection_start"]


def ensure_dirs() -> None:
    for p in (CACHE, CACHE / "basins", RESULTS):
        p.mkdir(parents=True, exist_ok=True)
