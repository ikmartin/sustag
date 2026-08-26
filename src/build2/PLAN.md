# New data pipeline — plan

A clean rewrite, not a migration. `src/build` is **reference material only** — read it for a trap or a formula, do not port it, do not run or diff against it.

The **data tree stays where it is**: `build2` reads and writes `src/data/`, reusing what is downloaded. Per-registration artifacts live in `interim2/`, identity-unified output in `processed2/`; both are gitignored. The read layer evolves in place in chunk 6.

**TWO SITE TABLES, named by the unit they hold.** `processed2/registrations.parquet` is one row per REGISTRATION and never loses one -- every chunk widens it, and a row excluded from modelling keeps its place and its reason there, which is what makes an exclusion judgeable at all. `processed2/sites.parquet` is one row per TRAINABLE PHYSICAL GAUGE: merged registrations collapsed to a single row, non-surface types and record-less sites gone. It is written once, by chunk 6, and is the only table a consumer opens.

The split does not contradict *the build never drops a SITE*. That rule exists because a build-time filter reaching forward for a later stage's artifact caused the 116<->123 oscillation and made dropped sites unjudgeable. Neither happens here: nothing is lost from `registrations`, and `sites` is projected LAST, when every predicate already has its evidence. Filtering early on partial evidence and projecting late on complete evidence are different operations.

## What changes, and why

**Scope is computed once, in chunk 0.** The three boxes in `pipeline_config.toml [region]` are only the **seed discovery cover**; the AOE is their union with every seeded sensor's basin, stored as a stamped geometry. Everything is collected inside it for the declared years (2008–2026 gridded dynamic; full span for annual per-COMID tables; none for static).

Full containment is affordable **only because the AOE follows drainage divides rather than squaring them off**: measured at 4,284,001 km², 1.43× the seed-box 3,004,514 km², where a bounding box of that union is 8.4M km², 2.80×. The difference is the Missouri arm, a narrow branching region a box turns into the entire high plains. Polygon-taking sources (`pynhd`, `pygridmet`) get the geometry and realise the saving; bbox-only ones get a cover and are clipped back at once. **It never re-expands** — sensors found later inside the arm are used but do not enlarge it, or each pass admits sensors whose basins admit more and the extent walks to CONUS; `frac_basin_covered` records the shortfall.

**The build never drops a SITE.** No successor to `filter_sites`; no stage removes a site for short record, poor coverage, oversized basin, wrong type or failed snap. Every stage only *widens* `registrations.parquet` — chunk 6's projection into `sites.parquet` is the single exception, and it leaves the registration table untouched. Selection happens afterwards, as predicates over it: `graph_node`, `train_label`, `claim_set` are modelling-time queries, never columns the build writes. Throwing away **data** is fine — clip rasters, keep declared years, subset variables, drop a theme. Only sites are protected.

The reason is mechanical. Evidence appears at different depths — row counts at ingest, coverage once basins exist, degree once the graph exists — so a build-time filter reaches forward for an artifact a later stage produces. That caused the old 116↔123 oscillation and made dropped sites *unjudgeable*. Chunk 0's filter is not an exception: it sets `aoe_seed`, deciding which basins **define the geometry**, and excludes nobody from the table.

