"""Experiment declarations. Each is a QUESTION + a dict of named recipes to compare, for ONE task (regression or classification -- a run is either, never both).

Add one with @experiment(id, question, task='reg'|'clf'): the decorated function takes the task and returns {recipe_name: recipe_fn}. The engine (run_exp.py) resolves sites, runs the comparison, and logs Question -> Winner -> Results to logs/experiments.jsonl. The FIRST recipe is the baseline; deltas are reported against it. To test the same recipes under both tasks, register two records (a plain id and a `c` id) sharing one build fn -- see exp 10 / 24 below.

Recipes here are self-contained (they don't route through src.features.recipes) so an experiment is a readable, standalone record of exactly what was compared. Each recipe is site_uid -> DataFrame that INCLUDES the task's target column (nitrate_con for reg, violation for clf). An untested feature family is implemented HERE first for the same reason -- promote it into src/features/ only once it has earned its place.

A build may instead return a MaskedSweep (kind='masked'): ONE wide recipe plus the column subsets that define the arms. Every arm then scores the same pool, the same rows and the same folds, so deltas are exactly paired and the cohort cannot drift between arms -- and a large grid costs one pooling pass instead of one per arm. Exps 30-32 use it.

    python run_exp.py --med 10 24c        # run these in med mode
    python run_exp.py --full 32 --seed 3  # replicate a run under a different model seed
    python run_exp.py --summary           # Question / Winner / Results for the latest of each
"""

from functools import lru_cache
from typing import cast

import networkx as nx
import numpy as np
import pandas as pd

from run_exp import MaskedSweep, experiment
from src.eval.cook import _STRUCTURAL  # cook's bookkeeping columns -- never features
from src.features.features import (
    agg_crops,
    agg_crops_normalized,
    agg_surplus,
    agg_surplus_normalized,
    agg_weather,
    agg_weather_w_lag,
    daily_nitrate,
    doy_climatology_pure_signal,
    _state_daily_wide,
    nitrate_daily_rolling,
    nitrate_violations_rolling,
    rolling_precip,
    site_static,
)
from src.features.transformers import _bucket_map, flatten_buckets, merge_on_date
from src.features.recipes import (  # shipped geometry + the shipped recipe
    CLF_EDGES,
    CLF_LAM,
    REG_EDGES,
    REG_LAM,
    recipe_maker,
)

# Water travel velocity (m/s) for the travel-time weather lag. It USED to come from recipes.REG_VEL / CLF_VEL, but the shipped recipe dropped the lag entirely (it now calls agg_weather, not agg_weather_w_lag) and deleted the constants. The historical experiments here still need a value to reproduce what they originally measured, so it lives locally at the shipped value of the day. bucket_lags rounds the lag to whole days, so nearby velocities are bit-identical anyway.
_HIST_VEL = 2.1
from src.data.access import (  # in lockstep with the accessors
    _COMID_ATTR_COLS,
    _PROC_MAP,
    _WEATHER_COLS,
    _basin_comid,
    get_basin,
    get_basin_area,
    get_basin_graph,
    get_crops,
    get_grid,
)


# ── shared building blocks ────────────────────────────────────────────────────
def _target(site_uid, task, window=1, min_obs=1):
    """The task's label column, named as the engine expects (nitrate_con / violation). The cast is only to satisfy the type checker -- the target builders return a Series at runtime, but pandas stubs type the upstream resample().agg() as Series|DataFrame, so .rename(<str>) looks ambiguous."""
    if task == "reg":
        s = nitrate_daily_rolling(site_uid=site_uid, window=window, min_obs=min_obs)
        return cast(pd.Series, s).rename("nitrate_con")
    s = nitrate_violations_rolling(site_uid=site_uid, window=window, min_obs=min_obs)
    return cast(pd.Series, s).rename("violation")


def _covariates(site_uid, edges, vel, lam):
    """The shipped spatial covariate block: travel-time-lagged weather + exp-decay crops + surplus, at the given bucket geometry. Returns a list of date-keyed frames."""
    e = list(edges)
    return [
        flatten_buckets(agg_weather_w_lag(site_uid=site_uid, edges=e, exp=False, water_velocity=vel)),
        flatten_buckets(agg_crops(site_uid=site_uid, edges=e, lam=lam, exp=True)),
        flatten_buckets(agg_surplus(site_uid=site_uid, edges=e, lam=lam, exp=True)),
    ]


def _assemble(site_uid, task, frames, static_drop=frozenset(), add_static=True):
    """target + the given covariate frames + the calendar signal, merged on the daily spine, with the static site descriptors broadcast in (minus any in `static_drop`). The spine dates only feed doy and the merge timeline -- no nitrate values leak into the features."""
    n = daily_nitrate(site_uid=site_uid)
    df = merge_on_date([_target(site_uid, task), *frames, doy_climatology_pure_signal(n)], spine=n.index)
    if add_static:
        for k, v in site_static(site_uid=site_uid).items():
            if k not in static_drop:
                df[k] = v
    return df


# ── exp 6: does lagged weather help? (HISTORICAL -- kept for the record) ───────────────────────────
# Original finding: lagged weather did essentially nothing for regression (yet fuel_moisture_1000h dominated importance -- it's a wetness proxy, chased later by exp 11/17). Preserved as a rerunnable record, not because the answer is expected to change.
_EXP6_LAGS = (1, 3, 7, 14, 30)


def _basin_weather_block(site_uid, lag):
    """Whole-basin daily weather (edges=[] -> one bucket), optionally shifted back `lag` days -- the same daily value lagged, exactly as exp 6 defined it (NOT an antecedent rolling sum; that's rolling_precip). Columns suffixed _now / _lagK."""
    w = flatten_buckets(agg_weather(site_uid=site_uid, edges=[])).sort_values("date").set_index("date").asfreq("D")
    if lag:
        w = w.shift(lag)
    sfx = f"_lag{lag}" if lag else "_now"
    w.columns = [f"{c}{sfx}" for c in w.columns]
    return w.reset_index()


@experiment(
    id="6",
    question="Does adding lagged copies of daily basin weather (the same value shifted back "
    "1/3/7/14/30 days) improve nitrate regression, or does weather add little beyond crops/surplus? "
    "[Historical: originally no -- kept as a rerunnable record.]",
    task="reg",
)
def lagged_weather(task):
    def base(site_uid):  # crops + surplus + doy only, no weather -- the paired baseline
        return [
            flatten_buckets(agg_crops(site_uid=site_uid, lam=5_000, exp=True)),
            flatten_buckets(agg_surplus(site_uid=site_uid, lam=5_000, exp=True)),
        ]

    def make(nlags, weather):
        def recipe(site_uid):
            frames = base(site_uid)
            if weather:
                frames.append(_basin_weather_block(site_uid, 0))  # contemporaneous
                frames += [_basin_weather_block(site_uid, L) for L in _EXP6_LAGS[:nlags]]
            return _assemble(site_uid, task, frames, add_static=False)

        return recipe

    recipes = {"no_weather": make(0, False)}
    for k in range(len(_EXP6_LAGS) + 1):
        recipes[f"{k}_lags"] = make(k, True)  # 0_lags = contemporaneous only; k_lags adds k lagged copies
    return recipes


# ── exp 10 / 10c: best spatial feature-construction geometry ──────────────────────────────────────
# A slimmed re-sweep of the original 27-cell grid. The shipped geometry came from exp 10 on the OLD feature set; now that static basin covariates (COMID/AgTile) exist, the optimum may have moved.
_GEOM_EDGES = (5_000, 50_000)
_GEOM_VELS = (1.2, 2.1)
_GEOM_LAMS = (20_000, 100_000)
_Q_GEOM = (
    "Which spatial geometry -- distance-bucket edge (m) / water travel velocity (m/s) / crop-surplus "
    "exp-decay length (m) -- maximizes transfer skill, re-swept now that static basin covariates exist?"
)


def _geometry_sweep(task):
    def make(e, v, lam):
        return lambda s: _assemble(s, task, _covariates(s, (e,), v, lam))

    return {
        f"e{e // 1000}k_v{v}_l{lam // 1000}k": make(e, v, lam)
        for e in _GEOM_EDGES
        for v in _GEOM_VELS
        for lam in _GEOM_LAMS
    }


experiment(id="10", question=_Q_GEOM, task="reg")(_geometry_sweep)
experiment(id="10c", question=_Q_GEOM, task="clf")(_geometry_sweep)


# ── exp 24 / 24c: do the static basin attributes close the between-site gap? ──────────────────────
# The modernized exp 8 static ablation, seeded with today's COMID + AgTile features. Geometry is held FIXED (single 50km edge) so the ONLY thing varying across arms is which static family is included.
_COMID_KEYS = frozenset(_COMID_ATTR_COLS)
_AGTILE_KEYS = frozenset({"tile_frac_ag", "tile_frac_basin"})
_STATIC_EDGES, _STATIC_VEL, _STATIC_LAM = (50_000,), 2.1, 100_000
_Q_STATIC = (
    "Do the static basin attributes close the between-site gap: base vs +AgTile vs +COMID vs +both, "
    "on a small covariate set (so the static features have room to matter)?"
)


def _static_ablation(task):
    def make(drop):
        return lambda s: _assemble(s, task, _covariates(s, _STATIC_EDGES, _STATIC_VEL, _STATIC_LAM), static_drop=drop)

    return {
        "base": make(_COMID_KEYS | _AGTILE_KEYS),  # baseline: neither static family
        "base+agtile": make(_COMID_KEYS),  # drop COMID -> base + AgTile
        "base+comid": make(_AGTILE_KEYS),  # drop AgTile -> base + COMID
        "base+both": make(frozenset()),  # full static (= shipped)
    }


