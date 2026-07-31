"""Solve for a decision threshold on an ALREADY-TRAINED violation classifier, without retraining a tree.

The deployed booster is frozen -- it emits P(violation) per day -- so "tuning" here only picks the cutoff tau. Two ways to ask for one:

    --target-recall 0.9    the OPERATIONAL question: what tau catches 90% of violations, and what does that cost in false alarms?
    --target-fdr 0.3       the inverse: the most violations catchable while keeping <=30% of alarms false.

Both are solved EXACTLY off the precision-recall curve. That is the reason this script still exists. train.build already writes a `beta_table` -- 14 F-beta operating points -- into every classifier's sidecar from the CV it just ran, at no extra cost, so rebuilding that table here duplicates work. But the beta grid answers only at its 14 points and deploy.predict.threshold_for_beta SNAPS to the nearest, and no beta corresponds to "90% recall". A target is a different question from a grid.

Rebuilding the table is still offered (--beta-table), for the case the sidecar lost it or the cohort moved under a deployed model. It costs a full pool plus one grouped CV.

FDR is prevalence-dependent, so the pooled base_rate is reported and stored alongside -- read every FDR as "expected at ~base-rate prevalence".

Usage
-----
    python -m src.models.tune_threshold isaac_CLF2 --target-recall 0.9
    python -m src.models.tune_threshold isaac_CLF2 --target-fdr 0.25 --recipe island_CLF
    python -m src.models.tune_threshold isaac_CLF2 --beta-table --write     # rebuild + patch the sidecar
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

from src.eval.cook import _pool_wide, _features, _target, basin_groups, _fold_models, _fold_oof, folds_grouped, BETA_GRID, beta_operating_points
from src.features.recipes import island_CLF, network_CLF
from src.data.access import get_site_ids
from src.models.train import xgb_for, _tuned_iters, UntunedRecipe

BETA = BETA_GRID  # the widget's slider values; cook owns the grid so train-time and post-hoc tables agree

_RECIPES = {"island_CLF": island_CLF, "network_CLF": network_CLF}


def _lofo_oof(recipe, sites=None, n_splits: int = 5, xgb: dict = None):
    """Honest LOFO out-of-fold P(violation) for `recipe` -- same pooling and family grouping the CV used. -> (y, oof).

    xgb defaults to the recipe's tuned config so the OOF probability scale matches the deployed model. An untuned recipe raises rather than defaulting: without a tree count the fold models would inherit cook's ceiling and every threshold below would be solved against a booster nobody deployed.
    """
    xgb = xgb_for(recipe, "clf") if xgb is None else xgb
    _tuned_iters(xgb, getattr(recipe, "__name__", str(recipe)), "clf")  # raises UntunedRecipe if the count is missing
    target = "violation"
    pool = _pool_wide(recipe, sites or get_site_ids(), target, progress_label=getattr(recipe, "__name__", "recipe"))
    feat = _features(pool, target)
    X, y = pool[feat], _target(pool, target, "clf")
    groups = basin_groups(pool["site"])  # LOFO: hold out whole basin families
    models = [(m, te) for te, m in _fold_models(X, y, folds_grouped(groups, n_splits), "clf", **xgb)]
    return np.asarray(y), _fold_oof(models, X, "clf", len(y))


def build_beta_table(recipe, sites=None, n_splits: int = 5, xgb: dict = None):
    """(beta_table, base_rate) over cook's BETA_GRID, rebuilt from a fresh LOFO OOF.

    Duplicates what train.build already writes into the sidecar; kept for a deployed model whose table was lost or whose cohort has moved. Costs a full pool plus one grouped CV.
    """
    y, oof = _lofo_oof(recipe, sites, n_splits, xgb)
    return beta_operating_points(y, oof), float(y.astype(float).mean())


def solve_operating_point(y, oof, *, target_recall=None, target_fdr=None):
    """The exact threshold meeting a recall or FDR target. -> dict(tau, recall, precision, fdr, alarm_rate) or None if unreachable.

    Read straight off precision_recall_curve, NOT snapped to BETA_GRID: the grid has 14 points and none of them means "90% recall", so the answer to a target question is only ever as precise as the nearest rung. Here it is exact.

    Among the thresholds that satisfy the constraint, the one that maximises the OTHER quantity wins -- best precision subject to a recall floor, or best recall subject to an FDR ceiling. Chosen by argmax over the satisfying set rather than by taking an endpoint, because neither curve is monotone in tau on real out-of-fold predictions, and an endpoint rule silently returns a dominated point when they wobble.
    """
    if (target_recall is None) == (target_fdr is None):
        raise ValueError("pass exactly one of target_recall / target_fdr")
    prec, rec, thr = precision_recall_curve(y, oof)
    prec, rec = prec[:-1], rec[:-1]  # sklearn appends (precision=1, recall=0) with no corresponding threshold
    ok = np.flatnonzero(rec >= target_recall) if target_recall is not None else np.flatnonzero(prec >= 1.0 - target_fdr)
    if not ok.size:
        return None
    i = ok[np.argmax(prec[ok])] if target_recall is not None else ok[np.argmax(rec[ok])]
    return {
        "tau": float(thr[i]),
        "recall": float(rec[i]),
        "precision": float(prec[i]),
        "fdr": float(1.0 - prec[i]),
        "alarm_rate": float((oof >= thr[i]).mean()),  # share of days flagged -- what the operator actually receives
    }


def _meta_paths(model: str) -> list[Path]:
    """The <path>.meta.json sidecars to patch for a model stem: the deploy copy (what the widget loads) and, if present, the src/models/models training copy."""
    candidates = [
        _ROOT / "deploy" / "models" / f"{model}.json.meta.json",
        Path(__file__).resolve().parent / "models" / f"{model}.json.meta.json",
    ]
    # the FILE must exist, not just its directory: patching a path with no sidecar would write one containing only the threshold fields, losing `feat` and making the model unloadable
    return [p for p in candidates if p.exists()]


def _rel(path: Path) -> str:
    """Repo-relative display path, falling back to the absolute one. Bare relative_to raises for anything outside _ROOT -- and it was being called while BUILDING an error message, so the diagnostic replaced the diagnosis."""
    try:
        return str(path.relative_to(_ROOT))
    except ValueError:
        return str(path)


def _check_recipe(metas, recipe_name) -> None:
    """Refuse a model/recipe pair the sidecars contradict.

    `model` and `--recipe` are independent arguments, and with two classifiers sharing one target nothing about the stem reveals which feature set the weights were fitted on. Unchecked, `tune_threshold network_CLF2 --recipe island_CLF` solves thresholds on ISLAND out-of-fold predictions and writes them into the NETWORK model's sidecar -- a wrong operating point with no error anywhere. save_model records `recipe` for exactly this; a sidecar predating that field can only be warned about.
    """
    for path in metas:
        got = json.loads(path.read_text()).get("recipe")
        if got is None:
            print(f"  [warn] {path.name} predates the `recipe` field -- cannot verify it was trained on {recipe_name}.")
        elif got != recipe_name:
            raise SystemExit(
                f"\nRECIPE MISMATCH: {_rel(path)} was trained on {got!r}, but --recipe says {recipe_name!r}.\n"
                f"  Re-run with --recipe {got}, or point at the right model."
            )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model", help="Model stem, e.g. isaac_CLF2 (reads/patches <stem>.json.meta.json).")
    p.add_argument("--recipe", default="island_CLF", choices=list(_RECIPES), help="Recipe the model was trained on. Verified against the sidecar's own `recipe` field.")
    p.add_argument("--target-recall", type=float, default=None, metavar="R", help="Solve for the threshold catching at least this share of violations, at the best precision available there.")
    p.add_argument("--target-fdr", type=float, default=None, metavar="F", help="Solve for the threshold catching the most violations while keeping at most this share of alarms false.")
    p.add_argument("--beta-table", action="store_true", help="Also rebuild the full F-beta operating-point table (train.build already writes one; this is for a sidecar that lost it).")
    p.add_argument("--write", action="store_true", help="Patch the results into the sidecar(s). Without it nothing is written -- solving a threshold is a question, not a deployment.")
    p.add_argument("--n-splits", type=int, default=5, help="GroupKFold folds for the LOFO OOF (match training).")
    args = p.parse_args()

    if args.target_recall is None and args.target_fdr is None and not args.beta_table:
        p.error("nothing to do: pass --target-recall, --target-fdr, and/or --beta-table")
    if args.target_recall is not None and args.target_fdr is not None:
        p.error("--target-recall and --target-fdr are alternative questions; pass one")
    for flag, v in (("--target-recall", args.target_recall), ("--target-fdr", args.target_fdr)):
        if v is not None and not 0.0 < v < 1.0:
            p.error(f"{flag} must be in (0, 1)")

    # Sidecars resolved and CHECKED before the CV, not after: the pool plus a grouped CV is minutes, and a mismatched recipe or a missing sidecar is knowable in milliseconds.
    metas = _meta_paths(args.model)
    if not metas:
        raise FileNotFoundError(f"No {args.model}.json.meta.json under deploy/models or src/models/models.")
    _check_recipe(metas, args.recipe)

    print(f"Rebuilding honest LOFO OOF for {args.recipe} (this runs a {args.n_splits}-fold grouped CV)...")
    try:
        y, oof = _lofo_oof(_RECIPES[args.recipe], n_splits=args.n_splits)
    except UntunedRecipe as e:
        raise SystemExit(f"\n{e}")
    base_rate = float(y.astype(float).mean())
    print(f"\nbase rate (pooled violation prevalence): {base_rate:.3f}")

    patch = {}
    if args.target_recall is not None or args.target_fdr is not None:
        op = solve_operating_point(y, oof, target_recall=args.target_recall, target_fdr=args.target_fdr)
        want = f"recall >= {args.target_recall:.0%}" if args.target_recall is not None else f"FDR <= {args.target_fdr:.0%}"
        if op is None:
            print(f"\n  {want} is UNREACHABLE at any threshold on these out-of-fold predictions.")
        else:
            print(f"\n  {want}  ->  tau = {op['tau']:.4f}")
            print(f"     recall  {op['recall']:.3f}   precision {op['precision']:.3f}   FDR {op['fdr']:.3f}"
                  f"   flags {op['alarm_rate']:.1%} of days")
            patch["target_operating_point"] = {"request": want, **op}

    if args.beta_table:
        table = beta_operating_points(y, oof)
        print()
        print(pd.DataFrame(table).round(4).to_string(index=False))
        patch |= {"beta_table": table, "base_rate": base_rate}

    if not args.write:
        print("\n(nothing written -- pass --write to patch the sidecar)")
        return
    for path in metas:
        meta = json.loads(path.read_text())
        if "feat" not in meta:
            raise ValueError(f"{path} has no `feat` -- refusing to patch a sidecar that cannot line up columns at predict time.")
        meta |= patch
        path.write_text(json.dumps(meta, indent=2))
        print(f"\npatched {_rel(path)} (booster file untouched)")


if __name__ == "__main__":
    main()
