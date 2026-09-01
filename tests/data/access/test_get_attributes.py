"""`get_attributes`: the read path for the COMID-keyed attribute stores.

The series store is 7.3 GB over 1.48M COMIDs and 653 metrics, so `comids` is required rather than defaulted -- an unfiltered read is a memory event, not a slow query. These tests use a synthetic store so they assert the contract rather than the archive.
"""

import pandas as pd
import pytest

from data.access import api


@pytest.fixture
def attrs(tmp_path, monkeypatch):
    from data.access import config

    monkeypatch.setattr(config, "ACQ_ATTRIBUTES", tmp_path)
    d = pd.DataFrame({"comid": [10, 20, 30, 40],
                      "n_ff_1990cat": [1.0, 2.0, 3.0, 4.0],
                      "n_ff_1990ws": [10.0, 20.0, 30.0, 40.0],
                      "aoe_stamp": ["s"] * 4})
    d.to_parquet(tmp_path / "streamcat_series.parquet", index=False)
    return d


def test_filters_to_the_requested_comids(attrs):
    got = api.get_attributes([20, 40], source="streamcat_series")
    assert sorted(got.comid) == [20, 40]
    assert len(got) == 2


def test_column_selection_always_keeps_comid(attrs):
    got = api.get_attributes([10], columns=["n_ff_1990ws"], source="streamcat_series")
    assert list(got.columns) == ["comid", "n_ff_1990ws"]
    assert got.n_ff_1990ws.iloc[0] == 10.0


def test_asking_for_comid_explicitly_does_not_duplicate_it(attrs):
    got = api.get_attributes([10], columns=["comid", "n_ff_1990cat"], source="streamcat_series")
    assert list(got.columns).count("comid") == 1


def test_a_comid_absent_from_the_store_is_absent_from_the_result(attrs):
    """StreamCat covers 1,478,330 of 1,478,684 askable reaches -- a missing row is the service, not the join."""
    got = api.get_attributes([20, 999], source="streamcat_series")
    assert sorted(got.comid) == [20]


def test_unknown_source_is_refused(attrs):
    with pytest.raises(ValueError, match="unknown attribute source"):
        api.get_attributes([10], source="nope")


def test_absent_store_names_the_path(tmp_path, monkeypatch):
    from data.access import config

    monkeypatch.setattr(config, "ACQ_ATTRIBUTES", tmp_path)
    with pytest.raises(FileNotFoundError, match="streamcat_series"):
        api.get_attributes([10], source="streamcat_series")


def test_attribute_columns_reads_names_without_rows(attrs):
    names = api.attribute_columns("streamcat_series")
    assert "comid" in names and "n_ff_1990ws" in names
