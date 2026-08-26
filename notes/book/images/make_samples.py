"""Generate the data-sample figures and tables for notes/book/data_inventory_chapter.md.

One PNG per raster/vector source into this directory; markdown tables printed to stdout for pasting into the chapter. Every sample is cut from the real archives -- re-running refreshes them from whatever the stores hold.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
RAW = REPO / "src" / "data" / "raw"
ACQ = REPO / "src" / "data" / "acquired"
OUT = Path(__file__).resolve().parent

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Old Mans Creek at the basin-editor example gauge -- one locale reused across the spatial figures so they compose into a single mental picture.
LOC_LON, LOC_LAT = -91.615722, 41.606406


def md_table(df: pd.DataFrame, floatfmt: str = "{:.3g}") -> str:
    def fmt(v):
        if isinstance(v, float):
            if pd.isna(v):
                return ""
            if abs(v) >= 10_000 and v == round(v):
                return f"{v:,.0f}"
            return floatfmt.format(v)
        return "" if v is None or v is pd.NA else str(v)
    lines = ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(fmt(v) for v in r) + " |")
    return "\n".join(lines)


def section(name):
    print(f"\n===== {name} =====")


# ---- water records ---------------------------------------------------------------------------------

section("USGS")
d = pd.read_parquet(ACQ / "water/native/USGS__05465500.parquet")
if "datetime" in d.columns:
    d = d.set_index("datetime")
d = d.loc["2019-06-10 23:30":"2019-06-11 00:31", ["99133", "00065", "00010_dv", "00060_dv"]].head(5)
d.insert(0, "datetime", d.index.astype(str))
d = d.reset_index(drop=True)
print(md_table(d))

section("IWQIS")
d = pd.read_parquet(ACQ / "water/iwqis/WQS0031.parquet", columns=["nitrate_con", "r_exc", "m_exc", "temp_water", "turbi_mean"])
d = d[d.nitrate_con.notna() & d.r_exc.notna()].head(4)
d.insert(0, "datetime", d.index.astype(str))
d = d.reset_index(drop=True)
print(md_table(d))

section("MPCA")
d = pd.read_parquet(ACQ / "water/native/MPCA__41043001.parquet", columns=["nitrate", "turbidity", "discharge", "water_temp"])
pos = np.flatnonzero((d.turbidity == 999.99).to_numpy())
j = int(pos[len(pos) // 2])
d = d.iloc[j - 2:j + 2]
d.insert(0, "datetime", d.index.astype(str))
d = d.reset_index(drop=True)
print(md_table(d, "{:.6g}"))

# ---- hydrography -----------------------------------------------------------------------------------

section("NHDPlus image")
import geopandas as gpd
from shapely.geometry import box

w = box(LOC_LON - 0.45, LOC_LAT - 0.3, LOC_LON + 0.45, LOC_LAT + 0.3)
fl = gpd.read_parquet(ACQ / "network/flowlines.parquet").clip(w)
cat = gpd.read_parquet(ACQ / "network/catchments.parquet").clip(w)
wb = gpd.read_parquet(ACQ / "network/waterbodies.parquet").clip(w)
vaa = pd.read_parquet(ACQ / "network/vaa.parquet")
comid_col = fl.columns[fl.columns.str.lower() == "comid"][0]
name_col = next(c for c in fl.columns if c.lower() == "gnis_name")
fl["order"] = np.nan_to_num(vaa.set_index("comid").reindex(fl[comid_col].astype("int64")).streamorde.to_numpy(), nan=1)
fig, ax = plt.subplots(figsize=(8.5, 6))
cat.boundary.plot(ax=ax, color="#d9d9d9", linewidth=0.25)
wb.plot(ax=ax, color="#9ecae1", edgecolor="#6baed6", linewidth=0.4)
minor, major = fl[fl.order <= 2], fl[fl.order > 2]
minor.plot(ax=ax, color="#a6cee3", linewidth=0.35)
major.plot(ax=ax, color="#2171b5", linewidth=[0.5 + 0.5 * (o - 2) for o in major.order])
import matplotlib.patheffects as pe
for name in ("Iowa River", "Cedar River", "Clear Creek", "Old Mans Creek", "English River"):
    seg = fl[fl[name_col] == name]
    if not len(seg):
        continue
    p = seg.geometry.iloc[seg.geometry.length.argmax()].interpolate(0.5, normalized=True)
    ax.annotate(name, (p.x, p.y), fontsize=8, style="italic", color="#08306b",
                path_effects=[pe.withStroke(linewidth=2.5, foreground="white")])
ax.plot(LOC_LON, LOC_LAT, "r^", markersize=8)
ax.annotate("USGS 05455100", (LOC_LON, LOC_LAT), xytext=(4, -10), textcoords="offset points",
            fontsize=7, color="darkred", path_effects=[pe.withStroke(linewidth=2.5, foreground="white")])
ax.set_title("NHDPlus V2 -- Old Mans Creek window", fontsize=11)
ax.set_aspect(1 / np.cos(np.radians(LOC_LAT)))
ax.tick_params(labelsize=7)
fig.tight_layout(); fig.savefig(OUT / "sample_nhdplus.png", dpi=150); plt.close(fig)
print(f"flowlines {len(fl)}, catchments {len(cat)}, waterbodies {len(wb)} in window")

section("NLDI image")
paths = sorted((ACQ / "seed_basins").glob("*.parquet"))
g = pd.concat([gpd.read_parquet(p) for p in paths[:200]], ignore_index=True)
g = gpd.GeoDataFrame(g, geometry="geometry", crs=4326)
g["km2"] = g.to_crs(5070).area / 1e6
pick = g.iloc[(g.km2 - 1500).abs().argsort()[:1]]
fig, ax = plt.subplots(figsize=(6, 5))
pick.boundary.plot(ax=ax, color="#2171b5", linewidth=1.2)
pick.plot(ax=ax, color="#c6dbef", alpha=0.5)
ax.set_title(f"NLDI basin, seed {pick.site_uid.iloc[0]} -- {pick.km2.iloc[0]:,.0f} km2", fontsize=11)
ax.tick_params(labelsize=7)
fig.tight_layout(); fig.savefig(OUT / "sample_nldi.png", dpi=150); plt.close(fig)
print("basin:", pick.site_uid.iloc[0], f"{pick.km2.iloc[0]:,.0f} km2")

section("HydroSHEDS image")
import rasterio
from rasterio.windows import from_bounds
with rasterio.open(RAW / "basins/cache/flowdir_15s.tif") as r:
    win = from_bounds(LOC_LON - 0.45, LOC_LAT - 0.3, LOC_LON + 0.45, LOC_LAT + 0.3, r.transform)
    a = r.read(1, window=win)
codes = [1, 2, 4, 8, 16, 32, 64, 128]
lut = np.zeros(256, dtype=float); lut[:] = np.nan
for i, c in enumerate(codes):
    lut[c] = i
fig, ax = plt.subplots(figsize=(8, 5.4))
im = ax.imshow(lut[a.clip(0, 255)], cmap="twilight", interpolation="nearest")
cb = fig.colorbar(im, ax=ax, ticks=range(8), shrink=0.8)
cb.ax.set_yticklabels(["E", "SE", "S", "SW", "W", "NW", "N", "NE"])
ax.set_title("HydroSHEDS 15s drainage direction", fontsize=11)
ax.set_xticks([]); ax.set_yticks([])
fig.tight_layout(); fig.savefig(OUT / "sample_hydrosheds.png", dpi=150); plt.close(fig)
print("window px:", a.shape)

# ---- weather ---------------------------------------------------------------------------------------

section("gridMET image")
grid = pd.read_parquet(ACQ / "weather/grid.parquet", columns=["cell_id", "lon", "lat"])
q = pd.read_parquet(ACQ / "weather/gridmet_2008Q2.parquet", columns=["cell_id", "date", "pr"])
day = q[q.date == q.date.max()]
day = day.merge(grid, on="cell_id")
fig, ax = plt.subplots(figsize=(8.5, 5))
sc = ax.scatter(day.lon, day.lat, c=day.pr, s=0.4, cmap="Blues", vmin=0)
fig.colorbar(sc, ax=ax, label="precipitation (mm)", shrink=0.8)
ax.set_title(f"gridMET pr, {pd.Timestamp(day.date.iloc[0]).date()}", fontsize=11)
ax.set_aspect(1.3); ax.tick_params(labelsize=7)
fig.tight_layout(); fig.savefig(OUT / "sample_gridmet.png", dpi=150); plt.close(fig)
print(f"{len(day):,} cells on {pd.Timestamp(day.date.iloc[0]).date()}")

# ---- land surface ----------------------------------------------------------------------------------

section("CDL image")
cdl_path = f"/vsizip/{RAW}/crops/national.zip/national/2023_30m_cdls.tif"
try:
    src_ds = rasterio.open(cdl_path)
except Exception:
    cdl_path = f"/vsizip/{RAW}/crops/national.zip/national/2021_30m_cdls.tif"
    src_ds = rasterio.open(cdl_path)
from pyproj import Transformer
tr = Transformer.from_crs(4326, src_ds.crs, always_xy=True)
cx, cy = tr.transform(LOC_LON, LOC_LAT)
half = 12_000
win = from_bounds(cx - half, cy - half, cx + half, cy + half, src_ds.transform)
a = src_ds.read(1, window=win)
cmap = src_ds.colormap(1)
rgba = np.zeros((*a.shape, 4), dtype=np.uint8)
for code in np.unique(a):
    rgba[a == code] = cmap.get(int(code), (0, 0, 0, 255))
fig, ax = plt.subplots(figsize=(7, 7))
ax.imshow(rgba, interpolation="nearest")
year = Path(cdl_path).stem.split("_")[0]
ax.set_title(f"CDL {year} -- 24 km at Old Mans Creek", fontsize=11)
ax.set_xticks([]); ax.set_yticks([])
fig.tight_layout(); fig.savefig(OUT / "sample_cdl.png", dpi=150); plt.close(fig)
counts = pd.Series(a.ravel()).value_counts(normalize=True).head(4)
print("window top codes:", dict((int(k), f"{v:.0%}") for k, v in counts.items()))
src_ds.close()

section("Nutrients image")
surplus_tif = sorted((RAW / "surplus/national").glob("Surplus_N_*.tif"))[-1]
with rasterio.open(surplus_tif) as r:
    a = r.read(1, out_shape=(r.height // 12, r.width // 12)).astype(float)
    nod = r.nodata
if nod is not None:
    a[a == nod] = np.nan
fig, ax = plt.subplots(figsize=(8.5, 5))
im = ax.imshow(a, cmap="RdYlGn_r", vmin=np.nanpercentile(a, 2), vmax=np.nanpercentile(a, 98))
fig.colorbar(im, ax=ax, label="N surplus (kg/ha)", shrink=0.8)
ax.set_title(f"N surplus, {surplus_tif.stem.split('_')[-1]}", fontsize=11)
ax.set_xticks([]); ax.set_yticks([])
fig.tight_layout(); fig.savefig(OUT / "sample_surplus.png", dpi=150); plt.close(fig)
print("national grid:", a.shape, "at 3 km after downsample")

section("AgTile image")
with rasterio.open(RAW / "agtile/clipped/agtile_clip.tif") as r:
    a = r.read(1, out_shape=(r.height // 8, r.width // 8)).astype(float)
    nod = r.nodata
if nod is not None:
    a[a == nod] = np.nan
fig, ax = plt.subplots(figsize=(8.5, 5))
im = ax.imshow(a, cmap="PuBuGn", interpolation="nearest")
fig.colorbar(im, ax=ax, label="tile-drained", shrink=0.8)
ax.set_title("AgTile-US tile-drained cropland", fontsize=11)
ax.set_xticks([]); ax.set_yticks([])
fig.tight_layout(); fig.savefig(OUT / "sample_agtile.png", dpi=150); plt.close(fig)
print("clip shape:", a.shape)

# ---- attributes and structures ---------------------------------------------------------------------

section("StreamCat")
d = pd.read_parquet(ACQ / "attributes/streamcat.parquet")
comid_col = next(c for c in d.columns if "comid" in c.lower())
cols = [comid_col, "pctagdrainagecat", "nsurpcat", "rockncat", "pctimpslphigh2019cat"]
t = d[cols].dropna().head(4).copy()
t[comid_col] = t[comid_col].astype("int64").astype(str)
print(md_table(t))

section("NID")
d = pd.read_parquet(ACQ / "attributes/nid.parquet")
keep = [c for c in ("name", "riverName", "yearCompleted", "nidHeight", "nidStorage", "primaryPurposeId") if c in d.columns]
d["nidStorage"] = pd.to_numeric(d.nidStorage, errors="coerce")
d["yearCompleted"] = pd.to_numeric(d.yearCompleted, errors="coerce").astype("Int64")
big = d.sort_values("nidStorage", ascending=False)
print(md_table(big[keep].head(4)))

section("ICIS outfalls")
d = pd.read_parquet(ACQ / "facilities/outfalls.parquet")
ia = d[(d.state == "IA") & d.facility_name.str.contains("IOWA CITY", na=False)]
pick = ia if len(ia) else d[d.state == "IA"]
print(md_table(pick[["npdes_id", "facility_name", "facility_type", "state", "lat", "lon"]].head(4), "{:.4f}"))

section("DMR")
d = pd.read_parquet(ACQ / "facilities/dmr_n/fy2010.parquet")
keep = [c for c in ("EXTERNAL_PERMIT_NMBR", "PERM_FEATURE_NMBR", "PARAMETER_DESC", "DMR_VALUE_NMBR", "DMR_UNIT_DESC", "MONITORING_PERIOD_END_DATE") if c in d.columns]
sub = d[d.EXTERNAL_PERMIT_NMBR.str.startswith("IA", na=False)]
sub = sub if len(sub) else d
vcol = next((c for c in keep if "VALUE" in c), None)
if vcol:
    sub = sub[pd.to_numeric(sub[vcol], errors="coerce").notna()]
print(md_table(sub[keep].head(4)))

section("CWNS")
d = pd.read_parquet(ACQ / "facilities/cwns_potw.parquet")
sub = d[(d.state == "IA") & d.design_flow_mgd.notna() & d.treatment_level.notna()]
print(md_table(sub.sort_values("population_2022", ascending=False)[["npdes_id", "facility_name", "treatment_level", "design_flow_mgd", "population_2022"]].head(4)))

section("HydroWASTE")
d = pd.read_csv(REPO / "reference/water-treatment-facilities/HydroWASTE_v10.csv", encoding="latin-1")
sub = d[(d.CNTRY_ISO == "USA") & d.WWTP_NAME.str.contains(r"\bIOWA\b", case=False, na=False, regex=True)]
print(md_table(sub[["WASTE_ID", "WWTP_NAME", "LAT_OUT", "LON_OUT", "POP_SERVED", "WASTE_DIS"]].head(4), "{:.4f}"))

print("\nall samples done ->", OUT)
