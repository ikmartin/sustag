"""A4 -- verification: every authority-pair disagreement named, units verified by measurement, drift adjudicated.

ONE REPORT, because scattered verification with no consumer is how a station with 13,644 observations and no location stayed invisible for two months. Every section returns rows into a single frame written beside the snapshot; a gap that is not named here does not exist as far as the build can tell.

DRIFT IS ADJUDICATED, NOT ABSORBED. `diff_snapshots` classifies every manifest change on CONTENT EVIDENCE -- sha256, the parquet schema fingerprint, and the newest data timestamp the manifest probes carry -- never on mtime, which a rewrite always refreshes and which therefore cannot distinguish a trailing-window refresh from a revision of approved data. The expected classes (trailing revisions, partition appends, additions) pass; the conflicting ones -- an authority changed something already relied upon -- queue to the drift ledger and GATE. Both directions have precedent: a repaired export is drift to accept, a malformed registry row is drift to reject. A snapshot whose notes begin `adoption:` amnesties its own diff against its parent, exactly once: adopted or wholesale-replaced files have unknown or operator-supplied provenance, and flooding the queue with unanswerable rows would teach the reviewer to stop reading it.

RAW IS NEVER WRITTEN BY THE BUILD, so ANY change to a `raw/` file is a conflict unless amnestied -- vendor drops arrive through adoption-noted snapshots, not through silent edits.

UNITS: three states, and only measurement closes the question. The registry declares the current status per mapping; this stage measures cross-source agreement at co-located sensors (exact-timestamp joins -- a tolerance join manufactures agreement on a series that varies within the day) and reports the verdicts against the declared state. Promotion to `verified` is a registry edit a human makes with the verdict in front of them, not an automatic write-back.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts, ledger, registry, verify
from .. import io as bio
from . import records, snapshot
from .sources import iwqis, mpca

# Two sensors this far apart are comparable for a UNIT check -- generous on purpose: this asks "is the
# scale right", not "is this one gauge"; a pair that is two real sites costs a wide ratio spread that
# `n` and separation make legible, while a tight radius would leave most channels with no pair at all.
COLOCATION_M = 300.0
MIN_MATCHED = 30

REPORT_PATH = config.ACQUIRED / "verification_report.parquet"


# ---- authority reconciliation ---------------------------------------------------------------------

def reconcile_sources() -> pd.DataFrame:
    """Every disagreement between a source's own authorities, one row each.

    IWQIS has three authorities -- the registry (location), the measures table (deployments), the export (observations). All pairs checked, both directions, WITHIN THE NAMESPACE (the namespace rule is already this source's scope filter, and a reconciliation across it compares things that were never meant to correspond); the acknowledged set lives in the registry-gaps ledger.
    """
    rows = []
    reg = iwqis._registry()
    reg_uids = set(reg["uid"].astype(str).str.strip().str.upper())

    export_uids = set()
    files = iwqis._chunk_files()
    if files:
        import pyarrow as pa
        import pyarrow.csv as pv

        opts = pv.ConvertOptions(column_types={"site_uid": pa.string()}, include_columns=["site_uid"])
        seen = pa.concat_tables([pv.read_csv(f, convert_options=opts) for f in files]).to_pandas()
        export_uids = set(seen.site_uid[iwqis._is_uid(seen.site_uid).to_numpy()].str.strip().str.upper())

    measures_path = config.RAW / "water" / "measures.csv"
    measures_uids = set()
    if measures_path.exists():
        m = pd.read_csv(measures_path, dtype=str)
        measures_uids = set(m.site_uid.dropna().str.strip().str.upper())

    ack = {uid for (src, uid), d in ledger.REGISTRY_GAPS.decisions().items()
           if src == "IWQIS" and d == "acknowledged"}

    def gap(uid, kind, detail):
        rows.append(dict(section="reconcile", source="IWQIS", key=uid, kind=kind,
                         detail=detail, acknowledged=uid in ack))

    for uid in sorted(export_uids - reg_uids):
        gap(uid, "export_not_registry", "observations exist; no location -- cannot be registered")
    for uid in sorted((export_uids & reg_uids) - measures_uids):
        gap(uid, "registry_not_measures", "registered with observations but no deployment record")
    return pd.DataFrame(rows)


# ---- units by measurement -------------------------------------------------------------------------

def _native(uid: str) -> pd.DataFrame | None:
    p = config.ACQ_WATER_NATIVE / f"{contracts.uid_to_filename(uid)}.parquet"
    return pd.read_parquet(p) if p.exists() else None


def colocated_pairs(sensors: pd.DataFrame) -> pd.DataFrame:
    """Cross-source pairs measuring the same water: declared co-location first, proximity second.

    Declared beats distance -- where the declaration survives measurement. MPCA's site list names the USGS gage a station sits on, and all 13 of its checkable assertions land within 300 m (median 34.6 m), so it is kept. IWQIS's retired `colloc_uid` did not survive: its 32 true pairs were all proximity-found and its 9 unique contributions were the ones geometry contradicts (a gage named 18.4 km away), so it is not read. A declaration is evidence to be verified, not a fact to be trusted.

    Same-source pairs are excluded; two IWQIS sensors agreeing says nothing about IWQIS's units.
    """
    import geopandas as gpd
    from scipy.spatial import cKDTree

    r = sensors.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    g = gpd.GeoSeries(gpd.points_from_xy(r.lon, r.lat), crs=4326).to_crs(config.EQUAL_AREA)
    xy = np.c_[g.x, g.y]
    uid = r.site_uid.to_numpy()
    src = r.source.to_numpy()
    pos = {u: i for i, u in enumerate(uid)}
    rows: dict[tuple, dict] = {}

    if mpca._SITES.exists():
        m = pd.read_csv(mpca._SITES, dtype=str)
        for cs, us in m.dropna(subset=["usgs_id"])[["csg_id", "usgs_id"]].itertuples(index=False):
            a, b = f"MPCA:{cs}", f"USGS:{us}"
            if a in pos and b in pos:
                rows[tuple(sorted((a, b)))] = dict(sep_m=float(np.hypot(*(xy[pos[a]] - xy[pos[b]]))),
                                                   how="declared")
    tree = cKDTree(xy)
    for i, j in tree.query_pairs(COLOCATION_M):
        if src[i] == src[j]:
            continue
        key = tuple(sorted((uid[i], uid[j])))
        rows.setdefault(key, dict(sep_m=float(np.hypot(*(xy[i] - xy[j]))), how="distance"))

    if not rows:
        return pd.DataFrame(columns=["uid_a", "uid_b", "sep_m", "how"])
    out = pd.DataFrame([{"uid_a": a, "uid_b": b, **v} for (a, b), v in rows.items()])
    return out.sort_values("sep_m", ignore_index=True)


def compare(uid_a: str, uid_b: str, channel: str) -> dict | None:
    """Agreement on one channel in canonical units, joined on EXACT timestamps. None unless both carry it.

    Sentinels are already masked inside `registry.extract`, so a fill value can no longer distort a co-location median ratio.
    """
    a, b = _native(uid_a), _native(uid_b)
    if a is None or b is None:
        return None
    sa, _ = registry.extract(a, contracts.split_uid(uid_a)[0], channel)
    sb, _ = registry.extract(b, contracts.split_uid(uid_b)[0], channel)
    if sa is None or sb is None:
        return None
    j = pd.concat([sa.rename("a"), sb.rename("b")], axis=1, join="inner").dropna()
    if len(j) < MIN_MATCHED:
        return None
    nz = j[j.b.abs() > 1e-9]
    return dict(uid_a=uid_a, uid_b=uid_b, channel=channel, n=len(j),
                ratio=float((nz.a / nz.b).median()) if len(nz) else np.nan,
                abs_diff=float((j.a - j.b).abs().median()),
                a_median=float(j.a.median()), b_median=float(j.b.median()))


def units_verdicts(sensors: pd.DataFrame) -> pd.DataFrame:
    """Per-channel unit verdicts over every comparable co-located pair, plus the still-assumed worklist."""
    pairs = colocated_pairs(sensors)
    print(f"  {len(pairs)} co-located cross-source pair(s) "
          f"({int((pairs.how == 'declared').sum())} declared, "
          f"{int((pairs.how == 'distance').sum())} within {COLOCATION_M:.0f} m)")
    rows = []
    for p in pairs.itertuples():
        for ch in registry.NAMES:
            r = compare(p.uid_a, p.uid_b, ch)
            if r:
                rows.append({**r, "sep_m": p.sep_m, "how": p.how})
    out = pd.DataFrame(rows)
    verdicts = []
    if len(out):
        for ch, g in out.groupby("channel", sort=False):
            c = registry.CHANNELS[ch]
            if c.scale_kind == "interval":
                d = float(g.abs_diff.median())
                gross = 0.1 * (c.hi - c.lo) if c.hi is not None and c.lo is not None else float("inf")
                verdict = ("confirms" if d <= c.agree_tol
                           else "UNIT LOOKS WRONG" if d > gross else "instruments differ")
                stat = f"median |diff| {d:.3f} {c.unit}"
            else:
                med = float(g.ratio.median())
                verdict = ("confirms" if abs(med - 1.0) <= 0.02
                           else f"SCALE MISMATCH ~{med:.4g}x" if np.isfinite(med) else "no usable ratio")
                stat = f"median ratio {med:.4f}"
            verdicts.append(dict(section="units", source="", key=ch, kind=verdict,
                                 detail=f"{stat} over {len(g)} pair(s), {int(g.n.sum()):,} matched",
                                 acknowledged=False))
            print(f"    {ch:20s} {stat:>26s}  {verdict}")
    for ch, src, status, note in registry.units_report():
        covered = len(out) and ((out.channel == ch) & (
            out.uid_a.str.startswith(src) | out.uid_b.str.startswith(src))).any()
        if not covered:
            verdicts.append(dict(section="units", source=src, key=ch, kind=f"still_{status}",
                                 detail=note or "no co-located evidence", acknowledged=False))
    return pd.DataFrame(verdicts)


# ---- snapshot drift -------------------------------------------------------------------------------

DRIFT_CLASSES = ("added", "extended", "revised_in_window", "revised_out_of_window",
                 "removed", "schema_changed", "adoption_amnesty")
CONFLICTS = ("revised_out_of_window", "removed", "schema_changed")


def _classify_change(old: pd.Series, new: pd.Series, role: str, as_of: pd.Timestamp) -> str:
    """One changed file's drift class, from the manifest's content probes.

    The evidence, in order of authority: a changed parquet schema is `schema_changed`; a changed `raw/` file is a conflict outright (the build never writes raw; vendor drops arrive via adoption); a grown file whose newest data timestamp advanced is `extended` (the append classes: new observations, a new partition row); otherwise the revision-window test runs on the OLD file's newest data timestamp -- a trailing-window refresh only rewrites data that was recent when the parent snapshot was cut, so a change to a file whose data entirely predates the window means approved data moved. A file with no probes (non-parquet, no statistics) falls back to size: grown is `extended`, anything else is a conflict -- the honest default when there is no evidence a change was routine.
    """
    if pd.notna(old.get("schema_fp")) and pd.notna(new.get("schema_fp")) \
            and old["schema_fp"] != new["schema_fp"]:
        return "schema_changed"
    if role == "raw":
        return "revised_out_of_window"
    grew = int(new["size"]) > int(old["size"])
    o_ts = pd.Timestamp(old["data_max_ts"]) if pd.notna(old.get("data_max_ts")) else None
    n_ts = pd.Timestamp(new["data_max_ts"]) if pd.notna(new.get("data_max_ts")) else None
    if o_ts is not None and n_ts is not None:
        if grew and n_ts > o_ts:
            return "extended"
        window = as_of - pd.Timedelta(days=records.REVISION_DAYS)
        return "revised_in_window" if o_ts.tz_localize(None) >= window.tz_localize(None) \
            else "revised_out_of_window"
    return "extended" if grew else "revised_out_of_window"


def diff_snapshots(parent_id: str, child_id: str) -> pd.DataFrame:
    """Classify every manifest change between two snapshots. Conflicts queue to the drift ledger.

    Change DETECTION uses every probe the manifests share -- size, sha256 where both carry one, schema fingerprint, data timestamp -- and never mtime alone, which the store's own atomic-rename writes refresh without any content change. Amnesty reads the CHILD's notes: the snapshot that INTRODUCES a wholesale replacement is the one that declares it.
    """
    a = snapshot.load(parent_id).set_index("relpath")
    b = snapshot.load(child_id).set_index("relpath")
    amnesty = str(snapshot.header(child_id).get("notes", "")).startswith("adoption")
    as_of = pd.Timestamp(snapshot.header(child_id).get("created_at", pd.Timestamp.now().isoformat()))

    rows = []
    for rel in b.index.difference(a.index):
        rows.append(dict(relpath=rel, drift="added"))
    for rel in a.index.difference(b.index):
        rows.append(dict(relpath=rel, drift="adoption_amnesty" if amnesty else "removed"))
    both = a.index.intersection(b.index)
    ra, rb = a.loc[both], b.loc[both]

    def _col(frame, name):
        return frame[name] if name in frame.columns else pd.Series(None, index=frame.index)

    diff_size = ra["size"].values != rb["size"].values
    sha_a, sha_b = _col(ra, "sha256"), _col(rb, "sha256")
    diff_sha = (sha_a.notna().values & sha_b.notna().values & (sha_a.values != sha_b.values))
    fp_a, fp_b = _col(ra, "schema_fp"), _col(rb, "schema_fp")
    diff_fp = (fp_a.notna().values & fp_b.notna().values & (fp_a.values != fp_b.values))
    ts_a, ts_b = _col(ra, "data_max_ts"), _col(rb, "data_max_ts")
    diff_ts = (ts_a.notna().values & ts_b.notna().values & (ts_a.values != ts_b.values))
    changed = both[diff_size | diff_sha | diff_fp | diff_ts]

    for rel in changed:
        if amnesty:
            rows.append(dict(relpath=rel, drift="adoption_amnesty"))
            continue
        rows.append(dict(relpath=rel,
                         drift=_classify_change(a.loc[rel], b.loc[rel],
                                                role=str(b.loc[rel, "role"]), as_of=as_of)))
    out = pd.DataFrame(rows, columns=["relpath", "drift"])
    if len(out):
        out["snapshot_pair"] = f"{parent_id}..{child_id}"
    return out


def gate_drift(diff: pd.DataFrame) -> None:
    """Queue the conflicting classes to the drift ledger and gate on the unanswered ones."""
    conf = diff[diff.drift.isin(CONFLICTS)] if len(diff) else diff
    if not len(conf):
        return
    q = pd.DataFrame({
        "source": [r.relpath.split("/")[1] if "/" in r.relpath else r.relpath for r in conf.itertuples()],
        "snapshot_pair": conf.snapshot_pair.values,
        "diff_id": conf.relpath.values,
        "class": conf.drift.values,
    })
    ledger.DRIFT.sync(q)
    ledger.DRIFT.gate(q)


# ---- the stage ------------------------------------------------------------------------------------

def main(force: bool = False, snapshot: str | None = None) -> pd.DataFrame:
    """`snapshot` pins the HEAD-side id (the runner's --snapshot flag); default is the store's HEAD. The parameter shares its name with the snapshot MODULE, which is why the module is re-imported locally below -- the shadow already cost one crash."""
    config.ensure_dirs()
    sections = [reconcile_sources()]
    if config.SENSORS_PATH.exists():
        sensors = pd.read_parquet(config.SENSORS_PATH)
        sections.append(units_verdicts(sensors))
    else:
        print("  no sensors table yet -- units verification skipped")

    from . import snapshot as snap_store

    head = snapshot or snap_store.head()
    parent = snap_store.header(head).get("parent") if head else None
    if parent:
        d = diff_snapshots(parent, head)
        print(f"  drift {parent}..{head}: {d.drift.value_counts().to_dict() if len(d) else 'none'}")
        if len(d):
            sections.append(pd.DataFrame(dict(section="drift", source="", key=d.relpath,
                                              kind=d.drift, detail=d.snapshot_pair,
                                              acknowledged=False)))
        gate_drift(d)
    else:
        print("  first snapshot -- no parent to diff against")

    report = pd.concat([s for s in sections if len(s)], ignore_index=True) if any(
        len(s) for s in sections) else pd.DataFrame(
        columns=["section", "source", "key", "kind", "detail", "acknowledged"])
    bio.write_parquet(report, REPORT_PATH)
    unack = report[(report.section == "reconcile") & ~report.acknowledged]
    if len(unack):
        rows = pd.DataFrame({"source": unack.source.values, "uid": unack.key.values,
                             "kind": unack.kind.values, "detail": unack.detail.values})
        ledger.REGISTRY_GAPS.sync(rows.drop_duplicates(subset=["source", "uid"]))
        ledger.REGISTRY_GAPS.gate(rows)
    print(f"  verification report: {len(report)} row(s) -> {REPORT_PATH.name}")
    return report


# ---- verify checks --------------------------------------------------------------------------------

@verify.check("a4", "verification report exists and names every unacknowledged gap")
def _check_report():
    if not REPORT_PATH.exists():
        return None, "a4 has not run"
    r = pd.read_parquet(REPORT_PATH)
    gaps = r[(r.section == "reconcile") & ~r.acknowledged]
    return True, f"{len(r)} row(s); {len(gaps)} unacknowledged gap(s)"
