"""Pooled cross-site cross-validation for the cohort models.

THE HOLDOUT REGIMES, and why more than one:

  LOSO   GroupKFold grouped by SITE. A site's own rows are held out, but a site NESTED inside
         another's basin still trains the model that predicts it -- so LOSO is optimistic by
         exactly the amount basins overlap. Retained as ONE column per task, purely to report
         the LOSO-LOFO gap.
  LOFO   GroupKFold grouped by BASIN FAMILY: sites whose basins contain one another travel
         together, so a whole nested family leaves the training set at once. Every reported
         aggregate comes from this out-of-fold vector.
  LODO_d Leave-one-distance-family-out: one test site per fold, with every site within d km of
         flow-connected distance dropped from TRAINING too, so the buffer is in neither split.

Both grouped regimes run GroupKFold(min(n_splits, n_groups)) -- the names describe the GROUPING, not a leave-one-out. Metric names are load-bearing and identical to the predecessor's: `lofo_r2` and `lofo_prauc` are the headline selectors, and renaming either invalidates comparison against every historical run.

THE FAMILY DEFINITION IS BUILT HERE, from the walked basins: site A and site B are in one family when A's basin contains B's outlet reach or vice versa. The predecessor approximated this through a basins graph that no longer exists; this is the same relation computed directly from the COMID sets the project already builds.
"""

from __future__ import annotations

import json
import time
from typing import Callable

import numpy as np
import pandas as pd
from sklearn.metrics import (average_precision_score, brier_score_loss, mean_absolute_error,
                             mean_squared_error, r2_score, roc_auc_score)
from sklearn.model_selection import GroupKFold

import cohort
import config

import xgboost as xgb          # the real library: config.py explains why sys.path is APPENDED, not prepended

_DEFAULT_XGB = dict(n_estimators=600, learning_rate=0.05, max_depth=5, subsample=0.9,
                    colsample_bytree=0.8, min_child_weight=5, n_jobs=-1, tree_method="hist")
FAST_XGB = dict(n_estimators=300, learning_rate=0.08, max_depth=4)


def _model(task: str, **kw):
    cfg = {**_DEFAULT_XGB, **kw}
    if task == "clf":
        return xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", **cfg)
    return xgb.XGBRegressor(eval_metric="rmse", **cfg)


# ---- the pool --------------------------------------------------------------------------------------

def _rss_note() -> str:
    """Current resident memory, or "" when psutil is absent. CURRENT, not `ru_maxrss` -- that is a high-water mark and never falls, so it cannot show whether releasing the caches actually worked."""
    try:
        import psutil
        return f"  (rss {psutil.Process().memory_info().rss / 1e9:.1f} GB)"
    except Exception:                                          # noqa: BLE001
        return ""


def build_pool(recipe: Callable, codes=None, quiet: bool = False) -> pd.DataFrame:
    """Run `recipe` over every site and stack the frames, tagged with `site`.

    A site whose recipe returns nothing is SKIPPED with a note rather than raising: a cohort of hundreds should not be hostage to one site's missing covariate.
    """
    codes = list(codes if codes is not None else cohort.sites().snr_id)
    frames, skipped = [], []
    # PER-SITE CACHES ARE RELEASED AS WE GO. cohort memoizes records and basins so one site's blocks
    # share their reads, but across a 188-site pool those frames only accumulate -- measured at 6+ GB
    # by site 12, and an OOM kill of a background run reports as a silent exit 0. The pooled cross-site
    # matrix is deliberately NOT cleared: it is read by every site and rebuilding it per site is the
    # opposite trade.
    import gc
    for i, code in enumerate(codes, 1):
        try:
            d = recipe(code)
        except Exception as e:                                  # noqa: BLE001 -- one site must not sink the pool
            skipped.append((code, f"{type(e).__name__}: {e}"))
            continue
        if d is None or not len(d):
            skipped.append((code, "empty frame"))
            continue
        d = d.copy()
        d["site"] = code
        frames.append(d)
        cohort.record.cache_clear()
        cohort.basin.cache_clear()
        if i % 10 == 0:
            gc.collect()
        if not quiet and i % 10 == 0:
            print(f"  pooled {i}/{len(codes)} sites{_rss_note()}", flush=True)
    if skipped and not quiet:
        print(f"  skipped {len(skipped)} site(s); first: {skipped[:3]}")
    if not frames:
        raise SystemExit("no site produced a frame -- is the published store on disk?")
    pool = pd.concat(frames, ignore_index=False)
    pool.index.name = "date"
    return pool.reset_index()