experiment(id="24", question=_Q_STATIC, task="reg")(_static_ablation)
experiment(id="24c", question=_Q_STATIC, task="clf")(_static_ablation)


# ── exp 19c: do the two CLF wins stack? ───────────────────────────────────────────────────────────
# Riparian 2km inner bucket (exp 18, +0.039 lofo_prauc) and antecedent rolling-precip (exp 17, +0.017) -- do they add, or overlap? `combined` is the shipped recipe_CLF. Geometry vel/lam = the shipped CLF values; only the bucket edges and the precip block vary.
@experiment(
    id="19c",
    question="Do the two CLF wins -- a 2km riparian inner bucket and antecedent rolling-precip -- "
    "stack additively, or do they overlap (is combined ~= base + both individual lifts)?",
    task="clf",
)
def clf_design(task):
    def make(edges, roll):
        def recipe(site_uid):
            frames = _covariates(site_uid, edges, _HIST_VEL, CLF_LAM)
            if roll:
                frames.append(rolling_precip(site_uid=site_uid, windows=(7, 14, 30)))
            return _assemble(site_uid, task, frames)

        return recipe

    return {
        "base": make((5_000,), False),
        "riparian": make((2_000, 5_000), False),
        "roll_precip": make((5_000,), True),
        "combined": make((2_000, 5_000), True),  # = the shipped recipe_CLF
    }


# ── exp 26 / 26c: do the local-catchment cat_* features help or hurt the shipped recipe? ───────────
# cat_tiles92/bfi/contact are per-reach LOCAL values (near-unique per site). In the full retrain they showed high gain but ~0 permutation importance -- classic site-fingerprint behaviour that inflates in-sample fit but hurts transfer. This tests removing them from the ACTUAL shipped recipe.
_CAT_KEYS = tuple(c for c in _COMID_ATTR_COLS if c.startswith("cat_"))  # cat_tiles92, cat_bfi, cat_contact
_Q_CAT = (
    "Do the local-catchment cat_* COMID features (cat_tiles92/bfi/contact) help or hurt the shipped "
    "recipe's transfer? They show high gain but ~0 permutation importance -- likely site fingerprints."
)


def _drop_cols(recipe, cols):
    """Wrap a recipe so its output frame has `cols` removed (silently kept if a col is absent)."""

    def wrapped(site_uid):
        df = recipe(site_uid)
        return df.drop(columns=[c for c in cols if c in df.columns])

    return wrapped


def _cat_ablation(task):
    shipped = recipe_maker(task, window=1)  # the ACTUAL shipped recipe (recipe_REG / recipe_CLF)
    return {
        "with_cat": shipped,  # baseline: shipped recipe as-is (cat_* included)
        "no_cat": _drop_cols(shipped, _CAT_KEYS),  # cat_* removed, tot_* + tile_frac_* kept
    }


experiment(id="26", question=_Q_CAT, task="reg")(_cat_ablation)
experiment(id="26c", question=_Q_CAT, task="clf")(_cat_ablation)


# ══ masked sweeps: one pooling pass, arms are column subsets ═══════════════════════════════════════ The blocks below are the encodings that will SHIP: crops and surplus as per-bucket normalized
# (pct_* / surplus_kgha_norm, which take no lam) plus whole-basin exp-decay (edges=(), which takes no
# edges). That split is what makes a joint edges x lam grid cheap -- see _geometry_grid.

_CROP_CLASSES = ("Alfalfa", "Corn", "Fallow", "Hay_Pasture", "Nonag", "Other", "Small_Grains", "Soybeans")
_WEATHER_VARS = tuple(c for c in _WEATHER_COLS if c not in ("date", "node_id", "global_node_id"))
_KGHA = ["surplus_kgha"]  # surplus's other column, total_kg_N, scales with basin size and is dropped
_DOY_COLS = ["doy_sin", "doy_cos", "doy_sin2", "doy_cos2"]  # doy_climatology_pure_signal's output
_STATIC_COLS = sorted(  # site_static's keys: the COMID block + AgTile + the base geo descriptors
    set(_COMID_ATTR_COLS)
    | {"tile_frac_ag", "tile_frac_basin", "lat", "lon", "log_basin_area", "mean_dist_to_sensor", "max_dist_to_sensor"}
)


def _tag(frame, suffix, keep=None):
    """Suffix a frame's VALUE columns so an encoding can coexist with its siblings in one wide frame.

    `date`, `year` and `bucket` are protected: they are merge/pivot keys, and suffixing `date` would leave flatten_buckets and merge_on_date unable to find the row key. `keep`, if given, first restricts the value columns to that list.
    """
    keys = [c for c in ("date", "year", "bucket") if c in frame.columns]
    if keep is not None:
        frame = frame[keys + [c for c in keep if c in frame.columns]]
    return frame.rename(columns={c: f"{c}{suffix}" for c in frame.columns if c not in keys})


def _bucket_names(bases, tag, n_buckets):
    """The column names a bucketed block emits. Deterministic from geometry, so masks need no pool -- compare_masks drops any name that did not materialize (an unoccupied bucket emits no column).

    A single-bucket geometry (edges=()) carries NO bucket dimension: agg_grid_to_buckets never adds the column and flatten_buckets returns the frame untouched, so the names stay bare rather than gaining a `_b0`.
    """
    if n_buckets <= 1:
        return [f"{b}{tag}" for b in bases]
    return [f"{b}{tag}_b{k}" for b in bases for k in range(n_buckets)]


def _edge_block(site_uid, edges, vel, tag):
    """The EDGES-dependent half of the shipping encodings: bucketed weather (lagged, plain area-mean) + per-bucket normalized crops and surplus. Carries no lam -- none of these three consume it."""
    e = list(edges)
    kw = {"site_uid": site_uid}
    return [
        flatten_buckets(_tag(agg_weather_w_lag(**kw, edges=e, exp=False, water_velocity=vel), tag)),
        flatten_buckets(_tag(agg_crops_normalized(**kw, edges=e), tag)),
        flatten_buckets(_tag(agg_surplus_normalized(**kw, edges=e), tag, keep=_KGHA)),
    ]


def _lam_block(site_uid, lam, tag):
    """The LAM-dependent half: whole-basin exp-decay crops and surplus. edges=() collapses the bucket dimension, so flatten_buckets passes these through unsuffixed and they are invariant to the edge grid."""
    kw = {"site_uid": site_uid}
    return [
        flatten_buckets(_tag(agg_crops(**kw, edges=(), lam=lam, exp=True), tag)),
        flatten_buckets(_tag(agg_surplus(**kw, edges=(), lam=lam, exp=True), tag, keep=_KGHA)),
    ]


def _edge_cols(edges, tag):
    n = len(edges) + 1
    return (
        _bucket_names(_WEATHER_VARS, tag, n)
        + _bucket_names([f"pct_{c.lower()}" for c in _CROP_CLASSES], tag, n)
        + _bucket_names(_KGHA, tag, n)
    )


def _lam_cols(tag):
    return [f"{c}{tag}" for c in _CROP_CLASSES] + [f"{c}{tag}" for c in _KGHA]


# ── exp 30 / 30c: joint edges x lam geometry grid ─────────────────────────────────────────────────
# The shipped geometry came from exp 10: a 27-cell grid on 20 sites, against an encoding set the project has since moved off. The cohort is now 123 sites, the crops/surplus aggregation bugs are fixed, and CLF's edges were last set by hand on grid-resolution grounds rather than measured.
#
# Why this is affordable: under the shipping encodings the two knobs barely interact. `edges` drives
# only the norm-bucketed block (agg_*_normalized take no lam at all), `lam` drives only the
# whole-basin expT block (its edges are fixed at ()). So ONE pooling pass emits |E| + |L| blocks and each of the |E| x |L| cells is a mask over them -- additive cost, multiplicative grid. Measured on this machine: 13.0s to load a site, 1.63s per extra geometry on an already-loaded one.
#
# Edge candidates are chosen against measured bucket occupancy over all 123 sites, because an unoccupied bucket emits no column for that site (NaN in the pool) -- a geometry can otherwise look bad purely for being ragged. Occupancy of the innermost bucket: (4k)=91.1%, (8k)=94.3%, (20k)=79.7%, (50k)=57.7%, (4k,8k)=91.1%, (5k,20k)=79.7%, (8k,30k)=72.4%, (4k,8k,20k)=79.7%.
# `()` is the no-bucketing null arm. lam's 10**9 cell is the decay-off control: exp(-d/lam) -> 1
# turns the whole-basin block into a plain membership-weighted sum.
_GRID_EDGES = {
    "reg": ((50_000,), (), (8_000,), (20_000,), (5_000, 20_000), (8_000, 30_000)),
    "clf": ((4_000, 8_000), (), (4_000,), (8_000,), (5_000, 20_000), (4_000, 8_000, 20_000)),
}
_GRID_LAMS = (2_000, 5_000, 20_000, 50_000, 100_000, 10**9)
_Q_GRID = "What're the best geometry parameters to use? What are the best bucket edges and the best lambda values for exp-distance decay?"


def _edge_tag(i):
    return f"_e{i}"


def _lam_tag(j):
    return f"_l{j}"


