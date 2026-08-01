# Data-pipeline audit — 2026-07-31

## 1. The pipeline as it stands

```
CANDIDATE UNIVERSE   raw/water/raw_site_list.csv  (171)
      │
      ├─── WATER BUILD ─────────────────────────────────────────────────────┐
      │    fetch_sites → download → filter_sites → make_water               │
      │                                 │                                    │
      │                      ┌──────────┴───────────┐                        │
      │                      │ quantity   evidence: row counts      ✔ local  │
      │                      │ quality    evidence: the series      ✔ local  │
      │                      │ tiny       evidence: basin × grid    ✘ ───────┼──┐
      │                      │ big_basin  evidence: containment     ✘ ───────┼─┐│
      │                      └──────────────────────┘                        │ ││
      └─────────────────────────────┬──────────────────────────────────────┘ ││
                                    ▼                                         ││
                 processed/water/meta/site_location_metadata.csv              ││
                              THE CONTRACT (116)                              ││
                                    │                                         ││
      ┌─── FEATURE BUILD ───────────┴──────────────────────────────────────┐ ││
      │  1 map_overlays(site box)                                          │ ││
      │  2 basins ──────────────► preferred_basin.csv ─────────────────────┼─┼┘
      │  3 region → covariate_bbox  (rewrites pipeline_config.toml)        │ │
      │  4 map_overlays(wide) + comid_attrs                                │ │
      │  5 grid_global ─► weather / crops / agtile / surplus  [global_node_id]
      │  6 site_grids                                                      │ │
      │  7 aux ─────────────────► basin_containment_graph.parquet ─────────┼─┘
      └────────────────────────────────────────────────────────────────────┘

      ✘ = a filter reaching FORWARD for an artifact the later build produces.
          Those two arrows are the 116↔123 cohort cycle (finding 1).
```

**Twelve site lists exist** (candidate universe, `good_sites`, contract, `get_site_ids()`, `preferred_basin.csv`, grid files, graph nodes, water files on disk, `KNOWN_BAD`, `big_basin`, `USGS_SITE_LIST`, `filtered_sites.json`). Only one is authoritative — the contract — and nothing reconciles them. They agree today except water files (131 vs 116).

## 2. Bottom line

**One defect is live and blocks rebuilds:** the cohort oscillates 116↔123 depending on which step ran last, so *a rebuild today would not reproduce the current cohort*. Fixed on the filter side this afternoon; the producer side remains.

**The largest blast radius is not the D8 bug.** `global_node_id` is a bare row counter with no version tag, and four covariate tables key on it. A `_make_grid --force` after any bbox change silently returns another cell's crops, surplus and weather for every site — 100% of cells, versus D8's 24% of sites.

**The most consequential finding is not a defect at all:** 11% of the cohort are not stream gauges — they are wetland and pond outflows, tile drains and treated ditches, several of them deliberate inflow/outflow pairs measuring nitrate *removal*. Nothing records this, and no watershed covariate can explain a site whose nitrate was cut 79% by a wetland (§5.2).

**The pipeline's real weakness is a pattern, not a list:** it derives the same fact from several independent sources and never reconciles them, and it caches aggressively without recording what the cache was built from. Every finding in §3 is one of those two.

## 3. Findings

L = live now · P = latent (correct today, one command from wrong)

| # | finding | sev | | evidence |
|---|---|---|---|---|
| 1 | Cohort oscillates 116↔123; `filter_sites` judges tiny-ness off `preferred_basin.csv`, which `_make_basins` rewrites from the contract | ■■■ | **L** | re-running the filter today yields 123, readmitting `WQS0109` (documented to break training) |
| 2 | `global_node_id` has no version tag; `crops/surplus/agtile/weather_global` all key on it with no provenance and bare `exists and not force` skips | ■■■ | P | no stamp file exists in `interim/`; every join would silently succeed against the wrong grid |
| 3 | `_state_daily_wide` has no invalidation of any kind, and backs a leave-one-out mean over all other sites | ■■■ | P | key is the filename; a dropped site keeps feeding every other site's `rest_of_state_*` |
| 4 | `site_view` catches `except Exception` → all-NaN `dist_to_sensor` → written to disk → stamped clean | ■■■ | P | swallows the `ValueError` today's D8 fix raises; `_frames_match(equal_nan=True)` then passes it |
| 5 | `flag_not_contained` requires *all four* candidate basins to exclude the sensor, so it can never fire — dead code implying coverage it does not provide | ■ | **L** | fired on 0 of 116. Fixing the predicate would fire on 15, but a sensor outside its own basin is often *correct* (the gauge sits below the outlet), so the flag needs a purpose before it needs a fix |
| 6 | ~~`.preferred_basin_archive.csv` absent → every `_make_basins` run takes its hard-reset branch~~ **NOT A DEFECT** | — | — | The archive was **deliberately retired**: it holds 88 rows of an older *manual* review (`selection_mode=manual`, still on disk as `.preferred_basin_archive-old.csv`, also at `git show 05cd9f4:…`), and the automatic basin-review rule that replaced it is better and has been verified. The hard-reset branch running every time is the intended behaviour, `reviewed=False` is correct, and re-wiring the old file back in would be a regression. Widget `basin4` edits still not persisting is a separate question, if it is one |
| 7 | `--site` rebuilds write the **global** provenance stamp | ■■ | P | `failed` is empty on a one-site run → stamp written → 115 stale grids read as current |
| 8 | Fresh-clone build produces a 45%-coverage `comid_attrs` | ■■ | P | `_make_comid_attrs` has no existence check; `_make_map_overlays` skips, leaving the narrow flowlines |
| 9 | `_cached_grid_env` is `lru_cache(1)`, read before the stamp is written | ■■ | **L** | defeats the `make_features:99` ordering; aux recomputes every grid live. Cost-only, but that is why aux is slow |
| 10 | `make_water`'s output directory is a search path for its own input | ■■ | P | a uid missing from `raw/` reads back an already-daily-maxed series; `MIN_NITRATE_ROWS` then drops it as "short" |
| 11 | `_make_region`'s bare `except` collapses `covariate_bbox` to the site box and triggers a full rebuild at the wrong extent | ■■ | P | one `[WARN]` line; also compares boxes by **string**, and reports a shrink as `grew` |
| 12 | 15 orphan water parquets, committed, for non-contract sites — two of them in `KNOWN_BAD` | ■ | **L** | `access.get_water` reads by path with no contract check |
| 13 | Only `_make_weather` writes atomically; six other builders `to_parquet` directly and skip on "file exists" | ■ | P | Ctrl-C mid-write leaves a truncated artifact the next run treats as done |
| 14 | `_DEAD_SURPLUS_PREFIX` missing from both `_cache_by_site` keys | ■ | P | violates `recipes.py:24`'s own stated rule; every other tunable constant obeys it |
| 15 | D8 accumulation `.npy` keyed on the raster but not on the code that interprets it | ■ | P | `grid_build_env` now tracks `d8.py`, so a d8 edit rebuilds grids *against a possibly-stale accumulation* |

