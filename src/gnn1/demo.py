"""The gnn1 demo: a sensor DAG built from the ACCESS LAYER ALONE -- the proof the published raw material suffices.

    python -m src.gnn1.demo

NODES ARE THE MODELER'S CHOICE, and this demo makes the simplest one: every published record is a node at its fronting sensor's snapped reach. Edges come from a downstream walk over the published VAA topology (`Downstream.next`, the mainstem-following step: minor divergence branches skipped, the branch carrying the most drainage wins); an edge M -> N means water leaving M reaches N without passing another node, and its attributes are measured over the CHARGE SET so `compose.compose_edges` contracts variants exactly. Nothing here touches the build or its stores: sensors, records and topology all arrive through `data.access`.
"""

from __future__ import annotations

import collections

import numpy as np
import pandas as pd

from data.access import api

from . import compose

_MINOR_DIVERGENCE = 2
MAX_EDGE_HOPS = 40_000


def _clean(col, n) -> np.ndarray:
    v = pd.to_numeric(col, errors="coerce").to_numpy(dtype="float64") if col is not None \
        else np.full(n, np.nan)
    return np.where(v <= -9998.0, np.nan, v)


class Downstream:
    """The flow network indexed for DOWNSTREAM traversal, from the published VAA."""

    def __init__(self, vaa: pd.DataFrame):
        c = vaa.comid.to_numpy()
        self.tonode = dict(zip(c, vaa.tonode.to_numpy()))
        self.totda = dict(zip(c, vaa.totdasqkm.to_numpy()))
        self.lengthkm = dict(zip(c, _clean(vaa.get("lengthkm"), len(c))))
        self.order = dict(zip(c, pd.to_numeric(vaa.get("streamorde"), errors="coerce").to_numpy()
                              if "streamorde" in vaa.columns else np.full(len(c), np.nan)))
        self.by_fromnode: dict = collections.defaultdict(list)
        for cid, fn in zip(c, vaa.fromnode.to_numpy()):
            self.by_fromnode[fn].append(int(cid))
        self._div = dict(zip(c, vaa.divergence.to_numpy()))

    @classmethod
    def load(cls) -> "Downstream":
        cols = ["comid", "fromnode", "tonode", "totdasqkm", "lengthkm", "streamorde", "divergence"]
        vaa = api.get_network("vaa")
        return cls(vaa[[c for c in cols if c in vaa.columns]])

    def next(self, comid: int) -> int | None:
        """The reach `comid` flows into, or None at a terminal outlet. Minor divergence branches are skipped; THE MAIN STEM IS THE ONE CARRYING THE MOST DRAINAGE -- min(comid) looks deterministic and is arbitrary, and in a delta it picks a distributary that dead-ends and silently truncates every basin below the fork."""
        tn = self.tonode.get(comid)
        if tn is None:
            return None
        down = self.by_fromnode.get(tn)
        if not down:
            return None
        main = [d for d in down if self._div.get(d, 0) != _MINOR_DIVERGENCE]
        pick = main or down
        return max(pick, key=lambda d: (self.totda.get(d, 0.0) or 0.0, -d))


def build(max_nodes: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(nodes, edges): every published record a node; edges by the downstream walk, charge-set measured."""
    s = api.get_sensors(columns=["site_uid", "snr_id", "publishes_under", "comid", "snap_pos_m",
                                 "is_bundle_canonical", "site_type"])
    fronts = s[(s.publishes_under.notna()) & (s.is_bundle_canonical == True)]      # noqa: E712
    fronts = fronts.dropna(subset=["comid"]).drop_duplicates("publishes_under")
    if max_nodes:
        fronts = fronts.head(max_nodes)
    nodes = pd.DataFrame({"node_id": fronts.publishes_under.to_numpy(),
                          "comid": fronts.comid.astype("int64").to_numpy(),
                          "snap_pos_m": pd.to_numeric(fronts.snap_pos_m, errors="coerce").to_numpy(),
                          "site_type": fronts.site_type.to_numpy()})
    return nodes, edges_for(nodes, Downstream.load())


def edges_for(nodes: pd.DataFrame, net: "Downstream") -> pd.DataFrame:
    """The edge table for a node frame over an indexed network -- injectable, so the contraction law is testable on synthetic topology."""
    on_reach: dict = collections.defaultdict(list)
    for r in nodes.itertuples():
        on_reach[int(r.comid)].append((0.0 if pd.isna(r.snap_pos_m) else float(r.snap_pos_m),
                                       r.node_id))
    for c in on_reach:
        on_reach[c].sort()

    edges = []
    for r in nodes.itertuples():
        # co-reach successor first: the next node downstream ON the same reach, empty charge set
        peers = on_reach[int(r.comid)]
        pos = 0.0 if pd.isna(r.snap_pos_m) else float(r.snap_pos_m)
        after = [(p, n) for p, n in peers if (p, n) > (pos, r.node_id)]
        if after:
            edges.append(dict(from_id=r.node_id, to_id=after[0][1], from_comid=int(r.comid),
                              to_comid=int(r.comid), n_reaches_between=0, via_comids=None,
                              via_truncated=False, flow_dist_km=net.lengthkm.get(int(r.comid), np.nan),
                              min_stream_order=net.order.get(int(r.comid)),
                              max_totda_km2=net.totda.get(int(r.comid), np.nan)))
            continue
        # walk downstream to the first reach holding a node
        via, cur, hops = [], int(r.comid), 0
        dist = net.lengthkm.get(int(r.comid), 0.0) or 0.0                       # the from-reach IS charged
        found = None
        while hops < MAX_EDGE_HOPS:
            nxt = net.next(cur)
            if nxt is None:
                break
            if on_reach.get(nxt):
                found = on_reach[nxt][0][1]
                break
            via.append(nxt)
            dist += net.lengthkm.get(nxt, 0.0) or 0.0
            cur = nxt
            hops += 1
        if found is None:
            continue
        charged = [int(r.comid)] + via
        orders = [net.order.get(c) for c in charged if pd.notna(net.order.get(c))]
        edges.append(dict(from_id=r.node_id, to_id=found, from_comid=int(r.comid),
                          to_comid=nxt, n_reaches_between=len(via),
                          via_comids=",".join(map(str, via)) or None,
                          via_truncated=hops >= MAX_EDGE_HOPS,
                          flow_dist_km=dist,
                          min_stream_order=min(orders) if orders else None,
                          max_totda_km2=max((net.totda.get(c, 0.0) or 0.0) for c in charged)))
    return pd.DataFrame(edges)


def main():
    nodes, edges = build()
    print(f"  {len(nodes):,} nodes, {len(edges):,} edges from access alone")
    if len(edges) > 1:
        # the composition law, demonstrated live: contract through any two chained edges
        chain = edges.merge(edges, left_on="to_id", right_on="from_id", suffixes=("_1", "_2"))
        if len(chain):
            r = chain.iloc[0]
            e1 = {k[:-2]: v for k, v in r.items() if k.endswith("_1")}
            e2 = {k[:-2]: v for k, v in r.items() if k.endswith("_2")}
            for e in (e1, e2):
                for c in compose.EDGE_COMPOSE:
                    e.setdefault(c, None)
            composed = compose.compose_edges(e1, e2)
            print(f"  contraction demo: {e1['from_id']} -> {e2['to_id']} "
                  f"flow_dist {composed['flow_dist_km']:.1f} km over "
                  f"{composed['n_reaches_between']} intervening reach(es)")
    return nodes, edges


if __name__ == "__main__":
    main()
