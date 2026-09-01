"""The within-basin variance decomposition, checked on layouts whose answer is known by construction.

`within` is the share a basin-level aggregate destroys, so the two extremes are the tests that matter: a source constant inside each basin loses nothing to aggregation, and one that varies only inside basins loses everything.
"""

import numpy as np
import pandas as pd
import pytest

from data.insights import geometry


def _panel(fn, n_basin=6, per_basin=20):
    basins = [f"b{i}" for i in range(n_basin) for _ in range(per_basin)]
    vals = [fn(i, j) for i in range(n_basin) for j in range(per_basin)]
    return pd.Series(vals), pd.Series(basins)


class TestDecompose:
    def test_constant_within_each_basin_is_all_between(self):
        """A basin-level constant: aggregation destroys nothing, so `within` is 0."""
        v, b = _panel(lambda i, j: float(i))
        r = geometry.decompose(v, b)
        assert r["within"] == pytest.approx(0.0, abs=1e-9)
        assert r["between"] == pytest.approx(1.0, abs=1e-9)

    def test_identical_basin_means_is_all_within(self):
        """Every basin has the same mean, so all structure is inside basins and aggregation destroys it."""
        v, b = _panel(lambda i, j: float(j))
        r = geometry.decompose(v, b)
        assert r["within"] == pytest.approx(1.0, abs=1e-9)
        assert r["between"] == pytest.approx(0.0, abs=1e-9)

    def test_shares_sum_to_one(self):
        rng = np.random.default_rng(0)
        v, b = _panel(lambda i, j: float(i) + rng.normal())
        r = geometry.decompose(v, b)
        assert r["within"] + r["between"] == pytest.approx(1.0, abs=1e-3)

    def test_singleton_basins_are_dropped_not_counted_as_zero_within(self):
        """One catchment in a basin has no within-basin variance to measure; keeping it would bias `within` down."""
        v = pd.Series([1.0, 2.0, 3.0, 9.0])
        b = pd.Series(["a", "a", "a", "solo"])
        r = geometry.decompose(v, b)
        assert r["basins"] == 1 and r["n"] == 3

    def test_a_constant_series_reports_nan_rather_than_dividing_by_zero(self):
        v, b = _panel(lambda i, j: 5.0)
        r = geometry.decompose(v, b)
        assert np.isnan(r["within"])

    def test_nulls_are_dropped(self):
        v = pd.Series([1.0, np.nan, 3.0, 4.0, np.nan, 6.0])
        b = pd.Series(["a", "a", "a", "b", "b", "b"])
        assert geometry.decompose(v, b)["n"] == 4


class TestSampling:
    def test_basin_sample_is_stable(self, monkeypatch):
        fake = pd.DataFrame({"comid": range(600), "huc8": [f"0701{i%30:04d}" for i in range(600)]})
        monkeypatch.setattr(geometry, "_huc8_map", lambda: fake)
        a, b = geometry.sample_basins(5), geometry.sample_basins(5)
        assert a.huc8.nunique() == 5
        assert sorted(a.comid) == sorted(b.comid)

    def test_whole_basins_come_back_not_scattered_catchments(self, monkeypatch):
        """A scattered catchment sample would leave almost no two catchments sharing a basin."""
        fake = pd.DataFrame({"comid": range(600), "huc8": [f"0701{i%30:04d}" for i in range(600)]})
        monkeypatch.setattr(geometry, "_huc8_map", lambda: fake)
        got = geometry.sample_basins(5)
        assert (got.groupby("huc8").size() == 20).all()
