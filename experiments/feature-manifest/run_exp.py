"""run_exp.py -- the feature-manifest experiment (add-one screen + drop-one leave-one-out).

A CONSISTENT, REPEATABLE TEST OF FEATURE USEFULNESS, deliberately independent of what currently ships. Fix a strong BASE (statics + geography + the statewide pooled nitrate + calendar; 14 columns on --island), then run two duals against it over one shared wide pool per task:
  * add-one  -- add each currently-absent unit ALONE over the base; marginal LOFO gain. Scores what perm importance structurally can't (absent weather/gridMET/crops/surplus/lags/rolling) and breaks the collinearity that deflated the weather block's perm.
  * drop-one -- remove each unit from the FULL model; LOFO loss. The synergy detector: a unit weak added alone but costly to remove is load-bearing only in company.

`full_feature_set` is a SUPERSET of the shipped recipe, never a copy of it. That is the point: if a feature was wrongly dropped from src/features/recipes.py, drop-one can only catch it while full still carries it. Each unit is logged with `shipped` (does the recipe carry it?) and `n_cols` (is it big enough to measure?), and render_results turns those into the shipped-gap report -- omitted-but-useful in one direction, shipped-but-inert in the other.

Both modes run in ONE invocation (--mode both, default), tagged with a shared run_id, into the unified log.json. Geometry is imported from recipes.py rather than restated, because changing the bucket RADII at a fixed bucket count leaves n_feat identical -- a drifted copy would be undetectable after the fact.

TWO XGB CONFIGS, one per mode (lean_cook._SCREEN_XGB_* / _FULL_XGB_*). Add-one arms are base-sized, drop-one arms are full-sized, and one config cannot be right for both. Within a mode every arm shares one config, so its deltas stay exactly paired; nothing ever compares across modes.

ONE VARIANT PER RUN, and it must be stated: --island is the donor-free feature set, --network adds features.neighbor_nitrate's reach-graph donor block (recipes.py's network_REG/network_CLF) to the BASE, so every candidate is screened on top of it and drop-one reports what removing it costs. There is no default because the two are not comparable -- network is scored only where a donor exists (~88% of sites) and its reach neighbours co-fall with the basin families LOFO holds out, so a bare invocation that silently picked one would produce a number no one could place. Every log entry carries `variant` for the same reason.

    python run_exp.py --island                                   # both modes, both tasks, canonical encodings
    python run_exp.py --network                                  # ... over the reach-graph donor block
    python run_exp.py --island --mode add --task reg             # add-one only, regression
    python run_exp.py --island --encodings                       # + the crop/surplus/weather bake-off (~3x)
    python run_exp.py --network --sites 10 --limit 4 --no-perm   # fast smoke

Then: python render_results.py <run_id>   (or `latest`; no arg lists valid run_ids).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root/

import lean_cook
from src.data.access import get_site_ids
from src.features.features import (
    agg_crops,
    agg_crops_normalized,
    agg_surplus,
    agg_surplus_normalized,
    agg_weather_w_lag,
    daily_nitrate,
    doy_climatology_pure_signal,
    nitrate_avg_except_this,
    nitrate_daily_rolling,
    nitrate_violations_rolling,
    rolling_nitrate_avg_except_this,
    site_static,
)
from src.features.transformers import flatten_buckets, merge_on_date, tag_values
from src.features import recipes as _shipped

_HERE = Path(__file__).resolve().parent
LOG_PATH = _HERE / "log.json"  # unified log: every entry carries run_id + mode (add_one/drop_one)
OLD_SITES_PATH = _HERE / "old_sites.txt"  # the 83 pre-expansion sites (see --old-sites-only)


def load_old_sites() -> list[str]:
    """The pre-expansion site list, intersected with what the current build actually has.

    Only 79 of the 83 survive the reworked water filter, so this reports the gap rather than silently returning a shorter list -- a run that quietly drops sites is not a controlled comparison. Returns the intersection, ordered as in the file.
    """
    if not OLD_SITES_PATH.exists():
        raise SystemExit(f"--old-sites-only needs {OLD_SITES_PATH}, which is missing.")
    listed = [ln.strip() for ln in OLD_SITES_PATH.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    current = {str(s) for s in get_site_ids()}
    keep = [s for s in listed if s in current]
    gone = [s for s in listed if s not in current]
    print(f"--old-sites-only: {len(listed)} listed, {len(keep)} present in the current build")
    if gone:
        print(f"  dropped by the reworked filter ({len(gone)}): {', '.join(gone)}")
    if not keep:
        raise SystemExit("none of the listed sites exist in the current build.")
    return keep


# ── the fixed base: statics + geography + neighbor + calendar (all non-bucketed) ──
# Grouped into BLOCKS, not listed flat, because the base is also the drop-one unit list. A single-column drop out of ~100 cannot clear the measured noise floor (REG 0.0085 / CLF 0.0037 on the headline), so 23 of the old 43 units were unmeasurable by construction -- they reported a number that could only ever be fold noise. Grouping trades per-column resolution the screen never had for units that can resolve. The lag block holds the SHIPPED lags; the others are candidates (see _EXTRA_LAGS).
_BASE_BLOCKS = {
    "neighbor_lags": [f"rest_of_state_nitrate_lag{k}" for k in _shipped._CROSS_SITE_LAGS_REG],
    "dist": ["mean_dist_to_sensor", "max_dist_to_sensor"],
    "tile_block": ["tile_frac_basin", "tile_frac_ag", "tot_tiles92"],
    "comid_block": ["tot_contact", "tot_bfi"],
    "geo": ["lat", "lon", "log_basin_area"],
    "calendar": ["doy_sin", "doy_cos"],
}
BASE_FEATURES = [c for cols in _BASE_BLOCKS.values() for c in cols]


# ── the reach-graph donor block, --network only ──────────────────────────────
# NOT the `neighbor_lags` base block above. That one is rest_of_state_nitrate_lag*, the statewide pool, which needs no donor and is island-legal; these are features.neighbor_nitrate's columns for ONE named up/down neighbour resolved on the basin-containment graph. Everything here carries an nbr_ prefix except the four scalars features.py emits unprefixed (distance_*, dist_to_neighbor, neighbor_up), which is why the block is listed rather than matched on a prefix.
#
# Derived from recipes.py's own _NBR_* constants, for the same reason neighbor_lags reads _CROSS_SITE_LAGS_REG: a lag or encoding change has to move the manifest with it. Grouped into blocks on the same grounds as _BASE_BLOCKS -- these are drop-one units, and a 1-column drop out of ~100 cannot clear the noise floor. The grouping matches the ablation arms exps.py:602 already screened these under (lagged / level / twohop / cover).
def _nbr_blocks(task: str) -> dict[str, list[str]]:
    """The donor block's columns for `task`, grouped into drop-one units. Mirrors features.neighbor_nitrate's emission rules, so main re-checks the names against the built pool -- see _check_nbr_pool."""
    clf = task == "clf"
    lags = _shipped._NBR_LAGS_CLF if clf else _shipped._NBR_LAGS_REG
    enc = _shipped._NBR_ENCODING_CLF if clf else _shipped._NBR_ENCODING_REG
    if enc == "both":  # an _up and a _down column set
        sfx = ("_up", "_down")
        return {
            "nbr_anom": [f"nbr_anom_lag{k}{s}" for s in sfx for k in lags],
            "nbr_level": [f"nbr_level_lag1{s}" for s in sfx],
            "nbr2": [f"nbr2_anom_lag1{s}" for s in sfx],
            "nbr_meta": [f"{n}{s}" for n in ("distance", "nbr_area_ratio", "nbr_n") for s in sfx],
        }
    if enc not in ("flag", "signed"):
        raise ValueError(f"recipes._NBR_ENCODING_{'CLF' if clf else 'REG'} is {enc!r}, which this module has no column list for")
    return {  # one neighbour either direction; 'signed' folds the direction into dist_to_neighbor's sign and drops the flag
        "nbr_anom": [f"nbr_anom_lag{k}" for k in lags],
        "nbr_level": ["nbr_level_lag1"],
        "nbr2": ["nbr2_anom_lag1"],
        "nbr_meta": ["nbr_n_neighbors", "nbr_area_ratio", "dist_to_neighbor"] + (["neighbor_up"] if enc == "flag" else []),
    }


def _nbr_cols(nbr: dict | None) -> list[str]:
    """Flatten a _nbr_blocks dict to its columns; [] on island."""
    return [c for cols in (nbr or {}).values() for c in cols]


def _check_nbr_pool(pool, nbr: dict | None, task: str) -> None:
    """Fail if a _nbr_blocks name did not materialize in the built pool. No-op on island.

    compare_many silently drops columns a recipe names but the pool lacks (lean_cook._pool_cols), which for a CANDIDATE shows up as an add_* arm that scores exactly `base`. These columns are in the BASE, so the same drift instead shrinks the base and moves every number in the run with nothing saying why -- hence a hard failure rather than a warning.
    """
    missing = [c for c in _nbr_cols(nbr) if c not in pool.columns]
    if missing:
        raise SystemExit(
            f"_nbr_blocks({task!r}) names {len(missing)} column(s) the wide pool does not hold: {', '.join(missing)}.\n"
            f"It mirrors features.neighbor_nitrate's emission rules for recipes._NBR_ENCODING_* -- one of the two moved."
        )

# ── every candidate currently ABSENT from the base, one screened per run ──
_WEATHER_VARS = [
    "precip_in_1d",
    "precip_gridmet",
    "max_temp",
    "min_temp",
    "max_rel_humidity",
    "min_rel_humidity",
    "specific_humidity",
    "vpd",
    "solar_rad",
    "wind_speed",
    "wind_direction",
    "evapotranspiration",
    "ref_et_alfalfa",
    "burning_index",
    "energy_release",
    "fuel_moisture_100h",
    "fuel_moisture_1000h",
]
_CROP_CLASSES = ["Alfalfa", "Corn", "Fallow", "Hay_Pasture", "Nonag", "Other", "Small_Grains", "Soybeans"]
# Crops & surplus are screened as block candidates (the whole class block at once) in FIVE encodings
# each: exp-decay-weighted sum (expT), plain sum (expF), per-bucket normalized (crops -> pct_*
# composition shares; surplus -> per-bucket mean intensity), whole-basin exp-decay sum with no buckets (whole), and whole-basin normalized with no buckets (norm_whole). total_kg_N is dropped entirely -- only surplus_kgha survives. The encodings share base names, so each variant's columns
# are tagged (_expT/_expF/_whole/_norm_whole; per-bucket norm crops are pct_*) to coexist in one pool.
_BLOCKS = {
    "crops_expT": [f"{c}_expT" for c in _CROP_CLASSES],
    "crops_expF": [f"{c}_expF" for c in _CROP_CLASSES],
    "crops_norm": [f"pct_{c.lower()}" for c in _CROP_CLASSES],  # per-bucket composition share
    "crops_whole": [f"{c}_whole" for c in _CROP_CLASSES],  # exp-decay sum over the WHOLE basin, no buckets
    "crops_norm_whole": [f"pct_{c.lower()}_whole" for c in _CROP_CLASSES],  # composition share over the WHOLE basin
    "surplus_expT": ["surplus_kgha_expT"],
    "surplus_expF": ["surplus_kgha_expF"],
    "surplus_norm": ["surplus_kgha_norm"],  # per-bucket mean intensity
    "surplus_whole": ["surplus_kgha_whole"],  # exp-decay sum over the WHOLE basin, no buckets
    "surplus_norm_whole": ["surplus_kgha_norm_whole"],  # mean intensity over the WHOLE basin
}
# whole-basin blocks: edges=() -> a single un-bucketed column per member (no _b suffix)
_WHOLE_BLOCKS = {"crops_whole", "surplus_whole", "crops_norm_whole", "surplus_norm_whole"}
# The crop/surplus encodings kept in the full run. PER TASK, because the shipped recipes diverged on 07-29: REG dropped bucketed exp-decay crops entirely, CLF replaced them with the whole-basin variant. `full` must be a SUPERSET of shipped -- a unit absent from full is absent from _full_units too, so drop-one cannot test it and the shipped-gap report cannot see it.
_CANON_BLOCKS = {
    "reg": {"crops_norm", "surplus_norm", "surplus_expT"},
    "clf": {"crops_norm", "surplus_norm", "surplus_expT", "crops_whole"},
}
_STATIC_CAT = ["cat_bfi", "cat_contact"]  # cat_tiles92 excluded by recipes._SKIP_STATIC_KEYWORDS_*; exp 26/26c settled it
# The cross-site lags NOT in the base -- i.e. the ones the shipped recipe does not carry, which is what makes them screenable. Derived by difference so a change to recipes._CROSS_SITE_LAGS_* moves a lag between base and candidate automatically instead of leaving it in both or neither.
_SCREENED_LAGS = (2, 5)
_EXTRA_LAGS = [
    f"rest_of_state_nitrate_lag{k}" for k in _SCREENED_LAGS if k not in _shipped._CROSS_SITE_LAGS_REG
]
_POOL_ROLL = (7, 14, 30, 60)  # every rolling window the pool emits: the shipped set plus the screened extras
_ROLLING = [f"roll_n_avg_except_this{w}d" for w in _POOL_ROLL]
# Every cross-site lag the pool emits -- base (shipped) plus screened. Derived, so adding a lag to either list can never leave the pool without the column its candidate names.
_POOL_LAGS = tuple(sorted(set(_shipped._CROSS_SITE_LAGS_REG) | set(_SCREENED_LAGS)))
# calendar: the first seasonal harmonic (2x frequency), screened as a sin+cos PAIR -- a Fourier harmonic needs both terms to represent arbitrary phase, so the pair is the meaningful unit.
_CALENDAR = ["doy_harmonic2"]


# Weather candidates are one per (variable, encoding). make_wide_recipe emits THREE tagged weather encodings -- exp-decay at lam (_expT{lam}k), exp-decay at lam/5 (_expT{lam//5}k), and plain lagged mean (_expF) -- so each variable is screened separately in each (chosen over one combined candidate). A candidate's name is its tagged base (e.g. precip_in_1d_expT20000k), which _expand turns into that base's _b0.._bN bucket columns -- matching what the wide pool actually holds. The names embed lam, so the list is built per task (see main).
def _weather_encodings(lam: int) -> list[str]:
    return [f"_expT{lam}k", f"_expT{lam // 5}k", "_expF"]


def _weather_singles(lam: int) -> list[str]:
    return [f"{v}{sfx}" for v in _WEATHER_VARS for sfx in _weather_encodings(lam)]


# Only the canonical weather encoding (the shipped exp=False lagged mean, _expF) enters the full model; the two exp-decay encodings are screened but kept out of full to avoid collinear copies (mirrors keeping only _CANON_BLOCKS crop/surplus encodings in full).
_CANON_WEATHER_SUFFIX = "_expF"

_MULTI = {"doy_harmonic2": ["doy_sin2", "doy_cos2"]}  # non-bucketed candidates spanning >1 column

# ── per-task shipped geometry (edges, vel, lam) + target ──
# Duplicated from src/features/recipes.py (REG_EDGES/REG_LAM, CLF_EDGES/CLF_LAM) rather than imported, so this experiment stays self-contained -- which means an edge or lam change has to be made in BOTH places or the manifest screens a geometry the shipped recipe does not use. `edges` sets n_buckets, so it also decides how many _b0.._bN columns every bucketed candidate expands into, and `lam` is embedded in the weather candidate names (_expT{lam}k / _expT{lam//5}k).
#
# Hand-aligned to recipes.py on edges and lam for both tasks, 2026-07-28. `vel` has NO counterpart to sync to: recipes.py no longer defines REG_VEL/CLF_VEL and the shipped recipes call plain agg_weather, so water_velocity reaches only this file's agg_weather_w_lag candidates and 2.1 is a manifest-local choice, not a copy of anything shipped.
#
# Scores are only comparable to earlier runs at the SAME geometry -- see the `geom` field each entry carries in log.json. Note _expT20000k is ambiguous across regimes: it is lam here, but was lam//5 back when REG ran at lam=100_000, so a bare suffix match across historical entries can pool two different decay lengths.
#
# CLF's inner edge is set by grid resolution, not hydrology: a grid_global cell is one ~4 km gridMET pixel, so a sub-4 km inner radius captures only the sensor's own cell and leaves bucket 0 empty (hence all-NaN) at a large share of sites -- 5_000 clears that floor. See the fuller note above CLF_EDGES in recipes.py.
GEOM = {
    "reg": {"edges": _shipped.REG_EDGES, "vel": 2.1, "lam": _shipped.REG_LAM, "target": "nitrate_con"},
    "clf": {"edges": _shipped.CLF_EDGES, "vel": 2.1, "lam": _shipped.CLF_LAM, "target": "violation"},
}


def _shipped_units(task: str, nbr: dict | None = None) -> set[str]:
    """The candidate/base units the SHIPPED recipe for `task` actually carries, in this module's unit vocabulary.

    Derived from recipes.py's own constants rather than restated, so it cannot drift from what ships. Used ONLY as a label -- `full` stays a superset, and render_results flags the two-way gap (shipped units that don't pay their way, unshipped units that do). Longrun composition and the calendar block have no candidate here, so they are absent by design rather than by omission.

    Every _nbr_blocks unit is shipped when `nbr` is given: the shipped recipe for a --network run is network_REG/network_CLF, which carries the donor block whole. Passing it here rather than reading a variant flag keeps the one caller (_unit_index) the only place the variant is decided.
    """
    clf = task == "clf"
    live = _shipped._LIVE_WEATHER_PREFIX_CLF if clf else _shipped._LIVE_WEATHER_PREFIX_REG
    roll = _shipped._CROSS_SITE_ROLL_CLF if clf else _shipped._CROSS_SITE_ROLL_REG
    skip = _shipped._SKIP_STATIC_KEYWORDS_CLF if clf else _shipped._SKIP_STATIC_KEYWORDS_REG
    # The shipped cross-site LAGS are deliberately absent here: the base carries them as the `neighbor_lags` block, and a unit set mixing block names with their member names would report the block as unshipped.
    return (
        {f"{v}{_CANON_WEATHER_SUFFIX}" for v in live}
        | set(_CANON_BLOCKS[task])
        | {f"roll_n_avg_except_this{w}d" for w in roll}
        | {u for u in _STATIC_CAT if u not in skip}
        | set(_BASE_BLOCKS)  # every base block is shipped: site_static descriptors, the shipped lags, the calendar
        | set(nbr or {})  # --network: the donor block ships whole as network_REG/network_CLF
    )


def make_wide_recipe(task, edges, vel, lam, encodings: bool = False, variant: str = "island"):
    """Build a wide_recipe(site) emitting every screened candidate column + the task target. `site` may be a site_uid (str) or a SiteData.

    `encodings` must match what `candidates(task, encodings)` screens. With it on, crops and surplus are emitted in five encodings each (exp-decay sum _expT, plain sum _expF, per-bucket normalized, whole-basin sum _whole, whole-basin normalized _norm_whole) and weather in three, all tagged so they coexist -- 13 aggregation calls per site. Off, only the canonical set the task's `full` can hold is computed: 4 calls for REG, 5 for CLF.

    THE GATE MUST MATCH THE CANDIDATE LIST. Skip an aggregation while still screening its candidate and that add_* recipe silently collapses to exactly `base`, paying a full CV fit to log a delta that is pure fold noise. surplus keeps only surplus_kgha (total_kg_N dropped).

    `variant='network'` also emits the reach-graph donor block via the SHIPPED wrapper recipes._neighbor_nitrate, so the manifest cannot screen a donor configured differently from the one that ships. It defaults to island because tune.py calls this positionally.
    """
    e = list(edges)
    KGHA = ["surplus_kgha"]  # the only surplus column screened (total_kg_N omitted)
    canon = _CANON_BLOCKS[task]

    def wide_recipe(site):
        kw = {"site_uid": site} if isinstance(site, str) else {"site_data": site}
        uid = site if isinstance(site, str) else site.site_uid
        n = daily_nitrate(**kw)  # spine carrier (only its index is used downstream)
        if task == "reg":
            target = nitrate_daily_rolling(**kw, window=1, min_obs=1).rename("nitrate_con")
        else:
            target = nitrate_violations_rolling(**kw, window=1, min_obs=1).rename("violation")
        blocks, nbr_stats = [], {}
        if variant == "network":
            nbr_frame, nbr_stats = _shipped._neighbor_nitrate(site, task)
            blocks.append(nbr_frame)
        # weather: the canonical lagged mean always; the two exp-decay encodings only for the bake-off
        blocks.append(
            flatten_buckets(tag_values(agg_weather_w_lag(**kw, edges=e, exp=False, water_velocity=vel), "_expF"))
        )
        if encodings:
            blocks += [
                flatten_buckets(
                    tag_values(agg_weather_w_lag(**kw, edges=e, exp=True, lam=L, water_velocity=vel), f"_expT{L}k")
                )
                for L in (lam, lam // 5)
            ]
        # crops / surplus: emit a block only if this task's `full` can hold it, or the bake-off wants it
        emit = {b for b in _BLOCKS if b in canon or encodings}
        if "crops_norm" in emit:
            blocks.append(flatten_buckets(agg_crops_normalized(**kw, edges=e)))  # pct_* composition (no exp/lam)
        if "crops_expT" in emit:
            blocks.append(flatten_buckets(tag_values(agg_crops(**kw, edges=e, lam=lam, exp=True), "_expT")))
        if "crops_expF" in emit:
            blocks.append(flatten_buckets(tag_values(agg_crops(**kw, edges=e, lam=lam, exp=False), "_expF")))
        if "crops_whole" in emit:
            blocks.append(flatten_buckets(tag_values(agg_crops(**kw, edges=(), lam=lam, exp=True), "_whole")))
        if "crops_norm_whole" in emit:
            blocks.append(flatten_buckets(tag_values(agg_crops_normalized(**kw, edges=()), "_whole")))
        if "surplus_norm" in emit:
            blocks.append(flatten_buckets(tag_values(agg_surplus_normalized(**kw, edges=e), "_norm", keep=KGHA)))
        if "surplus_expT" in emit:
            blocks.append(flatten_buckets(tag_values(agg_surplus(**kw, edges=e, lam=lam, exp=True), "_expT", keep=KGHA)))
        if "surplus_expF" in emit:
            blocks.append(flatten_buckets(tag_values(agg_surplus(**kw, edges=e, lam=lam, exp=False), "_expF", keep=KGHA)))
        if "surplus_whole" in emit:
            blocks.append(flatten_buckets(tag_values(agg_surplus(**kw, edges=(), lam=lam, exp=True), "_whole", keep=KGHA)))
        if "surplus_norm_whole" in emit:
            blocks.append(flatten_buckets(tag_values(agg_surplus_normalized(**kw, edges=()), "_norm_whole", keep=KGHA)))
        doy = doy_climatology_pure_signal(n)
        lagged = [nitrate_avg_except_this(uid, shift=k) for k in _POOL_LAGS]
        roll = rolling_nitrate_avg_except_this(uid, windows=_POOL_ROLL)
        frame = merge_on_date([target, *blocks, doy, *lagged, roll], spine=n.index)
        for k, v in site_static(**kw).items():  # ALL statics incl. cat_* fingerprints
            frame[k] = v
        for k, v in nbr_stats.items():  # donor scalars: static per site, so broadcast like the descriptors above
            frame[k] = v
        return frame

    wide_recipe.__name__ = "wide_recipe"
    return wide_recipe


def _expand(feat: str, n_buckets: int, bucketed: set, nbr: dict | None = None) -> list[str]:
    """Candidate name -> its column(s): an _nbr_blocks unit -> its member columns; a _BASE_BLOCK -> its member columns; a _BLOCK -> all its (bucketed) member columns; a `bucketed` single var (a tagged weather base, e.g. precip_in_1d_expT20000k) -> its _b0.._b{n-1} columns; a _MULTI candidate -> its explicit column list (e.g. doy_harmonic2 -> sin2+cos2); else the name itself. Deterministic from geometry, so no pool is needed (compare_many drops non-materializing cols). `nbr` is this task's _nbr_blocks under --network, None/{} on island."""
    if nbr and feat in nbr:
        return list(nbr[feat])
    if feat in _BASE_BLOCKS:
        return list(_BASE_BLOCKS[feat])
    if feat in _WHOLE_BLOCKS:
        return list(_BLOCKS[feat])  # single whole-basin column per member, no _b suffix
    if feat in _BLOCKS:
        return [f"{bn}_b{k}" for bn in _BLOCKS[feat] for k in range(n_buckets)]
    if feat in bucketed:
        return [f"{feat}_b{k}" for k in range(n_buckets)]
    if feat in _MULTI:
        return list(_MULTI[feat])
    return [feat]


