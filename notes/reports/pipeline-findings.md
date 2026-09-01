# Pipeline findings — a standing log

Things Isaac needs to see or decide that are not themselves planned deliverables. **Appended to across the project**, never rewritten. Each entry: what was found, how it was found, what it costs to leave alone, and its status.

Status vocabulary: **fixed** (done, with where) · **open** (needs Isaac's call) · **planned** (has a home in a plan) · **noted** (true, cheap, no action proposed).

---

## 2026-08-26 — July 2026 was deleted from the weather store · **fixed**

`gridmet_2026Q3.parquet` held 5,545,920 rows spanning 2026-08-01 to 2026-08-24 and nothing else. The snapshot manifests date the loss exactly: `snap-20260825-adopt` recorded that file at 152,176,153 B (July + August), `snap-20260825` at 66,465,518 B. Thirty-one days of gridMET across the whole AOE.

**Mechanism.** `compact()` rebuilt the quarter from its pending months alone. On 2026-08-25 July was complete (31/31 days) so `fetch_month` skipped it and wrote no pending file; August was partial (24/31) so it refetched; the rewrite replaced a two-month file with a one-month file. It was not a one-off — it oscillated on a two-run period and would have repeated every quarter.

**Why nothing caught it.** `snapshot._parquet_probe` matched only `timestamp`-typed columns while the weather `date` column is `date32[day]`, so every weather quarter reported `data_max_ts = NaN`. With no temporal evidence, `_classify_change` fell through to a size comparison and called a 56% row loss `revised_out_of_window` — the same class as a routine trailing refresh. It is row 4 of `drift.csv`, accepted by hand.

**Fixed** in `acquire/weather.py` and `acquire/snapshot.py`: `compact` now carries non-pending months across from the existing file; `write_quarter` gained an `allow_shrink=False` tripwire that refuses any rewrite dropping a day already on disk; `_parquet_probe` now reads `date32`/`date64` statistics, so weather quarters carry a real `data_max_ts`; and three `a3` checks were registered where `weather.py` previously had none — including one that detects precisely this state (`2026Q3 starts 2026-08-01, expected 2026-07-01`). July was refetched the same day.

**Repaired the same day.** July was refetched (7,163,480 rows) and folded back into 2026Q3, which now spans 2026-07-01 to 2026-08-25 at 12,940,480 rows and 152,411,856 B — marginally larger than the 152,176,153 B it held before the loss, because August has since gained a day. Both structural checks pass. Recorded in `snap-20260826a`.

**The lesson worth keeping:** a review gate is only as good as the evidence it is shown. This one was shown a size change with no dates attached, and a human reasonably accepted it. `drift.csv` row 4 still carries that acceptance; it is left as the historical record rather than rewritten.

---

## 2026-08-26 — coverage checks are key-aware but column-blind · **fixed (weather), planned (StreamCat)**

`weather._complete()` decided a month was done by counting **dates only**, and `attributes._covered()` counts rows against COMIDs. Neither noticed a requested *column* was absent — so adding `fm1000`/`fm100`/`bi`/`erc`/`th` to `VARIABLES` had no effect whatever on a normal run: every month reported complete and nothing was fetched. The same blindness means adding a StreamCat metric family would never trigger a refetch.

The codebase already held the right pattern: `attributes._dir_covered()` checks `n_attrs` for exact equality, so the Wieczorek pull genuinely knows when its declaration widens.

**Principle:** a coverage check must compare the declaration against disk on **every axis the declaration names** — rows, keys *and* columns. Applied to `weather._complete` via a footer null-count scan (0.09 s for the whole 75-quarter store). StreamCat's half is Movement 1g of the execution plan.

---

## 2026-08-26 — `attributes.py` documents a capability no code implements · **planned**

The module docstring states "Legacy-nitrogen accumulation needs the series and can request a family explicitly." No code path implements it: `streamcat()` takes no metric/name/family argument, and `latest_vintage` is applied unconditionally at its single call site. A docstring describing a state that does not exist, which the project's own docstring rule forbids. Closed by Movement 1g.

---

## 2026-08-26 — `published/quality.parquet` is joined by a string literal · **open (trivial)**

`publish.py` writes it as `config.PUBLISHED / "quality.parquet"` — a hardcoded path — while `api.get_quality()` reads `config.PUB_QUALITY`, which is declared in `data/access/config.py` but **not** in `data/build/config.py`. The two halves agree by coincidence of a literal; renaming either breaks the other silently. One constant fixes it.

---

## 2026-08-26 — `published/network/` is a vestigial empty directory · **open (trivial)**

Created by `ensure_dirs()` from a `PUB_NETWORK` constant that nothing writes. `api.get_network` correctly reads the *acquired* network in place, exactly as the chapter says it should. The empty directory misleads anyone browsing the published store into thinking the network is published there.

---

## 2026-08-26 — `data/stores/superseded/` is a store root named in no config · **open**

Holds the 3.1 GB pre-repair IIHR delivery (`iwqis_2026-06`). It is genuine provenance — the only copy of what IIHR shipped before the WQP0016 repair — but it is invisible to `ensure_dirs`, to both configs, and to anyone reading the code. Decide: keep and declare it, or retire it.

---

## 2026-08-26 — 36,826 grid cells never carry data · **noted**

`grid.parquet` holds 267,906 cells whose *footprint* intersects the AOE; only 231,080 ever appear in a quarter, because gridMET returns all-NaN over ocean and beyond CONUS and `_to_frame` drops rows null across every variable. Not a defect — but it means `grid.parquet` overstates covered cells by 14%, and any check written against its count fails on a healthy store. (One was; it is fixed.)

---

## 2026-08-26 — `th` (wind direction) is circular and averaged arithmetically · **open**

`th` was added to `VARIABLES` as part of restoring the fire-weather block. `api.get_weather` reduces cells to a catchment with an area-weighted **arithmetic** mean, under which 0° and 359° average to 180° — the opposite direction. Not yet biting (the column is not fetched), but it will ship silently the moment the backfill lands. The fix is a vector mean (average the unit vectors, take the atan2), which is a different reduction from every other variable and therefore a deliberate choice: decide whether `th` is worth that special case at all, since the predecessor's models never used wind direction.

---

## 2026-08-26 — `nwm.py` cites a chunk footprint that is not our cohort · **noted**

Its docstring records "measured 22 of 93 on the calibration cohort". Measured against the current nitrate cohort: **58 of 93** feature blocks, because our 342 nitrate COMIDs are spread across the whole AOE rather than one region. A stale number presented as current. The real 2008+ zarr cost is ~76 GB, against ~26 GB for CHANOBS.

Related, and not a defect: 4 of our 342 nitrate COMIDs are absent from NWM's `feature_id` list; the fetch already prints them rather than dropping them silently.

---

## 2026-08-26 — no verify check had ever been observed failing · **fixed (harness pending)**

`tests/storecheck/` and `tests/fixtures/` are empty directories, despite `verify.py`'s docstring promising the two harnesses "meet in `tests/storecheck/`". Nothing in `tests/` imported `data.build.verify` or called any `_check_*` function — so all 32 checks were unproven, and two of the three I wrote today were wrong on first run (they asserted against `grid.parquet`'s cell count, which fails on a healthy store). Both were caught only because I ran them against the real store by hand.

That is the argument for the harness: a check nobody has watched fail is not known to work. Movement 1a of the execution plan.

---

## 2026-08-26 — `python -m data.build.verify` reported "0 checks, 0 failed" · **fixed**

Running the module directly imported it as `__main__`, giving it a `_CHECKS` list separate from the one every stage module registers into via `data.build.verify`. The runner read its own empty registry and exited 0. `python -m data.build.run verify all` was unaffected, which is why it went unnoticed.

Fixed by resolving the canonical module explicitly in `main()`. The related hazard was also closed: `_load_all()` swallowed `ImportError` silently, so a stage module that failed to import would contribute no checks and the table would still look clean — it now prints a loud stderr warning naming the module.

**The shared failure mode is worth naming:** an empty check table is visually indistinguishable from a passing one, and exits 0.

---

## 2026-08-26 — the gridMET day-boundary fix, as planned, would have made the data WORSE · **plan corrected**

`notes/new-data-report.md` called the day boundary "the single highest-value item in the whole review" and proposed shifting gridMET's day label at read time. **Measured, that shift is harmful and must not be implemented.**

**What was measured.** gridMET `pr` against AORC hourly precipitation aggregated at all 24 possible day-start offsets, three corn-belt sites, 2020. Precipitation's window peaks sharply at **13–15 h UTC** (r = 0.969 at peak, against 0.474 at a UTC calendar day) — so gridMET's day runs about 14:00→14:00 UTC, not the 12:00 its metadata implies and not the +13 h the report reported. Our observation day is Central Standard, 06:00→06:00 UTC. **The two windows are eight hours apart**, so gridMET day D overlaps our day D by sixteen hours and our day D+1 by eight.

**Why the shift is wrong.** Sixteen of twenty-four is already the best integer-day assignment available. Against AORC aggregated on our own boundary: label as published **r = 0.798**, shifted +1 day **0.385**, shifted −1 day **0.137**. Relabelling moves all of the water rather than the misaligned third.

**What is true and unrepairable by relabelling:** the residual disagreement is large — 69% mean absolute daily error as a share of total rainfall at Ames in 2020. The only real repair is re-deriving precipitation from AORC hourly on our own boundary (AORC is anonymous zarr on S3 and now reachable; ~23 GB for the AOE, 2008–2025).

**A second correction: only precipitation's convention is measurable this way.** `tmmx` is nearly insensitive to windowing — its correlation plateau spans offsets 4–17, all within 0.005 — and `srad` more so, with offsets 0–15 all at r = 1.000, because radiation is zero at night so any night boundary captures the same daylight. `tmmn`'s plateau is 10–14. None of them contradicts 14 h; none confirms it. The report's worry that variables might carry *different* conventions is therefore neither confirmed nor refuted, and this method cannot settle it.

**Done instead:** `acquire.weather.GRIDMET_DAY_OFFSET_H = 14` records the measurement and its provenance, and `api.get_weather`'s docstring states the offset, the cost, and why it does not shift. **Isaac's call:** whether the AORC re-derivation is worth ~23 GB and an adapter, which is the only thing that would actually fix this.

---

## 2026-08-26 — DMR is dirtier than any source yet ingested · **fixed**

Building the effluent-loading product surfaced three independent pathologies in self-reported DMR values, all in FY2023 effluent rows alone:

- a **−9999 sentinel** in concentrations (min value observed),
- flows to **80,385,869 MGD** — two hundred times the Mississippi, with 3,261 rows above 5,000 MGD, which already exceeds the largest plant in the country,
- concentrations to **860,000,000 mg/L** — 860 times the density of water.

Unbounded, their product yields a single outfall claiming **8.2 × 10¹² kg of nitrogen in one month**, more than the world produces. Bounds are set where measurement says nothing real lives: for directly reported quantities, p99 is 1,200 kg/day, p99.9 is 9,157, p99.99 is 37,333, and then exactly **four rows of 221,534** sit above a million.

A residual heavy right tail remains inside the bounds (max 951,198 kg/day). It is deliberately not trimmed — the registry's principle is instrument-impossible, not site-implausible, and a distribution trim is the modeller's filter.

**Also worth knowing:** about half the loading rows carry no `comid`, for three legitimate reasons — 11,660 permits lie outside the AOE (the DMR archives are national, the outfalls layer is clipped), 4,549 (permit, outfall) pairs never snapped within 500 m, and 4,937 outfall numbers DMR reports against (`001A`, `000`, …) are absent from ICIS's outfalls layer entirely. Attributing those loads to another outfall on the same permit would place a discharge where it may not come from, so the build declines to.

---

## 2026-08-26 — every gridded product coarser than ~1 km is a catchment-level constant here · **noted, reusable**

Measured over all **1,478,688 catchments** (median area **1.38 km²**): the share smaller than a single source cell is **40.7%** for AORC (~1 km²), **98.4%** for TerraClimate (~16 km²), **98.8%** for gridMET (~19 km²) and **100.0%** for NLDAS-2 (~146 km²).

So for everything except AORC, within-catchment spatial variance is essentially zero by construction — a catchment smaller than one cell straddles at most two to four and usually sits inside one. **Do not pay for spatial resolution in gridded covariates at this grain.** It disposes of OpenET's headline 30 m advantage entirely, and it means resolution comparisons between TerraClimate and NLDAS-2 compare a quantity neither delivers.

This is the thematic audit's within-basin variance ratio, computed analytically instead of empirically, and it generalises to every gridded product the project will ever consider — which is why it belongs in the standing battery rather than being rediscovered per source.

---

## 2026-08-26 — the NLDAS-2 gate has no cheap instrument · **open**

