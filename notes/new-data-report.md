# New data sources — recommendations

Compiled 2026-08-26, against the pipeline as it exists today (build4: 20,233 registrations, 4.28M km² area of extent, 7,539 assembled records). Supersedes nothing; `nitrate_data_sources.md` was the input, and where this report contradicts it, this report was measured.

## Bottom line

**One number reframes every priority in the source document: the pipeline holds 20,233 registrations and exactly 372 of them carry any nitrate at all.** Median record 986 days, 279 with a year or more. Everything else — 7,201 cached sensors — is discharge, stage, temperature, conductance. We have built a 4.28-million-km² hydrological substrate around a nitrate node population that would fit in a lecture hall.

That makes the ranking straightforward:

| rank | source | what it buys | verdict |
|---|---|---|---|
| 1 | **Water Quality Portal (WQX 3.0)** | ~2.16M discrete nitrate results across 32 AOE states; ~83% of stations are non-USGS, i.e. genuinely new | **ADD** — and it fixes a live bug (below) |
| 2 | **NWM retrospective + `analysis_assim_no_da`** | modeled streamflow at *every* COMID, 1979–present, leakage-clean | **ADD**, scoped to a compact reach set |
| 3 | **ECHO DMR, completed** | point-source N loading with the flow needed to make it a mass | **FIX WHAT WE HAVE** before adding anything |
| 4 | **gTREND's remaining components** | manure, human waste, deposition, fixation, uptake — same figshare release we already use | **ADD** (pending size check) |
| 5 | **SPARROW dynamic CONUS** | seasonal source apportionment per reach | **ADD NARROWLY** — the shipped loads are conditioned; only some columns are safe |
| — | Annual NLCD, surficial geology, OpTIS, SNODAS/NLDAS | see the deferred section | mixed |

Two findings below are not recommendations but **defects in the current pipeline**, already fixed in code during this review.

---

## 1. Water Quality Portal — the node-population fix

**What it is.** WQP aggregates NWIS and STORET/WQX: discrete nitrate grab samples from state, federal, tribal and university programs. Sparse in time (monthly to quarterly), dense in space.

**Why it outranks everything else.** Our 372 nitrate nodes are the binding constraint on any spatially-inductive model — the number of distinct *spatial configurations* seen in training, not the temporal density at each. Measured over the 32 AOE-relevant states, 2008–present, across four nitrate-family characteristics: **2,158,204 results**. For Iowa alone, 2,318 nitrate stations, of which **1,928 are non-USGS** — roughly 83% are observations we do not hold in any form. The AOE's discrete pool is about 36× Iowa's.

**Access, verified live.** `https://www.waterqualitydata.us/wqx3/Result/search?dataProfile=narrow&zip=yes`, filtered per state × characteristic. A statewide two-year pull is 1–17 s and ~1.5 MB; ~130 requests covers the AOE-decade, small enough to run serially with retries. `Total-Result-Count` on a HEAD request is free and should be used to size the pull and detect truncation.

### The bug this uncovered

**The legacy endpoint silently drops all USGS data added after 2024-03-11.** Measured, Iowa nitrate calendar-2025: the legacy `/data/Result/search` returns 1,413 rows (STORET 1,413, **USGS 0**); `/wqx3/Result/search` returns 1,608 (STORET 1,413, **USGS 195**). No error, no warning header — a smaller file. `experiments/discrete-sample-pre-check/01_fetch_wqp.py` and `02_audit.py` both hardcode the legacy URL, so every pull they have made in the last 17 months is missing the USGS half.

WQX 3.0 also fixes speciation, which matters more than it sounds: the legacy profile jams speciation into the unit string and loses it on **75% of rows** (`mg/L` with no basis); 3.0 splits it into `Result_MethodSpeciation` and leaves only 8.9% unspecified. Any unit conversion must therefore key on **(unit, speciation)**, not the unit alone — `(mg/L, as N)` → 1.0, `(mg/L, as NO3)` → 0.22590, `(mg/L, as NO2)` → 0.30446.

### Canonicalization rules (measured, Iowa 2020–21)

**Accept:** `Nitrate` (3,540 rows), `Nitrate + Nitrite` (1,254), `Inorganic nitrogen (nitrate and nitrite)` (313) — the latter two as a distinct NOx channel, flagged, never silently pooled with nitrate.

