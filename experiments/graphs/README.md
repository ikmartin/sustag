# The basin graph over time

The containment graph the project uses (`processed/aux/basin_containment_graph.parquet`, via `access.get_basin_graph`) is **static**. It carries every edge that has ever existed across the cohort's 2008–2026 record, as though all 116 sites were deployed simultaneously. They never were: the cohort peaks at **71 concurrent sites**, and the graph that is actually valid on a given day is the subgraph induced by the sites reporting that day.

Nothing outside `src/splits/conflict_graph.py` accounts for this. That matters for the network model in particular — a donor feature computed against the static graph can reference a neighbour that had not yet been installed, or had already been retired.

## What this produces

| | |
|---|---|
| `intervals.json` | 159 entries, one per interval over which the active-site set is constant |
| `images/site_graphs/` | 159 PNGs, one per interval, 2400 px wide |
| `images/graph_evolution.mp4` | the sequence at 0.5 s/frame — **pausable and seekable** |
| `images/graph_evolution.gif` | same sequence, for anywhere a video tag won't go |

```
python intervals.py            # -> intervals.json
python render.py               # -> images/site_graphs/*.png + the mp4 and gif
python render.py --limit 6     # smoke test
python render.py --video-only  # rebuild both videos from existing PNGs
```

The MP4 is fed at 2 fps so a frame still holds half a second, then re-timed to 30 fps output — a 2 fps H.264 stream seeks in half-second lurches in most players. `yuv420p` and an even-numbered scale are what make it play outside ffmpeg.

## Deployment, not reporting

A site is introduced on the **first day it reports nitrate** and retired the **day after its last**. Gaps inside that span — NaNs, sensor outages, winter shutdowns — are *not* node deletions. A site that goes quiet for three months and comes back is one continuous deployment, not two.

This is what makes the decomposition finite: the active set changes at most twice per site, bounding the number of distinct graphs at 2 × cohort. The observed count is **159 against a ceiling of 232**, over 2008-03-27 to 2026-07-24. Median interval length is 13 days; the longest is 488.

Intervals with an empty active set are dropped, so consecutive entries need not be contiguous — a gap means nothing was reporting at all.

## The edge set: induce first, reduce second

Edges are the **transitive reduction of the active subgraph**, and the order of those two operations is the whole point.

The stored graph is transitively closed: if A drains into B and B into C, it also carries A→C. Drawn raw that is a hairball in which nesting depth is invisible. But reducing the *full* graph and then filtering to the active sites is wrong in the other direction — it would delete A→C because B lies between them, even in an interval when B is not deployed and A really does flow to C with nothing measured in between.

So the subgraph is induced first and reduced second. **"No basin lies between them" means no *active* basin**, which is the only reading that makes the frame a true picture of that interval.

`immediate_only=True` on `get_basin_graph` is a different thing and is not used here — it keeps each child's single smallest enclosing parent, which drops branches where a basin sits inside two incomparable parents.

## Styling

Mirrors the widget (`widget/colors.py`): IWQIS sites limegreen on darkgreen, USGS `#55a3f7` on `#6b21a8`, rivers `#2563eb`. Flowlines are `widget/assets/iowa_flowlines.geojson` — NHD stream order ≥ 5, pre-simplified — so the figure shows the same network the widget draws. Both river and border overlays are at 50% opacity on white.

The Iowa outline is read from the local `notes/graphics/_us_states.geojson` rather than the Census TIGER URL that `map_common.load_iowa_geojson` fetches, so this runs offline.

Every frame shares one extent, so nothing moves between frames and the growth of the network is the only thing changing. The extent is the cohort bounding box unioned with Iowa's — **8 cohort sites sit outside the state** (`USGS-06478513` in South Dakota, `USGS-05559900`/`USGS-05568705` in Illinois, `USGS-06900050`/`USGS-06901500` in Missouri, `USGS-05457000` in Minnesota, and two on the Big Sioux), and a state-only extent would cut them off.

## Two things the frames make visible

**The cohort is not a growing set.** It grows to 71 (2021-07-21, 42 edges) and then falls back — the last interval has 19 active sites. Any statement of the form "our 116-site cohort" describes a union over 18 years, not a network that ever existed.

**The IWQIS tail is a data artifact, not a decommissioning.** The final interval, 2026-06-09 to 2026-07-24, is **19 sites, all USGS, zero IWQIS** — every IWQIS sensor appears to retire on the same day. It did not: **the IWQIS record is a one-time manual export shared in June 2026 and it does not refresh.** USGS keeps flowing, IWQIS is frozen, so `last_report + 1` retires the entire IWQIS half at the export boundary.

This does not self-heal — it gets worse. The frozen end stays pinned at June while real time advances, so the all-USGS tail lengthens every week. **Any "currently active sites" derived from the water table is wrong, by a margin that grows until IWQIS is re-exported.** The interval decomposition is correct given the data; the data ends early for half the cohort.

## Palette note, if you regenerate the gif

Quantising each GIF frame independently silently wrecks the colours. The 2008–2010 frames contain no IWQIS site at all, so limegreen exists in them only as the ~350-pixel legend swatch; median cut discards something that small, PIL then applies frame 0's palette to the whole animation, and IWQIS renders as grey `(92,107,109)` for all 159 frames while the PNGs are correct. `build_gif` therefore derives ONE palette from the densest frame with a swatch bar of the key colours painted over it, and quantises every frame to that. The MP4 is unaffected — H.264 is full-colour — but the same class of bug is easy to reintroduce by "simplifying" the gif path.

**The three duplicate pairs never co-occur.** `basin_containment_graph.parquet` is not a DAG: it contains 3 two-cycles, from site pairs that are one gauge registered twice (`WQS0032`/`USGS-05483600`, `WQS0081`/`USGS-05481000`, `WQS0024`/`USGS-05451210`). Those pairs are sequential re-registrations with zero overlapping days, so **no interval contains both members and every frame is a DAG**. The temporal view dissolves the cycles for free. `_reduce` still guards against it and breaks any cycle it finds by keeping the edge into the larger basin, rather than failing the render.
