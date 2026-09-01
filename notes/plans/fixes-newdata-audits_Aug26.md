# Fixes, New Data and Audits

This is a three-part plan to fix documented problems with the existing pipeline (5 of them), to incorporate new data sources and to audit everything together once it is complete. Companion to `notes/new-data-report.md` (what the sources are, with access routes verified live) and `notes/plans/new-data-plan-Aug26.md` (the phasing this plan supersedes and absorbs). Here is a summary of these steps.

- **Fixes** — for each of the five known fixes, make a plan to implement the fix, to make a detailed one-time check that the issue is resolved, and to write a check which will run in `data.build.verify` to ensure that the issue does not reoccur. For example, for the gridMET day boundary issue, gridMET should be properly converted to a time zone which aligns with the rest of the data. A one-time check should be run to verify it matches the time conventions of the rest of the data by hand. A permanent check should then be written which ensures all time series data streams across the dataset are aligned and in agreement, that `2026-01-01T03:26:54` means the same instant on earth for all data sources. If a fix has already been implemented, it should be tested and a permanent check should be written which arises in the `data.build.verify all` call. Record a detailed plan for each of the 5 fixes in the Fixes section below. This plan should record details on the issue, a specific plan for fixing it with implementation instructions, a detailed description of the verify check to be implemented, and instructions for additions to be made in the data pipeline chapter (document both any changes to the pipeline AND the verification checks, the latter go in the verification section of course).
- **New Sources** — for each of the proposed sources, implement it in the pipeline. Record a detailed plan for each new source in the New Data section below. This plan should include justification for the new data, justification for the phase of the pipeline in which it is to live, detailed instructions for acquisition, a list of anticipated data quirks, and anything else you may anticipate. The gTREND rasters now sitting in `raw/gTREND/` are captured in this portion of the project in addition to the other sources.
- **Audit** — The point of this step is three-fold: to establish what is actually reachable from what we hold, to better understand the relationships between the data that already exists, and to audit the value of currently-gated data sources before spending the energy to acquire and incorporate them.
  - **Tier 0 — Exposure Accounting** — a complete reconciliation of *what is on disk* against *what `data.access` exposes*. Not thematic; it is about knowing what we can actually reach. It exists because roughly 1,150 per-COMID attributes across StreamCat and the Wieczorek release are acquired, snapshotted, and readable by no model.
  - **Thematic Audit** — there is thematic overlap between various data in the pipeline and there will be more once the new data lands. Identify a collection of themes and then for each make a complete list of the data inputs that fit under that theme (a data source can potentially fit under multiple themes). Then compare the data under each theme. What does it cover? What are the similarities? What gaps exist in one data source that are covered by another? Is any data source redundant? **The themes are enumerated from the domain, not from our holdings** — from what governs nitrate in a watershed — and then populated. A theme with no members is a finding, not an omission from the list.
  - **Gated Data Audit** — some data sources which might be highly valuable are currently gated behind audits to determine their worth. Notable among them is SPARROW, which could be quite useful but would require a lot of energy to incorporate. Audit these data sources to determine their worth. The deliverables here are (short) reports, one per audited source, detailing what the data source is, why it is gated, what the audit is, what the audit determines, what it does NOT determine, and a verdict on inclusion. Put these in `notes/reports/`; for SPARROW title it `data_audit_SPARROW_Aug26.md`, and adhere to a similar naming convention for the others.

## The rule that governs every permanent check

**A permanent check asserts a correct state. It never immortalises the bug.** The time-alignment check does not say "gridMET is shifted six hours"; it says every time series in the store agrees on what an instant is. The first formulation dies the moment the bug is fixed and protects nothing afterwards; the second catches the next source that arrives with a different convention. Where a fix suggests a narrow assertion, restate it as the invariant it is a special case of, and register that.

Two corollaries. A check that would pass on an empty store is not a check — the p1 leak scans return `None` (SKIP) when there is no published water, and that is the correct shape. And a check belongs in the module whose artifact it audits, sharing that module's constants and helpers rather than duplicating them; the `fetch_status` coverage bug of 2026-08-26 was a check that re-implemented a rule instead of importing it.

## Execution order

```
1. Fixes ................... independent of everything else, do first
2. Gated Data Audit ........ SPARROW, TerraClimate, NWM-scope
3. Decide .................. audit verdicts may ADD to or REMOVE from the source list
4. New Sources ............. including whatever step 3 approved
5. Tier 0 Exposure ......... after the new data lands, so the accounting is complete
6. Thematic Audit .......... tiers 1-2, over everything then in the pipeline
```

The Gated Audit precedes the sources because that is its whole purpose — to decide before paying. The Thematic Audit follows them because it describes what we ended up with. Tier 0 sits between: it needs the new sources on disk, and its output (what is acquired but unreachable) feeds the theme coverage verdicts directly.

---

# Fixes

## Fix 1 — the gridMET day boundary

**The issue.** gridMET's daily `pr` is a 12Z→12Z accumulation labelled by the *start* day. Our observation day is Central Standard, year-round (`io.STANDARD_TZ` = `Etc/GMT+6`), so the gridMET day runs 06:00–06:00 CST against our 00:00–00:00. Verified by correlating gridMET `pr` against AORC hourly precipitation aggregated at all 24 possible offsets: the maximum correlation sits six hours off the local calendar day, and **27–36% of corn-belt rain falls in the displaced window**. The consequence is not a small bias; it is a systematic mis-dating of the single most important dynamic driver, which corrupts every rain→nitrate lag relationship a model tries to learn.

**Scope of what is verified.** The offset is measured for `pr` only. Whether `tmmn`, `tmmx`, `srad`, `pet` and the rest share it is unknown, and the answer changes the shape of the fix: if conventions differ per variable, a single daily row in `acquired/weather/daily/` currently mixes day definitions, and no single shift repairs it.

**Step 1 — the one-time check (do this before writing any fix).** Correlate every acquired gridMET variable against AORC hourly aggregated at all 24 offsets, at a handful of corn-belt sites spanning the AOE, and tabulate the argmax offset per variable. Deliverable: a small table, variable × best-offset, recorded in the fix's write-up. This is an afternoon of work and it determines whether the fix is one shift or a per-variable map.

**Step 2 — the fix.** It does **not** belong in acquisition: the build stores what gridMET publishes, correctly, and rewriting the archive to a different convention would destroy the ability to check this ever again. It belongs at the read boundary, in `data/access/api.get_weather`:

- add `day_convention: {"source", "local"} = "source"` — defaulting to `"source"` so no existing read changes silently;
- `"local"` shifts the label by the measured offset (per-variable if step 1 says so);
- document the offset and its provenance in the docstring **regardless of the default**, because the trap is that the data looks fine either way.

If step 1 finds per-variable conventions, the offsets live in a module-level constant beside `LONG_NAMES` in `acquire/weather.py` (the convention is a property of the source, and that is where source facts live), and the access layer reads it.

**Step 3 — the permanent check.** Not "gridMET is shifted correctly". The invariant is:

> **Every time-series stream in the store agrees on what an instant is.**

Register in `data/build/derive/canonical.py` or a new `data/build/timebase.py`, as `@verify.check("d2", "every time-bearing store declares its day convention and they agree", deep=False)`. Concretely, the check walks every store that carries a date or datetime column — canonical water series, the weather daily store, the weather pentad store when it lands, NWM, SNODAS, DMR monthly — and asserts that each declares a `day_convention` (or an explicit UTC offset) in its metadata, that the declared value is one the registry knows, and that any two stores claiming the same convention agree. A store with no declaration is a FAIL, not a skip: an undeclared convention is exactly how this bug arrived.

The deep companion, `@verify.check("d2", "gridMET's local-day series reproduces an independent hourly aggregation", deep=True)`, re-derives one site-year of daily precipitation from AORC hourly on the local-calendar boundary and compares against the shifted gridMET series; agreement at r > 0.95 confirms the shift, disagreement means the convention differs by variable or by region.

**Chapter additions.** Body: a paragraph in the covariates section stating gridMET's native day convention, our standard day, the measured offset, and where the conversion happens — with the explicit note that the archive is stored unshifted on purpose. Verification section: both checks, each with what its failure would mean ("a source arrived without declaring its day convention" / "the shift no longer reproduces an independent aggregation").

## Fix 2 — the DMR loading derivation

**The issue.** Three defects in `data/build/acquire/facilities.py`, all now patched in code and all verified by measurement: the nitrogen word-filter excluded parameter `50050` (plant flow), so half the effluent monitoring cells carried a concentration with no quantity beside it and could never become a mass; `PERM_FEATURE_NMBR` was dropped from the outfalls extract, so loading could only be placed at *permit* grain, which is not a location on a river; and the fiscal-year range excluded the current year. FY2023 went from 1,483,580 rows to **4,052,650** after the re-fetch, of which **1,885,461 are flow rows** (1.72M of them "Flow, in conduit or thru treatment plant", MGD). All 19 fiscal years including FY2026 are on disk; 205,312 outfalls carry the join key.

**What remains: the derivation.** Genuinely new work, and it belongs in **`data/build/derive/structures.py`** beside the existing outfall snapping. The rules, each of which exists because the raw data punishes the naive version:

- filter `MONITORING_LOCATION_CODE ∈ {1, EG}` (effluent gross). Code `G` is **raw sewage influent** — 71,475 rows in FY2023 — and summing it as discharge is a serious, silent error.
- deduplicate on `DMR_VALUE_ID`; 5,999 duplicate rows differ only in violation code and carry identical values.
- species-convert by parameter code before any arithmetic: `71850` reports as NO₃ and `00620` as N, both in `mg/L`. This is the same trap WQX has and the same one the registry's turbidity split already guards against.
- follow EPA's own tier hierarchy: Tier 1 (`Q1`, monthly-average quantity, already a mass) → Tier 2 (`C2` concentration × period flow) → Tier 3.
- treat NODI code `C` (no discharge) as a **real zero, not a gap** — 263,064 rows in FY2023. Expect only 39.2% of series to report all twelve periods.

**The product.** A monthly `(npdes_id, perm_feature_nmbr, comid, month, kg_n)` table in `derived/structures/`, joined to reaches through the snapped outfalls. **Not a sensor and not a record**: it is a covariate on the network, and the sensors table must not grow rows for it.

**Phase justification.** Acquisition is done (a3). The derivation is a reduction to network grain, which is d5's job — it is the same shape as the dam and facility snapping already there.

**The permanent check.** The invariant is mass conservation and species consistency, not "flow is present":

> `@verify.check("d5", "every DMR loading row is a mass in kg N, derived by exactly one declared tier")`

asserting: every row carries a `tier` column naming which rule produced it; no row derives from a `G`-coded observation; no `DMR_VALUE_ID` appears twice; every species conversion factor applied is one the registry declares; and `kg_n` is non-negative with explicit zeros where NODI `C` fired (a null and a zero mean different things here and the check enforces the distinction).

A second, cheaper check guards the acquisition itself: `@verify.check("a3", "the DMR archive carries flow alongside nitrogen for every fiscal year")` — asserting each fiscal-year parquet contains rows matching the flow parameter codes. That is the one that would have caught the original bug, and it is stated as a property of a complete archive rather than as a memory of the defect.

**Chapter additions.** Body: extend the Structures subsection of § Covariates with the loading product — its grain, the tier hierarchy, and the influent/dedupe/speciation/NODI rules. Verification: both checks.

## Fix 3 — the restored fire-weather variables