FULL_NAME = "full_feature_set"


def candidates(task: str, encodings: bool = False) -> list[str]:
    """Every unit screened as an add-one candidate for `task`, in table order.

    THE single definition of the candidate list -- tune._columns imports this rather than rebuilding it, because the two lists were hand-duplicated and a divergence tunes one feature set while the screen measures another, silently.

    `encodings` controls the bake-off arms: the non-canonical weather encodings (2 of 3 per variable) and the non-canonical crop/surplus blocks exist only to compare encodings against each other and can never enter `full`. That is 42 of 71 candidates -- 59% of the screen -- answering a question settled occasionally rather than every run. Off by default; the pool skips computing them too (see make_wide_recipe), which is the larger saving.
    """
    return [f for fam in _candidate_families(task, encodings).values() for f in fam]


def _candidate_families(task: str, encodings: bool = False) -> dict[str, list[str]]:
    """The candidate list grouped by family, in table order. `candidates` flattens it; `smoke_subset` walks it."""
    g = GEOM[task]
    weather = _weather_singles(g["lam"])
    blocks = list(_BLOCKS)
    if not encodings:
        weather = [w for w in weather if w.endswith(_CANON_WEATHER_SUFFIX)]
        blocks = [b for b in blocks if b in _CANON_BLOCKS[task]]
    return {
        "weather": weather,
        "blocks": blocks,
        "static": list(_STATIC_CAT),
        "lags": list(_EXTRA_LAGS),
        "rolling": list(_ROLLING),
        "calendar": list(_CALENDAR),
    }


