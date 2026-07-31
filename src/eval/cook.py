"""
cook.py -- reusable harness for comparing nitrate "recipes".
============================================================

A *recipe* is a function  site_uid -> DataFrame  containing a target column, a `date` column, and feature columns (what recipe_A/B/C produce). Recipes stay pure (site -> frame) and know nothing about CV; this module supplies the evaluation.

Tell cook which column is the target by passing `target_col=`. Default "nitrate_con". Tell cook which task (regression or classification) by passing `task=`, either "reg" or "clf". Default "reg".

Entry points:

  cook_many(recipe, sites)                  score ONE recipe over the pooled cross-site data.
  compare_many({name: recipe}, sites)       score SEVERAL recipes, each pooled independently.
  compare_masks(wide_recipe, {name: cols})  score several COLUMN-SUBSET recipes against ONE pool.
  fit_full(recipe, sites)                   fit one deployable model on everything, no CV.

`compare_masks` is the paired path: every recipe sees the same rows and the same fold assignments, so a base-vs-candidate delta isolates the feature effect instead of mixing in a cohort effect. Prefer it whenever the recipes are column subsets of one wide frame.

THREE HOLDOUT STRATEGIES, ordered from optimistic to conservative:

  LOSO    GroupKFold grouped by SITE.   A site's own rows are held out, but its nested
          neighbours stay in training -- the direct leakage channel is open.
  LODO_d  Leave One *Distance-family* Out. One test site per fold; every containment-connected
          site within d km is dropped from training too. Closes the leakage channel while
          keeping ~120 of 123 sites in training, which resembles the deployed situation.
  LOFO    GroupKFold grouped by conflict-graph FAMILY. Discards a whole family -- up to ~25%
          of all rows -- so it is more pessimistic than deployment.

*** By DEFAULT the LOSO/LOFO names are historical and do NOT mean leave-one-out.
*** Both run GroupKFold(min(n_splits, n_groups)) with n_splits=5, i.e. 5 folds holding out ~1/5 of the sites or families each. LODO_d is a true leave-one-out (one fold per site). See notes/future-cv-work-report.md.

`true_lofo=True` (a flag on cook_many/compare_many/compare_masks/_score_pool) makes LOFO literal: one fold per basin family, that family held out, every other site trained on. `max_holdout_pct` (default 0.2) bars any family larger than that share of pooled rows from being held out -- it stays in training, but never becomes the test set, so one oversized family cannot dominate the pooled OOF. It returns the IDENTICAL column set, aggregated identically (metrics over the pooled out-of-fold vector), so it is a drop-in alternative. Direction of the difference: true LOFO holds out ONE family per fold where GroupKFold(5) holds out ~1/5 of them, so each fold trains on MORE data and the numbers come out HIGHER -- measured on an 18-site/14-family pool, lofo_auc 0.913 (GroupKFold) vs 0.930 (true). That makes it the better match for deployment (every known site in training, one unseen basin predicted) but a more optimistic number, not a more conservative one. Since a result carries no marker of which regime produced it, record the regime wherever you log the run.

Cross-site performance is a DECOMPOSITION, not one number:
  between_r2  predicted vs actual per-site means -- does it rank site levels?
  within_r2   after removing each site's mean    -- does it track daily movement?
  macro_r2    median per-site R2 (equal weight per site, vs row-weighted overall)

These aggregates are computed from the LOFO out-of-fold predictions. They used to be computed from the LOSO ones, which made every diagnostic optimistic -- LOSO and LOFO anti-correlate across recipes (REG -0.29, CLF -0.48), so LOSO is not a gentler LOFO but a misleading one. It is retained as a single column purely so the LOSO-LOFO gap can be read as a leakage gauge.
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
    n_estimators=800,
    learning_rate=0.02,
    max_depth=4,
    min_child_weight=10,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=5.0,
    random_state=42,
    early_stopping_rounds=None,
)

# fast configuration
# intended use: cook_many(sites, **FAST_XGB)
FAST_XGB = dict(n_estimators=800, learning_rate=0.05, max_depth=4)


# lazy {site : conflict-graph component id} loader maps a site to its group in the conflict-graph from data.splits
_BASINGRPS = None
# ── POOL -- sites in, one wide frame out ──────────────────────────────────────


def _check_target(recipe: Recipe, df: pd.DataFrame, target: str) -> None:
    """Fail quickly if a recipe didn't actually produce the requested target. One more way to avoid spending an hour on training for a crash at the end"""
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
            f"pass the right target_col= to cook_many."
        )


def _features(df: pd.DataFrame, target: str) -> list[str]:
    """Fetches feature columns: everything except the target and the bookkeeping columns."""
    return [c for c in df.columns if c != target and c not in _STRUCTURAL]


def _target(df: pd.DataFrame, target: str, task: Task) -> pd.Series:
    """Read the target column off the recipe's output frame (int-cast for clf)."""
    y = df[target]
    return y.astype("int64") if task == "clf" else y


