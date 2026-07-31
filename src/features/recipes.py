from src.features.features import (
    agg_crops,
    agg_crops_normalized,
    agg_surplus,
    agg_surplus_normalized,
    agg_weather,
    daily_nitrate,
    nitrate_avg_except_this,
    neighbor_nitrate,
    rolling_nitrate_avg_except_this,
    doy_climatology_pure_signal,
    site_static,
    longrun_composition,
    nitrate_daily_rolling,
    nitrate_violations_rolling,
)
from src.features.transformers import flatten_buckets, merge_on_date, tag_values
from functools import lru_cache, wraps
import pandas as pd

# ── shipped-model feature curation: shared ────────────────────────────────────
# The RECIPE (not the accessor) decides which columns the shipped model consumes. Every drop below is justified by permutation importance ~= 0 across all trained models plus the recorded ablations -- the accessors in features.py still expose the full data for experiments.
#
# A global read INSIDE _agg_features_REG / _agg_features_CLF must also appear in that function's _cache_by_site geometry tuple. Left out, editing it mid-process serves frames built under the old value: wrong numbers, no error.
_DEAD_SURPLUS_PREFIX = (
    "total_kg_N"  # high gain, ~0 perm both tasks (surplus_kgha kept); neither task keeps it, so it stays shared
)

# Long-run basin composition (features.longrun_composition): the per-year crop/surplus shares reduced over ALL years, static per site and broadcast alongside site_static. Which reductions each task takes is a measured split, from exps 32/32c on the 122-site cohort -- see _LONGRUN_STATS_REG / _LONGRUN_STATS_CLF in the sections below.
#
# rotation_index is excluded from both. It is dead on its own terms -- +0.80 correlated with pct_corn_sd across the cohort, ~2 percentage points in magnitude, and differencing the BASIN MEAN cancels the field-scale rotation it is meant to catch. features.longrun_cell_rotation is the per-cell definition that survives that averaging, but it postdates exp 32 and is untested as a model feature.
#
# Read the mean block as DENOISING rather than new information: averaging ~18 years of a share the model already sees per year is nearly collinear with the per-year column, which is exactly the weaker outcome notes/plan_long_run_composition.md predicted for a mean-only win.


# ── REG ──────────────────────────
REG_EDGES, REG_LAM = (8_000, 30_000), 20_000  # probed in exp 30, run it again to see

_LIVE_WEATHER_PREFIX_REG = (
    "fuel_moisture_1000h",  # only weather var with held-out signal; the rest are perm-dead in every run
    # "energy_release",
    # "precip_in_1d",
    # "burning_index",
    # "max_rel_humidity",
    # "evapotranspiration",
)
_SKIP_STATIC_KEYWORDS_REG = ("cat_tiles92",)
_LONGRUN_STATS_REG = (
    "mean",
)  # mean only: +0.0420 lofo_r2 with between_r2 0.044 -> 0.166, while the sd block COST -0.0149 headline and -0.0699 between_r2
_CROSS_SITE_LAGS_REG = (1, 3)  # rest_of_state_nitrate_lag1/3
_CROSS_SITE_ROLL_REG = (7, 14, 60)  # roll_n_avg_except_this{7,14,60}d

# NETWORK only -- the reach-graph donor block. 'flag' for REG: exps 33/34/35 separated by less than the 0.0085 headline floor, and one neighbour costs half the columns of two.
_NBR_LAGS_REG = (1, 3)
_NBR_SELECT_REG, _NBR_IMMEDIATE_REG, _NBR_ENCODING_REG = "area", True, "flag"


# ── CLF ──────────────────────────
CLF_EDGES, CLF_LAM = (5_000, 20_000), 20_000  # probed in exp 30c, run it again to see

_LIVE_WEATHER_PREFIX_CLF = (
    "fuel_moisture_1000h",  # only weather var with held-out signal; the rest are perm-dead in every run
    # "energy_release",
    # "precip_in_1d",
    # "burning_index",
    # "max_rel_humidity",
    # "evapotranspiration",
)
_SKIP_STATIC_KEYWORDS_CLF = ("cat_tiles92",)
_LONGRUN_STATS_CLF = ("mean", "sd")  # mean +0.0139, sd a further +0.0074 with the best between_rate_r2 of the four arms
_CROSS_SITE_LAGS_CLF = (1, 3)  # rest_of_state_nitrate_lag1/3
_CROSS_SITE_ROLL_CLF = (7, 14, 60)  # roll_n_avg_except_this{7,14,60}d

