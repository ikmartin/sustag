"""P1 -- publication: classify, gate, then write. Nothing lands in `published/` before its gate clears.

PUBLICATION CLASSIFIES RATHER THAN DROPS. Every registration ends in exactly one state -- `published` (its bundle's assembled record is in the store), `excluded` (a ledgered decision, reason kept), `held` (an unanswered gate names it), or `recordless` -- a PARTITION, counted, and the build fails if any registration is unclassified or claimed twice. Record floors DO NOT EXIST here; they belong to access profiles.

THE PUBLISHED WATER STORE is one file per assembled record, keyed by its fronting SNR id, carrying channel columns and per-day `{channel}_src` provenance -- nothing else. Everything ABOUT a record lives on the published sensors metadata table and the published quality table, one grain each, joined by key; the raw and canonical per-sensor series stay upstream where access reads them on request. The flow-network layers are not copied here: the acquired store is immutable, snapshot-manifested, and never wiped, so access reads it in place -- a 5 GB copy per publish would duplicate an archive to no one's benefit.

GATE BEFORE WRITE. The quality gate exits before a single parquet lands, so everything in `published/` has passed review by construction rather than by convention -- and the water directory is SWEPT first, because a stale file reads cleanly and a missing one does not.

FOUR LEAK SCANS are this stage's acceptance test (verify p1): the published store contains ZERO sentinel values, ZERO out-of-range values, ZERO days inside any confirmed excluded span, and ZERO days above any confirmed ceiling -- each a mechanical scan of the published files against the REGISTRY and the LEDGERS, never against a derived intermediate that could itself be stale.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts, io as bio, ledger, registry
from ..acquire import snapshot
from ..derive.assemble import has_record
from . import quality

# The published sensors table: every block -- the metadata that makes every absence explicable.
SENSOR_META_BLOCKS = ("discovery", "mint", "fetch", "snap", "type", "bundle")


def _stamps() -> dict:
    return {"snapshot": snapshot.head() or "", "aoe": config.aoe_stamp(),
            "ledgers": ledger.ledger_hash(), "registry": registry.fingerprint()}


def load_entities() -> tuple[pd.DataFrame, dict]:
    """(entities, records): one row per assembled record -- `code`, `members`, `channels_present` -- and the record frames keyed by code."""
    if not config.SENSORS_PATH.exists():
        raise SystemExit("no sensors table -- run the build through d4 first")
    sensors = bio.read_parquet(config.SENSORS_PATH)
    pub = sensors[sensors.publishes_under.notna()]
    rows, records = [], {}
    for code, grp in pub.groupby("publishes_under"):
        p = config.WATER_MERGED / f"{code}.parquet"
        if not p.exists():
            continue
        rec = bio.read_parquet(p, expect={"registry": registry.fingerprint()})
        records[code] = rec
        present = "+".join(c for c in registry.NAMES
                           if f"{c}_mean" in rec.columns and rec[f"{c}_mean"].notna().any())
        members = grp.bundle.dropna().unique()
        rows.append(dict(code=code, members=members[0] if len(members) else pd.NA,
                         channels_present=present or pd.NA))
    return pd.DataFrame(rows, columns=["code", "members", "channels_present"]), records


# ---- classification ----------------------------------------------------------------------------------

def partition(sensors: pd.DataFrame, published_codes: set) -> pd.Series:
    """One class per registration: `published` | `excluded` | `recordless` | `held`. Exact, or the build fails."""
    cls = []
    for r in sensors.itertuples():
        is_excluded = pd.notna(r.excluded_reason)
        is_recordless = not has_record(r.site_uid)
        is_published = pd.notna(r.publishes_under) and r.publishes_under in published_codes
        is_held = r.bundle_decided_by == "pending"
        hits = [k for k, v in (("excluded", is_excluded),
                               ("held", is_held and not is_excluded),
                               ("published", is_published and not is_excluded and not is_held),
                               ("recordless", is_recordless and not is_published
                                and not is_excluded and not is_held)) if v]
        if len(hits) != 1:
            raise SystemExit(f"partition violated for {r.site_uid}: classes {hits or 'NONE'} "
                             f"(bundle={r.bundle!r}, publishes_under={r.publishes_under!r}, "
                             f"excluded={r.excluded_reason!r}, record={not is_recordless})")
        cls.append(hits[0])
    return pd.Series(cls, index=sensors.site_uid, name="published_as")


# ---- masks -------------------------------------------------------------------------------------------

def _over_threshold(rec: pd.DataFrame, channel: str, thr: float) -> "pd.Series":
    """Days whose readings exceed the sensor's ceiling: any of the channel's statistics above `thr`.

    WHOLE DAYS, because the published record is daily and a day's mean and median were computed from the offending samples too -- nulling only the max would publish statistics quietly contaminated by the reading that was just ruled impossible. Checked across every stat column so a `_dv` day (mean only, no spread) is still judged.
    """
    stats = [c for c in rec.columns
             if c.startswith(f"{channel}_") and not c.endswith("_src") and not c.endswith("_n_obs")]
    if not stats:
        return pd.Series(False, index=rec.index)
    m = np.zeros(len(rec), dtype=bool)
    for c in stats:
        x = pd.to_numeric(rec[c], errors="coerce")
        m |= (x > thr).fillna(False).to_numpy()
    return pd.Series(m, index=rec.index)


def apply_quality_masks(records: dict, V: pd.DataFrame) -> dict:
    """Null every span-excluded day and every over-threshold day, per (record, channel), across ALL that channel's columns including provenance. Returns a NEW records dict; the derived store's frames are never mutated -- they are the full derivation record the review panel draws.

    Applied ONCE, before any artifact is built, so every published view describes the same masked data by construction.
    """
    masked = dict(records)
    todo = V[(V.verdict == "keep")
             & (V.exclude_spans.fillna("").str.strip().ne("") | V.max_threshold.notna())]
    for r in todo.itertuples():
        rec = masked.get(r.code)
        if rec is None or not len(rec):
            continue
        hit = pd.Series(False, index=rec.index)
        spans_text = str(r.exclude_spans or "").strip()
        if spans_text:
            hit |= pd.Series(quality.in_spans(rec.date, quality.parse_spans(spans_text)), index=rec.index)
        n_span = int(hit.sum())
        n_thr = 0
        if pd.notna(r.max_threshold):
            over = _over_threshold(rec, r.channel, float(r.max_threshold))
            n_thr = int((over & ~hit).sum())
            hit |= over
        if not hit.any():
            continue
        rec = rec.copy()
        cols = [c for c in rec.columns if c.startswith(f"{r.channel}_")]
        rec.loc[hit.to_numpy(), cols] = pd.NA
        val_cols = [c for c in rec.columns if c != "date" and not c.endswith("_src")]
        masked[r.code] = rec.dropna(how="all", subset=val_cols).reset_index(drop=True)
        parts = ([f"{n_span} day(s) in spans"] if n_span else []) \
            + ([f"{n_thr} day(s) over {r.max_threshold:g}"] if n_thr else [])
        print(f"  excised {' + '.join(parts)} of {r.channel} from {r.code}")
    return masked


# ---- the artifacts -----------------------------------------------------------------------------------

def publish_water(entities: pd.DataFrame, records: dict, V: pd.DataFrame) -> int:
    """Per-record assembled daily series, minus the channels a review excluded. Returns rows written.

    SWEEP FIRST: a record that dropped out leaves no stale parquet behind. An excluded channel's columns are withheld here and the published quality table says why -- classify-not-drop means the FACT is published even where the series is not.
    """
    config.PUB_WATER.mkdir(parents=True, exist_ok=True)
    keep_names = {f"{c}.parquet" for c in entities.code}
    stale = [p for p in config.PUB_WATER.glob("*.parquet") if p.name not in keep_names]
    for p in stale:
        p.unlink()
    if stale:
        print(f"  swept {len(stale)} stale published record(s)")

    excluded = {(r.code, r.channel) for r in V.itertuples() if r.verdict == "exclude"}
    n = 0
    for r in entities.itertuples():
        rec = records.get(r.code)
        if rec is None or not len(rec):
            continue
        drop = [c for c in rec.columns for ch in registry.NAMES
                if (r.code, ch) in excluded and c.startswith(f"{ch}_")]
        out = rec.drop(columns=drop)
        val_cols = [c for c in out.columns if c != "date" and not c.endswith("_src")]
        out = out.dropna(how="all", subset=val_cols)
        bio.write_parquet(out, config.PUB_WATER / f"{r.code}.parquet", stamps=_stamps())
        n += len(out)
    return n


def publish_comid_features() -> list[str]:
    """Copy whatever covariate products exist. Absent products stay absent -- publication never invents."""
    config.PUB_COMID_FEATURES.mkdir(parents=True, exist_ok=True)
    done = []
    for p in sorted(config.COVARIATES_DIR.glob("*.parquet")):
        bio.write_parquet(pd.read_parquet(p), config.PUB_COMID_FEATURES / p.name, stamps=_stamps())
        done.append(p.name)
    return done


def publish_proximity() -> int:
    """The pair table, re-stamped into the published store."""
    if not config.PROXIMITY_PATH.exists():
        return 0
    d = bio.read_parquet(config.PROXIMITY_PATH)
    bio.write_parquet(d, config.PUB_PROXIMITY, stamps=_stamps())
    return len(d)


# ---- the stage ---------------------------------------------------------------------------------------

def main(force: bool = False, snapshot: str | None = None) -> pd.DataFrame:
    sensors = bio.read_parquet(config.SENSORS_PATH)
    entities, records = load_entities()
    print(f"  {len(sensors)} registration(s), {len(entities)} assembled record(s)")

    print("\n[p1.1] quality  (per record, per channel)")
    V, F, series = quality.verdicts(entities, records)
    n_flag = int(V.flagged.sum())
    print(f"  {len(V)} (record, channel) series judged; {n_flag} flagged; "
          f"verdicts: {V.verdict.value_counts().to_dict()}")

    print("\n[p1.2] quality gate  (high-tier channels)")
    led = quality.quality_ledger()
    ask = V[(V.channel.isin(registry.high_scrutiny())) & V.flagged].copy()
    if len(ask):
        # the detector's stuck-window proposals ride along as evidence -- confirming one is a cell copy into exclude_spans, never an automatic action
        prop = (F.reset_index()[["code", "channel", "proposed_spans"]]
                if len(F) and "proposed_spans" in F.columns else
                pd.DataFrame(columns=["code", "channel", "proposed_spans"]))
        ask = ask.merge(prop, on=["code", "channel"], how="left")
        cols = ["members", "channel", "code", "reason", "score", "proposed_spans"]
        ordered = ask.sort_values("score", ascending=False)
        led.sync(ordered[cols])
        worth = {(str(r.members), r.channel): f"score {r.score}" for r in ask.itertuples()}
        led.gate(ordered[["members", "channel"]], worth=worth)
        print(f"  all {len(ask)} flagged high-tier series decided")
    else:
        print("  nothing flagged on a high-tier channel")

    print("\n[p1.3] quality masks  (span exclusions + sensor ceilings)")
    records = apply_quality_masks(records, V)

    print("\n[p1.3b] the partition")
    cls = partition(sensors, published_codes=set(records))
    counts = cls.value_counts().to_dict()
    print(f"  {counts} over {len(sensors)} registration(s) -- exact")

    print("\n[p1.4] publish")
    config.PUBLISHED.mkdir(parents=True, exist_ok=True)
    meta_cols = ["site_uid"] + [c for c, _, b, _ in contracts.SENSOR_COLUMNS
                                if b in SENSOR_META_BLOCKS and c != "site_uid"] + \
        ["channels_present"] + [f"n_days_{c}" for c in registry.NAMES]
    pub_sensors = sensors[[c for c in meta_cols if c in sensors.columns]].copy()
    pub_sensors["published_as"] = pub_sensors.site_uid.map(cls)
    bio.write_parquet(pub_sensors, config.PUB_SENSORS, stamps=_stamps())

    n_water = publish_water(entities, records, V)
    n_prox = publish_proximity()
    feats = publish_comid_features()
    bio.write_parquet(V, config.PUBLISHED / "quality.parquet", stamps=_stamps())
    bio.write_csv(V, config.REVIEW_DIR / "quality_verdicts.csv")
    if len(F):
        bio.write_csv(F.reset_index(), config.REVIEW_DIR / "quality_features.csv")

    print(f"  wrote sensors ({len(pub_sensors)}), water ({n_water:,} record-days over "
          f"{len(records)} record(s)), proximity ({n_prox:,} pairs), quality ({len(V)}), "
          f"comid_features: {feats or 'none yet'}")
    return pub_sensors


if __name__ == "__main__":
    main()


# ---- verify ------------------------------------------------------------------------------------------

from .. import verify


@verify.check("p1", "the partition is exact: every registration published, excluded, record-less, or held")
def _check_partition():
    if not config.PUB_SENSORS.exists():
        return None, "p1 has not run yet"
    s = bio.read_parquet(config.PUB_SENSORS)
    bad = int(s.published_as.isna().sum())
    counts = s.published_as.value_counts().to_dict()
    return bad == 0, f"{counts}; {bad} unclassified"


def _ledger_masks():
    """(members, channel) -> (verdict, spans text, threshold) straight from the LEDGER -- the authority, never a derived CSV."""
    out = {}
    for r in ledger.QUALITY.load().itertuples():
        out[(str(r.members), str(r.channel))] = (str(r.decision).strip(),
                                                 str(getattr(r, "exclude_spans", "") or "").strip(),
                                                 quality.parse_threshold(getattr(r, "max_threshold", "")))
    return out


@verify.check("p1", "published water honours every ledger exclusion: whole channels, spans, ceilings")
def _check_water_matches_ledger():
    if not config.PUB_SENSORS.exists():
        return None, "p1 has not run yet"
    s = bio.read_parquet(config.PUB_SENSORS, columns=["site_uid", "bundle", "publishes_under"])
    code_of = dict(zip(s.bundle.dropna(), s.publishes_under[s.bundle.notna()]))
    leaks = []
    for (members, ch), (verdict, spans, thr) in _ledger_masks().items():
        code = code_of.get(members)
        if code is None or pd.isna(code):
            continue
        p = config.PUB_WATER / f"{code}.parquet"
        if not p.exists():
            continue
        d = bio.read_parquet(p)
        col = f"{ch}_mean"
        if verdict == "exclude":
            if any(c.startswith(f"{ch}_") for c in d.columns):
                leaks.append((code, ch, "excluded channel present"))
            continue
        if col not in d.columns:
            continue
        if spans:
            hit = quality.in_spans(d.date, quality.parse_spans(spans)) & d[col].notna().to_numpy()
            if hit.any():
                leaks.append((code, ch, f"{int(hit.sum())} in-span day(s)"))
        if thr is not None:
            over = _over_threshold(d, ch, thr)
            if over.any():
                leaks.append((code, ch, f"{int(over.sum())} over-ceiling day(s)"))
    return not leaks, f"{len(_ledger_masks())} ledger mask(s) checked; leaks: {leaks[:4] or 'none'}"


@verify.check("p1", "the published store holds zero sentinel and zero out-of-range values", deep=True)
def _check_no_sentinels_or_range_leaks():
    """The two mechanical leak scans: every value in every published file against the registry's ranges and every source's scaled sentinels. Zero tolerance -- these are the chapter's first two cleaning mechanisms, audited at the door."""
    files = sorted(config.PUB_WATER.glob("*.parquet"))
    if not files:
        return None, "no published water yet"
    scaled_sentinels: set[float] = set()
    for ch, c in registry.CHANNELS.items():
        for src_name, sm in c.sources.items():
            for v in registry.SENTINELS.get(src_name, ()):
                scaled_sentinels.add(round(float(v) * sm.scale, 6))
    n_sent = n_range = 0
    for p in files:
        d = bio.read_parquet(p)
        for ch, c in registry.CHANNELS.items():
            stats = [x for x in d.columns if x.startswith(f"{ch}_")
                     and not x.endswith("_src") and not x.endswith("_n_obs")]
            for col in stats:
                v = pd.to_numeric(d[col], errors="coerce")
                if c.lo is not None:
                    n_range += int((v < c.lo).sum())
                if c.hi is not None:
                    n_range += int((v > c.hi).sum())
                n_sent += int(v.round(6).isin(scaled_sentinels).sum())
    ok = n_sent == 0 and n_range == 0
    return ok, f"{len(files)} file(s): {n_sent} sentinel value(s), {n_range} out-of-range value(s)"


