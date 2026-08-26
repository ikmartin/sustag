import pandas as pd
import pytest

from data.build import ledger as L


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
    led = L.Ledger("t_adv", ("members",), ("ok",), gates=False)
    led.sync(pd.DataFrame({"members": ["X+Y"]}))
    led.gate(pd.DataFrame({"members": ["X+Y"]}))    # no raise


# ---- decide(): the review panel's write path ------------------------------------------------------

def test_decide_writes_one_row_and_stamps_criterion(decisions_sandbox):
    led = L.Ledger("t_m2", ("uid_a", "uid_b"), ("merge", "distinct"), criterion="v1")
    led.sync(_cand())
    led.decide(("A", "B"), "merge", decided_by="isaac", note="same structure")
    d = pd.read_csv(led.path, dtype=str).fillna("")
    row = d[(d.uid_a == "A")].iloc[0]
    assert (row["decision"], row["decided_by"], row["note"]) == ("merge", "isaac", "same structure")
    assert row[L.CRITERION_COL] == "v1"
    other = d[(d.uid_a == "C")].iloc[0]
    assert other["decision"] == "" and other[L.CRITERION_COL] == ""
    assert led.decisions() == {("A", "B"): "merge"}


def test_decide_refusals(led):
    led.sync(_cand())
    with pytest.raises(ValueError, match="maybe"):
        led.decide(("A", "B"), "maybe")
    with pytest.raises(ValueError, match="no editable"):
        led.decide(("A", "B"), "merge", exclude_spans="2021-01-01..2021-02-01")
    with pytest.raises(KeyError):
        led.decide(("A", "ZZZ"), "merge")


def test_decide_extras_and_undo(decisions_sandbox):
    led = L.Ledger("t_q", ("members", "channel"), ("keep", "exclude"), criterion="q1",
                   extra_editable=("exclude_spans", "max_threshold"))
    led.sync(pd.DataFrame({"members": ["X+Y"], "channel": ["nitrate"]}))
    led.decide(("X+Y", "nitrate"), "keep", exclude_spans="2021-03-01..2021-07-15", max_threshold="44.0")
    d = pd.read_csv(led.path, dtype=str).fillna("")
    assert d.iloc[0]["exclude_spans"] == "2021-03-01..2021-07-15"
    # undo: blank decision restores not-reviewed and clears the criterion stamp
    led.decide(("X+Y", "nitrate"), "")
    d = pd.read_csv(led.path, dtype=str).fillna("")
    assert d.iloc[0]["decision"] == "" and d.iloc[0][L.CRITERION_COL] == ""
    assert led.pending(pd.DataFrame({"members": ["X+Y"], "channel": ["nitrate"]})) == [("X+Y", "nitrate")]


def test_criterion_change_requeues_and_names_prior_verdict(decisions_sandbox):
    v1 = L.Ledger("t_c", ("uid_a", "uid_b"), ("merge", "distinct"), criterion="v1")
    v1.sync(_cand())
    v1.decide(("A", "B"), "merge", decided_by="isaac")
    assert v1.decisions() == {("A", "B"): "merge"}
    # the criterion changes: same sheet, new version -- the old decision loses authority but not visibility
    v2 = L.Ledger("t_c", ("uid_a", "uid_b"), ("merge", "distinct"), criterion="v2")
    assert v2.decisions() == {}
    assert ("A", "B") in v2.pending(_cand())
    stale = v2.stale_decisions()
    assert list(stale.decision) == ["merge"]
    with pytest.raises(SystemExit) as e:
        v2.gate(_cand())
    assert "was 'merge' under criterion v1" in str(e.value)
    # reconfirming through decide() restores authority under the new version
    v2.decide(("A", "B"), "merge", decided_by="isaac")
    assert v2.decisions() == {("A", "B"): "merge"}


def test_sync_stamps_hand_edited_rows(decisions_sandbox):
    led = L.Ledger("t_h", ("uid_a", "uid_b"), ("merge", "distinct"), criterion="v1")
    led.sync(_cand())
    d = pd.read_csv(led.path, dtype=str)
    d.loc[0, "decision"] = "distinct"               # a hand edit carries no version stamp
    d.to_csv(led.path, index=False)
    led.sync(_cand())                               # the next sync stamps it with the current criterion
    d = pd.read_csv(led.path, dtype=str).fillna("")
    assert d.iloc[0][L.CRITERION_COL] == "v1"
    assert led.decisions() == {("A", "B"): "distinct"}


def test_ledger_registry_is_complete():
    names = {led.name for led in L.ALL}
    assert names == {"drift", "registry_gaps", "merge", "site_type", "exclusions", "composed", "quality"}


def test_add_row_opens_and_decides(decisions_sandbox):
    """add_row creates the sheet + blank row for a human-initiated ledger; decide then writes the verdict; duplicate keys refuse."""
    import pytest

    from data.build import ledger

    led = ledger.EXCLUSIONS
    led.add_row(("IWQIS:WQS9999",))
    d = led.read_all()
    assert list(d.uid) == ["IWQIS:WQS9999"] and d.decision.iloc[0] == ""
    led.decide(("IWQIS:WQS9999",), "exclude", decided_by="test", note="scratch")
    assert led.decisions()[("IWQIS:WQS9999",)] == "exclude"
    with pytest.raises(ValueError):
        led.add_row(("IWQIS:WQS9999",))


def test_decide_all_sets_and_clears_every_row(decisions_sandbox):
    """The bulk path writes every row in one pass, stamps the criterion, and clears back to not-reviewed."""
    import pandas as pd

    from data.build import ledger

    led = ledger.QUALITY
    led.sync(pd.DataFrame({"members": ["A", "B", "C"], "channel": ["nitrate"] * 3,
                           "reason": ["noisy"] * 3}))
    led.decide(("A", "nitrate"), "keep", exclude_spans="2023-01-01..2023-01-05", max_threshold="42")

    assert led.decide_all("keep", decided_by="bulk") == 3
    d = led.read_all()
    assert set(d.decision) == {"keep"} and set(d.decided_by) == {"bulk"}
    assert set(d[ledger.CRITERION_COL]) == {led.criterion}
    row_a = d[d.members == "A"].iloc[0]
    assert row_a.exclude_spans == "2023-01-01..2023-01-05"   # a bulk VERDICT keeps hand-entered extras

    assert led.decide_all("") == 3                    # the undo
    d = led.read_all()
    assert set(d.decision) == {""} and set(d[ledger.CRITERION_COL]) == {""}
    assert set(d.exclude_spans) == {""} and set(d.max_threshold) == {""}   # the reset wipes them
