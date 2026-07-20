"""model_demo.py -- notebook wrappers for the model-CV and full-training slices of the demo.

Thin one-liners over the real harness (src.eval.cook + src.models.train), so the notebook stays
clean:

    from model_demo import *
    cv = run_cv(recipe_CLF, task="clf")     # cross-site CV: LOSO/LOFO + the class-imbalance suite
    show_metrics(cv)                         # tidy transposed metric -> value view
    top_features(cv, n=15)                   # gain-ranked feature importances
    run_full_train("demo_CLF", recipe_CLF, task="clf")   # CV + fit on ALL rows + log + save booster

`run_cv` uses the quick FAST_XGB config by default (snappy for a live demo); `run_full_train` uses
the tuned REAL_XGB config and produces the actual deployable model + a fulltrain_logs.json entry.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from src.eval.cook import compare_many, FAST_XGB, _pool, _features, _target, _grouped_models, basin_groups
from src.features.recipes import recipe_REG, recipe_CLF  # noqa: F401  (re-exported for the notebook)
from src.features.recipes import _rolling_weather, _weather_windows
from src.data.access import get_site_ids
from src.features.features import (
    daily_nitrate,
    agg_weather,
    agg_crops,
    agg_surplus,
    doy_climatology_pure_signal,
    nitrate_violations_rolling,
    site_static,
)
from src.features.transformers import flatten_buckets, merge_on_date
import src.models.train as train
from demo_recipes import preet_recipe

_TARGET = {"reg": "nitrate_con", "clf": "violation"}


def demo_sites(min_obs: int = 1500) -> list:
    """Site UIDs with at least `min_obs` observed daily-nitrate days -- the modelling pool."""
    return [s for s in get_site_ids() if daily_nitrate(site_uid=s).dropna().shape[0] >= min_obs]


def run_cv(recipe, task: str = "clf", sites=None, xgb=None, name=None, min_rows: int = 500) -> pd.DataFrame:
    """Cross-site CV for one recipe: leave-one-site-out (loso_*) and leave-one-basin-family-out (lofo_*) metrics -- for clf that includes the class-imbalance suite (prauc_lift, f2, mcc, recall_at_far). Returns the one-row metrics DataFrame (gain importances on `.attrs`).

    Defaults: FAST_XGB (quick demo config)
    `sites`: subset to speed up a live demo further. `min_rows` is the per-site inclusion floor
    `min_rows`: (drop sites with fewer usable rows); lower it to admit shorter records.
    """
    xgb = FAST_XGB if xgb is None else xgb
    name = name or getattr(recipe, "__name__", "recipe")
    return compare_many({name: recipe}, sites=sites, target_col=_TARGET[task], task=task, min_rows=min_rows, **xgb)


def show_metrics(res: pd.DataFrame) -> pd.DataFrame:
    """Transposed, rounded view of a run_cv result (one metric per row)."""
    return res.T.round(4)


def top_features(res: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    """Top-`n` gain-ranked feature importances from a run_cv result (free, always computed)."""
    imp = res.attrs.get("importance", {})
    return pd.DataFrame({name: s.head(n) for name, s in imp.items()})


def run_full_train(name: str, recipe, task: str = "clf", xgb=None, final_iters=None, min_rows: int = 500) -> None:
    """Full deployable run: cross-site CV (logged to fulltrain_logs.json), then fit on ALL rows and save the booster to models/<name>.json. Wraps src.models.train.build. Uses the tuned REAL_XGB config for the task by default. NOTE: the real config trains 5000-tree models -- this is the slow, side-effecting "actual training run" (writes a model + a log entry). `min_rows` is the per-site inclusion floor for the pool."""
    if xgb is None:
        xgb = train.REAL_XGB_CLF if task == "clf" else train.REAL_XGB_REG
    train.build(name, recipe, target_col=_TARGET[task], task=task, xgb=xgb, final_iters=final_iters, min_rows=min_rows)


# Preet's model diagnostics: feature-importance bars + spatial-CV PR curves
def plot_feature_importance(cv_result: pd.DataFrame, n: int = 20, name=None):
    """Top-`n` gain-ranked feature importances from a run_cv result, as Preet's horizontal bar chart (feature_importance.png / feature_importance_nitrate_breach.png). Works for reg or clf."""
    imp = cv_result.attrs.get("importance", {})
    name = name or next(iter(imp))
    s = imp[name].head(n)
    fig, ax = plt.subplots(figsize=(9, 6))
    s.plot(kind="barh", ax=ax, color="steelblue")
    ax.invert_yaxis()  # highest at top
    ax.set_title(f"Top {n} feature importances — {name}", fontweight="bold")
    ax.set_xlabel("XGBoost feature importance (gain)")
    fig.tight_layout()
    return fig, ax


