# Data source manifest for the new pipeline

Written 2026-08-12. Replaces `new-data-report.md`. This is a work order: what the pipeline downloads, from where, into what, at which stage.

## 1. How to read this

**Inclusion rule, both directions.** A source with a **citable reason** — a citation, a measured number, or a stated mechanism — is collected. Whether it *helps* is tested later, during feature engineering, with tests built for those specific features. A source with **no stated reason** to suspect it aids inference is not collected at all; nobody should have to write a test for every possible feature.

**Verdicts.** `COLLECT` · `BLOCKED` (access unresolved after real effort — names what is unknown and the next step) · `EXCLUDED` (one of four grounds: licence prohibits modelling use · provably redundant · known-corrupt · no stated mechanism).

Not grounds for exclusion: failed as a feature under the old harness · sparse coverage · similar to another source. Where two sources overlap, take both and note the overlap.

**Justification is per block, never per column.** A theme with a mechanism is taken whole; hand-picking columns inside it is effect-size speculation. A block with no mechanism is excluded in one line.

**Verification tiers** are confidence labels on my claims, not quality scores, and they do not gate anything: V0 named · V1 URL and licence read · V2 request returned 200 · V3 payload parsed · V4 joined to our keys · V5 spot-truthed. Failed requests are logged with status codes. Numbers marked ° are not traceable to a command.

Scripts producing every V2+ number are in `experiments/research/conus-data-sources/`.

---

## 2. Scope — one declaration everything inherits

**Every source is collected inside a declared scope = (region) × (years).** This is not a raster-clipping detail. We do not collect all weather for the United States; we collect weather inside the relevant region, for the years we model. Sites, gauges, COMID attributes, grab samples and modelled discharge all inherit it.

This does not contradict *download everything, filter later*. That rule means do not filter by today's **modelling hypothesis**. Scope is declared once, in the open. Role and quality remain modelling-time queries.

### 2.1 The region is a pipeline step, not a constant

Programmatic default, manual override. Two stages, because the precise answer depends on basins and basins depend on an extent:

**Stage A — bootstrap boxes (liberal).** Bound the flowline and DEM download so basins can be delineated at all. Default: cluster every in-situ nitrate site (all three pcodes) and take generously buffered bounding boxes. Measured with DBSCAN at 150 km / min_samples 3 / 0.5° buffer over 401 points (235 USGS sensors + 171 cohort candidates) — **12 clusters, 2.13M km², 26% of CONUS**, with the primary corn-belt box carrying 270 of 401 points:

| cluster | sites | area | km²/site | box |
|---|---|---|---|---|
| **primary (corn belt)** | **270** | **1.222M km²** | 4,527 | `[-98.7, 36.68, -82.68, 44.86]` |
| mid-Atlantic / Chesapeake | 49 | 287k | 5,855 | `[-79.22, 36.73, -73.72, 42.22]` |
| Florida | 20 | 318k | 15,882 | `[-85.75, 25.83, -79.91, 30.84]` |
| CA Central Valley / Delta | 17 | 19k | **1,113** | `[-122.27, 37.44, -121.0, 38.98]` |
| lower Mississippi | 10 | 105k | 10,494 | `[-91.92, 29.19, -89.86, 34.05]` |
| Columbia basin | 6 | 25k | 4,123 | `[-120.97, 45.71, -118.78, 47.04]` |

Seven further clusters of 3–4 sites each; 10 points unclustered. Full list in `data/region_boxes.csv`.

**The primary box is 1.18× the current `covariate_bbox` (1.222M vs 1.035M km²) and contains 115 USGS nitrate sensors against 72 today** — +60% labels for +18% area, and it contains all 171 cohort candidates. That is the cheapest expansion available.

**Stage B — derived relevant area.** Once basins exist, the collection extent becomes the union of

- every nitrate site's basin, **and**
- the basin of every **valid pin placement within flow distance _D_** of a site.

A valid pin is a reach passing the deployment criterion (stream order ≥ *k*). This ties the download extent to the deployment scenario rather than to rectangles, and it is strictly tighter than Stage A in the headwater direction while extending along mainstems where a pin would actually sit.

Parameters — `D` (flow distance), `k` (minimum stream order), and the Stage-A buffer — are config with programmatic defaults and a manual override that replaces either stage with hand-specified geometry.