**Storage, measured rather than estimated.** gridMET 19.3 GB (267,906 cells x 6,940 days x 11 vars at 11.8 bytes/row, matching the old build's 12.9), NHDPlus AOE clip 2.2, StreamCat 3.5, water ~1.6, Wieczorek TBD -- about **30 GB against 53 GB free**. The NHDPlus national archive is deleted after clipping; re-clipping costs a 7.3 GB refetch. **Manually acquired, outside the pipeline and uncounted:** CDL national (50 GB, CONUS, reusable at any extent) and the gTREND rasters — both need a recorded source URL and vintage, and neither needs refetching when the AOE changes. **`gridMET_raw` was deleted**: 28,417 hash-named fragments scoped to the *old* bbox, and HyRiver keys its cache on a request hash, so even the overlap would have missed.

## Data sources

**Labels** — USGS continuous nitrate under all three pcodes `99133 + 99137 + 00630` (`99133` alone returns zero Nebraska sites); IWQIS; MPCA. **WQP is excluded** — not continuous surface monitoring. Heidelberg NCWQR and CAMELS-Chem remain candidates.

**Graph nodes** — every NWIS continuous series in the AOE: discharge, stage, temp, spec cond, turbidity, DO, pH, groundwater, precip, soil.

**Network** — NHDPlus V2 via `pynhd.nhdplus_l48()`, the whole national dataset once (7.3 GB, cached) and clipped locally, because a REST query over an AOE holding ~1.2M flowlines times out and tiling costs 29–466 requests; VAA via `nhdplus_vaa()`, not the HydroShare path (301→405); NWM `feature_id`/`latitude`/`longitude`/`order` as the COMID→coordinate table; HydroSHEDS 15s D8 re-clipped to the AOE.

**COMID attributes** — StreamCat via the module-level `pynhd.streamcat()` (NOT a method on the class), both `cat` and `ws` areas, minus the fitted-on-target metrics; Wieczorek via `pynhd.nhdplus_attrs_s3()`, which takes attribute NAMES and a `pyarrow_filter` rather than themes or comids -- 5,584 attributes across seven themes, dropping Climate Water Balance, which is 7,668 of the 14,139 and redundant against gridMET; National Inventory of Dams.

**Wieczorek scope: 3,570 attributes**, CAT and TOT (ACC is dropped -- it differs from TOT only at divergences). 42.7% of the release predates the 2008 window and MOST OF IT STAYS: nitrate carries a decadal groundwater lag, so SOHL's 1940-2005 backcast and the pre-2008 nitrogen surplus are the only legacy evidence there is, and neither is reconstructible from post-2008 data. Only 156 attributes are cut, and only where a better measurement of the same quantity is already in the pull -- NWALT 1974/1982/1992/2002 against annual 123-class CDL, and NLCD imperviousness 2001/2006 against IMPV11. `WIECZOREK_SUPERSEDED` is the list, and reversing it is a one-line edit.

**The national rasters are not superseded by any of it.** CDL is 18 annual years against Wieczorek's 2011-2014; nitrogen surplus is 88 annual years, 1930-2017, at 250 m against two vintages of gross application (1997, 2012), and surplus is the quantity that leaches rather than the quantity applied; AgTile replaces `CAT_TILES_Early90s`, which is 1992 and writes 0.0 where NODATA == 100. What Wieczorek adds uniquely is atmospheric deposition, soils, geology and the SOHL backcast. Surplus ENDS AT 2017, leaving 2018-2026 uncovered -- carry-forward or missing is a modelling decision, not a build one.

**Rasters** — gridMET; CDL; gTREND surplus, **fertiliser (on disk, used by nothing)** and manure; AgTile. `precip_in_1d` is dropped: its Iowa-only footprint is a covert state indicator.

**Modelled** — NWM v3.0 retrospective daily aggregates, COMID-keyed, site COMIDs first.

## Chunks

Scope, download, identity, basins, aggregation, assembly. Each widens `registrations.parquet` and is testable on its own; only chunk 6 projects, and it writes a separate table rather than narrowing this one.

**COMID replaces `global_node_id` everywhere.** Catchments partition the surface — verified at a median summed-catchment-to-basin area ratio of 1.001 — so the spatial key is a stable external identifier, not a bare row counter four tables depended on and any region change renumbered.

**Merge candidates come from the SHARED REACH, not distance.** On the 116-site pre-Aug-03 cohort this separates perfectly: all four true pairs share a reach and are sequential USGS→IWQIS handoffs, one of them 822 m apart where a 250 m radius misses it; all five concurrent pairs under 2 km sit on different reaches at r = 0.08–0.54, the inflow/outflow treatment case merging must never swallow. A drainage boundary states where water goes; a radius does not. Measured again on the full 392-site cohort: 58 sites in 27 groups, and grouping on `catchment_comid` instead reproduces those 27 groups exactly. So identity follows the snap, and basins follow identity — a merge cannot be undone once two basins exist for one gauge.

### Chunk 0 — seed filter and AOE definition

Discover every registration in the three seed boxes; pull source metadata; mark the seed set; take each seed's basin from NLDI (`comid_byloc` then `get_basins`, ~6 s per non-USGS site, no local network needed); write the AOE geometry and stamp.

The seed filter is **conservative on purpose**. Metadata gives a record *span*, not a count, so the only safe tests are `span < 1 day` and "no nitrate series at all", where `begin`/`end` are null. Record length is judged nowhere near here: chunk 1 archives anything with a nitrate observation, and `chunk6.assemble.MIN_GAUGE_DAYS` decides trainability at the end over the MERGED record. A thin site may still merge into a real gauge, so excluding its basin from the extent on span alone would truncate the AOE for a site that turns out to matter.

**Columns added.** `aoe_seed`, `seed_exclusion_reason`, `nldi_basin_km2`.

**Done when:** `aoe.parquet` holds one stamped geometry, its area reported against the seed-box union; every registration carries `aoe_seed` with a reason; no site deleted.

### Chunk 1 — raw download

> **⚠ DEFERRED — national raster check.** CDL, gTREND and AgTile are not in a standardised on-disk form, so chunk 1 has no presence check for them. Decide the form in chunk 5, then come back and write it.

Everything fetched, nothing derived. Water records keeping all parameters; NHDPlus flowlines, VAA and catchments; gridMET over the AOE; COMID attribute tables. Water, network and gridMET run in parallel; the COMID-keyed pulls (StreamCat, Wieczorek, NWM, NID) wait on the network, which defines the COMID set. The snap is chunk 3's — it derives rather than fetches.

**Columns added.** Identity: `site_uid`, `source`, `source_site_id`, `agency`. Location: `lat`, `lon`, `datum`, `state`, `county`, `huc`, `tz`. Source: `station_name`, `source_drainage_area_km2`, `altitude_m`, `site_type_raw`. Record: `first_day`, `last_day`, `n_nitrate_days`, `longest_gap_d`, `regime`, `pcodes`, `params_available`, `reported_units`, `detection_limit`. Type: `site_type`, `site_type_source`. Co-location: `nearest_nitrate_sensor_m`.

**Done when:** all raw data is on disk; record parquets carry every reported parameter; null and zero-area catchments are counted — 1.86% on the old cohort, four of its 116 sensor reaches, all order 5–6 mainstem, so a zero-area guard is needed wherever a per-area feature divides by it.

### Chunk 2 — canonicalisation

Native observations become one fixed channel schema: source columns mapped to canonical names via `params.CHANNELS`, units converted, each channel scrubbed against its own physical range, then binned to `io.STANDARD_TZ` days as seven primitives — `mean min max p25 median p75 n_obs`. `amplitude` and `iqr` are derived at read rather than stored, so they cannot drift from their inputs. The engine is `daily.py`, beside `io.py`, because the read layer and the widget's virtual-pin path need the same reduction on data that was never on disk.

**IT SITS BEFORE IDENTITY BECAUSE THE MERGE RULE NEEDS IT.** Co-located sensors reporting *different* variables have to merge into one node, and recognising that means knowing which channels each site reports — which is a canonical question, not a native one. It cannot fold into chunk 1 either: unit conversion is a derivation, and chunk 1's rule is everything fetched, nothing derived. Reading a documented fill value as missing (the MPCA sentinel) is reading the format correctly and stays in the adapter; multiplying by 0.0283 to reach m³/s is not.

**A pre-aggregated day writes NULL, not zero,** to everything but `mean`. USGS `_dv` columns are already a daily mean, so filling `min`/`max` from the single value would record a measured diel amplitude of zero where nothing was measured at all.

**Done when:** a site with nothing to scrub reproduces its chunk-1 nitrate daily file exactly; no `_dv`-sourced channel-day carries a fabricated spread statistic.

### Chunk 3 — snap, site type, and site identity

Anchor every registration on a reach, decide what it IS, then decide which registrations are one gauge. Snap lives here because it is a derivation, not a download, and identity is its only consumer until chunk 4.

**Merge candidates are pairs sharing a snapped COMID**; the decision comes from record agreement. No temporal overlap means sequential re-registration; overlap at r ≳ 0.80 with near-zero median difference means dual registration; overlap with poor agreement means distinct sensors, never merged. Group by connected components, not pairwise, or the result is order-dependent. Canonical member has the most nitrate days, source precedence breaking ties. **Every merge is human-reviewed.**

Both COMIDs are stored. `catchment_comid` adds no candidate the snap misses, so its value is the 25 sites where the two disagree — a **near-a-divide flag**, twelve of them snapped within 50 m where the mainstem carries no catchment of its own.

**Site type is decided here, not at discovery.** Chunk 0 has only the source's type field and a station-name regex, and for 152 of 392 the regex matches nothing — `*_default_stream` is a guess it cannot check, because the reach a site snaps to and the waterbody that reach threads do not exist until chunk 1 and the snap. Six ranks, highest confidence first; ranks 3–5 (NHD) **promote a guess but never overrule evidence**, recording disagreement in `site_type_conflict` instead. `Spirit Lake` is caught; the Susquehanna and Maumee — wide rivers threading area polygons — are flagged rather than reclassified and dropped. Chunk 0 keeps a metadata-only prior for the basin guard; the AOE is unchanged at 4,284,001 km².

**Columns added.** Type: `site_type`, `site_type_source`, `site_type_conflict`. Snap: `comid`, `catchment_comid`, `comid_agree`, `snap_dist_m`, `snap_status`, `stream_order`, `stream_level`, `terminal_path`, `pathlength_km`, `totma_d`, `catchment_area_km2`. Merge: `physical_gauge_group`, `is_group_canonical`, `merge_rule`, `merge_shared_days`, `merge_r`, `merge_median_diff`, `merge_seam_date`, `merge_decided_by`, `merge_sep_m` (evidence now, not the trigger).

**Done when:** the review artifact lists every shared-reach pair with its evidence and the four known pairs appear; nothing merges without a recorded decision; the merge block is null for any site in an undecided pair. Chunk 3 writes METADATA ONLY — no nitrate record is materialised here, so a changed decision is a re-read in chunk 6 rather than a rebuild.

### Chunk 4 — the sensor graph and basins

Basins per physical gauge, by **accumulating upstream catchments locally** rather than per-site NLDI calls. The walk is `fromnode`/`tonode` over the VAA — not `terminalpa` + `hydroseq`, which admits parallel branches that are not upstream — and it reproduces NHDPlus's own `totdasqkm` on **388/388** gauges, which is what makes it trustworthy rather than plausible. Chunk 0's NLDI basins were for scoping only.

Four methods: `basin0` the authority's polygon (USGS only), `basin1` from `comid`, `basin2` from `catchment_comid`, `basin3` the D8 raster. **Auto rule: basin0 if present, else basin1** — a precedence, not a score. `basin2` and `basin3` are cross-checks a human may select but the rule cannot reach, and `basin2` earns its keep: WQS0065 and WQS0066 come back at 765,616 km² from the containing catchment against 9,220 and 2,359 from the snap, which is precisely the old build's Missouri failure now visible as a disagreement instead of as the answer.

**The river flag was redesigned rather than ported.** `_make_basins.flag_river` fired on proximity to a large river — 20 of 116 sites, including every legitimate mainstem gauge, as its own comment conceded. The question worth asking is whether the site was *captured*: its basin is the river's when it should have been something smaller beside it. Snap distance does not discriminate (a wide river's gauge sits 100–430 m from the centreline) and reported drainage area is unusable (five of six candidates report exactly one square mile). The **name** carries it — 56 of 62 large-river sites share a word with their reach's GNIS name — and the flag now fires on 2, one of them a water-supply well snapped to the Des Moines River.