def basin_families(codes) -> pd.Series:
    """code -> family id: sites travel together when one's basin contains the other's outlet reach.

    Union-find over the containment relation, so a chain of nested basins forms one family rather than a set of overlapping pairs.
    """
    outlets, sets = {}, {}
    for code in codes:
        wset = cohort.basin(code)
        row = cohort.sites().set_index("snr_id")
        outlets[code] = int(row.loc[code, "comid"]) if code in row.index and pd.notna(row.loc[code, "comid"]) else None
        sets[code] = set(wset.comid.tolist()) if len(wset) else set()
    parent = {c: c for c in codes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    codes = list(codes)
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            oa, ob = outlets.get(a), outlets.get(b)
            if (ob is not None and ob in sets[a]) or (oa is not None and oa in sets[b]):
                parent[find(a)] = find(b)
    return pd.Series({c: find(c) for c in codes})


# ---- folds -----------------------------------------------------------------------------------------

def folds_grouped(groups, n_splits: int):
    """(train_idx, test_idx) pairs from GroupKFold; rows sharing a group stay together."""
    groups = pd.Series(groups).to_numpy()
    n = min(n_splits, len(pd.unique(groups)))
    if n < 2:
        return
    yield from GroupKFold(n).split(np.zeros(len(groups)), groups=groups)


def _fold_models(X, y, folds, task, **kw):
    """Fit one model per fold; return (models, oof) with rows in no test set left NaN."""
    oof = np.full(len(y), np.nan)
    models = []
    for tr, te in folds:
        m = _model(task, **kw)
        m.fit(X.iloc[tr], y.iloc[tr])
        pred = m.predict_proba(X.iloc[te])[:, 1] if task == "clf" else m.predict(X.iloc[te])
        oof[te] = pred
        models.append(m)
    return models, oof


# ---- scoring ---------------------------------------------------------------------------------------

def _score(y, pred, task: str) -> dict:
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)
    ok = ~(np.isnan(y) | np.isnan(pred))
    y, pred = y[ok], pred[ok]
    if not len(y):
        keys = ("auc", "prauc", "brier", "base") if task == "clf" else ("rmse", "mae", "r2")
        return {k: np.nan for k in keys}
    if task == "clf":
        two = len(np.unique(y)) == 2
        return dict(auc=roc_auc_score(y, pred) if two else np.nan,
                    prauc=average_precision_score(y, pred) if two else np.nan,
                    brier=brier_score_loss(y, pred), base=float(y.mean()))
    return dict(rmse=float(np.sqrt(mean_squared_error(y, pred))),
                mae=float(mean_absolute_error(y, pred)), r2=float(r2_score(y, pred)))


def _per_site(y, pred, task: str) -> float:
    y, pred = np.asarray(y), np.asarray(pred)
    if task == "clf":
        return roc_auc_score(y, pred) if len(np.unique(y)) == 2 else np.nan
    return r2_score(y, pred) if (len(y) > 1 and np.std(y) > 0) else np.nan


def _decomposition(y, oof, site, task: str) -> dict:
    """between / within / macro decomposition: does the model rank SITES, or days within a site?"""
    ok = ~(np.isnan(np.asarray(y, dtype=float)) | np.isnan(np.asarray(oof, dtype=float)))
    if ok.sum() < 2:
        keys = ("between_rate_r2", "macro_auc") if task == "clf" else ("between_r2", "within_r2", "macro_r2")
        return {k: float("nan") for k in keys}
    tab = pd.DataFrame({"y": np.asarray(y)[ok], "p": np.asarray(oof)[ok], "g": np.asarray(site)[ok]})
    means = tab.groupby("g")[["y", "p"]].mean()
    per = tab.groupby("g").apply(lambda d: _per_site(d.y, d.p, task), include_groups=False)
    if task == "clf":
        return dict(between_rate_r2=r2_score(means.y, means.p), macro_auc=float(np.nanmedian(per)))
    sm = tab.groupby("g")["y"].transform("mean").to_numpy()
    pm = tab.groupby("g")["p"].transform("mean").to_numpy()
    return dict(between_r2=r2_score(means.y, means.p),
                within_r2=r2_score(tab.y.to_numpy() - sm, tab.p.to_numpy() - pm),
                macro_r2=float(np.nanmedian(per)))


