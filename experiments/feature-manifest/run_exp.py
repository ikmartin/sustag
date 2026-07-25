"""run_exp.py -- the feature-manifest experiment (add-one screen + drop-one leave-one-out).

Fix a strong 14-feature BASE (statics + geography + neighbor + calendar), then run two duals against
it, both over one shared wide pool per task:
  * add-one  -- add each currently-absent feature ALONE over the base; marginal LOFO gain. Scores what
    perm importance structurally can't (absent weather/gridMET/crops/surplus/lags/rolling) and breaks
    the collinearity that deflated the weather block's perm.
  * drop-one -- remove each feature from the FULL model; LOFO loss. The synergy detector: a feature
    weak added alone but costly to remove is load-bearing only in company.

Both run in ONE invocation (--mode both, default), tagged with a shared run_id, into the unified
log.json. Per-task shipped geometry; fast-screen XGB.

    python run_exp.py                                  # both modes, both tasks
    python run_exp.py --mode add --task reg            # add-one only, regression
    python run_exp.py --sites 10 --limit 4 --no-perm   # fast smoke

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
from src.features.transformers import flatten_buckets, merge_on_date

_HERE = Path(__file__).resolve().parent
LOG_PATH = _HERE / "log.json"  # unified log: every entry carries run_id + mode (add_one/drop_one)

# ── the fixed base: statics + geography + neighbor + calendar (all non-bucketed) ──
BASE_FEATURES = [
    "rest_of_state_nitrate_lag1",
    "rest_of_state_nitrate_lag2",
    "mean_dist_to_sensor",
    "max_dist_to_sensor",
    "tile_frac_basin",
    "tile_frac_ag",
    "tot_tiles92",
    "tot_contact",
    "tot_bfi",
    "lat",
    "lon",
    "log_basin_area",
    "doy_sin",
    "doy_cos",
]

# ── every candidate currently ABSENT from the base, one screened per run ──
_WEATHER = [
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
# composition shares; surplus -> per-bucket mean intensity), whole-basin exp-decay sum with no
# buckets (whole), and whole-basin normalized with no buckets (norm_whole). total_kg_N is dropped
# entirely -- only surplus_kgha survives. The encodings share base names, so each variant's columns
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
_CANON_BLOCKS = {"crops_norm", "surplus_norm"}  # the crop/surplus encodings kept in the full run
_STATIC_CAT = ["cat_tiles92", "cat_bfi", "cat_contact"]
_EXTRA_LAGS = ["rest_of_state_nitrate_lag3", "rest_of_state_nitrate_lag5"]
_ROLLING = [
    "roll_n_avg_except_this7d",
    "roll_n_avg_except_this14d",
    "roll_n_avg_except_this30d",
    "roll_n_avg_except_this60d",
]
# calendar: the first seasonal harmonic (2x frequency), screened as a sin+cos PAIR -- a Fourier
# harmonic needs both terms to represent arbitrary phase, so the pair is the meaningful unit.
_CALENDAR = ["doy_harmonic2"]

TESTING_FEATURES = _WEATHER + list(_BLOCKS) + _STATIC_CAT + _EXTRA_LAGS + _ROLLING + _CALENDAR
_BUCKETED = set(_WEATHER)  # single vars that expand to _b0.._bN (crops/surplus are _BLOCKS now)
_MULTI = {"doy_harmonic2": ["doy_sin2", "doy_cos2"]}  # non-bucketed candidates spanning >1 column

# ── per-task shipped geometry (edges, vel, lam) + target ──
GEOM = {
    "reg": {"edges": (50_000,), "vel": 2.1, "lam": 100_000, "target": "nitrate_con"},
    "clf": {"edges": (2_000, 5_000), "vel": 2.1, "lam": 20_000, "target": "violation"},
}


def _tag_values(frame, suffix, keep=None):
    """Rename an aggregated frame's value columns (all but the date/year/bucket keys) by appending
    `suffix`, so a variant coexists with its siblings under distinct names. `keep`, if given, first
    restricts the value columns to that list (used to drop total_kg_N, keeping only surplus_kgha).

    NOTE `date` must be in the protected keys: weather-with-lag frames are DAILY (date-keyed), so
    without it the date column gets a suffix and flatten_buckets then can't find its row key."""
    struct = [c for c in ("date", "year", "bucket") if c in frame.columns]
    val = [c for c in frame.columns if c not in struct]
    if keep is not None:
        val = [c for c in val if c in keep]
        frame = frame[struct + val]
    return frame.rename(columns={c: f"{c}{suffix}" for c in val})


