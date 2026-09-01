# The Data Pipeline

The data pipeline collects data from a myriad of sources and exposes it via a unified access api.

- `data.build`: the build pipeline, go from `data/raw/` to `data/processed/`. It canonicalises, scrubs, resolves identity, aggregates.
- `data.access`: the read-only access layer. It exposes the data to modelers.

Only the broadest possible modeling considerations influence the build pipeline. The slogan: **modify or process the data ONLY if every conceivable model would implement the same cleaning or modification.** For example, garbage test sensors should not be surfaced in the access layer — no model will ever use them — but two neighboring sensors co-reporting near-identical values must be kept separate, because whether they constitute one node is a modeling preference, not a fact.

The slogan has a test, applied throughout this chapter: for any transformation, ask *would a model exist that wants the untransformed value?* A fill value of −999999 cfs fails the test — no model wants it — so it is scrubbed. A choice of drainage basin passes the test — different experiments delineate differently — so basins are not built here. Everything the pipeline does sits on the "no model would disagree" side of that line, and everything it declines to do sits on the other.

In practice the pipeline does the following:

- identify all relevant data streams in a defined region
- unify data coming from different sources under one common regime: one channel vocabulary, one unit system, one definition of a day
- clean obviously faulty sensor values, with every rule and constant documented and every removal counted
- determine the identity of every in-situ sensor, merging registrations only where they name one physical installation
- establish the geometry of the sensor cohort: where each sensor is, how certain that location is, and which sensors are near which
- aggregate gridded covariates to the flow network's own spatial unit
- expose extremely thorough metadata, so that every absence, exclusion, and modification is explicable from the published state alone

The pipeline is deliberately target-agnostic, with two exceptions inherited from the project's objective (see the Introduction): the area of effect is seeded from nitrate sensors, and nitrate alone occupies the high-scrutiny tier that routes decisions to a human. Everywhere else the pipeline records facts; models express preferences over those facts at read time.

Two things the pipeline does **not** do. It builds **no graph**: no nodes, no edges, no site-as-node abstraction. Which sensors become nodes, how they aggregate, and what connects them are the first decisions of a modeling experiment, and the pipeline's job is to publish the raw material — sensor records, the flow network, the proximity table — from which any such graph is derivable. And it builds **no basins**: a drainage basin is a function of the point you care about, and the points belong to the modeler. The access layer ships the *machinery* to delineate and aggregate (§ Basins); the pipeline ships no basin artifacts.
## Contents

