# Completion plan — `fixes-newdata-audits_Aug26.md`

## Context

`notes/plans/fixes-newdata-audits_Aug26.md` specified five fixes, seven sources, three gated-source audits, and a separate three-tier thematic audit. Where that stands, stated precisely because the two audit kinds are easy to conflate:

| | status |
|---|---|
| Five fixes | **all closed** |
| Seven sources | **two in** — StreamCat annual N families, gTREND component set |
| Three gated-source audits | **all complete** — SPARROW, TerraClimate, NWM, each with a verdict and a what-it-does-not-determine section |
| Thematic audit (Tiers 0–2) | **not complete** — Tier 0 and Tier 1 done, Tier 2 partial; specifics in Part 2 |

The tree is at 256 tests, `verify all --deep` 36/36, snapshot `snap-20260827`, 124 GiB free.

What remains is four unstarted sources, an unfinished thematic audit, and one open question about NWM. Executing the original document top to bottom would now be wrong, because the work produced measurements that change what those items are worth. This plan re-sequences the remainder on that evidence and adds one new piece of work — a cheap in-house discharge model, which is what actually settles NWM.

## What the work taught us, and what it moves

**The covariate stack is spatially rich and temporally thin. That is now measured.** The Tier 2 battery decomposed the nutrient panel into between-catchment, between-year, and interaction variance. For eleven of twelve gTREND components the interaction term — the only genuinely spatiotemporal part, the part a model cannot get from a static covariate beside a year dummy — is **under 4%**: 0.6% for reduced deposition, 2.6% for fertilizer, 3.6% for surplus. Only `fixation_pasture` (23.7%) earns its annual detail. This confirms the documented method rather than contradicting it: gTREND downscales county totals through a fixed land-use mask, so the spatial pattern cannot move and only the county-year total does.

The same battery found the budget highly collinear. Regressing each component on all the others, `uptake_cropland` is reconstructible at R² = 0.988, `fixation_cropland` at 0.974, `fertilizer` at 0.959 — and only **`livestock_hogs` (0.534)** and **`fixation_pasture` (0.629)** carry their own signal.

**This re-prioritizes the remaining sources.** CDL is annual, StreamCat annual-or-static, gTREND effectively static. The only sub-annual covariate the project holds is gridMET weather. So the two remaining sources that add *temporal* information — pentad drought indices and SNODAS snowmelt — are worth more than the static overlays, and this plan puts them first. That reverses the source document's ordering, on measurement.

**Other findings that carry into the work:**

- **An operation whose cost scales with the artifact is a latent failure the moment the artifact grows.** Nutrients grew 5.4× and broke three things nothing had touched: a raster pass (nine days projected, fixed to 237 min by cache-sized raster grouping), a closure tolerance (units — kg/ha precision arriving multiplied by the 6.25 ha pixel), and publication (~10 GB resident, fixed by streaming row-group copy). Every source below is a growth event, and each gets sized before it runs.
- **All eleven skip-logic instances are closed.** Guards now read the stamp their own writer emits, and checkpoint directories carry both declared value axes and an input stamp.
- **Fix 1 did not land as written, and should not.** The day-boundary measurement showed relabelling makes the data worse (published label r = 0.798, one-day shift 0.385, the other way 0.137), so `day_convention` was deliberately not added and `get_native` is the escape hatch. Recorded so nobody "finishes" it.
- **`verify` passes vacuously when a store is detached** — all three snapshot checks return PASS against a nonexistent root.
- **`du` from this environment under-reports**, silently skipping TCC-protected paths. Sizes I quote are floors; whole-volume accounting comes from `df`/`diskutil` or `sudo`.

## Step 0 — land this plan in the repo

Write it as `notes/plans/completion-plan_Aug27.md`, beside the document it completes. The Aug26 document is not edited — it stays the record of what was specified — and gets one appended status line pointing here.

## Part 1 — `projects/discharge-test/`

**Why it exists.** NWM's value proposition is modelled discharge where no gauge sits. We have observed discharge at **5,774 of 7,539 published records** and lack it at **158 of 364 nitrate records** — and CHANOBS covers almost exactly the sites that already have it (147 of its 148 nitrate overlaps). So "is NWM useful" reduces to "how much of observed discharge's value can be recovered at an ungaged site", which is answerable from data already on disk, for free, before downloading anything.