def smoke_subset(task: str, encodings: bool, k: int) -> list[str]:
    """`k` candidates ROUND-ROBIN across families, for --limit.

    Neither a prefix nor a stride works here: weather is 17 of ~29 candidates, so both hand back an almost pure weather sample and a smoke run never reaches _expand's block, whole-block or _MULTI branches -- four of its five code paths untested by the documented fast check. Round-robin takes one family at a time, so k=4 spans four families.
    """
    fams = [list(v) for v in _candidate_families(task, encodings).values() if v]
    out = []
    while len(out) < k and any(fams):
        for fam in fams:
            if fam and len(out) < k:
                out.append(fam.pop(0))
    return out


def _in_full(f: str, task: str, bucketed: set) -> bool:
    """Whether candidate `f` belongs in the full_feature_set for `task`. Excludes the non-canonical crop/surplus encodings (keep only that task's _CANON_BLOCKS) and the non-canonical weather encodings (keep only the shipped _expF), so full is a sensible 'everything' model rather than collinear copies -- while still a SUPERSET of what ships."""
    if f in _BLOCKS:
        return f in _CANON_BLOCKS[task]
    if f in bucketed:  # a tagged weather single -> only the canonical (_expF) encoding enters full
        return f.endswith(_CANON_WEATHER_SUFFIX)
    return True


