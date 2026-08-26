"""The canonical channel registry -- what a source calls a variable, what units it reports it in, and how it is summarised to a day.

ONE PLACE, BECAUSE THREE SOURCES NAME THE SAME QUANTITY THREE WAYS. USGS keys columns by parameter code, IWQIS by an ad-hoc English name, MPCA by an integer variable id mapped to a label. `usgs.fetch`'s docstring defers naming to a later stage on purpose -- "a pcode is unambiguous where a friendly name is not (five different codes all mean turbidity)" -- and this module is that stage. Nothing else in the build should contain a literal pcode or a source column name.

THE CHANNEL SET IS FIXED AND SMALL. Nodes are homogeneous: every published series carries the same columns, and a channel a site does not measure is a real null, never an absent column. That is what makes masking `notna()` rather than a parallel array that can disagree. Adding a channel is a schema change and is meant to feel like one; the 150 other parameters the sources happen to report are deliberately not here.

UNITS ARE DECLARED, NOT ASSUMED. `unit` is the source's own reported unit, verified against the NWIS parameter-code table for USGS. `scale` converts it to `Channel.unit`. Where a source does not document its unit, `verified=False` records that -- an unverified mapping is still used, but it is visible, and `chunk2` reports it. The one case where guessing is actively dangerous is turbidity, which is why `turbidity_unknown` exists: FNU, NTU, FNRU and FBU are different optical geometries, not different spellings, and averaging them produces a number that looks entirely reasonable and means nothing.

STATISTICS ARE COLLECTED, NOT PREDICTED. `stats="full"` stores the seven daily primitives; `stats="mean"` stores only the mean. Discharge and gage height are `mean` because their finer statistics carry nothing the model consumes, and they are also the two channels driving ~93% of the fetch, so the storage rule and the fetch tier agree by construction rather than by coincidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ft3/s -> m3/s and ft -> m. Exact by definition of the international foot.
_CFS_TO_CMS = 0.028316846592
_FT_TO_M = 0.3048


@dataclass(frozen=True)
class SourceMap:
    """How one source spells one channel.

    `cols` is pcodes for USGS and column names for IWQIS/MPCA, matching what each adapter's `fetch` returns. Several entries mean the source files the same quantity under more than one code and whichever is present wins, combined row-wise -- the rule `records.nitrate_series` already applies across the three nitrate pcodes.
    """

    cols: tuple[str, ...]
    unit: str
    scale: float = 1.0
    verified: bool = True
    note: str = ""


@dataclass(frozen=True)
class Channel:
    """One canonical variable: its units, its daily summary, and its plausible range.

    `lo`/`hi` bound a physically possible reading and exist to catch a failed sensor, not to trim a distribution -- they are deliberately generous. `clamp_negatives` distinguishes a concentration, where a small negative is a real sub-detection measurement that should become zero, from a level or a flow, where a negative is a legitimate value: USGS reports negative discharge at tidally reversed sites, and gage height is datum-relative and routinely below zero.

    `scale_kind` says how two measurements of the same water are compared. A RATIO scale has a true zero, so a unit error shows up as a constant multiplier and `units_check` judges on the median ratio. An INTERVAL scale does not -- degrees Celsius and pH both have an arbitrary origin, so a ratio of two readings near zero is unstable and means nothing, and the comparison has to be on the difference instead. `agree_tol` is how close two co-located instruments must be, in canonical units, to count as agreeing on an interval scale.
    """

    unit: str
    tier: int
    stats: str                                   # "full" | "mean"
    lo: float | None
    hi: float | None
    clamp_negatives: bool
    sources: dict[str, SourceMap] = field(default_factory=dict)
    scale_kind: str = "ratio"                    # "ratio" | "interval"
    agree_tol: float = 0.0                       # interval scales only
    combine: str = "mean"                        # how several native columns reduce to one series
    native: bool = False                         # is a sub-daily fetch worth paying for? see `native_channels`
    note: str = ""


# The twelve channels. Site counts in comments are the seed-box census, measured 2026-08-16.
CHANNELS: dict[str, Channel] = {
    "nitrate": Channel(
        unit="mg/L as N", tier=1, stats="full", lo=-1.0, hi=None, clamp_negatives=True,
        native=True,
        combine="max",
        note="the primary objective. No ceiling: removing one recovered ~10,700 real readings above 50. Combines by MAX across its three pcodes -- the existing rule, and the conservative one for a pollutant.",
        sources={
            # 99133 alone returns zero Nebraska sites; which code a network files under is local convention. 83554 is a LOAD and stays excluded.
            "USGS": SourceMap(("99133", "99137", "00630"), "mg/l as N"),
            "IWQIS": SourceMap(("nitrate_con",), "mg/L as N", verified=False,
                               note="undocumented; matches USGS at co-located sites"),
            # Settled by measurement, not documentation: Cedar River appears as CSG 48020005 and USGS 05457000, and over 56,125 matching timestamps the median ratio is 1.0000.
            "MPCA": SourceMap(("nitrate",), "mg/L as N"),
        }),
    "discharge": Channel(
        unit="m3/s", tier=1, stats="mean", lo=None, hi=None, clamp_negatives=False,
        note="the covariate that matters most -- it sets the dilution-vs-flushing regime. Negative is real at tidally reversed sites.",
        sources={
            "USGS": SourceMap(("00060",), "ft3/s", _CFS_TO_CMS),
            "MPCA": SourceMap(("discharge",), "ft3/s", _CFS_TO_CMS, verified=False,
                              note="US convention assumed; confirm against a co-located USGS gage"),
        }),
    "gage_height": Channel(
        unit="m", tier=1, stats="mean", lo=None, hi=None, clamp_negatives=False,
        note="datum-relative, so negative is normal. Present at stage-only sites with no rating, which is what makes it add nodes discharge does not.",
        sources={
            "USGS": SourceMap(("00065",), "ft", _FT_TO_M),
            "MPCA": SourceMap(("level",), "ft", _FT_TO_M, verified=False),
        }),
    "water_temp": Channel(
        unit="degC", tier=1, stats="full", lo=-5.0, hi=45.0, clamp_negatives=False,
        scale_kind="interval", agree_tol=1.0,
        note="the most ubiquitous sensor parameter; proxies seasonality and denitrification rate. Celsius has an arbitrary zero, so agreement is judged on the difference: two thermistors metres apart at different depths differ by a fraction of a degree, which as a RATIO near freezing reads as a large error and is not one.",
        sources={
            "USGS": SourceMap(("00010",), "deg C"),
            # dts_temp_water is the same quantity from the nitrate sensor's own thermistor, present where temp_water is not.
            "IWQIS": SourceMap(("temp_water", "dts_temp_water"), "deg C", verified=False),
            "MPCA": SourceMap(("water_temp",), "deg C", verified=False),
        }),
    "spec_cond": Channel(
        unit="uS/cm@25C", tier=1, stats="full", lo=0.0, hi=100_000.0, clamp_negatives=True,
        native=True,
        note="tracks the groundwater/baseflow versus event-water mixture; second only to discharge.",
        sources={
            "USGS": SourceMap(("00095",), "uS/cm @25C"),
            "IWQIS": SourceMap(("spec_cond", "spec_cond_v2"), "uS/cm", verified=False),
            "MPCA": SourceMap(("spec_cond",), "uS/cm", verified=False),
        }),
    "turbidity_fnu": Channel(
        unit="FNU", tier=1, stats="full", lo=0.0, hi=10_000.0, clamp_negatives=True,
        native=True,
        note="63680 ONLY -- 800 of the 841 turbidity sites. 63681 (FNRU, ratiometric) and 63682 (FBU, backscatter) are 3 sites each and a different optical geometry, so they are not folded in.",
        sources={"USGS": SourceMap(("63680",), "FNU")}),
    "turbidity_ntu": Channel(
        unit="NTU", tier=1, stats="full", lo=0.0, hi=10_000.0, clamp_negatives=True,
        native=True,
        note="63675, 35 sites. `00076` is the code usually named for legacy NTU and returns ZERO sites in the AOE, as do 61028, 82079, 63676-63679, 63683 and 99872.",
        sources={"USGS": SourceMap(("63675",), "NTU")}),
    "turbidity_unknown": Channel(
        unit="unknown", tier=1, stats="full", lo=0.0, hi=10_000.0, clamp_negatives=True,
        native=True,
        note="IWQIS and MPCA turbidity, whose optical method neither source states. Held apart from FNU and NTU until a co-located comparison settles it; promoting it is a one-line move once measured.",
        sources={
            # turbi_mean/max/min/med/var/bes are six STATISTICS of one sensor, not six variables. The mean is the value; min and max feed the daily statistics directly.
            "IWQIS": SourceMap(("turbi_mean", "turbi_mean_v2"), "unknown", verified=False),
            "MPCA": SourceMap(("turbidity",), "unknown", verified=False),
        }),
    "diss_oxy": Channel(
        unit="mg/L", tier=2, stats="full", lo=0.0, hi=25.0, clamp_negatives=True,
        native=True,
        note="the diel amplitude carries the biological activity that also drives nitrate uptake, which is why it is stats='full' despite being tier 2.",
        sources={
            "USGS": SourceMap(("00300",), "mg/l"),
            "IWQIS": SourceMap(("diss_oxy_con", "diss_oxy_con_v2"), "mg/L", verified=False),
            "MPCA": SourceMap(("diss_oxy",), "mg/L", verified=False),
        }),
    "ph": Channel(
        unit="std units", tier=2, stats="full", lo=0.0, hi=14.0, clamp_negatives=False,
        scale_kind="interval", agree_tol=0.3,
        note="weakest of the standard suite for nitrate, but co-located with everything else so collection is free. Already a logarithm, so a ratio of two pH readings is not a scale factor and agreement is judged on the difference.",
        sources={
            "USGS": SourceMap(("00400",), "std units"),
            "IWQIS": SourceMap(("ph", "ph_v2"), "std units", verified=False),
            "MPCA": SourceMap(("ph",), "std units", verified=False),
        }),
    "fdom": Channel(
        unit="ug/L QSE", tier=2, stats="full", lo=0.0, hi=5_000.0, clamp_negatives=True,
        native=True,
        note="DOC availability limits denitrification, and fDOM often anticorrelates with nitrate. Needs turbidity-interference correction downstream. The RFU variants 32293/32294 return zero sites, so only the QSE code exists here.",
        sources={"USGS": SourceMap(("32295",), "ug/l QSE")}),
    "fchl": Channel(
        unit="ug/L", tier=2, stats="full", lo=0.0, hi=1_000.0, clamp_negatives=True,
        native=True,
        note="spring/summer autotrophic uptake produces a diurnal nitrate drawdown. 32316 and 32318 are both ug/L in situ and share the channel; 32319 does NOT -- it is phycocyanin, a different pigment.",
        sources={
            "USGS": SourceMap(("32316", "32318"), "ug/l"),
            "IWQIS": SourceMap(("chloro_con",), "ug/L", verified=False),
        }),
    "fpc": Channel(
        unit="ug/L", tier=2, stats="full", lo=0.0, hi=1_000.0, clamp_negatives=True,
        native=True,
        note="fluorescent PHYCOCYANIN, 54 sites -- the cyanobacteria pigment, not chlorophyll. It was specified as a chlorophyll code; the NWIS table names it `fPC, water, in situ`, so it is its own channel and a harmful-bloom signal in its own right.",
        sources={"USGS": SourceMap(("32319",), "ug/l")}),
    "orthophosphate": Channel(
        unit="mg/L as P", tier=2, stats="full", lo=-0.1, hi=20.0, clamp_negatives=True,
        native=True,
        note="rare -- the most instrumented super gages only -- but a genuine co-nutrient signal, and cheap to carry given masking.",
        sources={
            "USGS": SourceMap(("51289",), "mg/l as P"),
            "IWQIS": SourceMap(("phosphate_con",), "mg/L as P", verified=False),
        }),
}

NAMES = tuple(CHANNELS)
LABEL_CHANNEL = "nitrate"          # the primary objective; the one channel the quality gate judges

# USGS returns daily-value columns with this suffix, and they are ALREADY a daily mean. Marked so `chunk2` never manufactures a min, max or quartile from a single pre-aggregated number.
DV_SUFFIX = "_dv"


def channels_of(source: str) -> tuple[str, ...]:
    """Canonical channels this source can supply, in registry order."""
    return tuple(n for n, c in CHANNELS.items() if source in c.sources)


def source_columns(source: str) -> dict[str, str]:
    """Native column (or pcode) -> canonical channel, for one source.

    Many-to-one: a source filing one quantity under several codes maps all of them to the same channel, and `chunk2` combines them row-wise. The USGS `_dv` suffix is NOT included -- use `resolve` to match a column that may carry it.
    """
    out: dict[str, str] = {}
    for name, ch in CHANNELS.items():
        sm = ch.sources.get(source)
        if sm is None:
            continue
        for col in sm.cols:
            out[col] = name
    return out


def resolve(source: str, column: str) -> tuple[str | None, bool]:
    """`(channel, is_preaggregated)` for a native column. `(None, False)` when nothing claims it.

    Strips `DV_SUFFIX` before matching, which is the whole reason this is a function rather than a dict lookup: `00060` and `00060_dv` are the same channel measured differently, and no USGS site carries both (checked across all seven major pcodes, every overlap count zero).
    """
    pre = column.endswith(DV_SUFFIX)
    base = column[: -len(DV_SUFFIX)] if pre else column
    return source_columns(source).get(base), pre


def scale_of(channel: str, source: str) -> float:
    """Multiplier taking the source's native unit to the channel's canonical unit."""
    return CHANNELS[channel].sources[source].scale


def usgs_pcodes(tier: int | None = None) -> tuple[str, ...]:
    """Every USGS parameter code the build collects, optionally one tier only. Deduplicated, registry order."""
    out: list[str] = []
    for ch in CHANNELS.values():
        sm = ch.sources.get("USGS")
        if sm is None or (tier is not None and ch.tier != tier):
            continue
        out += [c for c in sm.cols if c not in out]
    return tuple(out)


def native_channels() -> tuple[str, ...]:
    """Channels whose within-day behaviour is worth a sub-daily fetch.

    THE TIER IS A PROPERTY OF THE CHANNEL, NOT OF THE SITE. The daily service returns a mean and nothing else, so a channel is worth the native pull exactly when its spread carries something the mean loses: the chemistry does -- a diel dissolved-oxygen swing, a turbidity spike on the rising limb, the nitrate maximum -- while discharge, stage, water temperature and pH are smooth enough at daily resolution that the extra requests buy noise.

    Declared per channel rather than derived from `stats`, because the two questions are genuinely different: `stats` asks what to KEEP once the data is in hand, this asks what to PAY FOR. Water temperature keeps its full statistics wherever a native record already exists and still does not justify fetching one.
    """
    return tuple(n for n, c in CHANNELS.items() if c.native)


def fetch_tier(declared: str | None) -> str:
    """`native` | `dv` for a site, from its declared channel set. Unknown declarations get the cheap tier."""
    if not declared or declared != declared:                 # None or NaN
        return "dv"
    want = set(str(declared).split("+"))
    return "native" if want & set(native_channels()) else "dv"


def full_stats(channel: str) -> bool:
    """True where the seven daily primitives are stored, False where only the mean is."""
    return CHANNELS[channel].stats == "full"


def extract(obs, source: str, channel: str):
    """The channel's series from a native frame, in CANONICAL units. `None` where the source does not carry it.

    TWO SENSORS AT ONE LOCATION ARE ONE MEASUREMENT. Several native columns feeding one channel is either a source filing the same quantity under several codes (USGS nitrate) or a station carrying a second instrument (IWQIS `_v2`), and both reduce to a single series here.

    HOW they reduce is per channel. Nitrate combines by MAX -- the rule `records.nitrate_series` already applies and the conservative one for a pollutant. Everything else combines by MEAN, because two co-located instruments measuring pH or dissolved oxygen are two estimates of one value and the maximum of them is biased upward by construction. Where only one column reports on a given row, that value stands under either rule.
    """
    ch = CHANNELS[channel]
    sm = ch.sources.get(source)
    if sm is None:
        return None
    cols = [c for c in obs.columns if resolve(source, c)[0] == channel]
    if not cols:
        return None
    vals = obs[cols].astype("float64")
    s = (vals.max(axis=1) if ch.combine == "max" else vals.mean(axis=1)).dropna()
    return (s * sm.scale) if len(s) else None


def unverified() -> list[tuple[str, str, str]]:
    """`(channel, source, note)` for every mapping whose units are not confirmed.

    Chunk 2 prints this so an undocumented unit stays visible rather than becoming true by being used. Turbidity is the one where being wrong is silent, since a wrong optical method still produces plausible numbers.
    """
    return [(n, src, sm.note) for n, ch in CHANNELS.items()
            for src, sm in ch.sources.items() if not sm.verified]
