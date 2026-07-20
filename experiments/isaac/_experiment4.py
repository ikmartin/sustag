"""
Find GLOBAL feature-construction hyper-parameters for daily nitrate regression.
================================================================================

LAM, EDGES and VEL are *feature-construction* knobs, not model knobs:

  EDGES  distance-bucket cut points (m). How weather / crops / surplus are binned by
         cell distance to the sensor. edges=[] -> one whole-basin aggregate.
  LAM    exp-decay length (m) for crop / surplus weighting (_exp_curry): larger LAM =
         flatter weighting that counts far cells almost as much as near ones.
  VEL    water celerity (m/s). Sets each bucket's travel-time lag = median cell
         distance / (VEL * 86400) days, applied to the weather features.

We want ONE global setting that works across sites, so we optimise them on the POOLED
cross-site daily-nitrate regression (Recipe A + static descriptors) -- the setting where
weather/land-use actually carry signal. The objective is leave-sites-out R2 (GroupKFold
by site): stable enough to rank configs. We also report the honest leave-basins-out R2
(GroupKFold by data/splits.py conflict component) on the winning config.

Because each config requires REBUILDING the pooled dataset (these knobs change the
features themselves), a full grid is expensive. We use coordinate descent -- sweep EDGES,
then LAM at the best EDGES, then VEL -- which also exposes each knob's marginal effect.
This assumes near-separability; the knobs touch different feature groups, so that holds
roughly. Run:  python isaac/_experiment4.py
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
import xgboost as xgb
from sklearn.model_selection import GroupKFold
from sklearn.metrics import r2_score

from data.access import get_site_ids
from data.features import (agg_crops, agg_surplus, agg_weather_w_lag, daily_nitrate,
                           doy_climatology_pure_signal, site_static)
from data.transforms import flatten_buckets, merge_on_date
from data.splits import split_groups

TCOL = "nitrate_con"
MIN_DAYS = 1500          # longer records -> steadier estimates
N_SITES = 35             # subset for a tractable search
N_SPLITS = 5

# search space (coordinate descent starts from these defaults)
EDGES_CANDIDATES = [[], [100_000], [50_000, 150_000], [25_000, 75_000, 150_000]]
LAM_CANDIDATES = [5_000, 10_000, 25_000, 50_000]
VEL_CANDIDATES = [0.3, 0.8, 1.5, 3.0]
DEFAULTS = dict(edges=[50_000, 150_000], lam=10_000, vel=0.8)

# fixed, moderately-regularized model (we tune FEATURES here, not the model)
CFG = dict(n_estimators=600, learning_rate=0.05, max_depth=4, min_child_weight=20,
           subsample=0.8, colsample_bytree=0.8, reg_lambda=5.0, random_state=42, eval_metric="rmse")

_BASIN_GROUP = split_groups()


def _edges_label(edges):
    return "[]" if not edges else "[" + ",".join(f"{e//1000}k" for e in edges) + "]"


def build_pool(sites, edges, lam, vel):
    """Pooled Recipe A (+ static) built with the given (edges, lam, vel)."""
    frames = []
    for uid in sites:
        try:
            weather = flatten_buckets(agg_weather_w_lag(uid, edges=edges, water_velocity=vel))
            crops = flatten_buckets(agg_crops(uid, edges=edges, lam=lam, exp=True))
            surplus = flatten_buckets(agg_surplus(uid, edges=edges, lam=lam, exp=True))
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


def grouped_r2(pool, by="site"):
    """Out-of-fold R2 of pooled daily-nitrate regression, grouped by site or basin."""
    feat = [c for c in pool.columns if c not in (TCOL, "site", "date")]
    X, y = pool[feat], pool[TCOL]
    if by == "site":
        groups = pool["site"]
    else:  # basin conflict component; missing sites -> singletons
        nxt = max(_BASIN_GROUP.values(), default=-1) + 1
        m = {}
        for s in pd.unique(pool["site"]):
            m[s] = _BASIN_GROUP.get(s, nxt); nxt += (s not in _BASIN_GROUP)
        groups = pool["site"].map(m)
    oof = np.full(len(y), np.nan)
    for tr, te in GroupKFold(N_SPLITS).split(X, y, groups=groups):
        model = xgb.XGBRegressor(**CFG)
        model.fit(X.iloc[tr], y.iloc[tr], verbose=False)
        oof[te] = model.predict(X.iloc[te])
    return r2_score(y, oof)


def score(sites, edges, lam, vel):
    return grouped_r2(build_pool(sites, edges, lam, vel), by="site")


def sweep(sites, param, candidates, current):
    """Try each candidate value of `param` (others fixed at `current`); return the best."""
    print(f"\n── sweep {param} (others fixed: "
          f"{ {k: _edges_label(v) if k=='edges' else v for k, v in current.items() if k != param} }) ──")
    results = []
    for val in candidates:
        cfg = {**current, param: val}
        r2 = score(sites, cfg["edges"], cfg["lam"], cfg["vel"])
        label = _edges_label(val) if param == "edges" else val
        results.append((val, r2))
        print(f"   {param}={str(label):<22s} LOSO_R2={r2:.4f}")
    best_val, best_r2 = max(results, key=lambda t: t[1])
    print(f"   -> best {param} = {_edges_label(best_val) if param=='edges' else best_val}  (R2={best_r2:.4f})")
    return best_val


def main():
    sites = []
    for uid in get_site_ids():
        try:
            if daily_nitrate(uid).dropna().shape[0] >= MIN_DAYS:
                sites.append(uid)
        except Exception:
            pass
    sites = sorted(sites)[:N_SITES]
    print(f"using {len(sites)} sites (>= {MIN_DAYS} daily obs)")

    cur = dict(DEFAULTS)
    base_r2 = score(sites, cur["edges"], cur["lam"], cur["vel"])
    print(f"baseline {{'edges': {_edges_label(cur['edges'])}, 'lam': {cur['lam']}, 'vel': {cur['vel']}}}: LOSO_R2={base_r2:.4f}")

    # coordinate descent: edges -> lam -> vel
    cur["edges"] = sweep(sites, "edges", EDGES_CANDIDATES, cur)
    cur["lam"] = sweep(sites, "lam", LAM_CANDIDATES, cur)
    cur["vel"] = sweep(sites, "vel", VEL_CANDIDATES, cur)

    # final: report winner on both site- and basin-grouped CV
    pool = build_pool(sites, cur["edges"], cur["lam"], cur["vel"])
    loso = grouped_r2(pool, by="site")
    lobo = grouped_r2(pool, by="basin")
    print("\n" + "=" * 60)
    print(f"BEST GLOBAL: edges={_edges_label(cur['edges'])}  lam={cur['lam']}  vel={cur['vel']}")
    print(f"  baseline LOSO_R2={base_r2:.4f}  ->  tuned LOSO_R2={loso:.4f}  (honest LOBO_R2={lobo:.4f})")


if __name__ == "__main__":
    main()
