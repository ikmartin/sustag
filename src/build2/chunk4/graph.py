"""The sensor graph: which node is downstream of which, and which catchments drain to each node first.

ONE WALK, TWO ARTIFACTS. Labelling every reach with the first node found walking DOWNSTREAM from it answers both questions at once. The label is the reach's owner, so the labels partition the network into **incremental catchment sets** -- what drains to a node without passing another node first. And the same walk started from a node's own reach lands on the node immediately below it, which is a **DAG edge**.

THE CLOSURE DOES NOT REPRODUCE A BASIN, AND CANNOT. It was designed to: a full basin was to be the union of incremental sets over the upstream closure, which would have let ~1.5M rows replace an 8.2M-row `basin_reaches`. Measured against `accumulate.upstream` over 350 valid nodes it agrees on 319 and comes up SHORT on 31, never long, and the cause is structural rather than a defect here.

`accumulate.upstream` admits BOTH branches at a divergence, because walking up from a reach collects everything whose water can reach it. A partition cannot: a reach that splits flows to two places, and an owner map gives it one. So the two walks are not inverses wherever NHDPlus records a divergence, and the discrepancy is largest exactly where deltas are -- the Mississippi at Baton Rouge dead-ends 40 reaches short of the Atchafalaya gauge below it, because downstream the water goes to the Gulf while upstream the two share everything.

`accumulate.upstream` is the authority -- it reproduces NHDPlus's own `totdasqkm` on 388/388 gauges -- so BASINS KEEP USING IT. What this module provides is the DAG, which nothing computed before and which is verified separately: every edge M -> N has M inside `accumulate.upstream(N)`. The incremental sets remain meaningful in their own right as "what drains to this node without passing another", which is a real quantity for a graph, just not a basin decomposition.

EDGES COME FROM FLOW, NOT FROM GEOMETRY. M -> N because water leaving M reaches N without passing another node. Basin containment is the CONSEQUENCE of that -- if M is upstream of N then basin(M) is a subset of basin(N) -- so deriving edges by testing containment would be O(n^2) set comparisons to recover what one O(reaches) walk already knows.

DIVERGENCES FOLLOW THE MAIN PATH. NHDPlus marks a minor divergence branch `divergence == 2`; taking it would send a reach's water down a distributary and give it the wrong owner. Where a reach has several downstream candidates the minor ones are skipped, which is also what keeps the result acyclic.

TWO NODES ON ONE REACH are ordered by `snap_pos_m`, distance along the reach from its upstream end. The reach is owned by the UPPER one, because its own drainage enters at the head and meets that node first, and the lower one becomes its downstream neighbour. Both then resolve to the same full basin, which is what `accumulate.upstream` already says about two gauges sharing a reach.
"""

from __future__ import annotations

import collections

import numpy as np
import pandas as pd

# Minor side of a divergence. Following it would route a reach's water down a distributary.
_MINOR_DIVERGENCE = 2

# Sentinel key for a walk that hit `MAX_EDGE_HOPS`. It records that the path is partial rather than dropping
# it, because a silently short `via` would read as a short edge.
_TRUNCATED = "\x00truncated"

# Two nodes whose basins agree this closely are either an impoundment pair -- the same water above and below
# a structure, which is signal -- or a mis-snap, which is an error. The ratio cannot tell them apart;
# `crosses_impoundment` beside it can, and that combination is what the chunk 4 report prints.
NEAR_DUPLICATE_TOL = 0.95

_VAA_COLS = ["comid", "fromnode", "tonode", "areasqkm", "totdasqkm", "divergence", "ftype",
             "lengthkm", "totma", "wbareacomi", "streamorde"]
# A coastline carries no drainage; `accumulate` excludes it from a walk and so must this.
NON_DRAINAGE_FTYPE = {"Coastline"}

# NHDPlus writes -9998/-9999 where a value is unknown. It is not a small number, it is a MISSING one, and
# differencing or summing two of them yields a confident-looking zero. Guarded once at load rather than at
# each use, so nothing downstream has to remember.
VAA_NODATA = -9998.0

# The reach walk between two nodes is bounded by the network. A cap is a guard against an impossible
# condition -- a cycle the divergence rule failed to break -- not a size limit, in the spirit of
# `accumulate.upstream`'s own cap.
MAX_EDGE_HOPS = 100_000


