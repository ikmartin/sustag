"""Labels, nodes and grab samples INSIDE the declared scope.

Scope is one declaration every source inherits, so the manifest's numbers are in-scope numbers. The CONUS figures from 02/03 remain as context and as upper bounds -- they are not what the pipeline collects.

Also fixes the temporal half of the declaration: record spans decide the modelling window.

    python experiments/research/conus-data-sources/06_scoped_census.py

Writes data/scoped_census.csv.
"""

from __future__ import annotations

import io
import os
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
_HERE = Path(__file__).resolve().parent
_OUT = _HERE / "data" / "scoped_census.csv"

# The primary box: DBSCAN cluster 0 from 05_scope_and_footprint.py, 0.5 deg buffer.
PRIMARY = [-98.7, 36.68, -82.68, 44.86]
YEARS = (2008, 2026)

FAMILIES = {
    "nitrate": ["99133", "99137", "00630"], "discharge": ["00060"],
    "stage": ["00065", "63160", "63158"], "water_temp": ["00010", "00011"],
    "spec_cond": ["00095"], "turbidity": ["63680", "63675", "72213", "63682", "00070"],
    "diss_oxy": ["00300", "00301"], "ph": ["00400"],
    "groundwater": ["72019", "62611", "62610"], "precip_site": ["00045", "99772"],
    "soil_moisture": ["74207", "72181"], "soil_temp": ["72253", "62846"],
}


def _auth() -> None:
    with open(_ROOT / "src" / "build" / "api-keys.toml", "rb") as f:
        os.environ["API_USGS_PAT"] = tomllib.load(f)["usgs"]


def usgs_census(box) -> pd.DataFrame:
    from dataretrieval import waterdata
    rows = []
    for fam, pcodes in FAMILIES.items():
        frames = []
        for pc in pcodes:
            try:
                res = waterdata.get_time_series_metadata(parameter_code=pc, bbox=list(box), skip_geometry=True)
                df = res[0] if isinstance(res, tuple) else res
            except Exception as e:
                print(f"    {fam}/{pc}: {type(e).__name__}")
                continue
            if df is not None and len(df):
                frames.append(df)
        if not frames:
            rows.append(dict(family=fam, sites=0, span_median_yr=float("nan"))); continue
        a = pd.concat(frames, ignore_index=True)
        a["begin"] = pd.to_datetime(a["begin"], errors="coerce", utc=True)
        a["end"] = pd.to_datetime(a["end"], errors="coerce", utc=True)
        per = a.groupby("monitoring_location_id").agg(
            yrs=("begin", lambda s: 0), b=("begin", "min"), e=("end", "max"))
        per["yrs"] = (per.e - per.b).dt.days / 365.25
        rows.append(dict(family=fam, sites=int(a.monitoring_location_id.nunique()),
                         span_median_yr=round(float(per.yrs.median()), 2),
                         ge_3yr=int((per.yrs >= 3).sum()),
                         first=str(per.b.min())[:10], last=str(per.e.max())[:10]))
        print(f"  {fam:14} {rows[-1]['sites']:6,d} sites   median span {rows[-1]['span_median_yr']}yr")
    return pd.DataFrame(rows)


def wqp_grab(box) -> dict:
    """Discrete nitrate stations in the box. WQP takes bBox directly, so one call, no state loop.

    Needs a long timeout: the Iowa-only Station query returned 1.4 MB in 27.6 s.
    """
    q = {"bBox": ",".join(str(v) for v in box), "characteristicName": "Nitrate",
         "siteType": "Stream", "mimeType": "csv", "zip": "no", "providers": "NWIS"}
    url = "https://www.waterqualitydata.us/data/Station/search?" + urllib.parse.urlencode(q)
    try:
        raw = urllib.request.urlopen(url, timeout=300).read().decode("utf-8", "replace")
    except Exception as e:
        print(f"  WQP: {type(e).__name__} {e}")
        return {}
    d = pd.read_csv(io.StringIO(raw), dtype=str, low_memory=False)
    print(f"  WQP stations in box: {len(d):,}  ({len(raw)/1e6:.1f} MB)")
    org = d["OrganizationFormalName"].value_counts().head(6).to_dict() if "OrganizationFormalName" in d else {}
    return dict(wqp_stations=len(d), top_orgs=org)


def main() -> None:
    _auth()
    print(f"=== USGS continuous series inside the primary box {PRIMARY} ===")
    u = usgs_census(PRIMARY)

    print("\n=== WQP discrete nitrate stations inside the box ===")
    g = wqp_grab(PRIMARY)

    nit = int(u.loc[u.family == "nitrate", "sites"].iloc[0])
    dis = int(u.loc[u.family == "discharge", "sites"].iloc[0])
    print(f"\n  nitrate labels (continuous) : {nit:,}")
    print(f"  discharge nodes             : {dis:,}   ratio {dis/max(nit,1):.0f}x")
    if g:
        print(f"  discrete nitrate stations   : {g['wqp_stations']:,}  "
              f"({g['wqp_stations']/max(nit,1):.0f}x the continuous count)")
        print(f"  top organisations: {g['top_orgs']}")

    u["box"] = str(PRIMARY)
    u.to_csv(_OUT, index=False)
    print(f"\nwrote {_OUT.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