TerraClimate was to be the free proxy answering "does soil-moisture-family data earn anything", gating NLDAS-2 and OpenET. **It cannot.** The question is *antecedent* — how wet the profile was in the **days** before a storm — and TerraClimate is **monthly**, so a null from it is not evidence against hourly data and a positive is not evidence for it.

**Isaac's call:** acquire NLDAS-2 on the mechanistic argument that antecedent wetness is central, or leave the family alone. There is no cheap way to find out first. (TerraClimate remains a fair candidate on its own terms, for seasonal context at zero authentication cost back to 1950.)

---

## 2026-08-26 — twelve StreamCat metrics were requested for years and never landed · **fixed**

`latest_vintage()` has always asked for 159 metrics; `streamcat.parquet` holds **147**. The twelve absent ones are `bankfulldepth`, `bankfullwidth`, `thalwegdepth`, `wettedwidth`, `ici`, `iwi`, `mast2014`, `msst2014`, `mwst2014`, `nars_region`, `nrsa_frame`, `prg_bmmi0809`. All twelve are in the request list, so a previous pull should have contained them.

Two candidate causes, not yet distinguished: they are new in the catalogue since the last pull, or they were **dropped during the write** — the incremental flush aligns every batch to the first batch's column list and discards anything absent from it, so a metric missing from the first 5,000-COMID batch vanishes from the entire file. The second is more likely, since channel geometry and stream temperature are long-standing StreamCat metrics rather than recent additions.

**Fixed by carrying them in the series artifact** rather than refetching 159 metrics over 1.5M COMIDs to collect twelve. Worth noting three of them are mean annual/summer/winter **stream temperature**, which the in-stream-processing theme is thin on and which denitrification rate depends on directly.

---

## 2026-08-26 — widening a StreamCat request tripled its cost and started timing out · **fixed**

Adding 327 nitrogen vintages to the existing request took each 5,000-COMID batch from 159 metrics to 486. Measured on a single batch: **42 s at 159 metrics, 100 s at 486**. Over 296 batches that is 3.5 hours against 8, and under sustained load about a tenth of requests exceeded the server's timeout — the run reached 13.5% in two hours before being killed, projecting sixteen.

The batching is over COMIDs only, so nothing shrank when the columns tripled. **Fixed by splitting the series into its own artifact** (`streamcat_series.parquet`) at half the COMID batch, which keeps each request near the payload size known to work — and, more to the point, means the base file, already complete for its own metrics, is never refetched merely to add columns beside it.

**The general lesson for any paged API here:** a request's cost is rows × columns, and a batching scheme that paginates only one of them silently changes cost when the other moves.

---

## 2026-08-27 — `latest_vintage` collapses 160 families to 159 names · **noted**

Two StreamCat families resolve to the same vintage-stripped name, so one is lost in the collapse. Almost certainly a naming collision in the trailing-year regex (`^(.*?)_?((?:19|20)\d{2})$`) — a family whose name genuinely ends in four digits would be indistinguishable from a vintage. Cosmetic at present, but it means the declaration silently drops one metric family, and the same regex is what `expand_full_series` and the series artifact rely on.

---

## 2026-08-27 — THE SYSTEMIC ONE: "have I done work here?" instead of "have I done THIS work?" · **three fixed, an audit owed**

Three independent caches in three modules made the same mistake, and Isaac's read is that there are more. **Queued for the next long lull: sweep every cache, checkpoint and coverage test in the pipeline for this pattern.**

| where | skipped when | blind to | consequence |
|---|---|---|---|
| `weather._complete` | the month's dates are present | which **variables** | five declared variables never fetched; a re-run was a silent no-op |
| `attributes._covered` | the row count clears a threshold | which **columns** | 327 nitrogen vintages requested, none fetched; 12 base metrics missing for years |
| `zonal.extract` | the part file **exists** | which **products** | ten of twelve gTREND components would have been skipped and the run would have finished green |

Each answers *have I done work here before?* when the question is *have I done **this** work before?* Each silently converts a widened request into a no-op — the worst failure shape available, because the run reports success and the artifact looks complete.

**The fix pattern, applied to all three:** compare the request against the **declaration**, on every axis the declaration names, reading footers rather than rows. `attributes._dir_covered` had it right all along (it checks `n_attrs` for exact equality) and is the model.

**Where to look during the sweep** — anything that decides to skip:
- `archive.expanded` / `archive.present` and the CDL `crops_cached` manifest (does it notice a changed year set?)
- `derive/canonical.py`'s per-sensor memoisation against a changed registry
- `structures.build_dam_points` / `build_facilities` cache-with-stamp (stamps cover the AOE — do they cover a changed input?)
- `access/cache.py`'s pooled read (`_store_key` hashes name/size/mtime — does it notice a changed COLUMN set?)
- `records.py`'s `cached` fetch state against a widened channel declaration
- `d8._accum_cache_file` (keyed on raster identity — this one is correct, and is a useful contrast)

---

## 2026-08-27 — `expanded_dir` could not see a hand-delivered raster directory · **fixed**

`archive.expanded_dir` searched for a SUBdirectory containing `*.tif`, because a zipped archive unpacks into a named top-level folder. gTREND's components were dropped in by hand and have no such wrapper — the tifs sit directly in `raw/gTREND/Surplus/` — so every component probed as MISSING and the d5 run refused to start, while the rasters were sitting exactly where the declaration said.

Compounded by a second stale path: the presence probe iterated a hardcoded `("crops", "agtile", "surplus", "fertilizer")` and resolved `raw/surplus`, a directory that no longer exists. It now iterates `NUTRIENT_SOURCES` itself, so a component added to the declaration is probed automatically.

---

## 2026-08-27 — the sweep: ELEVEN instances, and one sentence explains them all · **2 fixed, 9 open**

A full audit of every skip guard in `data/`. Isaac's instinct was right and understated — three known instances became eleven, plus two unclear and ten confirmed-correct.

**One sentence covers eleven of them: THE STAMP NAMES FEWER INPUTS THAN THE FUNCTION READS.** And the two most severe are the sharpest version of it — they *write* `aoe_stamp` into their own output and then guard on `.exists()`, so the correct key is sitting inside the very file the guard declined to open.

### Severe — silently wrong data, checks still pass

1. **`network.fetch` (`network.py:37,83`)** — skips the entire NHDPlus backbone when three files exist, blind to the AOE. Widen the extent and `flowlines`/`catchments`/`vaa` stay clipped to the retired one; every D5 product is then computed over the old catchment set **and written with the new stamp**, so `_check_product_aoe_stamps` passes. Complete-looking, correctly stamped, wrong region. The worst shape in the package. (`network.py:198` for waterbodies has the same guard.)
2. **`weather.aoe_cells` (`weather.py:266`)** — `grid.parquet` is reused whenever it exists, so `keep` freezes at whichever AOE first wrote it and every subsequent fetch is masked back down to the old extent. `_check_weather_lattice` **passes**, because it checks the cell count is *consistent across quarters* and the store is consistently truncated. This is the `_complete` bug displaced one level up, into the input `_complete` is measured against.
3. **`records.process_sensor` (`records.py:75`)** — `cached` is blind to a widened channel declaration. Add a channel: the census widens it, `canonical.stale` correctly rebuilds every canonical file, and the new channel is all-null everywhere because the natives were never re-asked. Damning detail: `_params_available()` reads the native's footer and returns exactly the evidence needed to decide this — and uses it only for display.
4. **`facilities.fetch_loads` (`facilities.py:146`)** — per-year keying is right for years, blind to the *filter*. **This already happened**: adding `_FLOW_PCODES` was a widened request, and every `fy*.parquet` already on disk kept the flow-less filter. Separately, `out.exists()` freezes the current partial fiscal year forever.
5. **Checkpoint parts are blind to the catchment cohort** (`zonal.py:192`, `products.py:135,485`) — the only cohort guard is `ceil(n_items/20_000)`, invariant to changes under 20,000 catchments, while batch membership is a Morton sort where inserting **one** catchment renumbers everything downstream.

### Moderate

6. **`build_nutrients` guarded product and not year** — *mine, fixed today*.
7. **`structures` stamps carry the AOE and not the inputs** (`structures.py:50,79,250`) — `build_facilities` is blind to CWNS arriving (a manual acquisition, so the ordinary sequence is: run D5, download CWNS, rerun D5, and the `cwns_*` columns never join). **`build_dmr_loads` is blind to a new fiscal year** — compounds directly with #4.
8. **`id_raster` (`products.py:365`)** — blind to the grid it derives from, so on a widened AOE the failure chains three deep and the new region's catchments get no weights at all. Neither weights check catches it: one asserts weight cells are *in* the grid, the other takes a **median** over catchments that have rows.
9. **`dams._cached` (`dams.py:37`)** — stamped on the extent, blind to NID being re-pulled or `STORAGE_COLS` widening.
10. **`weather.backfill()` had no caller** — *mine, fixed today*. `fetch_month` short-circuits before `_complete` for any closed month, so the variable check never runs on the ordinary path; the remedy existed but was reachable only from a REPL.
11. **`access.cache._store_key` (`cache.py:15`)** — keyed on the published store, blind to the requested column set. **Low severity and worth saying why**: the consequence is a missing column, which fails loud as a `KeyError` rather than silently. One-line fix.

### The correct implementations, for reference

`attributes._dir_covered` (exact `n_attrs`), `attributes._covered` (key + columns + rows, with the name list built *before* the test), `weather._complete` (dates *and* null-counted variables, caches cleared by every writer), `canonical.stale` (mtime *and* registry fingerprint — per-sensor memoisation **does** notice a changed registry), `iwqis._stale` (mtimes *plus* a namespace stamp), `d8._accum_cache_file` (content-keyed filename), `crops_cached`'s year check (against the zip central directory, without expanding 50 GB), and `publish`/`assemble`, which sweep-then-write and so have no reuse predicate to get wrong.

**Recommended fix pattern, uniform across all nine open cases:** the guard should read the stamp its own writer already emits, and the stamp should name every input the function reads. Where there is no natural stamp, key the artifact's filename on its inputs, as `d8` does.

---

## 2026-08-27 — scoping: WQX stations must never influence the AOE · **decided (Isaac)**

WQX discrete samples are grab-bag observations. **We do not want their basins and they must not enlarge the area of extent.** They register, publish records, and stay `aoe_seed = False`; if basins are ever wanted they can be recovered then.

The build already protects this twice — `extent.main` never re-expands on its own (expansion is a deliberate seed pass) and `fetch_seed_basins` delineates only `sites[sites.aoe_seed]` — but both are *defaults*, not assertions. The WQX adapter's acceptance criteria should include a check that no WQX registration is ever flagged as a seed, so the guarantee survives someone later "fixing" the default.

**Correcting my own claim:** I had said five skip-logic bugs blocked WQX because it would move the AOE. It cannot. Only `records`' channel-blindness is a genuine WQX blocker, via the new `nitrate_discrete` channel. The four AOE-triggered bugs remain real but fire only on a deliberate seed pass.

---

## 2026-08-27 — the external volume cannot be used as working space · **constraint, not fixable here**

`/Volumes/Extra Space` returns `Operation not permitted` on every read and write. It is **macOS TCC** rather than the tool sandbox — it fails identically with sandboxing disabled — and Isaac reports the per-application removable-volume permission cannot be granted in this setup.

**Consequence for planning:** the overflow strategy is void and the internal disk (~100 GB counting purgeable) is the entire budget. Large acquisitions must run **sequentially** rather than being spread across two volumes, and anything downloaded to the external drive must be extracted inward — by member, not by whole archive — before it can be used. The live case is SPARROW: three files out of an 8.4 GB zip.

Worth noting the diagnosis took two wrong turns: the empty `ls` output looked first like an empty directory and then like the tool sandbox. `stat` reporting the directory at 192 bytes — larger than an empty directory — was what showed the entries existed and were merely unreadable.

---

## 2026-08-27 — SPARROW audit complete: conditioning confirmed in the shipped data · **verdict reached**

All three decisive checks are run and the source is settled: **acquire the `sh_*` source shares, `DEL_FRAC`, `rchtot` and `tile`; refuse every routed total.** Full evidence in `data_audit_SPARROW_Aug26.md`; the two findings worth carrying elsewhere:

**The conditioning is measurable, not inferred.** `SE_PLOAD_TOTAL` reaches exactly 0.0 (754 rows in 4M scanned) while `SE_PLOAD_TOTAL_ND` never drops below 0.000205. A zero-width interval on a routed total is the substitution the model documents, visible in the data. And it is not remote: **85 of our 364 nitrate sites (23.4%) sit on a calibration GenID**, with 83 within a kilometre of a calibration station and a median distance of 11.2 km — so the contamination is concentrated on our own cohort, and most of the remainder lies downstream of a substituted reach.