### The four worth reading about

**1 — the cycle.** `filter_sites._cell_coverage` skipped any uid absent from `preferred_basin.csv`. `_make_basins` rewrites that file from the 116-row contract, so the 7 sites the filter dropped lost their rows and became **unjudgeable** — the filter could no longer see the sites it exists to remove, so it removed nothing and they returned. Then the contract grew, their rows came back, and they were dropped again. Fixed today: coverage is now measured from the basin polygon **on disk**, which survives exclusion. Verified — all 7 judged, all 7 still tiny, coverage values matching `CLAUDE.md`'s record exactly.

**2 — `global_node_id`.** It is `np.arange(len(lon_f))` over the gridMET points inside `covariate_bbox`. Change the box, every cell renumbers. Four large tables join on it, none records which grid it came from, and `make_features`' shared `wide_force` papers over it *only* inside a full run — the documented standalone `_make_grid --force` does not. The fix machinery already exists (`grid_build_env`) and only `_make_site_grids` uses it.

**4 — the swallow undoes today's fix.** `_snap_outlet` now raises rather than snapping to the wrong river. `site_view` catches it and substitutes NaN; `build_one` sees a non-empty frame and writes it; the stamp records success. A refusal to compute becomes cached truth.

**6 — withdrawn, this is intended.** `_make_basins` printing *"Hard reset: archive ignored, every site re-picked by the auto rule"* on every run is the design, not a symptom. The manual review layer was superseded by the automatic rule, which is better and verified; its 88 rows survive as `.preferred_basin_archive-old.csv` purely as history. Restoring the filename would put the old hand-picks back in charge of the new rule's output.

## 4. The two graphs

They **are** the same relation, and the pipeline already uses each for the right job — vector polygons for topology, D8 for weights. Nothing needs restructuring.

They are measured independently (point-in-polygon on NLDI vector; routing over a 0.159 km²/cell raster), and agree on 231 of ~240 edges. All 14 disagreements are measurement error in one source:

- **6** are `WQS0066`, whose outlet is still 4.93× oversized — 2 of them *geometrically impossible* (a 9,221 km² basin "draining into" 2,359 km²)
- **5** are sub-resolution: a 17–32 km² basin is 100–200 cells and its headwater stream may not exist in the raster
- **3** are sensors 0.52–2.70 km from a divide, area-consistent, where neither source is authoritative

The independent check that discriminates is **area ordering** — a contained basin cannot exceed its container. 0 violations on all 236 containment edges; catches the `WQS0066` contamination for one comparison per edge. (A `flag_not_contained` that actually fired, finding 5, catches the same site by pure geometry.)

What is missing is that the two sources are **never compared**. Each containment edge predicts D8 finds a path; each D8 path predicts point-in-polygon agrees. Neither is checked, which is why a wrong outlet went undetected.

## 5. Structure: filter where the evidence appears

**The distributed-filter proposal is right, for a sharper reason than tidiness.** Every filter needs evidence, and each kind appears at a different depth — row counts at step 1, cell coverage only once basins x grid exist, hub-bridging only once the containment graph exists. Today all three live in `filter_sites.py`, so two reach *forward*. That is finding 1, not a coincidence.

Two conditions come with it:

- **Each step builds for the CANDIDATE set, not the survivors.** A site's basin is the evidence used to judge it; delete it and the site is permanently unjudgeable — exactly the state the 7 tiny sites were in.
- **Graph filters compute once on the full candidate graph.** Dropping a hub changes who the next hub is, so an iterated version has no unique fixed point.

**Output one cohort manifest, not twelve lists.** Schema and the per-filter contract are in the appendix (A1/A2).

### 5.1 Duplicate registrations — and the graph cycles they cause

**The containment graph is not a DAG.** It has 3 two-cycles, and a cycle means water returns to where it started:

| cycle | areas (km²) | separation | shared days |
|---|---|---|---|
| `WQS0032` ↔ `USGS-05483600` | 1117 / 1117 | 4 m | 0 |
| `WQS0081` ↔ `USGS-05481000` | 2187 / 2187 | 23 m | 0 |
| `WQS0024` ↔ `USGS-05451210` | 583 / 583 | 52 m | 0 |

All three are **one physical gauge registered twice** — sequential deployments (gaps of 142, 186, 1235 days), both ids delineated to the same basin, so each polygon contains the other's sensor and containment runs both ways. Nothing declares them the same location.

Merging is a gain, not hygiene: `USGS-05451210` covers 2010-2011 and `WQS0024` covers 2015-2025, so keeping one discards half the record. Where a brief co-deployment allows a check (`USGS-05482500`/`WQS0082`, 22 shared days) the agencies agree at **r = 0.898, median difference −0.02 mg/L**.

**Do not deduplicate by distance.** Eight pairs at 200 m-1.9 km are *concurrent*, sharing 389-2156 days at r = 0.006-0.589 — genuinely distinct sensors (see 5.2). The test is near-coincidence **AND** non-overlapping records; the two thresholds are separated 7x and 18x respectively. Spec in appendix B12.

*Carry into the neighbour experiment:* sensors 1.4 km apart can be uncorrelated (r = 0.006). Proximity does not imply hydrological similarity — a caution about the `dist` selection rule.

### 5.2 Site TYPE is unmodelled — 11% of the cohort is not a stream gauge

The eight concurrent pairs are **paired inflow/outflow monitoring of engineered nitrate-removal features**. `WQS0012` monitors the inflow to a CREP wetland (median 9.0 mg/L); `WQS0008` monitors its outflow (4.8). Five such pairs, the outflow member lower every time by 47-88%.

The models predict stream nitrate from land use, crops, surplus and weather. **A wetland outflow's nitrate is set by the wetland, not the watershed** — no feature in any recipe can express that, so these sites carry irreducible error while being trained on as ordinary gauges. Tile outlets are worse: their delineated "basin" is a field.

| non-stream type | n |
|---|---|
| wetland / pond / oxbow / CREP | 5 |
| tile / edge-of-field | 4 |
| ditch | 4 |
| **total** | **13 / 116 (11%)** |
| research / experimental marker | 27 / 116 (23%) |

