# exp 37 / 37c: a basin-local nitrate pool, and the donor's long-run level

Plan for the next network-feature ablation. Untested — this document is the case for running it, not a claim that it works.

## Bottom line

Experiments 33–36c asked whether a *named donor's daily reading* helps. Every column they added is either a same-day/lagged deviation (mean-zero per site by construction, so it can only move the timing channel) or a coverage scalar. `nbr_level_lag1` was the sole column aimed at the between-site channel, and it is one column out of nine.

exp 37 adds the missing half: **the donor's and the basin's long-run nitrate LEVEL**, plus a basin-local replacement for the statewide pooled scalar. These are between-site features by construction, so judge them on `lofo_between_r2` / `lofo_between_rate_r2` first — the opposite of exp 33, where the report correctly predicted the headline would not move and `within_r2` was the channel to read.

It also carries one rider that has nothing to do with the network: **stream position at the sensor's own reach**, `stream_order` and `stream_level`, present in every arm but `base`. That block is island-legal and donor-free, so it is the only thing in the run whose result transfers to a pin with no gauge in its basin.

Two runs: `37` (reg) and `37c` (clf), both on the **single-donor `flag` encoding** — one neighbour, either direction, picked by closeness in drainage scale. Same shipped base, same rows, same folds as 34/34c, so the `base` arm must score identically to theirs.

## Measured facts this design rests on

All on the current 116-site cohort, `get_basin_graph(immediate_only=True)` (the graph the `flag` variant already walks), re-verified against the containment graph rebuilt by the D8 fix on 2026-07-31 — that rebuild moved the immediate graph from 91 edges to 88 and left the component structure, and therefore every number below, unchanged:

| | |
|---|---|
| containment components over the cohort | 28 — sizes {1:14, 2:4, 4:2, 5:1, 6:3, 11:1, 15:1, 17:1, 20:1} |
| cohort edges on the immediate graph | 88 (was 91 before the D8 fix) |
| sites with at least one other site in their component | 102 / 116 (88%) |
| sites with NO connected site | 14 (12%) — exactly the sites that resolve no donor today |
| pool size for a covered site | 1 to 19 others, median 14 |
| sites whose pool is a single site | 8 (4 components of size 2) |
| within-component site pairs landing in DIFFERENT LOFO groups | 77 of 557 (14%) |

Two consequences. **A basin pool buys depth, not reach** — its coverage is identical to donor coverage, because a site has a pool-mate exactly when it has a donor. And on the 8 sites whose pool is one site, `rest_of_basin` *is* the donor, so `nbr_avg_nitrate` and `rest_of_basin_nitrate_avg` coincide and the anomaly is identically zero. That is accepted rather than worked around (see Degenerate cases).

The last row is the leakage exposure. LOFO groups are conflict components — containment **and** temporal overlap — so they are a refinement of containment components, and 14% of within-basin pairs are split across folds. Those are pairs whose nitrate records do not overlap in time, so no *daily* column can leak across them; only the long-run statics can, and only weakly. Record it in the report; it is not fixable inside this design.

## The new columns

Pool definition: `rest_of_basin` is the unweighted daily mean over every OTHER cohort site in the modelled site's containment component, always excluding the modelled site itself. Unweighted, matching `nitrate_avg_except_this` rather than area-weighting, and computed on the same `_state_daily_wide` daily-max frame every other nitrate feature reads. The donor stays IN the pool, matching `_except_this_mean`'s convention of excluding only the target.

**Causal by default.** The four "static" quantities as originally specified read the future — an all-time mean covers the evaluation window, and a same-calendar-year mean peeks up to twelve months forward. Both are shipped here in a causal form and mirrored by a peeking `_pk` twin that exists only as a ceiling arm, exactly the framing 33's `nbr_all` / `nbr_all_no_lag0` pair uses for lag0.

Note what causality costs: **the causal "static" is not static.** An expanding mean over strictly prior years is a stepwise series, one step per calendar year. The true one-value-per-site version survives only as `_pk`.

Eight causal columns:

| column | definition |
|---|---|
| `rest_of_basin_nitrate_lag1` | pooled basin mean, shifted 1 day |
| `rest_of_basin_nitrate_lag3` | pooled basin mean, shifted 3 days |
| `rest_of_basin_nitrate_avg` | mean of the pooled series over all years strictly before `year(t)` |
| `rest_of_basin_nitrate_avg_yearly` | mean of the pooled series over `year(t) - 1` |
| `nbr_avg_nitrate` | mean of the donor's series over all years strictly before `year(t)` |
| `nbr_avg_nitrate_yearly` | mean of the donor's series over `year(t) - 1` |
| `nbr_avg_nitrate_anom` | mean of (donor − pooled basin mean) over all years strictly before `year(t)` |
| `nbr_avg_nitrate_anom_yearly` | mean of (donor − pooled basin mean) over `year(t) - 1` |

