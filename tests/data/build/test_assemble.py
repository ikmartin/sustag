"""D4 assembly: winner-per-span merge, fronting ranks, the reach-disagreement halt, SNR-keyed records."""

import numpy as np
import pandas as pd
import pytest

from data.build import contracts, registry
from data.build.derive import assemble, identity


def _canon(days, ch="nitrate", offset=0.0):
    idx = pd.date_range(days[0], periods=len(days), freq="D") if isinstance(days[0], str) else days
    vals = np.linspace(5, 8, len(idx)) + offset
    f = pd.DataFrame({f"{ch}_mean": vals, f"{ch}_max": vals + 1, f"{ch}_min": vals - 1}, index=idx)
    f.index.name = "date"
    return f


class TestWinnerPerSpan:
    def test_leader_owns_overlap_others_fill_gaps(self, monkeypatch):
        a = _canon(pd.date_range("2024-01-01", periods=20))            # 20 days -- the leader
        b = _canon(pd.date_range("2024-01-15", periods=10), offset=3)  # overlaps 6, extends 4
        monkeypatch.setattr(identity, "_canonical", lambda u: {"IWQIS:A": a, "USGS:B": b}[u])
        rec = assemble.merge_record(["IWQIS:A", "USGS:B"], pd.Series({"IWQIS:A": 20, "USGS:B": 10}))
        r = rec.set_index("date")
        assert len(r) == 24
        # overlap days come ENTIRELY from the leader -- every statistic, no blending
        overlap = pd.Timestamp("2024-01-16")
        assert r.loc[overlap, "nitrate_src"] == "IWQIS:A"
        assert r.loc[overlap, "nitrate_mean"] == a.loc[overlap, "nitrate_mean"]
        assert r.loc[overlap, "nitrate_max"] == a.loc[overlap, "nitrate_max"]
        # the extension belongs to the filler, named per day
        ext = pd.Timestamp("2024-01-22")
        assert r.loc[ext, "nitrate_src"] == "USGS:B"
        assert r.loc[ext, "nitrate_mean"] == b.loc[ext, "nitrate_mean"]

    def test_per_channel_winners_are_independent(self, monkeypatch):
        idx = pd.date_range("2024-01-01", periods=10)
        a = pd.concat([_canon(idx), _canon(idx, ch="discharge").iloc[:2]], axis=1)
        b = pd.concat([_canon(idx, offset=1).iloc[:3], _canon(idx, ch="discharge", offset=5)], axis=1)
        monkeypatch.setattr(identity, "_canonical", lambda u: {"IWQIS:A": a, "USGS:B": b}[u])
        rec = assemble.merge_record(["IWQIS:A", "USGS:B"],
                                    pd.Series({"IWQIS:A": 10, "USGS:B": 10})).set_index("date")
        assert (rec.nitrate_src.dropna() == "IWQIS:A").all()
        assert (rec.loc[rec.discharge_mean.notna(), "discharge_src"] == "USGS:B").iloc[2:].all()

    def test_singleton_carries_its_own_provenance(self, monkeypatch):
        a = _canon(pd.date_range("2024-01-01", periods=5))
        monkeypatch.setattr(identity, "_canonical", lambda u: a)
        rec = assemble.merge_record(["IWQIS:A"], pd.Series({"IWQIS:A": 5})).set_index("date")
        assert (rec.nitrate_src == "IWQIS:A").all()


class TestFronting:
    def test_declared_colloc_beats_longest_record(self):
        uid, rank = assemble._front(["IWQIS:A", "USGS:B"], "IWQIS:A", ({"IWQIS:A": "USGS:B"}, {}))
        assert (uid, rank) == ("USGS:B", "declared_colloc")

    def test_measured_colocation_fronts_when_nothing_is_declared(self):
        uid, rank = assemble._front(["IWQIS:A", "USGS:B"], "IWQIS:A", ({}, {"IWQIS:A": "USGS:B"}))
        assert (uid, rank) == ("USGS:B", "measured_colloc")

    def test_declaration_is_preferred_over_measurement(self):
        _, rank = assemble._front(["IWQIS:A", "USGS:B"], "IWQIS:A",
                                  ({"IWQIS:A": "USGS:B"}, {"IWQIS:A": "USGS:B"}))
        assert rank == "declared_colloc"

    def test_anchor_outside_bundle_is_ignored(self):
        uid, rank = assemble._front(["IWQIS:A", "MPCA:C"], "MPCA:C", ({"IWQIS:A": "USGS:B"}, {}))
        assert (uid, rank) == ("MPCA:C", "longest_record")


