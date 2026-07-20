"""Feature frame for a virtual (ungauged) SiteData -- no target, ready to score.

Reuses src.features.recipes.build_feature_frame (the exact assembly the gauged recipes use) with
a weather-derived spine = the TARGET_YEAR daily dates. The SiteData.weather spans TARGET_YEAR
±2 months, so the trailing rolling/lag features have lead-in; trimming the spine to the year
keeps that buffer for lookback while emitting only in-year rows.
"""

import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent   # deploy
sys.path.insert(0, str(_HERE.parent))     # repo root -> import src.*
sys.path.insert(0, str(_HERE))            # deploy/ -> sibling build_virtual_basin

from src.features.recipes import build_feature_frame
from build_virtual_basin import TARGET_YEAR


def target_year_spine(site_data, target_year: int = TARGET_YEAR) -> pd.DatetimeIndex:
    """Daily DatetimeIndex over `target_year`, taken from the site's weather dates (which span
    target_year ±2 months). Trimming to the year drops the lookback buffer from the OUTPUT rows
    while keeping it available to the rolling/lag feature computations."""
    wd = pd.DatetimeIndex(sorted(pd.to_datetime(site_data.weather["date"].unique())))
    ystart = pd.Timestamp(f"{target_year}-01-01")
    yend = pd.Timestamp(f"{target_year}-12-31")
    return wd[(wd >= ystart) & (wd <= yend)]


def virtual_recipe(site_data, task: str = "reg", target_year: int = TARGET_YEAR) -> pd.DataFrame:
    """Feature frame (no target) for a virtual SiteData, task 'reg' or 'clf'.

    Columns match _best_features_REG / _best_features_CLF (recipe_REG/_CLF minus the target).
    Align to a trained model's feature_names before scoring (see predict).
    """
    spine = target_year_spine(site_data, target_year)
    if len(spine) == 0:
        raise ValueError(f"No weather dates fall in {target_year}; the SiteData weather window does not cover it.")
    return build_feature_frame(site_data, task=task, spine=spine)
