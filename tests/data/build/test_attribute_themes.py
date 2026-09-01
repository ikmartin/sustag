"""The attribute-theme declaration: every theme the catalogue offers is a decision, not an accident."""

import json

import pytest

from data.build.acquire import attributes as A


@pytest.fixture
def roster(tmp_path, monkeypatch):
    """Point the cached catalogue roster at a temp file and return a writer for it."""
    from data.build import config

    monkeypatch.setattr(config, "ACQ_ATTRIBUTES", tmp_path)

    def write(themes):
        (tmp_path / "wieczorek_catalogue_themes.json").write_text(json.dumps(sorted(themes)))

    return write


def test_passes_when_every_theme_is_accounted_for(roster):
    roster(list(A.WIECZOREK_THEMES) + list(A.WIECZOREK_THEMES_DECLINED))
    ok, detail = A._check_wieczorek_themes_declared()
    assert ok is True, detail


def test_fails_on_a_theme_that_is_neither_pulled_nor_declined(roster):
    """THE ACTUAL HISTORY: `Hydrologic` and `Regions` -- 437 attributes including the whole flow-pathway
    partition -- sat unpulled because nothing compared the tuple against the catalogue."""
    roster(list(A.WIECZOREK_THEMES) + list(A.WIECZOREK_THEMES_DECLINED) + ["Some New Theme"])
    ok, detail = A._check_wieczorek_themes_declared()
    assert ok is False and "Some New Theme" in detail


def test_fails_when_a_theme_is_both_pulled_and_declined(monkeypatch, roster):
    monkeypatch.setattr(A, "WIECZOREK_THEMES_DECLINED",
                        {**A.WIECZOREK_THEMES_DECLINED, "Soils": "contradiction"})
    roster(list(A.WIECZOREK_THEMES) + list(A.WIECZOREK_THEMES_DECLINED))
    ok, detail = A._check_wieczorek_themes_declared()
    assert ok is False and "both pulled and declined" in detail


def test_fails_when_a_decline_carries_no_reason(monkeypatch, roster):
    """A blank reason is a silent drop wearing a decision's clothes."""
    monkeypatch.setattr(A, "WIECZOREK_THEMES_DECLINED", {**A.WIECZOREK_THEMES_DECLINED, "Climate": "  "})
    roster(list(A.WIECZOREK_THEMES) + list(A.WIECZOREK_THEMES_DECLINED))
    ok, detail = A._check_wieczorek_themes_declared()
    assert ok is False and "no reason" in detail


def test_skips_rather_than_fails_when_the_roster_was_never_cached(roster, tmp_path):
    (tmp_path / "wieczorek_catalogue_themes.json").unlink(missing_ok=True)
    ok, _ = A._check_wieczorek_themes_declared()
    assert ok is None


def test_the_two_restored_themes_are_present():
    """`Hydrologic` is not `Hydrologic Modifications`; conflating them is how the gap survived."""
    assert "Hydrologic" in A.WIECZOREK_THEMES
    assert "Regions" in A.WIECZOREK_THEMES
    assert "Hydrologic Modifications" in A.WIECZOREK_THEMES
