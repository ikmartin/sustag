"""run_exp.py -- the neighbour-count sweep: how many donors, encoded how, ranked how.

Crosses neighbour COUNT (1-4) x INDICATOR encoding (sign/flag/rednt) x SIMILARITY rule (area/dist) against the shipped island recipe and the two exp-33/36 donor blocks, over ONE pool per task so every delta is exactly paired. 27 arms per task; see arms.py for the grid and neighbors.py for the donor block.

ONE XGB CONFIG PER TASK, shared by every arm, read from xgb_config.json. That is deliberate and it is the difference between a readable result and exp 36's: tuning each arm separately would make a variant's win indistinguishable from a better config. The tuner writes the file once; nothing here retunes.

BESIDES THE SCOREBOARD, one row per (arm, site) lands in eval_<task>.csv -- per-site accuracy alongside that site's connectivity (donor count, basin area, family size and area, rank), which is what lets "does a 4th donor help" be asked separately of the 44% of sites that HAVE one. cook computes per-site scores inside its between/within decomposition and discards them, so they are recomputed here from the returned OOF vectors at zero extra fits.

    python run_exp.py --task clf                       # 27 arms, full cohort
    python run_exp.py --task both                      # both tasks
    python run_exp.py --task clf --sites 12 --limit 4  # fast smoke
    python run_exp.py --pool-only --task clf           # build+cache the pool for the tuner

Then: python render_results.py <run_id>   (or `latest`).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arms as A
from neighbors import nearest_neighbors
from src.data.access import get_basin_area, get_site_ids
from src.eval import cook
from src.features.features import neighbor_nitrate
from src.features.recipes import recipe_maker
from src.features.transformers import merge_on_date

_HERE = Path(__file__).resolve().parent
LOG_PATH = _HERE / "log.json"
CONFIG_PATH = _HERE / "xgb_config.json"
TARGET = {"reg": "nitrate_con", "clf": "violation"}
# The two exp-33/36 donor blocks, reproduced as they were logged: 33 is area-ranked on the immediate
# graph, 36 is distance-ranked on the full one. Their (select, immediate) pairing is deliberately NOT
# untangled -- these arms exist for continuity with the logged results, not to test an axis.
_BASE_SPEC = {"area": dict(select="area", immediate=True), "dist": dict(select="dist", immediate=False)}


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True, cwd=_HERE).strip()
    except Exception:
        return "unknown"


def make_wide_recipe(task: str):
    """site -> the shipped island frame + every donor column any arm needs + the target.

    Emits BOTH rankings and BOTH exp-33/36 blocks in one pass: the pool is the expensive part and the arms are column subsets of it. Every block is prefixed, because the two rankings put different sites in slot k and the two base blocks share encoding='both' column names outright.
    """
    shipped = recipe_maker(task, window=1)

    def wide_recipe(site):
        df = shipped(site)
        uid = site if isinstance(site, str) else site.site_uid
        frames, stats = [df], {}
        for rule, spec in _BASE_SPEC.items():
            f, s = neighbor_nitrate(uid, lags=A.LAGS, encoding="both", **spec)
            p = A.BASE_PREFIX[rule]
            frames.append(f.rename(columns={c: f"{p}{c}" for c in f.columns if c != "date"}))
            stats.update({f"{p}{k}": v for k, v in s.items()})
        for rule in A.RULES:
            f, s = nearest_neighbors(uid, n=max(A.N_SLOTS), select=rule, prefix=f"{rule}_", lags=A.LAGS)
            frames.append(f)
            stats.update(s)
        out = merge_on_date(frames, spine=pd.DatetimeIndex(df["date"]))
        for k, v in stats.items():
            out[k] = v  # static per site -> broadcast, as recipes._assemble does
        return out

    wide_recipe.__name__ = "wide_recipe"
    return wide_recipe


def _fold_table(pool: pd.DataFrame, eval_method: str, n_splits: int, max_holdout_pct: float) -> pd.DataFrame:
    """{site -> n_train_sites, n_test_sites} for `eval_method`, recomputed OUTSIDE the scorer.

    cook never exposes its fold assignment (it only prints a per-fold average), but folds_grouped/folds_true_lofo are deterministic given the same groups and n_splits, so rebuilding them on the same pool reproduces exactly what _score_pool used. Train counts come from the train index rather than by subtraction, which is the only correct route once a regime can leave a site in neither split.
    """
    site = pool["site"].to_numpy()
    fam = cook.basin_groups(pool["site"])
    if eval_method == "loso":
        folds = cook.folds_grouped(pool["site"], n_splits)
    elif eval_method == "true_lofo":
        folds = cook.folds_true_lofo(fam, max_holdout_pct)
    else:
        folds = cook.folds_grouped(fam, n_splits)
    rows = []
    for tr, te in folds:
        n_tr, te_sites = len(np.unique(site[tr])), np.unique(site[te])
        rows += [{"site": s, "num_sites_training": n_tr, "num_sites_testing": len(te_sites)} for s in te_sites]
    return pd.DataFrame(rows, columns=["site", "num_sites_training", "num_sites_testing"])


def _site_context(pool: pd.DataFrame, task: str) -> pd.DataFrame:
    """Per-site connectivity and standing: basin area, donor count, family size/area, and the two ranks.

    family_area is the basin area of the family's MAX-AREA member, which was verified to strictly enclose every other member in all 11 non-singleton families -- so it is the family's drainage, not a sum over overlapping basins. Ranks are cohort-wide over each site's whole record, 1 = highest.
    """
    fam = pd.Series(cook.basin_groups(pool["site"]).to_numpy(), index=pool.index)
    y = cook._target(pool, TARGET[task], task)
    per = pd.DataFrame({"site": pool["site"].to_numpy(), "family": fam.to_numpy(), "y": np.asarray(y)})
    g = per.groupby("site", sort=False)
    ctx = pd.DataFrame({"site": list(g.groups), "family": g["family"].first().to_numpy(), "mean_y": g["y"].mean().to_numpy()})
    ctx["basin_area"] = [get_basin_area(s) for s in ctx["site"]]
    cnt_col = "area_num_neighbors"  # rule-independent: the reachable-set size, not the ranking
    ctx["num_neighbors"] = pool.groupby("site", sort=False)[cnt_col].first().reindex(ctx["site"]).to_numpy()
    fam_n = ctx.groupby("family")["site"].transform("size")
    fam_a = ctx.groupby("family")["basin_area"].transform("max")
    ctx["family_n_sites"], ctx["family_area"] = fam_n, fam_a
    ctx["rank_by_nitrate"] = ctx["mean_y"].rank(ascending=False, method="min").astype(int)
    ctx["rank_by_area"] = ctx["basin_area"].rank(ascending=False, method="min").astype(int)
    return ctx.drop(columns="mean_y")


def _per_site_rows(oof: pd.DataFrame, task: str, eval_method: str) -> pd.DataFrame:
    """Per-site scores off the returned OOF vectors -- a recomputation, never a refit.

    _per_site_score is the exact function cook._decomposition applies per site and then throws away; the rest come from cook._score on the site's own row slice. `oof_family` is the LOFO prediction and `oof_site` the LOSO one, so both regimes come out of the fits already paid for.
    """
    col = {"loso": "oof_site"}.get(eval_method, "oof_family")
    rows = []
    for s, d in oof.groupby("site", sort=False):
        ok = d[col].notna() & d["y"].notna()
        if not ok.any():
            rows.append({"site": s, "n_rows": 0})
            continue
        yy, pp = d.loc[ok, "y"].to_numpy(), d.loc[ok, col].to_numpy()
        r = {"site": s, "n_rows": int(ok.sum()), "mean_y": float(yy.mean()), "mean_pred": float(pp.mean())}
        r["site_score"] = cook._per_site_score(yy, pp, task)  # R2 for reg, AUC for clf
        try:
            r.update({k: float(v) for k, v in cook._score(yy, pp, task).items()})
        except Exception:
            pass  # a single-class or constant site cannot yield the full suite; site_score already reports NaN
        rows.append(r)
    return pd.DataFrame(rows)


def _load_xgb(task: str) -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"{CONFIG_PATH} is missing. Every arm in a task fits at ONE config so the deltas stay paired -- "
            f"run the tuner once and write {{'reg': {{...}}, 'clf': {{...}}}} before scoring."
        )
    cfg = json.loads(CONFIG_PATH.read_text())
    if task not in cfg:
        raise SystemExit(f"{CONFIG_PATH} has no '{task}' config (keys: {sorted(k for k in cfg if not k.startswith('_'))}).")
    return {k: v for k, v in cfg[task].items() if not k.startswith("_")}


def _new_run_id(path: Path) -> str:
    rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    seen = set()
    if path.exists() and path.stat().st_size > 0:
        seen = {e.get("run_id") for e in json.loads(path.read_text()).values()}
    if rid not in seen:
        return rid
    k = 1
    while f"{rid}-{k}" in seen:
        k += 1
    return f"{rid}-{k}"


def _to_native(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON-serializable: {type(o).__name__}")


def run(task, sites, xgb, log_path, eval_methods, n_splits, true_lofo, max_holdout_pct, limit=None):
    """Score every arm for `task` over one pool; append to log_path and write eval_<task>.csv."""
    run_id = _new_run_id(log_path)
    wide = make_wide_recipe(task)
    pool = cook._pool_wide(wide, sites, TARGET[task], progress_label=f"{task} wide")
    masks = A.build_masks(pool, task)  # raises if any donor column failed to materialise
    if limit:
        keep = list(masks)[: limit + 1]  # baseline + the first `limit` arms
        masks = {k: masks[k] for k in keep}
    print(f"run_id: {run_id}  ({task}, {len(masks)} arms, {pool['site'].nunique()} sites, {len(pool):,} rows)")

    ctx = _site_context(pool, task)
    folds = {m: _fold_table(pool, m, n_splits, max_holdout_pct) for m in eval_methods}
    want_loso = "loso" in eval_methods

    log = json.loads(log_path.read_text()) if log_path.exists() and log_path.stat().st_size > 0 else {}
    key = max((int(k) for k in log), default=-1) + 1
    eval_rows = []
    for i, (name, cols) in enumerate(masks.items(), 1):
        r = cook._score_pool(
            pool, cols, TARGET[task], task, n_splits,
            return_oof=True, loso=want_loso, true_lofo=true_lofo, max_holdout_pct=max_holdout_pct,
            extra_importance_test=False, label=name, **xgb,
        )
        oof = r.pop("oof")
        imp = r.pop("importance", None)
        r.pop("importance_perm", None)
        r.pop("operating_points", None)
        head = "lofo_r2" if task == "reg" else "lofo_prauc"
        print(f"  [{i}/{len(masks)}] {name:<34} {head}={r.get(head, float('nan')):.4f}  n_feat={r.get('n_feat')}")
        log[str(key)] = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_id": run_id, "git_sha": _git_sha(), "task": task, "name": name,
            "n_cols_donor": len(cols) - len(masks[next(iter(masks))]),
            "n_sites_requested": len(sites), "true_lofo": bool(true_lofo),
            "eval_methods": list(eval_methods), "xgb": dict(xgb),
            "score": {k: v for k, v in r.items()},
            "importance": imp.to_dict() if imp is not None else {},
        }
        key += 1
        for m in eval_methods:
            rows = _per_site_rows(oof, task, m)
            rows.insert(0, "recipe", name)
            rows["task"], rows["eval_method"] = task, m
            eval_rows.append(rows.merge(folds[m], on="site", how="left").merge(ctx, on="site", how="left"))

    log_path.write_text(json.dumps(log, indent=2, default=_to_native))
    out = pd.concat(eval_rows, ignore_index=True)
    ev = log_path.parent / f"eval_{task}.csv"
    out.to_csv(ev, index=False)
    print(f"logged {len(masks)} arms -> {log_path.name};  {len(out):,} per-site rows -> {ev.name}")
    return run_id


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=["reg", "clf", "both"], default="both")
    ap.add_argument("--sites", type=int, default=None, help="cap to the first N sites (smoke only)")
    ap.add_argument("--limit", type=int, default=None, help="score only the baseline + K arms (smoke)")
    ap.add_argument("--log", type=Path, default=LOG_PATH, help="append here instead of log.json")
    ap.add_argument("--eval-methods", default="lofo", help="comma-separated: lofo,loso,true_lofo. loso costs a full second CV pass")
    ap.add_argument("--n-splits", type=int, default=5)
    ap.add_argument("--true_lofo", action="store_true", help="one family per fold instead of GroupKFold")
    ap.add_argument("--max_holdout_pct", type=float, default=0.2)
    ap.add_argument("--pool-only", action="store_true", help="build the pool and report its shape, then stop (for the tuner)")
    a = ap.parse_args()

    sites = [str(s) for s in get_site_ids()]
    if a.sites:
        sites = sites[: a.sites]
    tasks = ["reg", "clf"] if a.task == "both" else [a.task]
    methods = [m.strip() for m in a.eval_methods.split(",") if m.strip()]

    for task in tasks:
        if a.pool_only:
            pool = cook._pool_wide(make_wide_recipe(task), sites, TARGET[task], progress_label=f"{task} wide")
            masks = A.build_masks(pool, task)
            print(f"{task}: pool {pool.shape}, {len(masks)} arms, donor cols {len(A.donor_cols())}")
            continue
        rid = run(task, sites, _load_xgb(task), a.log, methods, a.n_splits, a.true_lofo, a.max_holdout_pct, a.limit)
        print(f"\ndone. render with:  python render_results.py {rid}")


if __name__ == "__main__":
    main()
