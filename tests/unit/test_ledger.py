import pandas as pd
import pytest

from src.build3 import ledger as L


@pytest.fixture
def led(decisions_sandbox):
    return L.Ledger("t_merge", ("uid_a", "uid_b"), ("merge", "distinct"))


def _cand():
    return pd.DataFrame({"uid_a": ["A", "C"], "uid_b": ["B", "D"], "sep_m": [8.1, 400.0]})


def test_sync_adds_blank_never_seeds(led):
    led.sync(_cand())
    d = pd.read_csv(led.path, dtype=str).fillna("")
    assert list(d["decision"]) == ["", ""]          # a pre-filled verdict is one a reviewer scrolls past


def test_blank_is_not_reviewed_and_unknown_raises(led):
    led.sync(_cand())
    assert led.pending(_cand()) == [("A", "B"), ("C", "D")]
    d = pd.read_csv(led.path, dtype=str)
    d.loc[0, "decision"] = "maybe"
    d.to_csv(led.path, index=False)
    with pytest.raises(ValueError, match="maybe"):
        led.load()


def test_decisions_survive_resync_on_keys(led):
    led.sync(_cand())
    d = pd.read_csv(led.path, dtype=str)
    d.loc[0, "decision"] = "merge"
    d.to_csv(led.path, index=False)
    # resync in a different order: the decision follows the KEY, not the row number
    led.sync(_cand().iloc[[1, 0]].reset_index(drop=True))
    got = led.decisions()
    assert got[("A", "B")] == "merge"


def test_retired_decided_kept_retired_blank_dropped(led):
    led.sync(_cand())
    d = pd.read_csv(led.path, dtype=str)
    d.loc[0, "decision"] = "merge"
    d.to_csv(led.path, index=False)
    a, k, r = led.sync(_cand().iloc[[1]])           # A:B retired (decided), C:D stays (blank)
    assert (a, k, r) == (0, 1, 1)
    d = pd.read_csv(led.path, dtype=str).fillna("")
    retired = d[d["Row"] == ""]
    assert list(retired["uid_a"]) == ["A"] and list(retired["decision"]) == ["merge"]


def test_gate_names_rows_and_worth(led):
    led.sync(_cand())
    with pytest.raises(SystemExit) as e:
        led.gate(_cand(), worth={("C", "D"): "holds 747 nitrate days"})
    msg = str(e.value)
    assert "A : B" in msg and "747 nitrate days" in msg and "t_merge.csv" in msg


def test_non_gating_ledger_never_halts(decisions_sandbox):
    led = L.Ledger("t_basin", ("members",), ("basin1",), gates=False)
    led.sync(pd.DataFrame({"members": ["X+Y"]}))
    led.gate(pd.DataFrame({"members": ["X+Y"]}))    # no raise
