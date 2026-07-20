"""Surplus visualization helpers for the widget (colormap + statewide heatmap PNG).

Kept out of access.py so the lean read path stays free of matplotlib/PIL. The per-site *pixel*
heatmap from the old tree is intentionally dropped (its ~1GB pixel parquets were removed in the
re-grain); the widget colours the Voronoi rain-grid cells via surplus_to_hex(access.get_surplus)
and overlays the pre-rendered statewide PNG instead.

Data (src/data/processed/surplus/): meta/surplus_stats.csv (global min/max for a consistent
colour scale) + images/iowa_surplus_{year}.png (+ .json bounds).
"""

import base64
import functools
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib import colormaps, colors as mcolors
from PIL import Image

_PROC = Path(__file__).resolve().parent / "processed" / "surplus"
_META = _PROC / "meta"
_IMAGES = _PROC / "images"

_CMAP = colormaps["YlOrRd"]
_MIN_SURPLUS = None
_MAX_SURPLUS = None


@functools.lru_cache(maxsize=1)
def get_stats() -> pd.DataFrame:
    """Per-year surplus min/max (the global colour-scale reference)."""
    return pd.read_csv(_META / "surplus_stats.csv")


def _min_surplus() -> float:
    global _MIN_SURPLUS
    if _MIN_SURPLUS is None:
        _MIN_SURPLUS = get_stats()["min_surplus_kgha"].min()
    return _MIN_SURPLUS


def _max_surplus() -> float:
    global _MAX_SURPLUS
    if _MAX_SURPLUS is None:
        _MAX_SURPLUS = get_stats()["max_surplus_kgha"].max()
    return _MAX_SURPLUS


def surplus_to_hex(value: float) -> str:
    """Hex colour for a surplus_kgha value on the global scale (matches the statewide PNG)."""
    lo, hi = _min_surplus(), _max_surplus()
    rng = hi - lo if hi != lo else 1.0
    t = float(np.clip((value - lo) / rng, 0.0, 1.0))
    return mcolors.to_hex(_CMAP(t))


@functools.lru_cache(maxsize=18)
def get_iowa_surplus_image_buffer(year: int) -> tuple[str, list]:
    """(data_url, bounds) for the pre-rendered statewide surplus heatmap PNG, cached per year."""
    img_path = _IMAGES / f"iowa_surplus_{year}.png"
    bounds_path = _IMAGES / f"iowa_surplus_{year}.json"
    if not img_path.exists() or not bounds_path.exists():
        raise FileNotFoundError(f"Iowa surplus image for {year} not found in {_IMAGES}.")
    with open(bounds_path) as f:
        bounds = json.load(f)["bounds"]
    buf = io.BytesIO()
    Image.open(img_path).save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return data_url, bounds
