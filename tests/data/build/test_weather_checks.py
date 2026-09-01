"""The weather store's verify checks, each proven to FAIL on a store broken in the specific way it guards.

A check is only known to work once it has been watched failing. Two of these three shipped wrong on their first run -- asserting against `grid.parquet`'s cell count, which is legitimately larger than the count carrying data -- and failed on a healthy store. These tests are what would have caught that.
"""

import pytest

from data.build.acquire import weather


@pytest.fixture
def healthy(weather_store):
    """Two full quarters, every declared variable present. Every check must pass on this."""
    _, write = weather_store
    write(2020, 1, {1: 31, 2: 29, 3: 31})
    write(2020, 2, {4: 30, 5: 31, 6: 30})
    return write


def test_all_checks_pass_on_a_healthy_store(healthy):
    for fn in (weather._check_weather_variables,
               weather._check_weather_quarter_starts,
               weather._check_weather_lattice):
        ok, detail = fn()
        assert ok is True, f"{fn.__name__} failed on a healthy store: {detail}"


def test_variables_check_fails_when_a_declared_variable_is_absent(weather_store):
    """The failure that let five variables be declared and never fetched."""
    _, write = weather_store
    write(2020, 1, {1: 31, 2: 29, 3: 31}, variables=[v for v in weather.VARIABLES if v != "pr"])
    ok, detail = weather._check_weather_variables()
    assert ok is False and "pr" in detail


def test_variables_check_fails_when_a_column_exists_but_is_partly_null(weather_store):
    """NAMES ARE NOT COVERAGE. A column carried across a compact exists in the footer and is null for the
    months it did not cover; a name check calls that done, which is why the predicate is the null count."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    _, write = weather_store
    p = write(2020, 1, {1: 31, 2: 29, 3: 31})
    t = pq.read_table(p)
    vals = np.array(t.column("pr").to_pylist(), dtype="float32")
    mask = np.zeros(len(vals), dtype=bool)
    mask[: len(vals) // 3] = True                         # a third of the quarter never fetched
    t = t.set_column(t.column_names.index("pr"), "pr", pa.array(vals, mask=mask))
    pq.write_table(t, p)
    weather._forget_quarter()

    ok, detail = weather._check_weather_variables()
    assert ok is False and "pr" in detail


def test_quarter_start_check_fails_on_a_deleted_leading_month(weather_store):
    """THE JULY 2026 CASE, exactly. `compact` rebuilt a quarter from its pending months alone, so a month
    that was complete -- and therefore skipped, and therefore not pending -- was written out of existence.
    The lattice check cannot see it: whole days removed leave a valid smaller lattice."""
    _, write = weather_store
    write(2020, 1, {1: 31, 2: 29, 3: 31})
    write(2020, 2, {5: 31, 6: 30})                        # April deleted, exactly as July was

    ok, detail = weather._check_weather_quarter_starts()
    assert ok is False and "2020Q2" in detail and "2020-04-01" in detail

    lattice_ok, _ = weather._check_weather_lattice()
    assert lattice_ok is True, "the lattice check is not expected to catch a whole-month deletion"


def test_lattice_check_fails_when_rows_are_dropped(weather_store):
    """A partial row loss breaks divisibility without moving the span -- the case the start check misses."""
    import pyarrow.parquet as pq

    _, write = weather_store
    p = write(2020, 1, {1: 31, 2: 29, 3: 31})
    t = pq.read_table(p)
    pq.write_table(t.slice(0, t.num_rows - 3), p)         # three rows vanish
    weather._forget_quarter()

    ok, detail = weather._check_weather_lattice()
    assert ok is False and "2020Q1" in detail


def test_checks_skip_rather_than_fail_on_an_empty_store(weather_store):
    """A missing store is a fact for `run` to judge, never a verification failure."""
    for fn in (weather._check_weather_variables, weather._check_weather_lattice):
        ok, _ = fn()
        assert ok is None
