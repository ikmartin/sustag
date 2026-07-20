"""_experiment16: individual-site (gauged) spike modelling with own-history autoregression.

Where exp14 evaluated the spike target CROSS-site (unseen sites, compare_many) and found it weak
in absolute terms, this evaluates each site on its OWN history via chronological CV
(compare_fleet = cook_one per site, summarised to median/mean). The spike target needs a per-site
baseline, so the gauged-site regime is its natural home, and own autoregression (recipe_SPIKE_AR)
is the strongest signal there (cf. exp7 recipe_B: median R2 ~0.88 vs ~0 cross-site).

Two questions:
  1. does individual modelling + own-history rescue the spike target? -> recipe_SPIKE_AR vs the
     transfer-safe recipe_SPIKE (no own-history), both under compare_fleet.
  2. is the cross-site optimum N=30 still best for individual modelling? -> sweep N past 30.

REG (anomaly z) -> test_results/_experiment16.csv, CLF (binary spike) -> _experiment16c.csv.
NOTE: per-site CV over every site x N x {AR, non-AR} is heavy; trim `windows` or a task to iterate.
"""

import sys
from pathlib import Path

sys.path.insert(0, "../")
from cook import compare_fleet, save_comparison, FAST_XGB
from recipes3 import recipe_SPIKE, recipe_SPIKE_AR

sys.path.insert(0, "../../")
from data import get_site_ids
from data.features import daily_nitrate

windows = (21, 30, 45, 60, 90)  # baseline window (days); sweeps past the cross-site optimum (30)

# AR = + the site's own past nitrate (gauged-site only); non-AR = the transfer-safe spike recipe.
recipes_reg, recipes_clf = {}, {}
for N in windows:
    recipes_reg[f"spike_AR_reg_N={N}"] = recipe_SPIKE_AR(task="reg", window=N)
    recipes_reg[f"spike_reg_N={N}"] = recipe_SPIKE(task="reg", window=N)
    recipes_clf[f"spike_AR_clf_N={N}"] = recipe_SPIKE_AR(task="clf", window=N)
    recipes_clf[f"spike_clf_N={N}"] = recipe_SPIKE(task="clf", window=N)

# task -> (recipes, extra compare_fleet kwargs, output-file suffix)
_TASKS = {
    "reg": (recipes_reg, dict(), ""),
    "clf": (recipes_clf, dict(target_col="violation", task="clf"), "c"),
}


def _run_and_write(sites, task, extra=False):
    recipes, kw, suffix = _TASKS[task]
    print(f"[{task}] {len(recipes)} recipes x {len(sites)} sites (per-site chronological CV)\n")
    results = compare_fleet(recipes, sites, extra_importance_test=extra, **kw, **FAST_XGB)
    print(results.round(3).to_string())
    Path("test_results").mkdir(exist_ok=True)
    save_comparison(results, f"test_results/{Path(__file__).stem}{suffix}.csv")


def main(sites=None, extra=False):
    if sites is None:
        sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500]
    for task in ("reg", "clf"):
        _run_and_write(sites, task, extra=extra)


if __name__ == "__main__":
    main()
