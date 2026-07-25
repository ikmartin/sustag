"""lean_cook.py -- streamlined cross-site CV for the feature-manifest add-one screen.

A stripped, single-purpose fork of src/eval/cook.py. It keeps ONLY the pooled cross-site path
(LOSO + LOFO decomposition, gain + permutation importance) and adds the screen's core
optimization: instead of re-pooling for every recipe (cook.py's compare_many rebuilds every site's
feature frame per recipe -- the dominant cost), build ONE wide pooled dataframe per task and score
many recipes by MASKING its columns.

    pool = _pool_wide(wide_recipe, sites, target)      # build once
    for name, cols in recipes.items():                 # each recipe = a column subset
        score = _score_pool(pool, cols, ...)           # no re-pooling

Because every recipe scores the SAME rows under the SAME folds, base-vs-candidate deltas are
perfectly paired -- CV variance cancels, so small deltas are trustworthy.

Dropped vs cook.py: cook_one / cook_fleet / compare_one / compare_fleet / fit_full and all the
save/load/importance-view helpers. The scoring/importance internals are copied verbatim so the
score dicts and importance Series match cook.py (and thus fulltrain_logs) exactly.
"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")
import os, sys, time
from typing import Any, Callable, Literal, Sequence, TypeAlias

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root/
import numpy as np, pandas as pd
import xgboost as xgb
from sklearn.model_selection import GroupKFold
from sklearn.metrics import (
    mean_squared_error,
    mean_absolute_error,
    r2_score,
    roc_auc_score,
    average_precision_score,
    precision_recall_curve,
    roc_curve,
    brier_score_loss,
)
from src.splits.conflict_graph import split_groups
from src.data.access import get_site_ids

# ── type aliases ───────────────────────────
Recipe: TypeAlias = Callable[[str], pd.DataFrame]  # site_uid | SiteData -> feature/target frame
Task: TypeAlias = Literal["reg", "clf"]
Model: TypeAlias = "xgb.XGBClassifier | xgb.XGBRegressor"

TARGET = "nitrate_con"
_STRUCTURAL = {"site_uid", "site", "date", "year", "datetime"}  # bookkeeping cols, never features

# Fast screening config: fewer trees / higher LR than the shipped models. Fixed across every run so
# the screen is a fair PAIRED comparison (absolute base numbers won't match the shipped models).
_DEFAULT_XGB = dict(
    n_estimators=1500,
    learning_rate=0.03,
    max_depth=3,
    min_child_weight=10,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=5.0,
    random_state=42,
    early_stopping_rounds=50,
)

_PERM_REPEATS = 5  # shuffles per feature per fold for permutation importance
_FAR_BUDGET = 0.10  # false-alarm-rate budget for recall_at_far
_BASINGRPS = None  # lazy {site: conflict-graph component id}


# ── shared helpers (verbatim from cook.py) ─────────────────────────
def _check_target(recipe: Recipe, df: pd.DataFrame, target: str) -> None:
    name = getattr(recipe, "__name__", repr(recipe))
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"recipe {name!r} returned {type(df).__name__}, expected a DataFrame")
    if target not in df.columns:
        raise KeyError(f"recipe {name!r} produced columns {list(df.columns)} -- no target {target!r}")


def _target(df: pd.DataFrame, target: str, task: Task) -> pd.Series:
    y = df[target]
    return y.astype("int64") if task == "clf" else y


def _model(task: Task, **overrides: Any) -> Model:
    cfg = {**_DEFAULT_XGB, **overrides}
    if task == "clf":
        return xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", **cfg)
    return xgb.XGBRegressor(eval_metric="rmse", **cfg)


def _fit(task: Task, Xtr, ytr, Xval, yval, **xgb_kw: Any) -> Model:
    m = _model(task, **xgb_kw)
    m.fit(Xtr, ytr, eval_set=[(Xval, yval)], verbose=False)
    return m


def _oof_predict(model: Model, X: pd.DataFrame, task: Task) -> np.ndarray:
    return model.predict_proba(X)[:, 1] if task == "clf" else model.predict(X)


def _best_f1(y: np.ndarray, pred: np.ndarray) -> tuple[float, float]:
    if len(np.unique(y)) < 2:
        return float("nan"), float("nan")
    prec, rec, thr = precision_recall_curve(y, pred)
    f1 = np.divide(2 * prec * rec, prec + rec, out=np.zeros_like(prec), where=(prec + rec) > 0)
    i = int(np.nanargmax(f1))
    return float(f1[i]), float(thr[i]) if i < len(thr) else float("nan")


def _imbalance_suite(y, pred, far: float | None = None) -> dict[str, float]:
    far = _FAR_BUDGET if far is None else far
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)
    ok = ~(np.isnan(y) | np.isnan(pred))
    y, pred = y[ok], pred[ok]
    keys = ("prauc_lift", "f2", "mcc", "recall_at_far")
    if len(np.unique(y)) < 2:
        return {k: np.nan for k in keys}
    base = float(y.mean())
    prauc = average_precision_score(y, pred)
    prec, rec, _ = precision_recall_curve(y, pred)
    den_f2 = 4.0 * prec + rec
    f2 = np.divide(5.0 * prec * rec, den_f2, out=np.zeros_like(prec), where=den_f2 > 0)
    fpr, tpr, _ = roc_curve(y, pred)
    P = float(y.sum())
    N = float(len(y) - P)
    tp, fp = tpr * P, fpr * N
    fn, tn = P - tp, N - fp
    den_mcc = np.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    mcc = np.divide(tp * tn - fp * fn, den_mcc, out=np.zeros_like(den_mcc), where=den_mcc > 0)
    within = fpr <= far
    return dict(
        prauc_lift=float(prauc / base) if base > 0 else float("nan"),
        f2=float(np.nanmax(f2)),
        mcc=float(np.nanmax(mcc)),
        recall_at_far=float(tpr[within].max()) if within.any() else 0.0,
    )


def _score(y, pred, task: Task) -> dict[str, float]:
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)
    ok = ~(np.isnan(y) | np.isnan(pred))
    y, pred = y[ok], pred[ok]
    if len(y) == 0:
        keys = ("auc", "prauc", "f1", "f1_thresh", "brier", "base") if task == "clf" else ("rmse", "mae", "r2")
        return {k: np.nan for k in keys}
    if task == "clf":
        two = len(np.unique(y)) == 2
        f1, f1_thresh = _best_f1(y, pred)
        return dict(
            auc=roc_auc_score(y, pred) if two else np.nan,
            prauc=average_precision_score(y, pred) if two else np.nan,
            f1=f1,
            f1_thresh=f1_thresh,
            brier=brier_score_loss(y, pred),
            base=float(y.mean()),
        )
    return dict(
        rmse=float(np.sqrt(mean_squared_error(y, pred))),
        mae=float(mean_absolute_error(y, pred)),
        r2=float(r2_score(y, pred)),
    )


def _per_site_score(y, pred, task: Task) -> float:
    y = np.asarray(y)
    pred = np.asarray(pred)
    if task == "clf":
        return roc_auc_score(y, pred) if len(np.unique(y)) == 2 else np.nan
    return r2_score(y, pred) if (len(y) > 1 and np.std(y) > 0) else np.nan


def _persistence_pred(site: np.ndarray, date: np.ndarray, y: np.ndarray) -> np.ndarray:
    df = pd.DataFrame({"date": pd.to_datetime(np.asarray(date)), "y": np.asarray(y, dtype=float)})
    df["site"] = np.asarray(site)
    out = np.full(len(df), np.nan)
    for _, g in df.groupby("site", sort=False):
        g = g.sort_values("date")
        s = pd.Series(g["y"].to_numpy(), index=g["date"])
        out[g.index.to_numpy()] = s.asfreq("D").shift(1).reindex(g["date"]).to_numpy()
    return out


def _persistence_skill(y: np.ndarray, pred: np.ndarray, persist: np.ndarray) -> float:
    y, pred, persist = np.asarray(y, float), np.asarray(pred, float), np.asarray(persist, float)
    m = ~(np.isnan(y) | np.isnan(pred) | np.isnan(persist))
    if m.sum() == 0:
        return float("nan")
    mse_model = np.mean((y[m] - pred[m]) ** 2)
    mse_persist = np.mean((y[m] - persist[m]) ** 2)
    return float(1 - mse_model / mse_persist) if mse_persist > 0 else float("nan")


def _spearman(y: np.ndarray, pred: np.ndarray) -> float:
    s = pd.DataFrame({"y": np.asarray(y, float), "p": np.asarray(pred, float)}).dropna()
    return float(s["y"].corr(s["p"], method="spearman")) if len(s) >= 2 else float("nan")


def basin_groups(sites_mega_series: pd.Series) -> pd.Series:
    """Map each row's site_uid to its conflict-graph component id (the LOFO family grouping)."""
    global _BASINGRPS
    if _BASINGRPS is None:
        _BASINGRPS = split_groups()
    nxt = max(_BASINGRPS.values(), default=-1) + 1
    mapping = {}
    for s in pd.unique(sites_mega_series):
        mapping[s] = _BASINGRPS.get(s, nxt)
        nxt += s not in _BASINGRPS
    return sites_mega_series.map(mapping)


