"""E1 -- the distance-stratified donor-discharge experiment. One CV sweep per exclusion radius.

    python 04_run_exp.py --full                    # both tasks, every radius in common.RADII_KM
    python 04_run_exp.py --full --task clf --radii 1,10
    python 04_run_exp.py --small --radii 0,1       # shakedown

Each radius is a SEPARATE masked sweep over its own pool, because the donor set changes with the radius and therefore so do the feature values. Within a radius every arm shares the pool, the folds and the config, so the arm deltas are exactly paired -- the property the whole exps 33-36c design rests on. Across radii only the `base` arm is comparable, and it is logged every time precisely so that can be checked: base must score identically at every radius, since it holds no donor columns.

ARMS
    base            the shipped recipe, no donor columns
    q_lagged        donor discharge at lag 1 and 3 -- CAUSAL, the deployable form
    q_concurrent    lag 0 -- non-causal reference, quantifies the causality tax
    q_coverage      donor distance + drainage-area ratio ONLY -- THE NEGATIVE CONTROL
    q_all           everything

READ q_coverage FIRST. Gauge density tracks basin size, road access and funding history, all of which correlate with land use. If the coverage-only arm carries most of the effect, the block is a site-type fingerprint and not a hydrologic signal. This is the test that gave exps 33-36c their credibility, where the equivalent arm came back at +0.0008 REG.

READ r=0 AS A CONTROL. It is the co-located feature -- the target's own gauge. Its gap to r=1 is the train/serve skew the deployable version avoids, not a result.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import CACHE, RADII_KM, cohort, say

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[0] / "specific-small-experiments"))  # the shared CV engine

from run_exp import MaskedSweep, run_experiment  # noqa: E402
from src.eval.cook import _STRUCTURAL  # noqa: E402
from src.features.recipes import recipe_maker  # noqa: E402

DONORS, DAILY = CACHE / "donors.parquet", CACHE / "discharge_daily.parquet"
_LAGS = (0, 1, 3)  # 0 = concurrent (non-causal); 1/3 causal, matching rest_of_state_nitrate_lag*

_COLS = {
    "concurrent": ["q_anom_lag0"],
    "lagged": ["q_anom_lag1", "q_anom_lag3"],
    "cover": ["q_donor_km", "q_area_ratio"],
}
_ALL = [c for v in _COLS.values() for c in v]


def _donor_map(radius: float, have: set) -> tuple[dict, float]:
    """site -> (donor, km) at this radius, walking the rank order to the nearest donor that HAS a series.

    Falling back matters: ~20% of rank-1 donors publish instantaneous values but no daily-mean product, and taking rank 1 unconditionally would leave those sites with an all-NaN column that looks like "no gauge nearby" rather than "no daily product". Returns the realised coverage alongside, so step 04 reports what it actually got rather than what step 02 linked.
    """
    d = pd.read_parquet(DONORS)
    d = d[np.isclose(d.radius_km, radius) & d.donor.notna() & d.donor.isin(have)]
    d = d.sort_values(["site", "rank"]).groupby("site").first()
    return dict(zip(d.index, zip(d.donor, d.donor_km))), len(d)


def _series() -> dict:
    """donor -> a daily discharge Series on a regular date index, log1p-transformed.

    log1p because discharge is heavy-tailed over orders of magnitude and a tree splits on order rather than level; it also keeps the anomaly below symmetric, which a raw-cfs difference between a headwater and a mainstem gauge would not be.
    """
    q = pd.read_parquet(DAILY)
    out = {}
    for donor, g in q.groupby("donor"):
        s = g.set_index("date").discharge.sort_index()
        s = s[~s.index.duplicated()].asfreq("D")
        out[donor] = np.log1p(s.clip(lower=0))
    return out


def build(task: str, radius: float, areas: dict):
    """A MaskedSweep for one radius. `areas` is passed in so the basin-area lookup is paid once per run, not once per radius."""
    shipped = recipe_maker(task, window=1)
    series = _series()
    dmap, n_cov = _donor_map(radius, set(series))
    say(f"  donors resolved: {n_cov}/{len(cohort())} sites ({n_cov / len(cohort()):.0%}) "
        f"| median separation {np.median([v[1] for v in dmap.values()]):.1f} km")
    # The anomaly baseline is the cross-donor daily median: a donor's absolute discharge is dominated by its own basin size, so the useful signal is whether it is running high or low FOR ITS OWN SCALE on that day, relative to the region.
    panel = pd.DataFrame(series)
    baseline = panel.median(axis=1)

    def wide(site_uid):
        df = shipped(site_uid)
        spine = pd.DatetimeIndex(df["date"])
        donor, dist = dmap.get(site_uid, (None, np.nan))
        s = series.get(donor)
        for k in _LAGS:
            col = f"q_anom_lag{k}"
            df[col] = ((s - baseline).shift(k).reindex(spine).values if s is not None
                       else np.full(len(df), np.nan))
        df["q_donor_km"] = dist
        own, dar = areas.get(site_uid), areas.get(f"USGS-{donor}") if donor else None
        df["q_area_ratio"] = (np.log10(dar / own) if own and dar and own > 0 and dar > 0 else np.nan)
        return df

    def masks(pool):
        drop = set(_STRUCTURAL) | {"nitrate_con", "violation"}
        cols = [c for c in pool.columns if c not in drop]
        missing = [c for c in _ALL if c not in pool.columns]
        if missing:  # every arm would silently collapse to base -- see exps.py's identical guard
            raise RuntimeError(f"radius {radius}: columns absent from the pool: {missing}")
        blk = {k: [c for c in cols if c in set(v)] for k, v in _COLS.items()}
        nbr = {c for v in blk.values() for c in v}
        base = [c for c in cols if c not in nbr]
        return {"base": base,
                "q_lagged": base + blk["lagged"],
                "q_concurrent": base + blk["concurrent"],
                "q_coverage": base + blk["cover"],
                "q_all": base + sorted(nbr)}

    return MaskedSweep(wide=wide, masks=masks, base_cols=lambda pool: masks(pool)["base"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=["reg", "clf", "both"], default="both")
    ap.add_argument("--radii", default=None, help="comma-separated km; default common.RADII_KM")
    ap.add_argument("--mode", default="full", choices=["test", "small", "med", "full"])
    ap.add_argument("--extra", action="store_true", help="also run permutation importance (scoped to the added columns)")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()

    for f in (DONORS, DAILY):
        if not f.exists():
            raise SystemExit(f"{f} missing -- run steps 01-03 first")
    radii = [float(x) for x in a.radii.split(",")] if a.radii else list(RADII_KM)
    tasks = ["reg", "clf"] if a.task == "both" else [a.task]

    from src.data.access import get_basin_area
    areas = {}
    for s in cohort():
        try:
            areas[s] = get_basin_area(s)
        except Exception:
            pass

    for task in tasks:
        for r in radii:
            say(f"\n{'=' * 78}\n{task.upper()}  exclusion radius {r:g} km"
                f"{'   [CONTROL: co-located, not deployable]' if r == 0 else ''}\n{'=' * 78}")
            run_experiment(
                question=(f"At an exclusion radius of {r:g} km, does a donor gauge's discharge anomaly predict "
                          f"nitrate beyond the shipped recipe -- and does it survive being made causal?"),
                recipes=lambda t, _r=r: build(t, _r, areas),
                task=task, kind="masked", mode=a.mode, extra=a.extra, seed=a.seed,
                id=f"insitu_q_r{r:g}",
            )


if __name__ == "__main__":
    main()
