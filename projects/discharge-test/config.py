"""Project constants for the discharge model: who is in the cohort, what the target is, where caches live.

WHY THIS PROJECT EXISTS. NWM's value proposition is modelled discharge where no gauge sits. We hold observed discharge at 5,774 of 7,539 published records and lack it at 158 of 364 nitrate records -- and NWM's CHANOBS product covers almost exactly the reaches that already have it. So "is NWM useful" reduces to "how much of observed discharge's value can be recovered at an UNGAGED site", and that is answerable from data already on disk. This project answers it, and in passing produces the imputed series that gives the nitrate cohort total coverage.

NOTHING HERE EDITS `data/access`. Everything read goes through `cohort.py`, the single boundary -- the same rule `xgboost/` follows, and for the same reason: "does this need an access-layer change?" should be answerable by reading one file.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
RESULTS = HERE / "results"

# ---- cohort ------------------------------------------------------------------------------------------

# TRAIN BROAD, REPORT NARROW. Regionalisation needs hydrologic range to learn an area/climate/soil
# response rather than memorise one region, so training takes every discharge-bearing record in the AOE.
# The number that matters is skill on the sites we actually need predictions for, so the headline is
# scored on the cornbelt surface-watercourse subset alone.
SITE_TYPES = ("stream", "ditch", "canal")
MIN_DISCHARGE_DAYS = 730          # two years; a site with less cannot show a seasonal cycle to learn from
MAX_BASIN_KM2 = 50_000.0          # the mainstem giants are a national average wearing a site's name

# RUNOFF RATIO BOUNDS -- a PHYSICAL validity check on the site, not a modelling preference. Long-term
# discharge depth over precipitation depth must sit between zero and about one: a basin cannot shed more
# water than it receives (absent glaciers or inter-basin transfer), and a real gauged stream does not
# sustain 0.2% of its rainfall. Measured over the cohort the ratio is tightly behaved -- p50 0.349, p01
# 0.0023, p99 0.894 -- and the 3.5% of sites outside these bounds carry a median NSE of -1.2 against 0.489
# for the rest. They are bad snaps: the gauge sits on a tributary while the snapped reach is a big river,
# so the area is overstated and specific discharge collapses. Filtering them is correcting the cohort,
# not tuning the score.
MIN_RUNOFF_RATIO = 0.01
MAX_RUNOFF_RATIO = 1.5
REPORT_SEED_BOX = "cornbelt"

# ---- target ------------------------------------------------------------------------------------------

# SPECIFIC DISCHARGE, LOG-TRANSFORMED. Raw m3/s across basins spanning four orders of magnitude in area is
# mostly a prediction of area -- a model would score well by learning size and nothing else. Dividing by
# drainage area gives mm/day, which is the quantity a rainfall-runoff response actually produces, and the
# log keeps a heavy right tail from dominating the loss. Scores are reported after converting BACK, so they
# are comparable to NWM's on the same units.
TARGET = "discharge_mean"
EPS_MM = 1e-3

# ---- features ----------------------------------------------------------------------------------------

WEATHER_VARS = ("pr", "pet", "tmmx", "tmmn", "srad")
LAGS = (1, 2, 3, 5, 7, 14, 30)
WINDOWS = (3, 7, 14, 30, 60, 90)
STREAMCAT_COLS = ("pctagdrainagecat", "pctagdrainagews", "claycat", "sandcat", "permcat",
                  "wtdepcat", "runoffcat", "precip8110cat", "tmean8110cat", "elevcat")

# ---- cross-validation --------------------------------------------------------------------------------

N_SPLITS = 5
DAY_STRIDE = 3                    # hydrologic response is autocorrelated; consecutive days are near-duplicates


def ensure_dirs() -> None:
    for d in (CACHE, CACHE / "features", RESULTS):
        d.mkdir(parents=True, exist_ok=True)
