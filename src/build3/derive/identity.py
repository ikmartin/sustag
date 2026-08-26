"""Which registrations are the same physical gauge.

THE REACH PROPOSES, AGREEMENT DISPOSES. Two registrations are candidates when they SHARE A SNAPPED COMID. Distance was the old rule and it asks the wrong question: proximity cannot distinguish a duplicate registration from two real sensors metres apart, because on a river the thing that makes two sensors the same gauge is being on the same reach, not being near each other. Eight Iowa pairs sit 200 m to 1.9 km apart, are concurrent, and correlate at r = 0.006 to 0.589 -- inflow/outflow pairs straddling an engineered nitrate-removal structure, where merging destroys exactly the contrast they exist to measure. The reach rule separates all three of the concurrent ones on different COMIDs; a 250 m radius proposed them.

Measured on the cohort: 58 sites in 27 shared-reach groups, containing 4/4 of the known duplicate pairs and 0/3 of the treatment pairs. Grouping on `catchment_comid` instead reproduces the same 27 groups exactly, which corroborates the partition without adding to it.

FOUR BRANCHES, ordered by how much the evidence settles.
  sequential         a shared channel with no shared days -> one gauge re-registered after a redeployment
  dual_registration  a shared channel agreeing on shape and level -> two programs publishing one instrument
  complementary      NO shared channel, within COMPLEMENTARY_MAX_SEP_M -> one instrumented station whose sensors measure different things
  weak_evidence      the only shared channel is a seasonal one -> undecidable from records, a human judges it
  distinct           a shared channel that disagrees, or no shared channel and too far apart -> different sensors, never merge

`complementary` exists because the cohort is no longer nitrate-only. A nitrate analyser and a discharge gauge bolted to the same structure share a reach, share a location, and share NOTHING in their records -- so every test built on comparing two series is blind to them, and without this branch they enter the graph as two nodes for one place. It is deliberately the LAST resort: it applies only where no shared channel exists at all, because a channel both members report is stronger evidence than proximity ever is.

WHICH CHANNEL DECIDES. Nitrate where both have it, then discharge, then the densest shared channel. The level test is absolute for high-scrutiny channels (mg/L as N, the calibrated threshold), judged against the channel's own `agree_tol` on interval scales (degC, pH -- an arbitrary origin makes a relative difference meaningless), and relative to the pair's own median for everything else.

A non-overlap rule alone is not enough. WQS0082 and USGS-05482500 sit 8.1 m apart, DO overlap over 22 shared days, and agree at r = 0.887 with a median difference of exactly 0.00 mg/L: one gauge, published twice, concurrently. Requiring zero overlap would keep them apart, and so did `DUAL_MIN_R = 0.95`.

NOTHING MERGES WITHOUT A RECORDED DECISION. This step writes a candidate list with its evidence and a proposed branch; a human confirms or overrides in `decisions/merge.csv` (the generic ledger kit), which is tracked in git and keyed on the sorted uid pair so it survives every rebuild. A first run therefore merges nothing and reports pending decisions -- that is the designed behaviour, not a failure. A merge changes the unit of analysis, and a wrong one corrupts basins, graph edges and the concatenated record at once, silently.

COMPONENTS, NOT PAIRS. Grouping is by connected component: three registrations of one gauge produce three pairwise candidates, and applying them in sequence would give an order-dependent answer.

WHAT THE EVIDENCE IS. Shared reach, then the records themselves -- shared days, correlation, median difference -- plus source-reported drainage area and `comid_agree` per member. Separation survives as `sep_m`: still recorded, no longer the trigger.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts as schema, io as bio, ledger, registry

# Correlation alone never merges anything -- `DUAL_MAX_MEDIAN_DIFF` has to pass too, and a human still confirms. Two instruments on the same water agree on SHAPE more reliably than on level: they are different sensors with different calibration drift, so 0.95 was set for a co-located pair of identical instruments rather than for two programs publishing the same river. It withheld `WQS0082 / 05482500` at r = 0.887 with a median difference of exactly 0.00 mg/L -- 8.1 m apart, the pair the old build called one gauge published twice.
DUAL_MIN_R = 0.80
DUAL_MAX_MEDIAN_DIFF = 0.25      # mg/L as N -- nitrate only, the calibrated threshold
DUAL_MAX_RELATIVE_DIFF = 0.10    # every other channel, as a fraction of the pair's own median level
MIN_SHARED_DAYS_FOR_R = 10

# Which statistic represents a channel's day when two records are compared -- from the registry,
# where a channel's nature belongs. Nitrate compares on max because the thresholds were calibrated on it.
def _compare_stat(channel: str) -> str:
    return registry.CHANNELS[channel].compare_stat

# Which shared channel decides a pair when several overlap. See `_shared_channel`.
CORRELATION_PREFERENCE = ("nitrate", "discharge", "spec_cond")

# CHANNELS WHOSE AGREEMENT MEANS "SAME RIVER, SAME SEASON" RATHER THAN "SAME INSTRUMENT". Water temperature
# is set by air temperature over a whole basin; stage and dissolved oxygen track it; pH barely varies at all.
# Correlating on them says the two sites share a climate, which every pair on one reach does by construction.
#
# Measured, not assumed: on this cohort `IWQIS:WQS0092` and `USGS:05482000` sit 1,878 m apart and agree on
# water temperature at r = 0.9936, and two more pairs at 197 m and 441 m agree above 0.91. None of them is
# one gauge. A pair whose ONLY shared channel is one of these cannot be judged from its records, so the rule
# says so rather than guessing -- `weak_evidence` goes to a human instead of proposing a merge nobody checked.
# THE WEAK SET LIVES IN THE REGISTRY as `discriminating=False` -- a channel's nature, not identity's
# opinion. Derived here so the two can never disagree; the calibration (water_temp r=0.9936 at 1,878 m;
# 0 of 21 pairs disturbed when turbidity moved in) rides with the registry entries.
WEAK_FOR_IDENTITY = frozenset(set(registry.NAMES) - registry.discriminating())

# TWO SENSORS ON ONE REACH REPORTING DISJOINT CHANNELS ARE ONE INSTRUMENTED STATION, not two gauges -- a
# nitrate analyser and a discharge gauge on the same structure are the common case now that every sensor is
# a node, and nothing in a record comparison can see it because the records have nothing in common.
#
# The radius is tight ON PURPOSE. The known duplicate pair sits 8.1 m apart, while the inflow/outflow
# treatment pairs that must never merge are 200 m-1.9 km apart and are ALREADY separated by the reach rule.
# Beyond about a hundred metres a shared reach stops being evidence of a shared structure: a long reach can
# carry two genuinely different stations kilometres apart, and merging those destroys the contrast they
# exist to measure.
COMPLEMENTARY_MAX_SEP_M = 100.0

# A DUPLICATE REGISTRATION IS ONE STRUCTURE, so agreement alone does not establish one. Two gauges on the
# same river kilometres apart correlate at 0.9 with near-equal medians BECAUSE IT IS THE SAME RIVER, and the
# record cannot tell that from one instrument published twice. Measured on this cohort the two true
# duplicates sit at 8 m and 44 m while the two suspect pairs the old rule would have merged sit at 1,566 m
# and 2,141 m -- two orders of magnitude, and the only variable that separates them.
#
# Distance is deliberately absent from CANDIDATE GENERATION, where a radius missed a real pair 822 m apart.
# That argument is about which pairs to LOOK at; this is about what the evidence supports, and there
# proximity is doing work correlation cannot. `sequential` keeps no ceiling for the same reason -- a
# re-registration is a handoff in time, and the 822 m pair is one.
DUAL_MAX_SEP_M = 250.0

# Proposals that would COLLAPSE TWO REGISTRATIONS INTO ONE GAUGE. The asymmetry that gates on them: a wrong
# merge destroys the contrast it merged and leaves nothing behind to detect it, while a wrong `distinct`
# leaves two nodes with near-identical basins, which D4's edge table reports as
# `near_duplicate_basin` without `crosses_impoundment`. One failure has a downstream detector; the other
# does not, so only one needs a person.
MERGE_PROPOSALS = frozenset({"dual_registration", "sequential", "complementary"})

# Hard calls first. `sequential` pairs have no overlap, so no correlation exists to judge them by, and they are
# what the review panel is built around; `distinct` needs the least thought. ONE ordering, used by both the
# decisions worksheet and the panel, so row N of the CSV is row N of the PNG.
REVIEW_ORDER = ("sequential", "complementary", "weak_evidence", "dual_registration",
                "unknown_no_records", "distinct")

# Sheet layout, vocabulary and blank-means-unreviewed all live in `ledger.MERGE` -- one kit, zero copies.
# The evidence columns a reviewer sees, in the order they help: names first, then the proposal, then the record comparison.
EVIDENCE_COLUMNS = ["name_a", "name_b", "proposed", "sep_m", "on_channel", "shared_days", "r",
                    "median_diff", "channels_a", "channels_b", "agree_a", "agree_b", "seam"]
CANDIDATES_PATH = config.REVIEW_DIR / "merge_candidates.csv"


def _canonical(uid: str) -> pd.DataFrame | None:
    """The site's canonical daily frame from chunk 2, indexed on date. `None` when it has not been binned."""
    p = config.WATER_CANONICAL / f"{schema.uid_to_filename(uid)}.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    return d.set_index(pd.to_datetime(d.date)).drop(columns="date").sort_index()


