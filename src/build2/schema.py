"""The site-table column contract.

Every pipeline stage WIDENS this table; no stage removes a row. Selection is a query over these columns evaluated at modelling time -- `graph_node`, `train_label`, `claim_set` are predicates, never stored booleans, because storing them would make changing a role definition require a rebuild.

Every column declares the BLOCK that owns it, and `OWNERS` says per block who writes it and whether the answer depends on the AOE. Chunk 0 fills the `site` block, chunk 1 the `records` block, chunk 3 the `snap` and `merge` blocks, chunk 4 the `network` block. Each stage declares what it adds and never rewrites what came before.

The merge block is NULL until chunk 3 decides. It is tempting to seed it with singletons, but before identity runs both halves of a duplicate pair would claim `is_group_canonical = True` -- a false assertion that anything trusting the flag silently double-counts on. Null means "not yet decided", which is the truth.

NULL SEMANTICS. A column is present for every row always. Missing means "this source does not report it" and is written as a real null, never as an absent column, an empty string or a sentinel -- a downstream `notna()` has to mean something.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import params

# ---- uid ---------------------------------------------------------------------------------------
# site_uid = f"{SOURCE}:{native_id}". WQP re-emits NWIS identifiers verbatim, so a bare "USGS-05412500" is NOT unique across sources; the prefix makes every REGISTRATION unique and `physical_gauge_group` is what unifies registrations of one physical gauge.
UID_SEP = ":"
SOURCES = ("USGS", "IWQIS", "MPCA")

# Tie-break only. The canonical registration is the one with the MOST nitrate days; source order decides ties. Cedar River is why: MPCA holds 193,628 observations over 8.4 years and USGS 05457000 covers a 1.7-year window inside it, so a fixed IWQIS > USGS > MPCA order would have named the group after the registration supplying the smaller share.
CANONICAL_PRECEDENCE = {"IWQIS": 0, "USGS": 1, "MPCA": 2}


def make_uid(source: str, native_id: str) -> str:
    """`SOURCE:native_id`. native_id is kept as a STRING -- leading zeros are load-bearing (USGS:05412500)."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    n = str(native_id).strip()
    if not n:
        raise ValueError(f"empty native_id for source {source!r}")
    return f"{source}{UID_SEP}{n}"


def split_uid(uid: str) -> tuple[str, str]:
    """Inverse of make_uid. Splits on the FIRST separator only -- WQP native ids contain one themselves (WQP:USGS-05412500)."""
    source, _, native = str(uid).partition(UID_SEP)
    return source, native


def uid_to_filename(uid: str) -> str:
    """`:` is legal on POSIX but confuses Finder and every Windows tool, so paths use a doubled underscore."""
    return str(uid).replace(UID_SEP, "__")


# ---- block ownership ------------------------------------------------------------------------------
# Every column belongs to exactly one BLOCK, and the block declares who writes it and whether the answer depends on the AOE. Chunk 0 re-runs discovery from scratch, so anything a later chunk established has to be carried forward onto the fresh table -- and carrying it is only safe when the extent it was derived against has not moved.
#
# THIS EXISTS BECAUSE THE ALTERNATIVE FAILED SILENTLY. `run_chunk0` used to hold two hand-written lists of column names for exactly this purpose, which is a duplicate of the contract that nothing kept in step: `catchment_comid`, `detection_limit` and `nearest_gauge_m` were all absent from them, so each was nulled on every chunk-0 run with no error and no warning. Declaring the owner beside the column makes forgetting impossible, because there is only one place to write it down.
#
# Blocks are named after what they hold, NOT after a chunk number, so renumbering the pipeline does not churn the contract.
OWNERS: dict[str, dict] = {
    # written by chunk 0 itself on every run; never carried, always recomputed
    "site":     dict(carried=False, aoe_dependent=False),
    # the record structure, from per-site files keyed on site_uid. No extent anywhere in its derivation, so a new AOE does not invalidate it and re-deriving would mean a pointless pass over 334 parquets.
    "records":  dict(carried=True,  aoe_dependent=False),
    # which canonical channels a registration actually observes, measured off chunk 2's canonical files. Keyed on site_uid with no extent in its derivation, so it carries across a new AOE for the same reason `records` does.
    "canon":    dict(carried=True,  aoe_dependent=False),
    # snapped against the flowline layer CUT TO THE AOE, so carrying one across a new stamp preserves an answer derived from a different extent -- the staleness `aoe_stamp` exists to catch.
    "snap":     dict(carried=True,  aoe_dependent=True),
    # keyed on `comid`, so it inherits the snap's extent-dependence.
    "merge":    dict(carried=True,  aoe_dependent=True),
    # the non-nitrate gauge census, which walks the AOE network.
    "network":  dict(carried=True,  aoe_dependent=True),
    # what a site IS. Decided in chunk 3, not chunk 0, because the evidence that settles it -- the reach the
    # site snaps to and the waterbody that reach threads -- does not exist until the network is downloaded
    # and the snap is computed. Extent-dependent for the same reason: it reads a flowline layer cut to the AOE.
    "type":     dict(carried=True,  aoe_dependent=True),
    # chunk 6's verdict, written back onto the registration it excluded. The ONLY column a later chunk adds to
    # this table, and it earns the exception: without it "why is this site not in sites.parquet" is answerable
    # only by re-running the filter, which is precisely the question the old 116<->123 oscillation made
    # unanswerable. Never carried -- it is recomputed from scratch every time the filter runs, so a predicate
    # that stops firing clears the reason rather than leaving a stale one.
    "assembly": dict(carried=False, aoe_dependent=False),
}


