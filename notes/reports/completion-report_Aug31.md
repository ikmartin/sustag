# Completion report — `completion-plan_Aug27.md`

**Written as a decision record.** The purpose is that in six months you can see what was decided, on what evidence, and what would change it. Detail behind every claim is in `notes/reports/pipeline-findings.md` (57 dated entries); this distils it.

## Bottom line

Five decisions were made, each on measurement rather than argument:

1. **Do not acquire NWM.** A model built from data already on disk beats it on four of five metrics, at held-out basins.
2. **WQX is collected but is never a node.** That turned a 42,209-decision review burden into zero while keeping the coverage.
3. **Karst earns its place**, and the doubt raised against it was measurably wrong.
4. **SNODAS is deferred**, because its fetch cost is eight times what the plan implied and the plan never estimated it.
5. **SPARROW stays future work**, and the comparison that motivated reopening it turned out partly circular.

The data layer gained four sources and one modelled series. The pipeline gained **11 verify checks** (37 → 48) and **36 tests** (278 → 314), most of them guarding failures that produce valid-looking output rather than errors.

## The decisions, and the evidence

### NWM: do not acquire

`projects/discharge-test/` builds a regionalised daily discharge model from gridMET, StreamCat and the flow network — no acquisition. Scored on 4,620 gauged sites in 906 basin families, five folds grouped so a test site's basin never contains a training site's outlet.

| metric | this model (median per-site) | NWM at gaged reaches |
|---|---|---|
| Nash–Sutcliffe | **0.509** | 0.35 |
| log-space NSE | **0.585** | 0.521 |
| correlation | **0.787** | 0.701 |
| bias | **1.007** | 1.023 |
| Kling–Gupta | 0.529 | **0.646** |

**The comparison favours NWM at every turn and it still loses.** Its 0.35 comes from gauges it was calibrated against; ours comes from basins the model had never seen. Gauged skill is a *ceiling* on ungauged skill, so NWM's real number where we would use it — the 158 nitrate sites with no gauge — is below 0.35, while 0.509 is already an ungauged-equivalent measurement.

**What would change this:** KGE is the one axis NWM wins, and it is the metric that punishes a damped hydrograph. If a downstream task turns out to need flow *variability* rather than level and timing, that gap is the argument for revisiting — as its own project, not a source bolted on.

**Delivered alongside:** 110 of the 158 ungauged nitrate records now carry a modelled series (749,320 values). 48 are **refused** rather than filled — wells, lakes, estuaries, tile outlets, a pond — because a rainfall-runoff response over a contributing basin describes none of them.

### WQX: collect, surface, never register

WQX would have added 55,210 registrations and **42,209 merge candidates** to a review queue that has adjudicated 136 ledger masks in its life. At ten seconds a decision that is 117 hours.

Collecting without registering makes it **zero** while keeping what is worth having: 2,112,841 discrete nitrate results across 41 states, against ~20,000 instrumented sensors. The constraint is enforced by a check, not merely intended — no `WQX` source may appear in the sensors table.

**What would change this:** if the discrete samples ever need node identity, that is a separate project with its own identity strategy, not a relaxation of this rule.

### Karst: keep, and the doubt was wrong

The original justification — "no substitute on disk" — was a search for the *word* karst, and the Wieczorek **Hydrologic** theme arrived afterwards carrying the flow-pathway partition karst is supposed to predict. Acquired it so the question could be answered rather than argued.

Regressing each karst fraction on the entire Hydrologic partition plus `pctcarbresidcat`, over 153,953 catchments: **R² of 0.001 to 0.100.** None of it is reconstructible. The two describe different things — Hydrologic models how water moves, karst records what the rock is and how deeply buried — and `pctcarbresid` predicts buried carbonate at **R² = 0.04**, which confirms the original argument rather than restating it.

### SNODAS: deferred, with numbers

Anonymous HTTPS, four wanted products present, no new dependency. But **24.5 MB in 6.2 s per day → ~11.7 hours and ~167 GB** for the ~21.6 GB the plan budgeted as *stored*. The plan sized the store and never the fetch; this is by far the most expensive acquisition in the document.