Six peeking twins, `_pk` suffixed: `rest_of_basin_nitrate_avg_pk` and `nbr_avg_nitrate{,_anom}_pk` are true all-time site scalars; the three `*_yearly_pk` are same-calendar-year means. The two lag columns have no twin — they are already causal.

All averages are taken over DAYS of the pooled/donor series, not over per-site means, so a site with a longer record inside the pool weighs more. `nbr_` prefixes the donor columns to match the existing block; the daily `nbr_anom_lag{1,3}` (statewide baseline) and the new `nbr_avg_nitrate_anom` (basin baseline, long-run) are deliberately distinct names.

### Measured coverage of the block as built

Census over all 116 sites, on the block as implemented. "Rows" is the share of pooled rows carrying a value, weighted by each site's row count; "sites" is how many of the 116 get any value at all.

| column | rows | sites |
|---|---|---|
| `rest_of_basin_nitrate_lag{1,3}` | 0.763 | 96 |
| `rest_of_basin_nitrate_avg` | 0.800 | 97 |
| `rest_of_basin_nitrate_avg_yearly` | 0.772 | 97 |
| `nbr_avg_nitrate`, `nbr_avg_nitrate_anom` | 0.679 | 86 |
| `nbr_avg_nitrate_yearly`, `..._anom_yearly` | **0.489** | **71** |
| `rest_of_basin_nitrate_avg_pk` and the two donor `_pk` scalars | 0.865 | 102 |
| `rest_of_basin_nitrate_avg_yearly_pk` | 0.793 | 96 |
| `nbr_avg_nitrate_yearly_pk`, `..._anom_yearly_pk` | 0.517 | 73 |

**The donor's yearly columns are half empty, and it is not a defect.** Donor records frequently do not overlap the target's rows in calendar time at all — `USGS-05420400` is gauged 2021–2026 and its donor `USGS-05418720` ran 2014–2018 — so every same-year and previous-year donor column is NaN there while the expanding mean, which reaches back over all prior years, is fully populated. **16 sites** have a complete expanding donor mean and an empty yearly one. This is the same population as the LOFO-group mismatch above: pairs that fall in different folds are exactly the pairs with no temporal overlap.

**This confounds one arm.** `new_alltime_only` is fitted on ~68% donor coverage and `new_yearly_only` on ~49%, so their gap is part resolution and part coverage, and cannot be read as "does year-to-year resolution pay" on its own. The expanding column degrades gracefully where the yearly one cannot; if `new_alltime_only` wins, check whether it wins on the 71 sites where both are populated before concluding anything about resolution.

**Not in scope for 37.** Re-baselining the *daily* `nbr_anom_lag{1,3}` on the basin pool instead of the statewide mean is the obvious follow-up and a genuinely different question — it changes an existing column rather than adding one, so it belongs in its own experiment against a clean 34 baseline.

## Stream position: two extra statics, in every arm but `base`

NHDPlus carries **two** different quantities here and they run in opposite directions. Both are already on disk in `processed/map_overlays/iowa_flowlines.parquet` — no download, no build step.

| column | convention | over this cohort |
|---|---|---|
| `StreamOrde` | Strahler. **1 = tiny headwater**, rising downstream to a large river | 1:4, 2:11, 3:25, 4:23, 5:30, 6:17, 7:5, 8:1 |
| `StreamLeve` | NHDPlus stream level. **1 = mainstem** draining to the terminus, incrementing once per tributary step | 1:1, 2:23, 3:47, 4:33, 5:12 |

"1 for large rivers down to 5 for tiny streams" describes `StreamLeve`, not Strahler order — the range and the direction both match it and neither matches Strahler. The two are only ρ = −0.51 with each other, so they are not the same feature reversed.

**The measured reason to prefer `StreamLeve`:** Spearman(`StreamOrde`, `log_basin_area`) = **0.940** over the 116-site cohort. `log_basin_area` is already in the base of every arm, so Strahler order is very nearly a monotone recoding of a column the model already has, and a tree needs no help to use it. `StreamLeve` is only −0.53 against the same column — it encodes network *position* (mainstem versus side tributary) rather than size, which is information the recipe genuinely does not carry. `notes/future-work-report.md` R10 predicted exactly this collinearity and listed it as unverified; this measures it.

