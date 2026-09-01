"""Tests for the parts where a bug would be SILENT: unit conversion, the retransformation correction, and fold leakage.

Run from inside this directory: `python -m pytest test_discharge.py -q`.

Fold leakage is the one that would invalidate everything without changing a single number's plausibility -- a model that has seen a test basin's own outlet in training scores well and means nothing.
"""

import numpy as np
import pandas as pd
import pytest

import config
import cv
import features


class TestSpecificDischarge:
    def test_matches_a_hand_computed_case(self):
        """1 m3/s off 86.4 km2 is exactly 1 mm/day: 1 m3/s x 86400 s = 86,400 m3 over 86.4e6 m2 = 1 mm."""
        assert features.specific_discharge(1.0, 86.4) == pytest.approx(1.0)

    def test_scales_inversely_with_area(self):
        a = features.specific_discharge(10.0, 100.0)
        b = features.specific_discharge(10.0, 200.0)
        assert a == pytest.approx(2 * b)

    def test_a_plausible_midwest_basin_lands_in_range(self):
        """A 1,000 km2 basin at 10 m3/s is ~0.86 mm/day -- roughly 315 mm/yr of runoff, which is right here."""
        q = features.specific_discharge(10.0, 1000.0)
        assert 0.5 < q < 1.5


class TestMetrics:
    def test_nse_of_the_mean_predictor_is_zero(self):
        """The definition. If this drifts, NSE has silently become something else."""
        rng = np.random.default_rng(0)
        obs = rng.lognormal(0, 1, 400)
        assert cv.score(obs, np.full(400, obs.mean()))["nse"] == pytest.approx(0.0, abs=1e-12)

    def test_perfect_prediction_scores_one(self):
        obs = np.abs(np.random.default_rng(1).normal(5, 2, 300)) + 0.1
        s = cv.score(obs, obs)
        assert s["nse"] == pytest.approx(1.0) and s["kge"] == pytest.approx(1.0)

    def test_kge_punishes_a_flattened_hydrograph_that_nse_partly_forgives(self):
        """The reason both are reported: a damped prediction keeps the mean and loses the variance."""
        rng = np.random.default_rng(2)
        obs = rng.lognormal(0, 0.8, 500)
        damped = obs.mean() + 0.3 * (obs - obs.mean())
        s = cv.score(obs, damped)
        assert s["bias"] == pytest.approx(1.0, abs=1e-9)      # mean preserved
        assert s["kge"] < s["nse"]                            # variance ratio penalised

    def test_bias_is_a_ratio_not_a_difference(self):
        obs = np.full(100, 2.0)
        assert cv.score(obs, np.full(100, 3.0))["bias"] == pytest.approx(1.5)


class TestSmearing:
    def test_exponentiating_a_log_fit_underestimates_the_mean(self):
        """The bias the smearing estimator exists to remove; measured at 0.76 of observed on real data."""
        rng = np.random.default_rng(3)
        y = rng.normal(0, 0.8, 20_000)                        # log-space residuals
        naive = np.exp(y.mean())
        true_mean = np.exp(y).mean()
        assert naive < true_mean
        smear = np.mean(np.exp(y - y.mean()))
        assert naive * smear == pytest.approx(true_mean, rel=0.02)


class TestFolds:
    def test_no_family_appears_in_both_train_and_test(self):
        """THE experiment-invalidating bug: a test basin whose family was trained on is not held out."""
        groups = np.repeat(np.arange(50), 20)
        for tr, te in cv.folds(groups, n_splits=5):
            assert not (set(groups[tr]) & set(groups[te]))

    def test_every_row_is_tested_exactly_once(self):
        groups = np.repeat(np.arange(40), 10)
        seen = np.zeros(len(groups), dtype=int)
        for _, te in cv.folds(groups, n_splits=5):
            seen[te] += 1
        assert (seen == 1).all()

    def test_fewer_than_two_groups_yields_nothing_rather_than_a_bad_split(self):
        assert list(cv.folds(np.zeros(50), n_splits=5)) == []


class TestRunoffRatio:
    def test_bounds_are_physical_not_tuned(self):
        """A basin cannot shed more water than it receives; the upper bound allows headroom, not licence."""
        assert 0 < config.MIN_RUNOFF_RATIO < 0.05
        assert 1.0 <= config.MAX_RUNOFF_RATIO <= 2.0
