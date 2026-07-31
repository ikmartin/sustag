# Discrete (Lab / Grab) Water-Quality Samples as Model Features

**Question:** the cohort's 116 continuous sensors are the whole training set. Iowa also holds thousands of *discrete* grab-sampled stream stations in the Water Quality Portal. Can they be used — as extra "most recent sample" observations, as static basin aggregates, or otherwise? Compiled 2026-07-30. Live WQP enumeration plus containment measurement against the actual 116 cohort basins. Companion to [extra-in-situ-report.md](extra-in-situ-report.md), which covers the continuous-sensor side of the same question.

---

## 1. Bottom line

**The pool is 35× the cohort, and almost none of it is a time series.** Iowa has **3,999** discrete nitrate stream stations against 116 continuous ones. But sample density is the constraint that decides everything: on the best-documented credible source (USGS Iowa WSC, 2000+), the median station has **4 samples** across its entire record and a **0.5-year** span. Only **28 of 209** stations reach 20 samples *and* 5 years.

**So the "most recent sample" framing is dead, and the static-aggregate framing is the live one.** A feature like "days since last grab sample, and its value" would be missing or years stale for most of the pool. A *basin-integrated long-run level* — the same shape as `longrun_composition` or the surplus aggregates — asks only for a handful of samples anywhere inside the basin, which is exactly what the data provides.

**And it is deployment-legal in a way the in-situ covariates are not.** This is the strongest argument in this report. A co-located discharge gauge fails the arbitrary-pin test because the pin has no gauge. A basin aggregate over *historical third-party grab samples* does not: given a delineated basin at any pin, you pool whatever samples fall inside it. Nothing is read from the target sensor, and nothing requires the target to be instrumented — the same property that makes R12's donor channel legal.

**Coverage is the open question, and it is order-of-magnitude sensitive to whether you trust volunteer data.** 79% of cohort basins contain at least one discrete nitrate station across all organisations; excluding the Iowa Volunteer Water Monitoring Program (which is 73% of all stations) that falls to 58%, with a median of 1 station per basin and **49 basins holding none**.

> **UPDATE 2026-07-30, from `experiments/discrete-sample-pre-check/`.** E1 has been run and it settles two of the open questions below, in opposite directions.
>
> **The volunteer pool cannot be pooled with agency data.** Where a basin holds both, volunteer means run 3.5–4.1 mg/L *below* agency means and the rank correlation between them is +0.07 — and controlling for density makes it worse, not better: −0.28 at ≥10 samples each side, **−0.42 at ≥50**. Sampling noise washes out with more data; anti-correlation that strengthens with density is methodological.
>
> **But the agency aggregate carries real between-site signal, which is what actually decides this.** Correlated against each site's own long-run sensor mean: agency-only **ρ = +0.53** (Pearson +0.48, median abs error 1.84 mg/L) over 66 basins; volunteer-only +0.38 and flat with density; all-orgs +0.39, i.e. pooling *dilutes* the agency signal.
>
> So §6's stop condition — pools disagree **and** agency coverage is thin — is met on both limbs, and it should nonetheless **not** stop the experiment. Agreement between two pools was a proxy for usefulness; correlation against the target quantity is the thing itself. E2 is worth running on `--pool agency`, accepting that the block is NaN for ~43% of sites.

**One trap that will silently corrupt any naive pull:** WQP returns the same USGS measurements twice under two unit codes, `mg/l as N` and `mg/l asNO3`, differing by the stoichiometric factor 4.43. Pooled unconverted, the median nitrate reads 24.05 instead of 5.44 and the ≥10 exceedance rate reads 76% instead of 19%. Any aggregate built without unit harmonisation is nonsense, and it will not look like nonsense.

**Recommended sequence:** unit-harmonised extraction and a credibility audit (E1, ~1 day) → a single static basin-aggregate block through the exp-33 harness aimed at `between_r2` (E2, ~1 hour of fits) → only then consider the sparse-observation ideas in §5.

---

## 2. What is out there (live WQP enumeration, 2026-07-30)

Water Quality Portal station search, `statecode=US:19&siteType=Stream`, by characteristic:

| characteristic | stream stations in Iowa |
|---|---|
| Chloride | 7,100 |
| Orthophosphate | 6,009 |
| **Nitrate** | **3,999** |
| Turbidity | 2,239 |
| Specific conductance | 2,138 |
| Phosphorus | 489 |
| Ammonia | 243 |

Nitrate stations by organisation:

| organisation | stations |
|---|---|
| Iowa Volunteer Water Monitoring Program | 2,915 |
| USGS Iowa Water Science Center | 378 |
| EPA National Aquatic Resources Survey | 199 |
| Iowa Geological Survey — Watershed Snapshots | 180 |
| Polk County Conservation Board | 130 |
| Iowa DNR Surface Water Monitoring | 74 |

**73% of the pool is one volunteer programme.** That is not automatically disqualifying — IOWATER-style programmes use standardised kits and their data is in WQP precisely because it passed a submission standard — but it is a single methodological population, and treating it as interchangeable with USGS lab results is an assumption, not a fact. Every coverage number below is reported both ways.