**SPARROW is structurally blind to the intervention pairs.** 17.9% of our sites share a GenID with another (against ~4.3% for a random COMID set), and the collisions land on the paired deployments specifically: median nearest-neighbour distance 245 m for colliding sites against 712 m for the rest, and 52% of sub-kilometre-neighbour sites collide against 22% beyond. Any SPARROW feature is **constant across exactly the upstream/downstream contrast this project exists to measure**. The per-site map that makes this visible is kept at `notes/reports/data/sparrow_genid_map.csv`, and any model using SPARROW must carry the collision flag.

**A note on method that generalises.** The conditioning check needed two columns out of a 17 GB CSV on a disk at 98%. Streaming by column index through `csv.reader` cost no disk at all and the file was deleted the moment the two numbers were in hand. The instinct to load-then-measure would have needed headroom the machine did not have; **for a one-shot measurement over a large delivery, read it where it lies and keep the number, not the file.**

---

## 2026-08-27 — storage needs a real solution before the next bulk source · **needs Isaac's decision**

Disk ran to **12 GiB free (98%)** with StreamCat and d5 both writing and SPARROW's 17 GB staged. Deleting the SPARROW artifacts recovered it to 52 GiB (88%), so nothing was lost — but the margin was thin enough that a single unlucky writer would have tripped, and the recovery depended on a delivery being ephemeral.

Two structural facts make this recur rather than resolve: the external volume is unusable as working space (TCC, above), so there is no overflow; and the largest item in the plan is still ahead — **NWM at ~26 GB (CHANOBS) or ~76 GB (zarr)**, either of which is a *store*, not an ephemeral staging file, and so cannot be deleted after measurement.

Isaac has flagged that a new storage solution is needed. Until it exists, the operating rules are: run large acquisitions **strictly sequentially**, extract external deliveries **by member**, and treat any bulk file as ephemeral unless it is a store. The NWM verdict should not be executed on the current disk regardless of which route the audit favours.

---

## 2026-08-27 — the nutrients pass was on track to take nine days, and the cause is source tiling · **fixed**

Widening nutrients from 2 components to 12 (216 rasters, 2000–2017) turned a 31-minute pass into one projecting **~9 days**. It was not a scale effect and not the code that changed.

**Symptom.** The run held 99–100% CPU for 1h35m and wrote **zero** checkpoint parts — not one batch of 20,000 completed. The previous 36-raster pass wrote 74 parts in 31 minutes, about 25 s a batch.

**Diagnosis.** `sample` on the live process put **4,104 of 4,160 main-thread samples** in `FeatureSequentialProcessor::process → GDALRasterBand::GetLockedBlockRef → GTiffRasterBand::IReadBlock → gdal_LZWDecode`. `GetLockedBlockRef` reaching `IReadBlock` *is* the cache miss: every access was re-decompressing a block that should have been resident.

**Cause.** The gTREND GeoTIFFs are tiled **1168 x 1856 float32, LZW** — **8.67 MB a block**. GDAL's default cache is 5% of RAM, here **859 MB**, which holds **99 blocks against 216 rasters** — fewer than one apiece. A feature-sequential pass visits every raster per feature, so each catchment evicted what the last one loaded.

The tiling is the real mismatch, and it is worth stating in units: **one block covers 292 x 464 km — about 135,000 km2 — while the median catchment is 1.38 km2.** Reading a single catchment decompresses roughly a hundred thousand times the area it needs. Nothing makes that read cheap; it only has to happen **once per block**, which it does the moment the working set fits.

**Fix.** `zonal._raster_groups` splits the raster list into groups whose combined working set fits half the cache — 216 rasters become **18 groups of 12** — and each batch runs the groups in turn, concatenated by column. Single-raster and small-group passes (agtile, weights, CDL year groups) come back as one group and are untouched.

**The alternative was measured and rejected.** `strategy="raster-sequential"` also fixes the cache, but recomputes each feature's coverage fraction once per raster: over 12 rasters it ran **7x slower** (2.84 s against 0.40 s) for **bit-identical values** (12/12 columns, identical NaN pattern, exact equality). Grouping keeps the cheap strategy and simply asks for less at a time.

**Two lessons worth keeping.** First, `zonal.py:15` already documents Morton ordering as existing "so the block cache does its job" — the design knew about the cache and the assumption silently expired when the raster count went up 6x. A cache-fit assumption should be **computed at run time from the block size actually on disk**, which is now what happens. Second, my first benchmark showed both strategies finishing in under a second and I nearly acted on it: it declared `crs=5070` on geometry stored in **EPSG:4326**, so every catchment landed off-raster and the extract read nothing. The tell was a printed footprint of **0 x 0 km** — a sanity value in the benchmark output is what caught it, and a benchmark that returns all-NaN fast looks exactly like a benchmark that returns answers fast.

---

## 2026-08-27 — the nutrients closure was failing on units, not on data · **fixed, and the tolerance is now derived**

With the twelve gTREND components in, `fixation_ag = fixation_cropland + fixation_pasture` failed on **8,970 of 360,000** catchment-years. It was not a data defect and the fix was not a looser number.

**What the evidence said.** The median disagreement was **exactly 0**; failures concentrated on large catchments (median 88 pixels against 48 overall); and `uptake_ag`, identical in structure and extracted by the same code, closed at **0 of 360,000**. Error scaling with pixel count rather than with value is the signature of accumulated storage rounding — a genuinely missing component scales with the value.

**The actual cause.** Checked at the pixel level over 2.25M pixels with identical nodata masks: `max |Fix_Ag - (Fix_Cropland + Fix_Pasture)| = 0.0100`, exactly gTREND's kg/ha storage precision. The source closes to its own precision. But `kg = mean x n_px x HA_PER_PIXEL`, so that precision reaches the closure **multiplied by the 6.25 ha pixel** — 0.0625 kg a pixel, which is precisely the ceiling the failures were measured at (p90 and max both 0.0625). The tolerance was being compared in kg against a precision expressed in kg/ha.

**The fix** is `GTREND_PX_TOL = GTREND_KG_HA_PRECISION * HA_PER_PIXEL * 2`, a derived quantity with the factor covering three independently rounded rasters. Both closures now pass at 0 of 360,000, and a new test asserts the allowance still fails a genuinely absent component and still scales with pixel count rather than sitting at a fixed value.

**A false lead worth recording**, because it nearly became the answer: the failures capped at exactly 0.0625 = 1/16, which reads as a binary quantisation artefact. Testing it against the raw pixels refuted it — the values are ~0.01-quantised with float32 noise, not multiples of 1/16 — and 0.0625 turned out to be 0.01 x 6.25, a decimal precision times a pixel area. A number that looks like a power of two is not evidence that it is one.

---

## 2026-08-27 — an `*expect_values` signature that could never be called · **fixed**

`zonal.extract(..., force=False, *expect_values)` collected the checkpoint axes as varargs, and `build_nutrients` passed them as `*(("product", ...), ("year", ...))` **after** keyword arguments. Python unpacks `*args` into POSITIONAL slots, so the tuples landed on `ops` and `reduce`: every call raised `TypeError: extract() got multiple values for argument 'ops'`.

**It had never run.** The checkpoint-staleness fix this signature exists to serve was written, reviewed and left in place without once being executed — the long d5 run that appeared to exercise it had imported the module **before** the edit, so it was running the previous code from memory. The failure surfaced only when a fresh process started.

Now a keyword parameter, `expect_values=()`. Two things generalise: a long-running process is **not** evidence that the code on disk works, and varargs after defaulted keyword parameters is a signature that cannot be called the way it reads.

---

## 2026-08-27 — the eleven skip-logic instances are closed · **complete**

All eleven cases of *have I done work here?* standing in for *have I done **this** work?* are now fixed. The last three landed today:

- **`dams._cached`** stamped the extent alone; it now also stamps the NID pull it derives from, matching `build_dam_points`.
- **The crops, agtile and weights checkpoints** had no input key at all. `expect_values` guards axes that appear as VALUES in the output (product, year) and structurally cannot see an input that changed without changing the output's vocabulary — a re-pulled agtile raster, a regenerated cell-id raster, a CDL year rewritten in place. `zonal.extract` now takes `inputs=` and `_adopt_or_clear` keys the checkpoint directory on it, the way `d8._accum_cache_file` keys its cache.
- **`access.cache._store_key`** hashed the published store but not the column set asked of it, so widening the pooled frame's primitives returned the previous frame. Now both, and the column list is assembled BEFORE the key rather than after — the same ordering `attributes._covered` needs, for the same reason.

**A deliberate asymmetry in `_adopt_or_clear`: a MISSING stamp is adopted, not treated as a mismatch.** The parts predating the guard are the output of a four-hour pass and there is no evidence against them; only a stamp that actively disagrees clears the directory. Same coverage-not-equality reasoning as the network layers, and it is what keeps a new guard from destroying the work it was written to protect.

Five tests cover the semantics, including that a raster rewritten in place — which changes no value in the output — is caught.

**Two `verify --deep` failures are live-run artifacts, not defects**, recorded so they are not re-diagnosed: `no interrupted writes` sees StreamCat's in-progress `.parquet.tmp` (the check cannot distinguish an active atomic write from an abandoned one, which is a real limitation of running it during a fetch), and `every acquired file is manifested` sees the ten Wieczorek Hydrologic/Regions files acquired since the last cut. Both clear with a snapshot cut once the runs land.

Also worth recording: `verify all` reports **29** checks and `verify all --deep` reports **36**. That is the deep flag, not seven missing checks — the shallow run does not count them. The gap looks alarming and is not.

---

## 2026-08-27 — StreamCat series and d5 nutrients both landed; store is clean at 36/36 · **milestone**

Both long runs finished and the tree verifies end to end: **238 tests, 36 of 36 verify checks, zero failures**, snapshot `snap-20260827` cut over 15,978 files / 87.9 GB.

**d5 nutrients: 237.4 minutes**, against the ~9 days it was heading for before the raster-grouping fix. 320,834,088 rows (1,485,343 catchments x 12 components x 18 years), 3,521 MB. The closure result is the one worth recording: **0 of 26,736,174 off on both components** at full scale. The per-pixel tolerance was derived from gTREND's storage precision on a 20,000-catchment sample and then held exactly over 1,337x more data — which is the evidence that it was derived rather than tuned.

**StreamCat series: 398 minutes**, 1,478,330 rows x 654 columns (326 `cat` + 326 `ws` across eleven nitrogen families, plus `comid` and the stamp), 7.29 GB. Coverage is 1,478,330 of 1,478,684 askable reaches — 354 short, 0.024%, reaches the service had nothing for.

**A gap this run exposed and closed: the attribute stores were reachable by no reader at all.** `data/access` had no attributes path — not for the new 7.3 GB series and not for the 296-column `streamcat.parquet` that has been on disk for weeks. Acquisition without exposure is precisely what the Tier 0 audit exists to catch, and this plan's own criterion forbids adding to it, so `api.get_attributes` and `api.attribute_columns` now exist alongside `config.ACQ_ATTRIBUTES`.

Two design points in it. `comids` is **required, not defaulted** — an unfiltered read of the series is a memory event rather than a slow query, and this pipeline has already lost one run to a silent OOM. And the filter is **pushed down** rather than applied after materialising: `comid` is not sorted across row groups so statistics prune little, but the table built is the matched rows rather than 1.48M x 653 subsetted afterwards. Measured on the real cohort: **0.1 s** for a six-column slice of the 7.3 GB file, 338 of 342 nitrate COMIDs matched.

---

## 2026-08-27 — chapters brought up to the measured state · **Movement 5, in progress**

Written from measurement rather than expectation, which is the whole reason this movement was deferred to last.

**Pipeline chapter.** § Covariates/Rasters gains the twelve-component nutrients product, the per-pixel closure rule in the raster's own units, the block-cache raster grouping (with the 135,000 km2 block against a 1.38 km2 catchment stated in units, because that is the sentence that makes it obvious), and the two-key checkpoint guard. § Attribute tables gains StreamCat's two grains and the two lessons the fetch had to learn. § Access API gains `get_attributes`. § Verification gains a checkpoint-validity entry and a sharpened closure entry.

**Inventory chapter.** The gTREND section was describing two components at `raw/surplus/national/` — a path that no longer exists — and is now the twelve-component budget at `raw/gTREND/<Component>/`, including why two of the twelve are ingested *because* they are redundant. StreamCat gains its series pass, and the master table splits into two rows since the grain and window genuinely differ.

**A stale reference worth its own line: every runnable command in the pipeline chapter named `src.build3.run`** — the retired predecessor. A reader following the chapter would have gotten `No module named src.build3` on the first command. Now `data.build.run`, with the `--deep` form shown, and the documented command verified to run.

---

## 2026-08-27 — publication would have OOM'd on the new nutrients product · **fixed before it was hit**

