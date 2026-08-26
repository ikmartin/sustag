"""Read-side caches: optimizations with live fallbacks, never prerequisites, never authorities.

The pooled nitrate read replaces the retired `nitrate_daily.parquet` publication artifact: the same one-read convenience, assembled here from the published per-record files and cached keyed on the published store's own stamps -- a republish invalidates it by construction, and deleting the cache costs a recompute, never correctness.
"""

from __future__ import annotations

import hashlib

import pandas as pd

from . import config


def _store_key() -> str:
    h = hashlib.sha256()
    for p in sorted(config.PUB_WATER.glob("*.parquet")):
        st = p.stat()
        h.update(f"{p.name}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:12]


def pooled_nitrate(refresh: bool = False) -> pd.DataFrame:
    """Every published record's daily nitrate in one frame: (code, date, the seven primitives, provenance)."""
    config.CACHE.mkdir(parents=True, exist_ok=True)
    key = _store_key()
    p = config.CACHE / f"nitrate_pooled_{key}.parquet"
    if p.exists() and not refresh:
        return pd.read_parquet(p)
    for stale in config.CACHE.glob("nitrate_pooled_*.parquet"):
        stale.unlink()
    cols = [f"nitrate_{s}" for s in ("mean", "min", "max", "p25", "median", "p75", "n_obs")]
    frames = []
    for f in sorted(config.PUB_WATER.glob("*.parquet")):
        d = pd.read_parquet(f)
        if "nitrate_mean" not in d.columns:
            continue
        keep = ["date"] + [c for c in cols + ["nitrate_src"] if c in d.columns]
        d = d[keep].dropna(subset=["nitrate_mean"])
        if len(d):
            frames.append(d.assign(code=f.stem))
    out = (pd.concat(frames, ignore_index=True) if frames
           else pd.DataFrame(columns=["code", "date"] + cols))
    out = out[["code"] + [c for c in out.columns if c != "code"]]
    tmp = p.with_name(p.name + ".tmp")
    out.to_parquet(tmp)
    tmp.replace(p)
    return out
