# Model architectures beyond XGBoost

Written 2026-07-31, revised 2026-08-01. Cross-references `new-data-report.md` — most of what follows needs data we do not currently hold, and the data acquisition is shared across proposals.

## Bottom line

A portfolio, not a single bet. Ordered by cost:

| | proposal | why it should win | needs |
|---|---|---|---|
| 1 | **Physical removal term** (Crumpton) | addresses a failure mode no current feature can express | NID |
| 2 | **Distance-gated lag-0 donor** | travel time across an inflow/outflow pair is *minutes*; lag 1 is a day stale | one file |
| 3 | **Non-nitrate donor covariates** | cheap proxy for the GNN's core mechanism — tests it before building it | E1/E4, already scoped |
| 4 | **GNN over the full sensor network** | propagates *observed* state; edge attributes can encode structures; unlabelled reaches carry information between distant gauges | large — see §3 |
| 5 | **LSTM + static attributes** | the only architecture with a published win in our regime | nothing new |
| 6 | **Distributional / quantile GBDT** | uncertainty at a pin, which we currently cannot express | nothing new |

**A correction to the previous version of this report.** It dismissed GNNs on the grounds that our containment graph is transitively closed, so multi-hop message passing has nothing to do. That measurement was taken on the **116-node nitrate-site graph** and does not generalise to the graph actually worth building — one spanning every gauge in the region plus COMID reaches as intermediates. The dismissal was aimed at the wrong object and is withdrawn. §3 gives the design it should have engaged with.

---

## 1. Physical removal term (do first — not an architecture change)

Per structure upstream of a site, from NID's pool and drainage areas:

$$\text{removal} = 1.03\,\mathrm{HLR}^{-0.33},\qquad \mathrm{HLR} = \frac{\text{watershed water yield}}{\text{pool area}/\text{watershed area}}$$

Aggregate `Σ (treated_area_fraction × removal)`; separately weight the treated sub-basin's covariates by `(1 − removal)` rather than counting them at full strength. Fitted on Iowa CREP wetlands, $R^2 = 0.69$, and its 40–78% band brackets the 47–88% we measure across our five inflow/outflow pairs.

This is the only item here that addresses a failure mode nothing in the feature set can currently express, and it needs one datum per structure that NID already tabulates.

## 2. Lag-0 donor, distance-gated

`neighbors.py` emits donor nitrate at lags 1 and 3; every arm is `*_no_lag0`. For an inflow/outflow pair a few hundred metres apart, travel time is **minutes** — lag 1 is already a day stale, so lag 0 is the only lag carrying the treatment signal. Saha's dominant predictor is the *contemporaneous* neighbour mean.

Legitimate for nowcasting, not for prediction at an ungauged pin. Report as a separate arm; decide gate G3 first.

## 3. A GNN over the sensor network

### 3.1 Why this is the right shape for our problem

Three things a flat per-site feature vector structurally cannot do, and message passing can:

**Propagate observed state.** Our donor block is a hand-built 1-hop approximation of exactly this — take the nearest gauge, copy its nitrate in as a column. A graph model generalises it: state flows along the network, through any number of hops, weighted by learned functions of the edges. Where we hard-code "nearest donor by area or distance", the model learns which neighbours matter.

**Represent transformations on edges.** A wetland is a transformation between two nodes. Given edge attributes — pool area, HLR, an `is_treatment` flag — a GNN learns the *functional form* of removal from data. Our Crumpton term hardcodes an exponent of −0.33 fitted on 34 wetland-years; the GNN version is strictly more general and can discover interactions (removal varying with season, with load, with residence time) that a fixed equation cannot.

**Carry information through unlabelled nodes.** This is the part a flat model has no answer to at all. Two nitrate gauges 80 km apart with a dozen intervening reaches: the reaches have land use, tile density, travel time, and possibly a discharge gauge — but no nitrate label. In the Delaware RGCN, **456 stream segments were modelled and only 183 carried streamflow observations**; the unlabelled majority exist to route information. Our 116-site count is a *label* count, not a node count, and I previously conflated the two.

### 3.2 Concrete design

**Nodes** — three kinds, one feature space with modality masks:

| kind | count (est.) | has nitrate? | role |
|---|---|---|---|
| nitrate sensors (IWQIS + USGS) | 116 | yes | labelled, supervised |
| other gauges (Q, stage, temp, spec cond, turbidity, DO, pH) | hundreds | no | observed state, unlabelled for nitrate |
| COMID reaches | thousands | no | routing intermediates |
| **virtual pin** | 1, at inference | no | the deployment target |

