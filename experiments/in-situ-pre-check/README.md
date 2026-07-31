# In-Situ Covariate Pre-Check

Pre-check for [`notes/extra-in-situ-report.md`](../../notes/extra-in-situ-report.md) — decides whether donor-gauge discharge is worth integrating into the pipeline, **before** any pipeline work. Everything lands in `./cache/`; nothing writes to `src/data/` or the build, so this directory can be deleted without consequence.

## The question

The 116-site cohort has discharge at 25% of sites and essentially no other covariate. USGS runs 1,192 instantaneous-value discharge gauges inside the covariate footprint. Can each nitrate site borrow a nearby gauge's discharge as a feature?

The catch: 36 IWQIS sites sit within 200 m of a USGS gauge, so the naive "nearest gauge" feature reads **the target's own discharge**. An arbitrary map pin has no gauge, so that is train/serve skew — and LOFO cannot catch it, because LOFO holds out *sites*, not *instrument availability*.

The fix is to hide every gauge within an exclusion radius `r` and take the nearest survivor, simulating a pin that only reaches distant gauges. That turns the question from "is this deployable" into "how far away can a gauge be and still help", which is a measurable curve.

## Run order

```
python 01_enumerate_iv_sites.py      # ~3 min   the live USGS pool, all 8 core pcodes
python 02_link_donors.py             # seconds  top-3 donors per site per radius + the coverage curve
python 03_fetch_discharge.py         # ~10 min  daily-mean series for every linked donor (incremental)
python 04_run_exp.py --full --extra  # hours    one masked sweep per radius, logged to logs/experiments.jsonl
```

`02` and `03` interact: raising `--topk` in `02` links more donors, and `03` fetches only the ones it has not already cached.

## What each step establishes

**01** — the pool. 1,192 discharge / 1,012 stage sites in footprint, against 198 temperature, 97 specific conductance, 73 turbidity, 65 DO, 52 pH. Only discharge and stage are dense enough to survive an exclusion radius; the rest are enumerated so the counts can be re-checked cheaply, not because they are candidates.

**02** — the coverage curve. Linked coverage is **100% of the cohort at every radius up to 100 km**: the network is dense enough that no exclusion radius strands a site, so `r` is a free design parameter rather than a trade-off. The 0.1 → 12.2 km jump between `r=0` and `r=1` is the co-location itself.

**03** — usability. 235 donors fetched, median record span 18.6 yr, 225 clearing new-site-report's 3.92 yr bar, 202 still reporting. **63 donors publish instantaneous values but no daily-mean product** — which is why `02` links the top 3 and `04` walks the ranks. Rank-1-only coverage is 74–92%; with fallback it is a flat **97%** at every radius, for about 1.5 km more separation.

**04** — the answer. One sweep per radius, five arms.

## Reading step 04

| arm | what it adds |
|---|---|
| `base` | nothing — the shipped recipe |
| `q_lagged` | donor discharge at lag 1/3 — **causal, the deployable form** |
| `q_concurrent` | lag 0 — non-causal reference, measures the causality tax |
| `q_coverage` | donor distance + drainage-area ratio only — **the negative control** |
| `q_all` | everything |

**Read `q_coverage` first.** Gauge density tracks basin size, road access and funding history, all of which correlate with land use. If the coverage-only arm carries most of the effect, the block is a site-type fingerprint, not a hydrologic signal. This is the test that gave exps 33–36c their credibility, where the equivalent arm came back at +0.0008 REG.

**Read `r=0` as a control, not a candidate.** It is the co-located feature. Its gap to `r=1` is the amount of apparent skill that comes from reading the target's own gauge — the train/serve skew the deployable version avoids.

**Check the `base` arm across radii.** It holds no donor columns, so it must score identically at every radius. If it does not, something moved rows and the deltas are not comparable.

**Judge against the measured noise floors** — REG 0.0085, CLF 0.0037 on the headline. And hold the prior: `future-work-report.md` measured perfect *measured* specific discharge moving cross-sectional R² by +0.001 over crops+tile, and C–Q exponent b = +0.61 means Iowa nitrate mobilizes rather than dilutes. Availability was never the argument against discharge.

## Known limits

**Distances are great-circle**, so a "12 km donor" may sit across a divide. That is a lower bound on hydrologic separation. `02_link_donors.py --containment` runs the point-in-basin test that replaces it for the upstream half; the downstream half needs each donor's own delineation and is deferred to NLDI.

**Donor selection ignores drainage scale.** Exps 33–36c found that picking a nitrate donor by drainage-area ratio versus distance changes the pick for 20% of sites. The same question applies here and is untested.

**Not attempted:** stage (near-redundant with discharge — same rating curve), and the four sparse chemistry covariates, which at 12–16% cohort coverage within 10 km cannot clear the noise floor in either direction.