**Also written:** `basin_reaches.parquet`, the upstream COMID set behind each selection. That is what chunk 5's covariates are summed over, and it is the reason accumulating late costs nothing.

**THE GRAPH LIVES HERE TOO, AND IS COMPUTED FIRST.** The node DAG and the basins come out of ONE downstream-labelling pass over the network, so splitting them into two chunks would mean walking twice. For each reach, the first node encountered walking downstream owns it: that yields the **incremental catchment sets**, which partition the network at ~1.29M rows however many nodes there are, and the **DAG edges** M → N where N is the first node below M.

**The closure over those edges is NOT a basin, and cannot be.** It was meant to be: a full basin as the union of incremental sets over the upstream closure would let ~1.5M rows replace an 8.2M-row `basin_reaches`. Measured over 350 valid nodes it agrees with `accumulate.upstream` on 319 and comes up SHORT on 31, never long. The cause is structural rather than a defect: `accumulate.upstream` admits BOTH branches at a divergence, because walking up from a reach collects everything whose water can reach it, and a partition cannot — a reach that splits flows to two places and an owner map gives it one. So the two walks are not inverses wherever NHDPlus records a divergence, and the gap is widest at deltas. `accumulate.upstream` is the authority and basins keep using it; what this pass provides is the DAG, which nothing computed before.

