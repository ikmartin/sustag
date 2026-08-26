import pandas as pd
import pytest

from data.build import contracts as c


def test_uid_roundtrip():
    uid = c.make_uid("IWQIS", "WQS0031")
    assert uid == "IWQIS:WQS0031" and c.split_uid(uid) == ("IWQIS", "WQS0031")
    assert c.uid_to_filename(uid) == "IWQIS__WQS0031"
    # WQP native ids contain a separator themselves: split on the FIRST only.
    assert c.split_uid("WQP:USGS-05412500") == ("WQP", "USGS-05412500")


def test_uid_refusals():
    with pytest.raises(ValueError):
        c.make_uid("NOAA", "x")
    with pytest.raises(ValueError):
        c.make_uid("USGS", "  ")


def test_schema_fingerprint_order_independent_dtype_sensitive():
    a = c.schema_fingerprint([("a", "Int64"), ("b", "string")])
    assert a == c.schema_fingerprint({"b": "string", "a": "Int64"})
    assert a != c.schema_fingerprint([("a", "float64"), ("b", "string")])
    assert a != c.schema_fingerprint([("a", "Int64")])


def test_block_ownership_check():
    cols = [("x", "Int64", "site"), ("y", "Int64", "ghost")]
    owners = {"site": c.Block("A2", carried=False, aoe_dependent=False)}
    with pytest.raises(ValueError, match="ghost"):
        c.check_blocks(cols, owners, "test")
    assert c.columns_in(cols[:1], owners, carried=False) == ["x"]


def test_contract_has_the_new_columns():
    for col in ("snr_id", "coord_dp_lat", "coord_dp_lon", "location_missing_reason",
                "fetch_status", "fetch_last_attempt", "fetch_last_success", "publishes_under"):
        assert col in c.SENSOR_COLS, col
    assert "site_id" not in c.SENSOR_COLS      # positional site ids do not exist in this build


def _row(**over):
    base = {col: None for col in c.SENSOR_COLS}
    base.update(site_uid="IWQIS:WQS0001", source="IWQIS", lat=42.0, lon=-93.0)
    base.update(over)
    return base


def test_validate_sensors_null_coords_need_a_reason():
    ok = c.conform_sensors(pd.DataFrame([_row()]))
    assert c.validate_sensors(ok) == []
    # A coordinate-sentinel registration is legal WITH its reason recorded...
    sent = c.conform_sensors(pd.DataFrame([_row(lat=None, lon=None,
                                                location_missing_reason="coordinate_sentinel(-99.0)")]))
    assert c.validate_sensors(sent) == []
    # ...and a parsing loss -- null coordinates, no reason -- is a contract violation.
    lost = c.conform_sensors(pd.DataFrame([_row(lat=None, lon=None)]))
    assert any("location_missing_reason" in b for b in c.validate_sensors(lost))


def test_conform_refuses_unknown_columns():
    with pytest.raises(ValueError, match="ghost_col"):
        c.conform_sensors(pd.DataFrame([{**_row(), "ghost_col": 1}]))