def _geometry_grid(task):
    """One wide recipe emitting every edge block and every lam block; one arm per (edges, lam) cell.

    The shipped geometry is deliberately the FIRST cell, so delta_vs_base reads as "change vs what we ship today". Static/calendar columns are shared by every arm and form base_cols, which scopes --extra's permutation importance to the columns a cell actually varies.
    """
    edge_grid = _GRID_EDGES[task]
    vel = _HIST_VEL

    def wide(site_uid):
        frames = []
        for i, e in enumerate(edge_grid):
            frames += _edge_block(site_uid, e, vel, _edge_tag(i))
        for j, lam in enumerate(_GRID_LAMS):
            frames += _lam_block(site_uid, lam, _lam_tag(j))
        return _assemble(site_uid, task, frames)

    probe = _assemble_base_cols(task)
    masks = {}
    for i, e in enumerate(edge_grid):
        ecols = _edge_cols(e, _edge_tag(i))
        ename = "whole" if not e else "_".join(f"{x // 1000}k" for x in e)
        for j, lam in enumerate(_GRID_LAMS):
            lname = "off" if lam >= 10**8 else f"{lam // 1000}k"
            masks[f"e{ename}_l{lname}"] = probe + ecols + _lam_cols(_lam_tag(j))
    return MaskedSweep(wide=wide, masks=masks, base_cols=probe)


def _assemble_base_cols(task=None):
    """The geometry-INDEPENDENT columns every arm shares: the calendar harmonics from doy_climatology_pure_signal plus the statics site_static broadcasts. Verified against a built frame by the `masks_match_frame` check below -- if site_static gains a key, that check fails rather than the column silently vanishing from every mask."""
    return _DOY_COLS + _STATIC_COLS


experiment(id="30", question=_Q_GRID, task="reg", kind="masked")(_geometry_grid)
experiment(id="30c", question=_Q_GRID, task="clf", kind="masked")(_geometry_grid)


# ── exp 31 / 31c: is weather more useful bucketed or whole-basin? ─────────────────────────────────
# A 2x2, not a straight A/B, because un-bucketed weather is STILL travel-time lagged: agg_weather_w_lag calls bucket_lags/lag_buckets unconditionally, and with edges=() that collapses to a single basin-median lag rather than no lag. A bare bucketed-vs-whole comparison would therefore confound spatial resolution with differential lag. Crossing the two separates them: if `bucket_lag` beats
# `whole_lag` but `bucket_nolag` ~= `whole_nolag`, the win is the per-ring LAG, not the resolution.
# agg_weather (no _w_lag) is the un-lagged aggregator. Everything except weather is held at the shipped geometry.
_Q_WEATHER = (
    "Is weather more useful bucketed by distance or aggregated over the whole basin -- and is any "
    "difference due to spatial resolution or to the per-bucket travel-time lag?"
)


def _weather_arms(task):
    edges = REG_EDGES if task == "reg" else CLF_EDGES
    vel = _HIST_VEL
    lam = REG_LAM if task == "reg" else CLF_LAM
    e = list(edges)
    n = len(edges) + 1

    arms = {
        "bucket_lag": (lambda s: agg_weather_w_lag(site_uid=s, edges=e, exp=False, water_velocity=vel), True),
        "bucket_nolag": (lambda s: agg_weather(site_uid=s, edges=e, exp=False), True),
        "whole_lag": (lambda s: agg_weather_w_lag(site_uid=s, edges=(), exp=False, water_velocity=vel), False),
        "whole_nolag": (lambda s: agg_weather(site_uid=s, edges=(), exp=False), False),
    }

    def wide(site_uid):
        frames = []
        for name, (fn, _) in arms.items():
            frames.append(flatten_buckets(_tag(fn(site_uid), f"_{name}")))
        # the non-weather covariates, held at the shipped geometry, shared by every arm
        frames += _edge_block(site_uid, edges, vel, "_fixed")[1:]  # [1:] drops this block's weather
        frames += _lam_block(site_uid, lam, "_fixed")
        return _assemble(site_uid, task, frames)

    shared = (
        _assemble_base_cols(task)
        + _bucket_names([f"pct_{c.lower()}" for c in _CROP_CLASSES], "_fixed", n)
        + _bucket_names(_KGHA, "_fixed", n)
        + _lam_cols("_fixed")
    )
    masks = {}
    for name, (_, bucketed) in arms.items():
        tag = f"_{name}"
        wcols = _bucket_names(_WEATHER_VARS, tag, n) if bucketed else [f"{v}{tag}" for v in _WEATHER_VARS]
        masks[name] = shared + wcols
    return MaskedSweep(wide=wide, masks=masks, base_cols=shared)


experiment(id="31", question=_Q_WEATHER, task="reg", kind="masked")(_weather_arms)
experiment(id="31c", question=_Q_WEATHER, task="clf", kind="masked")(_weather_arms)


# ── exp 32 / 32c: does long-run basin composition move the BETWEEN-site metrics? ──────────────────
# Tests notes/plan_long_run_composition.md. Crops and surplus are joined PER YEAR, so the model sees one year's land cover -- a snapshot carrying that year's weather, rotation phase and price response. What separates two basins over the long run is their CHARACTERISTIC land use and how variable it is, and no current feature is a function of the time series of the CDL raster.
#
# Two blocks, both static per site (so both stay precomputable per COMID for the widget): mean    -- per class and bucket, the mean share across all available years sd_rot  -- the same quantities' year-to-year SD, plus rotation_index, the mean absolute year-over-year change in the corn share. Continuous corn and a corn/soy rotation have very different N loading at the SAME mean corn share; nothing currently separates them.
#
# Two measurements taken on this cohort before running, which shape how to read the result:
#   * rotation_index is nearly orthogonal to what the recipe already has -- vs pct_corn_mean
# pearson +0.17, vs pct_soybeans_mean +0.09, vs surplus_kgha_mean -0.02. The plan's claim that this is new information rather than a re-slice holds.
#   * but it correlates +0.80 with pct_corn_sd, so the sd_rot arm is close to testing ONE signal,
# not two; and its magnitude is small (median 0.0195, i.e. ~2 percentage points of year-over-year change). That smallness is an artifact of averaging: a basin holds thousands of fields, and half flipping corn->soy while the other half flip back leaves the basin MEAN almost unmoved. Hence the _cell arms below, which measure rotation per cell first -- 1.3x to 10x larger, with a site-dependent ratio, so the two definitions re-rank basins rather than rescale them.
#
# Implemented here rather than in src/features/ deliberately: the proposal is untested, and exps.py's convention is that an experiment is a standalone record of exactly what was compared. Promote it to features.py/recipes.py only if it earns its place.
#
# Protocol notes from the plan, and how they are met:
#   * ablate on the FULL shipped recipe, not a light one   -> baseline IS recipe_maker(task)
#   * blocks separately then together                      -> 4 arms: base / mean / sd_rot / both
#   * hold the site set FIXED across arms                  -> kind='masked' scores one pool, so the
# cohort is identical by construction (two sites were worth 0.036 of between_r2 in the logs)
#   * report between/macro alongside the headline          -> the record's metrics carry every column
#     cook.py returns, so lofo_between_r2 / lofo_between_rate_r2 / lofo_macro_* are all logged
#   * treat < ~0.08 as no result, repeat across seeds      -> `python run_exp.py --full 32 --seed N`;
# judge the PAIRED delta's spread, not either arm's absolute score
_Q_LONGRUN = (
    "Does long-run basin composition -- the multi-year mean crop/surplus shares, their year-to-year "
    "std, and a corn-rotation index -- improve the BETWEEN-site metrics over the shipped recipe, which "
    "only ever sees one year of land cover at a time?"
)


def _longrun_cols(site_uid, edges) -> dict:
    """Long-run composition + rotation intensity as STATIC scalars: {column_name: value}.

    Reduces the per-year normalized shares (the same agg_*_normalized frames the shipped recipe already builds) over ALL available years, per distance bucket. Static per site, so the caller broadcasts them across the daily spine rather than merging on date.

    rotation_index is the mean absolute year-over-year change in the corn share, per the plan: high for a corn/soy rotation, low for continuous corn or permanent pasture. std() over a single year is NaN, which XGBoost consumes natively -- that is the honest encoding of "this site has no multi-year record to reduce".
    """
    e = list(edges)
    pct = [f"pct_{c.lower()}" for c in _CROP_CLASSES]
    out = {}
    for frame, vals in (
        (agg_crops_normalized(site_uid=site_uid, edges=e), pct),
        (agg_surplus_normalized(site_uid=site_uid, edges=e), _KGHA),
    ):
        vals = [c for c in vals if c in frame.columns]
        groups = (
            [(int(b), g) for b, g in frame.groupby("bucket", observed=True)]
            if "bucket" in frame.columns
            else [(None, frame)]
        )
        for b, g in groups:
            sfx = "" if b is None else f"_b{b}"
            g = g.sort_values("year")
            for c in vals:
                out[f"{c}_mean{sfx}"] = float(g[c].mean())
                out[f"{c}_sd{sfx}"] = float(g[c].std())
            if "pct_corn" in vals:
                out[f"rotation_index{sfx}"] = float(g["pct_corn"].diff().abs().mean())
    return out


