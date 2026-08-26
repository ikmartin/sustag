# Rebuild the data pipeline to the data_chapterv2 spec — `data/build`, `data/access`, review panel, basin editor

## Context

`notes/book/data_chapterv2.md` is the binding specification, rewritten 2026-08-25 under a new design philosophy: the pipeline publishes *facts* (sensor records, geometry, the flow network, catchment covariates) and refuses *preferences* (no graph, no basins, no class collapse, no record floors). A six-agent audit of `src/build3` against the chapter established: the evidence engines, halt machinery, ledger kit, snap/accumulate/zonal machinery, and detector battery survive nearly verbatim; the genuinely new mechanisms are the SNR id ledger, the proximity table + coordinate precision, the type gate, source sentinels + tallies, four-state fetch accounting, criterion versioning, CDL long format + `remap`, the treatment-facilities source, the access surface, the review panel, and the basin editor. The old access layer (`src/data/access.py`, generation 1) and every consumer of it are deprecated wholesale — nothing external reads build3's stores, so there is no compatibility to preserve.

Decisions fixed during Phase 1 (do not re-litigate): new code lives at its FINAL home from day one — `data/build` (pipeline + review panel), `data/access` (access layer), `tools/basin_editor` (modeler tool), `src/gnn1` (graph demo); the stores eventually land in `data/stores/` but stay physically under `src/data/` behind config constants until the build is complete; **migrate nothing** from build3's decisions, derived, or published stores — fresh empty decision sheets, populated through the review panel; the existing `src/data/raw/` and `src/data/acquired/` archives are ADOPTED in place (re-fetching ~6 GB of API pulls is waste; the snapshot adoption mechanism exists for exactly this); build3 stays untouched on disk as reference until Isaac deletes it.

House rules that bind every phase: **full pipeline runs are AUTHORIZED for this build** (Isaac's explicit grant, scoped to this rebuild, since nearly all raw data is already on disk) — every full run is announced in chat when it starts; **gated decisions remain Isaac's**, made through the review panel once it exists; git is not a safety net (large uncommitted work in tree); no hard-wrapped prose in docstrings/docs; docstrings describe current state only; ledger decision cells take literal values; don't offer to commit.

## Final directory layout

```
data/                          # the data layer (code); repo root on sys.path, so imports are data.build / data.access
  build/                       # the pipeline: build(snapshots, parameters, decisions) -> artifacts
    config.py  registry.py  contracts.py  ids.py  io.py  ledger.py  run.py  verify.py
    acquire/   {base,usgs,iwqis,mpca,facilities}.py  discovery.py  census.py  seed.py  seed_basins.py
               records.py  reconcile.py  snapshot.py  network.py  weather.py  attributes.py  nwm.py
    derive/    extent.py  reduction.py  canonical.py  geometry.py  snap.py  identity.py  assemble.py
               site_type.py  zonal.py  products.py  archive.py
    publish/   publish.py  quality.py
    review_panel/  app.py  backend.py  plots.py  map.py  tabs/{merge,site_type,composed,quality,drift,gaps,exclusions}.py
    decisions/     merge.csv site_type.csv composed.csv quality.csv drift.csv registry_gaps.csv exclusions.csv ids.csv  (fresh, tracked)
    parameters/    scope.toml  canary_roster.csv  sensor_comids.txt
  access/                      # strictly read-only; never imports data.build
    api.py                     # get_sensors / get_record / get_proximity / get_network / get_covariates / get_weather / profiles
    machinery/                 # pure functions taking layers as arguments: snap.py  accumulate.py  aggregate.py  d8.py  remap.py
    cache.py                   # pooled-read cache (replaces nitrate_daily.parquet)
  stores/                      # FINAL home of raw/acquired/snapshots/derived/published — created at migration, not before
tools/
  basin_editor/                # PoC Dash app; imports data.access ONLY (+ sanctioned live NLDI call); README + example case
src/gnn1/                      # edge-composition/charge-set machinery as an access-layer graph demo
tests/data/                    # new test tree mirroring data/: tests/data/build/... tests/data/access/...
```

Interim store constants in `data/build/config.py` (flipped once at migration, R11): `RAW = src/data/raw`, `ACQUIRED = src/data/acquired`, `SNAPSHOTS/DERIVED/PUBLISHED = data/stores/{snapshots,derived,published}` — build4's own outputs go to their final home immediately (they're fresh anyway); only the heavy immovable archives stay in `src/data` until the end. The one asymmetry to respect: new acquisitions (facilities, full waterbodies, USGS GeoJSON) append into the EXISTING `src/data/acquired/` so the archive stays one thing.

