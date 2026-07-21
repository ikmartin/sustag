"""
Cross-site Recipe A: do STATIC site descriptors and/or DISTANCE BUCKETS help?
================================================================================

Recipe A (weather + land-use + calendar, no nitrate-derived features) has ~no skill
at a single site, because the covariates that drive nitrate (land use, N surplus) are
nearly constant *within* a site. They vary *across* sites, so we pool many sites and
ask honest generalization questions:

  (1a) LEAVE-SITES-OUT  (LOSO) -- GroupKFold by *site*. Prevents within-site leakage,
       but treats sites as independent. They are not: nested / overlapping basins
       (e.g. an upstream gauge and a downstream one on the same river) share catchment,
       so an unseen test site whose watershed sits in the train set isn't truly unseen.
       -> optimistic.
  (1b) LEAVE-BASINS-OUT (LOBO) -- GroupKFold by *conflict component* from data/splits.py
       (basin nesting x temporal overlap). Truly independent basins stay together, so
       this is the honest "ungauged basin" number. It is LOWER and coarser/noisier,
       because one indivisible mega-component (~34 sites) dominates a fold.
       The truth is bracketed: LOBO <= real generalization <= LOSO.
  (2)  TEMPORAL HOLDOUT -- train on all sites pre-2022, test on >= 2022 (pure time split;
       basin grouping does not apply).

Within each, we ablate two design choices, 2x2:
  * static features  : lat, lon, log basin area, mean/max cell distance (site_static).
  * distance buckets : edges=[] (one whole-basin aggregate) vs edges=[50k,150k] (3 bins).

Metrics printed per config:
  feat       number of features
  LOSO_R2    leave-sites-out  R2  (by site;  optimistic)
  LOBO_R2    leave-basins-out R2  (by conflict component; honest, coarse)
  btwn_R2    R2 of predicted vs actual per-site MEAN nitrate -- does it rank site levels?
  medPS      median per-site held-out R2 -- within-site daily tracking
  temp_R2    temporal-holdout R2

Scratch / untracked.  Run:  python isaac/_experiment2.py
"""

import warnings

warnings.filterwarnings("ignore")
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
import xgboost as xgb
from sklearn.model_selection import GroupKFold
from sklearn.metrics import r2_score

from data.access import get_site_ids
from data.features import (
    agg_crops,
    agg_surplus,
    agg_weather_w_lag,
    daily_nitrate,
    doy_climatology_pure_signal,
    site_static,
)
from data.transforms import flatten_buckets, merge_on_date
from data.splits import split_groups

TARGETCOL = "nitrate_con"
VEL = 0.8  # water celerity (m/s) for travel-time weather lags
STATIC_COLS = ["lat", "lon", "log_basin_area", "mean_dist_to_sensor", "max_dist_to_sensor"]
TEMPORAL_CUT = pd.Timestamp("2022-01-01")
MIN_DAYS = 1000  # require this many observed daily values to include a site
N_SPLITS = 5

CFG = dict(
    n_estimators=2000,
    learning_rate=0.03,
    max_depth=4,
    min_child_weight=20,
    subsample=0.8,
    colsample_bytree=0.8,
    reg_lambda=5.0,
    random_state=42,
    eval_metric="rmse",
)

# site -> conflict-component id (basin nesting x temporal overlap); computed once
_BASIN_GROUP = split_groups()


def basin_group_ids(site_series):
    """Map a Series of site_uids to conflict-component ids, giving any site missing from
    the basin graph its own singleton group (so it can't leak into another component)."""
    next_id = max(_BASIN_GROUP.values(), default=-1) + 1
    mapping = {}
    for s in pd.unique(site_series):
        if s in _BASIN_GROUP:
            mapping[s] = _BASIN_GROUP[s]
        else:
            mapping[s] = next_id
            next_id += 1
    return site_series.map(mapping)


def build_pool(site_uids, edges):
    """Stack Recipe A (+ static descriptors) for many sites into one long dataframe.

    Each site contributes its covariate frame (weather + crop/surplus + calendar) plus
    constant static-descriptor columns and a `site` label. `edges` controls distance
    bucketing; with edges=[] every site has identical columns. Sites that fail to build
    or are too short are skipped. Columns are unioned across sites (missing -> NaN).
    """
    frames = []
    for uid in site_uids:
        try:
            weather = flatten_buckets(agg_weather_w_lag(uid, edges=edges, water_velocity=VEL))
            crops = flatten_buckets(agg_crops(uid, edges=edges, lam=10_000, exp=True))
            surplus = flatten_buckets(agg_surplus(uid, edges=edges, lam=10_000, exp=True))
            n_daily = daily_nitrate(uid).rename(TARGETCOL)
            calendar = doy_climatology_pure_signal(n_daily)

            d = merge_on_date([n_daily, weather, crops, surplus, calendar], spine=n_daily.index)
            d = d.dropna(subset=[TARGETCOL])
            if len(d) < 500:
                continue
            for col, val in site_static(uid).items():  # broadcast the static descriptors
                d[col] = val
            d["site"] = uid
            frames.append(d)
        except Exception:
            pass
    return pd.concat(frames, ignore_index=True)