Both go in, as a two-column `STREAM` block named `stream_order` / `stream_level`. One extra column over a single feature is cheap, and it settles which of the two you meant with a number instead of an argument.

Coverage is **116/116** with no NaN, through the route the existing COMID statics already use: `access._basin_comid(get_basin(site))` → the preferred basin's outlet COMID → the flowlines VAA. Every cohort COMID resolves in the layer. It is also fully deployable at an arbitrary map pin — `_make_basins.snap_comid` already runs on the deploy path, so a virtual site gets a COMID and therefore both columns.

Two consequences for the design:

- **As specified it is unmeasurable.** In every arm but `base`, the block rides inside all thirteen `delta_vs_base` figures and no arm isolates it. So the arm list below adds **`base_stream`** = base + STREAM. Every network arm should then be read against `base_stream`, not against `base`; `base` stays the first mask (and so the printed baseline) because it is the clean no-network reference the rest of the network series is quoted against.
- **It is island-legal.** Neither column needs a donor. If `base_stream` beats `base`, the block belongs in `island_REG` / `island_CLF` regardless of how anything else in this run lands — a free island result out of a network experiment, and the one outcome here that is not conditional on donor coverage.

### A build bug this turned up

`_make_map_overlays.download_flowlines` filters with `if "streamorde" in flowlines.columns`, but pynhd returns the column as `StreamOrde`. The test has never been true, so `pipeline_config [map_overlays].min_stream_order = 3` has **never fired**: the saved layer holds 309,682 features spanning orders 0–9, including 153,303 order-1 reaches, not the `streamorde >= 3` subset that the code, the config comment and `_make_basins.py`'s module docstring all describe.

For this feature that is the lucky outcome — the snapped order is honest rather than floored at 3, which is why the cohort shows orders 1 and 2 at all. But `snap_comid` has been snapping against the full network including order-1 ditches rather than the intended `>= 3` subset, so the basins on disk were delineated under a different rule than the docs claim, and the artifact is ~4x larger than intended. `widget/components/map_common.py` filters with the correct capitalization at read time, so the map display is unaffected. **Do not fix this before 37 runs** — repairing the filter changes snapping, which changes basins, which changes everything.

## Arms

Fifteen, one pool, `MaskedSweep`. Blocks, all classified by exact membership in a `_NEW_COLS` dict mirroring `_NBR_COLS`:

- `STREAM` — `stream_order`, `stream_level` (two). In every arm below except `base`.
- `OLD_COVER` — exp 34's four coverage scalars: `nbr_n_neighbors`, `nbr_area_ratio`, `dist_to_neighbor`, `neighbor_up`
- `OLD_NOLAG0` — exp 34's nine donor columns minus `nbr_anom_lag0` (eight)
- `BD` — `rest_of_basin_nitrate_lag{1,3}` (two)
- `BS` — `rest_of_basin_nitrate_avg{,_yearly}` (two)
- `NS` — `nbr_avg_nitrate{,_yearly}` (two)
- `NA` — `nbr_avg_nitrate_anom{,_yearly}` (two)
- `NEW` = BD + BS + NS + NA (eight); `NEW_STATIC` = BS + NS + NA (six); `NEW_PK` = the six `_pk` twins
- `ALLTIME` = the three non-`_yearly` causal statics; `YEARLY` = the three `_yearly` causal statics

Every arm except `base` carries STREAM on top of what the column says. The `+` is relative to the shipped island base.

| arm | columns | what it answers |
|---|---|---|
| `base` | shipped island recipe, no STREAM | the clean no-network reference; must equal 34/34c's `base` |
| `base_stream` | + STREAM | is stream position worth anything, island-legal and donor-free? The reference every arm below should be read against |
| `nbr_coverage` | + OLD_COVER | knowing a donor exists, nothing read |
| `nbr_all_no_lag0` | + OLD_NOLAG0 | exp 34's best shippable donor set |
| `nbr_coverage_new` | + OLD_COVER + NEW | the new block over the cheapest donor set |
| `nbr_all_no_lag0_new` | + OLD_NOLAG0 + NEW | the full candidate — the recipe this experiment would ship |
| `new_only` | + NEW | is the new block additive to the old one, or does it replace it? |
| `new_static_only` | + NEW_STATIC | no column keyed to a donor's DAILY reading |
| `new_static_no_livefeed` | − `rest_of_state_nitrate_lag{1,3}` − `roll_n_avg_except_this{7,14,60}d` + NEW_STATIC | the strict version: no real-time nitrate telemetry of any kind |
| `basin_lags_add` | + BD | local pool on top of the statewide scalar |
| `basin_lags_swap` | − `rest_of_state_nitrate_lag{1,3}` + BD | local pool INSTEAD of the statewide scalar |
| `new_alltime_only` | + ALLTIME | is one number per site enough? |
| `new_yearly_only` | + YEARLY | does year-to-year resolution pay for its columns? |
| `new_only_pk` | + BD + NEW_PK | ceiling for `new_only` — price of causality |
| `nbr_all_no_lag0_new_pk` | + OLD_NOLAG0 + BD + NEW_PK | ceiling for the shippable candidate |

