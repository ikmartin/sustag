"""The site-type vocabulary and its metadata-only evidence rules.

THIS MODULE IS THE VOCABULARY; the evidence-ranked classifier over the snap and the NHD (`classify`, `ambiguous`) joins it at D4. The vocabulary comes first because acquisition needs one function from it before any snap exists: `prior_no_surface`, the metadata-only prior that saves ~35 pointless NLDI fetches at A1 -- a PRIOR, not the answer.

Two exclusion axes, and they answer different questions. `NO_SURFACE_BASIN` is "can a surface basin be delineated at all" -- standing and tidal water drains from nowhere, and snapping one to a reach hands it a basin belonging to something else. `SUB_CATCHMENT` is "is the delineated basin THIS site's contributing area" -- these drain, and a delineation succeeds and looks reasonable; it is simply not theirs, by two orders of magnitude. `prior_no_surface` and basin delineation deliberately use the narrower sets.
"""

from __future__ import annotations

import re

import pandas as pd

from .. import config, io as bio

# USGS publishes a real type vocabulary. BOTH the long names and the codes are mapped: `usgs.py`'s discovery
# takes whichever of `site_type` / `site_type_code` the response carries, and a shape change there would
# otherwise send all 227 USGS sites to `unknown` in silence. Mapping both makes that degrade to correct.
USGS_TYPE = {
    # long names, as returned today
    "stream": "stream", "tidal stream": "tidal_stream", "well": "well", "estuary": "estuary",
    "lake, reservoir, impoundment": "lake", "ditch": "ditch", "atmosphere": "atmosphere",
    "field, pasture, orchard, or nursery": "field", "spring": "groundwater",
    "wetland": "wetland_outflow", "ocean": "estuary", "coastal": "estuary", "storm sewer": "storm_drain", "canal": "canal",
    # NWIS codes for the same vocabulary
    "st": "stream", "st-ts": "tidal_stream", "st-dch": "ditch", "st-ca": "ditch",
    "lk": "lake", "es": "estuary", "oc": "estuary", "we": "wetland_outflow",
    "sp": "groundwater", "gw": "well", "fa-fon": "field", "at": "atmosphere",
}

# Neither IWQIS nor MPCA publishes a type field, so type comes from the station name for both. The patterns
# are English words for water bodies, not source-specific vocabulary, which is why one list serves both.
#
# WORD BOUNDARIES ARE LOAD-BEARING: a substring test reads "Beaver Creek at Colwell" as a well and "Powell
# Creek at Storm Lake" as a lake. Ordered most specific first, and deliberately conservative -- only strong
# signals classify. These are PROJECT DESCRIPTORS ("water supply well", "bioreactor", "drainage tile"), which
# a station is named for on purpose; a place name is not a site type.
NAME_PATTERNS = [
    (r"\bstorm drain\b|\bstorm sewer\b", "storm_drain"),
    (r"groundwater|\bspring\b|\bcave\b|\bkarst\b|\bsinkhole\b", "groundwater"),
    (r"\bwater supply well\b|\bwell\b", "well"),
    (r"blind inlet", "blind_inlet"),
    (r"saturated waterway", "saturated_waterway"),
    (r"saturated buffer", "saturated_buffer"),
    (r"in.?field (research|plot)", "field"),
    (r"drainage tile|\btile\b", "tile_outlet"),
    (r"\bwetland\b", "wetland_outflow"),
    (r"\bditch\b", "ditch"),
    (r"\bpond\b", "pond"),
    (r"\bbioreactor\b", "bioreactor"),
]

# Types whose assignment came from evidence rather than from falling through.
AUTHORITATIVE = ("usgs_site_type", "station_name", "source_description")

# TYPES WITH NO UPSTREAM DRAINAGE BASIN. Standing and tidal water does not drain from anywhere, so snapping
# one of these to a reach hands it a basin belonging to something else. Every impossible basin chunk 0 has
# produced came from this set: four estuaries at roughly 5,013,000-5,063,000 km2 (Orient Harbor, Hog Island
# Channel, Mattapoisett Harbor, Sippican Harbor), plus lakes like `New Canal Lighthouse`.
#
# It lives here rather than in `chunk0/basins.py` because it is a fact about the TYPE VOCABULARY, and because
# chunk 0 now asks this module the question -- the set living in chunk 0 while chunk 0 imports the classifier
# would be a cycle. Read by the basin guard, by chunk 6's eligibility filter and by `schema.validate_gauges`.
# `atmosphere` is the purest member of this set: a deposition gauge collects what falls on it and drains
# from nowhere, so a reach snap hands it somebody else's basin outright. `chunk0.discover` already treats
# the word as non-surface for the fetch skip, which is why these 16 sites were never downloaded; this is the
# same fact expressed where the vocabulary lives.
NO_SURFACE_BASIN = {"well", "groundwater", "estuary", "lake", "tidal_stream", "pond", "atmosphere",
                    "storm_drain"}   # a storm drain's catchment is the sewer-shed, not NHD topography

