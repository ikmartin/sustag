"""Which registrations are the same physical installation.

THE PROXIMITY TABLE PROPOSES, AGREEMENT DISPOSES. Candidates are read off D3's proximity table -- never generated here -- and a pair reaches the evidence branch only through the gate: same material class, both sides record-bearing, and `min_sep_m <= GATE_SEP_M`. The gate uses `min_sep_m`, the closest the two could possibly be given both authorities' declared precision, so a coarse coordinate cannot geometrically excuse a pair from consideration. The threshold is measured, not guessed: every confirmed handoff and duplicate sits at 52 m or less, treatment pairs that must never merge begin around 200 m, and 100 m is ~2x headroom over every accepted case. Pairs whose stated `sep_m` exceeds HESITATE_SEP_M are flagged for the review panel: approach with hesitation, distance displayed prominently.

THE GATE APPLIES TO EVERY BRANCH, SEQUENTIAL INCLUDED. The tempting exemption ("a replacement is a move, allow sequential at any distance") fails on the evidence: zero temporal overlap is a property of ANY two records that merely miss each other, an unbounded sequential branch manufactured 47 false proposals out to 8.7 km on one cohort (one estuary sensor proposing "sequential" with four different partners), and the one long-distance sequential pair ever adjudicated was ruled distinct. A station genuinely relocated beyond the gate becomes two published records, correctly -- whether to model them as one node is a read-time preference the proximity table serves.

THE TYPE GATE HAS TWO TIERS. Pairs crossing the MATERIAL boundary (`site_type.material`: groundwater / atmosphere / surface water) never merge and never surface -- a well and a stream sensor are different water by physical certainty, auto-distinct with the condition named. Pairs of DIFFERENT SURFACE TYPES (lake x stream, ditch x stream) are never auto-merged but DO queue to a human with the mismatch displayed: the type is inferred and noisy enough to carry its own conflict-review ledger, and a silent auto-reject keyed on a noisy label would kill a true dual registration with no witness. A sensor typed `unknown` WAITS: its pairs are neither judged nor rejected until a human types it, and the type ledger is where that happens.

RECORD-LESS REGISTRATIONS ARE A FILTER, NOT A VERDICT. A pair with a record-less side gets NO row anywhere: it cannot be judged, merging it would change nothing, and a standing auto-distinct would be wrong the day the record arrives. The registration keeps its row on the sensors table; when a record appears the pair enters the candidate set fresh.

FOUR BRANCHES, first match wins:
  complementary      no shared DISCRIMINATING channel -> disjoint instruments metres apart are one installation; incidental shared weak channels (every instrument carries a thermistor) are ignored, because judging a pair on the channel the registry itself calls "same climate" is how two in-field instruments 8 m apart were once judged on their turbidity correlation
  sequential         a shared discriminating channel with zero shared days -> one installation re-registered; the seam dates are the evidence
  dual_registration  a shared discriminating channel agreeing on shape and level, sep_m <= DUAL_MAX_SEP_M -> two programs publishing one instrument
  distinct           otherwise

There is deliberately NO weak_evidence branch: its old job was flagging far-apart pairs whose only agreement was on non-discriminating channels, and the uniform gate auto-rejects those before evidence is consulted. Within the gate, a pair with no discriminating overlap IS the complementary case.

WHICH CHANNEL DECIDES. Nitrate where both have it, then discharge, then the densest shared discriminating channel. The level test is absolute for high-scrutiny channels (mg/L as N, the calibrated threshold), judged against the channel's own `agree_tol` on interval scales (degC, pH -- an arbitrary origin makes a relative difference meaningless), and relative to the pair's own median for everything else.

A non-overlap rule alone is not enough. One known pair sits 8.1 m apart, DOES overlap over 22 shared days, and agrees at r = 0.887 with a median difference of exactly 0.00 mg/L: one gauge, published twice, concurrently. Requiring zero overlap would keep them apart, and so did DUAL_MIN_R = 0.95.

NOTHING MERGES WITHOUT A RECORDED DECISION where the stakes are high. A proposal touching a high-scrutiny record, or crossing surface types, gates on a person through `decisions/merge.csv` (the ledger kit, criterion-versioned); standard-tier same-type proposals take the rule's answer, written to the auto record. COMPONENTS, NOT PAIRS: grouping is by connected component over confirmed merges, and a component fusing a pair a person ruled distinct HALTS the build naming the members, the violated pair, and the chain.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts as schema, io as bio, ledger, registry
from . import site_type

# The uniform proximity gate, on min_sep_m -- see the module docstring for the calibration.
GATE_SEP_M = 100.0
# Stated separations beyond this are flagged in review: every confirmed merge but one sits under 52 m.
HESITATE_SEP_M = 50.0

# Correlation alone never merges anything -- DUAL_MAX_MEDIAN_DIFF has to pass too, and a human still confirms where it matters. Two instruments on the same water agree on SHAPE more reliably than on level: they are different sensors with different calibration drift, so 0.95 was set for a co-located pair of identical instruments rather than for two programs publishing the same river; 0.80 admits the known true duplicate at r = 0.887.
DUAL_MIN_R = 0.80
DUAL_MAX_MEDIAN_DIFF = 0.25      # mg/L as N -- nitrate only, the calibrated threshold
DUAL_MAX_RELATIVE_DIFF = 0.10    # every other channel, as a fraction of the pair's own median level
MIN_SHARED_DAYS_FOR_R = 10

# A duplicate registration is ONE STRUCTURE, so agreement alone does not establish one: two gauges
# kilometres apart on one river correlate at 0.9 with near-equal medians BECAUSE IT IS THE SAME RIVER.
# Measured: the true duplicates sit at 8 m and 44 m; the suspect pairs the old rule would have merged
# sit at 1.5-2.1 km.
DUAL_MAX_SEP_M = 250.0


def _compare_stat(channel: str) -> str:
    return registry.CHANNELS[channel].compare_stat


# Which shared channel decides a pair when several overlap. See `_shared_channel`.
CORRELATION_PREFERENCE = ("nitrate", "discharge", "spec_cond")

# THE WEAK SET LIVES IN THE REGISTRY as `discriminating=False` -- a channel's nature, not identity's
# opinion. Derived here so the two can never disagree.
WEAK_FOR_IDENTITY = frozenset(set(registry.NAMES) - registry.discriminating())

MERGE_PROPOSALS = frozenset({"dual_registration", "sequential", "complementary"})

# Hard calls first. `sequential` pairs have no overlap, so no correlation exists to judge them by;
# `distinct` needs the least thought. ONE ordering, used by the sheet, the auto record and the panel.
REVIEW_ORDER = ("sequential", "complementary", "dual_registration", "distinct")

# The evidence columns a reviewer sees, in the order they help: names, the type pair, the proposal,
# then geometry, then the record comparison.
EVIDENCE_COLUMNS = ["name_a", "name_b", "type_a", "type_b", "cross_type", "proposed",
                    "sep_m", "min_sep_m", "max_sep_m", "hesitate", "comid_a", "comid_b", "same_comid",
                    "on_channel", "shared_days", "r", "median_diff",
                    "channels_a", "channels_b", "agree_a", "agree_b", "seam"]
CANDIDATES_PATH = config.REVIEW_DIR / "merge_candidates.csv"


def _canonical(uid: str) -> pd.DataFrame | None:
    """The sensor's canonical daily frame from D2, indexed on date, STAMP-CHECKED against the live registry.

    The check is the point: identity evidence consumed through a bare read once outlived a registry edit silently. A stale canonical raises here, naming the fix (rerun d2).
    """
    p = config.WATER_CANONICAL / f"{schema.uid_to_filename(uid)}.parquet"
    if not p.exists():
        return None
    d = bio.read_parquet(p, expect={"registry": registry.fingerprint()})
    return d.set_index(pd.to_datetime(d.date)).drop(columns="date").sort_index()


def channels_of(frame: pd.DataFrame | None) -> list[str]:
    """Canonical channels this frame carries a value for, in registry order."""
    if frame is None or not len(frame):
        return []
    return [c for c in registry.NAMES
            if f"{c}_mean" in frame.columns and frame[f"{c}_mean"].notna().any()]


def _series(frame: pd.DataFrame | None, channel: str) -> pd.Series | None:
    """One channel's daily comparison value, by the registry's `compare_stat`."""
    if frame is None:
        return None
    col = f"{channel}_{_compare_stat(channel)}"
    if col not in frame.columns:
        return None
    s = frame[col].dropna()
    return s if len(s) else None


def _agreement(sa: pd.Series, sb: pd.Series, channel: str) -> tuple[float, float, float, bool]:
    """(shared_days, r, median_diff, agrees) on the channel's own scale kind."""
    j = pd.concat([sa.rename("a"), sb.rename("b")], axis=1, join="inner").dropna()
    if not len(j):
        return 0.0, np.nan, np.nan, False
    mdiff = float((j.a - j.b).median())
    r = float(j.a.corr(j.b)) if len(j) >= MIN_SHARED_DAYS_FOR_R else np.nan
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
    """Shared channels that DISCRIMINATE -- the ones whose agreement means shared instrument."""
    return [c for c in ca if c in cb and c not in WEAK_FOR_IDENTITY]


