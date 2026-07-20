"""_experiment10 -- buckets AND exp-decay together, grid-searched over edge x VEL x LAM.

Experiment 9 pitted the two spatial-aggregation styles against each other (exp-decay with NO
buckets vs buckets with NO decay). Experiment 9 suggested a single inner/outer bucket helps,
so here we COMBINE them: crops & surplus are split into ONE distance bucket boundary AND
exp-decay weighted within (exp=True, length LAM). Weather is bucketed and shifted back by its
water travel-time lag (velocity VEL), as in experiment 9's bucket family.

3 x 3 x 3 grid over the single bucket edge, the water velocity, and the decay length:

    edge (m) in {5_000, 15_000, 50_000}     -- one inner/outer boundary
    VEL  (m/s) in {0.3, 1.2, 2.1}           -- weather travel-time lag
    LAM  (m)  in {2_000, 20_000, 100_000}   -- crop/surplus distance-decay length

Same target (daily nitrate) and pure-calendar terms in all 27 recipes; static site
descriptors included by default (realistic cross-site setup). Compared cross-site via
cook_many (LOSO/LOFO).

Run:  python isaac/experiments/_experiment10.py
"""

import sys

sys.path.insert(0, "../")

from pathlib import Path

from data.features import (
    agg_crops,
    agg_surplus,
    agg_weather_w_lag,
    daily_nitrate,
    doy_climatology_pure_signal,
    site_static,
    nitrate_avg_except_this,
    water_balance,
)
from data.transforms import flatten_buckets, merge_on_date
from data import get_site_ids

from cook import compare_many, save_comparison, FAST_XGB


def _add_static(site, d):
    """Broadcast the time-invariant site descriptors (lat/lon, basin size, ...) onto every row."""
    for k, v in site_static(site).items():
        d[k] = v
    return d


# ── recipe factory: buckets AND exp-decay ──────────────────────────
def make_recipe(edge, vel, lam):
    """One distance bucket boundary `edge`, crops/surplus exp-decay weighted (length `lam`)
    within the buckets, weather bucketed + travel-time lagged at velocity `vel`."""

    def recipe(site):
        wb = flatten_buckets(agg_weather_w_lag(site, edges=[edge], exp=False, water_velocity=vel))
        cb = flatten_buckets(agg_crops(site, edges=[edge], lam=lam, exp=True))
        sb = flatten_buckets(agg_surplus(site, edges=[edge], lam=lam, exp=True))
        n = daily_nitrate(site).rename("nitrate_con")
        doy = doy_climatology_pure_signal(n)
        lagged_avgs = [
            nitrate_avg_except_this(site, shift=1),
            nitrate_avg_except_this(site, shift=2),
            nitrate_avg_except_this(site, shift=3),
            nitrate_avg_except_this(site, shift=5),
        ]
        return _add_static(site, merge_on_date([n, wb, cb, sb, doy, *lagged_avgs], spine=n.index))

    return recipe


# ── grid ────────────────────────────────
EDGES = (5_000, 15_000, 50_000)  # single inner/outer bucket boundary (m)
VELS = (0.3, 1.2, 2.1)  # water velocity (m/s) -> weather travel-time lag
LAMS = (2_000, 20_000, 100_000)  # crop/surplus exp-decay length (m)


recipes = {}
for edge in EDGES:
    for vel in VELS:
        for lam in LAMS:
            name = f"e{edge // 1000}k_v{vel}_l{lam // 1000}k"
            recipes[name] = make_recipe(edge, vel, lam)


def main(sites=None, extra=False):
    if sites is None:
        sites = [s for s in get_site_ids() if daily_nitrate(s).dropna().shape[0] >= 1500]
    print(f"{len(recipes)} recipes x {len(sites)} sites\n")

    results = compare_many(
        recipes, sites, extra_importance_test=extra, **FAST_XGB
    )  # target_col defaults to 'nitrate_con', task='reg'
    print(results.round(3).to_string())

    Path("test_results").mkdir(exist_ok=True)
    save_comparison(results, f"test_results/{Path(__file__).stem}.csv")


if __name__ == "__main__":
    main()