Running NWM ourselves is not a route: routing is not decomposable, and our largest cohort reach drains **3,130,494 km²** — about 39% of CONUS — so a domain covering the cohort is a CONUS run without the calibration that gives NWM its skill.

**Layout, mirroring `xgboost/`**, whose central rule is copied deliberately: *this project never edits `data/access`; everything it reads goes through `cohort.py`, so "does this need an access-layer change?" is answerable by reading one file.*

```
projects/discharge-test/
  README.md      what it is, how to run, what the numbers mean
  config.py      cohort definition, target transform, paths
  cohort.py      THE single boundary to data.access
  features.py    the feature blocks
  cv.py          GroupKFold on basin families + the hydrologic metric set
  run.py         entry point
  results/       scores, written per run
```

`cv.py` is deliberately not an import of `xgboost/cook.py`: that module is bound to its own cohort and recipe conventions, and `xgboost/` must run from inside its own directory because the package name collides with the library. Duplicating ~80 lines of GroupKFold is cheaper than coupling two projects, and it matches the stance `xgboost/README.md` already takes.

**Cohort.** Train on all discharge-bearing records in the AOE; hold out and report on the cornbelt surface-watercourse subset. Regionalization needs hydrologic range to learn an area/climate/soil response rather than memorize one region, but the number that matters is skill on the sites we need predictions for.

**Target: specific discharge, log-transformed.** Raw m³/s across basins spanning four orders of magnitude in area is mostly a prediction of area. Model `log(Q / A + ε)` in mm/day, convert back for scoring so metrics are comparable to NWM's.

**Features, all on disk:** basin-mean gridMET through `api.get_weather` (`pr`, `pet`, `tmmx`, `tmmn`, `srad`, plus the fire-weather block) with lags and rolling sums at 1/3/7/14/30/60/90 days, since a hydrograph is a convolution of rainfall rather than a function of today's; antecedent moisture as cumulative `pr − pet` over the same windows; static basin descriptors from VAA (`totdasqkm`, catchment area, slope, elevation, stream order); and StreamCat via `api.get_attributes` — soils, imperviousness, land cover, and `pctagdrainagews`, which matters because tile drainage is exactly where NWM is documented as weak.

**Evaluation matched to the NWM audit's metric set** so the comparison is apples-to-apples: Nash–Sutcliffe, Kling–Gupta, log-space NSE, bias, correlation. Note `sklearn`'s `r2_score` against the observed mean **is** NSE, so only KGE and log-NSE are additions. NWM's numbers to beat, measured on 19 of our own reaches: **NSE 0.35, KGE 0.646, log-NSE 0.521, bias 1.023, r 0.701**.

**The holdout must be spatial** — GroupKFold on basin families, the union-find over basin containment that `xgboost/cook.py:100` implements, so a test site's basin never contains a training site's outlet. Predicting held-out *sites* is the task; held-out *days* at a known site is a different and much easier problem that would flatter the result.

**Deliverables.** A score table in `results/`; predicted discharge for the 158 nitrate sites that lack it; and `notes/reports/discharge_model_report.md` with the bottom line. **The modelled series stays inside the project** — the inventory chapter already quarantines NWM as "a simulation wearing the shape of an observation", and our own modelled discharge earns the same treatment rather than entering `published/`.

**The real risk is feature assembly, not fitting.** 5,774 sites × ~5,000 days is ~29M rows; at ~40 float32 features that is ~4.6 GB resident, which this machine will not carry beside anything else. Two required mitigations: cache per-site feature frames to parquet so assembly is paid once, and sample days (every third) for the pooled fit, since hydrologic response is autocorrelated and consecutive days are near-duplicates. **Smoke on 50 sites and measure per-site assembly cost before the full pass.** The 7,123 authority basins are already on disk so no delineation is needed, but 5,774 `get_weather` reductions is the step that could turn hours into days.

## Part 2 — finish the thematic audit

Checked against the source document's battery and acceptance criteria rather than impression:

