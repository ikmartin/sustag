"""`io.copy_parquet_stamped`: restamping an artifact without materialising it.

`write_parquet(read_parquet(p))` is the obvious restamp and does not survive a large product -- the twelve-component nutrients table is 320.8M rows and needs ~10 GB resident for a 3.5 GB file. These tests assert the contract the streaming copy has to keep: same rows, same row-group boundaries (so pushdown statistics survive), stamps readable by the same reader that checks them.
"""

import pandas as pd
import pyarrow.parquet as pq
import pytest

from data.build import io as bio


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "src.parquet"
    d = pd.DataFrame({"comid": range(300), "product": ["surplus", "fertilizer"] * 150,
                      "kg": [float(i) for i in range(300)]})
    # several row groups, so boundary preservation is actually exercised
    pq.write_table(pq.Table.from_pandas(d, preserve_index=False) if hasattr(pq, "Table")
                   else __import__("pyarrow").Table.from_pandas(d, preserve_index=False),
                   p, row_group_size=50)
    return p


def test_rows_and_values_survive(src, tmp_path):
    dst = tmp_path / "dst.parquet"
    bio.copy_parquet_stamped(src, dst, stamps={"aoe": "a1"})
    a, b = pd.read_parquet(src), pd.read_parquet(dst)
    pd.testing.assert_frame_equal(a, b)


def test_row_group_boundaries_are_preserved(src, tmp_path):
    """Statistics a predicate-pushdown reader prunes on live per row group."""
    dst = tmp_path / "dst.parquet"
    bio.copy_parquet_stamped(src, dst, stamps={"aoe": "a1"})
    assert pq.ParquetFile(dst).metadata.num_row_groups == pq.ParquetFile(src).metadata.num_row_groups


def test_stamps_are_readable_by_the_checking_reader(src, tmp_path):
    dst = tmp_path / "dst.parquet"
    bio.copy_parquet_stamped(src, dst, stamps={"aoe": "a1", "snapshot": "snap-1"})
    assert bio.read_stamps(dst) == {"aoe": "a1", "snapshot": "snap-1"}
    bio.read_parquet(dst, expect={"aoe": "a1"})            # must not raise
    with pytest.raises(bio.StaleArtifactError):
        bio.read_parquet(dst, expect={"aoe": "different"})


def test_no_stamps_is_a_plain_copy(src, tmp_path):
    dst = tmp_path / "dst.parquet"
    bio.copy_parquet_stamped(src, dst)
    pd.testing.assert_frame_equal(pd.read_parquet(src), pd.read_parquet(dst))


def test_no_tmp_file_is_left_behind(src, tmp_path):
    dst = tmp_path / "dst.parquet"
    bio.copy_parquet_stamped(src, dst, stamps={"aoe": "a1"})
    assert list(tmp_path.glob("*.tmp")) == []
