# Extra In-Situ Covariates via Neighbouring USGS Sites

**Question:** the cohort's 116 nitrate sites carry almost no other covariate. Can a *second* stream of USGS in-situ sites — ones that measure discharge, stage, temperature, specific conductance, DO, pH or turbidity but not nitrate — be linked to each nitrate site as a nearest up/down neighbour, the way exps 33–36c link nitrate donors? Compiled 2026-07-30. Live USGS enumeration over the covariate footprint plus local measurement against the actual 116-site cohort. Companion to [future-work-report.md](future-work-report.md) (which rules discharge out on physics) and [new-site-report.md](new-site-report.md) (which inventories nitrate sites, not covariate sites).

---

## 1. Bottom line

**The pool is enormous for discharge and stage, and thin for everything else.** Live enumeration of USGS instantaneous-value stream sites inside the covariate footprint (lon −100.14…−85.85, lat 39.78…47.86) found **1,192 discharge** and **1,012 stage** sites against the 29 currently in the cohort — a 40× pool. Every water-chemistry covariate is one to two orders of magnitude smaller: temperature 198, specific conductance 97, turbidity 73, dissolved oxygen 65, pH 52.

**The fetch machinery already exists and is not the cost.** `src/build/util/usgs.py` defines all eight core pcodes — `00010 00060 00065 00095 00300 00301 00400 99133` — and already pulls every one of them for each site it collects. It is restricted to `USGS_SITE_LIST`, 26 hand-curated *nitrate* sites. Adding covariate-only sites is a list change plus a linking layer, not a new collector.

**Co-location is a controllable, not a blocker, and the control is free.** 36 IWQIS nitrate sites sit within 200 m of an Iowa USGS gauge, so a naive "nearest discharge gauge" feature would be reading *the target's own discharge* — untrainable-then-undeployable, since an arbitrary pin has no gauge. The fix is to hide every gauge within an exclusion radius `r` at training time and take the nearest survivor, simulating a pin that only has access to distant gauges. **Measured: coverage stays at 100% of the cohort for every `r` up to 100 km.** The discharge network is dense enough that no exclusion radius strands a site — so donor distance becomes a free design parameter rather than a coverage/realism trade-off.

That turns the central question from "is this deployable" into "how far away can a gauge be and still help", which is directly measurable as a skill-versus-`r` curve. It also composes with what exps 34/35 already do: `dist_to_neighbor` is a feature, so the model can condition on donor separation rather than assuming it fixed.

**Recommended sequence:** distance-stratified `nbr_discharge` block through the exp-33 harness at several exclusion radii (E1, ~4 hours of fits) → basin-containment linking (E2, ~1 day) → euclidean-vs-network donor selection (E3, ~2 hours) → promote on-disk covariates through the build (E4, half a day, independent of the rest). Do not touch the water-chemistry covariates at all; at 12–16% cohort coverage within 10 km and a median separation of 52 km, exclusion makes an already-unmeasurable feature worse.

---

## 2. What the cohort has today

Measured directly on `src/data/raw/water/{USGS,IWQIS}/*_water.parquet` for the exact 116-site contract cohort:

| parameter | sites | % of cohort | median days |
|---|---|---|---|
| `nitrate_con` | 116 | 100% | 270,158 |
| `temp_water` | 76 | 66% | 51,635 |
| turbidity (6 variants) | 52–57 | 45–49% | 57–92k |
| `spec_cond` | 52 | 45% | 43,042 |
| `diss_oxy_con` / `_sat` | 47 | 41% | 54k / 14k |
| `ph` | 46 | 40% | 58,182 |
| `stage` | 29 | 25% | 193,222 |
| `discharge` | 29 | 25% | 181,905 |
| `phosphate_con` | 2 | 2% | — |

**None of it reaches a model.** `make_water` writes only `nitrate_con` and `site_uid` into `processed/water/data/`, so every column above is dropped at the build boundary. That is a second, independent piece of work from acquiring new sites, and it is the cheaper one.

---

## 3. The available pool (live USGS enumeration, 2026-07-30)

USGS site service, `siteType=ST&hasDataTypeCd=iv`, queried per state over IA/MN/WI/IL/MO/NE/SD/KS and clipped to the `grid_global` bounding box. Distances are great-circle from each cohort site to the nearest pool site.