## Disposition of build3 (what ports, what changes, what dies)

**Port near-verbatim** (copy, fix imports, unwrap any hard-wrapped docstrings encountered): `io.py` (delete dead `local_day` or wire it), `acquire/base.py`, `acquire/snapshot.py` (+ wire `cut()`), `acquire/seed.py`, `acquire/weather.py`, `acquire/attributes.py`, `acquire/nwm.py` (input file renamed `sensor_comids.txt`, written from snapped record-bearing sensors), `derive/extent.py`, `derive/reduction.py` (+ tally schema), `derive/zonal.py`, `derive/archive.py`, `derive/snap.py` (+ failure vocabulary: `ok | far | no_location`), the USGS token/fetch machinery inside `usgs.py` (rotation ring, 1000-day windows, dv batching+bisection, `_valid_id`, `_active_window`, `_shape_dv`), `records.py`'s shard_of/run_sharded/revision-merge, identity's evidence engine (`_agreement`, thresholds, `_strong_shared`, contradiction halt, pending-NULL, `order_for_review`), sites' `merge_record`/`_merge_channel`/reach-disagreement halt/persist-before-gate, quality's parse/propose/impossible_runs/robust-z/two-tier verdicts, publish's `apply_quality_masks`/`_over_threshold`/`republished_spans`/partition, `panel.py`'s data half (`catchments`, `context_reaches`, `project`, `daily_record`) into `review_panel/map.py`+`plots.py`, `merge_review.pair_stats`/`_doy_r`/`_ks` as review-panel evidence.

**Adapt, with the specific change** — the load-bearing list:

| module | change |
|---|---|
| `registry.py` | new ranges (discharge [−1e4, 5e4]; gage_height [−1e3, 1e4]; water_temp hi 60; spec_cond [−5, 2e5]; diss_oxy [−1, 30]; turbidities/fchl/fpc/fdom negative lo per chapter table); per-source `sentinels` in native units ({−999999 all}, {999.99, −99999 MPCA}) hashed into `fingerprint()`; import-time closure `clamp_negatives ⇒ lo < 0`; two canary channels `gw_level` (72019) and `precip` (00045) at `depth="canary"`; `extract()` masks sentinels per native column BEFORE combine and returns the count |
| `contracts.py` | keep uid helpers, Block ownership, Stamps (+ registry member), `update_block`, `conform/validate_sensors`; relax lat/lon-required for coordinate-sentinel rows; add `coord_dp_lat/lon`, `snr_id` (mint block, owner a2), 4-state fetch block; DELETE all site/node/edge contracts and `S{comid}-{pos}` minting |
| `acquire/usgs.py` | archive the raw GeoJSON of every monitoring-locations response into `acquired/metadata/usgs_geojson/` (the coord_dp source); fix the empty-nitrate-kills-all-channels return; stale concurrency docstring |
| `acquire/iwqis.py` | coord_dp from registry text; −99.0 coordinate sentinel → registered with null coords, never box-dropped; census clip to AOE not boxes |
| `acquire/mpca.py` | coord_dp via `dtype=str`; DELETE the fetch-time `_SENTINEL` replacement (moves to registry; natives re-materialise from raw ZIPs) |
| `acquire/discovery.py` | delete `non_surface_no_nitrate` + `_NON_SURFACE_*`; window screen = `declared end < config.COLLECTION_START` only (a date, `2008-01-01`, in scope.toml), per-adapter `declares_spans` flag instead of hardcoded USGS; rename `nearest_nitrate_sensor_m` honestly |
| `acquire/census.py` | mint SNR ids (call `ids.mint_new`) as the last census step; canary + namespace verify checks |
| `acquire/records.py` | four-state accounting written per shard to `fetch_status.shard{i}.parquet`, merged+accumulated by the parent (fixes the clobber); `source_empty` durable with a marker, `attempt_failed` retried; outcomes joined onto the sensors table via `update_block` at a3 end; a3 partition check `registered = skipped + cached + source_empty + attempt_failed` |
| `acquire/reconcile.py` | drift classified on manifest sha256 + a per-file content probe (which observation days changed vs the revision window), not mtime; `schema_changed` detected from parquet footers; adoption amnesty reads the CHILD's notes; one revision-window constant |
| `acquire/snapshot.py` | `bulk.py`/a3 ends with `snapshot.cut()` — the drift path goes live |
| `derive/canonical.py` | persist per-(sensor, channel) scrub tallies to `derived/scrub_tallies.parquet`; tally invariant check |
| `derive/identity.py` | candidates read from the proximity table; the two-tier type gate (material sets `{well, groundwater}`, `{atmosphere}` never surface; cross-surface-type queues with mismatch displayed; `unknown` waits); records = filter (no row); uniform `min_sep_m ≤ 100`, >50 m flagged; 4-branch ladder (weak_evidence deleted); `comid_a/b/same_comid` on candidates |
| `derive/site_type.py` | delete the default-to-stream (untyped → `unknown` → queue); keep classifier, patterns, question() |
| `derive/assemble.py` (from `sites.py`) | SNR-keyed record assembly; canonical ranking WITHOUT human override; sweep logic reworked for stable ids (only dissolved bundles retire files) |
| `derive/products.py` | CDL long format `(comid, year, code, frac)` + coverage kept beside it; global cell-conservation closure on the weight matrix (new); structures snap (dams + facilities → `(comid, snap_pos_m)`) |
| `publish/publish.py` | partition + masks keep; store keyed `SNR-XXXX`; DELETE graph/basins/pooled publication and the `REQUIRES` on them; leak scans ×4 (sentinel + out-of-range are new), audited against LEDGERS not derived CSVs; sensors metadata gains the fetch block |
| `publish/quality.py` | `range_violation` consumes pre-scrub out-of-range/sentinel counts; retire `spike_rate`; extend rule verdicts to standard channels with per-site read-once + column pruning; absolute thresholds stay nitrate-calibrated and standard channels use the cohort-relative signals only (the honest subset) until calibrated |
| `ledger.py` | `Ledger.decide(key, decision, **editables)` single-row atomic write with mtime guard; `read_all()`; `criterion_version` column stamped by sync, decided-under-old-version treated as pending-for-confirmation; fix `ALL` to include every ledger |
| `run.py` / `verify.py` | stage map A1–A4, D1 extent, D2 canonical, D3 geometry, D4 identity, D5 covariates, P1; re-register the accumulate-vs-VAA check under `data/access/machinery` so it survives |

**New modules**: `ids.py` (base-36 codec `SNR-0001`.., append-only ledger keyed on uid, batch mint sorted `(lat, lon, uid)`, bijection/append-only/format/half-full checks); `derive/geometry.py` (D3: coord_dp backfill from archived source text — IWQIS/MPCA raw files, USGS archived GeoJSON — uncertainty rectangles, the 2 km proximity table with `sep_m/min_sep_m/max_sep_m/comid_a/comid_b/same_comid`); `acquire/facilities.py` (ICIS-NPDES `NPDES_OUTFALLS_LAYER.csv` weekly pull AOE-clipped + DMR loading-tool annual nitrogen + CWNS POTW attributes, joined on NPDES permit number; snapshotted like all acquisition); `acquire/network.py` gains the FULL waterbody layer (the one-time ~7 GB bulk route) and publishes network layers as-provided; dams become a snapped POINT layer beside the two comid tables.

**Delete (not ported)**: `derive/basins.py` (except `Catchments.dissolve`, `dist_to_basin_km`, `river_capture` → basin-editor/access), `basins_graphs.py`, `graph.py` (except `Downstream.next()` → gnn1), `basin_review.py`, `dams.py`'s edge-feeding half (its NID→catchment table stays), all PNG panel renderers, `_panel.py`, node/edge contracts, `publish_pooled/graph_and_basins`, the basins ledger.

