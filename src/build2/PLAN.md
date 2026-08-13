# New data pipeline — plan

A clean rewrite of the build, not a migration. `src/build` is **reference material only** — read it for a trap or a formula, do not port it, do not run or diff against it.

The **data tree stays where it is**: `build2` reads and writes `src/data/`, reusing what is already downloaded. There is no `data2`. The read layer in `src/data/` evolves in place in chunk 4.

## What changes, and why

**Scope is one declaration everything inherits.** The AOE is the union of three regions frozen in `pipeline_config.toml [region]` — `cornbelt_bbox`, `ne_bbox`, `mississippi_bbox` — derived from the reach network within 250 km of flow distance of a nitrate sensor. Every source is collected inside it, for the declared years (2008–2026 for gridded dynamic data; full span for annual per-COMID tables; none for static).

The AOE is stored as a **geometry**; the three boxes are its **bbox cover**. Sources taking a polygon (`pynhd`, `pygridmet`) get the geometry; the bbox-only ones — Water Quality Portal, CropScape, the USGS OGC client — get the cover, then results are clipped back. `cornbelt_bbox` and `mississippi_bbox` overlap near lon −92.16..−89.14, lat 34.92..35.88, so consumers union rather than sum.

**The build never drops a SITE.** No successor to `filter_sites`, and no stage may remove a site for any reason — short record, poor coverage, oversized basin, wrong type, failed snap. Every stage only ever *widens* the site table. Site selection happens after the build, as predicates over that table: `graph_node`, `train_label`, `claim_set` are queries at modelling time, never columns the build writes.

Throwing away **data** is fine and expected — clip rasters, keep only declared years, subset a source's variables, drop an attribute theme. Only sites are protected.

The reason is mechanical, not stylistic. A site filter needs evidence, and evidence appears at different depths — row counts at ingest, cell coverage once basins × grid exist, graph degree once the graph exists — so a filter in the build reaches forward for an artifact a later stage produces. That forward reach caused the old 116↔123 cohort oscillation, and it made dropped sites permanently *unjudgeable*, because their evidence was never built. Build for the candidate set; judge afterwards.

**Storage.** Retaining every download does not fit; clip → aggregate → discard for regenerable caches lands the new work at ~57 GB. Two deliberate exceptions, already on disk and not counted against the budget: **CDL national** (50 GB, genuinely CONUS and reusable at any extent) and the gTREND surplus/fertiliser/manure rasters. **`gridMET_raw` was deleted** — it was 28,417 hash-named per-request NetCDF fragments scoped to the *old* bbox, with no coverage of the Chesapeake or Mississippi regions, and HyRiver keys its cache on a request hash so even the overlap would miss. It had to be refetched regardless.

## Data sources

**Labels** — USGS continuous nitrate under all three pcodes `99133 + 99137 + 00630` (`99133` alone returns zero Nebraska sites); IWQIS; Water Quality Portal discrete nitrate by state (needs >30 s timeout); Heidelberg NCWQR; CAMELS-Chem.

**Graph nodes** — every NWIS continuous series in the AOE, querying all pcodes per family: discharge, stage, water temp, spec cond, turbidity, DO, pH, groundwater, precipitation, soil moisture/temp.

**Network** — NHDPlus V2 flowlines via `pynhd`; VAA via `pynhd.nhdplus_vaa()` (not the HydroShare path, 301→405); NWM `feature_id`/`latitude`/`longitude`/`order` arrays as the COMID→coordinate table; HydroSHEDS 15s D8 re-clipped to the AOE.

**COMID attributes** — StreamCat, all 168 metrics minus `sw_flux`; the Wieczorek release via `pynhd.nhdplus_attrs_s3()` (14,139 attributes; ScienceBase times out, S3 returns in 3 s) taking Soils, BMP, Hydrologic Modifications, Chemical, Land Cover, Geology, Topographic, excluding Climate Water Balance as redundant against gridMET; National Inventory of Dams.

**Rasters** — gridMET; CDL; gTREND surplus, **fertiliser (on disk, used by nothing)** and manure; AgTile. `precip_in_1d` is dropped: its Iowa-only footprint makes it a covert state indicator.

**Modelled** — NWM v3.0 retrospective daily aggregates, COMID-keyed, at site COMIDs first.

## Chunks

Four phases. Each widens the site table with named columns, and each is testable on its own.

### Chunk 1 — sites, records, identity

AOE geometry and bbox cover; discovery across every source; record download keeping all parameters; identity resolution; creation of the site table.

Identity cannot be deferred — basins, graph edges and record concatenation all key on it. Merge is near-coincidence **plus** agreement: no temporal overlap means sequential re-registration; overlap with r ≳ 0.95 means simultaneous dual registration; overlap with poor agreement means distinct sensors. Use connected components, not pairwise merges, or the result is order-dependent. **Every merge is human-reviewed** — see Human review.

**Columns added.** Identity: `site_uid`, `source`, `source_site_id`, `agency`, `physical_gauge_group`, `is_group_canonical`, `merge_rule`, `merge_sep_m`, `merge_shared_days`, `merge_r`, `merge_median_diff`, `merge_seam_date`, `merge_decided_by`. Location: `lat`, `lon`, `datum`, `state`, `county`, `huc`. Source description: `station_name`, `source_drainage_area_km2`, `altitude`, `site_type_raw`. Record: `first_day`, `last_day`, `n_nitrate_days`, `longest_gap_d`, `regime` ∈ {continuous, grab}, `pcodes`, `params_available`, `reported_units`, `detection_limit`. Type: `site_type`, `site_type_source`. Co-location: `nearest_nitrate_sensor_m`, `nearest_gauge_m`.