def build_recipes(base: list[str], testing: list[str], n_buckets: int, bucketed: set, task: str = "reg", nbr: dict | None = None) -> dict[str, set]:
    """{'base': base_cols} + one 'add_<feat>' per candidate (base_cols | expand(feat)) + a final 'full_feature_set' holding every column EXCEPT the non-canonical crop/surplus/weather encodings (see _in_full). Insertion order puts the full run last.

    Under --network the caller folds the donor columns into `base`, so they reach `full` through base_cols and there is no add_nbr_* arm -- a candidate already in the base expands to exactly `base` and pays a CV fit to log fold noise. Drop-one measures the block instead (see _full_units).
    """
    base_cols = {c for f in base for c in _expand(f, n_buckets, bucketed, nbr)}
    recipes = {"base": set(base_cols)}
    full = set(base_cols)
    for f in testing:
        cols = set(_expand(f, n_buckets, bucketed, nbr))
        recipes[f"add_{f}"] = base_cols | cols
        if _in_full(f, task, bucketed):
            full |= cols
    recipes[FULL_NAME] = full
    return recipes


def _full_units(testing: list[str], task: str, bucketed: set, nbr: dict | None = None) -> list[str]:
    """The feature units that live in the full_feature_set and can therefore be dropped: every base BLOCK, every donor block under --network, plus every candidate the full run keeps (see _in_full). Base blocks rather than base columns -- see _BASE_BLOCKS on why a 1-column drop is unmeasurable here, which is also why the donor block is four units and not fourteen columns."""
    return list(_BASE_BLOCKS) + list(nbr or {}) + [f for f in testing if _in_full(f, task, bucketed)]


