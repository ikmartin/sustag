export const meta = {
  name: 'sustag-future-work',
  description: 'Research the highest-value future work for the Iowa nitrate virtual-sensor problem: data improvements, model improvements, better use of existing data, and fluid dynamics.',
  phases: [
    { title: 'Search', detail: '8 pinned angles, one search agent each' },
    { title: 'Fetch', detail: 'fan out over top sources per angle, extract falsifiable claims' },
    { title: 'Verify', detail: '3-vote adversarial verification per claim' },
    { title: 'Angle synthesis', detail: 'per-angle findings from surviving claims' },
    { title: 'Cross-angle', detail: 'rank all recommendations against the between-site gap' },
  ],
}

const PROJECT = `
## The project being researched

An XGBoost "virtual sensor" for Iowa stream nitrate: predict at locations with NO physical sensor, using only weather + land-use data. Two targets:
- REGRESSION: daily-max nitrate concentration (mg/L)
- CLASSIFICATION (the primary one): binary \`violation\` = daily max >= 10 mg/L, base rate 0.263

~85 sensor sites, 160,074 site-days, 2008-2026. Each site has a delineated upstream drainage basin. Features are aggregated over basin cells in distance buckets from the outlet.

## THE DEPLOYMENT TARGET - judge every recommendation against this
The shipped product takes an ARBITRARY MAP PIN anywhere in Iowa, delineates its upstream basin on the fly via USGS NLDI, builds features from weather and land use alone, and predicts daily violation probability. There is NO nitrate history at the target location, NO discharge gauge there, and NO guarantee of a monitored neighbor upstream. Any method that requires the target site's own observations, or requires a gauged upstream site, DOES NOT WORK for this product - it may still be useful for other purposes, but that limitation must be stated explicitly and prominently.

## Current honest performance (leave-one-basin-family-out, LOFO)
- CLF: AUC 0.820, PR-AUC 0.634 (2.41x lift over base rate), recall 0.524 at 10% false-alarm rate, MCC 0.466, Brier 0.137
- REG: R2 0.328, RMSE 4.36 mg/L
- Deployed at beta=2: recall 0.864, but 59% of alarms are false

## THE CENTRAL PROBLEM: the model cannot rank sites
- within_r2 0.42 vs between_r2 0.21
- macro_auc 0.90 vs between_rate_r2 0.24
The project's own summary: "a good timer, a mediocre site-ranker." It captures WHEN nitrate rises at a given site, but fails at the ABSOLUTE LEVEL of an unseen basin. Essentially all remaining headroom is in the between-site term.

## CRITICAL SAMPLE-SIZE CONSTRAINT
The ~85 sites collapse to only 19 INDEPENDENT BASIN FAMILIES under the leak-safe CV (sites whose basins nest and whose records overlap in time are grouped). So learning a site's absolute level is a regression with roughly 19 effective independent units, NOT 160k rows. Any method whose published gains come from hundreds of catchments (e.g. CAMELS with 531-671 basins) must be explicitly flagged as possibly infeasible here.

## Current features (68 for CLF)
9 weather variables x 3 distance buckets (SAME-DAY SCALARS ONLY), 8 CDL crop classes x 3 buckets, 2 nitrogen-surplus x 3 buckets, doy_sin/doy_cos, 4 cross-site nitrate lags (rest_of_state_nitrate_lag1/2/3/5), 5 static (lat, lon, log_basin_area, mean_dist_to_sensor, max_dist_to_sensor).
Top features by permutation importance: rest_of_state_nitrate_lag1, lon, lat, doy_sin, fuel_moisture_1000h. BOTTOM: all daily weather; precip_in_1d is dead last (perm ~0.000).

## KNOWN NEGATIVE RESULTS - do not naively re-propose these
- Lagged weather copies: HURT badly (loso_r2 0.116 -> 0.019)
- Rolling precip / water balance as implemented: worse (lofo_r2 0.013 vs 0.003)
- Broadcasting 2017 N-surplus forward in time: much worse (-0.166)
- Rolling (3D/7D) smoothed targets: worse
- Spike / anomaly z-score targets: failed cross-site (lofo_r2 ~ -0.0006)
- Antecedent moisture + rain x surplus interactions: REG a wash, CLF only +0.017 PR-AUC
- Riparian 0-2km bucket: CLF +0.039 PR-AUC but REG worse
- Advective travel-time routing IS ALREADY IMPLEMENTED (weather per distance bucket shifted by median flow distance / 2.1 m/s) and is part of the "lagged weather hurt" failure.
PATTERN: nearly every hydrologically-motivated TEMPORAL feature has failed. The only wins came from geometry and static geography. This strongly suggests the deficit is in BETWEEN-SITE static information, not in temporal dynamics.

## Data present on disk but UNUSED
- discharge/streamflow: only 22 of 85 sites (all USGS, 0.66-0.97 density, up to 18 years). ZERO of the 61 IWQIS sites.
- co-located water chemistry: specific conductance (65 sites), water temp (80), pH (64), dissolved oxygen (64), turbidity + phosphate (61)
- upstream_uid (28 sites) / downstream_uid (40 sites) river-network topology: completely unread
- draining_area (80/85), NHDPlus comid (stored per basin), basin containment graph (used only to build CV folds)

## Known data problems
- Nitrogen surplus (gTREND product) ENDS IN 2017 -> NaN for 68% of training rows
- 16.5% of grid cells lack crop/surplus data; an inner join silently UNDER-COUNTS crop area and N surplus for basins extending outside Iowa (worst for the largest multi-state basins)
- Tile drainage density: known to be the dominant hydrologic control in Iowa, NOT in the feature set, and NO data source has been identified yet
`

