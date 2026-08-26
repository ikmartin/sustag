"""Curated feature lists per task, and the assembly that turns them into a model-ready frame.

THE RECIPE DECIDES WHAT THE MODEL SEES, not the accessors -- `features.py` exposes the full data for experiments, and the drops below are the model's curation. Every constant carried over from the predecessor's `src/features/recipes.py` is marked INHERITED: it was measured on the OLD euclidean-bucket geometry over a 116-site mostly-Iowa cohort, and neither the geometry nor the cohort survives here. They are starting hypotheses, and experiment 1 re-measures them.

TWO VARIANTS. `island` uses only the site's own basin and the cohort-wide pooled level; `network` adds a donor block from a monitored neighbour. The two share one aggregation pass so the only difference between them is what network adds.
"""

from __future__ import annotations

import pandas as pd

import config
import features as F
import transforms as T

# ---- REG ---------------------------------------------------------------------------------------

REG_EDGES = config.REG_EDGES
REG_LAM = config.DECAY_LAMBDA
_LONGRUN_STATS_REG = ("mean",)            # INHERITED: mean +0.042 lofo_r2, sd COST -0.015 on the old geometry
_CROSS_LAGS_REG = (1, 3)                  # INHERITED
_CROSS_ROLL_REG = (7, 14, 60)             # INHERITED
_PRECIP_WINDOWS_REG = (14, 30, 60)

# ---- CLF ---------------------------------------------------------------------------------------

CLF_EDGES = config.CLF_EDGES
CLF_LAM = config.DECAY_LAMBDA
_LONGRUN_STATS_CLF = ("mean", "sd")       # INHERITED: mean +0.014, sd a further +0.007 on the old geometry
_CROSS_LAGS_CLF = (1, 3)
_CROSS_ROLL_CLF = (7, 14, 60)
_PRECIP_WINDOWS_CLF = (14, 30, 60)

# WHY NO lag0 on the cross-site block. A same-day cohort level exists for a NOWCAST but not a FORECAST, and
# it is contemporaneous with the target -- one storm falling on both basins carries it, not transport. The
# old stack declined it for the same reason; adding it back is a one-tuple edit.


def _blocks(code: str, task: str, spine) -> list:
    """The task's feature blocks for one site, before assembly."""
    edges = REG_EDGES if task == "reg" else CLF_EDGES
    lam = REG_LAM if task == "reg" else CLF_LAM
    lags = _CROSS_LAGS_REG if task == "reg" else _CROSS_LAGS_CLF
    roll = _CROSS_ROLL_REG if task == "reg" else _CROSS_ROLL_CLF
    precip_w = _PRECIP_WINDOWS_REG if task == "reg" else _PRECIP_WINDOWS_CLF

    out = []
    # land cover: composition shares by flow bucket, plus the decayed AREA arm (both earned their place
    # on the old geometry; kept as a pair until measured here)
    cb = T.flatten_buckets(F.agg_crops_normalized(code, edges=edges))
    if len(cb):
        out.append(cb)
    cb_exp = T.flatten_buckets(T.tag_values(F.agg_crops(code, edges=(), decay=lam), "_expT"))
    if len(cb_exp):
        out.append(cb_exp)
    # nitrogen: intensity by bucket, and the decayed mass
    nb = T.flatten_buckets(T.tag_values(F.agg_nutrients_normalized(code, edges=edges), "_norm"))
    if len(nb):
        out.append(nb)
    nb_exp = T.flatten_buckets(T.tag_values(F.agg_nutrients(code, edges=edges, decay=lam), "_expT"))
    if len(nb_exp):
        out.append(nb_exp)
    # weather: the derived dryness integral (the fuel-moisture stand-in) and antecedent precipitation
    dry = F.dryness_index(code)
    if len(dry):
        out.append(dry.reset_index().rename(columns={"index": "date"}))
    rp = F.rolling_precip(code, windows=precip_w)
    if len(rp):
        out.append(rp)
    # cohort level, lagged and rolled
    out.extend(F.nitrate_avg_except_this(code, lags=lags))
    rn = F.rolling_nitrate_except_this(code, windows=roll)
    if len(rn):
        out.append(rn)
    # calendar
    out.append(F.doy_climatology(spine))
    return out


def _add_static(code: str, d: pd.DataFrame, task: str) -> pd.DataFrame:
    """Broadcast the per-site constants: descriptors, then the long-run composition block."""
    edges = REG_EDGES if task == "reg" else CLF_EDGES
    stats = _LONGRUN_STATS_REG if task == "reg" else _LONGRUN_STATS_CLF
    for k, v in F.site_static(code).items():
        d[k] = v
    for k, v in F.longrun_composition(code, edges=edges, stats=stats).items():
        d[k] = v
    return d


def build_feature_frame(code: str, task: str = "reg", spine=None) -> pd.DataFrame:
    """Model-ready features for one site, WITHOUT a target."""
    if spine is None:
        spine = F.daily_nitrate(code).index
    if not len(spine):
        return pd.DataFrame()
    d = T.merge_on_date(_blocks(code, task, spine), spine=spine)
    return _add_static(code, d, task)


def recipe(task: str = "reg", window: int = None, min_obs: int = None):
    """A one-argument recipe closure: code -> (target + features) frame, the shape `cook` consumes."""
    window = config.TARGET_WINDOW if window is None else window
    min_obs = config.TARGET_MIN_OBS if min_obs is None else min_obs

    def make(code: str) -> pd.DataFrame:
        if task == "reg":
            target = F.nitrate_daily_rolling(code, window=window, min_obs=min_obs, agg=config.TARGET_AGG)
        elif task == "clf":
            target = F.nitrate_violations_rolling(code, window=window, min_obs=min_obs,
                                                  agg=config.TARGET_AGG)
        else:
            raise ValueError(f"expected 'reg' or 'clf', got {task!r}")
        if not len(target):
            return pd.DataFrame()
        d = build_feature_frame(code, task=task, spine=target.index)
        if not len(d):
            return pd.DataFrame()
        d.insert(0, target.name, target.reindex(d.index))
        return d

    make.__name__ = f"recipe_{task}"
    return make


island_REG = recipe("reg")
island_CLF = recipe("clf")
