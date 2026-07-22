# [2026-07-22]

Had Claude make research report `notes/new-site-report.md`, report on potential for adding new sites.

Another model which could be tried: train on basins. In each basin, choose a site, include the nitrate records of all other sites in that basin as features. For training you move around the basin taking different sites as the target.

Then when you drop a pin, you can utilize the sensor feeds of all other sites connected to that pin's basin. Do this in an adhoc way without making an explicit graph structure to keep it simple.

Next projects:

1. Migrate away from current weather data. Use something national. GridMET works I think, using it for the other weather data already anyways.
2. Expand bounding box to all of united states, or at first to a couple other corn states.
3. Add more sensors, start with the USGS network.

Would like to test idea of a model which incorporates live sensor data across the US (things like nitrate con, flow, water height, discharge, chlorophyl content, oxygen content, pH, etc) and predicts danger level/concentration of nitrate at virtual points. This would be a huge project expansion. Need to run test to see if GNN is worth it or if XGBoost with engineered features can already handle it.

*Finding:* Contained in `experiments/pre-expansion-network-test/`. Only nearest neighbors matter, once you move two nodes over in the graph the correlation between sites completely disappears. The nearest neighbor correlation is strong though. XGBoost with engineered features should be enough.

*Aggregate over whatever set of monitored 1-hop neighbors exists into a fixed set of scalars that are always computable:*

- `nearest_upstream_monitor_anomaly` — the single most-connected neighbor's nitrate anomaly
- `wmean_upstream_monitor_anomaly` — a similarity-weighted mean over all 1-hop monitors (weight by drainage-area overlap / along-network distance — this is exactly the "similarity-weighted donor transfer" the report's R12 described)
- `n_upstream_monitorsr` — the count (0, 1, 2, …)
- `dist_to_nearest_monitor` and frac_basin_area_monitored — how much of the upstream basin is actually observed

Might be worth to have two models, one which uses this neighbor set, another with 0 neighbor features. The other option is to calculate the expected frequency of neighbors in deployment and then randomly mask rows in training to match that frequency. The experiment to run is then "randomly sample valid pins and see how often they have real-sensor neighbors".

# [2026-07-21]

Reworked `_make_basins.py` extensively so that everything comes with a COMID. Turns out the NHDPlus basins are the way to go especially if we will incorporate network effects in the future. Discovered some sites had bad basins, they are now fixed. Discovered one site was a groundwater site with no comparative surface basin, that site was removed (a pity because it was a great site). `Added new-usgs-features.ipynb` notebook to investigate potentially new USGS covariates/sensors to add in an eventual network update, also in there are cells looking around for other nitrate sensors in the US. Discovered Minnesota has its own sensor network, doesn't look good for any other nitrate sensors though. I think lack of data is a problem here. Experiment design was revamped.

Ended by incorporating the new features and running tests. The new static features yielded massive improvements on a medium run ~20 sites for the CLF target, but did basically nothing on the full run on 79 sites. It did improve the REG target noticeablly though, particularly the between_r2 score which measures the model's ability to order sites from least to most nitrate.

Here is a more detailed summary written by Claude for historical purposes.

## Vocabulary (NHDPlus)

- **Reach** — one stream segment between confluences (an NHD flowline), identified by a **COMID**.
- **Catchment** — the *local* land area draining directly into a single reach. Catchments **tile**: they tessellate the landscape 1:1 with reaches, no overlaps, no nesting (the `CAT_` attributes in COMID features).
- **Basin** — the *full upstream* drainage area of a point: the accumulated union of every catchment above it. Basins **nest**: a downstream basin contains all its tributaries' basins (`TOT_` attrs).
- One **COMID** ties them together — a reach + its 1:1 catchment — and is the join key for both the local (`CAT_`) and upstream-accumulated (`TOT_`) attribute tables. **Catchments tile; basins nest.**

## Basin delineation (`_make_basins.py`)

Reworked to three tiers, **every one now carrying a real COMID** (the join key the new static attributes need — this resolved the old "46/85 sites lack a COMID" blocker):

- **basin0** — NWIS authoritative, by site uid. USGS sites only.
- **basin1** — **nearest-flowline snap**: find the closest NHD reach to the sensor, take that COMID's
  upstream basin. Now the **default**.
- **basin2** — containing catchment (`/comid/position`). Demoted to fallback/diagnostic only.

*Why the snap.* `/comid/position` returns the catchment polygon *containing* the point, which near confluences grabs the wrong reach — e.g. an order-2 tributary 510 m away instead of the mainstem 4 m away. Snapping to the nearest flowline fixes that and stamps a correct COMID on every basin. Deploy pins use pure-nearest (no reported area to match against).

## Groundwater discovery

A few "sites" turned out to be groundwater/karst, not surface water: a USGS well (`USGS-415959091441301`, site_type = Well) and IWQIS springs (e.g. Big Spring `WQS0031`). They have **no surface drainage basin**, so they can't be delineated or featured like a stream site. They're now skipped at fetch (`usgs.py`) and filtered out via an `is_groundwater` flag — no wasted pulls, no nonsensical basins.

## New covariates (static, per-basin — the between-site-gap lever)

Joined by the basin's outlet COMID and folded into `SiteData` (`comid_attrs`, `agtile`) by
`access.get_data` / `build_virtual_site_data`, then read by `features._site_static`:

- **COMID-keyed** (ScienceBase): `tot_tiles92` (tile-drainage %), `tot_bfi` (base-flow index %),
  `tot_contact` (subsurface contact time, days). `CAT_` variants carried for diagnostics.
- **AgTile-US** (30 m tile-drainage raster) → `tile_frac_basin` (tile area / basin area) and
  `tile_frac_ag` (tile / CDL-2017 cultivated cropland).

*AgTile caveats found today:* the raster is plain binary (1 = tile-drained), with **no** cropland
mask — so the ag denominator comes from CDL, not the raster. Also fixed a CRS/degenerate-edge-cell
bug in `_make_agtile` that had corrupted coverage (2,502 → 19,043 cells).

## Experiments pipeline (`run_exp.py`)

New single-log experiment engine. An experiment = a **QUESTION** + a dict of named recipes compared
on the same sites/folds; results append to one git-tracked `logs/experiments.jsonl` (rendered to
`experiments.md`), formatted **Question → Winner → Results**.

- Declare saved experiments with `@experiment(id, question, task)` in `exps.py` (a registry populated
  at import). Each run is a **single task** — regression OR classification, never both.
- Added `run_experiment(question, recipes, task, ...)` for **one-off** experiments straight from a
  notebook — no decorator, no registry, no CLI. Always logs + renders, returns the metrics frame.

# [2026-07-20]

Migrated from the old Erdos git repo to this new one. Ran a rather expensive Claude research experiment to investigate new features. It did turn up some useful stuff, it looks like more static covariates can be derived for free from the basin data using stuff keyed to "COMID"s, including tile drainage attributes.