Node features: static catchment attributes we already build (crops, surplus, tile, geometry) + **StreamCat annual N inputs 1987–2017** (COMID-keyed, so available on *every* node including unlabelled reaches — this is what makes intermediate nodes informative rather than empty) + whatever that sensor reports dynamically, with a **per-modality missingness mask**. A reach node has statics and a mask of all-zeros on the dynamic block.

**Edges** — NHDPlus downstream connectivity, directed. Edge features:

- `flow_dist_m` (correct as of the 2026-07-31 D8 fix)
- **`totma` / `pathtimema` from NHDPlus VAA** — travel time in days. This is the physically right edge weight; distance is a proxy for it.
- area ratio (the dilution term)
- **structure attributes**: NID pool area and HLR, NHD FCode 43624 `is_treatment` flag, computed Crumpton removal fraction

Those last two rows are the ones that let the model learn the wetland effect, and they come from exactly the data `new-data-report.md` already recommends acquiring. **The data programme and the GNN are the same programme.**

**Architecture.** The Delaware RGCN pattern — an LSTM per node, plus a graph-convolution step mixing hidden states along the adjacency each timestep. Precedent: Jia et al. 2021, and Topp et al. 2023 for the Graph WaveNet variant.

**Training — the part that solves our label-scarcity problem.** Multi-task pretraining on the abundant targets, fine-tune on the scarce one:

1. Pretrain the shared graph encoder on **discharge and water temperature**, which are observed at hundreds of nodes across decades. These are the well-behaved targets in the literature (temperature transfers at KGE 0.89 under spatial holdout; nutrients at < 0.45), and they encode transport and residence time — exactly the representation nitrate needs.
2. Fine-tune a nitrate head on the 116 labelled nodes.

This is the Delaware group's own recipe (they pretrained on process-model output, which we lack — abundant observed targets are the available substitute), and it directly attacks the reason the nutrient literature fails: too few labels to learn transport from scratch.

**Deployment.** Insert a node at the pin, delineate its basin, compute statics, attach it to the network by its snapped COMID, run a forward pass. That is more principled than our current nearest-donor block, which approximates the same thing with hand-chosen rules.

### 3.3 The four honest risks

**The published graph premium is small.** At *unmonitored* Delaware sites: LSTM 3.23 °C versus RGCN 3.18 °C. Dropping 20% of edges barely changes performance, and a 13×-coarsened graph matches the full one. **Expect the gain to come from the extra sensor data and the sequence modelling, not from topology per se.** If that is where the gain is, items 3 and 5 capture most of it far more cheaply — which is the argument for sequencing.

**The mechanism that wins in the literature does not transfer to a pin.** Where a graph model clearly beats an LSTM, XAI attributes it to Graph WaveNet's **self-adaptive adjacency**, `softmax(ReLU(E₁E₂ᵀ))` learned from per-node embeddings. Per-node embeddings are transductive — a virtual pin has none, and falls back to the population mean. So we would be restricted to the *physical* adjacency, giving up the specific mechanism that produced the published win. This is the sharpest objection to the whole proposal and it should be tested early: compare physical-adjacency RGCN against adaptive-adjacency Graph WaveNet *at held-out nodes*, not at in-sample ones.

**Co-location leakage, which LOFO will not catch.** 36 IWQIS nitrate sites sit within 200 m of a USGS gauge. A graph that propagates observed discharge will read the target site's own discharge through a one-hop edge — and **LOFO holds out sites, not instruments**, so the fold structure cannot detect it. Any sensor-network graph needs an explicit co-location exclusion radius as a design parameter, and a skill-versus-radius curve rather than a single number.

**It does nothing for the island model.** If skill comes from propagated observed state, a pin with no gauged neighbour is back to static attributes. The island variant is our genuinely unoccupied axis, and a GNN does not touch it. **This is a network-model architecture.**

### 3.4 Staged plan, so the expensive step is conditional

**Stage 0 — build the graph, fit nothing.** Enumerate gauges in the region by modality, snap to COMID, measure connectivity: how many nitrate sites have a non-nitrate gauge within *k* hops, what the modality coverage looks like, how many intermediate reaches sit between labelled pairs. Cheap, and it either establishes that a useful graph exists or kills the idea outright.

**Stage 1 — the cheap proxy (items 3, and E1/E4).** Add non-nitrate donor covariates through the existing flat pipeline. If propagated observed state helps, this shows it for a fraction of the cost, and it establishes the co-location radius the GNN will need anyway.

**Stage 2 — head-to-head.** RGCN vs LSTM vs tuned XGBoost, identical pool, identical basin-family folds, `macro_kge` and `macro_nse` as the metrics. Include the physical-vs-adaptive adjacency comparison at held-out nodes.