# Preet's EDA_and_modeling.ipynb REGRESSION baseline geometry
PREET_LAM = 10_000  # Preet's single exp-decay length (~10 km) for crops + surplus


def _preet_eda_reg_frame(site, lam: int = PREET_LAM):
    """Preet's EDA_and_modeling.ipynb REGRESSION feature set (the notebook preet_xgb_baseline
    reconstructs), rebuilt on the refactored data layer: exp-decay-weighted crops & surplus at a
    single ~10 km scale, basin precip + rolling-SUM antecedent rain (3/7/14/30d), raw calendar
    (month / day_of_year / week), and basin area -- target = daily-MEAN nitrate_con (as the notebook
    used). Deliberately EXCLUDES the full gridMET weather block and lat/lon: those are
    testing_stuff.ipynb (the CLASSIFIER) traits, not this regression notebook. (That classifier
    feature set is demo_recipes.preet_recipe.)"""
    kw = {"site_uid": site} if isinstance(site, str) else {"site_data": site}
    n = daily_nitrate(**kw, agg_meth="mean").rename("nitrate_con")  # EDA used daily-MEAN nitrate
    # precip ONLY (drop the rest of the gridMET block) + rolling-SUM antecedent rain
    wb = flatten_buckets(agg_weather(**kw, edges=(), exp=False))[["date", "precip_in_1d"]].copy()
    wb = wb.sort_values("date").reset_index(drop=True)
    for k in (3, 7, 14, 30):
        wb[f"rain_roll_{k}d"] = wb["precip_in_1d"].rolling(k, min_periods=1).sum()
    cb = flatten_buckets(agg_crops(**kw, edges=(), lam=lam, exp=True))  # exp-decay crops
    sb = flatten_buckets(agg_surplus(**kw, edges=(), lam=lam, exp=True))  # exp-decay surplus
    idx = n.index
    s = pd.Series(idx, index=idx)
    cal = pd.DataFrame(
        {"month": s.dt.month, "day_of_year": s.dt.dayofyear, "week": s.dt.isocalendar().week.astype(int)}
    )
    frame = merge_on_date([n, wb, cb, sb], spine=idx).join(cal)
    frame["log_basin_area"] = site_static(**kw).get("log_basin_area")  # basin area (monotone ~ EDA's basin_area)
    return frame


