"""Fit the regionalised discharge model and score it on basins it has never seen.

WHAT THE NUMBER MEANS. The headline is the MEDIAN PER-SITE NSE over held-out sites, which is the same quantity the NWM audit reported on 19 of our own reaches: NSE 0.35, KGE 0.646, log-NSE 0.521, bias 1.023, r 0.701. If this model reaches that from data already on disk, NWM's gaged-reach skill is not worth a 26-76 GB acquisition, because gaged skill is a CEILING on ungauged skill and NWM would be paying to extend a covariate into the regime where it is weakest.

WHAT IT CANNOT MEAN. A high score here does not prove NWM is unnecessary either -- our own model is also being scored on gauged reaches, and it faces the same ceiling. This experiment can produce a clean NO and never a clean YES, which is why a positive result opens a separate project rather than an acquisition.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

import cohort                                                      # noqa: E402
import config                                                      # noqa: E402
import cv                                                          # noqa: E402
import features                                                    # noqa: E402


def _model(**kw):
    from xgboost import XGBRegressor

    p = dict(n_estimators=600, max_depth=8, learning_rate=0.05, subsample=0.8,
             colsample_bytree=0.8, min_child_weight=20, n_jobs=-1, tree_method="hist",
             random_state=20260827)
    p.update(kw)
    return XGBRegressor(**p)


def main(limit: int | None = None, stride: int | None = None, name: str = "run") -> dict:
    config.ensure_dirs()
    t0 = time.time()
    s = cohort.sites()
    if limit:
        rng = np.random.default_rng(20260827)
        s = s.iloc[np.sort(rng.choice(len(s), min(limit, len(s)), replace=False))].reset_index(drop=True)
    print(f"cohort: {len(s):,} sites", flush=True)
    s = features.plausible(s)

    fam = cv.basin_families(s)
    print(f"basin families: {fam.nunique():,} over {len(s):,} sites "
          f"({time.time()-t0:.0f}s)", flush=True)

    pool = features.build_pool(s, stride=stride)
    if not len(pool):
        raise SystemExit("empty pool")
    feat = features.feature_columns(pool)
    pool["family"] = pool.site.map(fam)
    print(f"pool: {len(pool):,} rows x {len(feat)} features ({time.time()-t0:.0f}s)", flush=True)

    X = pool[feat].astype("float32")
    y = pool.y.to_numpy("float64")
    oof = np.full(len(pool), np.nan)
    smear = np.full(len(pool), 1.0)
    for k, (tr, te) in enumerate(cv.folds(pool.family), 1):
        m = _model()
        m.fit(X.iloc[tr], y[tr])
        oof[te] = m.predict(X.iloc[te])
        # DUAN'S SMEARING ESTIMATOR. Fitting in log space and exponentiating returns the conditional
        # MEDIAN, not the mean, and for a right-skewed variable that is biased low -- measured at 0.76 of
        # observed before this correction, which also dragged KGE down since it penalises the mean and
        # variance ratios directly. The factor is the mean of exp(residual), and it is computed on the
        # TRAINING fold only: taking it from the test fold would leak the answer into its own correction.
        resid = y[tr] - m.predict(X.iloc[tr])
        smear[te] = float(np.mean(np.exp(resid)))
        print(f"  fold {k}: train {len(tr):,} / test {len(te):,} "
              f"smear {smear[te][0]:.3f} ({time.time()-t0:.0f}s)", flush=True)

    got = pd.DataFrame({"site": pool.site, "family": pool.family,
                        "obs_mm": pool.q_mm.to_numpy(),
                        "pred_mm": np.exp(oof) * smear - config.EPS_MM})
    got = got[np.isfinite(got.pred_mm)]
    pooled = cv.score(got.obs_mm, got.pred_mm)
    ps = cv.per_site(got)
    med = {f"median_{k}": float(ps[k].median()) for k in ("nse", "kge", "log_nse", "bias", "r")}
    out = dict(name=name, n_sites=int(len(s)), n_families=int(fam.nunique()), n_rows=int(len(pool)),
               n_features=len(feat), pooled=pooled, **med,
               frac_sites_nse_above_0=float((ps.nse > 0).mean()),
               minutes=round((time.time() - t0) / 60, 1))
    print(json.dumps(out, indent=1, default=float), flush=True)
    (config.RESULTS / f"{name}.json").write_text(json.dumps(out, indent=1, default=float))
    ps.to_parquet(config.RESULTS / f"{name}_per_site.parquet", index=False)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--stride", type=int)
    ap.add_argument("--name", default="run")
    a = ap.parse_args()
    main(limit=a.limit, stride=a.stride, name=a.name)
