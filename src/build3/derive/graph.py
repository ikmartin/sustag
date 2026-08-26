"""The site graph: which node is downstream of which, and which catchments drain to each node first.

ONE WALK, TWO ARTIFACTS. Labelling every reach with the first node found walking DOWNSTREAM from it answers both questions at once. The label is the reach's owner, so the labels partition the network into **incremental catchment sets** -- what drains to a node without passing another node first. And the same walk started from a node's own reach lands on the node immediately below it, which is a **DAG edge**.

THE CLOSURE DOES NOT REPRODUCE A BASIN, AND CANNOT. `accumulate.upstream` admits BOTH branches at a divergence, because walking up from a reach collects everything whose water can reach it. A partition cannot: a reach that splits flows to two places, and an owner map gives it one. So the two walks are not inverses wherever NHDPlus records a divergence, and the discrepancy is largest exactly where deltas are. `accumulate.upstream` is the authority for basins; what this module provides is the DAG, verified separately: every edge M -> N has M inside `accumulate.upstream(N)`.

EDGES COME FROM FLOW, NOT FROM GEOMETRY. M -> N because water leaving M reaches N without passing another node. Basin containment is the CONSEQUENCE of that, so deriving edges by testing containment would be O(n^2) set comparisons to recover what one O(reaches) walk already knows.

EVERY SUMMABLE EDGE ATTRIBUTE IS MEASURED OVER THE EDGE'S CHARGE SET -- the from-node's own reach plus the strictly-intervening reaches, empty for a co-reach pair. Charge sets tile the channel, which is what makes the contract's composition rules EXACT under contraction (see `contracts.EDGE_COLUMNS`): drop a node and the sums, mins, maxes and ORs of the two edges are identically the direct edge's. The build2 ancestor measured only the strictly-between reaches, and every contraction silently lost the dropped node's own reach.

DIVERGENCES FOLLOW THE MAIN PATH. NHDPlus marks a minor divergence branch `divergence == 2`; taking it would send a reach's water down a distributary and give it the wrong owner. Where a reach has several downstream candidates the minor ones are skipped, which is also what keeps the result acyclic.

TWO NODES ON ONE REACH are ordered by `snap_pos_m` -- the id primitive orders them lexically within the reach by construction. The reach is owned by the UPPER one, because its own drainage enters at the head and meets that node first, and the lower one becomes its downstream neighbour.
"""

from __future__ import annotations

import collections

import numpy as np
import pandas as pd

from .. import config, contracts, io as bio
from ..acquire import network as anet
from ..acquire.network import NON_DRAINAGE_FTYPE, VAA_NODATA  # noqa: F401 -- declared once, in the store's layer

# Minor side of a divergence. Following it would route a reach's water down a distributary.
_MINOR_DIVERGENCE = 2

# Sentinel key for a walk that hit `MAX_EDGE_HOPS`. It records that the path is partial rather than dropping it, because a silently short `via` would read as a short edge.
_TRUNCATED = "\x00truncated"

_VAA_COLS = ["comid", "fromnode", "tonode", "areasqkm", "totdasqkm", "divergence", "ftype",
             "lengthkm", "totma", "wbareacomi", "streamorde"]

# The reach walk between two nodes is bounded by the network. A cap is a guard against an impossible condition -- a cycle the divergence rule failed to break -- not a size limit.
MAX_EDGE_HOPS = 100_000


def _clean(col, n) -> np.ndarray:
    """A numeric VAA column with the NODATA sentinel turned into NaN. Missing column -> all NaN."""
    if col is None:
        return np.full(n, np.nan)
    v = pd.to_numeric(col, errors="coerce").to_numpy(dtype="float64")
    return np.where(v <= VAA_NODATA, np.nan, v)