@verify.check("p1", "published water reproduces the canonical-store derivation to rtol=1e-9", deep=True)
def _check_republication_exact():
    """Rebuild a sample of records from the canonical store and compare against the published parquet. Publication must introduce NO numeric change beyond its declared masks -- the build-side half of the virtual-equals-real invariant."""
    from ..derive import assemble, identity

    if not config.PUB_SENSORS.exists():
        return None, "p1 has not run yet"
    s = bio.read_parquet(config.PUB_SENSORS)
    pub = s[s.publishes_under.notna()].drop_duplicates("publishes_under")
    if not len(pub):
        return None, "nothing published yet"
    sample = pub.sample(min(5, len(pub)), random_state=0)
    days = identity.high_tier_days(bio.read_parquet(config.SENSORS_PATH))
    masks = _ledger_masks()
    bad = []
    for r in sample.itertuples():
        p = config.PUB_WATER / f"{r.publishes_under}.parquet"
        if not p.exists():
            continue
        if any(m == str(r.bundle) for (m, _), _ in masks.items()):
            continue                    # masked records legitimately differ; the mask check covers them
        pubd = bio.read_parquet(p).set_index("date")
        fresh = assemble.merge_record(str(r.bundle).split("+"), days).set_index("date")
        for c in pubd.columns:
            if c.endswith("_src"):
                x = pubd[c].astype("string").fillna("")
                y = fresh[c].reindex(pubd.index).astype("string").fillna("")
                if not (x == y).all():
                    bad.append(f"{r.publishes_under}.{c}: provenance differs")
                continue
            x = pd.to_numeric(pubd[c], errors="coerce").to_numpy(dtype="float64")
            y = pd.to_numeric(fresh[c].reindex(pubd.index), errors="coerce").to_numpy(dtype="float64")
            if not np.allclose(x, y, rtol=1e-9, atol=0.0, equal_nan=True):
                bad.append(f"{r.publishes_under}.{c}: values differ beyond rtol=1e-9")
    return not bad, f"{len(sample)} record(s) re-derived" + (f"; {bad[:4]}" if bad else ", all exact")
