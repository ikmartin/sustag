"""Basin-editor delineation against the REAL network stores -- skipped where the archive is absent."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools"))

from data.access import config

pytestmark = pytest.mark.skipif(not (config.ACQ_NETWORK / "vaa.parquet").exists(),
                                reason="network archive not on this machine")


def test_example_case_basin1_matches_vaa_within_1pct():
    from basin_editor import delineate, example

    b1 = delineate.basin1(example.LAT, example.LON)
    assert b1["status"] == "ok"
    assert abs(b1["area_km2"] - b1["vaa_km2"]) / b1["vaa_km2"] <= 0.01
    assert len(b1["comids"]) > 100
    ws = delineate.to_weighted_set(b1)
    assert len(ws) == len(b1["comids"]) and (ws.weight == 1.0).all()


def test_coastline_refusal():
    """Walking upstream from a Coastline reach is a category error the machinery must refuse, not approximate."""
    from data.access.machinery import accumulate as acc

    net = acc.Network.load()
    import pandas as pd

    vaa = pd.read_parquet(config.ACQ_NETWORK / "vaa.parquet", columns=["comid", "ftype"])
    coast = vaa[vaa.ftype.isin(acc.NON_DRAINAGE_FTYPE)]
    if not len(coast):
        pytest.skip("no coastline reaches in this AOE cut")
    comids, area, vaa_km2, status = net.accumulate(int(coast.comid.iloc[0]))
    assert status == "no_drainage_reach" and not comids   # refused: empty walk, named reason