**Refuse, do not convert:** Kjeldahl nitrogen (2,864), ammonia (820), `Nitrogen, mixed forms` (469), `Total Nitrogen` (55). TKN + NOx ≠ nitrate and no factor makes them equivalent — the same discipline the registry already applies to turbidity's optical geometries.

**Deduplicate after conversion, never before.** All 288 USGS nitrate activities in the test pull carry the measurement *twice* — pcode `00618` (as N) and `71851` (as NO3), paired ratio 4.4273 against the theoretical 4.4266. Converting first and deduplicating on (activity, location, date) keeping the as-N member took 2,884 rows to 2,610 usable over 148 stations.

**Joining to what we hold** is plain string equality: WQP's `Location_Identifier` for USGS rows is literally `USGS-{site_no}`, the same spelling our USGS metadata uses.

---

## 2. NOAA National Water Model — modeled flow everywhere

**What it is.** Hourly simulated streamflow for 2,776,734 NHDPlusV2 reaches, `feature_id` = COMID, no crosswalk needed. Discharge is the strongest single covariate for nitrate concentration and we currently have it only where a gauge sits.

**Leakage: clean, and this is documented rather than inferred.** NOAA's Big Data Program docs state the retrospective simulations "did not assimilate stream gauge observations", the AWS registry says "no streamflow or other data assimilation is performed within any of the NWM retrospective simulations", and the store metadata corroborates: no `nudge` variable exists anywhere in the retrospective, unlike the operational files.

**Coverage and the splice.** The v3.0 retrospective runs 1979-02-01 → **2023-02-01** (`s3://noaa-nwm-retrospective-3-0-pds/CONUS/zarr/chrtout.zarr`, 385,704 hourly steps). For 2023 onward, `gs://national-water-model` carries **2018-09-17 → today with no gaps**, and its COMID index is byte-identical to the retrospective's for the v3.0 era, so the join needs no reindexing. Two discontinuities are mandatory to handle:

- **Use `analysis_assim_no_da`, not `analysis_assim`.** Measured on a single hour, 6,214 reaches are directly nudged but **164,651 (5.9% of CONUS) differ** between the two, because assimilation propagates downstream through routing. The default configuration would inject gauge observations into our features — reintroducing exactly the leakage the retrospective avoids.
- **2023-02 → 2023-09 is NWM v2.2, not v3.0.** About 7.5 months of any splice comes from a different model version; it needs a regime flag, not a seamless concatenation.

**The cost, and the design consequence.** The zarr chunking is `[672 hours, 30000 features]`, so **the full 44-year series for one COMID costs the same as for 30,000**. Measured: 7,500 CONUS-scattered reaches touch all 93 of 93 feature blocks — a 326.6 GB read — and even 500 random reaches touch 92. Geographic compactness is the only lever: an Iowa-bounded set touches 32 blocks (~109 GB), the upper Midwest 75 (~263 GB).

For us that is better news than it sounds, because **only 342 distinct COMIDs carry nitrate**, 268 of them inside the cornbelt box. The recommendation is therefore: pull `streamflow` plus `q_lateral`/`qSfcLatRunoff` (both already per-reach, unlike anything in the land-surface store) for a cornbelt-compact reach set, and skip `ldasout` entirely — its soil-moisture and snow variables are 1–7 TB *per variable*, on a 1 km grid that would then need basin-weighted aggregation, for signal the channel-side runoff terms partly carry for free.

**One cheap route worth testing first:** `CONUS/netcdf/CHANOBS/` carries streamflow for **8,643 gaged reaches** at 195 KB/hour — about 25.6 GB for 2008–2023. If our sensor COMIDs are largely a subset of those gaged reaches, this beats the zarr read by an order of magnitude. Worth a set-intersection check before committing to the big pull.

---

## 3. ECHO DMR — fix before extending

We already download the annual DMR archives and hold the outfalls layer and CWNS attributes. The review found the acquisition **incomplete in three ways that make the data unusable for its intended purpose**, all now patched in code:

1. **Flow was excluded.** The nitrogen word-filter kept nitrogen parameters and dropped parameter `50050` (plant flow) — but **half the effluent monitoring cells carry a concentration (mg/L) with no quantity beside it**, and a concentration becomes a mass only against the period's flow. Half the record was silently unusable. *(Fixed; requires a re-fetch of the archives to take effect.)*
2. **The outfall-level join key was dropped.** DMR reports at permit × `PERM_FEATURE_NMBR`, and 55,745 permits have more than one outfall — so loading could only ever be placed at *permit* grain, which is not a location on a river. *(Fixed and re-extracted: 205,312 outfalls now carry it.)*
3. **The current fiscal year was never fetched** — the year range excluded FY2026, which EPA publishes as it accrues. *(Fixed.)*

