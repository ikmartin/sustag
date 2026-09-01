"""Repo-rooted imports and store sandboxing for data/build tests."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


@pytest.fixture
def decisions_sandbox(tmp_path, monkeypatch):
    """Redirect the decisions and review stores to a temp dir, so ledger and id-mint tests touch nothing real."""
    from data.build import config

    monkeypatch.setattr(config, "DECISIONS", tmp_path / "decisions")
    monkeypatch.setattr(config, "REVIEW_DIR", tmp_path / "review")
    (tmp_path / "decisions").mkdir()
    return tmp_path


@pytest.fixture
def weather_store(tmp_path, monkeypatch):
    """A tiny gridMET store in `tmp_path`, plus a builder for quarters. Returns `(root, write)`.

    THE VERIFY CHECKS NEED A STORE THEY CAN BREAK. `tests/storecheck/` was empty and no check had ever been
    observed failing -- which is how two of the three weather checks shipped asserting against
    `grid.parquet`'s cell count, a number that is legitimately larger than the cells carrying data, so they
    failed on a healthy store. A check nobody has watched fail is not known to work.

    `write(year, q, months, variables=..., n_cells=...)` lays down one quarter, where `months` is
    {month: n_days}. Both weather caches are cleared around every build, because they are module-level
    `lru_cache`s that would otherwise report the previous test's store.
    """
    import datetime as dt

    import numpy as np
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data.build import config
    from data.build.acquire import weather

    root = tmp_path / "weather"
    monkeypatch.setattr(config, "ACQ_WEATHER", root)
    weather._forget_quarter()

    def write(year, q, months, variables=None, n_cells=4):
        variables = list(variables if variables is not None else weather.VARIABLES)
        dates = [dt.date(year, m, d) for m, nd in sorted(months.items()) for d in range(1, nd + 1)]
        cols = {"cell_id": np.repeat(np.arange(n_cells, dtype="int32"), len(dates)),
                "date": np.tile(np.array(dates, dtype="object"), n_cells)}
        for v in variables:
            cols[v] = np.full(n_cells * len(dates), 1.0, dtype="float32")
        t = pa.Table.from_pandas(pd.DataFrame(cols), preserve_index=False)
        t = t.append_column("zkey", pa.array(weather.zkey(t.column("cell_id").to_numpy())))
        p = weather._quarter_path(year, q)
        p.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(t, p, compression="snappy")
        # the grid the lattice check reads its cell count from
        gp = weather._grid_path()
        gp.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({"cell_id": np.arange(n_cells, dtype="int32")}), gp)
        weather._forget_quarter()
        return p

    yield root, write
    weather._forget_quarter()
