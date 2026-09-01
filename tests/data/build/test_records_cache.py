"""`cached` must mean cached FOR THIS REQUEST, not merely "a file is here".

The fetch is channel-scoped, so a native on disk holds only what was declared when it was fetched. Adding a channel to the registry widens `channels_declared`, canonicalisation correctly rebuilds from those natives, and the new channel is all-null everywhere while every sensor reports `cached` -- indistinguishable downstream from a sensor that genuinely does not measure it.
"""

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.build.acquire import records


@pytest.fixture
def native(tmp_path):
    def write(*cols):
        p = tmp_path / "USGS__05412500.parquet"
        idx = pd.date_range("2020-01-01", periods=3, tz="UTC", name="datetime")
        d = pd.DataFrame({c: [1.0, 2.0, 3.0] for c in cols}, index=idx)
        d.to_parquet(p)
        return p
    return write


def test_native_holding_every_declared_channel_is_covered(native):
    p = native("99133_uv", "00060_uv")
    assert records._covers_declared(p, "USGS", "nitrate+discharge") is True


def test_native_missing_a_declared_channel_is_not_covered(native):
    """THE BUG: a native fetched before `nitrate` was declared has no nitrate column, and every downstream
    stage would read that absence as 'this sensor does not measure nitrate'."""
    p = native("00060_uv")
    assert records._covers_declared(p, "USGS", "nitrate+discharge") is False


def test_any_of_a_channels_alternate_columns_counts(native):
    """`nitrate` maps to several USGS pcodes; carrying any one of them satisfies the declaration."""
    p = native("00630_dv")
    assert records._covers_declared(p, "USGS", "nitrate") is True


def test_a_channel_the_source_does_not_advertise_is_not_owed(native):
    """Coverage is judged against what this SOURCE could supply, never against the channel list at large."""
    p = native("99133_uv")
    assert records._covers_declared(p, "USGS", "nitrate+a_channel_usgs_lacks") is True


def test_no_declaration_means_nothing_to_check(native):
    assert records._covers_declared(native("00060_uv"), "USGS", None) is True


def test_a_missing_native_is_not_covered(tmp_path):
    assert records._covers_declared(tmp_path / "absent.parquet", "USGS", "nitrate") is False