**A lever for whoever resumes:** July has **687 nonzero SWE cells of 12.0 million** against January's 6.6 million, and those few are western snowpack outside the AOE. An Oct–May window drops ~40% of days at essentially no cost here.

### SPARROW: the comparison was partly circular

Reopened to compare DMR measured loads against SPARROW's point-source share. Reading the model's own control file first showed why that cannot be clean: `TNwwtp_kg = TNwwtp_obsv_kg + mean1 + mean2`, and `obsv` is EPA permit reporting — the same lineage as our DMR. Streamed from the 19.9 GB model input: **62.1% of the national wastewater nitrogen is observed, 37.9% imputed**, and 38.6% of reach-seasons carry no observed component at all.

**The non-circular half was answered and is the useful result:** our DMR covers reaches SPARROW imputes. For municipal outfalls — exactly what SPARROW's term models — **47.0% are absent from it entirely** and another 23.5% are mostly imputed. Absence is *higher* for municipal than industrial (36.1%), the opposite of what "SPARROW only models treatment plants" would predict.

## What the data layer holds that it did not

| source | scale | reachable by |
|---|---|---|
| gridMET pentad drought | 25 aggregates × 2008–2026, 4.9 GB | `get_drought` |
| WQX discrete nitrate | 2,112,841 results, 41 states | `get_discrete_nitrate` |
| Sekellick manure split | 1,485,343 reaches, 12 columns | `get_attributes(source="sekellick")` |
| Karst by burial depth | 1,478,528 catchments, 28.6% carrying karst | `derived/covariates/karst.parquet` |
| Imputed discharge | 110 sites, 749,320 values | project artifact, deliberately not published |

## What the thematic audit established

**The covariate stack is spatially rich and temporally thin.** For eleven of twelve gTREND components the interaction term — the only genuinely spatiotemporal part — is **under 4%**. They are a static map plus a year dummy. Only `fixation_pasture` (23.7%) earns its annual detail. This is not a defect in the source: it is what county downscaling through a fixed land-use mask *means*, measured.

**Atmospheric deposition is close to one number per basin, in both dimensions at once** — 2.5–4.0% within-basin *and* 0.6–4.1% interaction. Two independent tests agree. It is stored at 250 m annual resolution and delivers roughly a constant.

**Shared ancestry does not show up as high correlation, and that cuts the wrong way.** StreamCat's `n_ff` and gTREND's fertiliser descend from the same county estimates, yet correlate at **0.517**; the deposition pair at **0.067**. The processing diverges after the shared input — accumulated versus local. A modeller reading only the correlation matrix would conclude they are independent and use both, committing exactly the double-count the lineage list exists to prevent. **The lineage column is the only thing carrying that information.**

**Wieczorek NODATA is clustered, as suspected.** `CAT_RECHG` has all 431 of its nulls inside a tenth of basins; `CAT_HGA` 83.6%. Imputing either writes a geographic pattern the data never contained. Those columns need a missingness flag, not a filled value.

**The extraction itself is now verified against something external.** Our zonal reduction against gTREND's own HUC8 aggregation, on fully-covered basins: **Pearson 0.99999, 98% within 1%**. Every other d5 check is internal and would pass on a reduction wrong the same way twice.

## Defects, and what they imply about the pipeline

Every one of these produced **valid-looking output rather than an error**, which is the common thread and the reason they are worth recording as a class.

**Guards that ask the wrong question.** Two stores froze their newest partition — `drought.fetch_year` and `wqx.fetch_state` both skipped on file existence while their newest partition was, by construction, incomplete at the moment it was written. A pentad year file created in August holds pentads to August. The file is valid, the lattice check passes, past partitions are unaffected.

