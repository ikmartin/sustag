import pandas as pd
import pytest

from data.build import io


def test_stamp_roundtrip_and_refusal(tmp_path):
    p = tmp_path / "t.parquet"
    io.write_parquet(pd.DataFrame({"a": [1, 2]}), p, stamps={"aoe": "30d8a9034912", "snapshot": "snap-x"})
    assert io.read_stamps(p) == {"aoe": "30d8a9034912", "snapshot": "snap-x"}
    assert len(io.read_parquet(p, expect={"aoe": "30d8a9034912"})) == 2
    with pytest.raises(io.StaleArtifactError):
        io.read_parquet(p, expect={"aoe": "ffffffffffff"})
    # A MISSING stamp fails like a wrong one: an unstamped artifact predates the rule it would record.
    with pytest.raises(io.StaleArtifactError):
        io.read_parquet(p, expect={"schema": "abc"})


def test_atomic_write_leaves_no_tmp(tmp_path):
    p = tmp_path / "t.parquet"
    io.write_parquet(pd.DataFrame({"a": [1]}), p)
    assert p.exists() and not list(tmp_path.glob("*.tmp"))


def test_local_day_boundary():
    # Etc/GMT+6: the day flips at 06:00 UTC. 05:59 is the previous local day.
    idx = pd.DatetimeIndex(["2026-06-01 05:59:00", "2026-06-01 06:01:00"], tz="UTC")
    days = list(io.local_day(idx))
    assert str(days[0]) == "2026-05-31" and str(days[1]) == "2026-06-01"


def test_local_day_date_only_grab_sample():
    # A date-only timestamp (assumed UTC midnight) must land on the PREVIOUS local day -- that is the
    # documented cost of one fixed offset, and the adapter's job is to localise grab samples first.
    idx = pd.DatetimeIndex(["2026-06-01"], tz="UTC")
    assert str(io.local_day(idx)[0]) == "2026-05-31"


def test_gap_stats():
    assert io.gap_stats([]) == (0, 0)
    assert io.gap_stats(["2026-01-01"]) == (1, 0)
    n, gap = io.gap_stats(["2026-01-01", "2026-01-02", "2026-01-10"])
    assert (n, gap) == (3, 7)


def test_sidecar_stamps(tmp_path):
    p = tmp_path / "thing.png"
    p.write_bytes(b"x")
    io.write_stamps_sidecar(p, {"aoe": "abc"})
    assert io.read_stamps_sidecar(p) == {"aoe": "abc"}


def test_in_seed_boxes_is_keyword_only():
    # An ancestor took (lat, lon) positionally; a port transposed it and 148 stations silently became 0.
    # Positional use must be a TypeError, not a wrong answer.
    from data.build import config

    with pytest.raises(TypeError):
        config.in_seed_boxes([42.0], [-93.5])
    assert config.in_seed_boxes(lon=[-93.5], lat=[42.0]).tolist() == [True]


def test_collection_window_is_a_start_date_only():
    import datetime

    from data.build import config

    assert config.collection_start() == datetime.date(2008, 1, 1)
