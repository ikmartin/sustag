# The D8 pour-point snapping bug

## Bottom line

`d8._snap_outlet` decided which raster cell a sensor "really" sits on by a rule that is relative to whatever happens to be nearby. Where a small basin's sensor sat within ~5 km of a much larger river, the rule put the outlet on the **wrong river**, and every distance measured from that sensor was then measured to the wrong place.

It affected **28 of 116 cohort sites (24.1%)**, biased systematically toward small basins, and it was silent: a wrong outlet still produces a complete, plausible-looking flow field. The worst case put a 35 km² basin's outlet on a 225,000 km² drainage — a factor of **6,439**.

The fix constrains snapping with the site's known basin area. 115 of 116 sites now snap within 3× of their true drainage, up from 88.

## The bug

`_snap_outlet` searched a 10-cell (~5 km) window around the sensor pixel and accepted any cell carrying at least half the **window's own maximum** flow accumulation, taking the nearest survivor:

```python
sub = acc[r0:r1, c0:c1]
rs, cs = np.where(sub >= _OUTLET_ACC_FRAC * sub.max())   # _OUTLET_ACC_FRAC = 0.5
j = int(np.argmin((cc - col) ** 2 + (rr - row) ** 2))    # nearest wins
```

The threshold is a fraction of `sub.max()`, so it means "is this cell a main stem *compared to its neighbours*". That is unbiased only when the window contains one stream. When the window straddles two — a small tributary and a big river — the big river sets `sub.max()`, every cell of the true stream falls under 50% of it, and the only survivors are on the wrong river. `argmin` then dutifully picks the nearest of those.

This is why the victims are systematically the **small basins**: the smaller the basin, the lower its own stream's accumulation, and the easier it is for any nearby larger river to dominate the window.

## Why it hid

Three reasons, all worth noting because each one independently would have delayed detection:

1. **A wrong outlet still works.** `_build_flow_field` happily builds a complete field from any cell. Nothing downstream can tell that the field belongs to a different river.
2. **Accumulation was only ever used relatively.** The comparison `sub >= 0.5 * sub.max()` is unitless, so the code never needed to know what an accumulation count meant in ground area — and therefore never had a way to notice that the answer implied a drainage 6,439× too large.
3. **Only some failures announced themselves.** Where the child's pixel also fell outside the wrong field, the distance came back NaN and was visible. Where the child happened to lie inside the wrong river's drainage, it sampled a **finite but wrong** distance. Of the 9 mis-snapped parents in the containment graph, only 5 produced NaNs; the other 4 produced numbers.

## Blast radius, measured

**`dist_to_sensor`, and therefore two base features in every recipe.** `site_view.build_site_view` uses the same snap to compute per-grid-cell flow distance, which aggregates into `mean_dist_to_sensor` and `max_dist_to_sensor` — members of `_BASE_BLOCKS["dist"]`, present in *every* arm of *every* experiment, island and network alike. 28 of 116 sites had a corrupted field:

| site | basin | snapped drainage |
|---|---|---|
| WQS0026 | 35 km² | 6,439× |
| WQS0025 | 107 km² | 2,077× |
| WQS0111 | 88 km² | 2,069× |
| USGS-05464475 | 45 km² | 374× |
| … | | 28 sites over 3× |

**`flow_dist_m` in `basin_containment_graph.parquet`.** 16 of 236 edges were unroutable, concentrated in 5 parents, 4 of which had *every* child fail:

```
WQS0084         5/5 children unroutable
USGS-05418720   4/4
WQS0110         3/3
WQS0002         3/3
WQS0009         1/2
```

This column feeds `features._edge_dist_m` (donor selection for the `network_*` recipes) and `splits/conflict_graph.lodo_neighbours(prefer="flow")` (CV fold composition and the leakage audit).

**The triangle inequality.** Because the full containment graph is transitively closed, an edge A→C coexists with the chain A→B→C. Comparing them, **32 of 149 chains (21.5%) had `d(A,C) < d(A,B) + d(B,C)`** — a shorter "direct" distance than the path it contains. Two causes:

- **10 from the euclidean fallback.** `_edge_dist_m` falls back to straight-line distance when D8 has none, and straight-line is ≤ flow distance by construction. Worst: `USGS-05418110 → WQS0110`, 73.46 km direct versus 100.31 km along the flow path (0.696×).
- **22 from mixing independently-rooted fields.** `d(A,B)` is read from B's field and `d(A,C)` from C's, each rooted at its own snapped outlet, so they are not exactly additive. Median 0.992 — noise-scale, but real, and it is a *separate* issue from the snapping bug.