def _grouped_models(X, y, groups, task, n_splits, seed=0, **xgb_kw):
    """GroupKFold fold-models (rows with the same group held out together). Yields (test_idx, model);
    each fold early-stops on a random 15% slice of its train rows."""
    folds = min(n_splits, pd.Series(groups).nunique())
    if folds < 2:
        return  # <2 groups -> grouped CV undefined; caller gets all-NaN OOF
    rng = np.random.RandomState(seed)
    for train, test in GroupKFold(folds).split(X, y, groups=groups):
        v = rng.permutation(train)
        cut = int(len(v) * 0.85)
        m = _fit(task, X.iloc[v[:cut]], y.iloc[v[:cut]], X.iloc[v[cut:]], y.iloc[v[cut:]], **xgb_kw)
        yield test, m


def _grouped_oof(X, y, groups, task, n_splits, seed=0, **xgb_kw) -> np.ndarray:
    oof = np.full(len(y), np.nan)
    for te, m in _grouped_models(X, y, groups, task, n_splits, seed, **xgb_kw):
        oof[te] = _oof_predict(m, X.iloc[te], task)
    return oof


def _gain_importance(fold_models, feat: list[str]) -> pd.Series:
    """Mean XGBoost GAIN importance across fold-models, indexed by feature, sorted desc."""
    cols = [pd.Series(m.feature_importances_, index=feat) for m, _ in fold_models]
    return pd.concat(cols, axis=1).mean(axis=1).sort_values(ascending=False)


