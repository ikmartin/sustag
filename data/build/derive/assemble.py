"""D4 orchestrator: type assignment, exclusions, identity, and the assembled winner-per-span records.

NO SITES, NO MINTING. The published record entity is a decided bundle with at least one canonical record, and it publishes under an id its FRONTING MEMBER already owns -- the SNR code minted at the census. The fronting member is ranked: a declared co-location a measurement confirms, then a measured co-location, then the longest high-tier record; the ranking is fully automatic and deliberately takes no human input, because the choice affects only which member's id and coordinates front the record, never the assembled series. `publishes_under` on the sensors table carries the answer for every member.

WINNER PER SPAN, NEVER A BLEND. Where two members overlap on a channel, each published day comes from exactly ONE contributor, chosen by the channel's registry merge policy, with the contributing sensor recorded per day (`{channel}_src`). Blending two instruments' values would manufacture numbers no instrument reported and destroy the provenance a reviewer needs when the pair later disagrees.

A BUNDLE WHOSE MEMBERS SNAP TO DIFFERENT REACHES HALTS BY NAME: the standing merge decision and the current geometry contradict each other, and only a person can say which is wrong (re-answer the pair, or exclude the offending sensor). A record-less registration remains registered -- snapped, typed, bundled -- and assembles nothing; its emptiness was established at canonicalisation and this stage READS it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts, ids, io as bio, ledger, registry
from ..acquire import snapshot
from . import identity, site_type

TYPE_COLS = [c for c, _, b, _ in contracts.SENSOR_COLUMNS if b == "type"]
BUNDLE_COLS = [c for c, _, b, _ in contracts.SENSOR_COLUMNS if b == "bundle"]

# Evidence a type reviewer sees next to the row they are deciding, in the order it helps.
TYPE_EVIDENCE = ["station_name", "site_type", "site_type_source", "site_type_conflict", "mismatch_others",
                 "ambiguity", "snap_status", "snap_dist_m", "source"]


def has_record(uid: str) -> bool:
    """Does D2 hold a canonical record for this registration? The publish-eligibility fact, read off disk."""
    return (config.WATER_CANONICAL / f"{contracts.uid_to_filename(uid)}.parquet").exists()


# ---- exclusions -------------------------------------------------------------------------------------

def type_review_set(sensors: pd.DataFrame) -> pd.DataFrame:
    """Rows the type review covers: ambiguous, RECORD-BEARING, non-excluded -- plus everything already decided, tagged.

    A record is what makes a type matter here: the type gates merges and classifies published records, and a record-less registration reaches neither. Review queues are bounded by consequence -- a queue of record-less `unknown`s is a queue nobody reads; those rows keep their `unknown` on the table and arrive here the day they gain a record.
    """
    tled = site_type.type_ledger()
    recorded = sensors.site_uid.map(has_record)
    excluded = (sensors.excluded_reason.notna() if "excluded_reason" in sensors.columns
                else pd.Series(False, index=sensors.index))
    # members of a settled multi-member bundle whose types DISAGREE: the co-location exemption (and any
    # human cross-type merge) creates these, and one published record carries one type -- a person picks it
    b = sensors[sensors.bundle.notna() & sensors.bundle.str.contains("+", regex=False)]
    n_types = b.groupby("bundle").site_type.nunique(dropna=True)
    bad = b[b.bundle.isin(n_types[n_types > 1].index)]
    by_bundle = {k: list(zip(g.site_uid, g.site_type)) for k, g in bad.groupby("bundle")}
    mismatch = {u: " + ".join(f"{ou}:{ot}" for ou, ot in by_bundle[bun] if ou != u)
                for u, bun in zip(bad.site_uid, bad.bundle)}
    return site_type.ambiguous(sensors[recorded & ~excluded],
                               include={k[0] for k in tled.decided_keys()}, mismatch=mismatch)


def auto_confirm_pairs() -> int:
    """COMPOSED RULE 1: two-member bundles confirm automatically (decided_by='auto (composed rule 1)') -- the pair decision that built the bundle was made looking at exactly this assembly (the merge tab's ghosted counterfactual), so a second human confirmation adds nothing. Every bundle of 3+ members stays a human question: chains are objects transitivity manufactured that no single pair decision approved."""
    d = ledger.COMPOSED.read_all() if ledger.COMPOSED.path.exists() else None
    if d is None or not len(d):
        return 0
    dec = d.decision.astype(str).str.strip()
    two = d.members.astype(str).str.count(r"\+") == 1
    n = 0
    for m in d.members[two & (dec == "")]:
        ledger.COMPOSED.decide((m,), "confirm", decided_by="auto (composed rule 1)",
                               note="two-member bundle: the pair decision was the whole-bundle review")
        n += 1
    if n:
        print(f"  {n} two-member bundle(s) auto-confirmed (auto (composed rule 1))")
    return n


def suggest_exclusions(sensors: pd.DataFrame) -> int:
    """Open exclusion rows for sensors with a good prima-facie reason, currently one rule: record-bearing but no location -- a record headed for publication from nowhere. Suggestions only: the note states the reason, the decision cell stays blank for a person, and rows already on the sheet (decided, hand-added, or previously suggested) are never touched."""
    d = ledger.EXCLUSIONS.read_all() if ledger.EXCLUSIONS.path.exists() else None
    on_sheet = set(d.uid.astype(str)) if d is not None and len(d) else set()
    recorded = sensors.site_uid.map(has_record)
    hot = sensors[recorded & sensors.lat.isna() & ~sensors.site_uid.isin(on_sheet)]
    for uid in hot.site_uid:
        ledger.EXCLUSIONS.add_row((uid,), note="suggested: record-bearing but no location")
    if len(hot):
        print(f"  {len(hot)} exclusion suggestion(s) opened: record-bearing, no location")
    return len(hot)


def apply_exclusions(sensors: pd.DataFrame) -> pd.DataFrame:
    """Stamp `excluded_reason` from the sensor-exclusion ledger; sensors it names leave every bundle.

    Rows arrive two ways, decisions only one: a person adds a uid on prior knowledge (a test logger, a scratch deployment), and the build SUGGESTS candidates with a stated reason (`suggest_exclusions`: record-bearing sensors with no location). Either way the verdict is a human's: `exclude` stamps the reason (the row's note, or the bare verdict); `keep` records that the question was asked and answered. A row someone opened but left blank gates in `main` -- an open question about membership cannot be built past.
    """
    out = sensors.copy()
    out["excluded_reason"] = contracts.null_sensor_column("excluded_reason", out.index)
    dec = ledger.EXCLUSIONS.load()
    if not len(dec):
        return out
    reasons = {str(r.uid): (str(r.note).strip() or "excluded")
               for r in dec.itertuples() if str(r.decision).strip() == "exclude"}
    hit = out.site_uid.isin(reasons)
    out.loc[hit, "excluded_reason"] = out.site_uid[hit].map(reasons)
    unknown = set(reasons) - set(out.site_uid)
    if unknown:
        print(f"  exclusion ledger names {len(unknown)} uid(s) not on the sensors table: {sorted(unknown)[:5]}")
    return out


def _exclusion_gate() -> list[str]:
    """SystemExit text for blank exclusion rows, or [] when the sheet is clean. Raw read: `load()` drops blanks, and blanks are exactly the question here."""
    p = ledger.EXCLUSIONS.path
    if not p.exists():
        return []
    d = pd.read_csv(p, dtype=str).fillna("")
    blank = d[d.get("decision", pd.Series("", index=d.index)).astype(str).str.strip() == ""]
    if not len(blank):
        return []
    rows = "\n".join(f"    {u}" for u in blank.uid)
    return [f"Build awaiting {len(blank)} decision(s) in {p.name}:\n{rows}\n\n"
            f"Fill `decision` (one of ['exclude', 'keep']) in {p} and rerun."]


def _persist_sensors(sensors: pd.DataFrame) -> None:
    """Conform, validate and write the sensors table with its stamps -- the one write path for interim state."""
    interim = contracts.conform_sensors(sensors)
    bad = contracts.validate_sensors(interim)
    if bad:
        raise ValueError("sensors table violates the contract:\n  " + "\n  ".join(bad))
    bio.write_parquet(interim, config.SENSORS_PATH,
                      stamps={"snapshot": snapshot.head() or "", "aoe": config.aoe_stamp(),
                              "ledgers": ledger.ledger_hash(), "registry": registry.fingerprint(),
                              "schema": contracts.schema_fingerprint(contracts.SENSOR_DTYPES)})


# ---- the composed-bundle review queue --------------------------------------------------------------

def composed_queue(sensors: pd.DataFrame) -> pd.DataFrame:
    """One row per DECIDED multi-member bundle: the composed record, offered for confirmation whole.

    Pairwise merge answers are the EDITING mechanism; each answer is about an edge, and the build assembles a RECORD nobody has looked at: which member fronts it, what the assembly gains from whom, what three pairwise-sound answers compose into. Keyed on the sorted member set, so changing the membership makes a NEW key and re-asks by construction. Unanswered rows sort first; singletons never appear.
    """
    s = sensors[sensors.bundle.notna() & sensors.bundle.str.contains(r"\+")]
    if not len(s):
        return pd.DataFrame(columns=["members"])
    done = {k[0] for k in ledger.COMPOSED.decided_keys()}

    def _from_store(uid):
        f = identity._canonical(uid)
        if f is None:
            return 0, []
        n = int(f["nitrate_mean"].notna().sum()) if "nitrate_mean" in f.columns else 0
        return n, identity.channels_of(f)

    rows = []
    for b, grp in s.groupby("bundle"):
        front = grp.site_uid[grp.is_bundle_canonical.fillna(False)].tolist()
        days, chan_sets = {}, []
        for r in grp.itertuples():
            if pd.notna(r.n_days_nitrate) and (int(r.n_days_nitrate) > 0 or pd.notna(r.channels_present)):
                days[r.site_uid] = int(r.n_days_nitrate)
                chan_sets.append(str(r.channels_present or "").split("+"))
            else:
                n, ch = _from_store(r.site_uid)
                days[r.site_uid] = n
                chan_sets.append(ch)
        chans = sorted({c for v in chan_sets for c in v if c})
        comids = sorted({int(c) for c in grp.comid.dropna()})
        types = sorted({str(t) for t in grp.site_type.dropna()})
        rows.append(dict(
            members=b, n_members=len(grp),
            fronted_by=front[0] if front else pd.NA,
            publishes_under=grp.publishes_under.dropna().iloc[0] if grp.publishes_under.notna().any() else pd.NA,
            rule="+".join(sorted(set(grp.bundle_rule.dropna()))),
            comid=comids[0] if len(comids) == 1 else "DISAGREE:" + ",".join(map(str, comids)),
            types="+".join(types),
            nitrate_days_by_member="; ".join(f"{u}:{d}" for u, d in sorted(days.items())),
            channels="+".join(chans),
            station_names=" | ".join(str(n)[:34] for n in grp.station_name.fillna("?")),
        ))
    q = pd.DataFrame(rows)
    q["_done"] = q.members.isin(done).astype(int)
    return q.sort_values(["_done", "members"], ignore_index=True).drop(columns="_done")


# ---- fronting and assembly -------------------------------------------------------------------------

def _colocation_anchors(sensors: pd.DataFrame) -> tuple[dict[str, str], dict[str, str]]:
    """`(declared, measured)`: member uid -> the USGS uid it co-locates with, by each kind of evidence.

    CO-LOCATION IS THE FACT; THE DECLARATION IS ONE WAY OF KNOWING IT. Keying on an operator's assertion alone makes the rule hostage to whether a source still publishes the column; measurement answers the same question and does not retire, so both are collected and the caller prefers the declaration only where one exists.
    """
    from ..acquire import reconcile

    pairs = reconcile.colocated_pairs(sensors)
    declared, measured = {}, {}
    for r in pairs.itertuples():
        a, b = str(r.uid_a), str(r.uid_b)
        if a.startswith("USGS:") == b.startswith("USGS:"):
            continue                      # a USGS gage on one side is what makes this an anchor
        usgs, other = (a, b) if a.startswith("USGS:") else (b, a)
        (declared if r.how == "declared" else measured)[other] = usgs
    return declared, measured


def _front(members: list[str], canon: str, anchors: tuple[dict[str, str], dict[str, str]]) -> tuple[str, str]:
    """(uid, rank) of the member whose SNR id and coordinates front the published record.

    A station co-located with a USGS gage fronts under the GAGE: a gage location is surveyed, a station's is recorded where the operator stood. Declared co-location is preferred where an operator asserts it and measurement confirms it; measured co-location carries the rest; otherwise the canonical member (most high-tier record days). Fully automatic -- within a <=100 m bundle the coordinate difference is immaterial and the series is identical whichever member fronts, so there is nothing here a human could usefully decide.
    """
    declared, measured = anchors
    for kind, book in (("declared_colloc", declared), ("measured_colloc", measured)):
        for m in members:
            a = book.get(m)
            if a is not None and a in members:
                return a, kind
    return canon, "longest_record"


def _merge_channel(frames: dict[str, pd.DataFrame], ranked: list[str], ch: str):
    """One channel's winner-per-span series over ranked contributors: the leader's days verbatim, later ranks filling only days the leader lacks. Returns (stat frame, per-day src Series) or (None, None)."""
    out, src = None, None
    for uid in ranked:
        f = frames.get(uid)
        if f is None:
            continue
        cols = [c for c in f.columns if c.startswith(f"{ch}_")]
        if not cols or not f[cols].notna().any().any():
            continue
        sub = f[cols].dropna(how="all")
        if out is None:
            out, src = sub.copy(), pd.Series(uid, index=sub.index, dtype="object")
        else:
            new = sub.index.difference(out.index)
            if len(new):
                out = pd.concat([out, sub.loc[new]])
                src = pd.concat([src, pd.Series(uid, index=new, dtype="object")])
    if out is None:
        return None, None
    out, src = out.sort_index(), src.sort_index()
    src.name = f"{ch}_src"
    return out, src


def merge_record(members: list[str], days: pd.Series) -> pd.DataFrame:
    """The assembled daily record: per channel, winner-per-span across members, provenance per day.

    Rank per channel is the registry policy -- `longest` today: most days on THAT channel, high-tier days breaking ties, uid breaking those. One channel's day never mixes stats from two contributors: the winner supplies every statistic of that day or none. A singleton's record is its canonical frame with itself as provenance, through the same path -- one code path, so the single- and multi-member cases cannot drift apart.
    """
    frames = {u: identity._canonical(u) for u in members}
    frames = {u: f for u, f in frames.items() if f is not None}
    parts = []
    for ch in registry.NAMES:
        n_by = {u: int(f[f"{ch}_mean"].notna().sum()) if f"{ch}_mean" in f.columns else 0
                for u, f in frames.items()}
        if registry.CHANNELS[ch].merge != "longest":
            raise NotImplementedError(f"merge policy {registry.CHANNELS[ch].merge!r} on {ch}")
        ranked = sorted((u for u, n in n_by.items() if n > 0),
                        key=lambda u: (-n_by[u], -int(days.get(u, 0)), u))
        stat, src = _merge_channel(frames, ranked, ch)
        if stat is not None:
            parts.append(pd.concat([stat, src], axis=1))
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, axis=1).sort_index()
    out.index.name = "date"
    return out.reset_index()