def _shared_channel(ca: list[str], cb: list[str], fa, fb) -> str | None:
    """The discriminating shared channel that decides the pair: the preference order, then the densest.

    Discriminating only -- a pair sharing nothing but water temperature has no deciding channel, and that IS the complementary case, not a comparison opportunity.
    """
    strong = _strong_shared(ca, cb)
    if not strong:
        return None
    for pref in CORRELATION_PREFERENCE:
        if pref in strong:
            return pref
    def density(ch):
        s = _series(fa, ch)
        return len(s) if s is not None else 0
    return max(strong, key=density)


# ---- candidate generation: the gate over the proximity table --------------------------------------

def candidates(sensors: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(gated candidates with evidence, auto-distinct record) from the proximity table.

    Requires D3's proximity table and snap block. Three filters run before any record is opened -- material, records, proximity -- and only the last two WRITE anything: a record-less pair is a filter (no row, see the module docstring), a cross-material or beyond-gate pair is an auto-distinct row with the failing condition named, and an unknown-type pair WAITS (a row in neither frame; the type ledger holds the question). What survives gets the evidence branch.
    """
    if not config.PROXIMITY_PATH.exists():
        raise FileNotFoundError("no proximity table -- run d3 (geometry) before identity")
    prox = bio.read_parquet(config.PROXIMITY_PATH)
    s = sensors.set_index("site_uid")
    names = s.station_name
    types = s.site_type
    agree = s.comid_agree if "comid_agree" in s.columns else pd.Series(dtype="boolean")

    has_rec = {u: (config.WATER_CANONICAL / f"{schema.uid_to_filename(u)}.parquet").exists()
               for u in set(prox.uid_a) | set(prox.uid_b)}

    def mat(u):
        t = types.get(u, pd.NA)
        return site_type.material(t) if pd.notna(t) else "unknown"

    rows, auto, waiting = [], [], 0
    dropped_recordless = 0
    for p in prox.itertuples():
        ma, mb = mat(p.uid_a), mat(p.uid_b)
        if ma == "unknown" or mb == "unknown":
            waiting += 1
            continue
        if ma != mb:
            auto.append(dict(uid_a=p.uid_a, uid_b=p.uid_b, proposed="distinct",
                             settled_by=f"cross_material({ma}|{mb})", sep_m=p.sep_m))
            continue
        if not (has_rec.get(p.uid_a) and has_rec.get(p.uid_b)):
            dropped_recordless += 1
            continue
        if p.min_sep_m > GATE_SEP_M:
            auto.append(dict(uid_a=p.uid_a, uid_b=p.uid_b, proposed="distinct",
                             settled_by=f"beyond_gate({p.min_sep_m:.0f}m)", sep_m=p.sep_m))
            continue
        rows.append(p)

    print(f"  proximity pairs: {len(prox):,} -> {len(rows)} gated "
          f"({sum(1 for a in auto if a['settled_by'].startswith('cross_material'))} cross-material, "
          f"{sum(1 for a in auto if a['settled_by'].startswith('beyond_gate'))} beyond the {GATE_SEP_M:.0f} m gate, "
          f"{dropped_recordless} with a record-less side [no row], {waiting} waiting on an unknown type)")

    out = []
    for p in rows:
        fa, fb = _canonical(p.uid_a), _canonical(p.uid_b)
        ca, cb = channels_of(fa), channels_of(fb)
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

        if ch is None:
            rule = "complementary"
        elif shared == 0:
            rule = "sequential"
        elif agrees and p.sep_m <= DUAL_MAX_SEP_M:
            rule = "dual_registration"
        else:
            rule = "distinct"

        ta, tb = types.get(p.uid_a, pd.NA), types.get(p.uid_b, pd.NA)
        out.append(dict(
            uid_a=p.uid_a, uid_b=p.uid_b,
            name_a=names.get(p.uid_a), name_b=names.get(p.uid_b),
            type_a=ta, type_b=tb, cross_type=bool(pd.notna(ta) and pd.notna(tb) and ta != tb),
            proposed=rule,
            sep_m=round(float(p.sep_m), 2), min_sep_m=round(float(p.min_sep_m), 2),
            max_sep_m=round(float(p.max_sep_m), 2),
            hesitate=bool(p.sep_m > HESITATE_SEP_M),
            comid_a=p.comid_a, comid_b=p.comid_b, same_comid=bool(p.same_comid),
            on_channel=on,
            channels_a="+".join(ca) if ca else pd.NA,
            channels_b="+".join(cb) if cb else pd.NA,
            shared_days=None if np.isnan(shared) else int(shared),
            r=None if np.isnan(r) else round(r, 4),
            median_diff=None if np.isnan(mdiff) else round(mdiff, 4),
            agree_a=agree.get(p.uid_a, pd.NA), agree_b=agree.get(p.uid_b, pd.NA),
            seam=seam,
        ))
    cand = pd.DataFrame(out, columns=["uid_a", "uid_b", *EVIDENCE_COLUMNS]) if out \
        else pd.DataFrame(columns=["uid_a", "uid_b", *EVIDENCE_COLUMNS])
    auto_df = pd.DataFrame(auto, columns=["uid_a", "uid_b", "proposed", "settled_by", "sep_m"])
    return cand.sort_values(["sep_m", "uid_a"], ignore_index=True), auto_df


# ---- review ordering, gating, the ledger ----------------------------------------------------------

def order_for_review(cand: pd.DataFrame, dec: pd.DataFrame | None = None) -> pd.DataFrame:
    """Candidates in review order: UNANSWERED FIRST, then by branch (`REVIEW_ORDER`), then by separation.

    The single source of this ordering; the sheet and the panel both consume it, which is what lets a reviewer read "pair 7" off the panel and find row 7 of the sheet.
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

    TWO ROUTES TO THE GATE, both about consequence. A merge proposal touching a HIGH-SCRUTINY record blocks -- a wrong merge rewrites the flagship record and consumes the evidence of its own wrongness. A CROSS-TYPE merge proposal blocks whatever the tier: the type label is inferred and noisy, so lake x stream is never auto-merged AND never silently auto-rejected -- a human sees the mismatch, prominently. EITHER SIDE for the tier test, not both: requiring both would auto-apply exactly the merges resting on the thinnest evidence. `distinct` never blocks -- it changes nothing, and a wrong one leaves two visible near-identical records where a wrong merge leaves nothing.
    """
    if not len(cand):
        return pd.Series(dtype="bool")
    is_merge = cand.proposed.isin(MERGE_PROPOSALS)
    return (high_tier_on_either_side(cand) | cand.cross_type.fillna(False)) & is_merge


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

    Written with `decision` BLANK for a new pair, never guessed at from `proposed` -- the gate exists so a merge is asserted by a person rather than inherited from a threshold. Existing decisions carry forward on the uid pair (and on the CRITERION VERSION: a decision made under an older gate re-queues for confirmation rather than silently carrying authority).
    """
    if not len(cand):
        return 0, 0, 0
    ordered = order_for_review(cand, load_decisions())
    cols = ["uid_a", "uid_b"] + [c for c in EVIDENCE_COLUMNS if c in ordered.columns]
    return ledger.MERGE.sync(ordered[cols])


def record_span(uid: str) -> tuple[str, str] | tuple[None, None]:
    """(first, last) observation date over ALL channels of the canonical record, floored at config.collection_start(); (None, None) when no record is on disk."""
    from .. import contracts

    p = config.WATER_CANONICAL / f"{contracts.uid_to_filename(uid)}.parquet"
    if not p.exists():
        return None, None
    d = pd.read_parquet(p, columns=["date"])
    if not len(d):
        return None, None
    lo = max(pd.Timestamp(d.date.min()), pd.Timestamp(config.collection_start()))
    return str(lo.date()), str(pd.Timestamp(d.date.max()).date())


# The redundancy rule's thresholds: spans must overlap by at least this fraction of the SHORTER record, and a shared channel counts as "differing" when at least this share of >= DIFFER_DAYS_MIN common days disagree -- a bit-identical duplicate feed fails that test and stays a human question.
OVERLAP_FRAC = 0.5
DIFFER_DAYS_MIN = 10
DIFFER_FRAC = 0.01


def _channel_series_differ(uid_a: str, uid_b: str, channels: set[str]) -> bool:
    """True when at least one shared channel's daily series genuinely differ on their common days."""
    from .. import contracts

    frames = {}
    for u in (uid_a, uid_b):
        p = config.WATER_CANONICAL / f"{contracts.uid_to_filename(u)}.parquet"
        if not p.exists():
            return False
        frames[u] = pd.read_parquet(p)
    for c in sorted(channels):
        col = f"{c}_mean"
        if col not in frames[uid_a].columns or col not in frames[uid_b].columns:
            continue
        a = frames[uid_a].set_index("date")[col].dropna()
        b = frames[uid_b].set_index("date")[col].dropna()
        common = a.index.intersection(b.index)
        if len(common) < DIFFER_DAYS_MIN:
            continue
        av, bv = a.loc[common].to_numpy(dtype=float), b.loc[common].to_numpy(dtype=float)
        differ = ~np.isclose(av, bv, rtol=1e-9, atol=1e-9)
        if differ.mean() > DIFFER_FRAC:
            return True
    return False


def auto_decide(cand: pd.DataFrame) -> int:
    """Two standing auto-rules, writing ordinary overturnable sheet decisions whose decided_by NAMES the rule; only blank-decision pairs are considered, so a human row is never overwritten. The rules are mutually exclusive by construction (disjoint vs identical channel sets), so no precedence exists between them.

    RULE 1 -- upgrade, MERGE, decided_by='auto (merge rule 1)': same type, under 10 m, channel sets fully disjoint. The USGS re-registers a station under a fresh id when its instrumentation gains capabilities; disjoint channels cannot even contradict each other -- they are complementary halves of one station's history.

    RULE 2 -- redundancy, DISTINCT, decided_by='auto (merge rule 2)': channel sets sharing at least one channel, record spans overlapping by >= OVERLAP_FRAC of the shorter record, and at least one shared channel's series genuinely differing on common days. The USGS installs co-located twin sensors for redundancy that report correlated but slightly different values; whether such twins constitute one node is a modeling preference, so the build keeps them separate.
    """
    dec = ledger.MERGE.read_all()
    decided = {tuple(sorted((a, b))) for a, b, d in
               zip(dec.uid_a, dec.uid_b, dec.decision.astype(str).str.strip())
               if d} if len(dec) else set()

    def chans(v):
        return {c for c in str(v or "").split("+") if c and c not in ("nan", "<NA>")}

    def span_overlap_frac(sa, sb):
        if None in (sa[0], sb[0]):
            return 0.0
        a0, a1 = pd.Timestamp(sa[0]), pd.Timestamp(sa[1])
        b0, b1 = pd.Timestamp(sb[0]), pd.Timestamp(sb[1])
        ov = (min(a1, b1) - max(a0, b0)).days + 1
        shorter = min((a1 - a0).days, (b1 - b0).days) + 1
        return max(ov, 0) / max(shorter, 1)

    sep = pd.to_numeric(cand.max_sep_m, errors="coerce")
    same_type = cand.type_a.notna() & (cand.type_a == cand.type_b)
    csets = [(chans(a), chans(b)) for a, b in zip(cand.channels_a, cand.channels_b)]
    overlapping = pd.Series([bool(ca & cb) for ca, cb in csets], index=cand.index)
    disjoint = pd.Series([bool(ca) and bool(cb) and not (ca & cb) for ca, cb in csets],
                         index=cand.index)

    n = 0
    spans: dict[str, tuple] = {}

    def redundant(r):
        sa = spans.setdefault(r.uid_a, record_span(r.uid_a))
        sb = spans.setdefault(r.uid_b, record_span(r.uid_b))
        return (span_overlap_frac(sa, sb) >= OVERLAP_FRAC
                and _channel_series_differ(r.uid_a, r.uid_b,
                                           chans(r.channels_a) & chans(r.channels_b)))

    rules = (((sep < 10.0) & same_type & disjoint, "merge", "auto (merge rule 1)",
              "upgrade: adjacent (max_sep < 10 m), same type, disjoint channels", lambda r: True),
             (overlapping, "distinct", "auto (merge rule 2)",
              "redundant twin: shared channels, overlapping spans, differing series", redundant))
    per_rule = {}
    for mask, verdict, who, note, extra in rules:
        for r in cand[mask].itertuples():
            key = tuple(sorted((r.uid_a, r.uid_b)))
            if key in decided or not extra(r):
                continue
            decided.add(key)
            ledger.MERGE.decide((r.uid_a, r.uid_b), verdict, decided_by=who, note=note)
            per_rule[who] = per_rule.get(who, 0) + 1
            n += 1
    if n:
        print(f"  {n} pair(s) auto-decided: {per_rule}")
    return n


def high_tier_days(sensors: pd.DataFrame) -> pd.Series:
    """Record days per uid for picking a bundle's canonical member: HIGH-TIER days first, total days breaking ties.

    Prefers the canon block's per-channel `n_days_*` counts (D2 already measured them) and falls back to counting the canonical file's days, so identity can run standalone before the canon columns are joined on. A uid with neither is 0, which sorts it last.
    """
    tier = registry.high_scrutiny()
    tier_cols = [f"n_days_{c}" for c in tier if f"n_days_{c}" in sensors.columns]
    all_cols = [f"n_days_{c}" for c in registry.NAMES if f"n_days_{c}" in sensors.columns]

    out = {}
    for _, row in sensors.iterrows():
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
    """Human merge decisions via the ledger kit: rows carrying a CURRENT-criterion verdict; blank or superseded means NOT DECIDED; unknown tokens raise."""
    return ledger.MERGE.load()


def apply_decisions(sensors: pd.DataFrame, cand: pd.DataFrame, dec: pd.DataFrame) -> pd.DataFrame:
    """The bundle block: connected components over CONFIRMED merges, stamped per the sensors contract.

    Returns the BLOCK -- `site_uid` plus `bundle`, `is_bundle_canonical`, `bundle_rule`, `bundle_decided_by` -- not the widened table; the D4 orchestrator joins it on with `update_block`. `bundle` is the '+'-joined SORTED member-uid set, the LEDGER KEY for every bundle-scoped decision downstream. The canonical member carries the most HIGH-TIER record days, `CANONICAL_PRECEDENCE` breaking ties and the uid breaking those.

    A SENSOR IN AN UNDECIDED PAIR HAS NO BUNDLE YET. Null is what "not yet decided" looks like; `bundle_decided_by = "pending"` names the state -- both halves of a duplicate carrying `is_bundle_canonical = True` would be a false assertion anything trusting the flag double-counts.
    """
    import networkx as nx

    out = sensors[["site_uid"]].copy()
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

    prec = sensors.set_index("site_uid").source.map(schema.CANONICAL_PRECEDENCE).fillna(99)
    days = high_tier_days(sensors)
    bundle_of, canon_of = {}, {}
    contradictions = []
    for comp in nx.connected_components(g):
        members = sorted(comp)
        # THE CONTRADICTION HALT. Merging is transitive by construction, so A-B and B-C `merge` fuse A and
        # C whatever anyone said about the pair A-C -- and if a HUMAN said `distinct`, the decisions do not
        # partition consistently and the walk must not paper over it. Only a human `distinct` contradicts:
        # the rule's `distinct` means "no pairwise evidence to merge", and a chain of re-registrations
        # legitimately fuses two sensors the rule proposed apart. A wrong fusion leaves no trace downstream,
        # because the evidence for it having been wrong is exactly what the fusion consumed -- a halt, not
        # a warning.
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
            f"\n{len(contradictions)} component(s) where the decisions contradict each other -- "
            f"merges fuse a pair a person ruled distinct:\n" + "\n".join(lines) +
            f"\n\nThe decisions do not partition these sensors consistently. Flip one side in "
            f"{ledger.MERGE.path} (either the distinct, or a merge in the chain) and rerun; "
            f"nothing was bundled for these components.")

    out["bundle"] = out.site_uid.map(bundle_of)
    # NULLABLE, because pending pairs write pd.NA into it below.
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