# NETWORK only -- see the REG block. 'both' for CLF: exp 33c beat 34c by +0.0175 lofo_prauc, ~5x the 0.0037 headline floor, so the two directions are worth their own columns here even though REG cannot tell them apart.
_NBR_LAGS_CLF = (1, 3)
_NBR_SELECT_CLF, _NBR_IMMEDIATE_CLF, _NBR_ENCODING_CLF = "area", True, "both"

# WHY NO lag0. features.neighbor_nitrate will emit a same-day column if asked; these tuples are where that is declined. A donor's day-T reading exists for a NOWCAST (estimate today at an ungauged pin off a live feed) but not for a FORECAST, and which of the two ships has not been decided -- see notes/model-split-report.md. It is also contemporaneous with the target, so it can be carried by one storm falling on both basins rather than by transport down the reach, which makes it hostage to donor telemetry arriving with no latency. Adding 0 back is a one-tuple edit; no feature code moves.


def _keep_live_weather(wb, task):
    """Keep the weather prefixes `task` declares live, plus `date`.

    Selected from _LIVE_WEATHER_PREFIX_CLF / _LIVE_WEATHER_PREFIX_REG, so the two tasks can carry different weather sets. A prefix commented out of the tuple leaves the model entirely rather than being downweighted, and the tuple is in the caller's _cache_by_site key so an edit invalidates rather than serving stale frames.
    """
    if task != "clf" and task != "reg":
        raise ValueError("Task must be one of 'clf' or 'reg'!")

    live = _LIVE_WEATHER_PREFIX_CLF if task == "clf" else _LIVE_WEATHER_PREFIX_REG
    return wb[[c for c in wb.columns if c == "date" or c.startswith(live)]]


def _drop_dead_surplus(sb):
    """Drop total_kg_N (dead); keep surplus_kgha."""
    return sb[[c for c in sb.columns if not c.startswith(_DEAD_SURPLUS_PREFIX)]]


def _site_kwargs(site):
    """Map a site identifier -- a site_uid (str) or a SiteData -- to the keyword the generalized data.features/data.transforms helpers expect (site_uid= vs site_data=)."""
    return {"site_uid": site} if isinstance(site, str) else {"site_data": site}


def _site_uid_of(site):
    """The site_uid label used for cross-site exclusion: the string itself, or a SiteData's .site_uid. A virtual site's uid is absent from the state, so nothing is excluded and the neighbour features become the full rest-of-state average."""
    return site if isinstance(site, str) else site.site_uid


# ── uid-memoized block dispatch: cache on a hashable uid, run a SiteData (unhashable) fresh ──
@lru_cache(maxsize=None)
def _by_uid(compute, site_uid, args):
    """Run `compute` under a uid-keyed memo; `args` must be hashable, so callers pass tuples rather than lists."""
    return compute(site_uid, *args)


def _site_cached(compute, site, *args):
    """Memoize `compute(site, *args)` on a hashable uid string; run fresh for a SiteData. `edges` must be a tuple (not a list) so the args are hashable on the cached path. Replaces the per-block _compute / @lru_cache _cached / isinstance-dispatch trios."""
    return _by_uid(compute, site, args) if isinstance(site, str) else compute(site, *args)


