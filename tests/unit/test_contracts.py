import pytest

from src.build3 import contracts as c


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


def test_site_ids_sort_into_flow_order():
    ids = [c.make_site_id(11915787, p) for p in (450.0, 37.2, 8.27)]
    assert sorted(ids) == [c.make_site_id(11915787, p) for p in (8.27, 37.2, 450.0)]


def test_site_id_parse_and_namespaces():
    sid = c.make_site_id(11915787, 37.23)
    assert sid == "S11915787-00037" and c.parse_site_id(sid) == (11915787, 37)
    off = c.make_off_network_id("IWQIS:WQS0029")
    assert not c.is_network_site(off) and c.parse_site_id(off) is None
    assert c.is_network_site(sid)


def test_site_id_refuses_nan_comid_and_negative_pos():
    with pytest.raises(ValueError):
        c.make_site_id(float("nan"), 5)
    with pytest.raises(ValueError):
        c.make_site_id(1, -1)


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
