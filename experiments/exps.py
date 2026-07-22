"""Experiment declarations. Each is a QUESTION + a dict of named recipes to compare, for ONE task
(regression or classification -- a run is either, never both).

Add one with @experiment(id, question, task='reg'|'clf'): the decorated function takes the task and
returns {recipe_name: recipe_fn}. The engine (run_exp.py) resolves sites, runs the comparison, and
logs Question -> Winner -> Results to logs/experiments.jsonl. The FIRST recipe is the baseline;
deltas are reported against it. To test the same recipes under both tasks, register two records (a
plain id and a `c` id) sharing one build fn -- see exp 10 / 24 below.

Recipes here are self-contained (they don't route through src.features.recipes) so an experiment is a
readable, standalone record of exactly what was compared. Each recipe is site_uid -> DataFrame that
INCLUDES the task's target column (nitrate_con for reg, violation for clf).

    python run_exp.py --med 10 24c     # run these in med mode
    python run_exp.py --summary        # Question / Winner / Results for the latest of each
"""

from typing import cast

import pandas as pd

from run_exp import experiment
from src.features.features import (
    agg_crops,
    agg_surplus,
    agg_weather,
    agg_weather_w_lag,
    daily_nitrate,
    doy_climatology_pure_signal,
    nitrate_daily_rolling,
    nitrate_violations_rolling,
    rolling_precip,
    site_static,
)
from src.features.transformers import flatten_buckets, merge_on_date
from src.features.recipes import CLF_VEL, CLF_LAM, recipe_maker  # shipped CLF geometry + the shipped recipe
from src.data.access import _COMID_ATTR_COLS  # COMID static columns, in lockstep with the accessor


# ── shared building blocks ────────────────────────────────────────────────────
def _target(site_uid, task, window=1, min_obs=1):
    """The task's label column, named as the engine expects (nitrate_con / violation). The cast is
    only to satisfy the type checker -- the target builders return a Series at runtime, but pandas
    stubs type the upstream resample().agg() as Series|DataFrame, so .rename(<str>) looks ambiguous."""
    if task == "reg":
        s = nitrate_daily_rolling(site_uid=site_uid, window=window, min_obs=min_obs)
        return cast(pd.Series, s).rename("nitrate_con")
    s = nitrate_violations_rolling(site_uid=site_uid, window=window, min_obs=min_obs)
    return cast(pd.Series, s).rename("violation")


def _covariates(site_uid, edges, vel, lam):
    """The shipped spatial covariate block: travel-time-lagged weather + exp-decay crops + surplus,
    at the given bucket geometry. Returns a list of date-keyed frames."""
    e = list(edges)
    return [
        flatten_buckets(agg_weather_w_lag(site_uid=site_uid, edges=e, exp=False, water_velocity=vel)),
        flatten_buckets(agg_crops(site_uid=site_uid, edges=e, lam=lam, exp=True)),
        flatten_buckets(agg_surplus(site_uid=site_uid, edges=e, lam=lam, exp=True)),
    ]


def _assemble(site_uid, task, frames, static_drop=frozenset(), add_static=True):
    """target + the given covariate frames + the calendar signal, merged on the daily spine, with the
    static site descriptors broadcast in (minus any in `static_drop`). The spine dates only feed doy
    and the merge timeline -- no nitrate values leak into the features."""
    n = daily_nitrate(site_uid=site_uid)
    df = merge_on_date([_target(site_uid, task), *frames, doy_climatology_pure_signal(n)], spine=n.index)
    if add_static:
        for k, v in site_static(site_uid=site_uid).items():
            if k not in static_drop:
                df[k] = v
    return df


# ── exp 6: does lagged weather help? (HISTORICAL -- kept for the record) ───────────────────────────
# Original finding: lagged weather did essentially nothing for regression (yet fuel_moisture_1000h
# dominated importance -- it's a wetness proxy, chased later by exp 11/17). Preserved as a rerunnable
# record, not because the answer is expected to change.
_EXP6_LAGS = (1, 3, 7, 14, 30)


def _basin_weather_block(site_uid, lag):
    """Whole-basin daily weather (edges=[] -> one bucket), optionally shifted back `lag` days -- the
    same daily value lagged, exactly as exp 6 defined it (NOT an antecedent rolling sum; that's
    rolling_precip). Columns suffixed _now / _lagK."""
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
# A slimmed re-sweep of the original 27-cell grid. The shipped geometry came from exp 10 on the OLD
# feature set; now that static basin covariates (COMID/AgTile) exist, the optimum may have moved.
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
# The modernized exp 8 static ablation, seeded with today's COMID + AgTile features. Geometry is held
# FIXED (single 50km edge) so the ONLY thing varying across arms is which static family is included.
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
# Riparian 2km inner bucket (exp 18, +0.039 lofo_prauc) and antecedent rolling-precip (exp 17,
# +0.017) -- do they add, or overlap? `combined` is the shipped recipe_CLF. Geometry vel/lam = the
# shipped CLF values; only the bucket edges and the precip block vary.
@experiment(
    id="19c",
    question="Do the two CLF wins -- a 2km riparian inner bucket and antecedent rolling-precip -- "
    "stack additively, or do they overlap (is combined ~= base + both individual lifts)?",
    task="clf",
)
def clf_design(task):
    def make(edges, roll):
        def recipe(site_uid):
            frames = _covariates(site_uid, edges, CLF_VEL, CLF_LAM)
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
# cat_tiles92/bfi/contact are per-reach LOCAL values (near-unique per site). In the full retrain they
# showed high gain but ~0 permutation importance -- classic site-fingerprint behaviour that inflates
# in-sample fit but hurts transfer. This tests removing them from the ACTUAL shipped recipe.
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
