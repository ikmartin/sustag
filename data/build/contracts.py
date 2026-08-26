"""Identifier namespaces, column-block ownership, and the stamp family.

TWO IDENTIFIER NAMESPACES, VISIBLY DISTINCT. A sensor uid is `{SOURCE}:{native_id}` -- the raw archive's key, never unique without the prefix (WQP re-emits NWIS identifiers verbatim). The PUBLISHED id is `SNR-XXXX` (base-36), minted once per registration by the append-only id ledger (`ids.py`) and never derived from anything mutable -- not position, not membership, not sort order. The prefix exists so nothing published can be mistaken for a source's own unprocessed series.

COLUMN BLOCKS DECLARE AN OWNER OR THE IMPORT FAILS. Every column on the sensors table belongs to exactly one block, and each block declares which stage writes it, whether it is carried across a re-discovery, and whether it depends on the extent. The alternative -- hand-written column lists at each call site -- once silently nulled three columns on every discovery run.

THE STAMP FAMILY answers "derived under which rules": `snapshot` (which raw state), `aoe` (which extent), `namespace` (which uid rule cut a split), `schema` (which columns -- the fingerprint), `registry` (which reduction rules), `ledgers` (which decisions). `io.write_parquet`/`read_parquet` carry and check them; this module computes them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

SOURCES = ("USGS", "IWQIS", "MPCA")

# Tie-break ONLY -- record length in the deciding channel wins first. On the Cedar River the MPCA and
# USGS registrations of one gauge differ by years of record, and naming the bundle after the shorter
# series would hide the longer one behind a non-canonical uid.
CANONICAL_PRECEDENCE = {"IWQIS": 0, "USGS": 1, "MPCA": 2}


def make_uid(source: str, native_id: str) -> str:
    """`{SOURCE}:{native_id}`. The native id stays a string -- leading zeros are identity."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    native_id = str(native_id).strip()
    if not native_id:
        raise ValueError("empty native id")
    return f"{source}:{native_id}"


def split_uid(uid: str) -> tuple[str, str]:
    """Partition on the FIRST separator only -- WQP native ids contain one themselves."""
    source, _, native = str(uid).partition(":")
    return source, native


def uid_to_filename(uid: str) -> str:
    return str(uid).replace(":", "__")


# ---- column blocks --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Block:
    """One owned group of columns on a contracted table."""

    owner: str                 # stage that writes it, e.g. "A2", "D3"
    carried: bool              # survives a re-discovery of the row set
    aoe_dependent: bool        # nulled when the extent stamp moves


def columns_in(columns: list[tuple], owners: dict[str, Block], **pred) -> list[str]:
    """Column names whose block matches every predicate, e.g. `columns_in(cols, owners, carried=True)`."""
    names = []
    for entry in columns:
        name, block = entry[0], entry[2]
        b = owners[block]
        if all(getattr(b, k) == v for k, v in pred.items()):
            names.append(name)
    return names


def check_blocks(columns: list[tuple], owners: dict[str, Block], label: str) -> None:
    """Import-time invariant: every column's block exists in the owners map. Raises, before any data."""
    unclaimed = sorted({entry[2] for entry in columns} - set(owners))
    if unclaimed:
        raise ValueError(f"{label}: columns declare blocks with no owner: {unclaimed}")


# ---- the stamp family -----------------------------------------------------------------------------

@dataclass
class Stamps:
    """What an artifact was derived under. Serialised into parquet metadata by `io.write_parquet`."""

    snapshot: str | None = None
    aoe: str | None = None
    namespace: str | None = None
    schema: str | None = None
    registry: str | None = None
    ledgers: str | None = None
    extra: dict = field(default_factory=dict)

    def asdict(self) -> dict[str, str]:
        out = {k: v for k, v in (("snapshot", self.snapshot), ("aoe", self.aoe),
                                 ("namespace", self.namespace), ("schema", self.schema),
                                 ("registry", self.registry), ("ledgers", self.ledgers)) if v is not None}
        out.update({str(k): str(v) for k, v in self.extra.items()})
        return out


def schema_fingerprint(columns) -> str:
    """Fingerprint of a column contract: names + dtypes, order-independent, 12 hex chars.

    THE STAMP THE AOE HASH CANNOT BE. An artifact cut against the right extent but by older code is missing columns in a way no row count reveals, and every `getattr(row, col, nan)` downstream degrades to a silent null. The fingerprint changes when the contract does, so the cache rebuilds instead of lying.

    Accepts `[(name, dtype, ...), ...]` tuples or a `{name: dtype}` mapping.
    """
    if isinstance(columns, dict):
        pairs = sorted((str(k), str(v)) for k, v in columns.items())
    else:
        pairs = sorted((str(c[0]), str(c[1])) for c in columns)
    spec = ",".join(f"{n}:{d}" for n, d in pairs)
    return hashlib.sha256(spec.encode()).hexdigest()[:12]


