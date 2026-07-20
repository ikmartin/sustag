"""_experiment13: do weekly rolling targets do better? (regression + classification)

Covers both tasks, so _experiment13c is redundant. Regression -> test_results/_experiment13.csv,
classification -> test_results/_experiment13c.csv (established naming preserved).
"""

import sys
from pathlib import Path

sys.path.insert(0, "../")
from cook import compare_many, save_comparison, FAST_XGB
from recipes3 import recipe_maker

sys.path.insert(0, "../../")
from data import get_site_ids
from data.features import daily_nitrate

windows = (1, 3, 7)
obs = (1, 3, 7)
w_obs = [(w, o) for w in windows for o in obs if o <= w]

recipes_reg = {f"target_reg_w={w}D_obs={o}": recipe_maker(task="reg", window=w, min_obs=o) for w, o in w_obs}

recipes_clf = {f"target_clf_w={w}D_obs={o}": recipe_maker(task="clf", window=w, min_obs=o) for w, o in w_obs}


def _run_and_write(sites, task, extra=False):
    # per task: recipes, extra compare_many kwargs, and the output-file suffix. clf scores
    # with target_col="violation"/task="clf" and writes to the "...c" file.
    if task == "reg":
        recipes, kw, suffix = recipes_reg, {}, ""
    elif task == "clf":
        recipes, kw, suffix = recipes_clf, dict(target_col="violation", task="clf"), "c"
    else:
        raise ValueError(f"Expected either 'reg' or 'clf', got {task}")
    if sites is None:
        sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500]
    print(f"[{task}] {len(recipes)} recipes x {len(sites)} sites\n")

    results = compare_many(recipes, sites, extra_importance_test=extra, **kw, **FAST_XGB)
    print(results.round(3).to_string())

    Path("test_results").mkdir(exist_ok=True)
    save_comparison(results, f"test_results/{Path(__file__).stem}{suffix}.csv")


def main(sites=None, extra=False):
    _run_and_write(sites, task="reg", extra=extra)
    _run_and_write(sites, task="clf", extra=extra)


if __name__ == "__main__":
    main()