def _sensors(rows):
    base = dict(source="IWQIS", station_name="x", lat=42.0, lon=-93.0, site_type="stream",
                n_days_nitrate=10, n_days_discharge=0)
    return pd.DataFrame([{**base, **r} for r in rows])


@pytest.fixture
def offline(monkeypatch, decisions_sandbox):
    monkeypatch.setattr(assemble, "has_record", lambda u: True)
    monkeypatch.setattr(assemble, "_colocation_anchors", lambda s: ({}, {}))
    monkeypatch.setattr(identity, "_canonical",
                        lambda u: _canon(pd.date_range("2024-01-01", periods=10)))
    from data.build import ids

    monkeypatch.setattr(ids, "mapping",
                        lambda: {"IWQIS:A": "SNR-0001", "USGS:B": "SNR-0002", "MPCA:W": "SNR-0003"})


class TestBuild:
    def test_reach_disagreement_halts_naming_bundle(self, offline):
        s = _sensors([dict(site_uid="IWQIS:A", bundle="IWQIS:A+USGS:B", comid=111),
                      dict(site_uid="USGS:B", bundle="IWQIS:A+USGS:B", comid=222)])
        with pytest.raises(SystemExit) as e:
            assemble.build(s)
        assert "IWQIS:A+USGS:B" in str(e.value) and "111" in str(e.value) and "222" in str(e.value)

    def test_merged_bundle_publishes_under_fronting_members_code(self, offline):
        s = _sensors([dict(site_uid="IWQIS:A", bundle="IWQIS:A+USGS:B", comid=111, n_days_nitrate=5),
                      dict(site_uid="USGS:B", bundle="IWQIS:A+USGS:B", comid=111, n_days_nitrate=50,
                           source="USGS")])
        records, block = assemble.build(s)
        # USGS:B has more high-tier days -> canonical -> fronts (no colloc anchors)
        assert set(records) == {"SNR-0002"}
        b = block.set_index("site_uid")
        assert b.loc["IWQIS:A", "publishes_under"] == "SNR-0002"
        assert b.loc["USGS:B", "publishes_under"] == "SNR-0002"
        assert bool(b.loc["USGS:B", "is_bundle_canonical"]) and not bool(b.loc["IWQIS:A", "is_bundle_canonical"])

    def test_singletons_publish_under_their_own_codes(self, offline):
        s = _sensors([dict(site_uid="IWQIS:A", bundle="IWQIS:A", comid=111),
                      dict(site_uid="MPCA:W", bundle="MPCA:W", comid=222, source="MPCA")])
        records, block = assemble.build(s)
        assert set(records) == {"SNR-0001", "SNR-0003"}
        assert set(block.publishes_under) == {"SNR-0001", "SNR-0003"}

    def test_recordless_bundles_assemble_nothing(self, offline, monkeypatch):
        monkeypatch.setattr(assemble, "has_record", lambda u: u != "IWQIS:A")
        s = _sensors([dict(site_uid="IWQIS:A", bundle="IWQIS:A", comid=111)])
        records, block = assemble.build(s)
        assert records == {} and len(block) == 0


def test_merged_mismatch_queue():
    """Members of a multi-member bundle with differing types queue as merged_mismatch; same-type bundles and singletons do not."""
    import pandas as pd

    from data.build.derive import site_type

    sites = pd.DataFrame({
        "site_uid": ["A", "B", "C", "D"],
        "site_type": ["stream", "lake", "stream", "stream"],
        "site_type_source": ["declared"] * 4,
        "site_type_conflict": [pd.NA] * 4,
        "comid": [pd.NA] * 4,
    })
    amb = site_type.ambiguous(sites, mismatch={"A", "B"})
    got = dict(zip(amb.site_uid, amb.ambiguity))
    assert got.get("A") == "merged_mismatch" and got.get("B") == "merged_mismatch"
    assert "C" not in got and "D" not in got
    q = site_type.question(next(r for r in amb.itertuples() if r.site_uid == "B"))
    assert "MERGED" in q and "lake" in q