def ledger_hash(paths) -> str:
    """One hash over the decision ledgers a build consumed, order-independent by filename.

    Whole-file granularity: any append invalidates everything derived under the old hash. Over-invalidation accepted -- rebuilds are the norm and a finer consumed-row scheme is machinery this stage does not need yet.
    """
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda p: p.name):
        h.update(p.name.encode())
        h.update(p.read_bytes() if p.exists() else b"<absent>")
    return h.hexdigest()[:12]


# ---- the sensors table ----------------------------------------------------------------------------
# One row per REGISTRATION, forever: every stage only widens it, and a sensor missing from any published
# set is explicable from this one table. Blocks are named for what they hold, owned by the stage that
# writes them; the block map is the single source for carry-forward and extent-invalidation.

SENSOR_OWNERS: dict[str, Block] = {
    "discovery": Block("A1/A2", carried=False, aoe_dependent=False),   # rebuilt each census pass
    "mint":      Block("A2",    carried=True,  aoe_dependent=False),   # the SNR id -- minted once, never rebuilt
    "fetch":     Block("A3",    carried=True,  aoe_dependent=False),   # fetch accounting
    "canon":     Block("D2",    carried=True,  aoe_dependent=False),   # measured off the records
    "snap":      Block("D3",    carried=True,  aoe_dependent=True),    # off a flowline layer cut to the AOE
    "type":      Block("D4",    carried=True,  aoe_dependent=True),
    "bundle":    Block("D4",    carried=True,  aoe_dependent=True),    # follows identity
}

