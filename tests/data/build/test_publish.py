"""P1 kernels: the partition invariant, detector semantics, and the classification strings."""

import numpy as np
import pandas as pd
import pytest

from data.build.publish import publish, quality


def _sensors(rows):
    base = dict(bundle=pd.NA, excluded_reason=pd.NA, bundle_decided_by="n/a", publishes_under=pd.NA)
    return pd.DataFrame([{**base, **r} for r in rows])


class TestPartition:
    def test_every_class_and_exactness(self, monkeypatch):
        monkeypatch.setattr(publish, "has_record", lambda u: u not in ("IWQIS:R",))
        sensors = _sensors([
            dict(site_uid="IWQIS:A", bundle="IWQIS:A", publishes_under="SNR-0001"),  # published
            dict(site_uid="IWQIS:X", bundle=pd.NA, excluded_reason="test logger"),   # excluded
            dict(site_uid="IWQIS:R", bundle="IWQIS:R"),                              # record-less
            dict(site_uid="IWQIS:P", bundle=pd.NA, bundle_decided_by="pending"),     # held
        ])
        cls = publish.partition(sensors, published_codes={"SNR-0001"})
        assert cls.to_dict() == {"IWQIS:A": "published", "IWQIS:X": "excluded",
                                 "IWQIS:R": "recordless", "IWQIS:P": "held"}

    def test_unclassifiable_registration_halts(self, monkeypatch):
        """A recorded, bundled sensor whose record was never assembled fits no class -- the build must say so, not shrug."""
        monkeypatch.setattr(publish, "has_record", lambda u: True)
        sensors = _sensors([dict(site_uid="IWQIS:Z", bundle="IWQIS:Z")])
        with pytest.raises(SystemExit, match="partition violated"):
            publish.partition(sensors, published_codes=set())


class TestDetector:
    def test_absolute_signals_fire_on_a_cohort_of_one(self):
        """Cohort-relative z-scores are 0 on a single row; the absolute signals must still catch a broken series."""
        F = pd.DataFrame([dict(noise=0.5, mean=5, std=1, flatline_frac=0, seg_noise=np.nan,
                               seg_flat=np.nan, seasonal_strength=0.5, n_impossible=200,
                               n_raw_samples=10_000, ac1=0.5,
                               impossible_run_days=3.0, lifespan_yr=5.0)], index=["S1"])
        sig = quality.detect(F)
        r = sig.iloc[0]
        assert r.range_violation and r.dead_autocorr and r.extreme_noise and r.stuck_impossible
        assert not r.noisy and not r.scale_outlier      # cohort-relative: nothing to compare against
        assert r.flagged

    def test_clean_series_not_flagged(self):
        F = pd.DataFrame([dict(noise=0.05, mean=5, std=1, flatline_frac=0, seg_noise=1.0,
                               seg_flat=0.01, seasonal_strength=0.5, n_impossible=0,
                               n_raw_samples=10_000, ac1=0.99,
                               impossible_run_days=0.0, lifespan_yr=5.0)] * 4,
                         index=["a", "b", "c", "d"])
        assert not quality.detect(F).flagged.any()

    def test_impossible_runs_measured_on_raw(self):
        """Six months pinned below the floor must be measurable BEFORE the scrub nulls it."""
        idx = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
        v = np.full(200, 5.0)
        v[50:110] = -12.4
        d = pd.DataFrame({"datetime": idx, "value": v})
        n, days = quality.impossible_runs(d, "nitrate")
        assert n == 60 and days == pytest.approx(59.0)

    def test_impossible_runs_see_in_range_sentinels(self):
        """999.99 turbidity sits INSIDE the physical range -- the fill run is invisible to the range test and must be named as a sentinel."""
        idx = pd.date_range("2024-01-01", periods=100, freq="D", tz="UTC")
        v = np.full(100, 12.0)
        v[10:40] = 999.99
        d = pd.DataFrame({"datetime": idx, "value": v})
        n0, _ = quality.impossible_runs(d, "turbidity_unknown")
        assert n0 == 0                                          # the range alone is blind here
        sent = quality.sentinel_values(["MPCA:X"], "turbidity_unknown")
        n1, days = quality.impossible_runs(d, "turbidity_unknown", sentinels=sent)
        assert n1 == 30 and days == pytest.approx(29.0)

    def test_flat_runs_are_gap_aware(self):
        t = np.array(["2024-01-01T00:00", "2024-01-01T00:15", "2024-01-01T00:30",
                      "2024-04-01T00:00"], dtype="datetime64[s]")
        v = np.array([3.0, 3.0, 3.0, 3.0])
        runs = quality._flat_runs(v, t)
        assert runs[0] == 3 and runs[3] == 1, "identical values across a 3-month hole are not one stuck run"

    def test_seasonal_strength_is_intrinsic(self):
        """An INVERTED annual cycle must score high -- the cohort-mean test flagged geography, not quality."""
        idx = pd.date_range("2018-01-01", periods=4 * 365, freq="D", tz="UTC")
        doy = idx.dayofyear.to_numpy(dtype="float64")
        rng = np.random.default_rng(0)
        inverted = 5 - 3 * np.sin(2 * np.pi * doy / 365.25) + rng.normal(0, 0.2, len(idx))
        d = pd.DataFrame({"datetime": idx, "value": inverted})
        assert quality.seasonal_strength(d) > 0.9

    def test_standard_channel_dead_series_excluded_by_rule(self):
        rec = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=120),
                            "ph_mean": 7.0})
        v, why = quality.assess_standard(rec, "ph")
        assert v == "exclude" and "dead series" in why
        rec2 = rec.assign(ph_mean=7.0 + np.linspace(0, 0.5, 120))
        assert quality.assess_standard(rec2, "ph")[0] == "keep"