def _cache_by_site(geometry):
    """Decorator: memoize a SPINE-INDEPENDENT `site -> [frames]` builder.

    The aggregations depend only on (site, geometry), so a window x min_obs sweep reuses one aggregation pass per site (~1.6 s each) instead of rebuilding it per window; only the calendar block depends on the spine, which is why _best_features_* keeps that line outside the cached half. `geometry` is a zero-arg callable evaluated at CALL time and folded into the key, so reassigning REG_EDGES / REG_LAM mid-process invalidates the cache rather than silently serving frames built at the old bucket edges -- a failure with wrong numbers and no error. A SiteData is unhashable and bypasses the cache entirely, which is correct: a virtual site is built once for one pin. The wrapper re-exports cache_clear / cache_info so a sweep can drop the ~50 MB per geometry it retains.
    """

    def deco(fn):
        """Wrap `fn` so a uid+geometry key hits the memo and a SiteData bypasses it."""

        @lru_cache(maxsize=None)
        def cached(uid, _geom):
            """The memoized inner call, keyed on (uid, geometry)."""
            return fn(uid)

        @wraps(fn)
        def wrapper(site):
            """Route a site_uid through the memo, a SiteData straight past it."""
            return cached(site, geometry()) if isinstance(site, str) else fn(site)

        wrapper.cache_clear = cached.cache_clear
        wrapper.cache_info = cached.cache_info
        return wrapper

    return deco


def _cross_site_nitrate_compute(site, lags, roll):
    """The cross-site nitrate neighbour features -- the given `lags`, plus rolling averages over the given `roll` windows -- for a site_uid OR SiteData. `site` is used only as the exclusion label (via _site_uid_of)."""
    uid = _site_uid_of(site)
    lagged_avgs = tuple(nitrate_avg_except_this(uid, shift=k) for k in lags)
    rolling_avg_not_this = rolling_nitrate_avg_except_this(uid, windows=roll)
    return lagged_avgs, rolling_avg_not_this


def _cross_site_nitrate(site, task):
    """Window- and geometry-independent cross-site nitrate features, memoized per (site_uid, lags, roll).

    Both tuples are passed EXPLICITLY rather than read from the globals inside the compute: keyed on site alone, editing _CROSS_SITE_LAGS_* / _CROSS_SITE_ROLL_* would keep serving whatever was cached on first use -- silently, with no error.
    """
    if task != "clf" and task != "reg":
        raise ValueError("Task must be one of 'clf' or 'reg'!")

    clf = task == "clf"
    lags = _CROSS_SITE_LAGS_CLF if clf else _CROSS_SITE_LAGS_REG
    roll = _CROSS_SITE_ROLL_CLF if clf else _CROSS_SITE_ROLL_REG
    return _site_cached(_cross_site_nitrate_compute, site, lags, roll)


def _neighbor_nitrate_compute(site, lags, select, immediate, encoding):
    """The reach-graph donor block for a site_uid OR SiteData -> (date-keyed frame, static scalars). `site` is used only as the graph key (via _site_uid_of)."""
    return neighbor_nitrate(_site_uid_of(site), lags=lags, select=select, immediate=immediate, encoding=encoding)


def _neighbor_nitrate(site, task):
    """The NETWORK-only donor block, memoized per (site_uid, lags, select, immediate, encoding).

    Every parameter is passed EXPLICITLY rather than read from the globals inside the compute, for the same reason as _cross_site_nitrate: keyed on site alone, editing _NBR_* would keep serving whatever was cached on first use -- silently, with no error.

    A uid the graph does not carry (a virtual pin) still gets the full column set, all NaN. That is the honest answer -- the block is undefined without a resolvable donor -- and it keeps the frame shape stable so a trained model can reindex against it.
    """
    if task != "clf" and task != "reg":
        raise ValueError("Task must be one of 'clf' or 'reg'!")

    clf = task == "clf"
    lags = _NBR_LAGS_CLF if clf else _NBR_LAGS_REG
    select = _NBR_SELECT_CLF if clf else _NBR_SELECT_REG
    immediate = _NBR_IMMEDIATE_CLF if clf else _NBR_IMMEDIATE_REG
    encoding = _NBR_ENCODING_CLF if clf else _NBR_ENCODING_REG
    return _site_cached(_neighbor_nitrate_compute, site, lags, select, immediate, encoding)


