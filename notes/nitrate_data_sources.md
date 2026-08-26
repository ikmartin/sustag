# Nitrate Virtual-Sensor Data Source Inventory

Scope: Iowa surface-water nitrate, GNN virtual-sensor / inductive spatiotemporal kriging.
Compiled 2026-08-25.

**Status key:** `HAVE` = already in the pipeline · `ADD` = recommended addition · `EVAL` = worth evaluating, lower priority
**Feature type:** `STATIC` = time-invariant node attribute · `DYNAMIC` = time series feature · `TARGET` = observation / label

---

## 0. Already in the stack

| Source | Type | Notes |
|---|---|---|
| [IWQIS](https://iwqis.iowawis.org/) continuous nitrate | TARGET | Primary label source. Continuous optical NO₃. |
| [USGS NWIS](https://waterservices.usgs.gov/) via [`dataretrieval`](https://github.com/DOI-USGS/dataretrieval-python) | TARGET + DYNAMIC | pcode 99133 confirmed at 27 IA sites. Tier 1/2 covariate pcodes already scoped. |
| [NHDPlus V2](https://www.epa.gov/waterdata/nhdplus-national-hydrography-dataset-plus) | STATIC | Flowline topology, catchments, COMID. Spine of the whole system. |
| [NLDI](https://api.water.usgs.gov/nldi/) | — | Upstream basin delineation. |
| [USDA CDL](https://nassgeodata.gmu.edu/CropScape/) | STATIC/annual | Known limits: no cover crops, no rotation history from single year. |
| [Stage IV precip](https://mesonet.agron.iastate.edu/archive/) (via IEM) | DYNAMIC | HRAP polar stereographic ~4 km. Local-day accumulation. |
| [SSURGO / SDA](https://sdmdataaccess.sc.egov.usda.gov/) | STATIC | `chorizon` ksat_r, claytotal_r, om_r, etc. `cocropyld` empty for IA. |
| [NASS QuickStats](https://quickstats.nass.usda.gov/) | STATIC/5-yr | County ag census. |
| [Falcone 2021 NANI](https://doi.org/10.5066/P9VSQN3C) | STATIC | Net anthropogenic N input. |
| [EPA SDWIS](https://www.epa.gov/ground-water-and-drinking-water/safe-drinking-water-information-system-sdwis-federal-reporting) | — | Drinking-water side only. |
| [OpenET](https://openetdata.org/) | DYNAMIC | *In progress* — polygon timeseries API 422; print response body before raising. |

---

## 1. Reach-level model output (the SPARROW category)

The defining property: **a value exists at every COMID, including ungauged ones.** This is the only class of data that can populate a virtual pin.

### 1.1 NOAA National Water Model retrospective — `ADD`, highest priority

| | |
|---|---|
| **What** | Hourly simulated streamflow + 3-hourly land surface state (soil moisture, snowpack, runoff) |
| **Coverage** | v3.0: Feb 1979 – Jan 2023 (44 yr). v2.1: 1979–2020. v2.0: 1993–2018 |
| **Spatial** | ~2.73M NHDPlusV2 reaches; `feature_id` = COMID |
| **Format** | Zarr (channel output) + NetCDF, on AWS S3 |
| **Access** | [AWS Open Data registry](https://registry.opendata.aws/nwm-archive/) · [BDP docs](https://github.com/NOAA-Big-Data-Program/bdp-data-docs/blob/main/nwm/README.md) · [RENCI/HydroShare feature-indexed subsets (v1.2, v2.0)](https://www.hydroshare.org/resource/89b0952512dd4b378dc5be8d2093310f/) |
| **Ref** | [Johnson et al. 2023, *Sci Data*](https://www.nature.com/articles/s41597-023-02316-7) |
| **Feature type** | DYNAMIC |

**Why it matters:** discharge is the strongest single covariate for nitrate concentration and you currently only have it at gauges. NWM gives it everywhere.

**No leakage:** the retrospective runs with *no streamflow data assimilation*, so unlike SPARROW there is no pathway from your gauge observations into the reach values.

**Caveat:** NWM does not represent tile drainage. Corn Belt hydrograph timing and low-flow skill are poor. Fine as a feature; not usable as ground truth.

### 1.2 USGS SPARROW — `ADD`

| | |
|---|---|
| **What** | Seasonal source-specific TN and TP loads, dynamic version |
| **Coverage** | Dec 1999 – Nov 2020, 84 seasonal timesteps (DJF/MAM/JJA/SON) |
| **Spatial** | 263,494 reaches + 77,220 HUC12 summaries |
| **Access** | [Data release DOI 10.5066/P9IJLEIY](https://doi.org/10.5066/P9IJLEIY) · [USGS landing page](https://www.usgs.gov/data/dynamic-sparrow-model-application-data-seasonal-total-nitrogen-and-total-phosphorus-loads) |
| **Ref** | Schmadel, Ator, Miller et al. 2026 · [theory: TM 6-B3](https://pubs.usgs.gov/tm/2006/tm6b3/) · [RSPARROW](https://github.com/USGS-R/RSPARROW) |
| **Feature type** | STATIC (weather-normalized) or DYNAMIC (seasonal) |

**Open items before use:**
- Read `ReadMe_conus.txt` — confirm whether shipped predictions are **conditioned** (observed load substituted at monitored reaches and routed downstream). Use **unconditioned** only; conditioned predictions put the target in the feature.
- 263,494 ≪ 2.73M NHDPlusV2 flowlines ⇒ almost certainly an aggregated/filtered network. Confirm the key field is COMID and build a pin→SPARROW-reach mapping if not 1:1.
- Pull the calibration station list (2,424 TN stations) and intersect with your sensor set. Overlap is probably small — TN calibration needs lab samples resolving organic/reduced N, which optical NO₃ sensors don't produce.
- Weather-normalized vs. unnormalized scenarios ship separately. Don't mix.
- Response is **TN load**, not nitrate. TN:NO₃ varies with season and flow.
- `MAM` spring definition may not match your spring-window statistic.

**Older vintages:** 2012 regional models are NHDPlusV2 (COMID-joinable). National/MRB1–8 ran on ERF1_2 — avoid, lossy crosswalk. [Overview + mappers](https://www.usgs.gov/mission-areas/water-resources/science/sparrow-modeling-estimating-nutrient-sediment-and-dissolved)

### 1.3 HAWQS (SWAT-based) — `EVAL`

Mechanistic complement to SPARROW's statistical approach. HUC8/HUC12 nutrient loads. [hawqs.tamu.edu](https://hawqs.tamu.edu/) · Useful mainly as an independent second opinion; low marginal value if SPARROW is already in.

---

## 2. Observations — expanding the node population

### 2.1 Water Quality Portal — `ADD`, second priority

| | |
|---|---|
| **What** | Aggregates NWIS + STORET/WQX: discrete nitrate grab samples from state, federal, tribal, university programs |
| **Spatial** | Hundreds of additional Iowa locations vs. 27 continuous sites |
| **Temporal** | Sparse, irregular; monthly to quarterly typical |
| **Access** | [waterqualitydata.us](https://www.waterqualitydata.us/) · REST + `dataretrieval` support |
| **Feature type** | TARGET |

**Why it matters more than it looks:** inductive kriging learns a location-agnostic aggregation function via sensor masking. The binding constraint is the number of distinct *spatial* configurations seen in training, not temporal density. 27 continuous nodes is a thin graph. Discrete-sample nodes with a temporal-availability mask widen it by an order of magnitude.

**Integration:** these become nodes with the nitrate channel present but sparsely observed — your existing mask-channel schema handles this directly. Watch analytical method heterogeneity (NO₃ vs NO₃+NO₂ vs TN) in `ResultAnalyticalMethod` / `CharacteristicName`.

### 2.2 EPA ECHO / NPDES DMR — `ADD`

| | |
|---|---|
| **What** | Reported effluent N discharge from permitted WWTPs and industrial facilities, monthly |
| **Access** | [echo.epa.gov](https://echo.epa.gov/) · [ECHO REST services](https://echo.epa.gov/tools/web-services) |
| **Feature type** | DYNAMIC (point-source loading) |

You have SDWIS (intake side); this is the discharge side. In smaller Iowa watersheds at low flow, point sources can dominate. **Their temporal signature is informative precisely because it breaks the flow–nitrate correlation** the model would otherwise learn everywhere.

### 2.3 Iowa DNR ambient monitoring — `EVAL`
Flows into WQP, but the DNR portal may carry richer metadata. [Iowa Geodata](https://geodata.iowa.gov/)

---

## 3. Nitrogen inputs and management

### 3.1 Tile drainage — `ADD`, third priority

Dominant nitrate transport pathway in Iowa. Currently absent from the stack.

**AgTile-US** — 30 m CONUS binary tile-drainage raster
- [figshare download](https://figshare.com/articles/dataset/AgTile-US/11825742) · [paper: Valayamkunnath et al. 2020, *Sci Data* 7:257](https://doi.org/10.1038/s41597-020-00596-x) · [GEE community catalog](https://gee-community-catalog.org/projects/tile/)
- Inputs: 2017 Census of Ag county tile statistics + NLCD-2016 cropland + SRTM slope + SSURGO drainage class
- Accuracy 86.0% CONUS; 82.7–93.6% over the Midwest
- **Collinearity warning:** derived from SSURGO drainage class + slope, both of which you already have. The genuinely new information is the county census constraint.

**SEETileDrain** — independent ML alternative, Midwest, satellite-imagery-based random forest. Wan et al. 2024, *Sci Total Environ* 950:175283, [doi:10.1016/j.scitotenv.2024.175283](https://doi.org/10.1016/j.scitotenv.2024.175283). Use for cross-checking AgTile-US rather than in addition to it.

### 3.2 Fertilizer and manure, county-level — `ADD`

| Dataset | Coverage | DOI |
|---|---|---|
| Falcone 2021, fertilizer + manure, ~5-yr intervals | 1950–2017 | [OFR 2020-1153](https://doi.org/10.3133/ofr20201153) · [data release](https://www.sciencebase.gov/catalog/item/5ebad56382ce25b51361806a) |
| Brakebill & Gronberg, commercial fertilizer N/P | 1987–2012 | [10.5066/F7H41PKX](https://doi.org/10.5066/F7H41PKX) |
| Gronberg & Arnold, animal manure N/P | 2007, 2012 | [10.5066/F7X34VMZ](https://doi.org/10.5066/F7X34VMZ) |

**Note:** the Falcone 2021 fertilizer/manure product is *not* the same as the Falcone NANI dataset you already have (10.5066/P9VSQN3C). Check for overlap before double-counting. The 1950–2017 series is the useful one — annual-ish resolution supports the lagged-loading features on your roadmap.

### 3.3 Manure N from livestock — `ADD`

Iowa is the top hog-producing state. **CDL sees none of this.** 
- [NASS QuickStats](https://quickstats.nass.usda.gov/) livestock inventories by county
- Iowa DNR **AFO/CAFO facility database** — point locations, via [Iowa Geodata](https://geodata.iowa.gov/) and the [AFO Siting Atlas](https://programs.iowadnr.gov/maps/)

### 3.4 Cover crops and tillage — `EVAL` (resolution-limited)

**OpTIS** (Operational Tillage Information System), Regrow / CTIC / TNC
- [ctic.org/optis](https://ctic.org/optis/) · [OpTIS info page](https://register.ctic.org/OpTIS_Information) · [older field-scale metadata, Ag Data Commons](https://data.nal.usda.gov/dataset/operational-tillage-information-system-optis-tillage-residue-and-soil-health-practice-dataset)
- v4.0 released Sept 2023, CONUS, years 2015–2021; two-year rotation context
- **Blocker:** public data is aggregated to **HUC8 or Crop Reporting District** only — producer-privacy constraint. Almost certainly too coarse for basin-level work. Field/sub-field scale requires a commercial arrangement with Regrow.
- Still worth pulling as a coarse temporal trend covariate; addresses the known CDL cover-crop gap at least directionally.

### 3.5 Conservation structures — `EVAL`

**Iowa BMP Mapping Project** (ISU) — LiDAR-derived terraces, WASCOBs, ponds, grassed waterways. Structural practices affect delivery fraction independently of source. Via [ISU GIS Facility](https://www.gis.iastate.edu/data) / [Iowa Geodata](https://geodata.iowa.gov/).

---

## 4. Physical setting — Iowa-specific

### 4.1 Landform regions and surficial geology — `ADD`, fourth priority

Iowa's landform regions differ in drainage development, tile density, depth to bedrock, and dominant flow path:

| Region | Dominant pathway | Implication |
|---|---|---|
| Des Moines Lobe | Tile drainage | Fast, high-concentration, flow-coupled |
| Iowan Surface | Mixed tile + shallow groundwater | Intermediate |
| Southern Iowa Drift Plain | Surface runoff, well-developed drainage | Lower tile density |
| Paleozoic Plateau (NE) | **Karst** — conduit flow, sinkholes | Sub-daily response, near-zero attenuation |
| Loess Hills (W) | Steep, erosional | High sediment, different N speciation |

Lumping karst with the Des Moines Lobe will hurt model performance. Cheap static node attributes, high explanatory value.

- [Iowa Geodata clearinghouse](https://geodata.iowa.gov/) (NRGIS Library merged here)
- [Iowa DNR Surficial Geology MapServer](https://programs.iowadnr.gov/geospatial/rest/services/Geology/SurficialGeology/MapServer) — 1:24,000 and 1:100,000 scale
- [ISU GLSI surficial geology of Iowa, 10 m raster](https://www.agron.iastate.edu/glsi/physiography-gis-data/surficial-geology-of-iowa-gis/) — gSSURGO-derived parent material to 2 m depth
- [GeoSam](https://programs.iowadnr.gov/maps/) — Iowa Geological Survey well/borehole database, depth to bedrock
- Sinkhole inventory (NE Iowa karst) via Iowa Geodata

### 4.2 Topography — `EVAL`
Statewide 1 m LiDAR DEM via Iowa Geodata. Derive slope, topographic wetness index, flow accumulation, riparian buffer width. Partially redundant with SSURGO/NHDPlus attributes.

### 4.3 Impoundments — `EVAL`
[National Inventory of Dams](https://nid.sec.usace.army.mil/) + NHDPlus waterbody attributes → reservoir residence time / areal hydraulic load. Matters for attenuation on specific reaches.

### 4.4 Land cover — `EVAL`
[Annual NLCD](https://www.mrlc.gov/) (1985–present, annual). More temporally resolved than the standard NLCD epochs; complements CDL for the non-ag fraction and for developed-vs-natural-sink separation.

---

## 5. Antecedent conditions and meteorology

Nitrate flush magnitude depends on soil profile state *before* the rain. Stage IV gives the rain, not the state.

| Source | What | Access | Notes |
|---|---|---|---|
| NWM land surface output | Soil moisture, runoff, baseflow, SWE | [AWS registry](https://registry.opendata.aws/nwm-archive/) | 3-hourly; free with §1.1 pull |
| NLDAS-2 | Soil moisture, runoff, forcing | [ldas.gsfc.nasa.gov/nldas](https://ldas.gsfc.nasa.gov/nldas) | 1/8°, hourly; independent alternative |
| SNODAS | Snowpack, melt | [NSIDC G02158](https://nsidc.org/data/g02158) | IA spring pulse is partly snowmelt-driven; Stage IV handles frozen precip poorly |
| Soil temperature | Mineralization / denitrification rate control | [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/) | Multiple depths at ISU sites; same portal as Stage IV |
| SMAP L3/L4 | Satellite soil moisture | [NSIDC](https://nsidc.org/data/smap) | Coarse (9–36 km); NWM/NLDAS probably better here |

**Do not add gridMET precipitation.** Gauge interpolation with PRISM climatological sharpening — in flat Iowa terrain it adds little per-storm signal over Stage IV, and its ~12Z day definition breaks rain-to-nitrate lag calculations.

---

## 6. Deferred / expansion

| Source | Purpose | Notes |
|---|---|---|
| [NEON DP1.20033.001](https://data.neonscience.org/data-products/DP1.20033.001) | Supplementary continuous nitrate | Few IA sites |
| [GLTG](https://greatlakestogulf.org/) | Great Lakes to Gulf nutrient aggregation | Cross-basin |
| MPCA (MN) | Cross-basin validation | KISTERS WISKI / [MN Geospatial Commons](https://gisdata.mn.gov/) |
| MDA (MN) | Cross-basin validation | OneRain Contrail / AEM Elements 360 |
| Iowa DNR private well nitrate | Groundwater | Deferred — lagged loading features serve as legacy-N proxy for now |

---

## Priority order

1. **NWM v3.0 retrospective** — every candidate pin location gets a discharge time series. Nothing else unblocks the virtual-sensor concept as directly.
2. **Water Quality Portal** — enough spatial nodes to actually train an inductive model.
3. **AgTile-US** — dominant Iowa transport pathway, currently missing.
4. **Iowa landform / surficial geology** — first-order control, cheap, static.
5. **SPARROW dynamic CONUS** — after resolving conditioning + COMID-keying + calibration-overlap questions.
6. **ECHO DMR** — distinct temporal signature, breaks the universal flow–nitrate coupling.

---

## Integration notes

**Split static vs. dynamic before writing loaders.** The mask-channel schema handles observed/absent, but static attributes (geology, tile fraction, landform) and dynamic features (NWM flow, soil moisture, DMR) want different treatment in the temporal encoder. Cheaper to make the split now than to refactor around it.

**Leakage audit, generalized.** Any model-derived layer calibrated against gauge data is a leakage candidate under sensor-masking cross-validation. Current status:
- NWM retrospective: **clean** — no data assimilation.
- SPARROW: **check** — 2,424 TN calibration stations; verify overlap with your sensor set and use unconditioned predictions.
- HAWQS/SWAT: **check** if used — calibration basins may include yours.

**Empirical leakage test:** fit the GNN with and without each model-derived feature; compare held-out performance at calibration-station nodes vs. non-calibration-station nodes. Disproportionate lift at calibration stations ⇒ leakage. Uniform lift ⇒ clean.

**Collinearity clusters to watch:**
- AgTile-US ↔ SSURGO drainage class ↔ slope
- SPARROW source terms ↔ Falcone NANI ↔ Brakebill/Gronberg fertilizer
- NWM soil moisture ↔ NLDAS-2 soil moisture ↔ SMAP

**Reproducibility:** Stage IV, SNODAS, and soil temperature all come through IEM — one collector module, multiple products. Same for the three USGS county nutrient datasets on ScienceBase.
