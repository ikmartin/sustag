"""D4 kernels: the composition law (contract == walk), delta-safe next(), acyclicity, flag semantics."""

import numpy as np
import pandas as pd
import pytest

from src.gnn1 import compose as contracts
from src.gnn1 import demo as graph


def _vaa(rows):
    cols = ["comid", "fromnode", "tonode", "areasqkm", "totdasqkm", "divergence", "ftype",
            "lengthkm", "totma", "wbareacomi", "streamorde"]
    return pd.DataFrame(rows, columns=cols)


def _net():
    """A five-reach mainstem 10->20->30->40->50 with a waterbody on 30 and an ArtificialPath at 40."""
    return graph.Downstream(_vaa([
        (10, 1, 2, 5.0,  5.0, 0, "StreamRiver",    1.0, 0.01, 0,   1),
        (20, 2, 3, 4.0,  9.0, 0, "StreamRiver",    2.0, 0.02, 0,   1),
        (30, 3, 4, 3.0, 12.0, 0, "StreamRiver",    3.0, 0.03, 777, 2),
        (40, 4, 5, 2.0, 14.0, 0, "ArtificialPath", 4.0, 0.04, 777, 2),
        (50, 5, 6, 1.0, 15.0, 0, "StreamRiver",    5.0, 0.05, 0,   3),
    ]))


def _nodes(rows):
    return pd.DataFrame(rows, columns=["site_id", "comid", "snap_pos_m", "terminal_path", "basin_km2"])


def _edges_for(net, nodes):
    on = graph.node_reaches(nodes)
    owner, below, via = graph.label(net, on)
    return graph.edge_table(net, below, via, nodes), below


class TestCompositionLaw:



    def test_attribute_without_rule_raises(self):
        e1 = dict(from_id="A", to_id="B", from_comid=10, to_comid=30, rogue_column=1)
        e2 = dict(from_id="B", to_id="C", from_comid=30, to_comid=50)
        with pytest.raises(ValueError, match="no composition rule"):
            contracts.compose_edges(e1, e2)

    def test_non_chaining_edges_raise(self):
        e1 = dict(from_id="A", to_id="B")
        e2 = dict(from_id="X", to_id="C")
        with pytest.raises(ValueError, match="do not chain"):
            contracts.compose_edges(e1, e2)


class TestDownstream:
    def test_next_skips_minor_divergence_and_follows_drainage(self):
        """At a fork, next() must take the major branch by totda -- min(comid) picks the dead-end distributary."""
        net = graph.Downstream(_vaa([
            (10, 1, 2, 5.0,  5.0, 0, "StreamRiver", 1.0, 0.01, 0, 1),
            (11, 2, 3, 0.1,  0.1, 2, "StreamRiver", 1.0, 0.01, 0, 1),   # minor distributary, smaller comid
            (12, 2, 4, 4.0,  9.0, 1, "StreamRiver", 1.0, 0.01, 0, 1),   # the mainstem
        ]))
        assert net.next(10) == 12




class TestContractionLaw:
    """The executable law: contract A->B->C and every attribute must match the direct edge built with B removed."""

    def _net(self):
        # a chain of five reaches, nodes on 100, 102, 104
        rows = [(100 + i, i, i + 1, 2.0, float(10 * (i + 1)), 0, "StreamRiver", 1.0 + i, 0.01, 0, 2 + i)
                for i in range(5)]
        return graph.Downstream(_vaa(rows))

    def _nodes(self, ids):
        import pandas as pd

        return pd.DataFrame({"node_id": [f"N{c}" for c in ids], "comid": ids,
                             "snap_pos_m": 0.0, "site_type": "stream"})

    def test_contract_equals_direct_walk(self):
        import pandas as pd

        net = self._net()
        with_b = graph.edges_for(self._nodes([100, 102, 104]), net).set_index(["from_id", "to_id"])
        without_b = graph.edges_for(self._nodes([100, 104]), net).set_index(["from_id", "to_id"])
        e1 = with_b.loc[("N100", "N102")].to_dict() | {"from_id": "N100", "to_id": "N102"}
        e2 = with_b.loc[("N102", "N104")].to_dict() | {"from_id": "N102", "to_id": "N104"}
        for e in (e1, e2):
            for c in contracts.EDGE_COMPOSE:
                e.setdefault(c, None)
        composed = contracts.compose_edges(e1, e2)
        direct = without_b.loc[("N100", "N104")].to_dict()
        assert composed["flow_dist_km"] == pytest.approx(direct["flow_dist_km"])
        assert composed["n_reaches_between"] == direct["n_reaches_between"]
        assert composed["min_stream_order"] == direct["min_stream_order"]
        assert composed["max_totda_km2"] == pytest.approx(direct["max_totda_km2"])
        assert (composed["via_comids"] or "") == (direct["via_comids"] or "")

    def test_charge_sets_tile_the_channel(self):
        """Consecutive edges' charge sets are disjoint and their union is the full path -- the exactness source."""
        net = self._net()
        E = graph.edges_for(self._nodes([100, 102, 104]), net).set_index(["from_id", "to_id"])
        def charge(row, from_comid):
            via = [int(x) for x in str(row["via_comids"] or "").split(",") if x]
            return [from_comid] + via
        c1 = charge(E.loc[("N100", "N102")], 100)
        c2 = charge(E.loc[("N102", "N104")], 102)
        assert not set(c1) & set(c2)
        assert sorted(set(c1) | set(c2)) == [100, 101, 102, 103]   # everything above N104's reach

    def test_co_reach_pair_has_empty_between(self):
        import pandas as pd

        net = self._net()
        nodes = pd.DataFrame({"node_id": ["A", "B"], "comid": [100, 100],
                              "snap_pos_m": [10.0, 500.0], "site_type": "stream"})
        E = graph.edges_for(nodes, net)
        r = E.iloc[0]
        assert (r.from_id, r.to_id, r.n_reaches_between) == ("A", "B", 0)