**Rules for the loading computation**, when it is built: filter `MONITORING_LOCATION_CODE` to `{1, EG}` (effluent gross) — code `G` is **raw sewage influent**, 71,475 rows in FY2023, and summing it as discharge would be a serious error. Deduplicate on `DMR_VALUE_ID` (5,999 duplicate rows differ only in violation code and carry identical values). Treat NODI `C` (no discharge, 263,064 rows) as a **real zero, not a gap**. Apply species conversion by parameter code — `71850` (as NO₃) and `00620` (as N) both report `mg/L`, the same trap WQP has. And expect gaps: only 39.2% of series report all twelve periods.

---

## 4. SPARROW dynamic CONUS — narrower than hoped, but not useless

Isaac's note was that the leakage risk "can be mitigated completely with proper use." The measurement says the mitigation is real but **much narrower than using the model's headline output**.

**The shipped predictions are conditioned.** The release ships no unconditioned scenario. Conditioning means, in the model's own documentation (TM6-B3 Appendix D.6), that "the monitored flux is substituted for the predicted flux at those reaches, this monitored value being used in the subsequent predictions of downstream flux" — and that the standard error at those reaches "is set to zero". The data dictionary confirms it by that signature: in `predict_tn_boot.csv`, `SE_PLOAD_TOTAL` has a range-minimum of **exactly 0** while `SE_PLOAD_TOTAL_ND` is strictly positive (1.76e-04). A zero-width confidence interval on a routed total is what conditioning produces and nothing else does.

So `PLOAD_TOTAL` at a calibration station **is the observed load**, routed downstream — and with 2,424 TN calibration stations placed at network points-of-interest, "at or downstream of a calibration station" covers most of the mainstem. Using it as a covariate would be feeding the model an observation of its own target.

**The mitigation that actually works** follows from the same passage: "the source shares for the reach are derived by applying the predicted source share times the monitored flux". The **shares are model predictions, not substitutions**. So the safe columns are:

- `sh_*` — source apportionment shares (wastewater, fertilizer, manure, atmospheric deposition, urban, natural, N-fixing crops, septic). Composition, not magnitude.
- `DEL_FRAC`, `rchtot` (time of travel), `tile` — network and landscape properties.
- `PLOAD_*_ND` (no-decay) with caution — its positive SE suggests it escapes substitution, but that is inferred, not verified.

**Refuse:** `PLOAD_TOTAL`, `concentration`, and anything else routed, unless the site is verified to be neither a calibration station nor downstream of one.

**Two further limits.** The reach key is **not COMID** — it is `genid` from GenNet, a ~10:1 aggregation of NHDPlusV2 (263,494 reaches vs 2.69M), median reach length 4.42 km. A published crosswalk exists (`comid_genid_xwalk.csv`, 48.8 MB), but 7,500 random COMIDs collapse to 7,175 GenIDs — **4.3% collide**, and deliberately-paired sites a kilometre apart will very often land on the same GenID and receive identical values. Our negative-distance finding at sub-kilometre separations lives entirely below SPARROW's resolution. Second, **it stops in November 2020**, so it can only be a static or slowly-varying basin descriptor, never a contemporaneous feature.

**Cost:** ~53 GB of all-or-nothing zips (no HTTP Range support). The station list needed for a leakage audit is inside a 5.75 GB file, though `lat`/`lon` for every calibration station comes with it, so the audit can intersect on coordinates without resolving identifier schemes.

---

## 5. Static landscape covariates — mostly already on disk

The headline here is deflationary and useful: **most of what the source document asks for is already in the archive**, and the most valuable missing piece is a two-word edit to a config tuple rather than a new acquisition.

### The two-word win

`data/build/acquire/attributes.py` pulls the USGS "Select Attributes for NHDPlus V2.1 Reach Catchments" release filtered to seven `themeLabel` values. The live catalogue carries **fourteen themes / 14,139 attributes**, and two of the omitted ones matter:

- **`Hydrologic`** (30 attributes) — and note this is *not* the "Hydrologic Modifications" theme we already pull, which is dams and canals. This is Wolock's decomposition of streamflow into flow pathways: `CAT_CONTACT` (subsurface contact time, days), `CAT_IEOF` (infiltration-excess overland flow, % of streamflow), `CAT_SATOF` (saturation overland flow, %), `CAT_RECHG` (groundwater recharge), `CAT_BFI`, `CAT_EWT` (water-table depth), `CAT_TWI`. **This is precisely the karst-vs-tile-vs-loess flow-pathway partition the source document argues for**, already computed per COMID.
- **`Regions`** (407 attributes) — Wolock Hydrologic Landscape Regions, Fenneman physiographic provinces, EPA Level-3 ecoregions.

Cost: ~437 attributes, an estimated 0.3–0.5 GB after the AOE filter, COMID join, no new code. One caveat worth stating: regional categoricals will look strong under `loso_` and weak under `lofo_` — they are as much *grouping* candidates as features, and our LOSO−LOFO gap column exists to expose exactly that.

### What we already hold and should not re-acquire

Verified against the archive: **Soller & Reheis surficial materials** (`CAT_SOLLER_*`, 50 classes — the fine form, not the 18-class reclass), **Principal Aquifers** (`CAT_AQ*`, 64), **bedrock permeability** (`CAT_BEDPERM_1..7`), **generalized geology** (`CAT_HUNT*`, `CAT_BUSHREED_*`), **Olson lithology/geochemistry** (StreamCat `pctcarbresid`, `pcteolfine`, `pctglactil*`, plus oxide chemistry), **STATSGO soil properties**, **NLCD class fractions** including 17 riparian-buffer classes at 50 m. The source document's §4.1 and §4.2 are, for practical purposes, already answered.

### Genuinely new, in order

1. **Karst — Weary & Doctor (USGS OFR 2014-1156).** 282 MB, our own polygon overlay. The only geology dataset in this review with *no* substitute in what we hold — verified zero karst hits across all 14,139 Wieczorek attributes and all 167 StreamCat families. And `pctcarbresid` does not substitute: karst units are classified by **burial depth**, so a carbonate aquifer under 40 ft of till scores zero carbonate-residual while being exactly the cover-collapse setting that routes nitrate to groundwater. Our AOE straddles the glacial margin, which is where that distinction lives.
2. **Sekellick & Sherr 2024** (DOI 10.5066/P9WDBIXC), 728 MB, Falcone's county fertilizer/manure **already downscaled to NHDPlus COMID**. The axis nothing else gives us: **confined vs unconfined manure** — CAFO versus pasture. Four census years only (2002/07/12/17).
3. **Annual NLCD `FctImp` + `ImpDsc`** (~51 GB). Continuous 0–100 imperviousness annually, plus a road/urban split. Skip `LndCov` — NASS's own FAQ states CDL's non-ag classes are NLCD-derived, so it would be a lossy re-derivation of what we have; and never `LndCnf` (317 GB of per-pixel confidence).

### gTREND's other 20 components — largely redundant, and here is why

The remaining components (manure by 9 animal classes, human waste, deposition, fixation, uptake) are 39.3 GB of zips, or ~8 GB extracted for our 2000–2017 window. **But gTREND downscales county totals by multiplying the county mean by a binary agricultural land mask** — so below county scale it carries no information beyond "is this cell agricultural." A catchment mean of gTREND surplus is an area-weighted county mean over ag fraction; the 250 m resolution is presentational, not informational.

Meanwhile **StreamCat already ships the full annual N mass balance per COMID, 1987–2017**: farm fertilizer, non-farm fertilizer, **livestock waste**, livestock waste recovered, crop fixation, crop removal, deposition, human waste, total inputs, agricultural surplus, and accumulated **legacy** N. We hold only the `_2017` vintage of each, because `latest_vintage()` deliberately collapses each family to its newest member (the docstring is explicit: all vintages would be 2,371 columns, 17.8 hours, 28 GB — "historical vintages are not gone, only unasked-for"). **Requesting those families annually is a better-matched, cheaper move than ingesting gTREND's extra rasters**, because it is already COMID-keyed and already annual.

What gTREND alone would still add: manure split by animal class (hog/broiler/layer vs beef-cow composition is a real confinement and application-timing proxy), reduced-vs-oxidized deposition, and cropland-vs-pasture splits. If those prove worth it, figshare's downloader supports HTTP Range — our 18 years are 18% of the bytes, so selective per-year extraction costs ~7 GB of transfer with no zip landing on disk. One trap: internal TIFF stems do not match zip names (`Lvst_Sum.zip` contains `Livestock_1930.tif`), so any ingest must read the actual archive listing.

