"""Chunk 6 — filter the registrations down to the trainable cohort, and publish their water.

    python -m src.build2.run chunk6
    python -m src.build2.run chunk6 --review      # re-render the quality panel

THIS IS A FILTER. `registrations.parquet` holds 392 rows and never fewer; `sites.parquet` holds the gauges that can actually be trained on. Two things follow, and they are the point of the chunk: the read layer NEVER MERGES ANYTHING ITSELF, because a merged pair arrives as one uid with one series; and everything in `processed2/` MEETS A QUALITY BAR, because nothing is written until it has passed one.

WHAT IT MATERIALISES, AND WHAT IT REFUSES TO. Identity determines the water -- merging can only be done correctly by the layer holding the merge decisions, and if this chunk skips it every consumer re-derives it, which is the double-count the merge block's null semantics exist to prevent. Aggregation determines the covariates, and features has to be able to sum those over a basin's catchments regardless, so caching one particular sum here would buy nothing and go stale. Hence: water yes, covariates never.

THE ORDER IS SET BY COST. The quality detector reads native-resolution series -- 1.2 GB across the cohort, about nine minutes -- so it runs only on gauges that already cleared the record floor. A well or a short record never reaches it, which is also what keeps the review queue short enough to work through.

`sites.parquet` IS THE FINAL ROW SET, NOT THE FINAL COLUMN SET. Chunk 6 widens it with the graph columns. No site is ever added to it after this.
"""

from __future__ import annotations

