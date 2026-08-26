# build3 — the daily-cadence runbook

The build is `build(snapshots, parameters, decisions)`; this file is the operating loop around it. The chapter (`notes/book/data_chapter.md`) is the binding spec; the plan (`notes/plan/build_sites_plan_Aug23.md`) records how each milestone landed.

## The daily acquisition loop

```
python -m src.build3.run acquire            # a1 seed refresh, a2 census, a3 bulk fetch, a4 verify+drift
```

or stage by stage when narrowing is wanted:

1. `a3 --single` — incremental fetch. Cached sensors are untouched; USGS natives get the trailing 120-day revision re-pull (`records.REVISION_DAYS`); new registrations fetch whole. Nothing derived exists here.
2. **Cut the snapshot** — `python -c "from src.build3.acquire import snapshot; snapshot.cut(notes='...')"`. The id names a *state* (`snap-YYYYMMDD[a]`), the manifest covers `raw/` + `acquired/`, HEAD advances.
3. `a4 --single` — reconcile authorities (gaps gate via `decisions/registry_gaps.csv`), verify units at the co-located pairs, diff HEAD against its parent and classify the drift. Benign classes (`added`, `extended`, `revised_in_window`, and `adoption_amnesty` on the first post-adoption diff) pass; conflicts (`revised_out_of_window`, `removed`, `schema_changed`) queue to `decisions/drift.csv` and HALT until answered `accept`/`reject`.
4. Derivation (`d1`..`d5`) and publication (`p1`) run offline against the pinned snapshot whenever wanted — they are pure, and every gate prints exactly which sheet holds its queue.

## The gates, and where their sheets live

Every sheet is in `src/build3/decisions/`; rendered evidence (panels, candidates, auto records) is in `src/data/derived/review/`. Blank decision = not reviewed, never approved. Decisions are keyed on natural keys and survive every rebuild.

| sheet | gates on | vocabulary |
|---|---|---|
| `registry_gaps.csv` | unacknowledged source inconsistencies (a4) | `acknowledged` |
| `drift.csv` | conflicting snapshot drift (a4) | `accept` / `reject` |
| `merge.csv` | merge proposals touching a high-tier record (d3) | `merge` / `distinct` |
| `site_type.csv` | recorded sensors with conflicting type evidence (d3) | the type vocabulary |
| `exclusions.csv` | rows a person opened (d3) | `exclude` / `keep` |
| `basins.csv` | never — advisory override (d4) | a method name |
| `quality.csv` | flagged high-tier series (p1) | `keep` / `exclude` (+ `exclude_spans`, `max_threshold`) |

## Measured facts the loop rests on

- The AOE-scoped census costs **4,297 arm sensors** beyond the seed boxes (~27% over the in-box 15,927) — measured 2026-08-24, the chapter's open cadence question.
- The canary roster (`parameters/canary_roster.csv`) is admitted on every discovery pass — by direct id lookup where the pcode sweep is structurally blind (wells, precip). Every canary must arrive typed and un-skipped; a missing canary is a broken scope filter, not a data gap.
- The first post-adoption diff classifies adopted-file changes as `adoption_amnesty` exactly once; from `snap-20260824` onward every change classifies against real provenance.
