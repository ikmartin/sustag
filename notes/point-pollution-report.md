# Point Sources of Nitrate in Iowa Streams

Research findings and recommended next steps. Written to be read cold — no prior context assumed.

*Compiled 2026-07-20 from a multi-agent research pass: 24 sources, 120 extracted claims, 25 adversarially verified (19 confirmed, 6 refuted).*

---

## 1. Why this question came up

Our model predicts nitrate concentration at ~85 continuous sensor sites in Iowa streams, using weather data and land-use data (crop type, ground nitrogen surplus). Every one of those features describes **diffuse pollution** — nitrogen spread thinly across the whole landscape as fertilizer and manure, then washed into streams by rain and drainage.

That means the model is structurally blind to **point sources**: nitrogen entering a stream at one identifiable location, typically a pipe. If a sewage plant discharges upstream of one of our sensors, no combination of crop acreage and rainfall will predict its contribution. The model will simply under-predict that site, persistently, and there is no feature that can fix it.

The motivating case: one site averages **17.7 mg/L** nitrate — above the EPA drinking-water limit of 10 mg/L — and sits near an airport. The question was whether something unmodeled and localized is driving it.

## 2. What "point source" actually means here

Five categories, which behave very differently:

**Municipal wastewater treatment plants (sewage plants).** The clearest case. A town's sewage is treated and discharged through a permitted outfall, continuously, every day of the year. Treated effluent still carries substantial nitrogen. These are individually located, individually permitted, and their discharges are reported to EPA.

**Industrial dischargers.** Food processing, ethanol plants, fertilizer manufacturing, and similar facilities holding discharge permits. Same regulatory system as sewage plants, same reporting.

**CAFOs — Concentrated Animal Feeding Operations.** Large-scale livestock facilities: hog confinements, cattle feedlots, poultry barns. Iowa has a great many. These are *ambiguous* rather than cleanly point-source: a well-run facility doesn't discharge to a stream at all. Its manure is stored, then land-applied to fields as fertilizer — at which point it becomes diffuse pollution indistinguishable in behavior from synthetic fertilizer. CAFOs matter as point sources mainly through spills, over-application near the facility, and lagoon leakage.

**Septic systems.** Individual household treatment, thousands of them. Each is tiny; collectively, in a basin with many rural homes, they can matter. Functionally a "diffuse point source" — mappable in principle, but only useful as a per-basin density measure.

**Combustion NOx deposition.** Airports, power plants, and highways emit nitrogen oxides, which return to the ground as nitrate in rain and dust. This is the mechanism people usually have in mind for an airport, and it is the weakest of the five for our purposes — see §5.

So: not one kind of thing. "Farms vs. factories" is the wrong axis. The real axis is **continuous permitted discharge** (sewage plants, industry — well documented, joinable to our basins) versus **everything else** (fuzzy, poorly located, or behaving diffusely despite a point origin).

## 3. Should we expect this to matter? Honestly: not much in aggregate

This is the finding that should shape expectations before investing effort.

**Roughly 90% of Iowa's stream nitrate originates from cropland**, which covers 72% of the state. The dominant delivery mechanism is **tile drainage** — networks of perforated pipe buried under fields to drain waterlogged soil, which route water (and dissolved nitrate) into streams quickly and efficiently. Iowa's agricultural landscape is, in effect, plumbed.

Against that, point sources are a small statewide share. Adding point-source features will **not** meaningfully improve overall error metrics. If the success criterion is aggregate RMSE across 85 sites, this work will probably disappoint.

The honest reframing — and the one worth pursuing — is that point sources are a plausible explanation for **specific outlier sites**, not for the network as a whole. The deliverable is "we can now explain why sites 12, 34, and 61 run persistently high," not "the model got better." That was already the implicit aim with the airport site; it's worth making explicit, because it changes how success is evaluated.

## 4. Can we separate point from nonpoint using data we already have?

This was the priority question. The answer is largely **no** — and two intuitive shortcuts that seemed promising turned out to be unsupported. Both were proposed early in this project, so this section is a correction to our own earlier reasoning.

### The two shortcuts that failed

**Shortcut 1: the concentration–discharge relationship ("C–Q").**

*What it is:* For each site, plot nitrate concentration against stream flow (discharge), usually on log-log axes, and fit a slope. The slope describes what happens to concentration as the river rises.

*The intuition:* A pipe discharging a fixed amount of nitrogen gets **diluted** when the river is high — concentration should fall as flow rises, giving a negative slope. Farm runoff is the opposite: rain **mobilizes** nitrate off the landscape, so concentration rises with flow, giving a positive slope. Slope sign would then reveal source type.

