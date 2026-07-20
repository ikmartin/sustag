"""_experiment19 -- do the two CLF wins STACK? (classification)

exp17 found antecedent-precip rolling sums lift CLF lofo_prauc +0.017; exp18 found a 2km riparian
inner bucket lifts it +0.039. Both were measured against the shipped baseline separately. This
confirms they add together rather than overlap, before the combination is shipped as recipe_CLF.

  base         shipped CLF geometry: edges=[5k], no antecedent precip
  riparian     edges=[2k, 5k]
  roll_precip  edges=[5k] + rolling_precip(7/14/30d)
  combined     edges=[2k, 5k] + rolling_precip(7/14/30d)     <- the NEW recipe_CLF

`combined` reconstructs exactly what recipe_CLF now builds (riparian geometry via make_edges_recipe +
the rolling_precip block). If combined's lofo_prauc ~= base + 0.039 + 0.017, the wins are additive.

NOTE (refactor): refactored codebase (src.*); reuses _experiment18.make_edges_recipe.

Run:  python experiments/isaac/experiments/_experiment19.py
"""

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # sustag2/ on path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling _experiment18 importable

from src.eval.cook import compare_many, save_comparison, FAST_XGB
from src.features.features import rolling_precip, daily_nitrate
from src.features.recipes import CLF_EDGE, CLF_VEL, CLF_LAM
from src.data.access import get_site_ids
from _experiment18 import make_edges_recipe

ROLL_PRECIP = (7, 14, 30)  # antecedent-precip windows tested in the roll_precip / combined arms


def _with_roll(base_recipe, windows=ROLL_PRECIP):
    """base_recipe + antecedent-precip rolling sums, merged on the frame's 'date' column."""

    def recipe(site):
        df = base_recipe(site)
        rp = rolling_precip(site_uid=site, windows=windows).copy()
        rp.index.name = "date"
        return df.merge(rp.reset_index(), on="date", how="left")

    return recipe


SINGLE, RIPARIAN = [CLF_EDGE], [2000, CLF_EDGE]
RECIPES = {
    "base": make_edges_recipe("clf", SINGLE, CLF_VEL, CLF_LAM),
    "riparian": make_edges_recipe("clf", RIPARIAN, CLF_VEL, CLF_LAM),
    "roll_precip": _with_roll(make_edges_recipe("clf", SINGLE, CLF_VEL, CLF_LAM)),
    "combined": _with_roll(make_edges_recipe("clf", RIPARIAN, CLF_VEL, CLF_LAM)),
}


def main(sites=None, extra=False):
    if sites is None:
        sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500]
    print(f"[clf] {len(RECIPES)} recipes x {len(sites)} sites")
    res = compare_many(RECIPES, sites, target_col="violation", task="clf", extra_importance_test=extra, **FAST_XGB)
    print(res.round(4).to_string())
    Path("test_results").mkdir(exist_ok=True)
    save_comparison(res, f"test_results/{Path(__file__).stem}c.csv")


if __name__ == "__main__":
    main()