def channels_of(frame: pd.DataFrame | None) -> list[str]:
    """Canonical channels this frame carries a value for, in registry order."""
    if frame is None or not len(frame):
        return []
    return [c for c in registry.NAMES
            if f"{c}_mean" in frame.columns and frame[f"{c}_mean"].notna().any()]


def _series(frame: pd.DataFrame | None, channel: str) -> pd.Series | None:
    """One channel's daily comparison value. Nitrate uses the day's MAX, everything else the mean.

    Nitrate is the max because that is the number the merge thresholds below were measured against -- the daily file the rule was calibrated on -- so keeping it preserves every decision already on record. For any other channel a daily maximum is not what two instruments agree about; the mean is.
    """
    if frame is None:
        return None
    col = f"{channel}_{_compare_stat(channel)}"
    if col not in frame.columns:
        return None
    s = frame[col].dropna()
    return s if len(s) else None


def _agreement(sa: pd.Series, sb: pd.Series, channel: str) -> tuple[float, float, float, bool]:
    """`(shared_days, r, median_diff, agrees)` for one shared channel.

    The level test reads the channel's REGISTRY comparison spec. High-scrutiny channels are judged on the ABSOLUTE median difference in mg/L as N, the calibrated threshold. An INTERVAL scale (degC, pH) is judged against its own `agree_tol`, because an arbitrary origin makes a relative difference meaningless -- two thermistors at different depths differ by a fraction of a degree, which as a RATIO near freezing reads as a large error and is not one; the build2 ancestor fell into the relative branch here, and never bit only because every interval channel was also weak, which the per-channel merge no longer guarantees. Everything else is RELATIVE to the pair's own median, since 0.25 units means nothing shared between discharge in m3/s and pH.
    """
    j = pd.concat([sa.rename("a"), sb.rename("b")], axis=1).dropna()
    if not len(j):
        return 0.0, np.nan, np.nan, False
    r = float(j.a.corr(j.b)) if len(j) >= MIN_SHARED_DAYS_FOR_R else np.nan
    mdiff = float((j.a - j.b).median())
    spec = registry.CHANNELS[channel]
    if channel in registry.high_scrutiny():
        close = abs(mdiff) <= DUAL_MAX_MEDIAN_DIFF
    elif spec.scale_kind == "interval":
        close = abs(mdiff) <= spec.agree_tol
    else:
        level = float(pd.concat([j.a, j.b]).abs().median())
        close = abs(mdiff) <= DUAL_MAX_RELATIVE_DIFF * level if level > 0 else abs(mdiff) < 1e-9
    return float(len(j)), r, mdiff, bool(close and pd.notna(r) and r >= DUAL_MIN_R)