def build_drop_recipes(base: list[str], testing: list[str], n_buckets: int, bucketed: set, task: str = "reg", nbr: dict | None = None) -> dict[str, set]:
    """'full_feature_set' + one 'drop_<f>' per droppable unit = full MINUS f's columns. The dual of the add-one screen: a NEGATIVE score delta vs full means removing f HURT (f is load-bearing). Cross-referenced with the add-one manifest, small add-one Δ + large negative drop Δ = synergy (a feature useless alone but load-bearing in company). Full run scored first (reference)."""
    full = build_recipes(base, testing, n_buckets, bucketed, task, nbr)[FULL_NAME]
    recipes = {FULL_NAME: set(full)}
    for f in _full_units(testing, task, bucketed, nbr):
        recipes[f"drop_{f}"] = full - set(_expand(f, n_buckets, bucketed, nbr))
    return recipes


def _unit_index(task: str, testing: list[str], n_buckets: int, bucketed: set, nbr: dict | None = None) -> dict:
    """{recipe_name: (unit, n_cols, shipped)} for every add_*/drop_* recipe this run will score.

    `n_cols` is what the unit adds or removes, and it is the difference between "not load-bearing" and "too small to have been measurable" -- a 1-column change out of ~100 cannot clear the noise floor either way. `shipped` says whether the shipped recipe carries the unit, which is what lets render_results flag the gap in both directions.
    """
    ship = _shipped_units(task, nbr)
    out = {}
    for f in list(_BASE_BLOCKS) + list(nbr or {}) + list(testing):
        n = len(_expand(f, n_buckets, bucketed, nbr))
        for prefix in ("add_", "drop_"):
            out[f"{prefix}{f}"] = (f, n, f in ship)
    return out