**Done when:** one row per registration with all of the above populated or explicitly null; record parquets carry every reported parameter; the USGS census reproduces inside the AOE; the merge review artifact lists every candidate with its evidence; no site deleted.

### Chunk 2 — network, snapping, basins

Flowlines and VAA over the AOE; COMID snap; basins by **accumulating upstream NHDPlus catchments** rather than per-site NLDI calls — local computation, and it makes basin extent agree with StreamCat's `ws` accumulation by construction. Basins are built per `physical_gauge_group`, not per registration.

**Columns added.** Snap: `comid`, `snap_dist_m`, `snap_status`, `stream_order`, `stream_level`, `terminal_path`, `pathlength_km`, `totma_d`. Basin: `basin_area_km2`, `basin_method`, `basin_status`, `d8_area_km2`, `area_agreement_ratio`. Graph: `n_upstream_sites`, `n_downstream_sites`, `flow_dist_nearest_site_km`, `n_reaches_within_250km`.

**Done when:** every site has a COMID, snap distance and basin — or a recorded reason it has none, keeping its row and gaining a flag, since engineered channels will fail to snap as three Florida canals did; the containment graph is a DAG (`nx.is_directed_acyclic_graph`); area ordering holds on every containment edge; every edge has a finite flow distance or a named reason.

### Chunk 3 — covariates

`grid_global` with a provenance stamp; rasters clipped to the AOE with regenerable sources discarded; COMID attribute tables; NWM daily aggregates.

`global_node_id` gets its stamp at creation and every covariate table carries it; joins refuse on mismatch. This is the largest blast radius in the old pipeline — a bare row counter with four tables keyed on it, and changing the region is exactly what renumbers it.

**Columns added.** On the site table: `cell_coverage` (Σ `frac_cell_in_basin`), `n_grid_cells`, `covariate_years`, `nwm_present`. On covariate tables: `grid_stamp` alongside `global_node_id`; COMID tables keyed on `comid`.

**Done when:** every covariate table joins `grid_global` on a matching stamp; per-COMID tables cover the in-AOE COMID set with gaps reported; total disk under budget; a cold rebuild reproduces the same `global_node_id` assignment.

### Chunk 4 — assembly and the read layer

Per-site grids; feature assembly; the `src/data` read API; the virtual-pin path.

The read layer must keep the surface everything funnels through — `get_site_ids`, `get_metadata`, `get_location`, `get_water`, `get_basin`, `get_basin_graph`, `get_grid`, `get_crops`, `get_surplus`, `get_agtile`, `get_weather`, `get_comid_attributes`, `get_data`, `SiteData`, `build_virtual_site_data`, `update_basin` — and must not import the build layer. `deploy/` and `widget/` currently violate that by reaching into `_make_basins` private functions for on-demand delineation; give them a public entry point.

**Columns added.** `has_grid`, `assembly_status`, `n_feature_rows`.

**Done when:** `build_virtual_site_data` reproduces `get_data` for a known site to `rtol=1e-9` — the strongest invariant the old selftest had and the one a rewrite is likeliest to break; every downstream importer resolves; assembling features at a random legal pin succeeds.

## Pitfalls

- **`global_node_id` has no version tag.** Stamp at creation; refuse mismatched joins.
- **`download_flowlines` filters on `"streamorde"` while pynhd returns `StreamOrde`**, so `min_stream_order` has never fired.
- **WQP returns the same measurement twice**, `mg/l as N` and `mg/l asNO3`, differing by 4.4269. Deduplicate on the harmonised value or the median reads 24.05 instead of 5.44.
- **StreamCat `ws` values are raw upstream sums.** Normalise by `CumAreaKm2` or they encode basin size.
- **`CAT_TILES_Early90s` writes tile 0.0 where NODATA == 100**, true for ~88% of CONUS.
- **AgTile's mask is NLCD-2016**; never denominate it against CDL crop cells.
- **Fitted-on-target columns**: SPARROW `flux_*`, StreamCat `sw_flux`, Wieczorek `TNLOAD`/`TNLOSS`. 20 sites sit within 100 m of a SPARROW calibration station, one at 0 m.
- **Co-location is invisible to every holdout scheme.** 36 IWQIS sites are within 200 m of a USGS gauge; record `nearest_gauge_m` at ingest, it cannot be recovered later.
- **Exception swallowing that caches failure as truth** — the old `site_view` wrote an all-NaN distance column and stamped it clean.
- **Non-atomic writes.** Ctrl-C left truncated artifacts the next run treated as done. Write tmp, then replace.
- **NWM chunking.** Site COMIDs occupy 22 of 93 feature chunks, so a narrow pull is 4.2× cheaper. Write a COMID-partitioned store so widening later is an append.

## Human review

**Site merges — a hard gate.** A merge changes the unit of analysis, and a wrong one corrupts basins, graph edges and the concatenated record at once, silently, in a way no downstream test attributes back to identity. Volume is small — Iowa had eleven candidate pairs — so per-pair review is affordable. The step emits every candidate with its evidence and proposed branch; a human confirms or overrides; the decision is written to a durable file keyed on the **pair of source ids** and re-read on every build. Nothing merges without a recorded decision. Model it on the basin review panel, which works — not on the retired `.preferred_basin_archive.csv`, which was keyed such that it silently stopped being consulted.

**Basin selection** is the second gate, now nearly ceremonial: the automatic rule needed zero overrides on the last pass. Keep the artifact and the override path; a run needing many overrides is evidence of a delineation bug, not normal operation.

**Site type is not a gate.** Derive it from source metadata, record it with its provenance, threshold it at modelling time.

Neither gate removes a site. Merge resolves identity; basin review picks among candidate delineations for a site kept either way.
