"""
cook.py -- reusable harness for comparing nitrate "recipes".
============================================================

A *recipe* is a function  site_uid -> DataFrame  containing a target column, a `date`
column, and feature columns (what recipe_A/B/C produce). Recipes stay pure
(site -> frame) and know nothing about CV; this module supplies the evaluation.

Tell cook which column is the target by passing `target_col=`. Default "nitrate_con".
Tell cook which task (regression or classification) by passing `task=`, either "reg" or "clf". Default "reg".

Two evaluation modes, differing only in their LEAKAGE AXIS:

  cook_one(recipe, site)     INDIVIDUAL site modelling. Leakage axis = TIME.
  cook_many(recipe, sites)   CROSS-SITE modelling. Leakage axis = SPACE and TIME.
  cook_fleet(recipe, sites)  bonus: run cook_one on each site, summarise the distribution.

Cross-site performance is reported as a DECOMPOSITION rather than one number:
  between_r2  predicted vs actual per-site means -- does it rank site levels?
  within_r2   after removing each site's mean    -- does it track daily movement?
  macro_r2    median per-site R2 (equal weight per site, vs row-weighted overall)
  loso_r2 / lofo_r2  the leakage bracket (lofo <= real generalisation <= loso)

Two different holdout strategies:
    LOSO = Leave One Site Out.
    LOFO = Leave One Family Out.
"""

# this makes all type annotations lazy, so no runtime cost for annotations
from __future__ import annotations

# control over runtime warning messages
import warnings

warnings.filterwarnings("ignore")
import os, sys, time, json
from typing import Any, Callable, Literal, Sequence, TypeAlias

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root/
import numpy as np, pandas as pd
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit, GroupKFold
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
from sklearn.inspection import permutation_importance

from src.splits.conflict_graph import split_groups
from src.data.access import get_site_ids

# ── type aliases ───────────────────────────
Recipe: TypeAlias = Callable[[str], pd.DataFrame]  # a recipe: site_uid -> feature/target frame
Task: TypeAlias = Literal["reg", "clf"]  # regression vs binary classification
Model: TypeAlias = "xgb.XGBClassifier | xgb.XGBRegressor"  # fitted estimator

TARGET = "nitrate_con"  # default target column if the caller doesn't pass target_col=
_STRUCTURAL = {"site_uid", "site", "date", "year", "datetime"}  # bookkeeping cols, never features

# fixed, somewhat regularized model config
_DEFAULT_XGB = dict(
    n_estimators=3000,
    learning_rate=0.02,
    max_depth=3,
    min_child_weight=10,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=5.0,
    random_state=42,
    early_stopping_rounds=50,
)

# fast configuration
# intended use: cook_many(sites, **FAST_XGB)
FAST_XGB = dict(n_estimators=800, learning_rate=0.05, max_depth=4)

# lazy {site : conflict-graph component id} loader
# maps a site to its group in the conflict-graph from data.splits
_BASINGRPS = None


# ── shared helpers ─────────────────────────
def _check_target(recipe: Recipe, df: pd.DataFrame, target: str) -> None:
    """Fail quickly if a recipe didn't actually produce the requested target.
    One more way to avoid spending an hour on training for a crash at the end
    """
    name = getattr(recipe, "__name__", repr(recipe))
    if not isinstance(df, pd.DataFrame):
        raise TypeError(
            f"recipe {name!r} returned {type(df).__name__}, expected a DataFrame -- did you "
            f"forget to merge_on_date(...) the parts into one frame (e.g. return a (Series, parts) "
            f"tuple by mistake)?"
        )
    if target not in df.columns:
        raise KeyError(
            f"recipe {name!r} produced columns {list(df.columns)} -- no target {target!r}; "
            f"pass the right target_col= to cook_one/cook_many."
        )


def _features(df: pd.DataFrame, target: str) -> list[str]:
    """Fetches feature columns: everything except the target and the bookkeeping columns."""
    return [c for c in df.columns if c != target and c not in _STRUCTURAL]


def _target(df: pd.DataFrame, target: str, task: Task) -> pd.Series:
    """Read the target column off the recipe's output frame (int-cast for clf)."""
    y = df[target]
    return y.astype("int64") if task == "clf" else y


def _model(task: Task, **overrides: Any) -> Model:
    """Wrapper for returning the appropriate model, Classifier vs Regressor
    Pass a different model configuration, for instance, as an unwrapped dictionary.
    It will override the defaults.
    """
    cfg = {**_DEFAULT_XGB, **overrides}
    if task == "clf":
        return xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", **cfg)
    return xgb.XGBRegressor(eval_metric="rmse", **cfg)


def _fit(
    task: Task,
    Xtr: pd.DataFrame,
    ytr: pd.Series,
    Xval: pd.DataFrame,
    yval: pd.Series,
    **xgb_kw: Any,
) -> Model:
    """Fitter helper. Basically exists only so extra model keywords can be handed off"""
    m = _model(task, **xgb_kw)
    m.fit(Xtr, ytr, eval_set=[(Xval, yval)], verbose=False)
    return m


def _oof_predict(model: Model, X: pd.DataFrame, task: Task) -> np.ndarray:
    """Just an 'Out Of Fold' prediction wrapper. Used soley to differentiate between
    The two primary tasks.
    """
    return model.predict_proba(X)[:, 1] if task == "clf" else model.predict(X)


def _best_f1(y: np.ndarray, pred: np.ndarray) -> tuple[float, float]:
    """Best achievable F1 over the probability-threshold sweep, with the threshold that hits it.

    The 0/1 violation target is rare, so a fixed 0.5 cutoff on a calibrated P(violation) scores F1~=0 and tells you nothing; the max-F1 operating point is the honest 'how good is the exceedance *decision* if you pick a sensible cutoff'. The threshold is tuned on these same OOF rows, so read it as mildly optimistic and always alongside prauc (which is threshold-free).
    """
    if len(np.unique(y)) < 2:
        return float("nan"), float("nan")
    prec, rec, thr = precision_recall_curve(y, pred)
    f1 = np.divide(2 * prec * rec, prec + rec, out=np.zeros_like(prec), where=(prec + rec) > 0)
    i = int(np.nanargmax(f1))
    # precision_recall_curve returns len(thr) == len(prec) - 1; the last point (recall 0) has no cutoff
    return float(f1[i]), float(thr[i]) if i < len(thr) else float("nan")


