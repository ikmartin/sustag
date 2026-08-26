import pandas as pd
import pytest

from src.build3.acquire import reconcile


def _manifest(rows):
    return pd.DataFrame(rows, columns=["relpath", "size", "mtime_ns", "fetched_at"])


@pytest.fixture
def snapshots(monkeypatch):
    now = pd.Timestamp.now()
    recent = (now - pd.Timedelta(days=10)).isoformat()
    old = (now - pd.Timedelta(days=400)).isoformat()
    parent = _manifest([
        ("acquired/water/native/A.parquet", 100, 1, old),
        ("acquired/water/native/B.parquet", 100, 1, old),
        ("acquired/water/native/C.parquet", 100, 1, old),
        ("acquired/weather/gridmet_2026Q2.parquet", 500, 1, recent),
        ("raw/water/sites.csv", 50, 1, old),
    ])
    child = _manifest([
        ("acquired/water/native/A.parquet", 100, 1, old),        # unchanged
        ("acquired/water/native/B.parquet", 120, 2, recent),     # grew recently -> extended
        ("acquired/water/native/D.parquet", 90, 2, recent),      # new -> added
        ("acquired/weather/gridmet_2026Q2.parquet", 480, 2, recent),  # changed recently -> revised_in_window
        ("raw/water/sites.csv", 55, 2, old),                # changed long-stable -> conflict
    ])                                                            # C removed -> conflict
    stores = {"p": parent, "c": child}
    monkeypatch.setattr(reconcile.snapshot, "load", lambda sid: stores[sid])
    monkeypatch.setattr(reconcile.snapshot, "header", lambda sid: {"notes": ""})
    return stores


def test_drift_classes(snapshots):
    d = reconcile.diff_snapshots("p", "c").set_index("relpath").drift
    assert d["acquired/water/native/D.parquet"] == "added"
    assert d["acquired/water/native/B.parquet"] == "extended"
    assert d["acquired/weather/gridmet_2026Q2.parquet"] == "revised_in_window"
    assert d["raw/water/sites.csv"] == "revised_out_of_window"     # approved data changed: conflict
    assert d["acquired/water/native/C.parquet"] == "removed"            # conflict


def test_conflicts_gate_via_drift_ledger(snapshots, decisions_sandbox):
    d = reconcile.diff_snapshots("p", "c")
    with pytest.raises(SystemExit) as e:
        reconcile.gate_drift(d)
    msg = str(e.value)
    assert "sites.csv" in msg and "drift.csv" in msg


def test_adoption_amnesty(snapshots, monkeypatch):
    monkeypatch.setattr(reconcile.snapshot, "header",
                        lambda sid: {"notes": "adoption of raw/ in place"})
    d = reconcile.diff_snapshots("p", "c").set_index("relpath").drift
    # every change against the adoption snapshot is amnestied -- unknown provenance cannot be conflict
    assert (d.drop("acquired/water/native/D.parquet") == "adoption_amnesty").all()
    assert d["acquired/water/native/D.parquet"] == "added"