def _extract(scores, name):
    """(row, gain, perm) for one recipe of a compare_many result -- everything log_results needs to re-emit it."""
    if name not in scores.index:
        return None
    return (
        scores.loc[[name]],
        (scores.attrs.get("importance", {}) or {}).get(name),
        (scores.attrs.get("importance_perm", {}) or {}).get(name),
    )


def _splice(scores, extracted):
    """Put an `_extract`ed row back at the FRONT of another compare_many result, importances included.

    Front because render_results._drop_one_tables deltas every drop against full and the log's insertion order is what a reader scans first.
    """
    import pandas as pd

    row, gain, perm = extracted
    out = pd.concat([row, scores])
    out.attrs = {k: dict(v or {}) for k, v in scores.attrs.items()}
    name = row.index[0]
    if gain is not None:
        out.attrs.setdefault("importance", {})[name] = gain
    if perm is not None:
        out.attrs.setdefault("importance_perm", {})[name] = perm
    return out


def _to_native(o):
    import numpy as np

    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON-serializable: {type(o).__name__}")


def log_results(
    scores, task, target_col, xgb, run_id, mode, site_set="all", n_sites=None, true_lofo=False, geom=None, units=None, variant="island", path: Path = LOG_PATH
) -> None:
    """Append one entry per recipe to the unified `path`, keyed by max+1 integer (runs coexist, never overwrite). Each entry carries `run_id` (one per run_exp invocation) and `mode` ('add_one' or 'drop_one') on top of the fulltrain-compatible schema. `site_set` records WHICH cohort the run used ('all' or 'old83'), because scores are only comparable within a cohort -- a log without it cannot tell a 127-site run from a 79-site one. `true_lofo` records WHICH HOLDOUT produced the lofo_* columns: the true leave-one-family-out regime returns the identical column set as the GroupKFold one but a systematically different (measurably HIGHER -- one family held out per fold leaves more training data than GroupKFold's ~1/5) number, so without this flag the log cannot tell the two apart and a cross-regime diff would read as a feature effect. `geom` records the feature-construction GEOMETRY (bucket edges / water velocity / exp-decay lam) the run was built with. Without it two runs can differ in exactly the thing experiments 30/30c exist to tune and nothing in the log says so -- and n_feat cannot stand in for it, because changing the edge RADII while keeping the bucket COUNT leaves n_feat identical. `variant` records island vs network, for the same reason as `site_set`: the two are not comparable (network scores only the ~88% of sites with a resolvable donor, and its reach neighbours co-fall with the basin families LOFO holds out), and without the field an island and a network run in one log are indistinguishable after the fact. `scores` is a lean_cook.compare_many result."""
    imps = scores.attrs.get("importance", {})
    perms = scores.attrs.get("importance_perm", {})
    log = {}
    if path.exists() and path.stat().st_size > 0:
        log = json.loads(path.read_text())
    key = max((int(k) for k in log), default=-1) + 1
    for name in scores.index:
        imp = imps.get(name)
        imp_perm = perms.get(name)
        unit, n_cols, shipped = (units or {}).get(name, (None, None, None))
        log[str(key)] = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_id": run_id,
            "mode": mode,
            # TOP LEVEL on purpose, never inside `score`: render_results lets the score dict drive its table columns, so a non-metric key there would be rendered as a nonsense delta column.
            "unit": unit,  # the feature unit this recipe adds/drops; None for base and full_feature_set
            "n_cols": n_cols,  # how many columns that unit is -- a null delta on 1 column is unmeasurable, not inert
            "shipped": shipped,  # whether the SHIPPED recipe carries it; drives the shipped-gap report
            "site_set": site_set,
            "n_sites_requested": n_sites,
            "true_lofo": bool(true_lofo),
            "variant": variant,  # 'island' | 'network'; absent on pre-flag entries, which were all island

            # feature-construction geometry; None on pre-flag entries, which cannot be compared to a later run without checking out the recipes.py of the day.
            "geometry": (
                {"edges": list(geom["edges"]), "vel": geom["vel"], "lam": geom["lam"]} if geom else None
            ),
            "name": name,
            "recipe": "wide_recipe",
            "features": list(imp.index) if imp is not None else [],
            "target_col": target_col,
            "task": task,
            "xgb": dict(xgb),
            "score": scores.loc[name].to_dict(),
            "importance": imp.to_dict() if imp is not None else {},
            "importance_perm": imp_perm.to_dict() if imp_perm is not None else {},
        }
        key += 1
    path.write_text(json.dumps(log, indent=2, default=_to_native))


