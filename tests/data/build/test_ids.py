import pandas as pd
import pytest

from data.build import ids


def test_codec_roundtrip_and_examples():
    assert ids.encode(1) == "SNR-0001"
    assert ids.encode(42) == "SNR-0016"
    assert ids.encode(20226) == "SNR-0FLU"
    assert ids.encode(ids.CAPACITY) == "SNR-ZZZZ"
    for n in (1, 35, 36, 1295, 1296, 20226, ids.CAPACITY):
        assert ids.decode(ids.encode(n)) == n


def test_codec_refusals():
    with pytest.raises(ValueError):
        ids.encode(0)                              # SNR-0000 is never assigned
    with pytest.raises(ValueError):
        ids.encode(ids.CAPACITY + 1)
    for bad in ("SNR-0000", "SNR-00016", "snr-0016", "SNR-001", "S-0016", "SNR-00!6"):
        with pytest.raises(ValueError):
            ids.decode(bad)


def _batch(rows):
    return pd.DataFrame(rows, columns=["site_uid", "lat", "lon"])


def test_christening_sorts_lat_lon_uid(decisions_sandbox):
    new = ids.mint_new(_batch([("USGS:B", 44.0, -93.0), ("USGS:A", 42.0, -95.0),
                               ("IWQIS:C", 42.0, -93.0)]), "snap-x")
    # sort is (lat, lon, uid): A (42, -95) then C (42, -93) then B (44, ...)
    assert new == {"USGS:A": "SNR-0001", "IWQIS:C": "SNR-0002", "USGS:B": "SNR-0003"}
    d = ids.load()
    assert list(d.snr_id) == ["SNR-0001", "SNR-0002", "SNR-0003"]
    assert set(d.minted_under) == {"snap-x"}


def test_later_mints_append_and_never_renumber(decisions_sandbox):
    ids.mint_new(_batch([("USGS:A", 42.0, -95.0)]), "snap-1")
    # a later arrival that would sort FIRST geographically still takes the NEXT code -- ids are minted
    # once and never re-derived from a sort of the current table.
    new = ids.mint_new(_batch([("USGS:0", 30.0, -99.0), ("USGS:A", 42.0, -95.0)]), "snap-2")
    assert new == {"USGS:0": "SNR-0002"}
    assert ids.mapping()["USGS:A"] == "SNR-0001"
    # re-minting the same batch assigns nothing
    assert ids.mint_new(_batch([("USGS:A", 42.0, -95.0)]), "snap-3") == {}


def test_null_coordinates_sort_last(decisions_sandbox):
    new = ids.mint_new(_batch([("IWQIS:WQS9994", None, None), ("USGS:A", 42.0, -95.0)]), "s")
    assert new["USGS:A"] == "SNR-0001" and new["IWQIS:WQS9994"] == "SNR-0002"


def test_duplicate_uids_in_batch_refused(decisions_sandbox):
    with pytest.raises(ValueError, match="duplicate"):
        ids.mint_new(_batch([("USGS:A", 1.0, 2.0), ("USGS:A", 1.0, 2.0)]), "s")


def test_validate_catches_rewrites(decisions_sandbox):
    ids.mint_new(_batch([("USGS:A", 42.0, -95.0), ("USGS:B", 43.0, -95.0)]), "s")
    assert ids.validate() == []
    d = ids.load().iloc[[1, 0]].reset_index(drop=True)          # reordered = rewritten, not appended
    assert any("appended" in b for b in ids.validate(d))
    dup = ids.load()
    dup.loc[1, "snr_id"] = "SNR-0001"                            # bijection broken
    assert any("bijection" in b for b in ids.validate(dup))