def _clean(col, n) -> np.ndarray:
    """A numeric VAA column with the NODATA sentinel turned into NaN. Missing column -> all NaN."""
    if col is None:
        return np.full(n, np.nan)
    v = pd.to_numeric(col, errors="coerce").to_numpy(dtype="float64")
    return np.where(v <= VAA_NODATA, np.nan, v)


class Downstream:
    """The flow network indexed for DOWNSTREAM traversal — the direction `accumulate.Network` does not index.

    `accumulate` walks `tonode -> fromnode` to collect what is above a reach. Labelling needs the inverse: from a reach, the reach it flows INTO, which is the one whose `fromnode` equals this reach's `tonode`.
    """

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
        ftype = dict(zip(c, vaa.ftype.to_numpy()))
        self.ftype = {k: str(v) for k, v in zip(c, vaa.ftype.to_numpy())}
        self.drains = {k: str(ftype.get(k, "")) not in NON_DRAINAGE_FTYPE for k in c}
        self.by_fromnode: dict[int, list[int]] = collections.defaultdict(list)
        for cid, fn in zip(c, vaa.fromnode.to_numpy()):
            self.by_fromnode[fn].append(int(cid))
        self._div = div

    @classmethod
    def load(cls) -> "Downstream":
        from ..chunk1 import network

        return cls(pd.read_parquet(network._path("vaa"), columns=_VAA_COLS))

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
        # THE MAIN STEM IS THE ONE CARRYING THE MOST DRAINAGE. `min(comid)` looks deterministic and is
        # arbitrary: in a delta it picks a distributary that dead-ends within a few dozen reaches, which
        # silently truncates every basin below the fork.
        return max(pick, key=lambda d: (self.totda.get(d, 0.0) or 0.0, -d))


def node_reaches(nodes: pd.DataFrame) -> dict[int, list[str]]:
    """`comid -> [node uid]`, ordered upstream-first by `snap_pos_m`.

    `snap_pos_m` is the only thing that can order two gauges on one reach: pathlength, travel time and stream order are reach attributes and identical for both.
    """
    out: dict[int, list[tuple[float, str]]] = collections.defaultdict(list)
    for r in nodes.itertuples():
        if pd.isna(r.comid):
            continue
        pos = float(r.snap_pos_m) if pd.notna(getattr(r, "snap_pos_m", None)) else 0.0
        out[int(r.comid)].append((pos, r.site_uid))
    return {c: [u for _, u in sorted(v)] for c, v in out.items()}