def _strong_shared(ca: list[str], cb: list[str]) -> list[str]:
    """Shared channels that DISCRIMINATE -- the overlap minus the ones that only prove a shared climate."""
    return [c for c in ca if c in cb and c not in WEAK_FOR_IDENTITY]


def _shared_channel(ca: list[str], cb: list[str], fa, fb) -> str | None:
    """Which shared channel decides the pair. Preference order first, then the densest overlap.

    Nitrate leads because it is the objective and the thresholds were measured on it; discharge next because it is the densest physical signal two gauges can share. Falling through to "most shared days" keeps a pair judgeable when neither is present.
    """
    shared = [c for c in ca if c in cb]
    if not shared:
        return None
    for c in CORRELATION_PREFERENCE:
        if c in shared:
            return c
    # A discriminating channel is always preferred over a seasonal one, however much denser the seasonal one is.
    strong = [c for c in shared if c not in WEAK_FOR_IDENTITY] or shared

    def n(f, c):
        s = _series(f, c)
        return 0 if s is None else len(s)      # `s or 0` would call bool() on a Series and raise

    return max(strong, key=lambda c: n(fa, c) + n(fb, c))


def candidates(sites: pd.DataFrame) -> pd.DataFrame:
    """Every pair of registrations sharing a snapped reach, with the evidence needed to judge it.

    Requires the snap block. Rows with no `comid` -- a failed snap -- propose nothing and are never candidates, which is correct: an unlocated registration cannot be shown to share a reach with anything.

    A REGISTRATION WITH NO CANONICAL RECORD IS NOT ELIGIBLE EITHER. It cannot be judged -- every branch here rests on comparing two records, so a pair missing one comes back `unknown_no_records` and sits pending forever -- and merging it would change nothing, because it contributes no rows whether bundled or not. D2 canonicalises every collected channel, so an absent file means the source published no usable record of ANY kind.

    A SHORT record is a different case and IS eligible. Nine IWQIS sites carrying 1 to 42 days were withheld by build2's old 60-day collection floor, and four of them share a reach with a gauge that clears the bar -- days that extend a real record, invisible while the floor discarded them at fetch time. Record floors live in ACCESS PROFILES now, applied to the MERGED site record; here a registration only has to be judgeable.

    This is NOT the build dropping a site. The registration keeps its row and every column it had; it is simply not offered as half of a merge nobody can adjudicate. Eligibility is read off the records on disk each run, so a site that gains one later becomes a candidate then.
    """
    import itertools

    import geopandas as gpd

    s = sites.reset_index(drop=True)
    if "comid" not in s.columns:
        raise KeyError("candidates() needs the snap block -- run snap.main() before identity")

    has_rec = s.site_uid.map(
        lambda u: (config.WATER_CANONICAL / f"{schema.uid_to_filename(u)}.parquet").exists())
    if not has_rec.all():
        print(f"  {int((~has_rec).sum())} registration(s) have no canonical record -- "
              f"not eligible to pair")
    s = s[has_rec].reset_index(drop=True)
    if s.empty:
        print("  no registration has a record yet; no candidates can be judged")
        return pd.DataFrame(columns=["uid_a", "uid_b"])

    # Projected AFTER the eligibility filter so positional indices into `xy` and `s` cannot drift apart.
    g = gpd.GeoSeries(gpd.points_from_xy(s.lon, s.lat), crs=4326).to_crs(config.EQUAL_AREA)
    xy = np.c_[g.x, g.y]

    # Row order in, row order out: the pair list is built by iterating the table, so it does not depend on dict or spatial-index ordering.
    by_reach: dict[int, list[int]] = {}
    for i, c in enumerate(s.comid):
        if pd.notna(c):
            by_reach.setdefault(int(c), []).append(i)
    pairs = [p for idx in by_reach.values() if len(idx) > 1 for p in itertools.combinations(idx, 2)]
    if not pairs:
        return pd.DataFrame(columns=["uid_a", "uid_b"])

    names = s.set_index("site_uid").station_name
    agree = s.set_index("site_uid").get("comid_agree", pd.Series(dtype="boolean"))
    frames: dict[str, pd.DataFrame | None] = {}
    chans: dict[str, list[str]] = {}
    rows = []
    for i, j in pairs:
        a, b = s.iloc[i], s.iloc[j]
        ua, ub = sorted([a.site_uid, b.site_uid])
        for u in (ua, ub):
            if u not in frames:
                frames[u] = _canonical(u)
                chans[u] = channels_of(frames[u])
        fa, fb = frames[ua], frames[ub]
        ca, cb = chans[ua], chans[ub]
        sep = float(np.hypot(*(xy[i] - xy[j])))

        shared = r = mdiff = np.nan
        seam, on = pd.NA, pd.NA
        agrees = False
        ch = _shared_channel(ca, cb, fa, fb)
        if ch is not None:
            on = ch
            sa, sb = _series(fa, ch), _series(fb, ch)
            shared, r, mdiff, agrees = _agreement(sa, sb, ch)
            if shared == 0:
                lo, hi = (sa, sb) if sa.index.max() <= sb.index.min() else (sb, sa)
                seam = f"{lo.index.max().date()}..{hi.index.min().date()}"

        # THE ORDER IS BY HOW MUCH THE EVIDENCE SETTLES, and complementarity comes early because DISJOINT
        # INSTRUMENTS ARE POSITIVE EVIDENCE OF ONE STATION. Nobody deploys two sensors metres apart to
        # measure different things unless it is one installation -- a stilling well with a stage sensor, a
        # nitrate analyser on the rail, a temp probe. Two sensors measuring the SAME thing that close is the
        # weaker case, not the stronger one: it is either two agencies duplicating each other or a pair
        # straddling a treatment, and the fact of overlap cannot say which.
        #
        # DISJOINT IN THE CHANNELS THAT DISCRIMINATE, not in every channel. The test used to be `ch is None`
        # -- no shared channel at all -- while `_shared_channel` returns one on any overlap including a weak
        # one, so a pair sharing nothing but water temperature was barred from this branch by a channel the
        # module itself says means "same climate". Two in-field instruments 8 m apart, one measuring nitrate
        # and the other turbidity, were judged on their turbidity correlation for exactly that reason.
        strong = _strong_shared(ca, cb)
        if not ca or not cb:
            rule = "unknown_no_records"
        elif not strong and sep <= COMPLEMENTARY_MAX_SEP_M:
            rule = "complementary"
        elif ch is None:
            rule = "distinct"
        elif shared == 0:
            # No overlap at all, so no correlation exists to be weak: a redeployment reads the same
            # whichever channel witnesses the gap.
            rule = "sequential"
        elif ch in WEAK_FOR_IDENTITY:
            rule = "weak_evidence"
        elif agrees and sep <= DUAL_MAX_SEP_M:
            rule = "dual_registration"
        else:
            rule = "distinct"

        rows.append(dict(
            uid_a=ua, uid_b=ub,
            name_a=names.get(ua), name_b=names.get(ub),
            comid=int(a.comid),
            sep_m=round(sep, 2),
            on_channel=on,
            channels_a="+".join(ca) if ca else pd.NA,
            channels_b="+".join(cb) if cb else pd.NA,
            shared_days=None if np.isnan(shared) else int(shared),
            r=None if np.isnan(r) else round(r, 4),
            median_diff=None if np.isnan(mdiff) else round(mdiff, 4),
            area_a_km2=a.source_drainage_area_km2, area_b_km2=b.source_drainage_area_km2,
            # Whether each member's containing catchment agrees with its snap. False means that member sits near a divide, so the shared reach is less certain -- the reviewer needs that in the same row as the correlation, not two lookups away.
            agree_a=agree.get(ua, pd.NA), agree_b=agree.get(ub, pd.NA),
            proposed=rule, seam=seam,
            evidence="shared_comid+records+source_area",
        ))
    return pd.DataFrame(rows).sort_values(["sep_m", "uid_a"], ignore_index=True)


