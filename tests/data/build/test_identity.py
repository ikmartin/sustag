"""D4 identity kernels: agreement respects the registry comparison spec; the bundle block's pending semantics."""

import numpy as np
import pandas as pd
import pytest

from data.build import registry
from data.build.derive import identity


def _pair(values_a, values_b, days=30):
    idx = pd.date_range("2024-03-01", periods=days)
    return pd.Series(values_a, index=idx), pd.Series(values_b, index=idx)


class TestAgreementScaleKind:
    def test_interval_channel_judged_on_agree_tol_not_ratio(self):
        """Regression for the build2 bug: two thermistors near freezing with a constant 0.3 degC offset. As a RATIO against a ~0.25 degC median that offset reads as a 120% error; against water_temp's agree_tol=1.0 it is agreement. The old code fell into the relative branch and called this pair apart."""
        rng = np.random.default_rng(7)
        base = 0.25 + np.abs(rng.normal(0, 0.05, 30)).cumsum() * 0.01
        sa, sb = _pair(base, base + 0.3)
        assert registry.CHANNELS["water_temp"].scale_kind == "interval"
        shared, r, mdiff, agrees = identity._agreement(sa, sb, "water_temp")
        assert shared == 30 and r > identity.DUAL_MIN_R
        assert mdiff == pytest.approx(-0.3, abs=1e-9)
        assert agrees, "interval channel must be judged on agree_tol, not a relative difference"

    def test_interval_channel_beyond_tol_disagrees(self):
        rng = np.random.default_rng(7)
        base = 10 + rng.normal(0, 0.2, 30).cumsum()
        sa, sb = _pair(base, base + 2.5)          # 2.5 degC apart: different water
        _, _, _, agrees = identity._agreement(sa, sb, "water_temp")
        assert not agrees

    def test_high_scrutiny_stays_absolute(self):
        rng = np.random.default_rng(3)
        base = 8 + rng.normal(0, 0.5, 30).cumsum() * 0.1
        sa, sb = _pair(base, base + 0.1)          # within 0.25 mg/L
        *_, agrees = identity._agreement(sa, sb, "nitrate")
        assert agrees
        sa, sb = _pair(base, base + 0.4)          # beyond it, even though relatively tiny at 8 mg/L
        *_, agrees = identity._agreement(sa, sb, "nitrate")
        assert not agrees

    def test_ratio_channel_stays_relative(self):
        rng = np.random.default_rng(5)
        base = 100 + rng.normal(0, 2, 30).cumsum()
        sa, sb = _pair(base, base * 1.02)         # 2% apart on discharge: same water
        *_, agrees = identity._agreement(sa, sb, "discharge")
        assert agrees
        sa, sb = _pair(base, base * 1.5)
        *_, agrees = identity._agreement(sa, sb, "discharge")
        assert not agrees


def _sensors(uids, source="IWQIS"):
    return pd.DataFrame({
        "site_uid": uids, "source": source,
        "n_days_nitrate": [100 + 10 * i for i in range(len(uids))],
        "n_days_discharge": 0,
    })


def _cand(ua, ub, proposed, channels="nitrate", cross_type=False):
    return pd.DataFrame([dict(uid_a=ua, uid_b=ub, comid_a=1, comid_b=1, same_comid=True,
                              sep_m=10.0, min_sep_m=8.0, max_sep_m=12.0, hesitate=False,
                              type_a="stream", type_b="lake" if cross_type else "stream",
                              cross_type=cross_type, proposed=proposed,
                              channels_a=channels, channels_b=channels)])


class TestBundleBlock:
    def test_pending_pair_has_null_bundle(self):
        """Both halves of an undecided high-scrutiny pair carry NULL, never two optimistic singletons."""
        cand = _cand("IWQIS:A", "IWQIS:B", "dual_registration")
        dec = pd.DataFrame(columns=["uid_a", "uid_b", "decision"])
        out = identity.apply_decisions(_sensors(["IWQIS:A", "IWQIS:B", "IWQIS:C"]), cand, dec).set_index("site_uid")
        for u in ("IWQIS:A", "IWQIS:B"):
            assert pd.isna(out.loc[u, "bundle"]) and pd.isna(out.loc[u, "is_bundle_canonical"])
            assert out.loc[u, "bundle_decided_by"] == "pending"
        assert out.loc["IWQIS:C", "bundle"] == "IWQIS:C"
        assert out.loc["IWQIS:C", "bundle_rule"] == "singleton"

    def test_human_merge_forms_sorted_member_bundle(self):
        cand = _cand("IWQIS:B", "IWQIS:A", "dual_registration")
        dec = pd.DataFrame([dict(uid_a="IWQIS:A", uid_b="IWQIS:B", decision="merge")])
        out = identity.apply_decisions(_sensors(["IWQIS:A", "IWQIS:B"]), cand, dec).set_index("site_uid")
        assert set(out.bundle) == {"IWQIS:A+IWQIS:B"}, "the ledger key is the sorted member set"
        assert out.loc["IWQIS:B", "is_bundle_canonical"] == True          # noqa: E712 -- more nitrate days
        assert out.loc["IWQIS:A", "is_bundle_canonical"] == False         # noqa: E712
        assert set(out.bundle_decided_by) == {"human"}

    def test_covariate_only_pair_settles_by_rule(self):
        """No high-scrutiny channel on either side: the proposal applies unasked."""
        cand = _cand("IWQIS:A", "IWQIS:B", "complementary", channels="discharge+water_temp")
        dec = pd.DataFrame(columns=["uid_a", "uid_b", "decision"])
        out = identity.apply_decisions(_sensors(["IWQIS:A", "IWQIS:B"]), cand, dec).set_index("site_uid")
        assert set(out.bundle) == {"IWQIS:A+IWQIS:B"}
        assert set(out.bundle_decided_by) == {"rule"}
        assert set(out.bundle_rule) == {"complementary"}

    def test_human_distinct_outranks_rule(self):
        cand = _cand("IWQIS:A", "IWQIS:B", "complementary", channels="discharge")
        dec = pd.DataFrame([dict(uid_a="IWQIS:A", uid_b="IWQIS:B", decision="distinct")])
        out = identity.apply_decisions(_sensors(["IWQIS:A", "IWQIS:B"]), cand, dec).set_index("site_uid")
        assert set(out.bundle) == {"IWQIS:A", "IWQIS:B"}
        assert set(out.bundle_rule) == {"distinct"}
        assert set(out.bundle_decided_by) == {"human"}


