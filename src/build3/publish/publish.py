"""P1 -- publication: classify, gate, then write. Nothing lands in `published/` before its gate clears.

PUBLICATION CLASSIFIES RATHER THAN DROPS. Every site is published, and each carries its classifications with reasons -- network membership (node, or off-graph and why), basin (method, or absent and why), and per-(site, channel) quality. Record floors DO NOT EXIST here; they moved to access profiles, where each modelling objective sets its own. The only things publication withholds entirely are sites held by an unanswered quality gate and the series of channels a review excluded.

THE PARTITION INVARIANT IS THE ACCEPTANCE TEST. Every registration is exactly one of: published-as-site, sensor-excluded-with-reason, record-less, or held by a named gate -- a partition, COUNTED, and the build fails if any registration is unclassified or claimed twice. "Everything is somewhere, and says why" is this stage's whole contract.

GATE BEFORE WRITE. The quality gate exits before a single parquet lands, so everything in `published/` has passed review by construction rather than by convention -- a stale file is worse than a missing one, because it reads cleanly. For the same reason the water directory is SWEPT first: a site that left the cohort leaves no parquet behind describing a site the table no longer lists.

SENSOR RECORDS ARE NOT RE-PUBLISHED. The raw and canonical per-sensor series stay in the acquisition and derivation stores, where access reads them on request. What publication does carry is the sensors METADATA table, because exclusion visibility is a principle and it must be answerable from the published state alone.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts, io as bio, ledger, registry
from ..acquire import snapshot
from . import quality

# What each upstream stage must have produced. Checked as PATHS, so running p1 alone gates exactly like the full sequence; named down to the FILE, because an empty directory satisfies a directory check.
REQUIRES = (
    ("d3", "the site table", config.SITES_PATH),
    ("d4", "basin delineation", config.BASINS_PATH),
    ("d4", "the graph", config.NODES_PATH),
)

# The published sensors table: registration, snap, type, bundle, exclusion -- the metadata that makes every absence explicable. Records stay upstream.
SENSOR_META_BLOCKS = ("discovery", "snap", "type", "bundle")


def _require_upstream() -> None:
    missing = [(n, why, p) for n, why, p in REQUIRES if not p.exists()]
    if not missing:
        return
    lines = "\n".join(f"    {n}  {why}\n        expects {p}" for n, why, p in missing)
    raise SystemExit(f"\np1 cannot publish yet -- these have not run:\n{lines}\n")


def _stamps() -> dict:
    return {"snapshot": snapshot.head() or "", "aoe": config.aoe_stamp(),
            "ledgers": ledger.ledger_hash(), "registry": registry.fingerprint()}


# ---- classification ----------------------------------------------------------------------------------

def partition(sensors: pd.DataFrame, sites: pd.DataFrame, held_bundles: set) -> pd.Series:
    """One class per registration: `site` | `excluded` | `recordless` | `held`. Exact, or the build fails.

    The classes are checked to PARTITION -- each registration matches exactly one -- because an overlap means two rules claim the same row and a gap means a registration nobody can explain. `held` is only ever non-empty when the caller is reporting on the way to a halt; a build that passes its gates publishes with zero held rows by construction.
    """
    from ..derive.sites import has_record

    site_bundles = set(sites.bundle)
    cls = []
    for r in sensors.itertuples():
        is_excluded = pd.notna(r.excluded_reason)
        is_recordless = not has_record(r.site_uid)
        in_site = pd.notna(r.bundle) and r.bundle in site_bundles
        is_held = (pd.notna(r.bundle) and r.bundle in held_bundles) or r.bundle_decided_by == "pending"
        hits = [k for k, v in (("excluded", is_excluded), ("held", is_held and not is_excluded),
                               ("site", in_site and not is_excluded and not is_held),
                               ("recordless", is_recordless and not in_site and not is_excluded and not is_held)) if v]
        if len(hits) != 1:
            raise SystemExit(f"partition violated for {r.site_uid}: classes {hits or 'NONE'} "
                             f"(bundle={r.bundle!r}, excluded={r.excluded_reason!r}, record={not is_recordless})")
        cls.append(hits[0])
    return pd.Series(cls, index=sensors.site_uid, name="published_as")


def network_class(site) -> str:
    """The site's network-membership classification, with its reason."""
    from ..derive import site_type

    if not bool(site.on_network):
        return "off_graph:off_network"
    if str(site.site_type) in site_type.SUB_CATCHMENT:
        return "off_graph:sub_catchment"
    return "node"