**A caution for Stage B.** `_make_map_overlays.download_flowlines` filters on `"streamorde"` while pynhd returns `StreamOrde`, so `min_stream_order = 3` has never fired. Every basin on disk was delineated under a different rule than the config claims. Stage B's valid-pin test uses the same column, so fix it before deriving the region or the answer inherits the bug.

### 2.2 Years

| class | window | why |
|---|---|---|
| gridded dynamic (gridMET, CDL, NWM) | **2008–2026** | where all the volume is. Matches the current build; predates the sensors, which gives antecedent-condition history |
| annual per-COMID tables (StreamCat `n_*`, Wieczorek vintages) | **full span** | 31 values per COMID is trivial in size, and legacy-N accumulation needs the history |
| static | none | — |

Justified against record spans: in-box USGS continuous nitrate has median span **5.03 yr**, discharge **22.69 yr**; IWQIS starts 2012.

### 2.3 In-scope counts, measured

CONUS totals are context and upper bounds; these are what the pipeline collects (`06_scoped_census.py`, primary box, V3):

| family | in-box sites | median span | | family | in-box sites |
|---|---|---|---|---|---|
| **nitrate (labels)** | **140** | 5.03 yr | | groundwater | 1,719 |
| stage | 4,993 | 23.82 yr | | water_temp | 1,100 |
| discharge | 4,860 | 22.69 yr | | precip_site | 915 |
| spec_cond | 573 | 4.05 yr | | diss_oxy | 454 |
| ph | 322 | 5.29 yr | | turbidity | 311 |
| soil_temp | 96 | 1.57 yr | | soil_moisture | 89 |

**140 nitrate labels against 4,860 discharge nodes — 35×.** Against the current 116-site cohort the label count rises ~20% inside the primary box alone, before state programs.

**Discrete nitrate is 147× the continuous count.** WQP stream stations across the twelve corn-belt states: **20,520** (IA 3,999 · IN 3,140 · MN 2,272 · KS 1,889 · MI 1,826 · MO 1,237 · WI 1,227 · KY 1,262 · IL 1,064 · SD 952 · OH 911 · NE 741). Counts from response headers, V2.

---

## 3. Footprint — and it does not fit under today's policy

Measured: **129 GB free; `src/data` is 103 GB.** Scaling is not uniform — most of the current footprint is national and does not grow with the region:

| | GB | scales with scope? |
|---|---|---|
| CDL national (2008–2025, ~3 GB/yr) | 50.0 | **no — already national** |
| gTREND surplus national | 4.7 | no |
| gTREND fertiliser national | 0.4 | no |
| AgTile national | 0.1 | no |
| gridMET raw NetCDF cache | 26.0 | yes |
| CDL clipped | 3.4 | yes |
| interim per-cell tables | 5.5 | yes |

### The verdict

| policy | current bbox | primary box | + mid-Atl | + CA + Columbia |
|---|---|---|---|---|
| **retain every download (today)** | 128.3 GB *(1 GB spare)* | **141.5 — DOES NOT FIT** | 161.8 ✗ | 164.8 ✗ |
| **clip → aggregate → discard caches** | 52.3 | **60.8 GB (68 spare)** | 73.8 (55) | 75.8 (53) |
| **discard caches + NWM at site COMIDs** | 24.3 | **27.7 GB (101 spare)** | 33.0 (96) | 33.8 (95) |

**Today's policy is already at the edge — 1 GB spare — and any region expansion breaks it.** The unlock is that `CDL national` (50 GB) and `gridMET_raw` (26 GB, a NetCDF cache) are **regenerable download caches**; CLAUDE.md already treats raw rasters that way. Clip, write the per-cell table, delete the source. That frees ~76 GB immediately and makes even a four-box scope comfortable.

Per source under the recommended policy (primary box, NWM at site COMIDs) — total **27.7 GB**:

Wieczorek attrs 10.91 · interim tables 6.49 · gTREND surplus 4.70 · CDL clipped 4.01 · StreamCat 0.84 · gTREND fertiliser 0.43 · VAA + flowlines 0.17 · AgTile 0.12 · NWM (sites) 0.03 · CDL national 0 · gridMET cache 0.

Arithmetic and assumptions in `05_scope_and_footprint.py`; COMID count scaled from the measured 2,776,734 CONUS reaches by area.

---

## 4. Already on disk — collect these first, zero download