`publish_comid_features` restamped each covariate with `write_parquet(read_parquet(p))`. That is fine for a 653 MB product and does not survive a 3.5 GB one: the twelve-component nutrients table is **320,834,088 rows** whose `product` column is a string, so pandas needs roughly **10 GB resident** — on a 16 GB machine already 9 GB into swap. The next p1 run would have died on it, and this project has already lost one run to a silent OOM that presented as exit 0 with no output.

`io.copy_parquet_stamped` streams row group by row group instead, attaching stamps to the schema without materialising the frame. Measured on the real product: **0.5 minutes at 0.37 GB peak RSS**, all 320,834,088 rows, all 306 row groups, stamps readable by the same `read_parquet(expect=)` that checks them. Row-group boundaries are preserved deliberately — they carry the statistics a predicate-pushdown reader prunes on, so flattening them would quietly slow every downstream read.

The general shape, since it has now appeared three times today: **an operation whose cost scales with the artifact is a latent failure the moment the artifact grows.** Nutrients grew 5.4x and broke a raster pass (nine days), a closure tolerance (units), and publication (memory) — none of which were touched by the change that grew it.

**Two related staleness facts, both expected rather than defects.** `published/comid_features/nutrients.parquet` is still the 653 MB two-component version from 2026-08-26; and `dmr_loads.parquet` exists in `derived/covariates` but not in the published store. Neither is a code gap — `publish_comid_features` copies whatever it finds — they are simply waiting on a p1 run, which is Isaac's to schedule.

---

## 2026-08-27 — p1 published; the twelve-component nutrients product is now reachable · **d5 batch closed**

Ran `p1 --single` to close the d5 batch, because leaving it unpublished recreated exactly the gap Tier 0 exists to catch: `get_covariates` was still returning the 653 MB two-component nutrients while the 3.5 GB twelve-component version sat in `derived/`.

Published: 32,491,828 record-days over 7,539 records, and **ten** comid_features products rather than nine — `dmr_loads.parquet` reached the published store for the first time, having been built and verified but never published. The streaming restamp held in production: 320,834,088 rows, 306 row groups preserved, and a reader now sees all twelve components. `verify all --deep` is **36 of 36** afterwards.

**A documentation error worth its own entry, because I introduced half of it.** The chapter's runnable command block named `python -m src.build3.run derive` / `publish` — the retired predecessor, and two targets that have never existed on the new runner. Correcting the module name alone left the targets wrong, and the correction was only caught by *running* the documented command and getting `unknown target 'publish'`. The real CLI is layers and stages, not verbs: `acquire` and `build` are the entry points, everything else is a stage id, a bare id runs from that stage onward and `--single` runs exactly it. Every command in the block has now been executed rather than reasoned about.

**Disk is the binding constraint on everything remaining: 15 GiB free.** Publishing nutrients cost ~2.9 GB net (0.65 -> 3.52 GB). The immediately reclaimable item is `derived/covariates/parts/nutrients` at **3.3 GB** — checkpoints for a product that is now built, verified and published, so they cost only a re-run if ever needed. Deleting them is Isaac's call rather than mine. Beyond that, the remaining plan items are all disk-bound: the gridMET pentad store (~10-15 GB), SNODAS, and NWM (~26 GB CHANOBS / ~76 GB zarr), none of which fit at 15 GiB.

---

## 2026-08-27 — where the disk actually goes, and which stores can leave · **measured; refactor deferred**

Recorded because the external-disk refactor is deferred and this measurement is its input. **Isaac is moving `cache/` to external media now and keeping everything else in place.**

**`cache/` was 61 GB of regenerable library cache** — gitignored, documented at `network.py:7`, read by no live code path (grepping `data/` for path construction into it returns nothing; only docstring prose mentions it). Contents, every one either a duplicate or a spent source:

| item | size | status |
|---|---|---|
| `aiohttp_cache.sqlite` | 49.5 GB | 3,953 HTTP response bodies; every product derived from them is in `acquired/` and manifested |
| `NHDPlusNationalData` | 14 GB | the geodatabase `acquired/network` was built from |
| `c9d2d67….parquet` + `nldplus_vaa.parquet` | 0.5 GB | **two** copies of the national VAA (2,691,339 x 45), from which the AOE-clipped `vaa.parquet` (1,511,095 rows) came |
| `nid_national.gpkg` | 75 MB | `cmp`-verified byte-identical to the manifested `acquired/attributes/` copy |

**The sqlite has no eviction: its `responses` table has exactly two columns, `key` and `value`.** No expiry, no size cap, so every bulk response body is kept forever at full size — which is how it reached 49.5 GB. It will regrow on the next bulk pull; a cap or expiry is the fix whenever the refactor happens.

**The store split for the deferred refactor, by ROLE rather than by layer.** The instinct is raw+acquired outward and derived inward. The measurement says otherwise: **`raw/` is 37 GB of which only 4 MB is read-path**, while **`acquired/` is 45 GB of which 43.3 GB is read-path.**

| store | size | read by `data.access`? |
|---|---|---|
| `raw/crops`, `raw/water`, `raw/gTREND`, `raw/agtile`, `raw/comid_attrs` | 37 GB | no — build only |
| `raw/basins/hydrosheds` | 4 MB | **yes** — `d8` flow direction, so basins and `dist_to_sensor` |
| `acquired/weather` | 23 GB | **yes** — `get_weather`, by design (the reduction is deferred to read) |
| `acquired/attributes` | 12 GB | **yes** — `get_attributes` |
| `acquired/water/native` | 6.1 GB | **yes** — `get_native`, the day-boundary escape hatch |
| `acquired/network` | 2.2 GB | **yes** — `get_network`, `snap`, `accumulate` |
| `acquired/facilities`, `metadata`, `*_basins` | 2.6 GB | no — build only |

So moving `raw/` costs nothing architecturally; moving `acquired/` severs six of twelve access functions plus the basin machinery. If `acquired/` must eventually leave, the honest route is **cohort-scoped projections into `published/`**, which is real work and gives up the ability to answer about reaches outside the current cohort — a deliberate decision, not a side effect of a disk move.

**A hazard the refactor must fix first.** With the stores detached, all three snapshot checks pass **vacuously** — tested by pointing the roots at a nonexistent path:

```
no interrupted writes           -> PASS  "clean"
every acquired file manifested  -> PASS  "0 files, all manifested"
expected populations            -> PASS  (reads the manifest, not the disk)
```

`verify` would report a green store with the drive unplugged. A mount assertion has to run before any of these, or the whole harness silently stops meaning anything when the volume is absent.

---

## 2026-08-27 — disk resolved: 124 GiB free · **constraint lifted**

Isaac moved `cache/` to external media. The Data volume went from **50 GiB free to 124 GiB** (used 381 -> 308 GiB), which lifts the constraint that was blocking the rest of the plan. The remaining Time Machine local snapshot is marked `Purgeable: Yes`, so it holds nothing back under pressure.

**What this unblocks:** the gridMET pentad store (~10-15 GB) and NWM CHANOBS (~26 GB) both fit now, individually and together. The external-disk refactor stays deferred; the by-role store split above is its input when it happens.

**A measurement caveat worth keeping, because it produced two wrong answers today.** `du` run from this environment lacks Full Disk Access and **silently skips TCC-protected paths** — it prints `Operation not permitted` to stderr and simply omits those bytes from the total. Any size I report is therefore a FLOOR, not a total, and reconciling it against `df` or `diskutil` will always leave an unexplained gap. Two hypotheses were built on that gap and both were wrong (root-only system directories: 5 GB, not 85; iOS backups in MobileSync: 8 KB, already purged).

Compounding it: `du -h` reports **GiB** labelled "G" while `diskutil` reports **GB**, a ~7% divergence at these magnitudes that widens the apparent gap further.

**The rule going forward:** for whole-volume accounting, trust `df`/`diskutil` totals and treat any `du` breakdown from this environment as a lower bound — or run it with `sudo` on Isaac's side. Do not reason about a residual until the measurement itself is known to be complete.

---

## 2026-08-27 — Tier 2 battery implemented, and gTREND's annual layers are mostly a static map · **Movement 4 complete**

`data/insights/agreement.py` implements the three tests Tier 2 owed — incremental information, temporal decomposition, missingness — over a seeded 20,000-catchment sample (352,206 catchment-years). All three are wired into the generated report; 13 tests assert each statistic on panels whose answer is known by construction.

**The result that matters: eleven of twelve nutrient components are a static map plus a year dummy.** Decomposing the (catchment x year) panel into between-catchment, between-year, and interaction variance:

| component | space | time | interaction |
|---|---|---|---|
| deposition_reduced | 0.994 | 0.000 | **0.006** |
| fertilizer | 0.974 | 0.000 | **0.026** |
| surplus | 0.963 | 0.000 | **0.036** |
| livestock_hogs | 0.948 | 0.000 | **0.052** |
| fixation_pasture | 0.745 | 0.018 | **0.237** |

Only the interaction term is spatiotemporal information — the part a model cannot obtain from a static covariate plus a year dummy. For most components it is **under 4%**. `fixation_pasture` is the lone exception at 24%.

**This confirms the documented method quantitatively rather than contradicting it.** The inventory chapter already records that gTREND downscales county-scale TREND totals across agricultural cells via a binary land-use mask, "so within-county variation in the raster is land-use pattern, not measured application variance". The decomposition is what that sentence looks like as a number: the spatial pattern is fixed by the mask and only the county-year total moves. Worth knowing before a model spends 320.8M rows on 18 annual layers — the practical reading is that a static component map plus a year term captures nearly all of it, with `fixation_pasture` the one component where the annual detail earns its storage.

**Incremental information: the budget is highly collinear.** Regressing each component on all the others (closure totals excluded, since they are their own parts summed): `uptake_cropland` R2 = 0.988, `fixation_cropland` 0.974, `fertilizer` 0.959 — reconstructible from the pool. The two that carry their own signal are **`livestock_hogs` (0.534)** and **`fixation_pasture` (0.629)**, which is a useful confirmation given that hog operations are the intervention story in this AOE.

Pairwise correlation is reported alongside but never instead: two products can each correlate weakly with a third and still be jointly redundant with it, which is exactly what the pairwise table misses and the regression catches.

**Missingness is structured and benign, now demonstrated rather than assumed.** 435 of 20,000 sampled catchments (2.17%) are absent from both StreamCat stores, and their **median area is 0.0 km2** against 1.389 for those present. They are reaches with no catchment polygon — matching the 32,411 the fetch explicitly skips as having "no catchment to summarise". The 2.17% also reconciles the two coverage figures: the sample is drawn from 1,511,095 flowlines while StreamCat is asked only for the 1,478,684 reaches that have a catchment.

**Two bugs the tests caught, both mine.** A product with zero variance made `variance_decomposition` raise `KeyError: 'interaction'` from `sort_values` on a column-less frame, rather than being skipped — a constant covariate is an answer, not a crash. And my first interaction test asserted the wrong thing: for `c x (t - 2000)` with uncentred factors the decomposition is 0.375/0.4375/0.1875, because main effects legitimately absorb most of a product of uncentred terms. The code was right and the expectation was wrong; the test now uses centred factors (interaction = 1.0 exactly) and a second case pins the uncentred behaviour, so **a low interaction share is not misread as proof of no interaction**.

---

## 2026-08-27 — the covariate layer's extraction is verified against external ground truth · **first time**

`products.huc8_surplus_reference()` had been implemented and **never run**. Running it closes the one gap every other d5 check leaves open: all of them are internal — class fractions summing to coverage, kg equalling mean times area — and every one would pass on a reduction that was wrong the same way twice. The gTREND authors published their own HUC8 aggregation of the same surplus raster, so aggregating our catchment values up and comparing tests our reduction against an independent one.

**Result, on 378 fully-covered basins x 18 years:** Pearson **0.99999**, median relative disagreement **0.039%**, **98.0% within 1%**, 100% within 5%, bias **0.99970**. Our zonal reduction reproduces the authors' aggregation essentially exactly.

**The restriction to fully-covered basins is load-bearing, not a convenience.** Across all 481 matched basins the correlation is 0.99700 and only 82.6% fall within 1% — but coverage runs to p10 = 0.903, because a HUC8 straddling the AOE edge is one we hold ~90% of, so our area-weighted mean is taken over a different region than theirs. That disagreement measures the clip, not the reduction. Restricting to basins whose summed catchment area matches the reference's own `AREASQKM` within 2% isolates the question actually being asked, and the answer moves from 0.997 to 0.99999.

Catchment-to-HUC8 mapping needs no spatial join: **NHDPlus `reachcode` is the HUC8 followed by a sequence number**, so the first eight characters are the basin id. The 363 MB `HUC8.gpkg` in the release is not required for this check.

Now a permanent `d5` check (`--deep`), with three tests including one proving it **fails on a 10% scale error** — exactly the class of defect no internal closure can detect.

---