That is 15 rows including `base`. Against exp 34's eight arms it is ~1.9x the per-task fits, on one pooling pass.

Because `base_stream` and not `base` is the paired reference for the twelve network arms, the two-column STREAM block cancels out of every network comparison — which is the point of including it as its own arm rather than letting it ride.

`new_static_only` needs the caveat spelled out in the report: the base still carries `rest_of_state_nitrate_lag*` and `roll_n_avg_except_this*`, which require live statewide telemetry, so that arm is "no DONOR-specific daily read", not "no live feed". `new_static_no_livefeed` is the arm that actually answers the deployment question, and it is the only arm that removes base columns — read its delta against `base` with that in mind, not against the other arms.

## Degenerate cases and NaN policy

- **14 singleton sites**: every new column NaN, matching the existing convention. Emit them anyway — dropping them leaves the pool ragged, and `_neighbor_block`'s docstring records what happens when a slice loses the columns entirely (every arm silently collapses to `base` and logs a 0.0000 delta with no error).
- **8 sites whose pool is one site**: `rest_of_basin_nitrate_avg == nbr_avg_nitrate` and the anomaly is identically 0. Kept as-is. Excluding the donor from its own baseline would trade a constant for a NaN on exactly those sites, and the existing statewide baseline already declines to exclude the donor for the same reason.
- **Early years**: `*_avg` (expanding over prior years) and `*_yearly` (prior year) are NaN over a site's first year of pooled record. No fill.
- **STREAM has no missing values on this cohort** (116/116 resolve), but `get_comid_attributes`' convention still applies to a pin whose COMID falls outside the layer: NaN, never a raise.
- Hard-fail in `masks(pool)` on any `_NEW_COLS` name absent from the built pool, same guard and same reason as `_neighbor_ablation`.

## Implementation sketch

Everything lives in `experiments/specific-small-experiments/exps.py`. Nothing moves into `src/features/` until it earns its place — the module docstring's rule, and the same path `neighbor_nitrate` took after 33–35c.

1. `_component_of(graph)` → `{site: frozenset(component)}` over the cohort, memoized whole-cohort like `_neighbor_context`, on `immediate_only=True`.
2. `_basin_pool_series(site_uid, wide, comp)` → the daily pooled mean excluding the site. Use the same O(1) subtract-your-own-column trick `_except_this_mean` uses, restricted to the component's columns: per component compute the row sum and non-null count once, then subtract the site's own column. Per-site recomputation over 28 components is affordable but pointless.
3. `_basin_pool_block(site_uid, ...)` → `(date-keyed frame, static scalars)`, matching `_neighbor_block`'s return shape. The three all-time `_pk` scalars are the only true statics and go in the dict; everything else is date-keyed.
4. `_basin_pool_ablation(task)` → `MaskedSweep`, structured exactly like `_neighbor_ablation`: `shipped = recipe_maker(task, window=1)`, then `_neighbor_block(..., variant="flag", select="area")`, then `_basin_pool_block(...)`, merged on the shipped frame's spine so rows are unchanged and the pool stays row-identical to 34's.
5. `_stream_position(comid)` → `{"stream_order": ..., "stream_level": ...}`, broadcast like any other static. Behind an `lru_cache(1)` read of the flowlines parquet with `columns=["COMID", "StreamOrde", "StreamLeve"]` — pyarrow prunes columns, so the three-column read off the 276 MB file is cheap and the 100+ VAA columns never materialize. Resolve the COMID with `access._basin_comid(get_basin(site))`, the same route `get_comid_attributes` already takes, and return NaN for an unresolvable one rather than raising.
6. Register `experiment(id="37", task="reg", kind="masked")` and `id="37c", task="clf"` on one build fn, the 33/33c pattern.

