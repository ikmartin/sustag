"""demo_baselines.py -- the team's NON-XGBoost baseline models, consolidated for the demo's
"why XGBoost was chosen" narrative. None of these use XGBoost; they are the benchmarks the spatial
XGBoost recipe had to beat.

    from demo_baselines import *
    regression_model_comparison(uid)     # Jay: dummy/linear/ridge/lasso/RF/GBM, CV R2 + test R2/RMSE
    plot_exponential_smoothing(uid)      # Jay: Holt-Winters seasonal forecast (pure history), test MSE
    erin_baselines(uid)                  # Erin: persistence / linreg / dummy / logreg / gaussian-RW

Ported to the refactored data layer (src.data.access). Provenance:
  * Jay   -- regression_first_attempt.ipynb (flat per-site regression panel)
             seasonal_modeling.ipynb        (Holt-Winters exponential smoothing)
  * Erin  -- baseline_models.ipynb          (next-day persistence / linear / logistic / random-walk)

NOTE on ARIMA/SARIMA: Isaac's ma_ar_sarima.py is an empty stub and his only ARIMA use is a single
exploratory AutoARIMA(...).fit(rain) that just prints the fitted order (no forecast, no metrics), so
there is no real classical-TS baseline to port here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path

import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from src.data import access

# shared constants + the daily-nitrate helper + the modelling-pool site picker live in demo_eda
from demo_eda import VIOLATION_MGL, CROP_COLORS, demo_sites, _daily  # noqa: F401  (re-exported)


######################################################
# Jay -- seasonal_modeling.ipynb: Holt-Winters
######################################################


def _resampled_nitrate(uid: str, freq: str = "ME", agg: str = "max") -> pd.Series:
    """Daily nitrate resampled to `freq` (ME=month-end / W=week), gap-filled by time-interpolation
    -- the continuous, evenly-spaced series Holt-Winters needs (Jay's prep in seasonal_modeling)."""
    s = _daily(uid, agg).dropna()
    s = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D"))  # continuous daily index
    s = s.interpolate(method="time")  # bridge the gaps so the seasonal model sees a regular series
    return s.resample(freq).mean()


def plot_exponential_smoothing(uid: str, freq: str = "ME", agg: str = "max"):
    """Jay's Holt-Winters (triple) exponential-smoothing forecast of nitrate. Resamples the daily
    max to `freq` (ME=monthly / W=weekly), splits 80/20 chronologically, fits an additive-trend
    (damped) + additive-seasonal model, and forecasts the held-out tail. A pure-history seasonal
    baseline -- no rain/land-use covariates, just level + trend + annual cycle -- the starting point
    the covariate models had to beat. Title carries the held-out MSE."""
    from statsmodels.tsa.api import ExponentialSmoothing
    from sklearn.metrics import mean_squared_error

    y = _resampled_nitrate(uid, freq=freq, agg=agg)
    periods = 12 if freq.upper().startswith("M") else 52  # annual cycle length at this sampling freq
    split = int(len(y) * 0.8)
    train, test = y.iloc[:split], y.iloc[split:]  # chronological split (no shuffling for a forecast)

    fit = ExponentialSmoothing(train, trend="add", damped_trend=True, seasonal="add", seasonal_periods=periods).fit()
    forecast = fit.forecast(len(test))
    mse = mean_squared_error(test, forecast)

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(train.index, train.to_numpy(), color="0.55", lw=1.2, label="train")
    ax.plot(test.index, test.to_numpy(), color="#1fb473", lw=1.8, label="actual")
    ax.plot(test.index, forecast.to_numpy(), color="#c1121f", lw=1.8, ls="--", label="forecast")
    ax.axhline(VIOLATION_MGL, ls=":", color="k", alpha=0.4)
    grain = "monthly" if periods == 12 else "weekly"
    ax.set(
        title=f"Holt-Winters {grain} forecast — {uid}  (test MSE={mse:.3f})",
        xlabel="date",
        ylabel=f"{agg} nitrate (mg/L)",
    )
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    return fig, ax


######################################################
# Jay -- regression_first_attempt.ipynb: flat-feature panel
######################################################


def _regression_frame(uid: str) -> pd.DataFrame:
    """Jay's weekly regression design matrix (regression_first_attempt), adapted to src.data.access:
    weekly-mean nitrate (target) joined to weekly-mean basin precip and the annual surplus/crop
    covariates broadcast by year. One flat row per week."""
    nitrate_weekly = access.get_water(uid)["nitrate_con"].resample("1W").mean().reset_index()
    nitrate_weekly.columns = ["date", "nitrate_avg"]
    nitrate_weekly["date"] = pd.to_datetime(nitrate_weekly["date"]).dt.tz_localize(None)
    nitrate_weekly["year"] = nitrate_weekly["date"].dt.year

    rain = access.get_weather(uid).groupby("date")["precip_in_1d"].mean()  # basin-mean daily precip
    rain.index = pd.to_datetime(rain.index)
    rain_weekly = rain.resample("1W").mean().reset_index()
    rain_weekly.columns = ["date", "precip_avg"]

    surplus_annual = access.get_surplus(uid).groupby("year")["surplus_kgha"].mean().reset_index()
    crop_cols = [c for c in CROP_COLORS if c in access.get_crops(uid).columns]
    crops_annual = access.get_crops(uid).groupby("year")[crop_cols].mean().reset_index()

    df = (
        nitrate_weekly.merge(surplus_annual, on="year", how="left")
        .merge(crops_annual, on="year", how="left")
        .merge(rain_weekly, on="date", how="left")
        .dropna()
    )
    return df


def regression_model_comparison(uid: str, test_size: float = 0.2, seed: int = 42) -> pd.DataFrame:
    """Jay's 'first attempt' baseline panel (regression_first_attempt): build the weekly flat-feature
    frame and score a set of off-the-shelf regressors -- dummy / linear / ridge / lasso / random
    forest / gradient boosting -- with 5-fold CV R² plus held-out test R²/RMSE. These per-site,
    flat-feature models (no spatial structure, no travel-time lags) were the motivation for moving to
    the spatial XGBoost recipe. Returns the results sorted by test_r2 (higher = better).

    NOTE (kept faithful to Jay): the split is a plain random train_test_split, so it leaks a little
    across time -- fine as a rough baseline, but the honest number is the spatial-CV recipe score."""
    from sklearn.model_selection import train_test_split, cross_val_score
    from sklearn.preprocessing import StandardScaler
    from sklearn.dummy import DummyRegressor
    from sklearn.linear_model import LinearRegression, Ridge, Lasso
    from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
    from sklearn.metrics import r2_score, mean_squared_error

    df = _regression_frame(uid)
    feature_cols = [c for c in df.columns if c not in ("date", "nitrate_avg", "year")]
    X, y = df[feature_cols], df["nitrate_avg"]
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=test_size, random_state=seed)
    scaler = StandardScaler().fit(X_tr)
    Xtr, Xte = scaler.transform(X_tr), scaler.transform(X_te)

    models = {
        "Dummy (mean)": DummyRegressor(strategy="mean"),
        "Linear Regression": LinearRegression(),
        "Ridge": Ridge(),
        "Lasso": Lasso(),
        "Random Forest": RandomForestRegressor(n_estimators=100, random_state=seed),
        "Gradient Boosting": GradientBoostingRegressor(n_estimators=100, random_state=seed),
    }
    rows = []
    for name, m in models.items():
        cv = cross_val_score(m, Xtr, y_tr, cv=5, scoring="r2")
        m.fit(Xtr, y_tr)
        pred = m.predict(Xte)
        rows.append(
            {
                "model": name,
                "cv_r2_mean": cv.mean(),
                "cv_r2_std": cv.std(),
                "test_r2": r2_score(y_te, pred),
                "test_rmse": np.sqrt(mean_squared_error(y_te, pred)),
            }
        )
    return pd.DataFrame(rows).sort_values("test_r2", ascending=False).reset_index(drop=True)


