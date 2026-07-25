from src.features.features import (
    agg_crops,
    agg_crops_normalized,
    agg_surplus,
    agg_surplus_normalized,
    agg_weather_w_lag,
    agg_weather_normalized,
    daily_nitrate,
    nitrate_avg_except_this,
    rolling_nitrate_avg_except_this,
    doy_climatology_pure_signal,
    site_static,
    nitrate_daily_rolling,
    nitrate_violations_rolling,
)
from src.features.transformers import flatten_buckets, merge_on_date
from functools import lru_cache
import pandas as pd

# Best feature-construction geometry from the exp10 grid search: bucket edge (m) / water
# travel velocity (m/s) / crop-surplus exp-decay length (m). REG and CLF picked different optima.
REG_EDGE, REG_VEL, REG_LAM = 50_000, 2.1, 100_000  # exp10 best lofo_r2:  e50k_v2.1_l100k
CLF_EDGE, CLF_VEL, CLF_LAM = 5_000, 2.1, 2_000  # exp10 best lofo_auc: e5k_v2.1_l20k

# Distance-bucket boundaries (m). REG stays single-edge -- exp18 showed finer buckets overfit and
# LOWER its LOFO. CLF gets a 2km riparian inner bucket on top of the tuned edge: exp18/exp19 found
# this lifts lofo_prauc +0.039 (the classifier's near-field flushing signal resolves violation risk).
# (Antecedent-precip rolling sums were tested too -- exp17 -- but only overlapped this gain, so out.)
REG_EDGES = (REG_EDGE,)
CLF_EDGES = (2_000, CLF_EDGE)

# ── shipped-model feature curation ────────────────────────────────────────────
# The RECIPE (not the accessor) decides which columns the shipped model consumes. Every drop below is
# justified by permutation importance ~= 0 across all trained models plus the recorded ablations --
# the accessors in features.py still expose the full data for experiments.
_LIVE_WEATHER_PREFIX = (
    "fuel_moisture_1000h",  # only weather var with held-out signal; the other 8 are perm-dead in every run
    "energy_release",
    "precip_in_1d",
    "burning_index",
    "max_rel_humidity",
    "evapotranspiration",
)
_DEAD_SURPLUS_PREFIX = "total_kg_N"  # high gain, ~0 perm both tasks (surplus_kgha kept)
_SKIP_STATIC_KEYWORDS = ("cat_bfi", "cat_tiles92")
_CROSS_SITE_LAGS = (1, 2)  # rest_of_state_nitrate_lag1/2; lag3/lag5 are perm-dead (negative in REG)


def _keep_live_weather(wb):
    """Drop the eight permutation-dead weather variables, keeping only the fuel_moisture_1000h buckets."""
    return wb[[c for c in wb.columns if c == "date" or c.startswith(_LIVE_WEATHER_PREFIX)]]


def _drop_dead_surplus(sb):
    """Drop total_kg_N (dead); keep surplus_kgha."""
    return sb[[c for c in sb.columns if not c.startswith(_DEAD_SURPLUS_PREFIX)]]


def _site_kwargs(site):
    """Map a site identifier -- a site_uid (str) or a SiteData -- to the keyword the
    generalized data.features/data.transforms helpers expect (site_uid= vs site_data=)."""
    return {"site_uid": site} if isinstance(site, str) else {"site_data": site}


def _site_uid_of(site):
    """The site_uid label used for cross-site exclusion: the string itself, or a SiteData's
    .site_uid. A virtual site's uid is absent from the state, so nothing is excluded and the
    neighbour features become the full rest-of-state average."""
    return site if isinstance(site, str) else site.site_uid


def _add_static(site, d):
    for k, v in site_static(**_site_kwargs(site)).items():
        if k in _SKIP_STATIC_KEYWORDS:
            continue  # cat_* local-reach attrs are diagnostics only (site fingerprints), never model features
        d[k] = v
    return d


# ── uid-memoized block dispatch: cache on a hashable uid, run a SiteData (unhashable) fresh ──
@lru_cache(maxsize=None)
def _by_uid(compute, site_uid, args):
    return compute(site_uid, *args)