@_cache_by_site(lambda: (REG_EDGES, REG_LAM, _CROSS_SITE_LAGS_REG, _CROSS_SITE_ROLL_REG, _LIVE_WEATHER_PREFIX_REG))
def _agg_features_REG(site):
    """The spine-INDEPENDENT half of the REG feature list: everything keyed on (site, geometry). Cached -- see _cache_by_site."""
    kw = _site_kwargs(site)
    KGHA = ["surplus_kgha"]

    wb = _keep_live_weather(flatten_buckets(agg_weather(**kw, edges=REG_EDGES, exp=False)), "reg")
    cb = flatten_buckets(agg_crops_normalized(**kw, edges=REG_EDGES))
    # dropped cb_exp due to ablation experiments on 07-29, see notebook
    # cb_exp = flatten_buckets(agg_crops(**kw, edges=REG_EDGES, lam=REG_LAM, exp=True))
    sb = _drop_dead_surplus(
        flatten_buckets(tag_values(agg_surplus_normalized(**kw, edges=REG_EDGES), "_norm", keep=KGHA))
    )
    sb_exp = _drop_dead_surplus(
        flatten_buckets(tag_values(agg_surplus(**kw, edges=REG_EDGES, lam=REG_LAM, exp=True), "_expT", keep=KGHA))
    )
    lagged_avgs, roll_n_all = _cross_site_nitrate(site, "reg")
    # roll_n_all is ONE DataFrame of the rolling windows -- starring it would unpack its column NAMES as strings into the frame list, which merge_on_date cannot consume.
    return [wb, cb, sb, sb_exp, *lagged_avgs, roll_n_all]


def _best_features_REG(site, n):
    """Best known REG feature list (no target). The cached aggregations plus the one spine-dependent block, the calendar signal.

    Static site features are thrown in at the end by assemble.
    """
    return [*_agg_features_REG(site), doy_climatology_pure_signal(n)]


@_cache_by_site(lambda: (CLF_EDGES, CLF_LAM, _CROSS_SITE_LAGS_CLF, _CROSS_SITE_ROLL_CLF, _LIVE_WEATHER_PREFIX_CLF))
def _agg_features_CLF(site):
    """The spine-INDEPENDENT half of the CLF feature list: everything keyed on (site, geometry). Cached -- see _cache_by_site."""
    kw = _site_kwargs(site)
    KGHA = ["surplus_kgha"]

    wb = _keep_live_weather(flatten_buckets(agg_weather(**kw, edges=CLF_EDGES, exp=False)), "clf")
    cb = flatten_buckets(agg_crops_normalized(**kw, edges=CLF_EDGES))
    # no bucketing, done after ablation experiments on 07-29, see notebook
    cb_exp = flatten_buckets(agg_crops(**kw, edges=(), lam=CLF_LAM, exp=True))
    sb = _drop_dead_surplus(
        flatten_buckets(tag_values(agg_surplus_normalized(**kw, edges=CLF_EDGES), "_norm", keep=KGHA))
    )
    sb_exp = _drop_dead_surplus(
        flatten_buckets(tag_values(agg_surplus(**kw, edges=CLF_EDGES, lam=CLF_LAM, exp=True), "_expT", keep=KGHA))
    )
    lagged_avgs, roll_n_all = _cross_site_nitrate(site, "clf")
    return [wb, cb, cb_exp, sb, sb_exp, *lagged_avgs, roll_n_all]


def _best_features_CLF(site, n):
    """Best known CLF feature list (no target). The cached aggregations plus the one spine-dependent block, the calendar signal.

    Static site features are thrown in at the end by assemble.
    """
    return [*_agg_features_CLF(site), doy_climatology_pure_signal(n)]


def _add_static(site, d, task):
    """Broadcast the per-site constants in: the site descriptors, then the long-run composition block. Both are one value per site, so they are assigned as columns rather than merged on date."""
    clf = task == "clf"
    skips = _SKIP_STATIC_KEYWORDS_CLF if clf else _SKIP_STATIC_KEYWORDS_REG
    edges = CLF_EDGES if clf else REG_EDGES
    stats = _LONGRUN_STATS_CLF if clf else _LONGRUN_STATS_REG
    for k, v in site_static(**_site_kwargs(site)).items():
        if k in skips:
            continue  # cat_* local-reach attrs are diagnostics only (site fingerprints), never model features
        d[k] = v
    # memoized on (uid, edges, stats), so a geometry change invalidates rather than serving stale shares
    for k, v in longrun_composition(**_site_kwargs(site), edges=edges, stats=stats).items():
        d[k] = v
    return d


