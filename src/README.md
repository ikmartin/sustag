# Documentation for the `src` module

This module contains the major components of the codebase for this project. Here is its structure.

- `build`: scripts for building the data from source (or re-downloading it). On a fresh clone you usually don't run this — just extract the downloaded weather layer into `src/data/interim/` (see [Build](#build) below). `make_data.py` is only needed for a from-source rebuild.
- `data/`: submodule for accessing the data. The actual data files also live in this directory.
- `eval/`: submodule for training and evaluating models. Consists mostly of one script, `cook.py`
- `features/`: submodule for generating features from the data. The feature sets and the methods for generating them are defined here.
- `models/`: train and tune full models. Models persist in `models/models/` for later loading.
- `splits/`: a single script, `conflict_graph.py`, lives here. Used for handling the train/test splits, made difficult by data leakage made possible by the geographical nature of the data.

## Build

Fast path — a fresh clone already contains everything except the weather layer:

1. Download the weather bundle from [the download link](https://utexas.box.com/s/xvoktu11q2s06c0rlpz39wz7itbn5sur).
2. Extract the `weather_global_*.parquet` files into `src/data/interim/` (~4.6 GB).

That's it — the project is set up; there is no required build step. (`weather_global` is a *built* table, so it goes in `interim/`, not `raw/` — dropping it in `raw/weather/` will not be picked up.)

Optionally run `python -m src.build.make_data` to confirm all data is present; it skips anything already built. To instead regenerate everything from the original raw sources, see **Catastrophic rebuild** below.

### Catastrophic rebuild

If the raw data download link doesn't work, `make_data.py` will not succeed. These are instructions for rebuilding weather, crop and surplus data in this event.

#### Rebuild of weather data

This happens automatically when `make_data.py` runs — it will just be slow, since `_make_weather.py` re-downloads the gridMET + IEM weather for every year (2008–2026) via API and re-aggregates it onto the IEM grid, writing `src/data/interim/weather_global_{year}.parquet`. No manual download is needed.

#### Rebuild of crop data

Crop data must be redownloaded from the USDA CDL. There is a provided utility for this, use

```python
python -m src.build.util.clip_crops --download
python -m src.build._make_crops --force 
```

This will take a while. The second command ensures stale files are overwritten. Once it completes, `make_data.py` should run.

#### Rebuild of surplus data

Navigate to [gTREND-Nitrogen - Long-term nitrogen mass balance data for the contiguous United States (1930-2017)](https://www.nature.com/articles/s41597-026-06576-x) and navigate to the "Data Records" section. Follow the [figshare link](https://springernature.figshare.com/articles/dataset/gTREND-Nitrogen_-_Long-term_nitrogen_mass_balance_data_for_the_contiguous_United_States_1930-2017_/29897375) and download the `Surplus.zip`. It should consist of tif files named `Surplus_N_{year}.tif`. Take the files corresponding to 2000-2017 and place them in `src/data/raw/surplus/tif/` (eg `src/data/raw/surplus/tif/Surplus_N_2000.tif`, `src/data/raw/surplus/tif/Surplus_N_2001.tif`, etc.).

Once those files are in place, run

```python
python -m src.build.util.build_source
```

to produce the necessary parquets.

## Data

`src.data.access` is the read layer — everything is keyed by a `site_uid` (USGS-* or WQS*). `get_data` returns the whole `SiteData` bundle; the `get_*` accessors are cheap single-table reads.

### Examples

```python
from src.data.access import get_site_ids, get_data, get_water, get_weather, aggregate_by_interval

sites = get_site_ids()                 # all site UIDs
uid = sites[0]

data = get_data(uid)                   # SiteData: .water .weather .crops .surplus .grid .basin .basin_area ...
water = get_water(uid)                 # per-timestamp nitrate_con / discharge / stage / temp
weather = get_weather(uid)             # per-cell daily gridMET + IEM precip (precip_in_1d, max_temp, vpd, ...)

# resample one water series to a calendar interval:
weekly = aggregate_by_interval(site_uid=uid, value_col="nitrate_con", interval="1W", agg_func="mean")
```

## Eval

`src.eval.cook` is the cross-site CV harness. A *recipe* is a function `site_uid -> DataFrame` (feature columns + a target column). `compare_many` scores recipes under Leave-One-Site-Out (`loso_*`, optimistic) and Leave-One-Family-Out (`lofo_*`, honest transfer) CV.

### Examples

```python
from src.eval.cook import compare_many, FAST_XGB
from src.features.recipes import recipe_CLF

scores = compare_many({"clf": recipe_CLF}, sites=None, target_col="violation", task="clf", **FAST_XGB)
scores.T                                    # lofo_auc, lofo_prauc_lift, lofo_recall_at_far, brier, ...
scores.attrs["importance"]["clf"].head()    # gain-ranked feature importances (free)
```

Also: `cook_one` (single-site chronological CV), `cook_fleet` / `compare_fleet` (per-site fleet summaries), and `fit_full` (train one deployable model on all rows).

## Features

`src.features.recipes` holds the feature recipes. `recipe_REG` / `recipe_CLF` are the shipped best feature sets; each returns a model-ready frame (features + target) indexed by date. Investigate `src.features.recipes` source code to see examples for building new recipes from the features present in `src.features.features`.

### Examples

```python
from src.features.recipes import recipe_CLF, recipe_REG, build_feature_frame

clf_frame = recipe_CLF("USGS-05465500")     # features + the `violation` target
reg_frame = recipe_REG("USGS-05465500")     # features + the `nitrate_con` target

# just the features (no target) -- e.g. for a virtual / ungauged site:
X = build_feature_frame("USGS-05465500", task="clf")
```

## Models

`src.models.train.build` runs the cross-site CV (logged to `logs/fulltrain_logs.json`), then fits a deployable model on ALL rows into `src/models/models/`. `tune_threshold` post-hoc tunes an already-trained classifier's β operating point (no retraining).

### Examples

```python
from src.models.train import build, REAL_XGB_CLF
from src.features.recipes import recipe_CLF

build("my_CLF", recipe_CLF, target_col="violation", task="clf", xgb=REAL_XGB_CLF)
```

```bash
# re-tune a deployed classifier's recall/precision cutoff table without retraining:
python -m src.models.tune_threshold isaac_CLF2 --recipe recipe_CLF
```

## Splits

`src.splits.conflict_graph` builds leak-safe train/test groups: sites whose nitrate records overlap in time form a "basin family" that must never be split across train and test. `basin_groups` (in `cook`) maps each pooled row to its family for the LOFO holdouts.

### Examples

```python
from src.splits.conflict_graph import split_groups, make_folds, audit_split

groups = split_groups()                     # {site_uid -> family id}; same id = must stay together
folds = make_folds(n_splits=5)              # a leak-safe fold assignment per site
audit = audit_split(train_sites=[...], test_sites=[...])   # flag any cross-family leakage in a split
```

