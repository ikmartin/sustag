"""The 27 arms per task, as column subsets over one shared pool.

THREE AXES, SEPARATED. Exps 33-36c varied direction encoding and selection rule but only ever at ONE donor per direction, and confounded the rule with the graph (33 = area+immediate, 36 = dist+full). Here neighbour COUNT (1-4), INDICATOR encoding (sign/flag/rednt) and SIMILARITY rule (area/dist) are crossed independently over one candidate set, so each delta isolates one axis.

    island_<task>                                   the shipped donor-free recipe -- the paired baseline
    nbr_all_no_lag0_{area,dist}                     exps 33 / 36 unchanged, for continuity with the logged results
    nearest_{1,2,3,4}_nbr_{sign,flag,rednt}_{area,dist}

TWO NAMING COLLISIONS FORCE THE POOL'S SHAPE. `flag` and `rednt` both want a column meaning "distance to donor k" but holding different values (absolute vs signed), and the area and dist rankings put DIFFERENT SITES in slot k. Neither can share a name in one frame, so the pool carries `{rule}_nbr{k}_dist_abs` AND `{rule}_nbr{k}_dist_signed` under both rule prefixes, and each arm selects the form it wants. XGBoost never sees the names, only the selection.

Masks are a callable of the pool, not a literal dict, for the reason exps.py:823 gives: the baseline is "whatever the shipped recipe currently emits", so deriving it as (pool columns - donor columns) cannot drift when recipes.py changes.
"""

from __future__ import annotations

from neighbors import column_names as _nearest_cols

N_SLOTS = (1, 2, 3, 4)
INDICATORS = ("sign", "flag", "rednt")
RULES = ("area", "dist")
LAGS = (1, 3)

# The two exp-33/36 bases, emitted by features.neighbor_nitrate(encoding='both') and prefixed so the
# area-ranked and dist-ranked copies can coexist. 14 columns each, matching the logged runs.
BASE_PREFIX = {"area": "nbase_area_", "dist": "nbase_dist_"}


def base_block_cols(rule: str) -> list[str]:
    """The 14 columns of one exp-33/36 donor block under its pool prefix. Mirrors features.neighbor_nitrate's encoding='both' emission; run_exp re-checks these against the built pool."""
    p = BASE_PREFIX[rule]
    out = []
    for sfx in ("_up", "_down"):
        out += [f"{p}nbr_anom_lag{k}{sfx}" for k in LAGS]
        out += [f"{p}nbr_level_lag1{sfx}", f"{p}nbr2_anom_lag1{sfx}"]
        out += [f"{p}distance{sfx}", f"{p}nbr_area_ratio{sfx}"]
    out += [f"{p}nbr_n_up", f"{p}nbr_n_down"]
    return out


def slot_cols(n: int, indicator: str, rule: str) -> list[str]:
    """The donor columns for one `nearest_*` arm: n slots plus the shared count.

    The indicator picks the distance form and whether is_up rides along -- 'sign' folds direction into the sign and carries no flag, 'flag' keeps magnitude and flag apart, 'rednt' carries both and is redundant on purpose (a tree can recover the flag from the sign with one split at 0; the question is whether that split costs anything).
    """
    if indicator not in INDICATORS:
        raise ValueError(f"indicator must be one of {INDICATORS}, got {indicator!r}")
    p = f"{rule}_"
    out = [f"{p}num_neighbors"]
    for slot in range(1, n + 1):
        b = f"{p}nbr{slot}_"
        out += [f"{b}anom_lag{k}" for k in LAGS] + [f"{b}level_lag{k}" for k in LAGS]
        out.append(f"{b}area_ratio")
        out.append(f"{b}dist_abs" if indicator == "flag" else f"{b}dist_signed")
        if indicator in ("flag", "rednt"):
            out.append(f"{b}is_up")
    return out


def donor_cols() -> list[str]:
    """Every donor column the pool carries, across both bases and both rankings. What `base` is defined by subtracting."""
    return _nearest_cols(n=max(N_SLOTS), select_prefixes=tuple(f"{r}_" for r in RULES), lags=LAGS) + [
        c for r in RULES for c in base_block_cols(r)
    ]


def arm_names(task: str) -> list[str]:
    """Every arm name for `task`, in table order. The FIRST is the paired baseline."""
    return (
        [f"island_{task.upper()}", "nbr_all_no_lag0_area", "nbr_all_no_lag0_dist"]
        + [f"nearest_{n}_nbr_{i}_{r}" for n in N_SLOTS for i in INDICATORS for r in RULES]
    )


def build_masks(pool, task: str) -> dict[str, list[str]]:
    """{arm_name: column list} over `pool`, baseline first.

    Raises if any named donor column is absent from the pool. That check is the whole safety margin here: cook drops unknown columns silently, so a drift between this module and neighbors.py would collapse arms onto `base` and log a winner with a 0.0000 delta and no error anywhere.
    """
    from src.eval.cook import _STRUCTURAL

    donors = donor_cols()
    missing = [c for c in donors if c not in pool.columns]
    if missing:
        raise RuntimeError(
            f"{len(missing)} donor column(s) named here are absent from the pool: {missing[:8]}"
            f"{' ...' if len(missing) > 8 else ''}. neighbors.py and arms.py have drifted apart."
        )

    drop = set(_STRUCTURAL) | {"nitrate_con", "violation"}
    cols = [c for c in pool.columns if c not in drop]
    donor_set = set(donors)
    base = [c for c in cols if c not in donor_set]

    out = {f"island_{task.upper()}": base}
    for r in RULES:
        out[f"nbr_all_no_lag0_{r}"] = base + base_block_cols(r)
    for n in N_SLOTS:
        for i in INDICATORS:
            for r in RULES:
                out[f"nearest_{n}_nbr_{i}_{r}"] = base + slot_cols(n, i, r)
    return out