| battery item | state |
|---|---|
| **A. Coverage** — cohort COMIDs non-null, record-days covered, span against the water record | **not implemented** |
| **B. Within-basin variance ratio** | **a geometric proxy, not the specified test** — `geometry.resolution_table()` compares cell area to catchment area analytically, never reads data, and covers gridded sources only |
| **C. Agreement and incremental information** | implemented, **deliberately differently** — R² of each product against *all others* rather than pairwise-both-directions. Defensible (two products can each correlate weakly with a third and still be jointly redundant with it), but the report must say why it deviates |
| **D. Lineage / do-not-double-count** | done |
| **E. Temporal behaviour** | done |
| **F. Missingness structure** | **partial** — StreamCat only, probed by catchment area rather than position, and the case the document names (Wieczorek NODATA clustering) is uncovered |

**Two deliverables outright unmet.** `data/insights/images/` is **empty**, and acceptance criterion 3 requires `report.md` *and* `images/` to regenerate from scratch. And redundancy matrices are specified **per theme**, where only nutrients carries one.

**The highest-value item has never been run.** `products.huc8_surplus_reference()` is implemented and its docstring states the case: it is **the only external ground truth in the covariate layer.** Everything else in d5 is checkable for internal consistency — class fractions summing to coverage, kg equalling mean × area — but nothing outside the pipeline says whether our zonal reduction of a raster is *right*. The gTREND authors published their own HUC8 aggregation of the same surplus raster across 2,139 basins, so aggregating our catchment values to HUC8 and comparing tests our extraction against an independent one. Cheap, on disk, and disagreement would mean our reduction is wrong. Run it and report it.

**The DMR–SPARROW validation is reopened** now that disk allows. It is the more interesting of the two validation opportunities because it is *measured against modelled on the same objects* — DMR is reported effluent under permit, SPARROW models the same quantity — so disagreement informs in both directions.

It is legitimate despite SPARROW's conditioning verdict, for a specific reason: conditioning substitutes monitored flux into **routed totals**, while source shares are computed as *predicted share × monitored flux*. **The shares are predictions and survive conditioning intact** — and `sh_TNwwtp_kg` is precisely the column this needs, the one part of SPARROW the audit cleared for use.

**The data is already inbound** — Isaac is copying the whole `conusModelsDR` and `tn` directories into **`data/stores/work/sparrow/`**, the staging root, deliberately outside `ACQUIRED` so `snapshot` never manifests it and outside `_pending/` so `weather.compact` never globs it. That removes the guess-which-member step: the sequence is now to read the directory listing and headers directly, find the file carrying `sh_TNwwtp_kg` beside `genid`, **stream out only those columns, and delete the staging directory immediately**. The 17 GB conditioning evidence was extracted that way at no disk cost, and nothing here should be handled otherwise.

Watch disk while it lands: `conusModelsDR` alone was 8.4 GB compressed and expands past 25 GB (`predict_tn_boot.csv` is 17 GB on its own). At 116 GiB free that is comfortable, but the staging directory is ephemeral by design and comes off as soon as the columns are in hand.

Two caveats to carry in rather than discover: SPARROW ends **2020** against DMR's fiscal-year archives, so only overlapping years compare; and aggregation goes to `genid` grain, where our **17.9% collision rate** matters far less than per-site, because this compares aggregate loads rather than near-neighbour contrasts.

If the shares are pulled anyway, acquiring them as a covariate becomes a cheap follow-on decision — the audit already permits it, restricted to basin-scale use and carried with the collision flag. That stays Isaac's call, not an automatic consequence.

## Part 3 — the two temporal acquisitions (authorized, run alongside Part 1)

Network-bound while Part 1 is compute-bound, so they overlap cleanly.

**Source 4 — gridMET pentad drought indices.** 26 aggregates (`spi`, `spei`, `eddi` at eight windows each, plus `pdsi` and `z`). Not an acquisition change but a request change on the existing path in `data/build/acquire/weather.py`: the pentad products ride **the same 585 × 1386 lattice**, so `grid.parquet` and `weights_gridmet.parquet` work unchanged. Land in `acquired/weather/pentad/`, sibling to `daily/`. **Fetch by year, not month** — the cost is latency rather than bytes, so year windows turn ~22 h into ~1–2 h. Values are `UInt16` with scale/offset. The access reader must **state the cadence in its return** so no consumer mistakes a pentad value for a daily one; forward-filling to daily stays a modelling choice the build does not make.