# TYPES WHOSE BASIN IS NOT THEIR CONTRIBUTING AREA. A SECOND AXIS, and it has to be second because the
# question is different: these DO drain, and a delineation from their reach succeeds and looks reasonable.
# It is simply not theirs. A blind inlet or a saturated buffer drains a field -- call it 10 ha -- against a
# finest-resolution NHD catchment of 1-3 km2, so the polygon it is handed is two orders of magnitude too big
# and every covariate summed over it describes land the sensor never sees. `NO_SURFACE_BASIN` cannot express
# that: a saturated buffer is not standing water and refusing it a basin on those grounds would be true by
# accident.
#
# THEY ARE ALSO NOT A MEASUREMENT ERROR. These sites are deployed IN PAIRS ACROSS AN INTERVENTION and read
# 15-20 mg/L against a cohort median of 6.6 -- undiluted field drainage, minus whatever the practice removed.
# The treatment sits between the land and the sensor and is in no feature set, so two of them with identical
# basin covariates carry deliberately different nitrate. Excluded from the cohort, kept in every other sense.
SUB_CATCHMENT = {"saturated_buffer", "saturated_waterway", "blind_inlet",
                 "tile_outlet", "bioreactor", "field"}

# THE MATERIAL BOUNDARY, for the identity gate: a pair crossing it never merges and never surfaces --
# a well and a stream sensor are different water by physical certainty. Everything not groundwater or
# atmosphere is surface water; `unknown` has NO material, and the gate makes its pairs WAIT rather than
# guessing. Deliberately not NO_SURFACE_BASIN, which mixes surface waterbodies (lake, estuary, pond) with
# wells because it answers "can a basin be delineated", not "what water is this".
GROUNDWATER_TYPES = {"well", "groundwater"}
ATMOSPHERE_TYPES = {"atmosphere"}


def material(site_type) -> str:
    """'surface_water' | 'groundwater' | 'atmosphere' | 'unknown' -- total over the vocabulary, and `unknown` propagates rather than defaulting."""
    t = str(site_type) if pd.notna(site_type) else "unknown"
    if t == "unknown":
        return "unknown"
    if t in GROUNDWATER_TYPES:
        return "groundwater"
    if t in ATMOSPHERE_TYPES:
        return "atmosphere"
    if t in VALID_TYPES:
        return "surface_water"
    raise ValueError(f"site type {t!r} not in the vocabulary -- material() is total over VALID_TYPES only")

VALID_TYPES = ("stream", "tidal_stream", "lake", "estuary", "well", "groundwater", "atmosphere", "storm_drain", "canal",
               "wetland_outflow", "tile_outlet", "ditch", "pond", "saturated_buffer",
               "saturated_waterway", "blind_inlet", "bioreactor", "field", "unknown")

# EVERY EXCLUSION MUST NAME A REAL TYPE. A typo in either set is silently permissive -- the type it meant to
# exclude keeps passing -- and both sets are edited by hand. Checked at import, like `schema`'s block and
# gauge-column closure checks, so the failure lands before any data is read.
_unknown = sorted((NO_SURFACE_BASIN | SUB_CATCHMENT) - set(VALID_TYPES))
if _unknown:
    raise ValueError(f"exclusion sets name types not in VALID_TYPES: {_unknown}")


def from_source_field(source: str, raw) -> str | None:
    """The source's own declaration, or None. Case- and vocabulary-insensitive; unmapped values return None."""
    if source != "USGS" or pd.isna(raw):
        return None
    return USGS_TYPE.get(str(raw).strip().lower())


def from_name(name) -> str | None:
    """A strong project descriptor in the station name, or None."""
    if pd.isna(name):
        return None
    low = str(name).lower()
    for pat, label in NAME_PATTERNS:
        if re.search(pat, low):
            return label
    return None


