# New data sources to add

Written 2026-07-31. Every endpoint below was verified by downloading or querying it. Cross-references `modeling-proposals-report.md` — two of these exist specifically to unlock model changes.

## Bottom line

**Four sources, all COMID-keyable, all free, none requiring a records request.** In priority order:

| | source | what it gives | effort |
|---|---|---|---|
| 1 | **StreamCat API** | annual N inputs **1987–2017**, COMID-keyed — our first time-varying nitrogen covariate | hours |
| 2 | **National Inventory of Dams** | pool area + drainage area for **4,128 Iowa impoundments** → the Crumpton removal equation | hours |
| 3 | **NHDPlus VAA** | reach travel time (`totma`, `pathtimema`) — transport time, not distance | afternoon |
| 4 | **Iowa drainage districts** | engineered-drainage footprints for the tile-outlet sites | hours |

Then, lower priority: NHD Waterbody FCode 43624, NWI modifiers, CWNS upgrade panel. **Skip ACPF entirely.** For Iowa CREP, stop searching and send an email.

---

## 1. StreamCat — do this first

`https://api.epa.gov/StreamCat/streams/metrics` · no API key · POST for COMID sets (5,000 COMIDs returned in 3.9 s; our full set is ~62 POSTs ≈ 2 minutes) · **97% coverage of our site outlet COMIDs** (129 of 133).

The reason this is first is not the static metrics — it is a genuinely **time-varying** series we do not currently have:

> `n_ff` farm fertiliser · `n_lw` livestock waste · `n_lwr` manure applied · `n_cf` crop fixation · `n_cr` crop removal · `n_ags` agricultural surplus · `n_dep` atmospheric deposition · `n_hw` human waste · `n_uf` urban fertiliser · **`n_leg` legacy N accumulated since 1950** · `n_tin` total N inputs
> — **annual, 1987–2017, COMID-keyed, watershed-accumulated** (Brehob et al. 2025, [doi:10.1021/acs.est.5c08196](https://doi.org/10.1021/acs.est.5c08196))

Almost everything in our current feature set is static or annual-crop. Thirty-one annual vintages of nitrogen *inputs*, including a legacy-accumulation term, is a different kind of covariate — and legacy N is the standard explanation for why nitrate does not respond to management changes on the expected timescale.

Static complements worth taking in the same pull: `nsurp`, `nani`, `fert`, `manure`, `cbnf`, `septic`, `wwtpalldens/majordens/minordens`, **`npdesdens`** (permitted discharger density, available at riparian-buffer AOIs), `runoff`, and `pctimp*`/`pctcrop*` on eight NLCD vintages.

**Two verified gotchas.** The `ws`-suffixed values are raw upstream **sums in kg** (median 103,941 for `n_tin_2017ws`) — normalise by `CumAreaKm2` or they simply encode basin size. And `nsurp`'s metadata says "Percent" while its description says kg N/yr; trust the description.

Licence: EPA disclaimer only. Cite Hill et al. 2016, JAWRA 52:120-128.

## 2. National Inventory of Dams — the removal-equation inputs

`https://nid.sec.usace.army.mil/api/nation/csv` · 67 MB · public domain · **4,128 Iowa dams**, with `Surface Area (Acres)` on 4,053 and `Drainage Area (Sq Miles)` on 4,111.

That is the complete input set for the Iowa-calibrated wetland removal equation:

$$\text{\% nitrate removed} = 103 \times \mathrm{HLR}^{-0.33}, \qquad \mathrm{HLR} = \frac{\text{watershed water yield}}{\text{wetland area} / \text{watershed area}}$$

(Crumpton, Stenback, Miller & Helmers, USDA-FSA; $R^2 = 0.69$ over 34 wetland-years. Peer-reviewed follow-up: [doi:10.1002/jeq2.20061](https://doi.org/10.1002/jeq2.20061).)

The NID population is hydraulically the right one: median 4.7 acres of pool over 0.33 sq mi of drainage is a **2.2% pool/watershed ratio**, sitting at the top of the CREP 0.5–2.0% design band. So these structures behave like the CREP wetlands the equation was fitted on.

**What it buys.** ~11% of our cohort sits below an engineered removal feature whose effect no watershed covariate can express — five are deliberate inflow/outflow pairs showing 47–88% reduction. This gives a physically-motivated scalar per site: `Σ_structures (treated_area_fraction × removal_fraction)`, which also degrades gracefully with distance downstream (Anderson et al. 2024 measured downstream reduction tracking treated-area fraction roughly 1:1).

It also **upgrades the basin-correction idea from "subtract the treated sub-basin" to "weight it by `(1 − removal)`"** — subtraction assumes 100% removal; the equation supplies the actual fraction.

## 3. NHDPlus VAA — travel time, which we currently approximate with distance

`https://www.hydroshare.org/resource/6092c8a62fac45be97a09bfd0b0bf726/data/contents/nhdplusVAA.parquet` · 258 MB · 2,691,339 rows keyed `comid` · **`totma` (reach travel time, days) and `pathtimema` (cumulative), 100% non-null on VPU 07 and 10L**.

Our distance-bucketed covariates are a proxy for how long it takes water to arrive. This is the quantity itself, already computed, on the key we already join by. It is also the natural input for any decay-weighted aggregation — the exponential kernel `exp(−d/λ)` we currently apply over *distance* is more defensible over *time*.

## 4. Iowa drainage districts — for the sites where a basin is the wrong abstraction

`https://iowageodata2.s3.us-east-2.amazonaws.com/farming/Drainage_Districts.zip` · 12.9 MB · 2018 · unauthenticated.

Legal drainage-district boundaries. Four of our sites (`WQS0054`, `WQS0055`, `WQS0056`, `WQS0080`) are **tile outlets**, where a topographically delineated basin is meaningless — the contributing area is an engineered network, not a catchment. This is the administrative footprint of that network.

Same host, also verified: `farming/tiled_soils.zip` (66 MB, 2019, Iowa-calibrated tile-likelihood polygons — arguably cleaner than AgTile-US within Iowa, which we already hold) and `farming/ag_drainage_wells.zip` (121 KB, direct-to-aquifer points).

---

## Secondary

**NHD Waterbody** — `https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/12`. FCode **43624 "Reservoir: Treatment" = 120 in Iowa**, 43613 "Water Storage" = 238. The only nationally consistent engineered-treatment flag, on keys we already have. Carries `AREASQKM` but no contributing area.

**NWI** — `https://documentst.ecosphere.fws.gov/wetlands/data/State-Downloads/IA_geodatabase_wetlands.zip`, 547 MB, refreshed 2026-05-01, carries `ACRES`. Distinguishes constructed via Cowardin modifiers (`x` excavated, `h` diked, `d` ditched, `K` artificially flooded). Use as a **recall net intersected with NID**, not as a classifier — a stock pond and a CREP cell both land on `PUBHh`.

**CWNS upgrade panel** — EPA's portal is not scriptable, but a HydroShare mirror of all vintages 1984–2022 is: `https://www.hydroshare.org/resource/3dab07fd628a4af2ba812f038b1af89f/`. Measured: **79 Iowa municipal facilities moved Secondary → Advanced between 2008 and 2022**, all with NPDES ID and lat/lon. A treatment upgrade is a step change in downstream nitrate — a genuinely valuable feature, but it arrives as a 4–14 year window, not a date.

**Iowa DNR outfalls** — `https://programs.iowadnr.gov/geospatial/rest/services/OneStop/QueryEnvFacs/MapServer` layer 14: **2,795 outfalls, 100% lat/lon, with receiving-stream name**. Matters because nitrogen enters at the *outfall*, often a different COMID from the facility centroid.

---

## Do not bother

**ACPF** maps where practices *could* go, not where they are. Its schema is exactly what we want (`PoolAreaAc`, `ContAreaAc`) — for hypothetical wetlands. Useful only as a counterfactual layer.

**Iowa BMP Mapping Project** (`conservation_practices.zip`, 149 MB) has 112,689 pond dams stored as **crest lines with no area field**, and contains no wetlands at all. Recovering pool area means pour-filling each line against a DEM. Keep it as a per-basin density covariate; it cannot feed the removal equation.

**ECHO / DMR** carries real nitrogen (`00600` Total N, `00610` ammonia, `00625` TKN, verified on a live Iowa facility), but **no NPDES→COMID crosswalk was confirmed**, so the join is unresolved. StreamCat's `npdesdens` and `n_usgsww_*` likely cover most of the signal for far less work.

---

## ⚠️ SPARROW: a leakage hazard, not a free feature

SPARROW Midwest 2012 is genuinely COMID-keyed (`5cbf5150e4b09b8c0b700df3`, 61,205 Iowa reaches, TN in its own file) and tempting as a prior that a daily model predicts departures from.

**But 20 of our 116 sites sit within 100 m of a SPARROW TN calibration station** — 25 within 1 km, and `USGS-05451210` at **0 m**. A fitted SPARROW load at those reaches is partly a function of our own target measured at our own sites.

**Use only structural columns** — `sh_*` source shares, `DEL_FRAC` delivery fraction, `rchdecay*`, `res_decay` — and drop every fitted-load and `flux_*` column. Otherwise the LOFO numbers are fiction.

The dynamic seasonal variant (`10.5066/P9IJLEIY`) is worse for us: `genid`-keyed at **~10:1 coarsening** against NHDPlus, which blurs exactly the headwater basins we care about, plus 16 GB and the same leakage problem. Defer.

## Requests, not downloads

**Iowa CREP wetland locations and areas do not exist as a public GIS layer** — the Iowa DNR ArcGIS org returns zero items for "CREP", and the monitoring reports name wetlands only by nickname with locations as dots on a figure.

Section 1619 blocks USDA *contract* records. It does **not** block third parties mapping the same physical structures from imagery, nor **IDALS's own state-held records** — IDALS is not USDA and holds the easements under Iowa public-records law.

**So: a records request to IDALS Division of Soil Conservation and Water Quality, and an email to Bill Crumpton's ISU Wetland Research Lab**, whose group holds the per-wetland pool and watershed areas behind the equation we want to use.

Two facts that partly substitute in the meantime: CREP wetlands are sited at a **0.5–2.0% wetland-to-watershed ratio by design**, bounding watershed area within a factor of 4 from pool area alone; and the 91-wetland population averages 9.6 acres pool over ~1,285 acres drainage.