######################################################
# Erin -- baseline_models.ipynb: next-day baselines
######################################################


def erin_site_df(site: str) -> pd.DataFrame:
    """Erin's per-site next-day frame (baseline_models.make_site_df), adapted to src.data.access.

    One row per day with daily nitrate mean/max + a `violation` flag, basin-mean precip, the
    per-cell precip×2017-surplus interaction (`rain_x_surplus`), rolling 7/14/30d rain and
    rain×surplus sums, annual crops, basin area, the autoregressive nitrate lags, and the shifted
    next-day targets `nitrate_tomorrow` / `violation_tomorrow`. (Faithful to Erin's quirk that
    `nitrate_lag1` is TODAY's value, i.e. the persistence/random-walk anchor.)"""
    # daily precip (basin mean) + the per-cell precip x 2017-surplus interaction
    weather = access.get_weather(site)
    precip = weather.groupby("date", as_index=False)["precip_in_1d"].mean()

    surplus = access.get_surplus(site)
    nitrogen_2017 = surplus[surplus["year"] == 2017]
    surplus_n_2017 = nitrogen_2017["surplus_kgha"].mean()
    rx = weather.merge(nitrogen_2017[["node_id", "surplus_kgha"]], on="node_id", how="left")
    rx["rain_x_surplus"] = rx["precip_in_1d"] * rx["surplus_kgha"]
    daily_rain_x_surplus = rx.groupby("date", as_index=False)["rain_x_surplus"].mean()

    # daily nitrate statistics + violation flag
    water = (
        access.get_water(site)
        .resample("1D")["nitrate_con"]
        .agg(nitrate_con="mean", nitrate_max="max")
        .reset_index(names="date")
    )
    water["date"] = pd.to_datetime(water["date"]).dt.tz_localize(None)
    water["violation"] = (water["nitrate_max"] > VIOLATION_MGL).astype(int)
    for frame in (precip, daily_rain_x_surplus):
        frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None)

    df = water.merge(precip, on="date", how="left").merge(daily_rain_x_surplus, on="date", how="left")

    # rolling rainfall + rain×surplus sums
    for window in (7, 14, 30):
        df[f"rain_{window}d"] = df["precip_in_1d"].rolling(window, min_periods=1).sum()
        df[f"rain_x_surplus_{window}d"] = df["rain_x_surplus"].rolling(window, min_periods=1).sum()

    # annual crops (summed over the basin), broadcast by year
    crops = access.get_crops(site)
    crop_cols = [c for c in CROP_COLORS if c in crops.columns]
    crops_annual = crops.groupby("year", as_index=False)[crop_cols].sum()
    df["year"] = df["date"].dt.year
    df = df.merge(crops_annual, on="year", how="left")

    # site-level scalars
    df["surplus_n_2017"] = surplus_n_2017
    df["site_id"] = site
    df["basin_area"] = access.get_basin_area(site)

    # autoregressive features (lag1 = today, per Erin) + next-day targets
    df["nitrate_lag1"] = df["nitrate_con"]
    df["nitrate_lag2"] = df["nitrate_con"].shift(1)
    df["nitrate_lag3"] = df["nitrate_con"].shift(2)
    df["nitrate_tomorrow"] = df["nitrate_con"].shift(-1)
    df["violation_tomorrow"] = df["violation"].shift(-1)

    return df.dropna(subset=["nitrate_tomorrow", "nitrate_lag3", "nitrate_con"]).reset_index(drop=True)