const RULES = `
## Non-negotiable rules for this research

1. RECORD THE SPLIT PROTOCOL FOR EVERY PERFORMANCE NUMBER, AND DO NOT COMPARE NUMBERS ACROSS PROTOCOLS. Random-split results are valid evidence about method behavior and should be reported - researchers using them often have good reasons for their own question. But only SPATIALLY HELD-OUT numbers (leave-one-basin-out, leave-one-region-out, ungauged-basin protocol) are comparable to our LOFO 0.820, because a random split over autocorrelated site-days lets a model memorize a site's level and measures interpolation rather than transfer. When the protocol cannot be determined, say so rather than guessing.
2. REPORT EFFECT SIZES AND SAMPLE SIZES, NOT CONCLUSIONS. "GNNs improve water quality prediction" is worthless. "Recurrent GCN, 456 reaches, 42 monitored, held-out-node RMSE 1.83 vs 2.11 for LSTM" is actionable. Always give the number of catchments/basins/sites used.
3. EVERY DATASET RECOMMENDATION MUST BE OPERATIONAL: exact name, spatial resolution, temporal resolution, coverage years, whether it covers ALL of Iowa AND the multi-state portions of basins, exact URL, access mechanism (API/bulk/FTP), authentication requirement, and license.
4. ACTIVELY HUNT FOR NEGATIVE RESULTS. Seven feature-engineering ablations have already failed on this project. Papers reporting that a technique did NOT transfer are as valuable as successes and are systematically under-published - look in discussion/limitations sections and in benchmark comparison papers.
5. FLAG THE n=19-FAMILIES CONSTRAINT against every recommendation. If a method's evidence base comes from 300+ basins, say so explicitly and assess whether it can work at n=19.
6. ACQUISITION CONSTRAINT: recommend only free/public data, or data obtainable by a reasonable request (e.g. emailing IIHR or Iowa DNR). No paid datasets, no field campaigns, no new sensor deployment.
7. Prefer peer-reviewed literature and authoritative agency sources over blog posts. Give DOIs where they exist.
8. JUDGE EVERYTHING AGAINST THE DEPLOYMENT TARGET: an arbitrary ungauged map pin with no local history. State plainly when a method only works at gauged sites.
`