**Source 3 — SNODAS.** A new `data/build/acquire/snow.py` rather than more weight in `weather.py`. Keep SWE (`11034`), melt (`11044`), and the rain/snow split (`01025 SlL00`/`SlL01`). **Resample onto the gridMET lattice at ingest** — 21.6 GB stored against 270 GB native, landing on the grid the weights already key to. Validate the resampled cell set against `weather/grid.parquet` and refuse on mismatch. Melt's nodata is −9999 and fills with **0**, not null: no snowpack is zero melt, not missing information. `water_input_mm = rain + melt` is a read-time derivation, not a stored column.

Each ends with `verify all --deep` and a snapshot cut.

## Part 4 — WQX

Unblocked: the `records` channel-blindness that was its only genuine blocker is fixed.

**WQX stations must never influence the AOE.** They register, they publish records, and they are never `aoe_seed`. The build protects this twice already — `extent.main` never re-expands on its own, and `fetch_seed_basins` delineates only `sites[sites.aoe_seed]` — but both are *defaults*. The adapter's acceptance criteria include an **assertion** that no WQX registration is ever flagged as a seed, so the guarantee survives someone later "fixing" the default.

Sequenced last among the sources because it is the only item touching identity and census, and so the only one whose blast radius includes the review queues. Size the review burden before the run, not during.

## Part 5 — the static overlays, and NWM's exit

**Source 6 — karst (Weary & Doctor) and Sekellick confined/unconfined manure.** Both land in `derive/zonal.py`'s existing catchment-overlay path. Karst has no substitute on disk — verified zero karst attributes across all Wieczorek and StreamCat families — and `pctcarbresid` does not stand in, because karst units are classed by burial depth and our AOE straddles the glacial margin where that distinction lives. Deprioritized behind Parts 1–3 on the temporal-thinness evidence, not on doubt about the source.

**Source 2 — NWM leaves this plan either way; Part 1 decides which exit.** If the in-house discharge model reaches NWM's gaged-reach skill, NWM adds nothing and the source is dropped, with the audit standing as the record of why. If NWM clears our model by a clear margin, it is worth doing properly — its own project, not a source bolted onto the end of this one, because an acquisition carrying a version splice, an assimilation trap, and an ungaged-skill question no available data settles is not a covariate column. **Nothing is downloaded under this plan either way.**

Operational notes recorded so that project starts from them: narrow `parameters/sensor_comids.txt` first (6,945 COMIDs where the nitrate-bearing set is 342) via `assemble._write_sensor_comids`; take `streamflow` plus `q_lateral`/`qSfcLatRunoff` and **skip `ldasout` entirely**; anything past 2023-02 must use `analysis_assim_no_da`, since the default assimilates gauge observations and 164,651 reaches (5.9% of CONUS) differ downstream through routing.

## Part 6 — deferred: the external-disk refactor

Two things from this work are its input when it happens.

**Split by role, not by layer.** `raw/` is 37 GB of which only 4 MB is read-path (the HydroSHEDS archive `d8` uses); `acquired/` is 45 GB of which **43.3 GB is read-path** — `get_weather`, `get_attributes`, `get_native`, `get_network`, `snap`, `accumulate`. Raw moves free; acquired does not move without either severing half the access layer or projecting cohort-scoped subsets into `published/`, which is real work with a real cost.

**The mount assertion is a prerequisite, not a nicety.** With roots detached, all three snapshot checks pass vacuously and `verify` reports a green store with the drive unplugged. Fix that *before* any store moves.

Unrelated to the move but outstanding: the HyRiver response cache has **no expiry column at all**, which is how it reached 49.5 GB. It regrows on the next bulk pull unless a cap or expiry is set.

## Verification

- `pytest tests/data -q` green throughout; currently 256.
- `python -m data.build.run verify all --deep` green after every acquisition batch; currently 36/36. Expect `schema_changed` drift rows on the cut after the pentad store lands — that is the drift machinery working.
- Every new acquisition reachable through `data.access` **before its batch is called done**, and reported reachable by `python -m data.insights`. Acquisition without exposure is the failure Tier 0 exists to catch, and this work already had to close one such gap (`acquired/attributes`, 12.4 GB, reachable by nothing).
- `python -m data.insights` regenerates **`report.md` and `images/`** from scratch — an empty `images/` is a FAIL of acceptance criterion 3, not a cosmetic gap — with a redundancy matrix per theme, and battery items A, B and F either implemented or explicitly recorded as not-measured with the reason.
- The HUC8 surplus cross-check is run and reported. A number exists, or Part 2 is not done.
- Both validation opportunities produce a number or a stated reason they cannot: NWM against our `discharge` (done, in the NWM audit), and DMR against SPARROW's point-source share. The SPARROW file is deleted once its columns are extracted.
- `projects/discharge-test/run.py` runs end to end on a 50-site smoke before the full pass, reporting the same five metrics the NWM audit used.
- `notes/reports/pipeline-findings.md` current — every incidental finding logged with a status.