**One-time scripts** (in `data/build/scripts/`, run once, kept): MPCA native re-materialisation from raw ZIPs; coord_dp backfill; the HydroWASTE cross-check — join HydroWASTE v1.0 outfalls (reference/water-treatment-facilities/) to our snapped ICIS outfalls within 1 km, report separation distribution and disagreements >500 m as a facilities-snapping audit, written to `notes/facilities-snapping-report.md`.

## Build phases

Each phase ends with its pytest suite green, its verify checks registered, and its full-store pass run (announced in chat), with expected outcomes stated below so the run confirms or refutes them. Sequencing consequence of fresh empty ledgers: full d4/p1 runs before R7 are EXPECTED to halt at their gates naming the undecided rows — that halt is the correct observable state, not a failure; after R7 Isaac reviews through the panel and the passes re-run to completion.

**R0 — kernel.** `data/build/{config,io,registry,contracts,ids,ledger}.py` + `tests/data/build/`. The registry lands with ALL chapter changes at once (one fingerprint bump, before any canonical exists to stale). Done when: ported test_io/test_registry/test_ledger green plus new tests — clamp closure raises on a lo=0 clamp channel; sentinel extraction pre-combine; SNR codec round-trips; id ledger appends-only and refuses reuse; `Ledger.decide` writes one row atomically and criterion-version requeue works.

**R1 — acquisition.** All adapters + discovery/census/records/reconcile/snapshot + facilities + full waterbodies + GeoJSON archiving. Cut the ADOPTION snapshot over `src/data/{raw,acquired}` (notes `adoption: build4 adopts the build3 archive ...`). Done when: unit tests green (discovery skips, span screen, canary punch-through incl. the two new channels, accounting partition, shard-merge); smoke = one cached sensor re-fetch + one facilities pull limited to one state, then the full `a2`→`a3`→`a4` chain (announced); expected: ~20,226+ registrations each carrying an SNR id, 530 previously-skipped non-surface sensors now fetch-eligible, fetch partition sums exactly, a4 names WQS0127 and no junk ids, drift path reports a real diff for the first time.

**R2 — canonicalisation.** D2 with sentinels + persisted tallies; MPCA re-materialisation script first. Done when: no-scrub-roundtrip, DV-null, tally-invariant, fingerprint-stamp tests green; smoke on ~20 sensors spanning all three sources shows sentinel counts land where the audit measured them (e.g. `MPCA:41043001` turbidity n_sentinel ≈ 49,531), then the full d2 recompute (announced — every canonical legitimately stales).

**R3 — geometry.** coord_dp backfill, snap port, proximity table. Done when: d3 verify green — table symmetric and complete vs direct recompute (`--deep`), `min ≤ sep ≤ max` rowwise, dp present wherever source text exists, −99.0 rows have no geometry and no pairs; the seven 2-dp IWQIS sensors show ~693 m half-widths.

**R4 — identity.** Gate + ladder + assembly + composed review + FRESH empty ledgers minted (merge, site_type, composed, exclusions). Done when: identity tests green (gate tiers incl. cross-surface-type queueing, ladder order, contradiction halt, SNR-keyed winner-per-span provenance); the full d4 run (announced) halts at its gates with queue sizes matching Phase-1 measurements (~126 record-bearing gated pairs; 91 material-boundary auto-rejects; the 47 long-distance sequential ghosts absent).

**R5 — covariates + structures.** CDL long, weight matrix + global conservation check, dams/facilities snapped points, attributes, NWM re-scope. Done when: per-grid closures + the new global check green; a spot catchment's long-format codes sum to its coverage; dams and facilities carry `(comid, snap_pos_m)`; HydroWASTE cross-check report written.

**R6 — publication.** Partition, masks, SNR-keyed store, all four leak scans. Done when: partition exact on the full p1 run (announced; pre-R7 it publishes what no gate holds and halts naming the rest); deep leak scans find zero sentinel/out-of-range/in-span/over-ceiling values; published sensors table answers "why does this sensor have no record" from one row.

