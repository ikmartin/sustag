"""lean_cook.py -- streamlined cross-site CV for the feature-manifest add-one screen.

A stripped, single-purpose fork of src/eval/cook.py. It keeps ONLY the pooled cross-site path (LOSO + LOFO decomposition, gain + permutation importance) and adds the screen's core optimization: instead of re-pooling for every recipe (cook.py's compare_many rebuilds every site's feature frame per recipe -- the dominant cost), build ONE wide pooled dataframe per task and score many recipes by MASKING its columns.

    pool = _pool_wide(wide_recipe, sites, target)      # build once
    for name, cols in recipes.items():                 # each recipe = a column subset
        score = _score_pool(pool, cols, ...)           # no re-pooling

Because every recipe scores the SAME rows under the SAME folds, base-vs-candidate deltas are perfectly paired -- CV variance cancels, so small deltas are trustworthy.

SELF-CONTAINED BY DESIGN: this directory does not import src/eval/cook.py, so the shared scoring logic is duplicated here on purpose. Any change to one must be mirrored in the other.

Score set (2026-07-27 rework, mirrors cook.py): 8 CLF / 6 REG columns, with every aggregate computed from the LOFO out-of-fold predictions rather than the LOSO ones. See METRICS.md and _cross_metrics below for what was dropped and why.

Dropped vs cook.py:
  * fit_full and all the save/load/importance-view helpers.
  * the LODO_d holdout (lodo{d}_*). It is a TRUE leave-one-out -- one fold per site, 123 vs 5 --
    so ~25x the fits, which is prohibitive across ~117 recipes. Screen here; run LODO_d in cook.py
    on the shortlist.
  * cook_one / cook_fleet / compare_one / compare_fleet, which no longer exist in cook.py either.

Comparability: results here share cook.py's COLUMN NAMES and definitions, so the two can be read against each other qualitatively. Absolute values still differ -- the _SCREEN_XGB_* / _FULL_XGB_* configs below are the fast screening configs, one per (task, regime), not the shipped ones in train.RECIPE_XGB.
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

# ── the SCREEN regime: base + one candidate, ~14-40 columns ──────────────────
# Split from the FULL regime below because the two are genuinely different model-sizing problems, and one config tuned on `full` is the wrong depth and the wrong absolute reg_lambda for a base-sized arm. The split is by MODE, never by arm: within add-one every arm shares SCREEN and within drop-one every arm shares FULL, so delta_vs_base and delta_vs_full stay exactly paired. Nothing ever compares an add-one score to a drop-one score -- the synergy table joins two within-mode deltas -- so the two regimes never meet in one number.
#
# Tune with `python fulltune.py --task <task> --cols base`; paste the block it prints. reg_lambda and min_child_weight are ABSOLUTE values competing against a leaf's accumulated Hessian, which is 1/row for squared error but p(1-p) ~= 0.18 for logistic -- so a REG block pasted into a CLF dict means something ~5.6x stronger than it says. Never cross-paste.
_SCREEN_XGB_REG = dict(
    n_estimators=100,
    learning_rate=0.05,
    max_depth=6,
    min_child_weight=3.079527,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=461.929125,
    random_state=42,
    early_stopping_rounds=None,
)  # lofo_r2 = 0.4020

_SCREEN_XGB_CLF = dict(
    n_estimators=500,
    learning_rate=0.05,
    max_depth=3,
    min_child_weight=21.979878,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=329.698167,
    random_state=42,
    early_stopping_rounds=None,
)  # lofo_prauc = 0.6391 at k=2050

# ── the FULL regime: full_feature_set minus one unit, ~80-105 columns ────────
_FULL_XGB_REG = dict(
    n_estimators=490,
    learning_rate=0.05,
    max_depth=6,
    min_child_weight=3.721096,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=186.054786,
    random_state=42,
    early_stopping_rounds=None,
)  # lofo_r2 = 0.4281 vs 0.4295 at k=1340
_FULL_XGB_CLF = dict(
    n_estimators=180,
    learning_rate=0.05,
    max_depth=4,
    min_child_weight=21.979878,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=329.698167,
    random_state=42,
    early_stopping_rounds=None,
)


_DEFAULTS: dict[str, dict[str, dict]] = {
    "screen": {"reg": _SCREEN_XGB_REG, "clf": _SCREEN_XGB_CLF},
    "full": {"reg": _FULL_XGB_REG, "clf": _FULL_XGB_CLF},
}
REGIMES = tuple(_DEFAULTS)


def default_xgb(task: Task, regime: str = "full") -> dict:
    """The screening config for (`task`, `regime`), as a fresh dict.

    `regime` is 'screen' (add-one arms: base + a candidate) or 'full' (drop-one arms: full minus a unit) -- see the block comments above for why they are separate. Defaults to 'full', the wider of the two, so a caller that has not been taught about regimes gets the config the old single dict held rather than a base-sized one.

    A copy, not the module-level dict: callers log it and pass it on, and a mutation of the shared object would silently retune every later recipe in the same run.
    """
    if regime not in _DEFAULTS:
        raise ValueError(f"regime must be one of {REGIMES}, got {regime!r}")
    return dict(_DEFAULTS[regime][task])


_PERM_REPEATS = 5  # shuffles per feature per fold for permutation importance
_FAR_BUDGET = 0.10  # false-alarm-rate budget for recall_at_far
_BASINGRPS = None  # lazy {site: conflict-graph component id}
# ── POOL -- sites in, one wide frame out ──────────────────────────────────────


def _check_target(recipe: Recipe, df: pd.DataFrame, target: str) -> None:
    name = getattr(recipe, "__name__", repr(recipe))
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"recipe {name!r} returned {type(df).__name__}, expected a DataFrame")
    if target not in df.columns:
        raise KeyError(f"recipe {name!r} produced columns {list(df.columns)} -- no target {target!r}")


def _target(df: pd.DataFrame, target: str, task: Task) -> pd.Series:
    y = df[target]
    return y.astype("int64") if task == "clf" else y


def _pool_wide(
    wide_recipe: Recipe, sites: Sequence[str], target: str, progress_label: str | None = None
) -> pd.DataFrame:
    """Stack every site's wide_recipe frame into one pooled master frame, tagged with `site`.

    Built once, then reused by every recipe in compare_many via column masking. Sites that raise, or have no non-nan target, are skipped and reported.
    """
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
        if d.empty:
            skipped.append((s, "no rows with a non-nan target"))
            continue
        d = d.copy()
        d["site"] = s
        frames.append(d)
    if progress_label is not None:
        print(
            f"\r  pooled {progress_label}: {len(frames)}/{total} sites usable" + " " * 30,
            flush=True,
        )
        for s, reason in skipped:
            print(f"    [skipped] {s}: {reason}")
    if not frames:
        from collections import Counter

        reasons = "; ".join(f"{n}x {r}" for r, n in Counter(r for _, r in skipped).most_common(3))
        raise ValueError(f"no sites produced a usable frame ({len(skipped)} skipped) -- {reasons}")
    out = pd.concat(frames, ignore_index=True)
    # float32 for the features: XGBoost casts to float32 internally anyway, so this is exact (verified: fold predictions bit-identical), and it halves pool memory (~99 MB -> ~49 MB at 120k x 103) while trimming ~11% off fit time. The target keeps its dtype so the metric arithmetic is untouched.
    f64 = [c for c in out.columns if c != target and out[c].dtype == "float64"]
    if f64:
        out[f64] = out[f64].astype("float32")
    return out


# ── FIT -- frame + folds in, fitted models out ────────────────────────────────


def _model(task: Task, **overrides: Any) -> Model:
    """Estimator for `task` at the task's screening config, with `overrides` layered on. The base is picked by task, so a caller that passes no overrides gets REG's tuned numbers on reg and CLF's on clf rather than one shared dict."""
    cfg = {**default_xgb(task), **overrides}
    if task == "clf":
        return xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", **cfg)
    return xgb.XGBRegressor(eval_metric="rmse", **cfg)


