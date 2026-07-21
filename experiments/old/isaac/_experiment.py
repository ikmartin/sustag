"""
Single-site feature-ablation experiment  (regression target: daily nitrate_con).
================================================================================

Question: at ONE site, how much does each *class* of feature actually buy us?
We compare three feature "recipes" of increasing information:

    A  covariates only   weather + crop/surplus land-use + pure calendar (doy sin/cos).
                         No nitrate-derived features -- the honest "sensorless" case.
    B  A + own history   the site's own past nitrate at several lags (autoregression;
                         a sensor legitimately sees its own past).
    C  A + cross-site    causal cross-site climatology + neighbouring sensors' past
                         nitrate (the spatial-nowcasting case).

Each recipe is trained with three XGBoost configs, from heavily over-fit to strongly
regularized, under a leakage-aware protocol:

  * chronological 70 / 15 / 15 split (train / validation / test). The validation slice
    drives early stopping; the final 15 % is reported on and never used for fitting.
  * RMSE is the headline metric. R2 is shown too but is noisy on small / low-variance
    windows, so read RMSE first.

Two naive baselines anchor the numbers:
  * predict-mean     -- always predict the training mean.
  * persistence(t-1) -- predict yesterday's own nitrate (very strong on an
                        autocorrelated series; a model that can't beat it is not earning
                        its complexity).

Scratch / untracked.  Run:  python isaac/_experiment.py
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

from data.features import (
    agg_crops, agg_surplus, agg_weather_w_lag, daily_nitrate, lagged_sensor_nitrate,
    nitrate_rolling, nitrate_avg_seasonal, nitrate_avg_calendar, doy_climatology_pure_signal,
)
from data.transforms import flatten_buckets, merge_on_date, match_seasonal

# ── what we model ────────────────────────────────────────────────────────────
TARGET = "USGS-05482300"                                   # long record, full land-use coverage
NEIGHBORS = ["USGS-05482500", "USGS-05484500", "USGS-05465500"]  # nearby gauges (recipe C)
TARGETCOL = "nitrate_con"
EDGES = [50_000, 150_000]      # distance-bucket edges (m): near / mid / far
VEL = 0.8                      # water celerity (m/s) for travel-time weather lags


# ── feature recipes ──────────────────────────────────────────────────────────
def _covariates(site):
    """The shared, leakage-free covariate block used by every recipe.

    Returns (daily_nitrate_series, [feature_frames]):
    distance-bucketed + travel-time-lagged weather, exp-decay-weighted crop & surplus
    land-use, and the pure-calendar day-of-year sin/cos. No nitrate values leak in.
    """
    weather = flatten_buckets(agg_weather_w_lag(site, edges=EDGES, water_velocity=VEL))
    crops = flatten_buckets(agg_crops(site, edges=EDGES, lam=10_000, exp=True))
    surplus = flatten_buckets(agg_surplus(site, edges=EDGES, lam=10_000, exp=True))
    n_daily = daily_nitrate(site).rename(TARGETCOL)
    calendar = doy_climatology_pure_signal(n_daily)        # doy_sin / doy_cos
    return n_daily, [weather, crops, surplus, calendar]


def recipe_A(site):
    """Covariates only -- weather + land-use + calendar. No nitrate-derived features."""
    n_daily, parts = _covariates(site)
    return merge_on_date([n_daily, *parts], spine=n_daily.index)


def recipe_B(site):
    """A + the site's OWN past nitrate at lags 1..30 days (legitimate autoregression)."""
    n_daily, parts = _covariates(site)
    own_history = [lagged_sensor_nitrate([site], shift=k) for k in (1, 2, 3, 7, 14, 30)]
    return merge_on_date([n_daily, *parts, *own_history], spine=n_daily.index)


def recipe_C(site):
    """A + causal cross-site climatology + neighbouring sensors' past nitrate."""
    n_daily, parts = _covariates(site)
    dates = n_daily.index
    climatology = [
        nitrate_rolling("7D", center=False).rename("nroll_7"),     # trailing cross-site means
        nitrate_rolling("30D", center=False).rename("nroll_30"),
        nitrate_avg_calendar("D").rename("ncal_d"),                # yesterday's cross-site mean
        match_seasonal(dates, nitrate_avg_seasonal("D")).rename("ndoy"),   # seasonal climatology
        match_seasonal(dates, nitrate_avg_seasonal("W")).rename("nwoy"),
        match_seasonal(dates, nitrate_avg_seasonal("M")).rename("nmoy"),
    ]
    neighbours = [lagged_sensor_nitrate(NEIGHBORS, shift=k) for k in (1, 3, 7)]
    return merge_on_date([n_daily, *parts, *climatology, *neighbours], spine=dates)