def _cell_rotation_cols(site_uid, edges) -> dict:
    """Rotation intensity measured PER CELL and then averaged, rather than as the year-over-year change of the basin's mean share. Emits rotation_cell[_b{k}].

    Why a second definition. A basin holds thousands of fields; when half flip corn->soy while the other half flip back, the basin's MEAN share barely moves even though every field rotated. Differencing that mean therefore cancels most of the field-scale rotation the index is meant to measure. Measured across this cohort the per-cell value runs 1.3x-10x the basin-level one and the ratio varies by site, so the two definitions RE-RANK basins rather than differing by a constant -- which is why they are compared head to head instead of one replacing the other.

    Each cell's corn share is differenced over years, absolute-valued and averaged; cells are then combined per distance bucket weighted by frac_cell_in_basin, so a partial edge cell counts partially. A cell with no crop pixels in a year contributes NaN and drops out of that cell's mean.
    """
    crops, grid = get_crops(site_uid), get_grid(site_uid)
    classes = [c for c in _CROP_CLASSES if c in crops.columns]
    total = crops[classes].sum(axis=1)
    share = pd.DataFrame(
        {
            "node_id": crops["node_id"].to_numpy(),
            "year": crops["year"].to_numpy(),
            "share": np.where(total > 0, crops["Corn"] / total.where(total > 0), np.nan),
        }
    )
    per_cell = share.sort_values("year").groupby("node_id")["share"].apply(lambda s: s.diff().abs().mean())
    weight = pd.Series(grid["frac_cell_in_basin"].to_numpy(), index=grid["node_id"].to_numpy())
    bucket = _bucket_map(site_uid=site_uid, edges=list(edges)).reindex(per_cell.index)

    out, n_buckets = {}, len(edges) + 1
    for k in range(n_buckets):
        sel = per_cell.index if n_buckets == 1 else per_cell.index[(bucket == k).to_numpy()]
        v, w = per_cell.reindex(sel), weight.reindex(sel)
        ok = v.notna() & w.notna()
        if not ok.any() or w[ok].sum() == 0:
            continue  # unoccupied bucket -> no column, matching every other bucketed block
        out[f"rotation_cell{'' if n_buckets == 1 else f'_b{k}'}"] = float((v[ok] * w[ok]).sum() / w[ok].sum())
    return out


def _longrun_ablation(task):
    """The shipped recipe, plus the long-run blocks, ablated one block at a time.

    Six arms. The two `_cell` arms swap ONLY the rotation definition -- basin-mean differencing vs
    per-cell-then-average -- holding the mean and sd blocks fixed, so the head-to-head isolates the
    definition rather than confounding it with which blocks are present:

        base                 the shipped recipe, unchanged (paired baseline)
        longrun_mean         + multi-year mean shares
        longrun_sd_rot       + year-to-year sd + rotation_index   (basin-mean differencing)
        longrun_sd_rot_cell  + year-to-year sd + rotation_cell    (per-cell, then averaged)
        longrun_both         + mean + sd + rotation_index
        longrun_both_cell    + mean + sd + rotation_cell

    Reading the result: if sd_rot is flat but sd_rot_cell is not, rotation matters and the basin-mean definition was averaging the signal away. If both are flat, rotation does not carry between-site signal at this cohort size, and the plan's fallback (tile drainage / soil permeability) is the next move rather than another CDL derivative.

    The masks are a callable of the pool because the baseline is "whatever the shipped recipe currently emits" -- those column names depend on the shipped geometry, the weather encodings and the static set, and enumerating them here would silently drift the moment recipes.py changes. Deriving base as (pool columns - long-run columns) cannot drift.
    """
    shipped = recipe_maker(task, window=1)
    edges = REG_EDGES if task == "reg" else CLF_EDGES

    def wide(site_uid):
        df = shipped(site_uid)
        for block in (_longrun_cols(site_uid, edges), _cell_rotation_cols(site_uid, edges)):
            for k, v in block.items():
                df[k] = v  # static per site -> broadcast, not merged on date
        return df

    def masks(pool):
        # _STRUCTURAL is cook.py's own "never a feature" set (site/date/year/...); using it rather than a hand-written list keeps this in step with what the recipe path would have selected.
        drop = set(_STRUCTURAL) | {"nitrate_con", "violation"}
        cols = [c for c in pool.columns if c not in drop]
        # Classify explicitly: "rotation_cell" must NOT fall into the rotation_index bucket, and a startswith test on "rotation" would put both in the same block and silence the comparison.
        mean_cols = [c for c in cols if "_mean_b" in c or c.endswith("_mean")]
        sd_cols = [c for c in cols if "_sd_b" in c or c.endswith("_sd")]
        rot_basin = [c for c in cols if c.startswith("rotation_index")]
        rot_cell = [c for c in cols if c.startswith("rotation_cell")]
        longrun = set(mean_cols) | set(sd_cols) | set(rot_basin) | set(rot_cell)
        base = [c for c in cols if c not in longrun]
        return {
            "base": base,
            "longrun_mean": base + mean_cols,
            "longrun_sd_rot": base + sd_cols + rot_basin,
            "longrun_sd_rot_cell": base + sd_cols + rot_cell,
            "longrun_both": base + mean_cols + sd_cols + rot_basin,
            "longrun_both_cell": base + mean_cols + sd_cols + rot_cell,
        }

    return MaskedSweep(wide=wide, masks=masks, base_cols=lambda pool: masks(pool)["base"])


experiment(id="32", question=_Q_LONGRUN, task="reg", kind="masked")(_longrun_ablation)
experiment(id="32c", question=_Q_LONGRUN, task="clf", kind="masked")(_longrun_ablation)


# ── exp 33-35c: does a hydrologically-connected NEIGHBOR beat the statewide scalar, and how should its direction be encoded? ──
# Tests experiments/pre-expansion-network-test/REPORT.md. The shipped recipes collapse the entire donor channel into rest_of_state_nitrate_lag{1,3} -- the mean over EVERY other site, which the report calls "the degenerate collapse of a donor channel". On the transitive-reduction (reach) graph the signal is graded and real: residual Spearman +0.36 at one hop, +0.22 at two, +0.10 at three, with +0.086 of the 2-hop surviving control for the intermediate.
#
# THREE ENCODINGS, one builder. They differ in exactly one thing -- how the neighbor's direction reaches the model -- so their delta_vs_base tables are directly comparable and their `base` arms must score identically:
#   33/33c 'both'   -- both neighbors, every column suffixed _up / _down; the model sees each direction separately
#   34/34c 'flag'   -- one either-direction neighbor by drainage scale, direction as the flag `neighbor_up` (Follow-up E's literal recommendation)
#   35/35c 'signed' -- the same one neighbor, direction folded into the SIGN of `dist_to_neighbor`, no flag
#
# Coverage on THIS cohort of 123, once mutual-containment pairs are resolved by area (see _best_neighbors); the report's figures are from 83 sites: upstream 44%, downstream 73%, either-direction 89%, and 14 sites (11%) with no monitored neighbor at all. The coverage scalars exist so the tree can switch regimes rather than reading NaN as a value.
#
# EXPECT THE HEADLINE NOT TO MOVE. The report's own recommendation 5 is explicit that this improves the timing channel (within_r2 ~0.42, already good) rather than the between-site ranker (~0.21, the actual bottleneck). Judge on lofo_within_r2 first; nbr_level is the only arm aimed at the between channel. Manifest noise floors: REG add 0.0085, CLF add 0.0037 on the headline, REG between_r2 add 0.0258.
#
# NOT COMPARABLE TO HISTORICAL LOFO. The model now consumes another site's observed nitrate, so a held-out basin is scored with a working gauge inside its own family -- a legitimately different, easier question than the current LOFO asks. 12 of 98 reach edges also cross family boundaries, leaving the neighbor in training when the target is held out.
_Q_NEIGHBOR_BOTH = (
    "Does the nitrate of the hydrologically-connected monitored neighbors -- BOTH the nearest "
    "upstream and the nearest downstream, on the reach graph -- predict a site beyond the statewide "
    "rest_of_state scalar the recipe already carries, and does any of it survive being made causal?"
)
_Q_NEIGHBOR_FLAG = (
    "With ONE either-direction neighbor picked by drainage scale, and its direction carried as a "
    "separate flag, does the neighbor's nitrate predict a site beyond the statewide rest_of_state "
    "scalar, and does any of it survive being made causal?"
)
_Q_NEIGHBOR_SIGNED = (
    "Same single either-direction neighbor as 34, but with direction folded into the SIGN of the "
    "distance instead of a separate flag: does one signed column encode direction as well as the "
    "magnitude-plus-flag pair does?"
)
_NBR_LAGS = (0, 1, 3)  # 0 = concurrent (what the report measured); 1/3 = causal, matching rest_of_state_nitrate_lag*

