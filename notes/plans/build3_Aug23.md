# Build3: full rewrite of the data pipeline to the data-chapter spec

## Context

`notes/book/data_chapter.md` is the binding specification for the data layer: three layers (acquisition / derivation / publication) plus a decisions input layer, the sensor/site identity model with minted ids, target-agnostic derivation with scrutiny tiers, a channel registry with collection tiers, a canary roster, the maximal non-temporal DAG with composable edge attributes, and per-(site, channel) quality. The existing `src/build2` was built chunk-wise under the older physical-gauge model and patched heavily through the census expansion; the user has chosen a **full rewrite of the build portion** (not the access layer). Three exhaustive audits of the existing code (below) establish what must be ported, what is new, and what must not be re-created. The new package is `src/build3`; old `src/build2` freezes as read-only reference (the same pattern build2 took toward `src/build`) and is deleted after the rewrite lands.

## Decisions made by the user

- **Location**: new package `src/build3`; build2 frozen as reference, deleted later.
- **Stores**: `src/data/raw/` adopted in place under snapshot manifests. **Approved: one physical move** — build2's acquisition-derived stores (native water 5.3 GB, gridMET 17 GB, comid attrs 4.5 GB, network 2.2 GB, iwqis split 604 MB) rename from `interim2/` into `src/data/acquired/` (same-filesystem, instant, manifested). Build2's *derived* outputs stay in `interim2`/`processed2` as cross-validation diff targets. ~150 GB disk free.
- **Tests**: pytest over synthetic fixtures + a `verify` CLI running each stage's done-when invariants against real stores.
- **Ledgers**: fresh start on all decision ledgers; old sheets remain readable in frozen build2 for reference only.
- **Site-keyed ledgers key on the sorted member-sensor set** (uids survive coordinate revisions and re-snaps); the site id is a display column. Decisions survive everything short of the bundle itself changing.
- **Site snap**: location from the highest-authority member (human override > operator-declared co-location > longest record), site re-snaps once from it; **bundle halts for review when members' snapped reaches disagree**.
- **Per-channel merge: winner per span** — each day's value from exactly one contributing sensor, chosen by the channel's registry merge policy; provenance per (channel, span). No blended values.
- Sanctioned by the chapter: AOE-scoped census, sub-catchment sites published typed/off-graph, canary roster (4 classes), NWM site-reach acquisition, channel `collection: full | canary | off`, publication classifies rather than drops, record floors move to access profiles.

## Audit findings

### 1 · Acquisition surface

**Network call sites the acquisition layer must own** (each with hard-won mechanics that must survive):
- **USGS waterdata API** (`sources/usgs.py`): token-rotation ring around *every* call (`_with_rotation`; bounded to one pass; `seen_idx` guard); one env-var token per process → **process sharding, round-robin by cost**; `API_USGS_CONCURRENT` pinned `"1"` (the 190-concurrent/429 incident, 194 sites lost); 1000-day windows vs the hard 1100-day envelope; `_active_window` per-parameter clipping; dv-vs-native tier decided from `channels_declared` (saves ~13,700 metadata round trips).
- **NWIS dv**: batch 300 (0.43→0.085 s/site), bisection on batch failure, `_valid_id` at discovery (one bad id 400s the batch); `_DV_CACHE` popped-not-read (3.9 GB incident).
- **NLDI**: seed basins + basin0 (currently leaking into D4); `/comid/position` **banned** (WQS0061 98.5% understated); polygons repaired before union.
- **WaterData/pynhd**: flowline snap 0.01° window, deliberately no wider pass (Bradley Creek); waterbody byid batches of 40 (leaks into D3).
- **NHDPlus bulk** via `nhdplus_l48` (REST times out); VAA via `nhdplus_vaa` (HydroShare 301→405).
- **gridMET**: hand-rolled NCSS — `pygridmet` retries on NaN but gridMET is land-only (infinite refetch); polygon tiles; HTTP 400 = not-published-yet; completeness-not-existence + `_RECHECK_MONTHS=2` (only ancestor of the trailing revision window).
- **StreamCat**: 1000 req/hr quota, empty-body errors; batch 5000 (at 500 the pull could not complete on any run); quota sleeps 3660 s, never retries; retry sweep at the SAME batch size.
- **Wieczorek S3**: names + `pyarrow_filter`, one prefix per request, col batches of 100 (24 GB OOM), `_complete.json` last with `nodata_masked` as format version.
- **NID**: direct HTTP (pygeohydro casts to a dead schema); 86-byte staging response → size guard.
- **NWM**: `NotImplementedError` carrying the design (site COMIDs = 22/93 chunks, 4.2× cheaper; partition by COMID).