# ---- the site-table column contract --------------------------------------------------------------
# (name, pandas dtype, owning block, one-line meaning). Order is the table's column order.
SITE_COLUMNS: list[tuple[str, str, str, str]] = [
    # identity
    ("site_uid",                "string",  "site", "SOURCE:native_id -- unique per registration"),
    ("source",                  "string",  "site", "USGS | IWQIS | MPCA"),
    ("source_site_id",          "string",  "site", "the source's own id, verbatim, never int()-ed"),
    ("agency",                  "string",  "site", "operating agency as the source reports it"),
    # AOE seeding -- chunk 0. `aoe_seed` says whose basin defined the extent, NOT who is in the cohort.
    ("aoe_seed",                "boolean", "site", "this registration's basin contributed to the AOE geometry"),
    ("seed_exclusion_reason",   "string",  "site", "why not a seed; null when aoe_seed is true"),
    ("nldi_basin_km2",          "float64", "site", "NLDI upstream basin area, seeds only"),
    ("nldi_basin_flag",         "string",  "site", "over_conus_cap where the basin is too large to be any in-scope site's"),
    ("nldi_basin_method",       "string",  "site", "basin0 authority | basin1 nearest-reach snap | basin1_far snap beyond 500 m"),
    # What the SOURCE advertises, before any record is fetched -- the only evidence chunk 0 has. Distinct from first_day/last_day, which the records block derives from what is actually on disk.
    ("nitrate_begin",           "string",  "site", "ISO date the source says its nitrate series starts"),
    ("nitrate_end",             "string",  "site", "ISO date the source says it ends"),
    # merge block. NULL until a decision exists; never optimistically singleton.
    ("physical_gauge_group",    "string",  "merge", "merge group; canonical uid of the group"),
    ("is_group_canonical",      "boolean", "merge", "this row is the group's name-bearer"),
    ("merge_rule",              "string",  "merge", "sequential | dual_registration | distinct | singleton"),
    ("merge_sep_m",             "float64", "merge", "separation to the paired registration, metres"),
    ("merge_shared_days",       "Int64",   "merge", "days both records observe"),
    ("merge_r",                 "float64", "merge", "Pearson r over shared days"),
    ("merge_median_diff",       "float64", "merge", "median(a - b) over shared days, mg/L"),
    ("merge_seam_date",         "string",  "merge", "sequential joins only: the gap the concatenation spans"),
    ("merge_decided_by",        "string",  "merge", "human | pending | n/a"),
    # location
    ("lat",                     "float64", "site", "WGS84 decimal degrees"),
    ("lon",                     "float64", "site", "WGS84 decimal degrees"),
    ("datum",                   "string",  "site", "horizontal datum as reported"),
    ("state",                   "string",  "site", "two-letter, lowercase"),
    ("county",                  "string",  "site", ""),
    ("huc",                     "string",  "site", "hydrologic unit code, as a string (leading zeros)"),
    ("tz",                      "string",  "site", "IANA zone used for local-day binning -- auditable on purpose"),
    # source description
    ("station_name",            "string",  "site", ""),
    ("source_drainage_area_km2", "float64", "site", "as reported by the source, NOT delineated"),
    ("altitude_m",              "float64", "site", ""),
    ("site_type_raw",           "string",  "site", "the source's own type string"),
    # record structure. `regime`, `pcodes` and `reported_units` come from the source's own metadata at discovery; the rest is measured off the records on disk.
    ("first_day",               "string",  "records", "ISO date, local calendar"),
    ("last_day",                "string",  "records", "ISO date, local calendar"),
    ("n_nitrate_days",          "Int64",   "records", "local days with >=1 nitrate observation"),
    ("longest_gap_d",           "Int64",   "records", "longest run of days with no nitrate"),
    ("regime",                  "string",  "site",    "continuous | grab"),
    ("pcodes",                  "string",  "site",    "'+'-joined parameter codes for nitrate at this site"),
    ("params_available",        "string",  "records", "'+'-joined every parameter the source reports here"),
    ("reported_units",          "string",  "site",    "nitrate units AS REPORTED; unconfirmed_* where undocumented"),
    # THE CENSUS BLOCK. `channels_declared` is what the source advertises before a byte is fetched, which is
    # what decides whether a site is worth fetching; `channels_present` (canon block) is what the data turned
    # out to hold. They differ, and the difference is a real signal -- a site declaring a series that returns
    # nothing is a discontinued sensor, not a fetch failure.
    ("channels_declared",       "string",  "site",    "'+'-joined canonical channels the source advertises"),
    ("census_source",           "string",  "site",    "nitrate | census -- which discovery pass found this site"),
    ("fetch_skip_reason",       "string",  "site",    "why chunk 1 will not fetch water here; NULL means it will"),
    ("detection_limit",         "float64", "records", ""),
    # snap. `comid` is the NEAREST REACH; `catchment_comid` is the polygon the point falls inside. Both are kept because they answer different questions and disagree exactly where it matters: on this cohort they agree on 363 of 388, and a disagreement means the sensor sits near a divide -- which is the case neither the merge rule nor a basin delineation should be asked to adjudicate blind.
    ("comid",                   "Int64",   "snap", "NHDPlus reach the sensor snaps to, nearest-line"),
    ("catchment_comid",         "Int64",   "snap", "COMID of the catchment CONTAINING the point"),
    ("comid_agree",             "boolean", "snap", "comid == catchment_comid; null where either is missing. False means the sensor sits near a divide"),
    ("snap_dist_m",             "float64", "snap", "distance to that reach, metres, on EPSG:5070"),
    ("snap_pos_m",              "float64", "snap", "distance ALONG the snapped reach from its upstream end, metres. Orders two gauges sharing one reach, which nothing else can: pathlength, totma and stream order are reach attributes and identical for both"),
    ("snap_status",             "string",  "snap", "ok | far -- far keeps the row and flags it"),
    ("snap_rule",               "string",  "snap", "which rule chose the reach: nearest | mainstem (a tie resolved toward the larger drainage) | area (the reported drainage area overruled a bad nearest). A surprising COMID is traceable to the rule that produced it rather than guessed at"),
    ("stream_order",            "Int64",   "snap", ""),
    ("stream_level",            "Int64",   "snap", ""),
    ("terminal_path",           "Int64",   "snap", "terminal path id, for network traversal"),
    ("pathlength_km",           "float64", "snap", "distance along the network to the terminal outlet"),
    ("totma_d",                 "float64", "snap", "cumulative travel time, days"),
    ("catchment_area_km2",      "float64", "snap", "the snapped reach's own catchment; NULL where it has none, 1.70% of reaches"),
    # derived type -- chunk 3. `site_type_raw` above is the source's own string and stays in `site`.
    ("site_type",               "string",  "type", "stream | tidal_stream | lake | estuary | well | groundwater | wetland_outflow | tile_outlet | ditch | pond | saturated_buffer | bioreactor | field | unknown"),
    ("site_type_source",        "string",  "type", "which rule decided it: usgs_site_type | station_name | nhd_waterbody | nhd_coastline | nhd_streamriver | *_default_stream"),
    ("site_type_conflict",      "string",  "type", "evidence that DISAGREES with the assigned type; null when nothing does. Never applied automatically -- a human decides it in site_type_decisions.csv"),
    # co-location -- unrecoverable later, and invisible to every holdout scheme
    ("nearest_nitrate_sensor_m", "float64", "site",    "distance to the nearest OTHER nitrate registration"),
    ("nearest_gauge_m",         "float64", "network", "distance to the nearest non-nitrate continuous gauge"),
    # assembly -- chunk 6's verdict on this registration
    ("excluded_reason",         "string",  "assembly", "why this registration is not in sites.parquet; NULL means it is"),
]

