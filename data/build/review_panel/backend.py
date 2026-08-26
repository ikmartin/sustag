"""The review panel's data layer: ledgers in, decisions out, evidence assembled on demand.

THE CSVs STAY THE AUTHORITY. This module reads the decision ledgers and the build's evidence artifacts and WRITES exactly one thing: single-row decisions through `Ledger.decide` -- atomic, mtime-guarded, validated before a byte lands. The panel is a view; a broken panel leaves the sheets answerable with a text editor, and nothing the panel renders is stored anywhere the build reads.

VALIDATION HAPPENS HERE, NOT IN THE CALLBACK. `submit_decision` refuses an unknown token, a malformed span, a non-numeric ceiling -- the same strict parsers the build gates on (`quality.parse_spans`, `parse_threshold`) run BEFORE the write, so a malformed cell can never reach a sheet through this path.
"""

from __future__ import annotations

import pandas as pd

from .. import config, contracts, ids, ledger, registry
from ..derive import identity
from ..publish import quality

# Every ledger the panel serves, in tab order. site_type's vocabulary is injected at runtime.
def ledgers() -> dict[str, ledger.Ledger]:
    from ..derive import site_type

    out = {}
    for led in ledger.ALL:
        out[led.name] = site_type.type_ledger() if led.name == "site_type" else led
    return out


def excluded_uids() -> set[str]:
    """uids currently decided `exclude` on the exclusions sheet."""
    if not ledger.EXCLUSIONS.path.exists():
        return set()
    d = ledger.EXCLUSIONS.read_all()
    return set(d.uid[d.decision.astype(str).str.strip() == "exclude"].astype(str)) if len(d) else set()


def sheet(name: str) -> pd.DataFrame:
    """The whole sheet, decided or not, as strings -- plus `_pending` (no CURRENT decision) and `_stale` (decided under a superseded criterion). Every sheet except exclusions itself HIDES rows referencing an excluded sensor: the exclusion verdict answers those rows' questions the moment it lands, without waiting for the next build pass to retire them."""
    led = ledgers()[name]
    d = led.read_all()
    if not len(d):
        return d.assign(_pending=pd.Series(dtype=bool), _stale=pd.Series(dtype=bool))
    if name != "exclusions":
        exc = excluded_uids()
        if exc:
            keep = pd.Series(True, index=d.index)
            for c in ("uid", "site_uid", "uid_a", "uid_b"):
                if c in d.columns:
                    keep &= ~d[c].astype(str).isin(exc)
            if "members" in d.columns:
                keep &= pd.Series([not (set(str(m).split("+")) & exc) for m in d.members],
                                  index=d.index)
            d = d[keep].reset_index(drop=True)
            if not len(d):
                return d.assign(_pending=pd.Series(dtype=bool), _stale=pd.Series(dtype=bool))
    dec = d.get("decision", pd.Series("", index=d.index)).astype(str).str.strip()
    ver = d.get(ledger.CRITERION_COL, pd.Series("", index=d.index)).astype(str).str.strip()
    stale = (dec != "") & bool(led.criterion) & (ver != led.criterion)
    d = d.copy()
    d["_pending"] = (dec == "") | stale
    d["_stale"] = stale
    return d


def queue_counts() -> dict[str, tuple[int, int]]:
    """name -> (pending, total) per ledger, for the tab badges."""
    out = {}
    for name in ledgers():
        d = sheet(name)
        out[name] = (int(d["_pending"].sum()) if len(d) else 0, len(d))
    return out


def submit_decision(name: str, key: tuple, decision: str, decided_by: str = "",
                    note: str = "", **extras) -> str:
    """Validate and write one verdict. Returns a status line; raises ValueError with the reason on refusal."""
    led = ledgers()[name]
    decision = str(decision or "").strip()
    if decision and decision not in led.vocabulary:
        raise ValueError(f"{decision!r} is not one of {sorted(led.vocabulary)}")
    if name == "quality":
        spans = str(extras.get("exclude_spans", "") or "").strip()
        if spans:
            quality.parse_spans(spans)              # raises with the offending span named
        quality.parse_threshold(extras.get("max_threshold", ""))
    try:
        led.decide(key, decision, decided_by=decided_by, note=note, **extras)
    except ledger.ConcurrentEditError:
        raise ValueError("the sheet changed on disk (a build sync?) -- reload the tab and retry")
    what = f"'{decision}'" if decision else "cleared (back to not-reviewed)"
    return f"{name} {' : '.join(map(str, key))} -> {what}"