Sweeping all 67 existence guards in `data/` turned this into a usable rule rather than a suspicion: **the question is not whether a file exists, it is whether the artifact's correct content depends on when it was written.** If yes — a partitioned store with a moving upper bound — something must compare stored extent against reachable extent. If no — a vendor zip, a census year, a static polygon map — existence is exactly right, and adding a freshness test would manufacture drift the snapshot machinery then has to adjudicate. NID and the facilities archive are correct as they are.

Three standing currency checks now enforce it. **The daily weather store had none and needed one most**: its three existing checks all pass on a frozen store, because each is true of the partitions it has.

**Checks that never ran.** `verify._load_all` names its modules literally, so a module can define checks and register none — contributing no row rather than a failing one, so the table reports clean. The pentad store shipped with three such checks and verification said 37 of 37. `verify` now checks itself, and that guard has since caught **four** further omissions, each before the module ran in anger.

**Operations whose cost scales with the artifact.** Three instances, none touched by the change that grew the data: the nutrients raster pass (nine days projected → 237 minutes, once the request was sized to GDAL's block cache), publication's load-then-write (~10 GB resident → 0.37 GB, streaming), and feature assembly (7 hours → 7 minutes, reading the store once instead of per site). In each case the cost was repeated I/O over a shared resource.

**Sizing assumptions that were wrong.** Four, each caught by measuring before building: WQX's `Total-Result-Count` header does not exist; `spi270d` is not published; `zip=yes` is silently ignored; SNODAS's fetch time was never estimated. A fifth would have bitten hardest — **WQX truncates with HTTP 200, a valid header row and no error marker**, returning 3.7, then 9.5, then 14.8 MB of perfectly parseable CSV before completing at 16.7.

**A validity check is worth more pointed at your own output.** The runoff-ratio test caught two different things: 3.5% of gauged sites carrying a wrong snapped drainage area, and a bug in the imputation where static features arrived as NaN and the model silently fell back to weather alone. Neither raised; both produced numbers that looked reasonable in isolation.

## What is open

- **SNODAS**, when wanted, with the Oct–May window worth confirming against AOE cells first.
- **Two orphaned stores** — `acquired/authority_basins` (72 MB) and `raw/comid_attrs` (53 MB), both referenced only in the retired `src/`. `comid_attrs` holds `BFI_CONUS.zip` and `CONTACT_CONUS.zip`, superseded by the Wieczorek Hydrologic theme.
- **The `nitrate_discrete` channel question.** `Activity_TypeCode` is carried rather than flattened because the mix is state-dependent: Iowa is **42% field measurements** against a 7.1% national average. Whether that becomes a channel distinction is unresolved.
- **The external-disk refactor**, deferred with its input measured — `raw/` is 37 GB of which 4 MB is read-path, `acquired/` is 45 GB of which 43.3 GB is — and one prerequisite named: with stores detached, all three snapshot checks pass **vacuously**.

## Evidence quality

**Measured directly, against the real stores:** every metric in the discharge table; the karst redundancy regression; the HUC8 cross-check; all thematic-audit statistics; the WQX and SNODAS fetch costs; the 42,209 merge candidates; the SPARROW observed/imputed split; the DMR-versus-SPARROW coverage; every defect and its fix.

**Measured on a sample, with the sample stated:** the discharge model's per-site scores come from 4,620 sites at a 7-day stride; the thematic battery from seeded samples of 20,000 catchments or 120 whole HUC8s; the karst regression from 153,953 catchments in 120 basins.

**Taken from the source's own documentation rather than verified:** NWM's calibration methodology; SPARROW's conditioning passage in TM6-B3; gTREND's downscaling method. Each is cited where it matters and none is load-bearing on its own — where a documented claim mattered, it was checked against the data (SPARROW's conditioning was confirmed by `SE_PLOAD_TOTAL` reaching exactly zero; gTREND's mask by the variance decomposition).

**One figure was reported wrong and corrected.** WQX's field-measurement share was first given as 0.1% from a single state-year (Iowa 2015, 929 rows). The true figure across 2,111,704 results is **7.1%**, and 42% in Iowa specifically. The categories were right; the percentages were not, and only the categories should have been asserted from that sample.
