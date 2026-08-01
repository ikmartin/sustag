# Improving the model: new data sources and model infrastructure

Research conducted 2026-07-31, prompted by the discovery that ~11% of the sensor cohort sits below engineered nitrate-removal features whose effect no watershed covariate can express (see the site-type section of `audit-of-data-pipeline-0731.md`).

## Bottom line

**Buy data, do not change model class.** The highest-value action is a 67 MB CSV download that supplies both inputs to a published Iowa-calibrated removal equation for 4,128 impoundments. The second is a one-file change to an experiment that already exists. Neither is a modelling change.

**No architecture will discover the wetlands for you.** The strongest evidence is not a negative result but the shape of the positive ones: the USGS Delaware River Basin group, the most advanced river-network GNN programme in existence, hit exactly this problem with reservoirs and answered it across four papers by adding reservoirs as an explicit node type with physics-simulated inputs. Nobody let the graph work it out.

Ranked by value ÷ effort:

| | action | effort |
|---|---|---|
| 1 | **StreamCat pull** — 97% site coverage, ~2 min, incl. annual N inputs 1987-2017 | hours |
| 2 | **NID download → Crumpton removal feature** | hours |
| 3 | **Distance-gated lag-0 donor arm** | one file |
| 4 | **NHDPlus VAA** — reach travel time `totma`/`pathtimema` | afternoon |
| 5 | SPARROW Midwest 2012 structural columns (**not** fitted loads — see leakage warning) | half a day |
| 6 | Flag site type; weight the treated sub-basin by `(1 − removal)` | days |
| 7 | Iowa DNR outfalls; drainage districts for tile-outlet sites | half a day |
| 8 | CWNS upgrade panel — step-change feature, as windows not dates | 1-2 days |
| — | *dynamic seasonal SPARROW* — genid-keyed, 10:1 coarsening, 16 GB, leakage | defer |
| — | *GNN on the COMID graph* | months, and likely a tie |

---

## 1. The removal equation, and the data that feeds it