def make_wide_recipe(task, edges, vel, lam):
    """Build a wide_recipe(site) emitting EVERY candidate column + the task target. Crops and surplus
    are emitted in five encodings each (exp-decay sum _expT, plain sum _expF, per-bucket normalized,
    whole-basin sum _whole, and whole-basin normalized _norm_whole), all tagged so they coexist;
    surplus keeps only surplus_kgha (total_kg_N dropped). `site` may be a site_uid (str) or a SiteData."""
    e = list(edges)
    KGHA = ["surplus_kgha"]  # the only surplus column screened (total_kg_N omitted)

    def wide_recipe(site):
        kw = {"site_uid": site} if isinstance(site, str) else {"site_data": site}
        uid = site if isinstance(site, str) else site.site_uid
        n = daily_nitrate(**kw)  # spine carrier (only its index is used downstream)
        if task == "reg":
            target = nitrate_daily_rolling(**kw, window=1, min_obs=1).rename("nitrate_con")
        else:
            target = nitrate_violations_rolling(**kw, window=1, min_obs=1).rename("violation")
        wb = flatten_buckets(
            _tag_values(agg_weather_w_lag(**kw, edges=e, exp=True, lam=lam, water_velocity=vel), f"_expT{lam}k")
        )  # all 17 weather
        wb_short = flatten_buckets(
            _tag_values(agg_weather_w_lag(**kw, edges=e, exp=True, lam=lam // 5, water_velocity=vel), f"_expT{lam//5}k")
        )  # all 17 weather
        wb_n = flatten_buckets(
            _tag_values(agg_weather_w_lag(**kw, edges=e, exp=False, water_velocity=vel), "_expF")
        )  # all 17 weather
        # crops: five encodings -> {Class}_expT/_expF (weighted/plain bucketed sum), pct_{class}
        # (per-bucket share), {Class}_whole (whole-basin sum), pct_{class}_whole (whole-basin share)
        crops_t = flatten_buckets(_tag_values(agg_crops(**kw, edges=e, lam=lam, exp=True), "_expT"))
        crops_f = flatten_buckets(_tag_values(agg_crops(**kw, edges=e, lam=lam, exp=False), "_expF"))
        crops_n = flatten_buckets(agg_crops_normalized(**kw, edges=e))  # pct_* composition (no exp/lam)
        crops_w = flatten_buckets(_tag_values(agg_crops(**kw, edges=(), lam=lam, exp=True), "_whole"))
        crops_nw = flatten_buckets(_tag_values(agg_crops_normalized(**kw, edges=()), "_whole"))  # pct_*_whole
        # surplus: surplus_kgha only -> _expT/_expF/_norm (bucketed), _whole/_norm_whole (whole basin)
        surp_t = flatten_buckets(_tag_values(agg_surplus(**kw, edges=e, lam=lam, exp=True), "_expT", keep=KGHA))
        surp_f = flatten_buckets(_tag_values(agg_surplus(**kw, edges=e, lam=lam, exp=False), "_expF", keep=KGHA))
        surp_n = flatten_buckets(_tag_values(agg_surplus_normalized(**kw, edges=e), "_norm", keep=KGHA))
        surp_w = flatten_buckets(_tag_values(agg_surplus(**kw, edges=(), lam=lam, exp=True), "_whole", keep=KGHA))
        surp_nw = flatten_buckets(_tag_values(agg_surplus_normalized(**kw, edges=()), "_norm_whole", keep=KGHA))
        doy = doy_climatology_pure_signal(n)
        lagged = [nitrate_avg_except_this(uid, shift=k) for k in (1, 2, 3, 5)]
        roll = rolling_nitrate_avg_except_this(uid, windows=(7, 14, 30, 60))
        frame = merge_on_date(
            [
                target,
                wb,
                wb_short,
                wb_n,
                crops_t,
                crops_f,
                crops_n,
                crops_w,
                crops_nw,
                surp_t,
                surp_f,
                surp_n,
                surp_w,
                surp_nw,
                doy,
                *lagged,
                roll,
            ],
            spine=n.index,
        )
        for k, v in site_static(**kw).items():  # ALL statics incl. cat_* fingerprints
            frame[k] = v
        return frame

    wide_recipe.__name__ = "wide_recipe"
    return wide_recipe


def _expand(feat: str, n_buckets: int) -> list[str]:
    """Candidate name -> its column(s): a _BLOCK -> all its (bucketed) member columns; a bucketed
    single var -> its _b0.._b{n-1} columns; a _MULTI candidate -> its explicit column list (e.g.
    doy_harmonic2 -> sin2+cos2); else the name itself. Deterministic from geometry, so no pool is
    needed (compare_many drops non-materializing cols)."""
    if feat in _WHOLE_BLOCKS:
        return list(_BLOCKS[feat])  # single whole-basin column per member, no _b suffix
    if feat in _BLOCKS:
        return [f"{bn}_b{k}" for bn in _BLOCKS[feat] for k in range(n_buckets)]
    if feat in _BUCKETED:
        return [f"{feat}_b{k}" for k in range(n_buckets)]
    if feat in _MULTI:
        return list(_MULTI[feat])
    return [feat]


FULL_NAME = "full_feature_set"


def build_recipes(base: list[str], testing: list[str], n_buckets: int) -> dict[str, set]:
    """{'base': base_cols} + one 'add_<feat>' per candidate (base_cols | expand(feat)) + a final
    'full_feature_set' holding every column EXCEPT the non-canonical crop/surplus encodings -- the
    full run keeps only the normalized crop/surplus block (_CANON_BLOCKS), so it's a sensible
    'everything' model rather than five collinear copies. Insertion order puts the full run last."""
    base_cols = {c for f in base for c in _expand(f, n_buckets)}
    recipes = {"base": set(base_cols)}
    full = set(base_cols)
    for f in testing:
        cols = set(_expand(f, n_buckets))
        recipes[f"add_{f}"] = base_cols | cols
        if (
            f not in _BLOCKS or f in _CANON_BLOCKS
        ):  # keep only the canonical (per-bucket norm) crop/surplus block in full
            full |= cols
    recipes[FULL_NAME] = full
    return recipes


def _full_units(base: list[str], testing: list[str]) -> list[str]:
    """The feature units that live in the full_feature_set and can therefore be dropped: every base
    feature, plus every candidate the full run keeps (all non-block candidates + the canonical
    per-bucket norm crop/surplus blocks; the non-canonical crop/surplus encodings aren't in full)."""
    return list(base) + [f for f in testing if f not in _BLOCKS or f in _CANON_BLOCKS]


def build_drop_recipes(base: list[str], testing: list[str], n_buckets: int) -> dict[str, set]:
    """'full_feature_set' + one 'drop_<f>' per droppable unit = full MINUS f's columns. The dual of
    the add-one screen: a NEGATIVE score delta vs full means removing f HURT (f is load-bearing).
    Cross-referenced with the add-one manifest, small add-one Δ + large negative drop Δ = synergy
    (a feature useless alone but load-bearing in company). Full run scored first (reference)."""
    full = build_recipes(base, testing, n_buckets)[FULL_NAME]
    recipes = {FULL_NAME: set(full)}
    for f in _full_units(base, testing):
        recipes[f"drop_{f}"] = full - set(_expand(f, n_buckets))
    return recipes


def _to_native(o):
    import numpy as np

    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON-serializable: {type(o).__name__}")


def log_results(scores, task, target_col, xgb, run_id, mode, path: Path = LOG_PATH) -> None:
    """Append one entry per recipe to the unified `path`, keyed by max+1 integer (runs coexist, never
    overwrite). Each entry carries `run_id` (one per run_exp invocation) and `mode` ('add_one' or
    'drop_one') on top of the fulltrain-compatible schema. `scores` is a lean_cook.compare_many
    result."""
    imps = scores.attrs.get("importance", {})
    perms = scores.attrs.get("importance_perm", {})
    log = {}
    if path.exists() and path.stat().st_size > 0:
        log = json.loads(path.read_text())
    key = max((int(k) for k in log), default=-1) + 1
    for name in scores.index:
        imp = imps.get(name)
        imp_perm = perms.get(name)
        log[str(key)] = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_id": run_id,
            "mode": mode,
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
    ap.add_argument(
        "--mode",
        choices=["both", "add", "drop"],
        default="both",
        help="which run type(s) to run: add-one screen, drop-one leave-one-out, or both (default)",
    )
    ap.add_argument("--sites", type=int, default=None, help="cap to the first N sites (smoke test)")
    ap.add_argument("--limit", type=int, default=None, help="screen only the first K testing features (smoke)")
    ap.add_argument(
        "--no-perm", action="store_true", help="skip permutation importance on the add-one run (fast dry run)"
    )
    ap.add_argument("--min-rows", type=int, default=500)
    args = ap.parse_args()

    testing = TESTING_FEATURES[: args.limit] if args.limit else TESTING_FEATURES
    sites = [str(s) for s in get_site_ids()][: args.sites] if args.sites else None
    tasks = ["reg", "clf"] if args.task == "both" else [args.task]
    do_add, do_drop = args.mode in ("both", "add"), args.mode in ("both", "drop")
    run_id = _new_run_id()
    print(f"run_id: {run_id}  (mode={args.mode}, tasks={tasks})")

    for task in tasks:
        g = GEOM[task]
        n_buckets = len(g["edges"]) + 1
        wide = make_wide_recipe(task, g["edges"], g["vel"], g["lam"])
        # build the wide pool ONCE per task and reuse it for both add-one and drop-one
        pool = lean_cook._pool_wide(
            wide, sites or get_site_ids(), g["target"], min_rows=args.min_rows, progress_label=f"{task} wide"
        )

        if do_add:
            recipes = build_recipes(BASE_FEATURES, testing, n_buckets)
            print(f"=== {task.upper()} add-one: {len(recipes)} recipes (base + {len(testing)} candidates + full) ===")
            scores = lean_cook.compare_many(
                wide,
                recipes,
                target_col=g["target"],
                task=task,
                pool=pool,
                extra_importance_test=not args.no_perm,
                min_rows=args.min_rows,
                base_cols=recipes["base"],
                full_names={FULL_NAME},
            )
            log_results(scores, task, g["target"], lean_cook._DEFAULT_XGB, run_id, "add_one")
            print(f"logged {len(scores)} {task} add-one recipes")

        if do_drop:
            recipes = build_drop_recipes(BASE_FEATURES, testing, n_buckets)
            print(
                f"=== {task.upper()} drop-one: {len(recipes)} recipes (full + {len(recipes) - 1} leave-one-out, no perm) ==="
            )
            scores = lean_cook.compare_many(
                wide,
                recipes,
                target_col=g["target"],
                task=task,
                pool=pool,
                extra_importance_test=False,
                min_rows=args.min_rows,
            )
            log_results(scores, task, g["target"], lean_cook._DEFAULT_XGB, run_id, "drop_one")
            print(f"logged {len(scores)} {task} drop-one recipes")

    print(f"\ndone. render with:  python render_results.py {run_id}")


if __name__ == "__main__":
    main()
