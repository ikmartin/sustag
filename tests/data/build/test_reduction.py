import numpy as np
import pandas as pd
import pytest

from data.build.derive import reduction


def _obs(values, times, col="nitrate"):
    idx = pd.DatetimeIndex(times, tz="UTC")
    idx.name = "datetime"
    return pd.DataFrame({col: values}, index=idx)


def test_clean_sensor_roundtrips_exactly():
    # A sensor with nothing to scrub reproduces its raw daily statistics exactly -- the d2 done-when.
    obs = _obs([1.0, 3.0, 2.0], ["2021-06-01 10:00", "2021-06-01 14:00", "2021-06-02 10:00"])
    f, tal = reduction.summarise(obs, "MPCA", channels=["nitrate"])
    assert f.loc[f.index[0], "nitrate_mean"] == 2.0
    assert f.loc[f.index[0], "nitrate_min"] == 1.0 and f.loc[f.index[0], "nitrate_max"] == 3.0
    assert f.loc[f.index[0], "nitrate_n_obs"] == 2
    t = tal.iloc[0]
    assert t.n_cells == 3 and t.n_kept == 3 and t.n_sentinel == 0


def test_scrub_tally_closure_and_sparse_days():
    # sentinel + below-lo cells vanish COUNTED; a day left with nothing has no row, never a null one
    obs = _obs([1.0, 999.99, -12.4], ["2021-06-01 10:00", "2021-06-01 11:00", "2021-06-02 10:00"])
    f, tal = reduction.summarise(obs, "MPCA", channels=["nitrate"])
    t = tal.iloc[0]
    assert (t.n_cells, t.n_sentinel, t.n_below_lo, t.n_kept, t.n_days) == (3, 1, 1, 1, 1)
    assert len(f) == 1                                  # the -12.4-only day does not exist


def test_subdetection_clamps_to_zero_not_deleted():
    obs = _obs([-0.5, 1.0], ["2021-06-01 10:00", "2021-06-01 11:00"])
    f, tal = reduction.summarise(obs, "MPCA", channels=["nitrate"])
    assert f.iloc[0]["nitrate_min"] == 0.0              # the sub-detection reading survives, at 0
    assert tal.iloc[0].n_clamped == 1 and tal.iloc[0].n_kept == 2


def test_day_boundary_is_standard_time():
    # 05:59 UTC is the previous Etc/GMT+6 day; 06:01 the next
    obs = _obs([1.0, 2.0], ["2021-06-01 05:59", "2021-06-01 06:01"])
    f, _ = reduction.summarise(obs, "MPCA", channels=["nitrate"])
    assert [str(d) for d in f.index] == ["2021-05-31", "2021-06-01"]


def test_preaggregated_day_writes_null_spread():
    idx = pd.DatetimeIndex(["2021-06-01", "2021-06-02"], tz="UTC")
    idx.name = "datetime"
    obs = pd.DataFrame({"99133_dv": [3.0, 4.0]}, index=idx)
    f, tal = reduction.summarise(obs, "USGS", channels=["nitrate"])
    assert f.iloc[0]["nitrate_mean"] == 3.0
    assert pd.isna(f.iloc[0]["nitrate_max"]) and pd.isna(f.iloc[0]["nitrate_n_obs"])
    assert bool(tal.iloc[0].preaggregated)


def test_mean_stats_channels_store_mean_and_count_only():
    idx = pd.DatetimeIndex(["2021-06-01 10:00", "2021-06-01 11:00"], tz="UTC")
    idx.name = "datetime"
    obs = pd.DataFrame({"00060": [100.0, 200.0]}, index=idx)
    f, _ = reduction.summarise(obs, "USGS", channels=["discharge"])
    assert "discharge_mean" in f.columns and "discharge_n_obs" in f.columns
    assert "discharge_max" not in f.columns
