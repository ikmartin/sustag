"""Enumerate USGS instantaneous-value stream sites carrying each core pcode, clipped to the covariate footprint.

    python 01_enumerate_iv_sites.py              # all pcodes -> cache/iv_sites.parquet
    python 01_enumerate_iv_sites.py --pcode 00060

`hasDataTypeCd=iv` says a site HAS instantaneous data, not that its record spans the training window or runs to the present -- new-site-report.md's usability bar (>=3.92 yr, current) is deliberately NOT applied, so every count here is an upper bound. Step 03 resolves the real span for the donors that actually get used, which is the cheap place to pay that cost.
"""

from __future__ import annotations

import argparse
import io
import time
import urllib.request

import pandas as pd

from common import CACHE, PCODES, STATES, footprint, say

OUT = CACHE / "iv_sites.parquet"
_URL = ("https://waterservices.usgs.gov/nwis/site/?format=rdb&stateCd={st}&siteType=ST"
        "&hasDataTypeCd=iv&parameterCd={pc}&siteStatus=all")


def _rdb(url: str) -> pd.DataFrame:
    """One RDB response -> frame. RDB carries a units row directly under the header that is NOT data, so it is dropped before parsing; a site service returning no matches yields a comment-only body, which is an empty frame rather than an error."""
    try:
        raw = urllib.request.urlopen(url, timeout=120).read().decode(errors="replace")
    except Exception as e:
        say(f"    [warn] {type(e).__name__} -- skipping")
        return pd.DataFrame()
    lines = [l for l in raw.splitlines() if l and not l.startswith("#")]
    if len(lines) < 3:
        return pd.DataFrame()
    return pd.read_csv(io.StringIO("\n".join([lines[0]] + lines[2:])), sep="\t", dtype=str)


def enumerate_pcode(pc: str, name: str, box) -> pd.DataFrame:
    lo_lon, lo_lat, hi_lon, hi_lat = box
    frames = []
    for st in STATES:
        d = _rdb(_URL.format(st=st, pc=pc))
        if d.empty:
            continue
        d = d.dropna(subset=["dec_lat_va", "dec_long_va"]).copy()
        d["lat"] = pd.to_numeric(d.dec_lat_va, errors="coerce")
        d["lon"] = pd.to_numeric(d.dec_long_va, errors="coerce")
        d = d.dropna(subset=["lat", "lon"])
        d = d[(d.lon >= lo_lon) & (d.lon <= hi_lon) & (d.lat >= lo_lat) & (d.lat <= hi_lat)]
        frames.append(d[["site_no", "station_nm", "lon", "lat"]].assign(state=st))
        time.sleep(0.3)  # courtesy to a public service, not a rate limit we have hit
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).drop_duplicates("site_no")
    return out.assign(pcode=pc, covariate=name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pcode", default=None, help="enumerate one pcode instead of all of PCODES")
    a = ap.parse_args()

    box = footprint()
    say(f"covariate footprint: lon {box[0]:.2f}..{box[2]:.2f}  lat {box[1]:.2f}..{box[3]:.2f}")
    todo = {a.pcode: PCODES[a.pcode]} if a.pcode else PCODES

    frames = []
    for pc, name in todo.items():
        d = enumerate_pcode(pc, name, box)
        say(f"  {name:14s} pcode {pc}: {len(d):5d} IV stream sites in footprint")
        if len(d):
            frames.append(d)
    if not frames:
        raise SystemExit("no sites enumerated -- check network access to waterservices.usgs.gov")

    out = pd.concat(frames, ignore_index=True)
    if a.pcode and OUT.exists():  # a single-pcode refresh must not discard the others
        prev = pd.read_parquet(OUT)
        out = pd.concat([prev[prev.pcode != a.pcode], out], ignore_index=True)
    out.to_parquet(OUT, index=False)
    say(f"\nwrote {OUT}  ({len(out)} rows, {out.site_no.nunique()} distinct sites, {out.pcode.nunique()} pcodes)")


if __name__ == "__main__":
    main()
