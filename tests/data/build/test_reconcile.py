import pandas as pd
import pytest

from data.build.acquire import reconcile


def _manifest(rows):
    cols = ["relpath", "role", "size", "mtime_ns", "sha256", "schema_fp", "data_max_ts", "fetched_at"]
    return pd.DataFrame(rows, columns=cols)


NOW = pd.Timestamp("2026-08-25T12:00:00")
RECENT_TS = (NOW - pd.Timedelta(days=10)).isoformat()      # data newer than the revision window
OLD_TS = (NOW - pd.Timedelta(days=400)).isoformat()        # data long approved


@pytest.fixture
def snapshots(monkeypatch):
    parent = _manifest([
        ("acquired/water/native/A.parquet", "acquired", 100, 1, "aa", "s1", RECENT_TS, "x"),   # unchanged
        ("acquired/water/native/B.parquet", "acquired", 100, 1, "bb", "s1", RECENT_TS, "x"),   # grows
        ("acquired/water/native/C.parquet", "acquired", 100, 1, "cc", "s1", OLD_TS, "x"),      # removed
        ("acquired/water/native/E.parquet", "acquired", 100, 1, "ee", "s1", RECENT_TS, "x"),   # tail revision
        ("acquired/water/native/F.parquet", "acquired", 100, 1, "ff", "s1", OLD_TS, "x"),      # approved data changes
        ("acquired/water/native/G.parquet", "acquired", 100, 1, "gg", "s1", RECENT_TS, "x"),   # schema moves
        ("raw/water/sites.csv", "raw", 50, 1, "rr", None, None, "x"),                          # raw changes
    ])
    child = _manifest([
        ("acquired/water/native/A.parquet", "acquired", 100, 1, "aa", "s1", RECENT_TS, "x"),
        ("acquired/water/native/B.parquet", "acquired", 120, 2, "b2", "s1",
         NOW.isoformat(), "x"),                                                                # grew + ts advanced -> extended
        ("acquired/water/native/D.parquet", "acquired", 90, 2, "dd", "s1", RECENT_TS, "x"),    # added
        ("acquired/water/native/E.parquet", "acquired", 100, 2, "e2", "s1",
         NOW.isoformat(), "x"),                                                                # changed, old tail in-window
        ("acquired/water/native/F.parquet", "acquired", 100, 2, "f2", "s1", OLD_TS, "x"),      # changed, old data approved
        ("acquired/water/native/G.parquet", "acquired", 100, 2, "g2", "s2", RECENT_TS, "x"),   # schema_changed
        ("raw/water/sites.csv", "raw", 55, 2, "r2", None, None, "x"),                          # raw -> conflict
    ])
    stores = {"p": parent, "c": child}
    monkeypatch.setattr(reconcile.snapshot, "load", lambda sid: stores[sid])
    monkeypatch.setattr(reconcile.snapshot, "header",
                        lambda sid: {"notes": "", "created_at": NOW.isoformat()})
    return stores


def test_drift_classes(snapshots):
    d = reconcile.diff_snapshots("p", "c").set_index("relpath").drift
    assert d["acquired/water/native/D.parquet"] == "added"
    assert d["acquired/water/native/B.parquet"] == "extended"
    assert d["acquired/water/native/E.parquet"] == "revised_in_window"
    assert d["acquired/water/native/F.parquet"] == "revised_out_of_window"   # approved data changed
    assert d["acquired/water/native/G.parquet"] == "schema_changed"
    assert d["acquired/water/native/C.parquet"] == "removed"
    assert d["raw/water/sites.csv"] == "revised_out_of_window"               # the build never writes raw/
    assert "acquired/water/native/A.parquet" not in d.index                  # unchanged: no row


def test_mtime_alone_is_not_a_change(monkeypatch):
    # An atomic-rename rewrite refreshes mtime with identical content; the classifier must stay silent.
    a = _manifest([("acquired/x.parquet", "acquired", 100, 1, "aa", "s1", RECENT_TS, "x")])
    b = _manifest([("acquired/x.parquet", "acquired", 100, 999, "aa", "s1", RECENT_TS, "x")])
    monkeypatch.setattr(reconcile.snapshot, "load", lambda sid: {"p": a, "c": b}[sid])
    monkeypatch.setattr(reconcile.snapshot, "header",
                        lambda sid: {"notes": "", "created_at": NOW.isoformat()})
    assert len(reconcile.diff_snapshots("p", "c")) == 0


def test_conflicts_gate_via_drift_ledger(snapshots, decisions_sandbox):
    d = reconcile.diff_snapshots("p", "c")
    with pytest.raises(SystemExit) as e:
        reconcile.gate_drift(d)
    msg = str(e.value)
    assert "sites.csv" in msg and "drift.csv" in msg


def test_adoption_amnesty_reads_the_child(snapshots, monkeypatch):
    # The snapshot that INTRODUCES a wholesale replacement declares it -- amnesty is the child's notes.
    monkeypatch.setattr(reconcile.snapshot, "header",
                        lambda sid: {"notes": "adoption: vendor re-export" if sid == "c" else "",
                                     "created_at": NOW.isoformat()})
    d = reconcile.diff_snapshots("p", "c").set_index("relpath").drift
    assert (d.drop("acquired/water/native/D.parquet") == "adoption_amnesty").all()
    assert d["acquired/water/native/D.parquet"] == "added"