def _assemble(site, task, spine, target=None, variant="island"):
    """Shared assembly for the gauged and virtual paths: build the task's feature list on the given `spine` (a DatetimeIndex of output rows), merge, add the static descriptors, and optionally prepend a `target` column.

    `n` is a bare date-carrier built from `spine` -- only its index feeds doy and the merge timeline (no nitrate values enter the features), so a waterless virtual site works by supplying a weather-derived spine and no target.

    `variant='network'` appends the reach-graph donor block on top of the identical island list -- the two recipes share one aggregation pass rather than forking it, so the only difference between them is what this adds.
    """
    n = pd.Series(index=pd.DatetimeIndex(spine), dtype="float64")
    if task == "reg":
        feat = _best_features_REG(site, n)
    elif task == "clf":
        feat = _best_features_CLF(site, n)
    else:
        raise ValueError(f"Expected 'reg' or 'clf', got {task}")
    if variant not in ("island", "network"):
        raise ValueError(f"Expected 'island' or 'network', got {variant}")

    nbr_stats = {}
    if variant == "network":
        nbr_frame, nbr_stats = _neighbor_nitrate(site, task)
        feat = [*feat, nbr_frame]
    frames = feat if target is None else [target, *feat]
    d = _add_static(site, merge_on_date(frames, spine=n.index), task)
    for k, v in nbr_stats.items():
        d[k] = v  # static per site -> broadcast, same as _add_static's descriptors
    return d


def build_feature_frame(site, task="reg", spine=None, variant="island"):
    """Model-ready feature frame WITHOUT a target.

    Parameters
    ----------
    site : str or SiteData
        A site_uid for a gauged site (memoized), or a prebuilt SiteData for a virtual/ungauged one.
    task : {'reg', 'clf'}, default 'reg'
        Selects which feature list to build.
    spine : pd.DatetimeIndex, optional
        Output rows. Defaults to the site's daily-nitrate index (the gauged timeline, i.e. island_REG/_CLF minus the target). For an ungauged site pass a weather-derived spine -- e.g. the TARGET_YEAR daily dates -- and the frame is produced without touching water at all, which is what the deploy virtual recipe relies on.
    variant : {'island', 'network'}, default 'island'
        'network' adds the reach-graph donor block, which needs a resolvable up/downstream monitored neighbour.

    Returns
    -------
    pd.DataFrame
        date + features.
    """
    if spine is None:
        spine = daily_nitrate(**_site_kwargs(site)).index
    return _assemble(site, task, spine=spine, target=None, variant=variant)


def _target_maker(site, task="reg", window=1, min_obs=1, variant="island"):
    """Build the task's target on the site's gauged daily timeline and assemble it with the features."""
    spine = daily_nitrate(**_site_kwargs(site)).index  # the gauged daily timeline; values unused here
    if task == "reg":
        target = nitrate_daily_rolling(**_site_kwargs(site), window=window, min_obs=min_obs).rename("nitrate_con")
    elif task == "clf":
        target = nitrate_violations_rolling(**_site_kwargs(site), window=window, min_obs=min_obs).rename("violation")
    else:
        raise ValueError(f"Expected 'reg' or 'clf', got {task}")
    return _assemble(site, task, spine=spine, target=target, variant=variant)


# The shipped recipes. window=1 / min_obs=1 is the best geometry (exp13); recipe_maker is the parameterized factory experiments use for window sweeps, island_REG/island_CLF the fixed shipped pair.
def recipe_maker(task, window=1, min_obs=1):
    """Build a one-argument recipe closure for `task` at a chosen target geometry.

    The parameterized factory experiments use for window sweeps; island_REG / island_CLF are the fixed shipped pair at window=1, min_obs=1.

    Parameters
    ----------
    task : {'reg', 'clf'}
    window : int, default 1
        Trailing days the target is reduced over, today inclusive.
    min_obs : int, default 1
        Observed days required before a window gets a label; below it the row is <NA> and dropped before training.

    Returns
    -------
    callable
        site -> DataFrame, matching the recipe contract cook.py expects.
    """
    if task not in ("reg", "clf"):
        raise ValueError(f"Expected 'reg' or 'clf', got {task}")

    def recipe(site):
        """site -> feature+target frame, at the task and window this factory closed over."""
        return _target_maker(site, task=task, window=window, min_obs=min_obs)

    return recipe