def preet_xgb_baseline(sites=None, n_splits: int = 5, lam: int = PREET_LAM):
    """Faithful reconstruction of Preet's cell-12 XGBoost REGRESSION baseline (EDA_and_modeling,
    "Model Selection: Establishing an XGBoost Baseline"). Pools EDA_and_modeling's own regression
    feature set (precip + rolling rain + exp-decay crops/surplus + calendar + basin area, via
    _preet_eda_reg_frame -- NOT the testing_stuff classifier features in demo_recipes.preet_recipe),
    runs a 5-fold GroupKFold spatial CV, and PRINTS -- literally, as Preet did -- per-fold Test RMSE
    / Test R² on the held-out unseen sites, then the global feature importances.

    The fold-to-fold R² spread (Preet saw ~0.40 down to ~0.09) is her "spatial generalization gap":
    a single global model transfers unevenly to unseen watersheds. This is the benchmark the
    bucketed + travel-time-lagged spatial recipe_REG had to beat, and the reason plain per-site
    linear/tree baselines (see demo_baselines.regression_model_comparison) were set aside for XGBoost.

    Returns the global feature-importances Series (taken from the last fold's model, as Preet did).
    NOTE: target is daily-MEAN nitrate (as EDA_and_modeling used), and Preet's exact hyperparameters
    (500 trees, lr=0.03, depth=6, subsample/colsample=0.8)."""
    import xgboost as xgb
    from sklearn.model_selection import GroupKFold
    from sklearn.metrics import mean_squared_error, r2_score

    def recipe(site):
        return _preet_eda_reg_frame(site, lam=lam)

    recipe.__name__ = "preet_eda_reg"
    pool = _pool(recipe, sites or get_site_ids(), "nitrate_con", progress_label="preet_eda_reg")
    feat = _features(pool, "nitrate_con")
    X, y, groups = pool[feat], pool["nitrate_con"], pool["site"]

    model = None
    for fold, (tr, te) in enumerate(GroupKFold(n_splits=n_splits).split(X, y, groups), 1):
        model = xgb.XGBRegressor(
            n_estimators=500,
            learning_rate=0.03,
            max_depth=6,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            objective="reg:squarederror",
        )
        model.fit(X.iloc[tr], y.iloc[tr], eval_set=[(X.iloc[te], y.iloc[te])], verbose=False)
        preds = model.predict(X.iloc[te])
        rmse = np.sqrt(mean_squared_error(y.iloc[te], preds))
        r2 = r2_score(y.iloc[te], preds)
        print(
            f"Fold {fold} (Testing on {groups.iloc[te].nunique()} unseen sites) -> Test RMSE: {rmse:.4f}, Test R²: {r2:.4f}"
        )

    importances = pd.Series(model.feature_importances_, index=feat).sort_values(ascending=False)
    print("\nGlobal Feature Importances:\n", importances.head(20).to_string())
    return importances


def plot_pr_curves(recipe, sites=None, groupby: str = "family", n_splits: int = 5, xgb=None):
    """Spatial-CV precision-recall curves (Preet's pr_curve figure): one PR curve per held-out fold
    (faint dashed) + the interpolated mean (firebrick) + the random-guess baseline. `groupby`:
    'family' = leave-one-basin-family-out (leakage-aware; the honest analog of Preet's leave-one-
    K-means-cluster-out) or 'site' = leave-one-site-out. Classification only."""
    from sklearn.metrics import precision_recall_curve, auc

    xgb = FAST_XGB if xgb is None else xgb
    target = _TARGET["clf"]
    pool = _pool(recipe, sites or get_site_ids(), target, progress_label=getattr(recipe, "__name__", "recipe"))
    feat = _features(pool, target)
    X, y = pool[feat], _target(pool, target, "clf")
    groups = basin_groups(pool["site"]) if groupby == "family" else pool["site"]

    fig, ax = plt.subplots(figsize=(9, 7))
    mean_recall = np.linspace(0, 1, 100)
    precisions, aps = [], []
    for i, (te, m) in enumerate(_grouped_models(X, y, groups, "clf", n_splits, **xgb), 1):
        yv = y.iloc[te].to_numpy()
        if len(np.unique(yv)) < 2:  # a fold with one class -> PR undefined, skip
            continue
        sc = m.predict_proba(X.iloc[te])[:, 1]
        p, r, _ = precision_recall_curve(yv, sc)
        ap = auc(r, p)
        aps.append(ap)
        ax.plot(r, p, alpha=0.3, ls="--", lw=1, label=f"fold {i} (AP={ap:.3f})")
        precisions.append(np.interp(mean_recall, r[::-1], p[::-1]))
    if precisions:
        ax.plot(
            mean_recall,
            np.mean(precisions, axis=0),
            color="firebrick",
            lw=2.5,
            label=f"mean (AP={np.mean(aps):.3f} ± {np.std(aps):.3f})",
        )
    base = float(y.mean())
    ax.axhline(base, color="gray", ls=":", lw=1.5, label=f"random guess ({base:.3f})")
    ax.set(
        xlim=(-0.02, 1.02),
        ylim=(-0.02, 1.02),
        xlabel="Recall",
        ylabel="Precision",
        title=f"Spatial-CV precision-recall — leave-one-{groupby}-out ({getattr(recipe, '__name__', 'recipe')})",
    )
    ax.legend(loc="lower left", fontsize=8)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    return fig, ax