**R7 — review panel.** Dash app (`python -m data.build.review_panel`): one tab per ledger with undecided/all views and counts; radio verdicts from `Ledger.vocabulary`; literal-value fields validated on blur by `parse_spans`/`parse_threshold`; `Ledger.decide` write-per-decision with status flash; keybindings via dash-extensions (j/k prev-next, verdict keys, u = clear-to-blank); plotly record plots with cursor readout, span shading, worst-segment (port of `quality_review.html()`); dash-leaflet map with stated positions, uncertainty rectangles, snapped reach + 3-hop context, snap-residual context, 2 km proximity neighbors; counterfactual views — assembled winner-per-span beside members (live `merge_record`), masked-beside-raw (live `apply_quality_masks` on the uncommitted form values); proposals pre-filled in ledger syntax; per-signal `?` help from the SIGNAL_WHAT/legend prose; criterion-version banner on stale decisions. Done when: a scripted round-trip on every tab (decide → CSV row → reload shows it; malformed span rejected at the form, never written) and the gate message names the panel command.

**R8 — access layer.** `data.access.api` + machinery + cache: `get_sensors`, `get_record(snr_id | uid)`, `get_proximity(max_sep_m)`, `get_network(layers)`, `get_covariates(comids, products, window, remap=None)` (remap default none; `"default"` = ported cdl_classes map; result records the map), `get_weather(weighted_set, window)`, `snap/accumulate/aggregate`, pooled nitrate read with cache, minimal profile object (channels/floors/window as a filter). Stamps checked at read. Done when: access tests green; the accumulate-vs-VAA check re-registered here passes on a random reach sample; a demo notebook cell reproduces one published record and its catchment covariates end-to-end.

**R9 — basin editor PoC + gnn1 demo.** `tools/basin_editor`: Dash app with a lat/lon input + USGS-key input; renders basin1 (accumulate∘snap), basin2 (containment-catchment accumulate), basin3 (D8 flood-fill via `access.machinery.d8`), basin0 via clearly-labeled live NLDI call; sensors-within-basin displayed with type badges; per-method area readout vs VAA. **Example test case shipped in `tools/basin_editor/example.py` + README walkthrough**: delineate at `USGS:05455100` (English River at Riverside) — assert basin1 reach-set area within 1% of the VAA `totdasqkm`, basin0 fetches and overlays, and a headless pytest exercises the delineation functions without the UI. `src/gnn1`: port `compose_edges` + charge-set edge rules + `Downstream.next()` + test_graph tests as a package whose demo builds a sensor DAG purely from `data.access` (network + proximity + records) — the proof the published raw material suffices.

**R10 — verification hardening + docs.** Every chapter-Verification bullet mapped to a registered check (the audit's missing-check list: a3 partition, id-ledger five, both new leak scans, proximity checks, AOE-stamp-vs-extent, registry closure); `data/build/README.md` runbook in the build3-README style; chapter cross-check pass (spec vs implementation, discrepancies fixed or brought back to Isaac).

**R11 — the move** (after Isaac declares the build complete): `git mv`/`mv` of `src/data/{raw,acquired}` → `data/stores/`, flip the two config constants, re-run `verify all`; then old infrastructure retirement is a separate future effort.

## The rebuild is COMPLETE when

1. `pytest tests/data -q` fully green; 2. the full A→D→P chain completes with every gate answered — the decisions made by Isaac through the panel; 3. `python -m data.build.run verify all --deep` green on the real stores — including the four leak scans and the five id-ledger checks; 4. every Verification bullet in the chapter names an implemented check (table in verify.py's docstring); 5. the review panel has performed at least one real decision round-trip on each gating ledger; 6. the basin-editor example case passes and the app renders all four basins for the example inputs; 7. the gnn1 demo constructs its graph from access alone; 8. R11 executed and verify re-run green. The runs are mine; the reviews in 2 and 5 are Isaac's by design.

## Risks / notes

- **pandas 3.0**: build3 predates copy-on-write/string-dtype semantics; port-time fixes expected in chained-assignment spots. Ported tests are the net.
- **USGS GeoJSON capture** changes the metadata request path (bypass or extend `dataretrieval` for monitoring-locations); the fetch path is untouched.
- **Full-D2 recompute and the full waterbody pull are the two heavy one-timers**; both are Isaac-run and resumable (per-sensor memoization; archived download).
- **ICIS/DMR/CWNS schemas** are verified from the saved documentation in `reference/water-treatment-facilities/`; the facilities adapter treats column drift as a schema_changed drift event like any source.
- The old plan file content (IWQIS archive replacement) is superseded by this plan; that task shipped.