| covariate | pcode | IV sites in footprint | cohort <10 km | <25 km | <50 km | median km |
|---|---|---|---|---|---|---|
| discharge | 00060 | **1,192** | 76% | 97% | 100% | 0.1 |
| stage | 00065 | **1,012** | 73% | 96% | 100% | 0.2 |
| temp_water | 00010 | 198 | 34% | 56% | 84% | 21.7 |
| spec_cond | 00095 | 97 | 15% | 25% | 49% | 51.8 |
| turbidity | 63680 | 73 | 16% | 24% | 47% | 52.8 |
| diss_oxy_con | 00300 | 65 | 13% | 25% | 48% | 52.8 |
| ph | 00400 | 52 | 12% | 24% | 47% | 55.5 |
| nitrate_con | 99133 | 58 | 35% | 49% | 74% | 26.6 |

The 0.1 km median for discharge is partly an artifact — the 32 USGS cohort sites match themselves at distance 0. Restricting the pool to Iowa gauges and reading the distribution honestly:

| threshold | cohort sites with an Iowa IV discharge gauge within it |
|---|---|
| 50 m | 36 / 116 (31%) |
| 200 m | 54 / 116 (47%) |
| 1 km | 57 / 116 (49%) |
| 5 km | 68 / 116 (59%) |
| 10 km | 76 / 116 (66%) |

**36 of those within-200 m sites are IWQIS sites**, which carry no discharge of their own. So discharge could reach roughly 65 of 116 sites (56%) at essentially zero separation, against 29 (25%) today — by linking existing data, with no new delineation and no new collector. That zero separation is the problem §4 resolves: the deployable version deliberately throws those gauges away, and loses no coverage doing it.

---

## 4. Co-location is a design parameter, and the network is dense enough to make it free

`future-work-report.md` frames the deployment target as "an arbitrary Iowa map pin … **no discharge gauge**, no guaranteed monitored upstream neighbour." A feature read from a gauge 0 m from the nitrate sensor is that sensor's own discharge; training on it and deploying without it is train/serve skew, and LOFO will not catch it because LOFO holds out *sites*, not *instrument availability*.

The remedy is to make donor separation explicit: hide every gauge within an exclusion radius `r`, take the nearest survivor, and train the model at the separation you expect to deploy at. Measured over the full multi-state IV pool, nearest-surviving-donor distance against exclusion radius:

| exclusion `r` | cohort with a donor | median donor distance | p90 |
|---|---|---|---|
| 0 km (naive) | 100% | **0.1 km** | 21.0 |
| 1 km | 100% | 12.2 km | 25.2 |
| 5 km | 100% | 13.4 km | 26.5 |
| 10 km | 100% | 15.6 km | 26.6 |
| 20 km | 100% | 24.2 km | 32.5 |
| 50 km | 100% | 51.6 km | 55.6 |
| 100 km | 100% | 100.8 km | 102.9 |

Two things to read off it.

**No exclusion radius costs coverage.** At every `r` tested, all 116 sites retain a donor. That is the opposite of the water-chemistry situation and it is what makes the whole approach cheap: you can pick `r` on realism grounds alone, without trading away sites.

**The 0.1 → 12.2 km jump at `r = 1` confirms the co-location directly.** Most cohort sites have a gauge at essentially zero distance and then nothing until ~12 km. So the naive feature and the `r ≥ 1` feature are not variants of each other — they are different features, and only the second is deployable.

This preserves the property that makes the exps 33–36c neighbour work legal: R12 is deployment-legal because it "consumes *other* sites' concurrent observations, never the target's." A gauge at 15 km satisfies that; a gauge at 0 m does not.

**The residual caveat.** Training at radius `r` matches deployment only where the pin's true nearest gauge is also ~`r` away. Since coverage is free, the honest options are to train at the `r` matching the worst deployment case, or to train across a mixture of radii and let `dist_to_neighbor` — already a feature in exps 34/35 — carry the conditioning. The second is preferable and costs nothing extra.

Two further constraints worth stating before any of this is built:

**The physics objection stands independently.** `future-work-report.md` measured a median C–Q exponent of **b = +0.61** on this project's own 20 usable gauged sites: Iowa nitrate *mobilizes* with flow rather than diluting. A `C = load/Q` denominator therefore has the wrong sign, and the report separately notes that perfect *measured* specific discharge moved cross-sectional R² by +0.001 over crops+tile. Nothing here contradicts that. What is being proposed is discharge as a *daily timing covariate at a donor site*, which is a different feature from a dilution denominator — but the prior on it should be low, and E3 should be read against that prior.

**In-stream chemistry is ruled out at the target on the same grounds as turbidity.** R11(b) says turbidity is "diagnostic only — turbidity does not exist at an arbitrary pin and must never be a model feature." `spec_cond`, `ph`, `diss_oxy_*` and `temp_water` are in exactly the same position *at the target*. As **donor** covariates they are legal, but see §5.

---

## 5. Which covariates are worth pursuing

**Discharge and stage — the only two with enough reach.** 1,192 / 1,012 sites, 97% / 96% of the cohort within 25 km. These are also the two the reach graph can most plausibly link, because stream gauges sit on the network by construction. Stage is nearly redundant with discharge (they are the same rating curve either side), so carry one; discharge is the physically interpretable one and is what the C–Q literature is written in.

**Water temperature — marginal, worth one column if discharge succeeds.** 198 sites, 34% of the cohort within 10 km, median 21.7 km. Temperature is also the covariate most likely to be *predictable* from the weather grid the model already has, which makes its marginal value questionable: the model sees gridMET air temperature at every site already.

**Specific conductance, DO, pH, turbidity — do not pursue.** 52–97 sites, 12–16% of the cohort within 10 km, median separation 52–56 km. At 50 km on a network whose nitrate residual correlation crosses zero between 50 and 100 km (`future-cv-work-report.md` §3.3), a donor at median separation carries close to no information. Coverage that thin also lands in the `n_cols`-unmeasurable regime the feature-manifest work identified: a column present for one site in eight cannot move a pooled metric past its noise floor in either direction.

**A note on what specific conductance would have been good for.** It is the classic conservative-tracer complement to nitrate and would be the natural input to a mixing-model feature. The coverage simply is not there in Iowa. If that changes — or if IWQIS's own `spec_cond` (52 cohort sites, 45%, already on disk) were promoted through the build — it becomes interesting again as a *target-site* diagnostic, though never as a deployable feature.

---

## 6. Low-cost experiments, in order

### E1 — Distance-stratified `nbr_discharge` through the existing harness (~4 hours of fits) — **do this first**

Because coverage is 100% at every exclusion radius (§4), the interesting experiment is not "does a donor exist" but "how far away can it be and still help". Register exp 37/37c as exp 34/34c with one block swapped — the donor's discharge instead of its nitrate anomaly — and run it at several exclusion radii.

Arms per radius: `base`, `q_lagged` (donor discharge at lag 1/3), `q_concurrent` (lag 0, non-causal reference), `q_coverage` (donor distance + drainage-area ratio only — **the negative control**), `q_all`. Radii `r ∈ {1, 5, 10, 20, 50}` km, which per §4 give median donor separations of roughly 12, 13, 16, 24 and 52 km.

The deliverable is a skill-versus-`r` curve, and it answers the deployment question directly: the radius at which `q_lagged` stops clearing the noise floor is the maximum pin-to-gauge distance at which this feature is worth shipping. Judge on `lofo_r2` / `lofo_prauc` against the measured floors (REG 0.0085, CLF 0.0037).

**Read `q_coverage` before anything else.** If the coverage-only arm carries most of the effect, the feature is a site-type fingerprint rather than a hydrologic signal — gauge density tracks basin size, road access and funding history, all of which correlate with land use. This is exactly what `nbr_coverage` was built to detect in exps 33–36c, and its near-zero result there (+0.0008 REG) is what gave the neighbour finding its credibility.

**Do not skip `r = 0`.** Running the naive co-located version alongside is nearly free and bounds the whole exercise: the gap between `r = 0` and `r = 1` is the amount of apparent skill that comes from reading the target's own gauge, i.e. the size of the train/serve skew you would have shipped.

Reuse `_best_neighbors` from `experiments/specific-small-experiments/exps.py` — it already returns `(best_up, best_down, n_up, n_down)` and takes a `select` rule; the exclusion radius is one more filter on the candidate sets.