def island_REG(site, window=1, min_obs=1):
    """The shipped REGRESSION recipe: features + the `nitrate_con` target.

    ISLAND means every feature is computable at an arbitrary ungauged pin -- statewide aggregates included, since those need a monitoring network but no NEARBY donor. The network_* pair adds features that require a resolvable up/down reach neighbour, and so is undefined at a pin that has none.

    Parameters
    ----------
    site : str or SiteData
        A site_uid for a gauged site (memoized), or a prebuilt SiteData for a virtual/ungauged one.
    window : int, default 1
        Trailing days the target is reduced over, today inclusive.
    min_obs : int, default 1
        Observed days required before a window gets a label; below it the row is <NA> and dropped before training.

    Returns
    -------
    pd.DataFrame
        date + target + features, one row per day on the site's gauged timeline.

    See Also
    --------
    island_CLF : the classification pair.
    network_REG : the same, plus the reach-graph donor block.
    build_feature_frame : the same features with no target, for a virtual site.
    """
    return _target_maker(site, task="reg", window=window, min_obs=min_obs)


def island_CLF(site, window=1, min_obs=1):
    """The shipped CLASSIFICATION recipe: features + the binary `violation` target.

    ISLAND means every feature is computable at an arbitrary ungauged pin -- see island_REG.

    Parameters
    ----------
    site : str or SiteData
        A site_uid for a gauged site (memoized), or a prebuilt SiteData for a virtual/ungauged one.
    window : int, default 1
        Trailing days the target is reduced over, today inclusive.
    min_obs : int, default 1
        Observed days required before a window gets a label; below it the row is <NA> and dropped before training.

    Returns
    -------
    pd.DataFrame
        date + target + features, one row per day on the site's gauged timeline.

    See Also
    --------
    island_REG : the regression pair.
    network_CLF : the same, plus the reach-graph donor block.
    """
    return _target_maker(site, task="clf", window=window, min_obs=min_obs)


def network_REG(site, window=1, min_obs=1):
    """island_REG plus the reach-graph neighbour block: features + the `nitrate_con` target.

    NETWORK adds features that require a resolvable up/downstream MONITORED neighbour, so unlike island_REG it is undefined at a pin with no donor -- the columns are emitted NaN-filled rather than omitted, and the model's usefulness there collapses to whatever the island half carries. The island/network gap is therefore also a siting number: what a nearby gauge is worth.

    Parameters
    ----------
    site : str or SiteData
        A site_uid for a gauged site (memoized), or a prebuilt SiteData for a virtual/ungauged one.
    window : int, default 1
        Trailing days the target is reduced over, today inclusive.
    min_obs : int, default 1
        Observed days required before a window gets a label; below it the row is <NA> and dropped before training.

    Returns
    -------
    pd.DataFrame
        date + target + features, one row per day on the site's gauged timeline.

    See Also
    --------
    island_REG : the same recipe without the donor block, deployable at any pin.
    network_CLF : the classification pair.
    """
    return _target_maker(site, task="reg", window=window, min_obs=min_obs, variant="network")


def network_CLF(site, window=1, min_obs=1):
    """island_CLF plus the reach-graph neighbour block: features + the binary `violation` target.

    NETWORK requires a resolvable monitored neighbour -- see network_REG.

    Parameters
    ----------
    site : str or SiteData
        A site_uid for a gauged site (memoized), or a prebuilt SiteData for a virtual/ungauged one.
    window : int, default 1
        Trailing days the target is reduced over, today inclusive.
    min_obs : int, default 1
        Observed days required before a window gets a label; below it the row is <NA> and dropped before training.

    Returns
    -------
    pd.DataFrame
        date + target + features, one row per day on the site's gauged timeline.

    See Also
    --------
    island_CLF : the same recipe without the donor block, deployable at any pin.
    network_REG : the regression pair.
    """
    return _target_maker(site, task="clf", window=window, min_obs=min_obs, variant="network")