## 2026-08-27 — the DMR/SPARROW comparison is partly circular, and the reason is worth more than the comparison · **lineage finding**

The plan reopened DMR measured loads against SPARROW's point-source share as a *measured against modelled* validation. Reading the model's own control file first — `conusModelsDR/control/sparrow_control_CONUS_TN4.sas`, line 643 — shows it is not cleanly that:

```
TNwwtp_kg = TNwwtp_obsv_kg + TNwwtp_mean1_kg + TNwwtp_mean2_kg ;
```

**`obsv` is observed.** SPARROW's wastewater source term is built from observed WWTP discharge plus two tiers of gap-fill, and the observed part is EPA permit reporting — the same lineage as our `dmr_loads.parquet`. Comparing the two would compare DMR against (DMR + imputation), which is precisely the trap battery item D exists to name: no correlation test distinguishes *agrees because both are right* from *agrees because both are the same data*.

**How much is actually measured, streamed from `data1.sas7bdat` (19.9 GB, 22,133,496 rows, 0.7 min):**

| component | mass | share |
|---|---|---|
| `TNwwtp_obsv_kg` (observed) | 7.940 Mt | **62.1%** |
| `TNwwtp_mean1_kg` (gap-fill 1) | 0.958 Mt | 7.5% |
| `TNwwtp_mean2_kg` (gap-fill 2) | 3.888 Mt | **30.4%** |

So **38% of the national wastewater nitrogen mass is imputed.** At row grain, of 1,219,060 reach-seasons carrying a wastewater term, **38.59% have no observed component at all**; and of 15,964 genids, only **507 (3.18%)** are entirely observed while none are entirely gap-filled — virtually every reach is a seasonal mixture of the two.

**The consequence for anyone using the shares, and it is not in the audit:** the shipped prediction outputs carry only the combined `TNwwtp_kg` and `sh_TNwwtp_kg`. The observed/imputed components exist **only in the SAS model input**, so a model consuming the wastewater share cannot tell which reaches rest on measurement and which on a regional mean. That is a quality caveat on the one SPARROW column the conditioning audit had cleared for use.

**What remains genuinely non-circular**, and is the next step: our DMR product covers reaches SPARROW had to gap-fill. Measuring that overlap says whether our own point-source data adds information SPARROW lacks, which is a real covariate-stack question rather than a circular agreement test. Needs a facility-to-genid snap through `conusModelsDR/gis/`, the same machinery the 364-site GenID map used.

---

## 2026-08-27 — our DMR product covers point-source nitrogen SPARROW does not measure · **the non-circular half of the validation**

The direct DMR-versus-SPARROW comparison is circular (previous entry). The question that is not: **does our DMR cover reaches SPARROW had to impute?** Answered by snapping 20,544 nitrogen-reporting outfalls to GenNet (median snap 514 m) and joining to the per-genid observed/gap-filled split.

**It does, and most for the facilities SPARROW is supposed to model.** Splitting by `facility_type` to control the obvious confound — SPARROW's term is specifically WWTP, while our DMR covers every permitted outfall including industrial:

| | municipal (POF) | non-municipal |
|---|---|---|
| genids | 3,112 | 10,042 |
| **absent from SPARROW's wastewater term** | **1,462 (47.0%)** | 3,623 (36.1%) |
| present but **mostly imputed** (<50% observed) | 732 (23.5%) | 2,596 (25.9%) |

**Absence is HIGHER for municipal than industrial** — 47.0% against 36.1% — which is the opposite of what "SPARROW only models treatment plants" would predict, and so the gap is not a facility-type artifact. Roughly seven in ten municipal reaches where we hold measured effluent are ones where SPARROW either has no wastewater term or is mostly imputing one.

Two caveats, both real. The **514 m median snap** is far looser than the 35 m the sensor GenID map achieved, because an outfall sits at a facility set back from the channel — some "absent" genids will be mis-snaps rather than gaps. And the comparison is **all-time against SPARROW's 2000–2020 seasonal term**, so a reach present in years we do not cover reads as absent.

**A separate finding that anyone using `dmr_loads` needs: summing `kg_n` across species double-counts.** The species are `ammonia` (3.24M rows), `total_n` (910k), `nitrite_nitrate` (741k), `kjeldahl` (666k), `nitrate` (186k), `nitrite` (86k) — and `total_n` subsumes the others. **32.4% of outfall-months report more than one species**, so a naive `groupby(...).kg_n.sum()` inflates nitrogen mass. The genid counts above are unaffected because presence does not depend on mass, but every mass figure from this product needs a species-selection rule first — prefer `total_n` where reported, else sum the disjoint components. This belongs in the product's docstring and in any recipe that consumes it.

---

## 2026-08-27 — the within-basin variance test, measured rather than proxied · **Tier 2 battery B**

`geometry.resolution_table()` answered "does a source resolve within a catchment" from **cell geometry alone**, never reading a value. That is cheap and generalises, but it is blind to a source whose values are smooth for reasons other than cell size — which is most of them. `geometry.decompose()` now measures it: catchment-grain variance split into between-basin and within-basin over **whole sampled HUC8s** (60 basins, ~82,000 catchments, 3 s). Whole basins, because a scattered catchment sample leaves almost no two catchments sharing a basin and the within-basin term is then undefined.

`within` is the share a basin-level aggregate **destroys**, so a source near zero is already a basin constant whatever resolution it advertises:

| source | within | reading |
|---|---|---|
| gTREND deposition_oxidized | **0.025** | basin constant |
| gTREND deposition_reduced | **0.040** | basin constant |
| gTREND livestock_hogs | 0.168 | mostly basin-level |
| StreamCat `pctagdrainagews` | 0.293 | mostly basin-level |
| gTREND surplus / fertilizer / uptake_ag | ~0.40 | mostly basin-level |
| AgTile tile fraction (30 m) | 0.413 | resolves within basins |
| StreamCat `pctagdrainagecat` | **0.476** | resolves within basins |
| gTREND fixation_pasture | 0.824 | resolves within basins |
| gTREND uptake_pasture | 0.873 | resolves within basins |

**Three findings worth carrying.**

**Atmospheric deposition is close to a single number per basin, in both dimensions at once.** It is 2.5–4.0% within-basin *and* 0.6–4.1% interaction in the temporal decomposition — the two independent tests agree. We store it at 250 m annual resolution and it delivers roughly one value per basin per era. Nothing is wrong with the source; the resolution is simply not information at our grain.

**`cat` and `ws` are genuinely different covariates, now with a number.** StreamCat's catchment-scope drainage resolves 0.476 against the watershed-accumulated 0.293 — accumulation smooths, as it must. That justifies carrying both rather than treating `ws` as a better-behaved `cat`.

**The pasture terms are the informative ones on both axes.** `fixation_pasture` and `uptake_pasture` top the spatial table at 0.82–0.87, and `fixation_pasture` was the only component with a substantial temporal interaction (0.237). Pasture is patchy where cropland is uniform, and that patchiness is the signal.

**Also landed in this pass:** battery A (coverage) — all COMID-keyed sources reach **97.8%** of a 20,000-catchment sample, the 2.2% shortfall being the zero-area reaches with no catchment polygon, consistent with the missingness result; and `data/insights/images/` is **no longer empty**, carrying three regenerated figures (both variance decompositions and the nitrogen redundancy matrix), which closes acceptance criterion 3.

**A test-harness trap worth recording:** `tests/data/insights/test_geometry.py` collided with `tests/data/build/test_geometry.py`. With no `__init__.py` in the test tree, pytest derives module names from **basenames**, so two same-named test files anywhere under the root are an import-mismatch error — and it only appears on a full run, not when the directory is tested alone.

---

## 2026-08-27 — Wieczorek NODATA is clustered, as the audit suspected · **Tier 2 battery F**

The source document named "the Wieczorek NODATA clustering" as the known case for structured missingness. It is now measured, and confirmed — but only after two corrections to how the question was asked.

**First correction: whole-row absence is the wrong test.** No Wieczorek catchment is null across a whole family, so an all-columns probe reports "no nulls" for all four families and misses the thing entirely. The question is **per-column NODATA**, and the worst-null column per family is findable from **parquet footer statistics alone** — no rows read.

**Second correction: the concentration statistic is biased upward when nulls are few.** `share_in_worst_decile` picks the decile *after* seeing the data, so with fewer nulls than basins even a random scatter lands them all inside the top tenth by rate. Guarded now at three nulls per basin, which is what moved `CAT_HUNT1` from a spurious "100% concentrated" to inconclusive.

**Result** over 120 whole HUC8s (~158,000 catchments), where **0.10 is what a random scatter gives**:

| family | column | null rate | nulls | worst-decile share | reading |
|---|---|---|---|---|---|
| soils | `CAT_HGA` | 0.32% | 499 | **0.836** | structured |
| hydrologic | `CAT_RECHG` | 0.27% | 431 | **1.000** | structured |
| topographic | `CAT_STRM_DENS` | 3.29% | 5,187 | 0.208 | mildly clustered |
| geology | `CAT_HUNT1` | 0.01% | 18 | 1.000 | **inconclusive** |
| StreamCat (row absent) | — | 1.99% | 3,154 | 0.326 | mildly clustered |

**`CAT_RECHG` — groundwater recharge — has every one of its 431 nulls inside a tenth of the basins**, and soil hydrologic group A is nearly as concentrated. Imputing either with a global mean or a nearest-neighbour fill writes a geographic pattern into the feature that the data never contained, and a model would read it as signal. The practical rule is that these columns need a missingness **flag** carried alongside, not a filled value.

Note the inverse relation worth keeping: the *lowest*-rate nulls are the *most* clustered (0.27% null, 100% concentrated) while the highest-rate one is the most diffuse (3.29% null, 0.208). Rare nulls here are coverage holes in specific places; common ones are a broad thin scatter. A single "how much is missing" number would have ranked these exactly backwards.

**Battery F is now complete**, and with it Tier 2's items A, B, E and F. Only the per-theme redundancy matrices remain — one exists for `nitrogen_inputs`; the rest need a per-theme column registry, since a theme names sources rather than columns.

---

## 2026-08-27 — shared ancestry does not show up as high correlation, which cuts the wrong way · **Tier 2 complete**

The per-theme redundancy matrices close the last Tier 2 gap. Six themes now carry one, from a hand-written probe registry (`sources.THEME_PROBES`) — a theme names *sources*, not columns, and choosing a representative column is a judgement about what the theme means, so it cannot be derived.

**The nitrogen_inputs matrix says something the do-not-double-count list did not predict.** Two clean blocks, and the cross-block correlations are *low*:

| pair | Spearman |
|---|---|
| gTREND fertilizer ↔ gTREND livestock | 0.838 |
| StreamCat `n_ff` ↔ StreamCat `n_lw` | 0.925 |
| **gTREND fertilizer ↔ StreamCat `n_ff`** | **0.517** |
| **gTREND livestock ↔ StreamCat `n_lw`** | **0.449** |
| **gTREND deposition ↔ StreamCat `n_dep`** | **0.067** |

Those last three are exactly the pairs the lineage list flags as **not independent** — StreamCat's `n_ff` and gTREND's fertilizer descend from the same USGS county estimates, and `n_lw` and Lvst_Sum from the same livestock inventories. Yet measured, they agree only moderately, and the deposition pair looks entirely unrelated.

**The explanation is that the processing diverges after the shared input**: StreamCat's are watershed-ACCUMULATED (`ws`) while gTREND's are catchment-local, and accumulation is a different quantity. Deposition is the extreme case, and the earlier within-basin result explains why — it is nearly spatially uniform (2.5–4.0% within-basin), so the local value is almost constant while the accumulated value tracks basin size. Two numbers derived from one dataset, correlating at 0.067.

**Why this matters more than the usual warning.** Battery D says no correlation test distinguishes *agrees because both are right* from *agrees because both are the same data*. The converse is the trap here: **low correlation does not demonstrate independence either.** A modeller seeing r = 0.52 between gTREND fertilizer and StreamCat `n_ff` would reasonably conclude they are separate measurements and use both — committing precisely the double-count the list exists to prevent, with the correlation matrix appearing to license it. The lineage column is therefore not a redundant belt-and-braces alongside the statistics; it is the only thing carrying that information, and the matrices must be read beside it rather than instead of it.

**Also corrected in this pass: the StreamCat series ends 2016, not 2017.** Thirty distinct years, 1987–2016, and two families are shorter than the rest — `n_dep` runs 27 years and `n_tin` 29 against 30 for the other nine, so a recipe joining them on a shared year index loses rows at the ends unless it names the years it wants. The wrong span was in the inventory chapter in three places and in the coverage table; all corrected.

