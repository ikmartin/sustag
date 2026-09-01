"""The gTREND component set: twelve products, three nesting relations, and the closure that checks them.

`Surplus` is the mass balance's SUM. Summation destroys composition -- two basins with identical surplus can be a high-fertilizer corn basin and a moderate-manure pasture basin, whose nitrogen reaches a stream on entirely different schedules -- so the components are ingested and the published totals become checks.
"""

import pandas as pd
import pytest

from data.build.derive import products as P


def _row(product, kg, comid=1, year=2010):
    return dict(comid=comid, year=year, product=product, kg=kg,
                kg_per_ha=1.0, n_px=kg / P.HA_PER_PIXEL)


def test_every_component_directory_is_declared():
    """The set has grown twice during planning; a directory nobody declared is silently not ingested."""
    from data.build import config

    on_disk = {p.name for p in (config.RAW / "gTREND").iterdir()
               if p.is_dir() and p.name not in P.GTREND_NON_RASTER}
    declared = {d.split("/", 1)[1] for d, _ in P.NUTRIENT_SOURCES.values()}
    assert on_disk == declared, f"undeclared: {sorted(on_disk - declared)}"


def test_the_nesting_relations_are_declared_as_closures():
    totals = {t for t, _ in P.NUTRIENT_CLOSURES}
    assert totals == {"fixation_ag", "uptake_ag"}


def test_closure_passes_when_parts_sum_to_their_total():
    d = pd.DataFrame([_row("fixation_ag", 10.0), _row("fixation_cropland", 6.0),
                      _row("fixation_pasture", 4.0)])
    ok, detail = P._nutrients_closure(d, None)
    assert ok is True and "fixation_ag" in detail


def test_closure_fails_when_a_component_is_wrong():
    """This is the point of ingesting the totals: they catch OUR extraction, since the rasters themselves
    were verified to agree to 0.01 (the storage precision)."""
    d = pd.DataFrame([_row("fixation_ag", 10.0), _row("fixation_cropland", 6.0),
                      _row("fixation_pasture", 99.0)])
    ok, detail = P._nutrients_closure(d, None)
    assert ok is False and "off" in detail


def test_closure_is_skipped_when_a_total_is_absent():
    """A product subset must not fail a check it has no data for."""
    d = pd.DataFrame([_row("fixation_cropland", 6.0), _row("fixation_pasture", 4.0)])
    ok, _ = P._nutrients_closure(d, None)
    assert ok is True


def test_the_kg_identity_still_gates_first():
    d = pd.DataFrame([_row("surplus", 10.0)])
    d.loc[0, "kg"] = 999.0
    ok, detail = P._nutrients_closure(d, None)
    assert ok is False and "kg != mean x area" in detail


def test_huc8_reference_reads_the_authors_own_aggregation():
    """The one external ground truth available to the covariate layer."""
    ref = P.huc8_surplus_reference(years=[2010])
    if ref is None:
        pytest.skip("gTREND HUC8 release not on disk")
    assert len(ref) > 2000 and set(ref.columns) >= {"huc8", "year", "kg_per_ha"}
    assert ref.year.unique().tolist() == [2010]
