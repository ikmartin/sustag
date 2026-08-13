# Areas of interest by flow distance

Where should the pipeline collect data? The answer should follow from a deployment goal, not from a clustering hyperparameter. **`max_dist_from_sensor_km`** carries the goal: the furthest along the river network from a real sensor that we would still place a virtual one. Everything within it is worth paying for.

```
python experiments/research/aoe-boxes/01_compute_aoe.py   # traversal + clustering  (~60 s)
python experiments/research/aoe-boxes/02_make_figure.py   # notes/graphics/aoe_boundaries_conus.png
```

Both read two national files from the scratchpad, fetched once: `nhdplusVAA.parquet` via `pynhd.nhdplus_vaa()` (241 MB, 28 s) and a COMID → (lat, lon, order) table decompressed from the NWM v3.0 retrospective's coordinate arrays (22 MB, 12 s). After that everything is local, and a new distance costs nothing.

## Results

| `max_dist_from_sensor` | regions | single-sensor | reaches | box area | hull area | **fill ratio** |
|---|---|---|---|---|---|---|
| 25 km | 154 | 117 | 30,184 | 0.12M km² | 0.04M | 0.30 |
| 50 km | 117 | 71 | 80,868 | 0.42M km² | 0.16M | 0.37 |
| 100 km | 74 | 38 | 204,501 | 1.29M km² | 0.50M | 0.39 |
| **250 km** | **44** | **22** | 509,250 | 2.97M km² | 1.36M | **0.46** |
| 350 km | 42 | 21 | 662,939 | 4.74M km² | 1.80M | 0.38 |
| 500 km | 42 | 21 | 856,403 | 5.69M km² | 2.24M | 0.39 |

**Bounding boxes are 30–46% full.** More than half the area inside a box is not within deployment reach of any sensor. At 250 km, carrying the reachable hull instead of the rectangle cuts the collection area 2.97M → 1.36M km² — against the measured ~35 GB of scaling footprint per 1.03M km², roughly 55 GB.

**250 km is the knee, and past it you buy area rather than connectivity.** Going 250 → 500 km grows box area by 92% (2.97 → 5.69M km²) while the region count falls only 44 → 42 and the fill ratio *degrades* from 0.46 to 0.39.

**42 regions is the floor.** The count plateaus from 350 km onward because what remains are separate drainage systems — the Sacramento, the Columbia, Florida's canals and the Atlantic seaboard never join the Mississippi at any flow distance. Straight-line clustering cannot see that boundary; flow distance is what makes it visible.

**Hydrologic connectivity is far sparser than proximity suggests.** Straight-line DBSCAN at 150 km produced 12 clusters; flow distance at 250 km produces 44. Two sensors 50 km apart on different rivers share no water, and the flow-distance clustering says so.

**22 sensors have no other sensor within 250 km of flow distance.** For those, a network model has nothing to propagate — they are island-model territory by construction, and that is now a measured count rather than an assumption.

## Why not the existing D8 machinery

`src/data/d8.py` is bounded by `flowdir_15s.tif`, an 18°×12° Midwest window (`-105..-87`, `37..49`), and traverses **upstream only** — no downstream walk exists anywhere in the repo. The clusters this has to cover include California, Florida, the Chesapeake and the Gulf. Hence national NHDPlus VAA for topology and lengths, plus NWM coordinate arrays for geometry, since VAA carries no coordinates and pulling flowline geometry for 2.7M reaches to draw a map would be absurd.

## Verification

- **Sensor accounting.** 232 of 235 sensors snap within 5 km (median 0.365 km) and every one appears in exactly one region at every distance; singletons are counted, never dropped. The three misses are all Florida canals — Vero Beach, Indian Prairie, Three Mile — engineered channels NHDPlus V2 does not carry as flowlines.
- **The stray that motivated this.** Straight-line DBSCAN sent Round Rock (`08105891`) to noise and reported a Texas box that excluded it. It now snaps at 0.803 km to comid 5671165 and is in a region.
- **Monotonicity.** Regions non-increasing (154 → 117 → 74 → 44), reaches and area non-decreasing, across the sweep.
- **Cross-check against D8**, which is independent machinery — raster BFS over HydroSHEDS versus vector reach lengths. Over 10 cohort pairs spanning 12–56 km, **median ratio VAA/D8 = 1.18**: the vector network runs systematically longer. That is the physically expected direction, since NHDPlus is digitised at 1:100k and follows meanders a 463 m raster smooths away; straight mainstem pairs agree to 1% (`USGS-05484500 ← USGS-05484000`, ratio 0.991) while sinuous ones reach 1.45. **This is agreement in ordering and magnitude with a signed, explicable bias — not agreement to within a few percent, which is what the plan predicted and did not get.**

## Parameters

`DISTANCES_KM` · `MIN_STREAM_ORDER` (valid-pin criterion; filters the result, not the traversal, since a low-order reach can be the only path to a high-order one) · `MAX_BOXES` (**selects the largest regions, never merges** — merging California with Washington would span empty Nevada and is strictly worse than dropping either) · `SNAP_MAX_KM`.

## Files

| file | what |
|---|---|
| `aoe.py` | network construction, Dijkstra traversal bounded by distance, snapping, clustering |
| `01_compute_aoe.py` | the sweep; one traversal at the largest distance, smaller ones by filtering |
| `02_make_figure.py` | the figure; carries the parent map's DESIGN NOTES forward |
| `data/aoe_clusters.csv` | one row per region per distance: sensors, reaches, bbox, areas, fill ratio |

## For the pipeline

This is the region step's default. Two stages remain as designed: the boxes here are the liberal bootstrap that bounds the flowline and DEM download, and the reachable set is the precise collection extent. The fill ratios argue for carrying the reachable geometry rather than the rectangle wherever storage is tight.

**One prerequisite before this becomes the pipeline's method.** `_make_map_overlays.download_flowlines` filters on `"streamorde"` while pynhd returns `StreamOrde`, so `min_stream_order = 3` has never fired. `MIN_STREAM_ORDER` here reads stream order from the NWM `order` array and so sidesteps the bug — but the pipeline's own valid-pin test would inherit it.
