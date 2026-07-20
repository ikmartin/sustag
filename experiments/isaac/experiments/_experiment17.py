"""_experiment17 -- do antecedent-moisture integrators and rain-driven interaction terms help,
added ON TOP of the shipped recipe?  (regression + classification)

A refactored, expanded re-run of _experiment11. exp11 tested rolling_precip / water_balance on a
small 20-site pool at a throwaway geometry (edge=20k/vel=0.8/lam=5k) and found they didn't help.
Two things changed since: (1) the pipeline was re-grained, and (2) the teammate EDA (Preet) found
antecedent rain (rain_roll_30d) to be the DOMINANT weather signal while daily precip was near-useless
-- and the shipped weather block, at vel=2.1, carries only a 0-1 day travel-time lag (no multi-week
memory). So this re-tests the question against the ACTUAL shipped recipe (recipe_REG / recipe_CLF,
per-task geometry), and folds in the physically-motivated interactions the EDA used:

  base              shipped recipe_REG / recipe_CLF
  roll_precip       + rolling_precip(7/14/30d)                    -- antecedent-moisture index
  wbal              + water_balance(30/60d)                       -- PDSI-lite (precip - PET)
  roll+interact     + rolling_precip + leaching_risk(rain/ET) + rain_x_surplus  -- flush interactions

Read: does lofo_r2 / lofo_auc improve base -> roll_precip? does interact add anything on top? A win
here would justify adding an antecedent-rain block to the shipped feature list; a null result
corroborates exp11 (the travel-lag geometry already captures what the model needs).

NOTE (refactor): this experiment targets the REFACTORED codebase (src.* imports) and is written to
run as-is. The older _experiment*.py files still use the pre-refactor `data`/`cook` imports and are
NOT updated here.

Run:  python experiments/isaac/experiments/_experiment17.py
"""

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # sustag2/ on path

import pandas as pd

from src.eval.cook import compare_many, save_comparison, FAST_XGB
from src.features.recipes import recipe_REG, recipe_CLF
from src.features.features import rolling_precip, water_balance, _basin_daily_weather, daily_nitrate
from src.data.access import get_site_ids

ROLL_W = (7, 14, 30)  # antecedent-rain windows (days) -- Preet's gain concentrated at the long end


def _merge_date(base, extra):
    """Left-merge a DatetimeIndex `extra` frame onto `base` (RangeIndex + 'date' column) by date."""
    e = extra.copy()
    e.index.name = "date"
    return base.merge(e.reset_index(), on="date", how="left")


def _augment(base_fn, precip=False, wbal=False, interact=False):
    """Shipped recipe `base_fn` plus optional antecedent-moisture / interaction blocks."""

    def recipe(site):
        df = base_fn(site)
        if precip or interact:
            df = _merge_date(df, rolling_precip(site_uid=site, windows=ROLL_W))
        if wbal:
            df = _merge_date(df, water_balance(site_uid=site, windows=(30, 60)))
        if interact:
            et = _basin_daily_weather(site_uid=site)["evapotranspiration"]
            et_on = et.reindex(pd.to_datetime(df["date"])).to_numpy()
            for w in ROLL_W:
                pr = df[f"precip_roll{w}d"]
                df[f"leaching_risk_{w}d"] = pr.to_numpy() / (et_on + 1.0)  # rain / ET flush ratio (Preet)
                if "surplus_kgha_b0" in df.columns:  # N transport = load x flow (all 3 teammates)
                    df[f"rain_x_surplus_{w}d"] = pr * df["surplus_kgha_b0"]
        return df

    return recipe


def _recipes(base_fn):
    return {
        "base": _augment(base_fn),
        "roll_precip": _augment(base_fn, precip=True),
        "wbal": _augment(base_fn, wbal=True),
        "roll+interact": _augment(base_fn, precip=True, interact=True),
    }


def _run(sites, task, extra):
    base_fn, target_col, suffix, kw = (
        (recipe_REG, "nitrate_con", "", {}) if task == "reg"
        else (recipe_CLF, "violation", "c", dict(target_col="violation", task="clf"))
    )
    recipes = _recipes(base_fn)
    print(f"\n[{task}] {len(recipes)} recipes x {len(sites)} sites")
    results = compare_many(recipes, sites, extra_importance_test=extra, **kw, **FAST_XGB)
    print(results.round(4).to_string())
    Path("test_results").mkdir(exist_ok=True)
    save_comparison(results, f"test_results/{Path(__file__).stem}{suffix}.csv")


def main(sites=None, extra=False):
    if sites is None:
        sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500]
    _run(sites, "reg", extra)
    _run(sites, "clf", extra)


if __name__ == "__main__":
    main()