That order is what makes a large node set affordable. The per-gauge BFS in `accumulate.upstream` has no memoisation, so it does not survive a cohort in the thousands, and materialising full reach sets re-stores every Mississippi headwater reach once per node below it. Edges come from flow-path adjacency, never from testing basin containment — containment is the *consequence* (if M is upstream of N then basin(M) ⊂ basin(N)), and deriving it the other way is O(n²) set comparisons instead of O(reaches).

**Only valid nodes get a basin**: surface type plus a successful snap. Non-surface sites never enter the graph. **Chunk 4 owns every graph-derived column** — `n_upstream_nodes`, `n_downstream_nodes`, `flow_dist_nearest_node_km` — because a chunk-6 column that depended on chunk 4 would run the dependency backwards. `nearest_gauge_m` is unaffected: it is a co-location diagnostic over all registrations and already sits on the registration table.

**Done when:** every gauge has a basin or a named reason; `flag_area_vs_vaa` passes on every build; nothing flagged is undecided; the DAG is acyclic; the incremental sets partition the network with no reach owned twice; `full_basin` reproduces `accumulate.upstream` exactly; `snap_pos_m` orders every co-reach pair that is not genuinely coincident.

### Chunk 5 — covariate aggregation to catchments

**The archive question is settled.** Two products follow expand-use-delete and two do not: CDL is 24 GB zipped against 50 GB open and AgTile 126 MB against 15 GB, so for those the zip is the artifact and the expansion is scratch; surplus and fertilizer are already LZW float32, barely compress, and stay expanded. `/vsizip/` is not an option for CDL — its members are Deflate, so every windowed read decompresses from the start of a 3 GB member. Nothing about the layout is hard-coded: the archive is found under the product directory and the expansion's name is read from the archive's own listing, because the two archives on disk already disagree about where they put things.