# ---- evidence ---------------------------------------------------------------------------------------

SIGNAL_HELP = {
    "range_violation": "more than 0.5% of the RAW samples sit outside the physical range or match a fill value -- measured before the scrub, which would otherwise hide them as gaps",
    "dead_autocorr": "lag-1 autocorrelation under 0.9 at native resolution: adjacent 15-minute samples of real water are nearly identical, so this low means noise, not signal",
    "extreme_noise": "sample-to-sample roughness (std of first differences over std of the series) above 0.25",
    "stuck_impossible": "a run of out-of-range or fill values persisting more than a day -- a broken instrument, not a glitch",
    "noisy": "roughness beyond 3.5 robust-z of the channel cohort",
    "scale_outlier": "mean or spread beyond 3.5 robust-z of the cohort -- possibly a unit or calibration problem",
    "flatline_heavy": "fraction of samples in runs of 20+ identical values, far beyond the cohort",
    "seg_bad": "the worst ~3-week window is extreme even where the whole series looks fine",
    "aseasonal": "under 5% of daily variance explained by an annual cycle on a 2+ year record -- nitrate is seasonal; a flat annual profile is suspect",
}

EXTRA_HELP = {
    "exclude_spans": "inclusive day windows to excise, e.g. 2021-03-01..2021-07-15; 2022-01-02..2022-01-09 -- copy the shaded proposal dates you agree with",
    "max_threshold": "a number in the channel's canonical units: every published day with any reading above it is withheld, for THIS sensor alone",
}


_MERGE_CLASSES = {"complementary", "sequential", "dual_registration"}


def merge_conflicts() -> set[tuple]:
    """Keys of merge-sheet rows forming NON-TRANSITIVE structures: a pair decided (or proposed) distinct whose two sensors are nevertheless connected through merge edges, plus the merge edges of every component containing such a contradiction. Effective verdict per row: the human decision when present, else the ladder's proposed class -- so a looming conflict surfaces before every row is confirmed."""
    d = sheet("merge")
    if not len(d):
        return set()
    dec = d.get("decision", "").astype(str).str.strip()
    prop = d.get("proposed", "").astype(str).str.strip()
    eff = dec.where(dec != "", prop.map(lambda p: "merge" if p in _MERGE_CLASSES else "distinct"))
    parent: dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    merged = d[eff == "merge"]
    for a, b in zip(merged.uid_a, merged.uid_b):
        parent[find(a)] = find(b)
    distinct = d[eff == "distinct"]
    bad_roots = {find(a) for a, b in zip(distinct.uid_a, distinct.uid_b)
                 if find(a) == find(b)}
    if not bad_roots:
        return set()
    out = {(a, b) for a, b in zip(distinct.uid_a, distinct.uid_b)
           if find(a) == find(b) and find(a) in bad_roots}
    out |= {(a, b) for a, b in zip(merged.uid_a, merged.uid_b) if find(a) in bad_roots}
    return out


def reach_conflicts() -> set[tuple]:
    """Keys of pairs decided `merge` whose two sides snapped to DIFFERENT reaches -- the assembly-time reach-disagreement halt, visible as rows: a merged record must live on one reach, so one of the verdict and the geometry is wrong."""
    d = sheet("merge")
    if not len(d):
        return set()
    dec = d.decision.astype(str).str.strip()
    ca, cb = d.comid_a.astype(str), d.comid_b.astype(str)
    bad = (dec == "merge") & (ca != cb) & (ca != "") & (cb != "") & (ca != "nan") & (cb != "nan")
    return set(zip(d.uid_a[bad], d.uid_b[bad]))


def cluster_keys(min_members: int = 3) -> set[tuple]:
    """Keys of merge-sheet rows whose pair belongs to a constellation of >= min_members sensors -- component over ALL sheet pair rows, the same grouping the family pane draws."""
    d = sheet("merge")
    if not len(d):
        return set()
    parent: dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in zip(d.uid_a, d.uid_b):
        parent[find(a)] = find(b)
    size: dict[str, int] = {}
    for u in set(d.uid_a) | set(d.uid_b):
        r = find(u)
        size[r] = size.get(r, 0) + 1
    return {(a, b) for a, b in zip(d.uid_a, d.uid_b) if size[find(a)] >= min_members}