# Each variant's FULL column set, by block. ONE source of truth: _neighbor_block fills exactly these and `masks` classifies by exact membership in them, so a name can never drift between the emitter and the arm it belongs to.
_NBR_COLS = {
    # 33/33c -- both neighbors, nothing chosen between them. nbr_is_upstream is absent on purpose: suffixed per direction it would be a constant column. nbr_n_neighbors splits into nbr_n_up/nbr_n_down, whose sum reproduces the old scalar exactly because up and down are disjoint in a DAG.
    "both": {
        "concurrent": ["nbr_anom_lag0_up", "nbr_anom_lag0_down"],
        "lagged": ["nbr_anom_lag1_up", "nbr_anom_lag3_up", "nbr_anom_lag1_down", "nbr_anom_lag3_down"],
        "level": ["nbr_level_lag1_up", "nbr_level_lag1_down"],
        "twohop": ["nbr2_anom_lag1_up", "nbr2_anom_lag1_down"],
        "cover": ["distance_up", "distance_down", "nbr_area_ratio_up", "nbr_area_ratio_down", "nbr_n_up", "nbr_n_down"],
    },
    # 36/36c -- the SAME columns as 'both', selected by distance instead of drainage scale. Identical on purpose: 33 vs 36 must differ in exactly one thing, the rule that picks which gauge each direction resolves to. distance_up/_down are already here, so they simply change meaning -- in 'both' they describe the chosen neighbor on the axis it was NOT chosen by, in 'both_dist' they are the minimised quantity, and nbr_area_ratio_* swaps roles with them.
    "both_dist": {
        "concurrent": ["nbr_anom_lag0_up", "nbr_anom_lag0_down"],
        "lagged": ["nbr_anom_lag1_up", "nbr_anom_lag3_up", "nbr_anom_lag1_down", "nbr_anom_lag3_down"],
        "level": ["nbr_level_lag1_up", "nbr_level_lag1_down"],
        "twohop": ["nbr2_anom_lag1_up", "nbr2_anom_lag1_down"],
        "cover": ["distance_up", "distance_down", "nbr_area_ratio_up", "nbr_area_ratio_down", "nbr_n_up", "nbr_n_down"],
    },
    # 34/34c -- one neighbor, direction as its own flag. This is Follow-up E's literal recommendation: "nearest monitored neighbor, either direction, dilution-weighted, plus a direction flag".
    "flag": {
        "concurrent": ["nbr_anom_lag0"],
        "lagged": ["nbr_anom_lag1", "nbr_anom_lag3"],
        "level": ["nbr_level_lag1"],
        "twohop": ["nbr2_anom_lag1"],
        "cover": ["nbr_n_neighbors", "nbr_area_ratio", "dist_to_neighbor", "neighbor_up"],
    },
    # 35/35c -- identical to 'flag' but for the direction encoding, which is the ONLY thing that differs: no flag, and dist_to_neighbor goes negative upstream. A tree can still recover the flag with one split at 0; the question is whether that split costs anything.
    "signed": {
        "concurrent": ["nbr_anom_lag0"],
        "lagged": ["nbr_anom_lag1", "nbr_anom_lag3"],
        "level": ["nbr_level_lag1"],
        "twohop": ["nbr2_anom_lag1"],
        "cover": ["nbr_n_neighbors", "nbr_area_ratio", "dist_to_neighbor"],
    },
}


# Which reach graph each variant walks, and how it picks among candidates. Explicit rather than parsed off the variant name, because the two knobs are independent and a name-suffix convention would silently mis-set one of them.
#   immediate=True  -- get_basin_graph(immediate_only=True): each child keeps only its SMALLEST enclosing parent, so every site has exactly one successor and a downstream "choice" does not exist.
#   immediate=False -- the full containment graph: up to 7 successors and 22 predecessors, so a selection rule has something to choose in BOTH directions.
_VARIANT_RULE = {
    "both": dict(select="area", immediate=True),
    "flag": dict(select="area", immediate=True),
    "signed": dict(select="area", immediate=True),
    "both_dist": dict(select="dist", immediate=False),
}


def _edge_dist_m(graph, child, parent):
    """Metres along the containment edge child -> parent: flow distance, else straight-line, else NaN.

    NaN-SAFE, which an `or` chain is not: _make_aux writes float('nan') on the ~7 of 98 immediate-only edges D8 cannot route, and NaN is TRUTHY in Python, so `flow or euc or nan` short-circuits on the NaN and never reaches the fallback -- leaving the distance missing on exactly the edges the fallback exists for. Same idiom as src/splits/conflict_graph.py.
    """
    e = graph.edges[child, parent]
    for k in ("flow_dist_m", "euc_dist_m"):
        v = e.get(k)
        if v is not None and np.isfinite(v):
            return float(v)
    return np.nan


def _area(areas, uid, missing=np.nan):
    """Positive finite basin area (m^2) for `uid`, else `missing`.

    Guards both the log10 ratio and the min/max selection keys: max(nan, x) returns NAN because every NaN comparison is False, so an unguarded missing area survives as a sort key and silently reorders the candidates. Pass -inf / +inf to sort a missing area last under max / min.
    """
    a = areas.get(uid)
    return float(a) if a is not None and np.isfinite(a) and a > 0 else missing


def _best_neighbors(site_uid, graph, areas, select="area"):
    """(best_up, best_down, n_up, n_down) on the reach graph, picked by `select`.

    `select="area"` is the drainage-scale rule Follow-up D and E measured. `select="dist"` picks the NEAREST candidate in each direction instead -- along-network metres where D8 could route it, straight-line where it could not -- which is exp 36's whole question: the area rule was the only one ever measured, and "nearest" is the reading of the word the report never tested. Direction assignment and the mutual-containment tie-break stay on area under both rules, because those decide WHICH WAY a neighbor lies, not which of several to take.

    DOWNSTREAM is the single graph successor -- the smallest basin enclosing this one, since get_basin_graph(immediate_only=True) keeps each child's smallest enclosing parent. UPSTREAM is the LARGEST nested sub-basin: Follow-up D measures it at +0.465 against +0.470 for an area-weighted aggregate of EVERY upstream site, so one neighbor beats an aggregate, and it is the same site as "closest in drainage scale" because every predecessor is nested inside this basin and therefore smaller.

    Deliberately does NOT use the graph's corr / corr_resid / n_overlap_days edge attributes. They are precomputed and tempting as a dilution weight, but each is computed FROM THIS SITE'S OWN nitrate series -- selecting or weighting a neighbor by them would fit the feature on the label. Drainage area is the honest weight.
    """
    if site_uid not in graph:
        return None, None, 0, 0
    own = _area(areas, site_uid)
    up = set(graph.predecessors(site_uid))  # basins nested inside this one
    down = set(graph.successors(site_uid))  # smallest enclosing basin(s)
    # MUTUAL CONTAINMENT -- 4 pairs on this graph (8 of 123 sites) land in both sets: two near-coincident gauges each enclosing the other's sensor, so the containment relation carries no direction. Left alone they resolve as best_up == best_down and the same series ships under two column names. Direction goes to drainage area, the module's honest weight: area <= own is upstream, and every such pair here is EXACTLY equal-area, so the tie deliberately breaks upstream -- an equal-area donor is the undiluted extreme of the ratio Follow-up E ranks on, and upstream is the stronger channel (+0.503 vs +0.361). Making the sets disjoint also makes len(up) + len(down) the true neighbour count.
    for u in up & down:
        (down if _area(areas, u, np.inf) <= own else up).discard(u)
    if select == "dist":
        best_up = min(up, key=lambda u: _dist_or(graph, u, site_uid)) if up else None
        best_down = min(down, key=lambda d: _dist_or(graph, site_uid, d)) if down else None
    else:
        best_up = max(up, key=lambda u: _area(areas, u, -np.inf)) if up else None
        best_down = min(down, key=lambda d: _area(areas, d, np.inf)) if down else None
    return best_up, best_down, len(up), len(down)


def _dist_or(graph, child, parent, missing=np.inf):
    """_edge_dist_m, with a non-finite result sorted LAST rather than acting as a wildcard -- the same guard _area applies, for the same reason: every NaN comparison is False, so an unrouted edge would otherwise win a min()."""
    d = _edge_dist_m(graph, child, parent)
    return d if np.isfinite(d) else missing


def _best_either(site_uid, best_up, best_down, areas):
    """Whichever neighbor is closest to this basin in drainage scale -> (uid, is_up), or (None, None).

    The dilution law Follow-up E confirms (Spearman(area_ratio, rho) = -0.67): a downstream gauge scores lower on average (+0.361 vs +0.503) only because the nearest one is often a much bigger, more diluted river. With no usable area the tie breaks to upstream, the stronger predictor of the two -- unreachable while `areas` covers every graph node, and explicit so it is a decision rather than NaN ordering.
    """
    own = _area(areas, site_uid)
    cands = [(u, True) for u in (best_up,) if u] + [(d, False) for d in (best_down,) if d]
    scored = [(c, abs(np.log10(_area(areas, c[0]) / own))) for c in cands]
    scored = [(c, s) for c, s in scored if np.isfinite(s)]
    if scored:
        return min(scored, key=lambda t: t[1])[0]
    return next(iter(cands), (None, None))


def _two_hop(site_uid, best, is_up, graph, areas, exclude, select="area"):
    """One more step in the SAME direction from `best` (A -> M -> B), skipping `exclude`.

    `exclude` carries the origin AND the other direction's neighbor: in a diamond topology the second hop can land on a gauge already emitted as the opposite direction's first hop, shipping one series under two column names.
    """
    if best is None:
        return None
    nxt = [n for n in (graph.predecessors(best) if is_up else graph.successors(best)) if n not in exclude]
    if not nxt:
        return None
    if select == "dist":  # the second hop follows the same rule as the first, or the chain mixes criteria
        return min(nxt, key=lambda n: _dist_or(graph, n, best) if is_up else _dist_or(graph, best, n))
    return (
        max(nxt, key=lambda u: _area(areas, u, -np.inf)) if is_up else min(nxt, key=lambda d: _area(areas, d, np.inf))
    )


def _anom_baseline(wide):
    """(row sum, row non-null count) of the statewide daily frame, for an O(1)-per-site leave-one-out mean.

    Recomputing `wide.drop(columns=site).mean(axis=1)` inside the per-site loop is O(n_sites^2 x n_dates) -- ~120M float ops per pooling pass at this cohort size. Subtracting the site's own column from these two reproduces it exactly, NaN-skipping included.
    """
    return wide.sum(axis=1), wide.notna().sum(axis=1)


def _except_this_mean(wide, sums, counts, site_uid):
    """Statewide daily mean EXCLUDING `site_uid` -- the anomaly baseline.

    Excluding the target is load-bearing: without it the target's own nitrate re-enters the feature through the baseline, a silent label leak. It does NOT exclude the neighbor, mirroring nitrate_avg_except_this, so an anomaly carries 1/(n-1) of its own signal -- and under 'both' the _up and _down anomalies share a baseline containing both, giving them a small shared negative dependence. Harmless, recorded so it is not rediscovered as a bug.
    """
    if site_uid not in wide.columns:
        return sums / counts.where(counts > 0)
    own = wide[site_uid]
    n = counts - own.notna()
    return (sums - own.fillna(0.0)) / n.where(n > 0)


