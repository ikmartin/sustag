# data — the pipeline, the read surface, and the stores

Everything that turns published water-quality data into something a model can read. Four subpackages, one rule between them, and a build that halts and waits for a person rather than guessing.

The vocabulary you need before anything below makes sense:

- **channel** — a quantity a sensor can measure (nitrate, discharge, water temperature). Three sources spell the same channel three different ways; `build/registry.py` is the only place that knows how.
- **AOE** — area of expected effect. The geographic scope the census is drawn from; it is expanded deliberately and never on its own.
- **SNR code** — the project's own sensor id, minted once in `a2` and never re-derived.
- **ledger** — a CSV of pending decisions a human owns. The build writes proposals into one and stops; it does not decide.
- **snapshot** — a manifest naming a state of the raw and acquired archives. Not a copy: the files stay in place.

## The four subpackages

| | what it is | writes to |
|---|---|---|
| `build/` | the pipeline: acquire → derive → publish, plus `verify` and the review panel | `stores/{acquired,snapshots,derived,published}` |
| `access/` | the read surface every consumer goes through, read-only | nothing (caches under `stores/access_cache`) |
| `insights/` | source-coverage and redundancy diagnostics; regenerates `insights/report.md` | `insights/{report.md,images/}` |
| `stores/` | the data itself — raw, acquired, snapshots, derived, published | — |

## The one boundary that matters

**`access/` never imports `build/`.** That is verified and load-bearing: the read surface must work against a published store with no pipeline in the process, so a model, a notebook, or a downstream project can depend on `data.access` without dragging in the build.

The reverse is allowed and used — `build/verify.py` and `build/derive/snap.py` both import from `data.access.machinery` deliberately, so the build's snap and the runtime's snap are the *same* code and cannot drift into two answers. `insights/` sits above both and imports either freely.

Consumers follow the same discipline one level up: `xgboost/` and `projects/discharge-test/` each route every read through a single `cohort.py`, so "does this need an access-layer change?" is answerable by reading one file.

## Running it

```
python -m data.build.run acquire          # a1 seed (once), a2 census + id mint, a3 fetch + snapshot cut, a4 verify + drift
python -m data.build.run build            # full A -> D -> P against the pinned snapshot
python -m data.build.run d3 --single      # exactly one stage
python -m data.build.run --list           # the stage map
python -m data.build.run verify [stage] [--deep]
python -m data.build.review_panel         # decide every gated queue in one place
python -m data.insights                   # regenerate report.md + images/
python -m pytest tests/data -q
```

Stages: `a1` seed, `a2` census (mints SNR ids), `a3` bulk fetch (records / network / weather / attributes / facilities, ends by cutting a snapshot), `a4` reconcile + units + drift; `d1` extent, `d2` canonicalisation (sentinels + ranges), `d3` geometry (snap + precision + proximity), `d4` identity (type gate, evidence, assembly), `d5` covariates + structures; `p1` publication (partition + masks + the four leak scans).

**A halted build is normal.** A gate raises `SystemExit` naming outstanding ledger rows — the build did what it could and is waiting on a person. Decide in the review panel (writes land in `decisions/*.csv` immediately) and rerun. A blank decision means *not reviewed*, never *approved*; a decision made under a superseded criterion re-queues flagged with its prior verdict.

Current state: **314 tests**, **48/48 verify checks**.

## The ledgers

All in `build/decisions/`, tracked in git, keyed on natural keys, appended to and never rewritten. A sheet with no file yet simply has nothing pending.

| sheet | gates on | vocabulary |
|---|---|---|
| `drift.csv` | conflicting snapshot drift (a4) | `accept` / `reject` |
| `registry_gaps.csv` | unacknowledged source inconsistencies (a4) | `acknowledged` |
| `merge.csv` | gated merge proposals (d4) | `merge` / `distinct` |
| `site_type.csv` | sensors with conflicting type evidence (d4) | the type vocabulary |
| `exclusions.csv` | rows a person opened (d4) | `exclude` / `keep` |
| `composed.csv` | multi-member compositions (d4) | `confirm` |
| `quality.csv` | flagged high-tier series (p1) | `keep` / `exclude` (+ `exclude_spans`, `max_threshold`) |
| `ids.csv` | never — the append-only SNR id mint, written by a2 alone | (not a decision sheet) |