`iwqis_river`, `iwqis_description` and `iwqis_nickname` already carry this and **no code reads them**. Recommended fix — classify the type, then for a sensor below a treatment feature subtract the treated contributing area from its basin (`basin(WQS0008) − basin(WQS0012)`). Full treatment, caveats and limits in the appendix.

This also explains `WQS0066`: it is the **Monona-Harrison Ditch**, an engineered channel, and it is the one site whose outlet snap survives the D8 fix at 4.93x. Routing a constructed ditch over a natural-terrain DEM is a physical mismatch, not a remaining bug.

### 5.3 Island vs network cohorts — aim for identical

**Absent topology is not disqualifying; impossible topology is.**

A site with **no donor must stay in the network cohort**. It emits the donor block NaN-filled, XGBoost splits on NaN natively, and `num_neighbors = 0` lets the tree condition on it — so the model *learns the no-donor regime*, which is a real deployment scenario (a pin with no upstream gauge). Excluding those 14 sites would make the deployed model worst where it has least support.

**A cycle is the disqualifier**, and after deduplication (5.1) there are none. So `network_ok == island_ok` **with nothing left over** — the target is reachable today, not aspirational. Two things that are explicitly *not* defects: a sensor outside its own basin (legitimate — the gauge can sit below the outlet; `WQS0052` and `WQS0066` are the largest cases and both are correct), and having no donor.

**Assert `nx.is_directed_acyclic_graph(graph)` at build time.** One line, currently failing, and it is what "water does not flow uphill" reduces to.

*Reporting consequence:* report network on **both** the full cohort and the donor-only subset. The full number answers "deploy network everywhere?", the subset answers "does the donor block work?". Exps 33-36c report only the first and conflate the two.

### 5.4 `WQS99xx` are temporary and test loggers

All seven are `agency_code = IIHR`. `WQS9901` is *"a temporary site"*; `WQS9999` is *"a test logger."* with nickname `test`. Filter on the test/temporary descriptions — but **not** on `iwqis_status = discontinued`, which only means the site stopped reporting and says nothing about its history.

### 5.5 Deployment — three open questions, all blocking

Undecided, and none measurable from what is built today. Each blocks a cohort decision:

1. **Forecast or nowcast?** Decides whether `lag0` is legal.
2. **Gauged sites or arbitrary pins?** Decides whether `network` is available at prediction time; there is no model-selection seam and downstream detection is a stub.
3. **What fraction of arbitrary pins resolve a donor?** The 88% figure is for *gauged* sites, which sit where someone chose to instrument. The pin number decides whether `network` is primary or fallback.

## 6. Recommendations

Each line: what, then why. **Bold = do before the next rebuild.**

### A. Streamline

| | | why |
|---|---|---|
| A1 | **Move `tiny` into the basin step and `big_basin` into the graph step**; leave `filter_sites` handling quantity + quality only | removes the forward reach that causes finding 1; each filter runs where its evidence exists |
| A2 | **Emit one cohort manifest**; every consumer reads only it | twelve site lists with no reconciliation is how both 2026-07-30 cohort bugs happened |
| A3 | Give the water build an orchestrator | four modules run by hand in a required order enforced by nothing |
| A4 | Reap or archive orphaned per-site artifacts | 15 stale water parquets are what make finding 10 reachable |
| A5 | Delete the three dead config keys; collapse the crop schema from three declarations to one | a config key that lies is worse than no key |

### B. Fidelity

| | | why |
|---|---|---|
| B1 | **Assert cross-source invariants at build time**: area ordering, antisymmetry, every containment edge has a finite D8 distance, snap-implied area within band | 0 violations today; would have caught the D8 bug on the first build for one comparison per edge |
| B2 | **Stamp `global_node_id` provenance into all four covariate tables; refuse to join on mismatch** | finding 2 — the largest blast radius in the pipeline |
| B3 | **Key `_state_daily_wide` on a cohort fingerprint** | finding 3 — otherwise undetectable |
| B4 | **Stop `site_view` persisting an all-NaN distance column as valid** | finding 4 — today's D8 fix cannot fail loudly until this changes |
| B5 | **Assert the containment graph is a DAG**; decide whether `flag_not_contained` has a purpose, then fix or delete it | a cycle means water flows uphill — currently 3, all duplicate gauges. The flag is dead code, but a sensor outside its own basin is often correct, so the naive fix yields 15 flags of unproven value |
| B6 | **`--site` must not write the global stamp**; `_cached_grid_env.cache_clear()` after stamping | findings 7 and 9 |
| B7 | Narrow the two `except Exception` blocks that manufacture data (`site_view`, `_make_region`); make the bbox comparison geometric | findings 4 and 11 |
| B8 | Add `access.py` + `crs.py` to `grid_build_env`; fingerprint the D8 accumulation cache by code | findings 15 and the residue of the class the D8 bug came from |
| B9 | Existence check on `_make_comid_attrs`; stamp `iowa_flowlines.parquet` with its bbox | finding 8 |
| B10 | Add `_DEAD_SURPLUS_PREFIX` to both cache keys; extend `clear_site_caches` to the feature layer | finding 14 |
| B11 | Atomic writes (tmp + replace) in every builder | finding 13 |
| B12 | **Merge sequential re-registrations; do NOT deduplicate by distance** — a pair is one site only if it is near-coincident AND its records do not overlap in time | 3 in-contract pairs at <52 m are sequential with 0 shared days (gaps 142/186/1235 d) and are exactly the 3 graph cycles. 8 pairs at 200 m-1.9 km are *concurrent* with r = 0.006-0.589 — distinct sensors. Distance alone would have merged all 11 |
| B13 | **Classify site type from the existing metadata; subtract the treated contributing area for sensors below a treatment feature** | 13/116 sites are wetland/pond/tile/ditch monitors, not stream gauges. A wetland outflow reads 47-88% below its inflow and no watershed covariate can express it |

### C. Tests

All are pure functions of built artifacts, run in seconds, and pass on the current tree. Today's entire suite is `src/selftest.py` — no `tests/`, no pytest, no CI — and several of its assertions are tautologies.

