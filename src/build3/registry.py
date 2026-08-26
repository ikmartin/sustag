"""The channel registry -- the single extension point for everything a sensor can measure.

ONE PLACE, BECAUSE THREE SOURCES NAME THE SAME QUANTITY THREE WAYS. USGS keys columns by parameter code, IWQIS by ad-hoc English names, MPCA by integer variable ids. Nothing else in the build may contain a literal pcode or a source column name. Adding a channel is: add the entry -> the schema generates the per-channel columns -> the census registers advertisers next pass -> canonicalisation produces it -> sites gain it. The two genuinely manual costs are declaring/verifying units and a bounded USGS backfill.

FOUR INDEPENDENT AXES ON A CHANNEL, never conflated:
- `fetch_priority` (1|2) -- which channels the census asks for first. A fetch-cost fact.
- `scrutiny` (high|standard) -- the stewardship tier: identity and quality decisions touching a high-tier record gate on a human; standard-tier decisions take the rule's answer, recorded. A function of scarcity x consequence, not of any model's target. Nitrate sits alone in the high tier.
- `depth` (full|canary|off) -- collection depth. Full is collected everywhere; canary only at roster probes, keeping the path exercised at negligible cost; off is named so absence is a decision.
- `discriminating` (bool) -- whether agreement on this channel is evidence of a shared INSTRUMENT rather than a shared climate. Water temperature, stage, pH, DO and turbidity are storm- or season-driven basin-wide: WQS0092 and 05482000 sit 1,878 m apart and agree on water temperature at r = 0.9936, and neither is the other. Consolidates build2's WEAK_FOR_IDENTITY into the registry, where a channel's nature belongs.

UNITS ARE A THREE-STATE FACT: `declared` (the source documents the unit -- NWIS parameter table, IWQIS params.csv), `assumed` (our guess, visible), `verified` (settled by cross-source measurement at co-located instruments -- the only state that closes the question). MPCA nitrate is the exemplar: verified over 56,125 matching timestamps at median ratio 1.0000 against 4.4269 for as-NO3. Turbidity is why the states matter: FNU, NTU, FNRU and FBU are different optical geometries, not spellings, so `turbidity_unknown` holds IWQIS and MPCA apart from both named channels until MEASUREMENT -- not declaration -- promotes it; IWQIS's params.csv declares NTU (instrument dts12), which is recorded here and is still not a promotion.

STATISTICS ARE COLLECTED, NOT PREDICTED. `stats="full"` stores the seven daily primitives; `stats="mean"` only the mean. Discharge and gage height are `mean` and drive ~93% of the fetch, so the storage rule and the fetch tier agree by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ft3/s -> m3/s and ft -> m. Exact by definition of the international foot.
_CFS_TO_CMS = 0.028316846592
_FT_TO_M = 0.3048

UNITS_STATES = ("declared", "assumed", "verified")


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
    """One canonical variable: units, daily summary, plausible range, and its four axes.

    `lo`/`hi` bound a physically possible reading -- they catch a failed sensor, not trim a distribution. `clamp_negatives` distinguishes a concentration (a small negative is a real sub-detection measurement, becomes 0) from a level or flow (negative is legitimate: tidal reversal, datum-relative stage).

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