This matters because ranking donors by distance is one of the two axes of the planned neighbour-count experiment; half that grid would have been unreadable.

## The fix

Give the snapper the one thing that makes the test absolute rather than relative: **the site's known basin area**.

A candidate outlet is accepted only when its accumulation-implied drainage (`acc × cell_area`) falls inside `_OUTLET_AREA_BAND = (0.33, 3.0)` of the known area; nearest survivor wins as before. If nothing qualifies, the band is widened once by `_OUTLET_WIDEN = 3.0` with a warning; if still nothing, it **raises** rather than returning a wrong outlet. Both callers turn that into a NaN distance, which is the honest answer when the D8 network and the basin polygon genuinely disagree.

The band is calibrated, not guessed. Across the 116 sites, the 88 that snapped correctly span **0.96×–2.51×** with a median of **1.01×**, while every mis-snap is **>4×** — a clean gap with no overlap. Accumulation-implied area tracks polygon area closely when snapping works, which is what makes it a usable acceptance test.

Supporting changes, each of which is load-bearing:

- **`d8.cell_area_m2()`** — per-row cell area in m², read out of the existing `_hop_lengths` table (the N and W hops out of a cell centre *are* its height and width) rather than hardcoding a constant. Area varies 1.21× across the raster's latitude span. Verified against an independent geodesic polygon computation: 0.1589 km² at row 1600, exact agreement.
- **`_FLOW_FIELD_CACHE` key now includes the expected area.** Without this the fix silently does nothing during a build: `_make_aux` materialises every parent's basin area *before* its flow loop, and computing that area can itself build a grid through `flow_distance_field_ll` with no area — populating the cache with an un-refined field that the later area-aware call would then get back.
- **The area must be passed in already materialised, never looked up inside the snapper.** `access.get_basin_area(uid)` is *defined as* a sum over `get_grid(uid)`, and `get_grid` → `build_grid_live` → `build_site_view` → `flow_distance_field_ll` → `_snap_outlet`. Looking it up from inside would recurse without bound. `_make_aux` passes its precomputed `area[p]`; `site_view` passes the area of the basin polygon it has already reprojected.
- **`site_view` derives the area from the polygon, not from `get_basin_area`** — besides avoiding the recursion, this keeps the real and virtual paths deriving it identically, which is what `selftest.test_virtual_equals_real` compares at `rtol=1e-9`. It also means the deploy path (an arbitrary map pin, which has an NLDI-derived polygon but no site record) gets the fix too.
- **`access.grid_build_env()` now records `d8.py`'s mtime.** It recorded `site_view.py`'s but not `d8.py`'s, so a snapping fix would have left every cached grid reading as "current" and the corrected distances would never have reached a single consumer.

`expected_area_m2=None` reproduces the pre-fix rule **byte-identically** (verified on 40 sites, 40/40 identical outlets), because the deploy path may genuinely have no area and because `experiments/raster-investigation` asserts parity against the old implementation.

## Result, measured

Rebuilt all 116 site grids (`_make_site_grids --force`) and the containment graph (`_make_aux`), then diffed against the pre-fix artifact:

| | before | after |
|---|---|---|
| worst parent snap ratio | 141× | **1.29×** |
| median parent snap ratio | 1.022× | 1.011× |
| parents snapping >3× off | 9 of 54 | **0** |
| cohort sites snapping >3× off | 28 of 116 | **1** (WQS0066, 4.93×) |
| edges with no D8 flow distance | 16 of 236 | **0** |
| triangle-inequality violations | 32 of 149 (21.5%) | **20 of 149 (13.4%)** |
| worst violation | 0.696× | **0.984×** |

Every one of the 16 unroutable edges resolved, which settles a question the diagnosis left open: they were **all** snapping failures, not genuine polygon-versus-raster disagreements. The severe triangle violations went with them — the remaining 20 are the field-mixing residue, now bounded at 1.6% error.

The change is targeted, which is the important regression check. Of 220 edges finite before and after, **14 moved by more than a metre, and every one sits under a parent already identified as mis-snapped**. Zero edges under correctly-snapping parents moved. 16 went NaN→value; none went value→NaN.

`src/selftest.py` passes 7/7, including the three tests most exposed to this change: `get_data` (asserts every `dist_to_sensor` is finite), `virtual_equals_real` (compares the real and virtual construction paths at `rtol=1e-9`, which is why both must derive the expected area from the same source), and `splits` (still 30 conflict components, holdout 92/24, 0 hard leaks — so moving 14 flow distances did not perturb fold composition).

