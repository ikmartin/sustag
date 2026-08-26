# build2

```
python -m src.build2.run              # chunk0 -> chunk1 -> ... -> chunk6, in order
python -m src.build2.run chunk3       # chunk 3 AND every chunk after it
python -m src.build2.run chunk1 --single --only records
python -m src.build2.run --list
```

`interim2/` holds per-registration artifacts keyed on `site_uid`; `processed2/` holds identity-unified output. Both gitignored.

**Two site tables.** `processed2/registrations.parquet` — one row per registration, 392, never fewer; every chunk widens it and it is build-internal. `processed2/sites.parquet` — one row per trainable physical gauge, 283, written once by chunk 6; **this is the only table a consumer opens.** Design and rationale live in `PLAN.md` — this file is only how to run it.

Shared by every chunk: `config.py` (paths, seed boxes, AOE geometry + stamp), `schema.py` (site-table contract), `io.py` (atomic writes, standard-time day binning), `_panel.py` (the figure machinery all three review panels share), `sources/` (USGS, IWQIS, MPCA adapters).

## chunk0 — seed filter and AOE definition

**After:** `processed2/aoe.parquet` (one stamped geometry, 4,284,001 km², 1.43× the seed boxes) · `processed2/sites.parquet` (392 registrations, 374 seeds) · `interim2/basins_nldi/{uid}.parquet`.

**Requires:** `pipeline_config.toml [region]`, `api-keys.toml`, network.

**Scripts**
- `run_chunk0.py` — orchestrates the four steps below.
- `discover.py` — runs every source adapter over the seed boxes into the site table.
- `seed.py` — marks which registrations may define the AOE; excludes only what metadata proves cannot qualify.
- `basins.py` — one NLDI upstream basin per seed, cached per site, with a plausibility guard.
- `aoe.py` — unions seed basins with the seed boxes, stamps, writes.

**Run:** `python -m src.build2.run chunk0`

**--force:** refetches every NLDI basin and recomputes the geometry — which changes `aoe_stamp()` and invalidates every chunk-1 artifact cut against the old one.

## chunk1 — raw download

**After:** `interim2/water/{native,daily}/{uid}.parquet` · `interim2/network/{flowlines,vaa,catchments}.parquet` · `interim2/weather/grid.parquet` + `gridmet_YYYY-MM.parquet` (228) · `interim2/comid_attrs/{streamcat,wieczorek_*}.parquet` · `sites.parquet` widened with the record block.

**Requires:** chunk 0 (fails immediately without `aoe.parquet`).

**Scripts**
- `run_chunk1.py` — runs records ‖ network ‖ weather concurrently, then attrs, which needs the network.
- `records.py` — water observations at native resolution, every parameter, plus the standard-time daily max. Archives anything with ≥1 nitrate day; the modelling floor is chunk 6's.
- `network.py` — downloads NHDPlus nationally once (7.3 GB → `./cache`) and clips flowlines, VAA and catchments to the AOE; verifies the catchment partition.
- `weather.py` — gridMET over the AOE, ONE FILE PER MONTH, on the native 4 km grid; 267,906 cells, ~19 GB and ~8.5 h for 2008–2026.
- `comid_attrs.py` — StreamCat and Wieczorek subset to the AOE's COMIDs, plus NID. NWM is deferred and raises `NotImplementedError`.

**First run is slow and mostly download**, roughly 12 hours: NHDPlus 7.3 GB compressed (~20 min, then reclaimable), gridMET 228 monthly fetches (~8.5 h), StreamCat over 1.5M COMIDs (~2.6 h). Every step skips what is already on disk, so an interrupted run resumes rather than restarts.

**Run:** `python -m src.build2.run chunk1` · `--only {records,network,weather,attrs}` for one branch · `--serial` if HTTP 429s appear.

**--force:** refetches everything rather than skipping what is on disk; never touches the national rasters, which are manual and read-only here.

## chunk2 — canonicalisation

