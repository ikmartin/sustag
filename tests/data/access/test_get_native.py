"""`get_native`: the escape hatch that lets a model choose its own day boundary.

The published record is collapsed to Central Standard calendar days. gridMET's precipitation day runs
eight hours off that, and no relabelling of a daily series repairs it -- but the native samples can be
re-binned onto any boundary, which is the only real fix available without a new precipitation source.
"""

import pandas as pd
import pytest

from data.access import api


@pytest.fixture
def native(tmp_path, monkeypatch):
    from data.access import config

    monkeypatch.setattr(config, "ACQ_WATER_NATIVE", tmp_path)
    idx = pd.date_range("2020-06-01", "2020-06-03", freq="15min", tz="UTC", name="datetime")
    d = pd.DataFrame({"99133_uv": range(len(idx)), "00060_uv": range(len(idx))}, index=idx)
    d.to_parquet(tmp_path / "USGS__05412500.parquet")
    return d


def test_returns_utc_indexed_native_samples(native):
    d = api.get_native("USGS:05412500")
    assert len(d) == len(native) and str(d.index.tz) == "UTC"
    assert set(d.columns) == {"99133_uv", "00060_uv"}


def test_channels_and_window_narrow_the_read(native):
    d = api.get_native("USGS:05412500", channels=["99133_uv"], window=("2020-06-02", "2020-06-02 23:59"))
    assert list(d.columns) == ["99133_uv"]
    assert d.index.min().date().isoformat() == "2020-06-02"
    assert d.index.max().date().isoformat() == "2020-06-02"


def test_naive_window_bounds_are_read_as_utc(native):
    """A naive timestamp must not silently mean local time -- the index is UTC and so are the bounds."""
    d = api.get_native("USGS:05412500", window=(pd.Timestamp("2020-06-01"), pd.Timestamp("2020-06-01 12:00")))
    assert d.index.max() == pd.Timestamp("2020-06-01 12:00", tz="UTC")


def test_samples_can_be_rebinned_onto_a_foreign_day_boundary(native):
    """THE POINT OF THE FUNCTION. Binning onto gridMET's ~14:00 UTC day is a modelling choice the build
    cannot make on a modeller's behalf, and can only be made from sub-daily samples."""
    d = api.get_native("USGS:05412500", channels=["99133_uv"])
    gridmet_day = (d.index - pd.Timedelta(hours=14)).date
    daily = d.groupby(gridmet_day)["99133_uv"].mean()
    assert len(daily) >= 2 and daily.notna().all()


def test_missing_sensor_raises_rather_than_returning_empty(native):
    with pytest.raises(FileNotFoundError, match="no native series"):
        api.get_native("USGS:99999999")
