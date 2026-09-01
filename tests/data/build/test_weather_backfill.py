"""The column backfill: staging, the packed join key, and the merge that adds columns to an existing quarter.

Every test here guards a way the merge could silently corrupt a 1.28 GB file it does not fully read.
"""

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.build.acquire import weather

NEW = ["fm1000", "fm100"]


def _stage(year, q, dates, n_cells=4, cols=NEW, drop_last=0):
    """Write a staging file covering `dates` x cells, optionally short by `drop_last` rows."""
    import pandas as pd

    d = [(c, dt) for c in range(n_cells) for dt in dates]
    if drop_last:
        d = d[:-drop_last]
    f = pd.DataFrame({"cell_id": np.array([x[0] for x in d], dtype="int32"),
                      "date": [x[1] for x in d]})
    for i, c in enumerate(cols):
        f[c] = np.arange(len(d), dtype="float32") + i * 1000
    p = weather._backfill_path(year, q)
    p.parent.mkdir(parents=True, exist_ok=True)
    f.to_parquet(p, index=False)
    return f


def test_pack_key_is_unique_and_order_preserving():
    cells = np.array([0, 1, 1, 810_808], dtype="int64")
    days = np.array([0, 0, 1, 20_000], dtype="int64")
    k = weather._pack_key(cells, days)
    assert len(np.unique(k)) == 4
    assert (np.diff(k) > 0).all(), "packing must sort by cell then date"


def test_pack_key_refuses_out_of_range():
    with pytest.raises(ValueError, match="cell_id out of packing range"):
        weather._pack_key(np.array([1 << 20]), np.array([0]))


def test_merge_adds_columns_and_preserves_every_existing_value(weather_store, monkeypatch, tmp_path):
    """The core guarantee: new columns land correctly and NOTHING already in the file moves or changes."""
    from data.build import config

    monkeypatch.setattr(config, "WORK", tmp_path / "work")
    _, write = weather_store
    p = write(2020, 1, {1: 31, 2: 29, 3: 31}, variables=[v for v in weather.VARIABLES if v not in NEW])
    before = pq.read_table(p)

    dates = sorted({d for d in before.column("date").to_pylist()})
    _stage(2020, 1, dates)
    n = weather.merge_quarter_vars(2020, 1)
    after = pq.read_table(p)

    assert n == before.num_rows and after.num_rows == before.num_rows
    for c in before.column_names:                      # every pre-existing column, value for value
        assert after.column(c).to_pylist() == before.column(c).to_pylist(), f"{c} changed"
    for c in NEW:
        assert c in after.column_names
        assert after.column(c).null_count == 0


def test_merge_refuses_when_staging_cannot_answer_every_row(weather_store, monkeypatch, tmp_path):
    """A column null for part of the record looks complete in the footer's NAMES and would read as data."""
    from data.build import config

    monkeypatch.setattr(config, "WORK", tmp_path / "work")
    _, write = weather_store
    p = write(2020, 1, {1: 31}, variables=[v for v in weather.VARIABLES if v not in NEW])
    dates = sorted({d for d in pq.read_table(p).column("date").to_pylist()})
    _stage(2020, 1, dates, drop_last=5)                # five rows the staging has no answer for

    with pytest.raises(RuntimeError, match="refusing to write a partly-null column"):
        weather.merge_quarter_vars(2020, 1)
    assert "fm1000" not in pq.ParquetFile(p).schema_arrow.names, "the file must be untouched"


def test_merge_leaves_no_tmp_behind_when_it_refuses(weather_store, monkeypatch, tmp_path):
    """`snapshot.manifest_rows` REFUSES a stray .tmp under the stores, so a failed merge must clean up."""
    from data.build import config

    monkeypatch.setattr(config, "WORK", tmp_path / "work")
    _, write = weather_store
    p = write(2020, 1, {1: 31}, variables=[v for v in weather.VARIABLES if v not in NEW])
    dates = sorted({d for d in pq.read_table(p).column("date").to_pylist()})
    _stage(2020, 1, dates, drop_last=5)
    with pytest.raises(RuntimeError):
        weather.merge_quarter_vars(2020, 1)
    assert not list(p.parent.glob("*.tmp"))


def test_merge_makes_the_variables_check_pass(weather_store, monkeypatch, tmp_path):
    """End to end: a store that fails the coverage check passes it once the backfill lands."""
    from data.build import config

    monkeypatch.setattr(config, "WORK", tmp_path / "work")
    _, write = weather_store
    p = write(2020, 1, {1: 31, 2: 29, 3: 31}, variables=[v for v in weather.VARIABLES if v not in NEW])
    write(2020, 2, {4: 30, 5: 31, 6: 30})              # a complete quarter, so only Q1 is short

    ok, detail = weather._check_weather_variables()
    assert ok is False and "fm1000" in detail

    dates = sorted({d for d in pq.read_table(p).column("date").to_pylist()})
    _stage(2020, 1, dates)
    weather.merge_quarter_vars(2020, 1)
    ok, detail = weather._check_weather_variables()
    assert ok is True, detail


def test_staging_is_removed_on_success(weather_store, monkeypatch, tmp_path):
    from data.build import config

    monkeypatch.setattr(config, "WORK", tmp_path / "work")
    _, write = weather_store
    p = write(2020, 1, {1: 31}, variables=[v for v in weather.VARIABLES if v not in NEW])
    dates = sorted({d for d in pq.read_table(p).column("date").to_pylist()})
    _stage(2020, 1, dates)
    weather.merge_quarter_vars(2020, 1)
    assert not weather._backfill_path(2020, 1).exists()