def basin_class(row) -> str:
    """`method:{basin1}` or `absent:{reason}` -- the classification; the geometry lives in basins.parquet."""
    m = row.get("basin_method")
    if isinstance(m, str) and m:
        return f"method:{m}"
    return f"absent:{row.get('no_basin_reason')}"


# ---- the artifacts -----------------------------------------------------------------------------------

def publish_sites(sites: pd.DataFrame, basins_tbl: pd.DataFrame, V: pd.DataFrame) -> pd.DataFrame:
    """The published site table: the D3 table plus its three classifications, per the chapter.

    Quality arrives as one column per channel the site carries -- `quality_{channel}` = keep | exclude, with the reason in `quality_notes` -- so "can I use this site's discharge" is answerable from this one table.
    """
    out = sites.copy()
    b = basins_tbl.set_index("site_id")
    out["network_class"] = [network_class(r) for r in out.itertuples()]
    out["basin_class"] = [basin_class(b.loc[s].to_dict()) if s in b.index else "absent:not_delineated"
                          for s in out.site_id]
    v = V.set_index(["site_id", "channel"]).verdict
    why = V.set_index(["site_id", "channel"]).reason
    for ch in registry.NAMES:
        col = [v.get((s, ch), pd.NA) for s in out.site_id]
        if any(pd.notna(x) for x in col):
            out[f"quality_{ch}"] = pd.array(col, dtype="string")
    noted = V[(V.verdict == "exclude") | (V.exclude_spans.fillna("").str.strip() != "")
              | V.max_threshold.notna()]

    def _note(r):
        if r.verdict == "exclude":
            return f"{r.channel}: {r.reason}"
        bits = []
        if str(r.exclude_spans or "").strip():
            n = quality.span_days(quality.parse_spans(r.exclude_spans))
            bits.append(f"{n} day(s) excised ({r.exclude_spans})")
        if pd.notna(r.max_threshold):
            bits.append(f"days over {r.max_threshold:g} nulled")
        return f"{r.channel}: {'; '.join(bits)}"

    # an empty groupby.apply returns a FRAME, and mapping a frame raises -- the nothing-noted build is the common case, not the edge
    notes = (noted.groupby("site_id").apply(
        lambda g: "; ".join(_note(r) for r in g.itertuples()), include_groups=False)
        if len(noted) else pd.Series(dtype="object"))
    out["quality_notes"] = out.site_id.map(notes).astype("string")
    return out


def _over_threshold(rec: pd.DataFrame, channel: str, thr: float) -> "pd.Series":
    """Days whose readings exceed the sensor's ceiling: any of the channel's statistics above `thr`.

    WHOLE DAYS, because the published record is daily and a day's mean and median were computed from the offending samples too -- nulling only the max would publish statistics quietly contaminated by the reading that was just ruled impossible. Checked across every stat column so a `_dv` day (mean only, no spread) is still judged.
    """
    import numpy as np

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
    """Null every span-excluded day and every over-threshold day, per (site, channel), across ALL that channel's columns including provenance. Returns a NEW records dict; the derived store's frames are never mutated -- they are the full derivation record the review panel draws.

    Applied ONCE, before any artifact is built, so the water files, the pooled projection and the published site table's span columns all describe the same masked data by construction.
    """
    masked = dict(records)
    todo = V[(V.verdict == "keep")
             & (V.exclude_spans.fillna("").str.strip().ne("") | V.max_threshold.notna())]
    for r in todo.itertuples():
        rec = masked.get(r.site_id)
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
        masked[r.site_id] = rec.dropna(how="all", subset=val_cols).reset_index(drop=True)
        parts = ([f"{n_span} day(s) in spans"] if n_span else []) \
            + ([f"{n_thr} day(s) over {r.max_threshold:g}"] if n_thr else [])
        print(f"  excised {' + '.join(parts)} of {r.channel} from {r.site_id}")
    return masked


