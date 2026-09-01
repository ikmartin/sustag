"""`get_discrete_nitrate`: the read path for WQX samples, which are NOT sensors.

The contract these guard: quality-control activities are excluded unless asked for (a field blank reads as a near-zero nitrate and would not look anomalous), and the frame declares its class so nobody mistakes discrete samples for a monitored series.
"""

import pandas as pd
import pytest

from data.access import api


@pytest.fixture
def store(tmp_path, monkeypatch):
    from data.access import config

    monkeypatch.setattr(config, "ACQ_WQX", tmp_path)
    rows = [
        {"Location_Identifier": "A-1", "lat": 42.0, "lon": -93.0, "Activity_StartDate": "2015-06-01",
         "Activity_TypeCode": "Sample-Routine", "Result_Characteristic": "Nitrate", "Result_Measure": 3.0},
        {"Location_Identifier": "A-2", "lat": 41.0, "lon": -92.0, "Activity_StartDate": "2019-06-01",
         "Activity_TypeCode": "Quality Control Sample-Field Blank", "Result_Characteristic": "Nitrate",
         "Result_Measure": 0.01},
        {"Location_Identifier": "A-3", "lat": 40.0, "lon": -91.0, "Activity_StartDate": "2021-06-01",
         "Activity_TypeCode": "Sample-Composite Without Parents",
         "Result_Characteristic": "Nitrate + Nitrite", "Result_Measure": 5.0},
    ]
    pd.DataFrame(rows).to_parquet(tmp_path / "nitrate_IA.parquet", index=False)
    return tmp_path


def test_quality_control_is_excluded_by_default(store):
    """A field blank is not an observation of the river and would not look anomalous among low readings."""
    d = api.get_discrete_nitrate()
    assert len(d) == 2
    assert not d.Activity_TypeCode.str.startswith("Quality Control").any()


def test_quality_control_can_be_asked_for(store):
    assert len(api.get_discrete_nitrate(include_qc=True)) == 3


def test_the_frame_declares_it_is_not_a_sensor_series(store):
    assert api.get_discrete_nitrate().attrs["class"] == "discrete_sample"


def test_no_site_uid_is_offered(store):
    """These have no node identity, so nothing should invite a join against get_sensors."""
    assert "site_uid" not in api.get_discrete_nitrate().columns


def test_window_filters_on_activity_date(store):
    d = api.get_discrete_nitrate(window=("2015-01-01", "2016-01-01"))
    assert len(d) == 1 and d.Location_Identifier.iloc[0] == "A-1"


def test_bbox_filters_spatially(store):
    d = api.get_discrete_nitrate(bbox=(-93.5, 41.5, -92.5, 42.5))
    assert len(d) == 1 and d.Location_Identifier.iloc[0] == "A-1"


def test_activity_type_is_selectable(store):
    """A routine grab is an instant; a composite averages over time. A model may want them apart."""
    d = api.get_discrete_nitrate(activity_types=("Sample-Composite Without Parents",))
    assert len(d) == 1 and d.Location_Identifier.iloc[0] == "A-3"


def test_characteristic_is_selectable(store):
    assert len(api.get_discrete_nitrate(characteristics=("Nitrate",))) == 1


def test_an_uncollected_state_returns_empty_not_an_error(store):
    assert api.get_discrete_nitrate(states=["ZZ"]).empty


def test_states_helper_lists_what_is_collected(store):
    assert api.discrete_nitrate_states() == ["IA"]
