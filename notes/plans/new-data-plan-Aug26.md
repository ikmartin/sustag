# Plan — incorporating the recommended data sources into the pipeline

Companion to `notes/new-data-report.md`, written against build4 as it stands on 2026-08-26 (a1–a4 closed, d1–d4 verified, d5 running, p1 pending its quality gate). Every item below states **where in the existing architecture it lands**, because the pipeline's shape already answers most of the design questions: acquisition adapters write to `acquired/`, snapshots manifest what arrived, derivation reduces to catchment or record grain, publication partitions and masks, and the access layer reads. A source that does not fit one of those slots is a source we do not yet understand.

Ordered by **value per unit of disturbance**, which is not the same as the report's ranking by value.

---

## Phase 0 — the free fixes (no new data, hours not days)

### 0.1 The gridMET day boundary

The single highest-value item in the whole review, and it acquires nothing.

**What:** gridMET's daily `pr` is a 12Z→12Z accumulation labelled by the start day; our observation day is Central Standard calendar (`io.STANDARD_TZ`). Six hours apart, 27–36% of corn-belt rain in the displaced window.

**Where it lands:** *not* in acquisition. The build stores what gridMET publishes, correctly. Three candidate homes, in order:

1. **`data/access/api.get_weather`** — the convention is a property of the source, so it belongs beside the reader. Add a `day_convention` parameter (`"source"` | `"local"`), defaulting to `"source"` so nothing changes silently, with `"local"` shifting the label. Document the offset in the docstring regardless of what the default is.
2. The feature layer of each modeling project, if the access layer stays neutral.
3. Re-derive precipitation from AORC hourly (Phase 3), which removes the inherited convention entirely.

**Before any of it: test the other variables.** The offset is verified for `pr` only. If `tmmn`/`tmmx`/`srad` follow a different convention, a single daily row currently mixes day definitions and the fix is per-variable. One afternoon: correlate each gridMET variable against AORC hourly aggregated at all 24 offsets, same method the review used, at a handful of sites.

**Verification:** a check that re-derives one site-year of daily precipitation from AORC on the local-calendar boundary and compares against the shifted gridMET series — agreement at r > 0.95 confirms the shift; disagreement means the convention differs by variable or by region.

### 0.2 Complete the DMR acquisition

Code already fixed during the review (`data/build/acquire/facilities.py`): the flow parameter is no longer excluded by the nitrogen word-filter, `PERM_FEATURE_NMBR` is retained on outfalls (already re-extracted, 205,312 populated), and the current fiscal year is fetched.

**Remaining:** a re-fetch of the DMR archives so the flow rows land — 18 fiscal years, ephemeral download-filter-discard, roughly 30–45 minutes of wall time. Then the loading derivation, which is genuinely new work and belongs in **`derive/structures.py`** beside the existing outfall snapping:

- filter `MONITORING_LOCATION_CODE ∈ {1, EG}` (effluent gross) — `G` is **raw sewage influent**, 71,475 rows in FY2023, and summing it as discharge is the error this filter exists to prevent
- deduplicate on `DMR_VALUE_ID` (repeats differ only in violation code, values identical)
- species-convert by parameter code (`71850` as NO₃ vs `00620` as N both report `mg/L` — the same trap WQP has, and the same one the registry's turbidity split already guards against)
- Tier 1 (`Q1` monthly-average quantity) → Tier 2 (`C2` × flow) → Tier 3, EPA's own hierarchy
- treat NODI `C` as a **real zero**, not a gap; expect only 39% of series to report all twelve periods

**Where the product lands:** a monthly `(npdes_id, perm_feature_nmbr, comid, month, kg_n)` table in `derived/structures/`, joined to reaches through the snapped outfalls. **Not** a sensor and not a record — it is a covariate on the network, and the sensors table must not grow rows for it.

### 0.3 gridMET's unfetched aggregates

Measured against the endpoint's own catalogue on 2026-08-26: **the aggregated MET catalogue publishes 42 variables and the build was pulling 11.**

**Done already** — the five DAILY ones are added to `VARIABLES`/`LONG_NAMES` and need only a weather re-fetch: `fm1000`, `fm100`, `bi`, `erc`, `th`. All five verified serving real data. `fm1000` is the important one: it was the **only** weather variable that survived feature curation in either shipped model, and the rebuild dropped it silently because the variable tuple was assembled from the standard long-name set rather than from the catalogue.

**`snow` is not available** despite `pygridmet` mapping it — the name resolves and returns zero bytes, and it is absent from the aggregated catalogue. So the phase split still needs SNODAS (Phase 1.2); there is no cheap substitute.

**The 25 drought aggregates are a separate change, because of cadence.** `spi`, `spei` and `eddi` each ship at eight accumulation windows (14d, 30d, 90d, 180d, 270d, 1y, 2y, 5y), plus `pdsi` and `z` (drought category). All are **PENTAD** — 3,404 timesteps since 1979, one per five days — while `acquired/weather/` is one row per (cell, **day**). Landing them in that store would leave four of every five days null, and forward-filling to daily is a modelling preference the build must not bake in (the slogan test: a model might legitimately want the pentad stamp).

They are worth having. `spei90d`/`spei180d` are properly-computed multi-scale water-balance deficits — the same quantity this project currently hand-rolls as a 40-day (ET − precipitation) integral, at eight timescales and standardized. The change is a **sibling store** (`acquired/weather_pentad/`) on the same fetch path with its own date grain, plus an access-layer reader that states the cadence. Sized like the daily variables per-variable, so 25 of them is the cost to weigh.

### 0.4 Two Wieczorek themes

`WIECZOREK_THEMES` in `data/build/acquire/attributes.py` pulls 7 of the catalogue's 14 themes. Add **`"Hydrologic"`** (30 attributes: `CONTACT`, `IEOF`, `SATOF`, `RECHG`, `BFI`, `EWT`, `TWI` — the flow-pathway partition) and **`"Regions"`** (407: HLR, Fenneman, EPA L3 ecoregion). Note `"Hydrologic"` ≠ the `"Hydrologic Modifications"` already pulled, which is dams and canals.

~0.3–0.5 GB after the AOE filter, COMID join, no new code. Costs one a3 attributes re-run.

**One discipline note:** the regional categoricals are as much *grouping* candidates as features. They will look strong under LOSO and weak under LOFO, which is exactly what the LOSO−LOFO gap column exists to expose — and if a model's lift comes from ecoregion, that is a finding about the CV design, not a feature win.

---

## Phase 1 — the node population (the one that changes what the project can be)

### 1.1 Water Quality Portal as a source adapter

**This is the largest single change in the plan, and the architecture already has the slot for it:** a new adapter under `data/build/acquire/sources/wqx.py`, alongside `usgs.py` / `iwqis.py` / `mpca.py`, implementing the same uniform interface (`discover`, `fetch`, `DECLARES_SPANS`, coordinate precision from source text).

**Name the source `WQX`, not `WQP`.** IWQIS already owns a `WQP\d+` namespace — IIHR's conservation-practice programme (`IWQIS:WQP0001`...), unrelated to the portal — so a `WQP:` source prefix would put two different WQPs in one sensors table forever. WQX (Water Quality Exchange) is the portal's own data-system name and what the modern endpoint is literally called (`/wqx3/`), so the honest prefix is also the unambiguous one.

**What it adds:** ~2.16M discrete nitrate results across 32 AOE states; in Iowa alone 2,318 nitrate stations of which 83% are non-USGS. Against our 372 nitrate-bearing sensors, this is an order-of-magnitude change in the node population.

**The design questions it forces, and the answers the pipeline's own principles give:**

- **Registry.** WQP results are *discrete grab samples*, not continuous series. The registry's channel model already handles per-sample values; what changes is density, not kind. Add `nitrate_discrete` as a channel distinct from `nitrate`, because the two are different measurement processes and the slogan test applies — a model might legitimately want continuous-only, and it cannot un-pool them if the build merges them.
- **Speciation.** Query `wqx3` with `dataProfile=narrow`; key the unit factor on `(Result_MeasureUnit, Result_MethodSpeciation)`, never the unit alone. Accept `Nitrate`, `Nitrate + Nitrite`, `Inorganic nitrogen (nitrate and nitrite)`; **refuse** Kjeldahl, ammonia, total-N forms — they are not nitrate and no factor makes them so. This is the turbidity discipline applied to a second source.
- **The USGS overlap.** WQP's `Location_Identifier` is literally `USGS-{site_no}`, so the already-held subset is identifiable by string equality. Two options, and the registry-gaps ledger suggests the answer: register WQP stations that are *not* already ours, and for those that are, treat the discrete samples as an additional channel on the existing registration rather than a second sensor. The identity machinery would otherwise be asked to merge them, which is work the join key already did for free.
- **Deduplicate after conversion, never before** — every USGS activity carries the measurement twice (as N and as NO₃).
- **The pull:** per state × characteristic, `zip=yes`, ~130 requests for the AOE-decade, sized by the free `Total-Result-Count` HEAD. Watch for the `ERROR: INCOMPLETE DATA` sentinel that arrives with HTTP 200 and a valid header row.

**Where the cohort filter absorbs it:** `filter_sites` and the quality tier both assume dense series. A station with 40 samples over 12 years is a legitimate node for a spatially-inductive model and useless for a daily-timing one — so the *build* should publish it with its true density and let each project's cohort filter decide, exactly as the xgboost project's `MIN_NITRATE_DAYS` already does.

**Also fix, separately:** `experiments/discrete-sample-pre-check/01_fetch_wqp.py` and `02_audit.py` hardcode the legacy endpoint and have been silently missing all post-2024-03-11 USGS data.

### 1.2 SNODAS

A new branch in `acquire/weather.py` (or its own `acquire/snow.py` — the weather module is already large). Anonymous HTTPS, one tar per day, flat big-endian int16 with ASCII headers, no new binary dependency.

**Products to keep:** SWE (`11034`), melt (`11044`), and the rain/snow accumulation split (`01025 SlL00`/`SlL01`). **Resample onto the gridMET 1/24° grid at ingest** — 21.6 GB stored versus 270 GB native, and it lands on the grid the weather weights already key to, so the existing catchment reduction machinery works unchanged. Fill melt's −9999 with 0 (no snowpack is zero melt, not missing).

**The derived feature that matters:** `water_input_mm = rain + melt`, to be used in place of `pr` in winter. The review's measured case is the argument — 44 mm of melt at Ames on 2019-03-14 where gridMET's `pr` reports 6 mm, i.e. blind to 88% of the water reaching the soil.

---

## Phase 2 — modeled flow (the biggest single covariate, at real cost)

### 2.1 NWM streamflow

**Where it lands:** `acquire/nwm.py` already exists and already partitions by COMID — the module was written for exactly this and currently pulls the retrospective for the sensor-COMID list. What changes is scope and the splice.

- **Scope by compactness, not by count.** The zarr chunking is `[672 h, 30000 features]`, so one COMID costs what 30,000 do; a scattered CONUS set reads 326 GB regardless. Our 342 nitrate COMIDs (268 in the cornbelt) are compact enough that a bounded pull is 3× cheaper than a national one.
- **Check CHANOBS first.** `CONUS/netcdf/CHANOBS/` carries 8,643 *gaged* reaches at 195 KB/hour (~26 GB for 2008–2023). Many of our sensors are USGS gages. **A set intersection between our snapped COMIDs and the CHANOBS feature list is a ten-minute check that could save 300 GB** — do it before committing to the zarr route.
- **Splice for 2023→present** from `gs://national-water-model`, configuration **`analysis_assim_no_da`**. This is not optional: the default `analysis_assim` assimilates gauge observations into 5.9% of reaches and routes them downstream, which would reintroduce the leakage the retrospective is valuable for avoiding.
- **Flag the version discontinuity.** 2023-02 → 2023-09 is NWM v2.2, not v3.0. A `nwm_version` column, not a silent concatenation.
- **Take `streamflow` and `q_lateral`/`qSfcLatRunoff` only.** They are already per-reach. Skip `ldasout` entirely — 1–7 TB per variable on a 1 km grid needing basin aggregation, for signal the channel terms partly carry.

**Leakage:** clean and documented (no data assimilation in the retrospective, no `nudge` variable in the store). It needs no audit, which is precisely what makes it more attractive than SPARROW.

### 2.2 NLDAS-2 state variables

`acquire/weather.py`, a second branch. Earthdata Login required — a `.netrc` and one account, the only auth-gated source in this plan. Take `SoilM_0_10cm`, `SoilM_0_100cm`, `SoilT_0_10cm`, `SWE`; **reduce to daily in flight** and store on the native 1/8° grid (6.4 GB, versus 1.18 TB of raw granules).

**Refuse `Qs`/`Qsb` as catchment runoff.** A flux attributed to a 1.4 km² catchment cannot come off a 146 km² cell. This distinction — state variables survive the resolution mismatch, fluxes do not — is the rule for every gridded land-surface product, and it is worth writing into the module docstring so the next person does not re-litigate it.

---

## Phase 3 — the narrower additions

### 3.1 SPARROW, shares only

**Take:** `sh_*` source-apportionment shares, `DEL_FRAC`, `rchtot`, `tile`. **Refuse:** `PLOAD_TOTAL`, `concentration`, and everything routed.

The reasoning is the report's §4: the shipped predictions are *conditioned* — at calibration reaches the observed load is substituted and routed downstream — but the model's own documentation computes shares as *predicted share × monitored flux*, so the shares are genuine predictions. Composition is safe; magnitude is not.

**Prerequisites, in order:**
1. **Calibration-station intersection audit.** `data1.csv` carries `lat_tn`/`lon_tn` for all 2,424 TN stations, so the audit is a spatial join and needs no identifier-scheme resolution. Any of our sensors at or downstream of one is in the conditioned set.
2. **GenID collision check on our actual cohort.** The key is GenNet's `genid`, not COMID — a ~10:1 aggregation, median reach 4.42 km. Random COMIDs collide at 4.3%; our deliberately-paired sub-kilometre sites will collide far worse, and pairs sharing a GenID receive *identical* SPARROW values. If the collision rate on our cohort is high, SPARROW cannot be a per-site feature at all.
3. **Accept the temporal limit.** It stops November 2020 against a record running to 2026, so it is a static or slowly-varying basin descriptor, never contemporaneous.

Cost ~53 GB of all-or-nothing zips (no HTTP Range). Do the two checks *before* the download; either could disqualify it.

### 3.2 Karst, and Sekellick confined/unconfined manure

Both land in `derive/zonal.py`'s existing catchment-overlay path.

- **Karst** (Weary & Doctor, 282 MB): the only geology dataset in the review with no substitute on disk. Verified zero karst attributes across all 14,139 Wieczorek and 167 StreamCat families, and `pctcarbresid` does *not* substitute — karst units are classed by **burial depth**, so a carbonate aquifer under 40 ft of till scores zero carbonate-residual while being exactly the cover-collapse setting that routes nitrate to groundwater. Our AOE straddles the glacial margin, where that distinction lives.
- **Sekellick & Sherr** (728 MB, direct COMID join): the **confined vs unconfined manure** split, an axis nothing else we hold provides. Four census years only.

### 3.3 StreamCat annual N families

Not an acquisition change so much as a *request* change: `latest_vintage()` deliberately collapses each metric family to its newest member (all vintages would be 2,371 columns, 17.8 hours, 28 GB). The annual N series — farm fertilizer, livestock waste, recovered waste, crop fixation and removal, deposition, human waste, agricultural surplus, accumulated legacy N, 1987–2017 — exists and is simply unasked-for.

Request those families explicitly through the existing quota-aware path. **This substantially replaces the case for ingesting gTREND's other 20 components**, because gTREND downscales county totals by a binary agricultural mask and therefore carries no sub-county information — a catchment mean of gTREND surplus is an area-weighted county mean, which is what StreamCat already delivers, COMID-keyed and annual.

What gTREND would still uniquely add, if wanted later: manure by animal class, reduced-vs-oxidized deposition, cropland-vs-pasture splits. Figshare supports HTTP Range, so our 18 years are ~18% of the bytes.

---

## Explicitly not doing

- **OpTIS** — released only at HUC8/CRD (~4,000 km²), 60% tillage accuracy, ends 2021, no bulk download. The spatial unit is close enough to our basin grouping to be a leakage risk rather than a weak feature. Substitute: Census of Agriculture county cover-crop acreage (2012/2017/2022, free API).
- **SMAP** — starts 2015 (a feature null for 40% of rows is a date proxy our CV would not catch), 81 km² cells against 1.4 km² catchments, and it is a land-surface model anyway.
- **Stage IV / MRMS** — 130 GB of whole-CONUS GRIB with no subsetting, and no GRIB reader in the environment. AORC dominates it on resolution, access and dependencies; its only advantage is 2026 currency.
- **Annual NLCD `LndCov`** — CDL's non-ag classes are NLCD-derived by NASS's own statement, so it is a lossy re-derivation of what we have. `FctImp`/`ImpDsc` (continuous annual imperviousness, road/urban split) are the parts worth having, later. `LndCnf` never — 317 GB of per-pixel confidence.
- **Brakebill & Gronberg, Gronberg & Arnold** — upstream of gTREND, not independent.
- **Re-acquiring** Soller surficial materials, Principal Aquifers, bedrock permeability, Olson lithology, STATSGO soils, NLCD class fractions. All verified present.

---

## Sequencing, and what gates what

```
Phase 0  gridMET day test + fix ....... independent, do first
         DMR re-fetch + loading ....... independent
         Wieczorek themes ............. one a3 attributes re-run

Phase 1  WQP adapter .................. new registry channel -> a2 census -> a3 -> d2 -> d4
         SNODAS ....................... a3 weather branch -> d5 weights (grid unchanged)

Phase 2  NWM (CHANOBS check first) .... a3 nwm branch, needs d3's sensor-COMID list
         NLDAS-2 ...................... a3 weather branch, needs an Earthdata account

Phase 3  SPARROW (2 checks first) ..... d5 covariate, or refused
         karst / Sekellick ............ d5 zonal
         StreamCat annual N ........... a3 attributes request
```

The WQP adapter is the only item that touches the identity and census machinery, so it is the only one whose blast radius includes the review queues. Everything else is additive: a new acquired product, a new derived covariate, a new column.

**Chapter updates are deliberately deferred.** Both `data_pipeline_chapterv2.md` and `data_inventory_chapter.md` will need substantial additions — a new source section per adapter, the inventory's master table extended, the re-materialisation classes revisited — and they should be written *after* the sources are in and their quirks measured, not from this plan's expectations.