## What this does not fix

- **The 20 remaining field-mixing triangle violations**, bounded at 1.6% error. Distances to different outlets come from different fields, each rooted at its own snapped outlet. **An all-pairs rebuild does not fix this**, contrary to what was assumed while planning: the triangle test is `d(A,C)` vs `d(A,B) + d(B,C)`, and while `d(A,C)` and `d(B,C)` both already come from C's field, `d(A,B)` is rooted at B's — sampling more of C's field never touches it. A real fix would have to define every distance in a chain relative to one outlet (`d(A,B) := field_C[A] - field_C[B]`), which is not globally well-defined because it depends on which C. Not worth 1.6% on a metric used only to rank donors.
- **Genuine polygon-versus-raster disagreement.** Where the basin outline says "contained" and the D8 network says "no path", no amount of snapping repairs it. Those edges should stay NaN.
- **`WQS0066`** is the one site that still only matches the widened band, at 4.93× (down from 171.9×). Its sensor appears to sit where no cell within 5 km has a drainage close to its reported 2,359 km². Worth a look, but it is now a 5× error rather than a 172× one.

## Found while verifying, not fixed

Building a flow field rooted at every one of the 116 sites and sampling all the others gives the exact D8-connected relation, which can be checked against the containment graph. The two delineations **disagree in both directions**: 240 strict D8 pairs versus 236 containment edges, with 9 D8 pairs the graph does not carry and 5 containment edges D8 denies at the exact pixel (those 5 survive only via `sample_field`'s divide-straddle recovery, which is why the build still reports 236/236). Under `sample_field` semantics — what the build actually uses — D8 finds **20 connections the containment graph never recorded, 14 of which join sites in different LOFO families**.

Three separable issues came out of that, none of them fixed here:

1. **`WQS0066`'s outlet is still wrong**, at 4.93× its 2,359 km² basin (down from 171.9×). It is the only site that needed the widened band, and it accounts for **8 of the 14** cross-family connections — anything "draining to" it is suspect while its field is oversized. This is the highest-value loose end.
2. **`sample_field`'s `NODE_SNAP_RADIUS` (4 cells, ~1.8 km) bridges co-located sensors**, producing A→B *and* B→A for three pairs all separated by 0.8–1.9 km. Mutual drainage is hydrologically impossible — one site must be upstream — so these are recovery artifacts, not connections. `USGS-05418720`/`WQS0110` are 0.82 km apart with **identical** basin areas of 4,829 km², i.e. two sensors on one reach.
3. **The containment builder appears to miss genuine long-range containment.** After discounting (1) and (2), what survives is small basins sitting deep inside much larger ones that point-in-polygon did not record: `WQS0073` (35 km²) inside `WQS0041` (9,179 km²) at 96.8 km, and `WQS0104` (32 km²) inside `WQS0040` (2,259 km²) at 92.9 km. If real, the conflict graph is under-linking and LOFO can hold out a site while its hydrological donor stays in training — the same leak class as the 123-vs-116 node mismatch fixed on 2026-07-30, which produced 5 hard leaks.

The fix for (3) belongs in the point-in-polygon step of `_make_aux`, not in bolting D8-derived pairs onto a containment graph — mixing a real correction with the artifacts from (1) and (2) would land all of them in the structure that defines LOFO families.

## Evidence quality

Everything quantified here was **measured directly** against the live artifacts and the live cohort: the per-site snap ratios, the 28-site count, the unroutable-edge tally, the triangle-violation census, the acceptance-band calibration, and the `None`-path parity check. Nothing is from documentation or inference.

Two claims are **structural rather than measured**: that a wrong outlet cannot be detected downstream (it follows from `_build_flow_field` accepting any cell), and that the recursion through `get_basin_area` would be unbounded (traced through the call path, not triggered).

In the *Found while verifying* section, the pair counts, family assignments and separations are measured. The **interpretations are not**: that the mutual pairs are snap-radius artifacts is inferred from mutual drainage being impossible plus their ~1.8 km separation matching `NODE_SNAP_RADIUS`, and that `WQS0073`/`WQS0104` are genuinely missing containment is inferred from basin size and separation, not confirmed against the polygons. Both need checking before either is acted on.

The **downstream model impact is not yet measured.** That 28 sites had a corrupted `dist_to_sensor` is certain; how much any headline score moves once the grids and the containment graph are rebuilt is not, and any run comparison spanning this fix is invalid in both directions.