def republished_spans(sites: pd.DataFrame, records: dict) -> pd.DataFrame:
    """Recompute `n_days_{ch}` / `first_{ch}` / `last_{ch}` from the MASKED records for the published table.

    The derived site table keeps derivation truth; the published one must describe PUBLISHED data -- temporal graph contraction reads these spans, and claiming days an excision removed would silently falsify a variant's overlap.
    """
    out = sites.copy()
    for r in out.itertuples():
        rec = records.get(r.site_id)
        if rec is None or not len(rec):
            continue
        for ch in registry.NAMES:
            col = f"{ch}_mean"
            if f"n_days_{ch}" not in out.columns or col not in rec.columns:
                continue
            d = rec.loc[pd.notna(rec[col]), "date"]
            out.loc[out.index[r.Index], f"n_days_{ch}"] = len(d)
            out.loc[out.index[r.Index], f"first_{ch}"] = str(pd.Timestamp(d.min()).date()) if len(d) else pd.NA
            out.loc[out.index[r.Index], f"last_{ch}"] = str(pd.Timestamp(d.max()).date()) if len(d) else pd.NA
    return out


def publish_water(sites: pd.DataFrame, records: dict, V: pd.DataFrame) -> int:
    """Per-site merged daily series, minus the channels a review excluded. Returns rows written.

    SWEEP FIRST: a site that dropped out of the cohort leaves no stale parquet behind. An excluded channel's columns are withheld here and the site table says why -- classify-not-drop means the FACT is published even where the series is not.
    """
    config.PUB_WATER.mkdir(parents=True, exist_ok=True)
    keep_names = {f"{s.replace(':', '__')}.parquet" for s in sites.site_id}
    stale = [p for p in config.PUB_WATER.glob("*.parquet") if p.name not in keep_names]
    for p in stale:
        p.unlink()
    if stale:
        print(f"  swept {len(stale)} stale per-site parquet(s)")

    excluded = {(r.site_id, r.channel) for r in V.itertuples() if r.verdict == "exclude"}
    n = 0
    for r in sites.itertuples():
        rec = records.get(r.site_id)
        if rec is None or not len(rec):
            continue
        drop = [c for c in rec.columns for ch in registry.NAMES
                if (r.site_id, ch) in excluded and c.startswith(f"{ch}_")]
        out = rec.drop(columns=drop)
        val_cols = [c for c in out.columns if c != "date" and not c.endswith("_src")]
        out = out.dropna(how="all", subset=val_cols)
        bio.write_parquet(out, config.PUB_WATER / f"{r.site_id.replace(':', '__')}.parquet",
                          stamps=_stamps())
        n += len(out)
    return n


