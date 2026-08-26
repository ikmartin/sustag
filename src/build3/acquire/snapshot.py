"""Snapshots: manifests over the raw and acquired stores, and the one-time adoption pass.

A SNAPSHOT NAMES A STATE, NEVER A COPY. `snap-YYYYMMDD[a]` is a manifest -- one row per file under `raw/` and `acquired/` -- plus a header; the files themselves are adopted in place. Immutability is a convention `verify` enforces, not an OS guarantee: a manifested file changes only through the two declared classes (trailing-revision re-pulls, partition appends), and every acquisition run ends by cutting a new snapshot and diffing it against its parent (the diff lives in `reconcile`).

HASHES ARE LAZY FOR GIANTS. Files at or under `HASH_MAX_BYTES` get a sha256 at manifest time; larger ones (the 24 GB CDL zip, the 1.25 GB flowline layer, quarter-sized weather files) carry size+mtime only and are hashed on first drift suspicion. Content-addressing everything would spend minutes per cut to protect files that change through declared channels anyway.

THE ADOPTION PASS runs once: it MOVES build2's acquisition-derived stores from `interim2/` into `acquired/` (same-filesystem rename -- instant, no copy; approved), walks both roots, and cuts `snap-<date>-adopt`. Adopted files have unknown fetch provenance, which is why the first post-adoption diff carries an amnesty class in `reconcile`. Frozen build2 keeps its code and its DERIVED outputs (canonical, basins, comid_features, processed2) as cross-validation targets; only its ability to re-run acquisition dies, which is what frozen means.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd

from .. import config, verify
from .. import io as bio

HASH_MAX_BYTES = 100 * 1024 * 1024

# The approved one-time move: build2 acquisition stores -> the acquired root. Order is (source, dest).
# `comid_attrs` is renamed `attributes` to match the store layout; everything else keeps its name.
_ADOPTION_MOVES: tuple[tuple[str, str], ...] = (
    ("interim2/water/native", "acquired/water/native"),
    ("interim2/water/iwqis", "acquired/water/iwqis"),
    ("interim2/network", "acquired/network"),
    ("interim2/weather", "acquired/weather"),
    ("interim2/comid_attrs", "acquired/attributes"),
    ("interim2/basins_nldi", "acquired/seed_basins"),
)


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
        raise FileNotFoundError("no snapshot exists yet -- run the adoption pass")
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


def manifest_rows(root: Path, role: str) -> list[dict]:
    """One row per file under `root`, relpaths from `src/data/`. Hidden files and .tmp are refused, not skipped -- a .tmp in a snapshot store is an interrupted write nobody cleaned up."""
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
        st = p.stat()
        rel = p.relative_to(config.DATA)
        rows.append(dict(
            relpath=str(rel),
            role=role,
            source=rel.parts[1] if len(rel.parts) > 1 else "",
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            sha256=_sha256(p) if st.st_size <= HASH_MAX_BYTES else None,
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
    """Manifest raw/ + acquired/, write the snapshot, advance HEAD. Returns the id."""
    snap_id = snap_id or next_id()
    parent_id = head() if parent == "HEAD" else parent
    rows = manifest_rows(config.RAW, "raw") + manifest_rows(config.ACQUIRED, "acquired")
    if not rows:
        raise RuntimeError("nothing to manifest -- both stores are empty")
    m = pd.DataFrame(rows)
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


def adopt(dry_run: bool = False) -> str | None:
    """The one-time adoption: move build2 acquisition stores into `acquired/`, then cut the adopt snapshot.

    Idempotent: a move whose source is gone and whose dest exists is recorded as already done; a move where BOTH exist stops -- that is a half-adopted state a person should look at, not a case to merge silently.
    """
    done, todo, conflict = [], [], []
    for src_rel, dst_rel in _ADOPTION_MOVES:
        src, dst = config.DATA / src_rel, config.DATA / dst_rel
        if src.exists() and dst.exists() and any(dst.iterdir()):
            conflict.append((src_rel, dst_rel))
        elif src.exists():
            todo.append((src, dst))
        elif dst.exists():
            done.append(dst_rel)
        # neither existing is fine: a store this machine never had
    if conflict:
        raise SystemExit(f"half-adopted state -- both source and destination exist for: {conflict}. "
                         f"Resolve by hand; nothing was moved.")
    for src, dst in todo:
        print(f"  {'would move' if dry_run else 'moving'} {src.relative_to(config.DATA)} -> "
              f"{dst.relative_to(config.DATA)}")
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
    if dry_run:
        return None
    config.ensure_dirs()
    return cut(next_id(suffix="-adopt"),
               notes="adoption of raw/ in place and build2 acquisition stores moved from interim2/; "
                     "fetch provenance unknown -- first diff carries adoption amnesty",
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
    quarters = int(m.relpath.str.match(r"acquired/weather/gridmet_\d{4}Q\d\.parquet").sum())
    ok = natives >= 6000 and quarters >= 70
    return ok, f"{natives:,} native water files (audit: 6,124), {quarters} weather quarters (audit: 76)"


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
    on_disk = {str(p.relative_to(config.DATA)) for p in config.ACQUIRED.rglob("*")
               if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".tmp")}
    extra = on_disk - manifested
    return not extra, (f"{len(extra)} unmanifested file(s), e.g. {sorted(extra)[:3]}"
                       if extra else f"{len(on_disk):,} files, all manifested")