def conflict_family(uid_a: str, uid_b: str) -> dict | None:
    """The whole constellation a pair belongs to, for the family pane: members (component over ALL sheet pair rows), every edge with its verdict, the partition the current merges imply, and the distinct rulings that partition violates -- the d4 contradiction halt, computed live. None for plain pairs (component under 3 members)."""
    d = sheet("merge")
    if not len(d):
        return None
    parent: dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in zip(d.uid_a, d.uid_b):
        parent[find(a)] = find(b)
    root = find(uid_a)
    members = sorted(u for u in set(d.uid_a) | set(d.uid_b) if find(u) == root)
    if len(members) < 3:
        return None
    rows = d[[find(a) == root for a in d.uid_a]]
    dec = rows.decision.astype(str).str.strip()
    edges = [dict(a=a, b=b, verdict=(v if v in ("merge", "distinct") else "undecided"),
                  decided_by=w, sep_m=s)
             for a, b, v, w, s in zip(rows.uid_a, rows.uid_b, dec,
                                      rows.decided_by.astype(str), rows.sep_m)]
    p2: dict[str, str] = {}

    def find2(x):
        while p2.setdefault(x, x) != x:
            p2[x] = p2[p2[x]]
            x = p2[x]
        return x

    for e in edges:
        if e["verdict"] == "merge":
            p2[find2(e["a"])] = find2(e["b"])
    parts: dict[str, list] = {}
    for m in members:
        parts.setdefault(find2(m), []).append(m)
    violations = [(e["a"], e["b"]) for e in edges
                  if e["verdict"] == "distinct" and find2(e["a"]) == find2(e["b"])]
    s = sensor_rows(members)
    facts = {}
    for m in members:
        r = s.loc[m] if m in s.index else None
        facts[m] = dict(lat=(None if r is None else r.lat), lon=(None if r is None else r.lon),
                        type=(None if r is None else str(r.site_type)),
                        name=(None if r is None else str(r.get("station_name", ""))),
                        span=record_span(m))
    return dict(members=members, edges=edges, parts=sorted(parts.values(), key=len, reverse=True),
                violations=violations, facts=facts)


def record_span(uid: str) -> tuple[str, str] | tuple[None, None]:
    """(first, last) observation date across ALL channels of the canonical record, the first capped at config.collection_start() and the last at collection_end() if the config ever declares one; (None, None) when nothing is on disk."""
    p = config.WATER_CANONICAL / f"{contracts.uid_to_filename(uid)}.parquet"
    if not p.exists():
        return None, None
    d = pd.read_parquet(p, columns=["date"])
    if not len(d):
        return None, None
    lo, hi = pd.Timestamp(d.date.min()), pd.Timestamp(d.date.max())
    lo = max(lo, pd.Timestamp(config.collection_start()))
    end = getattr(config, "collection_end", None)
    if end is not None and end() is not None:
        hi = min(hi, pd.Timestamp(end()))
    return str(lo.date()), str(hi.date())


def daily_record(uid: str, channel: str = "nitrate") -> pd.Series | None:
    """One sensor's canonical daily series. None when nothing is on disk."""
    p = config.WATER_CANONICAL / f"{contracts.uid_to_filename(uid)}.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    col = f"{channel}_mean" if f"{channel}_mean" in d.columns else None
    if col is None or "date" not in d.columns:
        return None
    s = pd.Series(pd.to_numeric(d[col], errors="coerce").to_numpy(),
                  index=pd.DatetimeIndex(pd.to_datetime(d.date))).dropna().sort_index()
    return s if len(s) else None


def merge_counterfactual(members: list[str]) -> pd.DataFrame:
    """The assembled winner-per-span record these members would produce -- the thing a merge decision actually builds."""
    from ..derive import assemble

    days = pd.Series({u: (0 if (s := daily_record(u)) is None else len(s)) for u in members})
    return assemble.merge_record(members, days)