**Fractional coverage, not whole pixels.** A boundary pixel contributes its area share to every catchment it touches, so a "count" is an area in pixel units and a float. Measured against the catchment layer's own `AreaSqKM`: whole-pixel rasterising is off by a median 0.10% and a p90 of 0.94%, 3× supersampling by 0.01%/0.11%, and `exactextract` by 0.00% — while also being the fastest of the three at 0.48 ms per catchment. **Rasters sharing a grid are passed together**, because the coverage is a property of geometry and is computed once for the group: one raster costs 0.48 ms per catchment and three cost 0.64, so all 17 same-grid CDL years go through in one pass rather than eighteen.

**Everything stored is LOCAL** (`cat`), never accumulated. Chunk 6 sums over `basin_reaches.parquet`, which also makes StreamCat's `ws` and Wieczorek's `tot` a free check on our reach sets across all 1.49M catchments rather than only the 388 gauges.

**Daily rasters are still not materialised per COMID**: 1.49M catchments × ~6,600 days is ten billion rows. The catchment × gridMET-cell **weight matrix** is stored instead, built by the same engine with cell ids as the value layer. Chunk 6 collapses it to gauge level *before* touching a date. It is kept rather than consumed because the widget's virtual-pin path runs the same reduction on a basin that does not exist until it is asked for. The 250 m weight matrix the plan also called for was dropped: with the engine at ~1 ms per catchment on that grid, a future 250 m product is a few minutes of recompute, so a 60M-row table would exist only to avoid a cost smaller than reading it.

**Fertilizer is included** alongside surplus. Same source family, identical grid, and a *component* of surplus rather than a copy — surplus nets out crop removal, so a basin with heavy application and heavy uptake reads high on one and low on the other. It is an input to gTREND, which is worth remembering when reading importances.

**Columns added.** COMID tables keyed on `comid`: `crops`, `nutrients`, `agtile`, `dams`, `weights_gridmet`, `coverage` — each stamped with the AOE version it was cut against. `nwm_present` is **not** written: chunk 1's NWM pull still raises `NotImplementedError`.

**Done when:** area closes against `AreaSqKM` per catchment and globally; class columns sum to `n_px`; no gridMET cell is over-claimed; a known basin reproduces its accumulated area from crop pixels alone; the CDL expansion is gone and its zip intact.

### Chunk 6 — assembly and the read layer

