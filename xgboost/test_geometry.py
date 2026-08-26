"""Tests for the project's own geometry: flow bucketing, weighting, lags, and the frame plumbing.

Run from this directory: `python -m pytest test_geometry.py -q`. Deliberately NOT under tests/data -- that tree tests the pipeline, and this project is a consumer of it, not part of it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import transforms as T


def _wset():
    """Four catchments at 0, 5, 12 and 40 km of flow distance, 1 km2 each."""
    return pd.DataFrame({
        "comid": [1, 2, 3, 4],
        "w": [1.0, 1.0, 1.0, 1.0],
        "areasqkm": [1.0, 1.0, 1.0, 1.0],
        "pathlength": [100.0, 105.0, 112.0, 140.0],
        "flow_dist_m": [0.0, 5_000.0, 12_000.0, 40_000.0],
        "pathtimema": [10.0, 10.5, 11.0, 13.0],
    })


def test_flow_buckets_split_on_edges():
    b = T.flow_buckets(_wset(), (8_000.0, 30_000.0))
    assert list(b.to_numpy()) == [0, 0, 1, 2]      # 0/5 km near, 12 km middle, 40 km far


def test_no_edges_is_one_bucket():
    b = T.flow_buckets(_wset(), ())
    assert set(b.to_numpy()) == {0}


def test_decay_weights_fall_with_flow_distance():
    plain = T.bucket_weights(_wset())
    decayed = T.bucket_weights(_wset(), decay_lambda=20_000.0)
    assert list(plain.to_numpy()) == [1.0, 1.0, 1.0, 1.0]        # membership x area, no decay
    assert decayed.iloc[0] > decayed.iloc[1] > decayed.iloc[2] > decayed.iloc[3]
    assert decayed.iloc[0] == pytest.approx(1.0)                 # at the outlet, exp(0) = 1


def test_sum_accumulates_mass_and_mean_does_not_scale_with_size():
    """A mass sums; an intensity must NOT -- summing an intensity builds a feature that measures basin size."""
    wset = _wset()
    buckets = T.flow_buckets(wset, ())
    weights = T.bucket_weights(wset)
    frame = pd.DataFrame({"comid": [1, 2, 3, 4], "year": [2020] * 4, "kg": [10.0, 10.0, 10.0, 10.0],
                          "kgha": [5.0, 5.0, 5.0, 5.0]})
    mass = T.agg_to_buckets(frame, buckets, weights, keys=["year"], value_cols=["kg"], how="sum")
    inten = T.agg_to_buckets(frame, buckets, weights, keys=["year"], value_cols=["kgha"], how="mean")
    assert mass.kg.iloc[0] == pytest.approx(40.0)     # four catchments x 10 kg
    assert inten.kgha.iloc[0] == pytest.approx(5.0)   # the intensity is unchanged by how many there are


def test_lags_prefer_the_network_path_time():
    wset = _wset()
    buckets = T.flow_buckets(wset, (8_000.0, 30_000.0))
    lags = T.bucket_lags(wset, buckets, velocity_mps=0.5)
    # pathtimema differenced to the outlet: 0, 0.5, 1.0, 3.0 days -> medians 0, 1, 3 by bucket
    assert lags[0] == 0 and lags[1] == 1 and lags[2] == 3


def test_lag_shifts_dates_forward_per_bucket():
    frame = pd.DataFrame({"date": pd.to_datetime(["2020-01-01"] * 2), "bucket": [0, 2], "pr": [1.0, 2.0]})
    out = T.lag_buckets(frame, {0: 0, 2: 3})
    assert out.date.tolist() == [pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-04")]


def test_flatten_names_columns_by_flow_bucket():
    frame = pd.DataFrame({"year": [2020, 2020], "bucket": [0, 1], "corn": [0.4, 0.6]})
    wide = T.flatten_buckets(frame)
    # `_f` for FLOW distance -- never `_b`, which would read as the predecessor's euclidean rings
    assert set(wide.columns) == {"year", "corn_f0", "corn_f1"}
    assert wide.corn_f0.iloc[0] == pytest.approx(0.4)


def test_merge_broadcasts_year_frames_over_daily_spine():
    spine = pd.date_range("2020-12-30", "2021-01-02", freq="D")
    yearly = pd.DataFrame({"year": [2020, 2021], "corn_f0": [0.4, 0.6]})
    daily = pd.DataFrame({"date": spine, "pr": [1.0, 2.0, 3.0, 4.0]})
    out = T.merge_on_date([yearly, daily], spine=spine)
    assert out.corn_f0.tolist() == [0.4, 0.4, 0.6, 0.6]
    assert out.pr.tolist() == [1.0, 2.0, 3.0, 4.0]


def test_empty_inputs_do_not_raise():
    empty = pd.DataFrame(columns=["comid", "w", "areasqkm", "flow_dist_m"])
    assert not len(T.flow_buckets(empty, (1_000.0,)))
    assert not len(T.bucket_weights(empty))
    assert T.bucket_lags(empty, pd.Series(dtype="int64"), 0.5) == {}