# ---- the harness -----------------------------------------------------------------------------------

def cook_many(recipe: Callable, codes=None, task: str = "reg", n_splits: int = None,
              name: str = "run", log: bool = True, quiet: bool = False, **xgb_kw) -> dict:
    """Single-recipe pooled cross-site CV.

    Parameters
    ----------
    recipe : callable
        code -> frame whose FIRST column is the target.
    codes : Sequence[str], optional
        SNR codes; default the whole cohort.
    task : {'reg', 'clf'}, default 'reg'
    n_splits : int, optional
        GroupKFold splits; default config.N_SPLITS.
    name : str, default 'run'
        Recorded in the log.
    log : bool, default True
        Append the run to results/log.json.

    Returns
    -------
    dict
        The score columns, plus n_sites / n_families / n_rows / n_feat and the gain importance.
    """
    n_splits = n_splits or config.N_SPLITS
    t0 = time.time()
    pool = build_pool(recipe, codes, quiet=quiet)
    target_col = pool.columns[1] if pool.columns[0] == "date" else pool.columns[0]
    feat_cols = [c for c in pool.columns if c not in ("date", "site", target_col)]
    d = pool.dropna(subset=[target_col])
    X = d[feat_cols].astype("float32")
    y = d[target_col]
    site = d["site"]

    fam_map = basin_families(sorted(site.unique()))
    fam = site.map(fam_map)

    _, oof_site = _fold_models(X, y, folds_grouped(site, n_splits), task, **xgb_kw)
    models, oof_fam = _fold_models(X, y, folds_grouped(fam, n_splits), task, **xgb_kw)

    lofo = _score(y, oof_fam, task)
    loso = _score(y, oof_site, task)
    if task == "clf":
        # PRAUC_LIFT IS THE ONE THAT COMPARES ACROSS COHORTS. Average precision is bounded below by the
        # positive rate, so a cohort with fewer violation-days scores lower at IDENTICAL ranking skill --
        # this cohort's base rate is 0.130 against the predecessor's 0.233, which moves prauc by more than
        # any modelling change in the log. The ratio removes that, and AUC (base-rate free) is the check.
        lift = (lofo["prauc"] / lofo["base"]) if lofo["base"] else float("nan")
        out = dict(loso_auc=loso["auc"], lofo_prauc=lofo["prauc"], lofo_prauc_lift=lift,
                   lofo_auc=lofo["auc"], lofo_brier=lofo["brier"], base=lofo["base"])
    else:
        out = dict(loso_r2=loso["r2"], lofo_r2=lofo["r2"], lofo_rmse=lofo["rmse"],
                   lofo_mae=lofo["mae"])
    out.update({f"lofo_{k}": v for k, v in _decomposition(y, oof_fam, site, task).items()})
    out.update(n_sites=int(site.nunique()), n_families=int(fam.nunique()), n_rows=int(len(d)),
               n_feat=int(len(feat_cols)), fit_seconds=round(time.time() - t0, 1))

    gain = pd.Series(0.0, index=feat_cols)
    for m in models:
        b = pd.Series(m.get_booster().get_score(importance_type="gain"))
        gain = gain.add(b.reindex(feat_cols).fillna(0.0), fill_value=0.0)
    out_importance = (gain / max(len(models), 1)).sort_values(ascending=False)

    if log:
        _append_log(name, task, recipe, out, out_importance)
    return {**out, "importance": out_importance}


def _append_log(name, task, recipe, score, importance) -> None:
    config.ensure_dirs()
    p = config.RESULTS / "log.json"
    runs = json.loads(p.read_text()) if p.exists() else []
    runs.append(dict(
        timestamp=pd.Timestamp.now().isoformat(timespec="seconds"),
        name=name, task=task, recipe=getattr(recipe, "__name__", str(recipe)),
        cohort=dict(seed_box=config.SEED_BOX, site_types=list(config.SITE_TYPES),
                    min_nitrate_days=config.MIN_NITRATE_DAYS),
        geometry=dict(reg_edges=list(config.REG_EDGES), clf_edges=list(config.CLF_EDGES),
                      decay_lambda=config.DECAY_LAMBDA),
        score={k: (None if isinstance(v, float) and np.isnan(v) else v)
               for k, v in score.items()},
        importance={k: round(float(v), 4) for k, v in importance.head(40).items()},
    ))
    p.write_text(json.dumps(runs, indent=1, default=str))
