"""StreamCat full-series family requests, and the column-aware coverage test that makes them take effect.

`latest_vintage` collapses each metric family to its newest member, which is right for most metrics and destroys exactly the families whose value IS the series -- the annual nitrogen mass balance. Restoring them was a two-part fix: ask for the vintages, and notice they are missing.
"""

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.build.acquire import attributes as A

NAMES = ["n_ff_1987", "n_ff_2017", "n_lw_1987", "n_lw_2017", "pctcrop2019", "elev", "n_ff_[Year]"]


def test_family_of_strips_the_vintage():
    assert A._family_of("n_ff_2017") == "n_ff"
    assert A._family_of("n_lwr_1987") == "n_lwr"
    assert A._family_of("elev") == "elev"


def test_expand_full_series_returns_every_vintage_of_named_families():
    got = A.expand_full_series(NAMES, ("n_ff", "n_lw"))
    assert got == ["n_ff_1987", "n_ff_2017", "n_lw_1987", "n_lw_2017"]


def test_expand_skips_unexpanded_templates():
    """`n_ff_[Year]` is a template, not a metric; asking for it fetches nothing."""
    assert "n_ff_[Year]" not in A.expand_full_series(NAMES, ("n_ff",))


def test_expand_reports_a_family_that_matches_nothing(capsys):
    """A typo'd family would otherwise leave the series missing for as long as nobody checked."""
    A.expand_full_series(NAMES, ("n_ff", "n_nonexistent"))
    assert "n_nonexistent" in capsys.readouterr().out


def test_the_declared_families_cover_the_nitrogen_balance():
    for fam in ("n_ff", "n_lw", "n_leg", "n_ags", "n_dep"):
        assert fam in A.STREAMCAT_FULL_SERIES


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    """A StreamCat-shaped file on disk carrying only the newest vintage."""
    monkeypatch.setattr(A.config, "ACQ_ATTRIBUTES", tmp_path)
    d = pd.DataFrame({"COMID": range(100), "n_ff_2017cat": 1.0, "n_ff_2017ws": 1.0})
    pq.write_table(pa.Table.from_pandas(d, preserve_index=False), tmp_path / "streamcat.parquet")
    return list(range(100))


def test_covered_is_false_when_a_requested_column_is_absent(artifact, capsys):
    """THE ACTUAL BUG: 327 columns of annual nitrogen were added to the request and never fetched,
    because the rows were already there and nothing compared the columns."""
    assert A._covered("streamcat", artifact, key="COMID",
                      columns=["n_ff_2017cat", "n_ff_1987cat"]) is False
    assert "missing" in capsys.readouterr().out


def test_covered_is_true_when_every_requested_column_is_present(artifact):
    assert A._covered("streamcat", artifact, key="COMID",
                      columns=["n_ff_2017cat", "n_ff_2017ws"]) is True


def test_covered_still_refuses_a_file_with_no_key_column(tmp_path, monkeypatch):
    monkeypatch.setattr(A.config, "ACQ_ATTRIBUTES", tmp_path)
    d = pd.DataFrame({"n_ff_2017cat": [1.0] * 100})
    pq.write_table(pa.Table.from_pandas(d, preserve_index=False), tmp_path / "streamcat.parquet")
    assert A._covered("streamcat", list(range(100)), key="COMID", columns=["n_ff_2017cat"]) is False


def test_covered_without_a_column_list_behaves_as_before(artifact):
    """The column test is opt-in; callers that do not name columns keep the old row-count semantics."""
    assert A._covered("streamcat", artifact, key="COMID") is True