## The access surface

Read-only, stamps checked, published primitives one join away. Caches are optimizations with live fallbacks, never prerequisites.

**Sensors and their records**

```python
get_sensors(columns=None)                      # every registration's full story, one row each
get_record(code_or_uid, channels, window)      # one record's DAILY series with per-day provenance
get_native(uid, channels, window)              # the sub-daily observations exactly as the source published them
get_quality()                                  # every (record, channel) verdict + masks -- why a masked day is absent
get_proximity(max_sep_m=None)                  # located pairs within 2 km of STATED coordinates
```

**Catchment covariates and attributes**

```python
get_covariates(comids, products, years)        # catchment-grain covariates over a plain COMID set
get_attributes(comids, columns, source)        # COMID-keyed attributes, filtered before any row materialises
attribute_columns(source="streamcat")          # column names from the footer alone -- no rows read
```

**Weather and drought**

```python
get_weather(wset_or_comids, window, variables) # area-weighted DAILY weather; one row per day
get_drought(wset_or_comids, window, variables) # area-weighted drought indices -- one row per PENTAD, not per day
drought_columns()                              # every aggregate in the pentad store
```

**Discrete samples** — a distinct class from the sensor networks: WQX results have no `site_uid`, never enter the census, and never become nodes.

```python
get_discrete_nitrate(states, bbox, window, ...)  # QC activities excluded by default
discrete_nitrate_states()
```

**Network geometry**

```python
get_network(layer="flowlines")                 # flowlines | vaa | catchments | waterbodies, as the authority provides
snap(lat, lon)                                 # one point onto the network -- same machinery as the build's sensor snap
accumulate(comid)                              # the upstream COMID set and its areas
```

Durable changes never enter through this layer. They go into a decision ledger and arrive at the next build.

## Where the depth is

This file is a map. The binding detail lives in:

- [`notes/book/data_pipeline_chapter.md`](../notes/book/data_pipeline_chapter.md) — the pipeline as specified: layers, gates, identity, publication.
- [`notes/book/data_inventory_chapter.md`](../notes/book/data_inventory_chapter.md) — every source: what it is, what we take, its quirks, what consumes it.
- [`data/insights/report.md`](insights/report.md) — generated: source coverage, per-theme redundancy, what reaches the cohort.
- [`notes/reports/pipeline-findings.md`](../notes/reports/pipeline-findings.md) — the standing log of everything found and not yet a deliverable.
- [`notes/plans/completion-plan_Aug27.md`](../notes/plans/completion-plan_Aug27.md) — current plan; [`build4_Aug25.md`](../notes/plans/build4_Aug25.md) records how the rebuild landed.

## Facts the loop rests on

- The census is the row authority (**20,233 registrations**) and the **only** minting step. SNR codes are appended to `decisions/ids.csv` and never re-derived — deleting that file is the one act that renumbers everything, which is why it is tracked.
- Every `a3` pass ends by cutting a snapshot; `a4` diffs HEAD against its parent on **content probes** (sha256, parquet schema fingerprint, newest data timestamp), never mtime. A wholesale vendor replacement is one `adoption:`-noted cut, not hundreds of drift rows.
- The registry fingerprint (mappings, scales, sentinels, ranges, stats, combine) stamps every canonical file, so editing the registry legitimately stales the whole canonical store.
- The fetch partition `registered = skipped + cached + source_empty + attempt_failed` is checked after every full `a3`. `source_empty` is durable; `attempt_failed` retries.
- Two canary probes (groundwater level, precipitation) must arrive registered, un-skipped and fetchable on every census — a missing canary is a broken scope filter, not a data gap.