def prior_no_surface(source: str, raw, name) -> bool:
    """Can this site NOT have an upstream surface basin? The only typing acquisition needs.

    A PRIOR, NOT THE ANSWER. It sees only metadata -- exactly the classifier restricted to the question that matters before any snap exists. Not the only protection either: the CONUS cap excludes oversized basins from the AOE union afterwards; this saves the fetches and catches the case the cap cannot see, a lake handed a plausibly-sized basin that is not its drainage.
    """
    t = from_source_field(source, raw) or from_name(name)
    return t in NO_SURFACE_BASIN


# ---- the NHD evidence -------------------------------------------------------------------------------

# Flowline attributes the classifier reads. `WBAREACOMI` is the join key to the polygon; the rest are the weaker corroborating signals whose measured precision is in the module docstring.
_FLOWLINE_COLS = ["COMID", "FTYPE", "WBAreaType", "WBAREACOMI", "LakeFract", "Tidal", "StreamOrde"]


def reach_attrs(comids) -> pd.DataFrame:
    """The snapped reach's own attributes, keyed on `comid`. Column-projected pushdown."""
    import pyarrow.parquet as pq

    from ..acquire import network

    ids = [int(c) for c in comids if pd.notna(c)]
    t = pq.read_table(network._path("flowlines"), columns=_FLOWLINE_COLS,
                      filters=[("COMID", "in", ids)])
    d = t.to_pandas().rename(columns={"COMID": "comid"})
    return d.drop_duplicates("comid")


def waterbodies(wb_comids):
    """NHD waterbody polygons for the given waterbody COMIDs, EPSG:5070 -- a PURE READ of the acquired store.

    The fetch lives in `acquire.network.fetch_waterbodies` (A3); derivation never touches the network. The store's sidecar records which ids were REQUESTED, so "fetched and absent" (an NHDArea id -- expected, no polygon exists) is distinguishable from "never requested" (the store predates these snaps). Never-requested ids degrade softly: no rank-4 evidence for those sites, which is never worse than the guess they already carry, and the shortfall is printed so the next acquisition pass is asked for.
    """
    import geopandas as gpd

    from ..acquire import network

    ids = {int(w) for w in wb_comids if pd.notna(w) and int(w) > 0}
    empty = gpd.GeoDataFrame({"comid": pd.Series([], dtype="int64")},
                             geometry=gpd.GeoSeries([], crs=config.EQUAL_AREA))
    if not ids:
        return empty
    p = network.waterbodies_path()
    if not p.exists():
        print(f"  no waterbody store -- {len(ids)} referenced id(s) carry no rank-4 evidence "
              f"(run the a3 network step to fetch)")
        return empty
    sidecar = p.with_name(p.name + ".stamps.json")
    if sidecar.exists() and "requested" in sidecar.read_text():
        # the predecessor's by-id subset: coverage is whatever one cohort happened to ask for, so absent
        # ids mean nothing until the a3 network step rebuilds the full layer
        print("  waterbody store is the legacy by-id subset -- rank-4 evidence is partial until the a3 network step reruns")
    out = gpd.read_parquet(p)
    return out[out.comid.isin(ids)].reset_index(drop=True)

def _inside(sites: pd.DataFrame, water) -> dict:
    """site_uid -> the waterbody COMID whose polygon CONTAINS the site. The rank-3 test.

    Containment, not the reach's `WBAreaType`. That attribute says the reach threads a waterbody and is right only half the time on this cohort -- it calls the Susquehanna, the Maumee and the Illinois at Peoria lakes, because a wide river is coded as an area too. Whether the SITE is inside the polygon is the question that separates a lake gauge from a river gauge, and it needs the polygon.
    """
    import geopandas as gpd

    if water is None or not len(water):
        return {}
    pts = gpd.GeoDataFrame(
        {"site_uid": sites.site_uid.to_numpy()},
        geometry=gpd.points_from_xy(sites.lon, sites.lat), crs=4326).to_crs(config.EQUAL_AREA)
    j = pts.sjoin(water[["comid", "geometry"]], predicate="within", how="inner")
    return dict(zip(j.site_uid, j.comid))


# The type ledger: the generic kit with this module's vocabulary. Keyed on the sensor uid.
def type_ledger():
    import dataclasses

    from .. import ledger

    return dataclasses.replace(ledger.SITE_TYPE, vocabulary=tuple(VALID_TYPES), key_cols=("site_uid",))