def erin_baselines(
    uid: str,
    feature_cols=("nitrate_lag1", "rain_x_surplus_7d"),
    n_splits: int = 5,
    test_size: int = 100,
    verbose: bool = True,
) -> pd.DataFrame:
    """Erin's next-day baseline suite (baseline_models). Under a TimeSeriesSplit (train on the past,
    test on a later contiguous block) it scores, per fold:

      classification (Average Precision / PR-AUC, higher better):
        dummy_most_frequent, dummy_stratified, gaussian_rw (P(tomorrow>10 | today) ~ N(today, σ²)), logreg
      regression (MAE / RMSE in mg/L, lower better):
        persistence (predict today's value), linreg

    The reading (Erin's): the Gaussian random walk should beat the dummies by a wide margin (it knows
    today's value); if logreg can't beat the random walk, or linreg can't beat persistence, the
    engineered features aren't adding information beyond "what happened yesterday" -- an honest,
    useful negative result. Returns a mean/std-across-folds summary DataFrame."""
    from scipy.stats import norm
    from sklearn.dummy import DummyClassifier
    from sklearn.linear_model import LogisticRegression, LinearRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import average_precision_score, mean_absolute_error, mean_squared_error

    df = erin_site_df(uid)
    feature_cols = list(feature_cols)
    X = df[feature_cols].fillna(0).values
    y_class = df["violation_tomorrow"].values
    y_reg = df["nitrate_tomorrow"].values
    today_value = df["nitrate_con"].values  # the persistence / random-walk anchor

    keys = [
        "dummy_most_frequent_AP", "dummy_stratified_AP", "gaussian_rw_classifier_AP", "logreg_AP",
        "persistence_regression_MAE", "persistence_regression_RMSE", "linreg_MAE", "linreg_RMSE",
    ]
    results = {k: [] for k in keys}

    tscv = TimeSeriesSplit(n_splits=n_splits, test_size=test_size)
    for fold, (tr, te) in enumerate(tscv.split(X)):
        Xtr, Xte = X[tr], X[te]
        yc_tr, yc_te = y_class[tr], y_class[te]
        yr_tr, yr_te = y_reg[tr], y_reg[te]
        today_tr, today_te = today_value[tr], today_value[te]
        if verbose:
            print(f"Fold {fold}: train={len(tr)}  test={len(te)}  test_violations={int(yc_te.sum())}")
            if yc_te.sum() == 0:
                print("  WARNING: this fold has zero violations -- AP undefined/misleading for it.")

        # dummy classifiers (the honest floor)
        for strategy, key in [("most_frequent", "dummy_most_frequent_AP"), ("stratified", "dummy_stratified_AP")]:
            dummy = DummyClassifier(strategy=strategy, random_state=0).fit(Xtr, yc_tr)
            results[key].append(average_precision_score(yc_te, dummy.predict_proba(Xte)[:, 1]))

        # Gaussian random walk: sigma from TRAIN diffs only (no leakage)
        train_diffs = np.diff(today_tr)
        sigma = max(np.std(train_diffs), 1e-6) if len(train_diffs) > 1 else 1.0
        rw_proba = 1 - norm.cdf((VIOLATION_MGL - today_te) / sigma)  # P(tomorrow > 10 | today)
        results["gaussian_rw_classifier_AP"].append(average_precision_score(yc_te, rw_proba))

        # persistence regression: forecast = today's value
        results["persistence_regression_MAE"].append(mean_absolute_error(yr_te, today_te))
        results["persistence_regression_RMSE"].append(np.sqrt(mean_squared_error(yr_te, today_te)))

        # logistic regression (feature-based classification)
        logreg = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=1000)).fit(Xtr, yc_tr)
        results["logreg_AP"].append(average_precision_score(yc_te, logreg.predict_proba(Xte)[:, 1]))

        # linear regression (feature-based regression)
        linreg = make_pipeline(StandardScaler(), LinearRegression()).fit(Xtr, yr_tr)
        pred = linreg.predict(Xte)
        results["linreg_MAE"].append(mean_absolute_error(yr_te, pred))
        results["linreg_RMSE"].append(np.sqrt(mean_squared_error(yr_te, pred)))

    summary = pd.DataFrame(
        {"mean": {k: float(np.mean(v)) for k, v in results.items()},
         "std": {k: float(np.std(v)) for k, v in results.items()}}
    )
    if verbose:
        print("\n=== Cross-validated results (mean +/- std across folds) ===")
        print("\n-- Classification (Average Precision / PR-AUC, higher is better) --")
        for k in ["dummy_most_frequent_AP", "dummy_stratified_AP", "gaussian_rw_classifier_AP", "logreg_AP"]:
            print(f"{k:30s}: {summary.loc[k, 'mean']:.4f} +/- {summary.loc[k, 'std']:.4f}")
        print("\n-- Regression (MAE / RMSE in mg/L, lower is better) --")
        for k in ["persistence_regression_MAE", "persistence_regression_RMSE", "linreg_MAE", "linreg_RMSE"]:
            print(f"{k:30s}: {summary.loc[k, 'mean']:.4f} +/- {summary.loc[k, 'std']:.4f}")
    return summary