## Risks

- **Feature assembly, not fitting, is the discharge model's cost.** Smoke and measure before the full pass; cache per-site frames.
- **Memory is the recurring failure mode on this machine**, and twice it presented as something other than itself — a silent OOM at exit 0, and a nine-day raster pass that was really a block-cache thrash. Size every step whose cost scales with an artifact.
- **The discharge experiment can produce a clean *no* but never a clean *yes*.** Low recovery at gauges is decisive and NWM is dropped. High recovery is only suggestive, since gaged skill is a ceiling on ungaged skill and no obtainable data closes the gap — which is exactly why a positive result opens a separate project rather than an acquisition here.
- **Reopening SPARROW re-introduces a file this machine cannot comfortably hold.** Identify the smallest member first, extract by streaming columns, delete immediately.
- **WQX is the only item that consumes Isaac's time rather than mine**, and its review burden is still unsized.

---

## STATUS 2026-08-31 — complete except two deferred items

| part | outcome |
|---|---|
| **Step 0** | plan landed; Aug26 document marked superseded |
| **Part 1 — discharge-test** | **NWM: do not acquire.** Median per-site NSE **0.509** against NWM's 0.35, on held-out basins — the harder test, since NWM's figure comes from gauges it was calibrated against. 110 ungauged nitrate sites given a validated imputed series; 48 refused on physical grounds. `notes/reports/discharge_model_report.md` |
| **Part 2 — thematic audit** | complete: all six battery items, `images/` populated, six theme matrices, HUC8 cross-check at Pearson **0.99999** |
| **Part 3a — pentad drought** | 25 aggregates × 2008–2026, 4.9 GB, `api.get_drought` |
| **Part 3b — SNODAS** | **DEFERRED (Isaac).** Sized: ~11.7 h and ~167 GB, eight times the pentad store |
| **Part 4 — WQX** | 2,112,841 results, 41 states, **collect-only** — never a node, enforced by check |
| **Part 5a — Sekellick** | confined/unconfined manure, 1.49M reaches |
| **Part 5b — karst** | 1.48M catchments, 28.6% carrying karst; measured **not** redundant with Hydrologic (R² 0.001–0.100) |
| **Part 5c — NWM exit** | dropped on Part 1's evidence |
| **Part 6 — external disk** | **DEFERRED**, with its input measured and a prerequisite named |

**Final state:** 314 tests, `verify all --deep` **48 of 48**, snapshot `snap-20260830`.

**What the plan did not anticipate, and what it cost.** Four sizing assumptions in the source document were wrong and each was caught by measuring before building rather than after: WQX's `Total-Result-Count` header does not exist; `spi270d` is not published; `zip=yes` is ignored; and SNODAS's fetch time was never estimated at all. Two further defects were found in code written *during* this plan — `drought.fetch_year` and `wqx.fetch_state` both froze their newest partition — which is why the freeze rule is now three standing checks rather than a paragraph.

**Open for Isaac:**

- **SNODAS**, when wanted. An Oct–May window would drop ~40% of days at essentially no cost here (July has 687 nonzero SWE cells of 12.0M, all western snowpack outside the AOE).
- **Two orphaned stores** to retire or keep: `acquired/authority_basins` (72 MB) and `raw/comid_attrs` (53 MB), both referenced only in the retired `src/`.
- **The `nitrate_discrete` channel question.** `Activity_TypeCode` is carried rather than flattened because the mix is state-dependent — Iowa is 42% field measurements against a 7.1% national average. Whether that becomes a channel distinction is unresolved.
- **NWM as its own project**, only if something downstream needs flow *variability* rather than level and timing — the one axis where NWM won (KGE 0.646 against 0.529).
