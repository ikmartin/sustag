"""Experiment driver: build the pool once, fit both tasks, print the comparison against the predecessor's numbers.

    python run_experiment.py                 # both tasks, whole cohort
    python run_experiment.py --task reg      # one task
    python run_experiment.py --limit 20      # smoke on 20 sites
    python run_experiment.py --old-cohort    # restrict to sites the old models trained on

THE COMPARISON IS NOT LIKE-FOR-LIKE and the printout says so. The predecessor scored 0.4818 lofo_r2 / 0.7223 lofo_prauc on 116 sites and 175,973 rows, mostly Iowa, on euclidean distance rings over a raster grid. This cohort is wider (the whole cornbelt box), larger, and built on flow-distance buckets over catchments. `--old-cohort` is the only apples-to-apples read available, and even it differs in features.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cohort
import cook
import recipes

# The predecessor's best runs, 2026-07-31, experiments/feature-manifest/log.json.
OLD = {"reg": ("lofo_r2", 0.4818, "drop_crops_norm"),
       "clf": ("lofo_prauc", 0.7223, "drop_min_rel_humidity_expF")}
# The predecessor's cohort was 23.3% violation-days; a base-rate-free comparison needs these too.
OLD_CLF_CONTEXT = dict(base=0.2325, prauc_lift=3.107, auc=0.8777)
OLD_COHORT_FILE = Path(__file__).resolve().parents[1] / "experiments" / "feature-manifest" / "old_sites.txt"


def old_cohort_codes(codes) -> list:
    """Cohort sites whose source uid appears in the predecessor's site list."""
    if not OLD_COHORT_FILE.exists():
        print(f"  no old-site list at {OLD_COHORT_FILE}; using the full cohort")
        return list(codes)
    # the file uses the legacy `USGS-05412500` spelling; the pipeline's uid is `USGS:05412500`
    old = {ln.strip().replace("-", ":", 1) for ln in OLD_COHORT_FILE.read_text().splitlines()
           if ln.strip() and not ln.startswith("#")}
    native = {u.split(":")[-1] for u in old}
    s = cohort.sites()
    keep = s[s.site_uid.isin(old) | s.site_uid.str.split(":").str[-1].isin(native)]
    print(f"  old-cohort overlap: {len(keep)} of {len(old)} historical sites still in the cohort")
    return keep.snr_id.tolist()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=["reg", "clf", "both"], default="both")
    ap.add_argument("--limit", type=int, default=None, help="first N sites only (smoke)")
    ap.add_argument("--old-cohort", action="store_true", help="restrict to the predecessor's sites")
    ap.add_argument("--name", default="baseline")
    ap.add_argument("--fast", action="store_true", help="fewer trees, for iteration")
    ap.add_argument("--scratch", action="store_true",
                    help="read the SCRATCH store built from assembled records (development only, "
                         "no quality masks) -- for use before p1's quality gate is answered")
    a = ap.parse_args(argv)

    if a.scratch:
        import scratch_store
        scratch_store.activate()
        print("  !! SCRATCH STORE: no quality masks applied; these numbers are a development signal")

    sites = cohort.sites()
    codes = sites.snr_id.tolist()
    print(f"cohort: {len(codes)} published records "
          f"({sites.site_type.value_counts().to_dict()})")
    if a.old_cohort:
        codes = old_cohort_codes(codes)
    if a.limit:
        codes = codes[:a.limit]
        print(f"  limited to {len(codes)} site(s)")

    tasks = ["reg", "clf"] if a.task == "both" else [a.task]
    kw = cook.FAST_XGB if a.fast else {}
    rows = []
    for task in tasks:
        print(f"\n=== {task} ===", flush=True)
        recipe = recipes.island_REG if task == "reg" else recipes.island_CLF
        out = cook.cook_many(recipe, codes=codes, task=task,
                             name=f"{a.name}-{task}" + ("-oldcohort" if a.old_cohort else ""), **kw)
        metric, old_val, old_name = OLD[task]
        new_val = out.get(metric)
        rows.append(dict(task=task, metric=metric, new=new_val, old=old_val,
                         n_sites=out["n_sites"], n_rows=out["n_rows"], n_feat=out["n_feat"]))
        print(f"  {metric}: {new_val:.4f}   (predecessor {old_val:.4f} on 116 sites / 175,973 rows"
              f", run '{old_name}')")
        if task == "clf":
            c = OLD_CLF_CONTEXT
            print(f"  base rate: {out['base']:.4f}  (predecessor {c['base']:.4f}) "
                  f"-- prauc is bounded below by this, so the cohorts are not comparable on it directly")
            print(f"  lofo_prauc_lift: {out['lofo_prauc_lift']:.3f}  (predecessor {c['prauc_lift']:.3f})"
                  f"   <- the base-rate-free comparison")
            print(f"  lofo_auc: {out['lofo_auc']:.4f}  (predecessor {c['auc']:.4f})")
        print(f"  pool: {out['n_sites']} sites, {out['n_families']} families, "
              f"{out['n_rows']:,} rows, {out['n_feat']} features, {out['fit_seconds']}s")
        top = out["importance"].head(12)
        print("  top features by gain:")
        for k, v in top.items():
            print(f"     {v:10.1f}  {k}")

    print("\n=== summary (NOT like-for-like -- different cohort, geometry and features) ===")
    print(pd.DataFrame(rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
