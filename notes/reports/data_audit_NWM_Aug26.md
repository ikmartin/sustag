# Data audit — NOAA National Water Model retrospective

**Verdict: acquire, at CHANOBS scope first, and use it as a covariate rather than as a discharge substitute.** Unlike the other audits in this set, NWM's *inclusion* was never in question — modelled discharge at every reach is the strongest single covariate for nitrate and we hold observed discharge only where a gauge sits. What was in question was **scope** (which product, which reaches, which years) and **fitness** (whether a model with a documented corn-belt weakness is good enough on *our* rivers). Both are now measured.

Audited 2026-08-26. Total data downloaded to reach these conclusions: **under 2 GB.**

## What the source is

Hourly simulated streamflow for **2,776,734 NHDPlusV2 reaches**, `feature_id` = COMID with no crosswalk. The v3.0 retrospective spans 1979-02 → 2023-02 as zarr on anonymous S3; `gs://national-water-model` continues to the present.

**Leakage is clean and documented rather than inferred.** NOAA's Big Data Program states the retrospective "did not assimilate stream gauge observations", and the store corroborates it — no `nudge` variable exists anywhere in the retrospective, unlike the operational files. This is what makes NWM more attractive than SPARROW: it needs no leakage audit at all.

## Why it was gated

Two decisions with large consequences, neither answerable from documentation.

**1. The read cost, which the chunking makes counter-intuitive.** The zarr is chunked `[672 hours, 30000 features]`, so **the full series for one COMID costs the same as for 30,000** — cost scales with how many feature *blocks* a reach set touches, not how many reaches it names.

**2. Fitness for our region.** NWM does not represent tile drainage, and corn-belt hydrograph timing and low-flow skill are its documented weak spot. That was an assumption inherited from a paper, and we own the ground truth to test it: `discharge` is a registry channel at our USGS sites, and NWM predicts discharge at those same COMIDs.

## What the audit did

**Cost, computed from metadata alone — no data downloaded.** Read the consolidated metadata and the `feature_id` coordinate array (~11 MB), mapped our COMIDs to chunk indices, and sampled stored chunk sizes from S3.

| quantity | measured |
|---|---|
| logical chunk | 161.28 MB |
| stored chunk (compressed) | **6.63 MB** — a 24.3× ratio |
| feature blocks our 342 nitrate COMIDs touch | **58 of 93** |
| time chunks covering 2008 → Feb 2023 | 197 of 574 |
| **zarr download, nitrate cohort, 2008+** | **~76 GB** |

**CHANOBS intersection.** `CONUS/netcdf/CHANOBS/` carries the same model's output at only the **8,643 gaged reaches**, ~26 GB for 2008–2023.

| set | in CHANOBS |
|---|---|
| our 342 nitrate COMIDs | **148 (43%)** |
| our 5,558 discharge COMIDs | 4,113 (74%) |
| nitrate **and** discharge **and** CHANOBS | **147** |

**Skill, measured on 19 of our own reaches**, daily, 2018–2019, NWM against our published `discharge`:

| metric | median |
|---|---|
| Nash–Sutcliffe | **0.35** |
| Kling–Gupta | **0.646** |
| log-space NSE | **0.521** |
| bias (predicted / observed mean) | **1.023** |
| correlation | 0.701 |

17 of 19 reaches beat predicting the mean (NSE > 0).

## What the audit determines

**Scope.** CHANOBS is *not* a substitute — it reaches 43% of our nitrate sites. But at ~26 GB against ~76 GB it covers enough for the skill question and for nearly half the cohort, so it is the right first pull. The zarr route is affordable (76 GB, not the 326 GB a scattered CONUS set would cost) and is justified only if ungaged coverage proves worth roughly 50 GB.

**Fitness.** NWM is **fair and very nearly unbiased** on our reaches. A median bias of 1.023 means it gets the water balance essentially right; a median NSE of 0.35 with KGE 0.646 means it captures the shape of the hydrograph without being accurate enough to stand in for a measurement. **Use it as a covariate, not as a gauge.**

One result runs against the received wisdom and is worth flagging: **log-space NSE (0.521) exceeds ordinary NSE (0.35)**, meaning NWM does *relatively better* on low and moderate flows than on peaks. The documented corn-belt low-flow weakness did not reproduce as the dominant failure here — the errors are concentrated in high flows instead.

## What the audit does NOT determine

- **Skill at ungaged reaches.** CHANOBS covers gaged reaches by definition, and gaged-reach skill is an **upper bound** on skill elsewhere — gauges sit at well-behaved, well-instrumented locations, and NWM's calibration saw them. Since the entire value of the expensive zarr pull is discharge *where no gauge exists*, this is the audit's central unanswered question, and it is not answerable with the data that exists.
- **Whether the sample generalises.** 19 reaches in two feature blocks, chosen for read economy rather than representativeness, over two years. One reach (SNR-08YS) scores NSE −1.412, and nothing here explains why.
- **Whether NWM adds anything beyond what we already hold.** That is a modelling question, deliberately out of scope for a data audit.
- **The post-2023 splice.** Untested here. It must use `analysis_assim_no_da`: measured on a single hour, 6,214 reaches are directly nudged and **164,651 (5.9% of CONUS) differ** from the no-DA configuration, because assimilation propagates downstream through routing. The default would inject gauge observations into our features. The 2023-02 → 2023-09 window is also NWM **v2.2**, not v3.0, and needs a version flag rather than a silent concatenation.

## Recommendation

1. Pull **CHANOBS for 2008–2023** (~26 GB) and narrow `parameters/sensor_comids.txt` first — it currently holds 6,945 COMIDs where the nitrate-bearing set is 342.
2. Take `streamflow` plus `q_lateral`/`qSfcLatRunoff`. **Skip `ldasout` entirely** — 1–7 TB per variable on a 1 km grid needing basin aggregation, for signal the channel terms partly carry.
3. Defer the zarr pull until a model has shown that gaged-reach NWM earns its place. If it does, 76 GB is affordable.
