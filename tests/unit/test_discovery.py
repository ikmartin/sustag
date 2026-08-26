import numpy as np
import pandas as pd
import pytest

from src.build3.acquire import discovery


def _frame(**over):
    base = dict(site_uid=["USGS:1", "USGS:2", "USGS:3"],
                site_type_raw=["Well", "Well", "Stream"],
                channels_declared=["discharge", "nitrate+discharge", None],
                canary=[False, False, False])
    base.update(over)
    return pd.DataFrame(base)


def test_fetch_skip_needs_both_halves():
    r = discovery._fetch_skip(_frame())
    # well without nitrate -> skipped; well WITH nitrate -> fetched; unknown declaration -> fetched
    assert list(r) == ["non_surface_no_nitrate", None, None]


def test_absence_of_evidence_never_skips():
    r = discovery._fetch_skip(_frame(channels_declared=[None, None, None]))
    assert list(r) == [None, None, None]


def test_canary_punches_through_scope_filters():
    r = discovery._fetch_skip(_frame(canary=[True, False, False]))
    assert list(r) == [None, None, None]   # the well canary is fetched despite the rule


def test_free_text_spring_does_not_match_code_prefix():
    # An IWQIS 'Spring at ...' upper-cases into SP; the rule must only fire on declared-channel sources.
    f = _frame(site_type_raw=["Spring at Elkader", "Well", "Stream"],
               channels_declared=[None, "discharge", None])
    r = discovery._fetch_skip(f)
    assert r.iloc[0] is None       # unknown channels: not skipped, whatever the words say
    assert r.iloc[1] == "non_surface_no_nitrate"


class TestSeedWriteSemantics:
    def test_a1_updates_in_place_never_defines_rows(self, tmp_path, monkeypatch):
        """The seed pass discovers a subset; it must not gut the censused table nor blank derived blocks."""
        import src.build3.config as config
        from src.build3 import contracts
        from src.build3.acquire import seed

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


class TestDeclaredSpanScreen:
    def _frame(self, rows):
        base = dict(source="USGS", site_type_raw="Stream", channels_declared="discharge",
                    canary=False, declared_begin=pd.NA, declared_end=pd.NA)
        return pd.DataFrame([{**base, **r} for r in rows])

    def test_screen_semantics(self):
        from src.build3.acquire.discovery import _fetch_skip

        a = self._frame([
            dict(declared_begin="2010-01-01", declared_end="2020-01-01"),   # in window: fetch
            dict(declared_begin="1950-01-01", declared_end="1990-06-01"),   # ended before 2008: skip
            dict(declared_begin="1970-01-01", declared_end="2012-01-01"),   # overlaps: fetch
            dict(),                                                          # declares channels, NO dates: skip
            dict(source="IWQIS", channels_declared=pd.NA),                   # declares nothing: NEVER skipped
            dict(declared_begin="1950-01-01", declared_end="1990-06-01", canary=True),  # canary punches through
        ])
        got = _fetch_skip(a)
        assert pd.isna(got[0]) and pd.isna(got[2]) and pd.isna(got[4]) and pd.isna(got[5])
        assert got[1] == "declared_span_outside_window"
        assert got[3] == "no_declared_series"
