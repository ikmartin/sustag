import pandas as pd
import pytest

from data.build import registry as R


def test_high_scrutiny_is_nitrate_alone():
    assert R.high_scrutiny() == frozenset({"nitrate"})


def test_weak_set_matches_calibration():
    # Channels whose agreement means shared climate, not shared instrument -- the calibrated seven plus the two canary channels.
    weak = set(R.NAMES) - R.discriminating()
    assert weak == {"water_temp", "gage_height", "ph", "diss_oxy",
                    "turbidity_fnu", "turbidity_ntu", "turbidity_unknown",
                    "gw_level", "precip"}


def test_canary_channels_exist_at_canary_depth():
    # The roster's groundwater-level and precipitation probes must have something fetchable, or the
    # canary invariant ("flows end-to-end") is vacuously false: the registry defines what we request.
    assert set(R.collected("canary")) == {"gw_level", "precip"}
    assert R.CHANNELS["gw_level"].sources["USGS"].cols == ("72019",)
    assert R.CHANNELS["precip"].sources["USGS"].cols == ("00045",)


def test_resolve_strips_dv_and_reports_preaggregation():
    assert R.resolve("USGS", "99133_dv") == ("nitrate", True)
    assert R.resolve("USGS", "99133") == ("nitrate", False)
    assert R.resolve("IWQIS", "turbi_mean") == ("turbidity_unknown", False)
    assert R.resolve("USGS", "unknown_pcode") == (None, False)


def test_extract_converts_units_combines_and_counts_sentinels():
    obs = pd.DataFrame({"00060_dv": [100.0, 200.0]})
    s, t = R.extract(obs, "USGS", "discharge")
    assert t["n_sentinel"] == 0 and abs(s.iloc[0] - 2.8316846592) < 1e-12
    # nitrate combines by MAX across its pcodes
    obs = pd.DataFrame({"99133": [1.0, None], "00630": [2.0, 5.0]})
    s, t = R.extract(obs, "USGS", "nitrate")
    assert s.tolist() == [2.0, 5.0] and t["n_cells"] == 3


def test_sentinels_masked_per_column_before_combine():
    # A -999999 averaged with a real value is no longer -999999 and is uncatchable afterward -- the mask
    # must run per native column, pre-combine. Here the combined mean must equal the REAL column's value.
    obs = pd.DataFrame({"spec_cond": [500.0, 500.0], "spec_cond_v2": [-999999.0, 480.0]})
    s, t = R.extract(obs, "IWQIS", "spec_cond")
    assert t["n_sentinel"] == 1
    assert s.tolist() == [500.0, 490.0]


def test_sentinels_are_per_source():
    # 999.99 is an MPCA fill; the same number from USGS is a (strange) measurement, not a sentinel.
    s, t = R.extract(pd.DataFrame({"nitrate": [999.99]}), "MPCA", "nitrate")
    assert t["n_sentinel"] == 1 and s is None
    s, t = R.extract(pd.DataFrame({"99133": [999.99]}), "USGS", "nitrate")
    assert t["n_sentinel"] == 0 and s.tolist() == [999.99]


def test_extract_modes():
    # none: sentinels intact (impossible_runs reads this); full: per-cell range scrub with closure.
    obs = pd.DataFrame({"nitrate": [1.0, 999.99, -0.5, -12.4, 200.0]})
    s, t = R.extract(obs, "MPCA", "nitrate", scrub="none")
    assert 999.99 in s.tolist() and t["n_sentinel"] == 0
    s, t = R.extract(obs, "MPCA", "nitrate", scrub="full")
    # 999.99 sentinel; -0.5 clamped to 0 (kept); -12.4 below lo nulled; 200 above hi nulled
    assert t == dict(n_cells=5, n_sentinel=1, n_below_lo=1, n_clamped=1, n_above_hi=1)
    assert s.tolist() == [1.0, 0.0]
    n_kept = t["n_cells"] - t["n_sentinel"] - t["n_below_lo"] - t["n_above_hi"]
    assert n_kept == len(s)


def test_clamp_closure_rejects_dead_clamp():
    # clamp_negatives with lo >= 0 is dead code: the below-lo null runs first and every sub-detection
    # reading is deleted instead of clamped. The registry must refuse the combination at declaration.
    with pytest.raises(ValueError, match="clamp_negatives"):
        R.Channel(unit="x", fetch_priority=1, stats="full", lo=0.0, hi=10.0, clamp_negatives=True)
    with pytest.raises(ValueError, match="clamp_negatives"):
        R.Channel(unit="x", fetch_priority=1, stats="full", lo=None, hi=10.0, clamp_negatives=True)


def test_every_clamping_channel_has_negative_lo():
    for name, c in R.CHANNELS.items():
        if c.clamp_negatives:
            assert c.lo is not None and c.lo < 0, name


def test_ranges_match_the_chapter_table():
    C = R.CHANNELS
    assert (C["discharge"].lo, C["discharge"].hi) == (-10_000.0, 50_000.0)
    assert (C["gage_height"].lo, C["gage_height"].hi) == (-1_000.0, 10_000.0)
    assert C["water_temp"].hi == 60.0          # publishes the hot spring; elsewhere 5 of 240M values in (45, 60]
    assert (C["spec_cond"].lo, C["spec_cond"].hi) == (-5.0, 200_000.0)   # road salt is real to 177k
    assert (C["diss_oxy"].lo, C["diss_oxy"].hi) == (-1.0, 30.0)
    assert C["nitrate"].hi == 100.0            # physical bound, NOT the old 50 mg/L trim


def test_fingerprint_covers_sentinels(monkeypatch):
    before = R.fingerprint()
    monkeypatch.setitem(R.SENTINELS, "MPCA", (-999999.0,))
    assert R.fingerprint() != before           # a sentinel edit must stale every canonical file


def test_fetch_tier_from_declaration_only():
    assert R.fetch_tier("nitrate+water_temp") == "native"
    assert R.fetch_tier("discharge+gage_height") == "dv"
    assert R.fetch_tier(None) == "dv"          # unknown declaration gets the cheap tier


def test_axis_validation_raises():
    with pytest.raises(ValueError):
        R.Channel(unit="x", fetch_priority=1, stats="full", lo=None, hi=None,
                  clamp_negatives=False, scrutiny="extreme")
    with pytest.raises(ValueError):
        R.SourceMap(("a",), "u", units="guessed")


def test_units_report_lists_unverified():
    rows = R.units_report()
    assert ("nitrate", "MPCA") not in {(r[0], r[1]) for r in rows}   # verified, not listed
    assert any(r[0] == "turbidity_unknown" for r in rows)