# ---- canonical channel coverage -- chunk 2 -------------------------------------------------------
# GENERATED FROM THE REGISTRY, NOT WRITTEN OUT, so adding a channel to `params.CHANNELS` adds its column
# here and nowhere else. Writing fourteen near-identical lines by hand is the same duplicated-contract
# mistake `OWNERS` exists to prevent -- one that already cost three silently-nulled columns.
#
# `channels_present` is what chunk 3's merge rule reads: two co-located sensors measuring DIFFERENT things
# are one node, and recognising that needs the channel sets, not the record. The per-channel day counts say
# whether a channel is usable here rather than merely present, which is a different question and the one a
# consumer deciding on a feature actually asks.
SITE_COLUMNS += [
    ("channels_present", "string", "canon", "'+'-joined canonical channels with at least one observation"),
    *((f"n_days_{c}", "Int64", "canon", f"standard-time days carrying a {c} observation")
      for c in params.NAMES),
]

COLUMNS = [c for c, _, _, _ in SITE_COLUMNS]
DTYPES = {c: d for c, d, _, _ in SITE_COLUMNS}
BLOCK = {c: b for c, _, b, _ in SITE_COLUMNS}

_unclaimed = sorted(c for c, b in BLOCK.items() if b not in OWNERS)
if _unclaimed:
    raise ValueError(f"columns with no owning block in OWNERS: {_unclaimed}")