class Downstream:
    """The flow network indexed for DOWNSTREAM traversal -- the direction `accumulate.Network` does not index."""

    def __init__(self, vaa: pd.DataFrame):
        c = vaa.comid.to_numpy()
        self.tonode = dict(zip(c, vaa.tonode.to_numpy()))
        self.area = dict(zip(c, vaa.areasqkm.to_numpy()))
        self.totda = dict(zip(c, vaa.totdasqkm.to_numpy()))
        self.lengthkm = dict(zip(c, _clean(vaa.get("lengthkm"), len(c))))
        self.totma = dict(zip(c, _clean(vaa.get("totma"), len(c))))
        self.order = dict(zip(c, pd.to_numeric(vaa.get("streamorde"), errors="coerce").to_numpy()
                              if "streamorde" in vaa else np.full(len(c), np.nan)))
        wb = pd.to_numeric(vaa.get("wbareacomi"), errors="coerce") if "wbareacomi" in vaa else None
        self.wbarea = dict(zip(c, wb.to_numpy())) if wb is not None else {}
        div = dict(zip(c, vaa.divergence.to_numpy()))
        self.ftype = {k: str(v) for k, v in zip(c, vaa.ftype.to_numpy())}
        self.drains = {k: self.ftype.get(k, "") not in NON_DRAINAGE_FTYPE for k in c}
        self.by_fromnode: dict[int, list[int]] = collections.defaultdict(list)
        for cid, fn in zip(c, vaa.fromnode.to_numpy()):
            self.by_fromnode[fn].append(int(cid))
        self._div = div

    @classmethod
    def load(cls) -> "Downstream":
        return cls(pd.read_parquet(anet._path("vaa"), columns=_VAA_COLS))

    def __len__(self):
        return len(self.tonode)

    def next(self, comid: int) -> int | None:
        """The reach `comid` flows into, or None at a terminal outlet. Minor divergence branches are skipped."""
        tn = self.tonode.get(comid)
        if tn is None:
            return None
        down = self.by_fromnode.get(tn)
        if not down:
            return None
        main = [d for d in down if self._div.get(d, 0) != _MINOR_DIVERGENCE]
        pick = main or down
        # THE MAIN STEM IS THE ONE CARRYING THE MOST DRAINAGE. `min(comid)` looks deterministic and is arbitrary: in a delta it picks a distributary that dead-ends within a few dozen reaches, which silently truncates every basin below the fork.
        return max(pick, key=lambda d: (self.totda.get(d, 0.0) or 0.0, -d))


def node_reaches(nodes: pd.DataFrame) -> dict[int, list[str]]:
    """`comid -> [site_id]`, ordered upstream-first by `snap_pos_m` -- the only thing that can order two nodes on one reach."""
    out: dict[int, list[tuple[float, str]]] = collections.defaultdict(list)
    for r in nodes.itertuples():
        if pd.isna(r.comid):
            continue
        pos = float(r.snap_pos_m) if pd.notna(getattr(r, "snap_pos_m", None)) else 0.0
        out[int(r.comid)].append((pos, r.site_id))
    return {c: [u for _, u in sorted(v)] for c, v in out.items()}


def label(net: Downstream, on_reach: dict[int, list[str]]) -> tuple[dict[int, str], dict[str, str], dict]:
    """One downstream pass. Returns `(owner per reach, downstream node per node, reaches between each pair)`.

    ITERATIVE WITH PATH COMPRESSION, not recursion: a Mississippi headwater is over a thousand reaches from its outlet and Python's stack is not. Each reach is resolved once and every reach on the path to it is stamped with the same answer, so the whole layer costs one traversal rather than one per reach.

    THE PATH IS THE THIRD ARTIFACT, and it was always being computed. The edge walk visits every reach between two nodes; `via[(M, N)]` is that list, upstream-first, and everything an edge knows about what it crosses is derived from it.
    """
    # `resolved` memoises EVERY walked reach, including the ones that drain to no node -- None is an
    # answer, not an absence. The ancestor popped those from the owner map, so a reach resolving to None
    # was re-walked every time another path crossed it; with a sparse node set that is nearly the whole
    # network walking to its terminal outlet, and the "path compression" the docstring promises never
    # happened where it mattered. `owner` stays the None-free view the artifacts want.
    resolved: dict[int, str | None] = {}
    for c, uids in on_reach.items():
        if net.drains.get(c, True):            # a coastline reach drains nothing and owns nothing
            resolved[c] = uids[0]              # the upper node owns its own reach

    for start in net.tonode:
        if start in resolved or not net.drains.get(start, True):
            continue
        path, cur = [], start
        while cur is not None and cur not in resolved:
            path.append(cur)
            cur = net.next(cur)
            if cur is not None and not net.drains.get(cur, True):
                cur = None
        val = resolved.get(cur) if cur is not None else None
        for p in path:
            resolved[p] = val

    owner = {c: v for c, v in resolved.items() if v is not None}

    # Edges: from each node's own reach, the next node downstream. Co-reach nodes chain by snap_pos_m.
    below: dict[str, str] = {}
    via: dict[tuple[str, str], list[int]] = {}
    for c, uids in on_reach.items():
        for a, b in zip(uids, uids[1:]):
            below[a] = b
            via[(a, b)] = []                   # two nodes on one reach: nothing lies between them
        last = uids[-1]
        path: list[int] = []
        cur = net.next(c)
        while cur is not None:
            # SAME GUARD AS THE OWNER WALK ABOVE. Without it an edge could be traced THROUGH a reach the owner walk refuses to cross, and the two halves of this one pass would disagree about what the network is.
            if not net.drains.get(cur, True):
                break
            if cur in on_reach:
                below[last] = on_reach[cur][0]
                via[(last, on_reach[cur][0])] = path
                break
            path.append(cur)
            if len(path) > MAX_EDGE_HOPS:
                via[(last, _TRUNCATED)] = path
                break
            cur = net.next(cur)
    return owner, below, via