def _new_run_id(path: Path = LOG_PATH) -> str:
    """A UTC stamp identifying this run_exp invocation (shared by every entry it writes). Suffixed
    -1/-2 if a same-second id already exists in the log."""
    rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    existing = set()
    if path.exists() and path.stat().st_size > 0:
        existing = {e.get("run_id") for e in json.loads(path.read_text()).values()}
    if rid not in existing:
        return rid
    k = 1
    while f"{rid}-{k}" in existing:
        k += 1
    return f"{rid}-{k}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=["reg", "clf", "both"], default="both")
    # REQUIRED, with no default: island and network scores are not comparable, so a bare invocation that quietly picked one would write numbers into the shared log that nobody could place afterwards. --mode can default because both its values are logged and rendered side by side; this cannot.
    var = ap.add_mutually_exclusive_group(required=True)
    var.add_argument("--island", dest="variant", action="store_const", const="island",
                     help="the donor-free feature set (recipes.island_REG/island_CLF)")
    var.add_argument("--network", dest="variant", action="store_const", const="network",
                     help="add the reach-graph donor block to the BASE, so every candidate is screened over it "
                          "and drop-one reports what removing it costs (recipes.network_REG/network_CLF)")
    ap.add_argument(
        "--mode",
        choices=["both", "add", "drop"],
        default="both",
        help="which run type(s) to run: add-one screen, drop-one leave-one-out, or both (default)",
    )
    ap.add_argument("--sites", type=int, default=None, help="cap to the first N sites (smoke test)")
    ap.add_argument(
        "--log",
        type=Path,
        default=LOG_PATH,
        help="where to append results (default log.json). log.json is tracked and append-only, so a smoke "
        "run_id in it is permanent and shows up in `render_results.py` forever -- point a shakedown elsewhere",
    )
    ap.add_argument(
        "--old-sites-only",
        action="store_true",
        help="restrict to the 83 pre-expansion sites in old_sites.txt (79 survive the current "
        "filter) -- the like-for-like cohort for comparing against the pre-rework runs",
    )
    ap.add_argument("--limit", type=int, default=None, help="screen only K testing features, strided across families (smoke)")
    ap.add_argument(
        "--encodings",
        action="store_true",
        help="also screen the non-canonical crop/surplus/weather ENCODINGS -- the bake-off arms that can "
        "never enter full_feature_set. 59%% of the candidate list and ~3x the pooling cost; off by default "
        "because which encoding wins is settled occasionally, not every run",
    )
    ap.add_argument(
        "--no-perm", action="store_true", help="skip permutation importance on the add-one run (fast dry run)"
    )
    ap.add_argument(
        "--true_lofo",
        action="store_true",
        help="use a TRUE leave-one-family-out holdout (one fold per family) instead of the GroupKFold LOFO; "
        "the lofo_* columns keep their names but are NOT comparable across regimes",
    )
    ap.add_argument(
        "--max_holdout_pct",
        type=float,
        default=0.2,
        help="with --true_lofo: a family above this share of pooled rows is never held out (default 0.2)",
    )
    args = ap.parse_args()

    sites = load_old_sites() if args.old_sites_only else [str(s) for s in get_site_ids()]
    if args.sites:
        sites = sites[: args.sites]  # --sites caps whichever cohort was selected
    site_set = "old83" if args.old_sites_only else "all"
    tasks = ["reg", "clf"] if args.task == "both" else [args.task]
    do_add, do_drop = args.mode in ("both", "add"), args.mode in ("both", "drop")
    run_id = _new_run_id(args.log)
    print(
        f"run_id: {run_id}  (variant={args.variant}, mode={args.mode}, tasks={tasks}, site_set={site_set}, "
        f"n_sites={len(sites)}, true_lofo={args.true_lofo})"
    )

    for task in tasks:
        g = GEOM[task]
        n_buckets = len(g["edges"]) + 1
        # weather candidates are per-task (their tags embed lam); bucketed = the tagged weather singles.
        bucketed = set(_weather_singles(g["lam"]))
        nbr = _nbr_blocks(task) if args.variant == "network" else None
        testing_all = candidates(task, args.encodings)
        testing = smoke_subset(task, args.encodings, args.limit) if args.limit else testing_all
        wide = make_wide_recipe(task, g["edges"], g["vel"], g["lam"], args.encodings, args.variant)
        # build the wide pool ONCE per task and reuse it for both add-one and drop-one
        pool = lean_cook._pool_wide(wide, sites, g["target"], progress_label=f"{task} wide")
        _check_nbr_pool(pool, nbr, task)

        base_feats = BASE_FEATURES + _nbr_cols(nbr)  # donor columns join the BASE -- see build_recipes
        units = _unit_index(task, testing, n_buckets, bucketed, nbr)
        full_row = None  # the add-one run's full_feature_set score, reused by drop-one rather than refitted

        if do_add:
            recipes = build_recipes(base_feats, testing, n_buckets, bucketed, task, nbr)
            xgb = lean_cook.default_xgb(task, "screen")
            print(f"=== {task.upper()} add-one: {len(recipes)} recipes (base + {len(testing)} candidates + full) ===")
            scores = lean_cook.compare_many(
                wide,
                recipes,
                target_col=g["target"],
                task=task,
                pool=pool,
                extra_importance_test=not args.no_perm,
                base_cols=recipes["base"],
                full_names={FULL_NAME},
                true_lofo=args.true_lofo,
                max_holdout_pct=args.max_holdout_pct,
                **xgb,
            )
            full_row = _extract(scores, FULL_NAME)
            log_results(
                scores,
                task,
                g["target"],
                xgb,
                run_id,
                "add_one",
                site_set=site_set,
                n_sites=len(sites),
                true_lofo=args.true_lofo,
                geom=g,
                units=units,
                variant=args.variant,
                path=args.log,
            )
            print(f"logged {len(scores)} {task} add-one recipes")

        if do_drop:
            recipes = build_drop_recipes(base_feats, testing, n_buckets, bucketed, task, nbr)
            xgb = lean_cook.default_xgb(task, "full")
            # full_feature_set is bit-identical between the two modes -- same columns, pool, folds and seed -- so it is fitted once in add-one and its row spliced in here. render_results needs it present in BOTH modes (the drop-one deltas and the synergy join both reference it), hence log twice, fit once.
            reuse = full_row is not None and xgb == lean_cook.default_xgb(task, "screen")
            if reuse:
                recipes.pop(FULL_NAME)
            n_drop = sum(1 for r in recipes if r.startswith("drop_"))
            print(
                f"=== {task.upper()} drop-one: {len(recipes)} fits "
                f"({n_drop} leave-one-out + {'full reused from add-one' if reuse else 'full'}, no perm) ==="
            )
            scores = lean_cook.compare_many(
                wide,
                recipes,
                target_col=g["target"],
                task=task,
                pool=pool,
                extra_importance_test=False,
                true_lofo=args.true_lofo,
                max_holdout_pct=args.max_holdout_pct,
                **xgb,
            )
            if reuse:
                scores = _splice(scores, full_row)
            log_results(
                scores,
                task,
                g["target"],
                xgb,
                run_id,
                "drop_one",
                site_set=site_set,
                n_sites=len(sites),
                true_lofo=args.true_lofo,
                geom=g,
                units=units,
                variant=args.variant,
                path=args.log,
            )
            print(f"logged {len(scores)} {task} drop-one recipes")

    print(f"\ndone. render with:  python render_results.py {run_id}")


if __name__ == "__main__":
    main()