# ── XGBoost configs: over-fit -> strongly regularized ────────────────────────
CONFIGS = {
    "deep_underreg":  dict(max_depth=6, min_child_weight=1,  reg_lambda=1,  subsample=0.8, colsample_bytree=0.8, learning_rate=0.03, n_estimators=2000),
    "regularized":    dict(max_depth=3, min_child_weight=10, reg_lambda=5,  subsample=0.7, colsample_bytree=0.7, learning_rate=0.02, n_estimators=3000),
    "shallow_stumps": dict(max_depth=2, min_child_weight=20, reg_lambda=10, subsample=0.6, colsample_bytree=0.6, learning_rate=0.02, n_estimators=4000),
}


def evaluate(df, cfg):
    """Train one config on a recipe's dataframe and score it on the held-out test slice.

    Chronological 70/15/15 split: train fits, validation drives early stopping, the
    final 15 % is the reported test set. Returns a dict of metrics + the fitted model.
    """
    df = df.dropna(subset=[TARGETCOL]).reset_index(drop=True)
    feat = [c for c in df.columns if c not in (TARGETCOL, "site_uid", "date")]
    X, y = df[feat], df[TARGETCOL]

    n = len(df)
    i_tr, i_val = int(n * 0.70), int(n * 0.85)
    Xtr, ytr = X.iloc[:i_tr], y.iloc[:i_tr]            # train
    Xva, yva = X.iloc[i_tr:i_val], y.iloc[i_tr:i_val]  # validation -> early stopping
    Xte, yte = X.iloc[i_val:], y.iloc[i_val:]          # test -> report only

    model = xgb.XGBRegressor(random_state=42, eval_metric="rmse", early_stopping_rounds=50, **cfg)
    model.fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)
    pred = model.predict(Xte)
    return dict(
        n_feat=len(feat), n_test=len(yte), best_iter=model.best_iteration + 1,
        rmse=np.sqrt(mean_squared_error(yte, pred)), mae=mean_absolute_error(yte, pred),
        r2=r2_score(yte, pred), model=model, feat=feat,
    )


def baselines(df):
    """Naive reference RMSEs on the same test window: predict-mean and persistence(t-1)."""
    df = df.dropna(subset=[TARGETCOL]).reset_index(drop=True)
    n = len(df); i_val = int(n * 0.85)
    yte = df[TARGETCOL].iloc[i_val:]

    mean_pred = df[TARGETCOL].iloc[:i_val].mean()                       # always the train mean
    yesterday = lagged_sensor_nitrate([TARGET], shift=1).iloc[:, 0]     # own value one day back
    yesterday = yesterday.reindex(pd.to_datetime(df["date"])).to_numpy()[i_val:]
    ok = ~np.isnan(yesterday)
    return {
        "mean": np.sqrt(mean_squared_error(yte, np.full(len(yte), mean_pred))),
        "persist_t-1": np.sqrt(mean_squared_error(yte[ok], yesterday[ok])),
    }, len(yte)


def main():
    print(f"target={TARGET}  neighbors={NEIGHBORS}  edges={EDGES}\n")

    # build each recipe's dataframe once
    recipes = {"A_covariates": recipe_A, "B_+own_AR": recipe_B, "C_+clim+neighbors": recipe_C}
    built = {}
    for name, fn in recipes.items():
        d = fn(TARGET)
        built[name] = d
        n_rows = d.dropna(subset=[TARGETCOL]).shape[0]
        n_feat = len([c for c in d.columns if c not in (TARGETCOL, "date", "site_uid")])
        print(f"{name:18s} rows={n_rows:4d}  feats={n_feat:3d}")

    bl, n_test = baselines(built["A_covariates"])
    print(f"\nbaselines on test window (n={n_test}):  "
          f"predict-mean RMSE={bl['mean']:.3f}   persistence(t-1) RMSE={bl['persist_t-1']:.3f}\n")

    # every recipe x every config
    header = f"{'recipe':18s} {'config':15s} {'feat':>4s} {'iters':>5s} {'RMSE':>7s} {'MAE':>7s} {'R2':>7s}"
    print(header); print("-" * len(header))
    results = []
    for rname, d in built.items():
        for cname, cfg in CONFIGS.items():
            r = evaluate(d, cfg)
            results.append((rname, cname, r))
            print(f"{rname:18s} {cname:15s} {r['n_feat']:4d} {r['best_iter']:5d} "
                  f"{r['rmse']:7.3f} {r['mae']:7.3f} {r['r2']:7.3f}")

    best_recipe, best_cfg, best = min(results, key=lambda t: t[2]["rmse"])
    print(f"\nbest: {best_recipe} / {best_cfg}  (RMSE={best['rmse']:.3f})")
    imp = pd.Series(best["model"].feature_importances_, index=best["feat"]).sort_values(ascending=False)
    print("top 12 features:\n" + imp.head(12).to_string())


if __name__ == "__main__":
    main()
