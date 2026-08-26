"""D3 site minting: winner-per-span merge, authority ranking, and the two halts (reach disagreement, id collision)."""

import numpy as np
import pandas as pd
import pytest

from src.build3 import contracts, registry
from src.build3.derive import identity, sites


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
        rec = sites.merge_record(["IWQIS:A", "USGS:B"], pd.Series({"IWQIS:A": 20, "USGS:B": 10}))
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
        rec = sites.merge_record(["IWQIS:A", "USGS:B"], pd.Series({"IWQIS:A": 10, "USGS:B": 10})).set_index("date")
        assert (rec.nitrate_src.dropna() == "IWQIS:A").all()
        assert (rec.loc[rec.discharge_mean.notna(), "discharge_src"] == "USGS:B").iloc[2:].all()


class TestAuthority:
    def test_declared_colloc_beats_longest_record(self):
        uid, rank = sites._authority(["IWQIS:A", "USGS:B"], "IWQIS:A", ({"IWQIS:A": "USGS:B"}, {}))
        assert (uid, rank) == ("USGS:B", "declared_colloc")

    def test_measured_colocation_anchors_when_nothing_is_declared(self):
        """The rule survives a source retiring its declaration column: measurement answers the same question."""
        uid, rank = sites._authority(["IWQIS:A", "USGS:B"], "IWQIS:A", ({}, {"IWQIS:A": "USGS:B"}))
        assert (uid, rank) == ("USGS:B", "measured_colloc")

    def test_declaration_is_preferred_over_measurement(self):
        uid, rank = sites._authority(["IWQIS:A", "USGS:B"], "IWQIS:A",
                                     ({"IWQIS:A": "USGS:B"}, {"IWQIS:A": "USGS:B"}))
        assert rank == "declared_colloc"

    def test_anchor_outside_bundle_is_ignored(self):
        uid, rank = sites._authority(["IWQIS:A", "MPCA:C"], "MPCA:C", ({"IWQIS:A": "USGS:B"}, {}))
        assert (uid, rank) == ("MPCA:C", "longest_record")


def _sensors(rows):
    base = dict(source="IWQIS", station_name="x", source_drainage_area_km2=10.0,
                lat=42.0, lon=-93.0, site_type="stream", site_type_source="usgs_site_type",
                site_type_conflict=pd.NA, n_days_nitrate=10, n_days_discharge=0)
    return pd.DataFrame([{**base, **r} for r in rows])


def _snap_stub(table):
    def fake(frame, fl, cat, vaa):
        out = pd.DataFrame([dict(site_uid=u, **table[u]) for u in frame.site_uid])
        return out
    return fake


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(sites, "has_record", lambda u: True)
    monkeypatch.setattr(sites, "_colocation_anchors", lambda s: ({}, {}))
    monkeypatch.setattr(identity, "_canonical",
                        lambda u: _canon(pd.date_range("2024-01-01", periods=10)))


class TestBuildHalts:
    def test_reach_disagreement_halts_naming_bundle(self, offline, monkeypatch):
        s = _sensors([dict(site_uid="IWQIS:A", bundle="IWQIS:A+USGS:B", comid=111),
                      dict(site_uid="USGS:B", bundle="IWQIS:A+USGS:B", comid=222)])
        with pytest.raises(SystemExit) as e:
            sites.build(s, None, None, None)
        assert "IWQIS:A+USGS:B" in str(e.value) and "111" in str(e.value) and "222" in str(e.value)

    def test_id_collision_halts_naming_both_bundles(self, offline, monkeypatch):
        s = _sensors([dict(site_uid="IWQIS:A", bundle="IWQIS:A", comid=111),
                      dict(site_uid="USGS:B", bundle="USGS:B", comid=111)])
        snap = _snap_stub({u: dict(comid=111, snap_pos_m=42.4, snap_status="ok", snap_dist_m=1.0,
                                   snap_rule="nearest") for u in ("IWQIS:A", "USGS:B")})
        monkeypatch.setattr(sites.snap_mod, "snap", snap)
        with pytest.raises(SystemExit) as e:
            sites.build(s, None, None, None)
        assert "S111-00042" in str(e.value) and "IWQIS:A" in str(e.value) and "USGS:B" in str(e.value)


class TestBuildMinting:
    def test_mints_flow_ordered_ids_and_off_namespace(self, offline, monkeypatch):
        s = _sensors([dict(site_uid="IWQIS:A", bundle="IWQIS:A", comid=111),
                      dict(site_uid="USGS:B", bundle="USGS:B", comid=111),
                      dict(site_uid="MPCA:W", bundle="MPCA:W", comid=222, site_type="well")])
        snap = _snap_stub({"IWQIS:A": dict(comid=111, snap_pos_m=42.0, snap_status="ok", snap_dist_m=1.0, snap_rule="nearest"),
                           "USGS:B": dict(comid=111, snap_pos_m=980.0, snap_status="ok", snap_dist_m=2.0, snap_rule="nearest"),
                           "MPCA:W": dict(comid=222, snap_pos_m=5.0, snap_status="ok", snap_dist_m=3.0, snap_rule="nearest")})
        monkeypatch.setattr(sites.snap_mod, "snap", snap)
        tbl, recs = sites.build(s, None, None, None)
        t = tbl.set_index("bundle")
        assert t.loc["IWQIS:A", "site_id"] == "S111-00042"
        assert t.loc["USGS:B", "site_id"] == "S111-00980"
        # zero-padded positions sort into flow order lexically within the reach
        ids = sorted([t.loc["IWQIS:A", "site_id"], t.loc["USGS:B", "site_id"]])
        assert ids == ["S111-00042", "S111-00980"]
        # a well is a site without a surface node: OFF namespace, on_network False
        assert t.loc["MPCA:W", "site_id"] == "OFF-MPCA:W"
        assert not t.loc["MPCA:W", "on_network"]
        assert set(recs) == set(t.site_id)
        assert t.loc["IWQIS:A", "n_days_nitrate"] == 10

    def test_recordless_bundle_is_never_a_site(self, offline, monkeypatch):
        monkeypatch.setattr(sites, "has_record", lambda u: u != "IWQIS:E")
        s = _sensors([dict(site_uid="IWQIS:A", bundle="IWQIS:A", comid=111),
                      dict(site_uid="IWQIS:E", bundle="IWQIS:E", comid=333)])
        snap = _snap_stub({u: dict(comid=c, snap_pos_m=10.0, snap_status="ok", snap_dist_m=1.0, snap_rule="nearest")
                           for u, c in (("IWQIS:A", 111), ("IWQIS:E", 333))})
        monkeypatch.setattr(sites.snap_mod, "snap", snap)
        tbl, _ = sites.build(s, None, None, None)
        assert tbl.site_id.tolist() == ["S111-00010"], "the record-less registration stays registered, never a site"