def publish_pooled(sites: pd.DataFrame, records: dict, V: pd.DataFrame) -> int:
    """`nitrate_daily.parquet`: the pooled convenience projection -- one read instead of hundreds, never an authority."""
    excluded = {(r.site_id, r.channel) for r in V.itertuples() if r.verdict == "exclude"}
    pooled = []
    cols = [f"nitrate_{s}" for s in ("mean", "min", "max", "p25", "median", "p75", "n_obs")]
    for r in sites.itertuples():
        rec = records.get(r.site_id)
        if rec is None or (r.site_id, "nitrate") in excluded or "nitrate_mean" not in getattr(rec, "columns", []):
            continue
        keep = [c for c in cols + ["nitrate_src"] if c in rec.columns]
        d = rec[["date"] + keep].dropna(subset=["nitrate_mean"])
        if len(d):
            pooled.append(d.assign(site_id=r.site_id))
    out = (pd.concat(pooled, ignore_index=True) if pooled
           else pd.DataFrame(columns=["site_id", "date"] + cols))
    out = out[["site_id"] + [c for c in out.columns if c != "site_id"]]
    bio.write_parquet(out, config.PUB_NITRATE_DAILY, stamps=_stamps())
    return len(out)


def publish_graph_and_basins() -> None:
    """Copy the D4 tables into `published/`, re-stamped. Copies, not links: the published store must stay readable if the derived one is wiped for a rebuild."""
    import geopandas as gpd

    for src, dst in ((config.NODES_PATH, config.PUB_NODES), (config.EDGES_PATH, config.PUB_EDGES),
                     (config.BASIN_REACHES, config.PUB_BASIN_REACHES)):
        bio.write_parquet(bio.read_parquet(src), dst, stamps=_stamps())
    b = gpd.read_parquet(config.BASINS_PATH)
    config.PUB_BASINS.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.PUB_BASINS.with_name(config.PUB_BASINS.name + ".tmp")
    b.to_parquet(tmp)
    tmp.replace(config.PUB_BASINS)
    bio.write_stamps_sidecar(config.PUB_BASINS, _stamps())


def publish_comid_features() -> list[str]:
    """Copy whatever covariate products exist. Absent products stay absent -- publication never invents."""
    config.PUB_COMID_FEATURES.mkdir(parents=True, exist_ok=True)
    done = []
    for p in sorted(config.COVARIATES_DIR.glob("*.parquet")):
        bio.write_parquet(pd.read_parquet(p), config.PUB_COMID_FEATURES / p.name, stamps=_stamps())
        done.append(p.name)
    return done


# ---- the stage ---------------------------------------------------------------------------------------

