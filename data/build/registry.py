"""The channel registry -- the single extension point for everything a sensor can measure.

ONE PLACE, BECAUSE THREE SOURCES NAME THE SAME QUANTITY THREE WAYS. USGS keys columns by parameter code, IWQIS by ad-hoc English names, MPCA by integer variable ids. Nothing else in the build may contain a literal pcode or a source column name. Adding a channel is: add the entry -> the schema generates the per-channel columns -> the census registers advertisers next pass -> canonicalisation produces it -> published records gain it. The two genuinely manual costs are declaring/verifying units and a bounded USGS backfill.

FOUR INDEPENDENT AXES ON A CHANNEL, never conflated:
- `fetch_priority` (1|2) -- which channels the census asks for first. A fetch-cost fact.
- `scrutiny` (high|standard) -- the stewardship tier: identity and quality decisions touching a high-tier record gate on a human; standard-tier decisions take the rule's answer, recorded. A function of scarcity x consequence, not of any model's target. Nitrate sits alone in the high tier.
- `depth` (full|canary|off) -- collection depth. Full is collected everywhere; canary only at roster probes, keeping the path exercised at negligible cost; off is named so absence is a decision.
- `discriminating` (bool) -- whether agreement on this channel is evidence of a shared INSTRUMENT rather than a shared climate. Water temperature, stage, pH, DO and turbidity are storm- or season-driven basin-wide: two sensors 1,878 m apart agree on water temperature at r = 0.9936, and neither is the other.

SENTINELS ARE PER-SOURCE FACTS IN NATIVE UNITS, matched exactly, per native column, BEFORE any combine and BEFORE any scale factor. Before scaling because -999999 cfs becomes -28,316.82 m3/s, a number that looks like nothing in particular; before combining because a -999999 averaged with a real value in a two-column channel is no longer exactly -999999 and is uncatchable afterward. A sentinel becomes a missing value, counted, never clamped. The IWQIS coordinate fill (-99.0 in both coordinates) is a DISCOVERY-layer sentinel handled by the adapter, not a value sentinel here.

UNITS ARE A THREE-STATE FACT: `declared` (the source documents the unit -- NWIS parameter table, IWQIS params.csv), `assumed` (our guess, visible), `verified` (settled by cross-source measurement at co-located instruments -- the only state that closes the question). MPCA nitrate is the exemplar: verified over 56,125 matching timestamps at median ratio 1.0000 against 4.4269 for as-NO3. Turbidity is why the states matter: FNU, NTU, FNRU and FBU are different optical geometries, not spellings, so `turbidity_unknown` holds IWQIS and MPCA apart from both named channels until MEASUREMENT -- not declaration -- promotes it.

RANGES ENCODE INSTRUMENT-IMPOSSIBLE, NOT SITE-IMPLAUSIBLE. `lo`/`hi` bound what an instrument can truthfully report -- they catch a failed sensor, never trim a distribution, and never null real water measured truthfully at an unusual site (a hot spring at 59.8 degC and road-salt runoff at 175,000 uS/cm are data). Site-implausible is a per-sensor judgment and lives in the quality ledger's `max_threshold`.

STATISTICS ARE COLLECTED, NOT PREDICTED. `stats="full"` stores the seven daily primitives; `stats="mean"` only the mean. Discharge and gage height are `mean` and drive ~93% of the fetch, so the storage rule and the fetch tier agree by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ft3/s -> m3/s, ft -> m, in -> mm. Exact by definition of the international foot/inch.
_CFS_TO_CMS = 0.028316846592
_FT_TO_M = 0.3048
_IN_TO_MM = 25.4

UNITS_STATES = ("declared", "assumed", "verified")

# Fill values each source writes where a measurement should be, in the source's NATIVE units. -999999 is
# the cross-vendor convention (also seen scaled: -999999 cfs, -1e6 ft); MPCA additionally uses the 999.99
# rail-style fill (14,631 nitrate + 49,531 turbidity samples at one malfunctioning station) and a
# five-nines -99999 (10,439 readings). Matched as exact equality; the control for 999.99: across 17M
# turbidity observations at all other stations an exact 999.99 occurs four times.
SENTINELS: dict[str, tuple[float, ...]] = {
    "USGS": (-999999.0,),
    "IWQIS": (-999999.0,),
    "MPCA": (-999999.0, -99999.0, 999.99),
}


@dataclass(frozen=True)
class SourceMap:
    """How one source spells one channel.

    `cols` is pcodes for USGS and column names for IWQIS/MPCA. Several entries mean the source files one quantity under more than one code; whichever is present wins, combined row-wise. `units` is the three-state verification status of `unit`; `scale` converts to the channel's canonical unit.
    """

    cols: tuple[str, ...]
    unit: str
    scale: float = 1.0
    units: str = "declared"
    note: str = ""

    def __post_init__(self):
        if self.units not in UNITS_STATES:
            raise ValueError(f"units status {self.units!r} not in {UNITS_STATES}")


@dataclass(frozen=True)
class Channel:
    """One canonical variable: units, daily summary, physical range, and its four axes.

    `lo`/`hi` bound a physically possible reading -- see the module docstring's range doctrine. `clamp_negatives` distinguishes a concentration (a small negative in [lo, 0) is a real sub-detection measurement, becomes 0) from a level or flow (negative is legitimate: tidal reversal, datum-relative stage, artesian head). A clamping channel MUST declare lo < 0 -- with lo = 0 the below-lo null fires first and the clamp is dead code, deleting sub-detection readings instead of keeping them; the import-time closure below enforces it.

    `scale_kind`/`agree_tol` say how two measurements of the same water are compared: a RATIO scale has a true zero so a unit error is a constant multiplier judged on the median ratio; an INTERVAL scale (degC, pH) has an arbitrary origin so the comparison is the difference against `agree_tol`. `compare_stat` is the day statistic identity evidence uses -- max for nitrate because the merge thresholds were calibrated on it. `merge` is the winner-per-span policy when two bundled sensors overlap on this channel: each published day comes from exactly ONE contributor, chosen by the policy, provenance per (channel, span).
    """

    unit: str
    fetch_priority: int                          # 1 | 2 -- fetch ordering, nothing else
    stats: str                                   # "full" | "mean"
    lo: float | None
    hi: float | None
    clamp_negatives: bool
    sources: dict[str, SourceMap] = field(default_factory=dict)
    scale_kind: str = "ratio"                    # "ratio" | "interval"
    agree_tol: float = 0.0                       # interval scales only
    combine: str = "mean"                        # several native columns -> one series
    compare_stat: str = "mean"                   # day statistic identity evidence compares on
    discriminating: bool = True                  # agreement = shared instrument, not shared climate
    scrutiny: str = "standard"                   # "high" | "standard"
    depth: str = "full"                          # "full" | "canary" | "off"
    merge: str = "longest"                       # winner-per-span policy on member overlap
    native: bool = False                         # is a sub-daily fetch worth paying for
    note: str = ""

    def __post_init__(self):
        if self.scrutiny not in ("high", "standard"):
            raise ValueError(f"scrutiny {self.scrutiny!r}")
        if self.depth not in ("full", "canary", "off"):
            raise ValueError(f"depth {self.depth!r}")
        if self.stats not in ("full", "mean"):
            raise ValueError(f"stats {self.stats!r}")
        if self.scale_kind not in ("ratio", "interval"):
            raise ValueError(f"scale_kind {self.scale_kind!r}")
        if self.clamp_negatives and (self.lo is None or self.lo >= 0):
            raise ValueError(f"clamp_negatives with lo={self.lo} can never fire: the below-lo null runs first, so a clamping channel must declare a negative sub-detection floor")


# The channels. Bounds follow a full-native-store audit (1.76B values, 2026-08-25); each hi/lo names what it catches.
CHANNELS: dict[str, Channel] = {
    "nitrate": Channel(
        unit="mg/L as N", fetch_priority=1, stats="full", lo=-1.0, hi=100.0, clamp_negatives=True,
        native=True, combine="max", compare_stat="max", scrutiny="high",
        note="the primary objective, and the sole high-scrutiny channel: scarce, health-relevant, the product's headline. hi=100 is a PHYSICAL bound: the cohort's highest real readings sit at the ~44 instrument rail with a verified tail to ~55, and ~10,700 real readings live in (50, 100] -- anything there stays data; what the bound nulls is fill and failure, and the 999.99 fill is ALSO a sentinel, so the range is the second net. lo=-1: sub-detection noise clamps to 0; below -1 is a failed instrument (one station held -12.3/-12.4 for 10,697 samples). Combines by MAX across its three pcodes, the conservative rule for a pollutant. compare_stat is max because the merge thresholds were calibrated on the daily-max series and changing it would silently re-litigate every decision on record.",
        sources={
            # 99133 alone returns zero Nebraska sites; which code a network files under is local convention. 83554 is a LOAD and stays excluded.
            "USGS": SourceMap(("99133", "99137", "00630"), "mg/l as N"),
            "IWQIS": SourceMap(("nitrate_con",), "mg/L as N", units="declared",
                               note="params.csv: 'Nitrate + Nitrite as N', mg/L (instrument nitratax); matches USGS at co-located sites but not yet measured to the verified bar"),
            # Settled by measurement, not documentation: over 56,125 matching timestamps at a co-located USGS gauge the median ratio is 1.0000 (4.4269 for as-NO3).
            "MPCA": SourceMap(("nitrate",), "mg/L as N", units="verified"),
        }),
    "discharge": Channel(
        unit="m3/s", fetch_priority=1, stats="mean", lo=-10_000.0, hi=50_000.0, clamp_negatives=False,
        note="the covariate that matters most -- it sets the dilution-vs-flushing regime. ASYMMETRIC BOUNDS because huge positive flow is real and huge negative is not: the Mississippi peaks near 35,000 m3/s so hi=50,000 is loose, while no tidal reversal approaches -10,000 (a 353,000 cfs backflow). Measured: the bounds catch 36,473 values of which 36,471 are the -999999 cfs sentinel (-28,316.82 canonically -- also on the sentinel list, the range is the second net) and 2 are isolated glitches; nothing real is touched.",
        sources={
            "USGS": SourceMap(("00060",), "ft3/s", _CFS_TO_CMS),
            "MPCA": SourceMap(("discharge",), "ft3/s", _CFS_TO_CMS, units="assumed",
                              note="US convention assumed; confirm against a co-located USGS gage"),
        }),
    "gage_height": Channel(
        unit="m", fetch_priority=1, stats="mean", lo=-1_000.0, hi=10_000.0, clamp_negatives=False,
        discriminating=False,
        note="datum-relative, so negative is normal (measured p1 = -0.02 m, real tail to -0.86). Real stage-datum values reach 687 m in-cohort so hi=10,000 is loose; the bounds catch exactly one value, 5,458 times -- the -1e6 ft fill (-304,799.7 m), which the sentinel list also names. Present at stage-only sites with no rating, which is what makes it add coverage discharge does not. Non-discriminating: two gauges on one river agree on stage because it is one river.",
        sources={
            "USGS": SourceMap(("00065",), "ft", _FT_TO_M),
            "MPCA": SourceMap(("level",), "ft", _FT_TO_M, units="assumed"),
        }),
    "water_temp": Channel(
        unit="degC", fetch_priority=1, stats="full", lo=-5.0, hi=60.0, clamp_negatives=False,
        scale_kind="interval", agree_tol=1.0, discriminating=False,
        note="the most ubiquitous sensor parameter; proxies seasonality and denitrification rate. lo=-5: flowing water cannot persist below (measured 0.01st percentile is -2.8); below it lies frozen or air-exposed-sensor territory and the -999999 fill. hi=60, NOT 45: a hot-spring station's genuine record runs 45-59.8 degC, and outside that one site exactly 5 of 240M values fall in (45, 60] -- 60 publishes real thermal water at the cost of nothing, per the instrument-impossible doctrine. Interval scale: two thermistors metres apart at different depths differ by a fraction of a degree, which as a RATIO near freezing reads as a large error and is not one. Non-discriminating: agreement at r=0.9936 from 1,878 m apart has been measured.",
        sources={
            "USGS": SourceMap(("00010",), "deg C"),
            # dts_temp_water is the same quantity from the nitrate sensor's own thermistor, present where temp_water is not.
            "IWQIS": SourceMap(("temp_water", "dts_temp_water"), "deg C", units="declared",
                               note="params.csv: Temperature (Water), degC"),
            "MPCA": SourceMap(("water_temp",), "deg C", units="assumed"),
        }),
    "spec_cond": Channel(
        unit="uS/cm@25C", fetch_priority=1, stats="full", lo=-5.0, hi=200_000.0, clamp_negatives=True,
        native=True,
        note="tracks the groundwater/baseflow versus event-water mixture; second only to discharge. hi=200,000 sits at saturated-brine territory: road-salt-impacted highway stations genuinely reach 177,000, so the former 100,000 ceiling was nulling 6,668 real readings. lo=-5 gives the sub-detection clamp room (48 readings at -1).",
        sources={
            "USGS": SourceMap(("00095",), "uS/cm @25C"),
            "IWQIS": SourceMap(("spec_cond", "spec_cond_v2"), "uS/cm", units="declared",
                               note="params.csv: uS/cm; the @25C compensation is assumed to match"),
            "MPCA": SourceMap(("spec_cond",), "uS/cm", units="assumed"),
        }),
    "turbidity_fnu": Channel(
        unit="FNU", fetch_priority=1, stats="full", lo=-5.0, hi=10_000.0, clamp_negatives=True,
        native=True, discriminating=False,
        note="63680 ONLY -- 800 of the 841 turbidity sites. 63681 (FNRU) and 63682 (FBU) are 3 sites each and a different optical geometry, so they are not folded in. Real turbidity reaches 6,940 in-cohort so hi=10,000 nulls only electronics garbage; lo=-5 clamps optical sub-detection noise (34,423 readings) to 0 instead of deleting it.",
        sources={"USGS": SourceMap(("63680",), "FNU")}),
    "turbidity_ntu": Channel(
        unit="NTU", fetch_priority=1, stats="full", lo=-5.0, hi=10_000.0, clamp_negatives=True,
        native=True, discriminating=False,
        note="63675, 35 sites. `00076`, the code usually named for legacy NTU, returns ZERO sites in the AOE, as do 61028, 82079, 63676-63679, 63683 and 99872. Bounds as turbidity_fnu.",
        sources={"USGS": SourceMap(("63675",), "NTU")}),
    "turbidity_unknown": Channel(
        unit="unknown", fetch_priority=1, stats="full", lo=-5.0, hi=10_000.0, clamp_negatives=True,
        native=True, discriminating=False,
        note="IWQIS and MPCA turbidity, held apart from FNU and NTU until a co-located MEASUREMENT settles the optical geometry -- declaration is not promotion. IWQIS params.csv declares NTU on a dts12, which is recorded and still waits for the measured bar. The MPCA 999.99 fill is a SENTINEL, deliberately not a range problem: real turbidity reaches 4,441 here, so no defensible ceiling sits below 1,000, and without the sentinel rule 49,531 fill values (34% of one station's record) would publish as water.",
        sources={
            # turbi_mean/max/min/med/var/bes are six STATISTICS of one sensor, not six variables.
            "IWQIS": SourceMap(("turbi_mean", "turbi_mean_v2"), "NTU", units="declared",
                               note="params.csv: NTU, instrument dts12"),
            "MPCA": SourceMap(("turbidity",), "unknown", units="assumed"),
        }),
    "diss_oxy": Channel(
        unit="mg/L", fetch_priority=2, stats="full", lo=-1.0, hi=30.0, clamp_negatives=True,
        native=True, discriminating=False,
        note="the diel amplitude carries the biological activity that also drives nitrate uptake, which is why it is stats='full' despite fetch priority 2. hi=30: real supersaturation in eutrophic streams tops out near 20-25, and the mass between 25 and 35 is a measured instrument-rail approach (only 22 values ever above 35) -- 30 leaves margin for real blooms while cutting the rail's shoulder; a probe out of water pinning high is a per-sensor `max_threshold` case, not a channel-bound one. lo=-1 gives the sub-detection clamp room (6,114 readings).",
        sources={
            "USGS": SourceMap(("00300",), "mg/l"),
            "IWQIS": SourceMap(("diss_oxy_con", "diss_oxy_con_v2"), "mg/L", units="declared",
                               note="params.csv: Dissolved Oxygen, mg/L"),
            "MPCA": SourceMap(("diss_oxy",), "mg/L", units="assumed"),
        }),
    "ph": Channel(
        unit="std units", fetch_priority=2, stats="full", lo=0.0, hi=14.0, clamp_negatives=False,
        scale_kind="interval", agree_tol=0.3, discriminating=False,
        note="weakest of the standard suite for nitrate, but co-located with everything else so collection is free. Already a logarithm: agreement is judged on the difference. Bounds are the definitional limits of the scale; measured range 0-12.5, zero violations.",
        sources={
            "USGS": SourceMap(("00400",), "std units"),
            "IWQIS": SourceMap(("ph", "ph_v2"), "std units", units="declared"),
            "MPCA": SourceMap(("ph",), "std units", units="assumed"),
        }),
    "fdom": Channel(
        unit="ug/L QSE", fetch_priority=2, stats="full", lo=-10.0, hi=5_000.0, clamp_negatives=True,
        native=True,
        note="DOC availability limits denitrification, and fDOM often anticorrelates with nitrate. Needs turbidity-interference correction downstream. Real values to 4,995; above the ceiling lies pure electronics garbage (813 distinct values up to 7.7e10). lo=-10 for clamp room. The RFU variants 32293/32294 return zero sites.",
        sources={"USGS": SourceMap(("32295",), "ug/l QSE")}),
    "fchl": Channel(
        unit="ug/L", fetch_priority=2, stats="full", lo=-1.0, hi=1_000.0, clamp_negatives=True,
        native=True,
        note="spring/summer autotrophic uptake produces a diurnal nitrate drawdown. 32316 and 32318 are both ug/L in situ and share the channel; 32319 does NOT -- it is phycocyanin. Real max 787; the out-of-range mass is 98% the -999999 sentinel. lo=-1 for clamp room.",
        sources={
            "USGS": SourceMap(("32316", "32318"), "ug/l"),
            "IWQIS": SourceMap(("chloro_con",), "ug/L", units="declared",
                               note="params.csv: Chlorophyll-A, ug/L. chloro_v is the raw voltage and stays out"),
        }),
    "fpc": Channel(
        unit="ug/L", fetch_priority=2, stats="full", lo=-1.0, hi=1_000.0, clamp_negatives=True,
        native=True,
        note="fluorescent PHYCOCYANIN, 54 sites -- the cyanobacteria pigment, not chlorophyll; a harmful-bloom signal in its own right. Real max 99. lo=-1 matters most here: 24,087 sub-detection readings (1.5% of the channel) clamp to 0 rather than vanish.",
        sources={"USGS": SourceMap(("32319",), "ug/l")}),
    "orthophosphate": Channel(
        unit="mg/L as P", fetch_priority=2, stats="full", lo=-0.1, hi=20.0, clamp_negatives=True,
        native=True,
        note="rare -- the most instrumented super gages only -- but a genuine co-nutrient signal, cheap to carry given masking. Real max 13.35; the only out-of-range value ever observed is the -999999 sentinel.",
        sources={
            "USGS": SourceMap(("51289",), "mg/l as P"),
            "IWQIS": SourceMap(("phosphate_con",), "mg/L as P", units="declared",
                               note="params.csv: 'Orthophosphate as PO4-P' -- as P, NOT as PO4, which would be a 3.07x trap"),
        }),
    # Canary-depth channels: collected only at roster probes, so the classes no water-parameter sweep can
    # return (a groundwater-level well, a precipitation station) stay exercised end-to-end instead of
    # registering and then returning no-data forever. Negligible cost; the whole point is that the path runs.
    "gw_level": Channel(
        unit="m below surface", fetch_priority=2, stats="mean", lo=-100.0, hi=2_000.0, clamp_negatives=False,
        discriminating=False, depth="canary",
        note="depth to water level below land surface. Negative is real -- an artesian head stands above the surface -- so lo=-100 bounds any plausible head while catching the -999999 ft fill; hi=2,000 exceeds the deepest water tables. Canary depth: fetched at roster probes only.",
        sources={"USGS": SourceMap(("72019",), "ft below land surface", _FT_TO_M)}),
    "precip": Channel(
        unit="mm", fetch_priority=2, stats="mean", lo=0.0, hi=2_000.0, clamp_negatives=False,
        discriminating=False, depth="canary",
        note="precipitation total. A negative accumulation is not a measurement (nulled by lo=0, not clamped -- a gauge reports 0, so a negative is instrument error, not sub-detection); hi=2,000 mm/day exceeds the world record. Canary depth: fetched at roster probes only.",
        sources={"USGS": SourceMap(("00045",), "in", _IN_TO_MM)}),
}

NAMES = tuple(CHANNELS)

# USGS returns daily-value columns with this suffix, ALREADY a daily mean. Marked so canonicalisation
# never manufactures a min, max or quartile from a single pre-aggregated number.
DV_SUFFIX = "_dv"

# Import-time closure: every axis value is validated by the dataclasses above; here, the cross-channel
# facts that must hold. High scrutiny must be rare by design -- it is a stewardship tier, not a target list.
_high = [n for n, c in CHANNELS.items() if c.scrutiny == "high"]
if len(_high) > 3:
    raise ValueError(f"high-scrutiny tier is meant to be small and rarely changed; got {_high}")
for _src, _vals in SENTINELS.items():
    if not all(isinstance(v, float) for v in _vals):
        raise ValueError(f"sentinels for {_src} must be floats (exact-equality matching): {_vals}")


def high_scrutiny() -> frozenset[str]:
    """The stewardship tier: decisions touching these records gate on a human."""
    return frozenset(n for n, c in CHANNELS.items() if c.scrutiny == "high")


def discriminating() -> frozenset[str]:
    """Channels whose agreement is evidence of a shared instrument."""
    return frozenset(n for n, c in CHANNELS.items() if c.discriminating)


def collected(depth: str | None = None) -> tuple[str, ...]:
    """Channel names at a collection depth; default everything not `off`."""
    if depth is None:
        return tuple(n for n, c in CHANNELS.items() if c.depth != "off")
    return tuple(n for n, c in CHANNELS.items() if c.depth == depth)


def channels_of(source: str) -> tuple[str, ...]:
    return tuple(n for n, c in CHANNELS.items() if source in c.sources)


def source_columns(source: str) -> dict[str, str]:
    """native column/pcode -> channel name, many-to-one."""
    out: dict[str, str] = {}
    for name, c in CHANNELS.items():
        for col in c.sources.get(source, SourceMap((), "")).cols:
            out[col] = name
    return out


def resolve(source: str, column: str) -> tuple[str | None, bool]:
    """(channel, is_preaggregated) for a native column; (None, False) when unmapped.

    Strips DV_SUFFIX and reports the pre-aggregation flag -- the reason this is a function and not a dict. No USGS site carries both forms of the same pcode (checked across all seven major pcodes; every overlap count zero).
    """
    pre = str(column).endswith(DV_SUFFIX)
    base = str(column)[: -len(DV_SUFFIX)] if pre else str(column)
    return source_columns(source).get(base), pre


def scale_of(source: str, channel: str) -> float:
    return CHANNELS[channel].sources[source].scale


def usgs_pcodes(priority: int | None = None) -> tuple[str, ...]:
    """The sweep/fetch pcode set. CANARY-DEPTH CHANNELS ARE EXCLUDED: canary means collected at roster probes only, and the probes reach their pcodes through their own declared channels (`register_one` -> `_tier_pcodes`), not through this list -- a census sweep over 72019 once registered ~4,600 groundwater wells nationally, a 23% scope change made by accident."""
    out: list[str] = []
    for c in CHANNELS.values():
        if priority is not None and c.fetch_priority != priority:
            continue
        if c.depth in ("off", "canary"):
            continue
        out.extend(c.sources.get("USGS", SourceMap((), "")).cols)
    return tuple(dict.fromkeys(out))


def native_channels() -> tuple[str, ...]:
    return tuple(n for n, c in CHANNELS.items() if c.native and c.depth != "off")


def fetch_tier(channels_declared) -> str:
    """'native' when the declaration includes any native-worthy channel, else the cheap 'dv' tier.

    Decided from what the source DECLARED, never a per-site metadata round trip -- ~13,700 saved at census scale. An unknown declaration gets the cheap tier.
    """
    import pandas as pd

    # pd.isna first: a census row with no mapped channels carries pd.NA, and `not NA` is not False but AMBIGUOUS
    if channels_declared is None or pd.isna(channels_declared) or not str(channels_declared).strip():
        return "dv"
    declared = set(str(channels_declared).split("+"))
    return "native" if declared & set(native_channels()) else "dv"


def full_stats(channel: str) -> bool:
    return CHANNELS[channel].stats == "full"


EXTRACT_MODES = ("none", "sentinel", "full")


def extract(obs, source: str, channel: str, scrub: str = "sentinel"):
    """One channel's series from a native frame, in CANONICAL units, plus a cell-grain tally: `(series | None, tally)`.

    THREE MODES, because three consumers need three different amounts of cleaning. `scrub="none"` returns the raw scaled series with sentinels intact -- the quality battery's `impossible_runs` measures fill-value flatlines on exactly this, and a pre-masked series would hide them. `"sentinel"` (default) masks and counts the per-source fill values -- identity evidence and unit verification want real measurements without fill distortion but must not consume the physical-range judgment. `"full"` additionally applies the physical range PER CELL -- below `lo` nulled, `[lo, 0)` clamped to 0 on clamping channels, above `hi` nulled -- and is what canonicalisation reduces.

    EVERYTHING COUNTS AT THE NATIVE-CELL GRAIN, per column, BEFORE the combine: a -999999 averaged into a two-column channel is no longer exactly -999999 and is uncatchable afterward, and the same argument holds for a range violation. The tally is `{n_cells, n_sentinel, n_below_lo, n_clamped, n_above_hi}` with the closure `n_kept = n_cells - n_sentinel - n_below_lo - n_above_hi` (clamped cells are KEPT, at 0). Assembles from every matching native column (dv or not), combined by the channel's rule.
    """
    import pandas as pd

    if scrub not in EXTRACT_MODES:
        raise ValueError(f"scrub mode {scrub!r} not in {EXTRACT_MODES}")
    tally = dict(n_cells=0, n_sentinel=0, n_below_lo=0, n_clamped=0, n_above_hi=0)
    c = CHANNELS[channel]
    sm = c.sources.get(source)
    if sm is None:
        return None, tally
    cols = [x for x in obs.columns if resolve(source, x)[0] == channel]
    if not cols:
        return None, tally
    frame = obs[cols].apply(pd.to_numeric, errors="coerce")
    tally["n_cells"] = int(frame.notna().sum().sum())
    if scrub in ("sentinel", "full"):
        for v in SENTINELS.get(source, ()):
            hit = frame == v
            n = int(hit.sum().sum())
            if n:
                tally["n_sentinel"] += n
                frame = frame.mask(hit)
    frame = frame * sm.scale
    if scrub == "full":
        if c.lo is not None:
            below = frame < c.lo
            tally["n_below_lo"] = int(below.sum().sum())
            frame = frame.mask(below)
        if c.clamp_negatives:
            neg = frame < 0.0
            tally["n_clamped"] = int(neg.sum().sum())
            frame = frame.mask(neg, 0.0)
        if c.hi is not None:
            above = frame > c.hi
            tally["n_above_hi"] = int(above.sum().sum())
            frame = frame.mask(above)
    s = frame.max(axis=1) if c.combine == "max" else frame.mean(axis=1)
    s = s.dropna()
    return (s if len(s) else None), tally


def fingerprint() -> str:
    """Hash of everything the CANONICAL REDUCTION depends on: mappings, scales, sentinels, ranges, stats, combine.

    THE CACHE KEY MTIME CANNOT BE. A registry edit (new source columns, a changed scale, a moved bound, an added sentinel) changes the correct output of every canonical file while touching no native file's mtime. A canonical file stamps this; staleness treats a mismatch as stale.
    """
    import hashlib

    parts = []
    for src in sorted(SENTINELS):
        parts.append(f"sentinels.{src}:{','.join(str(v) for v in SENTINELS[src])}")
    for name in sorted(CHANNELS):
        c = CHANNELS[name]
        for src in sorted(c.sources):
            sm = c.sources[src]
            parts.append(f"{name}.{src}:{','.join(sm.cols)}:{sm.scale}")
        parts.append(f"{name}:{c.stats}:{c.lo}:{c.hi}:{c.clamp_negatives}:{c.combine}")
    return hashlib.sha256(";".join(parts).encode()).hexdigest()[:12]


def units_report() -> list[tuple[str, str, str, str]]:
    """(channel, source, status, note) for every mapping not yet VERIFIED -- the A4 units check's worklist."""
    out = []
    for name, c in CHANNELS.items():
        for src, sm in c.sources.items():
            if sm.units != "verified":
                out.append((name, src, sm.units, sm.note))
    return out