- [The Pipeline](#the-pipeline)
- [Channels and Canonicalisation](#channels-and-canonicalisation)
- [AOE Determination and Sensor Discovery](#aoe-determination-and-sensor-discovery)
- [Geometry and Proximity of Sensors](#geometry-and-proximity-of-sensors)
- [Identity and Sensor Merges](#identity-and-sensor-merges)
- [Cleaning, Scrubbing and Filtering](#cleaning-scrubbing-and-filtering)
- [Publication](#publication)
- [The Flow Network](#the-flow-network)
- [Basins](#basins)
- [Covariates](#covariates)
  - [Rasters](#rasters)
  - [Weather](#weather)
  - [Attribute tables](#attribute-tables)
  - [Structures](#structures)
  - [Modelled streamflow](#modelled-streamflow)
- [Gates and the Review Dashboard](#gates-and-the-review-dashboard)
- [Access API](#access-api)
- [Verification](#verification)
- [Unimplemented Future Work](#unimplemented-future-work)

## The Pipeline

The build is a pure function: `build(snapshots, parameters, decisions) → artifacts`. Snapshots are immutable captures of everything acquired; parameters are declared intent (the collection window, the canary roster); decisions are human adjudications in tracked ledgers. Wipe everything derived and the same three inputs reproduce it exactly. Only the acquisition layer touches the network; derivation and publication run offline against a pinned snapshot.

Three layers, run in order by a full build, with acquisition additionally running alone on its own daily cadence:

```
python -m data.build.run acquire            # Layer A alone -- the daily-cadence entry point
python -m data.build.run build              # full A -> D -> P against the pinned snapshot
python -m data.build.run d3                 # from a stage onward (D3 geometry, then D4, D5, P1)
python -m data.build.run p1 --single        # exactly that stage: P1 classify and publish
python -m data.build.run verify [stage]     # the runnable invariants, § Verification
python -m data.build.run verify all --deep  # adds the expensive scans (36 checks against 29)
```

**The targets are layers and stages, not verbs** — `acquire` and `build` are the two layer entry points, and everything else is a stage id (`a1`–`a4`, `d1`–`d5`, `p1`). A bare stage id runs *from* that stage onward; `--single` runs exactly it. Each stage prints what it read, what it wrote, and what it skipped as cached. A gated stage that lacks a decision halts and names exactly which ledger rows are outstanding; a build is complete only when every gating ledger is answered or empty.

**Stages and what to expect from each:**

| stage | does | expect |
|---|---|---|
| A1 | seed discovery in the bootstrap boxes; one NLDI basin per seed | runs once; a few hundred nitrate-bearing registrations, each with a basin or a named failure |
| A2 | census: every sensor any source advertises in the AOE, all channels | ~20,000 registrations; row authority for the sensors table |
| A3 | bulk fetch: observations, network, weather, attributes, metadata files | hours on first run, minutes incremental; immutable snapshot cut at the end |
| A4 | reconcile authorities, verify units, classify snapshot drift | a report naming every gap, unit verdict, and drift row; halts on unadjudicated conflicts |
| D1 | derive the AOE from seeds | a stamped extent polygon; every registration marked seed or not, with reason |
| D2 | canonicalise: native → channels, units, daily primitives, scrub | one canonical daily file per record-bearing sensor; scrub tallies printed |
| D3 | geometry: snap, coordinate precision, the proximity table | every sensor snapped or named-failed; the pair table on disk |
| D4 | identity: merge gate, evidence, ledgered review, record assembly | merged records assembled winner-per-span; halts on contradictions and undecided gated pairs (high-tier or cross-type) |
| D5 | covariates: rasters and attribute tables to catchments | per-product parquet keyed on comid; closure checks pass per grid |
| P1 | publish: classify every registration, apply quality masks, write the store | the partition counted; leak invariants verified |

**Stamps.** Every derived artifact records the rules it was derived under, and every reader checks. The stamp family: the **snapshot id** (which raw state), the **AOE stamp** (which extent — a statement about the region, not its vertices, so re-noding a boundary is not a change), the **namespace stamp** (which uids a store admits), the **schema fingerprint** (which columns), the **registry fingerprint** (a hash of every channel's mappings, scales, sentinels, ranges, and reduction rules — because a registry edit changes the correct output of every canonical file while touching no input file's mtime, mtime alone can never be the cache key), and **ledger hashes** (which decisions). An artifact whose stamps mismatch the current rules is stale, loudly.

**Drift.** Acquisition accrues: new observations append daily, and a trailing revision window (120 days) is re-pulled because sources revise provisional data for months. Each new snapshot is diffed against its parent and every changed file is classified: `added`, `extended`, and `revised_in_window` are the expected classes and pass; `revised_out_of_window`, `removed`, and `schema_changed` mean an authority changed something already relied upon — a repair or an error, both with precedent — and each queues a row in the drift ledger and halts A4 until a human answers `accept` or `reject`. A wholesale, operator-supplied replacement (a vendor re-export) is one event, not hundreds: a snapshot cut with notes beginning `adoption:` classifies its entire diff as `adoption_amnesty`, exactly once, trading per-file audit rows for one snapshot-level record.

**The fetch is accounted, not assumed.** Every registration's fetch outcome lives on the sensors table itself, in one of four states: `skipped(reason)` — a scope rule declined to fetch, with the rule named; `cached` — a prior fetch holds the record; `source_empty` — the source was asked and affirmatively returned nothing; `attempt_failed` — the request errored and will be retried. The last two are different facts and are never conflated: `source_empty` is recorded once and is durable, `attempt_failed` is retried on the next pass. Outcomes accumulate across runs rather than being rewritten, so "when did we last successfully ask" is always answerable. The invariant: `registered = skipped + cached + source_empty + attempt_failed`, a partition, counted at A3's end.

## Channels and Canonicalisation

Three sources name the same quantity three ways: USGS keys columns by parameter code, IWQIS by ad-hoc English names, MPCA by integer variable ids. One **channel registry** is the single extension point for everything a sensor can measure; nothing else in the build may contain a literal parameter code or source column name. Each channel declares its canonical unit, its per-source native mappings and scales, its physical range (§ Cleaning, Scrubbing and Filtering), its daily-statistics tier, and four independent axes that must never be conflated:

- **fetch_priority** (1 | 2) — which channels the census asks for first. A fetch-cost fact, nothing else.
- **scrutiny** (high | standard) — the stewardship tier. Identity and quality decisions touching a high-tier record gate on a human; standard-tier decisions take the rule's answer, recorded. A function of scarcity × consequence, not of any model's target. Nitrate sits alone in the high tier, and an import-time check refuses a tier larger than three channels — it is meant to be small and rarely changed.
- **depth** (full | canary | off) — collection depth. Full is collected everywhere; canary only at roster probes, keeping the path exercised at negligible cost; off is named, so absence is a decision rather than an accident.
- **discriminating** (bool) — whether agreement on this channel is evidence of a shared *instrument* rather than a shared *climate*. Water temperature, stage, pH, dissolved oxygen, and turbidity are storm- and season-driven basin-wide: two sensors 1,878 m apart agree on water temperature at r = 0.9936, and neither is the other. Non-discriminating channels never decide identity (§ Identity).

**Units are a three-state fact**: `declared` (the source documents the unit), `assumed` (our guess, visibly marked), `verified` (settled by cross-source measurement at co-located instruments — the only state that closes the question). The exemplar: MPCA nitrate was verified as mg/L-as-N by comparing 56,125 matching timestamps against a co-located USGS instrument at median ratio 1.0000 — the as-NO₃ alternative would read 4.4269. The cautionary case: FNU, NTU, FNRU, and FBU are different optical geometries, not spellings, so turbidity from sources with undeclared or unverified geometry is held in a separate `turbidity_unknown` channel until *measurement* — never declaration — promotes it. Unit conversions are exact constants: ft³/s → m³/s is 0.028316846592 and ft → m is 0.3048, by definition of the international foot.

**A day is one fixed 24 hours.** All reductions bucket in `Etc/GMT+6` — Central Standard year-round — so no day is 23 or 25 hours and every source's timestamps land in one frame. DST-aware conversion happens once, in the source adapter.

**Seven primitives per channel-day**: `mean min max p25 median p75 n_obs`. Derived quantities (`amplitude = max − min`, `iqr = p75 − p25`) are computed at read time, never stored — a stored difference beside its inputs is two things that can disagree. Channels declared `stats = "mean"` store only `mean` and `n_obs`: discharge and gage height carry nothing a model consumes in their spread, and they drive roughly 93% of the fetch, so the storage rule and the fetch tier agree by construction. Days are sparse: a day with no observation has no row, never a null one.

**A pre-aggregated day writes NULL, not zero.** USGS daily-value columns are already a daily mean; fabricating min and max from that single number would record a measured diel amplitude of zero where nothing was measured, and `n_obs` would claim one observation where the true count is unknown. Both stay null, and the reduction detects the case from the column form, never from per-site assumptions.

**Adding a channel is one registry entry.** The schema generates per-channel columns from the registry, the census registers advertisers on the next pass, canonicalisation produces the series, publication carries it. The two genuinely manual costs: declaring and verifying the new channel's units, and a bounded fetch backfill for sources whose archives hold only requested columns.

## AOE Determination and Sensor Discovery

**The area of effect (AOE)** is the pipeline's extent of record, and it is built once, by a staged bootstrap:

1. **Seed discovery (A1)** finds nitrate-bearing registrations inside hand-drawn bootstrap boxes, from source metadata alone. A registration seeds unless its metadata *proves* it cannot: excluded only if the source publishes series spans and this registration has none (`no_nitrate_series` — a registration with no series, distinct from a short one), or its declared span cannot contain even **1** observation day (`span_under_min`). The floor is 1 day, not higher, because the cost asymmetry is extreme: wrongly seeding a thin sensor slightly enlarges the AOE, while wrongly excluding a real one truncates the upstream half of its basin and nothing downstream reports the loss. A source that declares no spans seeds by default — absence of a declaration is never a declaration of absence. Seed marking is not a cohort filter: a non-seed keeps its row and every column; it only loses its vote on how far the AOE reaches.
2. Each seed fetches one authority drainage basin (NLDI).
3. **D1** unions the bootstrap boxes with the seed basins, drops slivers, and stamps the result. This is the AOE.

The AOE never re-expands on its own. Sensors discovered inside it later are used but do not enlarge it; expansion is a deliberate manual event that re-runs the bootstrap. This is the first of the two places nitrate preference is allowed to exist.

**The census (A2)** then discovers *every* continuous sensor in the AOE — all channels, all sources, all site types. Results are clipped to the AOE geometry, never to the bootstrap boxes: the extent's arms beyond the boxes hold roughly 4,300 sensors, about a quarter again over the in-box count, and a box clip would silently cut off exactly the drainage the extent exists to cover — the count of arm sensors is the invariant that catches it reading zero. The census is the **row authority** for the sensors table: it may re-derive the full table; no narrower pass may.

Every registration gets a namespaced uid, `SOURCE:source_id` (`USGS:05455100`, `IWQIS:WQS0031`, `MPCA:48020005`). Each source adapter declares which of the source's ids are admitted — test and scratch namespaces are excluded *by named rule*, recorded as a choice, and reconciliation between a source's export and its registry compares within the namespace, so a registry that correctly scopes itself does not manufacture false gaps.

**Non-surface sensors are collected.** Wells, groundwater, and atmospheric stations register, fetch, and publish exactly like stream sensors; their type is carried, and the only place the surface/subsurface distinction is *enforced* is the identity layer's type gate (§ Identity). Fetch scoping applies exactly two screens, both decidable from source declarations alone:

- **The window screen.** The collection window has one configured bound: a start date, `2008-01-01`. A sensor is skipped only when every declared series *ends* before it. There is deliberately no upper-bound test — a declaration beginning "after the window" is far more likely a data-entry error or a station registered ahead of deployment than a reason never to fetch, and any upper test against the current year silently starts excluding real stations as the calendar advances. Sources that declare no spans are never skipped by this screen. A registration that declares channels but no dates at all is skipped as `no_declared_series`.
- **The namespace rule**, above.

**The canary roster** is the counterweight to scope filtering's blind spot: the class nobody fetches is the class nobody tests, and its handling rots invisibly until requirements expand onto it. The roster is a small tracked *parameter* — probe sensors chosen one per structurally-at-risk class (a groundwater well, a precipitation station, a grab-regime sensor, one sensor per canary-depth channel) — that **every scope filter must admit**, including discovery itself: a parameter-code sweep is structurally blind to some classes, so roster probes the sweep cannot return are admitted by direct id lookup, and a probe the source does not know is reported as a roster defect, never invented. Canaries flow end-to-end as ordinary data; the standing invariant is *every canary arrives where its class says it should*.
The current roster — a parameter, expected to evolve as classes come into and out of structural risk:

| probe | class | why this one |
|---|---|---|
| `USGS:423127084321901` | groundwater-level well | 81-year active record (1945–); proves the groundwater/off-network path end-to-end. Chosen when non-surface fetches were skipped; now that they are collected, its role shifts from "proves the skip admits it" to "proves the class publishes correctly" |
| `USGS:415457088150600` | precipitation / atmosphere | 40-year active record (1986–); proves the atmosphere type path, which no water-parameter sweep can return |
| `MPCA:48020005` | grab / intermittent regime | placeholder — the nearest exercised path to grab cadence until a true grab-sample sensor registers |
| `USGS:05553700` | canary-tier channel | placeholder — exercises the channel-extension path whenever a channel is set to `depth = canary` |

## Geometry and Proximity of Sensors

A sensor's stated latitude and longitude is the closest thing the pipeline has to ground truth about where the instrument is. Everything in this section derives from stated coordinates; nothing in it derives from snapped positions.

**Declared precision is captured at acquisition, because parsing destroys it.** The number of decimal places a source prints is a statement of how well the authority knows the location, and it survives only in the source's text: the moment a coordinate becomes a float, binary representation manufactures noise digits and the declaration is gone from every parsed table. Each adapter therefore records `coord_dp_lat` and `coord_dp_lon` — the printed decimal places of each coordinate — while it still has the text. Where the text lives differs by source, and the difference matters. IWQIS and MPCA ship vendor text files that the raw archive retains verbatim, so their dp is recoverable for the already-archived cohort by a backfill pass over the archive — no refetch. USGS coordinates arrive through a client library that parses the HTTP response in memory and returns floats; the text never touches disk, so the adapter must archive the raw response of the monitoring-locations request (or read the legacy site service, whose format is text) for USGS dp to exist at all — a one-time metadata pass, not a data refetch. Measured on the current cohort: IWQIS prints a median of 4 decimal places (≈ 11 m half-width) with a tail at 3 dp (≈ 69 m) and 2 dp (≈ 693 m); MPCA prints ≥ 5 dp nearly universally; USGS *appears* to (measured off floats, so a lower bound until the raw capture lands).

**The uncertainty region is a rectangle**, because precision is declared per coordinate. Half-widths in metres, at latitude φ:

```
half_lat_m = 0.5 · 10^(−dp_lat) · 111,320
half_lon_m = 0.5 · 10^(−dp_lon) · 111,320 · cos(φ)
```

A sensor stated as `43.21, −92.7503` has a true position inside a sliver ±557 m north–south and ±4 m east–west — a shape no radius can represent, and one that a review map can intersect with the stream geometry to pin the position far tighter than either constraint alone.

**Coordinate sentinels.** The value `−99.0` in both coordinates is a fill, not a place. It is a member of the sentinel machinery (§ Cleaning, Scrubbing and Filtering): a sensor carrying it has *no* stated location, participates in no proximity computation, and snaps to nothing.

**Snapping.** Each located sensor snaps to the nearest NHDPlus flowline, recording `comid` (the reach), `snap_dist_m` (the residual), `snap_pos_m` (metres from the reach's upstream end along the flowline), and `comid_agree` (whether the containing catchment's reach matches the snapped reach — disagreement means the sensor sits near a drainage divide and its reach assignment is less certain). The snap searches outward in radius tiers (500 m, 2 km, 10 km); among reaches within **25 m** of the closest it prefers the larger upstream drainage area, which resolves a confluence toward the mainstem without dragging a genuine tributary sensor onto it; where the source publishes its own drainage area, a candidate whose accumulated area disagrees by more than **2×** (|log error| > 0.69) is rejected in favor of one within **1.25×** if such exists inside 600 m; a snap beyond **500 m** is marked far. The snap residual is **context, never positional uncertainty**: a sensor 300 m from any mapped flowline is not a sensor whose own position is doubtful by 300 m — it means the flowline geometry is coarse there, or the sensor is not on a mapped stream at all (a well, a tile outlet, a field plot). Its stated coordinate may be exactly right, and the residual is displayed beside decisions as "the reach assignment is unreliable here", not folded into geometry.

**The proximity table** is a published data product: every unordered pair of located sensors whose stated coordinates lie within **2,000 m** of each other, computed in the pipeline's fixed equal-area CRS. Columns:

| column | meaning |
|---|---|
| `uid_a`, `uid_b` | the pair, sorted |
| `sep_m` | distance between the *stated* coordinates — the observable, what the two authorities jointly assert |
| `min_sep_m` | minimum distance between the two uncertainty rectangles (0 if they overlap) |
| `max_sep_m` | maximum distance between the two uncertainty rectangles |
| `comid_a`, `comid_b`, `same_comid` | each member's snapped reach, and whether they match |

`sep_m` is deliberately the stated distance rather than any average over the uncertainty regions: the stated distance is what was asserted, min and max bound what is possible, and an "expected" separation would depend on a prior nobody stated. The 2 km radius is generous on purpose — the table is the *candidate universe* for identity (§ Identity) but also the modeling layer's raw material for grouping sensors into nodes, and a modeler re-cuts it at whatever radius their node definition wants. At the current cohort it holds ~7,300 rows and well under a megabyte; it is recomputed whole on every D3 pass, since it costs seconds.

## Identity and Sensor Merges

A **sensor** (interchangeably: registration) is one source's record-producing unit. Sometimes two registrations name one physical installation, and then — and only then — they merge:

- **dual registration**: one instrument published twice under different names by different agencies. The identical record should exist once.
- **sequential replacement**: an instrument retired and replaced at the same installation. One record with a time gap, not two records.
- **complementary co-location**: disjoint instruments on one structure — a nitrate analyser mounted on a USGS gauge's rail, a stage sensor in the same stilling well. Nobody deploys two sensors metres apart to measure *different* things unless it is one installation, which is why disjointness of instruments is *positive* evidence here, and stronger than overlap: two sensors measuring the *same* thing that close is either two agencies duplicating each other or a pair straddling a treatment, and the record alone cannot say which.

**The criterion: a merge asserts one physical installation.** Never "a useful union" — a merge must strictly enhance what a model can do and can never cost anything, because any model that wants the union of two *distinct* neighboring sensors builds it from the proximity table at read time, while a wrong merge destroys the contrast the two sensors existed to measure and leaves nothing behind to detect the loss. That asymmetry is also why merges gate on humans and `distinct` verdicts largely do not: a wrong `distinct` leaves two near-identical records side by side, which is visible; a wrong `merge` is invisible forever.

Consequences of the criterion, each with a real case behind it:

- **Proximity is necessary and never sufficient.** The ledger's own `distinct` rulings include well pairs at 0.00 m, 0.95 m, and 3.11 m separation — nested piezometers screened at different depths in one borehole, with nitrate correlations of −0.39, 0.15, and −0.02. Same coordinate, different water.
- **Correlation is never sufficient**, on any channel. Two gauges kilometres apart on one river correlate at 0.9 with near-equal medians *because it is the same river*. Highly correlated neighboring sensors reporting near-identical values are the canonical **keep separate** case.
- **Non-discriminating channels never decide identity.** Agreement on water temperature, stage, pH, DO, or turbidity is evidence of shared climate, not shared instrument (the 1,878 m / r = 0.9936 pair). Such agreement can never make a `dual_registration`, and the `complementary` branch ignores it entirely — it is neither evidence for a pair nor against one.
- **Declared associations are verified, never trusted.** An operator's co-location field can name "the associated mainstem gauge" 18 km away. A declaration a measurement contradicts is dropped; a declaration measurement confirms may establish location authority (below).

**The gate.** Candidates are read off the proximity table — never generated inside the identity stage — and a pair must pass all of:

1. **Type.** The gate reads `site_type` directly, with two tiers of refusal. Pairs crossing the *material* boundary — `{well, groundwater}` and `{atmosphere}` against everything else (every remaining type is surface water) — **never merge and never surface**: a well and a stream sensor are different water by physical certainty, and no record evidence could change it. Pairs of *different surface types* (lake × stream, ditch × stream) **never auto-merge but do queue to a human, with the type mismatch prominently displayed**: the type is inferred from declared codes, name patterns, and reach attributes, and it is noisy enough to carry its own conflict-review ledger — one physical installation registered by two agencies can plausibly arrive under two types, and a silent auto-reject keyed on a noisy label would kill a true dual registration with no witness. Same-type pairs proceed on the evidence branch alone — with two standing auto-rules, mutually exclusive by construction (disjoint versus intersecting channel sets), each naming itself in `decided_by`. Rule 1 merges (`auto (merge rule 1)`): same type, within 10 m, channel sets fully disjoint — the upgrade pattern, one station re-registered as its instrumentation grows, with records that cannot even contradict each other. Rule 2 distincts (`auto (merge rule 2)`): at least one shared channel, spans overlapping at least half of the shorter record, and at least one shared channel's series genuinely differing on common days — the redundancy pattern, twin sensors installed side by side reporting correlated but slightly different values, kept separate because whether such twins constitute one node is a modeling preference, not a fact. The pattern behind it is the USGS's own registration practice: a station whose instrumentation is upgraded frequently reappears as a fresh registration at the same coordinates, so co-location plus matching type is the fingerprint of one station across an upgrade rather than two installations. The verdict is an ordinary, overturnable sheet decision, inspectable in the panel's by-decider view. When a human **does** merge a cross-type pair, the type conflict does not dissolve with the identity question: every member of a settled bundle whose members carry differing types enters the type review as `merged_mismatch`, because one published record carries one type and a person picks which. The type review carries three standing auto-rules of its own, keyed on the exact (conflict, kept-type) pair: a `conflict_tidal` row kept as `stream` is decided `tidal_stream` (`auto (type rule 1)`), keeping its stream nature while joining the tidal class that `NO_SURFACE_BASIN` already routes out of basin modeling; a `conflict_tidal` row kept as `lake` is confirmed `lake` (`auto (type rule 2)`), because a tidal flag on the reach does not make a lake anything else; and a `conflict_tidal` row kept as `well`, `ditch`, `wetland_outflow`, `storm_drain` or `canal` keeps its type (`auto (type rule 3)`) on the same reasoning. Any other tidally-conflicted type stays a human question. A sensor typed `unknown` *waits* — its pairs are neither judged nor rejected until a human types it — and the classifier never defaults an untyped sensor: an unlabeled well defaulted to `stream` would cross the material boundary silently, one rank before any check could see it.
2. **Records.** Both members carry a canonical record. This is a *filter, not a verdict*: a record-less registration is simply not a candidate — it cannot be judged (every evidence branch compares two records), merging it would change nothing (it contributes no rows bundled or not), and it is never surfaced in the access layer anyway. No row is written for such a pair anywhere; if the registration ever gains a record, the pair enters the candidate set fresh, exactly as if the sensor had just been discovered. Record-less registrations exist at this stage only because the census deliberately registers every advertised station (a metadata row is nearly free, and a station's location and declared channels are network context even without a record) — the identity stage reads the record-bearing subset and never adjudicates the rest.
3. **Proximity, on `min_sep_m`.** A pair may *propose a merge* only when `min_sep_m ≤ 100 m`. The threshold is measured, not guessed: of the merges approved to date, all but one sit under 52 m, so 100 m is roughly 2× headroom over every accepted case; and the treatment pairs that must never merge begin around 200 m. The gate uses `min_sep_m` — the closest the two could possibly be given both authorities' stated precision — so a coarse coordinate cannot geometrically excuse a pair from consideration; measured, this admits a handful of coarse-precision pairs beyond the stated-distance cut and nothing else. Pairs whose `sep_m` exceeds **50 m** are visually flagged in review: approach with hesitation, distance displayed prominently.

The gate applies to **every proposal class, sequential included** — a replacement is only a replacement if it happened at the installation. The tempting exemption ("a handoff is a move, so allow sequential at any distance") fails on the evidence three ways. Zero temporal overlap is a property of *any* two records that merely miss each other, so an unbounded sequential branch manufactures false handoffs wholesale — on the current cohort one estuary sensor proposes "sequential" with four different partners at 0.9–1.5 km, and 47 such proposals sit beyond 100 m, out to 8.7 km. The one long-distance sequential proposal ever adjudicated (835 m) was ruled distinct: an in-field research installation beside a river sensor, records disjoint in time by coincidence. And every handoff actually confirmed sits at 52 m or less. A station genuinely relocated beyond the gate becomes two published records, *correctly* — whether to model them as one node is a read-time preference the proximity table serves, not an identity fact, and asserting identity across a relocation would silently splice two different drainage positions into one series.

Pairs failing the material-boundary or proximity condition are **auto-distinct**, written to the auto record with the failing condition named, and never shown to a human — this is what keeps the review queue at the scale of hundreds, not thousands. The records condition is the exception: failing it writes nothing, per its filter-not-verdict semantics above.

**The evidence branch**, for gated pairs, evaluated top to bottom — first match wins:

```
no shared DISCRIMINATING channel     -> complementary        (disjoint instruments are one
                                                              installation; incidental shared
                                                              weak channels are ignored)
shared discriminating channel,
  zero overlap days                  -> sequential           (the seam dates are the evidence)
shared discriminating channel agrees,
  sep_m <= 250                       -> dual_registration
otherwise                            -> distinct
```

Two things about the first branch. "Disjoint instruments" means disjoint in the channels that *discriminate* — never total disjointness, because every real instrument carries incidental weak channels (a thermistor on everything, turbidity on a nitrate analyser), so requiring no shared channel at all bars exactly the one-installation pairs the branch exists for, and judging them on the incidental channel judges climate, not identity (that mistake has been made and is why this sentence exists). And there is deliberately no `weak_evidence` class: its old job was flagging far-apart pairs whose only agreement was on non-discriminating channels, and the uniform gate auto-rejects those before evidence is consulted. Within the gate, a pair with no discriminating overlap *is* the complementary case, and its non-discriminating agreement is neither required nor consulted.

Agreement on a shared channel means all of: at least **10** shared days (below which no correlation is computed), r ≥ **0.80**, and level agreement — for nitrate, |median daily difference| ≤ **0.25 mg/L as N** (the calibrated absolute threshold for the one channel whose thresholds were tuned on real duplicates); for other ratio-scale channels, |median difference| ≤ **10%** of the pair's own median level; for interval-scale channels (temperature, pH), |median difference| ≤ the channel's registry tolerance (1.0 °C, 0.3 pH). The r floor is 0.80 rather than higher because two different instruments on the same water agree on shape less than two copies of one instrument — the known true duplicate pair agrees at r = 0.887 with a median difference of exactly 0.00, and a 0.95 floor would have withheld it. Dual registration additionally requires `sep_m ≤ 250 m`, because agreement without tight proximity only proves same river — the measured suspect pairs at 1.5–2.1 km correlate beautifully and are two stations.

**Who decides.** Every proposal that touches a high-scrutiny record gates on a human through the merge ledger, verdicts `merge` or `distinct` — two tokens, nothing else. Standard-tier proposals take the branch's answer, recorded in the auto record so the silence is inspectable. Every decision row records the **criterion version** under which it was made: when the criterion changes, decisions inherited from an older criterion re-queue for confirmation rather than silently carrying authority they were never given.

**Merging is transitive, and contradictions halt.** Confirmed pairs compose into components. A component that would fuse a pair a human ruled `distinct` is a contradiction: the build halts naming the component's members, the violated pair, and the merge chain that fused them. Only a *human* `distinct` contradicts — the rule's `distinct` means "no pairwise evidence", and a chain of sequential re-registrations legitimately fuses two sensors the rule proposed apart. If a component's members snapped to different reaches, the build halts for review of which reach the merged record lives on. Multi-member compositions of three or more members are additionally confirmed *whole* through a composed-record review — every member's record on one timeline — because three locally-sound pairwise answers can compose a trio nobody looked at; two-member bundles confirm automatically (`auto (composed rule 1)`), since the pair decision that built one was made looking at exactly its assembly. Rejecting a composition is not a token on that sheet; it is editing the pairs, after which the changed membership re-queues under its new key by construction.

**Published ids.** Every registration is minted a published id: **`SNR-` + four base-36 digits** (`0`–`9`, `A`–`Z`, uppercase — `SNR-0016`, `SNR-0FLU`), giving a namespace of 36⁴ = 1,679,616 against ~20,000 registrations today. The prefix exists precisely so that nothing published can be mistaken for a source's own series — a file named `USGS:05455100` invites the reader to assume it *is* USGS's unprocessed record, and it is not: it has been canonicalised, scrubbed, and possibly merged. Codes are **minted once, in an append-only id ledger, and never re-derived**: an id defined as "position in the sorted table" would renumber the whole cohort every time a sensor arrives, so sort order determines only how codes are born and is not an invariant anyone may rely on. Codes are never reused, including those of retired registrations. Merged-ness is deliberately **not** encoded in the id (no `M` infix): merge state is mutable — flipping one ledger pair dissolves a bundle — and an id that encodes it gets renamed by a decision edit, breaking every reference to it; the fact lives in the `members` metadata column, where facts that change belong. For the same reason, **merges mint nothing**: a merged record publishes under an id its canonical member already owns (below), so no decision — making, revising, or dissolving a merge — ever consumes or retires a code. Capacity is spent by registration alone, once per sensor, ever. Source uids remain what ledgers key on and remain columns everywhere; the SNR id is the key of the published store and the access API.

**The id ledger.** A three-column append-only table living beside the decision ledgers and tracked the same way, but written only by the build — the one ledger no human edits:

```
snr_id, uid, minted_under
SNR-0001, IWQIS:WQP0001, snap-YYYYMMDD
```

Codes count from `SNR-0001`; `SNR-0000` is never assigned.

`uid` is the natural key: one uid holds exactly one code, forever, a bijection. Minting happens at the census, which is where registrations are born: every registration not yet in the ledger is assigned the next free code and appended, with the batch sorted `(lat, lon, uid)` before assignment so that a batch mint is reproducible whatever order discovery iterated in. The **christening** is nothing special — it is the first mint, when the ledger is empty and the batch is the whole cohort, which is what gives the initial ids their geographic locality. `minted_under` records the snapshot the minting pass ran against, so every code's origin is datable without wall-clock state in the build.

The ledger's standing in the build's purity contract is the same as the decision ledgers': durable input-state. A build *reads* it, appends codes for unseen registrations, and the appended state is input to every later build — which is exactly why wiping everything derived reproduces identical ids. Its loss is the one event that renumbers (a re-christening reproduces the original ids only if the cohort happens to match the original initial cohort), which is why it is git-tracked; recovery from an accidental deletion is a git restore, never a re-mint. And like every ledger it is single-writer: two clones minting concurrently produce a git conflict to be resolved by hand, never auto-merged.

Verified invariants (§ Verification): the bijection holds; every code is uppercase base-36 at fixed width; existing rows are byte-identical to the tracked history — appends are the only diff a build may produce; and the pass warns when the namespace crosses half full.

**What a merge produces.** The merged record publishes under its **canonical member's** SNR id — the highest-authority member, ranked: a declared co-location a measurement confirms, then a measured co-location, then the longest record. The ranking is fully automatic and deliberately takes no human input: the choice affects only which member's id and coordinates front the record — never the assembled series — and within a ≤ 100 m bundle the coordinate difference is immaterial, so there is nothing here a human could usefully care about. The members, the rule that merged them, and who decided are columns. Where members overlap on a channel, the channel's registry merge policy picks a **winner per span**: every published day of every channel comes from exactly one contributing sensor, recorded per day in a `{channel}_src` provenance column. Values are never blended — a blend of two calibrations is a number no instrument produced.
**Groundwater pairs are decided on record evidence alone.** Nested piezometers share a coordinate exactly, so geometry has no discriminating power in the one same-type class where nothing else separates a pair; the separating signal is the records (different screens in different aquifer units are uncorrelated or anticorrelated). Screen depth is not currently carried by any source we ingest; until it is, groundwater pairs always go to a human.

## Cleaning, Scrubbing and Filtering

Data is cleaned, scrubbed or filtered at various points in this pipeline, this section accounts for each place this occurs. The rule is that we remove **only the data no model would ever use**. This means we discard sensor records which come back empty, we scrub sentinel values like -999999 when they are clearly unphysical or documented by the publisher, but we do nothing else. The exception to this is the quality review panel which flags sensor data with basic statistics and has the ability to manually exclude ranges from the series.

Every removal is one of three kinds, and this section enumerates all of them: **value scrubs** (is this a measurement at all? — automatic, tally-closed), **structural filters** (is this site in scope? — never about quality), and **recorded judgments** (the quality ledger — a person's decision, by default absent).

### Value scrubs

Three mechanisms, applied in order per value: sentinel check (native units) → unit scaling → range check (canonical units).

**Sentinels** — exact fill values, matched per native column, *before* combining and *before* scaling: after a scale factor −999999 cfs becomes −28,316.82 m³/s, a number that looks like nothing in particular, and a sentinel averaged into a two-column combine is no longer exact and is uncatchable afterward. Counted at the native-cell grain into the persisted scrub tallies, which close: `n_kept = n_cells − n_sentinel − n_below_lo − n_above_hi`, with clamped cells kept and counted in `n_clamped`. The lists, each value measured in the wild:

| source | sentinel (native) | seen as, canonically | measured occurrences |
|---|---|---|---|
| all sources | −999999 (any unit) | −999999, −28,316.82 m³/s, −304,799.7 m, … | 36,471 discharge · 8,755 water_temp · 5,458 gage_height · 1,151 fchl · 560 spec_cond · 309 orthophosphate |
| MPCA | 999.99 | 999.99 | 14,631 nitrate + 49,531 turbidity, both at one malfunctioning station |
| MPCA | −99999 | −99999 (or scaled) | 10,439 readings; five nines, distinct from the six-nine fill |
| IWQIS | −99.0 in both coordinates | no location | 6 registrations |

The 999.99 case is why sentinels exist as a mechanism separate from ranges: real turbidity in this cohort reaches 6,940, so no defensible range ceiling sits below 1,000 — and without the sentinel rule, 49,531 fill values (34% of that station's turbidity record) publish as water. The control that shows the match is safe: across 17 million turbidity observations at all other stations, an exact 999.99 occurs four times. A sentinel becomes a missing value; it is never clamped.

**The physical range** — per channel, in canonical units: `lo` and `hi` bound what an *instrument* can truthfully report — they catch a failed sensor, never trim a distribution. The principle that sets every bound: **instrument-impossible, not site-implausible.** A hot spring at 59.8 °C is real water measured truthfully at an unusual site; turbidity at 7.7 × 10¹⁰ is electronics. Nulling the second removes a non-measurement; nulling the first would be a modeling preference smuggled into the build, so site-weirdness is left to the modeler. Every bound was set by scanning all 1.76 billion values in the current native store; "what the bound catches" below is measured, not hypothesized.

Semantics of a bound, for a channel with `clamp_negatives` set (concentrations and optical channels, which have a true zero):

```
value < lo          -> null            (not a measurement)
lo <= value < 0     -> clamped to 0    (a real sub-detection reading: "at or below detection";
                                        keeps the information and the non-negativity downstream
                                        log transforms assume)
value > hi          -> null            (not a measurement)
```

For channels without the clamp (flow, level, temperature, pH — where negative is legitimate), only the null rules apply. **Closure: `clamp_negatives` requires `lo < 0`, enforced at registry import.** A clamping channel with `lo = 0` has an unreachable clamp — every sub-detection reading is deleted before the clamp can save it — and the measured cost of that mistake is 85,425 real readings across eight channels, which is why the constraint is checked by the machine and not the reviewer.

The table. Every number justified in the right-hand column:

| channel | unit | lo | hi | clamp | justification |
|---|---|---|---|---|---|
| nitrate | mg/L as N | −1 | 100 | yes | hi: the highest credible real readings sit at the ~44 instrument rail with a verified tail to ~55; values in (50, 100] are data (≈ 10,700 real readings live there — 100 is not a distribution trim), while the value hi exists to null is the 999.99 fill. lo: sub-detection noise to −1 clamps to 0 (23 readings); below −1 is a failed instrument — one station held −12.3/−12.4 for 10,697 samples. |
| discharge | m³/s | −10,000 | 50,000 | no | Asymmetric because huge positive flow is real and huge negative is not: the Mississippi peaks near 35,000, so hi = 50,000 is loose; no reversal approaches −10,000 (a 350,000 cfs backflow). Measured: the bounds catch 36,473 values of which 36,471 are the −999999 cfs sentinel and 2 are isolated glitches (−11,780; 73,624 — the latter exceeding any river on Earth). Nothing real is touched. |
| gage_height | m | −1,000 | 10,000 | no | Datum-relative, so negative is normal (measured p1 = −0.02, real tail to −0.86). Real stage-datum values reach 687 in-cohort, so hi = 10,000 is loose. The bounds catch exactly one value, 5,458 times: the −10⁶ ft fill (−304,799.7 m). |
| water_temp | °C | −5 | 60 | no | lo: flowing water cannot persist below −5 (measured 0.01st percentile is −2.8); below it lies frozen/air-exposed-sensor territory (median offender −16) and the −999999 fill. hi = 60, not 45: a hot-spring station's genuine record runs 45–59.8 °C, and outside that one site exactly 5 of 240 million values fall in (45, 60] — so 60 publishes real thermal water at the cost of nothing, per the principle. |
| spec_cond | µS/cm @25C | −5 | 200,000 | yes | hi ≈ saturated brine: road-salt-impacted highway stations genuinely reach 177,000, so the former conventional ceiling of 100,000 was nulling 6,668 real readings. lo = −5 gives the sub-detection clamp room (48 readings at −1). |
| turbidity (fnu / ntu / unknown) | FNU / NTU / unknown | −5 | 10,000 | yes | Real turbidity reaches 6,940 in-cohort, so hi = 10,000 nulls only electronics garbage. lo = −5: optical sub-detection noise (−0.01…−4.2; 55,000+ readings across the three channels) clamps to 0 instead of being deleted. The MPCA 999.99 fill is a *sentinel*, deliberately not a range problem. |
| diss_oxy | mg/L | −1 | 30 | yes | Real supersaturation in eutrophic streams tops out near 20–25; hi = 30 leaves margin while cutting the shoulder of a measured instrument rail at 35 (the mass between 25 and 35 is a smooth rail-approach continuum with only 22 values ever above 35). One saturated-waterway sensor spends 29% of its record above 25 — a probe out of water, handled by its per-sensor ceiling, not by tightening the channel bound. lo = −1 for the clamp (6,114 sub-detection readings). |
| ph | std units | 0 | 14 | no | Definitional bounds of the scale. Measured range 0–12.5; zero violations. |
| fdom | µg/L QSE | −10 | 5,000 | yes | Real values to 4,995; above the ceiling lies pure electronics garbage (813 distinct values up to 7.7 × 10¹⁰). lo = −10 for clamp room. |
| fchl | µg/L | −1 | 1,000 | yes | Real max 787. Out-of-range mass is 98% the −999999 sentinel. |
| fpc | µg/L | −1 | 1,000 | yes | Real max 99. lo = −1 matters most here: 24,087 sub-detection readings (−0.01…−0.06), 1.5% of the whole channel, clamp to 0 rather than vanish. |
| orthophosphate | mg/L as P | −0.1 | 20 | yes | As-P (the as-PO₄ reading would be 3.07× — a declared trap). Real max 13.35; the only out-of-range value ever observed is the sentinel. |

Ranges and sentinels are registry facts, so the registry fingerprint covers them: editing a bound stales every canonical file, loudly, which is the intended cost.

**The sub-detection clamp**, third of the three, is the one value *modification* rather than removal — the `lo <= value < 0 -> 0` rule in the semantics block above. It is listed here so the enumeration is complete: a model reading the published store should know that a zero concentration may be a clamped "at or below detection" reading, and the scrub tallies count clamped cells separately (`n_clamped`) so the substitution is always visible.

### Structural filters

None of these consult data quality; each is recorded, not silent.

| where | what | recorded as |
|---|---|---|
| census | outside the AOE | never registered |
| discovery | every declared series ends before the window / channels but no dates | `declared_ends_before_window` / `no_declared_series` |
| records | source returned nothing / request errored | `source_empty` (durable) / `attempt_failed` (retried) |
| exclusions ledger | a human names a uid (test loggers, scratch deployments) | `excluded_reason` |
| identity | record-less pair → no row; cross-material pair → auto-`distinct` | candidate table |
| snap | unplaceable coordinate | `snap_status`, sensor kept |
| publication | — | the exact `published \| excluded \| held \| recordless` partition |

**There is no record-length floor anywhere.** A four-day sensor publishes like an eighteen-year one; cohort definitions ("a year of nitrate", "surface water only") are access-time preferences owned by each modeling project. One rule sits at the boundary: a standard channel with 90+ days and **literally zero variance** is auto-excluded as a dead instrument — the single statistical exclusion the build makes on its own, kept because no model wants a constant.

**Attribute NODATA.** The catchment-attribute tables write −9999 for "no answer"; stored literally, a catchment with unknown corn cover reads as minus nine thousand percent corn, and since missing coverage clusters geographically the value doubles as a region id wearing a covariate's name. Masked to NaN at acquisition. (Documented trap: `CAT_TILES_Early90s` encodes NODATA as 100, which no attribute description states.)

### The quality tab of the review panel

The quality machinery makes a reviewer's judgment *possible and recorded* — it cleans nothing automatically, and the standing policy is `keep`. Per (record, channel) it computes a battery of signals, each either absolute (broken on its own terms) or cohort-relative (unlike its peers, robust-z over median/MAD with the z capped at 8 so one broken series cannot flatten the scale):

| signal | type | trigger |
|---|---|---|
| `range_violation` | absolute | > 0.5% of the RAW samples outside the physical range or matching a sentinel, measured before the scrub — after the scrub those samples are NaN and the signal is structurally blind |
| `dead_autocorr` | absolute | lag-1 autocorrelation of the native-resolution series < 0.9 (at a 15-minute cadence adjacent samples of real water are nearly identical; this low is noise) |
| `extreme_noise` | absolute | sample-to-sample roughness — std of first differences over std of the series — > 0.25 |
| `stuck_impossible` | absolute | a run of *out-of-range* values persisting > 1 day — measured on the **raw native series, before the scrub**, because the scrub hides exactly this (a six-month −12.4 flatline arrives at the detector as six months of NaN otherwise) |
| `noisy`, `scale_outlier` | relative | roughness, mean, or std beyond 3.5 robust-z of the channel cohort |
| `flatline_heavy` | relative | fraction of samples in runs of ≥ 20 identical consecutive values beyond 4.5 z |
| `seg_bad` | relative | worst 2,000-sample window (≈ 3 weeks at 15-min cadence) beyond 4.5 z — a bad month inside a good decade is invisible to whole-series averages |
| `aseasonal` | relative | < 5% of daily variance explained by an annual cycle, on records longer than 2 years |

Records with fewer than 50 samples are left unjudged by shape — shape features on 50 points are noise about noise. Any signal flags the series: high recall on purpose, because the human is the precision stage. **Stuck-span proposals** are maximal runs of one identical value spanning ≥ **7 continuous days of record** — gap-aware, so identical values merely bracketing a hole are not one stuck stretch, and 7 days because quantised low-flow plateaus shorter than that are real (a dry-summer week at the detection limit). Instrument railing is deliberately *not* proposed as a span: "at least the ceiling" is information, and the ceiling mechanism handles it.

A reviewer has three actions, all written to the quality ledger keyed on (member set, channel), all surviving every rebuild. **`keep`** publishes the series as is, signals and notes beside it in the metadata. **`exclude_spans`** takes literal inclusive windows in the syntax `YYYY-MM-DD..YYYY-MM-DD; ...` — a malformed span halts the build naming the row, and the ledger stores the human's literal dates, never a flag that re-resolves against a recomputed proposal; the detector pre-fills its proposals in this exact syntax, so confirming one is a cell copy rather than a transcription off a plot. **`max_threshold`** is a per-sensor ceiling in canonical units, where the registry's `hi` is the channel-wide physical one: **whole days** with any statistic above it are withheld, not just the offending samples, because the day's published mean was computed from those samples too — on a plot of daily means this reads as a below-ceiling day vanishing, so the panel's mask preview reports the counts that explain it. The preview cannot disagree with publication: it calls publication's own masking function on the same record, so what it draws is what submitting would store.

High-scrutiny channels gate on a human — a flagged series with no verdict on file withholds its site from publication. Standard channels record the rule's verdict and never ask: the absolute thresholds are nitrate-calibrated and would misfire wholesale on flashy channels, so their cohort-relative outliers are notes beside a keep, evidence for the person who eventually cares.

With the standing policy at keep, the tab's role is **diagnostic**: review a sensor's statistics against its channel cohort, see proposed spans against the record, inspect a suspicious site on the map and the timeline together. The signals publish as metadata either way, so a modeler can consume them as read-time features or filters — which is where noise, flatlines and improbable-but-physical values belong.

### What each mechanism writes

A sentinel → the value was never a measurement, becomes missing. A range violation → missing. A sub-detection negative → 0. A ceiling violation → the whole day withheld for that channel. An excluded span → the channel's days in-window withheld. An excluded series → the channel absent from the published record, reason on the metadata. Nothing in any mechanism deletes a sensor: cleaning operates on values and days, exclusion of records is publication's classify-with-reason (§ Publication), and the raw archive underneath is immutable regardless.

## Publication

Publication projects derived artifacts into the access-ready store, and it **classifies rather than drops**. Every registration ends in exactly one state, and the states partition — counted, checkable:

- **published**: carries a record in the published store, with per-channel quality classifications;
- **excluded**, with a reason: a ledgered decision (test loggers, scratch namespaces, adjudicated garbage) — the registration and its reason stay queryable forever, the record stays in the archive, and nothing downstream has to rediscover what was already known;
- **record-less**: registered, located, typed, but no source ever returned data — published in metadata, absent from the record store;
- **held**: an unanswered gate names it; publication of that record waits.

The published water store is one file per merged record, keyed by its SNR id, carrying channel columns and per-day `{channel}_src` provenance — nothing else; locations, types, and memberships live in the metadata tables, at one grain each, joined by key. The sensors metadata table carries every registration's full story: discovery, declared metadata, coordinates with declared precision, snap results, type, fetch accounting, membership, exclusion status and reason, per-channel quality verdicts and masks.

**Leak invariants, verified at publication**: the published store contains zero sentinel values, zero out-of-range values, zero days inside any confirmed excluded span, and zero days above any confirmed ceiling — each a mechanical scan of the published files against the ledgers and the registry, run by `verify p1`. Record floors, cohort definitions, and "good enough to model" judgments do not exist anywhere in publication; they are access-time preferences.

## The Flow Network

The surface flow network is **published raw data, not a derived graph**. NHDPlus V2 within the AOE ships essentially as the authority provides it: flowlines with their value-added attributes (`comid`, `fromnode`/`tonode`, `hydroseq`, stream order, lengths, incremental and total drainage areas, velocities and travel times), catchment polygons, and waterbody polygons. The pipeline's own uses of the network — snapping, reach assignment, catchment-keyed aggregation — consume the same tables a modeler reads; there is no private network state. Physically the layers are SERVED FROM THE ACQUIRED ARCHIVE in place rather than copied into the published store: the archive is immutable and snapshot-manifested, so a multi-gigabyte copy per publish would duplicate it to no reader's benefit, and the access layer's `get_network` reads it directly.

COMID is the spatial key of the entire data layer: catchments partition the surface, every spatial join resolves through them, every raster product is keyed on them, and the proximity table names them. What the pipeline does *not* publish is any graph over sensors — no nodes, no edges, no contracted variants. A modeling chapter that wants a sensor graph has everything required: records keyed to reaches, the reach topology, and pairwise sensor geometry.

The network earns one standing self-test (§ Verification): the access layer's accumulation machinery, run on a sample of reaches, must reproduce NHDPlus's own total drainage areas within tolerance. That check exists so the machinery § Basins hands to modelers is continuously validated against the authority it walks.

## Basins

A drainage basin is a function of the point you care about, and the points belong to the modeler — different experiments delineate for different node sets, at different tolerances, with different appetites for the network's known quirks. So the pipeline builds **no basin artifacts**, and the access layer ships **machinery**:

- `snap(lat, lon)` — the same snap the pipeline uses, § Geometry;
- `accumulate(comid)` — the upstream COMID set, by walking the published topology;
- polygon → weighted-COMID-set normalisation — any externally-supplied basin polygon reduces to the same representation;
- `aggregate(weighted_set, products, window)` — covariates over any basin, from the catchment-grain products.

The unification that makes this compose: **every basin is a weighted COMID set** — rows of `(basin_id, comid, weight)`. An exact upstream walk is the special case where every weight is 1; a polygon that partially covers boundary catchments carries fractional weights; and every aggregation question reduces to a weighted sum over catchment-grain products. Nothing about a basin needs to be stored to be usable, and what a modeler chooses to store lives in *their project files* — never in the data directories.

The **basin manager** is the widget for that workflow: the modeler supplies node locations, it snaps and accumulates, renders each basin for eyeball review against the terrain and the network, accepts overrides (an explicit reach set where the network is wrong), and writes the reviewed weighted sets to the modeler's project. It is a client of the access layer, holds no authority, and its output is input to experiments, not to builds.
*The basin manager is not yet implemented. When it lands, its workflow, review affordances, and project-file formats get documented here; until then this section specifies the machinery it will be a client of.*

## Covariates

Everything gridded or reach-keyed aggregates to **catchments** — the flow network's own spatial unit — and never to basins (§ Basins reduces every basin question to a weighted sum over catchments). Aggregation is by **fractional coverage**: a cell partially inside a catchment contributes its overlapped fraction, which is both more accurate than whole-pixel assignment at every boundary and cheaper, because coverage fractions are a property of the geometry and are computed once per distinct grid, then reused by every product sharing that grid.

Four categories, split by how their values reach a model. Static rasters reduce **once, at build**, to `(comid, value)` rows and are done. Weather does not: it is a raster in *form* but a time series in *kind*, and its reduction is deliberately deferred to read time. Attribute tables arrive already reach-keyed. Modelled streamflow is a simulation wearing the shape of an observation, and is quarantined accordingly.

### Rasters

- **CDL (crops)** publishes in **long format**: `(comid, year, code, frac)` — every distinct raw class code present in the catchment, with its area fraction. No class collapse happens at build time: the raw code is the fact, and any grouping into "corn / soy / pasture / …" is a preference applied at read time via the access layer's `remap` argument, which **defaults to no remap**. `remap="default"` applies the project's standard class map; a custom map is supplied inline or by path; and the returned frame records which map produced it, so two experiments cannot both report "corn" meaning different things. The median catchment carries ~7 distinct codes, so the long table stays modest.
- **Nutrients** is the gTREND nitrogen budget, taken as **twelve components** rather than as the single surplus layer: atmospheric deposition (oxidized and reduced), fertilizer, fixation (agricultural, cropland, pasture), livestock (total and hogs), surplus, and crop uptake (agricultural, cropland, pasture), 2000–2017. Two of those are redundant by construction — `fixation_ag` is cropland plus pasture, and `uptake_ag` likewise — and they are ingested **because** they are redundant: they are closure checks on our own zonal reduction, not covariates. The full pass is 1,485,343 catchments × 216 product-years = 320,834,088 rows.
- **AgTile and kin**: aggregated fractionally like the rest, with each product's sentinel discipline handled at ingest (a raster whose NODATA is encoded as a plausible value is decoded by the adapter that knows it, never downstream). Product vintages are recorded — a covariate without its temporal validity is a trap for any future model of time.

**A COMPONENT CLOSURE IS CHECKED IN THE RASTER'S UNITS, NOT THE PRODUCT'S.** gTREND stores kg/ha to two decimals, and the reduction publishes `kg = mean × n_px × HA_PER_PIXEL`, so the source's ±0.005 rounding arrives in the closure **multiplied by the 6.25 ha pixel**. The allowance is therefore per-pixel (0.01 kg/ha × 6.25 ha, doubled for three independently rounded rasters) rather than a relative tolerance on the total, and that is not a convenience: a closure error that accumulates with **pixel count** is rounding, while one that scales with **value** is a missing component, and only a per-pixel allowance can tell them apart. Verified at the pixel level — `max |Fix_Ag − (Fix_Cropland + Fix_Pasture)| = 0.0100` over 2.25M pixels with identical nodata masks — and then at full scale: **0 of 26,736,174 catchment-years off, on both closures**.

**MANY RASTERS AT ONCE DEFEAT THE BLOCK CACHE, AND THE PIPELINE SIZES THE REQUEST TO FIT.** A fractional-coverage pass visits every raster for each feature, so GDAL's cache must hold one working set *per raster* simultaneously. The gTREND GeoTIFFs are tiled 1168 × 1856 float32 — 8.7 MB a block — against a default cache of 5% of RAM: 99 blocks for 216 rasters, fewer than one apiece, so each catchment re-decompressed what the previous one evicted. The mismatch is in the source tiling and is worth seeing in units: **one block covers about 135,000 km² where the median catchment is 1.38 km²**, so a single catchment read decompresses roughly a hundred thousand times the area it needs. Nothing makes that read cheap — but it need happen only *once per block*, which it does as soon as the working set fits. The extractor therefore splits its raster list into groups sized to half the cache, computed at run time from the block size actually on disk (216 rasters become 18 groups of 12); single-raster and small-tiled passes come back as one group and are unaffected. Measured, the difference is **237 minutes against a projected nine days**. Processing one raster to completion before opening the next also fixes the cache but recomputes each feature's coverage fraction per raster, and was measured 7× slower for bit-identical values — so the pipeline keeps the cheap traversal and simply asks for less at a time.

**A CHECKPOINT IS ONLY VALID FOR THE REQUEST THAT WROTE IT.** Long raster passes checkpoint per batch, and a resumed pass must not reuse parts computed from a narrower question. Two independent keys guard this, because they see different things: declared **value axes** (product, year) are checked from the parquet footer, catching a component or year added to the request; and an **input stamp** over the identity of every raster read catches what the values cannot — a raster re-pulled or rewritten in place, which changes no vocabulary in the output. A *missing* stamp is adopted rather than treated as a mismatch, so introducing the guard cannot destroy the four-hour pass it exists to protect.

### Weather

gridMET is daily, ~4 km, and decades deep, so reducing every catchment × day at build would both bake in the aggregation and store a derived quantity beside its inputs. Instead the pipeline publishes the **weight matrix** `(comid, cell_id, km²)` — the area of each weather cell inside each catchment, in physical units — and the reduction happens at read: weather for *any* region, including any basin a modeler ever builds, is one weighted average through the same matrix. The matrix is the covariate machinery's one geometric artifact, and it is exact by construction against the catchment geometry it was cut from.

**THE DROUGHT AGGREGATES ARE A SIBLING STORE, AT THEIR OWN CADENCE.** Twenty-five standardized indices — `spi`, `spei`, `eddi` at each published accumulation window, plus `pdsi` and Palmer `z` — ride the same 1/24° lattice as the daily variables, so the weight matrix reduces them unchanged. But they are **pentad**: one value per five days, 73 a year. They live in `acquired/weather/pentad/` rather than beside the daily rows, because the alternative is leaving four of every five days null or forward-filling, and a forward-fill is a modelling preference the build must not make on a consumer's behalf. The reader states the cadence in what it returns, and names its date column `pentad_start` rather than `date` — a frame calling it `date` would join cleanly against a daily series and silently give four days in five the wrong value, whereas a different name makes that join fail. `spi` publishes seven accumulation windows where `spei` and `eddi` publish eight; there is no `spi270d`, and the absence is named in code rather than merely observed.

**Sixteen variables, and the store knows which.** The aggregated gridMET catalogue publishes 42; we take the eleven standard meteorological variables plus the fire-weather block (`fm1000`, `fm100`, `bi`, `erc`, `th`), which was restored in 2026-08 after the rebuild had assembled its variable tuple from a library's standard long-name mapping rather than from the catalogue — and thereby dropped `fuel_moisture_1000h`, the *only* weather variable that survived feature curation in either shipped model. `snow` is declared by the library and does not exist at the endpoint: the name resolves and returns zero bytes. The 25 drought aggregates (`spi`, `spei`, `eddi` at eight accumulation windows each, plus `pdsi` and `z`) are **pentad** — one value per five days — and belong in a sibling store at their own grain rather than in a daily table where four of every five rows would be null.

**Adding a variable is a normal operation, not a rebuild.** The completeness check reads parquet footers and compares the *declaration* against what each quarter carries, nulls included, so a widened `VARIABLES` is noticed; a backfill then fetches only the missing columns and merges them into each quarter through a streaming row-group join that preserves the file's own `(zkey, date)` order. Before that existed, adding one variable meant re-downloading all sixteen for every month — fourteen hours with no checkpoint.

**THE DAY BOUNDARY IS NOT OURS, AND CANNOT BE REPAIRED BY RELABELLING.** gridMET's precipitation day runs approximately **14:00→14:00 UTC** — measured by correlating against AORC hourly at all 24 possible offsets, three corn-belt sites, where the peak is sharp (r = 0.969, against 0.474 at a UTC calendar day). Our observation day is Central Standard, 06:00→06:00 UTC, so the two windows sit **eight hours apart** and gridMET day D overlaps our day D by sixteen of its twenty-four hours.

Sixteen of twenty-four is already the best integer-day assignment available, which is why the reader does **not** shift the label: measured against AORC aggregated on our own boundary, the published label scores r = 0.798, a one-day shift 0.385, and a shift the other way 0.137. Relabelling moves all of the water rather than the misaligned third. The residual cost is real and irreducible — 69% mean absolute daily disagreement in rainfall at one site-year — and the honest responses are to lag precipitation features deliberately knowing the offset, to re-derive precipitation from an hourly source on our own boundary, or to bin the native sub-daily observations onto gridMET's boundary instead (`access.get_native` exists for exactly that). Only precipitation's convention is measurable this way: `tmmx`'s correlation plateau spans fourteen offsets and `srad`'s sixteen, because both are nearly insensitive to windowing.

### Attribute tables

COMID-keyed families (StreamCat, Wieczorek) pass through keyed as published, with two disciplines: *absent is not zero* (a dam table lists only catchments with dams; the join must not manufacture zeros), and columns fitted on observed water quality (SPARROW-family fluxes and their kin) are **named leakage** — carried, flagged, and excluded from any default covariate list, because a model consuming them is partially consuming its own target.

**StreamCat is taken twice, at two grains, because one request cannot serve both.** The ordinary pull takes the **latest vintage** of everything — 296 columns, a snapshot. The nitrogen families are taken again as **full annual series**: eleven families across every published year, 326 `cat` columns and 326 `ws` columns, 1,478,330 reaches, 7.3 GB. The distinction is `cat` versus `ws` — the local catchment against the accumulated watershed above it — and both are kept because a nitrogen source term and its routed accumulation answer different questions. Coverage is 1,478,330 of 1,478,684 askable reaches; the 354 missing are reaches the service holds nothing for, which is why *absent is not zero* is load-bearing here rather than pedantic.

Two things the fetch had to learn. The service is fronted by an umbrella that caps requests per hourly window and **says so in the response header**, returning an error with an empty body when exceeded — a failure that reads as a mystery until the header is consulted, so the pull reads its own remaining allowance, states the budget before spending two hours, and waits out an exhausted window rather than retrying into it. And a coverage check that counts **rows** cannot see a missing **column**: twelve metrics were requested for months and silently never arrived, because every COMID had a row and the check was satisfied. Coverage must be tested on every axis the request names.

### Structures

Physical structures on the network — dams and treatment facilities — are point data, and the points are published as points: aggregating a structure's location away into "exists somewhere in this catchment" discards exactly the information a flow-path question needs (which of two sensors is the structure between?). Every structure point is snapped with the same machinery as sensors, so each carries `(comid, snap_pos_m)` — a position in metres along a specific flowline — and structures and sensors order together along a reach.

- **Dams** come from the National Inventory of Dams: exact coordinates plus height, storage, year, and purpose. Three artifacts: the snapped **point layer**, and two comid-keyed tables that answer different questions with the same key — `dams` keys each dam to the catchment *containing* it (the drainage-area view: how much storage sits upstream), `dams_on_reach` keys each dam to the flowline it *impounds* (the flow-path view: is there a dam on this channel). The predicates genuinely disagree at boundaries — a dam is built across a divide, so point-in-polygon can contain it in one catchment while it impounds the neighboring reach — which is why both tables exist rather than one.
- **Treatment facilities** come from EPA's ICIS-NPDES discharge-points layer: every permitted point-source outfall in the AOE, with per-outfall NAD83 coordinates, facility type, SIC/NAICS, and permit status, refreshed weekly at the source. Drinking-water plants enter through the same door — a nitrate-removal facility discharges its residuals under an NPDES permit even though its intake is not public data — so the treatment-pair installations (paired sensors bracketing a plant) are locatable from the discharge side. Two joins enrich the layer, both by NPDES permit number: **the nitrogen-family DMR rows** — reported effluent measurements per (permit, outfall, monitoring period) for total N, nitrate, nitrite, TKN and ammonia, filtered from the raw fiscal-year DMR archives under the ephemeral-archive discipline (the loading tool's *computed* annual loads have no stable bulk URL, so the honest aggregation to loads happens at read time with the tool's documented methodology; the rows are a covariate and not leakage, because an effluent load is an *input* to ambient quality rather than a quantity fitted on it) — and **treatment level, design flow, and population served** from the Clean Watersheds Needs Survey POTW inventory, whose national zip is a manual acquisition like CDL's (the portal is an interactive application with no stable bulk URL). Facilities snap like every other point, so a plant sits between its bracketing sensors on the reach axis by construction.

### Modelled streamflow

NWM retrospective streamflow is acquired per-reach for the reaches carrying record-bearing sensors — COMID-partitioned so widening the reach set later is an append, never a rewrite. It carries the leakage flag in a milder form: NWM is calibrated against USGS gauges, so at gauged reaches it has partially seen the observations our models train on.

**Closure is checked per grid, in that grid's native cells** — the grids differ (30 m CDL, ~4 km gridMET, coarser products between), so a single pixel-count invariant would be fiction. For each product: class fractions sum to the catchment's covered cell total, fractional coverage closes against catchment area, and the gridMET weight matrix additionally conserves cell area globally — no cell claimed twice.

## Gates and the Review Dashboard

Every human decision lives in a named, tracked ledger, and all ledgers share one contract:

- **keyed on natural keys** — uids, sorted pairs, sorted member sets, `(member set, channel)` — never on minted or positional ids, so decisions survive regeneration, re-snaps, and coordinate revisions. Membership-keyed sheets re-queue changed compositions *by construction*: edit a pair and the new member set is a new key.
- **added-to, never rewritten.** Retired-but-decided rows are kept, unnumbered. History is not overwritten by a re-proposal.
- **blank means not reviewed, never approved.** An unrecognized token raises. Unanswered rows sort to the top.
- **decisions are literal values** — explicit dates in `exclude_spans`, an explicit number in `max_threshold`, a verdict token in `decision` — never booleans that resolve against a recomputed proposal, because the proposal can drift under a re-run and a `true` would silently mean something new.
- **every decision row records the criterion version** it was answered under; a criterion change re-queues inherited decisions for confirmation.
- where a rule settles a case without asking, the settlement is written to a companion **auto record**, so the silence is inspectable.

The ledger inventory:

| ledger | stage | keyed on | gates? | decides |
|---|---|---|---|---|
| drift | A4 | (snapshot pair, path) | yes — conflicting classes | `accept` / `reject` the changed file |
| registry gaps | A4 | (source, uid) | yes — unacknowledged | `acknowledged` — a named source inconsistency |
| merge (+ auto) | D4 | sorted (uid_a, uid_b) | yes — high-tier proposals | `merge` / `distinct` |
| type (+ auto) | D4 | uid | yes — conflicting type evidence | a type from the vocabulary |
| exclusions | D4 | uid | yes — substantial records | `exclude` / `keep`, with reason |
| composed review | D4 | sorted member set | yes — new/changed multi-member compositions | `confirm` (rejecting = editing pairs) |
| quality | P1 | (sorted member set, channel) | yes — flagged high-tier series | `keep` / `exclude`, + `exclude_spans`, `max_threshold` |

**The review dashboard** is the single interface to all of them, and its authority model is fixed: **the CSV ledgers are the sole durable record** — git-tracked, diffable, answerable with a text editor when the dashboard is broken — and the dashboard is a view that reads and writes them. Requirements:

- one tab per ledger, with `undecided` / `all` views; undecided counts visible per tab;
- radio buttons for the verdict vocabulary, free-text for notes, literal-value fields for spans and ceilings; **every decision written to the CSV immediately** — a crash loses nothing;
- keybindings for the review loop (next/previous row, verdict keys, undo) so a hundred-row queue is an hour, not an afternoon;
- interactive record plots — zoom, and cursor readout of exact date and value, because span decisions are written as literal dates read off the record;
- an **interactive map** per geometric decision: both sensors' stated positions, their **uncertainty rectangles** drawn from declared precision, the snapped reaches, the snap residuals as context, and every other sensor within the proximity table's radius;
- the **counterfactual view**: for a merge, the assembled winner-per-span record beside the members' records; for a quality verdict, the masked series beside the unmasked; a reviewer sees what the decision *does*, not just what it is named;
- proposals pre-filled in ledger syntax (stuck spans, ceilings) so confirmation is a copy, never a transcription.

Gate semantics are unchanged by the dashboard's existence: a stage missing decisions halts, names its rows, and the build is complete only when every gating ledger is answered or empty.

## Access API

The access layer is **strictly read-only** — no code path writes to the data layer; durable changes enter through ledgers and take effect at the next build. It never imports the build. Published data is primitives one join away, never pre-baked feature matrices; feature assembly belongs to modeling. Caches are optimizations with live fallbacks, never prerequisites. Every read checks stamps.

The surface, indicative names:

- `get_sensors(...)` — the full metadata table: registration, geometry with declared precision, type, fetch accounting, membership, exclusion, per-channel quality;
- `get_record(snr_id, channels=None, window=None)` — published daily primitives with per-day provenance; source uids resolve too, through the metadata mapping, as a convenience;
- `get_native(uid, channels=None, window=None)` — the sensor's **sub-daily observations**, UTC-indexed, in the source's own names and units, before any scrub or canonicalisation. This is the only way a model can choose its own day: the published record is collapsed to Central Standard calendar days, which the build must decide and cannot un-decide, and a covariate whose day runs on a different boundary (gridMET's does, by eight hours) can only be aligned by re-binning the raw samples. About 59% of sensors carry genuinely sub-daily data — 5- or 15-minute — and the rest publish daily values, where nothing finer exists to re-bin;
- `get_proximity(max_sep_m=None)` — the pair table, § Geometry;
- `get_network(layers=...)` — flowlines with VAA, catchments, waterbodies;
- `get_covariates(comids, products, window=None, remap=None)` — catchment-grain products; `remap` per § Covariates;
- `get_discrete_nitrate(states, bbox, window, ...)` — WQX discrete samples. **Not sensors**: addressed by location and time, offering no `site_uid`, and stamped `.attrs['class'] = 'discrete_sample'` so nothing mistakes a bottle at a moment for a monitored series. Quality-control activities are excluded by default;
- `get_attributes(comids, columns=None, source="streamcat")` — the COMID-keyed attribute stores, filtered before any row is materialised. `comids` is **required rather than defaulted**, and the reason is size: the nitrogen series is 7.3 GB over 1.48M reaches and 653 metrics, so an unfiltered read is a memory event, not a slow query. The filter is pushed into the parquet read rather than applied after — a six-column slice for the 342-site nitrate cohort takes 0.1 s. `attribute_columns(source)` lists what a store holds, from the footer alone;
- `get_weather(weighted_set, window)` — gridMET through the weight matrix;
- `get_drought(weighted_set, window, variables=...)` — the pentad drought indices through the **same** matrix, returning `pentad_start` and `.attrs['cadence'] = 'pentad'`. `drought_columns()` lists what the store holds;
- `snap(lat, lon)`, `accumulate(comid)`, `aggregate(weighted_set, products, window)` — the basin machinery, § Basins;
- profiles — a modeling objective's declared preferences (channels, floors, windows, classifications to honor) as a named filter over the same published state; profiles belong to objectives, are cheap and versioned, and the build knows nothing of them.

## Verification

Two harnesses, by design complementary: a **pytest suite** over small synthetic fixtures proves each rule's logic in isolation and runs in seconds, and a **`verify` CLI** runs each stage's invariants against the real stores — a registry of named checks per stage, with expensive scans behind a `--deep` flag. Every check below is runnable by a reader of this chapter; each is listed with what its failure would mean.

- **registry closure** (import time): axis vocabularies valid; the high-scrutiny tier ≤ 3 channels; `clamp_negatives ⇒ lo < 0`. Failure: a mis-specified channel entered the registry.
- **a2**: every canary registered, typed, un-skipped; every uid matches its source's namespace; arm-sensor count nonzero. Failures: a scope filter silently ate a class; a namespace rule leaks; the census clipped to boxes instead of the AOE.
- **a3**: snapshot manifests cover `raw/` + `acquired/`; the fetch partition holds (`registered = skipped + cached + source_empty + attempt_failed`); no `attempt_failed` older than the retry horizon. Failures: an unmanifested file; unaccounted registrations; a stuck retry loop.
- **a3, the weather store**: every quarter carries every variable the registry declares, with no nulls anywhere in a declared column; no quarter starts later than its own first calendar day; every quarter is an exact (cells × days) lattice. All three read parquet footers only — 0.13 s for the whole 75-quarter store. Failures, each a different thing: *a declared variable was never fetched* — the state the store sat in for as long as `VARIABLES` had entries the completeness check could not see; *a month was deleted from a quarter* — which happened, and which the lattice check cannot detect because removing whole days leaves a valid smaller lattice, hence the separate start-date assertion; *rows were dropped or duplicated inside a quarter*.
- **a3, the pentad store**: every declared aggregate present in every year file; every cell on the gridMET lattice; and the **median timestep spacing is five days**. The last is the one that matters — the store exists separately *because* it is pentad, so a daily-spaced file here means the wrong endpoint was read, and nothing else in the build would notice.
- **a3, the check harness itself**: every module that defines a check must have contributed one to the registry. `verify` names its modules literally, so adding one is a second edit that is easy to forget — and a check that never loads contributes no row rather than a failing one, so the table reports clean. This was not hypothetical: the pentad store shipped with three registered checks that never ran and passed verification at 37 of 37, and the guard immediately found a second module in the same state. It tests *registration* rather than membership of the load list, because some modules legitimately arrive through another's imports.
- **a3, WQX is never a sensor**: no `WQX` source may appear in the sensors table. This is the constraint the source is collected under rather than a tidiness rule — WQX would add 55,210 registrations and 42,209 merge candidates to machinery whose review queue has handled 136 ledger masks, so the separation is enforced rather than intended.
- **a3, the attribute declaration**: every theme the Wieczorek catalogue offers is either pulled or listed in `WIECZOREK_THEMES_DECLINED` with a reason, and no theme is in both. Failure: a theme nobody decided about — which is how 437 attributes including the entire flow-pathway partition sat unpulled.
- **a4**: every export/registry gap acknowledged or queued; the unit worklist reflects every non-verified mapping; every conflicting drift row answered. Failure: an authority inconsistency passing unnamed.
- **d2**: a sensor with nothing to scrub reproduces its raw daily series exactly; no pre-aggregated day carries a fabricated spread; every canonical file's registry fingerprint matches the live registry; scrub tallies are consistent (`n_raw = n_kept + n_sentinel + n_nulled`, clamped values counted separately). Failures: the reduction distorts clean data; stale canonicals survive a registry edit.
- **d3**: the proximity table is symmetric, complete at its radius against a direct recomputation (`--deep`), and every row satisfies `min_sep_m ≤ sep_m ≤ max_sep_m`; every located sensor has `coord_dp_*` wherever its source publishes text coordinates; coordinate sentinels have no geometry. Failures: the pair table drifted from the sensors table; precision capture silently lost.
- **d4**: merge components contradict no human `distinct`; no bundle crosses the material boundary and no cross-type bundle lacks a human decision; every multi-member composition is confirmed or queued; every published record's provenance columns name only member uids; every observed site type is classifiable against the material-boundary sets; the id ledger is append-only against its tracked history, uid ↔ SNR code is a bijection, every code is fixed-width uppercase base-36, and no code is reused. Failures: the contradiction halt is broken; the gate leaked; provenance is fabricated; ids silently renumbered.
- **d5**: per-grid closure (§ Covariates) for every product; the weight matrix conserves cell area globally; every product's AOE stamp matches the current extent. Failure: an aggregation is double-counting or missing area.
- **d5, component closure**: where a source publishes both a total and its parts, the parts must reproduce the total *through our reduction* — `fixation_ag = fixation_cropland + fixation_pasture`, and the same for uptake. The allowance is **per-pixel, in the raster's own units** (§ Covariates): the sources agree to 0.01 kg/ha at the pixel, and the reduction multiplies that by the 6.25 ha pixel, so an error growing with pixel count is rounding while one growing with value is a missing component. Passing at 0 of 26,736,174 catchment-years. This is the reason the two published totals are ingested at all: they are checks rather than covariates, and no recipe may feed a total beside its own components.
- **d5, checkpoint validity**: a resumed raster pass reuses a checkpoint only when it was written for the same request — declared value axes (product, year) read from the parquet footer, and an input stamp over the identity of every raster read. Failure: a widened request silently returns the narrower answer with every batch reporting "cached", which is how a two-component product could report success as a twelve-component one.
- **d5, karst fractions**: every fraction is a share of its catchment in [0, 1], and the *buried* classes are populated. The second half is the point — a karst layer carrying only the exposed class would reproduce `pctcarbresid` and silently lose the burial-depth distinction the source is held for.
- **d5, DMR loading**: every row is a non-negative mass in kg N below the physical ceiling, names the tier that produced it, and carries a declared species. Failure: a self-reported field published unbounded — which, unguarded, yields a single outfall claiming 8.2 × 10¹² kg of nitrogen in one month.
- **p1**: the publication partition is exact and counted; the **leak scans** — zero sentinel values, zero out-of-range values, zero in-excluded-span days, zero over-ceiling days anywhere in the published store (`--deep`). Failure: the store contains what this chapter promises it cannot.
- **network machinery**: `accumulate` reproduces NHDPlus total drainage areas on a random reach sample within tolerance. Failure: the walk the basin machinery depends on is wrong.

The chapter's contract with the reader is that these checks — not this prose — are the specification of record for "the pipeline works": every numeric rule above appears in some check, and a rule that cannot be checked does not belong in the build.

## Unimplemented Future Work

Recorded here so their absence is a decision, not an oversight.

- **Uncleaned access (`clean=False`).** The published store is the cleaned store; there is currently no access-layer path that unwinds quality masks or scrubs. The right unwind semantics depend on the eventual live-data → modeling deployment scenario, which is not yet designed, so the flag is deliberately unspecified rather than guessed at. What the build already records keeps every option open: quality masks are ledger rows and metadata columns (not destructive edits), per-sensor canonical intermediates are retained, and the raw archive is immutable — so any future uncleaned view is a read-path change, not a rebuild.
- **Groundwater screen depth.** Nested piezometers are geometrically indistinguishable, and no ingested source carries screen depth; until one does, groundwater identity pairs gate on record evidence and a human, with no geometric assist.
- **Modelled streamflow at arbitrary points.** The NWM store is scoped to sensor reaches; serving an arbitrary pin needs either a per-request remote read or a wider archive, and either is a deliberate, named exception to the access layer's offline posture — not to be introduced casually.
- **Live cadence.** Acquisition is built for a daily refresh, but the operational loop (scheduling, alerting on drift and gate queues) is not yet specified.
