"""Chunk 2 — native observations become the canonical channel schema.

    python -m src.build2.run chunk2
    python -m src.build2.run chunk2 --single --force      # re-bin every site
    python -m src.build2.run chunk2 --single --sources MPCA

ONE SHAPE FOR EVERY SITE. Three sources name the same quantity three ways -- USGS by parameter code, IWQIS by an ad-hoc English name, MPCA by an integer variable id -- and downstream the nodes are homogeneous, so a published series has to carry the same columns everywhere with real nulls for what a site does not measure. That mapping is `params.CHANNELS`; the reduction is `daily.summarise`; this chunk is the pass that applies them and reports what it found.

DERIVATION, WHICH IS WHY IT IS NOT CHUNK 1. Chunk 1 fetches and does not derive -- reading a documented fill value as missing is reading the format correctly, but multiplying by 0.0283 to reach m3/s is a decision about meaning. Keeping the two apart is what lets a re-bin be free: every native is already on disk, so this pass touches no network at all.

AND WHY IT IS BEFORE IDENTITY. Chunk 3 has to recognise that two co-located sensors measuring DIFFERENT things are one physical gauge, which is a question about channel sets rather than about records. `channels_present` is the column that answers it, and it does not exist until this has run.

ENTIRELY OFFLINE AND IDEMPOTENT. A site whose canonical file is newer than its native is skipped, so an interrupted pass resumes, and `--force` re-bins regardless.
"""

from __future__ import annotations

if __name__ == "__main__" and __package__ in (None, ""):
    import pathlib as _pl
    import runpy as _rp
    import sys as _sys

    _root = _pl.Path(__file__).resolve().parents[3]
    _sys.path.insert(0, str(_root))
    _rp.run_module("src.build2.chunk2.run_chunk2", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

import argparse

import pandas as pd

from .. import config, daily, io as bio, params, schema

# What this chunk contributes to the site table.
CANON_BLOCK = [c for c in schema.COLUMNS if schema.BLOCK[c] == "canon"]


def _native_path(uid: str):
    return config.WATER_NATIVE / f"{schema.uid_to_filename(uid)}.parquet"


def _canon_path(uid: str):
    return config.WATER_CANONICAL / f"{schema.uid_to_filename(uid)}.parquet"


def stale(uid: str) -> bool:
    """True when the canonical file is missing or older than the native it derives from."""
    n, c = _native_path(uid), _canon_path(uid)
    if not n.exists():
        return False
    return not c.exists() or c.stat().st_mtime < n.stat().st_mtime


def process_site(uid: str, source: str, force: bool = False) -> tuple[dict, pd.DataFrame]:
    """Canonicalise one registration. Returns `(canon-block row, scrub tallies)`.

    A site with no native yields an empty row rather than an absent one, so `channels_present` distinguishes "nothing observed" from "not looked at" the way the contract's null semantics require.
    """
    row = {"site_uid": uid}
    n = _native_path(uid)
    if not n.exists():
        return row, pd.DataFrame()

    if not force and not stale(uid):
        frame = pd.read_parquet(_canon_path(uid))
        tal = pd.DataFrame()
    else:
        obs = pd.read_parquet(n)
        frame, tal = daily.summarise(obs, source)
        if len(frame):
            bio.write_parquet(frame.reset_index(), _canon_path(uid))
        else:
            _canon_path(uid).unlink(missing_ok=True)

    present = []
    for ch in params.NAMES:
        col = f"{ch}_mean"
        k = int(frame[col].notna().sum()) if col in frame.columns else 0
        row[f"n_days_{ch}"] = k
        if k:
            present.append(ch)
    row["channels_present"] = "+".join(present) if present else pd.NA
    if len(tal):
        tal = tal.assign(site_uid=uid)
    return row, tal


def main(force: bool = False, sources: list[str] | None = None,
         sites: pd.DataFrame | None = None) -> pd.DataFrame:
    """Canonicalise every registration with a native, and widen `registrations.parquet`."""
    config.ensure_dirs()
    reg = sites if sites is not None else pd.read_parquet(config.REGISTRATIONS_PATH)
    todo = reg if not sources else reg[reg.source.isin(sources)]

    unv = params.unverified()
    print(f"  {len(params.CHANNELS)} channels; {len(unv)} mapping(s) with unverified units", flush=True)

    rows, tallies, done, skipped = [], [], 0, 0
    for k, r in enumerate(todo.itertuples(), 1):
        if not force and not stale(r.site_uid) and _canon_path(r.site_uid).exists():
            skipped += 1
        row, tal = process_site(r.site_uid, r.source, force=force)
        rows.append(row)
        if len(tal):
            tallies.append(tal)
        if row.get("channels_present") is not pd.NA:
            done += 1
        if k % 50 == 0:
            print(f"    {k}/{len(todo)}", flush=True)

    block = pd.DataFrame(rows)
    tal = pd.concat(tallies, ignore_index=True) if tallies else pd.DataFrame()
    _report(block, tal, done, skipped)

    if sites is None:
        out = schema.conform(schema.update_block(reg, block, CANON_BLOCK))
        bad = schema.validate(out)
        if bad:
            raise ValueError("registration table violates the contract:\n  " + "\n  ".join(bad))
        bio.write_parquet(out, config.REGISTRATIONS_PATH)
        print(f"  wrote {config.REGISTRATIONS_PATH.name}: canon block on {len(block)} registration(s)")
    return block


def _report(block: pd.DataFrame, tal: pd.DataFrame, done: int, skipped: int) -> None:
    """Per-channel coverage and what the scrub removed. Printed because a channel silently missing everywhere is the failure this pass is most likely to have."""
    print(f"\n  {done} registration(s) carry at least one channel; {skipped} already current")
    print(f"\n  {'channel':20s}{'sites':>7s}{'site-days':>12s}{'clamped':>10s}{'nulled':>9s}  pre-agg")
    for ch in params.NAMES:
        col = f"n_days_{ch}"
        if col not in block.columns:
            continue
        n = block[col].fillna(0)
        sites, days = int((n > 0).sum()), int(n.sum())
        cl = nu = 0
        pre = ""
        if len(tal):
            t = tal[tal.channel == ch]
            cl, nu = int(t.n_clamped.sum()), int(t.n_nulled.sum())
            if len(t) and t.preaggregated.any():
                pre = f"{int(t.preaggregated.sum())} site(s)"
        if sites or days:
            print(f"  {ch:20s}{sites:>7d}{days:>12,}{cl:>10,}{nu:>9,}  {pre}")
    empty = [c for c in params.NAMES if int(block.get(f"n_days_{c}", pd.Series([0])).fillna(0).sum()) == 0]
    if empty:
        print(f"\n  channels with NO observation anywhere: {empty}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="re-bin every site, ignoring mtimes")
    ap.add_argument("--sources", nargs="*", default=None, choices=list(schema.SOURCES))
    a = ap.parse_args()
    main(force=a.force, sources=a.sources)
