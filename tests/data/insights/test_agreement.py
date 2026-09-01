"""Tier 2's battery, checked against panels whose answer is known by construction.

Each statistic is asserted on data built to have the property it claims to detect -- a column that IS a combination of others, a panel that is purely spatial, one that is purely temporal. A summary statistic nobody has watched succeed on a known case is not evidence.
"""

import numpy as np
import pandas as pd
import pytest

from data.insights import agreement


def _panel(values, products, comids=8, years=5):
    """A (comid, year) x product frame from a callable value(c, t, p)."""
    idx = pd.MultiIndex.from_product([range(comids), range(2000, 2000 + years)],
                                     names=["comid", "year"])
    return pd.DataFrame({p: [values(c, t, p) for c, t in idx] for p in products}, index=idx)


class TestIncrementalInformation:
    def test_an_exact_combination_is_reconstructible(self):
        rng = np.random.default_rng(0)
        base = _panel(lambda c, t, p: rng.normal(), ["a", "b"])
        base["c"] = base.a + base.b                    # exactly implied by the others
        got = agreement.incremental_information(base).set_index("product")
        assert got.loc["c", "r2_given_others"] > 0.999
        assert got.loc["c", "verdict"] == "reconstructible from the pool"

    def test_an_independent_column_carries_its_own_signal(self):
        rng = np.random.default_rng(1)
        d = _panel(lambda c, t, p: rng.normal(), ["a", "b", "c"], comids=200)
        got = agreement.incremental_information(d).set_index("product")
        assert (got.r2_given_others < 0.5).all()
        assert set(got.verdict) == {"carries its own signal"}

    def test_exclude_drops_columns_by_name(self):
        d = _panel(lambda c, t, p: float(c + t), ["a", "b", "closure"])
        got = agreement.incremental_information(d, exclude=("closure",))
        assert "closure" not in set(got["product"])

    def test_too_few_columns_returns_empty_rather_than_raising(self):
        d = _panel(lambda c, t, p: 1.0, ["only"])
        assert agreement.incremental_information(d).empty


class TestVarianceDecomposition:
    def test_a_pure_static_map_is_all_space(self):
        """Value depends on catchment alone -- eighteen annual layers of the same picture."""
        d = _panel(lambda c, t, p: float(c), ["m"])
        row = agreement.variance_decomposition(d).iloc[0]
        assert row.space == pytest.approx(1.0, abs=1e-6)
        assert row.interaction == pytest.approx(0.0, abs=1e-6)
        assert row.verdict == "static map + year dummy"

    def test_a_pure_national_trend_is_all_time(self):
        d = _panel(lambda c, t, p: float(t), ["m"])
        row = agreement.variance_decomposition(d).iloc[0]
        assert row.time == pytest.approx(1.0, abs=1e-6)
        assert row.interaction == pytest.approx(0.0, abs=1e-6)

    def test_a_genuine_interaction_is_detected(self):
        """Each catchment moves on its own trajectory -- the only case worth 18 years of rows.

        The factors are CENTRED: a product of uncentred factors is not pure interaction, because the main effects legitimately absorb most of it (c*(t-2000) over c in 0..7 decomposes 0.375/0.4375/0.1875). Centring is what isolates the term this test is about.
        """
        d = _panel(lambda c, t, p: (c - 3.5) * (t - 2002.0), ["m"])
        row = agreement.variance_decomposition(d).iloc[0]
        assert row.interaction == pytest.approx(1.0, abs=1e-6)
        assert row.verdict == "spatiotemporal"

    def test_uncentred_factors_split_across_main_effects(self):
        """Guards the reading of the table above: a low interaction share is not proof of no interaction."""
        d = _panel(lambda c, t, p: float(c) * (t - 2000), ["m"])
        row = agreement.variance_decomposition(d).iloc[0]
        assert row.space + row.time + row.interaction == pytest.approx(1.0, abs=1e-6)
        assert 0.0 < row.interaction < 0.5

    def test_shares_sum_to_one(self):
        rng = np.random.default_rng(2)
        d = _panel(lambda c, t, p: float(c) + 0.5 * t + rng.normal(), ["m"], comids=30)
        row = agreement.variance_decomposition(d).iloc[0]
        assert row.space + row.time + row.interaction == pytest.approx(1.0, abs=1e-3)

    def test_a_constant_product_is_skipped_not_divided_by_zero(self):
        d = _panel(lambda c, t, p: 7.0, ["flat"])
        assert agreement.variance_decomposition(d).empty


class TestSpearman:
    def test_constant_columns_are_dropped_rather_than_returning_nan(self):
        """A constant correlates with nothing; NaN would read as 'no relationship'."""
        d = _panel(lambda c, t, p: 1.0 if p == "flat" else float(c), ["flat", "varies", "also"])
        m = agreement.spearman_matrix(d)
        assert "flat" not in m.columns
        assert not m.isna().any().any()

    def test_top_pairs_is_long_form_and_sorted(self):
        d = _panel(lambda c, t, p: float(c) if p != "c" else float(-c), ["a", "b", "c"])
        pairs = agreement.top_pairs(d, n=2)
        assert list(pairs.columns) == ["a", "b", "spearman"]
        assert pairs.spearman.is_monotonic_decreasing


class TestSampling:
    def test_the_sample_is_stable_across_calls(self, monkeypatch):
        """A regenerated report must not move for sampling reasons alone."""
        fake = pd.DataFrame({"comid": range(1000), "areasqkm": 1.0})
        monkeypatch.setattr("data.access.api.get_network", lambda layer="flowlines": fake)
        a = agreement.sample_comids(50)
        b = agreement.sample_comids(50)
        assert (a == b).all()
        assert len(a) == 50


class TestNullStructure:
    """`_null_structure` decides whether a gap is geography. Its failure mode is calling noise structure."""

    def _nulls(self, pattern, n_basin=20, per_basin=50):
        basins = [f"b{i}" for i in range(n_basin) for _ in range(per_basin)]
        null = [pattern(i, j) for i in range(n_basin) for j in range(per_basin)]
        return np.array(null), pd.Series(basins)

    def test_nulls_confined_to_a_few_basins_are_structured(self):
        null, b = self._nulls(lambda i, j: i < 2)          # 2 of 20 basins entirely null
        r = agreement._null_structure(null, b)
        assert r["basins_all_null"] == 2
        assert r["share_in_worst_decile"] == pytest.approx(1.0)
        assert "structured" in r["verdict"]

    def test_nulls_spread_evenly_are_diffuse(self):
        null, b = self._nulls(lambda i, j: j % 5 == 0)      # same rate in every basin
        r = agreement._null_structure(null, b)
        assert r["share_in_worst_decile"] == pytest.approx(0.10, abs=0.02)
        assert r["verdict"] == "diffuse"

    def test_too_few_nulls_is_inconclusive_not_structured(self):
        """The decile is picked after seeing the data, so a handful of nulls always looks concentrated."""
        null, b = self._nulls(lambda i, j: i == 0 and j < 3)   # 3 nulls over 20 basins
        r = agreement._null_structure(null, b)
        assert r["n_null"] == 3
        assert r["share_in_worst_decile"] == pytest.approx(1.0)
        assert "inconclusive" in r["verdict"]

    def test_no_nulls_reports_cleanly(self):
        null, b = self._nulls(lambda i, j: False)
        assert agreement._null_structure(null, b)["verdict"] == "no nulls"
