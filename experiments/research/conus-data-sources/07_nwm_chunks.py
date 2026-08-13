"""Does a narrow NWM extraction actually cost less than a broad one?

The NWM retrospective zarr chunks `streamflow` as [672 hours x 30000 reaches], so reading ANY reach pulls its whole 30,000-reach chunk. If our COMIDs scatter across every chunk, "just our sites" costs the same download as "every reach" and the sensible move is to keep everything from the one pass. If they cluster into a few chunks, a narrow pull is genuinely cheap.

An earlier draft asserted the scattered case from `min(93, 324)` -- a worst-case placeholder, not a measurement. This measures it.

Decompresses the feature_id chunk with the `zstd` CLI rather than adding numcodecs/zarr to the environment; the NWM ingest itself will want the real zarr stack.

    python experiments/research/conus-data-sources/07_nwm_chunks.py
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
_OUT = Path(__file__).resolve().parent / "data" / "nwm_chunks.csv"
BASE = "https://noaa-nwm-retrospective-3-0-pds.s3.amazonaws.com/CONUS/zarr/chrtout.zarr/"
CHUNK_REACHES = 30_000  # from streamflow/.zarray chunks[1]


def site_comids() -> list[int]:
    out: set[int] = set()
    for p in list((_ROOT / "src/data/processed/basins").rglob("*.csv")) + \
             list((_ROOT / "src/data/processed/basins").rglob("*.parquet")):
        try:
            d = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
        except Exception:
            continue
        for c in d.columns:
            if c.lower() == "comid":
                out |= set(pd.to_numeric(d[c], errors="coerce").dropna().astype(int))
                break
    return sorted(out)


def feature_ids() -> np.ndarray:
    raw = urllib.request.urlopen(BASE + "feature_id/0", timeout=300).read()
    dec = subprocess.run(["zstd", "-d", "-c"], input=raw, capture_output=True, check=True).stdout
    return np.frombuffer(dec, dtype="<i8")


def main() -> None:
    meta = json.load(urllib.request.urlopen(BASE + ".zmetadata", timeout=60))["metadata"]
    sf = meta["streamflow/.zarray"]
    n_t, n_f = sf["shape"]
    ct, cf = sf["chunks"]
    n_time_chunks, n_feat_chunks = int(np.ceil(n_t / ct)), int(np.ceil(n_f / cf))
    print(f"=== streamflow: shape={sf['shape']} chunks={sf['chunks']} ===")
    print(f"  {n_time_chunks} time-chunks x {n_feat_chunks} feature-chunks")

    fid = feature_ids()
    print(f"  feature_id: n={len(fid):,} min={fid.min()} max={fid.max()} sorted={bool(np.all(np.diff(fid) >= 0))}")

    ours = site_comids()
    print(f"\n  site COMIDs on disk: {len(ours)}")
    idx = np.flatnonzero(np.isin(fid, ours))
    print(f"  matched in NWM: {len(idx)} ({100*len(idx)/max(len(ours),1):.0f}%)")

    ch = np.unique(idx // cf)
    frac = len(ch) / n_feat_chunks
    print(f"\n  our COMIDs occupy {len(ch)} of {n_feat_chunks} feature-chunks = {100*frac:.0f}%")
    print(f"  chunk indices: {ch.tolist()}")

    chunk_gb = ct * cf * 4 / 1e9
    print(f"\n  one chunk = {chunk_gb*1000:.0f} MB uncompressed")
    print(f"  narrow pull (our sites): {len(ch)} x {n_time_chunks} chunks = {len(ch)*n_time_chunks*chunk_gb:,.0f} GB read")
    print(f"  broad pull (all reaches): {n_feat_chunks} x {n_time_chunks} chunks = {n_feat_chunks*n_time_chunks*chunk_gb:,.0f} GB read")
    print(f"  narrow is {1/frac:.1f}x cheaper" if frac < 0.9 else "  narrow is NOT meaningfully cheaper")

    pd.DataFrame([dict(site_comids=len(ours), matched=len(idx), feature_chunks_total=n_feat_chunks,
                       feature_chunks_touched=len(ch), frac_touched=round(frac, 3),
                       narrow_read_gb=round(len(ch)*n_time_chunks*chunk_gb),
                       broad_read_gb=round(n_feat_chunks*n_time_chunks*chunk_gb))]).to_csv(_OUT, index=False)
    print(f"\nwrote {_OUT.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
