import pandas as pd
import pytest

from data.build.acquire import discovery


class TestWindowScreen:
    def _frame(self, rows):
        base = dict(source="USGS", site_type_raw="Stream", channels_declared="discharge",
                    canary=False, declared_begin=pd.NA, declared_end=pd.NA)
        return pd.DataFrame([{**base, **r} for r in rows])

    def test_screen_semantics(self):
        a = self._frame([
            dict(declared_begin="2010-01-01", declared_end="2020-01-01"),   # in window: fetch
            dict(declared_begin="1950-01-01", declared_end="1990-06-01"),   # ended before 2008: skip
            dict(declared_begin="1970-01-01", declared_end="2012-01-01"),   # overlaps: fetch
            dict(),                                                          # declares channels, NO dates: skip
            dict(source="IWQIS", channels_declared=pd.NA),                   # declares nothing: NEVER skipped
            dict(declared_begin="1950-01-01", declared_end="1990-06-01", canary=True),  # canary punches through
        ])
        got = discovery._fetch_skip(a)
        assert pd.isna(got[0]) and pd.isna(got[2]) and pd.isna(got[4]) and pd.isna(got[5])
        assert got[1] == discovery.ENDS_BEFORE_WINDOW
        assert got[3] == discovery.NO_SERIES

    def test_no_upper_bound(self):
        # A declaration beginning after today is a data-entry error or a pre-registration, never a
        # reason to skip -- and any upper test against the current year starts eating real stations
        # as the calendar advances.
        a = self._frame([dict(declared_begin="2035-01-01", declared_end="2036-01-01")])
        assert pd.isna(discovery._fetch_skip(a)[0])

    def test_non_surface_sites_are_fetched(self):
        # Wells, springs, atmosphere: collected like everything else. The material rule lives at the
        # identity gate, not in fetch scoping -- there is no non_surface skip in this build.
        a = self._frame([
            dict(site_type_raw="Well", channels_declared="gw_level",
                 declared_begin="2010-01-01", declared_end="2020-01-01"),
            dict(site_type_raw="Atmosphere", channels_declared="precip",
                 declared_begin="2010-01-01", declared_end="2020-01-01"),
        ])
        got = discovery._fetch_skip(a)
        assert pd.isna(got[0]) and pd.isna(got[1])

    def test_spans_only_screen_declaring_sources(self):
        # IWQIS/MPCA declare no spans; their rows are never screened even with (carried) dates present.
        a = self._frame([dict(source="IWQIS", declared_begin="1950-01-01", declared_end="1990-01-01"),
                         dict(source="MPCA", declared_begin="1950-01-01", declared_end="1990-01-01")])
        got = discovery._fetch_skip(a)
        assert pd.isna(got[0]) and pd.isna(got[1])


class TestSeedWriteSemantics:
    def test_a1_updates_in_place_never_defines_rows(self, tmp_path, monkeypatch):
        """The seed pass discovers a subset; it must not gut the censused table nor blank derived blocks."""
        import data.build.config as config
        from data.build import contracts
        from data.build.acquire import seed

        prev = contracts.conform_sensors(pd.DataFrame([
            dict(site_uid="USGS:A", source="USGS", lat=42.0, lon=-93.0, comid=111,
                 snap_status="ok", site_type="stream"),
            dict(site_uid="USGS:B", source="USGS", lat=42.1, lon=-93.1, comid=222,
                 snap_status="ok", site_type="stream"),
        ]))
        p = tmp_path / "sensors.parquet"
        monkeypatch.setattr(config, "SENSORS_PATH", p)
        monkeypatch.setattr(config, "ensure_dirs", lambda: None)
        prev.to_parquet(p)

        fresh = contracts.conform_sensors(pd.DataFrame([
            dict(site_uid="USGS:A", source="USGS", lat=42.0, lon=-93.0, station_name="renamed"),
            dict(site_uid="USGS:C", source="USGS", lat=42.2, lon=-93.2),
        ]))
        fresh["aoe_seed"] = True
        monkeypatch.setattr(seed.discovery, "run", lambda **k: fresh)
        monkeypatch.setattr(seed, "mark_seeds", lambda s: s)
        monkeypatch.setattr(seed.seed_basins, "fetch_seed_basins", lambda s, force=False: pd.DataFrame())

        out = seed.main().set_index("site_uid")
        assert set(out.index) == {"USGS:A", "USGS:B", "USGS:C"}, "no censused row may be lost"
        assert out.loc["USGS:B", "comid"] == 222, "underived rows pass through untouched"
        assert out.loc["USGS:A", "comid"] == 111, "the derived block survives a discovery refresh"
        assert out.loc["USGS:A", "station_name"] == "renamed", "the discovery block does refresh"


def test_text_dp():
    from data.build.acquire.sources import base

    got = base.text_dp(["43.21", "-92.7503", "42", "43.210", "", "abc", None])
    assert list(got[:5]) == [2, 4, 0, 3, pd.NA][:5] or (
        got.iloc[0] == 2 and got.iloc[1] == 4 and got.iloc[2] == 0 and got.iloc[3] == 3
        and pd.isna(got.iloc[4]))
    assert pd.isna(got.iloc[5]) and pd.isna(got.iloc[6])
