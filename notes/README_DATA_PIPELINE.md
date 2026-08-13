# Data pipeline — build

Covers the build path only. The read/access layer is documented separately.

## Introduction

*Placeholder.*

## Pipeline Structure

```
0  SCOPE                     declared (VPUs / states), not derived
   │
   ├─ 1  SITE DISCOVERY ─────────────────┐   parallel by source
   │     nitrate: USGS 99133 + 99137 + 00630(iv) · IWQIS · MPCA · other state programs
   │     other modality: Q · stage · temp · spec cond · turbidity · DO · pH
   │
   ├─ 2  RECORD DOWNLOAD                     parallel by site
   │
   └─ 3  FLOWLINES  (NHDPlus V2 + VAA over SCOPE)      independent of 1–2
   │
4  MERGE / IDENTITY                needs 1, 2, 3
   │
5  COMID SNAP                      needs 3, 4
   │
6  BASINS, per group               needs 5
   │
   ├────────────────────┬─────────────────────────┬──────────────────┐
   │                    │                         │                  │
7  COVARIATE BBOX    8  COMID ATTRIBUTES       9  DEM / D8        8 and 9 run
   │                    StreamCat · VAA ·         HydroSHEDS 15"   parallel to
10 GRID_GLOBAL          NHD waterbody · NID ·                      7 → 10 → 11
   │                    drainage districts
11 RASTER CLIP          │                         │
   weather | crops |    │                         │
   surplus | agtile     │                         │
   └────────────────────┴─────────────────────────┴──────────────────┘
   │
   ├─ 12  SITE GRIDS          needs 6, 10
   ├─ 13  FLOW DIST + GRAPH   needs 6, 9
   └─ 14  SITE TYPE           needs 1, 8
```

### Stages

| # | stage | needs | produces |
|---|---|---|---|
| 0 | Scope | — | the extent every download is bounded by |
| 1 | Site discovery | 0 | one row per registration: source id, coords, source-reported drainage area, modality, record span |
| 2 | Record download | 1 | nitrate and other-modality series |
| 3 | Flowlines | 0 | NHDPlus V2 flowlines and value-added attributes |
| 4 | Merge / identity | 1, 2, 3 | `physical_gauge_group`, `is_group_canonical`, `merge_rule`, merge evidence, `merge_seam_date` |
| 5 | COMID snap | 3, 4 | `comid`, `snap_dist_m`, `stream_order`, `StreamLeve` |
| 6 | Basins | 5 | basin polygon, area, upstream COMID set — one per group |
| 7 | Covariate bbox | 6 | the raster extent |
| 8 | COMID attributes | 6 | StreamCat, VAA, NHD waterbody, NID, drainage districts |
| 9 | DEM / D8 | 0 | flow direction and accumulation |
| 10 | `grid_global` | 7 | covariate cells, `global_node_id`, provenance stamp |
| 11 | Raster clip | 10 | weather, crops, surplus, agtile — keyed on `global_node_id` |
| 12 | Site grids | 6, 10 | per-site cell fractions |
| 13 | Flow distance + graph | 6, 9 | flow distances, containment graph |
| 14 | Site type | 1, 8 | `site_type`, `site_type_source` |

### Invariants

**Scope is declared; only the covariate bbox is derived.** Flowlines need an extent, basins need flowlines, and the covariate extent needs basins — so exactly one of the two extents may be computed. Scope is chosen; the bbox follows from the basins.

**No stage removes a site.** Every stage widens the site table with columns. Cohort selection is a query over that table, evaluated at modeling time, never baked into a build artifact.

**Build for candidates, not survivors.** A site's basin and coverage are the evidence used to judge it. Skip the build and the site becomes permanently unjudgeable.

**Merge runs after records and before basins.** Its evidence — coordinates, record overlap and agreement, COMID — exists only after stage 2; its output is the key stage 6 builds against. It writes group membership as columns and leaves every original registration in place.

**Everything joins through the grid.** `global_node_id` is created once at stage 10 and carries a provenance stamp; weather, crops, surplus and agtile all clip to that grid and refuse to join on a stamp mismatch.

**Basins are the union of upstream NHDPlus catchments on the COMID network** — the same accumulation StreamCat and the VAA use, so basin extent and COMID-keyed attributes agree by construction.