# The channels. Site counts in comments are the seed-box census, measured 2026-08-16.
CHANNELS: dict[str, Channel] = {
    "nitrate": Channel(
        unit="mg/L as N", fetch_priority=1, stats="full", lo=-1.0, hi=100.0, clamp_negatives=True,
        native=True, combine="max", compare_stat="max", scrutiny="high",
        note="the primary objective, and the sole high-scrutiny channel: scarce, health-relevant, the product's headline. hi=100 is a PHYSICAL bound, not a distribution trim: the cohort's highest real reading is the ~44 instrument rail, and the value this exists to null is the 999.99 fill sentinel (MPCA:41043001 held it for 14,631 samples). It is NOT the old 50 mg/L ceiling, whose removal recovered ~10,700 real readings above 50 -- anything in (50, 100] stays data. Combines by MAX across its three pcodes, the conservative rule for a pollutant. compare_stat is max because the merge thresholds were calibrated on the daily-max series and changing it would silently re-litigate every decision on record.",
        sources={
            # 99133 alone returns zero Nebraska sites; which code a network files under is local convention. 83554 is a LOAD and stays excluded.
            "USGS": SourceMap(("99133", "99137", "00630"), "mg/l as N"),
            "IWQIS": SourceMap(("nitrate_con",), "mg/L as N", units="declared",
                               note="params.csv: 'Nitrate + Nitrite as N', mg/L (instrument nitratax); matches USGS at co-located sites but not yet measured to the verified bar"),
            # Settled by measurement, not documentation: Cedar River is CSG 48020005 and USGS 05457000; over 56,125 matching timestamps the median ratio is 1.0000 (4.4269 for as-NO3).
            "MPCA": SourceMap(("nitrate",), "mg/L as N", units="verified"),
        }),
    "discharge": Channel(
        unit="m3/s", fetch_priority=1, stats="mean", lo=None, hi=None, clamp_negatives=False,
        note="the covariate that matters most -- it sets the dilution-vs-flushing regime. Negative is real at tidally reversed sites.",
        sources={
            "USGS": SourceMap(("00060",), "ft3/s", _CFS_TO_CMS),
            "MPCA": SourceMap(("discharge",), "ft3/s", _CFS_TO_CMS, units="assumed",
                              note="US convention assumed; confirm against a co-located USGS gage"),
        }),
    "gage_height": Channel(
        unit="m", fetch_priority=1, stats="mean", lo=None, hi=None, clamp_negatives=False,
        discriminating=False,
        note="datum-relative, so negative is normal. Present at stage-only sites with no rating, which is what makes it add nodes discharge does not. Non-discriminating: two gauges on one river agree on stage because it is one river.",
        sources={
            "USGS": SourceMap(("00065",), "ft", _FT_TO_M),
            "MPCA": SourceMap(("level",), "ft", _FT_TO_M, units="assumed"),
        }),
    "water_temp": Channel(
        unit="degC", fetch_priority=1, stats="full", lo=-5.0, hi=45.0, clamp_negatives=False,
        scale_kind="interval", agree_tol=1.0, discriminating=False,
        note="the most ubiquitous sensor parameter; proxies seasonality and denitrification rate. Interval scale: two thermistors metres apart at different depths differ by a fraction of a degree, which as a RATIO near freezing reads as a large error and is not one. Non-discriminating: WQS0092/05482000 agree at r=0.9936 from 1,878 m apart.",
        sources={
            "USGS": SourceMap(("00010",), "deg C"),
            # dts_temp_water is the same quantity from the nitrate sensor's own thermistor, present where temp_water is not.
            "IWQIS": SourceMap(("temp_water", "dts_temp_water"), "deg C", units="declared",
                               note="params.csv: Temperature (Water), degC"),
            "MPCA": SourceMap(("water_temp",), "deg C", units="assumed"),
        }),
    "spec_cond": Channel(
        unit="uS/cm@25C", fetch_priority=1, stats="full", lo=0.0, hi=100_000.0, clamp_negatives=True,
        native=True,
        note="tracks the groundwater/baseflow versus event-water mixture; second only to discharge.",
        sources={
            "USGS": SourceMap(("00095",), "uS/cm @25C"),
            "IWQIS": SourceMap(("spec_cond", "spec_cond_v2"), "uS/cm", units="declared",
                               note="params.csv: uS/cm; the @25C compensation is assumed to match"),
            "MPCA": SourceMap(("spec_cond",), "uS/cm", units="assumed"),
        }),
    "turbidity_fnu": Channel(
        unit="FNU", fetch_priority=1, stats="full", lo=0.0, hi=10_000.0, clamp_negatives=True,
        native=True, discriminating=False,
        note="63680 ONLY -- 800 of the 841 turbidity sites. 63681 (FNRU) and 63682 (FBU) are 3 sites each and a different optical geometry, so they are not folded in.",
        sources={"USGS": SourceMap(("63680",), "FNU")}),
    "turbidity_ntu": Channel(
        unit="NTU", fetch_priority=1, stats="full", lo=0.0, hi=10_000.0, clamp_negatives=True,
        native=True, discriminating=False,
        note="63675, 35 sites. `00076`, the code usually named for legacy NTU, returns ZERO sites in the AOE, as do 61028, 82079, 63676-63679, 63683 and 99872.",
        sources={"USGS": SourceMap(("63675",), "NTU")}),
    "turbidity_unknown": Channel(
        unit="unknown", fetch_priority=1, stats="full", lo=0.0, hi=10_000.0, clamp_negatives=True,
        native=True, discriminating=False,
        note="IWQIS and MPCA turbidity, held apart from FNU and NTU until a co-located MEASUREMENT settles the optical geometry -- declaration is not promotion. IWQIS params.csv declares NTU on a dts12 (a DTS-12 reads NTU), which is recorded and still waits for the measured bar; promoting it is a one-line move once units_check clears it.",
        sources={
            # turbi_mean/max/min/med/var/bes are six STATISTICS of one sensor, not six variables.
            "IWQIS": SourceMap(("turbi_mean", "turbi_mean_v2"), "NTU", units="declared",
                               note="params.csv: NTU, instrument dts12"),
            "MPCA": SourceMap(("turbidity",), "unknown", units="assumed"),
        }),
    "diss_oxy": Channel(
        unit="mg/L", fetch_priority=2, stats="full", lo=0.0, hi=25.0, clamp_negatives=True,
        native=True, discriminating=False,
        note="the diel amplitude carries the biological activity that also drives nitrate uptake, which is why it is stats='full' despite fetch priority 2.",
        sources={
            "USGS": SourceMap(("00300",), "mg/l"),
            "IWQIS": SourceMap(("diss_oxy_con", "diss_oxy_con_v2"), "mg/L", units="declared",
                               note="params.csv: Dissolved Oxygen, mg/L"),
            "MPCA": SourceMap(("diss_oxy",), "mg/L", units="assumed"),
        }),
    "ph": Channel(
        unit="std units", fetch_priority=2, stats="full", lo=0.0, hi=14.0, clamp_negatives=False,
        scale_kind="interval", agree_tol=0.3, discriminating=False,
        note="weakest of the standard suite for nitrate, but co-located with everything else so collection is free. Already a logarithm: agreement is judged on the difference.",
        sources={
            "USGS": SourceMap(("00400",), "std units"),
            "IWQIS": SourceMap(("ph", "ph_v2"), "std units", units="declared"),
            "MPCA": SourceMap(("ph",), "std units", units="assumed"),
        }),
    "fdom": Channel(
        unit="ug/L QSE", fetch_priority=2, stats="full", lo=0.0, hi=5_000.0, clamp_negatives=True,
        native=True,
        note="DOC availability limits denitrification, and fDOM often anticorrelates with nitrate. Needs turbidity-interference correction downstream. The RFU variants 32293/32294 return zero sites.",
        sources={"USGS": SourceMap(("32295",), "ug/l QSE")}),
    "fchl": Channel(
        unit="ug/L", fetch_priority=2, stats="full", lo=0.0, hi=1_000.0, clamp_negatives=True,
        native=True,
        note="spring/summer autotrophic uptake produces a diurnal nitrate drawdown. 32316 and 32318 are both ug/L in situ and share the channel; 32319 does NOT -- it is phycocyanin.",
        sources={
            "USGS": SourceMap(("32316", "32318"), "ug/l"),
            "IWQIS": SourceMap(("chloro_con",), "ug/L", units="declared",
                               note="params.csv: Chlorophyll-A, ug/L. chloro_v is the raw voltage and stays out"),
        }),
    "fpc": Channel(
        unit="ug/L", fetch_priority=2, stats="full", lo=0.0, hi=1_000.0, clamp_negatives=True,
        native=True,
        note="fluorescent PHYCOCYANIN, 54 sites -- the cyanobacteria pigment, not chlorophyll; a harmful-bloom signal in its own right.",
        sources={"USGS": SourceMap(("32319",), "ug/l")}),
    "orthophosphate": Channel(
        unit="mg/L as P", fetch_priority=2, stats="full", lo=-0.1, hi=20.0, clamp_negatives=True,
        native=True,
        note="rare -- the most instrumented super gages only -- but a genuine co-nutrient signal, cheap to carry given masking.",
        sources={
            "USGS": SourceMap(("51289",), "mg/l as P"),
            "IWQIS": SourceMap(("phosphate_con",), "mg/L as P", units="declared",
                               note="params.csv: 'Orthophosphate as PO4-P' -- as P, NOT as PO4, which would be a 3.07x trap"),
        }),
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


def high_scrutiny() -> frozenset[str]:
    """The stewardship tier: decisions touching these records gate on a human."""
    return frozenset(n for n, c in CHANNELS.items() if c.scrutiny == "high")


def discriminating() -> frozenset[str]:
    """Channels whose agreement is evidence of a shared instrument (build2's WEAK set, inverted)."""
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
    out: list[str] = []
    for c in CHANNELS.values():
        if priority is not None and c.fetch_priority != priority:
            continue
        if c.depth == "off":
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


def extract(obs, source: str, channel: str):
    """One channel's series from a native frame, in CANONICAL units, or None.

    Assembles from every matching native column (dv or not), combined by the channel's rule.
    """
    import pandas as pd

    c = CHANNELS[channel]
    sm = c.sources.get(source)
    if sm is None:
        return None
    cols = [x for x in obs.columns if resolve(source, x)[0] == channel]
    if not cols:
        return None
    frame = obs[cols].apply(pd.to_numeric, errors="coerce")
    s = frame.max(axis=1) if c.combine == "max" else frame.mean(axis=1)
    s = s.dropna()
    return (s * sm.scale) if len(s) else None


def fingerprint() -> str:
    """Hash of everything the CANONICAL REDUCTION depends on: mappings, scales, ranges, stats, combine.

    THE CACHE KEY THE ANCESTOR LACKED. build2 rebuilt a canonical file when its native was newer -- and a registry edit (new source columns, a changed scale) changed the correct output of every file while touching no native's mtime. Six IWQIS canonicals were measurably wrong that way, invisible until a rewrite diffed them. A canonical file stamps this; `stale` treats a mismatch as stale.
    """
    import hashlib

    parts = []
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