### 4.1 The gTREND fertiliser rasters

`src/data/raw/fertilizer/tif/Fertilizer_Ag_{2000..2017}.tif`, 431 MB, on disk since 2024-01-09, **referenced by zero lines of code**.

Identified by grid fingerprint (V3): identical CRS (EPSG:5070), raster size (18458 × 11613), 250 m resolution, bounds, and `MATLAB 9.14, Mapping Toolbox 5.5` producer tag to the `Surplus_N_*.tif` rasters in production. The discriminator is sign — fertiliser is bounded at 0 (median 0, p99 112–125), surplus reaches −242.8, because a gross input cannot be negative but a net balance can.

**Citable reason:** gTREND-Nitrogen ([doi:10.1038/s41597-026-06576-x](https://doi.org/10.1038/s41597-026-06576-x)) decomposes the balance into fertiliser, manure, deposition, biological fixation, crop uptake and human waste. Surplus is the *net*; two basins with identical surplus can have very different application, and leaching responds to gross application. Not a re-slice of a held raster.

**Cost:** `src/build/util/build_source.py` already clips this exact geometry; generalise its `GLOB` from `Surplus_N_*.tif`.

**Follow-on, now confirmed:** the release ships a **manure** layer (V1, from the paper's component list). It was never downloaded and completes the input decomposition.

### 4.2 Covariates fetched then discarded

`src/build/water/_paths.py:69-83` selects `['datetime','nitrate_con']` and drops the rest. Scored by days overlapping that site's own nitrate record (V3, `01_held_but_discarded.py`):

| source | column | sites | ≥1 yr | median yr | overlap days |
|---|---|---|---|---|---|
| IWQIS | **`m_exc`** | 110 | 84 | 2.85 | **126,071** |
| IWQIS | **`r_exc`** | 110 | 84 | 2.85 | **125,852** |
| USGS | stage | 32 | 30 | 3.39 | 62,244 |
| USGS | discharge | 34 | 31 | 3.27 | 62,046 |
| USGS | water_temp | 29 | 25 | 3.39 | 40,741 |
| IWQIS | turbidity (5 cols) | 94 | 28 | 0.01 | 35,057 |
| IWQIS | ph / spec_cond / diss_oxy | 50–55 | 18–19 | 0.43–0.63 | ~23,000 each |
| IWQIS | chloro_con | 36 | 5 | 0.12 | 5,776 |
| IWQIS | phosphate_con | 4 | **0** | 0.07 | 197 |

`r_exc` / `m_exc` are reference and measurement extinction from the optical nitrate sensor — **a direct instrument-validity signal at 110 of ~126 IWQIS sites over 126,071 co-observed days**, and the largest discarded block in the project.

Correction to the prior survey: IWQIS turbidity was reported as "on disk at 63 sites", which overstates usability — 94 sites offer the column but median nitrate overlap is **0.01 yr** and only 28 clear a year.

### 4.3 Other holdings

`nass_quickstats` key in `src/build/api-keys.toml`, used by zero lines (V3) — QuickStats returns HTTP 401 without it, so the key is the access path. · `src/data/raw/basins/cache/rivers.gpkg`, unreferenced. · 15 orphan water parquets for non-contract sites.

---

## 5. Access specs, by pipeline stage

Stages refer to `README_DATA_PIPELINE.md`. Every entry carries its spatial and temporal filter.

### Stage 1 — site discovery

| | |
|---|---|
| **USGS continuous nitrate** | `dataretrieval.waterdata.get_time_series_metadata(parameter_code=pc, bbox=BOX)` for `pc ∈ {99133, 99137, 00630}`, `hasDataTypeCd=iv`. Auth: PAT from `api-keys.toml`. **Excludes `83554`** — a load, not a concentration. Spatial: bootstrap boxes. Temporal: all. → 140 in primary box |
| **Citable reason** | `99133` alone returns **zero** Nebraska sites; `00630` returns five (Platte, Elkhorn, Bazile). A single-pcode query deletes networks rather than thinning them. V3 |
| **IWQIS** | bulk chunked CSV via contact, as today. Spatial: Iowa. |
| **Water Quality Portal** | `https://www.waterqualitydata.us/data/Station/search?statecode=US:{fips}&characteristicName=Nitrate&siteType=Stream&mimeType=csv&zip=no`. **Needs a >30 s timeout** — 1.4 MB in 27.6 s for one state; `bBox` form returned HTTP 500, use `statecode`. Spatial: states intersecting the boxes. → 20,520 stations |
| **Citable reason** | 147× the continuous sensor count; labels for sensor+ and nodes for the graph. **Unit trap:** the same USGS measurement appears as `mg/l as N` and `mg/l asNO3`, differing by 4.4269 — deduplicate on the harmonised value or the median reads 24.05 instead of 5.44 |
| **State programs** | **Largely already in WQP** (V3): a Minnesota query returns 2,272 stations of which **1,264 are "Minnesota Pollution Control Agency – Ambient Surface Water"** via STORET. One WQP ingest covers the state ambient networks; no per-state ingest needed for discrete data |
| **MPCA continuous nitrate network** | Separate — 21 sensors installed 2025, 35 planned, ice-free months only, series on MN DNR Cooperative Stream Gaging (`dnr.state.mn.us/waters/csg/site.html?id=`). ~1 yr each: **graph nodes, not training labels** |
| **Heidelberg NCWQR HTLP** | `https://ncwqr-data.org/` + Zenodo `10.5281/zenodo.6606949` (Zenodo API 403s to scripted clients; use the portal). ~20 stations since 1974, 450–500 samples/station/year — the strongest non-Iowa nitrate record |
| **CAMELS-Chem** | HydroShare `841f5e85085c423f889ac809c1bed4ac`. 516 catchments, 18 constituents incl. NO₃, 1980–2018. Doubles as a **paired comparator cohort** |

### Stage 3 — flowlines and network

| | |
|---|---|
| **NHDPlus flowlines** | `pynhd` NHD MR by geometry, as today. **Fix the `streamorde` vs `StreamOrde` filter first** — see §2.1 |
| **NHDPlus VAA** | **`pynhd.nhdplus_vaa()`** — a library call, not a URL. The HydroShare content path is a dead end (301 → 405; `hsapi` download and `django_irods` both 302 into auth). The resource holds `nhdplusVAA.parquet` (257.7 MB) and `.fst` (214.4 MB) |
| **Citable reason** | `totma` / `pathtimema` are reach travel time — the physical quantity our distance buckets approximate, and the natural edge weight for both donor features and a graph model |

### Stage 8 — COMID attributes

| | |
|---|---|
| **StreamCat** | `https://api.epa.gov/StreamCat/streams/metrics` POST by COMID; catalogue at `.../variable_info?name=all` (HTTP 200, 75 KB, 0.8 s, no key). Spatial: in-scope COMIDs. Temporal: full span. **Take all 168 metrics**, minus `sw_flux` |
| **Citable reason** | 76 metrics are time-varying, including nitrogen inputs annual 1987–2017 with **farm fertiliser (`n_ff_`) and livestock waste (`n_lw_`, `n_lwr_`) as separate series**, legacy N (`n_leg_`), and land cover on 8 NLCD vintages 2001–2019. **This supersedes the Sekellick & Sherr recommendation** in `aug-01.md` §4, whose entire justification was separating manure from fertiliser |
| **Trap** | `ws`-suffixed values are raw upstream sums — normalise by `CumAreaKm2` or they encode basin size. `[AOI]`/`[Year]` in `METRIC_NAME` are templates expanded from the `AOI`/`YEAR` columns |
| **Wieczorek NHDPlusV2** | **`pynhd.nhdplus_attrs_s3()`** — 14,139 attributes in 3.1 s. `pynhd.nhdplus_attrs()`, the ScienceBase route, **times out**. `_make_comid_attrs.py:57-59` wires **3**. Catalogue cached at `data/wieczorek_cat_attrs.csv` |
| **Take** | Soils (108) · **Best Management Practices (45)** · Hydrologic Modifications (144) · Chemical (1,323) · Land Cover (3,303) · Geology (573) · Topographic (88) · Climate (345) — all three accumulation forms |
| **Exclude** | **Climate Water Balance (7,668)** — monthly PPT/PET accumulations 1945–2015, redundant against gridMET which we hold daily at higher fidelity. Plus `CAT_TNLOAD_2012` / `CAT_TNLOSS_2012` (§7) |
| **Citable reason (BMP)** | Pandit 2025: *"the continental-scale data sets used in this study … do not have estimates of other types of BMPs."* They exist, COMID-keyed, percent of catchment 2012: `CPRAC16` cover crop · `CPRAC13` no-till · `CPRAC7` conservation tillage · `CPRAC10` conventional tillage · `CPRAC4` **tile drainage** · `CPRAC1` ditch drainage · `CPRAC19` easement · `PFARM12` CRP fraction. `CPRAC4` is a third independent tile estimate at a 2012 vintage against AgTile's 2017 and `TILES92`'s 1992, and tile density is the top predictor in Pandit's own SHAP ranking |
| **Citable reason (Soils)** | `CAT_WTDEP` depth to water table and `CAT_PERMAVE` permeability are two of the five soil variables Saha explicitly lists as wanted-but-not-obtained; also `CLAYAVE`, `SANDAVE`, `KFACT`, `OM`, `AWCAVE`, hydrologic soil groups. COMID-keyed, so no delineation or raster clip |
| **National Inventory of Dams** | `https://nid.sec.usace.army.mil/api/nation/csv` — HTTP 200, 67.3 MB, 18.0 s, public domain. Snap to COMID, filter to scope |
| **Citable reason** | Pool area + drainage area are the complete input set for the Iowa-calibrated wetland removal equation, `% removed = 103 × HLR^(−0.33)` (Crumpton et al., R² = 0.69) — the only route to a feature expressing engineered nitrate removal |

### Stage 9 — modelled hydrology

| | |
|---|---|
| **NWM v3.0 retrospective** | `https://noaa-nwm-retrospective-3-0-pds.s3.amazonaws.com/CONUS/zarr/chrtout.zarr/` — AWS Open Data, no auth. `feature_id` declares `comment: "NHDPlusv2 ComIDs within CONUS"`, `cf_role: timeseries_id`. 2,776,734 reaches × 385,704 hourly steps, 1979-02 → 2023-02. Variables `streamflow` (m³/s), `velocity`, `q_lateral`, `qSfcLatRunoff`, `qBucket`, `qBtmVertRunoff`. int32, `scale_factor` 0.01, `missing_value` −999900. Carries a **`gage_id` array — a free COMID ↔ USGS crosswalk**. Siblings: `ldasout` (land surface incl. soil moisture), `gwout`, `rtout`, `lakeout`, `forcing/` |
| **Citable reason** | Every comparable model — Xia, Jain, Gorski, SPARROW — assumes a discharge gauge at the prediction location; our feature set has no discharge at all. NWM makes discharge computable at an ungauged pin. Saha measured the substitution: observed Q → NSE 0.77, simulated Q → **0.76**, precipitation alone 0.67 |
| **Extraction spec** | **Measured, not assumed** (`07_nwm_chunks.py`): our 162 site COMIDs all match, and occupy **22 of 93 feature-chunks (24%)**. A narrow pull reads 1,018 GB against 4,305 GB broad — **4.2× cheaper**. So start at site COMIDs. Store **daily aggregates** (mean/min/max per COMID-day; our target is daily, so hourly is 24× waste) in a **COMID-partitioned store keyed by feature-chunk index**, so widening for deployment is an append rather than a re-derivation. Spatial: in-scope COMIDs. Temporal: 2008–2026 |
| **Correction** | An earlier draft asserted our sites touch all 93 chunks and concluded "extract broadly". That came from `min(93, 324)`, a placeholder. It was wrong and would have cost 4× |

### Stage 11 — rasters

gridMET (in production; re-base to the region, **delete the NetCDF cache after the interim table is written**) · CDL (national tifs held; clip then delete, saving 50 GB) · gTREND surplus · **gTREND fertiliser** (§4.1) · **gTREND manure** (new) · AgTile.

**Drop `precip_in_1d`.** IEM's footprint is Iowa-only, so the column is NaN elsewhere and XGBoost splits on NaN natively — the model can learn "am I in Iowa" and attach state-specific behaviour to it. No mechanism outside Iowa; excluded.

### Also collect

**NWIS non-nitrate continuous series** — the node population. Discharge 4,860 · stage 4,993 · groundwater 1,719 · water_temp 1,100 · precip 915 · spec_cond 573 · diss_oxy 454 · pH 322 · turbidity 311 in the primary box. *Citable reason:* a graph model needs nodes, and 4,860 discharge gauges against 140 nitrate labels is 35×; the Delaware RGCN that worked had 2.5×. **Query every pcode in a family** — groundwater loses 1,120 CONUS sites to a single-code query, water_temp 294, turbidity 102.

**NASS Crop Progress** via QuickStats (key held, currently unused; 401 without it). Weekly state-level planting/emergence/harvest. *Citable reason:* Saha states fertiliser *timing* and crop phenology could not be obtained; this is the only weekly management signal available at no cost.

---

## 6. Metadata contract

What each source contributes to the uniform tables. Roles (`graph_node`, `train_label`, `claim_set`) are queries over these, so a source's value is partly whether it makes a role expressible.

**Site table** — identity (`site_uid`, `source`, `agency`, `physical_gauge_group`, `is_group_canonical`, `merge_rule`, `merge_seam_date`) · location (`lat`, `lon`, `comid`, `snap_dist_m`, `stream_order`, `StreamLeve`, `huc12`) · basin (`basin_area_km2`, `cell_coverage`, `d8_vs_polygon_area_ratio`) · record (`first_day`, `last_day`, `n_nitrate_days`, `longest_gap_d`, `regime` ∈ {continuous, grab}) · type (`site_type`, `site_type_source`) · **co-location** (`nearest_discharge_gauge_m`, `nearest_nitrate_sensor_m`, `nearest_wqp_station_m`) · QA (`pcodes`, `reported_units`, `detection_limit`).

**COMID table** — StreamCat metrics · Wieczorek attributes by theme · VAA (`totma`, `pathtimema`, `tocomid`, order) · NID-derived removal (`hlr`, `removal_frac`, `treated_area_frac`) · NWM daily aggregates.

**Grid-cell table** — gridMET, CDL, surplus, **fertiliser**, **manure**, AgTile, keyed on `global_node_id` **with a provenance stamp**; joins refuse on mismatch.

---

## 7. Provenance recorded at ingest

Not a filter. These are facts that **cannot be recovered later** — you cannot reconstruct "how far was the nearest gauge" or "was this value fitted on observations" from a parquet six months on — and they are invisible to *any* holdout scheme, present or future, because they live in provenance rather than in row or site structure. Record them as columns; exclusion is a modelling-time predicate.

| flag | applies to | why it survives no split |
|---|---|---|
| `is_fitted_on_observations` | SPARROW fitted loads, StreamCat `sw_flux`, Wieczorek `TNLOAD`/`TNLOSS`, competitor predictions | contamination happened during *their* calibration, before the data reached us. 20 of 116 sites sit within 100 m of a SPARROW TN calibration station, one at 0 m |
| `nearest_discharge_gauge_m` | every site | 36 IWQIS sites are within 200 m of a USGS gauge. Holding out sites, time, basins or families does not help — the feature at the test site is a near-copy of its own state |
| `is_modelled` | NWM and any model output | errors correlate with basin characteristics rather than being white noise |
| `source_organisation` | WQP | Iowa volunteer means run 3.5–4.1 mg/L below agency means and **anti-correlate at −0.42** once both sides have ≥50 samples |

---

## 8. Blocked

| source | what is unknown | next step |
|---|---|---|
| **EPA ECHO / DMR nitrogen** | the official NPDES → COMID crosswalk. Endpoints return 200; the join does not exist yet | search EPA WATERS / Watershed Index Online / Pollutant Loading Tool **before** building one. StreamCat `npdesdens`, `wwtp*dens` and `n_usgsww_` may cover most of the signal |
| **CWNS treatment-upgrade panel** | whether the HydroShare mirror still serves all vintages | one request; 79 Iowa facilities moved Secondary → Advanced 2008–2022° |
| **gNATSGO / gSSURGO** | current distribution URL — Box 404s, NRCS landing page refused connection | relocate. Lower priority: the Wieczorek Soils theme is COMID-keyed and covers most of the need |
| **CropScape `GetCDLFile`** | direct calls time out at 40 s; the repo's own client works | treat as slow, not broken; national tifs are already held |
| **Iowa CREP wetland geometry** | not public | records request to IDALS; email ISU Wetland Research Lab (§10) |

---

## 9. Excluded

| source | ground |
|---|---|
| **IWAND** | licence — CC BY-NC-ND blocks modelling use |
| **Wieczorek Climate Water Balance (7,668 attrs)** | redundant — monthly PPT/PET accumulations against daily gridMET |
| **`CAT_TILES_Early90s`** | known-corrupt — writes tile value 0.0 where `CAT_NODATA == 100`, true for ~88% of CONUS |
| **StreamCat `sw_flux`; Wieczorek `TNLOAD`/`TNLOSS`** | fitted N at the reach we predict at — collected only if flagged; not in the recommended pull |
| **`precip_in_1d` (IEM)** | no mechanism outside Iowa, and a covert state indicator via NaN |
| **Load pcodes** (`83554`, `80155`, `91050`) | redundant — a load is concentration × discharge, so it re-encodes two variables we hold |
| **ACPF** | no mechanism — maps where practices *could* go, not where they are |
| **Iowa BMP crest lines** | no mechanism as shipped — 112,689 pond dams as crest lines with no area field, so they cannot feed the removal equation |
| **SPARROW fitted-load columns** | fitted on our target. Structural columns (`sh_*`, `DEL_FRAC`, `rchdecay*`) remain collectable |

Sparse sources are **not** excluded: `fdom` (132 CONUS sites), `phosphate_con` (4 sites, 0 with ≥1 yr overlap) and the thin chemistry families are collected as nodes and diagnostics. They cost nothing — they are already on disk or one query away.

---

## 10. Requests and long-lead items

**Iowa CREP wetland locations and areas** — no public GIS layer. Records request to IDALS Division of Soil Conservation and Water Quality (state-held, not blocked by §1619); email Bill Crumpton's ISU Wetland Research Lab, who hold the per-wetland pool and watershed areas behind the removal equation. · **IWQIS discharge re-export** — `measures.csv` declares discharge, load and yield at 38 sites but none of the three columns exist in the delivered CSV. · **NASS QuickStats key** — confirm the held key authenticates.

---

## 11. Evidence ledger

**V3 — parsed, reproducible from a script here:** the gTREND fertiliser identification · held-but-discarded table · CONUS and in-scope censuses, and the Nebraska result · node census and pcode undercounts · NWM zarr metadata, span, chunking, and the 22-of-93 chunk measurement · StreamCat's 168-metric catalogue · Wieczorek's 14,139 attributes and theme breakdown · region boxes and footprint arithmetic · WQP state station counts (V2, response headers).

**V2 — reached, 200:** NID · NADP TDep grids (`gaftp.epa.gov/castnet/tdep/CURRENT_grids/`, `bc_dw-YYYY.zip`, 2000–2022) · Heidelberg portal · CAMELS-Chem · MPCA and MN DNR CSG · NASA CMR SPL4SMGP · POLARIS · OpenET · SNODAS · EPA WATERS · NASS QuickStats (401).

**Resolved from first-pass failures:** every 404 in the first pass was a guessed landing-page URL. Wieczorek → `pynhd.nhdplus_attrs_s3()` · VAA → `pynhd.nhdplus_vaa()` · TDep → the gaftp path · Heidelberg → `ncwqr-data.org` · CAMELS-Chem → a corrected HydroShare id · MPCA → the PCA/DNR pages · SMAP → NASA CMR · WQP → a longer timeout. **Two of the three genuinely stuck sources were fixed by a library already in the dependency tree.**

**ScienceBase is a real outage**, not a client problem: HTTP 000 at 25 s, 000 at 45 s, 502 at 30 s across ~90 minutes on two paths, and `pynhd.nhdplus_attrs()` (which routes through it) times out while the S3 sibling returns in 3.1 s. Prefer S3 in the build.

**V0, carried from the July report on its authority, not re-measured:** the StreamCat `ws`-sum gotcha · CWNS mirror · ScienceBase-ignores-Range · SPARROW calibration-station counts · the AgTile mask-vintage trap · the volunteer −0.42 anti-correlation.

**Interpretations, not measurements:** the gTREND fertiliser identification is inferred from grid fingerprint and value sign, not a metadata record — confirm units before citing kg N/ha/yr. The claim that NWM makes discharge pin-legal rests on Saha's substitution result in a different setting; the cheap check is a KGE spot-truth against the observed discharge already on disk at 34 sites.

**Known open:** connected node coverage on the reach graph (straight-line figures are upper bounds; needs the graph, then minutes) · how many unlabelled reaches sit between labelled pairs · whether NWM assimilates USGS gauges · whether MPCA's *continuous* series reach WQP (its discrete ambient network does).