def main(sensors: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    sensors = sensors if sensors is not None else bio.read_parquet(config.SENSORS_PATH)
    cand, auto_reject = candidates(sensors)
    dec = load_decisions()

    if len(cand):
        print(f"  proposed: {cand.proposed.value_counts().to_dict()}")
        gate = blocks(cand)
        ask, auto = cand[gate], cand[~gate]
        n_auto_merge = int(auto.proposed.isin(MERGE_PROPOSALS).sum())
        print(f"  {len(auto)} pair(s) settled by rule ({n_auto_merge} merged, "
              f"{len(auto) - n_auto_merge} left apart); {len(ask)} need a person "
              f"({int(cand.cross_type.sum())} cross-type)")
        # ONE AUTO RECORD holding both silences: pairs the gate rejected before evidence, and pairs the
        # evidence branch settled unasked. A record, not a queue -- but it must not be invisible.
        settled = pd.concat([auto_reject,
                             auto.assign(settled_by="rule:" + auto.proposed.astype(str))],
                            ignore_index=True)
        ledger.MERGE.record_auto(settled)
        added, kept, retired = sync_decisions_file(cand)
        auto_decide(cand)
        pend = pending_pairs(cand, dec)
        print(f"  decisions on file: {len(dec)}   PENDING: {len(pend)}")
        bio.write_csv(order_for_review(cand, dec), CANDIDATES_PATH)
        print(f"  synced {ledger.MERGE.path.name} (+{added} / {kept} kept / {retired} retired); "
              f"wrote {CANDIDATES_PATH.name} and {ledger.MERGE.auto_path.name}")
    else:
        ledger.MERGE.record_auto(auto_reject)

    block = apply_decisions(sensors, cand, dec)
    n_bundles = block.bundle.nunique()
    n_undecided = int(block.bundle.isna().sum())
    print(f"\n{len(block)} registrations -> {n_bundles} bundle(s) "
          f"({len(block) - n_bundles - n_undecided} merged away, {n_undecided} awaiting a decision)")
    return block, cand


if __name__ == "__main__":
    main()
