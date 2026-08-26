import pandas as pd
import pytest

from src.build3 import registry as R


def test_high_scrutiny_is_nitrate_alone():
    assert R.high_scrutiny() == frozenset({"nitrate"})


def test_weak_set_matches_calibration():
    # The seven channels whose agreement means shared climate, from build2's WEAK_FOR_IDENTITY.
    weak = set(R.NAMES) - R.discriminating()
    assert weak == {"water_temp", "gage_height", "ph", "diss_oxy",
                    "turbidity_fnu", "turbidity_ntu", "turbidity_unknown"}


def test_resolve_strips_dv_and_reports_preaggregation():
    assert R.resolve("USGS", "99133_dv") == ("nitrate", True)
    assert R.resolve("USGS", "99133") == ("nitrate", False)
    assert R.resolve("IWQIS", "turbi_mean") == ("turbidity_unknown", False)
    assert R.resolve("USGS", "unknown_pcode") == (None, False)


def test_extract_converts_units_and_combines():
    obs = pd.DataFrame({"00060_dv": [100.0, 200.0]})
    s = R.extract(obs, "USGS", "discharge")
    assert abs(s.iloc[0] - 2.8316846592) < 1e-12
    # nitrate combines by MAX across its pcodes
    obs = pd.DataFrame({"99133": [1.0, None], "00630": [2.0, 5.0]})
    s = R.extract(obs, "USGS", "nitrate")
    assert s.tolist() == [2.0, 5.0]


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


def test_nitrate_ceiling_is_physical_not_a_trim():
    # hi=100 nulls fill sentinels (999.99); it is NOT the old 50 mg/L distribution trim, whose removal recovered ~10,700 real readings above 50 -- the bound must stay well clear of real extremes (cohort rail ~44).
    assert R.CHANNELS["nitrate"].hi == 100.0
    assert R.CHANNELS["nitrate"].hi > 50