def columns_in(**pred) -> list[str]:
    """Contract columns whose owning block matches every keyword, in table order.

    `columns_in(carried=True)` is what chunk 0 re-attaches after re-running discovery; `columns_in(aoe_dependent=True)` is what a changed `aoe_stamp` invalidates. Derived from the contract, so a new column joins the right set by declaring its block and nothing else.
    """
    return [c for c in COLUMNS if all(OWNERS[BLOCK[c]][k] == v for k, v in pred.items())]


def update_block(sites: pd.DataFrame, block: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Update one block on the site table from the rows a run produced. No registration is lost, and NO SITE THE RUN DID NOT TOUCH IS BLANKED.

    THE UPDATE IS THE POINT. The obvious spelling -- drop the block's columns and left-join the new frame -- is correct only when the frame covers every registration, and a run narrowed by `--sources` or by a site list does not. That version silently erased the record block for every site outside its filter: `n_nitrate_days` was set for 126 of 392 registrations while 342 had daily files on disk, so 232 sites held a record and no statistics describing it, and nothing downstream noticed because `chunk6.assemble.collapse` recomputes the span from the files rather than trusting the column.

    Lives here rather than in a chunk because every chunk that owns a block needs exactly this rule, and two copies of it is the duplicated contract `OWNERS` exists to prevent.
    """
    if block is None or not len(block):
        return sites
    keep = [c for c in cols if c in block.columns]
    if not keep:
        return sites
    out = sites.set_index("site_uid")
    fresh = block.drop_duplicates("site_uid").set_index("site_uid")[keep]
    for c in keep:
        if c not in out.columns:
            out[c] = pd.NA
    # `update` writes only where the new frame HAS a row, so a site outside this run keeps what it had.
    out.update(fresh)
    return out.reset_index()


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=d) for c, d, _, _ in SITE_COLUMNS})


def _conform(df: pd.DataFrame, columns: list[str], dtypes: dict[str, str], label: str) -> pd.DataFrame:
    """Reindex to a contract and cast. Missing columns become real nulls; unknown columns are an ERROR, not a silent drop -- a typo'd column name must not vanish."""
    extra = [c for c in df.columns if c not in dtypes]
    if extra:
        raise ValueError(f"columns not in the {label} contract: {extra}")
    out = df.reindex(columns=columns)
    for c, d in dtypes.items():
        try:
            out[c] = out[c].astype(d)
        except (TypeError, ValueError) as e:
            raise TypeError(f"column {c!r} will not cast to {d}: {e}") from None
    return out


def null_column(name: str, index, dtypes: dict | None = None) -> pd.Series:
    """An all-null Series of the column's DECLARED dtype.

    `frame[col] = pd.NA` LOOKS dtype-neutral AND IS NOT. It builds an object column, and nine of the contract's columns -- `snap_dist_m`, `merge_sep_m`, `catchment_area_km2` and six others -- are plain numpy `float64`, which has no NA. The cast in `_conform` then dies with "float() argument must be a string or a real number, not 'NAType'", nowhere near the assignment that caused it. That is precisely what happened the first time an AOE stamp actually moved and `_invalidate_stale` blanked all 26 extent-dependent columns.

    `None` is the neutral null the assignment was reaching for: pandas turns it into NaN for float64, NaT for datetimes and <NA> for every nullable extension type.
    """
    d = (dtypes or DTYPES).get(name)
    return pd.Series([None] * len(index), index=index, dtype=d) if d else pd.Series(pd.NA, index=index)


def conform(df: pd.DataFrame) -> pd.DataFrame:
    """Reindex to the REGISTRATION contract and cast. See `conform_gauges` for the other table."""
    return _conform(df, COLUMNS, DTYPES, "registration")


def validate(df: pd.DataFrame) -> list[str]:
    """Contract violations, as a list of human-readable problems. Empty list means conforming."""
    bad = []
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        bad.append(f"missing columns: {missing}")
    if "site_uid" in df.columns:
        if df.site_uid.isna().any():
            bad.append("site_uid has nulls")
        dup = df.site_uid[df.site_uid.duplicated()].unique().tolist()
        if dup:
            bad.append(f"site_uid not unique: {dup[:8]}{'...' if len(dup) > 8 else ''}")
        badsrc = sorted({split_uid(u)[0] for u in df.site_uid.dropna()} - set(SOURCES))
        if badsrc:
            bad.append(f"uids with unknown source prefix: {badsrc}")
    for c in ("lat", "lon"):
        if c in df.columns and df[c].isna().any():
            bad.append(f"{c} has nulls -- every registration must have coordinates")
    return bad


# ---- the gauge-table contract ----------------------------------------------------------------------
# `sites.parquet`: one row per TRAINABLE PHYSICAL GAUGE, written once by chunk 6 and read by nothing
# inside the build. Distinct from the registration contract above in two ways that matter.
#
# THE UNIT IS A GAUGE, NOT A REGISTRATION. Merged registrations collapse to a single row keyed on the
# canonical uid, so a consumer never learns that `Middle Raccoon River at Panora` was published twice.
# `member_uids` and `n_registrations` keep the composition visible without making it the key.
#
# THE RECORD COLUMNS DESCRIBE THE COMBINED SERIES. `first_day`, `last_day`, `n_nitrate_days` and
# `longest_gap_d` are computed over the merged record, not copied from the canonical member -- a
# sequential merge spans both halves, and reporting the canonical member's span would understate it by
# however long the other registration ran.
#
# Everything else is the canonical member's, INCLUDING THE COORDINATES. Merged registrations sit metres
# apart so the choice is nearly free, but it is a choice: the gauge is placed where its name-bearer is.
GAUGE_COLUMNS: list[tuple[str, str, str]] = [
    # identity and composition
    ("site_uid",                "string",  "canonical uid; the gauge id"),
    ("member_uids",             "string",  "'+'-joined registrations composing this gauge, canonical first"),
    ("n_registrations",         "Int64",   "1 for an unmerged gauge"),
    ("source",                  "string",  "canonical member's source"),
    ("source_site_id",          "string",  "canonical member's native id"),
    ("agency",                  "string",  ""),
    # merge provenance -- how this gauge came to be one row
    ("merge_rule",              "string",  "sequential | dual_registration | singleton"),
    ("merge_seam_date",         "string",  "sequential joins only: the gap the concatenation spans"),
    # location, from the canonical member
    ("lat",                     "float64", "WGS84 decimal degrees"),
    ("lon",                     "float64", "WGS84 decimal degrees"),
    ("state",                   "string",  "two-letter, lowercase"),
    ("county",                  "string",  ""),
    ("huc",                     "string",  "hydrologic unit code, as a string (leading zeros)"),
    ("tz",                      "string",  "IANA zone; binning uses STANDARD_TZ regardless"),
    # description
    ("station_name",            "string",  ""),
    ("site_type",               "string",  "trainable types only -- see NOT_TRAINABLE for what is excluded"),
    ("site_type_source",        "string",  "how the type was decided; *_default_stream means inferred"),
    ("source_drainage_area_km2", "float64", "as reported by the source, NOT delineated"),
    ("altitude_m",              "float64", ""),
    # record structure, over the COMBINED series
    ("first_day",               "string",  "ISO date, earliest across all members"),
    ("last_day",                "string",  "ISO date, latest across all members"),
    ("n_nitrate_days",          "Int64",   "distinct days with an observation, deduplicated across members"),
    ("longest_gap_d",           "Int64",   "longest run with no observation, across the merged record"),
    ("regime",                  "string",  "continuous | grab"),
    ("reported_units",          "string",  "nitrate units AS REPORTED"),
    # network position, from the canonical member
    ("comid",                   "Int64",   "NHDPlus reach the gauge snaps to"),
    ("catchment_comid",         "Int64",   "COMID of the catchment containing the point"),
    ("comid_agree",             "boolean", "False means the gauge sits near a divide"),
    ("snap_dist_m",             "float64", "distance to that reach, metres"),
    ("stream_order",            "Int64",   ""),
    ("stream_level",            "Int64",   ""),
    ("terminal_path",           "Int64",   "terminal path id, for network traversal"),
    ("pathlength_km",           "float64", "distance along the network to the terminal outlet"),
    ("totma_d",                 "float64", "cumulative travel time, days"),
    ("catchment_area_km2",      "float64", "the snapped reach's own catchment"),
    # co-location -- invisible to every holdout scheme, so it is recorded rather than inferred
    ("nearest_nitrate_sensor_m", "float64", "distance to the nearest OTHER nitrate gauge"),
    ("nearest_gauge_m",         "float64", "distance to the nearest non-nitrate continuous gauge"),
    # the basin chunk 4 selected, carried so a consumer never opens a second table to size a watershed
    ("basin_method",            "string",  "basin0 | basin1 | basin2 | basin3 -- which delineation was selected"),
    ("basin_km2",               "float64", "the selected basin's area"),
    ("log_basin_km2",           "float64", "log10 of it; the cohort spans 60 km2 to 3.1M, nearly five decades"),
    ("area_rank",               "Int64",   "1 is the largest basin IN THE FILTERED COHORT; ties share a rank and the next one skips, so a two-way tie for second gives 1, 2, 2, 4"),
    ("n_reaches",               "Int64",   "catchments composing the basin"),
    ("n_corroborations",        "Int64",   "independent delineations agreeing with the selected one"),
    ("corroborated_by",         "string",  "which ones; NULL means nothing corroborates it"),
    # assembly
    ("gridmet_cells_eff",       "float64", "effective gridMET cells behind this gauge's weather: (sum w)^2 / sum(w^2) over its basin's cell weights. NOT a count of cells touched -- it reports how many genuinely contribute, and a basin smaller than one cell lands near 1.0"),
    ("assembly_status",         "string",  "ok, or why this gauge is unusual despite passing every filter"),
    ("covariate_years",         "string",  "the span its covariates actually cover, which differs by product"),
]

GAUGE_COLS = [c for c, _, _ in GAUGE_COLUMNS]
GAUGE_DTYPES = {c: d for c, d, _ in GAUGE_COLUMNS}

# Every gauge column except the ones computed at collapse time must exist on the registration contract,
# or chunk 6 would be inventing a value rather than carrying one. Checked at import, not at run time.
# Computed at collapse time or carried from an artifact OTHER than the registration table (chunk 4's basins),
# so the import-time orphan check must not demand a registration-contract source for them.
_GAUGE_DERIVED = {"member_uids", "n_registrations",
                  "basin_method", "basin_km2", "log_basin_km2", "area_rank", "n_reaches",
                  "n_corroborations", "corroborated_by", "assembly_status", "covariate_years",
                  "gridmet_cells_eff"}
_orphan = sorted(c for c in GAUGE_COLS if c not in DTYPES and c not in _GAUGE_DERIVED)
if _orphan:
    raise ValueError(f"gauge columns with no registration-contract source: {_orphan}")


# ---- the node-table contract -------------------------------------------------------------------
# `nodes.parquet`: one row per VALID GRAPH NODE -- a gauge whose type drains a surface catchment and whose
# snap landed on a reach. It is the gauge contract plus what the graph knows, and nothing else: a node is a
# gauge seen from the network's point of view, not a different object.
#
# WHY THE GRAPH COLUMNS LIVE HERE AND NOT ON `gauges`. They only exist for nodes -- a well has no position
# in a surface flow network -- and they are chunk 4's to write. Putting them on the gauge table would mean
# every well carrying six permanently-null columns and chunk 3 declaring fields it cannot fill.
NODE_EXTRA: list[tuple[str, str, str]] = [
    ("node_below",                "string",  "the node immediately downstream; NULL at an outlet"),
    ("n_immediate_upstream",      "Int64",   "nodes flowing DIRECTLY into this one -- in-degree"),
    ("n_upstream_nodes",          "Int64",   "nodes anywhere above, the whole closure"),
    ("n_downstream_nodes",        "Int64",   "nodes between this one and its outlet"),
    ("component_outlet",          "string",  "uid of the outlet naming this weakly-connected component"),
    ("component_size",            "Int64",   "nodes in that component"),
    ("flow_dist_below_km",        "float64", "channel distance to `node_below`, read off the edge table -- summed reach lengths, defined across terminal paths"),
    ("flow_dist_nearest_node_km", "float64", "the same distance to the nearest node in either direction"),
    # Summaries of the edge below, so a model reading only this table still sees what the edge crosses.
    ("crosses_impoundment_below", "boolean", "the edge to `node_below` threads a waterbody or passes a dam"),
    ("dam_storage_below_af",      "float64", "summed NID maxStorage on that edge, acre-feet"),
]

NODE_COLUMNS = GAUGE_COLUMNS + NODE_EXTRA
NODE_COLS = [c for c, _, _ in NODE_COLUMNS]
NODE_DTYPES = {c: d for c, d, _ in NODE_COLUMNS}


def conform_nodes(df: pd.DataFrame) -> pd.DataFrame:
    """Reindex to the NODE contract and cast."""
    return _conform(df, NODE_COLS, NODE_DTYPES, "node")


def validate_nodes(df: pd.DataFrame) -> list[str]:
    """Node-contract violations. Everything the gauge table must satisfy, plus the graph's own invariants."""
    bad = validate_gauges(df, surface_only=True)
    if "node_below" in df.columns:
        known = set(df.site_uid)
        orphan = sorted(set(df.node_below.dropna()) - known)
        if orphan:
            bad.append(f"node_below points outside the node set: {orphan[:8]}")
        self_edge = df.site_uid[df.site_uid == df.node_below].tolist()
        if self_edge:
            bad.append(f"a node is its own downstream neighbour: {self_edge[:8]}")
    return bad


# ---- the edge-table contract -------------------------------------------------------------------
# `node_edges.parquet`: one row per M -> N, where water leaving M reaches N without passing another node.
#
# THE EDGE IS AN OBJECT, NOT A POINTER. It carried three columns while the walk that produced it visited
# every reach in between and discarded them -- so two gauges straddling Saylorville Dam looked exactly like
# two gauges on 26 km of open channel, and a model over the network could not learn a relationship the
# network itself records. Everything below is derived from `graph.label`'s `via`, which costs nothing extra.
#
# `via_comids` and `waterbody_comids` are ','-joined strings, matching the '+'-joined convention that
# `member_uids`, `channels_present` and `corroborated_by` already use: they survive `_conform`'s cast
# unchanged and need no list-typed column.
EDGE_COLUMNS: list[tuple[str, str, str]] = [
    ("from_uid",              "string",  "upstream node"),
    ("to_uid",                "string",  "the node immediately downstream"),
    ("n_reaches_between",     "Int64",   "intervening reaches; 0 for two nodes sharing one reach"),
    ("via_comids",            "string",  "','-joined intervening COMIDs, upstream-first"),
    ("via_truncated",         "boolean", "the walk hit MAX_EDGE_HOPS; `via_comids` is partial"),
    ("flow_dist_km",          "float64", "channel distance, SUMMED lengthkm -- never a pathlength difference"),
    ("travel_time_d",         "float64", "summed per-reach totma; NOT pathtimema, which accumulates residence. UNDERSTATES ACROSS AN IMPOUNDMENT -- NHDPlus writes totma 0 on reservoir reaches, so 19.5 km of Saylorville sums to 0.10 d. Residence is the dam columns' job, not this one"),
    ("same_terminal_path",    "boolean", "both ends drain to the same terminal outlet"),
    ("min_stream_order",      "Int64",   "smallest order on the path -- a dip means the walk left the mainstem"),
    ("max_totda_km2",         "float64", "largest accumulated drainage area on the path"),
    ("n_artificial_path",     "Int64",   "ArtificialPath reaches -- NHDPlus's synthetic threads through water bodies"),
    ("n_connector",           "Int64",   "Connector reaches -- where flow crosses a structure"),
    ("n_waterbody_reaches",   "Int64",   "reaches carrying a WBAREACOMI"),
    ("waterbody_comids",      "string",  "','-joined distinct waterbodies threaded"),
    ("threads_impoundment",   "boolean", "n_waterbody_reaches > 0"),
    ("n_dams",                "Int64",   "NID dams in the intervening catchments; NULL only if dams.parquet is absent"),
    ("dam_max_storage_af",    "float64", "summed NID maxStorage, acre-feet, NID's own unit"),
    ("dam_normal_storage_af", "float64", "summed NID normalStorage, acre-feet"),
    ("dam_max_height_m",      "float64", "tallest dam on the path"),
    ("dam_first_year",        "Int64",   "earliest completion year -- a dam does not exist before it is built"),
    ("crosses_impoundment",   "boolean", "threads_impoundment or n_dams > 0"),
    ("basin_area_ratio",      "float64", "min(basin)/max(basin) over the pair; NULL where either has no basin"),
    ("near_duplicate_basin",  "boolean", "basin_area_ratio above graph.NEAR_DUPLICATE_TOL"),
]
EDGE_COLS = [c for c, _, _ in EDGE_COLUMNS]
EDGE_DTYPES = {c: d for c, d, _ in EDGE_COLUMNS}

_dupe = sorted({c for c in EDGE_COLS if EDGE_COLS.count(c) > 1})
if _dupe or EDGE_COLS[:2] != ["from_uid", "to_uid"]:
    raise ValueError(f"edge contract malformed: duplicates {_dupe}, leads with {EDGE_COLS[:2]}")


def conform_edges(df: pd.DataFrame) -> pd.DataFrame:
    """Reindex to the EDGE contract and cast."""
    return _conform(df, EDGE_COLS, EDGE_DTYPES, "edge")


def validate_edges(df: pd.DataFrame, nodes: pd.DataFrame | None = None) -> list[str]:
    """Edge-contract violations. What the walk asserts, checked rather than assumed."""
    bad = []
    missing = [c for c in EDGE_COLS if c not in df.columns]
    if missing:
        return [f"missing columns: {missing}"]
    if not len(df):
        return bad
    if nodes is not None:
        known = set(nodes.site_uid)
        for col in ("from_uid", "to_uid"):
            orphan = sorted(set(df[col].dropna()) - known)
            if orphan:
                bad.append(f"{col} points outside the node set: {orphan[:8]}")
    self_edge = df.from_uid[df.from_uid == df.to_uid].tolist()
    if self_edge:
        bad.append(f"an edge leaves and enters the same node: {self_edge[:8]}")
    dup = df[df.duplicated(["from_uid", "to_uid"])]
    if len(dup):
        bad.append(f"duplicate edges: {list(zip(dup.from_uid, dup.to_uid))[:8]}")

    n_between = pd.to_numeric(df.n_reaches_between, errors="coerce")
    if (n_between < 0).any():
        bad.append("n_reaches_between is negative")
    dist = pd.to_numeric(df.flow_dist_km, errors="coerce")
    # A ZERO DISTANCE ACROSS INTERVENING REACHES IS THE SENTINEL ARTEFACT, not a measurement: differencing
    # two -9998 pathlengths produced exactly this, and it read as "these adjacent nodes are 0 km apart".
    # Summing per-reach lengths cannot produce it, so this check is what keeps it from coming back.
    z = df[(dist == 0) & (n_between > 0)]
    if len(z):
        bad.append(f"flow_dist_km is 0 across intervening reaches: "
                   f"{list(zip(z.from_uid, z.to_uid))[:8]}")
    if (dist < 0).any() or not np.isfinite(dist.dropna()).all():
        bad.append("flow_dist_km is negative or non-finite")

    n_dams = pd.to_numeric(df.n_dams, errors="coerce")
    orphan_dam = df[(n_dams > 0) & (df.via_comids.isna() | (n_between <= 0))]
    if len(orphan_dam):
        bad.append(f"a dam is counted on an edge with no path to attribute it to: "
                   f"{list(zip(orphan_dam.from_uid, orphan_dam.to_uid))[:8]}")
    ci = df.crosses_impoundment.fillna(False).astype(bool)
    inconsistent = df[ci & ~(df.threads_impoundment.fillna(False).astype(bool) | (n_dams.fillna(0) > 0))]
    if len(inconsistent):
        bad.append(f"crosses_impoundment set with neither a waterbody nor a dam: "
                   f"{list(zip(inconsistent.from_uid, inconsistent.to_uid))[:8]}")
    over = df[(pd.to_numeric(df.n_artificial_path, errors="coerce").fillna(0)
               + pd.to_numeric(df.n_connector, errors="coerce").fillna(0)) > n_between]
    if len(over):
        bad.append(f"more typed reaches than reaches on the path: "
                   f"{list(zip(over.from_uid, over.to_uid))[:8]}")
    return bad


def conform_gauges(df: pd.DataFrame) -> pd.DataFrame:
    """Reindex to the GAUGE contract and cast. Mirrors `conform` for the other table."""
    return _conform(df, GAUGE_COLS, GAUGE_DTYPES, "gauge")


def validate_gauges(df: pd.DataFrame, surface_only: bool = True) -> list[str]:
    """Gauge-contract violations, as human-readable problems. Empty list means conforming.

    Checks what is specific to this table rather than repeating the registration checks: one row per gauge, a resolvable composition, and -- where `surface_only` -- no non-surface type.

    `surface_only=False` IS FOR `gauges.parquet`, which chunk 3 writes over every decided registration whatever its type: a well is a physical gauge and belongs in the table of physical gauges. Excluding non-surface types is a statement about what can be MODELLED, so it belongs to `sites.parquet` at the end of the pipeline, not to the unit itself.
    """
    from .chunk3.site_type import NOT_TRAINABLE

    bad = []
    missing = [c for c in GAUGE_COLS if c not in df.columns]
    if missing:
        bad.append(f"missing columns: {missing}")
        return bad
    if df.site_uid.isna().any():
        bad.append("site_uid has nulls")
    dup = df.site_uid[df.site_uid.duplicated()].unique().tolist()
    if dup:
        bad.append(f"site_uid not unique -- a gauge appears twice: {dup[:8]}")
    if surface_only:
        off = sorted(set(df.site_type.dropna()) & NOT_TRAINABLE)
        if off:
            bad.append(f"untrainable site_type present, which the eligibility filter should have removed: {off}")
    if (df.n_registrations < 1).any():
        bad.append("n_registrations below 1")
    # The canonical uid must be among its own members, or the composition does not resolve.
    orphan = [u for u, m in zip(df.site_uid, df.member_uids)
              if pd.notna(m) and u not in str(m).split("+")]
    if orphan:
        bad.append(f"canonical uid missing from its own member_uids: {orphan[:8]}")
    return bad