def order_for_review(cand: pd.DataFrame, dec: pd.DataFrame | None = None) -> pd.DataFrame:
    """Candidates in review order: UNANSWERED FIRST, then by branch (`REVIEW_ORDER`), then by separation.

    The single source of this ordering. `merge_decisions.csv` and `merge_review.png` both use it, which is what lets a reviewer read "Merge Pair 7" off the panel and edit row 7 of the sheet without cross-referencing uids -- so the sort has to live here rather than in either of them, and both have to be handed the same decisions.

    WHAT IS OUTSTANDING SORTS TO THE TOP. A sheet ordered only by branch buries the rows still needing an answer among the ones already settled, and the reviewer's job is to find the former. Answering a pair sinks it, which is the behaviour you want from a queue: the work remaining is always at row 1. Pass `dec` to get it; without it the ordering is by branch alone, which is what a reader wanting the full evidence in a stable order should ask for.
    """
    if not len(cand):
        return cand
    rank = {b: i for i, b in enumerate(REVIEW_ORDER)}
    out = cand.copy()
    if dec is not None and len(dec):
        answered = {tuple(sorted((a, b))) for a, b, d in zip(dec.uid_a, dec.uid_b, dec.decision)
                    if str(d).strip() and str(d).strip().lower() != "nan"}
        out["_done"] = [1 if tuple(sorted((a, b))) in answered else 0
                        for a, b in zip(out.uid_a, out.uid_b)]
    else:
        out["_done"] = 0
    out["_k"] = out.proposed.map(rank).fillna(len(REVIEW_ORDER))
    return (out.sort_values(["_done", "_k", "sep_m"], ignore_index=True)
               .drop(columns=["_done", "_k"]))


