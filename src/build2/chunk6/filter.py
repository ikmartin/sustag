"""Every reason a registration does not become a trainable gauge, in one place and in order.

THIS IS THE ONLY PLACE THE BUILD NARROWS. Every other stage widens `registrations.parquet` and drops nothing, because a build-time filter that reaches forward for a later stage's artifact is what caused the old 116<->123 oscillation and made dropped sites unjudgeable. This runs LAST, when every predicate's evidence is already on disk, so it PROJECTS rather than filters -- the registration it leaves out keeps its row, its columns, and now its reason.

TWO STAGES, BECAUSE THE UNIT CHANGES HALFWAY. Predicates 1-6 judge a REGISTRATION; the collapse then merges registrations into physical gauges; predicates 7-9 judge a GAUGE. A record floor applied before the merge would throw away the half that extends a gauge already clearing the bar -- which is why `MIN_NITRATE_DAYS` never existed at collection and this one is applied to the combined series.

EVERY EXCLUSION IS NAMED AND WRITTEN BACK. `reasons()` returns one string per registration, which chunk 6 writes onto `registrations.parquet` as `excluded_reason`. A site missing from `sites.parquet` is then explicable from a single table instead of by re-running the filter and watching where it disappears.
"""

from __future__ import annotations

import pandas as pd

from .. import config, schema
from ..chunk3.site_type import NO_SURFACE_BASIN, SUB_CATCHMENT

# THE MODELLING FLOOR, on the MERGED record. Chunk 1 archives anything with a nitrate observation; this decides
# whether the result is enough to train on. Measured on the current cohort: 60 days leaves 294 gauges, 180
# leaves 275, 365 leaves 243, 730 leaves 196.
MIN_GAUGE_DAYS = 365

# A second floor, on RAW samples rather than distinct days, carried over from the old `filter_sites`. It catches
# the site with many days and almost nothing in each of them -- a year of one grab sample a week clears the day
# floor and is not a continuous record. 10,000 samples is roughly 3.5 months at a 15-minute cadence.
MIN_NITRATE_ROWS = 10_000

# Ordered, and the order is the report's order. Each entry is (key, label, predicate over the frame).
REGISTRATION_PREDICATES = (
    ("no_record", "no nitrate record"),
    ("non_surface", "no surface drainage"),
    ("sub_catchment", "engineered sub-catchment monitoring"),
    ("far_snap", "snap beyond 500 m"),
    ("identity_undecided", "merge decision pending"),
    ("no_basin", "no basin delineated"),
)

GAUGE_PREDICATES = (
    ("short_record", f"under {MIN_GAUGE_DAYS} days"),
    ("few_samples", f"under {MIN_NITRATE_ROWS:,} raw samples"),
    ("corrupt", "excluded by quality review"),
)


def _has_record(reg: pd.DataFrame) -> pd.Series:
    return reg.site_uid.map(
        lambda u: (config.WATER_DAILY / f"{schema.uid_to_filename(u)}.parquet").exists())


def _delineated() -> set:
    """Gauges chunk 4 gave a basin. Empty when chunk 4 has not run, which the caller gates on separately.

    Read with pandas rather than geopandas: `basins.parquet` is a GeoParquet, and asking geopandas for two non-geometry columns raises rather than returning them. The geometry is 19 MB and nothing here needs it.
    """
    if not config.BASINS.exists():
        return set()
    b = pd.read_parquet(config.BASINS, columns=["site_uid", "selection_mode"])
    return set(b.site_uid[b.selection_mode != "none"])


