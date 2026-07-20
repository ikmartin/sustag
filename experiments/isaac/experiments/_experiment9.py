"""_experiment9 -- exp-decay (no buckets) vs distance-buckets (no exp), across LAM/VEL/edges.

Two ways to fold the spatial grid into features, compared head-to-head:

  A) EXP-DECAY, NO BUCKETS  (edges=[])
       crops & surplus aggregated with an exponential distance-decay weight exp(-dist/LAM)
       -> one column each; weather is a single basin area-mean. Tuning knob: LAM (the
       decay length, metres). VEL/edges don't apply (a single bucket has no travel lag).

  B) BUCKETS, NO EXP  (edges=[...])
       crops & surplus aggregated as a plain SUM within each distance bucket -> one column
       per bucket; weather is bucketed and each bucket shifted back by its water travel-time
       lag. Tuning knobs: edges (bucket boundaries, metres) and VEL (water velocity -> lag).

Same target (daily nitrate) and the same pure-calendar terms in both, so the only thing
that differs is how crops/surplus/weather are spatially summarised. Compared cross-site via
cook_many (LOSO/LOFO) -- the regime where these aggregation choices actually matter.

Run:  python isaac/_experiment9.py
"""

import sys

sys.path.insert(0, "../")

from pathlib import Path

from data.features import (
    agg_crops,
    agg_surplus,
    agg_weather,
    agg_weather_w_lag,
    daily_nitrate,
    doy_climatology_pure_signal,
    site_static,
)
from data.transforms import flatten_buckets, merge_on_date
from data import get_site_ids

from cook import compare_many, save_comparison, FAST_XGB


def _add_static(site, d):
    """Broadcast the time-invariant site descriptors (lat/lon, basin size, ...) onto every row.

    Included by default: across sites these let the pooled model place a site, which is the
    realistic cross-site setup (without them the comparison is dominated by missing
    site-placement signal rather than the aggregation choice we're testing).
    """
    for k, v in site_static(site).items():
        d[k] = v
    return d


# ── recipe factories ───────────────────────────────────────────────
def make_exp_recipe(lam):
    """Family A: no buckets, exponential distance-decay (length `lam`) on crops & surplus."""

    def recipe(site):
        wb = flatten_buckets(agg_weather(site, edges=[]))  # single area-mean
        cb = flatten_buckets(agg_crops(site, edges=[], lam=lam, exp=True))
        sb = flatten_buckets(agg_surplus(site, edges=[], lam=lam, exp=True))
        n = daily_nitrate(site).rename("nitrate_con")
        doy = doy_climatology_pure_signal(n)
        return _add_static(site, merge_on_date([n, wb, cb, sb, doy], spine=n.index))

    return recipe


def make_bucket_recipe(edges, vel):
    """Family B: plain-sum distance buckets (`edges`); weather bucketed + travel-time lagged at `vel`."""

    def recipe(site):
        wb = flatten_buckets(agg_weather_w_lag(site, edges=edges, exp=False, water_velocity=vel))
        cb = flatten_buckets(agg_crops(site, edges=edges, exp=False))
        sb = flatten_buckets(agg_surplus(site, edges=edges, exp=False))
        n = daily_nitrate(site).rename("nitrate_con")
        doy = doy_climatology_pure_signal(n)
        return _add_static(site, merge_on_date([n, wb, cb, sb, doy], spine=n.index))

    return recipe


# ── case grid ---------
LAMS = (2_000, 5_000, 10_000, 20_000)
EDGES = ([5_000], [10_000], [5_000, 15_000])
VELS = (0.3, 0.8, 1.5)


def _edge_tag(edges):
    return "-".join(f"{e // 1000}k" for e in edges)


recipes = {}
for lam in LAMS:
    recipes[f"A_exp_lam{lam // 1000}k"] = make_exp_recipe(lam)
for edges in EDGES:
    for vel in VELS:
        recipes[f"B_buck_{_edge_tag(edges)}_v{vel}"] = make_bucket_recipe(edges, vel)


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