def high_tier_on_either_side(cand: pd.DataFrame) -> pd.Series:
    """Does either side carry a HIGH-SCRUTINY channel? The stewardship tier, not any model's target."""
    tier = registry.high_scrutiny()

    def has(col):
        return col.fillna("").astype(str).str.split("+").apply(lambda v: bool(tier & set(v)))

    return has(cand.channels_a) | has(cand.channels_b)


def blocks(cand: pd.DataFrame) -> pd.Series:
    """Which candidate pairs must be answered by a person. Everything else takes the rule's proposal.

    TWO CONDITIONS, AND BOTH ARE ABOUT CONSEQUENCE. A pair blocks when it would COLLAPSE two registrations
    into one gauge and when at least one side carries a HIGH-SCRUTINY channel -- the two facts that decide whether a wrong
    answer reaches the cohort and whether anything downstream could notice.

    EITHER SIDE, NOT BOTH. A one-sided pair is a nitrate analyser being merged into a discharge gauge on a
    hundred-metre radius with no record evidence at all, which rewrites that gauge's identity, its canonical
    uid and its channel set. Requiring both sides would auto-apply exactly the merges resting on the
    thinnest evidence in the system.

    `distinct` NEVER BLOCKS. It changes nothing -- the two registrations stay as they were -- and if it is
    wrong the result is two nodes with near-identical basins, which D4's edge table reports as `near_duplicate_basin`
    with no impoundment between them. A wrong merge leaves no such trace, because the evidence for it having
    been wrong is the thing the merge consumed.
    """
    if not len(cand):
        return pd.Series(dtype="bool")
    high = high_tier_on_either_side(cand)
    return high & cand.proposed.isin(MERGE_PROPOSALS)


