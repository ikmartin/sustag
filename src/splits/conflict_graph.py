"""Custom train/test splits over the basin network.

There are two ways that basins can overlap:
  * spatially  — one basin is nested in (or overlaps) another, so they share drainage area, weather, surplus and crop cells
  * temporally — the nitrate records of the two sites overlap in calendar time (or sit close enough that nitrate's months-long autocorrelation still links them).

When both of these occur at once, you get data leakage...potentially. Updating this docstring at the end of the project: it seems that the leakage wasn't that big of an issue after all, at least for the classification target. Nevertheless we keep it, as it was annioying problem to solve.

This script builds a "conflict graph" whose edges are exactly the both-axes pairs, takes its connected components as indivisible split groups, and assigns whole groups to folds. It reuses code written for the manual basin building "basin3" method.

Spatial relatedness comes from the directed basin-containment graph (data.aux.get_basin_graph); temporal relatedness from each site's nitrate date range, padded by a buffer to account for autocorrelation.

See also: data/aux/make_aux.py (build_basin_graph / get_basin_graph).
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from src.data.access import get_basin_graph, get_site_ids, get_water

# Default autocorrelation buffer. Exists because nitrate surplus is a highly
# autocorrelated signal, this is an extra super duper safeguard against leakage
_DEFAULT_BUFFER = "100D"


def nitrate_span(site_uid: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Return (first, last) timestamp of a site's non-null nitrate record.

    Returns None if the site has no nitrate_con column or no observations.
    """
    try:
        water = get_water(site_uid)
    except FileNotFoundError:
        return None
    if "nitrate_con" not in water.columns:
        return None
    series = water["nitrate_con"].dropna()
    if series.empty:
        return None
    return series.index.min(), series.index.max()


def overlaps(span_a, span_b, buffer: str = _DEFAULT_BUFFER) -> bool:
    """Do two nitrate records overlap in time, within an autocorrelation buffer?"""
    if span_a is None or span_b is None:
        return False
    buf = pd.Timedelta(buffer)
    (start_a, end_a), (start_b, end_b) = span_a, span_b
    return (start_a - buf <= end_b) and (start_b - buf <= end_a)


def build_conflict_graph(buffer: str = _DEFAULT_BUFFER) -> nx.Graph:
    """Undirected graph whose edges denote basin containment.

    Nodes <-> sites. An edge a—b is added when one site is contained in the other AND the sites overlap temporally (see the `overlaps` method). Due to some sites being incredibly close, some basins DO actually coincide, creating cycles in this graph.
    """
    spatial = get_basin_graph().to_undirected()
    # Keyed on the GRAPH's nodes, not get_site_ids(). get_basin_graph adds nodes implicitly from the containment edge list, so it carries every site with a basin (123) while the water cohort carries only those surviving the site filter (116). Keyed on the cohort, the 7 extras missed the lookup and `spans.get` returned None -- indistinguishable from "no nitrate record" at the call site below, so `overlaps` was False and EVERY edge touching them was silently dropped. They became conflict-free singletons that holdout_split could place opposite sites they genuinely conflict with, which is exactly the leak this module exists to prevent (audit_split, which builds its spans over train|test, then reported them as 'hard').
    spans = {s: nitrate_span(s) for s in spatial.nodes}

    conflict = nx.Graph()
    conflict.add_nodes_from(spatial.nodes)
    for a, b in spatial.edges:
        if overlaps(spans.get(a), spans.get(b), buffer=buffer):
            conflict.add_edge(a, b)
    return conflict


def split_groups(buffer: str = _DEFAULT_BUFFER) -> dict[str, int]:
    """Map each site_uid to an integer group id (its conflict component).

    All sites within a group must stay on the same side of any train/test split. Sites with no hard conflict are singleton groups.
    """
    conflict = build_conflict_graph(buffer=buffer)
    groups: dict[str, int] = {}
    for gid, component in enumerate(nx.connected_components(conflict)):
        for site in component:
            groups[site] = gid
    return groups


def make_folds(n_splits: int = 5, buffer: str = _DEFAULT_BUFFER, shuffle=False, random_state=None) -> pd.DataFrame:
    """Assign sites to ``n_splits`` folds without breaking any conflict group.

    Uses GroupKFold with the conflict-component id as the group key, so no fold boundary ever separates two both-axes-related basins. Returns a DataFrame indexed by site_uid with columns ``group`` (conflict component) and ``fold`` (0..n_splits-1). Fold f is the test set for split f; the rest is train.
    """
    groups = split_groups(buffer=buffer)
    sites = sorted(groups)
    gids = [groups[s] for s in sites]

    fold = pd.Series(-1, index=sites, dtype=int)
    splitter = GroupKFold(n_splits=n_splits, shuffle=shuffle, random_state=random_state)
    # GroupKFold needs an X of the right length; the values are unused.
    for f, (_, test_idx) in enumerate(splitter.split(sites, groups=gids)):
        for i in test_idx:
            fold.iloc[i] = f

    out = pd.DataFrame({"group": [groups[s] for s in sites], "fold": fold.values}, index=sites)
    out.index.name = "site_uid"
    return out


