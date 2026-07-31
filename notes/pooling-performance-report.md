# Pooling performance: the 2026-07-27 optimizations

**Record document. Written because the before/after numbers are no longer reproducible.**
Date written: 2026-07-28, covering work completed 2026-07-27. Scope: the wide-pool build path — `src/features/transformers.py`, `src/data/d8.py`, `src/data/access.py`, and the new `src/build/_make_site_grids.py`.

The mechanisms are documented in the code (see §4). What was missing, and what this file exists for, is the **measurements**: the work is uncommitted, so no commit message holds them, and the pre-optimization implementations no longer exist in the working tree. Per CLAUDE.md's own warning, HEAD is not a substitute baseline — it still carries the legacy 500 m Iowa PNG rather than the 15 arc-sec HydroSHEDS raster, so reverting to re-measure would silently swap the algorithm underneath the benchmark.

---

## 1. Bottom line

**The wide pool build went from ~8.7 hours to ~19 minutes**, roughly a 27x end-to-end reduction, and that single change is what made the feature-manifest screen and the tuning work practical at all. Everything else here is secondary by comparison.

**Essentially all of that time was Python call overhead, not arithmetic.** The per-group weighted aggregators were being invoked once per (group, column): for one site's weather that is 15,657 groups x 17 columns = **266,000 calls**. Because both reductions are linear in the values, scaling each row by its cell weight once turns the whole thing into a single Cython groupby sum.

**The remaining three wins are caching or vectorization of things that were being recomputed**: flow accumulation now persists to disk (45.3s -> 0.04s), the upstream BFS expands whole levels at once (19.7s -> 1.2s), and site grids are built once as artifacts rather than per process (123 grids: 333s -> 60s).

**Correctness was verified, not assumed.** The vectorized aggregation was checked against the per-group implementation to float32 epsilon before the old path was removed.

---

## 2. What changed, and what each bought

| change | file | before | after | factor |
|---|---|---|---|---|
| Vectorized weighted groupby | `features/transformers.py` | ~8.7 h (pool) | ~19 min | ~27x end-to-end; 14–640x per aggregation |
| Flow accumulation cached to disk | `data/d8.py` | 45.3 s | 0.04 s | ~1100x |
| BFS level-set vectorization | `data/d8.py` | 19.7 s | 1.2 s | ~16x |
| Site grids as build artifacts | `build/_make_site_grids.py` | 333 s / 123 grids | 60 s | ~5.5x |

**Vectorized weighted groupby** (`_weighted_group_agg`). `wsum` is `Sum(v*w)` and `wmean` is `Sum(v*w)/Sum(w)`; both are linear in `v`, so multiplying each row by its weight up front reduces them to a plain groupby sum. Aggregators opt in by carrying `.agg_kind` and `.cell_weights` attributes (`_func.cell_weights, _func.agg_kind = area_ha, "wmean"`), and `agg_grid_to_buckets` routes any tagged callable through the vectorized path while everything else still goes through `groupby.agg` unchanged. Columns sharing an (agg_kind, weights) pair are batched into one call.

**Flow accumulation** (`_flow_accumulation`, `_accum_cache_file`). A Kahn topological sort over all H*W cells in a Python loop — about 38 s for the 2880x4320 HydroSHEDS raster. It is a pure function of the direction raster, so it is memoized in-process and to `src/data/cache/`, under a filename keyed on the raster's `st_size` and `st_mtime_ns`.

**BFS level-set** (`_build_flow_field`). The upstream frontier is expanded one level at a time with the whole level vectorized, instead of cell by cell.

**Hop-length table** (`_hop_lengths`, `@lru_cache(maxsize=1)`). Geodesic hop length depends only on the cell's row and the direction, not its longitude, so an H x 8 table replaces a per-cell geodesic solve.

**Site grids as artifacts** (`_make_site_grids.py`, `access.get_grid`). A site's grid is a pure function of its basin polygon, sensor location and `grid_global`, but was rebuilt from scratch in every process. It is now written to `processed/grids/{uid}_grid.parquet` and read back disk-first, with staleness by mtime against the basin parquet and `grid_global`.

---

## 3. Invariants these depend on — what a future change must not break

This is the section to read before touching any of it.