def _perm_importance(fold_models, X, y, feat: list[str], task: Task, cols=None, seed: int = 0) -> pd.Series:
    """Permutation importance for `cols` (default: all `feat`), averaged over folds. For each fold
    model, shuffle a column on that fold's HELD-OUT rows and measure the drop in score (R2 for reg,
    ROC-AUC for clf), _PERM_REPEATS times.

    Restricting `cols` to a recipe's ADDED columns is the screen's key saving: a full per-column perm
    over every feature is ~K * n_feat * _PERM_REPEATS rescorings, but for an add-one run we only care
    about the added feature's marginal perm, so we permute just those 1-3 columns. Same score-drop
    semantics as sklearn.inspection.permutation_importance, done by hand so the column set is
    restrictable.
    """
    cols = list(feat) if cols is None else [c for c in cols if c in feat]
    if not cols:
        return pd.Series(dtype=float)
    score_fn = r2_score if task == "reg" else roc_auc_score
    rng = np.random.RandomState(seed)
    fold_models = list(fold_models)
    n = len(fold_models)
    acc = {c: [] for c in cols}
    for i, (m, te) in enumerate(fold_models, 1):
        print(f"\r    perm fold {i}/{n} ({len(cols)} cols x {_PERM_REPEATS})", end="", flush=True)
        Xte = X.iloc[te].copy()
        yte = y.iloc[te]
        try:
            base = score_fn(yte, _oof_predict(m, Xte, task))
        except ValueError:
            continue  # fold unscoreable (e.g. single-class clf window) -> skip
        for c in cols:
            orig = Xte[c].to_numpy().copy()
            drops = []
            for _ in range(_PERM_REPEATS):
                Xte[c] = rng.permutation(orig)
                try:
                    drops.append(base - score_fn(yte, _oof_predict(m, Xte, task)))
                except ValueError:
                    pass
            Xte[c] = orig  # restore before the next column
            if drops:
                acc[c].append(float(np.mean(drops)))
    print(flush=True)
    return pd.Series({c: (float(np.mean(v)) if v else np.nan) for c, v in acc.items()}).sort_values(ascending=False)


