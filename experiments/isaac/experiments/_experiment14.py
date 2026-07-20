"""_experiment14: do 'spike' targets (deviation from a trailing rolling-mean baseline) model better than the absolute level / violation targets?

Spikes are site-relative by construction, so they target the within-site dynamics the level models capture rather than the between-site level they don't -- and persistence ("predict yesterday") is a much weaker baseline for an anomaly, so persist_skill is the number to watch.

  REG target -> the continuous standardized anomaly  z(t) = (x - trailing_mean) / trailing_std
  CLF target -> the binary spike  1[z(t) >= k]

REG sweeps the baseline window N (z is independent of k); CLF sweeps N x k. Regression -> test_results/_experiment14.csv, classification -> test_results/_experiment14c.csv.
"""

import sys
from pathlib import Path

sys.path.insert(0, "../")
from cook import compare_many, save_comparison, FAST_XGB
from recipes3 import recipe_SPIKE

sys.path.insert(0, "../../")
from data import get_site_ids
from data.features import daily_nitrate

windows = (7, 14, 21, 30)  # trailing baseline window (days) the anomaly is measured against
ks = (1.5, 2.0, 2.5)  # z-threshold for the binary spike (CLF only; REG z(t) ignores it)
MIN_OBS = 5  # min observed days in the baseline window for z(t) to be defined

# REG: z(t) doesn't depend on k, so sweep only the baseline window.
recipes_reg = {f"spike_reg_N={N}D": recipe_SPIKE(task="reg", window=N, min_obs=MIN_OBS) for N in windows}
# CLF: the binary spike depends on both baseline window and threshold k.
recipes_clf = {
    f"spike_clf_N={N}D_k={k}": recipe_SPIKE(task="clf", window=N, k=k, min_obs=MIN_OBS) for N in windows for k in ks
}

# task -> (recipes, extra compare_many kwargs, output-file suffix). clf scores with
# target_col="violation"/task="clf" and writes to the "...c" file.
_TASKS = {
    "reg": (recipes_reg, dict(), ""),
    "clf": (recipes_clf, dict(target_col="violation", task="clf"), "c"),
}


def _run_and_write(sites, task, extra=False):
    recipes, kw, suffix = _TASKS[task]
    print(f"[{task}] {len(recipes)} recipes x {len(sites)} sites\n")
    results = compare_many(recipes, sites, extra_importance_test=extra, **kw, **FAST_XGB)
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
