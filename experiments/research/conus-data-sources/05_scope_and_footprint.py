"""Derive the region box collection, then price every source inside it.

WHY BOXES. A single CONUS box pays for enormous empty area: nitrate sensors cluster hard, and a graph model is useless where there are none. Clustering the corrected sensor set (all three nitrate pcodes, so Nebraska is present) gives a small number of dense regions whose bounding boxes cover a fraction of CONUS.

WHY THIS IS THE GATING SCRIPT. Scope is one declaration that every source inherits -- weather, sites, gauges, COMID attributes, grab samples and modelled discharge are all collected inside it, for the declared years. So the footprint cannot be priced until the boxes are fixed, and the boxes cannot be fixed without knowing whether the footprint fits.

    python experiments/research/conus-data-sources/05_scope_and_footprint.py

Writes data/region_boxes.csv and data/footprint.csv.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

_ROOT = Path(__file__).resolve().parents[3]
_OUT = Path(__file__).resolve().parent / "data"

EPS_KM, MIN_SAMPLES, BUFFER_DEG = 150, 3, 0.5
CONUS_KM2 = 8.08e6
CURRENT_BBOX = [-100.158, 39.75, -85.839, 47.885]  # pipeline_config.toml [region].covariate_bbox
NHDPLUS_REACHES_CONUS = 2_776_734                  # measured from the NWM feature_id array
YEARS = 2008, 2026


def box_area_km2(b) -> float:
    """Approximate area of a lon/lat box, evaluated at its mean latitude."""
    latm = (b[1] + b[3]) / 2
    return (b[2] - b[0]) * 111.32 * np.cos(np.radians(latm)) * (b[3] - b[1]) * 110.57


def _points() -> pd.DataFrame:
    d = pd.read_parquet(_ROOT / "notes" / "graphics" / "_usgs_nitrate_conus.parquet")[["lat", "lon"]]
    sl = pd.read_csv(_ROOT / "src" / "data" / "raw" / "water" / "raw_site_list.csv")
    c = {x.lower(): x for x in sl.columns}
    la, lo = c.get("lat") or c.get("latitude"), c.get("lon") or c.get("longitude")
    iw = sl[[la, lo]].dropna().rename(columns={la: "lat", lo: "lon"})
    p = pd.concat([d, iw], ignore_index=True).drop_duplicates()
    return p[(p.lon > -125) & (p.lon < -66) & (p.lat > 24) & (p.lat < 50)].reset_index(drop=True)


def boxes() -> pd.DataFrame:
    pts = _points()
    g = gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts.lon, pts.lat), crs=4326).to_crs(5070)
    lab = DBSCAN(eps=EPS_KM * 1000, min_samples=MIN_SAMPLES).fit_predict(np.c_[g.geometry.x, g.geometry.y])
    rows = []
    for k in sorted(set(lab) - {-1}):
        sub = pts[lab == k]
        b = [round(sub.lon.min() - BUFFER_DEG, 2), round(sub.lat.min() - BUFFER_DEG, 2),
             round(sub.lon.max() + BUFFER_DEG, 2), round(sub.lat.max() + BUFFER_DEG, 2)]
        rows.append(dict(cluster=int(k), n_sites=int((lab == k).sum()), area_km2=box_area_km2(b),
                         km2_per_site=box_area_km2(b) / int((lab == k).sum()), bbox=b))
    out = pd.DataFrame(rows).sort_values("n_sites", ascending=False).reset_index(drop=True)
    out["noise_pts"] = int((lab == -1).sum())
    return out


# Per-M-km2 rates, derived from what the CURRENT bbox actually holds on disk. Only these scale.
_CUR_AREA_M = box_area_km2(CURRENT_BBOX) / 1e6
SCALING_GB = {"gridMET raw (NetCDF cache)": 26.0, "CDL clipped": 3.4, "interim per-cell tables": 5.5}
FIXED_GB = {"CDL national": 50.0, "gTREND surplus national": 4.7,
            "gTREND fertiliser national": 0.431, "AgTile national": 0.120}

# Regenerable download caches. Under clip -> aggregate -> discard they are deleted once the
# per-cell table they feed exists; CLAUDE.md already treats raw rasters as regenerable.
TRANSIENT = {"CDL national", "gridMET raw (NetCDF cache)"}
N_SITE_COMIDS = 400  # in-scope labelled sites -> the NWM phase-1 pull


def footprint(area_m_km2: float, discard_caches: bool = False, nwm: str = "all") -> pd.DataFrame:
    """Disk for a scope of `area_m_km2` million km2.

    discard_caches -> delete the regenerable raster downloads once clipped. nwm='sites' prices only the labelled sites' COMIDs (training), 'all' every in-scope reach (deployment).
    """
    n_comid = NHDPLUS_REACHES_CONUS * (area_m_km2 * 1e6 / CONUS_KM2)
    n_days = (YEARS[1] - YEARS[0]) * 365.25
    rows = []
    for k, gb in FIXED_GB.items():
        rows.append(dict(source=k, scales="no", gb=gb, note="already national on disk"))
    for k, gb in SCALING_GB.items():
        rows.append(dict(source=k, scales="yes", gb=gb / _CUR_AREA_M * area_m_km2,
                         note=f"{gb:.1f} GB at the current {_CUR_AREA_M:.2f}M km2"))
    nwm_n = N_SITE_COMIDS if nwm == "sites" else n_comid
    rows.append(dict(source=f"NWM daily aggregate ({nwm})", scales="yes",
                     gb=nwm_n * n_days * 3 * 4 / 1e9,
                     note=f"{nwm_n/1e3:.1f}k COMIDs x {n_days:.0f} days x 3 stats x 4B"))
    rows.append(dict(source="Wieczorek attrs (~6.5k after excl.)", scales="yes",
                     gb=n_comid * 6500 * 4 / 1e9, note="CAT+ACC+TOT minus Climate Water Balance"))
    rows.append(dict(source="StreamCat (~500 values/COMID)", scales="yes",
                     gb=n_comid * 500 * 4 / 1e9, note="168 metrics x AOI x vintages"))
    rows.append(dict(source="NHDPlus VAA + flowlines", scales="yes",
                     gb=n_comid * 100 * 4 / 1e9, note="~100 columns"))
    df = pd.DataFrame(rows)
    if discard_caches:
        df.loc[df.source.isin(TRANSIENT), "gb"] = 0.0
        df.loc[df.source.isin(TRANSIENT), "note"] = "deleted after clipping (regenerable)"
    return df


def main() -> None:
    b = boxes()
    print(f"=== clusters (DBSCAN eps={EPS_KM}km, min_samples={MIN_SAMPLES}, buffer {BUFFER_DEG}deg) ===")
    print(f"  {len(b)} clusters, {int(b.noise_pts.iloc[0])} unclustered points")
    for _, r in b.iterrows():
        print(f"  c{r.cluster:<2d} n={r.n_sites:3d}  {r.area_km2/1e3:7.0f}k km2  "
              f"{r.km2_per_site:7.0f} km2/site  {r.bbox}")

    cur = box_area_km2(CURRENT_BBOX) / 1e6
    cb = b.iloc[0]
    print(f"\n=== the primary box vs what we build today ===")
    print(f"  current covariate_bbox : {cur:.3f}M km2")
    print(f"  primary (corn belt)    : {cb.area_km2/1e6:.3f}M km2  = {cb.area_km2/1e6/cur:.2f}x, "
          f"{100*cb.area_km2/CONUS_KM2:.0f}% of CONUS")

    opts = {"current bbox (baseline)": cur,
            "primary box only": cb.area_km2 / 1e6,
            "primary + mid-Atlantic": (cb.area_km2 + b.iloc[1].area_km2) / 1e6,
            "primary + mid-Atl + CA + Columbia":
                (cb.area_km2 + b.iloc[1].area_km2 + b[b.cluster == 0].area_km2.iloc[0]
                 + b[b.cluster == 10].area_km2.iloc[0]) / 1e6}

    FREE = 129.0
    for policy, kw in [("retain every download (today's policy)", dict(discard_caches=False, nwm="all")),
                       ("clip -> aggregate -> discard caches", dict(discard_caches=True, nwm="all")),
                       ("discard caches + NWM at site COMIDs only", dict(discard_caches=True, nwm="sites"))]:
        print(f"\n=== footprint: {policy}   (free disk {FREE:.0f} GB) ===")
        for nm, a in opts.items():
            f = footprint(a, **kw)
            tot = f.gb.sum()
            print(f"  {nm:34} {a:5.2f}M km2   {tot:6.1f} GB   "
                  f"{'FITS  (' + format(FREE-tot, '.0f') + ' GB spare)' if tot < FREE else 'DOES NOT FIT'}")

    keep = footprint(cb.area_km2 / 1e6, discard_caches=True, nwm="sites")
    print("\n=== per-source, primary box, recommended policy ===")
    for _, r in keep.sort_values("gb", ascending=False).iterrows():
        print(f"  {r.source:36} {r.scales:3}  {r.gb:7.2f} GB   {r.note}")
    print(f"  {'TOTAL':36}      {keep.gb.sum():7.2f} GB")

    _OUT.mkdir(parents=True, exist_ok=True)
    b.to_csv(_OUT / "region_boxes.csv", index=False)
    keep.to_csv(_OUT / "footprint.csv", index=False)
    print(f"\nwrote {(_OUT/'region_boxes.csv').relative_to(_ROOT)}, {(_OUT/'footprint.csv').relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