def build(sensors: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Assemble every decided, record-bearing bundle. Returns (records keyed by SNR id, the publishes_under block).

    Halts on reach disagreement, over every bundle at once -- a reviewer should see the whole queue, not one name per rerun. A `publishes_under` collision (two bundles fronting the same SNR id) is structurally impossible: a member belongs to exactly one bundle and fronts with its own code.
    """
    s = sensors.set_index("site_uid", drop=False)
    snr = ids.mapping()
    bundles = sensors.bundle.dropna().unique()
    days = identity.high_tier_days(sensors)
    anchors = _colocation_anchors(sensors)

    disagreements, plans = [], []
    for b in bundles:
        members = b.split("+")
        recorded = [m for m in members if has_record(m)]
        if not recorded:
            continue                    # registered, never published
        comids = sorted({int(c) for m in members if pd.notna(c := s.loc[m, "comid"])})
        if len(comids) > 1:
            disagreements.append((b, comids))
            continue
        plans.append((b, members))
    if disagreements:
        lines = "\n".join(f"    {b}   reaches {c}" for b, c in disagreements)
        raise SystemExit(
            f"\n{len(disagreements)} bundle(s) whose members snap to DIFFERENT reaches:\n{lines}\n\n"
            f"The standing merge decision and the current geometry contradict each other. Re-answer the pair in "
            f"{ledger.MERGE.path} (distinct?), or exclude the offending sensor in {ledger.EXCLUSIONS.path}; "
            f"decisions are keyed on uids and survive the rebuild.")

    canon_of = {b: min(m, key=lambda u: (-int(days.get(u, 0)), u)) for b, m in plans}
    records, rows = {}, []
    for b, members in plans:
        front_uid, rank = _front(members, canon_of[b], anchors)
        code = snr.get(front_uid)
        if code is None:
            raise SystemExit(f"member {front_uid} of bundle {b} carries no SNR id -- the census mint "
                             f"did not cover it; rerun a2")
        rec = merge_record(members, days)
        records[code] = rec
        for m in members:
            rows.append(dict(site_uid=m, publishes_under=code,
                             is_bundle_canonical=(m == front_uid)))
    block = pd.DataFrame(rows, columns=["site_uid", "publishes_under", "is_bundle_canonical"]) \
        if rows else pd.DataFrame(columns=["site_uid", "publishes_under", "is_bundle_canonical"])
    return records, block


# ---- the stage --------------------------------------------------------------------------------------

def main(force: bool = False, snapshot: str | None = None) -> pd.DataFrame:
    sensors = bio.read_parquet(config.SENSORS_PATH)

    # 1 -- type assignment; human decisions apply inside classify (rank 0).
    typed = site_type.classify(sensors)
    sensors = contracts.update_block(sensors, typed, TYPE_COLS)

    # 2 -- exclusions from the ledger.
    sensors = apply_exclusions(sensors)
    n_exc = int(sensors.excluded_reason.notna().sum())
    if n_exc:
        print(f"  {n_exc} sensor(s) excluded by ledger")
    eligible = sensors[sensors.excluded_reason.isna()].reset_index(drop=True)

    # PERSIST THE TYPE BLOCK NOW, before identity can halt -- the persist-before-gate rule: a halt on an
    # inconsistent sheet must not discard the classification pass.
    _persist_sensors(sensors)

    # 3 -- identity over eligible sensors; syncs decisions/merge.csv and the auto record itself.
    iblock, cand = identity.main(eligible)
    bcols = [c for c in BUNDLE_COLS if c not in ("publishes_under", "excluded_reason")]
    sensors = contracts.update_block(sensors, iblock, bcols)
    excluded = sensors.excluded_reason.notna()
    for c in [*bcols, "publishes_under"]:
        if c in sensors.columns:
            sensors.loc[excluded, c] = pd.NA   # an excluded sensor is in NO bundle, stale stamps included

    # 4 -- review sheets, then EVERY gate at once: one sitting, not one rerun per queue.
    tled = site_type.type_ledger()
    amb = type_review_set(sensors)
    if len(amb):
        sheet = amb.copy()
        sheet["question"] = [site_type.question(r) for r in sheet.itertuples()]
        cols = ["site_uid", "question"] + [c for c in TYPE_EVIDENCE if c in sheet.columns]
        tled.sync(sheet[cols])
        site_type.auto_decide_types(amb)
    suggest_exclusions(sensors)
    composed = composed_queue(sensors)
    if len(composed):
        ledger.COMPOSED.sync(composed)
        auto_confirm_pairs()
    # PERSIST AGAIN, NOW WITH THE BUNDLE BLOCK. The blocks are FACTS -- the pending-NULL semantics exist
    # precisely so an undecided state is safe on disk.
    _persist_sensors(sensors)

    halts = []
    for gate_fn in (lambda: tled.gate(amb),
                    lambda: ledger.MERGE.gate(identity.order_for_review(cand[identity.blocks(cand)])
                                              if len(cand) else cand),
                    lambda: ledger.COMPOSED.gate(composed)):
        try:
            gate_fn()
        except SystemExit as e:
            halts.append(str(e))
    halts.extend(_exclusion_gate())
    if halts:
        raise SystemExit("\n".join(halts) +
                         "\n\nDecide via the review panel (python -m data.build.review_panel); "
                         "evidence CSVs are in " + str(config.REVIEW_DIR))

    # 5 -- assemble the records.
    records, block = build(sensors)
    sensors = contracts.update_block(sensors, block, ["publishes_under", "is_bundle_canonical"])

    stamps = {"snapshot": snapshot or "", "aoe": config.aoe_stamp(),
              "ledgers": ledger.ledger_hash(), "registry": registry.fingerprint()}
    # SWEEP FIRST -- but under stable ids only DISSOLVED bundles retire files: a record's SNR id never
    # moves with a re-decision unless its fronting membership changed, and a stale assembled record
    # reading cleanly beside the new one is worse than a missing file.
    keep = {f"{code}.parquet" for code in records}
    stale = [q for q in config.WATER_MERGED.glob("*.parquet") if q.name not in keep]
    for q in stale:
        q.unlink()
    if stale:
        print(f"  swept {len(stale)} stale assembled record(s) (dissolved or re-fronted bundles)")
    for code, rec in records.items():
        bio.write_parquet(rec, config.WATER_MERGED / f"{code}.parquet",
                          stamps={"registry": registry.fingerprint()})
    _persist_sensors(sensors)
    _write_sensor_comids(sensors)

    n_multi = int((sensors.bundle.dropna().str.contains(r"\+")).sum())
    print(f"\nassembled {len(records)} record(s) ({n_multi} sensors in multi-member bundles); "
          f"store: {config.WATER_MERGED}")
    return sensors


def _write_sensor_comids(sensors: pd.DataFrame) -> None:
    """Capture the record-bearing sensors' snapped COMIDs as a parameter file, for the NWM fetch.

    Acquisition must not import derivation, so D4 writes the list where parameters live and `acquire.nwm` reads a FILE, making the fetch reproducible from tracked inputs alone. Regenerated every pass; sorted, so the diff under git is the actual membership change.
    """
    rec = sensors.site_uid.map(has_record)
    comids = sorted({int(c) for c in sensors.comid[rec].dropna()})
    p = config.PARAMETERS / "sensor_comids.txt"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("# snapped COMIDs of record-bearing sensors, written by derive.assemble -- input to acquire.nwm\n"
                   + "\n".join(str(c) for c in comids) + "\n")
    tmp.replace(p)
    print(f"  wrote {p.name} ({len(comids)} reach(es)) for the NWM fetch")


# ---- verify ----------------------------------------------------------------------------------------

from .. import verify


@verify.check("d4", "every sensor's bundle state is decided, pending by name, or excluded")
def _check_sensor_partition():
    if not config.SENSORS_PATH.exists():
        return None, "no sensors table yet"
    t = bio.read_parquet(config.SENSORS_PATH)
    if t.bundle_decided_by.isna().all():
        return None, "d4 has not run yet"
    pend = int((t.bundle_decided_by == "pending").sum())
    null_bundle_not_pending = int((t.bundle.isna() & t.excluded_reason.isna()
                                   & (t.bundle_decided_by != "pending")).sum())
    ok = null_bundle_not_pending == 0
    return ok, (f"{pend} pending pair member(s), "
                f"{null_bundle_not_pending} unbundled without a named state")


@verify.check("d4", "publishes_under is consistent: one fronting SNR code per bundle, owned by a member")
def _check_fronting():
    if not config.SENSORS_PATH.exists():
        return None, "no sensors table yet"
    t = bio.read_parquet(config.SENSORS_PATH, columns=["site_uid", "snr_id", "bundle", "publishes_under"])
    d = t[t.publishes_under.notna()]
    if not len(d):
        return None, "no assembled records yet"
    bad = 0
    for b, grp in d.groupby("bundle"):
        codes = set(grp.publishes_under)
        if len(codes) != 1 or codes - set(grp.snr_id):
            bad += 1
    return bad == 0, f"{d.bundle.nunique():,} bundles; {bad} with inconsistent fronting"


@verify.check("d4", "every assembled day names exactly one contributing sensor per channel", deep=True)
def _check_record_provenance():
    files = sorted(config.WATER_MERGED.glob("*.parquet"))
    if not files:
        return None, "no assembled records yet"
    bad = 0
    for p in files:
        d = pd.read_parquet(p)
        for ch in registry.NAMES:
            if f"{ch}_mean" in d.columns:
                bad += int((d[f"{ch}_mean"].notna() & d[f"{ch}_src"].isna()).sum())
    return bad == 0, f"{len(files)} assembled record(s), {bad} channel-day(s) missing provenance"