def load_decisions() -> pd.DataFrame:
    """Human type decisions via the ledger kit: blank means NOT YET REVIEWED; unknown types raise."""
    d = type_ledger().load()
    if not len(d):
        return pd.DataFrame(columns=["site_uid", "decision"])
    out = d.copy()
    out["decision"] = out.decision.str.strip().str.lower()
    return out[["site_uid", "decision"]].reset_index(drop=True)


# ---- classification ---------------------------------------------------------------------------------

# Why a row needs a human. Ordered most-likely-wrong first, which is also the review panel's row order.
AMBIGUITY_ORDER = ("promote", "conflict_waterbody", "conflict_tidal", "merged_mismatch", "no_evidence", "reviewed")

# The reason codes are the classifier's vocabulary, not a reviewer's. Each row states its question in words
# instead, because "conflict_waterbody" does not tell you that the type was LEFT ALONE and you are being asked
# to overrule a source, while "promote" does not tell you that it was CHANGED and you are being asked to
# confirm. That asymmetry is the whole of what a reviewer needs and none of what the label conveys.
QUESTION = {
    "promote": "CHANGED to {t}: nothing was known, and the sensor falls inside a waterbody polygon. Right?",
    "conflict_waterbody": "KEPT as {t} ({src}), but the sensor falls inside a waterbody polygon. Which is right?",
    "conflict_tidal": "KEPT as {t} ({src}), but this reach is marked tidal. Tidally influenced?",
    "merged_mismatch": "MERGED with {others}; this member says {t} ({src}). One station publishes one type -- which?",
    "no_evidence": "KEPT as {t} by default: no evidence either way. What is it?",
    "reviewed": "DECIDED as {t} by review. The evidence that raised it is settled; change it here to revisit.",
}


def question(r) -> str:
    """The row's decision, phrased as the question being asked rather than as the rule that fired."""
    src = str(r.site_type_source).replace("_", " ")
    others = getattr(r, "mismatch_others", "") or "a bundle of differing types"
    return QUESTION[r.ambiguity].format(t=r.site_type, src=src, others=others)