**And an over-claim caught in my own output.** The report first said every theme without a matrix "carries fewer than two sources with a comparable COMID-keyed column" — false for `soils_and_geology` and `physiography_and_regions`, which had the sources and merely lacked a registry entry. Both now have matrices, and the remaining nine carry a **per-theme** reason instead of one blanket sentence: five are genuinely single-source, two are sparse point layers where a shared zero is an absence of structures rather than an agreement, one is a time series that is not commensurable with catchment columns, and one is the observations themselves.

---

## 2026-08-27 — the discharge experiment has a first number, and a 60x assembly speedup on the way to it · **Part 1 in progress**

**Feature assembly went from 7 hours to 7 minutes, and the reason is the third instance of one pattern today.** `api.get_weather` opens all 76 quarterly store files on every call to select row groups from footer statistics — excellent for one basin, and paid 4,927 times it is ~3 s of fixed scan per site against ~5 s total. Reading each quarter **once** and reducing every site from it turns 4,927 x 76 file opens into 76. Measured: **4,926 sites x 6,812 days reduced in 5.1 minutes**, 7.0 min end to end, 1.03 GB peak, 1.3 GB written.

The same shape has now appeared three times — the nutrients raster pass (nine days -> 237 min), publication's load-then-write (~10 GB -> 0.37 GB resident), and this. In each case the cost was repeated I/O over a shared resource and the fix was to touch the resource once.

**What made it safe to replace a working path:** the batched reduction replicates `get_weather`'s NaN-aware weighted mean rather than reinventing it, and `--validate` scores the output against `get_weather` itself, **raising rather than writing** if they disagree by more than 1e-9. Measured agreement: **7.4e-13**. A silent weighting bias there would have corrupted every downstream number invisibly, and no test of the model would have found it.

**Two cohort defects caught by smoking before the long run:**

- The published store carries a `discharge` channel for **122 tidal streams, 23 storm drains, 15 groundwater wells, 7 estuaries and one atmosphere station**. `config.SITE_TYPES` existed and `cohort.sites()` never applied it. A rainfall-runoff model over a contributing basin is the wrong description of every one.
- **5.48% of cached rows carried negative discharge**, which a specific-discharge log transform cannot represent. After the site-type filter, every one of those rows was in an excluded type — tidal reverse flow, not a gauge fault.

**First result, 250 sites, stride 7, held out by basin family** — against the NWM audit's numbers on 19 of our own reaches:

| metric | this model | NWM (gaged reaches) |
|---|---|---|
| median NSE | **0.428** | 0.35 |
| median r | **0.747** | 0.701 |
| median log-NSE | 0.487 | 0.521 |
| median KGE | 0.453 | 0.646 |
| median bias | 0.904 | 1.023 |

**A diagnosis worth recording because the first fit looked worse than it was.** Bias came out at 0.759 — a systematic 24% under-prediction — with KGE at 0.387. That is textbook **retransformation bias**: fitting in log space and exponentiating returns the conditional MEDIAN, not the mean, which is biased low for a right-skewed variable, and KGE punishes it twice because it scores the mean and variance ratios directly. Duan's smearing estimator (the mean of `exp(residual)`, computed on the TRAINING fold only, since taking it from the test fold would leak the answer into its own correction) moved bias to 0.904 and KGE to 0.453, leaving NSE and r untouched as expected — correlation is invariant to a multiplicative factor.

Not a verdict yet: this is 250 of 4,927 sites at a coarse stride, and the full run is underway. But the direction is that a model built entirely from data already on disk is **already ahead of NWM on NSE and correlation** at held-out basins.

---

## 2026-08-27 — the discharge experiment answers the NWM question: do not acquire · **Part 1 decided**

Full result in `notes/reports/discharge_model_report.md`. 4,620 gauged sites, 906 basin families, 3.46M daily rows, five folds grouped so a test site's basin never contains a training site's outlet.

| metric | this model (median per-site) | NWM at gaged reaches |
|---|---|---|
| NSE | **0.509** | 0.35 |
| log-NSE | **0.585** | 0.521 |
| correlation | **0.787** | 0.701 |
| bias | **1.007** | 1.023 |
| KGE | 0.529 | **0.646** |

**Beaten on four of five, and on the harder test.** NWM's 0.35 was measured at gauges it was calibrated against; this was measured on basins the model had never seen. Gauged skill is a ceiling on ungauged skill, so NWM's real number where we would use it — the 158 nitrate sites with no gauge — is below 0.35, while 0.509 is already an ungauged-equivalent measurement. The decision rule was written before the numbers existed and it says: **do not acquire**.

KGE is the honest exception and worth keeping visible: 0.646 against 0.529. KGE penalises the variance ratio directly, and a boosted-tree regression damps a hydrograph in a way a routed physical model does not. If a downstream task depends on flow *variability* rather than level and timing, that is the argument for revisiting.

**The finding that nearly hid inside a good headline.** The first full run reported median per-site NSE 0.464 — already ahead of NWM — beside a **pooled NSE of −0.064 and pooled r of 0.025**. Those two cannot both describe the same predictions, and the divergence was the symptom, not a quirk of pooling: a handful of sites over-predicted by **1,000–14,000x**. For *specific* discharge that can only mean the drainage area is wrong, which is the bad-snap failure `xgboost/basins.py` documents — gauge on a tributary, snapped reach a large river.

The fix is physics rather than a threshold: **runoff ratio**, long-term discharge depth over precipitation depth, which cannot exceed ~1 and cannot plausibly sit at 0.002. The cohort is tightly behaved (p50 0.349, p01 0.0023, p99 0.894) and the **170 sites (3.5%) outside 0.01-1.5 carry a median NSE of -1.2 against 0.489 for the rest**. After filtering, pooled and median AGREE (0.523 against 0.509) — which is what says a defect was removed rather than a tail trimmed. **That ~3.5% of gauged sites carry a wrong snapped area is a finding about the published store, not about this model.**

Two things the filter does *not* explain, kept because reporting only the flattering half would be wrong: 375 of the 463 sites with NSE below -1 have perfectly plausible runoff ratios and are simply hard basins; and both models are scored at gauges, so neither number is true ungauged skill.

**Also caught by smoking before the long run:** the published store carries a discharge channel for 122 tidal streams, 23 storm drains, 15 groundwater wells, 7 estuaries and one atmosphere station, and all of the 5.5% negative-discharge rows were in those types.

Twelve project tests cover the paths where a bug would be silent — the unit conversion, the smearing correction, and **fold leakage**, which would invalidate the entire experiment without making any number look implausible.

---

## 2026-08-27 — the imputed discharge series, and a bug only the physics caught · **Part 1 complete**

110 of the 158 ungauged nitrate records now carry a modelled daily discharge (749,320 values); **48 are refused rather than filled** — 11 wells, 7 lakes, 7 estuaries, 5 groundwater stations, 3 tidal streams and assorted tile outlets, buffers and a pond, each recorded with its reason. A rainfall-runoff response over a contributing basin describes none of them, and a number there would be invented rather than imputed.

**The first imputation ran clean and was wrong.** All 749,320 values positive and finite, nothing raised, every individual figure plausible. But the predicted **runoff ratio was 0.103 against the training cohort's observed 0.349**, with a spread of 0.092–0.113 — essentially the same value at every site. A model predicting near-identical runoff everywhere is not damping; it is running blind.

**Cause: the static features were never attached on the prediction path.** `site_frame_nodrop` produced 80 columns where the model expects 90 — drainage area, slope, elevation, stream order and all ten StreamCat attributes arrived as NaN. XGBoost accepts NaN silently, so the model fell back to weather alone and returned something near the global average.

The root cause was **two implementations of one thing**: `build_pool` attached statics inline, and the prediction path simply did not. It is now a single `attach_statics` called by both, and the prediction path **prints loudly** when any expected feature is absent — which immediately surfaced 3 sites whose COMIDs are missing from StreamCat entirely, consistent with its 2.2% coverage gap.

**After the fix**, checked against the same water balance that screens the training cohort:

| | blind model | corrected | observed cohort |
|---|---|---|---|
| runoff ratio p50 | 0.103 | **0.310** | 0.349 |
| p05–p95 | 0.092–0.113 | **0.165–0.450** | 0.025–0.577 |
| median q, mm/day | 0.193 | **0.453** | — |
| outside physical bounds | 0 | **0 of 110** | — |

The predicted spread stays narrower than observed, and that is the honest face of the KGE deficit rather than a residual bug: a boosted-tree regression damps variance, so peaks are underrepresented — predicted daily maxima reach 47.8 mm/day where observed reach about 140. The series is reliable for level and timing, conservative for extremes, and consumers should be told so.

**The lesson worth keeping.** The same water-balance check has now caught two different things: a defect in the *store* (3.5% of gauged sites with a wrong snapped area) and a defect in my *own output*. Neither raised an exception and both produced numbers that looked reasonable in isolation. **A validity check is worth more pointed at your own results than at someone else's data** — and a check that only ever passes has not yet earned its place.

---

## 2026-08-28 — sizing the pentad fetch corrected two things the plan assumed · **Part 3, before any code**

The plan's rule is that every acquisition is sized before it runs. Probing the endpoint first corrected two assumptions that would otherwise have been written into the fetch:

**`spi270d` does not exist.** `spei` and `eddi` publish all eight accumulation windows (14d, 30d, 90d, 180d, 270d, 1y, 2y, 5y); **`spi` publishes seven** — no 270-day. So the set is **25 aggregates, not 26**, and a loop over a hardcoded eight-window list would have failed on one request in twenty-five, most likely partway through a multi-hour run.

**`z` is its own dataset, and `category` is not a product at all.** The plan recorded "`category` for `z`". Measured, `agg_met_z_...` carries `daily_mean_palmer_z_index`, and **`category` is a companion grid inside EVERY drought file** — a binned classification of that file's own index. It is therefore derivable from the value and should not be stored: keeping it would add 25 columns that are a deterministic function of 25 columns we already have, which is the definition of the redundancy the Tier 2 battery exists to find.

Worth noting how this was established, because `dataset.xml` is not a validity test — THREDDS answers 200 for a nonexistent dataset. The distinguishing signal is the **response body**: a real dataset returns grid declarations (`agg_met_pr_...` 3,981 bytes, one grid), a bogus one returns **zero bytes**. A probe that only checked the status code would have reported all 26 present, including `spi270d`.

**Measured cost:** one tile-year of `spei90d` is **19.44 MB in 3.3 s**. Over 25 aggregates x 6 AOE tiles x 19 years (2008-2026) that is **2,850 requests, ~2.6 hours**, and ~55 GB transferred — transient, since each tile-year is reduced to AOE cells and discarded rather than staged. Consistent with the plan's 1-2 h estimate at year granularity, and it confirms year windows are right: the same request at month granularity would multiply the fixed per-request cost twelvefold for the same payload.

---

## 2026-08-29 — the pentad drought store landed, and a check that catches unloaded checks · **Source 4 done**

**`acquired/weather/pentad/` now holds 25 aggregates over 2008-2026** — 19 year files, 4.9 GB, 61.5M rows in the resumed pass. Every full year is exactly **16,868,840 rows** (231,080 cells x 73 pentads), a complete lattice each time; 2026 is correctly partial at 47 pentads. Reachable through `api.get_drought` with seven tests, and `verify` gained three checks that pass: all aggregates present, all cells on the gridMET lattice, and **median spacing exactly 5 days** — the last because the store's whole reason for existing separately is its cadence, and a daily-spaced file there would mean the wrong endpoint was read.

**The reader names its date column `pentad_start`, not `date`, on purpose.** These values cover the five days beginning at that stamp. A frame calling the column `date` would join cleanly against a daily series and align four days out of five to the wrong value -- no exception, no warning. Naming it differently makes that join fail loudly. The frame also carries `.attrs['cadence'] = 'pentad'`.

**A transient timeout killed the first run at year sixteen of nineteen.** No data was lost (each year is skipped when present) but a human had to notice and restart, which over 2,850 requests against a public THREDDS server is not a rare event. The fetch now retries four times with linear backoff, and re-raises `HTTPError` immediately since a 400 means "not published" and retrying cannot help.

**The finding that outlives the source: three registered checks had never run, and verification passed.** `verify._load_all` names its modules literally, so `data.build.acquire.drought` shipped with three checks that were never registered — and the run reported a clean 37 of 37, because a check that is not loaded contributes no row rather than a failing one. The module docstring already warns that "an empty table looks exactly like a clean one" for a module that fails to IMPORT; a module simply omitted from the list is the same hazard with no loud error at all.

So `verify` now checks itself: **every module defining a check must have contributed one to the registry.** It tests registration rather than membership of the load list, because some modules arrive transitively (`weather` and `structures` via `products`) and forbidding that would be wrong. Cheap — a source scan for the decorator, no imports.

**It immediately found a second instance.** `data.build.acquire.attributes` defines the Wieczorek-themes check, is on no load path, and had therefore never executed since it was written. Both are now registered, and the count moved 37 -> 41.