def edge_table(net: Downstream, below: dict[str, str], via: dict, nodes: pd.DataFrame,
               dams: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per edge M -> N, describing the CHANNEL BETWEEN THEM rather than just naming its ends.

    WHY AN EDGE NEEDS ATTRIBUTES. Two gauges 26 km apart on the Des Moines River have basins that differ by 2.6% and nitrate that diverges by 45% at autumn low flow, because Saylorville Dam and 15 km of reservoir sit between them. Given only `(from, to, distance)` that pair is indistinguishable from two gauges on 26 km of open channel, so a model over the network cannot learn a relationship the network itself records.

    DISTANCES ARE SUMMED ALONG THE CHARGE SET, never differenced from a cumulative field. `pathlength` carries the -9998 sentinel, so differencing two of them yields a confident 0.0, and it is undefined across terminal paths, where a summed length is perfectly well defined. Travel time sums per-reach `totma` for the same reason, and because `pathtimema` accumulates RESERVOIR RESIDENCE rather than travel; residence belongs in the dam columns, named, not smuggled into a distance.
    """
    area = dict(zip(nodes.site_id, pd.to_numeric(nodes.get("basin_km2"), errors="coerce"))) \
        if "basin_km2" in nodes else {}
    tp = dict(zip(nodes.site_id, nodes.get("terminal_path", pd.Series(index=nodes.index))))
    comid_of = dict(zip(nodes.site_id, nodes.comid))
    dam = _dam_index(dams)

    rows = []
    for a, b in below.items():
        path = via.get((a, b))
        truncated = path is None and (a, _TRUNCATED) in via
        if path is None:
            path = via.get((a, _TRUNCATED), [])
        fc = None if pd.isna(comid_of.get(a)) else int(comid_of.get(a))
        tc = None if pd.isna(comid_of.get(b)) else int(comid_of.get(b))
        # THE CHARGE SET: the from-node's own reach plus the strictly-between reaches; empty for a co-reach pair. Tiles the channel, which is what makes every `sum`/`min`/`max`/`or` compose exactly under contraction.
        charge = [] if (fc is not None and fc == tc) else ([fc] if fc is not None else []) + path
        ft = [net.ftype.get(c, "") for c in charge]
        wb = [int(w) for w in (net.wbarea.get(c) for c in charge) if w and w == w and w > 0]
        lens = [net.lengthkm.get(c, np.nan) for c in charge]
        times = [net.totma.get(c, np.nan) for c in charge]
        orders = [net.order.get(c, np.nan) for c in charge]
        totdas = [net.totda.get(c, np.nan) for c in charge]
        d = _dam_totals(dam, charge)
        aa, ab = area.get(a, np.nan), area.get(b, np.nan)
        ratio = (min(aa, ab) / max(aa, ab)) if (aa == aa and ab == ab and max(aa, ab) > 0) else np.nan
        rows.append({
            "from_id": a, "to_id": b, "from_comid": fc, "to_comid": tc,
            "n_reaches_between": len(path),
            "via_comids": ",".join(str(c) for c in path) or None,
            "via_truncated": bool(truncated),
            # NaN only when a charge reach has no length at all; a co-reach edge is genuinely 0 km.
            "flow_dist_km": float(np.nansum(lens)) if charge else 0.0,
            "travel_time_d": float(np.nansum(times)) if charge and not np.isnan(times).all() else (
                0.0 if not charge else np.nan),
            "same_terminal_path": bool(pd.notna(tp.get(a)) and tp.get(a) == tp.get(b)),
            "min_stream_order": _int_or_na(np.nanmin(orders)) if _any_finite(orders) else pd.NA,
            "max_totda_km2": float(np.nanmax(totdas)) if _any_finite(totdas) else np.nan,
            "n_artificial_path": sum(1 for f in ft if f == "ArtificialPath"),
            "n_connector": sum(1 for f in ft if f == "Connector"),
            "n_waterbody_reaches": len(wb),
            "waterbody_comids": ",".join(str(x) for x in dict.fromkeys(wb)) or None,
            "threads_impoundment": bool(wb),
            **d,
            "basin_area_ratio": ratio,
        })
    out = pd.DataFrame(rows)
    if not len(out):
        return out
    n = pd.to_numeric(out.n_dams, errors="coerce")
    out["crosses_impoundment"] = out.threads_impoundment | (n.fillna(0) > 0)
    out["near_duplicate_basin"] = out.basin_area_ratio > contracts.NEAR_DUPLICATE_TOL
    return out


def _any_finite(v) -> bool:
    return bool(len(v)) and bool(np.isfinite(np.asarray(v, dtype="float64")).any())


def _int_or_na(v):
    return pd.NA if v is None or not np.isfinite(v) else int(v)


def _dam_index(dams: pd.DataFrame | None) -> dict | None:
    """`comid -> dam aggregate`, or None when D5's dam table has not been built.

    NONE AND EMPTY MEAN DIFFERENT THINGS. The dam table holds only catchments that HAVE a dam, so a comid missing from it genuinely has none and the edge gets 0. A missing TABLE is not evidence of anything and the edge gets null -- confusing the two would report every edge in the network as dam-free.
    """
    if dams is None or not len(dams):
        return None
    return {int(r.comid): r for r in dams.itertuples()}


def _dam_totals(dam: dict | None, path: list) -> dict:
    """Dam aggregates over the reaches an edge charges. All null when the table is absent, 0 when it is empty here."""
    keys = ("n_dams", "dam_max_storage_af", "dam_normal_storage_af", "dam_max_height_m", "dam_first_year")
    if dam is None:
        return dict.fromkeys(keys, None)
    hits = [dam[c] for c in path if c in dam]
    if not hits:
        return {"n_dams": 0, "dam_max_storage_af": 0.0, "dam_normal_storage_af": 0.0,
                "dam_max_height_m": np.nan, "dam_first_year": pd.NA}

    def _sum(f):
        return float(np.nansum([float(getattr(h, f, np.nan) or 0.0) for h in hits]))

    def _max(f):
        v = [float(getattr(h, f, np.nan)) for h in hits]
        return float(np.nanmax(v)) if _any_finite(v) else np.nan

    def _min(f):
        v = [float(getattr(h, f, np.nan)) for h in hits]
        return _int_or_na(np.nanmin(v)) if _any_finite(v) else pd.NA

    return {"n_dams": int(sum(int(getattr(h, "n_dams", 0) or 0) for h in hits)),
            "dam_max_storage_af": _sum("max_storage"),
            "dam_normal_storage_af": _sum("normal_storage"),
            "dam_max_height_m": _max("max_height"),
            "dam_first_year": _min("first_year")}


def incremental_sets(owner: dict[int, str]) -> dict[str, set]:
    """Invert the labels: `site_id -> the reaches it owns`. These partition the draining network."""
    out: dict[str, set] = collections.defaultdict(set)
    for comid, sid in owner.items():
        out[sid].add(comid)
    return dict(out)


def upstream_nodes(below: dict[str, str]) -> dict[str, list[str]]:
    """Invert the edge map: `node -> the nodes immediately above it`."""
    out: dict[str, list[str]] = collections.defaultdict(list)
    for a, b in below.items():
        out[b].append(a)
    return dict(out)


def components(nodes, below: dict[str, str]) -> tuple[dict[str, str], dict[str, int]]:
    """`(node -> outlet id, outlet -> component size)` over the WEAKLY connected components.

    A component is the natural definition of a hydrologic region, and its outlet names it: the node with nothing below it is the one all the others drain past. Worth storing rather than re-deriving, because "train on the Upper Mississippi" is otherwise a graph traversal every consumer has to repeat.
    """
    parent = {u: u for u in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in below.items():
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for u in nodes:
        groups[find(u)].append(u)

    outlet_of, size_of = {}, {}
    for root, members in groups.items():
        ends = [u for u in members if u not in below] or [root]
        outlet = sorted(ends)[0]
        for u in members:
            outlet_of[u] = outlet
        size_of[outlet] = len(members)
    return outlet_of, size_of


def reach_counts(nodes, below: dict[str, str]) -> pd.DataFrame:
    """Per node: immediate and total neighbours in each direction, plus its outlet and component size.

    `n_upstream_nodes` is the whole closure above, not the in-degree -- the number of sensors whose water this one eventually sees, which is what says how much of a node's signal is already observed somewhere. `n_immediate_upstream` is kept beside it because a confluence gauge with fourteen direct tributaries is a different object from one with a single long chain above it.
    """
    above = upstream_nodes(below)
    outlet_of, size_of = components(nodes, below)

    def closure_up(u):
        seen, stack, n = {u}, [u], 0
        while stack:
            x = stack.pop()
            for p in above.get(x, ()):
                if p not in seen:
                    seen.add(p)
                    stack.append(p)
                    n += 1
        return n

    rows = []
    for u in nodes:
        down, n_down = below.get(u), 0
        seen = {u}
        while down is not None and down not in seen:
            seen.add(down)
            n_down += 1
            down = below.get(down)
        rows.append(dict(site_id=u, node_below=below.get(u, pd.NA),
                         n_immediate_upstream=len(above.get(u, ())),
                         n_upstream_nodes=closure_up(u), n_downstream_nodes=n_down,
                         component_outlet=outlet_of.get(u, pd.NA),
                         component_size=size_of.get(outlet_of.get(u), 1)))
    return pd.DataFrame(rows)


def flow_distances(nodes: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    """Channel distance to the node below, and to the nearest node either way -- READ OFF THE EDGES.

    ONE COMPUTATION, NOT TWO. Differencing `pathlength_km` looked free and was wrong twice over: the -9998 sentinel differenced to a confident 0.0, and the difference is undefined across terminal paths. `edge_table` already sums `lengthkm` along the charge set, which has neither problem. Deriving these from that rather than recomputing them is what makes the node and edge tables agree BY CONSTRUCTION.
    """
    below_km, nearest = {}, {}
    if len(edges):
        d = pd.to_numeric(edges.flow_dist_km, errors="coerce")
        for a, b, km in zip(edges.from_id, edges.to_id, d):
            if pd.isna(km):
                continue
            below_km[a] = float(km)
            for u in (a, b):
                nearest[u] = float(km) if u not in nearest else min(nearest[u], float(km))
    return pd.DataFrame({
        "site_id": nodes.site_id,
        "flow_dist_below_km": [below_km.get(u, np.nan) for u in nodes.site_id],
        "flow_dist_nearest_node_km": [nearest.get(u, np.nan) for u in nodes.site_id],
    })


def is_acyclic(below: dict[str, str]) -> bool:
    """True when following `below` from every node terminates. A cycle would mean water reaching itself."""
    state: dict[str, int] = {}
    for start in below:
        path, cur = [], start
        while cur is not None and state.get(cur, 0) == 0:
            state[cur] = 1
            path.append(cur)
            cur = below.get(cur)
        if cur is not None and state.get(cur) == 1 and cur in path:
            return False
        for p in path:
            state[p] = 2
    return True