- **`_weighted_group_agg` is only valid for reductions LINEAR in the values.** `wsum` and `wmean` qualify; a median, max, or any quantile does not. Tagging a non-linear aggregator with `.agg_kind` would not raise — it would silently return wrong numbers. Tagging is opt-in precisely so this cannot happen by accident.
- **NaN handling is explicit and load-bearing.** A bare groupby sum *skips* NaN, while the per-group `np.dot`/`np.average` it replaced *propagate* it. The vectorized path masks any group containing a NaN back to NaN to preserve the old semantics. Remove that masking and every group with one missing cell changes value.
- **`_hop_lengths` assumes a north-up, axis-aligned transform** (`TRANSFORM.b == TRANSFORM.d == 0`) and raises if that fails. A rotated or skewed raster invalidates the table. It is also not bit-identical to solving per cell — pyproj rounds differently at different absolute longitudes, giving ~1e-9 m per hop and ~4e-7 m accumulated.
- **The BFS level-set assumes the flow relation is a forest.** Every D8 cell drains to exactly one downstream cell, so the upstream walk from an outlet is a tree and no cell is reachable twice within a level. A multi-flow-direction raster would break this.
- **The accumulation cache key is (size, mtime_ns).** A raster edited so as to preserve both — e.g. restored from a backup with timestamps — would serve a stale array.
- **Grid artifacts are an optimization, never a prerequisite.** `get_grid` falls back to computing live, so `processed/grids/` and `src/data/cache/` are both safe to delete; deleting only costs time.

---

## 4. Where the mechanisms are documented

In the code, in place, which is the right home for them — this report deliberately does not duplicate the explanations:

`_weighted_group_agg`, `agg_grid_to_buckets` (transformers.py) · `_hop_lengths`, `_build_flow_field`, `_flow_accumulation`, `_accum_cache_file` (d8.py) · the `_make_site_grids.py` module docstring · `get_grid` / `site_grid_is_current` / `build_grid_live` (access.py).

CLAUDE.md's "Build artifacts that back the read path" covers the operational contract — disk-first with live fallback, staleness rules, both caches gitignored and safe to delete.

---

## 5. If pooling gets slow again

Check in this order, cheapest first:

1. **Did a new aggregator skip the tagged path?** An untagged callable silently falls back to per-group `groupby.agg`. `getattr(how, "agg_kind", None)` on the aggregator is the test — this is the failure mode that would cost hours rather than seconds.
2. **Is `src/data/cache/` being invalidated every run?** A raster whose mtime changes per build produces a new cache key each time, so accumulation pays 38 s repeatedly.
3. **Are grid artifacts stale?** `access.site_grid_is_current(uid)` reports it per site; a changed `grid_global` invalidates all of them at once.

---

## 6. Evidence quality

**Measured directly at the time (2026-07-27), and NOT reproducible now:**

- ~8.7 h -> ~19 min pool build; 14–640x per-aggregation speedups; equality with the per-group implementation to float32 epsilon.
- `_flow_accumulation` 45.3 s -> 0.04 s; `_build_flow_field` 19.7 s -> 1.2 s; 123 site grids 333 s -> 60 s.

These were timed before and after the change, in the working tree. The "before" implementations have since been replaced and cannot be re-run without reconstructing them by hand — and HEAD will not serve, because it carries a different D8 raster entirely. **Treat the figures as a record of what was observed, not as a benchmark anyone can repeat today.** Rough magnitudes are what matters; the third significant figure is not meaningful.

**Recorded in the code and therefore re-checkable:**

- The 15,657 x 17 = 266k per-group call count for one site's weather (`_weighted_group_agg` docstring).
- ~38 s for the Kahn sort on the 2880x4320 HydroSHEDS raster (`_flow_accumulation` docstring).
- The `_hop_lengths` deviation bounds, ~1e-9 m per hop and ~4e-7 m accumulated.

**Not measured, and stated as reasoning only:**

- That the ~27x end-to-end pool speedup is attributable *predominantly* to the aggregation change rather than the caching changes. It is strongly implied — the pool build is dominated by aggregation and the other three touch the grid/flow path — but the contributions were never isolated by disabling one at a time.
- The invariants in §3 are read off the implementations and their docstrings, not established by tests. There is no test asserting that a non-linear aggregator tagged with `.agg_kind` produces wrong numbers, so that trap is documented but unguarded.