def mask_preview(code: str, channel: str, exclude_spans: str, max_threshold) -> pd.DataFrame | None:
    """The record as the UNCOMMITTED form values would publish it -- masked beside raw, before any write.

    Runs `publish.apply_quality_masks` itself, so the preview cannot disagree with publication: it is the same function on the same record, and what it returns is what submitting would store.
    """
    from ..publish import publish

    p = config.WATER_MERGED / f"{code}.parquet"
    if not p.exists():
        return None
    rec = pd.read_parquet(p)
    V = pd.DataFrame([dict(code=code, channel=channel, verdict="keep",
                           exclude_spans=str(exclude_spans or ""),
                           max_threshold=quality.parse_threshold(max_threshold))])
    return publish.apply_quality_masks({code: rec}, V)[code]


def mask_explain(code: str, channel: str, exclude_spans: str, max_threshold) -> dict:
    """Why each withheld day was withheld, in counts -- the ceiling's whole-day rule is invisible on a plot of daily MEANS.

    A ceiling withholds the WHOLE DAY when ANY of the channel's statistics exceeds it (see `publish._over_threshold`: the day's mean was computed from the offending samples too). On a mean-valued plot that reads as "a value below the threshold was removed", which is the single most confusing thing the panel does. This returns the counts that explain it, including how many withheld days have a mean UNDER the ceiling and which statistic tripped them.
    """
    from ..publish import publish

    p = config.WATER_MERGED / f"{code}.parquet"
    if not p.exists():
        return {}
    rec = pd.read_parquet(p)
    out = {"days_total": len(rec)}
    spans_text = str(exclude_spans or "").strip()
    if spans_text:
        hit = pd.Series(quality.in_spans(rec.date, quality.parse_spans(spans_text)), index=rec.index)
        out["days_in_spans"] = int(hit.sum())
    thr = quality.parse_threshold(max_threshold)
    if pd.notna(thr) and thr is not None:
        over = publish._over_threshold(rec, channel, float(thr))
        out["threshold"] = float(thr)
        out["days_over"] = int(over.sum())
        mean_col = f"{channel}_mean"
        if mean_col in rec.columns:
            m = pd.to_numeric(rec[mean_col], errors="coerce")
            out["days_over_whose_mean_is_under"] = int((over & (m <= float(thr))).sum())
        trips = {}
        for c in rec.columns:
            if not c.startswith(f"{channel}_") or c.endswith(("_src", "_n_obs")):
                continue
            x = pd.to_numeric(rec[c], errors="coerce")
            n = int((x > float(thr)).fillna(False).sum())
            if n:
                trips[c] = n
        out["tripped_by"] = trips
    return out


def bulk_decide(name: str, decision: str, decided_by: str = "") -> str:
    """Set (or clear) every row's verdict on one sheet. The panel guards this behind a DANGER section; the validation is the ledger's."""
    led = ledgers()[name]
    n = led.decide_all(decision, decided_by=decided_by,
                       note="bulk decision" if decision else "")
    what = f"set to '{decision}'" if decision else "cleared to not-reviewed"
    return f"{name}: {n} row(s) {what}"


def open_exclusion(uid: str) -> str:
    """Open a blank exclusions row for a REAL uid -- validated against the sensors table so a typo cannot mint a phantom question."""
    s = pd.read_parquet(config.SENSORS_PATH, columns=["site_uid"])
    if uid not in set(s.site_uid):
        raise ValueError(f"{uid!r} is not a registered sensor uid")
    ledger.EXCLUSIONS.add_row((uid,))
    return f"exclusions: opened a row for {uid} -- pick exclude/keep and submit"


def sensor_rows(uids: list[str]) -> pd.DataFrame:
    """The sensors-table rows for a set of uids -- the map and evidence panes read these."""
    s = pd.read_parquet(config.SENSORS_PATH)
    return s[s.site_uid.isin(set(uids))].set_index("site_uid", drop=False)


def proximity_neighbors(uids: list[str]) -> pd.DataFrame:
    """Every proximity pair touching these uids -- the neighborhood the map draws."""
    if not config.PROXIMITY_PATH.exists():
        return pd.DataFrame()
    p = pd.read_parquet(config.PROXIMITY_PATH)
    hit = p.uid_a.isin(set(uids)) | p.uid_b.isin(set(uids))
    return p[hit].reset_index(drop=True)


def native_series(members: list[str], channel: str) -> pd.DataFrame:
    return quality.native_series(members, channel)