**Stage 3 — structure edges.** Only once the graph is shown to carry signal: add NID/FCode edge attributes and test whether the model recovers the removal relationship at the five gauged inflow/outflow pairs. **That is the cleanest possible test of "can it learn the wetland automatically"** — we know the answer for those five, so it is a supervised check on an unsupervised claim.

## 4. LSTM with static-attribute conditioning

The only architecture with a published win in our exact regime: Kratzert et al. 2019 reached median NSE **0.69** on 531 held-out CAMELS basins under 12-fold spatial CV, beating SAC-SMA calibrated *per basin* (0.64) and the National Water Model (0.58).

Why it should transfer: an LSTM carries **state**, and nitrate has memory a tree does not represent — antecedent wetness, legacy N accumulation, the flushing and dilution regimes Saha's c–Q analysis identified. Our features encode memory only through hand-built lags and rolling windows.

The static-attribute ablation in that paper is the load-bearing detail: median NSE **0.74 with** static attributes concatenated at every timestep, **0.63 without**. Regionalization is what makes PUB work, and it is exactly the mechanism we need.

**But do not promise a win.** On tabular problems near 10k samples trees remain state of the art and neural nets are hurt by uninformative features (Grinsztajn et al. 2022). On stream temperature across 10 catchments, XGBoost and a feed-forward net each won 4 of 6 with a median RMSE spread of 0.08 °C. The best continental nutrient model published is a random forest.

**The experiment is a contribution regardless of outcome**, because no tree-based model has ever been evaluated in the held-out-site regime for daily nutrients — XGBoost appears in this literature only where the site is in training, or where the target is a long-term mean.

## 5. Distributional / quantile GBDT

Zero sources found anywhere in the hydrology literature, low friction against the existing XGBoost, never tried here. Worth an afternoon because it addresses something we currently cannot express at all: **models trained on monitored sites are overconfident at unmonitored ones — 90% intervals covered ~60%.** A quantile objective gives a per-pin interval directly rather than a point estimate with an implied and wrong confidence.

Pairs with the `n_monitored_upstream == 0` UI guardrail: widen the band where the model has no neighbour, using a number rather than a heuristic.

## 6. Still rejected

**Per-site random effects contribute exactly zero under our CV.** The BLUP for site $j$ is a shrunk function of $j$'s own residuals; at a held-out site it collapses to the population mean by construction. This is the same objection as the adaptive-adjacency one in §3.3 — both are transductive.

**Spatial stream-network (tail-up) models are mis-specified here.** Tail-up covariance is stationary in stream distance; our inflow/outflow pairs are flow-connected at near-zero stream distance with a 3–8× concentration difference, precisely the configuration the model assumes is maximally correlated. Also $O(n^3)$ in observations, and our record is $10^5$–$10^6$.

**Mass-conserving architectures are the wrong inductive bias.** MC-LSTM conserves mass by construction; denitrification legitimately removes nitrogen as N₂. The thing we most need to model is the thing it forbids.

**Differentiable process models** are the design pattern one would want, but **no published application to nitrate or water quality exists**. A research programme, not an adoption.

---

## Sequencing

Items 1 and 2 first — they are days, not months, and they attack a failure mode we have measured directly. Item 3 next, because it is the cheap test of the GNN's core mechanism and it produces the co-location radius the GNN needs regardless.

Then Stage 0 of §3.4: build the graph and look at it. That is the decision point. If nitrate sites turn out to be sparsely connected to other-modality gauges, the GNN case weakens sharply and item 5 becomes the better bet. If they are densely connected — and with hundreds of USGS gauges in the region they may well be — then the architecture has genuine work to do, and the data acquisition in `new-data-report.md` was its first step whether or not we knew it.

## Evidence quality

**Verified from primary literature:** the Delaware dataset structure (456 segments, 183 with streamflow observations); Kratzert's scaling curve and static-attribute ablation; Topp's adaptive-adjacency finding; Zwart's unmonitored-site RMSEs; Sun's DropEdge and coarsening ablations; Feigl's six-model comparison; both Crumpton equations; the 36-site co-location count and the 116-node transitive-closure measurement, both from this repo.

**Reasoning, not published findings:** that multi-task pretraining on discharge and temperature would transfer usefully to nitrate (the analogous Delaware result pretrains on process-model output, not on observed alternate targets); that edge attributes suffice to learn a removal function; that a virtual pin can be inserted cleanly at inference. Each is plausible and none is demonstrated.

**Not established:** whether any of these beats tuned XGBoost on our data. No published comparison exists in this regime for nutrients, which is why Stage 2 is worth running rather than arguing about.