if __name__ == "__main__" and __package__ in (None, ""):
    import pathlib as _pl
    import runpy as _rp
    import sys as _sys

    _root = _pl.Path(__file__).resolve().parents[3]
    _sys.path.insert(0, str(_root))
    _rp.run_module("src.build2.chunk6.run_chunk6", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

import argparse

import pandas as pd

from .. import config, io as bio
from . import assemble, corrupt_review, filter as flt, quality

# What each upstream chunk must have produced before a cohort can honestly be assembled. Checked as PATHS
# rather than as call order, so running `chunk6` on its own is gated exactly like running the full sequence.
REQUIRES = (
    ("chunk4", "basin delineation", config.BASINS),
    ("chunk5", "covariate aggregation", config.COMID_FEATURES / "crops.parquet"),
)


def _require_upstream() -> None:
    """Refuse to assemble until chunks 3 and 4 have produced their artifacts.

    A gauge with no delineated basin has no covariates, so a `sites.parquet` built before chunk 4 is a table that reads as a finished cohort and is not one -- present, well-formed, and describing sites nothing can generate features for. Named down to the FILE rather than the directory, because an empty `comid_features/` satisfies a directory check and nothing else.
    """
    missing = [(n, why, p) for n, why, p in REQUIRES if not p.exists()]
    if not missing:
        return
    lines = "\n".join(f"    {n}  {why}\n           expects {p}" for n, why, p in missing)
    raise SystemExit(
        f"\nChunk 6 cannot assemble a cohort yet -- these have not run:\n{lines}\n"
        f"\nA gauge with no basin has no covariates, so assembling now would write a sites.parquet\n"
        f"that looks complete and is not.\n")


def main(force: bool = False, review: bool = False) -> pd.DataFrame:
    _require_upstream()
    if not config.REGISTRATIONS_PATH.exists():
        raise FileNotFoundError(f"{config.REGISTRATIONS_PATH} is missing -- run chunk 0 first")
    reg = pd.read_parquet(config.REGISTRATIONS_PATH)

    print("\n[6.1] filter  (registration predicates)")
    kept, reg_reason = flt.registrations(reg)

    print("\n[6.2] collapse  (merged registrations -> physical gauges)")
    g = assemble.collapse(kept)
    members = {r.site_uid: str(r.member_uids).split("+") for r in g.itertuples()}
    merged = int((g.n_registrations > 1).sum())
    print(f"  {len(kept)} registration(s) -> {len(g)} gauges ({merged} merged from more than one)")

    print(f"\n[6.3] quality  (scrub + detect, on gauges clearing {flt.MIN_GAUGE_DAYS} days)")
    # The record floor is applied FIRST and separately, so the nine-minute native read runs on the gauges
    # that could still qualify rather than on every one. `filter.gauges` re-applies it uniformly below, so
    # the funnel still attributes each drop to exactly one predicate.
    assessable = g[g.n_nitrate_days >= flt.MIN_GAUGE_DAYS]
    print(f"  assessing {len(assessable)} of {len(g)} gauges", flush=True)
    F, series = quality.assess(assessable, members)
    print(f"  clamped {int(F.n_clamped.sum()):,} sub-detection reading(s) to zero across "
          f"{int((F.n_clamped > 0).sum())} gauge(s); nulled {int(F.n_scrubbed.sum()):,} impossible one(s) "
          f"across {int((F.n_scrubbed > 0).sum())}")
    g = g.drop(columns=["n_samples"], errors="ignore").merge(
        F[["n_samples"]].reset_index(), on="site_uid", how="left")

    print("\n[6.4] filter  (gauge predicates)")
    survivors, gauge_reason = flt.gauges(g, excluded=corrupt_review.excluded())
    flt.report(reg_reason, gauge_reason, len(reg), len(g), len(survivors))

    print("\n[6.5] quality gate")
    _gate(F.loc[F.index.intersection(survivors.site_uid)], series, survivors, review=review)

    print("\n[6.6] publish")
    sites = assemble.finalise(survivors, series)
    bio.write_parquet(sites, config.SITES_PATH)
    n = assemble.write_water(sites, series)
    bio.write_parquet(flt.write_reasons(reg, reg_reason, gauge_reason, members),
                      config.REGISTRATIONS_PATH)
    print(f"  wrote {config.SITES_PATH.name} ({len(sites)} gauges), {len(sites)} per-gauge parquet(s), "
          f"and {config.NITRATE_DAILY.name} ({n:,} gauge-days)")
    print(f"  {config.REGISTRATIONS_PATH.name}: excluded_reason set on "
          f"{int(sites.shape[0] and (len(reg) - len(sites)))} registration(s)")
    return sites


def _gate(F: pd.DataFrame, series: dict, survivors: pd.DataFrame, review: bool = False) -> None:
    """Stop the build while any flagged series is unjudged. NOTHING is written to processed2 before this clears.

    That ordering is the whole guarantee. If the gate ran after the write, a half-reviewed cohort would leave parquets on disk that a later `exclude` should have prevented -- and a stale file is worse than a missing one, because it reads cleanly. Exiting first is what makes "everything in processed2 meets a quality bar" true by construction rather than by convention.

    The queue is only ever gauges that passed every OTHER predicate, so a `keep` here is a statement about data somebody is about to train on.
    """
    q = corrupt_review.queue(F, survivors)
    if not len(q):
        print(f"  nothing flagged across {len(F)} assessed gauge(s)")
        return

    a, k, rt = corrupt_review.sync_decisions_file(q)
    print(f"  {config.CORRUPT_DECISIONS.name}: {len(q)} flagged -- {k} carried forward, {a} new"
          + (f", {rt} retired (kept, decided)" if rt else ""))
    bio.write_csv(q, config.CORRUPT_CANDIDATES)
    pend = corrupt_review.pending(q)
    if not pend:
        print(f"  all {len(q)} flagged series decided")
        return

    png = corrupt_review.out_dir() / "corrupt_review.png"
    if review or not png.exists() or png.stat().st_mtime < config.CORRUPT_CANDIDATES.stat().st_mtime:
        print(f"  rendering {png.name} ...", flush=True)
        corrupt_review.main(F, series, survivors)

    rows = "\n".join(f"    {u}" for u in pend)
    raise SystemExit(f"\nData build awaiting series-quality review for:\n{rows}\n"
                     f"\n  panel  {png}"
                     f"\n  sheet  {config.CORRUPT_DECISIONS}"
                     f"\n  set `decision` to `keep` or `exclude` on every row"
                     f"\n\nNothing has been written to {config.GAUGE_WATER} -- a series is published only "
                     f"after it is judged.\nThen resume with:\n  python -m src.build2.run chunk6\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--review", action="store_true", help="force the quality panel to re-render")
    a = ap.parse_args()
    main(force=a.force, review=a.review)