def _cross_metrics(pool, y, oof_site, oof_family, task: Task) -> dict[str, float]:
    site = pool["site"].to_numpy()
    tab = pd.DataFrame({"y": y, "p": oof_site, "g": site})
    site_means = tab.groupby("g")[["y", "p"]].mean()
    per_site = tab.groupby("g").apply(lambda d: _per_site_score(d.y, d.p, task))
    overall = _score(y, oof_site, task)  # LOSO (optimistic: nested basins leak)
    lofo = _score(y, oof_family, task)  # LOFO (the honest generalization number)
    persist = _persistence_pred(site, pool["date"].to_numpy(), y)
    persist_skill = _persistence_skill(y, oof_site, persist)

    if task == "clf":
        loso_imb = _imbalance_suite(y, oof_site)
        lofo_imb = _imbalance_suite(y, oof_family)
        return dict(
            loso_auc=overall["auc"],
            lofo_auc=lofo["auc"],
            loso_prauc=overall["prauc"],
            lofo_prauc=lofo["prauc"],
            loso_f1=overall["f1"],
            lofo_f1=lofo["f1"],
            **{f"loso_{k}": v for k, v in loso_imb.items()},
            **{f"lofo_{k}": v for k, v in lofo_imb.items()},
            brier=overall["brier"],
            persist_skill=persist_skill,
            base=overall["base"],
            between_rate_r2=r2_score(site_means.y, site_means.p),
            macro_auc=float(np.nanmedian(per_site)),
        )
    sm = tab.groupby("g")["y"].transform("mean").to_numpy()
    pm = tab.groupby("g")["p"].transform("mean").to_numpy()
    return dict(
        loso_r2=overall["r2"],
        lofo_r2=lofo["r2"],
        rmse=overall["rmse"],
        persist_skill=persist_skill,
        spearman=_spearman(y, oof_site),
        between_r2=r2_score(site_means.y, site_means.p),
        within_r2=r2_score(y - sm, oof_site - pm),
        macro_r2=float(np.nanmedian(per_site)),
    )


# ── pooling + scoring: the shared-pool optimization ───────────────
def _pool_wide(
    wide_recipe: Recipe, sites: Sequence[str], target: str, min_rows: int = 500, progress_label: str | None = None
) -> pd.DataFrame:
    """Build the wide pooled master frame ONCE: stack every site's wide_recipe frame (all candidate
    columns + target), tagged with `site`. Same logic as cook.py's _pool -- built once, reused by
    every recipe in compare_many via column masking."""
    sites = list(sites)
    total = len(sites)
    frames, skipped = [], []
    for i, s in enumerate(sites, 1):
        if progress_label is not None:
            print(f"\r  pooling {progress_label}: site {i}/{total} ({len(frames)} usable)", end="", flush=True)
        try:
            d = wide_recipe(s)
        except Exception as e:
            skipped.append((s, f"{type(e).__name__}: {e}"))
            continue
        _check_target(wide_recipe, d, target)
        d = d.dropna(subset=[target])
        if len(d) < min_rows:
            skipped.append((s, f"only {len(d)} usable rows (< {min_rows})"))
            continue
        d = d.copy()
        d["site"] = s
        frames.append(d)
    if progress_label is not None:
        print(f"\r  pooled {progress_label}: {len(frames)}/{total} sites usable (min_rows >= {min_rows})" + " " * 20, flush=True)
        for s, reason in skipped:
            print(f"    [skipped] {s}: {reason}")
    if not frames:
        from collections import Counter

        reasons = "; ".join(f"{n}x {r}" for r, n in Counter(r for _, r in skipped).most_common(3))
        raise ValueError(f"no sites produced a usable frame ({len(skipped)} skipped) -- {reasons}")
    return pd.concat(frames, ignore_index=True)


def _score_pool(
    pool: pd.DataFrame,
    feat: list[str],
    target_col: str,
    task: Task,
    n_splits: int = 5,
    extra_importance_test: bool = True,
    perm_cols=None,
    **xgb_kw: Any,
) -> dict:
    """Score a recipe = an explicit feature-column list against an ALREADY-POOLED frame (cook_many's
    body minus pooling). Runs LOSO fold-models + LOFO OOF, returns the decomposition metrics, gain
    importance (always, over ALL feat -- it's free), and permutation importance. Because `pool` and
    the folds are shared across recipes, all comparisons are paired on identical rows.

    `perm_cols` scopes the permutation test: None -> every column (the full-feature-set run);
    a list -> only those columns (an add-one run permutes just its added columns); [] -> skip perm
    (the base run has no added feature)."""
    X, y = pool[feat], _target(pool, target_col, task)
    oof_site = np.full(len(y), np.nan)
    fold_models = []
    for te, m in _grouped_models(X, y, pool["site"], task, n_splits, **xgb_kw):
        oof_site[te] = _oof_predict(m, X.iloc[te], task)
        fold_models.append((m, te))

    basin_grp = basin_groups(pool["site"])
    n_families = int(pd.Series(basin_grp).nunique())
    oof_family = _grouped_oof(X, y, basin_grp, task, n_splits, **xgb_kw)
    out = dict(
        n_sites=pool["site"].nunique(),
        n_families=n_families,
        n_rows=len(pool),
        n_feat=len(feat),
        **_cross_metrics(pool, np.asarray(y), oof_site, oof_family, task),
        importance=_gain_importance(fold_models, feat),
    )
    if extra_importance_test:
        cols = feat if perm_cols is None else perm_cols
        out["importance_perm"] = _perm_importance(fold_models, X, y, feat, task, cols=cols)
    return out