*Why it fails:* **Refuted 0–3 by adversarial verification.** The hydrology literature interprets C–Q slope in terms of hydrologic connectivity, groundwater residence time, and in-stream processing — not source type. The paper this reasoning was drawn from (Gorski & Zimmer 2021, in the Raccoon River basin) makes no such claim. Using C–Q as a source discriminant is an inferential leap the evidence doesn't support.

**Shortcut 2: the dry-weather nitrate floor.**

*What it is:* "Baseflow" is the streamflow that persists between rain events, sustained by groundwater seepage — and in Iowa, heavily by tile drainage. The "dry-weather floor" is the nitrate level a site holds during those non-storm periods.

*The intuition:* Farm pollution is event-driven — it spikes with spring rain and snowmelt. A sewage plant discharges the same amount on a dry August Tuesday as on any other day. So a high, flat nitrate level during dry weather should betray a continuous discharger.

*Why it fails:* In Iowa, **baseflow carries about two-thirds of annual nitrate export** (17.3 of 26.1 kg/ha in the Raccoon River), rising above 80% in spring and late fall. Tile drains keep delivering fertilizer nitrate long after the rain stops. High dry-weather nitrate is the *normal agricultural signal* in this landscape, not an anomaly.

**Both fail for the same underlying reason.** Tile drainage makes nonpoint agricultural pollution behave exactly like a point source — continuous, flow-insensitive, present at low flow. The thing that would make these heuristics work in a typical watershed is precisely the thing Iowa has engineered away. Verification also found that tile-drainage density is statistically entangled with cropped area (R²=0.95), so the two can't be cleanly separated even as covariates.

### What survives and remains useful

**C–Q as a descriptor, not a diagnosis.** The classification rule itself is solid: on log-log axes, |slope| ≤ 0.2 means "chemostatic" (concentration barely responds to flow), > 0.2 means enrichment, < −0.2 means dilution. Useful for characterizing site hydrology; just don't read source type off it.

**Iowa-specific C–Q structure.** Verified, and directly in-domain (five nested Raccoon River basins): storm periods cluster chemostatic year-round, while baseflow periods are more variable and strongly seasonal. Individual storms look erratic — events must be averaged.

**The baseflow enrichment ratio (BER) — the one clean feature.** Compare the share of *nitrate load* carried by baseflow to the share of *water* carried by baseflow. If nitrate's share exceeds water's share, nitrate is preferentially delivered by the baseflow pathway. The Raccoon River long-term value is 1.23. This is computable per site, has published precedent for reuse, and would be a reasonable engineered feature or anomaly statistic. **It requires paired flow (discharge) records at each sensor site**, which we may not currently have.

### The method that actually discriminates sources — and its costs

**Nitrate stable isotopes.** Nitrogen occurs as ¹⁴N and ¹⁵N, oxygen as ¹⁶O and ¹⁸O. Different nitrate sources carry different ratios, because different biological and industrial processes fractionate them differently. Synthetic fertilizer is manufactured from atmospheric N₂ and sits near 0‰ in δ¹⁵N. Manure and sewage are enriched in ¹⁵N (ammonia volatilization preferentially removes the lighter isotope), typically +10 to +20‰. Atmospheric nitrate carries a distinctive high δ¹⁸O. Measuring both isotopes in a water sample therefore constrains which source mix produced it.

This is the canonical tool, and it is genuinely the only method found that addresses source identity directly. But four caveats are serious:

- **It's probabilistic, not definitive.** Endmember ranges are wide and overlapping. Results come as posterior distributions from a Bayesian mixing model, not assignments.
- **Denitrification is a severe confounder.** Bacteria in low-oxygen conditions convert nitrate to nitrogen gas, preferentially consuming ¹⁴N and leaving the residual nitrate enriched in ¹⁵N. This pushes a synthetic-fertilizer signature *upward into the manure/sewage window* — exactly the confusion we'd be trying to resolve. Screening for it is necessary but not sufficient; denitrification proceeds in anoxic microsites even in well-oxygenated water.
- **The Iowa precedent is discouraging.** The only Iowa isotope dataset located (Cedar River, δ¹⁵N +0.45 to +5.35‰) sits entirely in the fertilizer/soil-organic-nitrogen window and never approaches the manure endmember. It's also δ¹⁵N-only, one season, ~20 years old.
- **A Wisconsin study found manure inputs explained *none* of the variance in stream δ¹⁵N**, attributing the missing signal to ammonia volatilization dispersing manure nitrogen beyond its application footprint. This directly threatens CAFO attribution. (Caveat: that study measured δ¹⁵N in invertebrate tissue, not in nitrate, so it shouldn't be over-generalized.)

Practically: isotopes require **physical water sampling and laboratory analysis**. This cannot be done from existing data. It's a field campaign, not a data pull.

## 5. The airport hypothesis specifically

Weakened, though not eliminated. The natural mechanism — aircraft combustion NOx depositing as nitrate — cannot be measured with available deposition data: the national monitoring network (NADP/NTN) deliberately sites its stations *away from urban areas and point sources*, because it's designed to measure regional background. It will not resolve a single airport's plume.

The more tractable hypothesis is mundane: **airports sit at the edges of towns, and towns have sewage plants.** Check the permitted-discharger database for that basin before pursuing anything atmospheric. A third possibility is turf fertilizer on runway margins, which is diffuse and wouldn't be distinguishable from ordinary agricultural signal.

Because our site metadata already includes a nearest-airport code for every site, this can be tested network-wide rather than anecdotally: do airport-adjacent sites run systematically high after conditioning on agricultural features? One site is a story; 85 sites is a test.

## 6. Datasets available

| Source | What it covers | Access | Key limitations |
|---|---|---|---|
| **EPA ECHO / Water Pollutant Loading Tool** | Permitted dischargers — sewage plants and industry. Actual pounds of nitrogen discharged per facility per year | Bulk download (incl. a watershed-level option) and REST API; weekly refresh | Doesn't aggregate facilities reporting *only* nitrate/nitrite/ammonia; imputes missing loads from industry-code medians; **EPA notes some major Iowa municipal plant records are inaccurate** |
| **Iowa DNR Animal Feeding Operations** | Livestock facility locations with per-species animal counts | Bulk geodatabase + live ArcGIS REST service; lat/lon included | Only facilities ≥300 animal units; those under 500 aren't permit-regulated, so smaller operations are invisible |
| **USGS SPARROW (Midwest 2012)** | Modeled nitrogen source attribution by catchment | DOI-linked data release, public domain | Long-term average conditions for 1999–2014 — static, cannot align with our weather-driven time series |
| **Atmospheric deposition** (NADP, CASTNET, TDep) | Regional nitrogen deposition | Bulk download / FTP | Cannot resolve local sources — see §5 |

### Access details

- EPA ECHO bulk downloads: <https://echo.epa.gov/trends/loading-tool/get-data> (includes a "Watershed Statistics and Loadings" option intended for watershed modeling)
- EPA ECHO REST API: <https://echo.epa.gov/tools/web-services/loading-tool>
- Iowa DNR AFO bulk geodatabase: `https://iowageodata2.s3.us-east-2.amazonaws.com/farming/AFO.zip` (CC0)
- Iowa DNR AFO ArcGIS REST: <https://programs.iowadnr.gov/geospatial/rest/services/Agriculture/AnimalFeedingOperations/MapServer> — Layer 3 `Facilities`, point geometry, per-species animal counts plus aggregate `AnimalUnit`
- USGS Midwest SPARROW 2012: DOI [10.5066/P93QMXC9](https://doi.org/10.5066/P93QMXC9)
- CASTNET / TDep: `https://gaftp.epa.gov/castnet/`

## 7. What remains genuinely unknown

Seven of ten research questions returned nothing that survived verification. This is absence of research, not evidence of absence:

- **Load apportionment models** — the published statistical framework closest to our actual goal
- **Any prior attribution work on the Iowa IWQIS sensor network** itself
- **Precedent for using ML model residuals to detect point sources** — the approach most natural to our pipeline
- **Clean Watersheds Needs Survey**, **Iowa septic system data**, **National Emissions Inventory NOx**, and any **Iowa DNR wastewater inventory** more accurate than EPA's

The middle one matters most and is worth stating as an open problem: nobody has told us how to distinguish a genuine point-source residual from ordinary model misspecification — unmodeled tile density, flow-rating error, sensor drift. That question is currently unanswered, and it's the crux of the whole approach.

## 8. Recommended next steps

Ordered by dependency and by value-per-effort. The first four are data work; the last two are only worth doing if the first four show signal.

**1. Acquire stream discharge (flow) records for as many of the 85 sites as possible.** This is the prerequisite for nearly everything else — BER, any flow-conditioned analysis, any load calculation. USGS-operated sites have discharge; IIHR-operated ones may not. Establishing which sites have paired flow data determines what fraction of the network is analyzable at all. *Do this first, because it bounds every subsequent step.*

**2. Acquire tile-drainage density per basin.** Given that tile drainage is the dominant confounder in §4, this is arguably the single highest-value new feature — and it may improve the model on its own merits, independent of point sources. **Note: we never researched where to obtain this.** It's a gap in the research, and finding a source should be treated as a small standalone task before the rest.

**3. Pull EPA ECHO watershed loadings and join to the delineated basins.** Sum permitted nitrogen load within each site's upstream basin to create a per-basin feature. This is the most direct, best-documented point-source signal available and the cheapest to obtain. Two integration wrinkles: the data is **annual**, while the model is weather-driven at much finer resolution, so it enters as a slowly-varying or static per-basin attribute rather than a time series; and given the flagged inaccuracies in Iowa municipal records, spot-check the largest dischargers in our basins by hand.

**4. Pull the Iowa DNR AFO layer and compute animal units per basin.** Straightforward spatial join, gives a manure-pressure feature. Bear in mind this is likely to behave as a *diffuse* feature (manure gets land-applied), so it may correlate with the existing nitrogen-surplus feature rather than adding independent information — check for collinearity before adding it.

**5. Build the residual diagnostic properly.** Rank sites by mean and seasonal residual (observed minus predicted). But — per §4 — condition on tile-drainage density, cropped fraction, and season *first*, or we'll rediscover agricultural hydrology and mistake it for point sources. Treat the output as a **ranked watchlist for investigation**, not as evidence. And keep the existing basin-conflict cross-validation splits: a feature that helps at a handful of sites can inflate apparent accuracy without generalizing.

**6. Exploit the upstream/downstream site topology.** Our site metadata already encodes partial flow topology (`upstream_uid` / `downstream_uid`). A discharge located between a paired upstream and downstream site should appear as a step increase in load-normalized nitrate across that pair. This localizes a source to a stream reach and is the most defensible inference available from data we already hold — it requires no new source-attribution theory, just mass balance. (The formal published version of this, the load apportionment model, is one of the uncovered gaps.)

**7. Only then consider isotope sampling.** Expensive, requires fieldwork, and both the Iowa and Wisconsin precedents are discouraging. If pursued, target the top handful of residual sites from step 5 rather than sampling broadly, measure **both** δ¹⁵N and δ¹⁸O (the Iowa study's single-isotope design is a large part of why it couldn't resolve much), and follow the three-stage workflow: narrow endmembers with site-specific measurements, corroborate with independent water chemistry, then apportion with a Bayesian mixing model.

---

## Confidence and caveats

This synthesis rests on 24 sources and 25 adversarially verified claims, of which 6 were killed. Specific limitations:

- Several key papers were paywalled (Wiley returned HTTP 402, ScienceDirect 403), so some verification relied on Crossref/PubMed metadata and mirrored abstracts rather than full text. **The BER formula and the ">80% seasonal baseflow" figure should be confirmed against full text before anything is published on them.**
- Some verification agents exhausted their search budget and couldn't run complete adversarial sweeps, most notably on the C–Q and baseflow findings.
- The 90%-cropland figure and the Raccoon River load numbers come from older studies (2004–2018) and should be used for structural conclusions, not as current values.
- A set of specific isotope endmember ranges (δ¹⁵N −8 to +25‰, sewage +10 to +20‰) was refuted as misattributed to its cited review. Use Kendall's canonical compilations instead.
- The Gorski & Zimmer C–Q results rest on n=5 *nested* watersheds in a single basin — spatially correlated, with pseudoreplication risk, and R²=0.85 at n=5 corresponds to only p≈0.026.

## Key references

- Gorski & Zimmer (2021), "Hydrologic regimes drive nitrate export behavior in human-impacted watersheds," *Hydrol. Earth Syst. Sci.* 25:1333–1345 — Raccoon River, Iowa; C–Q classification
- Schilling & Zhang (2004), "Baseflow contribution to nitrate-nitrogen export from a large, agricultural watershed, USA," *J. Hydrology* 295:305–316, DOI 10.1016/j.jhydrol.2004.03.010 — baseflow fraction, BER
- Miller et al. (2016), "Quantifying watershed-scale groundwater loading and in-stream fate of nitrate using high-frequency water quality data," *Water Resources Research* 52(1):330–347, DOI 10.1002/2015WR017753 — hydrograph separation with high-frequency sensors
- Xia et al. (2017), *J. Geophys. Res. Biogeosciences* 122:2–14, DOI 10.1002/2016JG003447 — three-stage isotope + Bayesian apportionment workflow
- Gautam & Iqbal (2010), "Using stable isotopes of nitrogen to study its source and transformation in a heavily farmed watershed," *Environmental Earth Sciences*, DOI 10.1007/s12665-009-0165-7 — the Cedar River, Iowa isotope dataset
- Diebel & Vander Zanden (2009), "Nitrogen stable isotopes in streams: effects of agricultural sources and transformations," *Ecological Applications* 19(5):1127–1134, DOI 10.1890/08-0327.1 — the negative result on manure detection
- Granger & Wankel (2016), *PNAS*, DOI 10.1073/pnas.1601383113 — denitrification isotope trajectories
- Chang, Kendall, Silva, Battaglin & Campbell (2002), *Can. J. Fish. Aquat. Sci.*, DOI 10.1139/f02-153 — Mississippi Basin land-use isotope signatures