def main(force: bool = False) -> pd.DataFrame:
    _require_upstream()
    sensors = bio.read_parquet(config.SENSORS_PATH)
    sites = bio.read_parquet(config.SITES_PATH)
    import geopandas as gpd

    basins_tbl = gpd.read_parquet(config.BASINS_PATH).drop(columns="geometry")

    print(f"  {len(sensors)} registration(s), {len(sites)} site(s)")
    records = {}
    for r in sites.itertuples():
        p = config.WATER_SITES / f"{r.site_id.replace(':', '__')}.parquet"
        if p.exists():
            records[r.site_id] = bio.read_parquet(p)

    print("\n[p1.1] quality  (per site, per channel)")
    V, F, series = quality.verdicts(sites, records)
    n_flag = int(V.flagged.sum())
    print(f"  {len(V)} (site, channel) series judged; {n_flag} flagged; "
          f"verdicts: {V.verdict.value_counts().to_dict()}")

    print("\n[p1.2] quality gate  (high-tier channels)")
    led = quality.quality_ledger()
    ask = V[(V.channel.isin(registry.high_scrutiny())) & V.flagged].copy()
    if len(ask):
        # the detector's stuck-window proposals ride along as evidence -- confirming one is a cell copy into exclude_spans, never an automatic action
        prop = (F.reset_index()[["site_id", "channel", "proposed_spans"]]
                if len(F) and "proposed_spans" in F.columns else
                pd.DataFrame(columns=["site_id", "channel", "proposed_spans"]))
        ask = ask.merge(prop, on=["site_id", "channel"], how="left")
        cols = ["members", "channel", "site_id", "reason", "score", "proposed_spans"]
        ordered = ask.sort_values("score", ascending=False)
        led.sync(ordered[cols])
        if led.pending(ordered[["members", "channel"]]):
            # THE PANEL RENDERS BEFORE THE HALT, so the reviewer opens the evidence, not a bare list of ids.
            from . import quality_review

            quality_review.main(F, series)
        worth = {(str(r.members), r.channel): f"score {r.score}" for r in ask.itertuples()}
        led.gate(ordered[["members", "channel"]], worth=worth)
        print(f"  all {len(ask)} flagged high-tier series decided")
    else:
        print("  nothing flagged on a high-tier channel")

    print("\n[p1.3] quality masks  (span exclusions + sensor ceilings)")
    records = apply_quality_masks(records, V)

    print("\n[p1.3b] the partition")
    cls = partition(sensors, sites, held_bundles=set())
    counts = cls.value_counts().to_dict()
    print(f"  {counts} over {len(sensors)} registration(s) -- exact")

    print("\n[p1.4] publish")
    config.PUBLISHED.mkdir(parents=True, exist_ok=True)
    pub_sites = publish_sites(republished_spans(sites, records), basins_tbl, V)
    bio.write_parquet(pub_sites, config.PUB_SITES, stamps=_stamps())

    meta_cols = ["site_uid"] + [c for c, _, b, _ in contracts.SENSOR_COLUMNS
                                if b in SENSOR_META_BLOCKS and c != "site_uid"] + ["channels_present"]
    pub_sensors = sensors[[c for c in meta_cols if c in sensors.columns]].copy()
    pub_sensors["published_as"] = pub_sensors.site_uid.map(cls)
    bio.write_parquet(pub_sensors, config.PUB_SENSORS, stamps=_stamps())

    n_water = publish_water(sites, records, V)
    n_pool = publish_pooled(sites, records, V)
    publish_graph_and_basins()
    feats = publish_comid_features()
    bio.write_csv(V, config.REVIEW_DIR / "quality_verdicts.csv")
    if len(F):
        bio.write_csv(F.reset_index(), config.REVIEW_DIR / "quality_features.csv")

    print(f"  wrote sites ({len(pub_sites)}), sensors ({len(pub_sensors)}), water ({n_water:,} site-days "
          f"over {len(records)} site(s)), nitrate_daily ({n_pool:,}), nodes/edges/basins/basin_reaches, "
          f"comid_features: {feats or 'none yet'}")
    return pub_sites


if __name__ == "__main__":
    main()


# ---- verify ------------------------------------------------------------------------------------------

from .. import verify


@verify.check("p1", "the partition is exact: every registration published-as-site, excluded, record-less, or held")
def _check_partition():
    if not config.PUB_SENSORS.exists():
        return None, "p1 has not run yet"
    s = bio.read_parquet(config.PUB_SENSORS)
    bad = int(s.published_as.isna().sum())
    counts = s.published_as.value_counts().to_dict()
    return bad == 0, f"{counts}; {bad} unclassified"


