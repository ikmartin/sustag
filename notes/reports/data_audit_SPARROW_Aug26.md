# Data audit — USGS dynamic SPARROW (CONUS, seasonal TN)

**Verdict: ACQUIRE THE SOURCE SHARES ONLY, and never as a near-neighbour discriminator.** All three decisive checks are now run against the real data. SPARROW is confirmed conditioned, so every routed total is off-limits; it is confirmed to collide on exactly the site pairs this project cares most about, so it cannot distinguish them; and what survives — the `sh_*` source shares, `DEL_FRAC`, `rchtot` — is a legitimate basin-scale descriptor for the 82% of sites holding a unique GenID.

**Status: FUTURE WORK — not pursued further in the current plan (Isaac, 2026-08-27).** The audit is finished and the verdict stands; acquisition is deliberately deferred. Nothing downstream depends on it, and the restricted column list plus the collision flag are recorded here so the work resumes from a verdict rather than from scratch.

Audited 2026-08-26, completed 2026-08-27. Working files were extracted, measured, and deleted; Isaac retains the archives externally.

## What the source is

Seasonal source-specific total-nitrogen and total-phosphorus loads for **263,494 reaches**, 2000–2020, from the USGS dynamic SPARROW application (ScienceBase `65786839d34e952b22746520`, DOI 10.5066/P9IJLEIY). Thirteen files totalling ~53 GB.

## Check 1 — conditioning: CONFIRMED

The signature is `SE_PLOAD_TOTAL` reaching a range-minimum of exactly 0 while `SE_PLOAD_TOTAL_ND` stays strictly positive. A zero-width confidence interval on a routed total is what conditioning produces and nothing else does. Streamed from `predict_tn_boot.csv` (columns 9 and 42), 4,000,000 rows scanned:

| column | minimum | exact zeros |
|---|---|---|
| `SE_PLOAD_TOTAL` | **exactly 0.0** | 754 (0.02%) |
| `SE_PLOAD_TOTAL_ND` | 0.0002053234 | 0 |

This is the model's own documentation (TM6-B3 Appendix D.6) visible in the shipped data: at a calibration reach "the monitored flux is substituted for the predicted flux... this monitored value being used in the subsequent predictions of downstream flux", with the standard error there "set to zero". The `_ND` column is the same quantity computed without that substitution, and it never reaches zero because a prediction always carries uncertainty.

**What follows:** `PLOAD_TOTAL` and every concentration or routed total derived from it **is our target, routed** wherever the network passes a calibration station. Using it as a covariate would feed the model an observation of what it predicts. The refuse list is not precautionary — it is measured.

The mitigation the release permits is narrow but real: the same passage computes source shares as "the predicted source share times the monitored flux", so **the shares are predictions, not substitutions**, and survive conditioning intact.

## Check 2 — calibration-station overlap: 23.4% of our sites sit on one

From `resids_tn.csv`: **182,883 calibration records across 2,424 distinct TN stations**, occupying **2,424 GenIDs**.

| quantity | measured |
|---|---|
| our nitrate sites sitting on a calibration GenID | **85 of 364 (23.4%)** |
| our sites within 1 km of a calibration station | 83 |
| within 10 km | 165 |
| within 25 km | 281 |
| median distance to the nearest calibration station | **11.2 km** |

The two independent measures agree (85 sharing a GenID, 83 within a kilometre), which is the expected result if calibration stations are placed at network points-of-interest — the same places a nitrate sensor goes. Nearly a quarter of our cohort sits at a reach where the shipped total *is* a monitored observation, and most of the rest lies downstream of one, since substitution propagates to "subsequent predictions of downstream flux".

This is what makes the conditioning finding load-bearing rather than theoretical: the contamination is not in some distant corner of the network, it is concentrated on our own sites.

## Check 3 — GenID collisions: 17.9%, concentrated on the pairs

SPARROW keys on GenNet's `genid`, not COMID — roughly a 10:1 aggregation, median reach 4.42 km. Measured by snapping all 364 nitrate-bearing published records to the nearest GenNet reach (`conus_flow.gpkg`, 132,354 reaches in our bounding box; every site matched, median snap distance **35 m**):

| quantity | measured |
|---|---|
| distinct GenIDs used by our 364 sites | **328** |
| GenIDs carrying more than one of our sites | **29** |
| our sites sharing a GenID with another | **65 (17.9%)** |
| worst single GenID | **4 of our sites** |

17.9% against the ~4.3% a random COMID set would show — four times worse, as predicted. But the rate is the less important half.

**The collisions are concentrated exactly on the paired deployments.** Median nearest-neighbour distance is **245 m for sites that collide** against **712 m for sites that do not**, and of records whose nearest neighbour is under 1 km, **52% collide** — against 22% for those a kilometre or more apart.

So SPARROW does not fail randomly. It assigns **identical values to precisely the upstream/downstream pairs that were deployed to measure a difference** — the intervention pairs that are this project's most informative contrasts, and the ones the identity layer spent its whole review budget adjudicating. A SPARROW-derived feature would look like a real covariate while being constant across exactly the comparison a model most needs to resolve.

## What to acquire, and under what constraints

**Take:** the `sh_*` source shares (34 defined columns, e.g. `sh_TNwwtp_kg`), `DEL_FRAC` (delivery fraction to the nearest target reach), `rchtot` (seasonal time of travel), and `tile`. These are predictions throughout, unaffected by conditioning, and they describe basin-scale source apportionment that gTREND and StreamCat do not supply in routed form.

**Refuse:** `PLOAD_TOTAL`, `SE_PLOAD_TOTAL`, every concentration, and anything else routed. Measured contamination, not a precaution.

**Carry the collision flag.** Any model using SPARROW must carry a per-site flag marking the 65 records sharing a GenID, so a degenerate contrast is visible rather than silent. The per-site `genid` map produced by check 3 is the artifact that makes this possible and is the thing worth keeping from this audit.

**Temporal limit:** SPARROW stops November 2020 against a record running to 2026, so it is a static or slowly-varying descriptor regardless. That is acceptable for source apportionment, which changes on a decadal scale, and disqualifying for anything meant to track a year.

## What this audit does NOT determine

Whether the shares carry information beyond what gTREND and StreamCat already supply. That is a redundancy question, and it belongs to the Tier 1 thematic audit (`data/insights`), which measures exactly this — not to a source audit, which can only establish that a source is admissible. SPARROW is now admissible in a restricted form; whether it earns its place is the next question, and the six-test battery answers it.

## Provenance

Files measured and then deleted (Isaac retains them on external media):

| file | size | used for |
|---|---|---|
| `predict_tn_boot.csv` | 17.1 GB | check 1, streamed by column, never loaded |
| `conus_flow.gpkg` | 534 MB | check 3, the GenNet reach network |
| `resids_tn.csv` | 112 MB | check 2, the calibration stations |

All three came from `conusModelsDR.zip` and `tn.zip`, requested through the ScienceBase CAPTCHA form. **The gate is mechanical, not technical:** all eight large files in the release carry `pathOnDisk: __s3__` and are served through `catalog/item/requestDownload`, an email request form. The five small files (`ReadMe_conus.txt`, `dataDictionary_conus.csv`, the XML metadata, two GIFs) are directly fetchable through their `url` field. Anyone reproducing this audit needs the same browser step; the measurements above are what it buys.