const ANGLES = [
  {
    key: 'A1-pub-ceiling',
    title: 'Prediction in Ungauged Basins: what regionalization transfers, and what is the ceiling',
    question: `In the 25+ years of Prediction in Ungauged Basins (PUB) and catchment-similarity/regionalization literature, which regionalization strategy (spatial proximity, physical-attribute similarity, regression on catchment attributes, donor-catchment transfer, hierarchical/partial pooling) gives the largest skill gain when predicting a CONCENTRATION or EXCEEDANCE signal at an unmonitored site? And critically: what BETWEEN-BASIN skill (R2 of site means, or equivalent) do published studies actually attain when they have fewer than ~100 donor catchments?`,
    why: `Our diagnosis (within_r2 0.42 vs between_r2 0.21) is the textbook PUB signature. Before investing in features or architecture we need to know whether between_r2 = 0.21 is bad or is near the published ceiling for ~19 independent basins. This calibrates expectations for every other angle.`,
    good: `A ranked comparison of regionalization families with reported effect sizes. Explicit evidence on how skill SCALES WITH NUMBER OF DONOR CATCHMENTS (a curve or table, not an assertion). At least 2-3 papers doing WATER QUALITY PUB rather than streamflow PUB - and if none exist, say so plainly, because that absence is itself a finding. The standard evaluation protocol. A verdict on whether attribute-based similarity beats plain spatial proximity in flat, agriculturally homogeneous regions - note that lat/lon are already top-3 features for us, suggesting proximity may already be doing this work. Seeds: Bloschl et al. "Runoff Prediction in Ungauged Basins" (IAHS PUB Decade); Hrachowitz et al. 2013 "A decade of PUB"; Razavi & Coulibaly 2013 regionalization review; Oudin et al. catchment similarity.`,
  },
  {
    key: 'A2-between-site-attributes',
    title: 'Basin attributes that explain cross-site mean nitrate in tile-drained corn belt catchments',
    question: `In peer-reviewed Midwest/Corn Belt studies, which STATIC basin attributes explain cross-site variance in mean or exceedance-frequency stream nitrate concentration, and with what partial effect sizes? For each attribute, does a downloadable, basin-aggregatable dataset covering Iowa (and neighboring states) exist?`,
    why: `Every failed ablation on this project was a TEMPORAL feature; the only wins were static/geographic. The model times events well and cannot rank sites. This angle attacks the actual deficit with new between-site information. Tile drainage is the acknowledged dominant control in Iowa and we currently have NO data source for it - resolving that single item may be the highest-value output of this entire research effort.`,
    good: `A table of candidate attributes - artificial/tile drainage extent, baseflow index, soil drainage class and hydrologic soil group (SSURGO), depth to water table, subsurface residence/transit time proxies, riparian buffer and wetland fraction, stream/drainage density, tile-weighted N surplus, manure vs commercial N split, soil organic N - each with reported effect size and direction, plus the specific dataset (name, resolution, years, URL, access, license). MUST resolve tile drainage concretely: check Valayamkunnath et al. 2020 (30 m CONUS tile drainage map), Nakagaki & Wieczorek USGS drainage layers, Sugg 2007 (drained agricultural land), and USDA NASS Ag Census county tile acreage. MUST also address the N-surplus 2017 cliff: is there a post-2017 continuation, an alternative N-input product (USGS county-level N fertilizer/manure estimates - Falcone, Brakebill/Gronberg), or a defensible argument for using surplus as a STATIC basin attribute (a basin's rank is stable over time) rather than as a broadcast time series - note the failed broadcast experiment does NOT rule out the static-rank use.`,
  },
  {
    key: 'A3-cq-mass-balance',
    title: 'The fluid dynamics angle: concentration-discharge behavior, dilution mass balance, and modeled streamflow',
    question: `What does the concentration-discharge (C-Q) literature say about nitrate in agricultural catchments - chemostatic vs dilution vs mobilization behavior - and would knowing discharge at an ungauged site, obtained from a MODELED source such as the NOAA National Water Model retrospective, convert into between-site skill for concentration and exceedance prediction?`,
    why: `This is the concrete, defensible content of "could basic fluid dynamics help." Advective in-channel routing was ALREADY tried on this project and failed, which is expected: channel travel time in an Iowa basin is hours-to-days, while the nitrate signal is set by subsurface transit times of months-to-years. What is UNTESTED is the mass-balance side: C = load / Q. Discharge exists for only 22 of 85 sites - but if the National Water Model retrospective provides simulated Q for every NHDPlus reach, that is a complete new covariate axis with NO coverage gap, joinable via the comid already stored per basin, available at any arbitrary map pin, and it is the physically correct denominator. Nitrate in tile-dominated catchments is widely reported as near-chemostatic, which would simultaneously EXPLAIN why our weather features are useless and why the level term is weak.`,
    good: `The C-Q slope regimes for nitrate in agricultural catchments and what controls them. Whether the literature supports concentration ~ N-input-yield / specific-discharge as a BETWEEN-SITE relation, with reported R2. Concrete evaluation of NWM v3 retrospective: coverage, years, hourly/daily, access via AWS S3/Zarr, per-reach COMID join, and its KNOWN BIAS in tile-drained low-relief basins (this bias assessment is essential - modeled Q error could swamp the signal). Equivalent evaluation of alternatives: USGS SPARROW predicted mean-annual flow and N yield per reach, GAGES-II, and regionalizing streamflow from our own 22 gauged sites. Finally: whether LOAD (C x Q) is a better modeling target with concentration back-derived - that changes the target definition, not just the features. Seeds: Godsey/Kirchner power-law C-Q; Basu et al. chemostatic behavior / biogeochemical stationarity; Thompson et al.; Van Meter & Basu legacy nitrogen and transit times; Damkohler number framing (Oldham, Basu).`,
  },
  {
    key: 'A4-lstm-transfer',
    title: 'Do LSTM / deep hydrologic models beat gradient-boosted trees for ungauged basins at small n',
    question: `In head-to-head, SPATIALLY HELD-OUT comparisons, do LSTM, entity-aware LSTM, or hybrid differentiable models beat gradient-boosted decision trees for ungauged-basin prediction, specifically for WATER QUALITY targets? And at what number of training basins does any deep-model advantage actually appear?`,
    why: `This is the angle most likely to be oversold and it needs the most skeptical treatment. Kratzert's PUB results come from 500+ CAMELS basins with rich static attributes; the mechanism of the gain is learning a shared dynamical process across many catchments while conditioning on static attributes. We have ~19 independent families, and our failure mode is the STATIC/LEVEL term - precisely the part an LSTM handles via the same static-attribute conditioning a tree already receives. If the literature shows the crossover requires hundreds of basins, close this angle fast and cheaply.`,
    good: `The specific PUB experiments (Kratzert et al. 2019 "Towards learning universal, regional and local hydrological behaviors via machine learning"; Kratzert et al. ungauged-basin work) with their metrics AND basin counts. Any ablation reporting performance vs NUMBER OF TRAINING BASINS. Any study applying LSTM to nitrate or nutrient CONCENTRATION with spatial holdout - and whether it beat RF/XGBoost or merely beat a linear baseline. An explicit note on whether reported gains persist under leave-one-REGION-out rather than leave-one-basin-out. A recommendation on the cheaper middle ground: multi-task / shared-embedding models, site-embedding regularization, or per-site random effects layered on top of the existing booster.`,
  },
  {
    key: 'A5-network-topology',
    title: 'River-network structure: graph neural networks vs cheap mass-balance topology features',
    question: `Does exploiting river-network topology - either graph neural networks over reaches, or explicit upstream-downstream mass balance - improve prediction at UNMONITORED nodes? What network size and monitored-node density do published successes require?`,
    why: `We have upstream_uid (28 sites), downstream_uid (40 sites), a basin containment graph, and draining_area (80/85) - all unread, with the containment graph used only to REMOVE data when building CV folds. Two distinct ideas must be separated: (a) GNN message passing as an architecture, and (b) the far cheaper deterministic trick - for a nested pair, downstream load = upstream load + incremental-area load, so an upstream sensor reading is a legitimate, causally-ordered predictor. Idea (b) may deliver most of the value with none of the complexity, BUT it only helps at sites that HAVE a monitored upstream neighbor, which is NOT the deployment case (an arbitrary ungauged map pin). Surface this tension explicitly; do not gloss over it.`,
    good: `Leading river-network GNN papers (Jia/Kumar et al. process-guided recurrent GCN for stream temperature in the Delaware River Basin; Moshe et al. HydroNets; graph models for water quality) with node counts, monitored fractions, and HELD-OUT-NODE results. A clear statement of whether the reported gain is at UNMONITORED nodes or only at gauged nodes with missing time periods - this distinction decides the whole angle. The incremental-area mass-balance literature (USGS load estimation, SPARROW's routing formulation). A blunt assessment of whether an 85-node, ~40-edge graph can support any of it - including whether the honest answer is "use topology as FEATURES (upstream drainage area, Strahler stream order, distance to confluence, position in network) rather than as an architecture." Note that topology-derived FEATURES are computable at an arbitrary ungauged pin from NHDPlus, whereas upstream-sensor-reading features are not.`,
  },
  {
    key: 'A6-process-model-priors',
    title: 'Borrow a calibrated process model instead of learning physics: SPARROW / SWAT / Iowa NRS outputs as features',
    question: `Which calibrated process-model outputs for nitrogen in Iowa and the Mississippi basin are publicly downloadable per NHDPlus reach or per catchment? And does injecting process-model output as an INPUT FEATURE to an ML model improve ungauged-site skill, relative to the ML model alone?`,
    why: `The pattern of failures says hand-built hydrologic features fail here; the pattern of wins says static geography works. A calibrated process model's predicted mean-annual N yield for a reach is exactly a static geographic feature that encodes decades of hydrology, fitted on far more monitoring data than our 85 sites, and available at any arbitrary pin via COMID. This is plausibly the cheapest route to a strong between-site prior and it sidesteps the "our engineered feature failed again" trap entirely. It is also the honest interpretation of "physics-informed" for a project that will not be writing a PDE solver.`,
    good: `For USGS SPARROW (especially Midwest / Mississippi-Atchafalaya total-nitrogen models): what is published, at what spatial unit, downloadable how, which vintage, incremental vs total yield, and known limits. Likewise for the Iowa Nutrient Reduction Strategy modeling, EPIC/APEX/SWAT Iowa applications, and any published gridded nitrogen-leaching product. The process-guided ML literature (Read, Jia, Karpatne): pretrain on process-model output then fine-tune on observations, and physics-informed loss terms - with reported gains and the specific conditions under which pretraining helped (usually sparse labels, which is exactly our between-site situation). A caution about CIRCULARITY if a SPARROW model was itself calibrated using some of our 85 monitoring sites.`,
  },
  {
    key: 'A7-level-shape-decomposition',
    title: 'Fix the output not the input: level/shape decomposition, regional duration curves, hierarchical pooling',
    question: `What methods explicitly separate "when does it go up" (which we do well) from "how high is it at this site" (which we do badly)? Specifically: regional flow/concentration-duration-curve transfer (the QPPQ method), quantile mapping, hierarchical/partial-pooling models with site random effects predicted from attributes, distributional/quantile regression, and domain adaptation. What gains do these report for UNGAUGED sites?`,
    why: `This may be the highest-leverage angle and the user's framing under-weights it. macro_auc 0.90 with between_rate_r2 0.24 means the model already produces a nearly correct rank ordering WITHIN each site. QPPQ, a workhorse PUB method, does exactly this: take a donor's exceedance-probability series, then map probabilities to magnitudes through a REGIONALIZED duration curve estimated from basin attributes. The reformulation for us: predict percentile (we are good at this, 160k rows) x predict the site's concentration-duration curve from static attributes (a small-n regression with ~85 targets, well-posed, and it directly determines exceedance rate). This converts one ill-posed problem into two problems each matched to its own sample size.`,
    good: `The QPPQ / regional duration-curve literature with reported skill for ungauged sites, and HOW the duration curve is regionalized from attributes. Whether anyone has applied it to WATER QUALITY - concentration-duration curves, and especially LOAD-DURATION CURVES, which are standard in US TMDL practice and are a promising, underused literature here. The hierarchical Bayesian / mixed-model alternative (site random intercept predicted from basin attributes) with evidence. Quantile regression forests / distributional GBDT options. Post-hoc site-level calibration and quantile mapping, WITH a clear warning about what is estimable when the target site has ZERO observations - this is the deployment case and it is the crux of whether this angle is usable.`,
  },
  {
    key: 'A8-target-methodology',
    title: 'Exceedance methodology, and whether the prediction target itself is right',
    question: `Two separate questions. (a) What methods are established for predicting THRESHOLD EXCEEDANCE in environmental time series - direct binary classification vs regression-then-threshold vs distributional-then-integrate, extreme-value / peaks-over-threshold approaches, cost-sensitive and calibrated classification? (b) Is "daily maximum >= 10 mg/L in raw stream water" the target the water-quality literature would actually use?`,
    why: `Two independent issues. Methodologically: at base rate 0.263 the problem is only mildly imbalanced, so exotic resampling is probably a distraction; the live question is whether modeling the distribution and integrating above 10 mg/L beats direct classification, and whether the deployed 59% false-discovery rate comes from calibration (Brier 0.137) or from the beta-threshold choice. On framing: the 10 mg/L MCL is a standard for FINISHED DRINKING WATER AT THE TAP, applied here to instantaneous stream concentration - defensible as a screening proxy, but the literature may show operationally relevant targets are running annual averages or concentrations at drinking-water intakes. Also: daily MAX from a 5-15 minute sensor is a high-variance order statistic sensitive to record density and sensor drift, which would inject SITE-SPECIFIC bias into exactly the between-site term we care about. This angle is allowed to conclude "the current target is fine" - but the check is cheap and the failure mode (optimizing hard against a partly artifactual target) is expensive.`,
    good: `Evidence on regression-then-threshold vs direct classification for environmental exceedance, with metrics. Whether peaks-over-threshold / extreme value theory is used for water quality. The regulatory definition of the nitrate MCL and its averaging period, and how monitoring and TMDL practice operationalize exceedance for STREAMS as opposed to treated water. Any literature on sensor-derived daily statistics and sampling-frequency bias. Guidance on evaluation metrics for spatially-held-out exceedance prediction, so our 0.82 AUC / 2.41x lift can be compared to published virtual-sensor numbers rather than to nothing.`,
  },
]