SENSOR_COLUMNS: list[tuple[str, str, str, str]] = [
    # identity
    ("site_uid",                 "string",  "discovery", "SOURCE:native_id -- unique per registration"),
    ("source",                   "string",  "discovery", "USGS | IWQIS | MPCA"),
    ("source_site_id",           "string",  "discovery", "the source's own id, verbatim, never int()-ed"),
    ("agency",                   "string",  "discovery", "operating agency as the source reports it"),
    # AOE seeding -- A1. `aoe_seed` says whose basin defined the extent, NOT who is in any cohort.
    ("aoe_seed",                 "boolean", "discovery", "this registration's basin contributed to the AOE geometry"),
    ("seed_exclusion_reason",    "string",  "discovery", "why not a seed; null when aoe_seed is true"),
    ("nldi_basin_km2",           "float64", "discovery", "NLDI upstream basin area, seeds only"),
    ("nldi_basin_flag",          "string",  "discovery", "over_conus_cap where the basin is too large to be any in-scope site's"),
    ("nldi_basin_method",        "string",  "discovery", "basin0 authority | basin1 nearest-reach snap | basin1_far"),
    # what the SOURCE advertises, before any record is fetched
    ("nitrate_begin",            "string",  "discovery", "ISO date the source says its nitrate series starts"),
    ("nitrate_end",              "string",  "discovery", "ISO date the source says it ends"),
    # location. Stated coordinates are the closest thing to ground truth of sensor position; declared
    # precision is captured HERE, at acquisition, because parsing to float destroys it everywhere else.
    ("lat",                      "float64", "discovery", "WGS84 decimal degrees; null ONLY with location_missing_reason"),
    ("lon",                      "float64", "discovery", "WGS84 decimal degrees"),
    ("coord_dp_lat",             "Int64",   "discovery", "decimal places the source PRINTS for lat -- the rounding half-width primitive; null where the source text is unavailable"),
    ("coord_dp_lon",             "Int64",   "discovery", "decimal places the source prints for lon"),
    ("location_missing_reason",  "string",  "discovery", "why this registration has no coordinates (e.g. coordinate_sentinel(-99.0)); null for located sensors"),
    ("datum",                    "string",  "discovery", "horizontal datum as reported"),
    ("state",                    "string",  "discovery", "two-letter, lowercase"),
    ("county",                   "string",  "discovery", ""),
    ("huc",                      "string",  "discovery", "hydrologic unit code, as a string (leading zeros)"),
    ("tz",                       "string",  "discovery", "IANA zone recorded for audit; binning uses STANDARD_TZ"),
    ("station_name",             "string",  "discovery", ""),
    ("source_drainage_area_km2", "float64", "discovery", "as reported by the source, NOT delineated"),
    ("altitude_m",               "float64", "discovery", ""),
    ("site_type_raw",            "string",  "discovery", "the source's own type string"),
    ("regime",                   "string",  "discovery", "continuous | intermittent | grab"),
    ("pcodes",                   "string",  "discovery", "'+'-joined parameter codes for nitrate at this site"),
    ("reported_units",           "string",  "discovery", "nitrate units AS REPORTED"),
    ("detection_limit",          "float64", "discovery", ""),
    ("channels_declared",        "string",  "discovery", "'+'-joined canonical channels the source advertises -- decides the fetch; a declared series that returns nothing is a discontinued sensor, not a fetch failure"),
    ("declared_begin",           "string",  "discovery", "earliest declared series begin across ALL channels (ISO date)"),
    ("declared_end",             "string",  "discovery", "latest declared series end across ALL channels (ISO date)"),
    ("census_source",            "string",  "discovery", "nitrate | census -- which discovery pass found this sensor"),
    ("canary",                   "boolean", "discovery", "on the canary roster: admitted through every scope filter to keep its class exercised"),
    ("fetch_skip_reason",        "string",  "discovery", "why A3 will not fetch water here; NULL means it will"),
    ("nearest_registration_m",   "float64", "discovery", "distance to the nearest OTHER registration in the same discovery pass -- relational, unrecoverable later"),
    # the published id -- A2, minted once by the id ledger
    ("snr_id",                   "string",  "mint", "SNR-XXXX base-36 published id; minted once, never reused, never re-derived"),
    # fetch accounting -- A3. The four-state partition: registered = skipped + cached + source_empty + attempt_failed.
    ("fetch_status",             "string",  "fetch", "skipped | cached | source_empty | attempt_failed"),
    ("fetch_last_attempt",       "string",  "fetch", "ISO timestamp of the most recent fetch attempt"),
    ("fetch_last_success",       "string",  "fetch", "ISO timestamp of the most recent attempt that returned data"),
    ("params_available",         "string",  "fetch", "'+'-joined every parameter the archived native holds"),
    # snap -- D3. `comid` is the NEAREST REACH; `catchment_comid` the polygon containing the point. Both
    # kept because they disagree exactly where it matters: a disagreement means the sensor sits near a divide.
    ("comid",                    "Int64",   "snap", "NHDPlus reach the sensor snaps to, nearest-line"),
    ("catchment_comid",          "Int64",   "snap", "COMID of the catchment CONTAINING the point"),
    ("comid_agree",              "boolean", "snap", "comid == catchment_comid; False reads NEAR A DIVIDE"),
    ("snap_dist_m",              "float64", "snap", "distance to that reach, metres, on EPSG:5070 -- CONTEXT for reach reliability, never positional uncertainty"),
    ("snap_pos_m",               "float64", "snap", "distance ALONG the reach from its upstream end -- orders sensors and structures sharing a reach"),
    ("snap_status",              "string",  "snap", "ok | far | no_location -- far keeps the row and flags it; no_location is the coordinate-sentinel class"),
    ("snap_rule",                "string",  "snap", "nearest | mainstem | area -- a surprising COMID is traceable to the rule that produced it"),
    ("stream_order",             "Int64",   "snap", ""),
    ("stream_level",             "Int64",   "snap", ""),
    ("terminal_path",            "Int64",   "snap", "terminal path id, for network traversal"),
    ("pathlength_km",            "float64", "snap", "distance along the network to the terminal outlet"),
    ("totma_d",                  "float64", "snap", "cumulative travel time, days"),
    ("catchment_area_km2",       "float64", "snap", "the snapped reach's own catchment; NULL where it has none"),
    # type -- D4
    ("site_type",                "string",  "type", "from the type vocabulary; human review outranks every rule; NEVER defaulted -- an untyped sensor is `unknown` and queues"),
    ("site_type_source",         "string",  "type", "which rank decided it"),
    ("site_type_conflict",       "string",  "type", "evidence that DISAGREES; never applied automatically"),
    # bundle -- D4. NULL until a decision exists; never optimistically singleton -- both halves of a
    # duplicate claiming to be canonical is a false assertion, not a missing one.
    ("bundle",                   "string",  "bundle", "'+'-joined sorted member uids of this sensor's bundle -- the ledger key"),
    ("publishes_under",          "string",  "bundle", "the SNR id fronting this sensor's published record: its own for singletons, the canonical member's for merged bundles; NULL while pending or excluded"),
    ("is_bundle_canonical",      "boolean", "bundle", "this sensor fronts the bundle"),
    ("bundle_rule",              "string",  "bundle", "complementary | sequential | dual_registration | distinct | singleton"),
    ("bundle_decided_by",        "string",  "bundle", "human | rule | pending | n/a"),
    ("excluded_reason",          "string",  "bundle", "sensor-exclusion ledger verdict; NULL means bundled normally"),
]