def _oof_predict(model: Model, X: pd.DataFrame, task: Task) -> np.ndarray:
    return model.predict_proba(X)[:, 1] if task == "clf" else model.predict(X)


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


def folds_grouped(groups, n_splits: int):
    """GroupKFold (train_idx, test_idx) pairs; rows sharing a group stay together.

    groups=site -> LOSO, groups=basin_groups(...) -> LOFO. min(n_splits, n_groups) folds, NOT leave-one-out. Empty if <2 groups, which the caller sees as an all-NaN OOF.
    """
    n = min(n_splits, pd.Series(groups).nunique())
    if n < 2:
        return
    yield from GroupKFold(n).split(np.zeros(len(groups)), groups=groups)


def folds_true_lofo(families, max_holdout_pct: float = 0.2):
    fam = np.asarray(families)
    total = len(fam)
    counts = pd.Series(fam).value_counts()
    for f in sorted(counts.index):
        if total and counts[f] / total > max_holdout_pct:
            continue
        test = np.flatnonzero(fam == f)
        train = np.flatnonzero(fam != f)
        if len(test) and len(train):
            yield train, test


def _fold_models(X, y, folds, task, **xgb_kw):
    """Fit one model per (train_idx, test_idx) fold; yield (test_idx, model).

    `folds` = any iterable of index pairs: folds_grouped for LOSO/LOFO, folds_true_lofo for one-family-at-a-time. Fits on ALL of a fold's training rows -- no validation slice, no eval_set, no early stopping. A non-None `early_stopping_rounds` therefore raises from XGBoost, deliberately: nothing here can honour it. Tree count is tune.py's job. cook.py's version still carves 15% and early-stops.
    """
    for train, test in folds:
        m = _model(task, **xgb_kw)
        m.fit(X.iloc[np.asarray(train)], y.iloc[np.asarray(train)], verbose=False)
        yield test, m


