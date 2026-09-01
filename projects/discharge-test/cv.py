"""Spatial cross-validation and the hydrologic score set.

THE HOLDOUT MUST BE SPATIAL, and that is the whole design. Predicting held-out DAYS at a site the model already knows is a much easier problem -- the model has seen that basin's response and only has to interpolate in time -- and it is not the question. We need discharge at 158 nitrate sites that have no gauge at all, so the test is: given a basin the model has NEVER seen, how well does it do? Folds are therefore grouped by basin FAMILY, so a test site's basin never contains a training site's outlet.

FAMILIES ARE FOUND FROM THE OUTLET MAP, not by comparing every pair. The obvious union-find over "does A's basin contain B's outlet" is O(n^2) in sites and dies at cohort scale; inverting it -- walk each basin's reaches and look each up in a comid -> site index -- is linear in total basin size and gives the identical partition.

THE METRIC SET MATCHES THE NWM AUDIT EXACTLY so the comparison is like-for-like: Nash-Sutcliffe, Kling-Gupta, log-space NSE, bias and correlation. Note that NSE is the coefficient of determination against the observed mean, so `sklearn.r2_score` computes it and no separate implementation is needed -- writing one would be a second definition of the same quantity, which is how two numbers that should agree start not to.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import r2_score
from sklearn.model_selection import GroupKFold

sys.path.append(str(Path(__file__).resolve().parent))

import cohort                                                      # noqa: E402
import config                                                      # noqa: E402


def basin_families(sites: pd.DataFrame) -> pd.Series:
    """code -> family id; nested basins travel together. Linear in total basin size, not quadratic in sites."""
    outlet = {}
    for _, r in sites.iterrows():
        if pd.notna(r.comid):
            outlet.setdefault(int(r.comid), []).append(r.code)
    parent = {c: c for c in sites.code}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for _, r in sites.iterrows():
        b = cohort.basin(r.code, int(r.comid)) if pd.notna(r.comid) else None
        if b is None or not len(b):
            continue
        for c in b.comid.to_numpy():
            for other in outlet.get(int(c), ()):
                if other != r.code:
                    union(r.code, other)
    return pd.Series({c: find(c) for c in sites.code})


def folds(groups, n_splits: int | None = None):
    """(train_idx, test_idx) from GroupKFold. Empty when fewer than two groups -- the caller sees all-NaN OOF."""
    n_splits = n_splits or config.N_SPLITS
    g = pd.Series(groups).to_numpy()
    n = min(n_splits, len(pd.unique(g)))
    if n < 2:
        return
    yield from GroupKFold(n).split(np.zeros(len(g)), groups=g)


def kge(obs, pred) -> float:
    """Kling-Gupta efficiency: 1 - sqrt((r-1)^2 + (sigma ratio - 1)^2 + (mean ratio - 1)^2).

    Reported beside NSE because they fail differently -- NSE rewards getting the mean right and forgives a flattened hydrograph, while KGE penalises the variance ratio directly, which is where a regression to the mean shows up.
    """
    obs, pred = np.asarray(obs, float), np.asarray(pred, float)
    m = np.isfinite(obs) & np.isfinite(pred)
    obs, pred = obs[m], pred[m]
    if len(obs) < 2 or obs.std() == 0 or obs.mean() == 0:
        return np.nan
    r = np.corrcoef(obs, pred)[0, 1]
    return float(1 - np.sqrt((r - 1) ** 2 + (pred.std() / obs.std() - 1) ** 2
                             + (pred.mean() / obs.mean() - 1) ** 2))


def score(obs_mm, pred_mm) -> dict:
    """The NWM audit's five metrics, on mm/day rather than on the log the model was fit in."""
    obs, pred = np.asarray(obs_mm, float), np.asarray(pred_mm, float)
    m = np.isfinite(obs) & np.isfinite(pred)
    obs, pred = obs[m], pred[m]
    if len(obs) < 2:
        return dict(nse=np.nan, kge=np.nan, log_nse=np.nan, bias=np.nan, r=np.nan, n=len(obs))
    lo = np.log(np.clip(obs, config.EPS_MM, None))
    lp = np.log(np.clip(pred, config.EPS_MM, None))
    return dict(nse=float(r2_score(obs, pred)),              # NSE IS r2 against the observed mean
                kge=kge(obs, pred),
                log_nse=float(r2_score(lo, lp)),
                bias=float(pred.mean() / obs.mean()) if obs.mean() else np.nan,
                r=float(np.corrcoef(obs, pred)[0, 1]),
                n=int(len(obs)))


def per_site(oof: pd.DataFrame) -> pd.DataFrame:
    """Scores computed WITHIN each site, then reportable as a distribution.

    A pooled NSE over sites of wildly different magnitude is dominated by the wettest basins; the median of per-site NSE is what "how well does this do at a typical ungauged site" actually means, and it is the number the NWM audit reported.
    """
    rows = []
    for site, g in oof.groupby("site"):
        s = score(g.obs_mm, g.pred_mm)
        s["site"] = site
        rows.append(s)
    return pd.DataFrame(rows)
