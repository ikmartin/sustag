"""`get_drought`: the pentad reader, and the naming that stops a pentad value being read as a daily one.

The store exists separately because these are one value per five days. The failure this guards is not an exception — it is a silent join in which four days of every five take the wrong value — so the contract is that the date column is NOT called `date`.
"""

import numpy as np
import pandas as pd
import pytest

from data.access import api


@pytest.fixture
def pentad(tmp_path, monkeypatch):
    from data.access import config

    monkeypatch.setattr(config, "ACQ_WEATHER_PENTAD", tmp_path)
    days = pd.date_range("2015-01-05", periods=6, freq="5D")
    rows = []
    for c, val in ((10, 1.0), (20, 3.0)):            # two cells, so weighting is exercised
        for d in days:
            rows.append({"cell_id": c, "date": d, "spei90d": val, "pdsi": -val})
    pd.DataFrame(rows).to_parquet(tmp_path / "gridmet_pentad_2015.parquet", index=False)
    return days


@pytest.fixture
def weights(monkeypatch):
    """Equal-area weights over the two cells, so the mean of 1.0 and 3.0 must come back as 2.0."""
    from data.access.machinery import aggregate as agg

    monkeypatch.setattr(agg, "weather_weights",
                        lambda wset: pd.DataFrame({"cell_id": [10, 20], "w_km2": [1.0, 1.0]}))
    monkeypatch.setattr(agg, "from_comids", lambda c: pd.DataFrame({"comid": list(c), "weight": 1.0}))


def test_the_date_column_is_not_called_date(pentad, weights):
    """A frame calling it `date` would join silently against a daily series and misalign four days in five."""
    d = api.get_drought([1], ("2015-01-01", "2015-12-31"), variables=("spei90d",))
    assert "pentad_start" in d.columns
    assert "date" not in d.columns


def test_the_cadence_is_stated_in_the_return(pentad, weights):
    d = api.get_drought([1], ("2015-01-01", "2015-12-31"), variables=("spei90d",))
    assert d.attrs["cadence"] == "pentad"


def test_rows_are_five_days_apart(pentad, weights):
    d = api.get_drought([1], ("2015-01-01", "2015-12-31"), variables=("spei90d",))
    gaps = d.pentad_start.diff().dt.days.dropna().unique()
    assert set(gaps) == {5}


def test_values_are_area_weighted_across_cells(pentad, weights):
    d = api.get_drought([1], ("2015-01-01", "2015-12-31"), variables=("spei90d",))
    assert d.spei90d.round(6).eq(2.0).all()          # equal weights over 1.0 and 3.0

def test_the_window_bounds_the_result(pentad, weights):
    d = api.get_drought([1], ("2015-01-05", "2015-01-15"), variables=("spei90d",))
    assert len(d) == 3

def test_multiple_variables_come_back_together(pentad, weights):
    d = api.get_drought([1], ("2015-01-01", "2015-12-31"), variables=("spei90d", "pdsi"))
    assert list(d.columns) == ["pentad_start", "spei90d", "pdsi"]
    assert d.pdsi.round(6).eq(-2.0).all()

def test_an_empty_cell_set_returns_the_shape_not_an_error(pentad, monkeypatch):
    from data.access.machinery import aggregate as agg

    monkeypatch.setattr(agg, "weather_weights", lambda wset: pd.DataFrame(columns=["cell_id", "w_km2"]))
    monkeypatch.setattr(agg, "from_comids", lambda c: pd.DataFrame({"comid": list(c), "weight": 1.0}))
    d = api.get_drought([1], ("2015-01-01", "2015-12-31"), variables=("spei90d",))
    assert list(d.columns) == ["pentad_start", "spei90d"] and len(d) == 0
