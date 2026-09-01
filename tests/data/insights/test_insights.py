"""The insights audit: exposure accounting, the theme registry, and the geometry test."""

import pytest

from data.insights import exposure, geometry, report, sources


def test_every_source_belongs_to_a_declared_theme():
    """A source with no theme is invisible to the completeness map, which defeats the map's purpose."""
    declared = set(report.themes())
    missing = sorted({r.theme for r in sources.table().itertuples()} - declared)
    assert not missing, f"sources reference undeclared themes: {missing}"


def test_themes_are_domain_driven_not_data_driven():
    """Themes enumerated from our holdings can never reveal a gap. Some must be thin BY DESIGN."""
    t = report.themes()
    thin = [k for k, v in t.items() if len(v.get("sources", [])) <= 1]
    assert thin, "no thin themes -- the registry is describing our data rather than the domain"
    assert "legacy_nitrogen" in t and "instream_processing" in t


def test_every_theme_carries_a_description():
    for name, spec in report.themes().items():
        assert spec.get("description", "").strip(), f"{name} has no description"


def test_exposed_by_names_only_real_access_functions():
    """A stale entry would silently mark a store reachable through a function that no longer exists."""
    from data.access import api

    for fn in exposure.EXPOSED_BY:
        assert hasattr(api, fn), f"EXPOSED_BY names {fn}, which api does not define"


def test_resolution_test_separates_fine_from_coarse_products():
    """The decisive tier-2 test: cells bigger than a catchment cannot vary within it."""
    import pandas as pd

    areas = pd.Series([1.38] * 100)          # the real median
    d = geometry.resolution_table(areas).set_index("product")
    assert d.loc["CDL (30 m)", "verdict"] == "resolves within catchments"
    assert d.loc["NLDAS-2 (1/8 deg)", "verdict"] == "catchment-level constant"
    assert d.loc["gridMET (1/24 deg)", "frac_smaller_than_cell"] == 1.0


def test_do_not_double_count_pairs_reference_known_sources():
    """The list is only actionable if its members are nameable; a bare prefix must still resolve."""
    known = {r.source for r in sources.table().itertuples()}
    for a, b, why in sources.DO_NOT_DOUBLE_COUNT:
        assert a.split(":")[0] in known, f"unknown source {a}"
        assert why.strip()


def test_report_renders_without_a_store():
    """The report must degrade rather than crash when a store is absent -- it is run on partial builds."""
    text = report.render()
    assert "# Data insights" in text and "Tier 0" in text and "Do not double-count" in text


class TestThreeStateExposure:
    """`substores()` must separate "no reader" from "reachable by nobody".

    One false finding makes the true ones easy to dismiss, and this tier's whole value is that its rows are worth acting on. `acquired/facilities` has no access reader and is read by `derive/structures` whose product is published — calling that a gap would send someone to fix working code.
    """

    def test_status_has_exactly_three_values(self):
        from data.insights import exposure

        g = exposure.substores()
        assert set(g.status) <= {"reachable", "intermediate (consumed by the build)", "ORPHANED"}

    def test_a_substore_with_a_reader_is_reachable(self):
        from data.insights import exposure

        g = exposure.substores().set_index("substore")
        assert g.loc["network", "status"] == "reachable"

    def test_a_build_consumed_substore_is_not_called_orphaned(self):
        """The false positive this distinction exists to remove."""
        from data.insights import exposure

        g = exposure.substores().set_index("substore")
        if "facilities" in g.index:
            assert g.loc["facilities", "status"] != "ORPHANED"

    def test_the_consumption_scan_ignores_the_retired_predecessor(self):
        """`src/` is excluded on purpose — data referenced ONLY there is exactly the finding."""
        import pathlib

        from data.insights import exposure

        src = pathlib.Path(exposure.__file__).resolve().parents[2] / "src"
        if src.exists():
            names = exposure._consumed_substores()
            # authority_basins appears only under src/, so it must NOT count as consumed
            assert "authority_basins" not in names