const SOURCES_SCHEMA = {
  type: 'object',
  required: ['sources'],
  properties: {
    sources: {
      type: 'array',
      items: {
        type: 'object',
        required: ['url', 'title', 'why'],
        properties: {
          url: { type: 'string' },
          title: { type: 'string' },
          why: { type: 'string' },
          kind: { type: 'string' },
        },
      },
    },
  },
}

const CLAIMS_SCHEMA = {
  type: 'object',
  required: ['claims'],
  properties: {
    sourceQuality: { type: 'string' },
    claims: {
      type: 'array',
      items: {
        type: 'object',
        required: ['text'],
        properties: {
          text: { type: 'string' },
          url: { type: 'string' },
          splitProtocol: { type: 'string', description: 'spatial-holdout | random-split | temporal-holdout | unknown | not-applicable' },
          nBasins: { type: 'string' },
          effectSize: { type: 'string' },
        },
      },
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['refuted', 'confidence', 'evidence'],
  properties: {
    refuted: { type: 'boolean' },
    confidence: { type: 'string' },
    evidence: { type: 'string' },
    correction: { type: 'string' },
  },
}

const ANGLE_SCHEMA = {
  type: 'object',
  required: ['angle', 'findings', 'verdict'],
  properties: {
    angle: { type: 'string' },
    verdict: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'confidence'],
        properties: {
          claim: { type: 'string' },
          confidence: { type: 'string' },
          effectSize: { type: 'string' },
          splitProtocol: { type: 'string' },
          nBasins: { type: 'string' },
          sources: { type: 'array', items: { type: 'string' } },
          feasibilityAtN19: { type: 'string' },
          worksAtUngaugedPin: { type: 'string' },
        },
      },
    },
    datasets: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'url'],
        properties: {
          name: { type: 'string' },
          url: { type: 'string' },
          resolution: { type: 'string' },
          years: { type: 'string' },
          access: { type: 'string' },
          license: { type: 'string' },
          coversMultiState: { type: 'string' },
          whyUseful: { type: 'string' },
          howToIncorporate: { type: 'string' },
          limitations: { type: 'string' },
        },
      },
    },
    refuted: { type: 'array', items: { type: 'string' } },
    uncovered: { type: 'array', items: { type: 'string' } },
  },
}

