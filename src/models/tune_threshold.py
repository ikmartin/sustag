"""Post-hoc class-imbalance threshold tuning for an ALREADY-TRAINED violation classifier.

The deployed booster is frozen -- it emits P(violation) per day and is never retrained. "Tuning"
here just picks the decision cutoff tau via an F-beta operating point: beta is the recall/precision
emphasis (beta=2 => recall weighted 4x precision => catch more violations, tolerate more false
alarms). For a grid of beta we find tau maximising F_beta on the honest LOFO out-of-fold
predictions, and record the operating point there -- recall (catch rate) and FDR (false-discovery
rate = 1 - precision = the share of our alarms that are false), the two numbers that measure real
usefulness. The whole table is patched into the model's <path>.meta.json; the booster file is left
byte-for-byte unchanged. The widget reads the table and lets the user dial beta live.

FDR is prevalence-dependent, so we also store the pooled base_rate -- the widget reports FDR as
"expected at ~base-rate prevalence".

This is deliberately self-contained (NOT wired into cook.py's CV / metrics path): it reuses cook's
OOF helpers read-only and rebuilds the honest LOFO OOF for the recipe, so it can be re-run against a
deployed model any time to change its operating point without retraining a single tree.

Usage
-----
    python -m src.models.tune_threshold isaac_CLF2                    # recipe_CLF, patch deploy meta
    python -m src.models.tune_threshold isaac_CLF2 --recipe recipe_CLF --n-splits 5
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_curve

_ROOT = Path(__file__).resolve().parents[2]  # repo root/
sys.path.insert(0, str(_ROOT))

from src.eval.cook import _pool, _features, _target, basin_groups, _grouped_oof
from src.features.recipes import recipe_CLF
from src.data.access import get_site_ids
from src.models.train import REAL_XGB_CLF

# beta grid == the widget slider values (0.5..4.0 step 0.5); the slider snaps to these, so the
# widget looks each up exactly -- no interpolation.
BETA = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]

_RECIPES = {"recipe_CLF": recipe_CLF}


def _beta_operating_points(y, oof, betas=BETA) -> list[dict]:
    """For each beta, the threshold tau maximising F_beta on (y, oof), with the honest operating
    point at that tau: recall (TP/(TP+FN)), precision (TP/(TP+FP)), and fdr (= 1 - precision, the
    share of alarms that are false). One precision_recall_curve sweep drives all betas."""
    y = np.asarray(y, dtype=float)
    oof = np.asarray(oof, dtype=float)
    ok = ~(np.isnan(y) | np.isnan(oof))
    y, oof = y[ok], oof[ok]
    if len(np.unique(y)) < 2:
        raise ValueError("Need both classes present in the OOF predictions to tune a threshold.")

    # precision_recall_curve returns prec/rec of length n+1; the final point (recall 0, precision 1)
    # has no threshold, so drop it to align prec/rec with the n thresholds (cf. cook._best_f1).
    prec, rec, thr = precision_recall_curve(y, oof)
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
                "fdr": float(1.0 - P[i]),  # false-discovery rate = share of our alarms that are false
            }
        )
    return rows


def build_beta_table(recipe, sites=None, n_splits: int = 5, min_rows: int = 500, xgb: dict = None):
    """Rebuild the honest LOFO out-of-fold predictions for `recipe` (same pooling + grouping the CV
    used) and return (beta_table, base_rate). xgb defaults to REAL_XGB_CLF so the OOF probability
    scale matches the deployed model."""
    xgb = REAL_XGB_CLF if xgb is None else xgb
    target = "violation"
    pool = _pool(recipe, sites or get_site_ids(), target, min_rows=min_rows, progress_label=getattr(recipe, "__name__", "recipe"))
    feat = _features(pool, target)
    X, y = pool[feat], _target(pool, target, "clf")
    groups = basin_groups(pool["site"])  # LOFO: hold out whole basin families
    oof = _grouped_oof(X, y, groups, "clf", n_splits, **xgb)
    return _beta_operating_points(np.asarray(y), oof), float(np.asarray(y, dtype=float).mean())


def _meta_paths(model: str) -> list[Path]:
    """The <path>.meta.json sidecars to patch for a model stem: the deploy copy (what the widget
    loads) and, if present, the src/models/models training copy."""
    candidates = [
        _ROOT / "deploy" / "models" / f"{model}.json.meta.json",
        Path(__file__).resolve().parent / "models" / f"{model}.json.meta.json",
    ]
    return [p for p in candidates if p.parent.exists()]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model", help="Model stem to patch, e.g. isaac_CLF2 (patches <stem>.json.meta.json).")
    p.add_argument("--recipe", default="recipe_CLF", choices=list(_RECIPES), help="Recipe the model was trained on.")
    p.add_argument("--n-splits", type=int, default=5, help="GroupKFold folds for the LOFO OOF (match training).")
    p.add_argument("--min-rows", type=int, default=500, help="Per-site inclusion floor (match training).")
    args = p.parse_args()

    print(f"Rebuilding honest LOFO OOF for {args.recipe} (this runs a {args.n_splits}-fold grouped CV)...")
    beta_table, base_rate = build_beta_table(_RECIPES[args.recipe], n_splits=args.n_splits, min_rows=args.min_rows)

    print(f"\nbase rate (pooled violation prevalence): {base_rate:.3f}\n")
    print(pd.DataFrame(beta_table).round(4).to_string(index=False))

    metas = _meta_paths(args.model)
    if not metas:
        raise FileNotFoundError(f"No {args.model}.json.meta.json under deploy/models or src/models/models.")
    for path in metas:
        meta = json.loads(path.read_text()) if path.exists() else {}
        meta["beta_table"] = beta_table
        meta["base_rate"] = base_rate
        path.write_text(json.dumps(meta, indent=2))
        print(f"\npatched {path.relative_to(_ROOT)} (booster file untouched)")


if __name__ == "__main__":
    main()