**Lineage, settled:** Brakebill & Gronberg and Gronberg & Arnold are *upstream* of gTREND, not independent — ingesting them adds nothing. Falcone 2021 is partly independent for manure only (gTREND reports R²=0.99 against it for fertilizer but 0.85 for manure, using newer excretion coefficients). **Nothing public in this family extends past 2017** — the post-2017 gap is structural and applies to gTREND, StreamCat, Falcone and Sekellick alike, while our water record runs to 2026.

### OpTIS — do not build on it

Frozen at v4.0 (2023), **years 2015–2021 only**, computed at 30 m but **released only at HUC8 and Crop Reporting District**, no bulk download (the Ag Data Commons record contains zero data files — its single resource is a hyperlink), and tillage accuracy is **60% at field level** before aggregation. The spatial unit is the disqualifier: a HUC8 averages ~4,000 km², so one value per HUC8 is a near-constant regional covariate that will look predictive under LOSO, contribute nothing under LOFO, and sit close enough to our basin grouping to be a genuine leakage risk rather than merely a weak feature.

**Substitute:** USDA Census of Agriculture cover-crop acreage (county, 2012/2017/2022, free Quick Stats API), normalized by county cropland. Same information content OpTIS aggregates toward, it is the reference OpTIS is validated against, public domain, and it reaches 2022.

### One check this review triggered, already resolved