def pending_pairs(cand: pd.DataFrame, dec: pd.DataFrame) -> list[tuple[str, str]]:
    """Blocking pairs with no usable decision on file, in review order. Empty means the gate is clear."""
    if not len(cand):
        return []
    decided = {tuple(sorted((a, b))) for a, b in zip(dec.uid_a, dec.uid_b)} if len(dec) else set()
    gate = blocks(cand)
    return [(a, b) for a, b, g in zip(cand.uid_a, cand.uid_b, gate)
            if g and tuple(sorted((a, b))) not in decided]


def sync_decisions_file(cand: pd.DataFrame) -> tuple[int, int, int]:
    """Reconcile `decisions/merge.csv` with the current candidates via the ledger kit. Returns (added, kept, retired).

    Written with `decision` BLANK for a new pair, never guessed at from `proposed` -- the gate exists so a merge is asserted by a person rather than inherited from a threshold. Existing decisions carry forward on the uid pair, so re-running never costs work already done. The sheet is synced in `order_for_review` order, which is what lets row N of the CSV be row N of the panel.
    """
    if not len(cand):
        return 0, 0, 0
    ordered = order_for_review(cand, load_decisions())
    cols = ["uid_a", "uid_b"] + [c for c in EVIDENCE_COLUMNS if c in ordered.columns]
    return ledger.MERGE.sync(ordered[cols])


def high_tier_days(sites: pd.DataFrame) -> pd.Series:
    """Record days per uid for picking a bundle's canonical member: HIGH-TIER days first, total days breaking ties.

    Prefers the canon block's per-channel `n_days_*` counts (D2 already measured them) and falls back to counting the canonical file's days, so identity can run standalone before the canon columns are joined on. A uid with neither is 0, which sorts it last. Total days matter only for covariate-only bundles, where no high-tier record exists to prefer -- the longest record still names the bundle deterministically.
    """
    tier = registry.high_scrutiny()
    tier_cols = [f"n_days_{c}" for c in tier if f"n_days_{c}" in sites.columns]
    all_cols = [f"n_days_{c}" for c in registry.NAMES if f"n_days_{c}" in sites.columns]

    out = {}
    for _, row in sites.iterrows():
        uid = row.site_uid
        ht = sum(int(v) for c in tier_cols if pd.notna(v := row[c]))
        tot = sum(int(v) for c in all_cols if pd.notna(v := row[c]))
        if not all_cols or (ht == 0 and tot == 0):
            f = _canonical(uid)
            if f is not None:
                ht = sum(int(x.notna().sum()) for c in tier if (x := _series(f, c)) is not None)
                tot = len(f)
        # Composite so one min() key ranks both: tier days dominate, total days break covariate-only ties.
        out[uid] = ht * 10_000_000 + tot
    return pd.Series(out, dtype="int64")