def _pool_wide(recipe: Recipe, sites: Sequence[str], target: str, progress_label: str | None = None) -> pd.DataFrame:
    """Stack every site's recipe frame into one pooled master frame, tagged with `site`.

    Sites that raise, or produce no row with a non-NaN target, are skipped and reported. NO row-count floor here: src/build/water/filter_sites.py is the sole site filter, so a second one would silently shrink the cohort below what the build published. `progress_label` turns on a self-overwriting per-site status line.
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
        if d.empty:
            skipped.append((s, "no rows with a non-nan target"))
            continue
        d = d.copy()
        d["site"] = s
        frames.append(d)
    if progress_label is not None:
        # overwrite the last heartbeat with the accurate completed total (trailing spaces clear leftovers from the longer in-progress line), then newline to keep it
        print(f"\r  cooked {progress_label}: {len(frames)}/{total} sites usable" + " " * 30, flush=True)
        # list dropped sites AFTER the heartbeat line -- an inline print mid-loop gets clobbered by the next \r heartbeat, so build failures were invisible.
        for s, reason in skipped:
            print(f"    [skipped] {s}: {reason}")
    if not frames:
        from collections import Counter

        # if there aren't ANY frames, builds a human-readable error messages explaining (hopefully) why the sites got skipped.
        reasons = "; ".join(f"{n}x {r}" for r, n in Counter(r for _, r in skipped).most_common(3))
        raise ValueError(f"no sites produced a usable frame ({len(skipped)} sites skipped) -- {reasons}")
    out = pd.concat(frames, ignore_index=True)
    # float32 for the features: XGBoost casts to float32 internally anyway, so this is exact (verified: fold predictions bit-identical), and it halves pool memory (~99 MB -> ~49 MB at 120k x 103) while trimming ~11% off fit time. The target keeps its dtype so the metric arithmetic is untouched.
    f64 = [c for c in out.columns if c != target and out[c].dtype == "float64"]
    if f64:
        out[f64] = out[f64].astype("float32")
    return out


# ── FIT -- frame + folds in, fitted models out ────────────────────────────────


def _model(task: Task, **overrides: Any) -> Model:
    """Wrapper for returning the appropriate model, Classifier vs Regressor Pass a different model configuration, for instance, as an unwrapped dictionary. It will override the defaults."""
    cfg = {**_DEFAULT_XGB, **overrides}
    if task == "clf":
        return xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", **cfg)
    return xgb.XGBRegressor(eval_metric="rmse", **cfg)


def _oof_predict(model: Model, X: pd.DataFrame, task: Task) -> np.ndarray:
    """Just an 'Out Of Fold' prediction wrapper. Used soley to differentiate between The two primary tasks."""
    return model.predict_proba(X)[:, 1] if task == "clf" else model.predict(X)


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


def folds_grouped(groups, n_splits: int):
    """(train_idx, test_idx) pairs from GroupKFold -- rows sharing a group stay together.

    groups=site -> LOSO, groups=basin_groups(...) -> LOFO. min(n_splits, n_groups) folds, NOT leave-one-out. Empty if <2 groups, which the caller sees as an all-NaN OOF.
    """
    n = min(n_splits, pd.Series(groups).nunique())
    if n < 2:
        return
    yield from GroupKFold(n).split(np.zeros(len(groups)), groups=groups)


def folds_lodo(sites: pd.Series, d_km: float, prefer: str = "flow"):
    """(train_idx, test_idx) pairs for LODO_d -- one fold per site, buffer excluded from BOTH sides.

    For test site s, every containment-connected site within d km is dropped from training too, so those rows are in neither split -- which is why LODO_d cannot be expressed as a GroupKFold `groups` vector and why the fold machinery takes an index-pair iterator. A site whose training rows are all buffered out yields no fold, skipped rather than raised.
    """
    from src.splits.conflict_graph import lodo_folds

    site_arr = np.asarray(sites)
    order = sorted(pd.unique(site_arr))
    for s, excluded in lodo_folds(order, d_km, prefer=prefer):
        test = np.flatnonzero(site_arr == s)
        train = np.flatnonzero(~np.isin(site_arr, list(excluded)))
        if len(test) and len(train):
            yield train, test


def folds_true_lofo(families, max_holdout_pct: float = 0.2):
    """(train_idx, test_idx) pairs for TRUE leave-one-family-out: one fold per basin family.

    Against folds_grouped, which holds out ~1/5 of the families at once. `max_holdout_pct` bars a family larger than that share of pooled rows from ever being the test set -- it still TRAINS in every fold, it just never receives an OOF prediction, so one oversized family cannot dominate the pooled score; the caller reports the resulting coverage. Families are visited in sorted order, so folds are deterministic. A family that is the whole pool yields no fold.
    """
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

    `folds` is any iterable of index pairs -- folds_grouped for LOSO/LOFO, folds_true_lofo for one-family-at-a-time, folds_lodo for LODO_d. Fits on ALL of a fold's training rows: no validation slice, no eval_set, no early stopping, so a non-None `early_stopping_rounds` raises from XGBoost -- deliberately, since nothing here can honour it. Tree count is src/models/tune.py's job.
    """
    for train, test in folds:
        m = _model(task, **xgb_kw)
        m.fit(X.iloc[np.asarray(train)], y.iloc[np.asarray(train)], verbose=False)
        yield test, m


def _fold_oof(models, X, task: Task, n: int) -> np.ndarray:
    """Out-of-fold prediction vector from already-fitted (model, test_idx) pairs.

    Split from fitting so one set of fits serves the OOF, gain importance and permutation importance. Rows in no fold's test set stay NaN -- impossible under LODO_d (every site is a test site once), but it is how a degenerate grouped split surfaces.
    """
    oof = np.full(n, np.nan)
    for m, te in models:
        oof[te] = _oof_predict(m, X.iloc[te], task)
    return oof


