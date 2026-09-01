# discharge-test — can a cheap model replace NWM?

Regionalised daily discharge from data already on disk, scored on basins the model has never seen. The point is not the model: it is the **NWM decision**. If a model built from gridMET, StreamCat and the flow network reaches NWM's gaged-reach skill, then a 26–76 GB acquisition buys nothing, because gaged skill is a *ceiling* on ungauged skill and NWM would be paying to extend a covariate into the regime where it is weakest.

## Running it

```
cd projects/discharge-test
python assemble_fast.py --validate 12    # one-time, ~7 min, writes cache/weather/
python run.py --limit 250 --stride 7     # smoke
python run.py --stride 7 --name full     # the cohort
```

`assemble_fast.py` must be run first; `run.py` reads only its cache.

## What the numbers mean

The headline is the **median per-site NSE over held-out sites**, the same quantity the NWM audit reported on 19 of our own reaches: **NSE 0.35, KGE 0.646, log-NSE 0.521, bias 1.023, r 0.701**.

**The holdout is spatial, and that is the whole design.** Predicting held-out *days* at a site the model already knows is a much easier problem — it has seen that basin's response and only interpolates in time. We need discharge at 158 nitrate sites with no gauge at all, so folds are grouped by basin **family**: a test site's basin never contains a training site's outlet. Families come from inverting the outlet map rather than comparing every pair, which is linear in total basin size instead of quadratic in sites.

**This experiment can produce a clean NO and never a clean YES.** Our own model is scored on gauged reaches too, so it faces the same ceiling. Low skill here is decisive against NWM; high skill is suggestive, and a positive result opens a separate project rather than an acquisition.

## Design decisions worth knowing

**The target is specific discharge, log-transformed.** Raw m³/s across basins spanning four orders of magnitude in area is mostly a prediction of *area* — a model would score well by learning size and nothing about hydrology. `Q × 86.4 / A` is the depth leaving the basin per day, which is what a rainfall-runoff response produces.

**Predictions are smearing-corrected.** Exponentiating a log-space fit returns the conditional median, not the mean, which is biased low for a right-skewed variable — measured at 0.76 of observed before correction, and KGE punished it twice by scoring the mean and variance ratios directly. Duan's estimator (the mean of `exp(residual)`) fixes it, computed on the **training** fold only.

**Site type is a physical filter, not a tidy-up.** The published store carries a discharge channel for tidal streams, storm drains, groundwater wells, estuaries and one atmosphere station. A rainfall-runoff model over a contributing basin is the wrong description of every one, and tidal reaches reverse flow — which is where 5.5% of rows with negative discharge turned out to come from.

**`cohort.py` is the single boundary to `data.access`.** Every read passes through it, so "does this need an access-layer change?" is answerable by reading one file. `xgboost/` takes the same stance; the two projects deliberately do not import each other, because each is bound to its own cohort definition.

**Assembly is cached because it is the only expensive step.** `api.get_weather` opens all 76 quarterly store files per call to select row groups — excellent for one basin, and paid 4,927 times it is ~7 hours. `assemble_fast.py` reads each quarter once and reduces every site from it: 7 minutes, and validated against `get_weather` itself to 7.4e-13, raising rather than writing on disagreement.

## The modelled series stays here

The inventory chapter quarantines NWM as "a simulation wearing the shape of an observation". Our own modelled discharge earns the same treatment: it is a project artifact and does not enter `published/`.