class TestHighTierDays:
    def test_tier_days_dominate_total(self):
        s = pd.DataFrame({"site_uid": ["IWQIS:A", "IWQIS:B"], "source": "IWQIS",
                          "n_days_nitrate": [10, 5], "n_days_discharge": [0, 5000]})
        d = identity.high_tier_days(s)
        assert d["IWQIS:A"] > d["IWQIS:B"], "a decade of discharge never outranks a day of nitrate"

    def test_total_days_break_covariate_only_ties(self):
        s = pd.DataFrame({"site_uid": ["IWQIS:A", "IWQIS:B"], "source": "IWQIS",
                          "n_days_nitrate": [0, 0], "n_days_discharge": [200, 900]})
        d = identity.high_tier_days(s)
        assert d["IWQIS:B"] > d["IWQIS:A"]


class TestGateRoutes:
    def test_cross_type_merge_proposal_blocks_whatever_the_tier(self):
        """lake x stream is never auto-merged: the label is noisy, a human sees the mismatch."""
        cand = _cand("IWQIS:A", "IWQIS:B", "complementary", channels="discharge", cross_type=True)
        assert identity.blocks(cand).tolist() == [True]
        # same-type covariate-only: the rule decides
        cand = _cand("IWQIS:A", "IWQIS:B", "complementary", channels="discharge")
        assert identity.blocks(cand).tolist() == [False]

    def test_high_tier_blocks_on_either_side(self):
        cand = _cand("IWQIS:A", "IWQIS:B", "dual_registration", channels="nitrate")
        assert identity.blocks(cand).tolist() == [True]

    def test_distinct_never_blocks(self):
        cand = _cand("IWQIS:A", "IWQIS:B", "distinct", channels="nitrate", cross_type=True)
        assert identity.blocks(cand).tolist() == [False]


class TestCandidateGate:
    def _prox(self, rows, monkeypatch, tmp_path):
        import data.build.config as config
        from data.build import io as bio

        p = tmp_path / "proximity.parquet"
        pd.DataFrame(rows, columns=["uid_a", "uid_b", "sep_m", "min_sep_m", "max_sep_m",
                                    "comid_a", "comid_b", "same_comid"]).to_parquet(p)
        monkeypatch.setattr(config, "PROXIMITY_PATH", p)
        monkeypatch.setattr(config, "WATER_CANONICAL", tmp_path / "canon")  # nothing has a record
        (tmp_path / "canon").mkdir()

    def _sensors(self, types):
        return pd.DataFrame({"site_uid": list(types), "source": "IWQIS",
                             "station_name": "x", "site_type": list(types.values()),
                             "comid_agree": True})

    def test_material_boundary_auto_rejects_and_unknown_waits(self, monkeypatch, tmp_path):
        self._prox([("IWQIS:S", "IWQIS:W", 5.0, 0.0, 10.0, 1, 1, True),
                    ("IWQIS:S", "IWQIS:U", 5.0, 0.0, 10.0, 1, 1, True)],
                   monkeypatch, tmp_path)
        s = self._sensors({"IWQIS:S": "stream", "IWQIS:W": "well", "IWQIS:U": "unknown"})
        cand, auto = identity.candidates(s)
        assert len(cand) == 0
        assert list(auto.settled_by.str.startswith("cross_material")) == [True]   # the well pair
        # the unknown pair is in NEITHER frame: it waits
        assert not ((auto.uid_a == "IWQIS:S") & (auto.uid_b == "IWQIS:U")).any()

    def test_recordless_pairs_write_nothing(self, monkeypatch, tmp_path):
        self._prox([("IWQIS:A", "IWQIS:B", 5.0, 0.0, 10.0, 1, 1, True)], monkeypatch, tmp_path)
        s = self._sensors({"IWQIS:A": "stream", "IWQIS:B": "stream"})
        cand, auto = identity.candidates(s)
        assert len(cand) == 0 and len(auto) == 0     # a filter, not a verdict

    def test_beyond_gate_auto_rejects_with_reason(self, monkeypatch, tmp_path):
        self._prox([("IWQIS:A", "IWQIS:B", 500.0, 400.0, 600.0, 1, 2, False)], monkeypatch, tmp_path)
        # give both a canonical record so only the proximity condition fails
        import data.build.config as config
        from data.build import io as bio

        for u in ("IWQIS__A", "IWQIS__B"):
            from data.build import registry as R
            bio.write_parquet(pd.DataFrame({"date": ["2021-01-01"], "nitrate_mean": [1.0]}),
                              config.WATER_CANONICAL / f"{u}.parquet",
                              stamps={"registry": R.fingerprint()})
        s = self._sensors({"IWQIS:A": "stream", "IWQIS:B": "stream"})
        cand, auto = identity.candidates(s)
        assert len(cand) == 0
        assert auto.iloc[0].settled_by.startswith("beyond_gate")
