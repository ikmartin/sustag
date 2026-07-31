"""Fetch daily-mean discharge for every donor any radius selected, and report which are actually usable.

    python 03_fetch_discharge.py                 # -> cache/discharge_daily.parquet
    python 03_fetch_discharge.py --start 2008-01-01

Daily values (`dv`, pcode 00060, statistic 00003 = mean), not instantaneous. The model's spine is daily and the target is a daily max, so a 15-minute series would only be resampled away; `dv` is one request per site instead of one per site-year.

THE SPAN CHECK LIVES HERE, not in step 01. `hasDataTypeCd=iv` in the enumeration says a site has data, not that it covers the training window -- and applying new-site-report.md's usability bar to 1,192 sites before knowing which ones any radius selects would be paying for 1,100 sites nobody asked about. Only the donors reached by some radius are fetched, and their real spans are reported so step 04 can drop the ones that would contribute an all-NaN column.
"""

from __future__ import annotations

import argparse
import io
import time
import urllib.request

import pandas as pd

from common import CACHE, say

DONORS = CACHE / "donors.parquet"
OUT = CACHE / "discharge_daily.parquet"
_URL = ("https://waterservices.usgs.gov/nwis/dv/?format=rdb&sites={site}"
        "&parameterCd=00060&statCd=00003&startDT={start}&endDT={end}")


def _fetch(site: str, start: str, end: str) -> pd.DataFrame:
    """Daily mean discharge for one site -> (date, discharge). Empty frame on any failure: a donor that cannot be fetched is a donor step 04 must drop, which is a data fact rather than a crash."""
    try:
        raw = urllib.request.urlopen(_URL.format(site=site, start=start, end=end), timeout=120).read().decode(errors="replace")
    except Exception:
        return pd.DataFrame()
    lines = [l for l in raw.splitlines() if l and not l.startswith("#")]
    if len(lines) < 3:
        return pd.DataFrame()
    d = pd.read_csv(io.StringIO("\n".join([lines[0]] + lines[2:])), sep="\t", dtype=str)
    # the value column is named <ts_id>_00060_00003 and the id varies by site, so it is found by suffix
    vc = [c for c in d.columns if c.endswith("_00060_00003")]
    if not vc or "datetime" not in d.columns:
        return pd.DataFrame()
    out = pd.DataFrame({"date": pd.to_datetime(d.datetime, errors="coerce"),
                        "discharge": pd.to_numeric(d[vc[0]], errors="coerce")}).dropna()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2008-01-01", help="the cohort's nitrate record starts ~2008")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--min-years", type=float, default=3.92, help="new-site-report's usability bar, for reporting only")
    a = ap.parse_args()

    if not DONORS.exists():
        raise SystemExit(f"{DONORS} missing -- run 02_link_donors.py first")
    need = sorted(pd.read_parquet(DONORS).donor.dropna().unique())
    prev = pd.read_parquet(OUT) if OUT.exists() else pd.DataFrame(columns=["date", "discharge", "donor"])
    done = set(prev.donor.unique())
    need = [s for s in need if s not in done]  # incremental: raising --topk re-links many donors but re-fetches only the new ones
    say(f"{len(need)} donors to fetch ({len(done)} already cached)\n")
    if not need:
        say("nothing to do")
        return

    frames, empty = [], []
    for i, s in enumerate(need, 1):
        d = _fetch(s, a.start, a.end)
        if d.empty:
            empty.append(s)
        else:
            frames.append(d.assign(donor=s))
        if i % 25 == 0 or i == len(need):
            say(f"  {i}/{len(need)} fetched ({len(empty)} empty)")
        time.sleep(0.2)

    if not frames and prev.empty:
        raise SystemExit("no discharge series retrieved")
    out = pd.concat([prev] + frames, ignore_index=True)
    out.to_parquet(OUT, index=False)

    g = out.groupby("donor").date
    span = ((g.max() - g.min()).dt.days / 365.25)
    say(f"\nwrote {OUT}  ({len(out):,} site-days over {out.donor.nunique()} donors)")
    say(f"  span (yr): median {span.median():.1f}, p10 {span.quantile(.1):.1f}")
    say(f"  donors clearing the {a.min_years} yr bar: {(span >= a.min_years).sum()}/{len(span)}")
    say(f"  donors still reporting in {out.date.max().year}: {(g.max() >= f'{out.date.max().year}-01-01').sum()}")
    if empty:
        say(f"  NO daily discharge returned for {len(empty)} donors -- step 04 drops these: {empty[:8]}{'...' if len(empty) > 8 else ''}")


if __name__ == "__main__":
    main()