def _site_cached(compute, site, *args):
    """Memoize `compute(site, *args)` on a hashable uid string; run fresh for a SiteData. `edges` must
    be a tuple (not a list) so the args are hashable on the cached path. Replaces the per-block
    _compute / @lru_cache _cached / isinstance-dispatch trios."""
    return _by_uid(compute, site, args) if isinstance(site, str) else compute(site, *args)


def _agg_block_compute(site, edges, vel, lam):
    """The window-INDEPENDENT spatial aggregations (weather-with-lag, exp-decay crop, exp-decay
    surplus) for a site_uid OR a SiteData (via _site_kwargs). `edges` is a tuple of bucket boundaries
    (m). Returned frames are READ-ONLY -- callers must not mutate them (merge_on_date never does)."""
    kw = _site_kwargs(site)
    e = list(edges)
    KGHA = ["surplus_kgha"]

    wb = _keep_live_weather(flatten_buckets(agg_weather_w_lag(**kw, edges=e, exp=False, water_velocity=vel)))
    cb = flatten_buckets(agg_crops_normalized(**kw, edges=e))
    sb = _drop_dead_surplus(flatten_buckets(_tag_values(agg_surplus_normalized(**kw, edges=e), "_norm", keep=KGHA)))
    sb_exp = _drop_dead_surplus(
        flatten_buckets(_tag_values(agg_surplus(**kw, edges=e, lam=lam, exp=True), "_norm", keep=KGHA))
    )

    return wb, cb, sb, sb_exp


def _agg_block(site, edges, vel, lam):
    """The window-independent aggregation block, memoized per (site_uid, edges, vel, lam) so a
    window x min_obs sweep reuses one computation per site."""
    return _site_cached(_agg_block_compute, site, edges, vel, lam)


def _cross_site_nitrate_compute(site):
    """The cross-site nitrate neighbour features (lags in _CROSS_SITE_LAGS + rolling 7/14/30/60d) for a
    site_uid OR SiteData. `site` is used only as the exclusion label (via _site_uid_of)."""
    uid = _site_uid_of(site)
    lagged_avgs = tuple(nitrate_avg_except_this(uid, shift=k) for k in _CROSS_SITE_LAGS)
    rolling_avg_not_this = rolling_nitrate_avg_except_this(uid, windows=(7, 14, 30, 60))
    return lagged_avgs, rolling_avg_not_this


def _cross_site_nitrate(site):
    """Window- and geometry-independent cross-site nitrate features, memoized per site_uid."""
    return _site_cached(_cross_site_nitrate_compute, site)


def _tag_values(frame, suffix, keep=None):
    """Rename an aggregated frame's value columns (all but year/bucket) by appending `suffix`, so a
    variant coexists with its siblings under distinct names. `keep`, if given, first restricts the
    value columns to that list (used to drop total_kg_N, keeping only surplus_kgha)."""
    struct = [c for c in ("year", "bucket") if c in frame.columns]
    val = [c for c in frame.columns if c not in struct]
    if keep is not None:
        val = [c for c in val if c in keep]
        frame = frame[struct + val]
    return frame.rename(columns={c: f"{c}{suffix}" for c in val})


def _covariate_block(site, n, edges, vel, lam, roll_nitrate_windows=(7, 60), include_crops=True):
    """The feature scaffold: lagged whole-basin weather (fuel_moisture only), exp-decay crop (REG
    only) and surplus aggregations (memoized via _agg_block), the pure calendar signal, and the
    cross-site nitrate lags. Returns a fresh list each call.

    `site` may be a site_uid (str, cached path) or a SiteData (virtual/ungauged, cache bypassed).
    `n` is a bare date-carrier -- only n.index feeds doy (and, upstream, the merge spine); no
    nitrate values enter the features, so a waterless virtual site works with a weather spine.

    `roll_nitrate_windows` picks which rolling cross-site nitrate windows to append (a subset of the
    cached 7/14/30/60d set; () to omit). These help REG -- gain concentrated in 7d -- but hurt CLF,
    so REG keeps {7} and CLF omits them. `include_crops` is REG-only: the crop block is
    permutation-dead for the violation classifier, so CLF drops it."""

    wb, cb, sb, sb_exp = _agg_block(site, edges, vel, lam)
    lagged_avgs, roll_n_all = _cross_site_nitrate(site)
    doy = doy_climatology_pure_signal(n)
    feats = [wb, cb, sb, sb_exp, doy, *lagged_avgs] if include_crops else [wb, sb, sb_exp, doy, *lagged_avgs]
    if roll_nitrate_windows:
        feats.append(roll_n_all[[f"roll_n_avg_except_this{w}d" for w in roll_nitrate_windows]])
    return feats