**Adapter contract** (`sources/base.py`): 21 discovery columns, conform-raises-on-unknown, `normalise_obs`, fetch-returns-everything, `min_nitrate_days` as cost hint. **Fix the drift**: USGS `fetch(tier=, channels=)` forces source-branching in callers.

**A4 gaps — genuinely new**: no snapshot diffing anywhere; no trailing revision window for USGS provisional data; no immutability/snapshot ids (every store mutates in place); verification is three functions with three output shapes; `reconcile` IWQIS-only; `measures.csv`/`params.csv` unread.

Key provenance constants (full tables in the audit transcripts): `_MAX_SPAN` 1000d, `_DV_BATCH` 300, `_STREAMCAT_BATCH` 5000, `MAX_CONUS_KM2` 3.5e6 (Cape Cod 5.06M km²), `MIN_NITRATE_DAYS` 1, all three `NITRATE_PCODES` (99133-only = zero Nebraska), `STANDARD_TZ` Etc/GMT+6, Morton-ordered gridMET cells (29→1 row groups), quarterly weather files (monthly fetch; 28 GB swap incident).

### 2 · Derivation kernels

**Nearly all portable.** Dies: the gauge unit (`physical_gauge_group` → site table), the nitrate-only/per-channel record-store split, three hand-copied gate implementations, the `"nitrate"` literal at ~9 call sites (becomes the scrutiny tier; `params.LABEL_CHANNEL` was meant to be this and has zero consumers).

**Portable kernels + calibrated constants**: snap 3-rule chooser (tie 25 m, area-match 600 m, rescue guard ~1.25× — 13 mis-snaps / 12 wrong moves without them; placeholder areas; `snap_pos_m` via `LineString.project`, verified NHDPlus digitises in flow direction — the id primitive already correct); 7-rank type ladder + human rank 0 (Coastline p=1.00, LakePond p=0.50); identity thresholds (r≥0.80 from the 8.1 m duplicate at 0.887; 0.25 mg/L; complementary ≤100 m vs treatment pairs 200 m–1.9 km; dual ≤250 m vs suspects 1.6/2.1 km; the 822 m pair killing candidate radii; weak channels incl. water_temp r=0.9936 at 1,878 m); graph (delta-safe `next()`, label-with-via, 23-col edge table, `flow_distances` off edges by construction, accumulate verified 388/392 within 1% — 4 failures all Coastline); basins (4 methods precedence-not-score, Boone-well guard, D8 outlet band 0.33–3.0× separating median-1.01× correct from >4× wrong, `coverage_union_all` fails on NHDPlus); 6 advisory flags (D8_TOL 0.35 because 4% fired on 61/116; river-capture name evidence 56/62); review machinery (`sync_sheet`, cap 120, unanswered-first, panel-row=sheet-row).

**Registry vs chapter**: mappings/ranges/fetch-tier exist; needs 3-state units verification (units_check verdicts never write back), scrutiny tier, collection depth, regime, and the comparison specs consolidated from identity.py. NAMES-driven schema generation already works.