def classify(sites: pd.DataFrame) -> pd.DataFrame:
    """Assign `site_type`, `site_type_source` and `site_type_conflict` for every registration.

    Six ranks, highest confidence first. The first that fires wins:

      1 usgs_site_type    the source's own declaration, long names or NWIS codes
      2 station_name      a strong project descriptor ("water supply well", "bioreactor")
      3 source_description the same descriptor in the source's own free text, for a source with no vocabulary
      4 nhd_waterbody     the site is INSIDE a waterbody polygon
      5 nhd_coastline     the snapped reach is Coastline
      6 nhd_streamriver   snapped to a StreamRiver reach, snap ok
      7 unknown           nothing fired -- reported by uid, never hidden

    RANKS 4-6 PROMOTE A GUESS; THEY DO NOT OVERRULE EVIDENCE. Where ranks 1-3 already answered and the NHD disagrees, the disagreement is recorded in `site_type_conflict` and the type is left alone. Applying it automatically would reclassify the Susquehanna, the Maumee and the Illinois at Peoria as lakes -- they are wide rivers threading area polygons -- and the cohort filter would then drop them. A human resolves those in `site-type-review/`.

    RANK 4 IS WEAKER THAN IT LOOKS. `waterbodies` queries the `nhdwaterbody` layer, but a reach's `WBAREACOMI` may name an NHDArea feature instead -- the wide-river polygons the Des Moines and the Iowa thread are `StreamRiver` NHDAreas, not LakePonds -- and those ids simply fail the lookup. So rank 4 cannot fire for the very sites the warning above is about, and a wide-river gauge reaches rank 6 rather than being flagged.
    """
    s = sites.copy()
    attrs = reach_attrs(s.comid.dropna().unique())
    m = s.merge(attrs, on="comid", how="left")

    water = waterbodies(m.WBAREACOMI)
    inside = _inside(m, water)

    stype, ssrc, conflict = [], [], []
    unmapped = []
    for r in m.itertuples():
        nhd_lake = r.site_uid in inside
        nhd_tidal = (r.Tidal == 1) or (r.FTYPE == "Coastline")
        t = from_source_field(r.source, r.site_type_raw)
        src = "usgs_site_type" if t else None
        if t is None and r.source == "USGS" and pd.notna(r.site_type_raw):
            unmapped.append((r.site_uid, str(r.site_type_raw)))
        if t is None:
            t = from_name(r.station_name)
            src = "station_name" if t else None
        if t is None:
            # THE SOURCE'S OWN WORDS, for a source with no type vocabulary. `from_source_field` answers only
            # for USGS, so for IWQIS and MPCA the carefully-assembled `site_type_raw` -- description plus
            # nickname -- was never read by anything. `from_name` is source-agnostic by construction, and a
            # descriptor is a descriptor whichever field it arrived in. Below `station_name` because a name is
            # chosen to identify the station while a description may be about the project around it.
            t = from_name(r.site_type_raw)
            src = "source_description" if t else None

        if t is None:                                   # no rank-1/2/3 evidence: the NHD decides
            if nhd_lake:
                t, src = "lake", "nhd_waterbody"
            elif r.FTYPE == "Coastline":
                t, src = "estuary", "nhd_coastline"
            elif r.FTYPE in ("StreamRiver", "ArtificialPath") and r.snap_status == "ok":
                # An ArtificialPath NOT inside a waterbody polygon (rank 4 already checked) is a wide
                # river threading an NHDArea -- a stream. Inside one it never reaches here.
                t, src = "stream", "nhd_flowline"
            elif r.FTYPE == "CanalDitch" and r.snap_status == "ok":
                t, src = "ditch", "nhd_flowline"
            else:
                # NEVER A DEFAULT. An unlabeled well silently typed `stream` crosses the material
                # boundary one rank before any gate can see it; `unknown` queues instead, and its
                # merge-gate pairs WAIT until a human answers.
                t, src = "unknown", "no_evidence"
        c = None                                        # evidence that disagrees, recorded not applied
        if src in AUTHORITATIVE:
            if nhd_lake and t not in ("lake", "pond"):
                c = "nhd_waterbody"
            elif nhd_tidal and t not in ("tidal_stream", "estuary"):
                c = "nhd_tidal"
        stype.append(t); ssrc.append(src); conflict.append(c)

    m["site_type"] = pd.Series(stype, dtype="string")
    m["site_type_source"] = pd.Series(ssrc, dtype="string")
    m["site_type_conflict"] = pd.Series(conflict, dtype="string")
    if unmapped:
        print(f"  WARNING: {len(unmapped)} USGS site_type_raw value(s) not in USGS_TYPE: "
              f"{sorted({v for _, v in unmapped})}")

    # RANK 0: A HUMAN DECISION OUTRANKS EVERY RULE. Without this the review is theatre -- the classifier
    # recomputes from evidence on every run and silently overwrites whatever was decided. Applying it also
    # clears `site_type_conflict`, which is what retires the row from `ambiguous` so the same question is not
    # asked again next run.
    dec = load_decisions()
    if len(dec):
        d = dict(zip(dec.site_uid, dec.decision))
        hit = m.site_uid.isin(d)
        m.loc[hit, "site_type"] = m.loc[hit, "site_uid"].map(d)
        m.loc[hit, "site_type_source"] = "human_review"
        m.loc[hit, "site_type_conflict"] = pd.NA
        changed = int((m.loc[hit, "site_type"] != pd.Series(stype, index=m.index)[hit]).sum())
        print(f"  {len(dec)} human type decision(s) applied ({changed} changed the assigned type)")
    return m[["site_uid", "site_type", "site_type_source", "site_type_conflict"]]


def _mask(v) -> "np.ndarray":
    """A pandas boolean of any backend as a plain numpy bool array, nulls read as False.

    THE BACKENDS DO NOT MIX. `registrations.parquet` round-trips through pyarrow, so a comparison on a string column returns an Arrow-backed boolean carrying NA, and `&`-ing that with a numpy or pandas-nullable boolean raises "boolean value of NA is ambiguous" from deep inside pandas, naming neither column. Every mask in `ambiguous` goes through here so the combination is over one representation and a null is a definite False.
    """
    import numpy as np

    return np.asarray(pd.Series(v).fillna(False).to_numpy(dtype="bool"), dtype=bool)


def _is(col, value: str) -> "np.ndarray":
    """`col == value` as a null-safe numpy mask."""
    return _mask(col.eq(value))


