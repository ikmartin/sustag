"""D3 orchestrator: from registrations to THE site table, minted once.

THE SITE IS THE UNIT OF ANALYSIS; THE SENSOR IS A DEVICE THAT REPORTED. This stage runs the chapter's D3 sequence in order: sensor snap (incremental -- only never-attempted registrations) -> type assignment -> bundle candidates by shared reach -> sensor exclusions applied from the ledger -> gated review -> site snap -> id minting -> the site table. Later stages consume the table as given; no stage re-derives site-ness.

THE SITE OWNS ITS OWN SNAP. Sensor snaps are cheap candidate-generation devices; once a bundle is decided, the site snaps ONCE from its highest-authority member's location -- declared operator co-location outranking longest record -- and the id, the basin, and the node all derive from that snap. A bundle whose members' snapped reaches DISAGREE halts by name: the standing merge decision and the current geometry contradict each other, and only a person can say which is wrong (revisit `decisions/merge.csv`, or exclude the offending sensor).

IDS ARE POSITIONS, AND A COLLISION IS A CONTRADICTION. `S{comid}-{pos:05d}` sorts into flow order along a reach by construction; two sites minting the same id means a *distinct* ruling plus coincident geometry, so the build halts and names the pair rather than suffixing its way past the evidence. Off-network sites -- failed or far snaps, non-surface types -- get `OFF-{canonical member uid}`, visibly non-positional.

WINNER PER SPAN, NEVER A BLEND. Where two members overlap on a channel, each published day comes from exactly ONE contributor, chosen by the channel's registry merge policy, with the contributing sensor recorded per day (`{channel}_src`). Blending two instruments' values would manufacture numbers no instrument reported and destroy the provenance a reviewer needs when the pair later disagrees.

A SITE REQUIRES AT LEAST ONE CANONICAL RECORD on some channel. Record-less registrations remain registered -- snapped, typed, bundled -- and are never sites; their emptiness was established at canonicalisation and this stage READS it rather than rediscovering it downstream.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts, io as bio, ledger, registry
from ..acquire import network, snapshot
from . import identity, site_type
from . import snap as snap_mod

SNAP_COLS = [c for c, _, b, _ in contracts.SENSOR_COLUMNS if b == "snap"]
TYPE_COLS = [c for c, _, b, _ in contracts.SENSOR_COLUMNS if b == "type"]
BUNDLE_COLS = [c for c, _, b, _ in contracts.SENSOR_COLUMNS if b == "bundle"]

# Evidence a type reviewer sees next to the row they are deciding, in the order it helps.
TYPE_EVIDENCE = ["station_name", "site_type", "site_type_source", "site_type_conflict",
                 "ambiguity", "snap_status", "snap_dist_m", "source"]


def has_record(uid: str) -> bool:
    """Does D2 hold a canonical record for this registration? The site-eligibility fact, read off disk."""
    return (config.WATER_CANONICAL / f"{contracts.uid_to_filename(uid)}.parquet").exists()


def _network_layers():
    import geopandas as gpd

    if not network.exists():
        raise FileNotFoundError("network layers are missing -- run the acquisition network stage first")
    fl = gpd.read_parquet(network._path("flowlines"))
    cat = gpd.read_parquet(network._path("catchments"))
    vaa = pd.read_parquet(network._path("vaa"))
    return fl, cat, vaa


# ---- exclusions -------------------------------------------------------------------------------------

def type_review_set(sensors: pd.DataFrame) -> pd.DataFrame:
    """Sites the type review covers: ambiguous, RECORDED, non-excluded -- plus everything already decided, tagged.

    ONE FRAME FOR THE PANEL AND THE SHEET, so the two can never disagree about which row is number 7. A record is what makes a type matter: typing decides whether a site can hold a surface basin, and a record-less registration has no route into any cohort -- once the census snapped 15,532 record-less rows, 711 of 735 outstanding type questions were exactly those, and a queue that long is not read at all. Not asking is not deciding: the classifier's type stands, the auto record holds it, and a sensor that later gains a record arrives here on the next build.
    """
    tled = site_type.type_ledger()
    recorded = sensors.site_uid.map(has_record)
    excluded = (sensors.excluded_reason.notna() if "excluded_reason" in sensors.columns
                else pd.Series(False, index=sensors.index))
    return site_type.ambiguous(sensors[recorded & ~excluded],
                               include={k[0] for k in tled.decided_keys()})


def apply_exclusions(sensors: pd.DataFrame) -> pd.DataFrame:
    """Stamp `excluded_reason` from the sensor-exclusion ledger; sensors it names leave every bundle.

    Rows are HUMAN-INITIATED -- a person adds a uid to `decisions/exclusions.csv` on prior knowledge (a test logger, a scratch deployment); nothing queues them automatically. `exclude` stamps the reason (the row's note, or the bare verdict); `keep` records that the question was asked and answered. A row someone opened but left blank gates in `main` -- an open question about membership cannot be built past.
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


def site_review_queue(sensors: pd.DataFrame) -> pd.DataFrame:
    """One row per DECIDED multi-member bundle: the composed site, offered for confirmation whole.

    Pairwise merge answers are the EDITING mechanism -- they match the evidence, and their keys survive every cohort change. But each answer is about an edge, and the build mints a NODE nobody has looked at: which member's location wins authority, what the assembled record gains from whom, what three pairwise-sound answers compose into. This queue is that node, keyed on the sorted member set, so changing the membership (by re-answering pairs) makes a NEW key and re-asks by construction. Unanswered rows sort first; singletons never appear -- there is nothing composed to confirm.
    """
    s = sensors[sensors.bundle.notna() & sensors.bundle.str.contains(r"\+")]
    if not len(s):
        return pd.DataFrame(columns=["members"])
    done = {k[0] for k in ledger.SITE_REVIEW.decided_keys()}

    def _from_store(uid):
        """(nitrate days, channels) read off the canonical FILE -- the fallback when the table's canon block is not populated yet (D2 owns those columns; a review must never show 0 for a sensor whose record is on disk)."""
        f = identity._canonical(uid)
        if f is None:
            return 0, []
        n = int(f["nitrate_mean"].notna().sum()) if "nitrate_mean" in f.columns else 0
        return n, identity.channels_of(f)

    rows = []
    for b, grp in s.groupby("bundle"):
        canon = grp.site_uid[grp.is_bundle_canonical.fillna(False)].tolist()
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
        rows.append(dict(
            members=b, n_members=len(grp),
            canonical_uid=canon[0] if canon else pd.NA,
            rule="+".join(sorted(set(grp.bundle_rule.dropna()))),
            comid=comids[0] if len(comids) == 1 else "DISAGREE:" + ",".join(map(str, comids)),
            nitrate_days_by_member="; ".join(f"{u}:{d}" for u, d in sorted(days.items())),
            channels="+".join(chans),
            station_names=" | ".join(str(n)[:34] for n in grp.station_name.fillna("?")),
        ))
    q = pd.DataFrame(rows)
    q["_done"] = q.members.isin(done).astype(int)
    return q.sort_values(["_done", "members"], ignore_index=True).drop(columns="_done")


# ---- site assembly ----------------------------------------------------------------------------------

def _colocation_anchors(sensors: pd.DataFrame) -> tuple[dict[str, str], dict[str, str]]:
    """`(declared, measured)`: member uid -> the USGS uid it co-locates with, by each kind of evidence.

    CO-LOCATION IS THE FACT; THE DECLARATION IS ONE WAY OF KNOWING IT. Keying the anchor on an operator's assertion alone made the rule hostage to whether a source still publishes the column: IIHR retired `colloc_uid` in 2026-08, and three sites bundled with their USGS gage would have silently re-snapped from the IWQIS station -- re-minting three ids -- for a reason that has nothing to do with where anything is. Measurement answers the same question and does not retire, so both are collected and the caller prefers the declaration only where one exists.
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


def _authority(members: list[str], canon: str, anchors: tuple[dict[str, str], dict[str, str]]) -> tuple[str, str]:
    """(uid, rank) of the member whose location the site snaps from.

    A station co-located with a USGS gage takes the GAGE's coordinate: a gage location is surveyed, a station's is recorded where the operator stood. Declared co-location is preferred where an operator asserts it and measured co-location carries the rest, so the rank records which kind of evidence decided. Otherwise the canonical member: the longest high-tier record is the deployment with the most operational history behind its coordinates. A human overrides neither directly; they exclude the offending sensor or re-answer the merge, which changes the bundle and therefore the answer here.
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
    """The site's merged daily record: per channel, winner-per-span across members, provenance per day.

    Rank per channel is the registry policy -- `longest` today: most days on THAT channel, canonical-tier days breaking ties, uid breaking those. One channel's day never mixes stats from two contributors: the winner supplies every statistic of that day or none.
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


def build(sensors: pd.DataFrame, fl, cat, vaa) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Mint the site table and merge each site's record. Halts on reach disagreement and on id collision.

    Returns `(sites, records)` with `records` keyed by site_id. Only DECIDED bundles with at least one canonical record become sites; pending bundles were already gated upstream, so reaching this function with one is a caller error, not a review queue.
    """
    s = sensors.set_index("site_uid", drop=False)
    bundles = sensors.bundle.dropna().unique()
    days = identity.high_tier_days(sensors)
    anchors = _colocation_anchors(sensors)

    # THE HALT COMES FIRST, over every bundle at once -- a reviewer should see the whole queue, not one name per rerun.
    disagreements = []
    minted, records, rows = {}, {}, []
    plans = []
    for b in bundles:
        members = b.split("+")
        recorded = [m for m in members if has_record(m)]
        if not recorded:
            continue                    # registered, never a site
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

    # ONE authoritative snap pass for every site at once -- same machinery, same layers as the sensor snap.
    canon_of = {b: min(m, key=lambda u: (-int(days.get(u, 0)), u)) for b, m in plans}
    auth = {b: _authority(m, canon_of[b], anchors) for b, m in plans}
    frame = pd.DataFrame([dict(site_uid=b, lat=s.loc[a, "lat"], lon=s.loc[a, "lon"],
                               station_name=s.loc[a, "station_name"],
                               source_drainage_area_km2=s.loc[a, "source_drainage_area_km2"])
                          for b, (a, _) in auth.items()])
    snapped = (snap_mod.snap(frame, fl, cat, vaa).set_index("site_uid")
               if len(frame) else pd.DataFrame())

    for b, members in plans:
        canon, (auth_uid, rank) = canon_of[b], auth[b]
        sn = snapped.loc[b]
        stype = s.loc[canon, "site_type"]
        member_types = sorted({t for m in members if pd.notna(t := s.loc[m, "site_type"])})
        conflict = ("+".join(member_types) if len(member_types) > 1
                    else s.loc[canon, "site_type_conflict"])
        off = (pd.isna(sn.comid) or sn.snap_status != "ok"
               or (pd.notna(stype) and stype in site_type.NO_SURFACE_BASIN))
        sid = (contracts.make_off_network_id(canon) if off
               else contracts.make_site_id(int(sn.comid), float(sn.snap_pos_m)))
        if sid in minted:
            raise SystemExit(
                f"\nsite id {sid} minted twice: bundles {minted[sid]!r} and {b!r}.\n"
                f"A distinct ruling plus coincident geometry means either the ruling or a snap is wrong -- "
                f"re-examine the pair in {ledger.MERGE.path}.")
        minted[sid] = b

        rec = merge_record(members, days)
        records[sid] = rec
        spans = {}
        if len(rec):
            r = rec.set_index("date")
            for ch in registry.NAMES:
                col = f"{ch}_mean"
                if col in r.columns and r[col].notna().any():
                    d = r[col].dropna().index
                    spans[f"n_days_{ch}"] = int(len(d))
                    spans[f"first_{ch}"] = str(pd.Timestamp(d.min()).date())
                    spans[f"last_{ch}"] = str(pd.Timestamp(d.max()).date())
        present = "+".join(c for c in registry.NAMES if spans.get(f"n_days_{c}"))

        rows.append(dict(
            site_id=sid, bundle=b, n_members=len(members), canonical_uid=canon,
            authority_uid=auth_uid, authority_rank=rank,
            station_name=s.loc[auth_uid, "station_name"], on_network=not off,
            lat=float(s.loc[auth_uid, "lat"]), lon=float(s.loc[auth_uid, "lon"]),
            **{c: sn.get(c) for c in SNAP_COLS},
            site_type=stype, site_type_source=s.loc[canon, "site_type_source"],
            site_type_conflict=conflict, channels_present=present or pd.NA, **spans,
        ))

    sites = contracts.conform_sites(pd.DataFrame(rows, columns=contracts.SITE_COLS))
    bad = contracts.validate_sites(sites) if len(sites) else []
    if bad:
        raise ValueError("site table violates the contract:\n  " + "\n  ".join(bad))
    return sites, records


# ---- the stage --------------------------------------------------------------------------------------

def main() -> pd.DataFrame:
    sensors = bio.read_parquet(config.SENSORS_PATH)
    fl, cat, vaa = _network_layers()

    # 1 -- sensor snap, incremental: a null snap_status is a registration never attempted.
    todo = sensors[sensors.snap_status.isna()] if "snap_status" in sensors.columns else sensors
    print(f"snap: {len(todo)} of {len(sensors)} registration(s) never attempted")
    if len(todo):
        block = snap_mod.snap(todo, fl, cat, vaa)
        sensors = contracts.update_block(sensors, block, SNAP_COLS)

    # 2 -- type assignment; human decisions apply inside classify (rank 0).
    typed = site_type.classify(sensors)
    sensors = contracts.update_block(sensors, typed, TYPE_COLS)

    # 3 -- exclusions from the ledger.
    sensors = apply_exclusions(sensors)
    n_exc = int(sensors.excluded_reason.notna().sum())
    if n_exc:
        print(f"  {n_exc} sensor(s) excluded by ledger")
    eligible = sensors[sensors.excluded_reason.isna()].reset_index(drop=True)

    # PERSIST THE SNAP AND TYPE BLOCKS NOW, before identity can halt. Identity's contradiction halt raises
    # inside step 4, and a persist that waits for step 5 discards a 20,000-sensor snap pass every time a
    # decision sheet is inconsistent -- the exact loss the persist-before-gate rule exists to prevent. The
    # bundle block is re-persisted at step 5 once identity has run.
    _persist_sensors(sensors)

    # 4 -- identity over eligible sensors; syncs decisions/merge.csv and the auto record itself.
    iblock, cand = identity.main(eligible)
    bcols = [c for c in BUNDLE_COLS if c not in ("site_id", "excluded_reason")]
    sensors = contracts.update_block(sensors, iblock, bcols)
    excluded = sensors.excluded_reason.notna()
    for c in [*bcols, "site_id"]:
        sensors.loc[excluded, c] = pd.NA       # an excluded sensor is in NO bundle, stale stamps included

    # 5 -- type review sheet, the site-composition sheet, then EVERY gate at once: one sitting, not one
    # rerun per queue.
    tled = site_type.type_ledger()
    amb = type_review_set(sensors)
    if len(amb):
        sheet = amb.copy()
        sheet["question"] = [site_type.question(r) for r in sheet.itertuples()]
        cols = ["site_uid", "question"] + [c for c in TYPE_EVIDENCE if c in sheet.columns]
        tled.sync(sheet[cols])
    composed = site_review_queue(sensors)
    if len(composed):
        ledger.SITE_REVIEW.sync(composed)
    # PERSIST AGAIN, NOW WITH THE BUNDLE BLOCK. The blocks are FACTS -- the bundle block's pending-NULL
    # semantics exist precisely so an undecided state is safe on disk. Gate-before-write is publication's
    # rule, about the published store; the sensors table is derivation's own evidence record.
    _persist_sensors(sensors)

    halts = []
    for gate_fn in (lambda: tled.gate(amb),
                    lambda: ledger.MERGE.gate(identity.order_for_review(cand[identity.blocks(cand)])
                                              if len(cand) else cand),
                    lambda: ledger.SITE_REVIEW.gate(composed)):
        try:
            gate_fn()
        except SystemExit as e:
            halts.append(str(e))
    halts.extend(_exclusion_gate())
    if halts:
        # THE PANELS RENDER BEFORE THE HALT, so the reviewer opens evidence, not a bare list of ids.
        for render in ("merge_review", "site_type_review", "site_review"):
            try:
                import importlib

                importlib.import_module(f"src.build3.derive.{render}").main()
            except Exception as e:                                    # noqa: BLE001 -- a panel failure must not mask the gate
                print(f"  {render} panel failed to render ({type(e).__name__}: {e}) -- the sheets still hold the queue")
        raise SystemExit("\n".join(halts))

    # 6-8 -- mint sites, merge records.
    sites, records = build(sensors, fl, cat, vaa)
    sid_of = dict(zip(sites.bundle, sites.site_id))
    sensors["site_id"] = sensors.bundle.map(sid_of).astype("string")

    stamps = {"snapshot": snapshot.head() or "", "aoe": config.aoe_stamp(),
              "ledgers": ledger.ledger_hash(), "registry": registry.fingerprint()}
    # SWEEP FIRST. A re-decided bundle re-mints its site id, and the old id's merged record would otherwise
    # sit beside the new one reading cleanly -- describing a site the table no longer lists. Same rule as
    # publication's sweep, for the same reason: a stale file is worse than a missing one.
    keep = {f"{sid.replace(':', '__')}.parquet" for sid in records}
    stale = [q for q in config.WATER_SITES.glob("*.parquet") if q.name not in keep]
    for q in stale:
        q.unlink()
    if stale:
        print(f"  swept {len(stale)} stale merged record(s) (re-minted or retired site ids)")
    for sid, rec in records.items():
        bio.write_parquet(rec, config.WATER_SITES / f"{sid.replace(':', '__')}.parquet",
                          stamps={"registry": registry.fingerprint()})
    bio.write_parquet(sites, config.SITES_PATH,
                      stamps={**stamps, "schema": contracts.schema_fingerprint(contracts.SITE_DTYPES)})
    out = contracts.conform_sensors(sensors)
    bad = contracts.validate_sensors(out)
    if bad:
        raise ValueError("sensors table violates the contract:\n  " + "\n  ".join(bad))
    bio.write_parquet(out, config.SENSORS_PATH,
                      stamps={**stamps, "schema": contracts.schema_fingerprint(contracts.SENSOR_DTYPES)})

    _write_site_comids(sites)

    on = int(sites.on_network.sum()) if len(sites) else 0
    print(f"\nminted {len(sites)} site(s) ({on} on-network, {len(sites) - on} OFF) from "
          f"{int(sensors.bundle.notna().sum())} bundled sensor(s); records in {config.WATER_SITES.name}/")
    return sites


def _write_site_comids(sites: pd.DataFrame) -> None:
    """Capture the on-network site COMIDs as a parameter file, for the NWM fetch.

    Acquisition must not import derivation, and the NWM pull is scoped to exactly these reaches -- so D3 writes the list where parameters live and `acquire.nwm` reads a FILE, making the fetch reproducible from tracked inputs alone. Regenerated on every mint; sorted, so the diff under git is the actual membership change.
    """
    comids = sorted({int(c) for c in sites[sites.on_network.fillna(False)].comid.dropna()})
    p = config.PARAMETERS / "site_comids.txt"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("# on-network site COMIDs, written by derive.sites -- input to acquire.nwm\n"
                   + "\n".join(str(c) for c in comids) + "\n")
    tmp.replace(p)
    print(f"  wrote {p.name} ({len(comids)} reach(es)) for the NWM fetch")


# ---- verify ----------------------------------------------------------------------------------------

from .. import verify


@verify.check("d3", "site ids partition the bundles: unique, flow-ordered, namespace matches on_network")
def _check_site_ids():
    if not config.SITES_PATH.exists():
        return None, "no site table yet"
    t = bio.read_parquet(config.SITES_PATH)
    bad = contracts.validate_sites(t)
    if bad:
        return False, "; ".join(bad)
    on = t[t.on_network.fillna(False)]
    ok_form = on.site_id.str.match(r"^S\d+-\d{5}$").all() if len(on) else True
    return bool(ok_form), f"{len(t)} site(s), {len(on)} on-network, ids well-formed: {ok_form}"


@verify.check("d3", "every sensor is snapped or has a named failure; bundles decided or pending by name")
def _check_sensor_partition():
    if not config.SENSORS_PATH.exists():
        return None, "no sensors table yet"
    t = bio.read_parquet(config.SENSORS_PATH)
    if t.bundle_decided_by.isna().all():
        return None, "d3 has not run yet"
    unsnapped = int(t.snap_status.isna().sum())
    pend = int((t.bundle_decided_by == "pending").sum())
    null_bundle_not_pending = int((t.bundle.isna() & t.excluded_reason.isna()
                                   & (t.bundle_decided_by != "pending")).sum())
    ok = null_bundle_not_pending == 0
    return ok, (f"{unsnapped} never-snapped, {pend} pending pair member(s), "
                f"{null_bundle_not_pending} unbundled without a named state")


@verify.check("d3", "every merged site day names exactly one contributing sensor per channel", deep=True)
def _check_record_provenance():
    files = sorted(config.WATER_SITES.glob("*.parquet"))
    if not files:
        return None, "no merged site records yet"
    bad = 0
    for p in files:
        d = pd.read_parquet(p)
        for ch in registry.NAMES:
            if f"{ch}_mean" in d.columns:
                bad += int((d[f"{ch}_mean"].notna() & d[f"{ch}_src"].isna()).sum())
    return bad == 0, f"{len(files)} site record(s), {bad} channel-day(s) missing provenance"


if __name__ == "__main__":
    main()