def _fold_oof(models, X, task: Task, n: int) -> np.ndarray:
    """Out-of-fold prediction vector from already-fitted (model, test_idx) pairs. Split from fitting so the same fits also serve gain and permutation importance."""
    oof = np.full(n, np.nan)
    for m, te in models:
        oof[te] = _oof_predict(m, X.iloc[te], task)
    return oof


# ── SCORE -- predictions in, metrics out ──────────────────────────────────────


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


_BETA = 2.0  # recall emphasis of the deployed operating point (recall weighted beta^2 = 4x precision)


def _decomposition(y: np.ndarray, oof: np.ndarray, site: np.ndarray, task: Task) -> dict[str, float]:
    """between / within / macro decomposition of ONE out-of-fold prediction vector."""
    ok = ~(np.isnan(y) | np.isnan(oof))
    if ok.sum() < 2:
        keys = ("between_rate_r2", "macro_auc") if task == "clf" else ("between_r2", "within_r2", "macro_r2")
        return {k: float("nan") for k in keys}
    tab = pd.DataFrame({"y": y[ok], "p": oof[ok], "g": site[ok]})
    site_means = tab.groupby("g")[["y", "p"]].mean()
    per_site = tab.groupby("g").apply(lambda d: _per_site_score(d.y, d.p, task))
    if task == "clf":
        return dict(
            between_rate_r2=r2_score(site_means.y, site_means.p),
            macro_auc=float(np.nanmedian(per_site)),
        )
    sm = tab.groupby("g")["y"].transform("mean").to_numpy()
    pm = tab.groupby("g")["p"].transform("mean").to_numpy()
    return dict(
        between_r2=r2_score(site_means.y, site_means.p),
        within_r2=r2_score(tab.y.to_numpy() - sm, tab.p.to_numpy() - pm),
        macro_r2=float(np.nanmedian(per_site)),
    )


def _beta_point(y: np.ndarray, pred: np.ndarray, beta: float = _BETA) -> dict[str, float]:
    """Recall and FDR at the threshold maximising F_beta -- the DEPLOYED operating point.

    Keys are named for the beta used (f2 by default). FDR = FP/(TP+FP) is prevalence-dependent and must be read against `base`; FAR = FPR is not. tau is tuned on the same OOF it is scored on, so read these as mildly optimistic.
    """
    keys = (f"recall_at_f{beta:g}", f"fdr_at_f{beta:g}")
    ok = ~(np.isnan(y) | np.isnan(pred))
    y, pred = y[ok], pred[ok]
    if len(np.unique(y)) < 2:
        return dict.fromkeys(keys, float("nan"))
    prec, rec, _ = precision_recall_curve(y, pred)
    den = (beta**2) * prec + rec
    fb = np.divide((1 + beta**2) * prec * rec, den, out=np.zeros_like(prec), where=den > 0)
    i = int(np.nanargmax(fb))
    return {keys[0]: float(rec[i]), keys[1]: float(1.0 - prec[i])}