def label(net: Downstream, on_reach: dict[int, list[str]]) -> tuple[dict[int, str], dict[str, str], dict]:
    """One downstream pass. Returns `(owner per reach, downstream node per node, reaches between each pair)`.

    ITERATIVE WITH PATH COMPRESSION, not recursion: a Mississippi headwater is over a thousand reaches from its outlet and Python's stack is not. Each reach is resolved once and every reach on the path to it is stamped with the same answer, so the whole layer costs one traversal rather than one per reach.

    THE PATH IS THE THIRD ARTIFACT, and it was always being computed. The edge walk visits every reach between two nodes and used to discard them the moment it found the far end -- so a pair of gauges straddling a reservoir and a pair on open channel produced identical edges, and the network could not express the difference between them. `via[(M, N)]` is that list, upstream-first, and everything an edge knows about what it crosses is derived from it.
    """
    owner: dict[int, str] = {}
    for c, uids in on_reach.items():
        if net.drains.get(c, True):            # a coastline reach drains nothing and owns nothing
            owner[c] = uids[0]                 # the upper node owns its own reach

    for start in net.tonode:
        if start in owner or not net.drains.get(start, True):
            continue
        path, cur = [], start
        while cur is not None and cur not in owner:
            path.append(cur)
            cur = net.next(cur)
            if cur is not None and not net.drains.get(cur, True):
                cur = None
        val = owner.get(cur) if cur is not None else None
        for p in path:
            if val is None:
                owner.pop(p, None)             # drains to no node; belongs to no incremental set
            else:
                owner[p] = val

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
            # SAME GUARD AS THE OWNER WALK ABOVE. Without it an edge could be traced THROUGH a reach the
            # owner walk refuses to cross, and the two halves of this one pass would disagree about what
            # the network is.
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

    WHY AN EDGE NEEDS ATTRIBUTES. Two gauges 26 km apart on the Des Moines River have basins that differ by 2.6% and nitrate that diverges by 45% at autumn low flow, because Saylorville Dam and 15 km of reservoir sit between them. Given only `(from, to, distance)` that pair is indistinguishable from two gauges on 26 km of open channel, so a model over the network cannot learn a relationship the network itself records. Every column here comes from reaches the walk in `label` already visited.

    DISTANCES ARE SUMMED ALONG THE PATH, never differenced from a cumulative field. `pathlength` is copied raw from the VAA and carries the -9998 sentinel, so differencing two of them yields a confident 0.0 -- "these adjacent nodes are 0 km apart" -- and it is undefined across terminal paths, where a summed length is perfectly well defined. Travel time sums per-reach `totma` for the same reason, and because `pathtimema` accumulates RESERVOIR RESIDENCE rather than travel and reaches 51,524 days on this network; residence belongs in the dam columns, named, not smuggled into a distance.
    """
    area = dict(zip(nodes.site_uid, pd.to_numeric(nodes.get("basin_km2"), errors="coerce"))) \
        if "basin_km2" in nodes else {}
    tp = dict(zip(nodes.site_uid, nodes.get("terminal_path", pd.Series(index=nodes.index))))
    dam = _dam_index(dams)

    rows = []
    for a, b in below.items():
        path = via.get((a, b))
        truncated = path is None and (a, _TRUNCATED) in via
        if path is None:
            path = via.get((a, _TRUNCATED), [])
        ft = [net.ftype.get(c, "") for c in path]
        wb = [int(w) for w in (net.wbarea.get(c) for c in path) if w and w == w and w > 0]
        lens = [net.lengthkm.get(c, np.nan) for c in path]
        times = [net.totma.get(c, np.nan) for c in path]
        orders = [net.order.get(c, np.nan) for c in path]
        totdas = [net.totda.get(c, np.nan) for c in path]
        d = _dam_totals(dam, path)
        aa, ab = area.get(a, np.nan), area.get(b, np.nan)
        ratio = (min(aa, ab) / max(aa, ab)) if (aa == aa and ab == ab and max(aa, ab) > 0) else np.nan
        rows.append({
            "from_uid": a, "to_uid": b,
            "n_reaches_between": len(path),
            "via_comids": ",".join(str(c) for c in path) or None,
            "via_truncated": bool(truncated),
            # NaN only when a reach on the path has no length at all; a zero-reach edge is genuinely 0 km.
            "flow_dist_km": float(np.nansum(lens)) if path else 0.0,
            "travel_time_d": float(np.nansum(times)) if path and not np.isnan(times).all() else (
                0.0 if not path else np.nan),
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
    out["near_duplicate_basin"] = out.basin_area_ratio > NEAR_DUPLICATE_TOL
    return out


def _any_finite(v) -> bool:
    return bool(len(v)) and bool(np.isfinite(np.asarray(v, dtype="float64")).any())


def _int_or_na(v):
    return pd.NA if v is None or not np.isfinite(v) else int(v)


def _dam_index(dams: pd.DataFrame | None) -> dict | None:
    """`comid -> dam aggregate`, or None when chunk 5's dam table has not been built.

    NONE AND EMPTY MEAN DIFFERENT THINGS. `dams.parquet` holds only catchments that HAVE a dam, so a comid missing from it genuinely has none and the edge gets 0. A missing TABLE is not evidence of anything and the edge gets null -- confusing the two would report every edge in the network as dam-free.
    """
    if dams is None or not len(dams):
        return None
    return {int(r.comid): r for r in dams.itertuples()}


def _dam_totals(dam: dict | None, path: list) -> dict:
    """Dam aggregates over the reaches an edge crosses. All null when the table is absent, 0 when it is empty here."""
    keys = ("n_dams", "dam_max_storage_af", "dam_normal_storage_af", "dam_max_height_m", "dam_first_year")
    if dam is None:
        return dict.fromkeys(keys, None)
    hits = [dam[c] for c in path if c in dam]
    if not hits:
        return {"n_dams": 0, "dam_max_storage_af": 0.0, "dam_normal_storage_af": 0.0,
                "dam_max_height_m": np.nan, "dam_first_year": pd.NA}
    def _sum(f):
        v = [float(getattr(h, f, np.nan) or 0.0) for h in hits]
        return float(np.nansum(v))
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
    """Invert the labels: `node uid -> the reaches it owns`. These partition the draining network."""
    out: dict[str, set] = collections.defaultdict(set)
    for comid, uid in owner.items():
        out[uid].add(comid)
    return dict(out)


def upstream_nodes(below: dict[str, str]) -> dict[str, list[str]]:
    """Invert the edge map: `node -> the nodes immediately above it`."""
    out: dict[str, list[str]] = collections.defaultdict(list)
    for a, b in below.items():
        out[b].append(a)
    return dict(out)


def full_basin(uid: str, inc: dict[str, set], above: dict[str, list[str]]) -> set:
    """Reaches reaching `uid` through the DAG: its incremental set plus the closure over the nodes above it.

    NOT A BASIN -- see the module docstring. It agrees with `accumulate.upstream` on 319 of 350 nodes and is short on the other 31, because a divergence sends water two ways and an owner map records one. Kept because it is the natural closure over the graph and useful for reasoning about it; use `accumulate.upstream` when the question is what drains to a gauge.
    """
    seen, out, stack = {uid}, set(), [uid]
    while stack:
        n = stack.pop()
        out |= inc.get(n, set())
        for u in above.get(n, ()):
            if u not in seen:
                seen.add(u)
                stack.append(u)
    return out


def components(nodes, below: dict[str, str]) -> tuple[dict[str, str], dict[str, int]]:
    """`(node -> outlet uid, outlet -> component size)` over the WEAKLY connected components.

    A component is the natural definition of a hydrologic region, and its outlet names it: the node with nothing below it is the one all the others drain past. Worth storing rather than re-deriving, because "train on the Upper Mississippi" is otherwise a graph traversal every consumer has to repeat, and on this cohort 286 of 350 nodes sit in the single Mississippi component -- so a region is almost always a subtree of one component rather than a component itself.
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
        rows.append(dict(site_uid=u, node_below=below.get(u, pd.NA),
                         n_immediate_upstream=len(above.get(u, ())),
                         n_upstream_nodes=closure_up(u), n_downstream_nodes=n_down,
                         component_outlet=outlet_of.get(u, pd.NA),
                         component_size=size_of.get(outlet_of.get(u), 1)))
    return pd.DataFrame(rows)


