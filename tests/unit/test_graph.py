"""D4 kernels: the composition law (contract == walk), delta-safe next(), acyclicity, flag semantics."""

import numpy as np
import pandas as pd
import pytest

from src.build3 import contracts
from src.build3.derive import basins, graph


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
    def test_contract_equals_walk_on_every_attribute(self):
        """Drop B from A -> B -> C: compose_edges must equal the edge a REAL walk over {A, C} produces."""
        net = _net()
        abc = _nodes([("A", 10, 5.0, 6, 100.0), ("B", 30, 5.0, 6, 200.0), ("C", 50, 5.0, 6, 300.0)])
        edges, below = _edges_for(net, abc)
        assert below == {"A": "B", "B": "C"}
        e = {r["from_id"]: r for r in edges.to_dict("records")}
        composed = contracts.compose_edges(e["A"], e["B"], nodes=abc.set_index("site_id"))

        direct, below2 = _edges_for(net, abc[abc.site_id != "B"])
        assert below2 == {"A": "C"}
        d = direct.iloc[0].to_dict()
        # the walked direct edge lacks the two derived columns edge_table adds after the fact
        d["crosses_impoundment"] = bool(d["threads_impoundment"]) or (d.get("n_dams") or 0) > 0
        ratio = min(100.0, 300.0) / max(100.0, 300.0)
        for col in contracts.EDGE_COLS:
            x, y = composed[col], d.get(col)
            if col in ("basin_area_ratio",):
                assert composed[col] == pytest.approx(ratio)
                continue
            if col == "near_duplicate_basin":
                assert composed[col] == False        # noqa: E712
                continue
            if pd.isna(x) and (y is None or pd.isna(y)):
                continue
            if isinstance(x, float):
                assert x == pytest.approx(y), f"{col}: composed {x} != walked {y}"
            else:
                assert x == y, f"{col}: composed {x!r} != walked {y!r}"

    def test_charge_sets_tile_the_channel(self):
        """Sum of the two edges' distances is exactly the direct edge's -- nothing lost at the dropped node."""
        net = _net()
        abc = _nodes([("A", 10, 5.0, 6, 100.0), ("B", 30, 5.0, 6, 200.0), ("C", 50, 5.0, 6, 300.0)])
        edges, _ = _edges_for(net, abc)
        direct, _ = _edges_for(net, abc[abc.site_id != "B"])
        assert edges.flow_dist_km.sum() == pytest.approx(direct.flow_dist_km.iloc[0])
        assert edges.travel_time_d.sum() == pytest.approx(direct.travel_time_d.iloc[0])

    def test_co_reach_contraction(self):
        """A and B share a reach: the co-reach edge charges nothing, and contraction is still exact."""
        net = _net()
        abc = _nodes([("A", 30, 10.0, 6, 100.0), ("B", 30, 200.0, 6, 110.0), ("C", 50, 5.0, 6, 300.0)])
        edges, below = _edges_for(net, abc)
        assert below == {"A": "B", "B": "C"}
        e = {r["from_id"]: r for r in edges.to_dict("records")}
        assert e["A"]["flow_dist_km"] == 0.0 and e["A"]["n_reaches_between"] == 0
        composed = contracts.compose_edges(e["A"], e["B"], nodes=abc.set_index("site_id"))
        direct, _ = _edges_for(net, abc[abc.site_id != "B"])
        d = direct.iloc[0]
        assert composed["flow_dist_km"] == pytest.approx(d.flow_dist_km)
        assert composed["via_comids"] == d.via_comids       # B's reach NOT inserted -- it is A's own
        assert composed["n_reaches_between"] == d.n_reaches_between

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

    def test_coastline_owns_nothing(self):
        net = graph.Downstream(_vaa([
            (10, 1, 2, 5.0, 5.0, 0, "Coastline", 1.0, 0.01, 0, 1),
        ]))
        owner, below, via = graph.label(net, {10: ["A"]})
        assert owner == {} and below == {}

    def test_cycle_detected(self):
        assert not graph.is_acyclic({"A": "B", "B": "C", "C": "A"})
        assert graph.is_acyclic({"A": "B", "B": "C"})


class TestFlags:
    def test_no_basin_gets_no_flags(self):
        """The Boone well guard: a site with no delineation makes no claims to flag."""
        row = {"basin_method": None, "basin1_km2": 100.0, "vaa_totda_km2": 500.0,
               "flag_river_capture": True, "dist_to_basin_km": 9.0}
        assert not any(basins.evaluate(row).values())

    def test_absent_comparison_is_not_a_disagreement(self):
        assert not basins._ratio_off(np.nan, 100.0, 0.05)
        assert not basins._ratio_off(100.0, None, 0.05)
        assert basins._ratio_off(110.0, 100.0, 0.05)

    def test_nan_distance_is_not_outside(self):
        assert not basins._outside(np.nan, 0.25)
        assert basins._outside(1.0, 0.25)

    def test_uninformative_name_never_fires_capture(self):
        assert not basins.river_capture("WQS9903", "Des Moines River", 15000.0,
                                        [("Small Creek", 20.0, 100.0)], uid="IWQIS:WQS9903")
        assert basins.river_capture("Small Creek Site", "Des Moines River", 15000.0,
                                    [("Small Creek", 20.0, 100.0)], uid="IWQIS:WQS0001")