---

## 2026-08-30 — SNODAS sized, then postponed (Isaac) · **deferred with its numbers**

Postponed to later in the week. Recording what the probe established so the deferred work resumes from measurement rather than starting over.

**Access and shape confirmed.** Anonymous HTTPS at `noaadata.apps.nsidc.org/NOAA/G02158/masked/{YYYY}/{MM}_{Mon}/SNODAS_{YYYYMMDD}.tar`, no credentials. One tar per day holding **16 members** — eight products as paired `.dat.gz` (flat big-endian int16) and `.txt.gz` (ASCII header). The four the plan wants are present: `11034tS__` (SWE), `11044` (melt), and `01025SlL00`/`SlL01` (the rain/snow accumulation split). No new binary dependency: `numpy.frombuffer(dtype='>i2')` reads it.

**The cost is much larger than the plan implied, and it is the download rather than the storage.** One day is **24.5 MB in 6.2 s**. Over the ~6,800 days of the collection window that is **~11.7 hours and ~167 GB transferred** for the ~21.6 GB the plan budgeted as stored. The plan estimated the store and never the fetch; at 6.2 s/day this is by far the most expensive acquisition in the document — nearly eight times the pentad store's 1.5 h.

**Per-product fetching is not available.** The tar is the unit, so all eight products are transferred even though four are wanted; there is no per-file URL to narrow it.

**A measured way to cut it roughly in half, for whoever resumes.** Sampling 2015-07-15 against 2015-01-15 over the full CONUS grid (23,239,185 cells):

| | July | January |
|---|---|---|
| SWE valid cells | 11,989,602 | 11,989,602 |
| SWE **nonzero** | **687** (0.006%) | 6,614,631 (55%) |
| melt nonzero | 780 | 450,795 |

July's few hundred nonzero cells are high-elevation western snowpack — **outside our AOE entirely**. So an Oct–May window would lose essentially nothing for this region while removing about 40% of the days. That is worth confirming against AOE cells specifically before relying on it, but the CONUS-wide numbers already make the case that a full-year fetch buys summer days that are all zero here.

---

## 2026-08-30 — Sekellick acquired; the karst justification needs re-examining before it is · **Source 6, half done**

**Sekellick & Sherr is in** — `acquired/attributes/sekellick_n.parquet`, 1,485,343 AOE reaches, 12 columns (confined manure, unconfined manure, fertiliser × 2002/2007/2012/2017), fetched and clipped in **0.5 minutes**. Exposed through `api.get_attributes(source="sekellick")` in the same pass, with a registered check asserting both manure classes are present — a file carrying only one would be useless, since the split is the entire reason to hold this source.

**The release is 728 MB and we took 279 MB of it.** Nine files ship: the nitrogen triplet, a phosphorus triplet, and two `de_` (delivered) variants. Only the three nitrogen files are modelled here, and the plan's "728 MB" was the release rather than the requirement.

**The meta-check added yesterday caught its first real omission, unprompted.** `data.build.acquire.manure` defined a check and was not on the load list; `verify` reported `DEFINED BUT NEVER LOADED` before the module had ever been run in anger. That is exactly the failure it was written for — three checks shipped invisible with the pentad store — and it took one tick to pay for itself.

**Karst is sized but NOT acquired, because its justification has weakened since the plan was written.** The download is confirmed direct and scriptable: `pubs.usgs.gov/of/2014/1156/downloads/USKarstMap.zip`, **282.3 MB** — though it returns **403 to a default urllib User-Agent** and needs a browser one, which is worth recording because it presents as "the file is gone" rather than "set a header".

The justification was **"the only geology dataset in the review with no substitute on disk — verified zero karst attributes across all 14,139 Wieczorek and 167 StreamCat families."** That verification was a search for the *word*. Since it was written we have acquired the **Wieczorek Hydrologic theme** (Fix 4), which `new-data-report.md:111` describes as "precisely the karst-vs-tile-vs-loess flow-pathway partition the source document argues for": `CAT_CONTACT` (subsurface contact time), `CAT_BFI`, `CAT_IEOF`, `CAT_SATOF`, `CAT_RECHG`, `CAT_EWT`, `CAT_TWI`.

Those are functionally what karst *predicts* — rapid routing with low attenuation — computed per COMID and already on disk. Karst may still add: it classifies by **burial depth**, so a carbonate aquifer under 40 ft of till scores zero carbonate-residual while being exactly the cover-collapse setting that matters, and no flow-pathway attribute encodes that directly. But "no substitute" is no longer the right description, and the honest test is the incremental-information measure the Tier 2 battery already implements — which cannot be run until karst is on disk. **Acquiring it is cheap (282 MB, minutes); the point is that the reason for doing so should be stated correctly, and the redundancy question answered afterwards rather than assumed away.**

---

## 2026-08-30 — karst acquired, and my doubt about it was measurably wrong · **Source 6 complete**

I flagged that karst's "no substitute on disk" justification had weakened, because the Wieczorek **Hydrologic** theme arrived after the plan was written and carries the flow-pathway partition (`CAT_CONTACT`, `CAT_BFI`, `CAT_IEOF`, `CAT_SATOF`, `CAT_RECHG`, `CAT_EWT`, `CAT_TWI`) that the source document argues karst is for. Acquired it anyway so the question could be answered rather than argued, and the answer is unambiguous.

**Regressing each karst fraction on the ENTIRE Hydrologic partition plus StreamCat's `pctcarbresidcat`, over 153,953 catchments in 120 whole HUC8s:**

| karst fraction | R² given Hydrologic + pctcarbresid |
|---|---|
| `carbonate_E_frac` | 0.100 |
| `carbonate_B2_frac` | 0.041 |
| `carbonate_B3_frac` | 0.029 |
| `evaporite_E_frac` | 0.058 |
| `evaporite_B2_frac` | 0.006 |
| `evaporite_B3_frac` | 0.001 |

**None of it is reconstructible.** The two describe different things and are empirically almost unrelated: Hydrologic models *how water moves* — contact time, baseflow index, overland-flow shares — while karst records *what the rock is and how deeply buried*. Conceptually adjacent, statistically independent.

And the original justification is directly confirmed rather than merely restated: **`pctcarbresid` predicts buried carbonate at R² = 0.041 and 0.029.** A residual-material measure genuinely cannot see a carbonate aquifer under till, which is the whole argument for holding this source and is now measured instead of asserted.

**What landed.** `derived/covariates/karst.parquet`, 1,478,528 catchments, 3.1 minutes for the overlay. **422,749 catchments (28.6%) carry some karst**, so it is not a marginal layer here. Fractions rather than flags, computed in the map's own equal-area projection rather than reprojecting the source into ours. Buried carbonate (B2 + B3) reaches 11.6% of catchments.

**One column is always zero and should be known to be:** `carbonate_B1_frac` and `evaporite_B1_frac` are 0.00% across the AOE. B1 polygons exist in the CONUS bbox (467 of them, 121,164 km²) but none intersect an AOE catchment. The columns are kept rather than dropped, because their absence is a fact about our region rather than about the source, and a future extent could reach them.

**The meta-check caught its second and third omissions this session** — `acquire.manure` and then `derive.karst`, each before the module had run in anger. Two ticks after being written it has now prevented three sets of checks from shipping invisible.

---

## 2026-08-30 — WQX sized, and the review burden is ~42,000 merge candidates · **needs Isaac's decision before any fetch**

The plan required WQX's review burden be sized **before** the run, not during, because it is the only item whose blast radius includes the review queues. Sized, and the answer should change the plan.

**The count method the plan assumed does not work.** It specified sizing each request with "the free `Total-Result-Count` HEAD". The `wqx3` endpoint answers HEAD with **200 and no count header of any kind** — confirmed against Iowa. Station counts have to be fetched, which is cheap (0.7 MB, 15 s a state) and is what was done.

**The node population would grow 3.7x.** Censusing all 41 AOE states for the three accepted nitrate characteristics since 2008: **75,599 stations**, of which **20,158 are `USGS-` prefixed** (the overlap the plan correctly identifies as resolvable by string equality) leaving **55,210 genuinely new registrations** against our current 20,226 located sensors. Iowa alone returns 2,786, matching the plan's 2,318 estimate.

**The review burden, measured at the machinery's own thresholds:**

| | merge gate (100 m) | pair table (2 km) |
|---|---|---|
| new vs existing | 2,130 | 12,653 |
| **new vs new** | **40,079** | 106,089 |
| **total** | **42,209** | 118,742 |

**42,209 merge candidates is roughly two orders of magnitude beyond anything the queues have handled** — the published store currently holds 7,312 proximity pairs and 136 ledger masks in total. At ten seconds a decision that is 117 hours; at thirty, 350. It is not reviewable by a person, and no amount of panel polish changes that.

**The shape of the problem is specific and suggests its own remedy: 40,079 of the 42,209 are new-versus-new** — WQX stations near other WQX stations, largely volunteer programmes sampling the same stream at nearby points. Only **2,130** involve an existing sensor. So the burden is dominated by pairs where both sides are new registrations from possibly-different organisations, which is a different question from "is this new station the same node as one of ours".

**Options, for Isaac rather than for me:**

1. **Auto-distinct WQX-vs-WQX pairs**, reviewing only cross-source ones. Drops the burden from 42,209 to **2,130** — large but conceivable. The cost is a standing assumption that two organisations sampling within 100 m are different nodes, which is sometimes false.
2. **Narrow the geography** to the cornbelt core rather than all 41 states.
3. **Filter on record density** before registration, which the plan explicitly argues against — it wants density published and left to each project's cohort filter.
4. **Defer WQX**, and treat the discrete-sample expansion as its own project with its own identity strategy.

The plan's own framing supports taking this seriously: WQX "changes what the project can be rather than what it knows", and it is the only source that touches identity. That is exactly why the burden was to be sized first. **Nothing has been fetched beyond station coordinates.**

---

## 2026-08-30 — WQX collected as a distinct data class, never as nodes · **Isaac's decision, and it dissolves the problem**

**Decision (Isaac, 2026-08-30): collect WQX, surface it in the access API, and nothing more.** WQX stations are not stations in our sense — they do not enter the census, never become registrations, and must not appear anywhere in the proximity or identity machinery. They are a distinct **class** of observation: discrete samples rather than a monitored series.

That dissolves the blocker rather than negotiating with it. The measured review burden was **42,209 merge candidates** against a queue that has handled 136 ledger masks in its life; collecting without registering makes it **zero**, while keeping the thing worth having — 75,599 stations of nitrate observation across the AOE against our ~20,000 sensors, as a spatial field rather than a node population.

The constraint is **enforced, not intended**: a registered check asserts no `WQX` source ever appears in the sensors table, and it currently reports **639,259 results held, no leakage**. `api.get_discrete_nitrate` addresses them by location and time, offers no `site_uid`, and stamps `.attrs['class'] = 'discrete_sample'` so nothing mistakes them for a published series.

**Two endpoint hazards, both found by measuring rather than reading the docs:**

**`zip=yes` is silently ignored.** `wqx3` returns `text/plain` CSV whatever the parameter says.

**Truncated responses arrive as HTTP 200 with a valid header row and no error marker.** Alabama returned **3.7, then 9.5, then 14.8 MB** — each parsing as perfectly good CSV — before eventually completing at 16.7 MB. Kansas failed four times at ~5.9 MB. Without a completeness check the store would silently have held a third of a state, and nothing downstream could have detected it. The plan anticipated an `ERROR: INCOMPLETE DATA` marker; the real truncations carried **no marker at all**.

The detector therefore **parses** the final line rather than counting separators: a comma tolerance loose enough to survive a quoted field is loose enough to accept `4,` against a three-field header, which is exactly the case it exists to catch. And since truncation is **intermittent rather than a size limit** — the same 19-year Alabama query failed three times and then succeeded larger — the fetch retries the whole window first and only falls back to 3-year chunks for a state that keeps failing. Cost is per **request**, not per byte (one year 19 s, nineteen years 28 s), so chunking by default would be pure slowdown.

**The plan's sizing method does not exist.** It specified "the free `Total-Result-Count` HEAD"; `wqx3` answers HEAD with 200 and no count header of any kind. Station counts were fetched directly instead — 0.7 MB and 15 s a state.

**`Activity_TypeCode` is kept, and the reason is that "grab sample" is a state-dependent claim.** Iowa 2015 is 82% `Sample-Routine`; **Alabama is 62% routine and 36% integrated vertical profiles**, with 2% field measurements against Iowa's 0.1%. A composite averages over time and a cross-sectional profile across the channel — different measurement processes that a single `nitrate_discrete` channel would have flattened. Quality-control activities are held in the store and excluded by the **reader** by default: the build records what the source said, and "a field blank is not the river" is a judgement applied visibly at read.

---