class TestContradictionHalt:
    def _sensors3(self):
        return pd.DataFrame([
            dict(site_uid=u, source="IWQIS", n_days_nitrate=10, n_days_discharge=0)
            for u in ("IWQIS:A", "IWQIS:B", "IWQIS:C")])

    def _cand3(self):
        rows = [dict(uid_a=a, uid_b=b, comid=1, sep_m=10.0, proposed="sequential",
                     channels_a="nitrate", channels_b="nitrate")
                for a, b in (("IWQIS:A", "IWQIS:B"), ("IWQIS:B", "IWQIS:C"), ("IWQIS:A", "IWQIS:C"))]
        return pd.DataFrame(rows)

    def test_human_distinct_inside_a_fused_component_halts(self):
        """A-B merge + B-C merge fuse A and C; a HUMAN distinct on A-C is a contradiction, not an override."""
        dec = pd.DataFrame([dict(uid_a="IWQIS:A", uid_b="IWQIS:B", decision="merge"),
                            dict(uid_a="IWQIS:B", uid_b="IWQIS:C", decision="merge"),
                            dict(uid_a="IWQIS:A", uid_b="IWQIS:C", decision="distinct")])
        with pytest.raises(SystemExit) as e:
            identity.apply_decisions(self._sensors3(), self._cand3(), dec)
        msg = str(e.value)
        assert "contradict" in msg and "IWQIS:A : IWQIS:C" in msg

    def test_rule_distinct_fused_transitively_is_fine(self):
        """The rule's `distinct` means "no pairwise evidence" -- a chain of re-registrations legitimately fuses a pair the rule proposed apart."""
        cand = self._cand3()
        cand.loc[2, "proposed"] = "distinct"          # the rule's A-C proposal
        dec = pd.DataFrame([dict(uid_a="IWQIS:A", uid_b="IWQIS:B", decision="merge"),
                            dict(uid_a="IWQIS:B", uid_b="IWQIS:C", decision="merge")])
        out = identity.apply_decisions(self._sensors3(), cand, dec).set_index("site_uid")
        assert set(out.bundle) == {"IWQIS:A+IWQIS:B+IWQIS:C"}, "transitive fusion over a rule-distinct is legitimate"


class TestSiteReviewQueue:
    def _sensors(self, rows):
        base = dict(is_bundle_canonical=False, n_days_nitrate=10, channels_present="nitrate",
                    comid=1, bundle_rule="sequential", station_name="x")
        return pd.DataFrame([{**base, **r} for r in rows])

    def test_multi_member_bundles_queue_singletons_never(self, decisions_sandbox):
        s = self._sensors([
            dict(site_uid="IWQIS:A", bundle="IWQIS:A+USGS:B", is_bundle_canonical=True),
            dict(site_uid="USGS:B", bundle="IWQIS:A+USGS:B"),
            dict(site_uid="IWQIS:C", bundle="IWQIS:C", bundle_rule="singleton"),
        ])
        q = sites.site_review_queue(s)
        assert q.members.tolist() == ["IWQIS:A+USGS:B"]
        assert q.iloc[0].canonical_uid == "IWQIS:A" and q.iloc[0].n_members == 2

    def test_membership_change_requeues_by_key(self, decisions_sandbox):
        """A confirmed pair sinks; adding a member makes a NEW key that gates again."""
        from src.build3 import ledger

        pair = self._sensors([
            dict(site_uid="IWQIS:A", bundle="IWQIS:A+USGS:B", is_bundle_canonical=True),
            dict(site_uid="USGS:B", bundle="IWQIS:A+USGS:B"),
        ])
        q = sites.site_review_queue(pair)
        ledger.SITE_REVIEW.sync(q)
        sheet = pd.read_csv(ledger.SITE_REVIEW.path, dtype=str).fillna("")
        sheet["decision"] = "confirm"
        sheet.to_csv(ledger.SITE_REVIEW.path, index=False)
        ledger.SITE_REVIEW.gate(sites.site_review_queue(pair))          # confirmed: no halt

        trio = self._sensors([
            dict(site_uid="IWQIS:A", bundle="IWQIS:A+IWQIS:D+USGS:B", is_bundle_canonical=True),
            dict(site_uid="USGS:B", bundle="IWQIS:A+IWQIS:D+USGS:B"),
            dict(site_uid="IWQIS:D", bundle="IWQIS:A+IWQIS:D+USGS:B"),
        ])
        with pytest.raises(SystemExit, match="site_review"):
            q3 = sites.site_review_queue(trio)
            ledger.SITE_REVIEW.sync(q3)
            ledger.SITE_REVIEW.gate(q3)