class TestSpanExclusions:
    def test_parse_spans_strict(self):
        got = quality.parse_spans("2021-03-01..2021-07-15; 2022-01-02..2022-01-09")
        assert len(got) == 2 and got[0][0] == pd.Timestamp("2021-03-01")
        assert quality.parse_spans("") == [] and quality.parse_spans(None) == []
        for bad in ("2021-03-01", "2021-07-15..2021-03-01", "yesterday..today"):
            with pytest.raises(ValueError):
                quality.parse_spans(bad)

    def test_propose_spans_seven_day_rule(self):
        """A 10-day stuck run proposes; a 2-day quantised plateau does not; a gap breaks a run."""
        idx = pd.date_range("2024-01-01", periods=4000, freq="15min", tz="UTC")
        v = 5 + np.sin(np.arange(4000) / 50)
        v[500:1500] = 7.77                                    # ~10.4 days stuck
        d = pd.DataFrame({"datetime": idx, "value": v})
        got = quality.propose_spans(d)
        assert got.startswith("2024-01-06..2024-01-16")
        v2 = 5 + np.sin(np.arange(4000) / 50)
        v2[500:692] = 7.77                                    # ~2 days -- plausible plateau
        assert quality.propose_spans(pd.DataFrame({"datetime": idx, "value": v2})) == ""
        # identical values across a 3-month hole are two short runs, not one long one
        idx3 = idx[:600].append(idx[:600] + pd.Timedelta(days=90))    # two ~6-day halves, 90 days apart
        d3 = pd.DataFrame({"datetime": idx3, "value": 7.77})
        assert quality.propose_spans(d3) == "", "gap-aware: two sub-threshold halves never fuse into one run"

    def test_apply_masks_spans_inclusive_days_and_keeps_the_rest(self):
        rec = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=10),
            "nitrate_mean": np.arange(10.0), "nitrate_max": np.arange(10.0) + 1,
            "nitrate_src": "IWQIS:A", "ph_mean": 7.0,
        })
        V = pd.DataFrame([dict(code="SNR-0001", channel="nitrate", verdict="keep",
                               exclude_spans="2024-01-03..2024-01-05", max_threshold=None)])
        out = publish.apply_quality_masks({"SNR-0001": rec}, V)["SNR-0001"]
        d = out.set_index("date")
        for day in ("2024-01-03", "2024-01-04", "2024-01-05"):
            assert pd.isna(d.loc[day, "nitrate_mean"]) and pd.isna(d.loc[day, "nitrate_src"])
            assert d.loc[day, "ph_mean"] == 7.0, "other channels keep their in-span days"
        assert d.loc["2024-01-02", "nitrate_mean"] == 1.0 and d.loc["2024-01-06", "nitrate_mean"] == 5.0
        assert len(out) == 10, "rows survive while any channel still has data"
        # the source frame was not mutated -- the derived store is the full record
        assert rec.nitrate_mean.notna().all()

    def test_max_threshold_nulls_whole_days_above_it(self):
        """A day whose MAX exceeds the sensor ceiling loses the whole day -- its mean was computed from the offending samples too. A day exactly AT the threshold survives."""
        rec = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=5),
            "nitrate_mean": [5.0, 12.0, 5.0, 5.0, 5.0],
            "nitrate_max": [6.0, 43.9, 20.0, 6.0, 6.0],
            "nitrate_src": "MPCA:X", "ph_mean": 7.0,
        })
        V = pd.DataFrame([dict(code="SNR-0001", channel="nitrate", verdict="keep",
                               exclude_spans="", max_threshold=20.0)])
        d = publish.apply_quality_masks({"SNR-0001": rec}, V)["SNR-0001"].set_index("date")
        assert pd.isna(d.loc["2024-01-02", "nitrate_mean"]), "max 43.9 > 20: the day goes"
        assert d.loc["2024-01-03", "nitrate_mean"] == 5.0, "max exactly 20 is AT the ceiling, not above it"
        assert d.loc["2024-01-01", "nitrate_mean"] == 5.0
        assert (d.ph_mean == 7.0).all(), "the ceiling is per-channel"

    def test_threshold_and_spans_compose(self):
        rec = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=6),
            "nitrate_mean": [5.0, 5.0, 5.0, 99.0, 5.0, 5.0],
            "nitrate_src": "MPCA:X",
        })
        V = pd.DataFrame([dict(code="SNR-0001", channel="nitrate", verdict="keep",
                               exclude_spans="2024-01-01..2024-01-02", max_threshold=50.0)])
        d = publish.apply_quality_masks({"SNR-0001": rec}, V)["SNR-0001"]
        kept = pd.to_datetime(d.date[d.nitrate_mean.notna()]).dt.strftime("%m-%d").tolist()
        assert kept == ["01-03", "01-05", "01-06"]

    def test_verdicts_reject_malformed_cells(self, decisions_sandbox, monkeypatch):
        led = quality.quality_ledger()
        led.path.parent.mkdir(parents=True, exist_ok=True)
        entities = pd.DataFrame({c: pd.Series(dtype=str)
                                 for c in ("code", "members", "channels_present")})
        pd.DataFrame([dict(members="IWQIS:A", channel="nitrate", decision="keep",
                           criterion_version="qual-v1",
                           exclude_spans="not-a-span")]).to_csv(led.path, index=False)
        with pytest.raises(SystemExit, match="not 'YYYY-MM-DD"):
            quality.verdicts(entities, {})
        pd.DataFrame([dict(members="IWQIS:A", channel="nitrate", decision="keep",
                           criterion_version="qual-v1",
                           max_threshold="lots")]).to_csv(led.path, index=False)
        with pytest.raises(SystemExit, match="not a number"):
            quality.verdicts(entities, {})