The research flagged that **CDL moved from 30 m to 10 m starting with crop year 2024**, warning that ingesting 2024+ without pinning the 30 m variant would silently break grid alignment and every `frac_cell_in_basin` weight. Checked against the running build: `cdl_rasters` globs `*_30m_cdls.tif` (NASS's resampled continuity variant) and `grid_groups` splits years by raster grid with the 2025 case documented in its own docstring — which is exactly why the current d5 run shows "crops group 1/2 (2008-2024)" and "group 2/2 (2025)". Already handled.

## 6. Antecedent conditions — and a defect in what we already have

### The gridMET day boundary is misaligned with our observation day

**This is the most consequential finding in the review, and it costs nothing to fix.**

gridMET's daily `pr` is an accumulation over **12:00 UTC day D → 12:00 UTC day D+1**, labelled by the day the window *starts*. Its own netCDF metadata says otherwise (`note5` claims days end at midnight Mountain Standard Time), and that note is wrong for precipitation. Verified two independent ways: a 24-offset correlation scan against AORC hourly precipitation at 4 sites × 2 years lands on +13 h in 6 of 8 cases (r ≈ 0.98 at the best offset versus 0.60–0.94 at the documented one); and a cross-check against IEM's IEMRE, which publishes both day definitions for the same point, matches gridMET day D to IEMRE's 12z day **D+1** at r = 0.94–0.98 while the local-calendar-day correlation is 0.56–0.77.

Our pipeline bins every observation to Central Standard calendar days ([io.py](data/build/io.py) `STANDARD_TZ = "Etc/GMT+6"`, "the one place a day is decided"). gridMET's day therefore runs **06:00 CST day D → 06:00 CST day D+1** — six hours offset from the nitrate day it gets joined to. In the corn belt's nocturnal-storm regime that window is not a rounding error: measured from AORC, **27–36% of precipitation falls between midnight and 06:00 local** (Iowa 27–36%, Illinois 30–33%, Minnesota 30–33%, Maryland ~20%), and gridMET assigns all of it to the previous day.

On storm days it inverts whole events. At Ames in 2015, across 17 days with ≥25 mm under either definition, the median absolute difference is 11.5 mm and the maximum 89.2 mm — gridMET's window for 2015-08-08 holds 92.3 mm while the local calendar day 2015-08-08 received 3.1 mm, because the 90 mm fell on the 9th.

**This is not a build defect** — the build stores gridMET's days exactly as gridMET publishes them, which is correct. It is a *join* defect: any feature pairing weather day D with nitrate day D silently assumes they cover the same 24 hours. The fix is one of: shift the label at read time, re-derive precipitation from AORC hourly on our own boundary, or document the convention so features lag deliberately. It belongs in the access layer or the feature layer, not in acquisition. **Caveat: verified for `pr` only.** Whether `tmmn`/`tmmx`/`srad` follow the same convention is untested — and if they do not, a single daily row currently mixes day definitions, which would be worse.

*(Incidental: gridMET's NCSS `accept=csv` endpoint returns raw packed values without applying `scale_factor`, so CSV precip comes back 10× too large. Our pipeline uses `accept=netcdf` through xarray and is unaffected — worth knowing before anyone reaches for the CSV shortcut.)*

### SNODAS — the best value of the five, and the one to take first

**Anonymous, no login, no token**: `https://noaadata.apps.nsidc.org/NOAA/G02158/masked/{YYYY}/{MM}_{Mon}/SNODAS_{YYYYMMDD}.tar`, ~17 MB/day, 2003-10-01 → present, with a published errata file listing missing dates. The format is pleasantly dumb — one tar per day containing flat big-endian int16 grids with ASCII headers, no GDAL, no GRIB, no HDF.

It supplies two things nothing else we hold does: **precipitation phase** (so the pipeline stops crediting a snowfall day with liquid water input) and **melt as a dated water-input event**. The magnitude is not marginal — measured at Ames over 2019-03-08→24, SWE holds 52–70 mm for six days and then releases **44.2 mm of melt on 03-14** alongside 6.1 mm of rain, while **gridMET's `pr` reports 6 mm and is blind to ~88% of the water arriving at the soil surface that day**. Spring melt over frozen or near-saturated tile-drained ground is precisely the flush mechanism the domain argument is about, and it is currently invisible to us.

Cost: ~118 GB transfer whole-CONUS 2008→now, ~60 GB snow-season-only; resampled onto the gridMET 1/24° grid we already key weather on, **21.6 GB stored**. Carry `swe_mm`, `melt_mm`, snow fraction of precipitation, and a derived `water_input_mm = rain + melt` to use in place of `pr` in winter. One trap: melt is −9999 wherever there is no snowpack, so it must be filled with 0, not left null.

### NLDAS-2 — take the state variables, refuse the fluxes

1/8° hourly, 1979→present, Noah land-surface model. The variables that matter: `SoilM_0_10cm`, `SoilM_0_100cm`, `SoilT_0_10cm`, `SWE`. **Earthdata Login is mandatory** — verified precisely: metadata (`.das`/`.dds`/directory listings) is anonymous, but every data GET redirects to URS OAuth, the S3 mirror is a protected bucket needing temporary credentials and us-west-2, and the Hydrology Data Rods polygon-subsetting service **has been discontinued** (the endpoint now redirects to that announcement). There is no daily product; daily aggregation is ours to do from 24 hourly granules.

Volume: 1.18 TB as full granules, but **6.4 GB if reduced to daily in flight and stored on the native grid** — which is the only sane route, and it costs ~163k authenticated requests, a day or two of wall time.

**Take the state variables; refuse `Qs`/`Qsb` as catchment runoff.** A flux attributed to a 1.4 km² catchment cannot honestly come off a 146 km² cell — that is NWM's job at reach scale. But soil moisture and soil temperature genuinely *are* smooth at 10 km, so the resolution mismatch is acceptable for a state variable in a way it is not for a flux. This is the distinction that decides what to take from every gridded land-surface product.

### gridMET itself — we were fetching a quarter of what it publishes

Measured against the endpoint's own catalogue (`reacch_climate_MET_aggregated_catalog`) on 2026-08-26: **it publishes 42 aggregates and the build was pulling 11.** This is the cheapest finding in the report, because the fetch path, the grid, the AOE tiling and the catchment weights all already exist — the gap was a variable tuple assembled from `pygridmet`'s standard long-name mapping rather than from the catalogue.

**Five daily variables, now added** (`fm1000`, `fm100`, `bi`, `erc`, `th`), each verified serving real data by a one-day request. The important one is `fm1000`: dead fuel moisture at 1000 hours was the **only** weather variable that survived feature curation in either shipped model — the predecessor's recipe comments it as "the only weather var with held-out signal; the rest are perm-dead in every run" — and the rebuild had silently dropped it. Its physical content is a ~40-day moisture-memory integral, which is why the xgboost project's stand-in is a 40-day (reference ET − precipitation) deficit.

**`snow` is not available**, despite `pygridmet` mapping it to `snow_amount`. The name resolves and returns **zero bytes**, and it is absent from the aggregated catalogue. There is no cheap phase split here; SNODAS remains the route.

**Twenty-five drought aggregates exist, and they are pentad.** `spi`, `spei` and `eddi` each ship at eight accumulation windows (14d, 30d, 90d, 180d, 270d, 1y, 2y, 5y), plus `pdsi` and `z` (drought category). All carry **3,404 timesteps since 1979 — one per five days**, against `acquired/weather/`'s one row per (cell, day).

They are worth having: `spei90d` and `spei180d` are properly-computed, standardized multi-scale water-balance deficits — the same quantity this project hand-rolls at a single 40-day window. But landing them in the daily store would leave four of every five days null, and forward-filling to daily is a modelling preference the build must not bake in. They need a sibling store at their own grain; see the plan's §0.3.

### TerraClimate — the same server, a different resolution trade

`/thredds/catalog/TERRACLIMATE_ALL/data/` — **3,192 files, 1950–2025, monthly, ~4 km global**, one file per (variable, year), anonymous, with OPeNDAP alongside. Grids are 4320 × 8640 at 12 timesteps per file (verified).

Fourteen observed variables: **`soil`** (soil moisture), **`q`** (runoff), **`swe`** (snow water equivalent), **`aet`** (actual evapotranspiration), **`def`** (climatic water deficit), `PDSI`, `ppt`, `pet`, `tmax`, `tmin`, `srad`, `vap`, `vpd`, `ws`. The `plus2C`/`plus4C` and `counterfactual` trees are climate-scenario runs — out of scope here.

**Why it matters for the antecedent-conditions question:** `soil`, `q`, `swe` and `def` are exactly the state variables NLDAS was recommended for, and TerraClimate is **anonymous** where NLDAS requires an Earthdata account and ~163k authenticated requests. The trade is temporal: TerraClimate is **monthly**, NLDAS is hourly. For a *daily* nitrate model, a monthly soil-moisture value is a weak feature — it cannot separate the week before a storm from the week after. So this does not replace NLDAS for antecedent state; it is a cheap **seasonal-context** layer (how wet was this season relative to normal) and a zero-auth way to test whether soil-moisture-family features earn their place at all before paying for the hourly harvest. That test is worth running first.

### DROUGHT_LAYERS — mostly out of scope, one item worth noting

`/thredds/catalog/DROUGHT_LAYERS/` holds seven collections, all anonymous:

| collection | what | contents |
|---|---|---|
| `ESI` | Evaporative Stress Index | 854 files, weekly (`esi_12week_YYYYDDD.nc`), 2010+ |
| `MCDI` | soil-moisture / snow fractions | 509 files, monthly from 1980 |
| `LERI` | Landscape Evaporative Response Index | 124 monthly files, 2015+ |
| `FDSI` | Forest Drought Stress Index | one CONUS file, 1980–2020, gridMET-derived |
| `ANOMALIES` | monthly anomaly series for pdsi/pet and others | 4 files |
| `GRACE` | satellite gravimetry water storage | (timed out; GRACE is ~300 km resolution) |
| `QUICKDRI` | quick drought response index | empty at the catalogue level |

Verdict: **skip nearly all of it.** ESI and LERI are evaporative-stress indices aimed at agricultural drought monitoring, weekly-to-monthly, and largely redundant with the `eddi`/`spei` family already available in gridMET at finer temporal resolution. FDSI is forest-specific. GRACE is far too coarse for 1.4 km² catchments. The one item worth a look is **`MCDI`'s soil-moisture and snow fractions** — monthly from 1980, anonymous — for the same "cheap test before the NLDAS harvest" reason as TerraClimate.

### Stage IV / MRMS — no; AORC dominates it

Stage IV is anonymous at IEM back to 1997, but it is 796 KB per hour of whole-CONUS GRIB1 with no server-side subsetting — **130 GB** for our window — and the environment has **no GRIB reader at all** (`cfgrib`, `eccodes`, `pygrib` absent; rasterio's GDAL lacks the GRIB plugin). MRMS only reaches back to October 2014.

**AORC v1.1 beats it on every axis but currency**: `s3://noaa-nws-aorc-v1-1-1km`, **anonymous**, zarr, 800 m (finer than Stage IV's 4.76 km), hourly, 1979–2025, with eight forcing variables. A full year of hourly precipitation at one point took 2.1 s; the whole AOE precipitation-only 2008–2025 is ~23 GB. Its one limitation: the bucket stops at 2025, so the current year still needs gridMET or MRMS.

**But the honest verdict on radar skill is negative.** Within one gridMET 1/24° cell, the spatial standard deviation of daily precipitation on wet days is only **6–13% of the cell mean** — 6% on a 37 mm day. At our 1.4 km² median catchment, the sub-4-km structure recoverable by a finer product is worth single digits, and sub-daily timing is discarded by definition once we aggregate to a day. **The reason to pull AORC is the day boundary, not the resolution**: hourly data lets us aggregate on whatever boundary the nitrate uses instead of inheriting gridMET's.

### Soil temperature and SMAP — both refused, on measurement grounds

**No observation-based CONUS gridded soil temperature product exists**; everything gridded is land-surface-model output. Station networks cannot substitute: USCRN is ~250 CONUS station-years with soil probes only installed CONUS-wide in **2011** (so no record for our 2008–2010), SCAN adds ~200, SNOTEL is mountain-west. Against 1.5M catchments that is one station per 3,000–5,000 catchments — a 100 km interpolation distance for a variable set locally by soil texture, residue, tillage, drainage and snow cover, none of which the interpolation sees. What comes back is a smooth lagged function of air temperature, which `tmmn`/`tmmx` already give us free. Take `SoilT_0_10cm` from NLDAS since we will be pulling NLDAS anyway, and add cumulative growing- and freezing-degree-days from the gridMET temperatures already on disk — cheap, and it carries most of what a 10 cm soil temperature says about mineralization timing.

**SMAP fails on any one of three grounds**, and all three hold: coverage starts **2015-03-31**, missing 7 of our 18 years (a feature present for 60% of rows and null for the rest is a date proxy, and our CV groups by site and basin, not by era — it would leak rather than be caught); 9 km cells are 81 km² against a 1.4 km² median catchment, coarser in usable terms than NLDAS; and L4 is itself a land-surface model with brightness temperature assimilated, so against NLDAS we would trade one model for another while losing 7 years and 2 layers of profile depth.

---

## Recommended order, everything considered

1. **Fix the gridMET day alignment.** Free, no new data, and it is corrupting the central precipitation→nitrate relationship on a quarter to a third of corn-belt water.
1b. **Re-fetch weather with the five restored fire-weather variables** (`fm1000` above all — the one weather feature the shipped models kept). Already wired; costs one weather pass.
2. **WQP via WQX 3.0.** The node population is the binding constraint, and the legacy endpoint we currently use is dropping USGS data silently.
3. **Complete the DMR acquisition** (flow, outfall key, current year — code already fixed, needs a re-fetch).
4. **SNODAS.** Anonymous, cheap, supplies phase and melt.
5. **Add the `Hydrologic` and `Regions` Wieczorek themes.** A config-line change for the flow-pathway partition.
6. **NWM streamflow** for a cornbelt-compact reach set, spliced with `analysis_assim_no_da`.
7. **Test soil-moisture-family features cheaply first** — TerraClimate `soil`/`q`/`swe`/`def` (monthly, anonymous, same server) or MCDI, before paying for **NLDAS-2**'s Earthdata harvest and ~163k authenticated requests. If a monthly soil-moisture context feature earns nothing, the hourly version is unlikely to earn its cost either; if it does, the harvest is justified by measurement rather than by argument.
8. **StreamCat annual N families** in place of gTREND's extra rasters; **karst** and **Sekellick confined/unconfined manure** as the two genuinely-new static layers.
9. **SPARROW `sh_*` shares only**, after a calibration-station intersection audit.
10. Skip: OpTIS, SMAP, Stage IV/MRMS, gTREND's redundant components, Annual NLCD `LndCov`/`LndCnf`.

## Evidence quality

Everything above marked as measured was verified by live probe, byte-level parsing, or computation on downloaded data during this review — including every access URL and auth outcome, the WQP endpoint comparison and its speciation counts, the DMR value-type semantics and duplicate structure, the SPARROW standard-error signature, the NWM chunk arithmetic and no-assimilation documentation, the Wieczorek theme catalogue, the SNODAS grid and March 2019 melt series, and the gridMET offset scan.

Inferred rather than measured, and flagged where it matters: SPARROW's literal `if_adjust` control-file token (the standard-error signature is diagnostic but indirect, and confirming it costs an 8.4 GB download); the weather-normalized scenario's conditioning state; gridMET's day convention for variables other than `pr`; the 27–36% displaced-precipitation figure (4 points × 2 years, not an AOE-wide computation); Annual NLCD's requester-pays S3 path; and several size estimates extrapolated from sampled files, marked as such in the sections above.