log(`Researching ${ANGLES.length} angles on the Iowa nitrate virtual-sensor problem`)

const angleResults = await pipeline(
  ANGLES,

  (a) => agent(
    `${PROJECT}\n${RULES}\n\n## Your assignment: search angle "${a.title}"\n\n### Research question\n${a.question}\n\n### Why this matters for this project\n${a.why}\n\n### What a good answer contains\n${a.good}\n\nRun several WebSearch queries covering this angle from different phrasings (academic terms, dataset names, author names, negative-result phrasings). Return the 7 most valuable, distinct, high-quality sources. Prefer peer-reviewed papers and authoritative agency pages. Do NOT return generic overviews or blog posts when a primary source exists.`,
    { label: `search:${a.key}`, phase: 'Search', schema: SOURCES_SCHEMA }
  ),

  (s, a) => parallel(
    (s?.sources || []).slice(0, 6).map((src) => () =>
      agent(
        `${PROJECT}\n${RULES}\n\n## Your assignment\nFetch and read this source, then extract falsifiable claims relevant to the research angle below.\n\nURL: ${src.url}\nTitle: ${src.title}\n\n### Research angle\n${a.title}\n\n### Research question\n${a.question}\n\n### What we need from it\n${a.good}\n\nUse WebFetch. If the source is paywalled or unreachable, say so and extract what you can from metadata, abstract mirrors, Crossref/PubMed/OpenAlex, or Semantic Scholar - and mark the claims accordingly.\n\nExtract up to 6 SPECIFIC, FALSIFIABLE claims. Each must be self-contained (understandable without the surrounding text). For every claim involving a performance number you MUST record splitProtocol and nBasins - if you cannot determine them, record "unknown". Quote exact figures wherever possible.`,
        { label: `fetch:${a.key}`, phase: 'Fetch', schema: CLAIMS_SCHEMA }
      )
    )
  ).then((rs) => (rs || []).filter(Boolean).flatMap((r) => r.claims || [])),

  (claims, a) => {
    const top = (claims || []).slice(0, 7)
    log(`${a.key}: ${(claims || []).length} claims extracted, verifying ${top.length}`)
    return parallel(
      top.map((c) => () =>
        parallel(
          ['primary-source', 'methodology', 'transferability'].map((lens) => () =>
            agent(
              `${PROJECT}\n${RULES}\n\n## Your assignment: REFUTE this claim\n\nCLAIM: ${c.text}\nCited source: ${c.url || 'unspecified'}\nReported effect size: ${c.effectSize || 'none'}\nReported split protocol: ${c.splitProtocol || 'unknown'}\nReported n basins: ${c.nBasins || 'unknown'}\n\nYou are an adversarial verifier working the "${lens}" lens:\n- primary-source: does the cited source actually say this? Fetch it. Is it misattributed, exaggerated, or a garbled paraphrase? Are quoted numbers exact?\n- methodology: is the underlying study sound? What was the split protocol, and is the claim being used in a way that protocol supports? Is n large enough? Is the metric appropriate? Is there pseudoreplication or leakage?\n- transferability: even if TRUE as stated, does it transfer to OUR setting - Iowa tile-drained agricultural basins, ~19 independent basin families, water-quality exceedance rather than streamflow, and prediction at an ARBITRARY UNGAUGED MAP PIN with no local history? A claim true in CAMELS with 531 basins may be useless at n=19; a method needing the target site's own observations is useless for this product.\n\nDefault to refuted=true if you cannot substantiate the claim. Being unable to verify is grounds for refutation, not for charity. If the claim is directionally right but wrong in detail, set refuted=false and supply a precise "correction".`,
              { label: `verify:${a.key}`, phase: 'Verify', schema: VERDICT_SCHEMA }
            )
          )
        ).then((votes) => {
          const vs = (votes || []).filter(Boolean)
          const kills = vs.filter((v) => v.refuted).length
          return { claim: c, votes: vs, survived: vs.length > 0 && kills < 2 }
        })
      )
    )
  },

  (verified, a) => {
    const alive = (verified || []).filter(Boolean).filter((v) => v.survived)
    const dead = (verified || []).filter(Boolean).filter((v) => !v.survived)
    log(`${a.key}: ${alive.length} claims survived, ${dead.length} refuted`)
    // SPLIT-RUN: stop after verification. Synthesis runs later from the journal snapshot.
    return { angleKey: a.key, angleTitle: a.title, question: a.question, good: a.good, alive, dead }
    return agent(
      `${PROJECT}\n${RULES}\n\n## Your assignment: synthesize the findings for angle "${a.title}"\n\n### Research question\n${a.question}\n\n### What a good answer contains\n${a.good}\n\n### Claims that SURVIVED adversarial verification\n${JSON.stringify(alive.map((v) => ({ claim: v.claim.text, url: v.claim.url, effectSize: v.claim.effectSize, splitProtocol: v.claim.splitProtocol, nBasins: v.claim.nBasins, corrections: v.votes.map((x) => x.correction).filter(Boolean) })), null, 1)}\n\n### Claims that were REFUTED (do not propagate; report them as corrections)\n${JSON.stringify(dead.map((v) => ({ claim: v.claim.text, why: v.votes.filter((x) => x.refuted).map((x) => x.evidence).slice(0, 2) })), null, 1)}\n\nProduce the structured finding set. Merge semantic duplicates. Apply every correction from the verifiers. Rank findings by relevance to the BETWEEN-SITE gap specifically. For each finding give feasibilityAtN19 and worksAtUngaugedPin honestly. Populate "datasets" for every concrete data source, with howToIncorporate written concretely enough to act on - name the feature that would be created and how it would be aggregated to a basin. Populate "uncovered" for any part of the question that produced nothing verifiable - absence of evidence must be reported as absence, never silently dropped. Your "verdict" must state plainly whether this angle is worth pursuing and why.`,
      { label: `synth:${a.key}`, phase: 'Angle synthesis', schema: ANGLE_SCHEMA }
    )
  }
)