_FAR_BUDGET = 0.10  # false-alarm-rate budget for recall_at_far


def _imbalance_suite(
    y: np.ndarray | pd.Series, pred: np.ndarray | pd.Series, far: float | None = None
) -> dict[str, float]:
    """Class-imbalance-robust metrics for a set of (y, P(positive))    threshold-free prauc_lift, which never cherry-picks an operating point.

    prauc_lift    average precision / base rate -- >1 beats a random ranker; imbalance-normalised
    f2            best F2 (recall weighted 2x precision)
    mcc           best Matthews correlation
    recall_at_far recall achievable at a false-alarm rate (FPR) <= `far`

    `far` defaults to the module-level `_FAR_BUDGET`, resolved at CALL time so a runtime override
    (e.g. train.py's --false-alarm-rate reassigning cook._FAR_BUDGET) is honoured.
    """
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
    den_f2 = 4.0 * prec + rec  # F_beta with beta=2 -> (1+4)PR / (4P+R)
    f2 = np.divide(5.0 * prec * rec, den_f2, out=np.zeros_like(prec), where=den_f2 > 0)

    fpr, tpr, _ = roc_curve(y, pred)
    P = float(y.sum())
    N = float(len(y) - P)
    tp, fp = tpr * P, fpr * N
    fn, tn = P - tp, N - fp
    den_mcc = np.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    mcc = np.divide(tp * tn - fp * fn, den_mcc, out=np.zeros_like(den_mcc), where=den_mcc > 0)
    within = fpr <= far  # operating points inside the false-alarm budget

    return dict(
        prauc_lift=float(prauc / base) if base > 0 else float("nan"),
        f2=float(np.nanmax(f2)),
        mcc=float(np.nanmax(mcc)),
        recall_at_far=float(tpr[within].max()) if within.any() else 0.0,
    )


def _score(y: np.ndarray | pd.Series, pred: np.ndarray | pd.Series, task: Task) -> dict[str, float]:
    """Pooled metrics for a set of (y, prediction). NaN predictions are dropped (e.g. an
    all-NaN OOF from a grouped split with too few groups -> all metrics NaN)."""
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)
    ok = ~(np.isnan(y) | np.isnan(pred))
    y, pred = y[ok], pred[ok]
    if len(y) == 0:  # nothing to score (e.g. LOFO with a single basin group)
        keys = ("auc", "prauc", "f1", "f1_thresh", "brier", "base") if task == "clf" else ("rmse", "mae", "r2")
        return {k: np.nan for k in keys}
    if task == "clf":
        two = len(np.unique(y)) == 2
        f1, f1_thresh = _best_f1(y, pred)
        return dict(
            auc=roc_auc_score(y, pred) if two else np.nan,
            prauc=average_precision_score(y, pred) if two else np.nan,
            f1=f1,  # best-F1 over the threshold sweep (exceedance-decision quality)
            f1_thresh=f1_thresh,  # the P(violation) cutoff that achieves it
            brier=brier_score_loss(y, pred),
            base=float(y.mean()),
        )
    return dict(
        rmse=float(np.sqrt(mean_squared_error(y, pred))),
        mae=float(mean_absolute_error(y, pred)),
        r2=float(r2_score(y, pred)),
    )


def _per_site_score(y: np.ndarray | pd.Series, pred: np.ndarray | pd.Series, task: Task) -> float:
    """One-site score, or NaN when undefined (one class / no variance)."""
    y = np.asarray(y)
    pred = np.asarray(pred)
    if task == "clf":
        return roc_auc_score(y, pred) if len(np.unique(y)) == 2 else np.nan
    return r2_score(y, pred) if (len(y) > 1 and np.std(y) > 0) else np.nan


