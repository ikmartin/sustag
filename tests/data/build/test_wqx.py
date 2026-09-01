"""WQX: the truncation detector, and the constraint that these are never sensors.

Both guard silent failures. A truncated payload parses as perfectly good CSV — Alabama returned 3.7, 9.5 and 14.8 MB of valid rows before eventually completing at 16.7 — so without `_complete` the store would have quietly held a third of a state. And a WQX identifier reaching the sensors table would put 55,210 registrations into the identity machinery and 42,209 pairs into a queue built for hundreds.
"""

import pandas as pd
import pytest

from data.build.acquire import wqx


class TestTruncationDetection:
    def test_a_whole_payload_passes(self):
        assert wqx._complete("a,b,c\n1,2,3\n")

    def test_a_row_cut_mid_record_is_caught(self):
        """The real failure mode: valid CSV that simply stops early."""
        assert not wqx._complete("a,b,c\n1,2,3\n4,")

    def test_a_quoted_comma_is_not_mistaken_for_truncation(self):
        """Why the check parses instead of counting separators — a comma tolerance loose enough to
        survive quoting is loose enough to accept a short row."""
        assert wqx._complete('a,b,c\n1,"x,y",3\n')

    def test_an_error_body_is_caught_even_though_http_said_200(self):
        assert not wqx._complete("ERROR: INCOMPLETE DATA\na,b,c\n1,2,3\n")

    def test_a_header_with_no_rows_is_not_complete(self):
        assert not wqx._complete("a,b,c\n")

    def test_empty_is_not_complete(self):
        assert not wqx._complete("")


class TestNotASensor:
    def test_the_check_skips_when_nothing_is_collected(self, tmp_path, monkeypatch):
        from data.build import config

        monkeypatch.setattr(config, "ACQUIRED", tmp_path)
        assert wqx._check_wqx_is_not_a_sensor()[0] is None

    def test_the_check_fails_if_a_wqx_source_reaches_the_sensors_table(self, tmp_path, monkeypatch):
        """The constraint this source is collected under, asserted rather than assumed."""
        import pyarrow.parquet as pq

        from data.build import config

        monkeypatch.setattr(config, "ACQUIRED", tmp_path)
        monkeypatch.setattr(config, "PUBLISHED", tmp_path)
        (tmp_path / "wqx").mkdir()
        pd.DataFrame({"x": [1]}).to_parquet(tmp_path / "wqx" / "nitrate_IA.parquet", index=False)
        pd.DataFrame({"source": ["USGS", "WQX"]}).to_parquet(tmp_path / "sensors.parquet", index=False)
        ok, detail = wqx._check_wqx_is_not_a_sensor()
        assert ok is False and "LEAKED" in detail

    def test_the_check_passes_when_sensors_are_clean(self, tmp_path, monkeypatch):
        from data.build import config

        monkeypatch.setattr(config, "ACQUIRED", tmp_path)
        monkeypatch.setattr(config, "PUBLISHED", tmp_path)
        (tmp_path / "wqx").mkdir()
        pd.DataFrame({"x": [1, 2]}).to_parquet(tmp_path / "wqx" / "nitrate_IA.parquet", index=False)
        pd.DataFrame({"source": ["USGS", "IWQIS"]}).to_parquet(tmp_path / "sensors.parquet", index=False)
        assert wqx._check_wqx_is_not_a_sensor()[0] is True


class TestCharacteristics:
    def test_only_nitrate_forms_are_requested(self):
        """Kjeldahl, ammonia and total N are different quantities and no factor makes them nitrate."""
        joined = " ".join(wqx.CHARACTERISTICS).lower()
        assert "nitrate" in joined
        for refused in ("kjeldahl", "ammonia", "total nitrogen"):
            assert refused not in joined

    def test_every_aoe_state_has_a_fips_code(self):
        assert len(wqx.STATE_FIPS) == 41
        assert all(len(v) == 2 and v.isdigit() for v in wqx.STATE_FIPS.values())


class TestCurrency:
    """`fetch_state` must ask whether the state was fetched RECENTLY, not whether a file exists.

    The request window runs to today, so a file written in August holds samples to August. A guard testing existence skips it on every later run and the state freezes at its first fetch — silently, because a frozen file is a perfectly valid file.
    """

    def _write(self, tmp_path, latest):
        p = tmp_path / "nitrate_IA.parquet"
        pd.DataFrame({"Activity_StartDate": [latest]}).to_parquet(p, index=False)
        return p

    def test_a_recent_file_is_current(self, tmp_path):
        recent = (pd.Timestamp.today() - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        assert wqx._current(self._write(tmp_path, recent))

    def test_a_frozen_file_is_not_current(self, tmp_path):
        """The actual failure: a file that stopped a year ago still exists and still parses."""
        stale = (pd.Timestamp.today() - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
        assert not wqx._current(self._write(tmp_path, stale))

    def test_an_unreadable_file_is_not_current(self, tmp_path):
        p = tmp_path / "broken.parquet"
        p.write_bytes(b"not parquet")
        assert not wqx._current(p)

    def test_all_null_dates_are_not_current(self, tmp_path):
        p = tmp_path / "nitrate_XX.parquet"
        pd.DataFrame({"Activity_StartDate": [None, None]}).to_parquet(p, index=False)
        assert not wqx._current(p)
