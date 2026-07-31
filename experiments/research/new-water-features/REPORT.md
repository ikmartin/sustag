# New water-borne covariates from the USGS network

**Question.** Beyond nitrate, what else does the USGS network measure that could improve the Iowa nitrate virtual sensor — at the sites we already have, and at sites near them — and how would any of it actually enter the model?

Compiled 2026-07-27 against the post-expansion site list (127 good sites: 35 USGS + 92 IWQIS) and the current `covariate_bbox = [-100.158, 39.75, -85.839, 47.885]`. All availability numbers are live queries against the USGS OGC API, reproducible with the scripts in this directory.

---

## 1. Bottom line

**Three findings, in order of how much they should change what we do.**

**(a) Almost none of this can be a model feature, and that reframes the whole question.** Every covariate below is measured by an instrument sitting in a stream. The product predicts at an *arbitrary map pin*, where no instrument exists. So turbidity, DO, pH, specific conductance and the rest are **not pin-legal as direct features** — the same disqualification the future-work report applies to discharge-at-the-target and to fitted per-site intercepts. This is worth stating plainly because [`notes/new-features.md`](../../../notes/new-features.md) lists these parameters as "covariates to try" without flagging it. The only pin-legal routes are **regional aggregates** (the `rest_of_state_nitrate` pattern) and **reach-graph neighbour features** (the current network project). Everything else is a diagnostic or an audit — valuable, but it does not go in the feature matrix. Section 5 works through each route.

**(b) The single most useful thing here is discharge, and its value is coverage, not novelty.** There are **3,474 external discharge gauges** in the covariate bbox. **98% of our 127 sites have one within 25 km; 100% within 50 km.** Compare nitrate: 53 external gauges, 14% within 25 km, 30% within 50 km. The network experiment's binding constraint was donor *coverage* — it reached 88% only by counting downstream neighbours as well as upstream. A discharge-based neighbour or regional-flow-state feature starts from a fleet two orders of magnitude denser than the nitrate fleet, and Iowa nitrate is *mobilizing* (verified C–Q exponent b = +0.61 on this project's own gauges), so flow state is mechanistically the right thing to condition on. **This is the highest-value item in the report and it is not currently in the model in any form.**