@verify.check("p1", "published water honours every exclusion: whole channels AND excised spans")
def _check_water_matches_verdicts():
    if not config.PUB_SITES.exists():
        return None, "p1 has not run yet"
    sites = bio.read_parquet(config.PUB_SITES)
    ids = set(sites.site_id)
    orphans, leaked = [], []
    qcols = [c for c in sites.columns if c.startswith("quality_")
             and c != "quality_notes"]
    q = sites.set_index("site_id")[qcols] if qcols else pd.DataFrame()
    for p in config.PUB_WATER.glob("*.parquet"):
        sid = p.stem.replace("__", ":")
        if sid not in ids:
            orphans.append(sid)
            continue
        if len(q):
            d_cols = None
            for ch in registry.NAMES:
                col = f"quality_{ch}"
                # str(), because a site that never carried the channel holds pd.NA here, and `NA == "exclude"` is not False but AMBIGUOUS -- the comparison must never reach an `if` as NA
                if col in q.columns and str(q.loc[sid].get(col)) == "exclude":
                    if d_cols is None:
                        d_cols = set(bio.read_parquet(p, columns=None).columns)
                    if any(c.startswith(f"{ch}_") for c in d_cols):
                        leaked.append((sid, ch))
    # masked (site, channel)s must publish ZERO days inside their spans and ZERO days over their threshold
    span_leaks, thr_leaks = [], []
    vpath = config.REVIEW_DIR / "quality_verdicts.csv"
    if vpath.exists():
        V = pd.read_csv(vpath, dtype=str).fillna("")
        masked_rows = V[(V.verdict == "keep")
                        & ((V.exclude_spans.str.strip() != "") | (V.max_threshold.str.strip() != ""))]
        for r in masked_rows.itertuples():
            p = config.PUB_WATER / f"{r.site_id.replace(':', '__')}.parquet"
            if not p.exists():
                continue
            d = bio.read_parquet(p)
            col = f"{r.channel}_mean"
            if col not in d.columns:
                continue
            if str(r.exclude_spans).strip():
                hit = (quality.in_spans(d.date, quality.parse_spans(r.exclude_spans))
                       & d[col].notna().to_numpy())
                if hit.any():
                    span_leaks.append((r.site_id, r.channel, int(hit.sum())))
            if str(r.max_threshold).strip():
                over = _over_threshold(d, r.channel, float(r.max_threshold))
                if over.any():
                    thr_leaks.append((r.site_id, r.channel, int(over.sum())))
    ok = not orphans and not leaked and not span_leaks and not thr_leaks
    return ok, (f"{len(list(config.PUB_WATER.glob('*.parquet')))} site file(s); "
                f"orphans {orphans[:4]}, leaked channels {leaked[:4]}, in-span days {span_leaks[:4]}, "
                f"over-threshold days {thr_leaks[:4]}")


@verify.check("p1", "published water reproduces the canonical-store derivation to rtol=1e-9", deep=True)
def _check_republication_exact():
    """Rebuild a sample of sites' merged records from the canonical store and compare against the published parquet. Publication must introduce NO numeric change -- this is the build-side half of the chapter's virtual-equals-real invariant (the access-layer half lands with the access layer)."""
    import random

    from ..derive import identity, sites as sites_mod

    if not config.PUB_SITES.exists():
        return None, "p1 has not run yet"
    sites = bio.read_parquet(config.PUB_SITES)
    rng = random.Random(0)
    sample = sites.sample(min(5, len(sites)), random_state=0)
    days = identity.high_tier_days(bio.read_parquet(config.SENSORS_PATH))
    bad = []
    for r in sample.itertuples():
        p = config.PUB_WATER / f"{r.site_id.replace(':', '__')}.parquet"
        if not p.exists():
            continue
        pub = bio.read_parquet(p).set_index("date")
        fresh = sites_mod.merge_record(str(r.bundle).split("+"), days).set_index("date")
        for c in pub.columns:
            if c.endswith("_src"):
                # compared as strings with NA normalised: the parquet round-trip returns pyarrow-backed str where the fresh derivation holds object, and `.equals()` is dtype-strict about a distinction no consumer can observe
                x = pub[c].astype("string").fillna("")
                y = fresh[c].reindex(pub.index).astype("string").fillna("")
                if not (x == y).all():
                    bad.append(f"{r.site_id}.{c}: provenance differs")
                continue
            x = pd.to_numeric(pub[c], errors="coerce").to_numpy(dtype="float64")
            y = pd.to_numeric(fresh[c].reindex(pub.index), errors="coerce").to_numpy(dtype="float64")
            if not np.allclose(x, y, rtol=1e-9, atol=0.0, equal_nan=True):
                bad.append(f"{r.site_id}.{c}: values differ beyond rtol=1e-9")
    return not bad, f"{len(sample)} site(s) re-derived" + (f"; {bad[:4]}" if bad else ", all exact")