const good = (angleResults || []).filter(Boolean)
log(`${good.length}/${ANGLES.length} angles synthesized; running cross-angle ranking`)

const FINAL_SCHEMA = {
  type: 'object',
  required: ['bottomLine', 'ranked'],
  properties: {
    bottomLine: { type: 'string' },
    ceilingAssessment: { type: 'string' },
    fluidDynamicsAnswer: { type: 'string' },
    ranked: {
      type: 'array',
      items: {
        type: 'object',
        required: ['recommendation', 'category', 'expectedGain', 'effort'],
        properties: {
          recommendation: { type: 'string' },
          category: { type: 'string', description: 'data-new | data-existing | model | reframing | correctness' },
          expectedGain: { type: 'string' },
          effort: { type: 'string' },
          evidence: { type: 'string' },
          feasibilityAtN19: { type: 'string' },
          worksAtUngaugedPin: { type: 'string' },
          howToIncorporate: { type: 'string' },
          risk: { type: 'string' },
        },
      },
    },
    conflicts: { type: 'array', items: { type: 'string' } },
    uncovered: { type: 'array', items: { type: 'string' } },
  },
}

// SPLIT-RUN: verification-only. Cross-angle synthesis deferred to a separate run.
return { final: null, angles: good, stoppedBeforeSynthesis: true }

