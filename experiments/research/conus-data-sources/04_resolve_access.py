"""Resolve the sources whose access route was unknown, and characterise what they hold.

Every 404 in the first pass was a GUESSED landing-page URL, not a resolved endpoint. This finds the real routes. Two of them turn out to be a library already in the dependency tree rather than a URL.

KEY RESULT. `pynhd.nhdplus_attrs_s3()` returns the whole Wieczorek NHDPlusV2 attribute catalogue -- 14,139 attributes -- from S3 in ~3 s, while `pynhd.nhdplus_attrs()` (the ScienceBase route) times out. ScienceBase is the broken link, not our client, and it is bypassable. `_make_comid_attrs.py` currently wires 3 of the 14,139.

    python experiments/research/conus-data-sources/04_resolve_access.py
"""

from __future__ import annotations

import collections
import re
import time
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
_OUT = Path(__file__).resolve().parent / "data"

# Resolved access routes. Confirmed by request; see the report's failure ledger for what these replace.
RESOLVED = {
    "NHDPlus VAA":            "pynhd.nhdplus_vaa()  -- library call, not a URL. HydroShare content path 301->405",
    "Wieczorek attributes":   "pynhd.nhdplus_attrs_s3()  -- 14,139 attrs from S3. ScienceBase route times out / 502",
    "NADP TDep":              "https://gaftp.epa.gov/castnet/tdep/CURRENT_grids/  -- bc_dw-YYYY.zip, GeoTIFF 2000-2022",
    "Heidelberg NCWQR HTLP":  "https://ncwqr-data.org/  (portal, 200) + Zenodo doi:10.5281/zenodo.6606949 (API 403 to bots)",
    "CAMELS-Chem":            "HydroShare 841f5e85085c423f889ac809c1bed4ac -- the id previously tried was wrong",
    "MPCA nitrate network":   "https://www.pca.state.mn.us/air-water-land-climate/continuous-nitrate-sensor-network; "
                              "series hosted on MN DNR CSG, https://www.dnr.state.mn.us/waters/csg/site.html?id=<id>",
    "Water Quality Portal":   "works -- needs >30 s timeout. IA nitrate stations = 1.4 MB in 27.6 s",
    "SMAP":                   "https://cmr.earthdata.nasa.gov/search/collections.json?short_name=SPL4SMGP  (NSIDC host refused)",
    "gNATSGO":                "largely SUPERSEDED -- the Soils theme below is COMID-keyed already",
    "CropScape GetCDLFile":   "UNRESOLVED -- 40 s timeout; the repo's own client works, so treat as slow not broken",
}


def main() -> None:
    for k, v in RESOLVED.items():
        print(f"  {k:24} {v}")

    import pynhd

    print("\n=== pynhd.nhdplus_attrs_s3() ===")
    t = time.time()
    a = pynhd.nhdplus_attrs_s3()
    print(f"  {len(a):,} attributes in {time.time()-t:.1f}s")

    print("\n  themes:")
    for th, c in a.themeLabel.value_counts().items():
        print(f"    {c:6,d}  {th}")

    yr = a[a.ID.str.contains(r"(?:19|20)\d{2}", regex=True, na=False)]
    print(f"\n  time-varying (year in ID): {len(yr):,}")
    stems = collections.Counter(re.sub(r"(19|20)\d{2}", "YYYY", i) for i in yr.ID)
    for s, c in stems.most_common(6):
        print(f"    {c:3d} vintages  {s}")

    # The two themes that change recommendations elsewhere in the report.
    print("\n  BEST MANAGEMENT PRACTICES -- the gap Pandit 2025 says does not exist at continental scale:")
    bmp = a[(a.themeLabel == "Best Management Practices") & a.ID.str.startswith("CAT_")]
    for _, r in bmp.iterrows():
        print(f"    {r.ID:20} {str(r.description)[:88]}")

    print("\n  SOILS -- COMID-keyed, so gSSURGO/POLARIS need no delineation:")
    soils = sorted(a[(a.themeLabel == "Soils") & a.ID.str.startswith("CAT_")].ID)
    print(f"    {soils}")

    _OUT.mkdir(parents=True, exist_ok=True)
    keep = a[a.ID.str.startswith("CAT_")][["ID", "description", "units", "themeLabel"]]
    keep.to_csv(_OUT / "wieczorek_cat_attrs.csv", index=False)
    pd.DataFrame([{"source": k, "route": v} for k, v in RESOLVED.items()]).to_csv(_OUT / "resolved_access.csv", index=False)
    print(f"\nwrote {(_OUT / 'wieczorek_cat_attrs.csv').relative_to(_ROOT)} ({len(keep):,} CAT_ rows)")
    print(f"wrote {(_OUT / 'resolved_access.csv').relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