def _neighbor_block(site_uid, graph, areas, wide, sums, counts, spine, variant, select="area"):
    """(date-keyed frame, static scalar dict) carrying the variant's FULL column set for one site.

    Every column in _NBR_COLS[variant] is emitted on every path, NaN-filled where a direction has no monitored neighbor. Omitting them instead leaves the pool ragged, and on a slice where NO site has a neighbor the columns are absent from the pool entirely -- compare_masks then filters every arm down to `base` and the run logs a winner with a 0.0000 delta and no error anywhere.
    """
    best_up, best_down, n_up, n_down = _best_neighbors(site_uid, graph, areas, select)
    baseline = _except_this_mean(wide, sums, counts, site_uid)
    own = _area(areas, site_uid)
    nan_col = pd.Series(np.nan, index=spine)
    others = {u for u in (site_uid, best_up, best_down) if u is not None}

    def anom(uid, k):
        """The neighbor's deviation from the statewide daily mean, shifted `k` days. NaN column if absent."""
        if uid is None or uid not in wide.columns:
            return nan_col
        return (wide[uid] - baseline).asfreq("D").shift(k)

    def level(uid):
        """The neighbor's raw LEVEL at lag 1 -- the only column that can move the between-site channel, since an anomaly is mean-zero per site by construction."""
        return wide[uid].asfreq("D").shift(1) if uid is not None and uid in wide.columns else nan_col

    def ratio(uid):
        a = _area(areas, uid)
        return float(np.log10(a / own)) if np.isfinite(a) and np.isfinite(own) else np.nan

    def dist(uid, is_up):
        """Metres to `uid`. Edge direction is child -> parent, so an upstream neighbor is the CHILD of this site and a downstream one is its parent."""
        if uid is None:
            return np.nan
        return _edge_dist_m(graph, uid, site_uid) if is_up else _edge_dist_m(graph, site_uid, uid)

    cols, stats = {}, {}
    # startswith, not ==: every both* variant emits the _up/_down column set and differs only in HOW the two neighbors are chosen (see _VARIANT_RULE). Matched exactly, `both_dist` fell through to the single-neighbor branch and emitted names no mask asked for -- caught loudly by the column assertion in `masks` rather than silently collapsing every arm to base, which is what that assertion exists for.
    if variant.startswith("both"):
        for uid, is_up, sfx in ((best_up, True, "_up"), (best_down, False, "_down")):
            two = _two_hop(site_uid, uid, is_up, graph, areas, others, select)
            for k in _NBR_LAGS:
                cols[f"nbr_anom_lag{k}{sfx}"] = anom(uid, k)
            cols[f"nbr_level_lag1{sfx}"] = level(uid)
            cols[f"nbr2_anom_lag1{sfx}"] = anom(two, 1)
            stats[f"distance{sfx}"] = dist(uid, is_up)
            stats[f"nbr_area_ratio{sfx}"] = ratio(uid)
        stats["nbr_n_up"], stats["nbr_n_down"] = float(n_up), float(n_down)
    else:
        best, is_up = _best_either(site_uid, best_up, best_down, areas)
        two = _two_hop(site_uid, best, is_up, graph, areas, others, select)
        for k in _NBR_LAGS:
            cols[f"nbr_anom_lag{k}"] = anom(best, k)
        cols["nbr_level_lag1"] = level(best)
        cols["nbr2_anom_lag1"] = anom(two, 1)
        d = dist(best, is_up)
        stats["nbr_n_neighbors"] = float(n_up + n_down)
        stats["nbr_area_ratio"] = ratio(best)
        # 'signed' folds direction into the sign and drops the flag; 'flag' keeps magnitude and flag apart. The one difference between exps 34 and 35.
        stats["dist_to_neighbor"] = (-d if is_up else d) if variant == "signed" else d
        if variant == "flag":
            stats["neighbor_up"] = float(is_up) if best is not None else np.nan

    return pd.concat(cols, axis=1).rename_axis("date").reset_index(), stats


def _neighbor_ablation(task, variant):
    """The shipped recipe plus reach-graph neighbor features, ablated one block at a time.

    `variant` selects the ENCODING under test and nothing else -- the base arm's column list, the pool rows and the fold assignment are identical across all three, so their delta_vs_base tables live on one scale and each experiment's `base` arm must score identically. See _NBR_COLS for what each emits.

    Arms isolate three separable questions: does the neighbor's reading help at all (nbr_lagged), how much of that is lost by making it causal (nbr_concurrent vs nbr_lagged), and is it the reading that helps or merely KNOWING a neighbor exists (nbr_coverage, which carries only the static scalars). nbr_level is the one arm aimed at the between-site channel.

    Masks are a callable of the pool for the same reason as exp 32: the baseline is whatever the shipped recipe currently emits, so deriving base as (pool columns - neighbor columns) cannot drift when recipes.py changes.
    """
    rule = _VARIANT_RULE[variant]
    select = rule["select"]
    shipped = recipe_maker(task, window=1)
    graph = get_basin_graph(immediate_only=rule["immediate"])
    areas = {s: get_basin_area(s) for s in graph.nodes}
    wide = _state_daily_wide()
    sums, counts = _anom_baseline(wide)
    spine = pd.date_range(wide.index.min(), wide.index.max(), freq="D")

    def wide_recipe(site_uid):
        df = shipped(site_uid)
        frame, stats = _neighbor_block(site_uid, graph, areas, wide, sums, counts, spine, variant, select)
        df = merge_on_date([df, frame], spine=pd.DatetimeIndex(df["date"]))
        for k, v in stats.items():
            df[k] = v  # static per site -> broadcast
        return df

    def masks(pool):
        spec = _NBR_COLS[variant]
        missing = [c for block in spec.values() for c in block if c not in pool.columns]
        if missing:  # every arm would silently collapse to `base` -- see _neighbor_block
            raise RuntimeError(f"exp neighbor[{variant}]: {len(missing)} column(s) absent from the pool: {missing}")
        drop = set(_STRUCTURAL) | {"nitrate_con", "violation"}
        cols = [c for c in pool.columns if c not in drop]
        # Classify by EXACT membership, never startswith: the _up/_down suffixes make prefix matching wrong outright (nbr_n_up would land in the nbr_anom block), and nbr_n_neighbors / nbr_area_ratio would fall into nbr_anom* and silence the coverage-vs-reading comparison.
        blk = {k: [c for c in cols if c in set(v)] for k, v in spec.items()}
        nbr = {c for v in blk.values() for c in v}
        base = [c for c in cols if c not in nbr]
        out = {
            "base": base,
            "nbr_lagged": base + blk["lagged"],
            "nbr_concurrent": base + blk["concurrent"],
            "nbr_level": base + blk["level"],
            "nbr_2hop": base + blk["lagged"] + blk["twohop"],
            "nbr_coverage": base + blk["cover"],
            "nbr_all": base + sorted(nbr),
            # The whole block MINUS same-day -- i.e. the largest column set a shipped recipe could plausibly carry, which no other arm measures: nbr_all includes lag0, and nbr_lagged drops level/twohop/cover with it. Against nbr_all and nbr_concurrent it completes the decomposition: total effect, same-day-only effect, and what survives without same-day.
            #
            # Named for its contents, not for a claim about them. lag0 is present-time rather than future-time, so it is not acausal; what makes it questionable is that its availability depends on the deployment MODE -- unavailable to a forecast, available to a nowcast off a live donor feed -- and on that donor reporting with no latency. It is also contemporaneous with the target, so it can be carried by one storm falling on both basins rather than by propagation down the reach.
            "nbr_all_no_lag0": base + sorted(nbr - set(blk["concurrent"])),
        }
        if variant.startswith("both"):
            # 44% upstream vs 80% downstream coverage on this cohort: without these two, 'both' beating the single-neighbor variants could be nothing but the downstream gauge under a new name.
            out["nbr_lagged_up_only"] = base + [c for c in blk["lagged"] if c.endswith("_up")]
            out["nbr_lagged_down_only"] = base + [c for c in blk["lagged"] if c.endswith("_down")]
        return out

    return MaskedSweep(wide=wide_recipe, masks=masks, base_cols=lambda pool: masks(pool)["base"])


# Module-level wrappers rather than a factory: every registration in this file reads `experiment(...)(_bare_name)`, and a wrapper keeps that shape while giving each variant a distinct name in a traceback.
def _nbr_both(task):
    return _neighbor_ablation(task, "both")


def _nbr_flag(task):
    return _neighbor_ablation(task, "flag")


def _nbr_signed(task):
    return _neighbor_ablation(task, "signed")


experiment(id="33", question=_Q_NEIGHBOR_BOTH, task="reg", kind="masked")(_nbr_both)
experiment(id="33c", question=_Q_NEIGHBOR_BOTH, task="clf", kind="masked")(_nbr_both)
experiment(id="34", question=_Q_NEIGHBOR_FLAG, task="reg", kind="masked")(_nbr_flag)
experiment(id="34c", question=_Q_NEIGHBOR_FLAG, task="clf", kind="masked")(_nbr_flag)
experiment(id="35", question=_Q_NEIGHBOR_SIGNED, task="reg", kind="masked")(_nbr_signed)
experiment(id="35c", question=_Q_NEIGHBOR_SIGNED, task="clf", kind="masked")(_nbr_signed)