# ── SCORE -- predictions in, metrics out ──────────────────────────────────────


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
    """Class-imbalance-robust metrics for (y, P(positive)).

    prauc_lift    average precision / base rate; >1 beats a random ranker, so it stays readable when prevalence moves
    f2            best F2 (recall weighted 2x precision)
    mcc           best Matthews correlation
    recall_at_far recall achievable at false-alarm rate (FPR) <= `far`

    `far` defaults to the module-level _FAR_BUDGET, resolved at CALL time so a runtime override (train.py's --false-alarm-rate reassigning cook._FAR_BUDGET) is honoured.
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
    """Pooled metrics for a set of (y, prediction). NaN predictions are dropped (e.g. an all-NaN OOF from a grouped split with too few groups -> all metrics NaN)."""
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


_BETA = 2.0  # recall emphasis of the deployed operating point (recall weighted beta^2 = 4x precision)


def _decomposition(y: np.ndarray, oof: np.ndarray, site: np.ndarray, task: Task) -> dict[str, float]:
    """between / within / macro decomposition of ONE out-of-fold prediction vector.

    Factored out so it can be applied to whichever holdout is being reported. It used to be hard-wired to the LOSO OOF, which made every diagnostic optimistic -- the project's "good timer, bad site-ranker" reading rested on numbers measured where nested basins leak.
    """
    ok = ~(np.isnan(y) | np.isnan(oof))
    if ok.sum() < 2:
        keys = ("between_rate_r2", "macro_auc") if task == "clf" else ("between_r2", "within_r2", "macro_r2")
        return {k: float("nan") for k in keys}
    tab = pd.DataFrame({"y": y[ok], "p": oof[ok], "g": site[ok]})
    site_means = tab.groupby("g")[["y", "p"]].mean()
    per_site = tab.groupby("g").apply(lambda d: _per_site_score(d.y, d.p, task))
    if task == "clf":
        return dict(
            between_rate_r2=r2_score(site_means.y, site_means.p),  # ranks per-site violation rates
            macro_auc=float(np.nanmedian(per_site)),  # equal weight per site
        )
    sm = tab.groupby("g")["y"].transform("mean").to_numpy()
    pm = tab.groupby("g")["p"].transform("mean").to_numpy()
    return dict(
        between_r2=r2_score(site_means.y, site_means.p),  # ranks site levels
        within_r2=r2_score(tab.y.to_numpy() - sm, tab.p.to_numpy() - pm),  # daily, level removed
        macro_r2=float(np.nanmedian(per_site)),
    )


# Betas the operating point is computed at. deploy.predict.threshold_for_beta SNAPS to the nearest row, so grid density IS the resolution a caller gets: deliberately fine through 1.75-2.75, around the beta=2 deployment, and coarse at the extremes where the operating point barely moves with beta.
BETA_GRID = (0.1, 0.5, 1.0, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0, 4.5, 5.0)


def beta_operating_points(y, pred, betas=BETA_GRID) -> list[dict]:
    """For each beta, the threshold tau maximising F_beta and the operating point there.

    Returns `tau`, `recall`, `precision` and `fdr` (= 1 - precision, the share of alarms that are false); one precision_recall_curve sweep drives every beta. Everything else follows from these plus the base rate: TP = recall*p, FP = TP*(1-precision)/precision, FN = p - TP, TN = (1-p) - FP, so accuracy, FPR and F1 need no extra storage.
    """
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)
    ok = ~(np.isnan(y) | np.isnan(pred))
    y, pred = y[ok], pred[ok]
    if len(np.unique(y)) < 2:
        raise ValueError("Need both classes present in the predictions to tune a threshold.")

    # prec/rec have length n+1; the final point (recall 0, precision 1) has no threshold, so drop it to align them with the n thresholds (cf. _best_f1)
    prec, rec, thr = precision_recall_curve(y, pred)
    P, R = prec[:-1], rec[:-1]

    rows = []
    for b in betas:
        b2 = b * b
        den = b2 * P + R
        fbeta = np.divide((1 + b2) * P * R, den, out=np.zeros_like(P), where=den > 0)
        i = int(np.nanargmax(fbeta))
        rows.append(
            {
                "beta": float(b),
                "tau": float(thr[i]),
                "recall": float(R[i]),
                "precision": float(P[i]),
                "fdr": float(1.0 - P[i]),
            }
        )
    return rows


def operating_points(y, pred, betas=BETA_GRID) -> dict:
    """Everything needed to reconstruct any decision threshold, from one PR sweep.

    `beta_table` is the exact operating point at each grid beta. No resampled PR curve is stored: precision read back off one is wrong by ~0.16 at 64 points and ~0.06 at 256, because the raw curve zigzags (measured: 1721 descents along ascending recall out of 3749 points), and nothing consumed it. A beta outside the grid wants a recomputation, not an interpolation -- or another entry in BETA_GRID.

    `base_rate` -- prevalence IN THE SCORED ROWS, which is not the pooled prevalence when the OOF covers only part of the pool (a capped true-LOFO run). FDR is prevalence-dependent, so quoting it against the wrong one misstates it, hence `coverage` alongside.
    """
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)
    ok = ~(np.isnan(y) | np.isnan(pred))
    ys, ps = y[ok], pred[ok]

    return {
        "beta_table": beta_operating_points(ys, ps, betas),
        "base_rate": float(ys.mean()),
        "coverage": float(ok.mean()),  # share of pooled rows that received a prediction at all
        "n_scored": int(ok.sum()),
    }


def _beta_point(y: np.ndarray, pred: np.ndarray, beta: float = _BETA) -> dict[str, float]:
    """Recall and FDR at the threshold maximising F_beta -- the DEPLOYED operating point.

    The single-beta case of beta_operating_points; keys are named for the beta used (f2 by default). FDR = FP/(TP+FP) is prevalence-dependent and must be read against `base`; FAR = FPR is not. tau is tuned on the same OOF it is scored on, so read these as mildly optimistic.
    """
    keys = (f"recall_at_f{beta:g}", f"fdr_at_f{beta:g}")
    try:
        row = beta_operating_points(y, pred, (beta,))[0]
    except ValueError:  # single-class OOF -- no threshold to pick
        return dict.fromkeys(keys, float("nan"))
    return {keys[0]: row["recall"], keys[1]: row["fdr"]}


def _cross_metrics(
    pool: pd.DataFrame,
    y: np.ndarray,
    oof_site: np.ndarray,
    oof_family: np.ndarray,
    task: Task,
    oof_lodo: np.ndarray | None = None,
    lodo_d_km: float | None = None,
) -> dict[str, float]:
    """The reported score set: 8 CLF / 6 REG columns, every aggregate from the LOFO OOF.

    See experiments/feature-manifest/METRICS.md for each column's derivation and for what was pruned. `lofo_auc` and `lofo_r2` are load-bearing names -- run_exp.py and render_results select the winner by them, and a rename surfaces only after a full run's fits are paid for. Exactly one LOSO column survives per task, purely to report the LOSO-LOFO leakage gap: the two anti-correlate across recipes (REG -0.29, CLF -0.48), so LOSO is not a gentler LOFO but a misleading one.
    """
    site = pool["site"].to_numpy()
    overall = _score(y, oof_site, task)  # LOSO -- retained only for the leakage gap
    lofo = _score(y, oof_family, task)  # LOFO -- the honest number everything else is based on

    if task == "clf":
        out = dict(
            loso_auc=overall["auc"],
            lofo_prauc=lofo["prauc"],  # HEADLINE: average precision on the family-grouped OOF
            lofo_auc=lofo["auc"],
            lofo_prauc_lift=_imbalance_suite(y, oof_family)["prauc_lift"],
            **{f"lofo_{k}": v for k, v in _beta_point(y, oof_family).items()},
            lofo_brier=lofo["brier"],
            base=lofo["base"],
            **{f"lofo_{k}": v for k, v in _decomposition(y, oof_family, site, task).items()},
        )
    else:
        out = dict(
            loso_r2=overall["r2"],
            lofo_r2=lofo["r2"],
            lofo_rmse=lofo["rmse"],  # human-readable mg/L; a monotone transform of lofo_r2, never rank on it
            **{f"lofo_{k}": v for k, v in _decomposition(y, oof_family, site, task).items()},
        )

    if oof_lodo is not None:
        # d is embedded in the column names: two runs at different d are different experiments and must not be silently averaged or diffed. lodo_d_km carries it machine-readably too.
        d = int(round(lodo_d_km or 0))
        lodo = _score(y, oof_lodo, task)
        blk = (
            {
                "auc": lodo["auc"],
                "prauc_lift": _imbalance_suite(y, oof_lodo)["prauc_lift"],
                "brier": lodo["brier"],
                **_beta_point(y, oof_lodo),
            }
            if task == "clf"
            else {"r2": lodo["r2"], "rmse": lodo["rmse"]}
        )
        blk.update(_decomposition(y, oof_lodo, site, task))
        out.update({f"lodo{d}_{k}": v for k, v in blk.items()})
        out["lodo_d_km"] = float(lodo_d_km)
    return out


_PERM_REPEATS = 5  # shuffles per feature per fold for the permutation-importance test


def _gain_importance(fold_models, feat: list[str]) -> pd.Series:
    """Mean XGBoost GAIN importance across the fold-models, indexed by feature, sorted desc.

    All-NaN over `feat` when there are no fold-models, mirroring _perm_importance. Both LOFO-skip branches in _score_pool (<2 basin families, or no family small enough to hold out) leave fam_models empty, and their contract is an all-NaN score row on the UNCHANGED column set -- so this cannot raise.
    """
    fold_models = list(fold_models)
    if not fold_models:
        return pd.Series(np.nan, index=feat)
    cols = [pd.Series(m.feature_importances_, index=feat) for m, _ in fold_models]
    return pd.concat(cols, axis=1).mean(axis=1).sort_values(ascending=False)


def _perm_importance(
    fold_models, X, y, feat: list[str], task: Task, seed: int = 0, *, cols: list[str] | None = None
) -> pd.Series:
    """Mean permutation importance across folds, indexed by feature, sorted desc.

    Shuffles each feature on each fold's HELD-OUT rows and measures the score drop (R2 for reg, ROC-AUC for clf) -- honest, and in metric units, but ~K * n_feat * _PERM_REPEATS rescorings. `cols` restricts the permuted set and is KEYWORD-ONLY: fit_full passes `seed` positionally, so a `cols` ahead of it would silently bind the wrong argument. Folds whose held-out set cannot be scored (a single-class clf window) are skipped; all-unscorable returns NaNs.
    """
    perm_cols = list(feat) if cols is None else [c for c in cols if c in feat]
    if not perm_cols:
        return pd.Series(dtype=float)
    scoring = "r2" if task == "reg" else "roc_auc"
    fold_models = list(fold_models)
    n = len(fold_models)
    acc = []
    for i, (m, te) in enumerate(fold_models, 1):
        # slow step (~K * n_cols * _PERM_REPEATS rescorings) -> self-overwriting status line
        print(
            f"\r  extra_importance_test: permutation fold {i}/{n} "
            f"({len(perm_cols)} feats x {_PERM_REPEATS} shuffles)",
            end="",
            flush=True,
        )
        try:
            r = permutation_importance(
                m, X.iloc[te], y.iloc[te], scoring=scoring, n_repeats=_PERM_REPEATS, random_state=seed
            )
        except ValueError:
            continue
        acc.append(pd.Series(r.importances_mean, index=feat).reindex(perm_cols))
    print(flush=True)  # newline so the finished status line stays put (not erased)
    if not acc:
        return pd.Series(np.nan, index=perm_cols)
    return pd.concat(acc, axis=1).mean(axis=1).sort_values(ascending=False)


def importance_table(result, topn: int | None = None, key: str = "importance") -> pd.DataFrame:
    """Feature-importance view: features (rows) x recipes (columns), sorted by row mean.

    Parameters
    ----------
    result : pd.DataFrame
        A compare_many / compare_masks result, carrying importances in .attrs.
    topn : int, optional
        Keep only the top-N features.
    key : {'importance', 'importance_perm'}, default 'importance'
        Gain, or permutation (present only if the compare_* call set extra_importance_test).

    Returns
    -------
    pd.DataFrame
    """
    if isinstance(result, pd.Series):
        imps = {key: result}
    elif isinstance(result, dict):
        imps = result
    else:  # a compare_* DataFrame
        imps = result.attrs.get(key)
        if not imps:
            raise ValueError(
                f"no {key!r} importances found -- pass a compare_many/compare_masks result"
                + (" run with extra_importance_test=True" if key == "importance_perm" else "")
            )
    tab = pd.DataFrame(imps)  # features (union) x recipes; features absent from a recipe -> NaN
    tab = tab.loc[tab.mean(axis=1).sort_values(ascending=False).index]  # order by avg importance
    tab.index.name = "features"  # so to_csv writes a proper header instead of "Unnamed: 0"
    return tab if topn is None else tab.head(topn)


# ── API -- scoring entry points ───────────────────────────────────────────────


def _score_pool(
    pool: pd.DataFrame,
    feat: Sequence[str],
    target_col: str,
    task: Task,
    n_splits: int = 5,
    extra_importance_test: bool = False,
    *,
    perm_cols: Sequence[str] | None = None,
    lodo_d_km: float | None = None,
    lodo_prefer: str = "flow",
    true_lofo: bool = False,
    max_holdout_pct: float = 0.2,
    return_oof: bool = False,
    loso: bool = True,
    label: str | None = None,
    **xgb_kw: Any,
) -> dict:
    """Score an ALREADY-POOLED frame against an explicit feature list.

    Split out of cook_many so many recipes share one pool (see compare_masks): identical rows and identical folds make a base-vs-candidate delta isolate the feature effect. `perm_cols` scopes permutation importance (None = all of `feat`, [] = skip). `lodo_d_km` adds the LODO_d holdout -- one fold per site, ~25x the fits of a 5-fold split, so off unless asked for. `return_oof` attaches the raw OOF vectors so a later metric change is a recomputation, not a refit.
    """
    feat = sorted(feat)  # deterministic column order: set iteration would perturb colsample draws
    # ── 1. the data ──
    X, y = pool[list(feat)], _target(pool, target_col, task)
    yv = np.asarray(y)

    # site (LOSO) folds: collect OOF predictions AND the fold-models (importance rides along free)
    # ── 2. the fits ── two passes over the same rows under two holdouts. The MODELS are kept, not just their predictions: they were being fitted and discarded anyway, so gain and permutation importance ride these same fits for free. Permutation importance in particular must be measured on the FAMILY holdout -- LOSO and LOFO anti-correlate across recipes (REG -0.29, CLF -0.48), so perm scored on LOSO models answers a systematically different question from the lofo_* columns and the deltas built from them.
    # The site-grouped pass exists only to produce loso_* -- the leakage gap. It is a FULL second CV pass, so callers that rank on LOFO alone (the tuners) turn it off and get roughly 2x. Off, oof_site stays all-NaN and _score returns NaNs, so the column set is unchanged rather than short.
    oof_site = np.full(len(y), np.nan)
    fold_models = []
    if loso:
        for te, m in _fold_models(X, y, folds_grouped(pool["site"], n_splits), task, **xgb_kw):
            oof_site[te] = _oof_predict(m, X.iloc[te], task)
            fold_models.append((m, te))

    basin_grp = basin_groups(pool["site"])
    n_families = pd.Series(basin_grp).nunique()
    if n_families < 2:  # grouped CV needs >=2 groups to hold one out
        print(
            f"  [LOFO skipped] {n_families} basin family across these {pool['site'].nunique()} "
            f"sites (need >=2) -- lofo_* = NaN",
            file=sys.stderr,
        )
    # family (LOFO) folds: keep the MODELS as well as the OOF. They were already being fitted and discarded, so retaining them is free -- and permutation importance must be measured on this holdout, not the LOSO one. LOSO and LOFO anti-correlate across recipes (REG -0.29, CLF -0.48), so perm scored on LOSO models answers a systematically different question from the lofo_* columns and the add-one/drop-one deltas built from them; a perm-vs-delta disagreement would then be an artifact of the mismatch rather than a fact about the feature.
    if true_lofo:
        fam_folds = list(folds_true_lofo(basin_grp, max_holdout_pct))
        if not fam_folds:
            print(
                f"  [LOFO skipped] no basin family is <= {max_holdout_pct:.0%} of the {len(pool)} pooled "
                f"rows, so none is eligible to hold out -- lofo_* = NaN. Raise max_holdout_pct.",
                file=sys.stderr,
            )
    else:
        fam_folds = folds_grouped(basin_grp, n_splits)

    oof_family = np.full(len(y), np.nan)
    fam_models = []
    for te, m in _fold_models(X, y, fam_folds, task, **xgb_kw):
        oof_family[te] = _oof_predict(m, X.iloc[te], task)
        fam_models.append((m, te))

    oof_lodo = None
    if lodo_d_km is not None:
        lodo_models = [
            (m, te) for te, m in _fold_models(X, y, folds_lodo(pool["site"], lodo_d_km, lodo_prefer), task, **xgb_kw)
        ]
        oof_lodo = _fold_oof(lodo_models, X, task, len(y))

    # ── 3. the scores ──
    out = dict(
        n_sites=pool["site"].nunique(),
        n_families=int(n_families),  # = the number of LOFO groups (low -> noisy LOFO)
        n_rows=len(pool),
        n_feat=len(feat),
        **_cross_metrics(pool, yv, oof_site, oof_family, task, oof_lodo, lodo_d_km),
        # Gain rides the FAMILY models, like every other reported quantity (always computed -- it is free from fits already paid for). It used to ride the LOSO ones, kept there only so historical `importance` blocks stayed comparable -- but a diagnostic describing a different set of models from the scores beside it is worse than a discontinuity in the log. LOSO and LOFO anti-correlate across recipes (REG -0.29, CLF -0.48), so a gain ranking read off LOSO trees was quietly answering a different question from the lofo_* columns, the add-one/drop-one deltas, and the permutation importance below.
        importance=_gain_importance(fam_models, list(feat)),
    )

    if task == "clf":
        # Free: derived from the LOFO OOF the CV already produced, so every clf run carries a reproducible decision-threshold table without a second grouped CV. Popped into .attrs by the compare_* wrappers so the metric table stays numeric.
        out["operating_points"] = operating_points(yv, oof_family)

    if true_lofo:
        # Reported to stderr, NOT as columns: this regime is a drop-in alternative to the GroupKFold one and must return the identical column set, so every existing consumer (the experiment log record, render_results' scoreboard) keeps working unchanged. Coverage still matters -- rows in a family too large to hold out never enter the OOF, so the scores describe only the part of the cohort that was eligible -- hence the line.
        covered = float(np.mean(~np.isnan(oof_family))) if len(oof_family) else float("nan")
        print(
            f"  [true LOFO] {len(fam_folds or [])}/{n_families} families held out one at a time "
            f"(cap {max_holdout_pct:.0%} of rows); scores cover {covered:.1%} of pooled rows",
            file=sys.stderr,
        )

    if label and fold_models:  # the fold-size line describes the SITE folds, which do not exist when loso is off
        n_total = pool["site"].nunique()
        test_per_fold = [pool["site"].iloc[te].nunique() for _, te in fold_models]
        print(
            f"  cooked {label}: {n_total} sites, {len(fold_models)}-fold site-grouped "
            f"(~{int(np.mean([n_total - t for t in test_per_fold]))} train / "
            f"~{int(np.mean(test_per_fold))} test per fold)"
        )
    if extra_importance_test:
        # LOFO models, matching the holdout every reported score is computed on -- as gain now does too, so both importances and every score describe the same fits.
        out["importance_perm"] = _perm_importance(fam_models, X, y, list(feat), task, cols=perm_cols)
    if return_oof:
        out["oof"] = pd.DataFrame(
            {
                "site": pool["site"].to_numpy(),
                "date": pool["date"].to_numpy() if "date" in pool.columns else np.nan,
                "family": np.asarray(basin_grp),
                "y": yv,
                "oof_site": oof_site,
                "oof_family": oof_family,
                **({"oof_lodo": oof_lodo} if oof_lodo is not None else {}),
            }
        )
    return out


def cook_many(
    recipe: Recipe,
    sites: Sequence[str] | None = None,
    target_col: str = TARGET,
    task: Task = "reg",
    n_splits: int = 5,
    extra_importance_test: bool = False,
    progress: bool | str = False,
    true_lofo: bool = False,
    max_holdout_pct: float = 0.2,
    pool: pd.DataFrame | None = None,
    **xgb_kw: Any,
) -> dict:
    """Pooled cross-site CV for one recipe, under site- and family-grouped holdouts.

    Parameters
    ----------
    recipe : Recipe
        Callable site_uid -> feature/target frame.
    sites : Sequence[str], optional
        Site ids; default the full cohort. Sites that fail to build, or yield no non-NaN target, are dropped with a reason.
    target_col : str, default TARGET
    task : {'reg', 'clf'}, default 'reg'
    n_splits : int, default 5
        Folds for both holdouts.
    extra_importance_test : bool, default False
        Also compute permutation importance on the family models.
    progress : bool or str, default False
        Per-site pooling status line; a string overrides the label.
    true_lofo : bool, default False
        One family per fold instead of GroupKFold.
    max_holdout_pct : float, default 0.2
        With true_lofo, families above this share of pooled rows are never held out (they still train).
    **xgb_kw
        XGBoost overrides.

    Returns
    -------
    dict
        Score columns + 'importance', plus 'importance_perm' and 'operating_points' when applicable.

    See Also
    --------
    compare_masks : many column-subset recipes over one shared pool; the paired path.
    """
    if sites is None:
        sites = get_site_ids()
    label = (progress if isinstance(progress, str) else getattr(recipe, "__name__", "recipe")) if progress else None
    if pool is None:  # a caller that already pooled these sites (train.py) passes it in rather than rebuilding
        pool = _pool_wide(recipe, sites, target_col, progress_label=label)
    return _score_pool(
        pool,
        _features(pool, target_col),
        target_col,
        task,
        n_splits=n_splits,
        extra_importance_test=extra_importance_test,
        true_lofo=true_lofo,
        max_holdout_pct=max_holdout_pct,
        label=label,
        **xgb_kw,
    )


def compare_many(
    recipes: dict[str, Recipe],
    sites: Sequence[str] | None = None,
    progress: bool = True,
    **kw: Any,
) -> pd.DataFrame:
    """Score several recipes, one row each, over the same sites and folds.

    Parameters
    ----------
    recipes : dict[str, Recipe]
        Recipe name -> callable. Each is pooled independently.
    sites : Sequence[str], optional
        Resolved once here so every recipe sees the same set; default the full cohort.
    progress : bool, default True
        Print a per-recipe header plus cook_many's own pooling line.
    **kw
        Forwarded to cook_many (target_col, task, n_splits, true_lofo, XGBoost overrides, ...).

    Returns
    -------
    pd.DataFrame
        Metric table indexed by recipe; importances in .attrs['importance'] / ['importance_perm'], and clf operating points in .attrs['operating_points'].

    See Also
    --------
    compare_masks : share ONE pool across recipes instead of re-pooling each.
    """
    if sites is None:
        sites = get_site_ids()
    if kw.get("pool") is not None and len(recipes) > 1:
        raise ValueError(
            f"compare_many got a prebuilt pool for {len(recipes)} recipes -- every recipe would be scored on the FIRST "
            f"recipe's columns. Pass pool= only for a single recipe (train.py's case), or use compare_masks, which "
            f"shares one wide pool across recipes by design."
        )
    rows, imps, perms, ops, n, t0 = [], {}, {}, {}, len(recipes), time.time()
    for i, (name, fn) in enumerate(recipes.items(), 1):
        if progress:
            print(f"compare_many: [{i}/{n}] {name:<28.28s} elapsed {time.time() - t0:4.0f}s", flush=True)
        r = cook_many(fn, sites, progress=(name if progress else False), **kw)
        imps[name] = r.pop("importance")
        if "importance_perm" in r:
            perms[name] = r.pop("importance_perm")
        if "operating_points" in r:
            ops[name] = r.pop("operating_points")  # dicts/lists -> attrs; the metric table stays numeric
        rows.append({"recipe": name, **r})
    if progress:
        print(f"compare_many: done {n}/{n} recipes in {time.time() - t0:.0f}s")
    out = pd.DataFrame(rows).set_index("recipe")
    out.attrs["importance"] = imps  # {recipe -> per-feature mean gain}; see importance_table()
    if ops:
        out.attrs["operating_points"] = ops  # {recipe -> beta_table + base_rate + coverage} (clf only)
    if perms:
        out.attrs["importance_perm"] = perms  # permutation importances (extra_importance_test=True)
    return out


def compare_masks(
    wide_recipe: Recipe | None,
    recipes: dict[str, Sequence[str]],
    sites: Sequence[str] | None = None,
    target_col: str = TARGET,
    task: Task = "reg",
    n_splits: int = 5,
    extra_importance_test: bool = True,
    progress: bool = True,
    *,
    pool: pd.DataFrame | None = None,
    base_cols: Sequence[str] | None = None,
    full_names: Sequence[str] = (),
    lodo_d_km: float | None = None,
    true_lofo: bool = False,
    max_holdout_pct: float = 0.2,
    **xgb_kw: Any,
) -> pd.DataFrame:
    """Score many COLUMN-SUBSET recipes against one shared pool -- the paired path.

    Every recipe sees identical rows and identical fold assignments, so a base-vs-candidate delta isolates the feature effect instead of mixing in a cohort effect.

    Parameters
    ----------
    wide_recipe : Recipe or None
        Emits every candidate column; unused (and may be None) when `pool` is given.
    recipes : dict[str, Sequence[str]]
        Recipe name -> the column subset it uses, not a callable.
    sites : Sequence[str], optional
    target_col : str, default TARGET
    task : {'reg', 'clf'}, default 'reg'
    n_splits : int, default 5
    extra_importance_test : bool, default True
    progress : bool, default True
    pool : pd.DataFrame, optional
        Prebuilt pool from _pool_wide, to share one pooling pass across calls.
    base_cols : Sequence[str], optional
        Scopes permutation importance: a recipe permutes only the columns it ADDS over these, turning K*n_feat*repeats rescorings into K*n_added*repeats. The base itself permutes nothing.
    full_names : Sequence[str], default ()
        Recipes exempt from that scoping -- they permute every column.
    lodo_d_km : float, optional
        Also run the LODO_d holdout at this buffer radius.
    true_lofo : bool, default False
    max_holdout_pct : float, default 0.2
    **xgb_kw
        XGBoost overrides.

    Returns
    -------
    pd.DataFrame
        Metric table indexed by recipe; importances and clf operating points in .attrs.
    """
    if pool is None:
        if wide_recipe is None:
            raise ValueError("compare_masks needs either wide_recipe or a prebuilt pool=")
        if sites is None:
            sites = get_site_ids()
        pool = _pool_wide(wide_recipe, sites, target_col, progress_label="wide")

    base_set = None if base_cols is None else set(base_cols)
    full = set(full_names)
    rows, imps, perms, ops, n, t0 = [], {}, {}, {}, len(recipes), time.time()
    for i, (name, cols) in enumerate(recipes.items(), 1):
        feat = sorted(c for c in cols if c in pool.columns)
        perm_cols = None if (base_set is None or name in full) else [c for c in feat if c not in base_set]
        if progress:
            scope = "all" if perm_cols is None else str(len(perm_cols))
            print(
                f"compare_masks: [{i}/{n}] {name:<34.34s} {len(feat):3d} feat  perm {scope:>3s}  "
                f"elapsed {time.time() - t0:4.0f}s",
                flush=True,
            )
        r = _score_pool(
            pool,
            feat,
            target_col,
            task,
            n_splits=n_splits,
            extra_importance_test=extra_importance_test,
            perm_cols=perm_cols,
            lodo_d_km=lodo_d_km,
            true_lofo=true_lofo,
            max_holdout_pct=max_holdout_pct,
            **xgb_kw,
        )
        imps[name] = r.pop("importance")
        if "importance_perm" in r:
            perms[name] = r.pop("importance_perm")
        if "operating_points" in r:
            ops[name] = r.pop("operating_points")  # dicts/lists -> attrs; the metric table stays numeric
        r.pop("oof", None)
        rows.append({"recipe": name, **r})
    if progress:
        print(f"compare_masks: done {n}/{n} recipes in {time.time() - t0:.0f}s")
    out = pd.DataFrame(rows).set_index("recipe")
    out.attrs["importance"] = imps
    if ops:
        out.attrs["operating_points"] = ops  # {recipe -> beta_table + base_rate + coverage} (clf only)
    if perms:
        out.attrs["importance_perm"] = perms
    return out


# ── API -- train & persist a deployable model ─────────────────────────────────


def fit_full(
    recipe: Recipe,
    sites: Sequence[str] | None = None,
    target_col: str = TARGET,
    task: Task = "reg",
    val_frac: float = 0.1,
    seed: int = 42,
    extra_importance_test: bool = False,
    save_path: str | None = None,
    progress: bool | str = False,
    pool: pd.DataFrame | None = None,
    **xgb_kw: Any,
) -> tuple[Model, list[str], dict[str, pd.Series]]:
    """Fit ONE deployable model on the full pooled dataset.

    cook_many only produces cross-validated fold-models for *scoring* -- each trained on N-1 sites, so none is a single model fit on all the data. This pools every usable row across `sites` and fits one estimator suitable for saving and predicting on new sites.

    `n_estimators` MUST be given explicitly (via xgb_kw), because nothing here can derive it: early stopping is gone, and defaulting to the config's ceiling is what silently over-trained the shipped model by ~8x. Get it from src/models/tune.py, whose winner is pasted into train.RECIPE_XGB beside the config it was tuned with.

    `val_frac` is carved only when `extra_importance_test` is set, and only to score permutation importance on rows the measuring model never saw. The shipped model always trains on 100% of rows.
    """
    if sites is None:
        sites = get_site_ids()
    if "n_estimators" not in xgb_kw:
        raise ValueError(
            "fit_full requires an explicit n_estimators -- no early stopping means nothing here can choose "
            "a tree count, and inheriting the config ceiling is what over-trained the shipped model. Run "
            "src/models/fulltune.py for the recipe and paste its winner into train.RECIPE_XGB."
        )
    label = (progress if isinstance(progress, str) else getattr(recipe, "__name__", "recipe")) if progress else None
    if pool is None:  # a caller that already pooled these sites (train.py) passes it in rather than rebuilding
        pool = _pool_wide(recipe, sites, target_col, progress_label=label)
    feat = _features(pool, target_col)
    X, y = pool[feat], _target(pool, target_col, task)

    # a holdout is needed ONLY to measure permutation importance honestly; the shipped model never depends on it
    es_model, val = None, None
    if extra_importance_test:
        val = np.random.RandomState(seed).rand(len(X)) < val_frac
        es_model = _model(task, **xgb_kw)
        es_model.fit(X[~val], y[~val], verbose=False)

    m = _model(task, **xgb_kw)
    m.fit(X, y, verbose=False)

    importance = {"importance": pd.Series(m.feature_importances_, index=feat).sort_values(ascending=False)}
    if extra_importance_test:  # perm on the holdout, using the model that never saw it
        importance["importance_perm"] = _perm_importance([(es_model, np.flatnonzero(val))], X, y, feat, task, seed)

    if save_path is not None:
        for f in save_model(m, feat, save_path, task=task, target_col=target_col,
                            recipe=getattr(recipe, "__name__", None)):
            print(f"  wrote {f}")
        stem = save_path[:-5] if save_path.endswith(".json") else save_path
        for f in _save_importance(importance, stem):
            print(f"  wrote {f}")
    return m, feat, importance


def save_model(model: Model, feat: Sequence[str], path: str, task: Task = "reg", target_col: str = TARGET,
               recipe: str | None = None) -> list[str]:
    """Persist a fitted booster plus the sidecar needed to use it.

    Parameters
    ----------
    model : Model
    feat : Sequence[str]
        ORDERED feature columns. The booster does not carry column names, so predict-time inputs must be lined up against this.
    path : str
        Booster path; the sidecar is written to '<path>.meta.json'.
    task : {'reg', 'clf'}, default 'reg'
    target_col : str, default TARGET
    recipe : str, optional
        Name of the recipe the model was fitted on, recorded in the sidecar. Four shipped models share two targets, so this is what identifies a booster once its filename no longer does.

    Returns
    -------
    list[str]
        The files written.

    See Also
    --------
    load_model : the inverse.
    """
    model.get_booster().save_model(path)  # booster-level: avoids the sklearn-wrapper save_model quirk
    # `recipe` so a booster carries its own provenance. There are four shipped models, two of them REG against the same target, and without this the FILENAME is the only thing distinguishing island_REG from network_REG -- which makes a rename or a copy enough to lose which feature set the weights were fitted on.
    meta = {"feat": list(feat), "task": task, "target": target_col, "model_class": type(model).__name__,
            "recipe": recipe}
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
    """Load a booster saved by save_model.

    Parameters
    ----------
    path : str
        Booster path; '<path>.meta.json' must sit beside it.

    Returns
    -------
    (Model, dict)
        The fitted model and its sidecar (feat / task / target, plus any beta_table + base_rate).
    """
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

    print(f"\nCROSS-SITE regression (cook_many) on {len(sites)} sites:")
    print(compare_many(reg_recipes, sites).round(3).to_string())

    print(f"\nCROSS-SITE classification -- caller passes target_col='violation', task='clf':")
    print(compare_many({"violation": recipe_violation}, sites, target_col="violation", task="clf").round(3).to_string())
