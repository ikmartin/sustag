# CONUS data sources — probes

Reproducible evidence behind [`notes/new-data-report_Aug-12.md`](../../../notes/new-data-report_Aug-12.md). Every V2+ number in that report comes from a script here.

```
python experiments/research/conus-data-sources/01_held_but_discarded.py   # no network
python experiments/research/conus-data-sources/02_label_census.py         # USGS OGC API + PAT
python experiments/research/conus-data-sources/03_node_census.py          # USGS OGC API + PAT
```

Scripts 02 and 03 read the USGS PAT from `src/build/api-keys.toml`. Each writes a small summary table to `data/`; raw payloads are not cached here on purpose — at CONUS scope they run to GB and belong in a scratch directory.

## Files

| file | what it establishes |
|---|---|
| `01_held_but_discarded.py` | every covariate already downloaded and dropped at `src/build/water/_paths.py:80`, scored by days overlapping that site's own nitrate record |
| `02_label_census.py` | gate G3 — how many nitrate-labelled sites exist CONUS-wide under `99133 + 99137 + 00630`, with site-type resolution |
| `03_node_census.py` | gate G4 (partial) — the CONUS node population by covariate family, and how many sites a single-pcode query loses |
| `04_resolve_access.py` | the resolved access route for every source that failed in the first pass, and what the Wieczorek catalogue actually holds |
| `05_scope_and_footprint.py` | the region box collection, and disk footprint per source under each storage policy — the gating measurement |
| `06_scoped_census.py` | labels, nodes and grab stations *inside* the declared scope, which is what the pipeline actually collects |
| `07_nwm_chunks.py` | whether a narrow NWM extraction costs less than a broad one — measured, not assumed |
| `sources.csv` | the scorecard: one row per source, existing and candidate, with join key, pin-legality, `models_served`, leakage, licence, friction, tier and verdict |
| `data/*.csv` | outputs of the three scripts |

## Three results worth knowing without reading the report

**`99133` alone returns zero Nebraska sites.** `00630` returns five. USGS codes continuous nitrate under three pcodes and the choice is a per-network convention, so querying one code deletes whole state networks rather than thinning them.

**33,481 discharge nodes against 324 nitrate labels.** The label set is the binding constraint on every model; the node set is not remotely binding on a graph model.

**The largest pcode-fragmentation loss is groundwater, not turbidity** — 1,120 sites missed by a single-code query, against turbidity's 102.

## Reading the tiers

`sources.csv` carries a verification tier (V0–V5) and a verdict (COLLECT / INVESTIGATE / SKIP) as **independent** fields. The tier says how much was checked; it does not imply the verdict. A source can be verified unusable — IWAND reaches V1 and is SKIP because the licence settled it. A source can be under-verified and still recommended, as INVESTIGATE with a named gate.

A `404` in the report's failure ledger means *the URL guessed was wrong*, not that the dataset does not exist.