**(c) `CORE_PCODES` is silently dropping data at sites we already own.** USGS codes turbidity by *instrument optics*, not by quantity: five distinct pcodes carry it in our region. **20 of our 35 USGS sites offer a turbidity series overlapping their own nitrate record (median 5.1 years) and we extract none of them.** Three of those sites publish under `72213`, not the common `63680` — so even a naive "add 63680" fix misses them. This is the same failure mode [`notes/new-site-report.md`](../../../notes/new-site-report.md) documented for nitrate itself (a 99133-only query drops Nebraska's entire continuous network, which files under 00630). Fixing it is a one-line edit plus a refetch.

**A caution on (c):** turbidity's best-supported use is *not* as a feature — it is the missing ingredient for the turbidity-driven MNAR audit (future-work R11(b)), which asks whether nitrate is disproportionately missing on high-turbidity days. If it is, measured performance is partly bounded by sensor validity, a ceiling no feature engineering can lift.

---

## 2. Scope constraint: pin-legality

The project's own vocabulary for this is "pin-legal" — can the feature be computed at an arbitrary ungauged point? The future-work report uses it to rule out QPPQ transfer, per-site random intercepts, load-as-target and daily modelled Q. It applies with full force here.

| Route | Pin-legal? | Why |
|---|---|---|
| Covariate measured **at the target site** | **No** | A pin has no sensor. Training on it produces a model that cannot be served. |
| Covariate **aggregated over all other sites** (regional scalar) | **Yes** | Consumes other sites' observations only — exactly how `rest_of_state_nitrate_lag*` already works. |
| Covariate at a **hydrologically connected neighbour** | **Yes** | Same logic; this is the network project. Requires the reach graph, not proximity. |
| Covariate collapsed to a **static per-site descriptor** | **No** | A long-term median at a gauged site is still unavailable at a new pin. |
| Covariate used for **label audit / diagnostics** | n/a | Never enters the feature matrix; improves the ceiling or the evaluation. |

The static-descriptor row is worth dwelling on, because it is the intuitive move and it does not work. A long-term median chloride or a Cl:NO₃ ratio *feels* like the `tot_tiles92` / `tot_bfi` static attributes that already earn their place in the base feature set — but those are derived from a national COMID-keyed raster/table computable at any pin, whereas a chloride median is derived from samples taken at a specific gauge. The analogy fails on exactly the property that matters.

---

## 3. The pcode problem (research task 1)

`src/build/util/usgs.py:CORE_PCODES` pulls 8 codes. [`pcodes.py`](pcodes.py) in this directory expands that to a curated registry of **67 pcodes across 20 covariate families**, tagged by measurement kind (`insitu` continuous / `lab` / `field` discrete / `flux` load).

**One quantity, many codes.** USGS parameter codes encode the *method*, so a single physical quantity fragments. Querying one code per quantity undercounts. Measured over the 28,594 continuous series at 5,749 sites in the bbox:

| Family | in-situ pcodes present | sites (union) | sites (best single pcode) | **missed by a single-pcode query** |
|---|---|---|---|---|
| groundwater | 3 | 1,045 | 982 | **+63** |
| soil_moisture | 2 | 69 | 36 | **+33** |
| soil_temp | 2 | 79 | 52 | **+27** |
| precip_site | 2 | 775 | 752 | **+23** |
| velocity | 2 | 62 | 43 | **+19** |
| chlorophyll | 4 | 37 | 21 | **+16** |
| **turbidity** | **5** | **172** | **159** | **+13** |
| water_temp | 2 | 950 | 937 | +13 |
| nitrate | 3 | 88 | 82 | +6 |
| fdom | 3 | 14 | 10 | +4 |

The turbidity family in-region is `63680` (FNU, near-IR — the modern standard), `63675` (NTU, broadband white light), `72213` (FBRU, backscatter ratio), `63682` (FBU) and `00070` (JTU, deprecated). They measure the same physical property through different optics; the *values are not interchangeable across instruments* but the ranked signal is, which is what a tree model consumes.

**Loads are excluded on purpose.** Several families have a `flux` twin in tons/day or lb/day (`80155` sediment discharge, `91050` phosphorus, `83554` nitrate). A load is `concentration × discharge`, so a load feature smuggles discharge in multiplicatively — and the future-work report already rules out load-as-target because the L–Q correlation is spurious by construction. They are catalogued in `pcodes.py` and kept out of every recommended pull set.

**Lab-only families.** Chloride and the reduced-N pool (ammonium, TKN, organic N) have **zero** in-situ sites in our region — they exist exclusively as discrete samples on a different API. Section 4.4.

---

## 4. What is actually available (research task 2)

### 4.1 At the sites we already have — the free tier

35 USGS good sites. Each candidate series scored by `overlap_yr` against **that site's own nitrate record** (a 20-year turbidity series at a site whose sensor ran 2019–2023 is worth 4 years, not 20). No absolute record-length bar is applied — the retired 3.92 yr lifespan cutoff has been replaced project-wide by `MIN_NITRATE_ROWS = 10_000`, which governs the target and does not transfer to a covariate.

| Family | offered | usable (≥1 yr overlap) | **on disk** | **gap** | median overlap | pcodes |
|---|---|---|---|---|---|---|
| **turbidity** | 22 | **20** | **0** | **20** | **5.1 yr** | 63675+63680+72213 |
| chlorophyll | 5 | 3 | 0 | 3 | 2.9 yr | 32316+62361 |
| phosphorus (ortho, in-situ) | 3 | 2 | 0 | 2 | 1.4 yr | 51289 |
| phycocyanin | 2 | 2 | 0 | 2 | 3.5 yr | 32319 |
| sediment (surrogate-est.) | 1 | 1 | 0 | 1 | 3.0 yr | 99409 |
| discharge | 32 | 32 | 31 | 1 | 5.9 yr | 00060 |
| water_temp / diss_oxy / pH / spec_cond | — | 26 / 12 / 11 / 11 | 28 / 13 / 12 / 13 | 0 | — | already in CORE |
| fdom | 4 | **0** | 0 | 0 | 0.2 yr | 32295 |

**Turbidity is the only substantial item.** fDOM is present at 4 sites but with essentially no overlap with their nitrate records — the sensors were installed at different times. Chlorophyll/phycocyanin/orthophosphate are 2–3 sites each, too thin to survive a 20-family LOFO.

Turbidity's split across pcodes at our own sites: 13 sites under `63680`, 3 under `72213` (`USGS-05412500`, `USGS-05484000`, `USGS-06808500`), plus `63675`. **The `72213` sites include USGS-05412500, one of the longest-running nitrate records we have.**

### 4.2 Co-located gauges — how the IWQIS sites get discharge

75 external USGS gauges sit within 5 km of one of our sites, attaching to **42 of our 127**. Split by which of our sources benefits:

- **36 IWQIS sites** gain a co-located gauge — **33 of them carrying discharge**
- 6 USGS sites gain something they lack

This matters because **the IWQIS bulk export contains no discharge at all**. `measures.csv` declares `discharge`, `load` and `yield` at 38 of our 92 IWQIS sites, but none of the three columns exist in the delivered chunked CSV — an export gap, not a pipeline filter. So there are two independent routes to the same missing quantity: ask IWQIS to re-export (38 sites), or attach the co-located USGS gauge (33 sites). They likely overlap heavily, since many IWQIS sensors are physically installed *at* a USGS gauge — several pairs in [`data/colocated_gauges.csv`](data/colocated_gauges.csv) are 0.01 km apart.

Either route roughly doubles the project's discharge base, from 31 sites to ~64. The point-pollution report made this its step 1 precisely because it "bounds every subsequent step" — C–Q analysis, baseflow separation, and the baseflow enrichment ratio all need paired flow.

### 4.3 Neighbour coverage — the number that governs the network project

Share of our 127 sites with at least one **external** gauge of each family within radius R:

| Family | gauges in region | ≤5 km | ≤10 km | ≤25 km | ≤50 km |
|---|---|---|---|---|---|
| **stage** | 3,606 | 51% | 76% | **99%** | **100%** |
| **discharge** | 3,474 | 49% | 72% | **98%** | **100%** |
| groundwater | 1,045 | 12% | 20% | 50% | 92% |
| water_temp | 918 | 14% | 28% | 62% | 95% |
| spec_cond | 408 | 9% | 18% | 44% | 83% |
| diss_oxy | 245 | 3% | 6% | 22% | 52% |
| turbidity | 150 | 2% | 5% | 21% | 41% |
| pH | 138 | 2% | 5% | 31% | 55% |
| **nitrate** | **53** | **2%** | **6%** | **14%** | **30%** |
| fdom | 10 | 2% | 2% | 7% | 13% |

**Read the last two rows together.** The network experiment built its case on nitrate donors and had to reach for downstream neighbours to get coverage from 47% to 88%. Discharge is denser by a factor of ~65. Any donor-style feature built on flow will have far better coverage than one built on nitrate — and unlike nitrate, a discharge gauge does not need to be a *water-quality* site at all.

**Hard caveat.** This is straight-line distance, which is a screen and **not a connectivity test**. Follow-up C of the network report is explicit that the reach graph, not proximity, carries the signal — two sites 5 km apart on different tributaries are hydrologically unrelated. These percentages are **upper bounds** on donor availability. Recomputing this table on the reach graph is the single most important follow-up and is cheap, because the graph machinery already exists.

### 4.4 Discrete / lab samples

Queried for our 35 USGS sites plus the 75 co-located gauges. Coverage is broad but **historical** — many series end in 2014 or earlier, well before most of our nitrate records.

| Family | our sites | median results/site | co-located gauges |
|---|---|---|---|
| specific conductance | 34 | 94 | 49 |
| nitrate (grab) | 34 | 39 | 42 |
| pH | 33 | 50 | 47 |
| phosphorus | 33 | 34 | 40 |
| reduced N (NH₄⁺, TKN) | 32 | 41 | 41 |
| sediment (SSC/TSS) | 30 | 48 | 28 |
| **chloride** | **24** | **59** | 40 |
| silica | 21 | 42 | 36 |
| **DOC** | **15** | **15** | 25 |

The two lab-only families the literature actually motivates are **chloride** (conservative co-tracer, section 5) and **DOC** (the electron donor for denitrification, and the quantity fDOM proxies). Both are present at a useful number of sites but at grab-sample cadence — a few dozen to a few hundred samples over decades. They cannot form a daily series and, per section 2, cannot be a static pin feature either. Their honest use is the **site-level exceedance-rate track** (future-work R4/R8), which is a different modelling objective evaluated at ~20 units rather than 160k rows.

---

## 5. How this could enter the model (research task 3)

### 5.1 Route A — regional flow state (pin-legal, cheapest, recommended)

The model already carries `rest_of_state_nitrate_lag{1,2,3,5}` and `roll_n_avg_except_this{7,14,30,60}d`. These are pin-legal because they aggregate *other* sites. The identical construction applies to discharge, and nothing like it exists today:

```
regional_q_anomaly_lag{k}   = standardized daily discharge anomaly averaged over
                              in-region gauges (optionally distance- or area-weighted),
                              excluding the target site
regional_q_pctile           = today's regional flow percentile against each gauge's own FDC
```

*Mechanism.* Iowa nitrate mobilizes rather than dilutes — the future-work report's verified median C–Q exponent is **b = +0.61** across this project's 20 usable gauged sites, so concentration rises with flow. Regionally, [Schilling & Wolter (2005)](https://onlinelibrary.wiley.com/doi/10.1111/j.1752-1688.2005.tb03803.x) explain 75.5% of long-term Iowa streamflow and 88.5% of baseflow from rainfall, sand content and row-crop percentage, and baseflow is the dominant nitrate pathway — [Schilling & Zhang (2004)](https://www.sciencedirect.com/science/article/abs/pii/S0022169404001738) attribute roughly two-thirds of annual nitrate export in the Raccoon River to baseflow. The weather layer carries precipitation, but precipitation is not runoff: the catchment's antecedent storage state determines whether rain becomes flow at all.

*Why it is likely to help where weather did not.* The feature-manifest runs show most weather features are inert or harmful, and the leading diagnosis in [`findings.md`](../../feature-manifest/findings.md) is aggregation. Regional discharge is a *response* variable — it integrates antecedent moisture, tile activation and routing that a rainfall column cannot express. [Davis et al. (2014)](https://acsess.onlinelibrary.wiley.com/doi/10.2134/jeq2013.11.0438) show antecedent moisture controls stream nitrate flux in an agricultural watershed, and a 20-year tile-drained catchment study ([Frontiers in Water, 2024](https://www.frontiersin.org/journals/water/articles/10.3389/frwa.2024.1369552/full)) finds tile runoff only initiates above a near-saturation soil-moisture threshold (~0.49 m³·m⁻³) — a threshold nonlinearity that discharge expresses directly and precipitation does not.

*Expected effect.* Timing channel (`within_r2` 0.42, already the strong one), not the between-site ranker. The add-one/drop-one cross-tab classes the existing `rest_of_state_nitrate_*` family as **SYNERGY** — small marginal gain alone, meaningful loss when removed. Expect the same shape. **Do not expect it to move `between_r2`.**

*Effort.* 2–4 days. Metadata is free; the daily-value pull for a few hundred gauges is modest.

### 5.2 Route B — neighbour covariates on the reach graph (pin-legal, this is the network project)

This is recommendation 3 of the network report verbatim — *"separately test the non-nitrate sensor features — the untested half of your vision"* — and section 4.3 now supplies the missing availability numbers. Discharge at a connected upstream gauge is a plausible 1-hop predictor precisely because b > 0.

Construction mirrors the nitrate-donor design already specified in [`notebook.md`](../../../notebook.md): nearest connected monitored neighbour, either direction, dilution-weighted by inverse drainage-area ratio, plus a direction flag, a count, and a distance-to-nearest scalar.

*The deployment-frequency problem applies here too, and more mildly.* `notebook.md` already flags it for nitrate donors: either train two models (with/without neighbour features) or randomly mask rows in training to match the deployment-time neighbour frequency. Because discharge coverage is so much higher than nitrate coverage, the masking rate is far lower and the "no neighbour" fallback regime is much rarer — which is an argument for building the flow-donor feature *before* the nitrate-donor one.

*Gating step.* Recompute section 4.3 on the reach graph. If connected-discharge coverage lands near the 88% that either-direction nitrate achieved, this is clearly worth building; if proximity collapses to a much smaller connected set, re-scope. **This is a day of work against machinery that already exists, and it decides the rest.**

### 5.3 Route C — diagnostics and audits (not features, but they raise the ceiling)

**Turbidity → the MNAR audit (future-work R11(b)).** The hypothesis is that nitrate readings are disproportionately missing or QA-flagged on high-turbidity days, which would mean measured performance is partly bounded by sensor validity rather than by features. This audit was previously blocked on not having turbidity. It is now unblocked at 20 USGS sites (continuous) and 30 more (discrete), plus the IWQIS turbidity already on disk at 63 sites. Note the IWQIS `r_exc`/`m_exc` columns — reference and measurement extinction from the optical nitrate sensor, present at **92/92 IWQIS sites with 100% co-coverage** — are a *direct* instrument-validity signal and an even better input to this audit than turbidity.

**Discharge → C–Q, baseflow separation, and validating `tot_bfi`.** The base feature set already contains `tot_bfi`, the modelled COMID base-flow index. With discharge at ~64 sites instead of 31 we could compute a *measured* baseflow index and check it against `tot_bfi` — a direct test of whether a static attribute the model leans on is actually right at our sites. Also unblocks the baseflow enrichment ratio the point-pollution report identified as "the one clean feature," and would let the b = +0.61 C–Q result be re-estimated on roughly double the sample.

**Chloride → separating conservative from reactive transport.** Chloride is effectively inert in streams, so it tracks conservative dilution/concentration while nitrate additionally responds to biological removal. The Cl:NO₃ contrast is the standard way to tell "the water changed" from "the nitrogen changed" ([USGS OFR 2015-1080](https://pubs.usgs.gov/of/2015/1080/ofr20151080.pdf)). At grab cadence this is a per-site diagnostic, not a feature — useful for explaining persistent site-level residuals, which connects to the point-source watchlist.

### 5.4 Route D — surrogate-expanded training set (speculative, not recommended yet)

USGS operationally estimates nutrient and sediment concentrations from continuously-measured surrogates — turbidity, specific conductance, water temperature, pH, DO and discharge — by regression against discrete samples ([Rasmussen et al., USGS SIR 2006-5241](https://pubs.usgs.gov/sir/2006/5241/pdf/sir2006-5241.pdf)); this is the basis of the real-time water-quality service. In principle a site with turbidity + specific conductance + discharge but *no* nitrate sensor could be given a surrogate-estimated nitrate series, adding training rows and possibly new LOFO families — the one thing that attacks n≈20 directly.

Two reasons to hold off. It trains on modelled labels, and the project's split-protocol discipline would require such rows never enter the honest LOFO evaluation, which is most of their value. And specific conductance is the surrogate with published nitrate-specific support (in agricultural spring systems), not turbidity — while our region has SC at only 408 gauges versus discharge at 3,474. Revisit only if Route B underdelivers.

### 5.5 What the literature does *not* support

- **fDOM.** Mechanistically the most interesting candidate — it proxies DOC, the electron donor for heterotrophic denitrification, so fDOM and nitrate often move oppositely during events ([USGS TM 1-D11](https://pubs.usgs.gov/tm/01/d11/tm1d11.pdf); Snyder et al. 2018, *WRR* 54, [doi:10.1002/2017WR020678](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1002/2017WR020678)). But it is present at **10 gauges region-wide, 4 of our sites, with ~0 nitrate overlap, and 13% of our sites have one within 50 km.** There is no dataset here. Drop it.
- **pH.** Real mechanism (NH₃/NH₄⁺ equilibrium, enzymatic efficiency) but already in `CORE_PCODES`, already extracted at 12 sites, and adds nothing new.
- **DO.** Genuine mechanism — denitrification is inhibited above roughly 2 mg/L DO, so DO gates whether nitrate is conservatively transported or removed. Already in `CORE_PCODES`; coverage (22% within 25 km) is too thin for a donor feature.
- **Chlorophyll / phycocyanin.** Assimilation proxies, 2–3 of our sites. Not viable at 20 families.
- **Soil moisture / soil temperature.** Strong mechanistic case (the tile-activation threshold above), but 69 and 79 gauges region-wide and ~0–1% of our sites within 10 km. The existing `fuel_moisture_1000h` gridMET feature — already one of the better-performing covariates in the feature-manifest runs — is the practical stand-in, and it is gridded rather than point-sampled.

---

## 6. Recommendations, sequenced

| # | Action | Route | Effort | Expected | Pin-legal |
|---|---|---|---|---|---|
| **1** | **Recompute §4.3 coverage on the reach graph for discharge.** Decides everything below it. | gating | ~1 day | — | — |
| **2** | **Add the turbidity family to `CORE_PCODES` — all of `63680`, `63675`, `72213` — and refetch.** 20 sites, median 5.1 yr overlap. Use it for the R11(b) MNAR audit, *not* as a feature. | C | ~1 day + refetch | ceiling diagnosis | n/a |
| **3** | **Get discharge onto the IWQIS sites.** Two independent routes: re-export request to IWQIS (38 sites declared) and/or attach co-located USGS gauges (33 sites, several at 0.01 km). Roughly doubles the discharge base, 31 → ~64. | C | 1–2 days + one email | unblocks C–Q, BER, measured BFI | n/a |
| **4** | **Build `regional_q_anomaly_lag{k}`** on the `rest_of_state_nitrate` pattern. Pin-legal, ~100% coverage, mechanistically motivated by b = +0.61. | A | 2–4 days | timing channel; SYNERGY-shaped | **Yes** |
| **5** | **Flow-donor neighbour features on the reach graph**, if #1 confirms coverage. Build these *before* nitrate donors — better coverage, milder masking problem. | B | 3–5 days | timing channel | **Yes** |
| **6** | Validate `tot_bfi` against measured baseflow index at the ~64 discharge sites. | C | 1 day | tests a live base feature | n/a |
| — | **Do not pursue now:** fDOM (no data), chlorophyll/phycocyanin/orthophosphate (2–3 sites), soil moisture/temp (~1% coverage), any `flux`/load pcode (smuggles discharge), surrogate-expanded training rows (modelled labels). | — | — | — | — |

**Set expectations on all of it.** Every pin-legal item here loads on the timing channel, which is already the strong one (`within_r2` 0.42, `macro_auc` 0.90). None of it is a between-site lever — that gap was closed by the normed crop/surplus aggregation and the COMID statics, and nothing in the USGS sensor network is a substitute for a static basin attribute. The honest pitch for this work is *better event timing and a defensible answer to "is our label noisy?"*, not a headline LOFO jump.

---

## 7. Files

| File | What it does |
|---|---|
| [`pcodes.py`](pcodes.py) | The curated registry: 67 pcodes / 20 families, tagged in-situ / lab / field / flux, with mechanistic rationale. Importable. |
| [`01_region_inventory.py`](01_region_inventory.py) | Every continuous series in `covariate_bbox`; per-pcode and per-family coverage; the single-pcode-query undercount. |
| [`02_at_existing_sites.py`](02_at_existing_sites.py) | What our 35 USGS sites offer vs. what we extract, scored by overlap with each site's own nitrate record. |
| [`03_neighbor_sites.py`](03_neighbor_sites.py) | Co-located (≤5 km) and nearby gauges; per-family distance bands; the §4.3 coverage table. |
| [`04_lab_samples.py`](04_lab_samples.py) | Discrete-sample coverage via the Samples service at our sites and their co-located gauges. |
| [`05_demo_pull.py`](05_demo_pull.py) | End-to-end proof: pulls the turbidity family for one site, coalesces the pcode variants, aligns to daily-max nitrate. |
| `data/` | Cached API responses and every table above, as CSV/parquet. |

Run in order; each caches to `data/` and re-runs cheaply. Metadata queries need no API key — only `05_demo_pull.py` reads `src/build/api-keys.toml`.

**Demo result.** `python 05_demo_pull.py` pulls `USGS-05412500` — deliberately chosen because it files turbidity under `72213`, so a naive `63680`-only fix would miss it. Over a 900-day window it returns 40,715 rows, covering **79% of that site's in-window nitrate-days**, with Spearman **+0.72** against daily-max nitrate. That correlation is a within-site descriptive statistic on one site over one window — it is not a skill number and must not be read next to a LOFO figure.

---

## 8. Evidence quality

Following the tiering discipline of the future-work report, since this report is partly literature-based:

- **Directly verified (live query, reproducible):** every availability number in sections 3 and 4, and the demo in section 7. These come from the USGS OGC API via the scripts here and can be re-derived by anyone.
- **Read directly:** USGS SIR 2006-5241 and TM 1-D11 are public USGS documents; the surrogate-regression framing in section 5.4 reflects their stated method.
- **Search-snippet only — not full text.** Schilling & Zhang (2004), Schilling & Wolter (2005), Davis et al. (2014), the Frontiers 2024 tile-drainage study, Snyder et al. (2018), and the DO-inhibition threshold were assessed from abstracts and search results. **Snyder et al. (2018) returned HTTP 402 and `nrtwq.usgs.gov` returned 403 — neither was retrieved.** Treat the specific figures attributed to them (the 0.49 m³·m⁻³ soil-moisture threshold, two-thirds baseflow export, the ~2 mg/L DO threshold, the 75.5%/88.5% regression fits) as leads to confirm before quoting anywhere load-bearing.
- **Project-internal, previously verified:** the C–Q exponent b = +0.61 and the pin-legality rulings are carried from [`notes/future-work-report.md`](../../../notes/future-work-report.md); the donor-coverage figures (47% / 88%) from [`experiments/pre-expansion-network-test/REPORT.md`](../../pre-expansion-network-test/REPORT.md).

No claim in this report was adversarially verified. The availability numbers do not need it — they are reproducible measurements, not claims. The mechanistic rationales do.