def flow_distances(nodes: pd.DataFrame, edges: pd.DataFrame) -> pd.DataFrame:
    """Channel distance to the node below, and to the nearest node either way -- READ OFF THE EDGES.

    ONE COMPUTATION, NOT TWO. This used to difference `pathlength_km`, the VAA's cumulative distance to the terminal outlet, which looked free and was wrong twice over: `pathlength_km` is copied raw through the snap block and carries the -9998 sentinel, so two unknown values differenced to a confident 0.0 -- "these adjacent nodes are 0 km apart" -- and the difference is undefined across terminal paths, where a channel distance is perfectly well defined. `edge_table` already sums `lengthkm` along the reaches the walk visited, which has neither problem.

    Deriving these from that rather than recomputing them is what makes `nodes.parquet` and `node_edges.parquet` agree BY CONSTRUCTION. Two implementations of one quantity disagree eventually, and the disagreement surfaces as a model feature rather than as an error.
    """
    below_km, nearest = {}, {}
    if len(edges):
        d = pd.to_numeric(edges.flow_dist_km, errors="coerce")
        for a, b, km in zip(edges.from_uid, edges.to_uid, d):
            if pd.isna(km):
                continue
            below_km[a] = float(km)
            for u in (a, b):
                nearest[u] = float(km) if u not in nearest else min(nearest[u], float(km))
    return pd.DataFrame({
        "site_uid": nodes.site_uid,
        "flow_dist_below_km": [below_km.get(u, np.nan) for u in nodes.site_uid],
        "flow_dist_nearest_node_km": [nearest.get(u, np.nan) for u in nodes.site_uid],
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