# ── exp 36 / 36c: the FULL containment graph, neighbor picked by distance ─────────────────────────
# Same 16 columns, same 9 arms, same pool rows and folds as 33/33c. Two things change together, and they have to: the graph, and the rule that picks among its candidates.
#
# WHY BOTH. 33 walks get_basin_graph(immediate_only=True), which keeps only each child's smallest enclosing parent -- so every site has exactly ONE successor and a downstream selection rule has nothing to choose between. Swapping the rule alone moved 0 of 90 downstream picks. The full graph restores the choice (up to 7 successors, 22 predecessors), which is what makes "nearest" a question in both directions rather than upstream-only.
#
# Measured contrast against 33: 23 of 116 sites (25% of rows) resolve to a different gauge -- 16 upstream, 7 downstream -- and coverage rises from 51/85 up/down sites to 55/91, because the full graph reaches sites whose only connection is to a non-immediate parent.
#
# READ IT AS A TWO-VARIABLE CHANGE. A gap between 33 and 36 says "full graph + distance beats immediate graph + drainage scale", not "distance beats area". Decomposing it needs a full-graph + area arm; on the full graph the two rules already agree on 115 of 116 downstream picks, because the smallest enclosing basin is nearly always also the nearest one, so that decomposition is mostly an upstream question (13 of 116 differ).
_Q_NEIGHBOR_DIST = (
    "On the FULL containment graph, does picking each direction's reach neighbor by distance -- nearest "
    "along the network -- beat exp 33's immediate-graph, drainage-scale pick, holding every column, arm "
    "and fold identical?"
)


def _nbr_both_dist(task):
    return _neighbor_ablation(task, "both_dist")


experiment(id="36", question=_Q_NEIGHBOR_DIST, task="reg", kind="masked")(_nbr_both_dist)
experiment(id="36c", question=_Q_NEIGHBOR_DIST, task="clf", kind="masked")(_nbr_both_dist)


# ── exp 37 / 37c: a basin-local nitrate pool, the donor's long-run LEVEL, and stream position ─────
# See notes/plan_exp37_basin_pool.md for the full case, the measured graph facts and the read order.
#
# WHAT 33-36c LEFT OUT. Every column those experiments added is either a deviation from the statewide daily mean -- mean-zero per site by construction, so it can only move the TIMING channel -- or a coverage scalar. `nbr_level_lag1` was the single column aimed at the between-site channel. exp 37 adds the other half: the donor's and the basin's long-run nitrate LEVEL, plus a basin-local replacement for the statewide pooled scalar. Judge it on lofo_between_r2 / lofo_between_rate_r2 FIRST; the manifest's REG between-channel add floor is 0.0258, which is large.
#
# THE POOL. `rest_of_basin` is the unweighted daily mean over every OTHER cohort site in the modelled site's connected component of the containment graph, always excluding the modelled site. Coverage is 102/116 (88%) with a median of 14 pool-mates -- identical to donor coverage, because a site has a pool-mate exactly when it has a donor, so the pool buys DEPTH and not reach. 8 sites sit in 2-site components where the pool IS the donor: there `nbr_avg_nitrate == rest_of_basin_nitrate_avg` and the anomaly is identically 0. Accepted, not worked around -- excluding the donor from its own baseline would trade a constant for a NaN on exactly those sites, and _except_this_mean already declines to exclude the donor for the same reason.
#
# CAUSALITY. The four quantities the request called "static" all read the future as literally specified: an all-time mean covers the evaluation window and a same-calendar-year mean peeks up to twelve months forward. Each ships in a causal form (expanding over years STRICTLY BEFORE this row's year; or the PREVIOUS calendar year) mirrored by a peeking `_pk` twin that exists only as a ceiling arm -- the same framing nbr_all / nbr_all_no_lag0 uses for lag0. Note what that costs: the causal "static" is not static, it is a stepwise series with one step per calendar year, and the true one-value-per-site version survives only as `_pk`.
#
# LEAKAGE EXPOSURE. LOFO groups are conflict components -- containment AND temporal overlap -- so they refine containment components, and 77 of 557 within-component site pairs (14%) land in different folds. Those pairs do not overlap in time, so no daily column can leak across them; only the long-run statics can, and only weakly. Not fixable inside this design; it belongs in the write-up.
#
# STREAM POSITION rides along in every arm but `base` and has nothing to do with the network -- see _NEW_COLS["stream"].
_Q_BASIN_POOL = (
    "Beyond the donor's day-to-day deviations, does a basin-local nitrate POOL and the donor's "
    "long-run LEVEL predict a site -- i.e. does the between-site channel move when the model is told "
    "how much nitrate its hydrological neighbourhood actually carries -- and how much of that survives "
    "being made causal?"
)

_BASIN_LAGS = (1, 3)  # matches _CROSS_SITE_LAGS_*, so the local pool is the statewide scalar's like-for-like

# The new block by drop unit, one source of truth: _basin_pool_block fills exactly these and `masks` classifies by exact membership, so a name cannot drift between the emitter and the arm it belongs to. Same contract as _NBR_COLS.
#
# `stream` is the odd one out -- it is island-legal (no donor, no pool, 116/116 coverage) and is here only because it is a static the recipe does not carry yet. It sits in every arm except `base`, which is exactly why `base_stream` exists: ride-along columns that no arm isolates are unmeasurable, and every network arm below is read against `base_stream` so the block cancels out of the comparisons that are about the network.
#
# WHICH stream column. NHDPlus carries two and they run opposite ways: StreamOrde is Strahler (1 = tiny headwater, rising downstream), StreamLeve is stream level (1 = mainstem, +1 per tributary step). They are only rho = -0.51 with each other. Both go in because Spearman(StreamOrde, log_basin_area) = 0.940 over this cohort -- Strahler order is very nearly a monotone recoding of a column the base already holds, exactly the collinearity notes/future-work-report.md R10 predicted and left unverified, while StreamLeve is only -0.53 and encodes network POSITION rather than size. Read the two permutation importances separately.
_NEW_COLS = {
    "stream": ["stream_order", "stream_level"],
    "basin_dyn": [f"rest_of_basin_nitrate_lag{k}" for k in _BASIN_LAGS],
    "basin_stat": ["rest_of_basin_nitrate_avg", "rest_of_basin_nitrate_avg_yearly"],
    "nbr_stat": ["nbr_avg_nitrate", "nbr_avg_nitrate_yearly"],
    "nbr_anom_stat": ["nbr_avg_nitrate_anom", "nbr_avg_nitrate_anom_yearly"],
    # the peeking twins of the six above -- ceiling arms only, never a shipping candidate
    "peek": [
        "rest_of_basin_nitrate_avg_pk",
        "rest_of_basin_nitrate_avg_yearly_pk",
        "nbr_avg_nitrate_pk",
        "nbr_avg_nitrate_yearly_pk",
        "nbr_avg_nitrate_anom_pk",
        "nbr_avg_nitrate_anom_yearly_pk",
    ],
}

# The one-hop donor exp 37 reads: ONE neighbour, either direction, ranked on closeness in drainage scale -- i.e. exp 34's encoding, so 37's `base` arm is the same columns, rows and folds as 34/34c's and the two must score identically.
_BASIN_POOL_VARIANT = "flag"


@lru_cache(maxsize=1)
def _flowline_vaa():
    """COMID -> (StreamOrde, StreamLeve) for the whole flowlines layer.

    Column-pruned: pyarrow reads three columns out of the 130-column, 276 MB GeoParquet, so the geometry and the ~120 unused VAA fields never materialize. access.get_flowlines() would read all of it. Deduplicated on COMID (the layer carries 2 duplicate rows out of 309,682).
    """
    vaa = pd.read_parquet(_PROC_MAP / "iowa_flowlines.parquet", columns=["COMID", "StreamOrde", "StreamLeve"])
    return vaa.drop_duplicates("COMID").set_index("COMID")


@lru_cache(maxsize=None)
def _stream_position(site_uid):
    """{stream_order, stream_level} for the reach the sensor sits on; NaN pair for anything unresolvable.

    COMID via the preferred basin, the same route get_comid_attributes takes for tot_bfi / tot_contact, so a pin gets it from its snapped basin with no extra work. Never raises -- a COMID outside the layer degrades to NaN features, matching get_comid_attributes.
    """
    nan = {"stream_order": np.nan, "stream_level": np.nan}
    try:
        comid = _basin_comid(get_basin(site_uid))
    except (FileNotFoundError, KeyError):
        return nan
    vaa = _flowline_vaa()
    if comid is None or comid not in vaa.index:
        return nan
    row = vaa.loc[comid]
    return {"stream_order": float(row["StreamOrde"]), "stream_level": float(row["StreamLeve"])}


@lru_cache(maxsize=None)
def _components(immediate=True):
    """(comp_of, sums, counts) for the containment components -- the basin pool's O(1)-per-site setup.

    comp_of maps a site to its component id; sums/counts are that component's row sum and non-null count over _state_daily_wide, so a member's pool is (sums - own) / (counts - own.notna()), the same subtract-your-own-column trick _anom_baseline uses statewide. Memoized on `immediate` alone because the components are cohort-level; the returned objects are SHARED, treat as read-only.

    A singleton component needs no special case: subtracting the one member leaves count 0 everywhere, which is NaN, which is the honest answer.
    """
    graph = get_basin_graph(immediate_only=immediate)
    wide = _state_daily_wide()
    comp_of, sums, counts = {}, {}, {}
    for cid, comp in enumerate(nx.connected_components(graph.to_undirected())):
        members = [c for c in comp if c in wide.columns]
        if not members:
            continue
        block = wide[members]
        sums[cid], counts[cid] = block.sum(axis=1), block.notna().sum(axis=1)
        for m in members:
            comp_of[m] = cid
    return comp_of, sums, counts


