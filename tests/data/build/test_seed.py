"""AOE seeding: which registrations get a vote on how far the region reaches."""

import pandas as pd

from data.build.acquire import seed


def _row(source, channels, begin="2010-01-01", end="2020-01-01"):
    return dict(source=source, channels_declared=channels,
                nitrate_begin=pd.Timestamp(begin, tz="UTC") if begin else pd.NaT,
                nitrate_end=pd.Timestamp(end, tz="UTC") if end else pd.NaT)


def test_in_situ_nitrate_seeds():
    d = pd.DataFrame([_row("USGS", "nitrate+discharge"), _row("IWQIS", "nitrate")])
    assert seed.mark_seeds(d).aoe_seed.all()


def test_discrete_samples_never_seed():
    """THE DESIGN RULE: the AOE is seeded on in-situ nitrate alone. Grab samples register and publish;
    their basins are not wanted and must not enlarge the region every raster is clipped to."""
    d = pd.DataFrame([_row("WQX", "nitrate_discrete", begin=None, end=None)])
    out = seed.mark_seeds(d)
    assert not out.aoe_seed.any()
    assert out.seed_exclusion_reason.iloc[0] == seed.NOT_IN_SITU


def test_a_discrete_source_cannot_seed_by_declaring_a_span():
    """The exclusion must be by CHANNEL, not incidental to a null span column -- otherwise a source that
    happens to populate `nitrate_begin` inherits a vote it was never meant to have."""
    d = pd.DataFrame([_row("WQX", "nitrate_discrete")])
    out = seed.mark_seeds(d)
    assert not out.aoe_seed.any() and out.seed_exclusion_reason.iloc[0] == seed.NOT_IN_SITU


def test_non_nitrate_registrations_are_untouched_by_the_channel_rule():
    """Seeding is not a cohort filter, and the channel rule fires only on the nitrate family.

    A discharge-only registration must never be excluded as NOT_IN_SITU -- whatever else decides its fate.
    Note the comparison is via `str()`: the reason is a pandas nullable string, and `pd.NA != "x"` is `<NA>`,
    which raises rather than passing when asserted directly.
    """
    d = pd.DataFrame([_row("USGS", "discharge", begin=None, end=None)])
    out = seed.mark_seeds(d)
    assert str(out.seed_exclusion_reason.iloc[0]) != seed.NOT_IN_SITU


def test_short_spans_still_excluded_on_evidence():
    d = pd.DataFrame([_row("USGS", "nitrate", begin="2010-01-01", end="2010-01-01")])
    out = seed.mark_seeds(d, min_span_days=5)
    assert out.seed_exclusion_reason.iloc[0] == seed.SHORT_SPAN