Crumpton, Stenback, Miller & Helmers (USDA-FSA, [report PDF](https://www.fsa.usda.gov/Internet/FSA_File/fsa_final_report_crumpton_rhd.pdf)), fitted on **Iowa CREP wetlands** — the exact population `WQS0008`/`WQS0012` belong to:

```
% nitrate mass removed = 103 × HLR^(−0.33)        R² = 0.69   (34 wetland-years, 12 wetlands)
HLR = annual watershed water yield ÷ (wetland area / watershed area)
```

Anchor points: area ratio 0.57% → 40% removal; 2.16% → 78%. **That band brackets the 47–88% drop measured across our five inflow/outflow pairs.** Peer-reviewed follow-up: Crumpton et al. (2020), J. Environ. Qual. 49(3), [doi:10.1002/jeq2.20061](https://doi.org/10.1002/jeq2.20061) — 9–92% removal across 26 wetlands, driven mainly by HLR.

### This replaces "subtract the overlap" with something calibrated

Subtracting a treated sub-basin entirely assumes 100% removal. Crumpton supplies the actual fraction, so **weight the treated sub-basin's covariates by `(1 − removal)`** instead of zeroing them. Every input is already computed except wetland surface area and its contributing area — and those are exactly what the next section provides.

For sites further downstream, Anderson, Schilling, Just & Seo (2024), Front. Env. Sci. 12:1416018 ([link](https://www.frontiersin.org/journals/environmental-science/articles/10.3389/fenvs.2024.1416018/full)) measured an Iowa wetland at 74.2% removal and found downstream concentration reduction tracks *treated drainage-area fraction* roughly 1:1 — 19% reduction at 16.3% treated, ~0% at 0.03%. So `Σ_structures (treated_area_fraction × removal_fraction)` is a single scalar that degrades gracefully with distance. *(That composition is a synthesis of two published results, not itself a published recipe.)*

---

## 2. Data sources — all endpoints verified 2026-07-31

### 2.1 National Inventory of Dams — do this first

`https://nid.sec.usace.army.mil/api/nation/csv` — 67 MB, verified 200. **4,128 Iowa dams**, `Surface Area (Acres)` on 4,053, `Drainage Area (Sq Miles)` on 4,111, `Year Completed` on 4,102, point geometry.

**That is the complete input set for the equation, already tabulated, public domain.** And the population is the right one: median 4.7 acres of pool over 0.33 sq mi of drainage is a **2.2% pool/watershed ratio**, at the top of the CREP 0.5–2.0% design band. Purposes include 2,739 stock/farm ponds, 431 flood-risk, 260 grade stabilization, 63 fish-and-wildlife ponds.

**Integration: hours.** Snap to COMID, join to basins, compute HLR per structure, aggregate upstream of each sensor.

### 2.2 NHD Waterbody — an explicit treatment flag on keys we already hold

`https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/12`, fields `AREASQKM`, `FTYPE`, `FCODE`, `REACHCODE`. Verified over the Iowa bbox: **FCode 43624 "Reservoir: Treatment" = 120**, 43613 "Water Storage" = 238, 43600 generic = 70.

Polygon area yes, contributing area no — we compute that ourselves. The closest thing to a nationally consistent engineered-treatment flag, and we are already COMID-keyed.

### 2.3 NWI — recall net for what NID misses

Iowa geodatabase `https://documentst.ecosphere.fws.gov/wetlands/data/State-Downloads/IA_geodatabase_wetlands.zip` (547 MB, last modified 2026-05-01). Carries **`ACRES`**.

Constructed-vs-natural comes through Cowardin modifiers — **`x` excavated, `h` diked/impounded, `d` partly drained, `K` artificially flooded**. A live query over a Story/Hamilton bbox returned `PUBFx`, `PUBKx`, `PEM1Ch`, `PEM1Cd`, with roughly a third of polygons carrying a human-modification modifier.

Caveats: no watershed area, 0.5-acre minimum mapping unit, old Iowa source photography (the DNR mirror is `NWI_2002.zip`), and `x`/`h` do **not** mean "built for nitrate treatment" — a stock pond and a CREP cell both land on `PUBHh`. Use intersected with NID, not as a classifier.

### 2.4 Drainage districts — for the tile-outlet sites specifically

`https://iowageodata2.s3.us-east-2.amazonaws.com/farming/Drainage_Districts.zip` (12.9 MB, 2018, unauthenticated). Legal drainage-district boundaries — an actual engineered-drainage administrative footprint, which is what our tile-outlet sites (`WQS0055`, `WQS0056`, `WQS0054`, `WQS0080`) need, because a delineated topographic "basin" is meaningless for them.

Also verified at the same host: `farming/tiled_soils.zip` (66 MB, 2019, Iowa-calibrated tile-likelihood polygons — arguably cleaner than AgTile-US within Iowa, which we already hold) and `farming/ag_drainage_wells.zip` (121 KB, direct-to-aquifer points).

### 2.5 Iowa BMP Mapping Project — high coverage, wrong geometry

`https://iowageodata2.s3.us-east-2.amazonaws.com/farming/conservation_practices.zip` (149 MB, 2022-01-28). Six statewide layers: grassed waterways (585,083 polygons), terraces (530,472 lines), WASCOBs (251,516), **pond dams (112,689 lines)**, contour buffers, strip cropping.

**`IA_POND_DAM` is the dam crest line with no area field at all**, so recovering pool area means pour-filling each line against a DEM — possible with our LiDAR-grade DEM, but a modelling job, not a join. **No wetlands in the dataset.** `DATE_CREATED` is the digitizing date, not installation; occurrence traces to a 2007–2010 imagery baseline.

Useful as a per-basin **density covariate** (upstream counts and line-lengths); useless for the equation.

### 2.6 Iowa CREP — send an email, stop searching

No public GIS layer exists. The Iowa DNR ArcGIS org returns **zero** items for "CREP"; the monitoring reports give aggregates only (91 wetlands by 2019, 873 acres of pool over 116,923 acres of protected drainage) and name individual wetlands only by nickname with locations as dots on a figure.

**Section 1619 (2008 Farm Bill), plainly:** it bars USDA/FSA/NRCS from disclosing producer-supplied information and is a FOIA Exemption 3 statute, so CLU boundaries and CREP *contract* records are genuinely blocked. It does **not** bar (a) non-USDA parties mapping the same physical structures from imagery or LiDAR — which is why the ISU BMP layer and NWI are legal and public — or (b) **IDALS's own state-held records**, since IDALS is not USDA and holds the state easements under Iowa public-records law.

**Path: records request to IDALS Division of Soil Conservation and Water Quality, and ask Bill Crumpton's ISU Wetland Research Lab directly** — his group holds the per-wetland pool and watershed areas behind the equation.

Two facts that partly rescue this with no request at all: CREP wetlands are sited at a **0.5–2.0% ratio by design**, so watershed area is bounded within a factor of 4 from pool area alone; and the 91-wetland population averages 9.6 acres pool over ~1,285 acres drainage (0.75%).

### 2.7 ACPF — do not budget time

`https://acpf4watersheds.org/` maps where practices **could** go, not where they are. An inspected output carries exactly the schema we want (`PoolAreaAc`, `ContAreaAc`) — for *hypothetical* wetlands. Only useful as a counterfactual layer.

---

## 2b. Nitrogen SOURCE data — all endpoints verified

### ⚠️ Read this before using any SPARROW output as a feature

**20 of our 116 sites sit within 100 m of a dynamic-SPARROW TN calibration station** (25 within 1 km, 35 within 5 km), including `USGS-05451210` at **0 m**. The 2012 Midwest model has 60 Iowa reaches carrying `flux_530_final`.

So a SPARROW *fitted* output used as a feature is partly a function of our own target, measured at our own sites. **Use only structural columns** — `sh_*` source shares, `DEL_FRAC`, `rchdecay*`, `res_decay` — and drop every fitted-load and `flux_*` column, or the LOFO numbers are fiction.

### 2b.1 StreamCat — do this first, today

`https://api.epa.gov/StreamCat/streams/metrics` — no API key, no observed rate limiting. GET is capped at ~500 COMIDs by URL length; **POST has no practical limit** (5,000 COMIDs × 4 metrics × 2 AOIs returned in 3.9 s; 49,059 records in 18.3 s across ten calls). Our full COMID set is ~62 POSTs, about two minutes. `conus=true` 502s and the FTP bulk path is retired — the API is the only route.

**Coverage: 97% of our site outlet COMIDs (129 of 133).** POST by our own COMID list beats state-list (86%) and region-list (81%) pulls, so that is the recipe.

The standout is a **time-varying** series, which is rare and essentially free:

> **`n_ff`, `n_lw`, `n_lwr`, `n_cf`, `n_cr`, `n_ags`, `n_dep`, `n_hw`, `n_uf`, `n_leg`, `n_tin` — annual, 1987–2017, COMID-keyed, watershed-accumulated** (Brehob et al. 2025, [doi:10.1021/acs.est.5c08196](https://doi.org/10.1021/acs.est.5c08196)). Farm fertiliser, livestock waste, manure applied, crop fixation, crop removal, agricultural surplus, atmospheric deposition, human waste, urban fertiliser, **legacy N accumulated since 1950**, and total inputs.

Static complements: `fert`, `manure`, `cbnf`, `nsurp`, `nani`, `septic`, `wwtpalldens/majordens/minordens`, **`npdesdens`** (permitted NPDES site density, from EPA FRS, available at riparian-buffer AOIs too), `runoff`. Dated: `popden2010`, `pctimp*` and `pctcrop*` on eight NLCD vintages.

`n_usgsww_[1988…2012]` is nitrogen from WWTP output — a ready-made point-source feature that partly obviates the DMR work, **but it ends in 2012**, likely before most of our nitrate record, and is **zero for ≥50% of our sites**.

**Two verified gotchas.** The `n_*` `ws` values are raw upstream **sums in kg** (median 103,941 for `n_tin_2017ws`), not per-area — normalise by `CumAreaKm2` or they simply encode basin size. And `nsurp`'s metadata lists units as "Percent" while its description says kg N/yr; trust the description.

Licence: EPA disclaimer only. Cite Hill et al. 2016, JAWRA 52:120-128.

### 2b.2 NHDPlus VAA — in-stream residence time, directly

`https://www.hydroshare.org/resource/6092c8a62fac45be97a09bfd0b0bf726/data/contents/nhdplusVAA.parquet` — 258 MB, 2,691,339 rows keyed `comid`. Carries **`totma` (reach travel time, days) and `pathtimema` (cumulative travel time)**, 100% non-null on VPU 07 and 10L.

That is transport time to the gauge without deriving it from EROM ourselves — directly relevant to how far upstream a nitrogen input can still matter on a given day. **An afternoon.**

### 2b.3 SPARROW Midwest 2012 — COMID-keyed, no crosswalk needed

ScienceBase `5cbf5150e4b09b8c0b700df3` (DOI `10.5066/P93QMXC9`). The key column is literally `comid`, taken from NHDPlusV2. 1,379,904 reaches, **61,205 in Iowa**. TN, TP, SS and FLOW are **separate files**, so TN is cleanly separable: `mw_sparrow_model_output_TN.txt` (493 MB CSV).

Useful structural columns: accumulated load split by source (`al_tn_fert/man/atm/urb/nfix/wwtp/can`), **`DEL_FRAC`** delivery fraction, `res_decay`, and from the inputs `rchdecay1/2/3` (in-stream decay by flow class). Single 2012 snapshot, so between-site only. Public domain. **Half a day** — it drops straight into `comid_attrs.parquet`.

Note `https://sparrow.wim.usgs.gov/` is **dead (DNS does not resolve)**; the live mapper is `https://apps.usgs.gov/sparrow/sparrow-midwest-2012`.

### 2b.4 Dynamic seasonal SPARROW — high ceiling, real costs

DOI `10.5066/P9IJLEIY`. Seasonal (84 quarters) source-specific TN/TP, 2000–2020, 263,494 reaches. **Keyed on `genid`, not COMID**; the crosswalk lives in a *different* ScienceBase item (`67d97dd6d34e5a489eed1a27`) and maps 2,691,587 COMIDs → 263,494 GenIDs — **~10:1, so it is 10× coarser than NHDPlus and will blur headwater sites**, which is where our small basins live. `tn.zip` is 13.5 GB compressed; filter to Iowa GenIDs while streaming. Plus the calibration-leakage problem above. **2–4 days, and defer.**

The trends companion (DOI `10.5066/P1XIOCF8`, `trends.parquet`, 826 MB, 21.3M rows) is `genid`-keyed and carries only `pload_total`/`pload_inc_total` — **no source attribution**, so it is much lower value than the seasonal product.

### 2b.5 Iowa DNR — outfalls are the DNR-only asset

`https://programs.iowadnr.gov/geospatial/rest/services/OneStop/QueryEnvFacs/MapServer`, 40 layers, no auth. **Layer 14 Wastewater Outfalls: 2,795 records, 100% lat/lon, with receiving-stream name.**

That matters because **nitrogen enters the network at the outfall, often a different COMID from the facility centroid**, and the receiving-stream name validates the snap. Also layer 13 Treatment Plant (1,850, design flow and population-equivalent) and layers 2/3/4 CAFOs (11,767 confinements, 13.64M animal units, construction-permit dates on 52%). `TreatType` is **null on all 1,850 rows** — no treatment level here.

### 2b.6 Wastewater upgrades — obtainable as windows, not dates

EPA's CWNS portal is an Oracle APEX app with session-checksummed links, not scriptable. Workaround: a HydroShare mirror of all vintages 1984–2022 as plain CSV, `https://www.hydroshare.org/resource/3dab07fd628a4af2ba812f038b1af89f/` (261 files, 405 MB, anonymous).

Measured: **79 Iowa municipal facilities moved Secondary → Advanced between the 2008 and 2022 surveys**, all with an NPDES ID and point lat/lon, holding 340.6 of Iowa's 807.6 MGD. The 2008 and 2012 surveys carry an explicit `PRES_NITROGEN_REMOVAL` flag (21 Iowa facilities each). A treatment upgrade is a **step change in downstream nitrate** and therefore a genuinely valuable feature — but it arrives as a 4–14 year window, not a date. Caveat: 12 facilities went *backwards* Advanced→Secondary (reclassification noise).

### 2b.7 ECHO / DMR — real nitrogen data, unresolved join

Verified on a live Iowa facility: `https://echodata.epa.gov/echo/eff_rest_services.get_effluent_chart?p_id=IA0053405&output=JSON&...` returns 1.68 MB for one facility over five years, carrying **`00600` Total N, `00610` ammonia, `00625` TKN**. Bulk: `https://echo.epa.gov/files/echodownloads/npdes_downloads.zip`, 348 MB, verified 200.

Two problems. **No NPDES→NHDPlus COMID crosswalk was confirmed** — assume lat/lon plus our own D8 snap until someone checks EPA's WATERS point-indexing service. And a permit nuance: `00600` can appear with `LimitValueNmbr = null`, i.e. **monitor-and-report rather than a numeric cap**, because Iowa defers numeric limits until after construction. So "first numeric TN limit" dates an upgrade years late; **"first appearance of `00600` in the permit" is the better marker.**

Lowest confidence of anything in this section. StreamCat's `npdesdens` and `n_usgsww_*` may cover most of the signal for far less work.

---

## 3. Model infrastructure — the honest answer is "not yet"

### 3.1 A graph model will not learn the wetland by itself

Message passing computes `h_v = f(h_v, AGG{h_u})` with **shared weights across all edges**. A 47–88% removal at one specific edge is representable only if something *distinguishes* that edge — an edge feature, a node type, or a per-node embedding. The first two are the flat-feature solution with more machinery.

The reservoir literature is the direct analogue and the escalation is instructive: Chen et al., *Heterogeneous Stream-Reservoir Graph Networks with Data Assimilation*, ICDM 2021 ([arXiv:2110.04959](https://arxiv.org/abs/2110.04959)) gives reservoirs their own node type; Jia et al. ([arXiv:2202.05714](https://arxiv.org/abs/2202.05714)) go further and *infer* the unobserved release schedule by reverse-engineering manager decisions, transferring physics-simulated release temperatures from gauged reservoirs. Four papers to handle **one unobserved edge-level intervention**.

### 3.2 Our graph specifically defeats the main argument for a GNN

The containment graph is **transitively closed** — measured, 0 of 116 sites have a directed-reachable site that is not already a direct edge. So multi-hop propagation, the thing message passing adds over a flat donor block, **has nothing to do on our graph**. A GNN would have to be built on the **COMID** graph, where unobserved reaches act as intermediates the way the Delaware's 456 segments do.

### 3.3 A per-site random effect contributes exactly zero under our CV

The BLUP for site *j* is a shrunk function of *j*'s own residuals; at a held-out site it is the population mean by construction. Under LOFO grouping it buys nothing at test time while inflating the LOSO−LOFO gap. The same applies to learned per-node embeddings (Graph WaveNet's adaptive adjacency, EA-LSTM) — they are transductive. GPBoost ([JMLR 23](https://www.jmlr.org/papers/v23/20-322.html)) would give cleaner in-sample estimates and honest uncertainty, but transfers no knowledge about wetlands.

### 3.4 Spatial Stream Network models are actively wrong here

Tail-up covariance (Ver Hoef & Peterson 2010; [SSN2](https://usepa.github.io/SSN2/articles/introduction.html)) is *stationary in stream distance*. Our inflow/outflow pairs are flow-connected at near-zero stream distance with a 3–8× concentration difference — precisely the configuration a tail-up model assumes is maximally correlated. Unmodelled, that variance goes into the nugget and shortens the estimated range globally. Also O(n³) in **observations**, and our daily record is 10⁵–10⁶. *(This reasoning is derived from the model definition, not from a cited experiment.)*

### 3.5 Lag 0 is the wetland signal, and we drop it

`neighbors.py` emits donor nitrate at lags 1 and 3; every arm is `*_no_lag0`, declined at the recipe layer pending the forecast/nowcast decision. **For an inflow/outflow pair a few hundred metres apart, travel time is minutes — lag 1 is already a day stale, so lag 0 is the only lag carrying the wetland signal.**

A distance-gated lag-0 donor column is a one-file change on infrastructure that already exists and already has an experiment harness around it.

**Decide the caveat first:** a lag-0 donor is legitimate for nowcasting and gap-filling, *not* for prediction at an ungauged site, and it will widen the LOSO−LOFO gap. Report it as a separate arm, possibly a separate shipped model.

### 3.6 Scale

Kratzert et al. (2024), HESS 28:4187 ([link](https://hess.copernicus.org/articles/28/4187/2024/)) give the only clean curve: median NSE 0.60 (1 basin) → 0.68 (50) → 0.69 (107) → 0.71 (531). Going from ~100 to 531 buys **0.02**. At 116 sites we are on the flat part — enough data to *train* a graph model, not enough to expect it to beat a tuned GBDT.

Corroborating, and more relevant: Gorski et al. (2024), WRR 60 e2023WR036591 — across 46 agricultural sites with four pooling schemes, the single pooled global model was **not** the winner; similarity-clustered pooling was. Our basin-family CV already does that structurally.

---

## Recommendations

**Do now (hours to days):**

1. **Download NID; compute HLR and the Crumpton removal fraction per impoundment; aggregate upstream of every sensor.** One new scalar feature, physically motivated, from data already tabulated. This is the only item here that directly addresses "the water passed through a removal process."
2. **Add a distance-gated lag-0 donor arm** to `xgb-neighbor-count`, reported separately from the ungauged-prediction recipe.
3. **Classify site type** into the cohort manifest (`is_treatment_outflow`, `is_tile_outlet`, `treated_area_fraction`) so the model can condition on it and diagnostics stop attributing structural bias to feature failure.
4. **Pull drainage-district boundaries** for the four tile-outlet sites, where a topographic basin is the wrong abstraction.

**Then:** NHD FCode 43624/43613 and NWI `x`/`h`/`K` as a recall net for structures NID misses; ISU BMP layer as a density covariate.

**Request, do not search:** CREP locations and areas, from IDALS and from Crumpton's ISU lab.

**Do not do:** SSN/tail-up models; per-site random effects as a fix for the wetlands; a differentiable-process rewrite (no water-quality precedent exists). **Defer:** a Graph WaveNet-style model over the COMID graph — budget it as an experiment with a real chance of tying XGBoost, and only after items 1–2 have had their ceiling measured.

## Evidence quality

**Verified by fetching the endpoint:** every data source in §2 — NID row counts and field population, NHD FCode counts over the Iowa bbox, the NWI download and its live-queried modifiers, the ISU BMP layer's geometry types and null `Examined` column, all four `iowageodata2` S3 files, the ACPF output schema, and the zero-result CREP searches on the Iowa DNR ArcGIS org.

**Verified from primary literature:** both Crumpton equations and their R²; the Delaware reservoir escalation across four papers; Kratzert's basin-count curve; the transitive-closure measurement on our own graph.

**From abstracts or secondary summaries:** Crumpton et al. 2020 JEQ figures (paywalled); Gorski 2024's four-scheme comparison (paywalled).

**Reasoning rather than published findings:** that shared-weight message passing cannot represent a per-edge removal without an edge feature; that tail-up covariance mis-specifies an inflow/outflow pair; the composed `Σ treated_fraction × removal` feature; and that a transitively-closed graph leaves a GNN's multi-hop machinery with nothing to do. Each follows from cited definitions or from measurements in this repo, but none is a result someone else published.
