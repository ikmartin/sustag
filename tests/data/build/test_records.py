import pandas as pd
import pytest

from data.build.acquire import records


@pytest.fixture
def status_sandbox(tmp_path, monkeypatch):
    import data.build.config as config

    monkeypatch.setattr(records, "STATUS_PATH", tmp_path / "fetch_status.parquet")
    monkeypatch.setattr(config, "SENSORS_PATH", tmp_path / "sensors.parquet")
    return tmp_path


def _rows(*triples):
    return pd.DataFrame([dict(site_uid=u, status=s, detail=d,
                              fetch_last_attempt="2026-08-25T12:00:00",
                              fetch_last_success="2026-08-25T12:00:00" if s == "cached" else None,
                              params_available=None)
                         for u, s, d in triples], columns=records.ACCOUNTING_COLS)


def test_merge_accounting_accumulates_last_wins(status_sandbox, tmp_path):
    # two shard files cover disjoint sensors -- both survive the merge (the old single-file scheme
    # let the last shard clobber the others' bookkeeping)
    s0 = tmp_path / "fetch_status.shard0.parquet"
    s1 = tmp_path / "fetch_status.shard1.parquet"
    _rows(("USGS:A", "cached", "fetched+10")).to_parquet(s0)
    _rows(("USGS:B", "source_empty", "no_data")).to_parquet(s1)
    out = records.merge_accounting([s0, s1])
    assert set(out.site_uid) == {"USGS:A", "USGS:B"}
    assert not s0.exists() and not s1.exists()

    # a later run updates only the sensors it touched; untouched rows carry forward
    s2 = tmp_path / "fetch_status.run.parquet"
    _rows(("USGS:B", "cached", "fetched+5")).to_parquet(s2)
    out = records.merge_accounting([s2])
    got = out.set_index("site_uid")
    assert got.loc["USGS:B", "status"] == "cached"
    assert got.loc["USGS:A", "status"] == "cached"


def test_merge_accounting_preserves_last_success(status_sandbox, tmp_path):
    s0 = tmp_path / "fetch_status.run.parquet"
    _rows(("USGS:A", "cached", "fetched+10")).to_parquet(s0)
    records.merge_accounting([s0])
    # a later FAILED attempt must not erase the recorded last success
    s1 = tmp_path / "fetch_status.run.parquet"
    _rows(("USGS:A", "attempt_failed", "Timeout")).to_parquet(s1)
    out = records.merge_accounting([s1]).set_index("site_uid")
    assert out.loc["USGS:A", "status"] == "attempt_failed"
    assert out.loc["USGS:A", "fetch_last_success"] == "2026-08-25T12:00:00"


def test_states_are_the_partition():
    assert set(records.STATES) == {"skipped", "cached", "source_empty", "attempt_failed"}