def load_decisions() -> pd.DataFrame:
    """Human merge decisions via the ledger kit: rows carrying a verdict, blank means NOT YET REVIEWED, unknown tokens raise."""
    return ledger.MERGE.load()


def apply_decisions(sites: pd.DataFrame, cand: pd.DataFrame, dec: pd.DataFrame) -> pd.DataFrame:
    """The bundle block: connected components over CONFIRMED merges, stamped per the sensors contract.

    Returns the BLOCK -- `site_uid` plus `bundle`, `is_bundle_canonical`, `bundle_rule`, `bundle_decided_by` -- not the widened table; the D3 orchestrator joins it on with `update_block`, and `site_id` / `excluded_reason` belong to sites.py, which mints ids only after the bundle is settled.

    `bundle` is the '+'-joined SORTED member-uid set, which is the LEDGER KEY for every site-keyed decision downstream -- it survives coordinate revisions and re-snaps, which a minted id cannot. The canonical member carries the most HIGH-TIER record days, with `schema.CANONICAL_PRECEDENCE` breaking ties and the uid breaking those. Source precedence alone is the wrong rule: on the Cedar River the MPCA and USGS registrations of one gauge differ by years of record, and naming the bundle after the shorter series would hide the longer one behind a non-canonical uid.

    A SENSOR IN AN UNDECIDED PAIR HAS NO BUNDLE YET. Components over confirmed merges alone would make every unreviewed sensor a singleton -- for one with no candidates that is the truth, but for one sitting in a pending pair it is a false assertion: both halves of a duplicate would carry `is_bundle_canonical = True` and anything trusting the flag double-counts. Null is what "not yet decided" looks like; `bundle_decided_by = "pending"` names the state.
    """
    import networkx as nx

    out = sites[["site_uid"]].copy()
    decisions = ({tuple(sorted((a, b))): str(d).strip() for a, b, d in zip(dec.uid_a, dec.uid_b, dec.decision)}
                 if len(dec) else {})
    confirmed = {k for k, d in decisions.items() if d == "merge"}
    rejected = {k for k, d in decisions.items() if d == "distinct"}

    by_rule = set()
    gate_of, prop_of = {}, {}
    if len(cand):
        # THE RULE DECIDES WHAT IT IS ALLOWED TO DECIDE. A pair that does not block takes its proposal --
        # merge where the proposal is one, stay apart otherwise -- so the queue holds only what a person can
        # actually adjudicate. A human decision on file still outranks the rule, in both directions.
        gate = blocks(cand)
        for (ua, ub), prop, g in zip(zip(cand.uid_a, cand.uid_b), cand.proposed, gate):
            key = tuple(sorted((ua, ub)))
            gate_of[key], prop_of[key] = bool(g), prop
            if g or key in confirmed or key in rejected:
                continue
            if prop in MERGE_PROPOSALS:
                by_rule.add(key)
        confirmed |= by_rule

    pending = {k for k, g in gate_of.items() if g and k not in decisions}
    pending_uids = {u for k in pending for u in k}

    g = nx.Graph()
    g.add_nodes_from(out.site_uid)
    g.add_edges_from(confirmed)

    prec = sites.set_index("site_uid").source.map(schema.CANONICAL_PRECEDENCE).fillna(99)
    days = high_tier_days(sites)
    bundle_of, canon_of = {}, {}
    contradictions = []
    for comp in nx.connected_components(g):
        members = sorted(comp)
        # THE CONTRADICTION HALT. Merging is transitive by construction, so A-B and B-C `merge` fuse A and
        # C whatever anyone said about the pair A-C -- and if a HUMAN said `distinct`, the decisions do not
        # partition the reach consistently and the walk must not paper over it. Only a human `distinct`
        # contradicts: the rule's `distinct` means "no pairwise evidence to merge", and a chain of
        # re-registrations legitimately fuses two sensors the rule proposed apart (A->B->C sequential,
        # A and C never overlapping). A wrong fusion leaves no trace downstream, because the evidence for
        # it having been wrong is exactly what the fusion consumed -- so this is a halt, not a warning.
        if len(comp) > 1:
            violated = [k for k in rejected if k[0] in comp and k[1] in comp]
            if violated:
                chain = [k for k in confirmed if k[0] in comp and k[1] in comp]
                contradictions.append((members, violated, chain))
                continue
        canon = min(comp, key=lambda u: (-int(days.get(u, 0)), prec.get(u, 99), u))
        for u in comp:
            bundle_of[u], canon_of[u] = "+".join(members), canon
    if contradictions:
        lines = []
        for members, violated, chain in contradictions:
            lines.append(f"    component {'+'.join(members)}")
            for a, b in violated:
                lines.append(f"      ruled DISTINCT by a person: {a} : {b}")
            for a, b in chain:
                lines.append(f"      fused by merge:            {a} : {b}")
        raise SystemExit(
            f"\n{len(contradictions)} reach component(s) where the decisions contradict each other -- "
            f"merges fuse a pair a person ruled distinct:\n" + "\n".join(lines) +
            f"\n\nThe decisions do not partition these sensors consistently. Flip one side in "
            f"{ledger.MERGE.path} (either the distinct, or a merge in the chain) and rerun; "
            f"nothing was bundled for these components.")

    out["bundle"] = out.site_uid.map(bundle_of)
    # NULLABLE, because pending pairs write pd.NA into it below. A numpy bool column raises on that
    # assignment -- and only when something is actually pending, so a numpy-typed version runs green
    # for exactly as long as every pair happens to be decided.
    out["is_bundle_canonical"] = pd.array(out.site_uid == out.site_uid.map(canon_of), dtype="boolean")
    out["bundle_rule"] = pd.Series("singleton", index=out.index, dtype="object")
    out["bundle_decided_by"] = pd.Series("n/a", index=out.index, dtype="object")

    for key, prop in prop_of.items():
        state = ("human" if key in decisions else
                 "rule" if key in by_rule or not gate_of[key] else "pending")
        merged = key in confirmed
        for u in key:
            sel = out.site_uid == u
            if not sel.any():
                continue
            out.loc[sel, "bundle_rule"] = prop if merged else "distinct"
            out.loc[sel, "bundle_decided_by"] = state

    if pending_uids:
        undecided = out.site_uid.isin(pending_uids)
        out.loc[undecided, ["bundle", "bundle_rule"]] = pd.NA
        out.loc[undecided, "is_bundle_canonical"] = pd.NA
        out.loc[undecided, "bundle_decided_by"] = "pending"
        print(f"  {int(undecided.sum())} sensor(s) in an undecided pair: bundle left NULL")
    return out