def cook_many(
    wide_recipe: Recipe,
    sites: Sequence[str] | None = None,
    target_col: str = TARGET,
    task: Task = "reg",
    n_splits: int = 5,
    extra_importance_test: bool = True,
    progress: bool | str = False,
    min_rows: int = 500,
    **xgb_kw: Any,
) -> dict:
    """Single-recipe pooled CV: pool once, score all its non-structural columns. (compare_many is the
    workhorse for the screen; this is the single-recipe convenience.)"""
    if sites is None:
        sites = get_site_ids()
    label = (progress if isinstance(progress, str) else getattr(wide_recipe, "__name__", "recipe")) if progress else None
    pool = _pool_wide(wide_recipe, sites, target_col, min_rows=min_rows, progress_label=label)
    feat = [c for c in pool.columns if c != target_col and c not in _STRUCTURAL]
    return _score_pool(pool, feat, target_col, task, n_splits, extra_importance_test, **xgb_kw)


def compare_many(
    wide_recipe: Recipe,
    recipes: dict[str, set],
    sites: Sequence[str] | None = None,
    target_col: str = TARGET,
    task: Task = "reg",
    n_splits: int = 5,
    extra_importance_test: bool = True,
    progress: bool = True,
    min_rows: int = 500,
    base_cols=None,
    full_names=(),
    pool: pd.DataFrame | None = None,
    **xgb_kw: Any,
) -> pd.DataFrame:
    """Score many recipes over ONE shared wide pool. `wide_recipe` emits every candidate column;
    `recipes` maps a recipe name to the set of columns it uses. The pool is built once, then each
    recipe is scored on `pool[cols]` -- no re-pooling. Same return contract as cook.py's compare_many
    (metric table indexed by recipe; importances in .attrs["importance"]/["importance_perm"]).

    Pass a prebuilt `pool` (from `_pool_wide`) to reuse it across calls -- e.g. the add-one and
    drop-one recipe-sets for one task share a single pool. If None, it is built here from
    `wide_recipe` + `sites`.

    Permutation scope per recipe (when `base_cols` is given): the run named in `full_names` permutes
    EVERY column (the full-feature-set reference); other recipes permute only the columns they add
    over `base_cols` (so an add-one run perms just its added feature, and the base run perms nothing).
    Without `base_cols`, every recipe permutes all its columns (cook.py's behaviour).
    """
    if pool is None:
        if sites is None:
            sites = get_site_ids()
        pool = _pool_wide(wide_recipe, sites, target_col, min_rows=min_rows, progress_label=("wide" if progress else None))

    base_set = None if base_cols is None else set(base_cols)
    full_names = set(full_names)
    rows, imps, perms, n, t0 = [], {}, {}, len(recipes), time.time()
    for i, (name, cols) in enumerate(recipes.items(), 1):
        feat = [c for c in cols if c in pool.columns]  # present-only mask (over-expansion is harmless)
        if base_set is None:
            perm_cols = None
        elif name in full_names:
            perm_cols = None  # full-feature-set run: permute every column
        else:
            perm_cols = [c for c in feat if c not in base_set]  # added columns only ([] for base)
        if progress:
            scope = "all" if perm_cols is None else str(len(perm_cols))
            print(f"compare_many: [{i}/{n}] {name:<34.34s} {len(feat):>3d} feat  perm {scope:>3}  elapsed {time.time() - t0:4.0f}s", flush=True)
        r = _score_pool(pool, feat, target_col, task, n_splits, extra_importance_test, perm_cols=perm_cols, **xgb_kw)
        imps[name] = r.pop("importance")
        if "importance_perm" in r:
            perms[name] = r.pop("importance_perm")
        rows.append({"recipe": name, **r})
    if progress:
        print(f"compare_many: done {n}/{n} recipes in {time.time() - t0:.0f}s")
    out = pd.DataFrame(rows).set_index("recipe")
    out.attrs["importance"] = imps
    if perms:
        out.attrs["importance_perm"] = perms
    return out
