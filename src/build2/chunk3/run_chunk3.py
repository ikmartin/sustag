"""Chunk 3 — put every registration on a reach, then decide which registrations are one gauge.

    python -m src.build2.run chunk3
    python -m src.build2.run chunk3 --force        # re-snap against the current network

Three steps: snap each site to its origin reach, decide what it IS, then decide which registrations are one gauge. Two human gates follow -- ambiguous site types and merge candidates -- and the build stops until both are resolved.

CHUNK 2 WRITES METADATA AND NOTHING ELSE. It decides what each site IS and which registrations are one gauge; it does not touch a nitrate record. Materialising the merged series belongs to chunk 6, which owns everything in `processed2/water/` -- keeping the decision and its consequence in separate chunks is what lets a changed `merge_decisions.csv` be a re-read rather than a rebuild.

WHY SNAP LIVES HERE AND NOT IN CHUNK 1. Chunk 1's rule is "everything fetched, nothing derived", and the snap is a derivation -- a nearest-line join against the flowline layer chunk 1 downloaded. It sits with identity because identity is its only consumer until chunk 4, so the two are one coupling rather than a pipeline.

NOTHING MERGES WITHOUT A RECORDED DECISION. Candidates are written to `merge_candidates.csv` with their evidence; a human confirms in `merge_decisions.csv`, which is tracked in git. A first run therefore reports N pending and merges nothing, and that is the designed behaviour. `--force` re-snaps and re-reads the decisions file; it never rewrites or discards it.
"""

from __future__ import annotations