def registrations(reg: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Apply the registration predicates. Returns (survivors, reason per registration indexed by site_uid).

    A registration fails for the FIRST reason that applies, so the counts partition the drops rather than overlapping -- a well with no record is reported once, under the reason that came first.
    """
    reason = pd.Series(pd.NA, index=reg.site_uid, dtype="object")
    ok = pd.Series(True, index=reg.index)

    def apply(mask, key):
        """`mask` is True where the registration PASSES."""
        failing = ok & ~mask
        reason[reg.site_uid[failing].to_numpy()] = key
        ok[failing] = False

    delineated = _delineated()
    apply(_has_record(reg), "no_record")
    apply(~reg.site_type.isin(NO_SURFACE_BASIN), "non_surface")
    # BEFORE `far_snap`, and the order is the point: an edge-of-field sensor is excluded for what it is, not
    # for where it landed. Reported as a snapping problem it would look fixable, and somebody would try.
    apply(~reg.site_type.isin(SUB_CATCHMENT), "sub_catchment")
    apply(reg.snap_status == "ok", "far_snap")
    apply(reg.physical_gauge_group.notna(), "identity_undecided")
    # A gauge is judged by its GROUP's basin: chunk 4 delineates the canonical member, so a non-canonical
    # registration has no basin of its own and would fail a naive membership test.
    apply(reg.physical_gauge_group.isin(delineated) | reg.physical_gauge_group.isna(), "no_basin")
    _no_silent_eviction(reason)
    return reg[ok].reset_index(drop=True), reason


def _no_silent_eviction(reason: pd.Series) -> None:
    """Stop if a gauge that was in the last cohort leaves it because something ELSE was proposed against it.

    `identity_undecided` is the only predicate here whose subject is not the registration it drops. Every other one is a fact about the site -- no record, wrong type, bad snap -- and a site that acquires one has genuinely stopped qualifying. This one fires because a NEW registration landed on the same reach, so an established gauge with years of record can leave the cohort without changing in any way, and the funnel reports it as an ordinary drop.

    That is a regression rather than a filter result, and it is the only exclusion a person can clear in thirty seconds by answering the pair. So it stops the build and says which rows to answer, in the same spirit as chunk 3's gate -- which cannot catch this case, because a build that reaches chunk 6 standalone never ran chunk 3's.
    """
    if not config.SITES_PATH.exists():
        return
    try:
        prev = set(pd.read_parquet(config.SITES_PATH, columns=["site_uid"]).site_uid)
    except Exception:                                   # noqa: BLE001 -- no previous cohort, nothing to regress against
        return
    evicted = sorted(set(reason.index[reason == "identity_undecided"]) & prev)
    if not evicted:
        return
    rows = "\n".join(f"    {u}" for u in evicted)
    raise SystemExit(
        f"\n{len(evicted)} gauge(s) in the previous cohort would leave it only because a merge pair is "
        f"undecided:\n{rows}\n\n"
        f"Nothing about these sites changed -- a new registration was proposed against them. Answer the "
        f"pair(s) in {config.MERGE_DECISIONS} and rerun; the decision is keyed on the uid pair and is read "
        f"on every build.")


def gauges(g: pd.DataFrame, excluded: set = ()) -> tuple[pd.DataFrame, pd.Series]:
    """Apply the gauge predicates to the collapsed table. Returns (survivors, reason per gauge uid).

    `excluded` is the set of uids a human marked `exclude` in the quality review; it is passed in rather than read here so the caller controls whether the review has run at all.
    """
    reason = pd.Series(pd.NA, index=g.site_uid, dtype="object")
    ok = pd.Series(True, index=g.index)

    def apply(mask, key):
        failing = ok & ~mask
        reason[g.site_uid[failing].to_numpy()] = key
        ok[failing] = False

    apply(g.n_nitrate_days >= MIN_GAUGE_DAYS, "short_record")
    if "n_samples" in g.columns:
        apply(g.n_samples >= MIN_NITRATE_ROWS, "few_samples")
    apply(~g.site_uid.isin(set(excluded)), "corrupt")
    return g[ok].reset_index(drop=True), reason


def report(reg_reason: pd.Series, gauge_reason: pd.Series, n_reg: int, n_gauges: int,
           n_final: int) -> None:
    """Print the funnel. Every drop is attributable to exactly one named predicate."""
    print(f"  {n_reg:>4}  registrations")
    for key, label in REGISTRATION_PREDICATES:
        n = int((reg_reason == key).sum())
        if n:
            print(f"  {-n:>4}  {label}")
    print(f"  {n_gauges:>4}  physical gauges after the merge")
    for key, label in GAUGE_PREDICATES:
        n = int((gauge_reason == key).sum())
        if n:
            print(f"  {-n:>4}  {label}")
    print(f"  {n_final:>4}  trainable gauges")


def write_reasons(reg: pd.DataFrame, reg_reason: pd.Series, gauge_reason: pd.Series,
                  members: dict) -> pd.DataFrame:
    """Set `excluded_reason` on every registration. A gauge-level exclusion marks ALL of its members.

    A merged pair excluded for a short record is two registrations that are not in `sites.parquet`, and both need to say so -- attributing it only to the canonical uid would leave the other looking like it had simply vanished.
    """
    out = reg.copy()
    reason = reg_reason.copy()
    for gid, why in gauge_reason.dropna().items():
        for u in members.get(gid, [gid]):
            reason[u] = why
    out["excluded_reason"] = pd.array(out.site_uid.map(reason).to_numpy(), dtype="string")
    return out