def _oof(X, y, groups):
    """Out-of-fold predictions from GroupKFold(N_SPLITS) with the given group key."""
    oof = np.full(len(y), np.nan)
    for train_idx, test_idx in GroupKFold(N_SPLITS).split(X, y, groups=groups):
        m = xgb.XGBRegressor(**CFG)
        m.fit(X.iloc[train_idx], y.iloc[train_idx], verbose=False)
        oof[test_idx] = m.predict(X.iloc[test_idx])
    return oof


def evaluate(pool, drop_static):
    """Leave-sites-out and leave-basins-out CV + temporal holdout; return a metrics dict.

    `drop_static=True` excludes the static descriptors so we can isolate their effect.
    """
    feat = [c for c in pool.columns if c not in (TARGETCOL, "site", "date")]
    if drop_static:
        feat = [c for c in feat if c not in STATIC_COLS]
    X, y = pool[feat], pool[TARGETCOL]

    # (1a) leave-sites-out and (1b) leave-basins-out: same model, different group key
    oof_site = _oof(X, y, pool["site"])
    oof_basin = _oof(X, y, basin_group_ids(pool["site"]))
    loso_r2 = r2_score(y, oof_site)
    lobo_r2 = r2_score(y, oof_basin)

    # between-site: can it rank a site's overall LEVEL? (predicted vs actual site means)
    site_means = pool.assign(pred=oof_site).groupby("site").agg(actual=(TARGETCOL, "mean"), pred=("pred", "mean"))
    between_r2 = r2_score(site_means.actual, site_means.pred)

    # within-site: median per-site held-out R2 (site-grouped predictions)
    per_site = (
        pool.assign(pred=oof_site)
        .groupby("site")
        .apply(lambda d: r2_score(d[TARGETCOL], d["pred"]) if d[TARGETCOL].std() > 0 else np.nan)
    )

    # (2) temporal holdout
    dt = pd.to_datetime(pool["date"])
    is_train, is_test = dt < TEMPORAL_CUT, dt >= TEMPORAL_CUT
    m_temporal = xgb.XGBRegressor(**CFG)
    m_temporal.fit(X[is_train], y[is_train], verbose=False)
    temporal_r2 = r2_score(y[is_test], m_temporal.predict(X[is_test]))

    return dict(
        n_feat=len(feat),
        loso_r2=loso_r2,
        lobo_r2=lobo_r2,
        between=between_r2,
        med_ps=per_site.median(),
        temp_r2=temporal_r2,
        imp=pd.Series(m_temporal.feature_importances_, index=feat).sort_values(ascending=False),
    )


def main():
    cands = []
    for uid in get_site_ids():
        try:
            if daily_nitrate(uid).dropna().shape[0] >= MIN_DAYS:
                cands.append(uid)
        except Exception:
            pass
    n_groups = len(set(_BASIN_GROUP.get(s, s) for s in cands))
    print(f"candidate sites: {len(cands)}  (~{n_groups} basin groups)\n")

    pool_nobucket = build_pool(cands, edges=[])  # one whole-basin aggregate
    pool_buckets = build_pool(cands, edges=[50_000, 150_000])  # near / mid / far
    print(f"pool edges=[]        : {len(pool_nobucket):,} rows, {pool_nobucket['site'].nunique()} sites")
    print(f"pool edges=[50k,150k]: {len(pool_buckets):,} rows, {pool_buckets['site'].nunique()} sites\n")

    # 2x2 ablation: {whole-basin, buckets} x {no static, +static}
    runs = {
        "no-bucket, no-static": (pool_nobucket, True),
        "no-bucket, +static": (pool_nobucket, False),
        "buckets,   no-static": (pool_buckets, True),
        "buckets,   +static": (pool_buckets, False),
    }
    header = (
        f"{'config':22s} {'feat':>4s} {'LOSO_R2':>7s} {'LOBO_R2':>7s} {'btwn_R2':>7s} " f"{'medPS':>6s} {'temp_R2':>7s}"
    )
    print(header)
    print("-" * len(header))
    res = {}
    for name, (pool, drop_static) in runs.items():
        r = evaluate(pool, drop_static)
        res[name] = r
        print(
            f"{name:22s} {r['n_feat']:4d} {r['loso_r2']:7.3f} {r['lobo_r2']:7.3f} {r['between']:7.3f} "
            f"{r['med_ps']:6.3f} {r['temp_r2']:7.3f}"
        )

    print("\ntop 15 features (buckets, +static):\n" + res["buckets,   +static"]["imp"].head(15).to_string())


if __name__ == "__main__":
    main()
