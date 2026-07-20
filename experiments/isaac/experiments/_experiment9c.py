"""_experiment9c -- CLASSIFIER version of _experiment9.

exp-decay (no buckets) vs distance-buckets (no exp), across LAM/VEL/edges -- but the TARGET
is the binary 'violation' (daily nitrate >= 10 mg/L) instead of continuous nitrate. Static
site descriptors are included by default (realistic cross-site setup). Continuous nitrate is
never a feature; n_daily is only the spine + calendar. Scored as classification (AUC).

  A) EXP-DECAY, NO BUCKETS  (edges=[])   -- tuning knob: LAM
  B) BUCKETS, NO EXP        (edges=[...]) -- tuning knobs: edges, VEL

Run:  python isaac/_experiment9c.py
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
    nitrate_violations,
    doy_climatology_pure_signal,
    site_static,
)
from data.transforms import flatten_buckets, merge_on_date
from data import get_site_ids

from cook import compare_many, save_comparison, FAST_XGB


def _add_static(site, d):
    """Broadcast time-invariant site descriptors (lat/lon, basin size, ...) onto every row."""
    for k, v in site_static(site).items():
        d[k] = v
    return d


# ── recipe factories (target = 'violation') ────────────────────────
def make_exp_recipe(lam):
    """Family A: no buckets, exponential distance-decay (length `lam`) on crops & surplus."""

    def recipe(site):
        wb = flatten_buckets(agg_weather(site, edges=[]))  # single area-mean
        cb = flatten_buckets(agg_crops(site, edges=[], lam=lam, exp=True))
        sb = flatten_buckets(agg_surplus(site, edges=[], lam=lam, exp=True))
        n = daily_nitrate(site)  # spine + calendar only (NOT a feature)
        doy = doy_climatology_pure_signal(n)
        v = nitrate_violations(site).rename("violation")
        return _add_static(site, merge_on_date([v, wb, cb, sb, doy], spine=n.index))

    return recipe


def make_bucket_recipe(edges, vel):
    """Family B: plain-sum distance buckets (`edges`); weather bucketed + travel-time lagged at `vel`."""

    def recipe(site):
        wb = flatten_buckets(agg_weather_w_lag(site, edges=edges, exp=False, water_velocity=vel))
        cb = flatten_buckets(agg_crops(site, edges=edges, exp=False))
        sb = flatten_buckets(agg_surplus(site, edges=edges, exp=False))
        n = daily_nitrate(site)
        doy = doy_climatology_pure_signal(n)
        v = nitrate_violations(site).rename("violation")
        return _add_static(site, merge_on_date([v, wb, cb, sb, doy], spine=n.index))

    return recipe


# ── case grid ──────────────────────────────────────────────────────
LAMS = (2_000, 5_000, 10_000, 20_000)  # decay length (m) for family A
EDGES = ([5_000], [10_000], [5_000, 15_000])  # bucket boundaries (m) for family B
VELS = (0.3, 0.8)  # water velocity (m/s) -> weather travel-time lag, family B


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

    results = compare_many(recipes, sites, target_col="violation", task="clf", extra_importance_test=extra, **FAST_XGB)
    print(results.round(3).to_string())

    Path("test_results").mkdir(exist_ok=True)
    save_comparison(results, f"test_results/{Path(__file__).stem}.csv")


if __name__ == "__main__":
    main()