**After:** `interim2/water/canonical/{uid}.parquet` — one row per standard-time day, one column group per canonical channel.

Native columns are mapped to the fixed channel set in `params.CHANNELS`, converted to canonical units, scrubbed against each channel's own physical range, and binned to seven daily primitives (`mean min max p25 median p75 n_obs`); `amplitude` and `iqr` are derived at read so they cannot drift from their inputs. The engine is `daily.py`, which sits beside `io.py` rather than in this directory because the read layer needs the same reduction on data that was never on disk.

**It precedes identity** because the merge rule has to know which channels a site reports in order to recognise two co-located sensors measuring different things as one node. It is not part of chunk 1 because unit conversion is a derivation, and chunk 1 fetches without deriving.

## chunk3 — snap and site identity

**After:** `registrations.parquet` widened with the snap and merge blocks · `processed2/merge_candidates.csv` (15 judgeable pairs). **Metadata only** — chunk 3 decides what a site is and which registrations are one gauge; materialising any nitrate record belongs to chunk 6.

**Requires:** chunk 1's `interim2/network/{flowlines,catchments,vaa}.parquet` and `interim2/water/daily/`.

**It stops the build.** Chunk 3 creates `chunk3/merge_decisions.csv` pre-filled with every candidate pair (`Merge Pair` numbered to match the panel's rows, `decision` blank), renders the panel, and exits 1 listing the undecided pairs. Fill in `decision` on every row, then `python -m src.build2.run chunk3` to resume — that re-runs chunk 3 and everything after it.

**Scripts**
- `run_chunk3.py` — snap, then site type, then identity, then the review gates.
- `site_type.py` — evidence-ranked typing; also owns `NO_SURFACE_BASIN` and the metadata prior chunk 0 uses.
- `snap.py` — nearest-flowline snap plus the containing catchment; both ids kept, `comid_agree` flags the 25 sites where they differ (near a divide).
- `identity.py` — pairs sharing a snapped COMID become merge candidates; records decide between `sequential`, `dual_registration` (r ≥ 0.80 and near-zero median difference) and `distinct`.
- `merge_review.py` — the human review panel (`--review`): one row per pair, map + both records + evidence.

**Nothing merges without a recorded decision.** Candidates land in `merge_candidates.csv` with their evidence; a human fills in `merge_decisions.csv` (tracked in git, keyed on the sorted uid pair). A first run reports 15 pending and merges nothing — that is the designed behaviour. Sites in an undecided pair carry a NULL group rather than an optimistic singleton.

**Two human gates, one stop.** Chunk 3 stops the build while any merge pair OR any ambiguous site type is undecided, rendering both panels and listing both queues. Currently 19 pairs + 16 types, all decided.

**`snap_pos_m` orders two gauges on one reach.** Every other positional attribute — `pathlength_km`, `totma_d`, `stream_order` — belongs to the REACH, so co-located sites carry identical values and nothing can tell which is upstream. `LineString.project` gives distance along the reach from its upstream end (NHDPlus digitises in flow direction; verified on this layer), which is what keeps the eventual gauge graph acyclic. 58 sites sit on 27 shared reaches; 31 of 36 co-reach pairs are ordered by it and 5 are genuinely coincident.

`site-type-review/` mirrors `merge-review/`: an ambiguous-only panel (map with the **waterbody polygon** | nitrate series | evidence) and `site_type_decisions.csv` where `decision` takes a type from the vocabulary. Only 16 of 392 are ambiguous — a conflict between the source's own type and the NHD, a fall-through default the NHD resolved, or no evidence at all.

**Reviewing merges:** `--review` renders `chunk3/merge-review/merge_review.png` (one row per pair: catchments and reaches | both records on one timeline | evidence) plus `merge_review_stats.csv` with all 46 numbers. Sequential pairs sort first — they have no overlap, so no correlation exists, and they are judged on `seasonal r` (monthly-climatology correlation) and `KS distance` (value-distribution distance), both of which need no shared days. The PNG is gitignored; the stats CSV is tracked.

**Run:** `python -m src.build2.run chunk3` · `--review` to also render the panel.

**--force:** re-snaps against the current network layers. It re-reads `merge_decisions.csv` and never rewrites or discards it.

## chunk4 — the sensor graph and basins

**After:** `processed2/basins.parquet` (one selected basin per physical gauge, with geometry and flags) · `processed2/basin_reaches.parquet` (the upstream COMID set behind each selection — what chunk 5's covariates are summed over) · `interim2/basins/{basin0,basin1,basin2,basin3}/{uid}.parquet` (every candidate, cached).

**The graph half computes first and shares the basins' walk:** labelling each reach with the first node downstream of it yields both the node DAG (`n_upstream_nodes`, `n_downstream_nodes`, `flow_dist_nearest_node_km`) and the incremental catchment sets. One pass, because splitting them means walking the network twice. The closure over upstream edges is not a full basin — it agrees with `accumulate.upstream` on 319 of 350 nodes and falls short at divergences, where a partition must assign one owner to a reach whose water goes two ways — so basins keep using `accumulate.upstream` and the pass contributes the DAG.

**Requires:** chunk 3 (the merge decisions) and chunk 1's network layers. **One row per physical gauge**, not per registration — a merged pair is delineated once, from the canonical member's snap.

**Four methods, and an auto rule that is a precedence rather than a score.**

| | how | selectable by the auto rule |
|---|---|---|
| `basin0` | NLDI `nwissite` — the authority's own polygon, USGS only | yes, first |
| `basin1` | local upstream accumulation from the snapped `comid` | yes, the default |
| `basin2` | the same accumulation from `catchment_comid` | only by a human |
| `basin3` | D8 flood-fill over HydroSHEDS 15s | only by a human |

**Accumulation is local, not NLDI.** The walk is `fromnode`/`tonode` over `vaa.parquet` — not `terminalpa`+`hydroseq`, which admits parallel branches that are not upstream — and it reproduces NHDPlus's own `totdasqkm` on **388/388** gauges. The four that report `no_drainage_reach` are the estuaries, which snap to `Coastline`; walking upstream from a coastline collects every river that reaches the sea along it (124,184 reaches, 251,225 km²), which is exactly where NLDI's 5M km² answers come from.

**Scripts**
- `accumulate.py` — the flow network, indexed for upstream traversal; topology and area only.
- `basins.py` — the four methods, the per-gauge cache, the auto rule, the decisions and the selection.
- `flags.py` — six advisory checks. Five compare a basin against another measurement of itself; `flag_river_capture` is the only one that looks outside, and it replaces the old proximity flag that fired on 20 of 116 sites including every legitimate mainstem gauge.
- `basin_review.py` — the panel: per row, the site at reach scale, the candidate basins at basin scale, and the evidence.

**It does NOT stop the build.** Every delineated gauge gets a row in `basin-review/basin_decisions.csv` with the auto rule's answer in `auto_method` and a blank `decision`; writing `basin0|basin1|basin2|basin3` there overrules the rule for that gauge on the next run, and `selection_mode` reports `auto` or `manual` per gauge. The file is added to and never rewritten. In-depth delineation review belongs to the map widget once chunk 6's read layer exists; `--review` renders a stopgap panel of the flagged gauges in the meantime.

**Corroboration for the sites with no authority.** 198 gauges have a `basin0` to be checked against; the other 190 are IWQIS, MPCA and USGS registrations NLDI omits. `n_corroborations` / `corroborated_by` count the INDEPENDENT methods confirming each basin — basin0, basin3 (a raster sharing no code or data with NHDPlus), and basin2 only where `comid_agree` is False, since an identical COMID makes it the same computation. Of the 170 gauges without an authority basin, 141 are corroborated by another method.

**Run:** `python -m src.build2.run chunk4` · `--force` rebuilds every candidate · `--review` also renders the panel · `--skip-basin0` avoids the network · `--skip-basin3` avoids the raster.

## chunk5 — covariate aggregation to catchments

**After:** `processed2/comid_features/{crops,nutrients,agtile,dams,weights_gridmet,coverage}.parquet`, each stamped with the AOE it was cut against.

**Requires:** chunk 1's network and gridMET grid, and the national rasters. Needs no basin — it is keyed on COMID, and which catchments belong to which gauge is chunk 6's question.

**Fractional coverage, everywhere.** A pixel on a boundary contributes its area share to every catchment it touches; it does not pick one. So every "count" is an area in pixel units and therefore a float — `corn_px = 1834.62` is 1834.62 pixels' worth of corn. Measured against the catchment layer's own `AreaSqKM`, whole-pixel assignment is off by a median 0.10% and a p90 of 0.94%; this is off by 0.00%.

**Local values only.** Everything here is the value over a catchment's own polygon. Accumulating upstream is chunk 6's job, over `basin_reaches.parquet` — the same split StreamCat's `cat`/`ws` and Wieczorek's `cat`/`tot` already use, which makes their accumulated columns a free check on ours.

**Scripts**
- `zonal.py` — the engine: `exactextract` over spatially ordered batches, checkpointed per batch. Rasters sharing a grid are passed **together**, because the coverage fractions are a property of geometry and are computed once for the group — all 17 same-grid CDL years in one pass, not eighteen passes.
- `archive.py` — expand, use, delete, for `crops` and `agtile` only; surplus and fertilizer stay expanded. Nothing about the layout is hard-coded: the archive is found under the product directory and the expansion's name is read from its own listing.
- `cdl_classes.py` — **the CDL filter, and the file to edit when classes change.** Ported from `src/data/cdl_legend.py`; 256 codes into 8 agronomic classes.
- `crops.py` · `nutrients.py` (surplus + fertilizer, 250 m) · `agtile.py` (ESRI:102003, its own pass) · `dams.py` (NID points into containing catchments) · `weights.py`.

**Daily weather is never stored per COMID** — 1.49M catchments × 6,600 days is ten billion rows. `weights_gridmet.parquet` stores the geometry instead: which cells each catchment overlaps and by how much. Chunk 6 collapses those to gauge level *before* touching a date, then reduces the cells' days to one row per gauge-day. The matrix is kept rather than consumed because the widget's virtual-pin path runs the same reduction on a basin that does not exist until it is asked for.

**No human gate.** Chunks 2 and 3 stop for a reviewer because they decide identity and delineation. Chunk 5 measures, and every number is checkable against the catchment's own area.

**Run:** `python -m src.build2.run chunk5` · `--only crops,weights` for a subset · `--limit N` for a smoke test · `--force` ignores the checkpoints.

## chunk6 — assembly

**After:** `processed2/sites.parquet` (283 trainable gauges, 37 columns) · `processed2/water/{gauge_uid}.parquet` (283, merged series) · `processed2/water/nitrate_daily.parquet` (412,821 site-days, pooled).

**Requires:** chunks 2, 3 and 4. Runs last on purpose — it is the only chunk that narrows, and every predicate it applies needs its evidence already on disk. **Gated:** it refuses to assemble until `processed2/basins.parquet` (chunk 4) and `processed2/comid_features/` (chunk 5) exist, because a gauge with no basin has no covariates and the resulting table would read as a finished cohort without being one.

**The funnel:** 392 registrations → 283 gauges. −59 no nitrate record, −28 no surface drainage, −5 snap beyond 500 m, −17 merge decision pending. An undecided identity is held back rather than admitted as a singleton; reviewing `merge_decisions.csv` admits it.

**Run:** `python -m src.build2.run chunk6`

## One-time migrations

Done, recorded so a stale checkout is recognisable. Water artifacts moved out of `processed2/` into `interim2/water/{native,daily}/` — they are per-registration, so they belong on the side of the tree where identity is not yet settled.
