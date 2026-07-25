"""Shared path resolution for the water builders (iwqis / usgs / site_metadata / _make_water).

Source (IWQIS chunks + site_clean/measures/params, USGS QA dump) reads from src/data/raw/water,
falling back to the legacy data/water/water_raw during staging. Outputs -- per-site nitrate
parquets and metadata CSVs -- are WRITTEN to src/data/processed/water/{data,meta} (the tracked,
durable IWQIS source of truth).
"""

from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]      # src
_REPO = _SRC.parent  # repo root
_RAW = _SRC / "data" / "raw" / "water"
_PROC = _SRC / "data" / "processed" / "water"


def raw_dir() -> Path:
    """IWQIS chunks/site_clean/measures/params + USGS QA source, under src/data/raw/water."""
    return _RAW


def data_dir() -> Path:
    """Per-site {uid}_water.parquet output dir (created)."""
    d = _PROC / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def meta_dir() -> Path:
    """Water metadata CSV output dir (created)."""
    d = _PROC / "meta"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_log_path() -> Path:
    """logs/data/water_build_results.txt -- the water build's human-readable funnel log."""
    d = _REPO / "logs" / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "water_build_results.txt"