def _cross_metrics(pool, y, oof_site, oof_family, task: Task) -> dict[str, float]:
    """The reported score set -- 8 CLF / 6 REG columns. See METRICS.md for the full rationale.

    Kept deliberately identical to src/eval/cook.py's set MINUS the optional lodo{d}_* block, which this screening fork does not run (LODO_d is a true leave-one-out: 123 folds vs 5, ~25x the fits, prohibitive across ~117 recipes). Everything else matches, so a screening result and a cook.py result name the same quantities -- only the XGB config differs (see _SCREEN_XGB_* / _FULL_XGB_*).

    What was dropped, and why:
      * every loso_* except loso_auc/loso_r2 -- they correlate >=0.91 with one another, and LOSO
        ANTI-correlates with LOFO across recipes (REG -0.29, CLF -0.48), so it is misleading as a
        ranking criterion. One column survives to report the LOSO-LOFO leakage gap.
      * lofo_f1 (max-F1 cherry-picks a threshold AND weights precision == recall, contradicting the
        beta=2 deployment), lofo_mcc / lofo_f2 (0.75-0.95 correlated with the rest).
      * persist_skill -- an exact monotone transform of loso_r2 (rho = +-1.00) whose baseline the
        product cannot use: predict-yesterday needs a history a virtual site does not have.
      * spearman (0.92 with loso_r2), and the LOSO-computed rmse/between/within/macro.

    ALL aggregates now come from the LOFO OOF; they used to come from LOSO, which made every diagnostic optimistic. `lofo_auc`/`lofo_r2` are load-bearing names -- run_exp.py ranks by them.
    """
    site = pool["site"].to_numpy()
    overall = _score(y, oof_site, task)  # LOSO -- retained only for the leakage gap
    lofo = _score(y, oof_family, task)  # LOFO -- the honest number everything else is based on

    if task == "clf":
        return dict(
            loso_auc=overall["auc"],
            lofo_prauc=lofo["prauc"],  # HEADLINE: average precision on the family-grouped OOF
            lofo_auc=lofo["auc"],
            lofo_prauc_lift=_imbalance_suite(y, oof_family)["prauc_lift"],
            **{f"lofo_{k}": v for k, v in _beta_point(y, oof_family).items()},
            lofo_brier=lofo["brier"],
            base=lofo["base"],
            **{f"lofo_{k}": v for k, v in _decomposition(y, oof_family, site, task).items()},
        )
    return dict(
        loso_r2=overall["r2"],
        lofo_r2=lofo["r2"],
        lofo_rmse=lofo["rmse"],  # human-readable mg/L; monotone in lofo_r2, never rank on it
        **{f"lofo_{k}": v for k, v in _decomposition(y, oof_family, site, task).items()},
    )


def _gain_importance(fold_models, feat: list[str]) -> pd.Series:
    """Mean XGBoost GAIN importance across fold-models, indexed by feature, sorted desc."""
    cols = [pd.Series(m.feature_importances_, index=feat) for m, _ in fold_models]
    return pd.concat(cols, axis=1).mean(axis=1).sort_values(ascending=False)