def _best_features_REG(site, n):
    """Best known REG feature list (no target). Single-bucket geometry (finer buckets overfit,
    exp18); rolling cross-site nitrate trimmed to the 7d window (REG1.1 importance concentrates
    there; 14/30/60d were near-zero); no antecedent-precip (it hurts REG, exp17)."""
    return _covariate_block(site, n, REG_EDGES, REG_VEL, REG_LAM, roll_nitrate_windows=(7,))


def _best_features_CLF(site, n, roll_nitrate_windows=()):
    """Best known CLF feature list (no target). Riparian 2km inner bucket (exp18/exp19, +0.039
    lofo_prauc); rolling cross-site nitrate omitted by default (hurts CLF); crops dropped (the crop
    block is permutation-dead for the classifier)."""
    return _covariate_block(site, n, CLF_EDGES, CLF_VEL, CLF_LAM, roll_nitrate_windows=roll_nitrate_windows)


def _assemble(site, task, spine, target=None):
    """Shared assembly for the gauged and virtual paths: build the task's feature list on the
    given `spine` (a DatetimeIndex of output rows), merge, add the static descriptors, and
    optionally prepend a `target` column.

    `n` is a bare date-carrier built from `spine` -- only its index feeds doy and the merge
    timeline (no nitrate values enter the features), so a waterless virtual site works by
    supplying a weather-derived spine and no target."""
    n = pd.Series(index=pd.DatetimeIndex(spine), dtype="float64")
    if task == "reg":
        feat = _best_features_REG(site, n)
    elif task == "clf":
        feat = _best_features_CLF(site, n)
    else:
        raise ValueError(f"Expected 'reg' or 'clf', got {task}")
    frames = feat if target is None else [target, *feat]
    return _add_static(site, merge_on_date(frames, spine=n.index))


def build_feature_frame(site, task="reg", spine=None):
    """Model-ready feature frame for `site` (a site_uid OR a SiteData), WITHOUT a target.

    `spine` is the DatetimeIndex of output rows. It defaults to the site's daily-nitrate index
    (the gauged timeline, matching recipe_REG/_CLF minus the target). For an ungauged/virtual
    site (no water) pass a spine derived from the weather window -- e.g. the TARGET_YEAR daily
    dates -- and the frame is produced without ever touching water. The deploy virtual recipe
    calls this."""
    if spine is None:
        spine = daily_nitrate(**_site_kwargs(site)).index
    return _assemble(site, task, spine=spine, target=None)


def _target_maker(site, task="reg", window=1, min_obs=1):
    spine = daily_nitrate(**_site_kwargs(site)).index  # the gauged daily timeline; values unused here
    if task == "reg":
        target = nitrate_daily_rolling(**_site_kwargs(site), window=window, min_obs=min_obs).rename("nitrate_con")
    elif task == "clf":
        target = nitrate_violations_rolling(**_site_kwargs(site), window=window, min_obs=min_obs).rename("violation")
    else:
        raise ValueError(f"Expected 'reg' or 'clf', got {task}")
    return _assemble(site, task, spine=spine, target=target)


# The shipped recipes. window=1 / min_obs=1 is the best geometry (exp13); recipe_maker is the
# parameterized factory experiments use for window sweeps, recipe_REG/recipe_CLF the fixed shipped pair.
def recipe_maker(task, window=1, min_obs=1):
    if task not in ("reg", "clf"):
        raise ValueError(f"Expected 'reg' or 'clf', got {task}")

    def recipe(site):
        return _target_maker(site, task=task, window=window, min_obs=min_obs)

    return recipe


def recipe_REG(site, window=1, min_obs=1):
    return _target_maker(site, task="reg", window=window, min_obs=min_obs)


def recipe_CLF(site, window=1, min_obs=1):
    return _target_maker(site, task="clf", window=window, min_obs=min_obs)
