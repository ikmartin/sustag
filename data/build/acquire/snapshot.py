"""Snapshots: manifests over the raw and acquired stores, and the adoption pass.

A SNAPSHOT NAMES A STATE, NEVER A COPY. `snap-YYYYMMDD[a]` is a manifest -- one row per file under the raw and acquired roots -- plus a header; the files themselves stay in place. Immutability is a convention `verify` enforces, not an OS guarantee: a manifested file changes only through the declared mutation classes (trailing-revision re-pulls, partition appends), and every A3 pass ends by cutting a new snapshot and diffing it against its parent (the diff lives in `reconcile`).

RELPATHS ARE ROLE-PREFIXED (`raw/...`, `acquired/...`) AND ROOT-INDEPENDENT: computed against each configured store root, so the manifest survives the stores physically moving -- the final migration relocates the archives and flips two config constants without invalidating a single snapshot.

MANIFESTS CARRY CONTENT PROBES so the drift classifier can be honest without retained copies: sha256 for files at or under `HASH_MAX_BYTES` (larger files carry size+mtime only -- content-addressing a 24 GB zip every cut protects a file that changes through declared channels anyway), and for parquet files a `schema_fp` (hash of the column names, the schema_changed detector) plus `data_max_ts` (the newest timestamp in the file, from row-group statistics in the footer -- no row is read). A revision to a file whose data predates the revision window is thereby distinguishable from a trailing-window refresh, which no mtime can tell apart.

THE ADOPTION PASS cuts the first snapshot over archives this build did not fetch: the raw and acquired stores inherited from the predecessor pipeline, adopted in place. Adopted files have unknown fetch provenance, which is why a snapshot whose notes begin `adoption:` amnesties its entire diff in `reconcile` -- and the same convention serves any wholesale operator-supplied replacement later.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pandas as pd

from .. import config, verify
from .. import io as bio

HASH_MAX_BYTES = 100 * 1024 * 1024

MANIFEST_COLS = ["relpath", "role", "source", "size", "mtime_ns", "sha256",
                 "schema_fp", "data_max_ts", "fetched_at"]


def head() -> str | None:
    p = config.SNAPSHOTS / "HEAD"
    return p.read_text().strip() if p.exists() else None


def _set_head(snap_id: str) -> None:
    config.SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    (config.SNAPSHOTS / "HEAD").write_text(snap_id + "\n")


def load(snap_id: str | None = None) -> pd.DataFrame:
    """A snapshot's manifest. Default: HEAD."""
    snap_id = snap_id or head()
    if snap_id is None:
        raise FileNotFoundError("no snapshot exists yet -- run the adoption pass (snapshot.adopt)")
    return pd.read_parquet(config.SNAPSHOTS / snap_id / "manifest.parquet")


def header(snap_id: str | None = None) -> dict:
    snap_id = snap_id or head()
    return json.loads((config.SNAPSHOTS / snap_id / "header.json").read_text())


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parquet_probe(p: Path) -> tuple[str | None, str | None]:
    """(schema_fp, data_max_ts) from a parquet footer alone; (None, None) for anything unreadable.

    `data_max_ts` is the max over every timestamp-typed column's row-group statistics -- for a native water file that is the newest observation; for a weather quarter, the newest day. Files written without statistics probe as None, which the classifier treats as "no evidence", never as a change.
    """
    import pyarrow.parquet as pq

    try:
        pf = pq.ParquetFile(p)
    except Exception:
        return None, None
    names = pf.schema_arrow.names
    fp = hashlib.sha256(",".join(sorted(names)).encode()).hexdigest()[:12]
    # DATE COLUMNS COUNT AS TEMPORAL EVIDENCE. Matching only "timestamp" left every weather quarter --
    # whose `date` is `date32[day]` -- reporting `data_max_ts = NaN`, so `_classify_change` had nothing but
    # a size delta to go on and called a 56% row loss `revised_out_of_window`, the same class as a routine
    # trailing refresh. That is how July 2026 left the store with a reviewer's signature on it.
    ts_cols = [i for i, f in enumerate(pf.schema_arrow)
               if "timestamp" in str(f.type) or str(f.type).startswith("date")]
    best = None
    try:
        meta = pf.metadata
        for rg in range(meta.num_row_groups):
            for ci in ts_cols:
                st = meta.row_group(rg).column(ci).statistics
                if st is not None and st.has_min_max and st.max is not None:
                    v = pd.Timestamp(st.max)
                    best = v if best is None or v > best else best
    except Exception:
        pass
    return fp, best.isoformat() if best is not None else None


def manifest_rows(root: Path, role: str) -> list[dict]:
    """One row per file under `root`, relpaths prefixed with the role. Hidden files skipped; .tmp REFUSED -- a .tmp in a snapshot store is an interrupted write nobody cleaned up."""
    rows = []
    if not root.exists():
        return rows
    stray = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name.endswith(".tmp"):
            stray.append(p)
            continue
        if p.name.startswith(".") or "__pycache__" in p.parts:
            continue
        # Fetch BOOKKEEPING is not fetched data: the accounting store rewrites on every pass by design,
        # and manifesting it would hand the drift classifier a guaranteed conflict per cut.
        if p.name.startswith("fetch_status"):
            continue
        st = p.stat()
        rel = p.relative_to(root)
        schema_fp, data_max_ts = _parquet_probe(p) if p.suffix == ".parquet" else (None, None)
        rows.append(dict(
            relpath=f"{role}/{rel}",
            role=role,
            source=rel.parts[0] if len(rel.parts) > 1 else "",
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            sha256=_sha256(p) if st.st_size <= HASH_MAX_BYTES else None,
            schema_fp=schema_fp,
            data_max_ts=data_max_ts,
            fetched_at=dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        ))
    if stray:
        raise RuntimeError(f"{len(stray)} .tmp file(s) under {root} -- interrupted writes; "
                           f"remove or finish them before cutting a snapshot: {stray[:3]}")
    return rows


