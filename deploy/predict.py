"""Load a trained recipe model and score a (virtual) feature frame.

The saved models (deploy/models/*.json) are XGBoost boosters. A feature frame from virtual_recipe
/ recipes.build_feature_frame is aligned to the booster's feature_names before scoring: columns
are selected + ordered to match, missing columns are NaN-filled (XGBoost handles NaN natively --
e.g. a small basin lacking the far `_b1` distance bucket), and extra recipe columns are dropped.
feature_mismatch() surfaces both sets so a recipe<->model version skew is explicit.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

_THIS_DIR = Path(__file__).resolve().parent  # deploy
_MODELS = _THIS_DIR / "models"
sys.path.insert(0, str(_THIS_DIR.parent))  # repo root -> import src.*
sys.path.insert(0, str(_THIS_DIR))  # deploy/ -> sibling modules

from build_virtual_basin import build_virtual_basin
from virtual_recipes import virtual_recipe

DEFAULT_REG_NAME = "isaac_REG2.json"
DEFAULT_CLF_NAME = "isaac_CLF2.json"


def load_model(*, task: str = None, name: str = None) -> xgb.Booster:
    """Load a saved XGBoost booster by file name, e.g. 'isaac_REG2.json'."""
    if name is None:
        if task == "clf":
            name = DEFAULT_CLF_NAME
        elif task == "reg":
            name = DEFAULT_REG_NAME
        else:
            raise ValueError("You must either specify a task ('clf' or 'reg') or pass a model name!")

    path = _MODELS / f"{name}"
    if not path.exists():
        raise FileNotFoundError(f"No model {path.name} in {_MODELS}.")

    booster = xgb.Booster()
    booster.load_model(str(path))
    return booster


def load_meta(*, task: str = None, name: str = None) -> dict:
    """The <model>.json.meta.json sidecar (feat / task / target, plus any beta_table + base_rate
    written by tune_threshold.py). Resolves the same name as load_model. Empty dict if absent."""
    if name is None:
        name = DEFAULT_CLF_NAME if task == "clf" else DEFAULT_REG_NAME if task == "reg" else None
        if name is None:
            raise ValueError("You must either specify a task ('clf' or 'reg') or pass a model name!")
    path = _MODELS / f"{name}.meta.json"
    return json.loads(path.read_text()) if path.exists() else {}


def threshold_for_beta(meta: dict, beta: float) -> dict | None:
    """Operating point for `beta` from a meta's beta_table (written by tune_threshold): the grid row
    nearest `beta`, augmented with the pooled base_rate -- keys tau / recall / precision / fdr /
    base_rate. Returns None when the model has no beta_table (untuned), so callers can fall back to
    showing the raw probability with no alarm threshold."""
    table = meta.get("beta_table")
    if not table:
        return None
    row = min(table, key=lambda r: abs(r["beta"] - beta))
    return {**row, "base_rate": meta.get("base_rate")}


def feature_mismatch(booster: xgb.Booster, features: pd.DataFrame) -> dict:
    """Column skew between a feature frame and the model: {'model_only', 'recipe_only'}. A
    model_only that is not just an absent distance bucket signals a recipe<->model version skew."""
    fn = set(booster.feature_names)
    cols = {c for c in features.columns if c != "date"}
    return {"model_only": sorted(fn - cols), "recipe_only": sorted(cols - fn)}


def predict(booster: xgb.Booster, features: pd.DataFrame) -> pd.Series:
    """Score `features`, aligning columns to the model's feature_names (reindex: select + order,
    NaN-fill missing). Returns a Series indexed by the frame's `date` (positive-class probability
    for a classifier, the predicted target for a regressor)."""
    fn = booster.feature_names
    date = pd.DatetimeIndex(pd.to_datetime(features["date"])) if "date" in features.columns else None
    X = features.reindex(columns=fn)
    dmat = xgb.DMatrix(X, feature_names=fn)
    return pd.Series(booster.predict(dmat), index=date, name="prediction")


def test():
    """Score a virtual site at a pin and plot predicted nitrate + P(violation) vs basin precip."""
    lat, lon, ty = 43.122036, -91.911358, 2017

    sd = build_virtual_basin(lat=lat, lon=lon, target_year=ty)
    reg_f = virtual_recipe(site_data=sd, task="reg", target_year=ty)
    clf_f = virtual_recipe(site_data=sd, task="clf", target_year=ty)
    Y_reg = predict(load_model(task="reg"), reg_f)
    Y_clf = predict(load_model(task="clf"), clf_f)

    from src.features.features import _basin_daily_weather

    precip = _basin_daily_weather(site_data=sd)["precip_in_1d"].reindex(Y_reg.index)

    import matplotlib.pyplot as plt

    fig, (ax_reg, ax_clf) = plt.subplots(2, 1, sharex=True, figsize=(12, 8))
    ax_reg.plot(Y_reg.index, Y_reg.to_numpy(), color="red", label="predicted nitrate (mg/L)")
    ax_reg.set_ylabel("predicted nitrate (mg/L)", color="red")
    ax_reg.tick_params(axis="y", labelcolor="red")
    ax_reg.axhline(10, color="red", ls="--", lw=0.8, alpha=0.5)
    ax_reg_p = ax_reg.twinx()
    ax_reg_p.plot(precip.index, precip.to_numpy(), color="blue", alpha=0.6)
    ax_reg_p.set_ylabel("basin-mean precip (in)", color="blue")
    ax_reg_p.tick_params(axis="y", labelcolor="blue")
    ax_reg.set_title(f"Regression -- virtual site ({lat:.4f}, {lon:.4f}), {ty}")

    ax_clf.plot(Y_clf.index, Y_clf.to_numpy(), color="red", label="P(violation)")
    ax_clf.set_ylabel("P(violation)", color="red")
    ax_clf.tick_params(axis="y", labelcolor="red")
    ax_clf.set_ylim(0, 1)
    ax_clf_p = ax_clf.twinx()
    ax_clf_p.plot(precip.index, precip.to_numpy(), color="blue", alpha=0.6)
    ax_clf_p.set_ylabel("basin-mean precip (in)", color="blue")
    ax_clf_p.tick_params(axis="y", labelcolor="blue")
    ax_clf.set_title("Classification -- probability of nitrate violation (>10 mg/L)")
    ax_clf.set_xlabel("date")

    fig.tight_layout()
    plt.show()
    return fig


if __name__ == "__main__":
    test()
