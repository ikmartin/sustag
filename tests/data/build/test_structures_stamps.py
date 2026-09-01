"""Product caches must notice a changed INPUT, not only a changed extent.

Every one of these stamped `aoe` and only `aoe`, so a rebuilt input left the cached artifact standing:
`build_dmr_loads` reads nineteen fiscal-year archives and would ignore a twentieth; `build_facilities`
reads CWNS, a MANUAL acquisition, so the ordinary sequence -- run D5, download the zip, rerun D5 --
returned the cached frame and the `cwns_*` columns never joined, with nothing printed to say so.
"""

import pathlib

from data.build.derive import structures as S


def test_stamp_changes_when_a_file_is_added(tmp_path):
    a = tmp_path / "fy2025.parquet"
    a.write_bytes(b"x")
    before = S._input_stamp(a)
    b = tmp_path / "fy2026.parquet"
    b.write_bytes(b"y")
    assert S._input_stamp(a, b) != before


def test_stamp_changes_when_a_file_is_rewritten(tmp_path):
    a = tmp_path / "outfalls.parquet"
    a.write_bytes(b"x")
    before = S._input_stamp(a)
    a.write_bytes(b"xxxxxxxxxx")            # different size
    assert S._input_stamp(a) != before


def test_an_absent_input_is_part_of_the_identity(tmp_path):
    """CWNS is optional and manual. Its ARRIVAL must invalidate the cache, so absence has to be stamped
    rather than skipped -- otherwise the artifact built without it looks identical to one built with it."""
    present = tmp_path / "cwns.parquet"
    absent_stamp = S._input_stamp(present)
    present.write_bytes(b"data")
    assert S._input_stamp(present) != absent_stamp


def test_stamp_is_order_independent(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"1"); b.write_bytes(b"2")
    assert S._input_stamp(a, b) == S._input_stamp(b, a)


def test_stamp_is_stable_when_nothing_changes(tmp_path):
    a = tmp_path / "a"
    a.write_bytes(b"1")
    assert S._input_stamp(a) == S._input_stamp(a)