### E2 — Link the covariate sites to the reach graph (~1 day)

The covariate sites are not in `basin_containment_graph.parquet`, which is built only from cohort sites' basins. Two options, cheapest first:

**(a) Point-in-basin containment.** Test whether each candidate gauge's coordinate falls inside each cohort basin polygon. A gauge inside the basin is upstream; a cohort site inside a gauge's basin is downstream — the latter needs the gauge's own delineation, so start with the upstream half, which is free. This is the same relation `_make_aux` already computes and needs no NLDI calls.

**(b) NLDI navigation.** Ask NLDI for up/down-mainstem features from each cohort site and intersect with the gauge list. More faithful to the network, one API call per site per direction, and the pipeline already uses NLDI for delineation.

Validate (a) against (b) on a 20-site sample before committing to either.

### E3 — Replace euclidean separation with network distance (~2 hours of fits, after E2)

E1 selects donors by straight-line distance, which is the wrong metric on a river network: a gauge 8 km away across a divide is hydrologically unrelated, while one 40 km downstream on the same channel is not. Once E2 places the covariate sites on the graph, re-run E1's winning radius with network selection and compare.

This is the same contrast exps 33 vs 36 draw for nitrate donors, and it can reuse `_VARIANT_RULE` directly. If network selection does not beat euclidean, that is a useful negative — it would mean straight-line proximity is a sufficient proxy here and E2's delineation cost can be skipped permanently.

### E4 — Promote the on-disk covariates through the build (~half a day, no new data, independent of E1–E3)

Independent of everything above. `temp_water` (66% of cohort), turbidity (49%), `spec_cond` (45%), `ph` (40%) and DO (41%) are already in `raw/water/` and are discarded by `make_water`. Carrying them into `processed/water/data/` costs one column-selection change and makes them available as *target-site diagnostics* — for the R11(b) turbidity-MNAR label audit in particular, which needs co-located turbidity and currently cannot run. They must not become model features, for the arbitrary-pin reason; the build should carry them under a name that says so.

---

## 7. Evidence quality

**Measured directly in this repo, reproducible:**
- Per-parameter cohort coverage in §2 — read from `raw/water/{USGS,IWQIS}/*_water.parquet` over `get_site_ids()`.
- That `processed/water/data/` carries only `nitrate_con` + `site_uid`.
- `CORE_PCODES` and `USGS_SITE_LIST` in `src/build/util/usgs.py`.
- The `grid_global` bounding box, from `src/data/interim/grid_global.parquet`.
- Basin containment counts and all distances, computed against `get_location` / `get_basin`.

**Live external query, 2026-07-30, reproducible but time-varying:**
- The IV site counts in §3, from `https://waterservices.usgs.gov/nwis/site/?format=rdb&stateCd={st}&siteType=ST&hasDataTypeCd=iv&parameterCd={pcode}&siteStatus=all`, per state, clipped to the bbox. Counts will drift as sites are commissioned and retired. **Not verified:** whether each site's record is long enough or current enough to be usable — `hasDataTypeCd=iv` says a site *has* IV data, not that it spans the training window. New-site-report's usability bar (≥3.92 yr span, runs to present) has **not** been applied here, so treat every count in §3 as an upper bound.

**Inferred, not measured:**
- That the 36 within-200 m IWQIS/USGS pairs are physically co-located instruments. The distances are measured; the inference that they share a structure is not verified against station metadata.
- That donor discharge carries any signal at all. The §4 table establishes only that a donor is *available* at any separation — E1 is what establishes whether it helps, and `future-work-report.md`'s +0.001 result for perfect measured specific discharge is a reason to expect little.
- That euclidean separation is a usable stand-in for network separation. §4's distances are great-circle; a gauge 12 km away may be across a divide. E2/E3 replace it, and until then every distance here is a lower bound on hydrologic separation.

**Carried from other reports, not re-verified here:**
- C–Q exponent b = +0.61 and the +0.001 specific-discharge result (`future-work-report.md`, itself marked partly unverified).
- The 50–100 km residual-correlation crossing (`future-cv-work-report.md`).
- The measured noise floors, REG 0.0085 / CLF 0.0037 (`experiments/specific-small-experiments/exps.py`).
