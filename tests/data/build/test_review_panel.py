"""The panel's round-trip contract: decide -> CSV row -> reload shows it; malformed values never reach a sheet."""

import pandas as pd
import pytest

from data.build import ledger as L
from data.build.review_panel import backend


@pytest.fixture
def merge_sheet(decisions_sandbox):
    cand = pd.DataFrame({"uid_a": ["IWQIS:A", "IWQIS:C"], "uid_b": ["USGS:B", "USGS:D"],
                         "sep_m": [8.1, 40.0], "proposed": ["dual_registration", "complementary"]})
    L.MERGE.sync(cand)
    return cand


def test_round_trip_decide_and_reload(merge_sheet):
    assert backend.queue_counts()["merge"] == (2, 2)
    msg = backend.submit_decision("merge", ("IWQIS:A", "USGS:B"), "merge", decided_by="isaac",
                                  note="one structure")
    assert "merge" in msg
    d = backend.sheet("merge").set_index(["uid_a", "uid_b"])
    row = d.loc[("IWQIS:A", "USGS:B")]
    assert row.decision == "merge" and row.decided_by == "isaac"
    assert row[L.CRITERION_COL] == L.MERGE.criterion
    assert not row._pending and not row._stale
    assert backend.queue_counts()["merge"] == (1, 2)
    # undo restores not-reviewed
    backend.submit_decision("merge", ("IWQIS:A", "USGS:B"), "")
    assert backend.queue_counts()["merge"] == (2, 2)


def test_unknown_token_refused_before_any_write(merge_sheet):
    before = L.MERGE.path.read_bytes()
    with pytest.raises(ValueError, match="not one of"):
        backend.submit_decision("merge", ("IWQIS:A", "USGS:B"), "maybe")
    assert L.MERGE.path.read_bytes() == before


def test_malformed_quality_extras_refused_before_any_write(decisions_sandbox):
    L.QUALITY.sync(pd.DataFrame({"members": ["IWQIS:A"], "channel": ["nitrate"], "score": [9.9]}))
    before = L.QUALITY.path.read_bytes()
    with pytest.raises(ValueError, match="not 'YYYY-MM-DD"):
        backend.submit_decision("quality", ("IWQIS:A", "nitrate"), "keep",
                                exclude_spans="not-a-span")
    with pytest.raises(ValueError, match="not a number"):
        backend.submit_decision("quality", ("IWQIS:A", "nitrate"), "keep", max_threshold="lots")
    assert L.QUALITY.path.read_bytes() == before
    # and a WELL-FORMED decision with extras lands
    backend.submit_decision("quality", ("IWQIS:A", "nitrate"), "keep",
                            exclude_spans="2021-03-01..2021-07-15", max_threshold="44.0")
    d = backend.sheet("quality").iloc[0]
    assert d.exclude_spans == "2021-03-01..2021-07-15" and d.max_threshold == "44.0"


def test_stale_criterion_rows_resurface_pending(decisions_sandbox, monkeypatch):
    cand = pd.DataFrame({"uid_a": ["IWQIS:A"], "uid_b": ["USGS:B"], "sep_m": [8.1]})
    L.MERGE.sync(cand)
    backend.submit_decision("merge", ("IWQIS:A", "USGS:B"), "merge")
    assert backend.queue_counts()["merge"] == (0, 1)
    import dataclasses

    v2 = dataclasses.replace(L.MERGE, criterion="prox100-v2")
    monkeypatch.setattr(L, "MERGE", v2)
    monkeypatch.setattr(L, "ALL", tuple(v2 if led.name == "merge" else led for led in L.ALL))
    d = backend.sheet("merge").iloc[0]
    assert d._pending and d._stale and d.decision == "merge"   # prior verdict visible, authority gone
    assert backend.queue_counts()["merge"] == (1, 1)


def test_sheets_hide_excluded_sensors(decisions_sandbox):
    """An exclude verdict hides the sensor's rows on every other sheet immediately."""
    from data.build import ledger
    from data.build.review_panel import backend

    ledger.MERGE.sync(__import__("pandas").DataFrame(
        {"uid_a": ["X:1", "X:2"], "uid_b": ["X:9", "X:8"], "proposed": ["distinct", "distinct"]}))
    ledger.EXCLUSIONS.add_row(("X:9",))
    ledger.EXCLUSIONS.decide(("X:9",), "exclude", decided_by="t", note="scratch")
    d = backend.sheet("merge")
    assert list(d.uid_a) == ["X:2"]
    assert "X:9" in backend.excluded_uids()
