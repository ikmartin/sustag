"""_experiment15: does removing group 2 make (regression + classification) perform better?

Runs the default recipe (window=1, min_obs=1) under cross-site CV on three site pools -- all
sites, group-2 only, and everything except group 2 -- for both tasks. The comparison of interest
is all_sites vs non-group2_sites. NOTE: group 2 is a single basin family, so the group-2-only
pool has n_families=1 and its lofo_* will be NaN (LOFO needs >=2 families).
"""

import sys
from pathlib import Path

sys.path.insert(0, "../")
from cook import compare_many, save_comparison, FAST_XGB
from recipes3 import recipe_maker

sys.path.insert(0, "../../")
from data import get_site_ids

group_2_sites = [
    "USGS-05480986",
    "USGS-05481000",
    "USGS-05482000",
    "USGS-05482300",
    "USGS-05482500",
    "USGS-05483600",
    "USGS-05484000",
    "USGS-05484500",
    "WQS0032",
    "WQS0033",
    "WQS0039",
    "WQS0046",
    "WQS0047",
    "WQS0073",
    "WQS0074",
    "WQS0080",
    "WQS0081",
    "WQS0104",
    "WQS0115",
]

all_sites = set(get_site_ids())
sites2 = set(group_2_sites)
all_other_sites = all_sites.difference(sites2)
site_lists = {"all_sites": list(all_sites), "group_2_sites": list(sites2), "non-group2_sites": list(all_other_sites)}


def _run_and_write(sites, task, extra=False, keyword=""):
    # per task: recipes, extra compare_many kwargs, and the output-file suffix. clf scores with
    # target_col="violation"/task="clf".
    if task == "reg":
        recipes, kw, suffix = {keyword + "_reg": recipe_maker(task="reg")}, {}, f"_{keyword}_reg"
    elif task == "clf":
        recipes = {keyword + "_clf": recipe_maker(task="clf")}
        kw, suffix = dict(target_col="violation", task="clf"), f"_{keyword}_clf"
    else:
        raise ValueError(f"Expected either 'reg' or 'clf', got {task}")
    print(f"[{keyword} / {task}] {len(recipes)} recipe x {len(sites)} sites\n")

    results = compare_many(recipes, sites, extra_importance_test=extra, **kw, **FAST_XGB)
    print(results.round(3).to_string())

    Path("test_results").mkdir(exist_ok=True)
    save_comparison(results, f"test_results/{Path(__file__).stem}{suffix}.csv")


def main(sites=None, extra=False):  # `sites` is ignored: the three pools are fixed by this experiment
    for keyword, site_set in site_lists.items():
        _run_and_write(site_set, task="reg", extra=extra, keyword=keyword)
        _run_and_write(site_set, task="clf", extra=extra, keyword=keyword)


if __name__ == "__main__":
    main()
