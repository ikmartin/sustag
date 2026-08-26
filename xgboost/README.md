# xgboost — cornbelt surface-nitrate models on the new access layer

Reproduces the project's CLF and REG models against the rebuilt data pipeline, over an expanded cohort. Read `PLAN.md` for the design and what changed from the predecessor; `project-notes.md` collects findings that may inform `data/access` and is for Isaac's review.

**This project never edits `data/access`.** Everything it reads comes through `cohort.py`, which is the single boundary — so "does this need an access-layer change?" is answerable by reading one file.

## Running it

The directory name collides with the `xgboost` library, so **run from inside this directory** (which puts these modules on `sys.path[0]` while the real library resolves from site-packages):

```
cd xgboost
python run_experiment.py                  # both tasks, whole cohort
python run_experiment.py --task reg       # one task
python run_experiment.py --limit 20 --fast  # smoke
python run_experiment.py --old-cohort     # restrict to the predecessor's sites
python -m pytest test_geometry.py -q      # the geometry tests
```

Every module appends the repo root to `sys.path` rather than prepending it; prepending would shadow the library with this package.

## The `--scratch` flag, and when it is a lie

`p1` halts wholesale at its quality gate, so until the quality queue is answered there is **no published store** and no cohort resolves. `--scratch` builds a published-shaped store from the assembled records (`derived/water/merged` + the covariate products) and repoints `data.access` at it for one process.

**It applies no quality masks.** Records include days a reviewer may later withhold, the publication partition is never computed, and nothing is verified. Numbers produced under `--scratch` establish that the chain runs and roughly where it lands — they are not model results. Re-run everything against the real store once `p1` completes.

## The cohort

Three conditions, in `config.py`, applied in `cohort.sites()`:

1. inside the **cornbelt seed box**, read live from `data/build/parameters/scope.toml` so it cannot drift from the pipeline's own geography
2. site type in **stream / ditch / canal** — excluding wells (different water), lakes and tidal (no contributing basin), and the sub-catchment practice types whose basin is a field footprint
3. at least **365 days** of published nitrate

Currently **188 published records** (104 USGS, 79 IWQIS, 5 MPCA), median 1,441 nitrate days, ~337k sensor-days. The predecessor trained on 116 sites and 175,973 rows.

## What replaced the distance buckets

The old stack bucketed raster **cells** by **euclidean** distance to the sensor. This one buckets **catchments** by **flow distance** along the channel network — `pathlength(reach) − pathlength(outlet)` from the NHDPlus value-added attributes — so a ring is a set of reaches at a given channel distance rather than a set of cells at a given crow-flight distance. Bucket columns are suffixed `_f0/_f1/_f2` (flow), never `_b`, so a column name can never be mistaken for the old geometry.

Travel-time lags come from the same place: NHDPlus's own modeled path time where present, `flow_dist / velocity` where absent. On Old Mans Creek the three rings lag 0, 1 and 2 days.

**Consequence:** feature values are not comparable to the predecessor's, and no ablation result transfers. Every curation constant inherited from the old `recipes.py` is marked INHERITED and is a hypothesis to re-measure, not a settled finding.

## Layout

| file | what |
|---|---|
| `config.py` | cohort constants, bucket edges, target geometry, CV parameters |
| `cohort.py` | **the access boundary** — sites, records, basins, covariates, weather, the pooled cross-site matrix |
| `basins.py` | `accumulate ∘ snap` delineation with the pipeline's guards, cached per SNR code |
| `transforms.py` | flow bucketing, weighted aggregation, lags, frame plumbing |
| `features.py` | targets, basin aggregates, statics, cross-site, calendar |
| `recipes.py` | per-task curated lists and assembly |
| `cook.py` | pooled CV: LOSO / LOFO / basin families, the predecessor's metric names preserved |
| `run_experiment.py` | driver; prints the comparison against the predecessor's numbers |
| `scratch_store.py` | the development store described above |
| `test_geometry.py` | geometry and plumbing tests |

## Baselines

The predecessor's best runs, 2026-07-31, on 116 sites / 175,973 rows (`experiments/feature-manifest/log.json`):

| task | metric | value |
|---|---|---|
| REG | `lofo_r2` | 0.4818 |
| CLF | `lofo_prauc` | 0.7223 |
| CLF | `lofo_auc` | 0.8783 |

These are **not** like-for-like targets: the cohort is larger and geographically wider, and the features differ by construction. `--old-cohort` restricts to the predecessor's site list, which is the only apples-to-apples read available, and even it differs in features.