# Running this file directly leaves the repo root off sys.path, so the relative imports below fail with "attempted relative import with no known parent package". Re-exec as a module instead of explaining the fix.
if __name__ == "__main__" and __package__ in (None, ""):
    import pathlib as _pl
    import runpy as _rp
    import sys as _sys

    _root = _pl.Path(__file__).resolve().parents[3]
    _sys.path.insert(0, str(_root))
    _rp.run_module("src.build2.chunk3.run_chunk3", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

import argparse

import pandas as pd

from .. import config, io as bio, schema
from . import gauges, identity, site_type, snap


def _attach(sites: pd.DataFrame, block: pd.DataFrame) -> pd.DataFrame:
    """Replace the snap block on `sites`, matched on site_uid. Drop-then-merge, never merge onto existing columns, which would produce `comid_x`/`comid_y` and fail `schema.conform`."""
    cols = [c for c in block.columns if c != "site_uid"]
    return sites.drop(columns=cols, errors="ignore").merge(block, on="site_uid", how="left")


def main(force: bool = False, review: bool = False) -> pd.DataFrame:
    if not config.REGISTRATIONS_PATH.exists():
        raise FileNotFoundError(f"{config.REGISTRATIONS_PATH} is missing -- run chunk 0 first")
    sites = pd.read_parquet(config.REGISTRATIONS_PATH)
    print(f"  {len(sites):,} registrations")

    print("\n[3.1] snap  (nearest reach + containing catchment)")
    # INCREMENTAL, because admission is incremental. The test used to be `comid.isna().all()` -- snap only
    # when NOTHING is snapped -- which is all-or-nothing on a table that grows: once any site had a comid,
    # every registration admitted afterwards kept a null one forever, so it could not become a node, take a
    # basin or pair with anything, and nothing said so. The 15 WQP stations landed exactly there.
    #
    # `snap_status` rather than `comid` marks the attempt: a site that was tried and landed nowhere has a
    # status and no comid, and retrying it every run would pay for the network layers to learn the same
    # nothing. `--force` still re-snaps the whole table.
    todo = sites if force else sites[sites.snap_status.isna()]
    if len(todo):
        print(f"  snapping {len(todo):,}/{len(sites):,} site(s)"
              + ("" if force else " never attempted before"))
        block = snap.main(todo)
        # A PARTIAL BLOCK MUST NOT BLANK THE REST. `_attach` drops the block's columns and left-joins, which
        # is right only when the frame covers every registration; `update_block` writes where the run has a
        # row and leaves every other site alone. Same rule chunks 1 and 2 use, and the same trap its
        # docstring records erasing the record block for 232 sites.
        sites = (_attach(sites, block) if force
                 else schema.update_block(sites, block, [c for c in block.columns if c != "site_uid"]))
    else:
        print(f"  every registration has been snapped ({int(sites.comid.notna().sum()):,} landed); "
              f"--force to re-snap")

    print("\n[3.2] site type  (evidence-ranked)")
    sites = _attach(sites, site_type.classify(sites))
    n_conf = int(sites.site_type_conflict.notna().sum())
    print(f"  {sites.site_type_source.value_counts().to_dict()}")
    print(f"  {n_conf} site(s) with evidence that disagrees -- recorded, not applied")

    print("\n[3.3] identity  (shared-reach candidates)")
    sites, cand = identity.main(sites)

    out = schema.conform(sites)
    bad = schema.validate(out)
    if bad:
        raise ValueError("site table violates the contract:\n  " + "\n  ".join(bad))
    bio.write_parquet(out, config.REGISTRATIONS_PATH)
    print(f"  wrote {config.REGISTRATIONS_PATH.name}")

    print("\n[3.4] review gates")
    _gate(cand, sites=out, review=review)

    # Reached only when nothing is pending -- `_gate` exits otherwise. The gauge table is written AFTER the
    # gates on purpose: a collapse over undecided identities would publish a unit a later decision changes,
    # and chunk 4 keys its nodes on this file.
    print("\n[3.5] collapse  (registrations -> physical gauges)")
    gauges.main(out)
    return out


def _name_collateral(pend_merge, sites: pd.DataFrame) -> None:
    """Print which gauges ALREADY IN THE COHORT a pending pair withholds, and what their records are worth.

    A PENDING PAIR COSTS SOMEBODY ELSE. `identity.apply_decisions` nulls `physical_gauge_group` for BOTH members, and chunk 6 then drops them on `identity_undecided` -- so proposing a new registration against an established gauge withdraws that gauge until a person answers. Without this the reviewer sees a list of unfamiliar pairs and no reason to treat any of them as urgent; with it they see that one row is holding 4,404 nitrate days out of the cohort.
    """
    if not config.SITES_PATH.exists():
        return
    try:
        prev = set(pd.read_parquet(config.SITES_PATH, columns=["site_uid"]).site_uid)
    except Exception:                                   # noqa: BLE001 -- an unreadable cohort is not a gate
        return
    held = sorted({u for pair in pend_merge for u in pair} & prev)
    if not held:
        return
    days = dict(zip(sites.site_uid, pd.to_numeric(sites.get("n_nitrate_days"), errors="coerce")))
    total = sum(days.get(u) or 0 for u in held)
    print(f"\n  {len(held)} gauge(s) ALREADY IN THE COHORT are withheld until these are answered "
          f"({int(total):,} nitrate days):")
    for u in sorted(held, key=lambda x: -(days.get(x) or 0)):
        print(f"    {u:24s} {int(days.get(u) or 0):>6,} days")


def _gate(cand: pd.DataFrame, sites: pd.DataFrame, review: bool = False) -> None:
    """Stop the build while any merge pair OR any ambiguous site type is undecided.

    ONE GATE, BOTH QUEUES. Two separate exits would show a reviewer half the outstanding work, send them away to fix it, then stop them again on the other half. Both panels render and both pending lists print before the build stops once.

    A HARD STOP, NOT A WARNING. Everything after chunk 3 is keyed on the merge outcome -- basins are delineated per gauge, `sites.parquet` collapses merged registrations, and the published water is concatenated across a seam -- so continuing past an undecided pair produces a cohort that is wrong in a way no downstream test attributes back to identity. A warning would be read once and then scrolled past on every subsequent run.

    Exits via `SystemExit`, which is not an error: the build did what it could and is waiting on a person. The panel is rendered on this path whether or not `--review` was passed, because this is precisely the moment it is needed and asking the reviewer to run a second command to see the evidence is friction for no gain.
    """
    from . import merge_review, site_type_review

    pend_merge, pend_type = [], []
    if len(cand):
        # THE SHEET COVERS THE QUEUE, NOT THE CANDIDATE SET. Row N of `merge_decisions.csv` is row N of the
        # panel, and the panel is drawn from `merge_candidates.csv`, which now holds only the pairs a person
        # must answer. Syncing the full set here would put hundreds of rule-settled pairs on a sheet nobody
        # is being asked about and break that correspondence. What the rule settled is in `merge_auto.csv`.
        cand = cand[identity.blocks(cand)].reset_index(drop=True)
    if len(cand):
        a, k, rt = identity.sync_decisions_file(cand)
        print(f"  {config.MERGE_DECISIONS.name}: {k} carried forward, {a} new"
              + (f", {rt} retired (kept, unnumbered)" if rt else ""))
        _dec = identity.load_decisions()
        pend_merge = identity.pending_pairs(identity.order_for_review(cand, _dec), _dec)

    amb = site_type_review.review_set(sites)
    auto = site_type_review.auto_set(sites)
    if len(auto):
        # The counterpart to `merge_auto.csv`: ambiguous sites the classifier settled because no record
        # makes their type consequential. Written every run, not only when the panel renders.
        bio.write_csv(auto, config.TYPE_AUTO)
        print(f"  {len(auto)} ambiguous site(s) settled by the classifier "
              f"({auto.ambiguity.value_counts().to_dict()}) -- no record, so no cohort effect; "
              f"see {config.TYPE_AUTO.name}")
    if len(amb):
        a, k, rt = site_type_review.sync_decisions_file(amb)
        print(f"  {config.TYPE_DECISIONS.name}: {k} carried forward, {a} new"
              + (f", {rt} retired (kept, unnumbered)" if rt else ""))
        pend_type = site_type_review.pending(amb)

    if not pend_merge and not pend_type:
        print(f"  all {len(cand)} merge pair(s) and {len(amb)} ambiguous type(s) decided")
        return

    # STALE IS WORSE THAN MISSING. A panel from a previous candidate set opens cleanly and shows the wrong
    # rows in the wrong order, so row N of the sheet points at somebody else's case. Rebuilt whenever it is
    # older than the candidate list it depicts.
    for pend, png, cands, render in (
            (pend_merge, merge_review.out_dir() / "merge_review.png",
             config.MERGE_CANDIDATES, merge_review.main),
            (pend_type, site_type_review.out_dir() / "site_type_review.png",
             config.TYPE_CANDIDATES, site_type_review.main)):
        if not pend:
            continue
        if review or not png.exists() or not cands.exists() or \
                png.stat().st_mtime < cands.stat().st_mtime:
            print(f"  rendering {png.name} ...", flush=True)
            render()

    parts = []
    if pend_merge:
        rows = "\n".join(f"    {a} : {b}" for a, b in pend_merge)
        _name_collateral(pend_merge, sites)
        parts.append(f"Data build awaiting manual merge reviews for:\n{rows}\n"
                     f"\n  panel  {merge_review.out_dir() / 'merge_review.png'}"
                     f"\n  sheet  {config.MERGE_DECISIONS}"
                     f"\n  set `decision` to `merge` or `distinct` on every row")
    if pend_type:
        rows = "\n".join(f"    {u}" for u in pend_type)
        parts.append(f"Data build awaiting manual site-type reviews for:\n{rows}\n"
                     f"\n  panel  {site_type_review.out_dir() / 'site_type_review.png'}"
                     f"\n  sheet  {config.TYPE_DECISIONS}"
                     f"\n  set `decision` to a type: {', '.join(site_type.VALID_TYPES[:7])} ...")
    raise SystemExit("\n" + "\n\n".join(parts) +
                     "\n\nThen resume with:\n  python -m src.build2.run chunk3\n"
                     "  (re-runs chunk 3 and everything after it)\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="re-snap against the current network layers")
    ap.add_argument("--review", action="store_true", help="force both review panels to re-render")
    a = ap.parse_args()
    main(force=a.force, review=a.review)
