"""One-time audit: HydroWASTE v1.0 outfalls against our snapped ICIS outfalls -- the facilities-snapping check.

    python data/build/scripts/hydrowaste_crosscheck.py

HydroWASTE (Ehalt Macedo et al. 2022; reference/water-treatment-facilities/) georeferenced ~58k WWTP outfalls onto a river network independently of us. Joining its AOE plants to our snapped ICIS outfalls within 1 km and reporting the separation distribution audits our snapping against an independent method; disagreements beyond 500 m are listed for eyeballing. A CROSS-CHECK, not an authority -- HydroWASTE is a static 2021 vintage and ICIS refreshes weekly.

Needs `HydroWASTE_v10.csv` beside this script or at reference/water-treatment-facilities/ (2.3 MB download from hydrosheds.org). Writes notes/facilities-snapping-report.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd


def main() -> None:
    from data.build import config
    from data.build.acquire import facilities

    hw_path = next((p for p in (Path(__file__).parent / "HydroWASTE_v10.csv",
                                REPO / "reference" / "water-treatment-facilities" / "HydroWASTE_v10.csv")
                    if p.exists()), None)
    if hw_path is None:
        raise SystemExit("HydroWASTE_v10.csv not found -- download from hydrosheds.org (2.3 MB) into "
                         "reference/water-treatment-facilities/")
    if not facilities.OUTFALLS_PATH.exists():
        raise SystemExit("no outfall layer yet -- run the a3 facilities branch first")

    import geopandas as gpd
    from scipy.spatial import cKDTree

    hw = pd.read_csv(hw_path, low_memory=False)
    hw = hw[(hw.COUNTRY == "United States") if "COUNTRY" in hw.columns else slice(None)]
    lat_c = next(c for c in hw.columns if c.upper() in ("LAT_OUT", "LAT_WWTP", "LATITUDE"))
    lon_c = next(c for c in hw.columns if c.upper() in ("LON_OUT", "LON_WWTP", "LONGITUDE"))
    hw = hw.dropna(subset=[lat_c, lon_c])
    from shapely import points
    from shapely.prepared import prep

    geom = prep(config.aoe_geometry())
    pts = points(hw[lon_c].to_numpy(float), hw[lat_c].to_numpy(float))
    hw = hw[np.fromiter((geom.contains(p) for p in pts), bool, len(pts))]

    ours = pd.read_parquet(facilities.OUTFALLS_PATH)
    g_o = gpd.GeoSeries(gpd.points_from_xy(ours.lon, ours.lat), crs=4326).to_crs(config.EQUAL_AREA)
    g_h = gpd.GeoSeries(gpd.points_from_xy(hw[lon_c], hw[lat_c]), crs=4326).to_crs(config.EQUAL_AREA)
    tree = cKDTree(np.c_[g_o.x, g_o.y])
    d, idx = tree.query(np.c_[g_h.x, g_h.y], k=1)

    within = d <= 1000
    sep = pd.Series(d[within])
    far = hw[~within]
    lines = [
        "# Facilities snapping cross-check (HydroWASTE v1.0 vs snapped ICIS outfalls)",
        "",
        "Bottom line: an independent georeferencing of the same plants lands "
        f"{int(within.sum()):,} of {len(hw):,} AOE HydroWASTE outfalls within 1 km of an ICIS outfall "
        f"(median separation {sep.median():.0f} m, p90 {sep.quantile(0.9):.0f} m).",
        "",
        f"- HydroWASTE AOE plants: {len(hw):,}; ICIS outfalls: {len(ours):,}",
        f"- separation over matches: median {sep.median():.0f} m, p90 {sep.quantile(0.9):.0f} m, max {sep.max():.0f} m",
        f"- disagreements beyond 500 m (eyeball these): {int(((d <= 1000) & (d > 500)).sum()):,}",
        f"- HydroWASTE plants with NO ICIS outfall within 1 km: {len(far):,} "
        "(vintage drift, non-NPDES facilities, or our clip)",
        "",
        "Evidence quality: both coordinate sets are measured products; HydroWASTE is a static 2021 "
        "academic dataset (see reference/water-treatment-facilities/), ICIS refreshes weekly -- "
        "treat disagreement as a QUESTION about a specific plant, never as either source's verdict.",
    ]
    out = REPO / "notes" / "facilities-snapping-report.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