# canonical channel coverage -- D2. GENERATED FROM THE REGISTRY, so adding a channel adds its column here
# and nowhere else; sixteen hand-written near-identical lines is the duplicated-contract mistake that
# already cost three silently-nulled columns once.
def _canon_columns():
    from . import registry

    return [("channels_present", "string", "canon",
             "'+'-joined canonical channels with at least one observation"),
            *((f"n_days_{c}", "Int64", "canon", f"standard-time days carrying a {c} observation")
              for c in registry.NAMES)]


SENSOR_COLUMNS += _canon_columns()

SENSOR_COLS = [c for c, _, _, _ in SENSOR_COLUMNS]
SENSOR_DTYPES = {c: d for c, d, _, _ in SENSOR_COLUMNS}

check_blocks([(c, d, b) for c, d, b, _ in SENSOR_COLUMNS], SENSOR_OWNERS, "sensors")
_dupe = sorted({c for c in SENSOR_COLS if SENSOR_COLS.count(c) > 1})
if _dupe:
    raise ValueError(f"sensors contract declares duplicate columns: {_dupe}")


def conform_sensors(df):
    """Reindex to the sensors contract and cast. Unknown columns are an ERROR, never a silent drop; missing ones become real nulls of the declared dtype."""
    extra = [c for c in df.columns if c not in SENSOR_DTYPES]
    if extra:
        raise ValueError(f"columns not in the sensors contract: {extra}")
    out = df.reindex(columns=SENSOR_COLS)
    for c, d in SENSOR_DTYPES.items():
        try:
            out[c] = out[c].astype(d)
        except (TypeError, ValueError) as e:
            raise TypeError(f"column {c!r} will not cast to {d}: {e}") from None
    return out


def validate_sensors(df) -> list[str]:
    """Contract violations as human-readable problems. Empty means conforming."""
    bad = []
    missing = [c for c in SENSOR_COLS if c not in df.columns]
    if missing:
        return [f"missing columns: {missing}"]
    if df.site_uid.isna().any():
        bad.append("site_uid has nulls")
    dup = df.site_uid[df.site_uid.duplicated()].unique().tolist()
    if dup:
        bad.append(f"site_uid not unique: {dup[:8]}")
    badsrc = sorted({split_uid(u)[0] for u in df.site_uid.dropna()} - set(SOURCES))
    if badsrc:
        bad.append(f"uids with unknown source prefix: {badsrc}")
    # A registration may lack coordinates ONLY as a recorded fact -- the coordinate-sentinel class. A null
    # location with no reason is a parsing loss, which is exactly what this check exists to catch.
    noloc = df.lat.isna() | df.lon.isna()
    unexplained = noloc & df.location_missing_reason.isna()
    if unexplained.any():
        bad.append(f"null coordinates without location_missing_reason: {df.site_uid[unexplained].head(8).tolist()}")
    return bad


def null_sensor_column(name: str, index):
    """An all-null Series of the column's DECLARED dtype. `frame[c] = pd.NA` builds an object column, and the float64 cast then dies far from the assignment."""
    import pandas as pd

    return pd.Series([None] * len(index), index=index, dtype=SENSOR_DTYPES[name])


def update_block(sensors, block, cols: list[str]):
    """Update one block on the sensors table from the rows a run produced. No row lost, NO UNTOUCHED ROW BLANKED.

    THE UPDATE IS THE POINT. Drop-the-columns-then-left-join is correct only when the new frame covers every registration; a run narrowed by source or by shard once silently erased the record block for 232 sensors -- they held daily files on disk and no statistics describing them. `DataFrame.update` writes only where the new frame HAS a row.
    """
    import pandas as pd

    if block is None or not len(block):
        return sensors
    keep = [c for c in cols if c in block.columns]
    if not keep:
        return sensors
    out = sensors.set_index("site_uid")
    fresh = block.drop_duplicates("site_uid").set_index("site_uid")[keep]
    for c in keep:
        if c not in out.columns:
            out[c] = pd.NA
    out.update(fresh)
    return out.reset_index()