def _perm_importance(fold_models, X, y, feat: list[str], task: Task, cols=None, seed: int = 0) -> pd.Series:
    """Permutation importance for `cols` (default: all `feat`), averaged over folds. For each fold model, shuffle a column on that fold's HELD-OUT rows and measure the drop in score (R2 for reg, ROC-AUC for clf), _PERM_REPEATS times.

    Restricting `cols` to a recipe's ADDED columns is the screen's key saving: a full per-column perm over every feature is ~K * n_feat * _PERM_REPEATS rescorings, but for an add-one run we only care about the added feature's marginal perm, so we permute just those 1-3 columns. Same score-drop semantics as sklearn.inspection.permutation_importance, done by hand so the column set is restrictable.
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


# ── API -- the three entry points ─────────────────────────────────────────────


def _score_pool(
    pool: pd.DataFrame,
    feat: list[str],
    target_col: str,
    task: Task,
    n_splits: int = 5,
    extra_importance_test: bool = True,
    perm_cols=None,
    *,
    true_lofo: bool = False,
    max_holdout_pct: float = 0.2,
    loso: bool = True,
    **xgb_kw: Any,
) -> dict:
    """Score one feature-column list against an already-pooled frame.

    Sharing `pool` and the folds across recipes is what makes base-vs-candidate deltas exactly paired. `perm_cols` scopes permutation importance: None = every column, a list = only those, [] = skip. `true_lofo` is keyword-only -- callers pass earlier args positionally, so a new positional would bind to perm_cols -- and returns an IDENTICAL column set to the GroupKFold regime, so the caller must record which was used.
    """
    # ── 1. the data ──
    X, y = pool[feat], _target(pool, target_col, task)
    basin_grp = basin_groups(pool["site"])
    n_families = int(pd.Series(basin_grp).nunique())
    fam_folds = list(folds_true_lofo(basin_grp, max_holdout_pct)) if true_lofo else None

    # ── 2. the fits ── two passes over the same rows under two holdouts. The MODELS are kept, not just their predictions: they were being fitted and discarded anyway, so both importances ride these same fits for free. Permutation importance in particular must be measured on the FAMILY holdout, not the site one -- LOSO and LOFO anti-correlate across recipes (REG -0.29, CLF -0.48), so perm scored on LOSO models answers a systematically different question from the lofo_* columns and the add-one/drop-one deltas built from them, and a perm-vs-delta disagreement would then be an artifact of the mismatch rather than a fact about the feature.
    # The site pass exists only to produce loso_* -- the leakage gap -- and is a FULL second CV pass, so callers ranking on LOFO alone turn it off for roughly 2x. Off, oof_site stays all-NaN and _score returns NaNs, so the column set is unchanged rather than short.
    site_models = (
        [(m, te) for te, m in _fold_models(X, y, folds_grouped(pool["site"], n_splits), task, **xgb_kw)] if loso else []
    )
    fam_models = [
        (m, te)
        for te, m in _fold_models(X, y, fam_folds if true_lofo else folds_grouped(basin_grp, n_splits), task, **xgb_kw)
    ]

    # ── 3. the scores ──
    oof_site = _fold_oof(site_models, X, task, len(y))
    oof_family = _fold_oof(fam_models, X, task, len(y))

    if true_lofo:
        # stderr, not a column: the column set stays identical to the GroupKFold regime so this is a drop-in. Coverage still matters -- rows in a family too large to hold out never enter the OOF, so the scores describe only the eligible part of the cohort.
        covered = float(np.mean(~np.isnan(oof_family))) if len(oof_family) else float("nan")
        print(
            f"  [true LOFO] {len(fam_folds)}/{n_families} families held out one at a time "
            f"(cap {max_holdout_pct:.0%} of rows); scores cover {covered:.1%} of pooled rows",
            file=sys.stderr,
        )
    out = dict(
        n_sites=pool["site"].nunique(),
        n_families=n_families,
        n_rows=len(pool),
        n_feat=len(feat),
        **_cross_metrics(pool, np.asarray(y), oof_site, oof_family, task),
        # Gain rides the FAMILY models, like every other reported quantity. It used to ride the LOSO ones, kept there only so historical log.json `importance` blocks stayed comparable -- but a diagnostic that describes a different set of models from the scores beside it is worse than a discontinuity in the log. LOSO and LOFO anti-correlate across recipes (REG -0.29, CLF -0.48), so a gain ranking read off LOSO trees was quietly answering a different question from the lofo_* columns, the add-one/drop-one deltas, and the permutation importance below.
        importance=_gain_importance(fam_models, feat),
    )
    if extra_importance_test:
        cols = feat if perm_cols is None else perm_cols
        # LOFO models, matching the holdout every reported score is computed on -- as gain now does too, so both importances and every score describe the same fits.
        out["importance_perm"] = _perm_importance(fam_models, X, y, feat, task, cols=cols)
    return out


def cook_many(
    wide_recipe: Recipe,
    sites: Sequence[str] | None = None,
    target_col: str = TARGET,
    task: Task = "reg",
    n_splits: int = 5,
    extra_importance_test: bool = True,
    progress: bool | str = False,
    true_lofo: bool = False,
    max_holdout_pct: float = 0.2,
    **xgb_kw: Any,
) -> dict:
    """Single-recipe pooled cross-site CV.

    Parameters
    ----------
    wide_recipe : Recipe
        Callable site_uid -> feature/target frame.
    sites : Sequence[str], optional
        Site ids; default the full cohort.
    target_col : str, default TARGET
    task : {'reg', 'clf'}, default 'reg'
    n_splits : int, default 5
        Folds for both holdouts.
    extra_importance_test : bool, default True
        Also compute permutation importance.
    progress : bool or str, default False
        Pooling progress label.
    true_lofo : bool, default False
        One family per fold.
    max_holdout_pct : float, default 0.2
        With true_lofo, families above this row share are never held out.
    **xgb_kw
        XGBoost overrides.

    Returns
    -------
    dict
        Score columns + 'importance' + 'importance_perm'.

    See Also
    --------
    compare_many : many recipes over one shared pool.
    """
    if sites is None:
        sites = get_site_ids()
    label = (
        (progress if isinstance(progress, str) else getattr(wide_recipe, "__name__", "recipe")) if progress else None
    )
    pool = _pool_wide(wide_recipe, sites, target_col, progress_label=label)
    feat = [c for c in pool.columns if c != target_col and c not in _STRUCTURAL]
    return _score_pool(
        pool,
        feat,
        target_col,
        task,
        n_splits,
        extra_importance_test,
        true_lofo=true_lofo,
        max_holdout_pct=max_holdout_pct,
        **xgb_kw,
    )


def compare_many(
    wide_recipe: Recipe,
    recipes: dict[str, set],
    sites: Sequence[str] | None = None,
    target_col: str = TARGET,
    task: Task = "reg",
    n_splits: int = 5,
    extra_importance_test: bool = True,
    progress: bool = True,
    base_cols=None,
    full_names=(),
    pool: pd.DataFrame | None = None,
    true_lofo: bool = False,
    max_holdout_pct: float = 0.2,
    **xgb_kw: Any,
) -> pd.DataFrame:
    """Score many recipes over one shared wide pool.

    Parameters
    ----------
    wide_recipe : Recipe
        Emits every candidate column; used only if `pool` is None.
    recipes : dict[str, set]
        Recipe name -> the column subset it uses. Each is scored on pool[cols], no re-pooling.
    sites : Sequence[str], optional
        Site ids; default the full cohort.
    target_col : str, default TARGET
    task : {'reg', 'clf'}, default 'reg'
    n_splits : int, default 5
        Folds for both holdouts.
    extra_importance_test : bool, default True
        Also compute permutation importance.
    progress : bool, default True
        Print per-recipe progress.
    base_cols : sequence, optional
        Scopes permutation importance: recipes permute only what they add over these. Without it, every recipe permutes all its columns.
    full_names : iterable, default ()
        Recipes exempt from `base_cols` scoping -- they permute every column.
    pool : pd.DataFrame, optional
        Prebuilt pool from _pool_wide, to share one pooling pass across calls.
    true_lofo : bool, default False
        One family per fold.
    max_holdout_pct : float, default 0.2
        With true_lofo, families above this row share are never held out.
    **xgb_kw
        XGBoost overrides.

    Returns
    -------
    pd.DataFrame
        Metric table indexed by recipe; importances in .attrs['importance'] and .attrs['importance_perm'].

    See Also
    --------
    cook_many : one recipe, pooling included.
    """
    if pool is None:
        if sites is None:
            sites = get_site_ids()
        pool = _pool_wide(wide_recipe, sites, target_col, progress_label=("wide" if progress else None))

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
            print(
                f"compare_many: [{i}/{n}] {name:<34.34s} {len(feat):>3d} feat  perm {scope:>3}  elapsed {time.time() - t0:4.0f}s",
                flush=True,
            )
        r = _score_pool(
            pool,
            feat,
            target_col,
            task,
            n_splits,
            extra_importance_test,
            perm_cols=perm_cols,
            true_lofo=true_lofo,
            max_holdout_pct=max_holdout_pct,
            **xgb_kw,
        )
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
