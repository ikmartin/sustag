"""The N-nearest monitored donors on the reach graph, for the neighbour-count sweep.

Generalises features.neighbor_nitrate from "one donor per direction" to "the N most similar donors, ranked across both directions at once". Everything expensive is reused from features.py -- the cohort context, the edge distance, the anomaly baseline -- so the only new logic here is the ranking and the slot filling.

THE CANDIDATE SET IS THE FULL CONTAINMENT GRAPH'S ADJACENCY, and that is not an approximation of "everything reachable": the graph is transitively closed (measured, 0 of 116 sites have a directed-reachable site that is not already a direct edge), so ancestors-union-descendants at any depth IS the neighbour list. That also makes every candidate pair a real edge carrying its own D8 flow distance, which is why no path-summing is needed.

SIBLINGS ARE EXCLUDED by construction -- only ancestors and descendants are enumerated. Two basins nested in the same parent share no water, so any correlation between them travels through shared weather or land use, both of which the crop/surplus/weather blocks already carry. Admitting them would let the donor block act as a land-use proxy dressed as a transport signal.

Coverage on the 116-site cohort: >=1 donor 87.9%, >=2 74.1%, >=3 59.5%, >=4 44.0%; median 3, max 19, and 14 sites (12.1%) have none at any depth. The high slots are therefore sparse ON PURPOSE and `num_neighbors` is emitted so a tree can condition on how many are real.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.features.features import (
    _basin_area_or,
    _edge_dist_m,
    _except_this_mean,
    _neighbor_context,
)

FIELDS = ("anom_lag1", "anom_lag3", "level_lag1", "level_lag3", "area_ratio", "dist_abs", "dist_signed", "is_up")
GLOBAL_FIELDS = ("num_neighbors",)


def _directed_neighbours(site_uid, graph, areas):
    """({upstream}, {downstream}) monitored donors, disjoint. Mirrors _best_neighbors' mutual-containment tie-break: a uid appearing in both directions is assigned by area, ties breaking upstream, so len(up) + len(down) is a true count."""
    if site_uid not in graph:
        return set(), set()
    own = _basin_area_or(areas, site_uid)
    up = set(graph.predecessors(site_uid))  # basins nested inside this one
    down = set(graph.successors(site_uid))  # enclosing basins
    for u in up & down:
        (down if _basin_area_or(areas, u, np.inf) <= own else up).discard(u)
    return up, down


def _edge_dist(graph, site_uid, donor, is_up):
    """Metres to `donor` along its containment edge. Edge direction is child -> parent, so an upstream donor is this site's CHILD and a downstream one is its parent."""
    return _edge_dist_m(graph, donor, site_uid) if is_up else _edge_dist_m(graph, site_uid, donor)


def _rank_key(select, graph, areas, site_uid, own):
    """Sort key over (donor, is_up) pairs: 'dist' by metres along the edge, 'area' by |log10 area ratio| -- closest in drainage scale, the symmetric form _best_either uses. Non-finite sorts last either way, so a donor with no measurable distance never wins a slot it cannot justify."""

    def key(item):
        donor, is_up = item
        if select == "dist":
            v = _edge_dist(graph, site_uid, donor, is_up)
        else:
            a = _basin_area_or(areas, donor)
            v = abs(np.log10(a / own)) if np.isfinite(a) and np.isfinite(own) and a > 0 and own > 0 else np.nan
        return (not np.isfinite(v), v if np.isfinite(v) else 0.0, donor)  # donor breaks ties deterministically

    return key


def nearest_neighbors(site_uid, n=4, select="area", prefix="", lags=(1, 3)):
    """The `n` most similar monitored donors on the reach graph, as model-ready columns.

    Parameters
    ----------
    site_uid : str
        The modelled site. A uid absent from the graph yields the full column set, all NaN.
    n : int, default 4
        Number of donor slots. Slots beyond the available donors are emitted NaN, never omitted.
    select : {'area', 'dist'}, default 'area'
        Ranking rule: closest in drainage scale, or nearest in metres along the containment edge.
    prefix : str, default ''
        Prepended to every column name, so two rankings can coexist in one pool (e.g. 'area_' -> 'area_nbr1_anom_lag1').
    lags : tuple of int, default (1, 3)
        Days back for both the anomaly and the level columns. 0 is legal and emits a same-day column; declining it is the caller's call, as in recipes._NBR_LAGS_*.

    Returns
    -------
    (pd.DataFrame, dict)
        A date-keyed frame of the time-varying columns, and the static per-site scalars to broadcast.

    See Also
    --------
    src.features.features.neighbor_nitrate : the shipped one-donor-per-direction block this generalises.
    """
    graph, areas, wide, sums, counts, spine = _neighbor_context(False)  # full graph: transitively closed, see module docstring
    up, down = _directed_neighbours(site_uid, graph, areas)
    own = _basin_area_or(areas, site_uid)
    baseline = _except_this_mean(wide, sums, counts, site_uid)
    nan_col = pd.Series(np.nan, index=spine)

    ranked = sorted([(d, True) for d in up] + [(d, False) for d in down], key=_rank_key(select, graph, areas, site_uid, own))

    def anom(uid, k):
        """The donor's deviation from the statewide daily mean (this site excluded), shifted k days."""
        if uid is None or uid not in wide.columns:
            return nan_col
        return (wide[uid] - baseline).asfreq("D").shift(k)

    def level(uid, k):
        """The donor's raw level shifted k days -- the only channel that can move the between-site ranker, since an anomaly is mean-zero per site by construction."""
        if uid is None or uid not in wide.columns:
            return nan_col
        return wide[uid].asfreq("D").shift(k)

    cols, stats = {}, {f"{prefix}num_neighbors": float(len(up) + len(down))}
    for slot in range(1, n + 1):
        p = f"{prefix}nbr{slot}_"
        donor, is_up = ranked[slot - 1] if slot <= len(ranked) else (None, None)
        for k in lags:
            cols[f"{p}anom_lag{k}"] = anom(donor, k)
            cols[f"{p}level_lag{k}"] = level(donor, k)
        a = _basin_area_or(areas, donor) if donor is not None else np.nan
        stats[f"{p}area_ratio"] = float(np.log10(a / own)) if np.isfinite(a) and np.isfinite(own) and a > 0 and own > 0 else np.nan
        d = _edge_dist(graph, site_uid, donor, is_up) if donor is not None else np.nan
        stats[f"{p}dist_abs"] = d
        # 'signed' folds direction into the sign; the sign variant carries no is_up, so this column is the ONLY place direction lives there.
        stats[f"{p}dist_signed"] = (-d if is_up else d) if donor is not None and np.isfinite(d) else np.nan
        stats[f"{p}is_up"] = float(is_up) if donor is not None else np.nan

    return pd.concat(cols, axis=1).rename_axis("date").reset_index(), stats


def column_names(n=4, select_prefixes=("area_", "dist_"), lags=(1, 3)):
    """Every column nearest_neighbors emits under `select_prefixes`, for building masks without a pool. Deterministic, and run_exp re-checks it against the built pool so a drift here fails loudly rather than silently shrinking an arm."""
    out = []
    for p in select_prefixes:
        out.append(f"{p}num_neighbors")
        for slot in range(1, n + 1):
            base = f"{p}nbr{slot}_"
            out += [f"{base}anom_lag{k}" for k in lags] + [f"{base}level_lag{k}" for k in lags]
            out += [f"{base}area_ratio", f"{base}dist_abs", f"{base}dist_signed", f"{base}is_up"]
    return out
