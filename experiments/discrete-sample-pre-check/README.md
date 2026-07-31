# Discrete (Lab / Grab) Sample Pre-Check

Pre-check for [`notes/discrete-sample-report.md`](../../notes/discrete-sample-report.md). Everything lands in `./cache/`; nothing writes to `src/data/` or the build.

## The question

Iowa holds ~3,900 discrete nitrate stream stations against the cohort's 116 continuous sensors. Almost none is a time series — the median station has 4 samples over a 0.5-year span — so "most recent grab sample" is dead. The live idea is a **static basin-integrated level**, the same shape as `longrun_composition` or the surplus aggregates, aimed at the between-site channel.

It is also **deployment-legal in a way the in-situ discharge block is not**: delineate a basin at any pin and pool whatever third-party samples fall inside. Nothing is read from the target sensor and nothing requires the target to be instrumented.

## Run order

```
python 01_fetch_wqp.py       # ~5 min   WQP pull, unit harmonisation, dedupe
python 02_audit.py           # ~5 min   six checks; step 5 is the go/no-go
python 03_run_exp.py --full --extra   # hours   the masked sweep, logged to logs/experiments.jsonl
```

## Findings so far (2026-07-30)

**The unit trap is real, and step 01 self-checks it.** WQP returns the same USGS measurements twice, `mg/l as N` and `mg/l asNO3`, differing by 62.00/14.01 = 4.4269. Unconverted, the median reads 24.05 instead of 5.44 and the ≥10 mg/L exceedance rate reads 76% instead of 19% — it looks like a dirtier state, not like an error. Step 01 asserts the conversion by checking the two populations coincide afterwards: **ratio of medians 1.014 after conversion**, against 4.43 before. It also drops 1,925 results in unrecognised units (`ppb`, `ueq/l`, and a literal `mg n/l******`) rather than guessing.

After harmonisation and dedupe: **67,215 results over 3,751 stations**, 38% volunteer by result count.

**The volunteer pool cannot be pooled with agency data.** Where a basin holds both, volunteer means run **−3.5 to −4.1 mg/L** below agency means, and the rank correlation between them is **+0.07**. Controlling for density makes it *worse*, not better:

| min samples each side | basins | median diff | spearman |
|---|---|---|---|
| 1 | 56 | −3.46 | +0.07 |
| 10 | 36 | −4.10 | −0.28 |
| 50 | 28 | −3.66 | **−0.42** |

Sampling noise would wash out with more data. Anti-correlation that *strengthens* with density is methodological.

**But the agency aggregate does carry between-site signal** — which is what actually decides this, and it is why step 5 supersedes step 3. Correlating each basin's grab aggregate against that site's own long-run sensor mean:

| pool | basins | spearman | pearson | median abs err |
|---|---|---|---|---|
| all orgs | 92 | +0.39 | +0.46 | 2.25 mg/L |
| volunteer only | 82 | +0.38 | +0.33 | 2.87 |
| **agency only** | **66** | **+0.53** | **+0.48** | **1.84** |

Volunteer-only does not improve with density (+0.37 at ≥100 samples), consistent with the anti-correlation. And pooling *dilutes* the agency signal, +0.53 → +0.39 — evidence against pooling, not against the data.

**Coverage is the binding constraint on the agency pool.** 57% of cohort basins hold ≥1 agency station, median 1 station, and **53% hold fewer than 5 samples**. So the block will be NaN for roughly 43% of sites. XGBoost routes that as a learned default direction, but it caps how much the feature can move a pooled metric.

**Era.** Median sample year per basin is 2006 (p10 2002, p90 2011) — largely pre-dating the 2008+ training window, so the aggregate describes an earlier agricultural regime than the target.

## Verdict

**Run step 03 on `--pool agency`.** The report's original stop condition — pools disagree *and* coverage is thin — is met on both limbs, but it was a proxy for "is this worth anything," and the direct measurement says the agency signal is real. ρ = +0.53 against the exact quantity the between-site channel is trying to rank is worth one CV run.

Do not run `--pool all`. It is measurably worse.

## Reading step 03

| arm | what it adds |
|---|---|
| `base` | nothing — the shipped recipe |
| `grab_level` | basin mean + p90 nitrate as N |
| `grab_exceed` | fraction of basin samples ≥10 mg/L — the discrete analogue of the CLF label |
| `grab_coverage` | station count, sample count, median era only — **the negative control** |
| `grab_all` | everything |

**Read `grab_coverage` first.** Station density tracks population, road access and watershed-project funding, all correlated with land use. If the coverage-only arm carries the effect, the block is a sampling-effort fingerprint rather than a nitrate measurement.

**Judge on `lofo_between_r2` / `lofo_between_rate_r2`**, not the headline — that is the channel this targets. Against the measured floors: REG headline 0.0085, CLF headline 0.0037, and **REG `between_r2` 0.0258**, the loosest of the three. A between-channel claim needs either a large effect or seed replication.

**`--exclude-km` is not optional.** It drops grab stations sitting on the cohort sensor itself, mirroring `_except_the_target` in the neighbour work. Without it the "third-party aggregate" is partly the target's own history, and the feature stops being computable at an ungauged pin — which is the entire basis of its deployment legality.

## Not attempted

**Discrete samples as extra training rows.** The covariates are the problem, not the labels: every model feature is basin-static or weather-derived and would need building for 3,900 new locations, for records averaging four points.

**Other characteristics.** Chloride (7,100 stations) is the interesting one — a conservative tracer indexing road salt and wastewater, a source term the current feature set has no proxy for. `01_fetch_wqp.py --characteristic Chloride` extracts it; the rest of the pipeline is unchanged.

**Sensor/lab paired validation.** Where a grab station is co-located with a cohort sensor, paired values measure sensor bias directly — the R11(b) label-quality audit `future-work-report.md` wants. A diagnostic, not a feature, and nearly free once this cache exists.