const final = await agent(
  `${PROJECT}\n${RULES}\n\n## Your assignment: cross-angle synthesis and ranking\n\nYou have the verified findings from ${good.length} research angles. Produce the final ranked set of recommendations for improving this virtual-sensor classification problem.\n\n### All angle results\n${JSON.stringify(good, null, 1)}\n\n### How to rank\nThe binding constraint is the BETWEEN-SITE gap (between_r2 0.21, between_rate_r2 0.24) - the model times events well and ranks sites badly. Rank by expected improvement to THAT term, not to aggregate metrics. Weigh effort honestly. Mark anything infeasible at n=19 independent basin families, and mark anything that cannot run at an ARBITRARY UNGAUGED MAP PIN - the latter is disqualifying for the shipped product regardless of how good the method is.\n\nCategorize each recommendation as: data-new (acquire a new dataset), data-existing (better use of what is already on disk), model (architecture or method change), reframing (change the problem formulation or target), correctness (fix something broken).\n\nYou MUST answer the fluid-dynamics question directly and specifically - the user asked whether "basic fluid dynamics additions" would help, and the honest answer must account for the fact that advective travel-time routing was already implemented and already failed, so the answer turns on whether the MASS-BALANCE side (C = load/Q, specific discharge, dilution) is different in kind.\n\nFlag any recommendation that resembles one of the seven known failed ablations, and say what would have to be different for it to work this time.\n\nReport conflicts between angles rather than smoothing them over. Report what remains uncovered.`,
  { label: 'cross-angle-synthesis', phase: 'Cross-angle', schema: FINAL_SCHEMA }
)

return { final, angles: good }