If STREAM survives, promoting it is small: `StreamOrde` / `StreamLeve` join `comid_attrs.parquet` and `access._COMID_ATTR_COLS`, which is the one-row-per-attribute path `pipeline_config.toml` documents. It does not need `ATTR_SOURCES` — unlike the `bfi` / `contact` / `tiles92` attributes, the values are already in a layer the build downloads.

The expanding and prior-year means are per-site groupbys on `year`: `s.groupby(year).mean()`, then `.shift(1)` for the yearly column and `.expanding().mean().shift(1)` for the avg column, mapped back onto the daily index by year. Compute them on the pooled and donor series *before* the merge, so the column is a plain date-keyed series like every other.

## Test protocol

```
python run_exp.py --full 34 34c 37 37c --extra
```

Run 34/34c in the same invocation. Their `base` arm and 37's are the same columns, rows, folds and XGB config, so the two must agree to the last digit — a free correctness check on the new block's merge, and the thing that makes 37's donor arms readable against 34's. Verified on a 20-site smoke: every score column of 37c's `base` matched 34c's exactly.

**Running 34/34c alongside is now mandatory, not a nicety.** The D8 fix rebuilt the containment graph on 2026-07-31 (91 → 88 immediate edges), so donor selection can differ from what exps 33–36c logged. The component structure and every coverage figure above are unchanged, but the *logged* 33/33c/34 numbers in `notebook.md` predate the rebuild and 37's donor arms are not comparable to them — only to a 34/34c run on the graph as it now stands.

**Do not paste a fulltune winner into `RECIPE_XGB` first.** `run_exp._xgb_config` reads it at import and that is exactly how exp 36 got confounded; it must hold the values 33–35c ran at, or 37 is not comparable to any of them.

Read in this order. Everything below `base_stream` is quoted against `base_stream`, not `base`:

1. `base_stream` vs `base`, on both the headline and the between-channel. Two columns, donor-free, island-legal — the cheapest possible win in the run and the only one that transfers to a pin with no gauge in its basin. Also read the two permutation importances separately: if `stream_order` is dead and `stream_level` is not, that is the ρ = 0.94 collinearity with `log_basin_area` showing up exactly where predicted, and only `stream_level` should be promoted.
2. `lofo_between_r2` (REG) / `lofo_between_rate_r2` (CLF) on `new_only` vs `base_stream`. This is the channel the block is aimed at. REG's between-channel add floor is 0.0258 from the manifest, which is large — an effect under it is not measurable at this cohort size regardless of what the headline does.
3. Headline: `lofo_r2` (REG, floor 0.0085) / `lofo_prauc` (CLF, floor 0.0037), on `nbr_all_no_lag0_new` vs `nbr_all_no_lag0`. This is the shipping decision. Both arms carry STREAM, so it cancels.
4. `new_only` vs `nbr_all_no_lag0` vs `nbr_all_no_lag0_new`: additive, redundant, or a replacement.
5. The `_pk` pairs. If the ceiling is far above the causal arm, the forecast-vs-nowcast decision (G3 in the 07-30 notebook entry) acquires a second, larger price tag than lag0's.
6. `new_alltime_only` vs `new_yearly_only` — **confounded by coverage**, see the census above. Read it only after checking the 71 sites where both columns are populated.
7. `basin_lags_swap` vs `basin_lags_add` vs `base_stream`. A swap that wins on fewer columns is the cheapest result in the run.
8. `new_static_no_livefeed` vs `base`. Not a shipping arm — a siting/deployment number, and the only arm that removes base columns.

## Expected outcome, and the interpretation trap

`nbr_avg_nitrate` and `rest_of_basin_nitrate_avg` are the long-run nitrate level of sites hydrologically connected to the target. For a nested pair that is very close to a target encoding of the modelled site's own level, so the likely result is a **large** `between_r2` move — much larger than anything in the 07-29 ablation table — and `nbr_avg_nitrate` finishing as the single highest-importance feature in the model.

That is real signal and not leakage under LOFO: a covered site's pool-mates are held out with it 86% of the time, and where they are not, it is because their records do not overlap in time. But it makes the arm a statement about **siting**, not about modelling. The number says what a monitored gauge inside the basin is worth; it does not transfer to a pin without one, where all fourteen columns are NaN and the model falls back to the island half. 12% of the *gauged* cohort is uncovered, and the figure for arbitrary map pins is still unmeasured — the same open number the 07-30 entry flags as deciding whether `network` is the primary model or the fallback.

So the headline of the write-up should be the pair, always reported together: the network gain, and the coverage it is conditional on.