def main(sites: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    sites = sites if sites is not None else bio.read_parquet(config.SENSORS_PATH)
    cand = candidates(sites)
    dec = load_decisions()

    n_groups_in = cand.comid.nunique() if len(cand) else 0
    print(f"candidate pairs sharing a reach: {len(cand)} over {n_groups_in} reach(es)")
    if len(cand):
        print(f"  proposed: {cand.proposed.value_counts().to_dict()}")
        gate = blocks(cand)
        ask, auto = cand[gate], cand[~gate]
        # TWO FILES, BECAUSE THEY ANSWER DIFFERENT QUESTIONS. `merge_candidates.csv` is the review queue and
        # the panel is drawn from it, so row N of one is row N of the other; the auto record is what the rule
        # settled unasked, which is not a queue but must not be invisible either. The per-sensor view of the
        # same fact is `bundle_rule` and `bundle_decided_by` on the sensors table.
        n_auto_merge = int(auto.proposed.isin(MERGE_PROPOSALS).sum())
        print(f"  {len(auto)} pair(s) settled by rule ({n_auto_merge} merged, "
              f"{len(auto) - n_auto_merge} left apart) -- no high-scrutiny channel at stake or nothing to collapse")
        ledger.MERGE.record_auto(auto)
        added, kept, retired = sync_decisions_file(cand)
        pend = pending_pairs(cand, dec)
        print(f"  {len(ask)} pair(s) need a person; decisions on file: {len(dec)}   PENDING: {len(pend)}")
        bio.write_csv(order_for_review(cand, dec), CANDIDATES_PATH)
        print(f"  synced {ledger.MERGE.path.name} (+{added} / {kept} kept / {retired} retired); "
              f"wrote {CANDIDATES_PATH.name} and {ledger.MERGE.auto_path.name}")

    block = apply_decisions(sites, cand, dec)
    n_bundles = block.bundle.nunique()
    n_undecided = int(block.bundle.isna().sum())
    print(f"\n{len(block)} registrations -> {n_bundles} bundle(s) "
          f"({len(block) - n_bundles - n_undecided} merged away, {n_undecided} awaiting a decision)")
    return block, cand


if __name__ == "__main__":
    main()