**The issue.** The gridMET aggregated catalogue publishes 42 variables; the build was pulling 11. The variable tuple was assembled from `pygridmet`'s standard long-name mapping rather than from the endpoint's catalogue, and the omission silently dropped **`fm1000` — the only weather variable that survived feature curation in either shipped model** (the predecessor's recipe comments it as "the only weather var with held-out signal; the rest are perm-dead in every run").

**Status.** `fm1000`, `fm100`, `bi`, `erc` and `th` are added to `VARIABLES` and `LONG_NAMES` in `data/build/acquire/weather.py`, each verified serving real data by a one-day request. **What remains is the fetch.**

**Implementation.** One weather pass. Read the merge path in `weather.py` before launching it and determine whether the five new variables can be fetched and folded into the existing quarter files, or whether all sixteen must be re-fetched — the fetcher works per (variable, box, window), so a merge-only pass should be possible and is worth an hour of reading to establish. Either way the store layout is already correct: `acquired/weather/grid.parquet` shared, `acquired/weather/daily/` for the quarters.

**Note for the record.** `snow` is *not* available despite `pygridmet` mapping it — the aggregate returns HTTP 200 with zero bytes and it is absent from the catalogue. There is no cheap precipitation-phase split; that need is met by SNODAS.

**The permanent check.** Not "fm1000 is present". The invariant:

> `@verify.check("a3", "the weather store carries every variable the registry declares, for every quarter in the window")`

comparing the column set of each quarterly parquet against `weather.VARIABLES`, and the quarter set against the collection window. A variable declared but absent anywhere is a FAIL. This generalises to every future variable added to the tuple, which is precisely the failure mode that occurred.

**Chapter additions.** Body: update the gridMET variable list in § Covariates, and add a sentence recording that the catalogue holds 42 aggregates of which we take 16 daily (plus the pentad set, Fix/Source below), with the reason for each exclusion. Verification: the completeness check.

## Fix 4 — the two missing Wieczorek themes

**The issue.** `WIECZOREK_THEMES` in `data/build/acquire/attributes.py` pulls 7 of the catalogue's 14 themes. Two of the missing seven matter:

- **`"Hydrologic"`** — 30 attributes describing the flow-pathway partition: `CONTACT` (subsurface contact time), `IEOF`/`SATOF` (infiltration-excess and saturation-excess overland flow fractions), `RECHG` (recharge), `BFI` (base-flow index), `EWT` (depth to water table), `TWI` (topographic wetness). This is the theme that populates the *groundwater and residence time* domain theme, currently thin. Note it is **not** the `"Hydrologic Modifications"` theme already pulled, which is dams and canals.
- **`"Regions"`** — 407 attributes: hydrologic landscape regions, Fenneman physiography, EPA Level III ecoregions.

~0.3–0.5 GB after the AOE filter, joined on COMID, no new code.

**Implementation.** Add both strings to the tuple; re-run the a3 attributes branch. The existing `_mask_nodata` handles the −9999 convention. One caution carried from the report: `CAT_TILES_Early90s` encodes NODATA as **100**, which no attribute description states — so the blanket sentinel rule is safe for these themes but is not safe in general, and any new theme deserves a value-distribution glance before trusting it.

**Discipline note that must reach the chapter.** The regional categoricals in `"Regions"` are as much *grouping* candidates as features. They will look strong under LOSO and weak under LOFO, which is exactly what the LOSO−LOFO gap column exists to expose — and if a model's lift comes from ecoregion, that is a finding about the CV design, not a feature win.

**The permanent check.** The invariant is that the pull matches its declaration and the declaration is complete against the catalogue:

> `@verify.check("a3", "every declared attribute theme is on disk, and the declaration accounts for every theme the catalogue offers")`

which asserts each theme in `WIECZOREK_THEMES` has its files, and — the part that would have caught this — that every theme the catalogue publishes is either in the tuple or in an explicit `WIECZOREK_THEMES_DECLINED` map with a reason. Declining a theme becomes a deliberate, documented act rather than an oversight.

**Chapter additions.** Body: the attribute themes list in § Covariates, extended, with the declined map and its reasons. Verification: the completeness check.

## Fix 5 — the WQP legacy-endpoint scripts

**The issue.** `experiments/discrete-sample-pre-check/01_fetch_wqp.py` and `02_audit.py` hardcode the Water Quality Portal's legacy endpoint, which **silently drops all USGS data published after 2024-03-11** — HTTP 200, valid header, missing rows. Any conclusion drawn from those audits about USGS discrete-sample availability is wrong by an unknown margin.

**This is not a pipeline bug.** It is experiment code, and the acquisition gap it represents is closed properly by the WQX adapter (New Data 1). So it owes no bespoke `data.build.verify` check — its check is whatever WQX's own checks are.

**Implementation.** Retire the scripts. Add a header note to the experiment directory's README (or a `SUPERSEDED.md`) recording that the endpoint was wrong, what was missing, and that `data/build/acquire/sources/wqx.py` is the live path. Do not repair them: their function is subsumed, and a repaired script that nobody runs is a second source of truth about WQP.

**Chapter additions.** None. This is experiment hygiene, not pipeline behaviour.

## Fixes — acceptance criteria

1. All five write-ups complete in this document, each with issue / implementation / one-time check / permanent check / chapter instructions.
2. The gridMET per-variable offset table exists and is recorded.
3. `python -m data.build.run verify all` includes every new check and passes; `--deep` likewise.
4. Each new check has a `tests/data/build/` unit test proving it FAILS on a deliberately broken fixture — a check never observed failing is not known to work.
5. Both chapters updated: pipeline behaviour in the body, every new check in the Verification section, each with what its failure would mean.

---

# New Data

Each source states where it lands, why that phase, how to acquire it, and what will go wrong.

## Source 1 — WQX (Water Quality Portal)

**Justification.** ~2.16M discrete nitrate results across the 32 AOE states; in Iowa alone 2,318 nitrate stations of which 83% are non-USGS, against our 372 nitrate-bearing sensors. This is an order-of-magnitude change in the node population and the only source on this list that changes what the project can be rather than what it knows.

**Name it WQX, not WQP.** IWQIS already owns a `WQP\d+` namespace — IIHR's conservation-practice programme (`IWQIS:WQP0001`…, where WQP expands to *Water Quality Project*) — so a `WQP:` source prefix would put two unrelated WQPs in one sensors table forever. WQX (Water Quality Exchange) is the portal's own data-system name and what the modern endpoint is called.

**Phase.** A4-shaped: a new adapter at `data/build/acquire/sources/wqx.py` beside `usgs.py` / `iwqis.py` / `mpca.py`, implementing the same uniform interface (`discover`, `fetch`, `DECLARES_SPANS`, coordinate precision from source text). **This is the only item in the plan that touches the identity and census machinery**, so its blast radius includes the review queues; everything else is additive.

**Acquisition.**
- Endpoint `/wqx3/Result/search?dataProfile=narrow` — **never** the legacy endpoint (Fix 5).
- Per state × characteristic, `zip=yes`; roughly 130 requests for the AOE-decade. Size each with the free `Total-Result-Count` HEAD before pulling.
- Accept characteristics `Nitrate`, `Nitrate + Nitrite`, `Inorganic nitrogen (nitrate and nitrite)`. **Refuse** Kjeldahl, ammonia and total-N forms — they are not nitrate and no factor makes them so.
- Key the unit conversion on the pair `(Result_MeasureUnit, Result_MethodSpeciation)`, never the unit alone.

**Registry.** Add `nitrate_discrete` as a channel distinct from `nitrate`. The two are different measurement processes, and the slogan test applies: a model might legitimately want continuous-only, and it cannot un-pool them if the build merges them.

**Anticipated quirks.**
- `ERROR: INCOMPLETE DATA` arrives with HTTP 200 and a valid header row. Detect it explicitly or you will silently truncate.
- Every USGS activity carries the measurement **twice** (as N and as NO₃). **Deduplicate after conversion, never before.**
- The USGS overlap is identifiable by string equality — `Location_Identifier` is literally `USGS-{site_no}`. Register stations that are *not* already ours; for those that are, attach the discrete samples as an additional channel on the existing registration rather than a second sensor. Handing that to the identity machinery is asking it to redo a join the key already did for free.
- Density is nothing like ours: a station with 40 samples over 12 years is a legitimate node for a spatially-inductive model and useless for a daily-timing one. The build publishes it with its true density and lets each project's cohort filter decide — exactly as `MIN_NITRATE_DAYS` already does in the xgboost project.

## Source 2 — NWM (National Water Model)

**Acquisition is settled; the shape is gated.** We are pulling some version of NWM — modeled discharge at every reach is the strongest single covariate for nitrate and we currently have discharge only where a gauge sits, and the retrospective is leakage-clean by documentation rather than by inference (NOAA's docs state the retrospective "did not assimilate stream gauge observations"; no `nudge` variable exists in the store). **But which product, which reaches, and which time span depend on the audit** (Gated Audit 3). This section is therefore written with the justification settled and the implementation deliberately incomplete; **complete it after the audit lands, not before.**

**The decision the audit resolves.** The zarr retrospective is chunked `[672 hours, 30000 features]` across 93 feature blocks, so the full series for one COMID costs the same as for 30,000, and a CONUS-scattered set reads 326.6 GB regardless of how few reaches you want. Two levers are ours — time (our record starts 2008, not 1979) and geographic compactness (268 of our 342 nitrate COMIDs are inside the cornbelt box; an Iowa-bounded set touches 32 of 93 blocks). And `CONUS/netcdf/CHANOBS/` carries the same model's output at only the 8,643 *gaged* reaches for ~25.6 GB over 2008–2023. The audit computes the exact byte cost for our reach set and answers whether CHANOBS suffices.

**What is settled regardless of the audit's verdict:**
- Take `streamflow` plus `q_lateral`/`qSfcLatRunoff`, both already per-reach. **Skip `ldasout` entirely** — 1–7 TB *per variable* on a 1 km grid needing basin aggregation, for signal the channel-side runoff terms partly carry.
- For 2023 onward, splice from `gs://national-water-model` using configuration **`analysis_assim_no_da`**, never the default `analysis_assim`: measured on a single hour, 6,214 reaches are directly nudged and **164,651 (5.9% of CONUS) differ**, because assimilation propagates downstream through routing. The default would inject gauge observations into our features and reintroduce exactly the leakage the retrospective avoids.
- 2023-02 → 2023-09 is NWM **v2.2**, not v3.0. Carry an `nwm_version` column; a seamless concatenation across a model-version change is a lie the data cannot later correct.

**Phase.** `acquire/nwm.py` already exists and already partitions by COMID — it was written for this. What changes is scope and the splice.

**Anticipated quirks.** `feature_id` = COMID with no crosswalk. The v3.0 retrospective ends 2023-02-01; the GCS bucket covers 2018-09-17 → today with no gaps and a byte-identical COMID index for the v3.0 era, so the join needs no reindexing. NWM does not represent tile drainage — corn-belt hydrograph timing and low-flow skill are its documented weak spot, which is *why* the audit measures skill rather than assuming it.

## Source 3 — SNODAS

**Justification.** The precipitation-phase and melt gap, which nothing else fills: gridMET publishes no usable `snow` aggregate (verified: HTTP 200, zero bytes, absent from the catalogue). The measured case for why it matters — 44 mm of melt at Ames on 2019-03-14 where gridMET's `pr` reports 6 mm, i.e. blind to 88% of the water reaching the soil that day. Spring nitrate flush in this region is partly snowmelt-driven, so the driver is missing exactly when the target is most active.

**Phase.** A new branch in `acquire/weather.py`, or its own `acquire/snow.py` — the weather module is already large. Anonymous HTTPS, one tar per day, flat big-endian int16 with ASCII headers; no new binary dependency.

**Acquisition.** Keep SWE (`11034`), melt (`11044`), and the rain/snow accumulation split (`01025 SlL00`/`SlL01`). **Resample onto the gridMET 1/24° grid at ingest** — 21.6 GB stored against 270 GB native, and it lands on the grid the weather weights already key to, so the existing catchment reduction works unchanged. Validate the resampled cell set against `weather/grid.parquet` at ingest and refuse on mismatch.

**Anticipated quirks.** Melt's nodata is −9999 and must be filled with **0**, not null: no snowpack is zero melt, not missing information. The derived feature that matters is `water_input_mm = rain + melt`, to be used in place of `pr` in winter — but that derivation is a *modelling* choice and belongs at read time or in a project's feature layer, not baked into the acquired store.

## Source 4 — gridMET drought indices

**Justification.** `spei90d` and `spei180d` are properly-computed, standardized multi-scale water-balance deficits — the same quantity the xgboost project currently hand-rolls as a single 40-day (reference ET − precipitation) integral, at eight timescales and on a defensible statistical footing. Twenty-six aggregates in total: `spi`, `spei` and `eddi` each at 14d/30d/90d/180d/270d/1y/2y/5y, plus `pdsi` and `z` (drought category).

**Phase.** A3, same fetch path as the daily variables — and this is the point: the pentad products ride **the same 585 × 1386 lattice**, verified against the endpoint to identical corner coordinates. So `grid.parquet` is shared, `weights_gridmet.parquet` works unchanged, and only the file layout and time column differ.

**Acquisition.** Same NCSS endpoint and bbox tiling. Internal variable names are `spi`/`spei`/`eddi` (the accumulation window is in the *filename*, not the variable), `daily_mean_palmer_drought_severity_index` for `pdsi`, and `category` for `z`. Land them in `acquired/weather/pentad/`, sibling to `daily/`.

**The cadence is the whole design constraint.** These are **pentad** — 3,404 timesteps since 1979, one per five days — while `daily/` is one row per (cell, day). Landing them in the daily store would leave four of every five days null; forward-filling to daily is a modelling preference the build must not bake in. Hence the sibling store, plus an access-layer reader that **states the cadence in its return** so no consumer can mistake a pentad value for a daily one.

**Anticipated quirks.** Values are `UInt16` with scale/offset, not float. Ride the same day-convention declaration Fix 1 introduces — a pentad stamp needs a convention as much as a daily one does.

## Source 5 — StreamCat annual nitrogen families

**Justification.** The cheapest real addition in the plan, and it changes the nitrogen theme materially: the annual 1987–2017 nitrogen mass balance per COMID — farm fertilizer (`n_ff`), **livestock waste / manure (`n_lw`)** and recovered manure (`n_lwr`), crop fixation and removal (`n_cf`, `n_cr`), deposition (`n_dep`), human waste (`n_hw`), urban fertilizer (`n_uf`), agricultural surplus (`n_ags`), **accumulated legacy N (`n_leg`)**, total inputs (`n_tin`), and point-source loads (`n_usgsww`). Manure and legacy nitrogen are the two axes nothing else we hold provides at COMID grain.

**Phase.** Not an acquisition change at all — a **request** change in the existing quota-aware a3 path. `latest_vintage()` deliberately collapses each metric family to its newest member (all vintages would be 2,371 columns, 17.8 hours, 28 GB); these families are on the menu and simply unasked-for.

**Implementation.** Request the named families explicitly, bypassing the vintage collapse for them only. Do not lift the collapse globally.

**Anticipated quirks.** The series ends 2017 against a water record running to 2026 — a slowly-varying basin descriptor, never contemporaneous. **This substantially reduces the case for gTREND's other components** (Source 7): gTREND downscales county totals by a binary agricultural mask and therefore carries no sub-county information, so a catchment mean of gTREND surplus is an area-weighted county mean — which is what StreamCat already delivers, COMID-keyed. What gTREND still uniquely adds is manure *by animal class*, the oxidized-vs-reduced deposition split, and cropland-vs-pasture splits.

## Source 6 — karst, and Sekellick confined/unconfined manure

**Justification for karst (Weary & Doctor, 282 MB).** The only geology dataset in the review with **no substitute on disk** — verified zero karst attributes across all 14,139 Wieczorek and 167 StreamCat families. And `pctcarbresid` does *not* substitute: karst units are classed by **burial depth**, so a carbonate aquifer under 40 ft of till scores zero carbonate-residual while being exactly the cover-collapse setting that routes nitrate to groundwater with near-zero attenuation. Our AOE straddles the glacial margin, where that distinction lives.

**Justification for Sekellick & Sherr (728 MB, direct COMID join).** The **confined vs unconfined manure** split — an axis nothing else we hold provides. Four census years only.

**Phase.** Both land in `derive/zonal.py`'s existing catchment-overlay path (d5). Karst is a polygon overlay; Sekellick joins on COMID directly.

**Acquisition.** Both are USGS ScienceBase releases with direct file URLs, scriptable (confirmed for SPARROW's release, and these use the same mechanism — verify per item before writing the fetch).

## Source 7 — the gTREND component set

**Justification.** Twelve component directories are in `raw/gTREND/`, each 18 annual rasters covering 2000–2017:

| directory | file stem | what |
|---|---|---|
| `Surplus` | `Surplus_N_` | the mass-balance sum |
| `Agriculture_Fertilizer` | `Fertilizer_Ag_` | farm fertilizer |
| `Agriculture_Fixation` | `Fix_Ag_` | biological fixation, all agriculture |
| `Cropland_Fixation` | `Fix_Cropland_` | fixation, cropland only |
| `Pasture_Fixation` | `Fix_Pasture_` | fixation, pasture only |
| `Agriculture_Uptake` | `CropUptake_Ag_` | crop + pasture uptake |
| `Cropland_Uptake` | `CropUptake_Cropland_` | uptake, cropland only |
| `Pasture_Uptake` | `CropUptake_Pasture_` | uptake, pasture only |
| `Atmospheric_Oxidized` | `Atmospheric_Oxidized_` | NOx deposition |
| `Atmospheric_Reduced` | `Atmospheric_Reduced_` | NH₃ deposition |
| `Lvst_Sum` | `Livestock_` | manure, all classes |
| `Lvst_Hogs` | `Hogs_` | manure, hogs |

**Three of these are exact sums of two others, verified numerically** (2010, a 600×600 corn-belt block): `Agriculture_Fixation = Cropland_Fixation + Pasture_Fixation` and `Agriculture_Uptake = Cropland_Uptake + Pasture_Uptake`, agreeing to a maximum absolute difference of 0.01 — the storage precision. `Lvst_Hogs` is a **subset** of `Lvst_Sum` rather than a partner (hogs ≤ livestock everywhere; 0.1% of the total in that block, since gTREND downscales by animal class and the other eight classes are not on disk).

**Keep the parts, and make the totals earn their keep as closure checks.** The split is the information: pasture uptake is 57% of agricultural uptake in the sampled block and is hydrologically unlike cropland uptake, which is seasonal and removed at harvest, while pasture fixation is only 7% of fixation. A total can always be reconstructed from its parts; parts can never be recovered from a total. So:

- **Ingest as covariates**: `Cropland_Fixation`, `Pasture_Fixation`, `Cropland_Uptake`, `Pasture_Uptake`, and the nine non-nested components.
- **Ingest the two `Agriculture_*` totals as verification products, not covariates** — assert that our zonal `Cropland + Pasture` reproduces our zonal `Agriculture` per (comid, year) within the 0.01 storage tolerance. If it does not, our extraction is broken. This converts a double-count hazard into a free correctness test, the same move `HUC8_scale` enables for the surplus reduction.
- **Delete nothing from `raw/`.** These are a delivery, adopted in place; removing a component because we chose not to model it is precisely the undocumented curation of an original that the raw/acquired split exists to prevent. The selection belongs in the ingest declaration, not on disk.

Record all three relations (two sums, one subset) in the theme registry so no recipe feeds a total and its own components as independent covariates. This is a shared-lineage case for the Thematic Audit's do-not-double-count list, unusual in being knowable *a priori* and confirmable arithmetically rather than by tracing documentation.

Surplus is the *sum* of the others; summation destroys composition, and two basins with identical surplus can be compositionally opposite (high-fertilizer corn versus moderate-manure pasture) with genuinely different delivery timing — fertilizer N is pulsed and soluble, manure N mineralises slowly, fixation N releases on residue decomposition. The evidence that composition matters is in-house: the pipeline already carries fertilizer beside surplus and it earns its place independently (`fertilizer_kgha_mean_f2` was the third-strongest feature by gain in the last regression run).

**Phase.** D5, through the **same `NUTRIENT_SOURCES` path** that already handles surplus and fertilizer — one entry per component, one shared exactextract pass, long output `(comid, product, year, kg_per_ha, kg, n_px)`. All components sit on the same 250 m EPSG:5070 lattice, so the coverage is computed once and adding a component costs one line plus more rows in the same product. Twelve components × 18 years = 216 rasters in one pass; size the run before launching it, since the existing two-component pass over 36 rasters already takes tens of minutes.

**Take every directory that appears in `raw/gTREND/`.** The set has grown twice during planning (ten directories at first inventory, twelve now, with `Pasture_Fixation` and `Pasture_Uptake` arriving later), so `nutrient_rasters()` should **discover components from the directory listing rather than from a hardcoded map** — the stem-to-product mapping is the only thing that must be declared, and an unknown directory should raise rather than be silently skipped.

**`HUC8_scale/` is a free validation, and the plan should use it.** `Nsurplus_HUC8.csv` is **the authors' own basin aggregation of the surplus raster**, with `HUC8.gpkg` supplying the boundaries. Aggregating our catchment-level surplus up to HUC8 and comparing against theirs is an external check on our zonal reduction — one of the very few places in this whole audit where ground truth exists. If they disagree, our extraction is wrong. Do this as part of the d5 closure work and record the agreement statistic.

**Anticipated quirks.** 1930–2017, but we take 2000–2017 to match the existing surplus block; the pre-2000 years have no partner in the water record. Units are kg-N ha⁻¹ yr⁻¹ throughout, so the existing `HA_PER_PIXEL` mass conversion applies unchanged. Read `gTREND-Nitrogen_README.pdf` Table 1 for the file-naming convention before writing the globs — the folder and file stems differ (`Agriculture_Fertilizer/Fertilizer_Ag_YYYY.tif`).

## New Data — acceptance criteria

1. Each source acquired into `acquired/`, covered by a snapshot manifest, with `snapshot.cut()` run and its notes naming what arrived.
2. Each source has at least one derived product or published channel reachable through `data.access` — **acquisition without exposure is the failure mode Tier 0 exists to catch, and this plan must not add to it.**
3. `verify all --deep` green, including the closure check each new product owes.
4. Each source has a section in the inventory chapter: what it is, grain, span, cadence, lineage, quirks.
5. WQX additionally: the review queues re-run and their new rows adjudicated, since it is the one source that touches identity.

---

# Audits

## Gated Data Audit

Reports go in `notes/reports/`, one per audited source, each stating what the source is, why it is gated, what the audit did, **what it determines, what it does NOT determine**, and a verdict. Two verdict shapes: *include / refuse* for sources whose inclusion is in question, and *scope and fitness-for-use* for NWM, whose inclusion is settled.

### `data_audit_SPARROW_Aug26.md` — verdict: include / refuse

**Why gated.** The shipped predictions are **conditioned**: at calibration reaches the observed load is substituted and routed downstream. The model's own documentation (TM6-B3 Appendix D.6) says "the monitored flux is substituted for the predicted flux at those reaches, this monitored value being used in the subsequent predictions of downstream flux", and the data corroborates the signature — `SE_PLOAD_TOTAL` has a range-minimum of **exactly 0** while `SE_PLOAD_TOTAL_ND` is strictly positive. A zero-width interval on a routed total is what conditioning produces and nothing else does. So `PLOAD_TOTAL` at or downstream of a calibration station **is our target, routed** — feeding it as a covariate is feeding the model an observation of what it is predicting.

**The mitigation that survives scrutiny.** The same passage computes shares as "predicted source share times the monitored flux" — the **shares are model predictions, not substitutions**. Safe: `sh_*` source-apportionment shares, `DEL_FRAC`, `rchtot` (time of travel), `tile`. Refuse: `PLOAD_TOTAL`, `concentration`, anything routed. `PLOAD_*_ND` only with caution — its positive SE *suggests* it escapes substitution, but that is inferred, not verified, and the audit should say so.

**The audit, in order — both checks precede any large download.**
1. **Calibration-station intersection.** `data1.csv` carries `lat_tn`/`lon_tn` for all 2,424 TN stations, so this is a spatial join needing no identifier-scheme resolution. Determine how many of our sensors sit at or downstream of one.
2. **GenID collision on our actual cohort.** The key is GenNet's `genid`, **not COMID** — roughly 10:1 aggregation, median reach 4.42 km. Random COMIDs collide at 4.3%; our deliberately-paired sub-kilometre sites will collide far worse, and **pairs sharing a GenID receive identical SPARROW values**. If the collision rate on our cohort is high, SPARROW cannot be a per-site feature at all.

**Cost.** The release is **13 individually-addressable zips with direct URLs** (not one 53 GB blob), so the audit needs only `data1.zip` (5.7 GB) for both checks. No browser.

**What the audit will not determine.** Whether the shares are *good* — there is no ground truth for source apportionment. And it stops November 2020 against a record running to 2026, so it is a static or slowly-varying descriptor regardless of verdict.

### `data_audit_TerraClimate_Aug26.md` — verdict: include / refuse

**Why gated.** It is the cheap instrument for a more expensive question. NLDAS-2 (Earthdata login, ~163k authenticated requests) and OpenET (API key, quota-limited) are both gated behind "does soil-moisture-family information earn anything here", and TerraClimate answers that for free: 3,192 files, 1950–2025, monthly ~4 km, anonymous, on the same THREDDS server we already call, carrying `soil` (soil moisture), `q` (runoff), `swe`, `aet`, `def` (climatic water deficit) and `PDSI`.

**The audit.** Acquire TerraClimate's four state variables for the AOE, reduce to catchments, and run the Tier-1 and Tier-2 battery on them — with particular attention to the **within-basin variance ratio**: monthly 4 km data aggregated to 1.4 km² catchments may carry no within-basin signal at all, which would settle the resolution question for the whole family.

**What it determines.** Whether soil-moisture-family covariates vary meaningfully at our grain and add information beyond what gridMET's water-balance terms already carry. **What it does not determine:** whether *hourly* soil moisture would — TerraClimate is monthly, and the antecedent-conditions argument for NLDAS is specifically about the days before a storm. A negative result here is strong evidence against the family; a positive result justifies the expensive acquisition but does not substitute for it.

**This audit is also, uniquely, an acquisition** — TerraClimate becomes a source if it passes on its own terms.

### `data_audit_NWM_Aug26.md` — verdict: scope and fitness-for-use

**Why audited despite being approved.** Two decisions with large consequences, neither answerable from documentation. First, route: CHANOBS (~26 GB) versus a zarr subset (109–326 GB depending on compactness and time span). Second, and more interesting: **NWM is a model and we own ground truth for it.** We hold `discharge` as a registry channel at USGS sites; NWM predicts discharge at those exact COMIDs; and the corn belt is NWM's documented weak spot because it does not represent tile drainage. That weakness is currently an assumption inherited from a paper and should be a number measured on our reaches.

**The audit, staged so the cheap step funds the expensive decision.**
1. **Compute the exact zarr cost for free.** Read the consolidated metadata and the `feature_id` coordinate array (2.78M int32, ~11 MB), map our COMIDs to chunk indices, intersect with the time chunks covering 2008→2023, multiply by chunk size. This yields the byte-exact figure for *our* reach set without downloading a single data chunk.
2. **Intersect our snapped COMIDs with the published CHANOBS feature list** (8,643 gaged reaches). Free.
3. **Pull a CHANOBS sample** — a handful of sites over a few years, hundreds of MB.
4. **Measure skill against our own measured discharge**: Nash–Sutcliffe, KGE, log-space NSE (low flow is where the tile-drainage weakness should show), and timing error on rising limbs. Report by site and pooled.
5. **Decide the scope**: whether the ungaged-reach zarr pull is justified, given that its entire value proposition is discharge where no gauge exists and step 4 measures how much to trust it there.

**Total data pulled by the audit: 1–2 GB.**

**What it determines.** The route, the byte cost, and NWM's actual skill on our cohort. **What it does not determine:** skill at *ungaged* reaches — CHANOBS covers gaged ones by definition, and skill there is an upper bound on skill elsewhere. The report must say so plainly, because the temptation to read gaged skill as general skill is exactly the error this staged design is meant to avoid.

## Tier 0 — Exposure Accounting

**Why this exists.** Roughly 1,150 per-COMID attributes are acquired, snapshotted, verified and reachable by nothing: StreamCat's 296 columns across 148 metric families, and 426 Wieczorek `CAT_` attributes across seven themes plus their `TOT_` upstream-accumulated twins. `api.get_covariates` handles exactly three products specially (crops, nutrients, agtile) and falls back to a naive full-file read plus `.query()` for anything else. This is not a small oversight; it is the largest single gap between what the project owns and what it can use, and it was discovered by accident.

**What it produces.** A reconciliation, per store, of three populations:

- **acquired but unexposed** — on disk, no reader (StreamCat, Wieczorek, NID's 98 columns, NWM once it lands).
- **acquired but under-requested** — exposed, but a request-shaping decision hides most of it (StreamCat's `latest_vintage()` collapse; the Wieczorek themes declined).
- **exposed but unread** — reachable, used by nothing (facilities, dam layers, the coverage table).

Each row names the store, the file, the column count, the size, and the reason — with "no reason recorded" itself being a finding.

**When.** After the new sources land (step 5 of the execution order), so the accounting is complete rather than a snapshot of a moving target.

**Where.** `data/insights/`, as tier 0 of the same runnable audit — it shares the report and the store-walking code with tiers 1–2 even though its question is not thematic.

**How its output is used.** Directly, in two places: the theme coverage verdicts (a theme is not "thin" if the data is on disk and merely unexposed — that is a different and much cheaper problem), and as a work list for the access layer.

## Thematic Audit

### Scope

**Tiers 1 and 2 only. Tier 3 — predictive power — is explicitly out of scope**, because it is a property of a task rather than of the data, and recording it here would make the inventory a record of one model's preferences at one date. Where predictive questions arise they belong in a modelling report, referenced from the inventory, never stated as a property of a source.

- **Tier 1 — structural**: what does each source cover, at what grain, over what span, at what cadence, from what lineage? Answerable from metadata and the data itself.
- **Tier 2 — relational**: do sources measuring one thing agree? Is one redundant given another? Computation on aligned data, no model.

### Where it lives, and how it runs

Everything in **`data/insights/`**: the code, `report.md`, `images/`, and `themes.toml`. Re-runnable after any pipeline run via its own entry point (`python -m data.insights`) — **not** a `data.build.run` stage, because it writes no store and gates nothing. It reads through `data.access`, reaching into `data.build.config` only for store paths, and nothing imports it back.

**`report.md` is always generated and never hand-edited.** It is what you read after a pipeline run to see what the data now looks like. It carries the tables, the statistics, and visualisations wherever they help — side-by-side samples of the series being compared, redundancy matrices per theme, coverage maps, variance-decomposition bars — with images written to `data/insights/images/`.

**`themes.toml` is the theme registry**: source → theme(s), maintained as sources arrive rather than reconstructed retroactively. It lives here rather than in `data/build/parameters/` because the build does not consume it; `parameters/` is for build inputs.

### The comparison battery

**A. Coverage.** Fraction of cohort COMIDs with non-null values; fraction of the 2008–2026 record-days covered; temporal span against the water record. Answers "does this source even reach our cohort" before anything subtler is attempted.

**B. The within-basin variance ratio.** *The decisive cheap test.* For each source at catchment grain, decompose variance into between-basin and within-basin components. **A source that is ~100% between-basin at our scale delivers a basin-level constant, not a spatially-resolved covariate** — whatever resolution it advertises. This is what actually answers "does 30 m resolution matter after catchment aggregation" (usually no), and what exposes county-downscaled products as regional indicators wearing a covariate's name. Model-free, one pass, decisive.

**C. Agreement and incremental information.** Spearman for rank agreement, then **R² of A predicted from B and B from A** — asymmetric when one source is coarser, and both directions must be reported. If B predicts A at 0.95, A is nearly redundant *given* B.

**D. Lineage — and this one cannot be done statistically.** StreamCat's `n_ff` and gTREND's `Fertilizer_Ag` both descend from the same USGS county fertilizer estimates. Two sources that look independent but share an upstream input are not two measurements; they are one measurement twice — and **no correlation test can distinguish "agrees because both are right" from "agrees because both are the same data".** Every theme table therefore carries a **lineage column**, traced by reading documentation, and the audit's headline deliverable is a **"do not double-count" list** of shared-ancestry pairs, which is directly actionable in recipe design.

**E. Temporal behaviour.** Does the series vary at the cadence it claims? gTREND is annual county downscaling, so its *spatial pattern* is fixed and only the level moves; decomposing variance into space × time × interaction measures exactly that, and distinguishes a genuinely annual covariate from a static one wearing a year column.

**F. Missingness structure.** Is nullity predictable from position? Where it is — the Wieczorek NODATA clustering is the known case — any imputation choice smuggles geography into the feature, and the audit should say which sources carry that hazard.

### The two validation opportunities

Most covariates have no ground truth, so agreement is not correctness. Two pairs in this pipeline are **measured against modelled on the same objects**, which makes them genuine skill assessments and the most informative comparisons available:

- **NWM streamflow against our own `discharge` channel** — see the NWM audit; the result belongs in both documents.
- **DMR measured loads against SPARROW's point-source share** — DMR is reported effluent, SPARROW models it; disagreement is informative in both directions.

A third, smaller one arrives with Source 7: **our zonal surplus aggregation against gTREND's own HUC8 aggregation.**

### The themes

Enumerated from the domain, then populated. Fourteen, each with a **coverage verdict** — *well covered / thin / absent / on disk but unexposed* — where the last category comes from Tier 0 and matters because it is a different, cheaper problem than a true gap.

| # | theme | expected members (pre-audit) |
|---|---|---|
| 1 | Meteorological forcing | gridMET daily (16 vars), AORC (future) |
| 2 | Antecedent moisture & snow | gridMET pentad drought, SNODAS, TerraClimate/NLDAS (gated) |
| 3 | Nitrogen inputs | gTREND components, StreamCat annual N, Wieczorek `CAT_DEP_*`, DMR loads, SPARROW shares (gated) |
| 4 | Nitrogen sinks & uptake | gTREND uptake/removal, crop composition as proxy |
| 5 | Legacy nitrogen & soil storage | StreamCat `n_leg` — **thin** |
| 6 | Crops & land cover | CDL, Wieczorek land cover, NLCD (partial) |
| 7 | Artificial drainage | AgTile, Wieczorek `TILES92`/`DITCHES92`, StreamCat, Sekellick (gated) |
| 8 | Surface hydrology & flow | NHDPlus VAA, `discharge` channel, NWM |
| 9 | Groundwater & residence time | Wieczorek Hydrologic (`RECHG`, `EWT`, `CONTACT`, `BFI`) — thin until Fix 4 |
| 10 | In-stream processing & attenuation | VAA travel time; SPARROW `rchtot`/`DEL_FRAC` if it passes — **thin/absent** |
| 11 | Soils & geology | Wieczorek soils + geology, StreamCat STATSGO, karst |
| 12 | Physiography & regions | Wieczorek Regions (Fix 4), HLR, ecoregions |
| 13 | Point sources & structures | ICIS outfalls, CWNS, DMR, NID dams |
| 14 | Conservation & management practices | Wieczorek BMP (13 attrs), Census of Ag cover crops (candidate) — **thin** |
| 15 | Water-quality observations | USGS/IWQIS/MPCA continuous, WQX discrete |

**Three of the thin themes are fillable without acquisition, and saying so is part of the deliverable:** in-stream residence time is derivable from NID dams + NHDPlus waterbodies + VAA travel time (data we hold); irrigation (`CAT_MIRAD_2002`) sits inside a Wieczorek theme we already pull; legacy nitrogen arrives with Source 5. That pattern — *thin because unexposed or underived, not because unavailable* — is what Tier 0 exists to distinguish, and it is the most actionable finding this audit can produce.

### Deliverables

1. **`## Data Themes` in `notes/book/data_inventory_chapter.md`** — hand-written prose for modellers and newcomers, citing the generated report. Per theme: a membership table (source, native grain, span, cadence, **lineage**, cohort coverage, within-basin variance ratio, coverage verdict), prose answering the four questions, the do-not-double-count list, and a "what to use when" recommendation. Its purpose is to be a **completeness map**: someone who knows this domain should be able to read it and immediately name a theme we have nothing under.
2. **`data/insights/themes.toml`** — the machine-readable registry, so new sources are classified on arrival and the section cannot silently go stale.
3. **`data/insights/`** — the runnable audit producing `report.md` and `images/`, covering tiers 0, 1 and 2.
4. Redundancy matrices per theme, in the report.

### Audits — acceptance criteria

1. Three gated-audit reports in `notes/reports/`, each stating what it determines **and what it does not**, with a verdict.
2. The source list updated to reflect those verdicts — including removals, if any.
3. `python -m data.insights` runs end to end against the real stores and regenerates `report.md` and `images/` from scratch.
4. `themes.toml` classifies every source in the pipeline; a source with no theme is a FAIL of this criterion, not a shrug.
5. The `## Data Themes` section written, every theme carrying a coverage verdict, and at least the thin/absent ones carrying a named next step.
6. The Tier 0 accounting complete, with an explicit work list of acquired-but-unexposed data.

---

# Conventions this plan changes

- **`notes/reports/` becomes the permanent home for audit-style reports** (research write-ups stay as `notes/*-report.md`). Update the "Reports and notes" section of `CLAUDE.md` to say so, and name the `data_audit_<SOURCE>_<Mon><YY>.md` convention.
- **Credentials live at `data/build/parameters/api-keys.toml`**, gitignored, with `api-keys.example.toml` committed as the shape. `config.API_KEYS` points there. *(Done 2026-08-26; previously it pointed into the retired `src/build/`, which would have broken the USGS record fetch the moment that directory was deleted.)*
- **`data/insights/` is a new top-level member of the data layer**, alongside `data/build` and `data/access`. Its import rule: it may read both, and neither may read it. Record this in the pipeline chapter's architecture section.

---

**STATUS 2026-08-27 — superseded for the remainder.** All five fixes are closed, two of seven sources are in (StreamCat annual N families, the gTREND component set), and all three gated-source audits have verdicts. The thematic audit is partial. What remains, re-sequenced on measurements taken during the work, is in **`notes/plans/completion-plan_Aug27.md`**. This document is not edited further; it stays the record of what was specified.