def _persistence_pred(site: np.ndarray, date: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-site 'predict yesterday's target', aligned to (site, date, y) on a daily grid so the
    prediction at t is that site's value at t-1 (NaN where the prior calendar day is absent). The
    reference baseline -- it 'cheats' by using the site's own history, but it's the standard strong
    baseline to score against, and normalising by it is comparable across target definitions."""
    df = pd.DataFrame({"date": pd.to_datetime(np.asarray(date)), "y": np.asarray(y, dtype=float)})
    df["site"] = np.asarray(site)
    out = np.full(len(df), np.nan)
    for _, g in df.groupby("site", sort=False):
        g = g.sort_values("date")
        s = pd.Series(g["y"].to_numpy(), index=g["date"])
        out[g.index.to_numpy()] = s.asfreq("D").shift(1).reindex(g["date"]).to_numpy()
    return out


def _persistence_skill(y: np.ndarray, pred: np.ndarray, persist: np.ndarray) -> float:
    """1 - MSE(model)/MSE(persistence) over rows where all three are defined (squared error for
    both tasks -> a Brier skill score for the 0/1 clf target). >0 beats 'predict yesterday', 0
    ties it, <0 is worse. Because it normalises by each target's OWN persistence difficulty, it is
    comparable ACROSS different target definitions, unlike raw R2 / RMSE / AUC."""
    y, pred, persist = np.asarray(y, float), np.asarray(pred, float), np.asarray(persist, float)
    m = ~(np.isnan(y) | np.isnan(pred) | np.isnan(persist))
    if m.sum() == 0:
        return float("nan")
    mse_model = np.mean((y[m] - pred[m]) ** 2)
    mse_persist = np.mean((y[m] - persist[m]) ** 2)
    return float(1 - mse_model / mse_persist) if mse_persist > 0 else float("nan")


def _spearman(y: np.ndarray, pred: np.ndarray) -> float:
    """Pooled Spearman rank correlation of prediction vs actual -- scale-free, so comparable
    across targets (a rolling-max target and a daily-max target are ranked on the same footing).
    NaN if fewer than 2 paired non-NaN rows."""
    s = pd.DataFrame({"y": np.asarray(y, float), "p": np.asarray(pred, float)}).dropna()
    return float(s["y"].corr(s["p"], method="spearman")) if len(s) >= 2 else float("nan")


def basin_groups(sites_mega_series: pd.Series) -> pd.Series:
    """Take in the mega multi-site dataframe but only the sites_uid column

    Return a mapping from index of sites_mega_series to conflict graph id. Example with fake conflict_graph_id numbers:
    site_mega_series         output pd.Series
    index site_uid        index conflict_graph_id
    0     WQS0039         0            4
    1     WQS0039    ->   1            4
    2     WQS0115         2            7
    3     WQS0003         3            2

    """

    # lazily load the basin conflict graph
    global _BASINGRPS
    if _BASINGRPS is None:
        _BASINGRPS = split_groups()

    # this is the max integer not already a
    nxt = max(_BASINGRPS.values(), default=-1) + 1

    # build a mapping from site_uid to conflict graph conn comp id
    mapping = {}
    for s in pd.unique(sites_mega_series):
        # if site s is in the basin graph, use its component id
        mapping[s] = _BASINGRPS.get(s, nxt)
        # otherwise, add 1 to the counter
        nxt += s not in _BASINGRPS
    return sites_mega_series.map(mapping)


# ── INDIVIDUAL site: chronological CV ──────────────
def cook_one(
    recipe: Recipe,
    site_uid: str,
    target_col: str,
    task: Task = "reg",
    n_splits: int = 5,
    test_size: int = 90,
    extra_importance_test: bool = False,
    **xgb_kw: Any,
) -> dict:
    """Hand off a recipe, a single site_uid, a target_col, and a task ('reg' or 'clf' are the only options) and then do CV on the one site.

    `target`/`task` say which output column is the label and how to score it. Pools out-of-fold predictions across folds and scores once (stable, unlike averaging noisy per-fold R2); each fold early-stops on the chronological tail of its train set. Regression also reports the persistence (predict-yesterday) baseline RMSE.

    Always returns "importance", free with XGBoost run.
    extra_importance_test=True it ALSO returns "importance_perm" (permutation importance on the held-out rows -- this is slow).
    """
    df = recipe(site_uid)
    _check_target(recipe, df, target_col)
    df = df.dropna(subset=[target_col]).reset_index(drop=True)
    feat = _features(df, target_col)
    X, y = df[feat], _target(df, target_col, task)

    # storage for the results
    oof = np.full(len(df), np.nan)

    # store (model, test_idx) per fold -> feeds gain + permutation importance
    fold_models = []

    for train, test in TimeSeriesSplit(n_splits=n_splits, test_size=test_size).split(X):
        cut = int(len(train) * 0.85)  # chronological val tail -> early stop
        # fit on the first 85% of the train split, eval on the last 15%
        m = _fit(task, X.iloc[train[:cut]], y.iloc[train[:cut]], X.iloc[train[cut:]], y.iloc[train[cut:]], **xgb_kw)

        # store the results of the prediction
        oof[test] = _oof_predict(m, X.iloc[test], task)
        fold_models.append((m, test))

    ok = ~np.isnan(oof)
    row = dict(site=site_uid, n_test=int(ok.sum()), n_feat=len(feat), **_score(y[ok], oof[ok], task))
    if task == "reg":
        row["persist_rmse"] = _persistence_rmse(df, ok, target_col)
    row["importance"] = _gain_importance(fold_models, feat)  # always (free)
    if extra_importance_test:
        row["importance_perm"] = _perm_importance(fold_models, X, y, feat, task)
    return row


def _persistence_rmse(df: pd.DataFrame, ok: np.ndarray, target: str) -> float:
    """The persistence baseline, "predict yesterday's value", for a site does very well. This is the RMSE of that baseline."""
    s = pd.Series(df[target].to_numpy(), index=pd.to_datetime(df["date"])).asfreq("D")
    yhat = s.shift(1).reindex(pd.to_datetime(df["date"])).to_numpy()
    y = df[target].to_numpy()
    m = ok & ~np.isnan(yhat)
    return float(np.sqrt(mean_squared_error(y[m], yhat[m]))) if m.any() else np.nan


# ── CROSS-SITE: pooled, basi/n-grouped CV ─────────
def _pool(
    recipe: Recipe, sites: Sequence[str], target: str, min_rows: int = 500, progress_label: str | None = None
) -> pd.DataFrame:
    """Build the mega dataframe. Stack many recipe frames into one long dataframe, tagged with `site`.

    Use `sites` to pass an explicit site list (REQUIRED: but if used via cook_many or compare_many, it'll pass all valid sites.)
    Use `target` to specify the target column in the dataframe (REQUIRED)
    Use `min_rows` to specify the minimum number of non-nan rows a site must have to make it into the dataframe

    If `progress_label` is given, a self-overwriting status line reports cooking progress (sites cooked so far / total, and how many produced a usable frame).
    """
    sites = list(sites)
    total = len(sites)
    frames, skipped = [], []
    for i, s in enumerate(sites, 1):
        if progress_label is not None:
            print(
                f"\r  pooling {progress_label}: site {i}/{total} ({len(frames)} usable so far)",
                end="",
                flush=True,
            )
        try:
            d = recipe(s)
        except Exception as e:
            skipped.append((s, f"{type(e).__name__}: {e}"))  # build failure; reported in the end-summary
            continue

        _check_target(recipe, d, target)  # a missing target is a bug -> raise, never skip
        d = d.dropna(subset=[target])  # drop any rows mising the target
        if len(d) < min_rows:
            skipped.append((s, f"only {len(d)} usable rows (< {min_rows})"))
            continue
        d = d.copy()
        d["site"] = s
        frames.append(d)
    if progress_label is not None:
        # overwrite the last heartbeat with the accurate completed total (trailing spaces
        # clear leftovers from the longer in-progress line), then newline to keep it
        print(
            f"\r  cooked {progress_label}: {len(frames)}/{total} sites usable (min_rows >= {min_rows})" + " " * 20,
            flush=True,
        )
        # list dropped sites AFTER the heartbeat line -- an inline print mid-loop gets clobbered by
        # the next \r heartbeat, so both build failures AND the <min_rows drops were invisible.
        for s, reason in skipped:
            print(f"    [skipped] {s}: {reason}")
    if not frames:
        from collections import Counter

        # if there aren't ANY frames, builds a human-readable error messages explaining
        # (hopefully) why the sites got skipped.
        reasons = "; ".join(f"{n}x {r}" for r, n in Counter(r for _, r in skipped).most_common(3))
        raise ValueError(f"no sites produced a usable frame ({len(skipped)} sites skipped) -- {reasons}")
    return pd.concat(frames, ignore_index=True)


def _grouped_models(X, y, groups, task, n_splits, seed=0, **xgb_kw):
    """This fits a model using a GroupKFold split. "Rows with the same group class must be kept together."

    The two main kinds of group identities:
        a. groups = site_uid col of X: then one SITE is left out, LOSO
        b. groups = conflict graph id: then one FAMILY of basins is left out, LOFO

    For each train/test split in GroupKFold:
        1. Permute the training rows
        If this didn't happen, we would always evaluate on the last temporal chunk of the last site. We want evaluation spread out between sites. XGBoost doesn't need contiguous time series, it just sees a bag of rows, so this is fine.
        2. Store the cut so we have an eval piece, hardcoded to 85/15% split
        3. First the model on this split
        4. Return the test indices and the model

    The fold/split logic lives here so both OOF prediction and feature importance consume the SAME models -- no duplicated splitting, and importance is free (the models already exist). Each fold early-stops on a random 15% slice of its TRAIN rows.
    """
    folds = min(n_splits, pd.Series(groups).nunique())
    if folds < 2:
        return  # <2 groups -> grouped CV undefined (e.g. LOFO with one basin); caller gets all-NaN OOF
    rng = np.random.RandomState(seed)
    for train, test in GroupKFold(folds).split(X, y, groups=groups):
        # XGBoost looks at a
        v = rng.permutation(train)
        cut = int(len(v) * 0.85)
        m = _fit(task, X.iloc[v[:cut]], y.iloc[v[:cut]], X.iloc[v[cut:]], y.iloc[v[cut:]], **xgb_kw)
        yield test, m


def _grouped_oof(
    X: pd.DataFrame,
    y: pd.Series,
    groups: pd.Series,
    task: Task,
    n_splits: int,
    seed: int = 0,
    **xgb_kw: Any,
) -> np.ndarray:
    """OOF predictions under GroupKFold (groups held out intact). Just a wrapper around the _grouped_models above. This method only cares about the predictions of the model. In particular, it doesn't return any info on feature importance."""
    oof = np.full(len(y), np.nan)
    for te, m in _grouped_models(X, y, groups, task, n_splits, seed, **xgb_kw):
        oof[te] = _oof_predict(m, X.iloc[te], task)
    return oof


_PERM_REPEATS = 5  # shuffles per feature per fold for the permutation-importance test


def _gain_importance(fold_models, feat: list[str]) -> pd.Series:
    """Mean XGBoost GAIN importance across the fold-models, indexed by feature, sorted desc."""

    cols = [pd.Series(m.feature_importances_, index=feat) for m, _ in fold_models]
    return pd.concat(cols, axis=1).mean(axis=1).sort_values(ascending=False)


def _perm_importance(fold_models, X, y, feat: list[str], task: Task, seed: int = 0) -> pd.Series:
    """Mean PERMUTATION importance across folds, indexed by feature, sorted desc. Extra feature importance check, optional

    For each fold, shuffle each feature on that fold's HELD-OUT rows and measure the drop in score (R2 for reg, ROC-AUC for clf) -- honest, in metric units, but costs ~K * n_feat * _PERM_REPEATS rescorings.

    Folds whose held-out set can't be scored (e.g. a single-class clf window -> AUC undefined) are skipped; if none are scorable, returns NaNs.
    """
    scoring = "r2" if task == "reg" else "roc_auc"
    fold_models = list(fold_models)
    n = len(fold_models)
    cols = []
    for i, (m, te) in enumerate(fold_models, 1):
        # slow step (~K * n_feat * _PERM_REPEATS rescorings) -> self-overwriting status line
        print(
            f"\r  extra_importance_test: permutation fold {i}/{n} " f"({len(feat)} feats x {_PERM_REPEATS} shuffles)",
            end="",
            flush=True,
        )
        try:
            r = permutation_importance(
                m, X.iloc[te], y.iloc[te], scoring=scoring, n_repeats=_PERM_REPEATS, random_state=seed
            )
        except ValueError:
            continue
        cols.append(pd.Series(r.importances_mean, index=feat))
    print(flush=True)  # newline so the finished status line stays put (not erased)
    if not cols:
        return pd.Series(np.nan, index=feat)
    return pd.concat(cols, axis=1).mean(axis=1).sort_values(ascending=False)


def cook_many(
    recipe: Recipe,
    sites: Sequence[str] | None = None,
    target_col: str = TARGET,
    task: Task = "reg",
    n_splits: int = 5,
    extra_importance_test: bool = False,
    progress: bool | str = False,
    min_rows: int = 500,
    **xgb_kw: Any,
) -> dict:
    """Pooled cross-site CV with site- (LOSO) and family- (LOFO) grouped holdouts.

    `sites` defaults to all sites (get_site_ids); _pool silently drops any that fail to
    build or produce fewer than `min_rows` usable (non-NaN-target) rows. `target_col`/`task`
    say which output column is the label and how to score it. Returns the decomposition
    (between/within/macro) plus the LOSO/LOFO bracket. For classification the 'level' analogue
    is the per-site violation RATE.

    `min_rows` (default 500) is the min num of non-nan rows a site needs to make it into the training/testing, otherwise its ignored.

    Always returns "importance" (mean GAIN across the LOSO fold-models, free). With    extra_importance_test=True it ALSO returns "importance_perm" (permutation importance on the held-out rows -- this is slow).

    progress: show a self-overwriting per-site "cooking" status line while building the pool. True labels it with recipe.__name__; pass a string to use that label instead (compare_many passes the recipe's display name).
    """
    if sites is None:
        sites = get_site_ids()
    label = (progress if isinstance(progress, str) else getattr(recipe, "__name__", "recipe")) if progress else None
    pool = _pool(recipe, sites, target_col, min_rows=min_rows, progress_label=label)
    feat = _features(pool, target_col)
    X, y = pool[feat], _target(pool, target_col, task)
    # site (LOSO) folds: collect OOF predictions AND the fold-models (for importance)
    oof_site = np.full(len(y), np.nan)

    fold_models = []
    for te, m in _grouped_models(X, y, pool["site"], task, n_splits, **xgb_kw):
        oof_site[te] = _oof_predict(m, X.iloc[te], task)
        fold_models.append((m, te))

    basin_grp = basin_groups(pool["site"])
    n_families = pd.Series(basin_grp).nunique()
    if n_families < 2:  # leave-one-basin-out needs >=2 families to hold one out
        print(
            f"  [LOFO skipped] {n_families} basin family across these {pool['site'].nunique()} "
            f"sites (need >=2) -- lofo_* = NaN",
            file=sys.stderr,
        )
    oof_family = _grouped_oof(X, y, basin_grp, task, n_splits, **xgb_kw)
    out = dict(
        n_sites=pool["site"].nunique(),
        n_families=int(n_families),  # # of basin families = # of LOFO groups (low -> noisy LOFO)
        n_rows=len(pool),
        n_feat=len(feat),
        **_cross_metrics(pool, np.asarray(y), oof_site, oof_family, task),
        importance=_gain_importance(fold_models, feat),  # always (free)
    )

    # status messages
    n_total = pool["site"].nunique()
    test_per_fold = [pool["site"].iloc[te].nunique() for _, te in fold_models]
    train_per_fold = [n_total - t for t in test_per_fold]
    n_folds = len(fold_models)
    print(
        f"  cooked {label}: {n_total} sites, {n_folds}-fold LOSO "
        f"(~{int(np.mean(train_per_fold))} train / ~{int(np.mean(test_per_fold))} test per fold)"
    )
    if extra_importance_test:
        out["importance_perm"] = _perm_importance(fold_models, X, y, feat, task)
    return out


def _cross_metrics(
    pool: pd.DataFrame,
    y: np.ndarray,
    oof_site: np.ndarray,
    oof_family: np.ndarray,
    task: Task,
) -> dict[str, float]:
    site = pool["site"].to_numpy()
    tab = pd.DataFrame({"y": y, "p": oof_site, "g": site})
    site_means = tab.groupby("g")[["y", "p"]].mean()  # per-site level
    per_site = tab.groupby("g").apply(lambda d: _per_site_score(d.y, d.p, task))
    overall = _score(y, oof_site, task)  # LOSO (optimistic: nested basins leak)
    lofo = _score(y, oof_family, task)  # LOFO (the honest generalization number)
    # skill vs the persistence ("predict yesterday") baseline -- comparable across targets
    persist = _persistence_pred(site, pool["date"].to_numpy(), y)
    persist_skill = _persistence_skill(y, oof_site, persist)

    if task == "clf":
        # class-imbalance suite on the OOF, for the leaking (loso) and honest (lofo) holdouts
        loso_imb = _imbalance_suite(y, oof_site)
        lofo_imb = _imbalance_suite(y, oof_family)
        return dict(
            loso_auc=overall["auc"],
            lofo_auc=lofo["auc"],
            loso_prauc=overall["prauc"],
            lofo_prauc=lofo["prauc"],  # base-rate-aware, honest generalization -- the headline pair
            loso_f1=overall["f1"],
            lofo_f1=lofo["f1"],  # best-F1 exceedance-decision quality, family-held-out
            **{f"loso_{k}": v for k, v in loso_imb.items()},
            **{f"lofo_{k}": v for k, v in lofo_imb.items()},
            brier=overall["brier"],
            persist_skill=persist_skill,  # Brier skill vs predict-yesterday (>0 beats it); a
            # gauged-site validation metric -- N/A for a virtual site, which has no own history
            base=overall["base"],
            between_rate_r2=r2_score(site_means.y, site_means.p),  # ranks violation rates
            macro_auc=float(np.nanmedian(per_site)),
        )
    sm = tab.groupby("g")["y"].transform("mean").to_numpy()
    pm = tab.groupby("g")["p"].transform("mean").to_numpy()
    return dict(
        loso_r2=overall["r2"],
        lofo_r2=lofo["r2"],
        rmse=overall["rmse"],
        persist_skill=persist_skill,  # 1 - MSE/MSE(persistence); comparable across targets
        spearman=_spearman(y, oof_site),  # rank corr of pred vs actual; scale-free
        between_r2=r2_score(site_means.y, site_means.p),  # ranks site levels
        within_r2=r2_score(y - sm, oof_site - pm),  # daily, level removed
        macro_r2=float(np.nanmedian(per_site)),
    )


def cook_fleet(recipe: Recipe, sites: Sequence[str] | None = None, progress: bool | str = False, **kw: Any) -> dict:
    """Run cook_one on each site (per-site models) and summarise the distribution.

    `sites` defaults to all sites (get_site_ids); sites that fail are skipped. `target_col=`
    / `task=` (and any model kwargs) are forwarded to cook_one. progress: show a
    self-overwriting per-site status line (True -> recipe.__name__, or pass a label)."""
    if sites is None:
        sites = get_site_ids()
    sites = list(sites)
    total = len(sites)
    label = (progress if isinstance(progress, str) else getattr(recipe, "__name__", "recipe")) if progress else None
    rows, imps, perms = [], [], []
    for i, s in enumerate(sites, 1):
        if label is not None:
            print(f"\r  fleet {label}: {i}/{total} sites ({len(rows)} fit)", end="", flush=True)
        try:
            r = cook_one(recipe, s, **kw)
        except Exception:
            continue
        imps.append(r.pop("importance"))  # keep the per-site table numeric; aggregate below
        if "importance_perm" in r:
            perms.append(r.pop("importance_perm"))
        rows.append(r)
    if label is not None:
        print(flush=True)
    df = pd.DataFrame(rows)
    metric = "auc" if kw.get("task") == "clf" else "r2"
    out = dict(
        n_sites=len(df),
        **{f"median_{metric}": df[metric].median(), f"mean_{metric}": df[metric].mean()},
        per_site=df,
        importance=(
            pd.concat(imps, axis=1).mean(axis=1).sort_values(ascending=False) if imps else pd.Series(dtype=float)
        ),
    )
    if perms:
        out["importance_perm"] = pd.concat(perms, axis=1).mean(axis=1).sort_values(ascending=False)
    return out


# ── comparison (paired: same sites/folds for every recipe) ────
def compare_one(recipes: dict[str, Recipe], site: str, target_col: str, **kw: Any) -> pd.DataFrame:
    """Table of cook_one metrics, one row per named recipe (same site). target_col=/task=
    (and model kwargs) are forwarded to cook_one.

    Per-feature importances are kept OUT of the (scalar) metric table and stashed in
    result.attrs["importance"] = {recipe -> Series} (gain); with extra_importance_test=True
    also result.attrs["importance_perm"]. View with importance_table(result[, key=...]).
    """
    rows, imps, perms = [], {}, {}
    for name, fn in recipes.items():
        r = cook_one(fn, site, target_col, **kw)
        imps[name] = r.pop("importance")
        if "importance_perm" in r:
            perms[name] = r.pop("importance_perm")
        rows.append({"recipe": name, **r})
    out = pd.DataFrame(rows).set_index("recipe")
    out.attrs["importance"] = imps
    if perms:
        out.attrs["importance_perm"] = perms
    return out


def compare_many(
    recipes: dict[str, Recipe],
    sites: Sequence[str] | None = None,
    progress: bool = True,
    **kw: Any,
) -> pd.DataFrame:
    """Table of cook_many metrics, one row per named recipe (same sites + folds).

    `sites` defaults to all sites; resolved once here so every recipe sees the same set.
    target_col=/task=/min_rows= (and model kwargs) are forwarded to cook_many. With progress=True each
    recipe prints a header line (which recipe / elapsed) followed by cook_many's own
    self-overwriting per-site cooking line.
    """
    if sites is None:
        sites = get_site_ids()
    rows, imps, perms, n, t0 = [], {}, {}, len(recipes), time.time()
    for i, (name, fn) in enumerate(recipes.items(), 1):
        if progress:
            print(f"compare_many: [{i}/{n}] {name:<28.28s} elapsed {time.time() - t0:4.0f}s", flush=True)
        r = cook_many(fn, sites, progress=(name if progress else False), **kw)
        imps[name] = r.pop("importance")
        if "importance_perm" in r:
            perms[name] = r.pop("importance_perm")
        rows.append({"recipe": name, **r})
    if progress:
        print(f"compare_many: done {n}/{n} recipes in {time.time() - t0:.0f}s")
    out = pd.DataFrame(rows).set_index("recipe")
    out.attrs["importance"] = imps  # {recipe -> per-feature mean gain}; see importance_table()
    if perms:
        out.attrs["importance_perm"] = perms  # permutation importances (extra_importance_test=True)
    return out


def compare_fleet(
    recipes: dict[str, Recipe],
    sites: Sequence[str] | None = None,
    target_col: str = TARGET,
    progress: bool = True,
    **kw: Any,
) -> pd.DataFrame:
    """Table of cook_fleet metrics, one AGGREGATED row per recipe (NOT per site).

    For each recipe, cook_one is fit on every site independently (individual-site modelling,
    chronological CV) and the per-site scores are summarised to median/mean. So this answers
    "which feature set is best suited to learning an individual monitoring site?" -- the
    individual-site analogue of compare_many's cross-site (transfer) question. The metric is
    r2 for regression, auc for classification (task='clf').

    `sites` defaults to all sites. target_col/task (and model kwargs) forward to cook_one.
    The full per-site tables are kept OUT of the (aggregated) result and stashed in
    result.attrs["per_site"] = {recipe -> DataFrame}; importances in result.attrs["importance"]
    (and ["importance_perm"] with extra_importance_test=True), viewable via importance_table().
    """
    if sites is None:
        sites = get_site_ids()
    rows, persite, imps, perms, n, t0 = [], {}, {}, {}, len(recipes), time.time()
    for i, (name, fn) in enumerate(recipes.items(), 1):
        if progress:
            print(f"compare_fleet: [{i}/{n}] {name:<28.28s} elapsed {time.time() - t0:4.0f}s", flush=True)
        r = cook_fleet(fn, sites, target_col=target_col, progress=(name if progress else False), **kw)
        persite[name] = r.pop("per_site")  # per-site detail -> attrs; the table stays aggregated
        imps[name] = r.pop("importance")
        if "importance_perm" in r:
            perms[name] = r.pop("importance_perm")
        rows.append({"recipe": name, **r})
    if progress:
        print(f"compare_fleet: done {n}/{n} recipes in {time.time() - t0:.0f}s")
    out = pd.DataFrame(rows).set_index("recipe")
    out.attrs["per_site"] = persite  # {recipe -> per-site metrics DataFrame}
    out.attrs["importance"] = imps
    if perms:
        out.attrs["importance_perm"] = perms
    return out


def importance_table(result, topn: int | None = None, key: str = "importance") -> pd.DataFrame:
    """Tidy feature-importance view: features (rows) x recipes (columns), values = mean
    importance, sorted by the row mean. Accepts the output of compare_one/compare_many
    (reads result.attrs[key]), or a {name -> Series} dict, or a single Series.

    key="importance" (gain, default) or key="importance_perm" (permutation, only present if
    the compare_* call used extra_importance_test=True). `topn` keeps the top-N features.
    """
    if isinstance(result, pd.Series):
        imps = {key: result}
    elif isinstance(result, dict):
        imps = result
    else:  # a compare_* DataFrame
        imps = result.attrs.get(key)
        if not imps:
            raise ValueError(
                f"no {key!r} importances found -- pass a compare_one/compare_many result"
                + (" run with extra_importance_test=True" if key == "importance_perm" else "")
            )
    tab = pd.DataFrame(imps)  # features (union) x recipes; features absent from a recipe -> NaN
    tab = tab.loc[tab.mean(axis=1).sort_values(ascending=False).index]  # order by avg importance
    tab.index.name = "features"  # so to_csv writes a proper header instead of "Unnamed: 0"
    return tab if topn is None else tab.head(topn)


def importance_breakdown(
    result, recipe: str | None = None, key: str = "importance", topn: int | None = None
) -> pd.DataFrame:
    """Per-feature importance for ONE recipe, as 'raw' score + 'pct' (share of the total).

    Accepts a single Series, a {name -> Series} dict, or a compare_* result (reads
    result.attrs[key]); for a multi-recipe result pass recipe= to pick one. Sorted desc.

    Units depend on `key`:
      key="importance"      raw = fraction of the model's total GAIN (already sums to ~1),
                            so pct is that as a percentage -- "X% of the model's gain".
      key="importance_perm" raw = drop in the score (R2/AUC) when the feature is permuted on
                            held-out rows (metric units; READ THIS ONE). pct is its share of
                            the total, which is only loosely meaningful (can be skewed by
                            negative entries) -- prefer the raw column for permutation.
    """
    if isinstance(result, pd.Series):
        s = result
    else:
        imps = result if isinstance(result, dict) else result.attrs.get(key)
        if not imps:
            raise ValueError(f"no {key!r} importances found -- pass a compare_* result or a Series")
        if recipe is None:
            if len(imps) != 1:
                raise ValueError(f"result has {len(imps)} recipes {list(imps)}; pass recipe=")
            recipe = next(iter(imps))
        s = imps[recipe]
    s = s.dropna().sort_values(ascending=False)
    out = pd.DataFrame({"raw": s, "pct": 100 * s / s.sum()})
    out.index.name = "features"
    return out if topn is None else out.head(topn)


def save_comparison(result: pd.DataFrame, path: str) -> list[str]:
    """Save a compare_one/compare_many result to CSV(s) for later comparison.

    Writes the metric table to '<path>.csv'. Importances are per-feature vectors that can't
    share the flat metric CSV, so any in result.attrs are written to sidecar files
    '<path>_importance.csv' (gain) and '<path>_importance_perm.csv' (permutation, if the run
    used extra_importance_test=True). Each sidecar carries the raw score per recipe (one
    '<recipe>' column each). Returns the list of files written; pair with load_comparison().
    Call this on the object compare_* RETURNED -- reshaping a DataFrame drops .attrs, taking
    the importances with it.
    """
    stem = path[:-4] if path.endswith(".csv") else path
    written = [f"{stem}.csv"]
    result.to_csv(written[0])
    for key, suffix in (("importance", "_importance"), ("importance_perm", "_importance_perm")):
        if result.attrs.get(key):
            raw = importance_table(result, key=key)  # features x recipes, raw mean importance
            fp = f"{stem}{suffix}.csv"
            raw.to_csv(fp)
            written.append(fp)
    print(f"Wrote {len(written)} files:")
    for f in written:
        print(f"  {f}")
    return written


def load_comparison(path: str) -> pd.DataFrame:
    """Load a comparison saved by save_comparison().

    Returns the metric DataFrame with any sidecar importances re-attached to .attrs (as
    {recipe -> Series} of RAW scores), so importance_table(result[, key=...]) works on it
    exactly as on a fresh compare_* result.
    """
    stem = path[:-4] if path.endswith(".csv") else path
    out = pd.read_csv(f"{stem}.csv", index_col="recipe")
    for key, suffix in (("importance", "_importance"), ("importance_perm", "_importance_perm")):
        fp = f"{stem}{suffix}.csv"
        if os.path.exists(fp):
            tab = pd.read_csv(fp, index_col=0)  # features x recipes (raw scores)
            out.attrs[key] = {c: tab[c].dropna() for c in tab.columns}
    return out


# ── train & persist a deployable model ───────────────────


def fit_full(
    recipe: Recipe,
    sites: Sequence[str] | None = None,
    target_col: str = TARGET,
    task: Task = "reg",
    val_frac: float = 0.1,
    seed: int = 42,
    extra_importance_test: bool = False,
    final_fit: bool = False,
    save_path: str | None = None,
    progress: bool | str = False,
    min_rows: int = 500,
    **xgb_kw: Any,
) -> tuple[Model, list[str], dict[str, pd.Series]]:
    """Fit ONE deployable model on the full pooled dataset (no cross-validation holdout).

    cook_many only produces cross-validated fold-models for *scoring* -- each is trained on N-1 sites, so none is a single model fit on all the data. This pools every usable row across `sites` (same pooling cook_many uses) and fits one estimator suitable for saving and predicting on new sites.

    A `val_frac` random slice is held out (it both picks the early-stopping iteration, when the config uses early_stopping_rounds -- the default does -- and serves as the held-out set for permutation importance). By default the shipped model is the one trained on the rest (1 - val_frac of the rows).

    final_fit=True instead trains the shipped model on 100% of the rows: it first fits on the holdout to learn the early-stopping iteration, then refits on ALL rows at that fixed tree count (early_stopping_rounds disabled). Use this for the deployed model -- it sees every
    row. (With no early_stopping_rounds configured there's nothing to learn, so it just fits on all rows.)

    Always computes GAIN importance (free, from the shipped model). With extra_importance_test=True also computes PERMUTATION importance on the val_frac holdout using the holdout-trained model (honest -- that model never saw those rows). Both are returned in the importance dict and, if `save_path` is given, written to CSV sidecars.

    If `save_path` is given, also persists the model: '<save_path>' (booster) + '<save_path>.meta.json' + '<stem>_importance.csv' (+ '<stem>_importance_perm.csv').
    """
    if sites is None:
        sites = get_site_ids()
    label = (progress if isinstance(progress, str) else getattr(recipe, "__name__", "recipe")) if progress else None
    pool = _pool(recipe, sites, target_col, min_rows=min_rows, progress_label=label)
    feat = _features(pool, target_col)
    X, y = pool[feat], _target(pool, target_col, task)

    cfg = {**_DEFAULT_XGB, **xgb_kw}
    has_es = bool(cfg.get("early_stopping_rounds"))
    need_val = has_es or extra_importance_test

    es_model = None  # trained on (1 - val_frac); source of best_iteration and perm importance
    if need_val:
        val = np.random.RandomState(seed).rand(len(X)) < val_frac
        if has_es:
            es_model = _fit(task, X[~val], y[~val], X[val], y[val], **xgb_kw)
        else:
            es_model = _model(task, **xgb_kw)
            es_model.fit(X[~val], y[~val], verbose=False)

    if final_fit or not need_val:  # shipped model trains on ALL rows
        final_kw = dict(xgb_kw)
        if has_es:  # can't early-stop without a holdout -> fix the tree count instead
            final_kw["early_stopping_rounds"] = None
            if final_fit:
                final_kw["n_estimators"] = int(es_model.best_iteration) + 1
        m = _model(task, **final_kw)
        m.fit(X, y, verbose=False)
    else:  # default: ship the holdout-trained model (1 - val_frac of the rows)
        m = es_model

    importance = {"importance": pd.Series(m.feature_importances_, index=feat).sort_values(ascending=False)}
    if extra_importance_test:  # perm on the holdout, using the model that never saw it
        importance["importance_perm"] = _perm_importance([(es_model, np.flatnonzero(val))], X, y, feat, task, seed)

    if save_path is not None:
        for f in save_model(m, feat, save_path, task=task, target_col=target_col):
            print(f"  wrote {f}")
        stem = save_path[:-5] if save_path.endswith(".json") else save_path
        for f in _save_importance(importance, stem):
            print(f"  wrote {f}")
    return m, feat, importance


def save_model(model: Model, feat: Sequence[str], path: str, task: Task = "reg", target_col: str = TARGET) -> list[str]:
    """Persist a fitted model (XGBoost native booster) plus a sidecar of what's needed to use it.

    Writes '<path>' (the booster in XGBoost's version-robust JSON format) and
    '<path>.meta.json' (the ordered feature columns, task, and target name). The sidecar is what lets you line up new data's columns at predict time. Pair with load_model(). Returns the files written.
    """
    model.get_booster().save_model(path)  # booster-level: avoids the sklearn-wrapper save_model quirk
    meta = {"feat": list(feat), "task": task, "target": target_col, "model_class": type(model).__name__}
    with open(f"{path}.meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    return [path, f"{path}.meta.json"]


def _save_importance(importance: dict[str, pd.Series], stem: str) -> list[str]:
    written = []
    for key, suffix in (("importance", "_importance"), ("importance_perm", "_importance_perm")):
        s = importance.get(key)
        if s is not None:
            fp = f"{stem}{suffix}.csv"
            s.rename("importance").rename_axis("features").to_frame().to_csv(fp)
            written.append(fp)
    return written


def load_model(path: str) -> tuple[Model, dict]:
    """Load a model saved by save_model(). Returns (model, meta)."""
    with open(f"{path}.meta.json") as f:
        meta = json.load(f)
    m = xgb.XGBClassifier() if meta["task"] == "clf" else xgb.XGBRegressor()
    m.load_model(path)
    return m, meta


# demo/smoke test
if __name__ == "__main__":
    from src.features.features import (
        agg_crops,
        agg_surplus,
        agg_weather_w_lag,
        daily_nitrate,
        nitrate_violations,
        doy_climatology_pure_signal,
        site_static,
        lagged_sensor_nitrate,
    )
    from src.features.transformers import flatten_buckets, merge_on_date

    EDGES, VEL = [], 0.8

    def _covariates(site):
        wb = flatten_buckets(agg_weather_w_lag(site, edges=EDGES, water_velocity=VEL))
        cb = flatten_buckets(agg_crops(site, edges=EDGES, lam=10_000, exp=True))
        sb = flatten_buckets(agg_surplus(site, edges=EDGES, lam=10_000, exp=True))
        nd = daily_nitrate(site).rename(TARGET)
        return nd, [wb, cb, sb, doy_climatology_pure_signal(nd)]

    def recipe_A(site):  # covariates + calendar (target defaults to 'nitrate_con')
        nd, parts = _covariates(site)
        return merge_on_date([nd, *parts], spine=nd.index)

    def recipe_A_static(site):  # + static site descriptors
        d = recipe_A(site)
        for k, v in site_static(site).items():
            d[k] = v
        return d

    def recipe_B(site):  # + own autoregression
        nd, parts = _covariates(site)
        own = [lagged_sensor_nitrate([site], shift=k) for k in (1, 2, 3, 7)]
        return merge_on_date([nd, *parts, *own], spine=nd.index)

    def recipe_violation(site):  # builds a binary 'violation' target column
        nd, parts = _covariates(site)  # nd is used only for the spine/calendar, NOT as a feature
        v = nitrate_violations(site).rename("violation")
        return merge_on_date([v, *parts], spine=nd.index)

    reg_recipes = {"A_covariates": recipe_A, "A_static": recipe_A_static, "B_+own_AR": recipe_B}
    sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500][:25]

    print(f"\nINDIVIDUAL (cook_one) on {sites[0]}:")
    print(compare_one(reg_recipes, sites[0]).round(3).to_string())

    print(f"\nCROSS-SITE regression (cook_many) on {len(sites)} sites:")
    print(compare_many(reg_recipes, sites).round(3).to_string())

    print(f"\nCROSS-SITE classification -- caller passes target_col='violation', task='clf':")
    print(compare_many({"violation": recipe_violation}, sites, target_col="violation", task="clf").round(3).to_string())
