# Project notes — findings that may inform the data access layer

Written for Isaac's later review. Nothing here has been acted on in `data/access`; each item states what the project hit, what it did instead, and how strong the case for an access-layer change actually is. Ordered by strength of case.

## 1. `get_covariates` collapses; bucketed features need the per-catchment rows — STRONG

`api.get_covariates(comids, ...)` reduces over the whole COMID set and returns one collapsed answer per product. Every spatially-resolved feature — the old stack's distance rings, this project's flow-distance buckets, any distance-decay weighting — needs the **per-catchment rows** before reduction, because the grouping happens on a geometry the access layer cannot know about.

The project's workaround reaches into `machinery.aggregate._product(name)`, a **private** function, and filters it by COMID. That works but is exactly the kind of private-API dependence that breaks silently on refactor.

Suggested: a public accessor that returns product rows for a COMID set *without* reducing — e.g. `get_covariates(..., reduce=False)` or `machinery.aggregate.rows(product, comids)`. The reduction machinery already separates the two steps internally; this only exposes the seam.

## 2. Weather reduces per call, so per-bucket weather costs N calls — MEDIUM

`api.get_weather(comids, window)` collapses whatever set it is handed to one row per day (correctly — the collapse-first design is what makes it fast). But a feature bucketed by flow distance needs one series per bucket, so the project calls it once per bucket, re-reading the same weather cells each time.

Not wrong, just wasteful: three buckets means three passes over overlapping cell sets. An optional grouping argument (`get_weather(comids, window, by=mapping)` returning one row per (day, group)) would let one read serve every bucket, and the weighted-mean arithmetic is the same either way.

## 3. StreamCat is acquired but never published — MEDIUM

`acquired/attributes/streamcat.parquet` is 1.6 GB of per-COMID catchment attributes (soils, %ag drainage, N surplus, lithology, imperviousness — hundreds of metrics, already scoped to exclude target-fitted ones), fetched by the a3 attributes branch. **No access-layer function exposes it.** The old models used comparable attributes via build3's `comid_attrs`; this project's static block is thinner as a result (no soil, no lithology, no upstream-accumulated land-use metrics).

Suggested: `get_attributes(comids, metrics=None)`, the obvious sibling of `get_covariates`. The data is on disk, per-COMID, and needs no derivation.

## 4. Flow distance is generally useful and currently everyone's to rebuild — MEDIUM

The single most valuable geometric quantity for basin features turned out to be **flow distance from an upstream reach to the basin outlet**, computed as `vaa.pathlength(reach) − vaa.pathlength(outlet)` (kilometres, along the channel). It replaces euclidean distance-to-sensor as a bucketing axis, and it is strictly better: it is the distance water travels, so it also gives travel-time lags directly (VAA's `pathtimema` differenced the same way — measured 0/1/2 days across this project's three rings on Old Mans Creek).

Any modeling project doing spatially-resolved basin features will re-derive this. `machinery.accumulate` already loads the VAA and walks the topology; returning `flow_dist_m` (and `path_time_days`) alongside the COMID set would make it free and consistent across projects. **Verification datum:** the project's `accumulate ∘ snap` basin for `USGS:05455100` reproduces 523.5 km², matching the basin-editor's verified value to the decimal.

## 5. gridMET's fire-weather indices are not acquired — WEAK-MEDIUM, but worth knowing

The predecessor models' feature curation kept **exactly one** weather prefix on both tasks: `fuel_moisture_1000h`. Every other weather variable was permutation-dead. The new pipeline acquires eleven gridMET variables (pr, tmmn, tmmx, rmin, rmax, sph, vs, srad, vpd, pet, etr) — none of them the fire indices (`fm1000`, `bi`, `erc`), which gridMET publishes from the same THREDDS endpoint with the same access code.

This project substitutes `dryness_40d`, a trailing 40-day (reference ET − precipitation) integral — the water-balance deficit a 1000-hour fuel moisture is a proxy for — and names it honestly rather than pretending equivalence. If it underperforms, acquiring `fm1000` is a one-line addition to the weather variable tuple, and the case for it would then be empirical rather than inherited.

## 6. The directory name `xgboost` shadows the library — WORKED AROUND, no change needed

`project-root/xgboost/` collides with the installed `xgboost` package. If the repo root leads `sys.path`, `import xgboost` finds this project instead of the library. Every module here therefore **appends** the repo root rather than prepending it, and the modules are flat (no `__init__.py`, no relative imports), so scripts run with `xgboost/` as `sys.path[0]` get project modules by name and the real library by its own name. Verified: `xgb.__version__` resolves to 3.3.0 from site-packages with `data.access` still importable.

Worth knowing if another project is ever named after its main dependency.

## 6b. The gridMET day boundary is offset from the observation day — STRONG, and it affects features here

Found during the 2026-08-26 data-source review and verified independently (see `notes/new-data-report.md` §6): gridMET's daily `pr` accumulates **12:00 UTC → 12:00 UTC labelled by the start day**, i.e. 06:00 CST day D → 06:00 CST day D+1, while the pipeline bins observations to Central Standard calendar days (`data/build/io.py`, `STANDARD_TZ`). The two are six hours apart, and in the corn belt's nocturnal-storm regime **27–36% of precipitation falls in the displaced window** — on storm days it moves whole events (92 mm assigned to the wrong calendar day in one measured case).

The build is not wrong: it stores gridMET's days as gridMET publishes them. The error appears at the JOIN, which is exactly what this project does in `transforms.merge_on_date` — weather day D is paired with nitrate day D on the assumption they cover the same 24 hours.

This project's features are affected today. `rolling_precip` and `dryness_index` integrate over windows long enough (14–60 days) that a one-day misalignment is second-order, but any same-day or short-lag precipitation feature is materially mis-dated. Until it is resolved, prefer the integrated forms and treat a `precip_1d`-style feature as suspect.

The clean fixes, in order of preference: (a) `data.access.get_weather` documents the convention and offers the shifted labelling, since the convention is a property of the source and belongs beside the reader; (b) this project shifts at feature-build time; (c) precipitation is re-derived from AORC hourly on our own boundary, which also removes the inherited convention entirely. **Verified for `pr` only** — if the temperature and radiation variables follow a different convention, a single daily weather row currently mixes day definitions, which is worth testing before either fix.

## 7. Published-store dependency for cohort resolution — OBSERVATION

`cohort.sites()` reads `api.get_sensors()`, which requires the **published** store. During development (before p1 ran) nothing in this project could resolve a cohort, even though basins, network walks and covariates were all readable. That is the correct layering — the access layer is a read surface over published artifacts, and a project should not reach behind it into `derived/` — but it does mean modeling work is gated on publication rather than on derivation. Noting it as a fact of the workflow, not a defect.
