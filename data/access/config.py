"""Store locations for the access layer. MIRRORS `data.build.config` deliberately, imports it never.

Access is strictly read-only and never imports the build -- so the handful of paths both need are declared twice, here and there, and the final store migration flips BOTH files (they are named in each other's docstrings so neither move forgets the other). Everything here points at immutable or published state: the raw/acquired archives (snapshot-manifested, never written by any reader) and the published store.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

# The adopted archives -- interim location, flipped to data/stores at the final migration (see data/build/config.py).
RAW = REPO / "src" / "data" / "raw"
ACQUIRED = REPO / "src" / "data" / "acquired"
ACQ_NETWORK = ACQUIRED / "network"
ACQ_WATER_NATIVE = ACQUIRED / "water" / "native"
ACQ_WEATHER = ACQUIRED / "weather"

STORES = REPO / "data" / "stores"
PUBLISHED = STORES / "published"
PUB_SENSORS = PUBLISHED / "sensors.parquet"
PUB_WATER = PUBLISHED / "water"
PUB_PROXIMITY = PUBLISHED / "proximity.parquet"
PUB_QUALITY = PUBLISHED / "quality.parquet"
PUB_COMID_FEATURES = PUBLISHED / "comid_features"

CACHE = STORES / "access_cache"

EQUAL_AREA = "EPSG:5070"