def holdout_split(test_size: float = 0.2, buffer: str = _DEFAULT_BUFFER, seed: int = 0):
    """Single train/test split that keeps whole conflict groups together.

    Shuffles the conflict groups (deterministically, via ``seed``) and adds whole groups to the test set until it reaches ~``test_size`` of the sites, skipping a group that would overshoot the target by more than half a group's slack so smaller groups can fill in. Returns (train_sites, test_sites) as sorted lists.
    """
    import random

    groups: dict[int, list[str]] = {}
    for site, gid in split_groups(buffer=buffer).items():
        groups.setdefault(gid, []).append(site)

    all_sites = sorted(s for members in groups.values() for s in members)
    target = test_size * len(all_sites)

    order = list(groups.values())
    random.Random(seed).shuffle(order)

    test: set[str] = set()
    for members in order:
        if len(test) >= target:
            break
        if not test or len(test) + len(members) <= 1.5 * target:
            test.update(members)
    train = [s for s in all_sites if s not in test]
    return train, sorted(test)


def lodo_neighbours(d_km: float, sites=None, prefer: str = "flow") -> dict[str, set[str]]:
    """{site -> the containment-connected sites within `d_km` of it}. The LODO_d buffer.

    Edge distances come from the metric columns `_make_aux` writes onto processed/aux/basin_containment_graph.parquet, so nothing is recomputed here. `prefer="flow"` uses along-network distance (`flow_dist_m`) and falls back to straight-line (`euc_dist_m`) on the ~7% of edges where the D8 network cannot route child to parent; `prefer="euc"` forces straight-line. An edge whose chosen distance is NaN is treated as WITHIN the buffer -- the conservative reading, since an unresolvable distance is not evidence of separation.
    """
    g = get_basin_graph().to_undirected()
    keep = set(sites) if sites is not None else set(get_site_ids())
    d_m = float(d_km) * 1000.0
    out: dict[str, set[str]] = {s: set() for s in keep}
    for a, b, attr in g.edges(data=True):
        if a not in keep or b not in keep:
            continue
        primary, backup = ("flow_dist_m", "euc_dist_m") if prefer == "flow" else ("euc_dist_m", "flow_dist_m")
        dist = attr.get(primary)
        if dist is None or (isinstance(dist, float) and np.isnan(dist)):
            dist = attr.get(backup)
        if dist is None or (isinstance(dist, float) and np.isnan(dist)) or dist <= d_m:
            out[a].add(b)
            out[b].add(a)
    return out


def lodo_folds(sites, d_km: float, prefer: str = "flow") -> list[tuple[str, set[str]]]:
    """LODO_d folds: [(test_site, sites_excluded_from_training), ...], one per site.

    The excluded set is the test site plus its `lodo_neighbours` -- those rows belong to NEITHER train nor test, which is why this cannot be expressed as a GroupKFold `groups` vector.

    Positioning (see the plan / notes/future-cv-work-report.md): LODO_d sits between LOSO and LOFO. LOSO leaves a site's nested neighbours in training, so the direct leakage channel stays open; LOFO discards a whole family (up to ~25% of rows), which is more pessimistic than deployment, where the model trains on every available sensor. LODO_d closes the leakage channel while keeping ~120 of 123 sites in training. NOTE it does not increase the test site's distance to the nearest TRAINING site (that saturates around 22 km at any d, because close neighbours on other rivers are not containment-connected) -- it is a leakage control, not a spatial one.
    """
    order = list(sites)
    nbrs = lodo_neighbours(d_km, sites=order, prefer=prefer)
    return [(s, {s} | nbrs.get(s, set())) for s in order]


def audit_split(train_sites, test_sites, buffer: str = _DEFAULT_BUFFER) -> pd.DataFrame:
    """List every cross-boundary leak between a train and a test site.

    Returns one row per train/test pair that is spatially related, temporally overlapping, or both, with a ``severity`` of 'hard' (both axes — should never appear if the split respects conflict groups), 'spatial' or 'temporal'. An empty 'hard' subset is the correctness check for make_folds / holdout_split.
    """
    spatial = get_basin_graph().to_undirected()
    spans = {s: nitrate_span(s) for s in set(train_sites) | set(test_sites)}
    train_set, test_set = set(train_sites), set(test_sites)

    rows = []
    for a in train_set:
        for b in spatial.neighbors(a):
            if b not in test_set:
                continue
            spatial_rel = True
            temporal_rel = overlaps(spans.get(a), spans.get(b), buffer=buffer)
            severity = "hard" if temporal_rel else "spatial"
            rows.append({"train_site": a, "test_site": b, "severity": severity})
    return pd.DataFrame(rows, columns=["train_site", "test_site", "severity"])
