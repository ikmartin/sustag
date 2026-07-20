from pathlib import Path

from cook import compare_many, save_comparison, fit_full, save_model
from recipes3 import recipe_REG, recipe_CLF

"""The reasoning for this dataset (cross-site, ~60k pooled rows, noisy nitrate, the LOSO/LOFO leakage gap you've seen):

Low learning_rate (0.01–0.03) + high n_estimators + early stopping is the single most important choice for a final model — slow learning generalizes better, and early stopping finds the right tree count instead of you guessing.
Shallow max_depth (3–5) matters more here than usual: deep trees latch onto site-specific interactions that don't transfer to unseen basins (your between_r2 problem). Keep it shallow.
subsample/colsample_bytree ~0.7 and min_child_weight ~10 are the regularizers that stop the model from overfitting a single site's quirks — keep them on.
reg_lambda 1–10 is fine; bump it if you still see a big LOSO↔LOFO gap.
How to actually pick values rather than eyeball them: tune against your lofo_r2 (the honest metric), not loso_r2, using cook_many — e.g. sweep max_depth ∈ {3,4,5,6} and learning_rate ∈ {0.01,0.02,0.05} and take the config with the best LOFO. Then fit the final model with fit_full(**that_config). If you want, I can write a small _tune.py that grid-searches cook_many on LOFO and prints the winning config.

One caveat on early_stopping_rounds: with it set, fit_full trains the saved model on 1 − val_frac of the rows (90% by default). If you want the deployed model to see 100% of rows, the usual pattern is to read model.best_iteration from a tuning run, then refit with early_stopping_rounds=None, n_estimators=best_iteration so fit_full trains on everything. Say the word and I'll add that as a final_fit option.
"""
# CLF config: still the old hand-picked defaults -- the clf _tune.py sweep is pending.
# TODO: replace with the `python _tune.py clf` winner when it finishes.
REAL_XGB_old = dict(
    n_estimators=5000,  # high ceiling; early stopping picks the real count
    learning_rate=0.02,  # low -> more trees, better generalization
    max_depth=4,  # shallow; cross-site signal is broad, deep trees memorize sites
    min_child_weight=10,  # leaves need real support -> resists site-specific noise
    subsample=0.7,  # row bagging -> variance reduction
    colsample_bytree=0.7,  # feature bagging -> avoids over-leaning on fm1000-type proxies
    reg_lambda=5.0,  # L2 on leaf weights
    reg_alpha=0.0,  # add L1 (~0.1-1) if you want feature sparsity
    early_stopping_rounds=50,  # needs the val holdout fit_full / cook_many carve
    random_state=42,
)

# current best for regression (from `python _tune.py reg`: lofo_r2 0.343)
REAL_XGB_REG = dict(
    n_estimators=5000,
    learning_rate=0.01,
    max_depth=3,
    min_child_weight=10,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=5.0,
    reg_alpha=0.0,
    early_stopping_rounds=50,
    random_state=42,
)

# current best for clsasification, but all the results in _tune.py were nearly identical.
REAL_XGB_CLF = dict(
    n_estimators=5000,
    learning_rate=0.01,
    max_depth=3,
    min_child_weight=10,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_lambda=5.0,
    reg_alpha=0.0,
    early_stopping_rounds=50,
    random_state=42,
)

OUT = Path("models")


def build(name, recipe, target_col, task, xgb):
    # first check the directory exists before committing
    OUT.mkdir(exist_ok=True)

    # then do CV
    print(f"\n===== {name} ({task}) =====")
    print("[1/2] evaluating cross-site (LOSO/LOFO)...")
    scores = compare_many(
        {name: recipe}, sites=None, target_col=target_col, task=task, extra_importance_test=True, **xgb
    )
    print(scores.round(3).to_string())
    save_comparison(scores, str(OUT / f"{name}.csv"))

    # then fit the deployable model on ALL rows (final_fit=True refits at the early-stopping count)
    print("[2/2] fitting deployable model on all rows...")
    model, feat, _imp = fit_full(
        recipe, sites=None, target_col=target_col, task=task, final_fit=True, progress=True, **xgb
    )
    for f in save_model(model, feat, str(OUT / f"{name}.json"), task=task, target_col=target_col):
        print(f"  wrote {f}")


def main():
    build("recipe_REG2", recipe_REG, target_col="nitrate_con", task="reg", xgb=REAL_XGB_REG)
    build("recipe_CLF2", recipe_CLF, target_col="violation", task="clf", xgb=REAL_XGB_CLF)


if __name__ == "__main__":
    main()
