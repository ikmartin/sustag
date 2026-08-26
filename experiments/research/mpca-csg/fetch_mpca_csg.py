"""Fetch the MPCA/MN-DNR continuous nitrate network from the CSG API.

WHY THIS IS NOT REDUNDANT. 22 of the 32 stations carry a `usgs_id`, but only 2 of those USGS sites report nitrate in NWIS -- the gage exists for stage and discharge while MPCA runs the nitrate sensor, and that series lives only in the Minnesota system. Skipping this network loses 30 corn-belt nitrate sites that no USGS or WQP query returns.

ACCESS. Undocumented but public, no auth. `.../sites` lists every MN station; `.../sites/{id}` gives lat/lon, usgs_id and the variable list; `.../sites/{id}/download` returns a ZIP of one CSV per variable. Variable 341 is nitrate, per maps.dnr.state.mn.us/ewr/csg/lib/config.js.

UNITS ARE NOT DOCUMENTED. `var_name` reads "NO3 + NO2 (mg/L)" without stating as-N or as-NO3, and the bundled readme is a quality-flag dictionary. Values look as-N (a sampled site runs median 6.49, p90 10.06 -- as-NO3 would be ~4.43x that), but CONFIRM before use or this is the WQP 4.4269 trap again.

    python experiments/research/mpca-csg/fetch_mpca_csg.py

ZIPs and per-site parquet land in src/data/raw/water/MPCA_CSG/ (gitignored).
"""

from __future__ import annotations

import io
import json
import re
import time
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
_RAW = _ROOT / "src" / "data" / "raw" / "water" / "MPCA_CSG"
_HERE = Path(__file__).resolve().parent

API = "https://webapps.dnr.state.mn.us/csg/api/v1/sites"
MPCA_PAGE = "https://www.pca.state.mn.us/air-water-land-climate/continuous-nitrate-sensor-network"
UA = {"User-Agent": "Mozilla/5.0 (sustag research)"}
NITRATE_VAR = 341


def _get(url: str, timeout: int = 300) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def station_ids() -> list[str]:
    """The network roster, parsed from the MPCA page's own links. The page text lists 37 entries but only 32 distinct ids -- several links repeat."""
    t = _get(MPCA_PAGE, 120).decode("utf-8", "replace")
    return sorted({m for _, m in re.findall(r'href="([^"]*csg/site\.html\?id=(\d+))"', t)})


def main() -> None:
    _RAW.mkdir(parents=True, exist_ok=True)
    ids = station_ids()
    print(f"{len(ids)} stations on the MPCA nitrate network")

    meta, frames = [], []
    for k, sid in enumerate(ids, 1):
        try:
            r = json.loads(_get(f"{API}/{sid}", 90))["result"]
        except Exception as e:
            print(f"  {sid}: metadata {type(e).__name__}")
            continue
        meta.append(dict(csg_id=sid, name=r["name"], lat=float(r["lat"]), lon=float(r["lon"]),
                         usgs_id=r.get("usgs_id"), equis_id=r.get("equis_id"),
                         providers=",".join(r.get("providers") or []),
                         variables=",".join(str(v) for v in (r.get("variables") or []))))

        zp = _RAW / f"csg_{sid}.zip"
        if not zp.exists():
            try:
                blob = _get(f"{API}/{sid}/download", 900)
            except Exception as e:
                print(f"  {sid}: download {type(e).__name__}")
                continue
            tmp = zp.with_suffix(".zip.tmp")          # atomic: a truncated zip must not look done
            tmp.write_bytes(blob)
            tmp.replace(zp)
        else:
            blob = zp.read_bytes()

        # every variable, long format -- the pipeline keeps co-reported parameters, not just nitrate
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for n in z.namelist():
                    m = re.match(rf"csg_{sid}_(\d+)_(.+)\.csv$", n)
                    if not m:
                        continue
                    d = pd.read_csv(z.open(n), dtype={"station": str})
                    d["variable"] = int(m.group(1))
                    d["csg_id"] = sid
                    frames.append(d)
        except Exception as e:
            print(f"  {sid}: unzip {type(e).__name__}")
            continue
        print(f"  [{k:2d}/{len(ids)}] {sid} {r['name'][:40]:40} {len(blob)/1e6:5.1f} MB")
        time.sleep(0.2)

    m = pd.DataFrame(meta)
    m.to_csv(_RAW / "mpca_csg_sites.csv", index=False)

    all_obs = pd.concat(frames, ignore_index=True)
    all_obs["tstamp"] = pd.to_datetime(all_obs.tstamp, errors="coerce")
    all_obs["value"] = pd.to_numeric(all_obs.value, errors="coerce")
    # `quality` mixes numeric codes (200) with words ("Unchecked"), so the concatenated column is
    # object dtype holding both int and str and pyarrow refuses it. Coerce to string.
    for c in ("quality", "var_name", "type", "station"):
        if c in all_obs.columns:
            all_obs[c] = all_obs[c].astype("string")
    for sid, g in all_obs.groupby("csg_id"):
        g.to_parquet(_RAW / f"csg_{sid}.parquet", index=False)

    nit = all_obs[all_obs.variable == NITRATE_VAR].dropna(subset=["value"])
    per = nit.groupby("csg_id").agg(n_obs=("value", "size"),
                                    first=("tstamp", "min"), last=("tstamp", "max"),
                                    median=("value", "median"), mx=("value", "max"))
    per["n_days"] = nit.groupby("csg_id").tstamp.apply(lambda s: s.dt.date.nunique())
    out = m.set_index("csg_id").join(per)
    out.to_csv(_HERE / "mpca_nitrate_summary.csv")

    print(f"\nstations with nitrate: {len(per)}/{len(m)}")
    print(f"  total nitrate obs {nit.shape[0]:,}   distinct site-days {int(per.n_days.sum()):,}")
    print(f"  record span {nit.tstamp.min()} .. {nit.tstamp.max()}")
    print(f"  median of per-site medians {per['median'].median():.2f} mg/L  "
          f"(as-N plausible; as-NO3 would be ~{per['median'].median()*4.4269:.0f})")
    print(f"  per-site n_days: min {per.n_days.min()} median {per.n_days.median():.0f} max {per.n_days.max()}")
    print(f"\nwrote {_RAW.relative_to(_ROOT)}/ and {(_HERE/'mpca_nitrate_summary.csv').relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
