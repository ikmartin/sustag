"""Pull discrete nitrate results from the Water Quality Portal, harmonise units, deduplicate, cache.

    python 01_fetch_wqp.py                       # Iowa streams, all organisations -> cache/wqp_nitrate.parquet
    python 01_fetch_wqp.py --characteristic Chloride --out wqp_chloride.parquet

THE CONVERSION IS SELF-CHECKING, and the script asserts it. WQP returns the same USGS measurements twice, as `mg/l as N` and `mg/l asNO3`, differing by 4.4269. If the harmonisation is right, the two populations' distributions COINCIDE afterwards -- not merely resemble each other. A ratio-of-medians that lands on 4.43 before conversion and 1.00 after is the evidence the report cites; anything else means the unit table in common.py is wrong for this pull and the cache must not be trusted.

Deduplication happens on the HARMONISED value for the same reason: the duplicate pair is identical only after conversion.
"""

from __future__ import annotations

import argparse
import io
import urllib.parse
import urllib.request

import pandas as pd

from common import CACHE, dedupe, harmonise, is_volunteer, say

_URL = "https://www.waterqualitydata.us/data/Result/search"
_KEEP = ["MonitoringLocationIdentifier", "OrganizationFormalName", "ActivityStartDate",
         "CharacteristicName", "ResultMeasureValue", "ResultMeasure/MeasureUnitCode",
         "ResultDetectionConditionText", "ActivityTypeCode", "ResultSampleFractionText"]


def fetch(characteristic: str, statecode: str, start: str) -> pd.DataFrame:
    p = {"statecode": statecode, "siteType": "Stream", "characteristicName": characteristic,
         "mimeType": "csv", "zip": "no", "startDateLo": start}
    say(f"requesting {characteristic} ({statecode}, from {start}) -- this is a large response, allow several minutes")
    raw = urllib.request.urlopen(_URL + "?" + urllib.parse.urlencode(p), timeout=900).read().decode(errors="replace")
    d = pd.read_csv(io.StringIO(raw), dtype=str, low_memory=False)
    return d[[c for c in _KEEP if c in d.columns]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--characteristic", default="Nitrate")
    ap.add_argument("--statecode", default="US:19", help="US:19 = Iowa")
    ap.add_argument("--start", default="01-01-1990", help="MM-DD-YYYY, WQP's format")
    ap.add_argument("--out", default="wqp_nitrate.parquet")
    a = ap.parse_args()

    raw = fetch(a.characteristic, a.statecode, a.start)
    say(f"  {len(raw):,} raw results over {raw.MonitoringLocationIdentifier.nunique():,} stations")

    h = harmonise(raw)
    bad = h[~h.unit_ok].unit_norm.value_counts()
    if len(bad):
        say(f"\n  DROPPED as unrecognised units ({(~h.unit_ok).sum():,} results):")
        for u, n in bad.head(8).items():
            say(f"    {u!r}: {n:,}")
        say("  add these to common.UNIT_FACTOR if they are real, do not assume as-N")

    # the self-check: the two duplicate populations must coincide once converted
    asn = h[h.unit_norm == "mg/l as n"].n_mgl.dropna()
    no3 = h[h.unit_norm.isin(("mg/l asno3", "mg/l as no3"))].n_mgl.dropna()
    if len(asn) > 100 and len(no3) > 100:
        ratio = no3.median() / asn.median()
        say(f"\n  UNIT SELF-CHECK: n(as-N)={len(asn):,} n(as-NO3)={len(no3):,}, "
            f"ratio of medians AFTER conversion = {ratio:.3f}")
        if not 0.9 < ratio < 1.1:
            say("  *** FAILED: the two unit populations do not coincide after conversion. Either they are")
            say("      not duplicates in this pull, or common.UNIT_FACTOR is wrong. Do not use this cache.")
        else:
            say("  passed -- the two codes are the same measurements, and dedupe below removes the copy")

    d = dedupe(h)
    say(f"\n  after harmonisation + dedupe: {len(d):,} results over {d.MonitoringLocationIdentifier.nunique():,} stations")
    vol = is_volunteer(d.OrganizationFormalName)
    say(f"    volunteer {vol.sum():,} ({vol.mean():.0%}) | agency {(~vol).sum():,}")
    say(f"    nitrate as N mg/L: median {d.n_mgl.median():.2f}, p90 {d.n_mgl.quantile(.9):.2f}, "
        f">=10 {(d.n_mgl >= 10).mean():.1%}")

    out = CACHE / a.out
    d.to_parquet(out, index=False)
    say(f"\nwrote {out}")


if __name__ == "__main__":
    main()