def _basin_pool(site_uid, wide, comp_of, sums, counts):
    """Daily mean over the OTHER cohort sites in this site's containment component; all-NaN where it has none."""
    cid = comp_of.get(site_uid)
    if cid is None:
        return pd.Series(np.nan, index=wide.index)
    tot, n = sums[cid], counts[cid]
    if site_uid in wide.columns:
        own = wide[site_uid]
        tot, n = tot - own.fillna(0.0), n - own.notna()
    return tot / n.where(n > 0)


def _year_stats(s, years, stem):
    """(year-keyed frame of the three per-year columns, {f'{stem}_pk': all-time mean}) for one daily series.

    DAY-weighted throughout, never a mean of yearly means: `stem` is the mean over every observed day in the years strictly before this row's year, `{stem}_yearly` the mean over the previous calendar year, `{stem}_yearly_pk` the mean over THIS year (peeking), and `{stem}_pk` the all-time scalar (peeking). An all-NaN input gives all-NaN out, which is how a site with no pool or no donor gets its columns.

    `years` must be CONTIGUOUS: the two causal columns are a one-position shift, so a gap year missing from the index would shift by more than a year and silently mis-date every value after it.
    """
    v = s.dropna()
    g = v.groupby(v.index.year)
    tot = g.sum().reindex(years, fill_value=0.0)
    n = g.size().reindex(years, fill_value=0)
    per_year = tot / n.where(n > 0)
    cum_tot, cum_n = tot.cumsum().shift(1), n.cumsum().shift(1)
    frame = pd.DataFrame(
        {
            stem: cum_tot / cum_n.where(cum_n > 0),
            f"{stem}_yearly": per_year.shift(1),
            f"{stem}_yearly_pk": per_year,
        },
        index=pd.Index(years, name="year"),
    ).reset_index()
    return frame, {f"{stem}_pk": float(v.mean()) if len(v) else np.nan}


def _basin_pool_block(site_uid, best, wide, comp_of, sums, counts, years):
    """(date-keyed frame, year-keyed frame, static scalar dict) carrying the exp-37 block for one site.

    `best` is the single donor _best_either resolved, or None. Every column in _NEW_COLS is emitted on every path, NaN-filled where the site has no pool or no donor -- same reason as _neighbor_block: omitting them leaves the pool ragged, and on a slice where nobody has a neighbour they vanish entirely and every arm silently collapses to `base`.

    The anomaly is the donor MINUS the basin pool, reduced over time -- a LOCAL baseline, unlike nbr_anom_lag*, which deviates from the statewide mean. On a 2-site component the pool is the donor and this is identically 0.
    """
    pool = _basin_pool(site_uid, wide, comp_of, sums, counts)
    donor = wide[best] if best is not None and best in wide.columns else pd.Series(np.nan, index=wide.index)

    daily = pd.concat(
        {f"rest_of_basin_nitrate_lag{k}": pool.asfreq("D").shift(k) for k in _BASIN_LAGS}, axis=1
    ).rename_axis("date").reset_index()

    frames, stats = [], {}
    for series, stem in ((pool, "rest_of_basin_nitrate_avg"), (donor, "nbr_avg_nitrate"), (donor - pool, "nbr_avg_nitrate_anom")):
        frame, stat = _year_stats(series, years, stem)
        frames.append(frame)
        stats.update(stat)
    yearly = frames[0]
    for f in frames[1:]:
        yearly = yearly.merge(f, on="year", how="outer")
    return daily, yearly, stats


def _basin_pool_ablation(task):
    """The shipped recipe plus exp 34's donor block, the basin-pool / long-run-level block, and stream position -- ablated one unit at a time.

    Row-identical to _neighbor_ablation(task, 'flag'): the same shipped recipe on the same spine, with columns added and none removed, so 37's `base` arm must score exactly what 34/34c's does. Run them in one invocation and check it -- it is a free correctness test on the merge.

    Masks are a callable of the pool for the same reason as exps 32 and 33: the baseline is whatever the shipped recipe currently emits, so deriving base as (pool columns - managed columns) cannot drift when recipes.py changes.
    """
    rule = _VARIANT_RULE[_BASIN_POOL_VARIANT]
    select = rule["select"]
    shipped = recipe_maker(task, window=1)
    graph = get_basin_graph(immediate_only=rule["immediate"])
    areas = {s: get_basin_area(s) for s in graph.nodes}
    wide = _state_daily_wide()
    sums, counts = _anom_baseline(wide)
    spine = pd.date_range(wide.index.min(), wide.index.max(), freq="D")
    years = range(int(spine.year.min()), int(spine.year.max()) + 1)  # contiguous -- see _year_stats
    comp_of, comp_sums, comp_counts = _components(rule["immediate"])

    def wide_recipe(site_uid):
        df = shipped(site_uid)
        nbr_frame, nbr_stats = _neighbor_block(
            site_uid, graph, areas, wide, sums, counts, spine, _BASIN_POOL_VARIANT, select
        )
        # The same donor _neighbor_block picked internally, resolved again rather than returned: exps 33-36c depend on that function byte-for-byte, and re-running two cheap graph lookups is a better trade than changing its signature.
        best_up, best_down, _, _ = _best_neighbors(site_uid, graph, areas, select)
        best, _is_up = _best_either(site_uid, best_up, best_down, areas)
        daily, yearly, pool_stats = _basin_pool_block(site_uid, best, wide, comp_of, comp_sums, comp_counts, years)
        df = merge_on_date([df, nbr_frame, daily, yearly], spine=pd.DatetimeIndex(df["date"]))
        for k, v in {**nbr_stats, **pool_stats, **_stream_position(site_uid)}.items():
            df[k] = v  # static per site -> broadcast
        return df

    def masks(pool):
        spec = _NBR_COLS[_BASIN_POOL_VARIANT]
        missing = [c for block in (*spec.values(), *_NEW_COLS.values()) for c in block if c not in pool.columns]
        if missing:  # every arm would silently collapse to `base` -- see _neighbor_block
            raise RuntimeError(f"exp basin_pool[{task}]: {len(missing)} column(s) absent from the pool: {missing}")
        drop = set(_STRUCTURAL) | {"nitrate_con", "violation"}
        cols = [c for c in pool.columns if c not in drop]
        old = {k: [c for c in cols if c in set(v)] for k, v in spec.items()}
        new = {k: [c for c in cols if c in set(v)] for k, v in _NEW_COLS.items()}
        managed = {c for v in (*old.values(), *new.values()) for c in v}
        base = [c for c in cols if c not in managed]

        stream = new["stream"]
        cover = old["cover"]
        nolag0 = sorted({c for v in old.values() for c in v} - set(old["concurrent"]))
        bd = new["basin_dyn"]
        stat = new["basin_stat"] + new["nbr_stat"] + new["nbr_anom_stat"]
        peek = new["peek"]
        # COVERAGE-CONFOUNDED PAIR. Measured over the cohort, the expanding columns carry a value on 68% of pooled rows (86 sites) against the yearly ones' 49% (71 sites): donor records often do not overlap the target's rows in calendar time at all -- USGS-05420400 is gauged 2021-2026 behind a donor that ran 2014-2018 -- so the yearly column is NaN where the expanding one still reaches back. 16 sites are in exactly that state. The gap between these two arms is therefore part resolution and part coverage; check the 71 sites where both are populated before reading it as either.
        alltime = [c for c in stat if not c.endswith("_yearly")]
        yearly = [c for c in stat if c.endswith("_yearly")]
        # Prefix-matched, unlike the neighbour blocks: these two names are unambiguous stems with no suffixed relatives to swallow, so matching them beats restating recipes._CROSS_SITE_LAGS_* / _CROSS_SITE_ROLL_* and letting the copy drift.
        statewide = [c for c in base if c.startswith("rest_of_state_nitrate_lag")]
        rolling = [c for c in base if c.startswith("roll_n_avg_except_this")]
        without = lambda gone: [c for c in base if c not in set(gone)]

        # STREAM is in every arm but `base`, so it cancels out of every comparison between two arms below and only `base_stream` vs `base` prices it. Read the network arms against base_stream.
        return {
            "base": base,
            "base_stream": base + stream,
            "nbr_coverage": base + stream + cover,
            "nbr_all_no_lag0": base + stream + nolag0,
            "nbr_coverage_new": base + stream + cover + bd + stat,
            "nbr_all_no_lag0_new": base + stream + nolag0 + bd + stat,
            "new_only": base + stream + bd + stat,
            # No column keyed to a donor's DAILY reading -- but the base still carries the statewide lags and rollings, which do need live telemetry, so this is 'no donor feed', NOT 'no live feed'.
            "new_static_only": base + stream + stat,
            # The strict version, and the only arm that REMOVES base columns: read its delta against `base`, not against its neighbours in this table.
            "new_static_no_livefeed": without(statewide + rolling) + stream + stat,
            "basin_lags_add": base + stream + bd,
            "basin_lags_swap": without(statewide) + stream + bd,
            "new_alltime_only": base + stream + alltime,
            "new_yearly_only": base + stream + yearly,
            "new_only_pk": base + stream + bd + peek,
            "nbr_all_no_lag0_new_pk": base + stream + nolag0 + bd + peek,
        }

    return MaskedSweep(wide=wide_recipe, masks=masks, base_cols=lambda pool: masks(pool)["base"])


experiment(id="37", question=_Q_BASIN_POOL, task="reg", kind="masked")(_basin_pool_ablation)
experiment(id="37c", question=_Q_BASIN_POOL, task="clf", kind="masked")(_basin_pool_ablation)