def auto_decide_types(amb: pd.DataFrame) -> int:
    """The type review's standing auto-rules, keyed on the EXACT (ambiguity, kept-type) pair; any other combination stays a human question. Ordinary overturnable sheet decisions like every auto verdict.

    TYPE RULE 1 -- `conflict_tidal` kept as `stream` -> `tidal_stream`, decided_by='auto (type rule 1)': the gauge keeps its stream nature but joins the tidal class, which NO_SURFACE_BASIN already routes out of basin modeling.

    TYPE RULE 2 -- `conflict_tidal` kept as `lake` -> `lake`, decided_by='auto (type rule 2)': a tidal flag on the reach does not make a lake anything else; the kept type is confirmed so the question stops resurfacing.

    TYPE RULE 3 -- `conflict_tidal` kept as `well`, `ditch`, `wetland_outflow`, `storm_drain` or `canal` -> the kept type stands, decided_by='auto (type rule 3)': tidal influence on the reach does not retype these either; a verdict of None in the rules table means "confirm whatever the row already says".
    """
    tled = type_ledger()
    done = {k[0] for k in tled.decided_keys()}
    rules = ((("stream",), "tidal_stream", "auto (type rule 1)",
              "stream close to a tidal body of water, possibly influenced by tides"),
             (("lake",), "lake", "auto (type rule 2)",
              "lake on a tidally-marked reach; the lake type stands"),
             (("well", "ditch", "wetland_outflow", "storm_drain", "canal"), None, "auto (type rule 3)",
              "tidal-marked reach; tidal influence does not retype this site"))
    n = 0
    for kept, verdict, who, note in rules:
        hot = amb[(amb.ambiguity == "conflict_tidal") & amb.get("site_type").isin(kept)]
        k = 0
        for r in hot.itertuples():
            if r.site_uid in done:
                continue
            done.add(r.site_uid)
            tled.decide((r.site_uid,), verdict or str(r.site_type), decided_by=who, note=note)
            k += 1
        if k:
            print(f"  {k} sensor(s) auto-typed ({who})")
        n += k
    return n


def ambiguous(sites: pd.DataFrame, include=(), mismatch=()) -> pd.DataFrame:
    """The rows a human should look at, with the reason each is ambiguous. Everything else is settled.

    Four reasons, and deliberately not "everything the NHD touched": a fall-through default that snapped to a StreamRiver reach is corroborated rather than questioned, so putting those in front of a reviewer would bury the real cases. Measured on the 15,924-registration census, this selects 24 -- 6 waterbody conflicts, 1 promotion, and 17 already-decided rows kept on the panel by `include`.

    `include` FORCES uids in whatever the current evidence says, tagged `reviewed`. Deciding a site clears the conflict that put it here, so without this a reviewed site vanishes from its own review the moment it is answered -- the sheet still holds the decision but the panel no longer shows what it was made about, and the row numbers shift under the reviewer. Every site that has ever been asked about stays on the panel.
    """
    a = sites.copy()
    attrs = reach_attrs(a.comid.dropna().unique())
    m = a.merge(attrs, on="comid", how="left")
    reason = pd.Series(pd.NA, index=m.index, dtype="object")
    reason[_is(m.site_type_source, "nhd_waterbody")] = "promote"
    reason[_is(m.site_type_conflict, "nhd_waterbody")] = "conflict_waterbody"
    reason[_is(m.site_type_conflict, "nhd_tidal")] = "conflict_tidal"
    if len(mismatch):
        reason[_mask(m.site_uid.isin(set(mismatch))) & reason.isna().to_numpy()] = "merged_mismatch"
        if isinstance(mismatch, dict):
            m["mismatch_others"] = m.site_uid.map(mismatch)
    reason[_is(m.site_type, "unknown") & reason.isna().to_numpy()] = "no_evidence"
    if len(include):
        reason[_mask(m.site_uid.isin(set(include))) & reason.isna().to_numpy()] = "reviewed"

    out = m[reason.notna()].copy()
    out["ambiguity"] = reason[reason.notna()].to_numpy()
    rank = {r: i for i, r in enumerate(AMBIGUITY_ORDER)}
    out["_k"] = out.ambiguity.map(rank).fillna(len(AMBIGUITY_ORDER))
    # WHAT IS OUTSTANDING SORTS TO THE TOP, so the work is always at row 1 and answering a site sinks it.
    # `include` is exactly the answered set -- `load_decisions` returns only rows carrying a verdict -- so
    # the queue orders itself without needing the decisions frame passed in.
    out["_done"] = out.site_uid.isin(set(include)).astype(int)
    return (out.sort_values(["_done", "_k", "site_uid"], ignore_index=True)
               .drop(columns=["_done", "_k"]))
