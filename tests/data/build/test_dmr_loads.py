"""The DMR effluent-loading derivation: speciation, the tier ladder, and the bounds that keep a
self-reported field from publishing more nitrogen than the world produces.
"""

import pandas as pd
import pytest

from data.build.derive import structures as S

XWALK = pd.DataFrame(columns=["npdes_id", "perm_feature_nmbr", "comid"])


def _archive(tmp_path, rows):
    d = pd.DataFrame(rows)
    p = tmp_path / "fy2020.parquet"
    d.to_parquet(p, index=False)
    return p


def _row(**kw):
    base = dict(EXTERNAL_PERMIT_NMBR="IA0000001", PERM_FEATURE_NMBR="001",
                PARAMETER_CODE="00630", MONITORING_LOCATION_CODE="1",
                VALUE_TYPE_CODE="Q1", STATISTICAL_BASE_CODE="MK", STANDARD_UNIT_DESC="kg/d",
                DMR_VALUE_STANDARD_UNITS=10.0, DMR_VALUE_ID=1,
                MONITORING_PERIOD_END_DATE="2020-01-31", NODI_CODE=None)
    base.update(kw)
    return base


def test_influent_is_excluded(tmp_path):
    """`G` is RAW SEWAGE INFLUENT -- the load a plant receives, not what it emits."""
    p = _archive(tmp_path, [_row(DMR_VALUE_ID=1),
                            _row(DMR_VALUE_ID=2, MONITORING_LOCATION_CODE="G",
                                 DMR_VALUE_STANDARD_UNITS=9999.0)])
    d = S._dmr_year(p, XWALK)
    assert len(d) == 1 and d.kg_per_day.iloc[0] == pytest.approx(10.0)


def test_duplicate_value_ids_are_collapsed(tmp_path):
    """Repeats differ only in violation code and carry identical values."""
    p = _archive(tmp_path, [_row(DMR_VALUE_ID=7), _row(DMR_VALUE_ID=7)])
    d = S._dmr_year(p, XWALK)
    assert len(d) == 1


def test_no3_speciation_is_converted_to_n(tmp_path):
    """`71850` reports as NO3 while every other code reports as N."""
    p = _archive(tmp_path, [_row(PARAMETER_CODE="71850", DMR_VALUE_STANDARD_UNITS=62.004)])
    d = S._dmr_year(p, XWALK)
    assert d.kg_per_day.iloc[0] == pytest.approx(14.007, rel=1e-3)


def test_species_are_kept_apart_never_summed(tmp_path):
    """Ammonia and nitrate are different molecules; one plant reporting both must not become one number."""
    p = _archive(tmp_path, [_row(DMR_VALUE_ID=1, PARAMETER_CODE="00610"),
                            _row(DMR_VALUE_ID=2, PARAMETER_CODE="00620")])
    d = S._dmr_year(p, XWALK)
    assert set(d.species) == {"ammonia", "nitrate"} and len(d) == 2


def test_nodi_c_is_a_real_zero_not_a_gap(tmp_path):
    """"No discharge" is a measurement. Dropping it biases every mean upward."""
    p = _archive(tmp_path, [_row(DMR_VALUE_STANDARD_UNITS=None, NODI_CODE="C")])
    d = S._dmr_year(p, XWALK)
    assert len(d) == 1 and d.kg_per_day.iloc[0] == 0.0


def test_sentinel_and_negative_values_are_refused(tmp_path):
    p = _archive(tmp_path, [_row(DMR_VALUE_ID=1, DMR_VALUE_STANDARD_UNITS=-9999.0),
                            _row(DMR_VALUE_ID=2, DMR_VALUE_STANDARD_UNITS=-5.0)])
    assert len(S._dmr_year(p, XWALK)) == 0


def test_impossible_quantities_are_refused(tmp_path):
    """The 41,078,607 kg/day case -- ten times the nitrogen the Mississippi carries."""
    p = _archive(tmp_path, [_row(DMR_VALUE_STANDARD_UNITS=41_078_607.0)])
    assert len(S._dmr_year(p, XWALK)) == 0


def test_concentration_times_flow_when_no_quantity_is_reported(tmp_path):
    """Tier 2: mg/L x MGD -> kg/day."""
    p = _archive(tmp_path, [
        _row(DMR_VALUE_ID=1, VALUE_TYPE_CODE="C2", STANDARD_UNIT_DESC="mg/L",
             DMR_VALUE_STANDARD_UNITS=10.0),
        _row(DMR_VALUE_ID=2, PARAMETER_CODE="50050", VALUE_TYPE_CODE="Q1",
             STANDARD_UNIT_DESC="MGD", DMR_VALUE_STANDARD_UNITS=1.0),
    ])
    d = S._dmr_year(p, XWALK)
    assert len(d) == 1 and d.tier.iloc[0] == "C_times_flow"
    assert d.kg_per_day.iloc[0] == pytest.approx(10.0 * 3_785_411.784 / 1e6, rel=1e-6)


def test_reported_quantity_wins_over_computed(tmp_path):
    """A reported quantity is the plant's own arithmetic; ours is a fallback."""
    p = _archive(tmp_path, [
        _row(DMR_VALUE_ID=1, DMR_VALUE_STANDARD_UNITS=5.0),
        _row(DMR_VALUE_ID=2, VALUE_TYPE_CODE="C2", STANDARD_UNIT_DESC="mg/L",
             DMR_VALUE_STANDARD_UNITS=99.0),
        _row(DMR_VALUE_ID=3, PARAMETER_CODE="50050", VALUE_TYPE_CODE="Q1",
             STANDARD_UNIT_DESC="MGD", DMR_VALUE_STANDARD_UNITS=1.0),
    ])
    d = S._dmr_year(p, XWALK)
    assert len(d) == 1 and d.tier.iloc[0] == "Q_reported"
    assert d.kg_per_day.iloc[0] == pytest.approx(5.0)


def test_impossible_flow_is_refused_so_the_product_cannot_explode(tmp_path):
    """80,385,869 MGD is two hundred Mississippis; multiplied by a concentration it is how a single
    outfall came to claim 8.2e12 kg of nitrogen in one month."""
    p = _archive(tmp_path, [
        _row(DMR_VALUE_ID=1, VALUE_TYPE_CODE="C2", STANDARD_UNIT_DESC="mg/L",
             DMR_VALUE_STANDARD_UNITS=10.0),
        _row(DMR_VALUE_ID=2, PARAMETER_CODE="50050", VALUE_TYPE_CODE="Q1",
             STANDARD_UNIT_DESC="MGD", DMR_VALUE_STANDARD_UNITS=80_385_869.0),
    ])
    assert len(S._dmr_year(p, XWALK)) == 0