## 2026-08-30 — WQX collected: 2.11M results, 41 states, zero failures · **Part 4 complete**

**2,112,841 discrete nitrate results across all 41 AOE states in 138 minutes, no state failed.** Four needed the three-year-window fallback; the rest completed on the whole-window request with retries. 26 MB stored. Matches the plan's ~2.16M estimate. Snapshot `snap-20260830` cut; `verify all` 37 of 37; the not-a-sensor check reports **2,112,841 results held, no WQX source in the sensors table**.

**A CORRECTION to what I reported earlier.** Asked whether WQX is all grab samples, I answered from Iowa 2015 — 929 rows — and said field measurements were 0.1%. That sample was badly unrepresentative. Over the full 2,111,704 results:

| `Activity_TypeCode` | count | share |
|---|---|---|
| Sample-Routine | 1,792,819 | **84.9%** |
| **Field Msr/Obs** | 148,961 | **7.1%** |
| QC Sample-Field Replicate | 35,370 | 1.7% |
| Sample-Composite Without Parents | 27,030 | 1.3% |
| Sample-Other | 19,832 | 0.9% |
| Sample-Integrated Vertical Profile | 17,303 | 0.8% |
| QC Sample-Field Blank | 13,407 | 0.6% |

**The aggregate vindicates the "grab sample" characterisation — 85% routine — but the state variation destroys it as a per-state claim.** Field measurements run at a median of 0.9% per state and reach **54.5% in Texas, 42% in Iowa**, 24% in Rhode Island, 16% in Massachusetts. Iowa is the core AOE state, and nearly half its WQX nitrate is a probe reading rather than a bottle. My 2015 figure of 0.1% was one year of one state and wrong by a factor of four hundred.

I flagged at the time that it was a single state-year and that percentages would move. They moved far more than "would move" conveys, and the lesson is the ordinary one: **a distribution sampled at one point is not a distribution.** The categories were right, the numbers were not, and only the categories should have been asserted.

**This vindicates carrying `Activity_TypeCode` rather than flattening to one `nitrate_discrete` channel.** A field measurement is a different instrument and a different error structure from a lab-analysed grab, and in the state we care about most it is 42% of the data. Pooling them would have been irreversible at read.

**QC is 2.3% of the corpus** — 48,777 results across the replicate and blank types — excluded by the reader's default and retained in the store, which is the division the quality philosophy asks for: the build records what the source said, and the judgement that a field blank is not the river is applied visibly at read.

---

## 2026-08-30 — the exposure audit was reporting false gaps; now it reports two real ones · **branch sweep**

A sweep of the finished plan work found the Tier 0 audit accusing things that work. It flagged **`acquired/wqx` as unreachable** while `get_discrete_nitrate` reads it — the four new access functions had no entry in `EXPOSED_BY` — and it flagged **`acquired/facilities`**, which has no direct reader but is consumed by `derive/structures` whose product is published.

**A false finding is worse than a missing one here**, because it sends a reader to fix working code and makes the true rows easy to dismiss. So the audit now reports **three states rather than two**:

- **reachable** — an access function names it
- **intermediate (consumed by the build)** — no access reader, but referenced somewhere in the live `data/` source, so it is an input to derivation and the architecture working
- **ORPHANED** — reachable by nobody: no access function, and no mention anywhere in `data/`

The consumption scan **excludes `src/` deliberately**. That is the retired predecessor, and data referenced only there is precisely what this is built to surface.

**Two genuine orphans, and both have clean explanations rather than being mysteries:**

| store | files | size | referenced only in |
|---|---|---|---|
| `acquired/authority_basins` | 7,123 | 72 MB | `src/build3/` |
| `raw/comid_attrs` | 3 | 53 MB | `src/features/`, `src/build/` |

**`authority_basins`** is the predecessor's NLDI-fetched basin set. The live layer delineates with the accumulate walk instead, which is why nothing reads it. Worth noting I leaned on its existence earlier as evidence that discharge-test needed no delineation — that was wrong in its reasoning even though the conclusion held: the project builds its own basins and never touches these.

**`raw/comid_attrs`** holds `BFI_CONUS.zip` and `CONTACT_CONUS.zip` — baseflow index and subsurface contact time, downloaded directly by the predecessor. Those are exactly `CAT_BFI` and `CAT_CONTACT`, which now arrive through the Wieczorek **Hydrologic** theme. Superseded rather than lost, and the same theme that made me doubt karst.

Both are safe to retire and both sit in snapshot-manifested stores, so removing them is Isaac's call rather than mine. Four tests cover the distinction, including that `src/`-only references must not count as consumed.

---

## 2026-08-31 — the skip-logic bug reappeared in code I wrote this week · **two more instances, caught by sweep**

A sweep of this session's code found **two fresh instances of the family I spent Wednesday closing** — a guard asking *have I done work here?* when the question is *have I done THIS work?* Both are mine, both were written after the eleven-instance audit, and both would have failed silently.

**`drought.fetch_year` skipped a year whose file existed.** But the current year is written **partial** — 2026 holds pentads through 2026-08-23 and stops. Every later run would skip it, so the newest year freezes at whatever the first pass happened to fetch, and nothing raises: a frozen file is a perfectly valid file, the lattice check passes on it, and past years are unaffected, which is what keeps the failure invisible. `_covers` now compares the stored maximum date against the last pentad the endpoint can have published — `min(year end, today − 5 days)`, one pentad of slack for the publication cadence.

**`wqx.fetch_state` had the same shape.** Its request window runs to today, so a state fetched in August holds samples to August and an existence guard would never refresh it. `_current` now tests staleness against a **90-day** horizon — long enough to be quiet given that WQX contributors submit months after sampling, short enough that the store cannot silently stop.

**Why this is worth recording rather than just fixing.** The eleven-instance audit made me confident the pattern was closed; it was closed *in the code that existed then*. The pattern is not a set of sites, it is a **habit** — `if p.exists(): return` is the shortest correct-looking thing to write, and it is wrong exactly when the artifact grows after being written. Every store with a moving upper bound has this hazard, and the daily weather store already solved it with `_complete` and `_recheck_from`; the two new stores simply did not inherit the lesson.

Eight tests cover both, each asserting the *frozen* case rather than only the healthy one.

**Also cleared in the sweep:**

- **A seven-hour trap.** `projects/discharge-test/assemble.py` — the naive assembler superseded by `assemble_fast.py` — was dead code that no longer appears in the README, is imported by nothing, and **writes to the same cache directory**. Running it would spend seven hours producing what takes seven minutes, and it would appear to work. Removed; the reasoning survives in `assemble_fast`'s docstring.
- **8.9 MB of regenerable parquet would have been committed.** `projects/*/results/*.parquet` is now ignored, matching the split `xgboost/results` already uses, where only `log.json` is tracked. The JSON score records and the refusal list stay tracked — they are the decision trail.

---

## 2026-08-31 — when an existence guard is a bug and when it is correct · **the rule, after sweeping all 67**

Sweeping every existence-guarded early return in `data/` — 67 of them — turned the skip-logic pattern into something more useful than "existence guards are suspect", because most of the 67 are right and changing them would be the error.

**Three kinds, and only one is a bug:**

**1. `if not X.exists(): return None` — the not-built-yet guard.** The large majority. A stage that has not run yet is a legitimate state and returning `None` is how the caller learns it. Correct.

**2. `if X.exists() and not force: return cached` on a WHOLE-FILE DELIVERY — correct, and deliberately so.** NID (`attributes.py:625`) and the facilities vendor archive (`facilities.py:43`) both freeze at first download, and that is the pipeline's own model rather than an oversight: `snapshot`'s contract is that a manifested file changes only through the **declared mutation classes — trailing-revision re-pulls and partition appends**. A vendor delivery is neither. Re-downloading one automatically would be an undeclared mutation that the drift gate would then have to adjudicate every pass. Getting a newer NID is a deliberate act (`force=True`), which is the right shape.

**3. The same guard on a PARTITIONED store with a moving upper bound — a bug, silently.** This is what `drought.fetch_year` and `wqx.fetch_state` were. The distinguishing feature is that **the artifact is incomplete at the moment it is written**: a pentad year file created in August holds pentads to August, and the year has not finished. The file is valid, every check passes on it, past partitions are unaffected — and the newest partition freezes forever.

**So the rule is not about `.exists()`. It is: does this artifact's correct content depend on when it was written?** If yes, existence cannot be the guard and something must compare the stored extent against the reachable extent — `weather._complete` counting dates, `records`' trailing `refresh`, `drought._covers` against `min(year end, today − 5 days)`, `wqx._current` against a 90-day horizon. If no — a vendor zip, a census year, a static polygon map — existence is exactly right and adding a freshness test would manufacture drift.

`records.py` deserves a note as the model implementation: it tests `_covers_declared` for a widened channel declaration *and* carries a `refresh` path that re-fetches a trailing revision window, so it handles both the shape-changed and the time-moved cases separately rather than conflating them.

---

## 2026-08-31 — the freeze rule is now a standing check, and writing it caught my own cry-wolf · **48 checks**

The two freeze fixes stopped the bug at fetch time; they do not detect a store that froze under older code, which is exactly the situation any store fetched before today was in. So each of the three time-partitioned stores now carries a **currency check** asserting its newest partition reaches the present.

**The daily weather store had no such check and needed one most.** Its three existing checks — variables complete, quarters starting on time, exact cell×day lattice — **all pass on a frozen store**, because each is true of the partitions it has. Nothing asked whether the newest one was recent. A guard skipping on existence could have stopped the store at 2026Q3 forever with verification green throughout.

**Writing the pentad check immediately caught an error in my own fix.** It failed: newest data 2026-08-23 against a horizon of 2026-08-26. But the store was not frozen — pentads fall every five days *and* publication lags them, so a one-pentad horizon calls a healthy store frozen. That would have done double damage: refetching the current year on every pass (4.8 minutes each), and a verify check crying wolf — which is worse than no check, because an alarm that fires when nothing is wrong stops being read. I had written exactly that warning into the weather check's own comment minutes earlier and then violated it.

The horizon is now **two pentads** and lives in one function, `_horizon`, **shared by the fetch guard and the verify check** so the two cannot drift apart. That sharing is the real fix: the first version had the same constant written twice, which is how a guard and its check start disagreeing about what "current" means.

Tolerances are deliberately generous — 21 days for daily weather, two pentads for drought, 90 for WQX where contributors submit months after sampling. The failure being guarded is a store stuck **months** back, not one a week behind.

Checks now 48, tests 311.

---

## 2026-08-31 — WQX in Iowa is majority probe readings, not bottles · **open — affects any use of the discrete channel**

Reviewing `notes/book/data_inventory_chapter.md` for staleness turned up a substantive error in it, and the underlying fact matters more than the correction.

**Across the whole 2,112,841-result corpus WQX looks like grab samples: 84.9% `Sample-Routine`, 7.1% `Field Msr/Obs`.** Per state that description collapses. The field-measurement share has a **median of 0.7%** across the 41 states with at least a thousand rows — and its **maximum, 51.6%, is Iowa**. More than half of Iowa's 43,595 WQX nitrate results are probe readings rather than laboratory analyses of a collected bottle. Alabama is separately 37.6% integrated vertical profiles, a cross-channel average rather than a point.

**Why this is a finding and not a footnote:** Iowa is the project's core state, and the working characterisation of WQX — the one that justified admitting it as a distinct class from the continuous sensors — was "grab-bag samples". In Iowa that is false for the majority of the rows. A probe reading is methodologically closer to an IWQIS sensor observation than to a lab result, so the clean class boundary the collect-only decision rests on is blurrier in exactly the state where the data would be used. This does not undo the decision — WQX still must not enter the census or the identity pipeline — but any model consuming the discrete channel should split on `Activity_TypeCode` rather than treating the channel as homogeneous. `Activity_TypeCode` is carried unflattened, so this is available rather than lost.

**The error trail is worth keeping, because the same mistake was made twice in opposite directions.** The chapter claimed Alabama had "twenty times Iowa's share of field measurements", from a single state-year (Iowa 2015, 929 rows) that put Iowa at 0.1%. The truth is 51.6% — off by a factor of five hundred, with the anomaly's sign inverted: the sample chosen to show field measurements were negligible came from the one state where they dominate. The first correction was then *also* wrong ("42% in Iowa, 54.5% in Texas") because `Location_State` holds full state names and a lookup by two-letter code matched zero rows, silently. Texas is 0.0%.

Two rules fall out. **A distribution sampled at one point is not a distribution** — one state-year cannot characterise a 41-state corpus. And **a key that returns no rows is not a small number**: the `groupby` returned a clean answer for a code that never appears, and nothing in the shape of the result said so. Any per-state or per-code aggregation should assert its key set intersects the data before the numbers are read.

Chapter corrected; the underlying `Activity_TypeCode` split is left to the consumer.