---

## 3. Sample density — the binding constraint

Measured on USGS Iowa WSC discrete nitrate results, 2000-01-06 to 2023-08-16, 7,807 results over 209 stations:

| quantity | value |
|---|---|
| samples per station, median | **4** |
| samples per station, p90 | 63 |
| record span per station, median | **0.5 yr** |
| record span, p75 / p90 | 4.2 / 11.9 yr |
| stations with ≥20 samples **and** ≥5 yr | **28 / 209** |
| results since 2015 / since 2020 | 23% / 9% |

Two consequences follow directly.

**The data is historical, not current.** Nine per cent of results postdate 2020. A feature that asks "what was the most recent grab sample here" answers with something years old at most stations, and the answer's staleness varies by station in a way correlated with which organisation sampled there — a nuisance variable, not a signal.

**Four samples is enough for a level, not a trajectory.** A station's mean over four samples is a noisy but unbiased estimate of its long-run level if sampling is not flow-biased (it usually is — grab campaigns oversample accessible conditions). Pooled over the ~10 stations inside a median basin, the basin-level estimate is considerably better than any individual station's. That is the entire case for §4.

---

## 4. The one design worth building: a static basin-integrated level prior

Counting discrete nitrate stations falling **inside** each of the 116 cohort basin polygons:

| | basins with ≥1 | ≥5 | ≥20 | median | max |
|---|---|---|---|---|---|
| all organisations | 79% | 62% | 41% | 10 | 1,045 |
| excluding volunteer | 58% | 37% | 16% | 1 | 337 |

**Why this shape and not another.** The project's stated bottleneck is the between-site ranker — "a good timer, a bad site-ranker," `within_r2` 0.42 against `between_r2` 0.21 historically, and every static basin attribute in the manifest exists to attack it. A basin-integrated historical nitrate level is about as direct an attack on that channel as exists: it is a *measurement* of the quantity the model is trying to rank basins by, rather than a proxy for it (crops, surplus, tile drainage are all proxies).

**The proposed block**, static per site, computed once and broadcast like `longrun_composition`:

- `grab_n_mean` — unit-harmonised mean nitrate-as-N over all discrete samples inside the basin
- `grab_n_p90` — the upper tail, which is what the CLF violation target actually cares about
- `grab_n_exceed_rate` — fraction of samples ≥10 mg/L as N, i.e. the discrete-sample analogue of the model's own label
- `grab_n_stations`, `grab_n_samples` — the coverage scalars, which double as the negative control
- optionally `grab_n_era` — median sample year, since a 1998-sampled basin and a 2022-sampled basin are not measuring the same agricultural regime

**The leakage question, and why it is answerable.** These are *other* sites' measurements of the same quantity the model predicts, which is a stronger form of what `rest_of_state_nitrate_lag*` and the exps 33–36c donor features already do. Two disciplines make it honest: (a) exclude any discrete station within a co-location radius of the cohort sensor itself, exactly as `_except_this_mean` excludes the target from its own baseline; (b) because the block is static and the aggregate spans decades, it cannot leak the target's *daily* variation — only its level, which is precisely the between-site quantity being predicted. Note (b) cuts both ways: a feature that is a direct measurement of the target's own long-run level will look spectacular under LOFO and is only legitimate if the same aggregate is computable at an ungauged pin. It is — but only from *third-party* stations, so (a) is not optional.

---

## 5. Ideas considered and ranked lower

**Discrete samples as extra training rows.** Treat each grab sample as an additional labelled observation and pool it with the sensor rows. Attractive because it multiplies the label count, but the covariates are the problem, not the labels: every feature the model uses is basin-static or weather-derived and would have to be built for 3,999 new locations, which is the full delineation cost of `new-site-report.md`'s Tier 2 expansion for records averaging four points. Poor ratio.

**"Most recent sample" as a dynamic feature.** Ruled out by §3 — median 0.5-year span, 9% of data since 2020. It would be a staleness indicator wearing a nitrate feature's clothes.

**Other characteristics as basin aggregates.** Chloride (7,100 stations) is the interesting one: it is a conservative tracer and a fairly direct index of urban/road-salt and wastewater influence, which is a source term the current feature set has no proxy for at all. Orthophosphate (6,009) indexes a different nutrient pathway and is co-limiting in Iowa streams. Both are strictly worse-motivated than nitrate itself for a nitrate model, but if E2 succeeds they are the obvious second and third columns, and they come from the same extraction with a changed `characteristicName`.

**Discrete samples for label-quality audit rather than features.** Where a grab station is co-located with a cohort sensor, the paired sensor/lab values measure sensor validity directly — which is what R11(b) in `future-work-report.md` wants turbidity for. This is a diagnostic, not a feature, and it is nearly free once E1's extraction exists.

---

## 6. Low-cost experiments, in order

### E1 — Harmonised extraction and credibility audit (~1 day, no fits)

Pull WQP `Result` for `characteristicName=Nitrate`, Iowa, streams, all organisations. Then:

1. **Harmonise units first.** Convert `mg/l asNO3` → `mg/l as N` by dividing by 4.4269 (62.00/14.01). Verify the conversion by checking that the two unit codes' distributions coincide afterwards — on the USGS subset they are the *same* measurements duplicated, so a correct conversion makes them identical, not merely similar. Drop or flag anything in a third unit.
2. **Deduplicate.** The same result appearing under two unit codes must not be counted twice; key on `(MonitoringLocationIdentifier, ActivityStartDate, ResultMeasureValue-as-N)`.
3. **Audit the volunteer subset against the rest.** For basins containing both volunteer and agency stations, compare the two basin means. If they agree within the noise, use the full pool and gain 79% coverage; if they diverge systematically, use the credible subset and accept 58%.
4. **Report flow bias if cheap.** For the ~29 basins with a co-located discharge gauge (see extra-in-situ-report E1), compare the discharge distribution on sample days against all days. Grab campaigns oversample low flow; if the bias is large, the basin mean is a biased level estimate and should be reported as such rather than corrected.

**Stop condition, as originally written:** if step 3 shows the volunteer and agency populations disagree *and* the credible-only coverage is the 58%/median-1-station picture, the block is unmeasurable for half the cohort and E2 should not run.

**Superseded by measurement.** Both limbs came true and the rule was still wrong, because agreement between two pools is only a proxy for usefulness. The direct check — does a basin aggregate predict the site's own long-run sensor level — is the thing itself, and agency-only returns ρ = +0.53. **The stop condition is now: kill the block only if that correlation is near zero.** `02_audit.py` reports it as step 5 and says so in its docstring.

### E2 — One static block through the exp-33 harness (~1 hour of fits)

Register exp 38/38c as a `MaskedSweep` reusing `_neighbor_ablation`'s structure — the paired `base` arm, the `_FLOOR` significance marking, the `n_cols` unmeasurability guard and the cross-experiment comparability check all apply unchanged.

Arms: `base`, `grab_level` (`mean` + `p90`), `grab_exceed` (`exceed_rate`), `grab_coverage` (`stations` + `samples` only — **the negative control**), `grab_all`.

Read `grab_coverage` first and read it hard. If the coverage-only arm carries most of the effect, the block is a station-density fingerprint — density tracks population, road access and watershed-project funding, all of which correlate with land use — not a nitrate measurement. This is the same test `nbr_coverage` was built for in exps 33–36c, and there it correctly came back near-zero (+0.0008 REG), which is what gave the neighbour result its credibility.

Judge on `lofo_between_r2` / `lofo_between_rate_r2` rather than the headline, since that is the channel the block targets, and against the measured floors: REG headline 0.0085, CLF headline 0.0037, **REG `between_r2` 0.0258** — the last is the relevant one and it is the loosest, so a between-channel claim needs a large effect or seed replication.

### E3 — Sensor/lab paired validation (~half a day, diagnostic only)

Where a discrete station sits within ~100 m of a cohort sensor, compare same-day lab values against the sensor's daily value. Produces a direct estimate of sensor bias and of the 9.6–10.4 mg/L label noise band that `future-work-report.md` R11 flags as an unresolved ceiling. Not a feature; a bound on how good any model can look.

---

## 7. Evidence quality

**Measured directly, reproducible:**
- Basin containment counts in §4 — WQP station coordinates tested against `get_basin(site_uid)` polygons for all 116 cohort sites.
- The unit-duplication finding in §1/§6 — 3,816 results under each of `mg/l as N` and `mg/l asNO3`, ratio of medians 4.43 against the stoichiometric 4.4269.
- Sample-density and record-span statistics in §3, and the organisation breakdown in §2.

**Live external query, 2026-07-30, time-varying:**
- All WQP counts, from `https://www.waterqualitydata.us/data/{Station,Result}/search`. Station counts will drift as organisations submit. **The §3 density figures are from the USGS Iowa WSC subset only** (209 stations, 7,807 results) and are extrapolated to the wider pool as an argument, not a measurement — the volunteer programme's density is unmeasured and could be materially different in either direction.

**Not verified:**
- ~~That volunteer-programme values are comparable in accuracy to agency lab results.~~ **Measured 2026-07-30: they are not.** Volunteer basin means run 3.5-4.1 mg/L low and anti-correlate with agency means at -0.42 once both sides have >=50 samples. Use agency only.
- Flow bias in grab sampling. Asserted from general practice, not measured here; E1 step 4 measures it.
- That basin-aggregated grab nitrate carries between-site signal **beyond the crops/surplus/tile proxies already in the model**. The ρ = +0.53 above is against the sensor level in isolation, NOT against what the shipped recipe already explains -- the model's between_r2 is ~0.44, so much of that correlation may already be captured. This remains the entire hypothesis and only E2's paired `base` arm tests it.

**Carried from other reports:**
- The between/within diagnosis and the `between_r2` bottleneck framing (`future-work-report.md`, `METRICS.md`).
- The measured noise floors (`experiments/specific-small-experiments/exps.py`).
- R11(b)'s label-quality and sensor-validity framing (`future-work-report.md`).