**Bugs to fix, not port**: case-sensitive `MIN_STREAM_ORDER` lookup (dormant); unkeyed waterbodies cache; `_agreement` ignoring `scale_kind`/`agree_tol` (bites under per-channel merge); duplicated `NON_DRAINAGE_FTYPE`/`VAA_NODATA`; no machine-readable edge composition rules (chapter's law — 4th tuple slot).

**~48 encoded traps indexed** — highest-cost: /comid/position, case-insensitive VAA, placeholder steering, Spirit-vs-Storm-Lake, null-snap_status≠far, order-dependent pairwise merging, nullable `is_group_canonical` (ran green until something was pending), NODATA differencing (four independent statements), pathtimema-is-residence, absent-vs-empty dam table, backward-reach obligation (the 9,801).

### 3 · Aggregation, publication, consumers

**The zonal engine is the crown jewel — port essentially unchanged**: exactextract 0.00% error / 0.48 ms per catchment; same-grid batching (0.08 ms marginal); Morton ordering; WKB storage; `make_valid` per batch (6,815 invalid catchments segfault exactextract); streaming parts. Fix: mixed-grid check prose-only; `_rename` stem-keying; `n_repaired` never reported.

**Products**: crops (2025 CDL larger grid = own group; manifest answers completeness from parts, avoiding a 50 GB unzip-to-read-nothing; fractions not stored — cannot sum along a network), nutrients (36 rasters one pass; mean AND kg; ends 2017), weights (exact spherical cell areas, 34% spread), dams (both keyings; `_cached` is the only stamp *reader* in the codebase; absent≠zero≠missing-table), agtile (ESRI:102003; **no mask** — PLAN.md:150's NLCD claim is stale), archive (expand-use-delete; recoverability-not-ownership; purgeable-space trap).

**🐛 Found bug**: `weights.id_raster` uses a −180/90 origin while `weather.cell_id` uses gridMET's CONUS origin — every weight-matrix cell_id = weather cell_id + 1,351,290; the join returns zero rows. The weather store's convention is canonical; fix key + area decode in one edit.

**Publication delta**: drops→classifies; floors (365 d/10k rows) → profiles; quality gauge→(site, channel); water nitrate-only day-max → seven primitives per channel; no sensors table exists; `sites.parquet` grain impure. **Keep verbatim**: `_no_silent_eviction`, gate-before-write, first-reason-wins→classification-with-reason, publish-exactly-what-the-detector-judged. Quality provenance: three populations of negatives, no nitrate ceiling (~10,700 readings recovered), cohort-mean seasonality = geography (72/123 flags — report-only), gap-aware flat runs, three USGS nitrate pcodes.

**Claimed-but-never-implemented**: per-grid closure checks exist nowhere; `coverage.parquet` has no reader; `frac_covered` hardcodes 900 m². Build fresh.

**Consumers**: nothing reads `processed2/` — publication is greenfield. The rtol=1e-9 selftest exists (`src/selftest.py`) against the old store — re-point, don't re-invent. `deploy/`+`widget/` import four `_make_basins` privates; `build_virtual_site_data` is the eventual remedy (access layer, out of scope here).

## Package layout — `src/build3/`

Layers as subpackages; stage names (A1…P1) are runner targets, modules named by content. ~33 modules.

```
src/build3/
  config.py       paths (RAW/ACQUIRED/SNAPSHOTS/DERIVED/PUBLISHED), years; scope literals move to parameters/
  registry.py     THE channel registry: Channel+SourceMap; 3-state units (declared|assumed|verified);
                  scrutiny (high|standard); depth (full|canary|off); regime; per-channel comparison spec
                  (consolidates identity.py's COMPARE_STAT/WEAK set as discriminating=False) and MERGE policy
                  (winner-per-span rules); fetch tier; NAMES-driven schema generation
  contracts.py    uid + site-id minting (S{comid}-{snap_pos_m}, OFF- namespace), column blocks with owners,
                  Stamps dataclass (snapshot, aoe, namespace, schema fingerprint, ledger hashes), fingerprint calc
  io.py           atomic writes, parquet stamp embed/check (generalises dams._cached — ONE reader pair),
                  STANDARD_TZ day rule, resolve_tz
  ledger.py       the ONE generic ledger kit: Ledger(name, key_cols, vocabulary, gates, tier_rule);
                  blank=unreviewed, unknown-token raises, pending(), gate()→SystemExit naming rows,
                  auto-records, sync_sheet (ported). Seven instantiations, zero copies
  panel.py        review-panel rendering (port _panel.py: cap 120, unanswered-first, row N = sheet row N)
  verify.py       the verify CLI: Check(stage, name, fn) registry mirroring the chapter's done-when clauses
  run.py          runner: full A→D→P, `acquire` alone, single stage, verify dispatch

  parameters/     tracked build inputs declaring intent
    scope.toml        seed boxes (copied from src/build/pipeline_config.toml; build3 never imports src/build)
    canary_roster.csv 4 probe classes, admitted through every scope filter
  decisions/      ALL human ledgers, one flat tracked dir:
    drift.csv registry_gaps.csv merge.csv site_type.csv exclusions.csv basins.csv quality.csv
    (basins + quality keyed on sorted member-sensor set, per user decision)

  acquire/        Layer A — every network call, every raw byte
    sources/base.py usgs.py iwqis.py mpca.py   (contract drift fixed: uniform fetch signature)
    seed.py       A1 seed discovery + NLDI seed basins
    census.py     A2 AOE-scoped census, canary admission enforced here
    records.py    A3 observation fetch (shard_of, incremental + trailing revision window)
    network.py    A3 nhdplus bulk, VAA, waterbodies (moved out of D3)
    weather.py    A3 gridMET NCSS
    attributes.py A3 StreamCat/Wieczorek/NID
    nwm.py        A3-late: site-reach retrospective daily aggregates, COMID-partitioned
    snapshot.py   manifest writer/walker, snapshot ids, adoption pass
    reconcile.py  A4: reconciliation generalised beyond IWQIS (measures/params finally read), units check port,
                  snapshot diff + drift classification, ONE verification report

  derive/         Layer D — pure function of (snapshots, parameters, decisions)
    extent.py     D1 AOE + region-not-vertices stamp
    canonical.py  D2 canonicalisation + seven-primitive reduction (regime-aware; per-sensor incrementality)
    snap.py       D3 sensor snap (3-rule chooser; case-INsensitive VAA)
    site_type.py  D3 7-rank ladder + human rank 0
    identity.py   D3 bundle candidates; agreement reads comparison specs from registry (fixes scale_kind bug)
    sites.py      D3 exclusions applied → gated review → site snap (authority-ranked; halt on reach disagreement)
                  → id minting → per-channel winner-per-span merge → THE site table, minted once
    basins.py     D4 four methods + 6 advisory flags (folds flags.py); keyed waterbodies cache
    graph.py      D4 label-with-via; edge table with machine-readable composition rules (4th tuple slot);
                  accumulate BFS; flow distances off edges
    zonal.py      D5 the engine (near-verbatim; mixed-grid check executable; _rename fixed)
    products.py   D5 crops/nutrients/agtile/weights over one shared driver — per-grid closure written ONCE
                  in the driver so it finally exists; weights conforms cell_id to the weather store
    dams.py       D5 both keyings; stamp reading via io.py
    archive.py    D5 expand-use-delete, Lazy, disk checks

  publish/        Layer P
    quality.py    per-(site,channel) detectors
    publish.py    P1 classify-not-drop, gate-before-write, _no_silent_eviction, the partition invariant,
                  sites/sensors/water/graph/pooled artifacts
```

Generated review evidence (candidates CSVs, panels, auto records) goes to `derived/review/`, not the package — separating what a human wrote (git) from what the build wrote (store). (Audit found build2's decision CSVs were never actually git-tracked despite the chapter requiring it — build3 tracks `decisions/` from day one.)

## Store layout — `src/data/`

```
raw/          adopted in place, manifest-covered, never written by build3
acquired/     Layer A output (immutable snapshot data): water/native, water/iwqis, metadata/{source}/{file}.{date}.csv,
              network/, weather/, attributes/, nwm/comid={comid}/
snapshots/    snap-YYYYMMDD[a]/manifest.parquet + header.json; HEAD pointer file
derived/      aoe, sensors.parquet (widened per stage, never loses a row), water/canonical/, sites.parquet,
              basins/, graph/, covariates/, review/
published/    sites, sensors, water/{site_id}, nitrate_daily, nodes, edges, basins, basin_reaches, comid_features/
```

**Stamps**: parquet artifacts carry stamps in pyarrow key-value schema metadata, written/checked by one function pair in `io.py`; non-parquet artifacts get `{name}.stamps.json` sidecars; `verify` sweeps stamps against the requested snapshot/ledger state.

**Snapshot design (minimal viable)**: id `snap-YYYYMMDD[a]` names a *state*, never a copy. Manifest row per file: relpath, size, mtime_ns, sha256 (nullable — lazy for the 24 GB zips), source, role, fetched_at, rule stamps. Adoption pass v0 walks raw/ + moved acquired/, cuts `snap-*-adopt`. Immutability is a convention verified, not OS-enforced: manifest-listed files change only via trailing-revision re-pulls and partition appends, and every acquisition run ends by cutting a snapshot and diffing. `diff_snapshots` classifies {added, extended, revised_in_window, revised_out_of_window, removed, schema_changed}; the last three queue to `decisions/drift.csv` via the ledger kit. First build has no parent — gates nothing. One `adoption_amnesty` class covers the first post-adoption diff (adopted files have unknown provenance). `run --snapshot ID` (default HEAD) pins builds. Not built: cron, per-file history, locking.

## Test harness

```
tests/
  conftest.py    tmp-store fixture (config override); factories
  fixtures/network.py   synthetic ~20-reach VAA/flowline factory (mainstem, tributary, divergence, Coastline,
                        isolated, one reach with two sites) — built in code, asserted by hand
  fixtures/golden/      tiny checked-in parquet pairs (obs→dailies; tiny raster+3 catchments→fractions);
                        regenerated only via explicit --golden-update
  unit/          pure-kernel tests, no store/network — the default pytest run
  storecheck/    @pytest.mark.store — thin wrappers asserting `verify <stage>` passes; skipped when stores absent
```

`verify` CLI: `python -m src.build3.run verify [stage|all] [--snapshot ID] [--deep] [--json]`; cheap checks first, expensive sweeps (full BFS, all-product closure) behind `--deep`; one table, nonzero exit on fail. The audits' verified measurements become assertions with provenance in the failure message.

Highest-value cases: (1) accumulate 388/392 within 1%, 4 failures Coastline (`--deep`); (2) per-grid closure per product — the never-implemented D5 invariant, generic in the driver; (3) weight-matrix joins weather store + conserves cell area — regression for the 1,351,290 offset; (4) rtol=1e-9 virtual-equals-real, re-pointed from src/selftest.py; (5) `_dv` day has NULL spread stats; (6) nothing-to-scrub sensor reproduces raw dailies exactly; (7) snap chooser: one constructed case per rule + placeholder steering; (8) site ids sort into flow order; distinct-ruling + coincident metre halts naming the pair; (9) edge composition law: contract(A→B→C) == direct A→C on every attribute; attribute without a rule raises; (10) ledger kit semantics (blank≠approved, unknown raises, added-never-rewritten, gate names rows); (11) publication partition exact and counted; (12) agreement respects scale_kind (the fixed bug's regression); (13) VAA case-insensitivity incl. MIN_STREAM_ORDER; (14) canary arrival end-to-end per class; (15) day binning across the Etc/GMT+6 boundary + date-only grab samples; (16) snapshot diff classes incl. revised-out-of-window → drift queue.

## Status (updated as milestones land)

- **M0 kit — DONE.** config/io/registry/contracts/ledger/panel/run/verify + 35 unit tests green. Registry carries the full new field set (3-state units incl. the params.csv declarations, scrutiny=nitrate-alone, depth, consolidated comparison specs — the weak set matches build2's calibration exactly).
- **M1 snapshot adoption — DONE.** The six approved moves executed (same-filesystem, ~12 s incl. hashing); `snap-20260823-adopt` cut: 7,038 files, 64.1 GB; `verify snapshot --deep` green; native count matches audit exactly (6,124). NID gpkg relocated into acquired/attributes.
- **M2 acquisition ports — CODE DONE.** All Layer A modules landed: sources (uniform fetch signature — the tier decision moved inside the USGS adapter), seed/seed_basins (type-vocabulary module pulled forward for the prior), census (AOE-scoped with parametric clip + arm measurement), records (nothing-derived; trailing REVISION_DAYS re-pull; per-run fetch_status), bulk orchestrator, reconcile (generalized reconciliation incl. measures.csv; units port; snapshot diff with 7 drift classes + adoption amnesty; drift + registry-gap gates), nwm stub carrying the design. Canary roster seeded (4 classes; probe uids are placeholders to confirm). One transposition bug found and killed structurally: `in_seed_boxes` is keyword-only after a (lat,lon)/(lon,lat) transposition silently zeroed IWQIS discovery. **Remaining M2 smokes are network-bound and user-run**: A1 USGS nitrate discovery, a small incremental fetch + revision-window sample, and the census dry-run that prices the AOE arms.

- **M3 D1+D2 — DONE.** extent.py reproduces the AOE exactly (4,284,001 km², 0.000 km² symmetric difference; stamp `30d8a9034912` carried forward via a one-time frozen-build2 fallback). canonical.py cross-validated 30/36 exact against build2's canonical store; the 6 divergences were build2's registry-blind cache (its params.py edited after its canonicals were built — mtime staleness cannot see registry edits). Fixed forward: every canonical file is stamped with `registry.fingerprint()` and `stale()` checks it.
- **M4 D3 — DONE.** snap.py ported (case-insensitive VAA lookup fixed, smoke reproduced 4 expected COMIDs with snap_pos_m); site_type.py classify machinery (7 ranks + human rank 0, request-keyed waterbody evidence); identity.py rewired to the registry (compare_stat/weak-set/high-scrutiny from registry; `_agreement` scale_kind fix — interval channels judged on `agree_tol`, the audit-#11 bug; ledger-kit sheet; bundle block per the sensors contract with sorted-member-set keys and NULL-when-pending) and **cross-validated 643/643 candidate pairs, 0 proposal diffs vs frozen build2**; sites.py minted (exclusion ledger, combined type+merge+exclusion gate in one halt, authority-ranked site snap [declared colloc > longest record] with halt-on-reach-disagreement, id minting with collision halt, winner-per-span per-channel merge with per-day provenance, sites contract + `verify d3`); waterbody FETCH moved to acquire/network.py (a3) — site_type reads the store purely, degrading loudly. Both review panels ported (merge_review.py channel-aware at the registry compare stat; site_type_review.py off the shared `type_review_set`). run.py REMAINDER bug fixed (`--single` was swallowed). **First real d3 run: 26 sites minted (24 on-network, 2 OFF-groundwater), ids flow-ordered, `verify d3` green, 53 unit tests.** Bundles are all singletons until the full D2 pass gives more sensors records (user-run).

- **M5 D4 — DONE.** accumulate.py / basins.py (flags folded in) / graph.py / basins_graphs.py orchestrator + basin_review panel. The NLDI basin0 fetch moved to acquisition (`acquire.seed_basins.fetch_authority_basins`, a3 `basins` branch; D4 reads the store purely, naming `not_fetched` separately from NLDI's own recorded misses). `NON_DRAINAGE_FTYPE`/`VAA_NODATA` declared once in acquire/network.py. Basin ledger runs through the kit, keyed on the member set, advisory. **The composition law is executable**: `contracts.EDGE_COLUMNS` carries a rule per attribute (`key/sum/or/min/max/via/recount/endpoints`), `compose_edges` refuses unruled attributes, and every summable attribute is measured over the edge's CHARGE SET (from-node's reach + strictly-between; empty co-reach) so charge sets tile the channel and contraction is EXACT — verified by unit tests on a synthetic net AND by `verify d4 --deep` re-walking real chains with the middle node removed (all attributes exact). Two defects found and fixed: NaN-truthiness in via composition; `label()`'s pop of no-owner reaches defeated its own path compression on sparse node sets (now memoised, None is an answer). **Real run: 24/24 basins delineated (auto: 1 basin0 + 23 basin1), 24/24 accumulate-vs-VAA within 1%, 7 edges acyclic (6 impounded), 30,094 reaches in the incremental partition, `verify d4 --deep` 4/4 green. Cross-validated vs frozen build2 basins.parquet: 26/26 method agreement, areas within 1.2e-05 relative.** 65 unit tests.

- **M6 D5+NWM — CODE DONE.** zonal.py (engine near-verbatim + two fixes: the shared-grid rule is now CHECKED in `extract`, and `n_repaired` is reported per pass); cdl_classes verbatim; archive.py verbatim; dams.py with io-stamp caching (`read_parquet(expect=)` replaces the ad-hoc aoe_stamp column); products.py = crops/nutrients/weights/agtile over ONE driver whose per-product CLOSURE CHECK runs gate-before-write (a failing product is never written; `--limit` smokes check but never write — a truncated artifact under the real name is indistinguishable from a finished one). **The weights bug is fixed at the root**: `id_raster` now CALLS `acquire.weather.cell_id` instead of reimplementing it (the two copies had different origins — the 1,351,290 offset), `cell_area_km2` decodes rows from the weather origin, and the sentinel moved from −1 to int32-min because this AOE reaches north of gridMET's 49.4°N origin row and carries LEGITIMATE NEGATIVE cell ids that build2's `>= 0` filter would have dropped. **Real smoke (8,000 catchments): 3,684/3,684 weight cells join the weather grid, median area closure 0.0129%.** Dams built full and cross-validated **exactly** (53,313/53,313 rows identical to build2, storage diff 0.0); D4 edge table now reads `dams.on_reach`. acquire/nwm.py implemented (retrospective zarr, year-windowed loads, comid partitions, Etc/GMT+6 days; needs `zarr`+`s3fs` — named loudly at run time); D3 captures `parameters/site_comids.txt` for it. `verify d5` re-checks stored closures + the weather-grid join standing regression. 77 unit tests. **User-run remainder**: full crops/nutrients/weights/agtile passes (archive expansion + hours) and their value diffs vs `interim2/comid_features`; `pip install zarr s3fs` + the NWM pull.

- **M7 P1 — DONE (review answered, store published).** publish/quality.py: detectors re-keyed to **(site, channel)** with two tiers per the chapter — high-scrutiny channels get the full battery (shape features over the native-resolution member concatenation, cohort-relative robust-z outliers, the INTRINSIC seasonality fit replacing the geography-measuring cohort-mean test, `impossible_runs` measured on the RAW native before the scrub hides it) and gate on human review via the quality ledger (keyed members+channel); standard channels take the rule's absolute checks, recorded, never asked about. Scrubbing itself was already in D2 (`reduction.scrub` off the registry's `lo/hi/clamp_negatives` — the detectors judge exactly what publishes). publish/publish.py: classify-not-drop (network_class, basin_class, per-channel `quality_{ch}` on the site table), NO record floors, sweep-first water writes, gate-before-write, and the **partition invariant executable**: every registration exactly one of site/excluded/recordless/held, halting on overlap or gap. Published artifacts: sites, sensors (metadata; records stay upstream), water/{site_id} minus review-excluded channels, nitrate_daily pooled, nodes/edges/basins/basin_reaches copies, comid_features pass-through. quality_review.py panel renders BEFORE the halt. `verify p1`: partition counted; excluded-channel leak check; **deep rtol=1e-9 republication check** (published water re-derived from the canonical store — the build-side half of virtual-equals-real; the access-layer half lands with the access layer, so `src/selftest.py` is NOT re-pointed yet). **Real run: 109 (site, channel) series judged, 10 nitrate series flagged and HALTING in `decisions/quality.csv` (panel rendered) — the review is Isaac's. Sandbox end-to-end (provisional keeps + one test exclusion): partition exact {26 site, 381 recordless}, excluded channel withheld from water and named on sites, 75,028 site-days published.** 87 unit tests.

- **M7 addendum — quality masks.** The keep/exclude binary grew two per-(site, channel) editable ledger columns after the first real review showed most flags were DEFECT WINDOWS in good records: `exclude_spans` (literal date windows, day-granular, strictly parsed; the detector pre-fills `proposed_spans` = identical-value runs >= 7 continuous days, gap-aware, never applied alone — a rejected true/false shortcut was removed on Isaac's ruling that ledger cells hold explicit values) and `max_threshold` (a sensor-specific ceiling in canonical units; any published day with a reading above it is nulled WHOLE, since the day's mean was computed from the offending samples — complements the registry's channel-wide `hi`, now nitrate=100, a physical bound that nulls fill sentinels at D2 and changed the registry fingerprint, so the full D2 pass re-canonicalises). Publication applies both masks once, before any artifact; published sites' per-channel spans are recomputed from masked records; `quality_notes` records every removal. The review panel gained a self-contained interactive HTML companion (plotly inline, hover reads exact dates, proposals shaded). **Isaac answered the 10-row queue + WQP0016; `published/` is live: 26 sites, 75,084 site-days, partition exact, `verify p1 --deep` 3/3 incl. rtol=1e-9 republication. `verify all`: 12 ok / 3 skipped (D5 rasters, user-run).** 93 unit tests.

- **M2 smokes — DONE (all network smokes run, two bugs found and fixed).** Records: `cached` leaves the store untouched; `refresh=True` re-pulled the trailing 120-day window live and merged 1,040 revised rows into USGS:01184000 (the ready-made `revised_in_window` case for M8's first diff); a never-fetched sensor recorded `no_data`. Census: the first sweep reported **0 AOE-arm sensors -- an artifact**: `usgs.discover` supported an AOE clip and its own comment said A2 must pass one, but `discovery.run` never forwarded the parameter, so the seed-box default clipped the arms off (fixed; the incident is now in the docstring). The canary punch-through had the same half-wired shape: discovery-by-pcode is itself a scope filter and can never return a groundwater-level or precip station, so `usgs.register_one` now admits absent roster probes by direct id lookup, and the two placeholder uids -- confirmed unknown to the API -- were replaced with live probes (well 423127084321901, 81-yr record active to 2026; precip 415457088150600, 40-yr active). **Measured: the AOE arms hold 4,297 sensors -- a 27% census expansion (20,224 total, matching build2's 15,924 inside the boxes). All four canaries land on the table, correctly typed, none fetch-skipped.** The chapter's open cadence question is answered with a number.

- **M8 A4 LIVE — DONE.** `snap-20260824` cut (7,042 files, 64.2 GB, parent `snap-20260823-adopt`); the first post-adoption diff classified exactly as designed -- 4 `added` (authority basin, waterbodies, fetch status, census metadata) and **1 `adoption_amnesty`: USGS:01184000, the revision-window refresh we caused** -- gate clear, amnesty spent; every future change classifies against real provenance. The conflicting-drift round-trip demonstrated end-to-end in a manifest sandbox (real store untouched): a perturbed out-of-window revision + a removal queued to `decisions/drift.csv` keyed (source, snapshot_pair, diff_id), the gate halted naming both rows, `accept`/`reject` written, gate cleared. a4 on the real store runs clean with the parent diff in its report (30 rows; co-location evidence grew to 81 cross-source pairs on the census cohort, nitrate ratio 0.9960 / discharge 0.9903 / gage 1.0000 all confirming). Daily-cadence runbook written to `src/build3/README.md` (the loop, the gates table, the measured facts). The registry-gap gate was exercised live earlier (WQP0016 halt -> acknowledged). **All eight milestones M0-M8 are landed**; remaining user-run work is the M7-chain over the census cohort (d3 with the 20k snap pass -> d4 -> p1) and the D5 raster passes.

- **D3 addendum — the site becomes a reviewed object.** Isaac's question ("shouldn't this be a site review?") exposed that the pairwise process reviews EDGES while the build mints NODES nobody looks at, and that transitivity silently overrode human `distinct` rulings. Three pieces landed: (1) **the contradiction halt** -- a component fusing a pair a HUMAN ruled distinct halts naming the members, the violated pair and the merge chain (rule-`distinct` deliberately does not contradict: a sequential chain legitimately fuses pairs the rule proposed apart); (2) **the site-review ledger** (`site_review.csv`, keyed on the sorted member set, vocabulary `confirm`, gating) -- one row per decided multi-member bundle with the composed evidence, so every minted multi-member site is accepted WHOLE by a person; membership changes re-ask by construction (new key), singletons never queue, and rejecting a composition = editing the pairs, which stays the editing mechanism; (3) **the composed-site panel** (`site_review.png`, every member's record on one timeline). Persist moved before identity so a step-4 halt cannot discard the 20k-sensor snap pass. **First live run: the halt caught TWO real contradictions inside the frozen build2 decisions** -- a stray Walcott row not in the frozen sheet (reverted to blank), and the Boone component (river station + water-supply well + WQS9902 fused as one gauge in build2 despite the human river!=well distinct -- shipped silently there, halted here; Isaac to flip one pair). 97 unit tests. The chapter's Gates-and-ledgers table needs a `site_review` row -- Isaac's document, flagged for his edit.

## Implementation order

| M | contents | share | smoke state |
|---|---|---|---|
| **M0 kit** | config, io, registry (full new fields), contracts, ledger, panel, run shell, verify skeleton + unit tests | 10% | pytest green; `run --list`; fingerprint computes |
| **M1 snapshot adoption** | snapshot.py; the approved interim2→acquired move; cut `snap-*-adopt` | 5% | `verify snapshot` counts match audit (6,124 natives, quarters…) |
| **M2 acquisition ports** | sources (drift fixed), seed, census, records, network, weather, attributes, reconcile (units port + diff classifier); canary roster wired | 25% | completeness over adopted store without refetch; small incremental fetch + revision-window sample; **census dry-run measures AOE-arm cost** (resolves the chapter's open question) |
| **M3 D1+D2** | extent, canonical | 10% | AOE stamp equals old region under same-region rule; canonical dailies diff clean vs `interim2/water/canonical` for unchanged sensors |
| **M4 D3** | snap, site_type, identity, sites; merge/type/exclusion ledgers live; first gated run; queues re-answered under tiered gates | 20% | site table minted; bundles sanity-diffed vs frozen merge_decisions.csv (reference only); `verify d3` |
| **M5 D4** | basins, graph | 10% | `verify d4 --deep`: 388/392, acyclicity, incremental-set partition, composition law on real edges; basin selections diffed vs old basins.parquet |
| **M6 D5+NWM** | zonal, products (closure driver), dams, archive; weights fix; acquire/nwm.py (site-comid list captured as a parameter file so derivation stays pure) | 15% | closure green per grid; weights joins weather nonzero; product values diffed vs interim2/comid_features |
| **M7 P1** | quality (re-keyed), publish, sensors table, pooled projections, quality ledger | 10% | partition invariant; rtol=1e-9 re-pointed at published/; `verify all` green — acceptance |
| **M8 A4 live** | one incremental acquisition; snapshot 2; diff with adoption amnesty; drift + registry-gap gates exercised (WQP0016 standing case); daily-cadence runbook | 5% | drift ledger round-trip demonstrated |

The user runs full builds; each milestone ends at a state where a partial pipeline produces verifiable artifacts against real data.

## Remaining open items (tracked in the plan, not blockers)

- **AOE-census cost** — measured at M2 before committing cadence (schedule risk only).
- **NWM recency**: retrospective ends early 2023; `published/` promises no NWM columns until the NWPS/operational-archive investigation closes; NWM stays an acquired-store product.
- **Ledger-hash granularity**: whole-file hash over-invalidates on any append; accepted, noted as future refinement.
- **Off-network ids**: `OFF-{canonical member uid}` — visibly non-positional; accepts registration identity leaking into a rare, graph-less class.

## Verification

1. `pytest` green at every milestone (unit suite requires no stores).
2. `python -m src.build3.run verify <stage>` after each milestone's real-data smoke; `verify all --deep` is the acceptance run at M7.
3. Cross-validation diffs against frozen build2 outputs at M3 (canonical dailies), M4 (bundle outcomes), M5 (basin selections), M6 (covariate values) — differences must be explained by a named design change, not silence.
4. The canary invariant (test 14) and the publication partition (test 11) run in every `verify all`.
5. M8 proves the acquisition loop: snapshot → incremental fetch → diff → drift gate → new HEAD.