| | | catches |
|---|---|---|
| C1 | **`max(dist_to_sensor) / basin_diameter < 6.0`**, all 116 grids (observed max **3.84**) | the D8 bug, without any D8 machinery |
| C2 | **`Σ(cell_area × frac) / preferred_basin.area ∈ [0.999, 1.001]`** (observed ±3.1e-5) | stale grids, PROJ drift, re-pointed basins, renumbered `global_node_id` — one cross-artifact tripwire |
| C3 | **Completeness**: every contract site has a basin, grid, water parquet, covariate row, graph node | silent holes; report the set difference, not a boolean |
| C4 | **Cohort reconciliation** across all twelve lists | both 2026-07-30 bugs were this shape |
| C5 | Graph invariants (B1's four) as a test, not only a build warning | runs without a rebuild |
| C6 | `global_node_id` set equality across covariate tables + stamp match | finding 2, short of not making the mistake |
| C7 | Raise sample sizes or sample **adversarially** — smallest/largest basin, most/zero donors | 5 sites of 116 is how the D8 bug hid across 28 |
| C8 | No column entirely NaN, any grid or recipe frame | finding 4 and its relatives, in one assertion |
| C9 | Audit all five CV folds, not just the holdout | the folds that actually train models are unaudited |
| C10 | Smoke test per CLI flag that promises a file | `--dump-curve` raised on its first row and shipped broken |

## Evidence quality

**Measured on the live tree:** every count and range quoted — the 116↔123 re-run, the 236/240 edge split and per-pair geometry, the 15 uncontained sensors, the 45.1% flowline intersection, the 131 water parquets, the C1/C2 observed ranges, and the coverage values for all 7 tiny sites.

**Read from code, not triggered:** findings 2, 3, 4, 7, 10, 11, 13, 14, 15. Each is traced through the call path and correct on the current tree — latent, not active.

**Withdrawn from the earlier draft:** that all 236 containment edges "verify" by point-in-polygon (tautological — they are *constructed* by that test) and that every D8-only edge is therefore wrong (3 of 9 are genuinely ambiguous).

**Not measured:** the model impact of any of this. That 28 sites had a corrupted `dist_to_sensor` is certain; how much any score moves is not.

---

# Appendix — enough detail to execute

## A1/A2. Per-step filter contract and the cohort manifest

Each build step exposes a pure function returning verdicts; no step mutates a site list.

```python
def verdicts(candidates: list[str]) -> dict[str, Verdict]:
    """Verdict = (status, reason, metric) where status ∈ {keep, drop, unjudged}."""
```

- `unjudged` is a first-class outcome and is **never** silently equivalent to `keep`. Today an unreadable geometry and a healthy basin are indistinguishable; `filter_sites` now reports `unjudged_sites` (40 candidates have no delineation on disk).
- Steps run on the **candidate universe** from `raw_site_list.csv`, not on the surviving set.

Where each filter moves:

| filter | from | to | evidence it needs |
|---|---|---|---|
| quantity (`MIN_NITRATE_ROWS`) | `filter_sites` | stays | non-NaN row count |
| quality (`KNOWN_BAD`, detectors) | `filter_sites` | stays | the series |
| tiny (`MIN_CELL_COVERAGE`) | `filter_sites` | **`_make_basins`**, after grid_global | basin polygon ∩ `grid_global` |
| big_basin (LOFO bridging) | `pipeline_config.toml` list | **`_make_aux`**, computed | containment graph, sites-contained count |

`big_basin` becomes computed rather than hand-maintained: rank candidate hubs by **sites contained** (not area — that is the documented selection criterion), drop while the largest conflict-graph component exceeds a target share of rows. Compute once on the full candidate graph. Emit the ranking so the choice is legible.

Manifest — `processed/cohort.json`, the single authority:

```json
{
  "build_id": "20260731T...",
  "candidate_universe": 171,
  "sites": {
    "WQS0109": {"island_ok": false, "network_ok": false,
                "drops": [{"step": "basins", "filter": "tiny",
                           "metric": 0.250, "threshold": 0.5}],
                "has_donor": true, "n_donors": 2}
  },
  "provenance": {"grid_build_env": {...},
                 "artifacts": {"grid_global.parquet": [size, mtime_ns], "...": []}}
}
```

`access.get_site_ids()` reads `sites` where `island_ok`; a network run filters on `network_ok`. Nothing else derives a cohort.

## B1. The four cross-source assertions

Against `basin_containment_graph.parquet` + `get_basin_area`, all currently passing:

```python
assert nx.is_directed_acyclic_graph(graph)                  # FAILS today: 3 two-cycles, all duplicate gauges
assert all(area[c] <= area[p] for c, p in edges)            # 0 violations / 236
assert not any((p, c) in edges for c, p in edges)           # antisymmetry = the 2-cycle special case; same 3
assert edges["flow_dist_m"].notna().all()                   # 236/236 since today
# and per site, at build time:
assert 0.33 <= acc_at_outlet * cell_area / basin_area <= 3.0  # 115/116; WQS0066 = 4.93
```

The last is the band `_snap_outlet` already enforces — asserted post-hoc on the artifact rather than trusted at build time. Report disagreement **counts** rather than failing: steady state is ~5 sub-resolution containment-only edges and 0 unexplained D8-only edges, so a regression shows as the count moving.

## B2. `global_node_id` provenance

Write `interim/_grid_env.json` alongside `grid_global.parquet` containing the `covariate_bbox` used, the cell count, and a hash of the sorted `global_node_id`. Each covariate builder stamps the same block into its own sidecar and refuses to build or join on mismatch. Reuse `access.grid_build_env`'s shape.

## B4. The `site_view` swallow

Either let the exception propagate so `_make_site_grids` records a real failure, or have `build_one` refuse to write:

```python
if grid.empty or grid["dist_to_sensor"].isna().all():
    return 0   # not a grid; let get_grid rebuild live rather than caching a hole
```

Also set `equal_nan=False` in `selftest._frames_match`, which currently makes two identically-NaN columns compare equal — blinding the strongest test to the most likely silent failure.

## B5. Graph acyclicity, and the dead flag

```python
assert nx.is_directed_acyclic_graph(graph), nx.find_cycle(graph)
```

Currently fails on 3 two-cycles — `WQS0032`↔`USGS-05483600`, `USGS-05481000`↔`WQS0081`, `USGS-05451210`↔`WQS0024` — each a pair of sensors 0.00-0.05 km apart with byte-identical basin areas, i.e. one physical gauge registered twice. Deduplicating (B12) removes all three and the graph becomes a DAG. `features._best_neighbors` already patches around them at read time with a mutual-containment area tie-break; nothing else does.

`flag_not_contained` (`_make_basins.py:474`) requires **all four** candidate basins to exclude the sensor, and basin2 or basin3 almost always contains it, so it cannot fire. That is dead code and worth removing on its own. But the obvious repair — testing the *selected* basin, `dist[basin_type] > 0` — would fire on 15 sites, and the two most extreme (`WQS0052` at 16.6% of basin diameter, `WQS0066` at 16.5%) are **confirmed-correct delineations**: a gauge legitimately sits below its basin outlet. So there is no evidence any of the 15 is a defect. Decide what the flag is for and calibrate it against known-good cases, or delete it — do not simply repair the predicate.

## B12. `merge_duplicates` — a new water-build step

Determinable entirely from `raw_site_list.csv` coordinates and the raw series, so it belongs in the WATER build (after `download`, before `make_water`) with no forward dependency on basins, grid or D8.

**Rule.** Two uids are one site iff they are near-coincident **and** their nitrate records barely overlap:

```python
NEAR_M          = 100    # merges observed <=52 m; nearest concurrent-distinct pair is 369 m (7x gap)
MAX_SHARED_DAYS = 90     # merges observed <=22 d; min concurrent-distinct overlap is 389 d (18x gap)
```

Both thresholds sit in a wide empty band, so neither is delicate. Distance alone is NOT sufficient: 8 pairs at 200 m-1.9 km are concurrent with r = 0.006-0.589 and are distinct sensors.

**Survivor naming.** Prefer the **IWQIS** uid. In all three material pairs the IWQIS member is the *current* deployment and the longer record, so the surviving id also names the sensor that is still reporting.

Two guards, because a bare agency preference misfires:

- **Never let a stub name the site.** In `USGS-05482500`/`WQS0082` the IWQIS side has 22 days against 5,265 — preferring it would name a 14-year record after a 3-week handover stub. Require the preferred side to hold at least a substantial fraction of the merged record (10% is comfortable; the real pairs are 51-88% IWQIS, the stubs are all <1%).
- **Never prefer a `WQS99xx`.** Those are temporary/test loggers, so `WQS0005` beats `WQS9904` regardless of agency.

Equivalently: rank by record length, tie-break toward IWQIS. That rule and the stated preference agree on all 10 pairs, which is a useful check that neither is doing something surprising.

**Merge semantics.** The merged site carries data from both agencies, so any code branching on the uid prefix to infer the source will be wrong for it — the surviving `WQS####` ids each hold a USGS-collected head. Records are non-overlapping by construction, so concatenation is unambiguous. On the few shared days (<=22) prefer one side by a stated rule; agreement is good where it can be checked (`USGS-05482500`/`WQS0082`, 22 shared days, **r = 0.898, median difference -0.02 mg/L**), so the choice is low-stakes but must be recorded.

**Record the alias.** The cohort manifest carries `merged_from: [...]` for the survivor and an entry for each absorbed uid pointing at it, so a historical reference to `WQS0032` still resolves.

**What it selects today** — 10 pairs, of which 3 matter (both members in the contract, both carrying substantial data, and each one a graph cycle):

| survivor | absorbed | gap | daily obs (survivor / absorbed) |
|---|---|---|---|
| `WQS0032` | `USGS-05483600` | 142 d | 2,540 / 1,490 |
| `WQS0081` | `USGS-05481000` | 186 d | 1,484 / 1,254 |
| `WQS0024` | `USGS-05451210` | 1235 d | 2,612 / 367 |

The other 7 pair a live site with one that has no nitrate or is a `WQS99xx` logger. **4 pairs are unjudgeable** (`WQS0043`/`WQS9905`, `WQS0066`/`WQS9907`, `WQS0067`/`WQS9906`, `WQS0065`/`WQS9907`, all 14-24 m) because a raw parquet is missing on one side — report them, do not silently merge or skip. The nearest rejected pair is `USGS-05418720`/`WQS0110` at 819 m with zero overlap and identical 4829 km² basins; 819 m is a real distance and two gauges on one reach legitimately share a drainage.

**Blast radius.** Merging takes the cohort 116 -> 113 and *lengthens* three records substantially, which moves `MIN_NITRATE_ROWS` outcomes, every score, and the fold structure. It also dissolves all 3 graph cycles, making the containment graph a DAG. Rebuild-affecting; do it deliberately.

## B13. Site TYPE — classify, then subtract the treated area

The 8 near-but-uncorrelated pairs are not a data problem. They are **paired inflow/outflow monitoring of engineered nitrate-removal features**, and the metadata says so explicitly:

```
WQS0012  "monitors the INFLOW of water to the CREP wetland on Slough Creek"    median 9.0 mg/L
WQS0008  "monitors the OUTFLOW of water from the weir of the CREP wetland"     median 4.8 mg/L
```

Five of the eight are that pattern, and the downstream member is lower every single time:

| pair | what it is | median in → out | reduction |
|---|---|---|---|
| `WQS0012` → `WQS0008` | CREP wetland, inflow → weir outflow | 9.0 → 4.8 | 47% |
| `WQS0101` → `WQS0112` | Mud Creek → Mud Creek **Wetland Outlet** | 11.1 → 2.3 | 79% |
| `WQS0058` → `WQS0059` | Willow Creek → **Perry Pond** (Grinnell College) | 5.0 → 0.9 | 82% |
| `WQS0025` → `WQS0026` | Catfish Creek **nutrient-trading study**, sites 1 and 2 | 3.2 → 0.9 | 72% |
| `WQS0002` → `WQS0076` | Clear Creek (UofI watershed) → **Drainage Ditch**, "IWA site" | 5.1 → 0.6 | 88% |

The remaining three are simply different water: `WQS0055`/`WQS0056` are two **ARS drainage-tile** outlets (17.9 and 15.7 mg/L — tile drainage, far above any stream); `WQS0064`/`WQS0091` are a mainstem and a tributary near their confluence (2009 vs 124 km²); `WQS0065`/`WQS0066` are two separate systems, a river and a ditch, each a "state load estimate site" below a different USGS gauge.

**This is a modelling problem, not a pipeline defect.** The models predict stream nitrate from watershed land use, crops, surplus and weather. A wetland outflow's nitrate is set by the wetland, not by the watershed — the covariates say "high-nitrate agricultural catchment" and the sensor reads 2.3 mg/L because 79% was removed in between. **No feature in the recipe can express that**, so these sites carry irreducible error and are being trained on as though they were ordinary stream gauges. A tile outlet is worse still: it measures drainage water directly, and its delineated "basin" is a field, so the whole covariate aggregation is meaningless for it.

Scanning `iwqis_river` / `iwqis_description` / `iwqis_nickname` across the contract:

| type | n | sites |
|---|---|---|
| wetland / pond / oxbow / CREP | 5 | `WQS0008`, `WQS0012`, `WQS0060`, `WQS0107`, `WQS0108` |
| tile / edge-of-field | 4 | `WQS0054`, `WQS0055`, `WQS0056`, `WQS0080` |
| ditch | 4 | `USGS-05482300`, `USGS-05482500`, `USGS-06604440`, `WQS0066` |
| **any non-stream type** | **13 / 116 (11%)** | |
| research / experimental marker | 27 / 116 (23%) | |

**None of this is recorded anywhere**, and the metadata is already fetched — `iwqis_river`, `iwqis_description` and `iwqis_nickname` sit in `raw_site_list.csv` and no code reads them.

It also closes a loop from §1: **`WQS0066` is the "Monona-Harrison Ditch"** — an engineered channel. Its outlet snap is the one that survives the D8 fix at 4.93×, and a constructed drainage ditch is exactly what routing over a natural-terrain DEM should be expected to fail on. That is a physical mismatch, not a remaining bug.

**Recommendation — classify the type, then subtract the treated area.**

1. **Classify** site type from the existing metadata into the cohort manifest. Costs nothing; the columns are already fetched.
2. **For a sensor below a treatment feature, remove the treated contributing area from its basin.** The covariates should describe the land whose runoff actually reaches the sensor untreated. For the CREP pair that is `basin(WQS0008) − basin(WQS0012)`, leaving only the land draining directly to the outflow gauge. The containment graph already encodes which basins are nested, so no new geometry machinery is needed — this is a polygon difference plus a re-run of the covariate aggregation for the affected sites.

Four caveats, none fatal:

- **It assumes 100% removal.** Measured reductions are 47–88%, so subtracting the whole overlap over-corrects. It is still far closer than the status quo, which counts the treated area at full weight. A weighted version (scale the overlap's covariates by 1 − removal fraction) is strictly better if a removal estimate is available, and the inflow/outflow pair *is* that estimate.
- **It only works where both ends are instrumented.** These 5 pairs are the treatment features we can see. An uninstrumented wetland or plant upstream of any other site is invisible and uncorrectable — so this fixes the known cases, not the class.
- **Sign is not always negative.** A wetland removes nitrate; a wastewater outfall adds it. The correction direction has to follow the feature type, not be assumed.
- **Tile and edge-of-field sites are not fixable this way.** `WQS0055`/`WQS0056` measure drainage water directly and their "basin" is a field — there is no treated sub-area to subtract, the delineation itself is the wrong abstraction. Those need excluding or a separate treatment.

3. **Decide per model** for what remains: exclude non-stream types, carry the type as a categorical feature, or keep and report performance per type. Doing nothing is the current state and the only clearly wrong option.

**Testable now, cheaply:** these 13 sites should be among the worst-predicted. `eval_<task>.csv` from the neighbour-count experiment carries per-site scores — joining on this classification tests the hypothesis with no new fits.

## B13b. Literature check — how to quantify the removal, and what will NOT learn it

Searched 2026-07-31. Full sourcing in the research thread; the load-bearing points:

**Nothing will discover the wetlands automatically.** The USGS Delaware River Basin group runs the most advanced river-network GNN programme in existence and hit exactly this problem with *reservoirs* — a node-level transformation with an unobserved control. Their answer across four papers was to add reservoirs as an explicit heterogeneous node type, feed in release data, simulate release temperature with a physics model, and add data assimilation ([arXiv:2110.04959](https://arxiv.org/abs/2110.04959), [arXiv:2202.05714](https://arxiv.org/abs/2202.05714)). Message passing uses *shared weights* across edges, so a 47-88% removal at one specific edge is representable only if something distinguishes that edge — an edge feature or a node type, i.e. the flat-feature solution with more machinery.

**A per-site random effect contributes exactly zero under our CV.** The BLUP for site *j* is a shrunk function of *j*'s own residuals; at a held-out site it is the population mean by construction. Under LOFO grouping it buys nothing at test time while inflating the LOSO−LOFO gap. Same applies to learned per-node embeddings (Graph WaveNet's adaptive adjacency, EA-LSTM) — they are transductive.

**Our graph specifically defeats the main argument for a GNN.** The containment graph is *transitively closed* (measured: 0 of 116 sites have a directed-reachable site that is not already a direct edge), so multi-hop propagation — the thing message passing adds over a flat donor block — has nothing to do on it. A GNN would have to be built on the **COMID** graph, where unobserved reaches act as intermediates, the way the Delaware's 456 segments do.

### The Crumpton equation — a calibrated replacement for "subtract the overlap"

Crumpton, Stenback, Miller & Helmers (USDA-FSA, [PDF](https://www.fsa.usda.gov/Internet/FSA_File/fsa_final_report_crumpton_rhd.pdf)), fitted on **Iowa CREP wetlands** — the exact population `WQS0008`/`WQS0012` belong to:

```
% nitrate mass removed = 103 × HLR^(-0.33)          R² = 0.69   (34 wetland-years, 12 wetlands)
HLR = annual watershed water yield ÷ (wetland area / watershed area)
```

Anchor points: area ratio 0.57% → 40% removal; 2.16% → 78%; 2.25% → 68%. **That 40-78% band brackets our observed 47-88%.** Peer-reviewed follow-up: Crumpton et al. (2020), J. Environ. Qual. 49(3), [doi:10.1002/jeq2.20061](https://doi.org/10.1002/jeq2.20061) — 9-92% removal across 26 wetlands, driven mainly by HLR.

**This upgrades B13's subtraction.** Subtracting the whole overlap assumes 100% removal; Crumpton supplies the actual fraction, so weight the treated sub-basin's covariates by `(1 − 1.03·HLR^-0.33)` instead of zeroing them. Every input is already computed except **wetland surface area**, which is one number per structure.

For sites further downstream, Anderson, Schilling, Just & Seo (2024), Front. Env. Sci. 12:1416018 ([link](https://www.frontiersin.org/journals/environmental-science/articles/10.3389/fenvs.2024.1416018/full)) measured an Iowa wetland at 74.2% removal and found downstream concentration reduction tracks *treated drainage-area fraction* roughly 1:1 (19% reduction at 16.3% treated, ~0% at 0.03%). So `Σ_structures (treated_area_fraction × removal_fraction)` is a single scalar that degrades gracefully with distance. (That composition is a synthesis of two published results, not itself a published recipe.)

### Lag 0 is the wetland signal, and we drop it

`neighbors.py` emits donor nitrate at lags 1 and 3; every arm is `*_no_lag0`, declined at the recipe layer pending the forecast/nowcast decision. **For an inflow/outflow pair a few hundred metres apart, travel time is minutes — lag 1 is already a day stale, so lag 0 is the only lag that carries the wetland signal.** A distance-gated lag-0 donor column is a one-file change on infrastructure that already exists.

Caveat that must be decided first: a lag-0 donor is legitimate for nowcasting/gap-filling, *not* for prediction at an ungauged site, and it will widen the LOSO−LOFO gap. Report it as a separate arm.

### Independent confirmation of the donor block

Saha, Rahmani, Shen, Li & Cibin (2023), STOTEN 878:162930 ([PubMed](https://pubmed.ncbi.nlm.nih.gov/36934914/)) trained an LSTM on **42 Iowa stream nitrate sites** — our network — reaching median NSE 0.75, and flags neighbouring-site concentration as a crucial predictor. Also note Gorski et al. (2024), WRR 60 e2023WR036591: across 46 agricultural sites, a single pooled global model was *not* the best of four pooling schemes; similarity-clustered pooling won. Our basin-family CV already does that structurally.

### Scale

Kratzert et al. (2024), HESS 28:4187 ([link](https://hess.copernicus.org/articles/28/4187/2024/)) give the only clean curve: median NSE 0.60 (1 basin) → 0.69 (107) → 0.71 (531). Going from ~100 to 531 basins buys 0.02. At 116 sites we are on the flat part — enough data to *train* a graph model, not enough to expect it to beat a tuned GBDT.

## B13c. Prior art — what already exists, and where the literature fails

Searched 2026-07-31. This is project positioning rather than a pipeline finding; it may deserve its own note. Verdict: **partially novel, and the novel slice is thinner than the framing suggests.**

### SPARROW already does the spatial half

Calibrated on monitored mean-annual loads, then applied to **every** NHDPlus reach including unmonitored ones — ungauged prediction is its design, not a gap. Midwest model catchments average **2.7 km²**, finer than we will achieve, nationally, with downloadable outputs and uncertainty ([SPARROW Mappers](https://www.usgs.gov/mission-areas/water-resources/science/sparrow-mappers)).

And the temporal gap is smaller than assumed: USGS released a **national seasonal dynamic SPARROW, 2000-2020** (`doi:10.5066/P1XIOCF8`). So our added axis is *seasonal → daily*, not *annual → daily*.

Do **not** benchmark against its headline TN fit (95% of variance in loads, Robertson & Saad 2019, [SIR 2019-5114](https://pubs.usgs.gov/publication/sir20195114)). Load = concentration × flow, and flow spans orders of magnitude across reaches, so most of that R² is drainage area. It is not commensurable with a concentration R².

**SPARROW is also a candidate feature**, not just a baseline: a COMID-keyed long-term mean that a daily model predicts departures from.

### The closest competitor is on our own sensor network

Saha, Rahmani, Shen, Li & Cibin (2023), STOTEN 878:162930 ([PubMed](https://pubmed.ncbi.nlm.nih.gov/36934914/)): LSTM, **42 Iowa gauges**, **daily nitrate concentration**, median **NSE 0.75**, RMSE 1.53 mg/L. And it reports that *"average nitrate concentration of neighboring sites was a crucial determinant"* — that is our network variant, published, on this network.

What it does not do: an **island variant with no donor gauge**, a true spatial holdout (it is gap-filling at monitored locations), arbitrary-point delineation, or a deployed product. **The island/network contrast is the one axis nobody occupies.** Saha established the donor matters; nobody has quantified what it costs to lose it.

### We are aimed at the known failure mode

Pandit et al. (2025), WRR ([10.1029/2024WR039207](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2024WR039207)) — CONUS LSTM, daily nitrate concentration:

| validation | median KGE |
|---|---|
| temporal (monitored sites) | 0.60 |
| **spatial (ungauged)** — CONUS | **0.18** |
| **spatial** — Mississippi basin | **0.34** |

Corroborated by Xia et al. (2025) ([arXiv:2503.09947](https://arxiv.org/html/2503.09947v2)): under spatial splits, temperature (0.88) and DO (0.71) transfer fine but nutrients land at median **KGE ≪ 0.4**. Daily concentration at ungauged sites is where this literature falls over, and nutrients are the worst-behaved family in it.

**Evaluation trap, and it runs against us.** Saha's NSE 0.75 is a *temporal* holdout at monitored sites. Comparing our LOFO score to it is apples-to-oranges in our own disfavour. The correct comparator for the island model is Pandit's **spatial** validation (~0.34 in the MRB); against that bar a decent LOFO result is a real contribution.

### Also in the space

- **Jain et al. (2025) "XGBest"**, STOTEN 963 ([PMC11833449](https://pmc.ncbi.nlm.nih.gov/articles/PMC11833449/)) — XGBoost on watershed attributes for **daily** TN/TP/TSS, 499→1,479 CONUS sites, open source. Our model class and target; it flags ungauged prediction as *future work*, i.e. a 2025 paper naming our niche as open.
- **Tso et al. (2023)**, Front. Water — 2,343 GB stations, RF, nitrate test R² 0.71, ~200,000 reaches, **plus a deployed web viewer**. Closest match to our *product form*; seasonal means, not daily.
- **Kratzert et al. (2019)**, WRR — the PUB recipe to borrow: regional LSTM + static attributes over 531 CAMELS basins, median out-of-sample **NSE 0.69**, beating calibrated SAC-SMA and the National Water Model.
- **StreamStats** — the pin-drop → delineate → regression pattern already ships as a USGS product, for flow. Nobody has wired it to daily nitrate.
- **IIHR NFEWS** ([announcement](https://iihr.uiowa.edu/all-news/2026/07/new-university-iowa-study-uses-nasa-earth-observations-advance-nitrate-forecasting), July 2026) — 3-year NASA-funded pilot, **60 IIHR real-time stations**, integrating into IWQIS, partnered with Des Moines Water Works, Cedar Rapids and Iowa City, explicitly extending to "areas with limited measurements". Same sensor network, will be a deployed product, institutional partnerships. Architecture not public.

### Implications

1. **Reframe.** Not "prediction at ungauged locations" (SPARROW) and not "daily nitrate ML" (Saha, Jain). The defensible claim is daily concentration under a **true spatial holdout**, with the island/network contrast quantified.
2. **Lead with the island model.** It is the part nobody has.
3. **116 sensors is small for PUB.** Kratzert used 531 basins; Pandit used CONUS. The binding constraint on the island model is the number of *distinct basins*, not features — so pooling beyond Iowa is likely higher-leverage than further feature engineering. This bears directly on the Minnesota expansion.

## B13d. Data sources for locating the invisible sinks

Searched and **endpoint-verified** 2026-07-31. Ranked by value x obtainability.

### 1. National Inventory of Dams — take this first

`https://nid.sec.usace.army.mil/api/nation/csv` (67 MB, verified 200). Downloaded and parsed: **4,128 Iowa dams**, with `Surface Area (Acres)` populated on 4,053 and `Drainage Area (Sq Miles)` on 4,111.

**That is the complete input set for `103 x HLR^-0.33`, already tabulated, free, unrestricted.** Median pond is 4.7 ac over 0.33 sq mi drainage — a **pool/watershed ratio of 2.2%**, right at the top of the CREP 0.5-2.0% design band, so these are hydraulically CREP-like structures. Purpose breakdown includes 2,739 stock/farm ponds, 431 flood-risk, **260 grade stabilization**, 63 fish-and-wildlife ponds. Point geometry, `Year Completed` on 4,102.

**Integration: hours.** Snap to COMID, join to basins, compute HLR. This is the single highest-value item in this audit's research and it will almost certainly surface sinks upstream of the flagged sensors that we cannot currently see.

### 2. NHD Waterbody — an explicit treatment flag, on keys we already have

`https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/12`, carrying `AREASQKM`, `FTYPE`, `FCODE`, `REACHCODE`. Verified counts over the Iowa bbox: **FCode 43624 "Reservoir: Treatment" = 120**, 43613 "Reservoir: Water Storage" = 238, 43600 generic = 70.

Polygon area yes, contributing watershed area no — but we compute that ourselves already. FCode 43624 is the closest thing to a nationally consistent "engineered treatment feature" flag, and we are already COMID-keyed. **Integration: low.**

### 3. NWI — the recall net, and it does distinguish constructed

Iowa geodatabase `https://documentst.ecosphere.fws.gov/wetlands/data/State-Downloads/IA_geodatabase_wetlands.zip` (547 MB, last modified 2026-05-01, verified). Carries **`ACRES`**.

Constructed-vs-natural is expressed through Cowardin special modifiers — **`x` excavated, `h` diked/impounded, `d` partly drained/ditched, `K` artificially flooded** — and a live query over a Story/Hamilton bbox returned `PUBFx`, `PUBKx`, `PEM1Ch`, `PEM1Cd` etc., roughly a third of polygons carrying a human-modification modifier. Caveats: no watershed area, 0.5-acre minimum mapping unit, Iowa source photography is old (the DNR mirror is literally `NWI_2002.zip`), and `x`/`h` do not mean "built for nitrate treatment" — a stock pond and a CREP cell both land on `PUBHh`. **Use as recall, intersected with NID, not as a classifier.**

### 4. geodata.iowa.gov conservation practices — verified, and it is the wrong geometry

The lead turned out to be the **Iowa BMP Mapping Project** (ISU GIS Facility + Iowa DNR), `https://iowageodata2.s3.us-east-2.amazonaws.com/farming/conservation_practices.zip` (149 MB, 2022-01-28, no auth). Six statewide layers: grassed waterways (585,083 polygons), terraces (530,472 lines), WASCOBs (251,516 lines), **pond dams (112,689 lines)**, contour buffer strips, strip cropping.

**Killer limitation: `IA_POND_DAM` is the dam crest LINE, with no area field at all.** Recovering pool area means pour-filling each line against a DEM — possible given our LiDAR-grade DEM, but a modelling job, not a join. **There are no wetlands in this dataset.** And `DATE_CREATED` is the *digitizing* date, not installation; practice occurrence traces to a 2007-2010 imagery baseline.

**Verdict: high coverage, wrong geometry.** Good as a per-basin density covariate (counts and line-lengths upstream); useless for the removal equation.

### 5. Tile drainage — two Iowa layers better than what we have

We already hold AgTile-US (`src/data/raw/agtile/`). Verified additions, all unauthenticated S3:

- **`farming/Drainage_Districts.zip`** (12.9 MB, 2018) — legal drainage-district boundaries. **This is the layer our tile-outlet sites want**: an actual engineered-drainage administrative footprint, where a delineated "basin" is meaningless.
- `farming/tiled_soils.zip` (66 MB, 2019) — soils requiring tile for full productivity, cropped. An Iowa-calibrated tile-likelihood polygon, arguably cleaner than AgTile within Iowa.
- `farming/ag_drainage_wells.zip` (121 KB, 2017) — direct-to-aquifer points.

### 6. Iowa CREP — no public GIS layer; the path is an email, not a download

Searched hard: the Iowa DNR ArcGIS org returns **0 items** for "CREP". The programme sites and annual reports give **aggregates only** — 91 wetlands complete by 2019, **873 acres of pool over 116,923 acres of protected drainage**; the 2021 monitoring report names its wetlands only by nickname with locations as dots on a figure.

**Section 1619, plainly:** it bars USDA/FSA/NRCS from disclosing producer-supplied information and is a FOIA Exemption 3 statute, so CLU boundaries and CREP *contract* records are genuinely blocked. It does **not** bar (a) non-USDA parties mapping the same physical structures from imagery or LiDAR — which is why the ISU BMP layer and NWI are legal and public — or (b) **IDALS's own state-held records**, since IDALS is not USDA and holds the state easements under Iowa public-records law.

**So: request from IDALS Division of Soil Conservation and Water Quality, and ask Bill Crumpton's ISU Wetland Research Lab directly** — his group holds the per-wetland pool and watershed areas behind the equation we want to use.

**Two facts that partly rescue this without any request.** CREP wetlands are sited at a **0.5-2.0% wetland-to-watershed ratio** by design, so for any located wetland the watershed area is bounded to within a factor of 4 from pool area alone. And the 91-wetland population averages **9.6 acres pool over ~1,285 acres drainage (0.75%)** — a usable prior.

### 7. ACPF — potential siting only; do not budget time

`https://acpf4watersheds.org/` maps where practices *could* go, not where they are. An inspected real output carries exactly the schema we want (`PoolAreaAc`, `ContAreaAc`, `PoolStorAF`) — for **hypothetical** wetlands. Useful only as a counterfactual layer ("how much removal capacity is missing here").

### Recommended order

NID first (hours, complete equation inputs, 4,128 structures) → NHD FCode 43624/43613 on existing COMIDs → NWI modifiers as recall → Drainage Districts for the tile-outlet sites. Treat the ISU BMP layer as a density covariate. Skip ACPF. For CREP, stop searching and send an email.

## C1/C2. The two artifact checks

```python
# C1 — per grid
diam = 2 * math.sqrt(basin_area / math.pi)
assert grid["dist_to_sensor"].max() / diam < 6.0        # observed: min 0.95, median 2.26, max 3.84

# C2 — grid vs preferred_basin.csv
r = (grid["cell_area"] * grid["frac_cell_in_basin"]).sum() / row[f"area{basin_type}"]
assert 0.999 <= r <= 1.001                              # observed spread ±3.1e-5
```

Both are pure functions of built parquets, need no weather bundle, and belong in a `selftest` group that can run without the 4.6 GB download — which is the prerequisite for any CI at all.