**The only chunk that narrows.** `assemble.py` projects `registrations.parquet` through four predicates, each with its evidence already on disk: a nitrate record exists (chunk 1's `MIN_NITRATE_DAYS`), the type drains a surface catchment (`NO_SURFACE_BASIN`, the same set that decides which sites get a basin at all), the snap landed within 500 m, and the identity is decided. Measured: 392 -> 283 gauges (-59 no record, -28 non-surface, -5 far snap, -17 pending merge review).

An undecided identity is NOT valid for modelling and is held back rather than admitted as a singleton -- admitting it is exactly the double-count the merge block's null semantics prevent, since both halves of a true duplicate would enter the cohort claiming to be separate gauges. Reviewing `merge_decisions.csv` is what admits them.

Water is written twice, deliberately: `processed2/water/{gauge_uid}.parquet` holds the merged series so `get_water` is a direct read and a merge is a fact on disk rather than a convention, and `nitrate_daily.parquet` stays pooled so a cross-site fit is one read instead of 283. Overlapping days on a `dual_registration` merge resolve by max, the same rule `io.daily_max` applies within a day.

Then feature assembly; the `src/data` read API; the virtual-pin path.

The read layer must keep the surface everything funnels through — `get_site_ids`, `get_metadata`, `get_location`, `get_water`, `get_basin`, `get_basin_graph`, `get_crops`, `get_surplus`, `get_agtile`, `get_weather`, `get_comid_attributes`, `get_data`, `SiteData`, `build_virtual_site_data`, `update_basin` — and must not import the build layer. `deploy/` and `widget/` violate that by reaching into `_make_basins` privates for on-demand delineation; give them a public entry point.

**Columns added.** `assembly_status`, `n_feature_rows`.

**Done when:** `build_virtual_site_data` reproduces `get_data` for a known site to `rtol=1e-9` — the strongest invariant the old selftest had and the one a rewrite is likeliest to break; every downstream importer resolves; assembly at a random legal pin succeeds.

## Pitfalls

- **`download_flowlines` filters on `"streamorde"` while pynhd returns `StreamOrde`**, so `min_stream_order` has never fired.
- **StreamCat `ws` values are raw upstream sums.** Normalise by `CumAreaKm2` or they encode basin size.
- **`CAT_TILES_Early90s` writes tile 0.0 where NODATA == 100**, ~88% of CONUS.
- **AgTile's mask is NLCD-2016**; never denominate it against CDL crop cells.
- **Fitted-on-target columns**: SPARROW `flux_*`, StreamCat `sw_flux`, Wieczorek `TNLOAD`/`TNLOSS`. 20 sites sit within 100 m of a SPARROW calibration station, one at 0 m.
- **USGS API**: a request's time envelope may not exceed 1100 days (`400 InvalidQuery`); there is NO row cap; `dataretrieval` fans out `API_USGS_CONCURRENT` deep, default 32, which multiplies any threading above it.
- **Co-location is invisible to every holdout scheme.** 36 IWQIS sites sit within 200 m of a USGS gauge; record `nearest_gauge_m` at ingest.
- **NLDI returns the wrong basin sometimes**, wrong by orders of magnitude: a Cape Cod site reporting 6.3 km2 came back with 5,063,593 km2, snapped onto the Mississippi. Guard on the source's reported area and an absolute CONUS cap.
- **Exception swallowing that caches failure as truth** — the old `site_view` wrote an all-NaN distance column and stamped it clean.
- **Non-atomic writes.** Ctrl-C left truncated artifacts the next run treated as done. Write tmp, then replace.
- **NWM chunking.** Site COMIDs occupy 22 of 93 feature chunks, so a narrow pull is 4.2× cheaper. Partition the store by COMID so widening later is an append.

## Human review

**Site merges — a hard gate.** A merge changes the unit of analysis, and a wrong one corrupts basins, graph edges and the record at once, silently, in a way no downstream test attributes back to identity. Volume is small — Iowa had eleven candidates — so per-pair review is affordable. The step emits each candidate with its evidence and proposed branch; a human confirms or overrides; the decision goes to a durable file keyed on the **pair of source ids**, re-read every build. Model it on the basin review panel, not the retired `.preferred_basin_archive.csv`, which was keyed such that it stopped being consulted.

**Basin selection is automatic with a durable manual override, and does NOT gate.** The old build had the right structure and a broken persistence layer: `update_basin` wrote into the same CSV the builder rewrote wholesale, and the archive meant to make overrides durable was keyed on a dotfile nobody wrote and carried a `basin_type` integer whose meaning had since changed, so "zero overrides on the last pass" was true by construction rather than by review. Chunk 4 keeps the structure and fixes the persistence: every delineated gauge has a row in a tracked file that is added to and never rewritten, `auto_method` records what the rule chose, and `decision` overrules it. Nothing blocks — the instrument that can actually judge a delineation is the map widget, and it needs chunk 6's read layer first.

**Site type IS a gate**, contrary to the original plan. Deriving it from source metadata and thresholding at modelling time is not available: for 152 of 392 registrations the source's own type field and the station name say nothing, and the NHD evidence that resolves them (the reach, the waterbody polygon it threads) does not exist until chunk 1 and the snap. So typing moved to chunk 3, where the evidence is, and the ~16 sites whose evidence disagrees go to a human. Human review is rank 0, above every rule.

**No gate removes a site.** Merge resolves identity, basin review picks among delineations, type review names what a thing is; the site is kept either way and chunk 6 decides the cohort.