def next_id(date: dt.date | None = None, suffix: str = "") -> str:
    d = (date or dt.date.today()).strftime("%Y%m%d")
    base = f"snap-{d}{suffix}"
    if not (config.SNAPSHOTS / base).exists():
        return base
    for letter in "abcdefghijklmnopqrstuvwxyz":
        cand = f"snap-{d}{suffix}{letter}"
        if not (config.SNAPSHOTS / cand).exists():
            return cand
    raise RuntimeError(f"more than 26 snapshots on {d}")


def cut(snap_id: str | None = None, notes: str = "", parent: str | None = "HEAD") -> str:
    """Manifest the raw + acquired roots, write the snapshot, advance HEAD. Returns the id."""
    snap_id = snap_id or next_id()
    parent_id = head() if parent == "HEAD" else parent
    rows = manifest_rows(config.RAW, "raw") + manifest_rows(config.ACQUIRED, "acquired")
    if not rows:
        raise RuntimeError("nothing to manifest -- both stores are empty")
    m = pd.DataFrame(rows, columns=MANIFEST_COLS)
    outdir = config.SNAPSHOTS / snap_id
    outdir.mkdir(parents=True, exist_ok=True)
    bio.write_parquet(m, outdir / "manifest.parquet")
    hdr = dict(
        id=snap_id,
        created_at=dt.datetime.now().isoformat(timespec="seconds"),
        parent=parent_id,
        n_files=len(m),
        n_raw=int((m.role == "raw").sum()),
        n_acquired=int((m.role == "acquired").sum()),
        bytes_total=int(m["size"].sum()),
        by_source={k: int(v) for k, v in m.groupby("source").size().items()},
        notes=notes,
    )
    tmp = outdir / "header.json.tmp"
    tmp.write_text(json.dumps(hdr, indent=1))
    tmp.replace(outdir / "header.json")
    _set_head(snap_id)
    print(f"  cut {snap_id}: {hdr['n_files']:,} files "
          f"({hdr['n_raw']:,} raw, {hdr['n_acquired']:,} acquired), "
          f"{hdr['bytes_total'] / 1e9:.1f} GB{f', parent {parent_id}' if parent_id else ''}")
    return snap_id


def adopt() -> str:
    """Cut the adoption snapshot over the inherited archives. Refuses when a HEAD already exists -- adoption is the store's FIRST state, and a wholesale later replacement is an ordinary `adoption:`-noted cut with a parent."""
    if head():
        raise SystemExit(f"snapshot store already initialised (HEAD={head()}); "
                         f"a later wholesale replacement is cut(notes='adoption: ...'), not adopt()")
    config.ensure_dirs()
    return cut(next_id(suffix="-adopt"),
               notes="adoption: this build adopts the inherited raw + acquired archives in place; "
                     "fetch provenance unknown -- the first diff against this snapshot is amnestied",
               parent=None)


def main(force: bool = False, snapshot: str | None = None) -> None:
    if head() and not force:
        print(f"  snapshot store already initialised (HEAD={head()}); nothing to do")
        return
    adopt()


# ---- verify checks --------------------------------------------------------------------------------

@verify.check("snapshot", "HEAD points at a readable manifest")
def _check_head():
    h = head()
    if h is None:
        return None, "no snapshot yet"
    m = load(h)
    return len(m) > 0, f"{h}: {len(m):,} files manifested"


@verify.check("snapshot", "adopted stores present with expected populations")
def _check_populations():
    if head() is None:
        return None, "no snapshot yet"
    m = load()
    natives = int(m.relpath.str.startswith("acquired/water/native/").sum())
    quarters = int(m.relpath.str.match(r"acquired/weather/daily/gridmet_\d{4}Q\d\.parquet").sum())
    ok = natives >= 6000 and quarters >= 70
    return ok, f"{natives:,} native water files (audit: 7,302), {quarters} weather quarters (audit: 76)"


@verify.check("snapshot", "no interrupted writes in the stores")
def _check_no_tmp():
    stray = [p for root in (config.RAW, config.ACQUIRED) if root.exists()
             for p in root.rglob("*.tmp")]
    return not stray, f"{len(stray)} .tmp file(s)" if stray else "clean"


@verify.check("snapshot", "every acquired file is manifested in HEAD", deep=True)
def _check_coverage():
    if head() is None:
        return None, "no snapshot yet"
    m = load()
    manifested = set(m[m.role == "acquired"].relpath)
    # the same skip rules as manifest_rows, fetch_status included -- the two lists must agree on what
    # "every file" means, or a deliberate manifest exclusion reads as a coverage hole forever
    on_disk = {f"acquired/{p.relative_to(config.ACQUIRED)}" for p in config.ACQUIRED.rglob("*")
               if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".tmp")
               and "__pycache__" not in p.parts and not p.name.startswith("fetch_status")}
    extra = on_disk - manifested
    return not extra, (f"{len(extra)} unmanifested file(s), e.g. {sorted(extra)[:3]}"
                       if extra else f"{len(on_disk):,} files, all manifested")
