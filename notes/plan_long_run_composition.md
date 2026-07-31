# Long-run basin composition: a between-site feature block

Proposal for a new feature family aimed specifically at `between_r2` / `between_rate_r2`. Untested — this document is the case for testing it, not a claim that it works.

## Why

`between_r2` is `r2_score(site_means.y, site_means.p)`: how well the model ranks the *level* of one site against another. It is the weakest metric in the logs (REG 0.14–0.21 against `within_r2` 0.41–0.43) and the one that matters most for ungauged-basin transfer, which is the entire point of a pin-drop forecast.

It is structurally capped by what the recipe knows about a site. Split the current feature set by whether a column can discriminate sites at all:

- **Between-blind.** `rest_of_state_nitrate_lag*` is the daily mean of every *other* sensor — for two sites on the same date it differs only by which one of ~80 is excluded. Averaged over a site's record it is essentially the same constant everywhere. `doy_*` is identical across sites by construction. Together these hold **43–55% of total permutation importance** and contribute nothing to between-site.
- **Between-capable.** `lat`, `lon`, the distance descriptors, `pct_*`, `surplus_kgha_norm`, the `*_expT` blocks, and basin-mean `fuel_moisture_1000h`.

So roughly half the model's importance is spent on features that cannot tell two basins apart, and `between_r2` rests entirely on the other half. Every remaining lever there is a **re-slice of the same two underlying rasters** (CDL crops, N surplus) — different distance rings, different decay lengths. The measured effect sizes are all inside the noise floor.

### The noise floor

`between_r2` is an R² over ~80 points. Simulated sampling spread at that n:

| | n | R² | sampling SD | 90% interval |
|---|---|---|---|---|
| REG | 81 | 0.17 | 0.079 | [0.02, 0.28] |
| CLF | 79 | 0.41 | 0.089 | [0.24, 0.53] |

Analytic `4R²(1−R²)²/n` agrees (0.076 / 0.085). The logs corroborate it: models 9 and 13 are the *same recipe and hyperparameters* with only the site set changed (79 → 81), and `between_r2` moved −0.036 while `loso_r2` moved −0.007.

Of every change logged, only one clears ~1 SD: raw crop counts → normalized shares, which took CLF `between_rate_r2` from 0.2375 to 0.4069. **Any proposal that hopes to move this metric detectably needs to add information, not rearrange it.**

## What to add

Crops and surplus are joined **per year**. The model sees a basin's 2013 corn share when predicting 2013 — a snapshot that carries the year's weather, rotation phase, and price response. What distinguishes one basin from another over the long run is its *characteristic* land use, and how variable that is.

Two derived blocks over the years already in `crops_global` / `surplus_global`, both static per site (and per COMID, so both stay precomputable for the widget):

**1. Long-run mean composition.** Per crop class and per distance bucket, the mean share across all available years:

```
pct_corn_mean_b0, pct_corn_mean_b1, ...          (8 classes x n_buckets)
surplus_kgha_norm_mean_b0, ...                   (1 x n_buckets)
```

A basin that is 60% corn every year and one that averages 60% while swinging 40–80% are the same under the current features in a mean year, and different in what they do to a river.

**2. Rotation intensity.** The year-to-year standard deviation of the same quantities, plus one scalar summarising rotation:

```
pct_corn_sd_b0, ...                              (8 classes x n_buckets)
rotation_index_b0, ...                           (1 x n_buckets)
```

where `rotation_index` is the mean absolute year-over-year change in the corn share — high for a corn/soy rotation, low for continuous corn or permanent pasture. Continuous corn and a corn/soy rotation have very different N loading at the same *mean* corn share, and nothing in the recipe currently separates them.

### Why this is not just another re-slice

The existing `pct_corn_b0` and a re-tuned `*_expT` lambda are both functions of one year's CDL raster over one basin. `pct_corn_sd` and `rotation_index` are functions of the *time series* of that raster, which no current feature touches. Whether that carries between-site signal is exactly the open question.

## Implementation sketch

The aggregation already exists; only the reduction over years is new.

1. `src/features/features.py` — add `agg_crops_longrun(**site_kwargs, edges)` and `agg_surplus_longrun(...)`. Both call the existing per-year aggregators, then reduce over `year` with `mean` / `std`, and compute `rotation_index` as `.diff().abs().mean()` on the corn share. Output is one row (no `year` column), bucket-wide, exactly like the `*_expT` blocks.
2. `src/features/recipes.py` — add a `LONGRUN` block to `_agg_block_compute` alongside `cb_exp` / `sb_exp`, tagged `_mean` / `_sd` via the existing `_tag_values`. It is year-invariant, so it merges as a broadcast constant rather than on `date`.
3. Gate it behind a flag on `_covariate_block` so the ablation is a one-line change, not a fork.

Cost: ~18 columns per bucket for block 1+2 combined, all static. For the browser they are additional columns in the per-COMID row that is already being shipped — no new per-day storage.

## Test protocol

The point is a between-site effect, so the comparison has to be powered for one.

- **Ablate on the full recipes**, not light: more baseline columns, and no confound from the light-only static drops.
- **Blocks separately then together**: mean-only, sd+rotation-only, both. If only the mean block moves anything, that is a weaker and more suspicious result than the rotation block moving it — the mean is nearly collinear with the current per-year share.
- **Hold the site set fixed** across arms. Models 9 vs 13 show two sites are worth 0.036 of `between_r2`; an arm that silently changes `n_sites` is uninterpretable.
- **Report `between_r2`, `between_rate_r2`, `macro_r2`, and `lofo_r2`/`lofo_auc` together.** A between-site gain paid for by pooled skill is a real trade to weigh, not a failure — but it has to be visible.
- **Treat anything under ~0.08 as no result.** With four arms this is worth repeating across several seeds and reporting the spread rather than a point estimate; the metric's sampling SD is larger than any effect seen in the logs so far bar one.

## Expected outcome

Honestly: uncertain, and the noise floor may swallow it. The structural argument is sound — the recipe currently describes a basin by geometry and one year of land cover, and long-run composition is genuinely new information — but the same argument applies more strongly to variables that are not in the repo at all.

If this comes back flat, the next move is not another feature derived from CDL and surplus. It is **tile-drainage density and soil permeability** (SSURGO), which is what actually sets a basin's baseline nitrate level and which nothing in the current feature set proxies. That is a data-acquisition project rather than a recipe change, and worth scoping only once the cheap option here has been ruled out.
