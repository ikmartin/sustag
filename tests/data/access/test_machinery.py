"""Access machinery kernels that need no stores: weighted sets, aggregation arithmetic, remap semantics."""

import numpy as np
import pandas as pd
import pytest

from data.access.machinery import aggregate, remap


def test_from_comids_is_weight_one():
    w = aggregate.from_comids([3, 1, 2, 2])
    assert w.comid.tolist() == [1, 2, 3] and (w.weight == 1.0).all()


def test_crops_weighted_and_remapped(monkeypatch):
    prod = pd.DataFrame({"comid": [1, 1, 2], "year": [2020, 2020, 2020],
                         "code": [1, 5, 1], "frac": [0.6, 0.4, 1.0], "n_px": [100.0, 100.0, 50.0]})
    monkeypatch.setattr(aggregate, "_product", lambda n: prod.copy())
    wset = pd.DataFrame({"comid": [1, 2], "weight": [1.0, 0.5]})
    # raw codes by default -- the code is the fact
    out = aggregate.crops(wset)
    assert out.attrs["remap"] == "none"
    got = out.set_index("code").area_px
    assert got[1] == pytest.approx(0.6 * 100 + 1.0 * 50 * 0.5)
    assert got[5] == pytest.approx(0.4 * 100)
    # the default map groups into classes and says so
    out = aggregate.crops(wset, remap="default")
    assert out.attrs["remap"] == "default"
    assert set(out["class"]) == {"Corn", "Soybeans"}


def test_nutrients_sum_kilograms(monkeypatch):
    prod = pd.DataFrame({"comid": [1, 2], "product": ["surplus", "surplus"], "year": [2010, 2010],
                         "kg": [100.0, 60.0], "kg_per_ha": [2.0, 3.0], "n_px": [10.0, 5.0]})
    monkeypatch.setattr(aggregate, "_product", lambda n: prod.copy())
    wset = pd.DataFrame({"comid": [1, 2], "weight": [1.0, 0.5]})
    out = aggregate.nutrients(wset)
    assert out.kg.iloc[0] == pytest.approx(100 + 30)


def test_weather_weights_collapse(monkeypatch):
    prod = pd.DataFrame({"comid": [1, 1, 2], "cell_id": [10, 11, 10], "w_km2": [4.0, 2.0, 6.0]})
    monkeypatch.setattr(aggregate, "_product", lambda n: prod.copy())
    wset = pd.DataFrame({"comid": [1, 2], "weight": [1.0, 0.5]})
    out = aggregate.weather_weights(wset).set_index("cell_id").w_km2
    assert out[10] == pytest.approx(4.0 + 3.0) and out[11] == pytest.approx(2.0)


def test_remap_covers_all_bytes():
    lut = remap.class_index()
    assert lut.shape[0] == 256
    assert remap.CLASSES[lut[1]] == "Corn" and remap.CLASSES[lut[121]] == "Nonag"
