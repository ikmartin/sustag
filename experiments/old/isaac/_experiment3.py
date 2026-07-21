"""
Cross-site Recipe A, CLASSIFICATION target: nitrate_violations (daily nitrate >= THRESHOLD).
============================================================================================

The classification twin of _experiment2.py. Same pooled cross-site setup and the same
2x2 ablation (static features x distance buckets), but the target is the binary
"is today a violation?" (daily nitrate >= 10 mg/L) and the model is XGBClassifier.

Honest generalization, two group keys (see _experiment2.py for the full rationale):
  LOSO  leave-sites-out  -- GroupKFold by site            (optimistic: nested basins leak)
  LOBO  leave-basins-out -- GroupKFold by conflict group  (honest: data/splits.py components)
  temporal -- train < 2022 / test >= 2022.

Metrics per config:
  LOSO_AUC / LOBO_AUC  ROC-AUC of out-of-fold P(violation), by site / by basin group
  PR-AUC               average precision (read against the base rate, not 0.5)
  Brier                probability quality (lower better); flags miscalibration
  btwn_R2              R2 of predicted-mean-prob vs actual violation RATE, per site
  medAUC               median per-site held-out AUC (within-site discrimination)
  tempAUC              temporal-holdout AUC

Scratch / untracked.  Run:  python isaac/_experiment3.py
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
import xgboost as xgb
from sklearn.model_selection import GroupKFold
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss, r2_score

from data.access import get_site_ids
from data.features import (agg_crops, agg_surplus, agg_weather_w_lag, daily_nitrate,
                           doy_climatology_pure_signal, site_static)
from data.transforms import flatten_buckets, merge_on_date
from data.splits import split_groups

TCOL = "nitrate_con"
VEL = 0.8
THRESHOLD = 10                    # violation if daily nitrate >= 10 mg/L
STATIC_COLS = ["lat", "lon", "log_basin_area", "mean_dist_to_sensor", "max_dist_to_sensor"]
CUT = pd.Timestamp("2022-01-01")
MIN_DAYS = 1000
N_SPLITS = 5

CFG = dict(n_estimators=2000, learning_rate=0.03, max_depth=4, min_child_weight=20,
           subsample=0.8, colsample_bytree=0.8, reg_lambda=5.0, random_state=42,
           objective="binary:logistic", eval_metric="logloss")

_BASIN_GROUP = split_groups()     # site -> conflict-component id (basin nesting x temporal overlap)


def basin_group_ids(site_series):
    """Map site_uids to conflict-component ids; sites absent from the basin graph become
    their own singleton group (so they can't leak into another component)."""
    next_id = max(_BASIN_GROUP.values(), default=-1) + 1
    mapping = {}
    for s in pd.unique(site_series):
        if s in _BASIN_GROUP:
            mapping[s] = _BASIN_GROUP[s]
        else:
            mapping[s] = next_id; next_id += 1
    return site_series.map(mapping)


def build_pool(site_uids, edges):
    """Stack Recipe A (+ static descriptors) for many sites into one long dataframe.
    (Identical to _experiment2.build_pool; the binary target is derived at eval time.)"""
    frames = []
    for uid in site_uids:
        try:
            weather = flatten_buckets(agg_weather_w_lag(uid, edges=edges, water_velocity=VEL))
            crops = flatten_buckets(agg_crops(uid, edges=edges, lam=10_000, exp=True))
            surplus = flatten_buckets(agg_surplus(uid, edges=edges, lam=10_000, exp=True))
            n_daily = daily_nitrate(uid).rename(TCOL)
            calendar = doy_climatology_pure_signal(n_daily)
            d = merge_on_date([n_daily, weather, crops, surplus, calendar], spine=n_daily.index)
            d = d.dropna(subset=[TCOL])
            if len(d) < 500:
                continue
            for col, val in site_static(uid).items():
                d[col] = val
            d["site"] = uid
            frames.append(d)
        except Exception:
            pass
    return pd.concat(frames, ignore_index=True)


def _oof_proba(X, y, groups):
    """Out-of-fold P(violation) from GroupKFold(N_SPLITS) with the given group key."""
    oof = np.full(len(y), np.nan)
    for train_idx, test_idx in GroupKFold(N_SPLITS).split(X, y, groups=groups):
        clf = xgb.XGBClassifier(**CFG)
        clf.fit(X.iloc[train_idx], y.iloc[train_idx], verbose=False)
        oof[test_idx] = clf.predict_proba(X.iloc[test_idx])[:, 1]
    return oof


def evaluate(pool, drop_static):
    """Leave-sites-out + leave-basins-out CV and temporal holdout; return a metrics dict."""
    feat = [c for c in pool.columns if c not in (TCOL, "site", "date")]
    if drop_static:
        feat = [c for c in feat if c not in STATIC_COLS]
    X = pool[feat]
    y = (pool[TCOL] >= THRESHOLD).astype(int)            # 1 = violation

    oof_site = _oof_proba(X, y, pool["site"])
    oof_basin = _oof_proba(X, y, basin_group_ids(pool["site"]))
    loso_auc = roc_auc_score(y, oof_site)
    lobo_auc = roc_auc_score(y, oof_basin)
    prauc = average_precision_score(y, oof_site)
    brier = brier_score_loss(y, oof_site)

    # between-site: actual violation RATE vs predicted mean prob, per site
    sm = pool.assign(p=oof_site, yv=y).groupby("site").agg(rate=("yv", "mean"), pred=("p", "mean"))
    between = r2_score(sm.rate, sm.pred)

    # per-site held-out AUC (sites with both classes present)
    ps = pool.assign(p=oof_site, yv=y).groupby("site").apply(
        lambda d: roc_auc_score(d.yv, d.p) if d.yv.nunique() == 2 else np.nan)

    # temporal holdout
    dt = pd.to_datetime(pool["date"]); is_tr, is_te = dt < CUT, dt >= CUT
    clf2 = xgb.XGBClassifier(**CFG); clf2.fit(X[is_tr], y[is_tr], verbose=False)
    p2 = clf2.predict_proba(X[is_te])[:, 1]
    temp_auc = roc_auc_score(y[is_te], p2) if y[is_te].nunique() == 2 else np.nan

    return dict(n_feat=len(feat), loso_auc=loso_auc, lobo_auc=lobo_auc, prauc=prauc, brier=brier,
                between=between, med_auc=ps.median(), temp_auc=temp_auc,
                imp=pd.Series(clf2.feature_importances_, index=feat).sort_values(ascending=False))


def main():
    cands = []
    for uid in get_site_ids():
        try:
            if daily_nitrate(uid).dropna().shape[0] >= MIN_DAYS:
                cands.append(uid)
        except Exception:
            pass
    print(f"candidate sites: {len(cands)}  |  target: nitrate >= {THRESHOLD} mg/L\n")

    pool_nobucket = build_pool(cands, edges=[])
    pool_buckets = build_pool(cands, edges=[50_000, 150_000])
    base = (pool_nobucket[TCOL] >= THRESHOLD).mean()
    print(f"pool edges=[]        : {len(pool_nobucket):,} rows, {pool_nobucket['site'].nunique()} sites  |  base rate={base:.3f}")
    print(f"pool edges=[50k,150k]: {len(pool_buckets):,} rows, {pool_buckets['site'].nunique()} sites\n")

    runs = {
        "no-bucket, no-static": (pool_nobucket, True),
        "no-bucket, +static":   (pool_nobucket, False),
        "buckets,   no-static": (pool_buckets, True),
        "buckets,   +static":   (pool_buckets, False),
    }
    header = (f"{'config':22s} {'feat':>4s} {'LOSO_AUC':>8s} {'LOBO_AUC':>8s} {'PR-AUC':>7s} "
              f"{'Brier':>6s} {'btwn_R2':>7s} {'medAUC':>6s} {'tempAUC':>7s}")
    print(header); print("-" * len(header))
    res = {}
    for name, (pool, drop_static) in runs.items():
        r = evaluate(pool, drop_static); res[name] = r
        print(f"{name:22s} {r['n_feat']:4d} {r['loso_auc']:8.3f} {r['lobo_auc']:8.3f} {r['prauc']:7.3f} "
              f"{r['brier']:6.3f} {r['between']:7.3f} {r['med_auc']:6.3f} {r['temp_auc']:7.3f}")

    print("\ntop 15 features (buckets, +static):\n" + res["buckets,   +static"]["imp"].head(15).to_string())


if __name__ == "__main__":
    main()
