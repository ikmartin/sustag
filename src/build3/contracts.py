"""Identity minting, column-block ownership, and the stamp family.

TWO IDENTIFIER NAMESPACES, VISIBLY DISTINCT. A sensor uid is `{SOURCE}:{native_id}` -- the raw archive's key, never unique without the prefix (WQP re-emits NWIS identifiers verbatim). A site id is `S{comid}-{pos:04d}` -- a POSITION ON A REACH, minted by the build after the bundle is decided, sortable into flow order along a reach by construction. Off-network sites (failed snaps, non-surface types) get `OFF-{canonical member uid}`: visibly non-positional, so nothing downstream can mistake one for a node.

COLUMN BLOCKS DECLARE AN OWNER OR THE IMPORT FAILS. Every column on a contracted table belongs to exactly one block, and each block declares which stage writes it, whether it is carried across a re-discovery, and whether it depends on the extent. The alternative -- hand-written column lists at each call site -- is how build2 silently nulled three columns on every chunk-0 run.

THE STAMP FAMILY answers "derived under which rules": `snapshot` (which raw state), `aoe` (which extent), `namespace` (which uid rule cut a split), `schema` (which columns -- the fingerprint), `ledgers` (which decisions). `io.write_parquet`/`read_parquet` carry and check them; this module computes them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

SOURCES = ("USGS", "IWQIS", "MPCA")

# Tie-break ONLY -- record length in the deciding channel wins first. On the Cedar River the MPCA and
# USGS registrations of one gauge differ by years of record, and naming the bundle after the shorter
# series would hide the longer one behind a non-canonical uid.
CANONICAL_PRECEDENCE = {"IWQIS": 0, "USGS": 1, "MPCA": 2}

_SITE_ID_RE = re.compile(r"^S(\d+)-(\d+)$")
_OFF_ID_RE = re.compile(r"^OFF-(.+)$")


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


def make_site_id(comid: int, snap_pos_m: float) -> str:
    """`S{comid}-{pos}` with the position in whole metres, zero-padded to sort lexically within a reach.

    METRE PRECISION IS THE CLAIM, NOT A CONVENIENCE. Coarser rounding would assert that near-coincident sensors are one site -- a hypothesis, and hypotheses about identity go to review, not into the key. Two distinct sites resolving to the same id is a contradiction the build halts on, not a collision to handle.
    """
    if comid is None or (isinstance(comid, float) and comid != comid):
        raise ValueError("cannot mint a network site id without a comid")
    pos = int(round(float(snap_pos_m)))
    if pos < 0:
        raise ValueError(f"negative snap position {snap_pos_m}")
    return f"S{int(comid)}-{pos:05d}"


def make_off_network_id(canonical_member_uid: str) -> str:
    """`OFF-{uid}` for a site with no network position. Accepts that registration identity leaks into a rare, graph-less class."""
    return f"OFF-{canonical_member_uid}"


def parse_site_id(site_id: str) -> tuple[int, int] | None:
    """`(comid, pos_m)` for a network site id; None for the OFF namespace."""
    m = _SITE_ID_RE.match(str(site_id))
    return (int(m.group(1)), int(m.group(2))) if m else None


def is_network_site(site_id: str) -> bool:
    return _SITE_ID_RE.match(str(site_id)) is not None


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
    ledgers: str | None = None
    extra: dict = field(default_factory=dict)

    def asdict(self) -> dict[str, str]:
        out = {k: v for k, v in (("snapshot", self.snapshot), ("aoe", self.aoe),
                                 ("namespace", self.namespace), ("schema", self.schema),
                                 ("ledgers", self.ledgers)) if v is not None}
        out.update({str(k): str(v) for k, v in self.extra.items()})
        return out


def schema_fingerprint(columns) -> str:
    """Fingerprint of a column contract: names + dtypes, order-independent, 12 hex chars.

    THE STAMP THE AOE HASH CANNOT BE. An artifact cut against the right extent but by older code is missing columns in a way no row count reveals -- `dams.parquet` carried a current `aoe_stamp` while lacking `max_height`, and every `getattr(row, col, nan)` downstream degraded to a silent null. The fingerprint changes when the contract does, so the cache rebuilds instead of lying.

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
# writes them; the block map is the single source for carry-forward and extent-invalidation, because
# hand-written column lists at call sites are how three columns were once silently nulled on every run.

SENSOR_OWNERS: dict[str, Block] = {
    "discovery": Block("A1/A2", carried=False, aoe_dependent=False),   # rebuilt each census pass
    "fetch":     Block("A3",    carried=True,  aoe_dependent=False),   # fetch bookkeeping
    "canon":     Block("D2",    carried=True,  aoe_dependent=False),   # measured off the records
    "snap":      Block("D3",    carried=True,  aoe_dependent=True),    # off a flowline layer cut to the AOE
    "type":      Block("D3",    carried=True,  aoe_dependent=True),
    "bundle":    Block("D3",    carried=True,  aoe_dependent=True),    # follows the snap
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
    # location
    ("lat",                      "float64", "discovery", "WGS84 decimal degrees"),
    ("lon",                      "float64", "discovery", "WGS84 decimal degrees"),
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
    ("nearest_nitrate_sensor_m", "float64", "discovery", "distance to the nearest OTHER nitrate registration -- relational, unrecoverable later, invisible to every holdout scheme"),
    # fetch bookkeeping -- A3
    ("fetch_status",             "string",  "fetch", "fetched | refreshed | cached | no_data | no_channels | error"),
    ("params_available",         "string",  "fetch", "'+'-joined every parameter the archived native holds"),
    # snap -- D3. `comid` is the NEAREST REACH; `catchment_comid` the polygon containing the point. Both
    # kept because they disagree exactly where it matters: a disagreement means the sensor sits near a divide.
    ("comid",                    "Int64",   "snap", "NHDPlus reach the sensor snaps to, nearest-line"),
    ("catchment_comid",          "Int64",   "snap", "COMID of the catchment CONTAINING the point"),
    ("comid_agree",              "boolean", "snap", "comid == catchment_comid; False reads NEAR A DIVIDE"),
    ("snap_dist_m",              "float64", "snap", "distance to that reach, metres, on EPSG:5070"),
    ("snap_pos_m",               "float64", "snap", "distance ALONG the reach from its upstream end -- the id primitive; the only thing that orders two sensors sharing a reach"),
    ("snap_status",              "string",  "snap", "ok | far -- far keeps the row and flags it"),
    ("snap_rule",                "string",  "snap", "nearest | mainstem | area -- a surprising COMID is traceable to the rule that produced it"),
    ("stream_order",             "Int64",   "snap", ""),
    ("stream_level",             "Int64",   "snap", ""),
    ("terminal_path",            "Int64",   "snap", "terminal path id, for network traversal"),
    ("pathlength_km",            "float64", "snap", "distance along the network to the terminal outlet"),
    ("totma_d",                  "float64", "snap", "cumulative travel time, days"),
    ("catchment_area_km2",       "float64", "snap", "the snapped reach's own catchment; NULL where it has none"),
    # type -- D3
    ("site_type",                "string",  "type", "from the type vocabulary; human review outranks every rule"),
    ("site_type_source",         "string",  "type", "which rank decided it"),
    ("site_type_conflict",       "string",  "type", "evidence that DISAGREES; never applied automatically"),
    # bundle -- D3. NULL until a decision exists; never optimistically singleton -- both halves of a
    # duplicate claiming to be canonical is a false assertion, not a missing one.
    ("bundle",                   "string",  "bundle", "'+'-joined sorted member uids of this sensor's bundle -- the ledger key"),
    ("site_id",                  "string",  "bundle", "the minted site id this sensor belongs to (display; the bundle is the key)"),
    ("is_bundle_canonical",      "boolean", "bundle", "this sensor names the bundle"),
    ("bundle_rule",              "string",  "bundle", "complementary | sequential | dual_registration | distinct | singleton"),
    ("bundle_decided_by",        "string",  "bundle", "human | rule | pending | n/a"),
    ("excluded_reason",          "string",  "bundle", "sensor-exclusion ledger verdict; NULL means bundled normally"),
]

# canonical channel coverage -- D2. GENERATED FROM THE REGISTRY, so adding a channel adds its column here
# and nowhere else; fourteen hand-written near-identical lines is the duplicated-contract mistake that
# already cost three silently-nulled columns once.
def _canon_columns():
    from . import registry

    return [("channels_present", "string", "canon",
             "'+'-joined canonical channels with at least one observation"),
            *((f"n_days_{c}", "Int64", "canon", f"standard-time days carrying a {c} observation")
              for c in registry.NAMES)]


SENSOR_COLUMNS += _canon_columns()

# ---- the site table -- D3, minted once ------------------------------------------------------------
# Grain: one row per SITE (a decided bundle with at least one canonical record). The bundle -- the
# '+'-joined sorted member-uid set -- is the durable key every site-scoped ledger uses; `site_id` is the
# positional display id derived from the site's own authoritative snap. Per-channel spans are generated
# from the registry so temporal graph contraction can read them without opening a single record file.

def _site_span_columns():
    from . import registry

    return [*((f"n_days_{c}", "Int64", "record", f"days on the MERGED site record carrying {c}")
              for c in registry.NAMES),
            *((f"first_{c}", "string", "record", f"first day of the merged {c} record (ISO date)")
              for c in registry.NAMES),
            *((f"last_{c}", "string", "record", f"last day of the merged {c} record (ISO date)")
              for c in registry.NAMES)]


SITE_COLUMNS: list[tuple[str, str, str, str]] = [
    ("site_id",             "string",  "identity", "S{comid}-{pos:05d} on-network, OFF-{uid} otherwise"),
    ("bundle",              "string",  "identity", "'+'-joined sorted member uids -- THE ledger key"),
    ("n_members",           "Int64",   "identity", ""),
    ("canonical_uid",       "string",  "identity", "member with the most high-tier record days; names the site"),
    ("authority_uid",       "string",  "identity", "member whose location the site snapped from"),
    ("authority_rank",      "string",  "identity", "declared_colloc | measured_colloc | longest_record"),
    ("station_name",        "string",  "identity", "the authority member's station name"),
    ("on_network",          "boolean", "identity", "surface-network node candidate; False = OFF- namespace"),
    ("lat",                 "float64", "location", "the authority member's coordinates"),
    ("lon",                 "float64", "location", ""),
    # the site's OWN snap -- computed once from the authority location, after the bundle is decided
    ("comid",               "Int64",   "snap", ""),
    ("catchment_comid",     "Int64",   "snap", ""),
    ("comid_agree",         "boolean", "snap", ""),
    ("snap_dist_m",         "float64", "snap", ""),
    ("snap_pos_m",          "float64", "snap", "the id primitive"),
    ("snap_status",         "string",  "snap", "ok | far; OFF sites may carry no snap at all"),
    ("snap_rule",           "string",  "snap", ""),
    ("stream_order",        "Int64",   "snap", ""),
    ("stream_level",        "Int64",   "snap", ""),
    ("terminal_path",       "Int64",   "snap", ""),
    ("pathlength_km",       "float64", "snap", ""),
    ("totma_d",             "float64", "snap", ""),
    ("catchment_area_km2",  "float64", "snap", ""),
    ("site_type",           "string",  "type", "the canonical member's type; member disagreements recorded"),
    ("site_type_source",    "string",  "type", ""),
    ("site_type_conflict",  "string",  "type", "member types that disagree, '+'-joined; NULL when unanimous"),
    ("channels_present",    "string",  "record", "'+'-joined channels on the merged record"),
]
SITE_COLUMNS += _site_span_columns()

SITE_COLS = [c for c, _, _, _ in SITE_COLUMNS]
SITE_DTYPES = {c: d for c, d, _, _ in SITE_COLUMNS}
_dupe_site = sorted({c for c in SITE_COLS if SITE_COLS.count(c) > 1})
if _dupe_site:
    raise ValueError(f"sites contract declares duplicate columns: {_dupe_site}")


# ---- the graph contracts -- D4 --------------------------------------------------------------------
# THE COMPOSITION RULE IS PART OF THE COLUMN, not documentation beside it. Access derives DAG variants by
# EDGE CONTRACTION -- drop B from A -> B -> C and the variant needs the composed edge A -> C -- so every
# edge attribute must state how it composes along a contracted path, and `compose_edges` refuses an
# attribute with no rule. That is the chapter's law, executable.
#
# EXACTNESS COMES FROM THE CHARGE SET. Every summable attribute is measured over the edge's charge set:
# the FROM-node's own reach plus the strictly-intervening reaches -- empty for a co-reach pair. Charge
# sets tile the channel (consecutive edges' sets are disjoint and their union is the full path), so `sum`,
# `min`, `max` and `or` compose EXACTLY under contraction; nothing is lost when a node is dropped. This
# differs from build2, whose edges measured only the strictly-between reaches and silently lost the
# dropped node's own reach on every contraction.
#
# Rules: `key` structural (contracted edge takes e1's from, e2's to); `sum`/`or`/`min`/`max` elementwise
# over the charge sets; `via` concatenates the between-lists inserting the dropped node's reach unless it
# is an endpoint reach; `recount` = len of the composed via; `endpoints` recomputes from the two endpoint
# NODES (pair diagnostics -- composing them along the path would be meaningless).

EDGE_COLUMNS: list[tuple[str, str, str, str]] = [
    ("from_id",               "string",  "key", "upstream node (site id)"),
    ("to_id",                 "string",  "key", "the node immediately downstream"),
    ("from_comid",            "Int64",   "key", "the from-node's reach -- needed by the via insertion rule"),
    ("to_comid",              "Int64",   "key", "the to-node's reach"),
    ("n_reaches_between",     "Int64",   "recount", "strictly-intervening reaches; 0 for a co-reach pair"),
    ("via_comids",            "string",  "via", "','-joined strictly-intervening COMIDs, upstream-first"),
    ("via_truncated",         "boolean", "or", "the walk hit MAX_EDGE_HOPS; `via_comids` is partial"),
    ("flow_dist_km",          "float64", "sum", "summed lengthkm over the charge set -- never a pathlength difference"),
    ("travel_time_d",         "float64", "sum", "summed per-reach totma over the charge set; NOT pathtimema, which accumulates reservoir residence. UNDERSTATES ACROSS AN IMPOUNDMENT -- NHDPlus writes totma 0 on reservoir reaches; residence is the dam columns' job"),
    ("same_terminal_path",    "boolean", "endpoints", "both ends drain to the same terminal outlet"),
    ("min_stream_order",      "Int64",   "min", "smallest order on the charge set -- a dip means the walk left the mainstem"),
    ("max_totda_km2",         "float64", "max", "largest accumulated drainage area on the charge set"),
    ("n_artificial_path",     "Int64",   "sum", "ArtificialPath reaches -- NHDPlus's synthetic threads through water bodies"),
    ("n_connector",           "Int64",   "sum", "Connector reaches -- where flow crosses a structure"),
    ("n_waterbody_reaches",   "Int64",   "sum", "charge-set reaches carrying a WBAREACOMI"),
    ("waterbody_comids",      "string",  "via", "','-joined distinct waterbodies threaded (union under contraction)"),
    ("threads_impoundment",   "boolean", "or", "n_waterbody_reaches > 0"),
    ("n_dams",                "Int64",   "sum", "NID dams in the charge-set catchments; NULL only if the dam table is absent"),
    ("dam_max_storage_af",    "float64", "sum", "summed NID maxStorage, acre-feet, NID's own unit"),
    ("dam_normal_storage_af", "float64", "sum", "summed NID normalStorage, acre-feet"),
    ("dam_max_height_m",      "float64", "max", "tallest dam on the charge set"),
    ("dam_first_year",        "Int64",   "min", "earliest completion year -- a dam does not exist before it is built"),
    ("crosses_impoundment",   "boolean", "or", "threads_impoundment or n_dams > 0"),
    ("basin_area_ratio",      "float64", "endpoints", "min(basin)/max(basin) over the pair; NULL where either has no basin"),
    ("near_duplicate_basin",  "boolean", "endpoints", "basin_area_ratio above the near-duplicate tolerance"),
]
EDGE_COLS = [c for c, _, _, _ in EDGE_COLUMNS]
EDGE_DTYPES = {c: d for c, d, _, _ in EDGE_COLUMNS}
EDGE_COMPOSE = {c: r for c, d, r, _ in EDGE_COLUMNS}

_LEGAL_COMPOSE = {"key", "sum", "or", "and", "min", "max", "via", "recount", "endpoints"}
_bad_rule = sorted(c for c, r in EDGE_COMPOSE.items() if r not in _LEGAL_COMPOSE)
if _bad_rule or EDGE_COLS[:2] != ["from_id", "to_id"]:
    raise ValueError(f"edge contract malformed: bad rules {_bad_rule}, leads with {EDGE_COLS[:2]}")


def compose_edges(e1, e2, nodes=None):
    """The contracted edge A -> C from A -> B and B -> C, per each attribute's declared rule.

    `e1`/`e2` are edge rows (dict or Series); `nodes` is the node/site frame indexed by site_id, needed only by the `endpoints` rules -- pass None to leave those columns NULL. Any edge column without a composition rule raises: an attribute that cannot state its rule does not go on the edge.
    """
    import numpy as np
    import pandas as pd

    g1 = dict(e1) if not isinstance(e1, dict) else e1
    g2 = dict(e2) if not isinstance(e2, dict) else e2
    if str(g1["to_id"]) != str(g2["from_id"]):
        raise ValueError(f"edges do not chain: {g1['from_id']}->{g1['to_id']} then {g2['from_id']}->{g2['to_id']}")
    extra = sorted((set(g1) | set(g2)) - set(EDGE_COMPOSE))
    if extra:
        raise ValueError(f"edge attribute(s) with no composition rule: {extra}")

    def _num(v):
        return np.nan if v is None or pd.isna(v) else float(v)

    # the dropped node's reach is inserted into the via unless it is an endpoint reach (co-reach pairs)
    b_comid = g1.get("to_comid")
    insert = (pd.notna(b_comid)
              and b_comid != g1.get("from_comid") and b_comid != g2.get("to_comid"))
    def _split(v):
        # `v or ""` is NOT a null guard here -- float NaN is truthy, and str(nan) is a "reach" called nan.
        return [] if v is None or pd.isna(v) else [x for x in str(v).split(",") if x]

    v1, v2 = _split(g1.get("via_comids")), _split(g2.get("via_comids"))
    via = v1 + ([str(int(b_comid))] if insert else []) + v2

    out = {}
    for c, rule in EDGE_COMPOSE.items():
        a, b = g1.get(c), g2.get(c)
        if rule == "key":
            out[c] = {"from_id": g1["from_id"], "to_id": g2["to_id"],
                      "from_comid": g1.get("from_comid"), "to_comid": g2.get("to_comid")}[c]
        elif rule == "sum":
            va, vb = _num(a), _num(b)
            out[c] = np.nan if np.isnan(va) and np.isnan(vb) else float(np.nansum([va, vb]))
        elif rule == "or":
            out[c] = bool(a) or bool(b)
        elif rule == "and":
            out[c] = bool(a) and bool(b)
        elif rule in ("min", "max"):
            va, vb = _num(a), _num(b)
            fin = [x for x in (va, vb) if not np.isnan(x)]
            out[c] = (min(fin) if rule == "min" else max(fin)) if fin else pd.NA
        elif rule == "via":
            if c == "via_comids":
                out[c] = ",".join(via) or None
            else:                                     # waterbody_comids: ordered union
                w = _split(a) + _split(b)
                out[c] = ",".join(dict.fromkeys(w)) or None
        elif rule == "recount":
            out[c] = len(via)
        elif rule == "endpoints":
            out[c] = pd.NA if nodes is None else _endpoint_diag(c, g1["from_id"], g2["to_id"], nodes)
    for c in ("n_reaches_between", "min_stream_order", "n_dams", "dam_first_year",
              "from_comid", "to_comid"):
        if out.get(c) is not None and not pd.isna(out.get(c)):
            out[c] = int(out[c])
    return out


def _endpoint_diag(col: str, a: str, b: str, nodes):
    """One `endpoints`-rule value, recomputed from the two endpoint rows -- no network walk."""
    import numpy as np
    import pandas as pd

    ra, rb = nodes.loc[a], nodes.loc[b]
    if col == "same_terminal_path":
        return bool(pd.notna(ra.get("terminal_path")) and ra.get("terminal_path") == rb.get("terminal_path"))
    aa, ab = ra.get("basin_km2"), rb.get("basin_km2")
    ratio = (min(float(aa), float(ab)) / max(float(aa), float(ab))
             if pd.notna(aa) and pd.notna(ab) and max(float(aa), float(ab)) > 0 else np.nan)
    if col == "basin_area_ratio":
        return ratio
    if col == "near_duplicate_basin":
        return bool(ratio > NEAR_DUPLICATE_TOL) if not np.isnan(ratio) else False
    raise KeyError(col)


# Two nodes whose basins agree this closely are either an impoundment pair -- the same water above and
# below a structure, which is signal -- or a mis-snap, which is an error. The ratio cannot tell them
# apart; `crosses_impoundment` beside it can.
NEAR_DUPLICATE_TOL = 0.95


def conform_edges(df):
    """Reindex to the edge contract and cast; unknown columns raise -- an attribute with no rule stays off the edge."""
    import pandas as pd

    extra = [c for c in df.columns if c not in EDGE_DTYPES]
    if extra:
        raise ValueError(f"columns not in the edge contract (no composition rule): {extra}")
    out = df.reindex(columns=EDGE_COLS)
    for c, d in EDGE_DTYPES.items():
        try:
            out[c] = out[c].astype(d)
        except (TypeError, ValueError) as e:
            raise TypeError(f"edge column {c!r} will not cast to {d}: {e}") from None
    return out


def validate_edges(df, nodes=None) -> list[str]:
    """Edge-contract violations. What the walk asserts, checked rather than assumed."""
    import numpy as np
    import pandas as pd

    bad = []
    missing = [c for c in EDGE_COLS if c not in df.columns]
    if missing:
        return [f"missing columns: {missing}"]
    if not len(df):
        return bad
    if nodes is not None:
        known = set(nodes.site_id)
        for col in ("from_id", "to_id"):
            orphan = sorted(set(df[col].dropna()) - known)
            if orphan:
                bad.append(f"{col} points outside the node set: {orphan[:8]}")
    self_edge = df.from_id[df.from_id == df.to_id].tolist()
    if self_edge:
        bad.append(f"an edge leaves and enters the same node: {self_edge[:8]}")
    dup = df[df.duplicated(["from_id", "to_id"])]
    if len(dup):
        bad.append(f"duplicate edges: {list(zip(dup.from_id, dup.to_id))[:8]}")
    n_between = pd.to_numeric(df.n_reaches_between, errors="coerce")
    if (n_between < 0).any():
        bad.append("n_reaches_between is negative")
    dist = pd.to_numeric(df.flow_dist_km, errors="coerce")
    # A ZERO DISTANCE ACROSS INTERVENING REACHES IS THE SENTINEL ARTEFACT, not a measurement: differencing
    # two -9998 pathlengths once produced exactly this. Summing per-reach lengths cannot.
    z = df[(dist == 0) & (n_between > 0)]
    if len(z):
        bad.append(f"flow_dist_km is 0 across intervening reaches: {list(zip(z.from_id, z.to_id))[:8]}")
    if (dist < 0).any() or not np.isfinite(dist.dropna()).all():
        bad.append("flow_dist_km is negative or non-finite")
    return bad


# The node table: one row per VALID GRAPH NODE, carrying ONLY what the graph knows. Everything else about
# the site -- location, type, snap, spans -- lives on `sites.parquet`; the chapter's shape rule is that no
# column appears in two artifacts except keys, and a node is a site seen from the network's point of view.
NODE_COLUMNS: list[tuple[str, str, str]] = [
    ("site_id",                   "string",  "the site this node is"),
    ("node_below",                "string",  "the node immediately downstream; NULL at an outlet"),
    ("n_immediate_upstream",      "Int64",   "nodes flowing DIRECTLY into this one -- in-degree"),
    ("n_upstream_nodes",          "Int64",   "nodes anywhere above, the whole closure"),
    ("n_downstream_nodes",        "Int64",   "nodes between this one and its outlet"),
    ("component_outlet",          "string",  "site id of the outlet naming this weakly-connected component"),
    ("component_size",            "Int64",   "nodes in that component"),
    ("flow_dist_below_km",        "float64", "channel distance to `node_below`, read off the edge table"),
    ("flow_dist_nearest_node_km", "float64", "the same distance to the nearest node in either direction"),
    ("crosses_impoundment_below", "boolean", "the edge to `node_below` threads a waterbody or passes a dam"),
    ("dam_storage_below_af",      "float64", "summed NID maxStorage on that edge, acre-feet"),
]
NODE_COLS = [c for c, _, _ in NODE_COLUMNS]
NODE_DTYPES = {c: d for c, d, _ in NODE_COLUMNS}


def conform_nodes(df):
    import pandas as pd

    extra = [c for c in df.columns if c not in NODE_DTYPES]
    if extra:
        raise ValueError(f"columns not in the node contract: {extra}")
    out = df.reindex(columns=NODE_COLS)
    for c, d in NODE_DTYPES.items():
        out[c] = out[c].astype(d)
    return out


def validate_nodes(df) -> list[str]:
    bad = []
    if df.site_id.isna().any():
        bad.append("site_id has nulls")
    dup = df.site_id[df.site_id.duplicated()].tolist()
    if dup:
        bad.append(f"site_id not unique: {dup[:8]}")
    if "node_below" in df.columns:
        known = set(df.site_id)
        orphan = sorted(set(df.node_below.dropna()) - known)
        if orphan:
            bad.append(f"node_below points outside the node set: {orphan[:8]}")
        self_edge = df.site_id[df.site_id == df.node_below].tolist()
        if self_edge:
            bad.append(f"a node is its own downstream neighbour: {self_edge[:8]}")
    return bad


def conform_sites(df):
    """Reindex to the sites contract and cast; unknown columns raise, missing ones become typed nulls."""
    import pandas as pd

    extra = [c for c in df.columns if c not in SITE_DTYPES]
    if extra:
        raise ValueError(f"columns not in the sites contract: {extra}")
    out = df.reindex(columns=SITE_COLS)
    for c, d in SITE_DTYPES.items():
        try:
            out[c] = out[c].astype(d)
        except (TypeError, ValueError) as e:
            raise TypeError(f"column {c!r} will not cast to {d}: {e}") from None
    return out


def validate_sites(df) -> list[str]:
    """Contract violations on the minted site table. Empty means conforming."""
    bad = []
    missing = [c for c in SITE_COLS if c not in df.columns]
    if missing:
        return [f"missing columns: {missing}"]
    for c in ("site_id", "bundle", "canonical_uid"):
        if df[c].isna().any():
            bad.append(f"{c} has nulls")
    for c in ("site_id", "bundle"):
        dup = df[c][df[c].duplicated() & df[c].notna()].unique().tolist()
        if dup:
            bad.append(f"{c} not unique: {dup[:8]}")
    on = df.on_network.fillna(False)
    wrong = df.site_id.str.startswith("OFF-") == on
    if wrong.any():
        bad.append(f"on_network disagrees with the id namespace: {df.site_id[wrong].head(8).tolist()}")
    return bad


SENSOR_COLS = [c for c, _, _, _ in SENSOR_COLUMNS]
SENSOR_DTYPES = {c: d for c, d, _, _ in SENSOR_COLUMNS}

check_blocks([(c, d, b) for c, d, b, _ in SENSOR_COLUMNS], SENSOR_OWNERS, "sensors")
_dupe = sorted({c for c in SENSOR_COLS if SENSOR_COLS.count(c) > 1})
if _dupe:
    raise ValueError(f"sensors contract declares duplicate columns: {_dupe}")


def conform_sensors(df):
    """Reindex to the sensors contract and cast. Unknown columns are an ERROR, never a silent drop; missing ones become real nulls of the declared dtype."""
    import pandas as pd

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
    for c in ("lat", "lon"):
        if df[c].isna().any():
            bad.append(f"{c} has nulls -- every registration must have coordinates")
    return bad


def null_sensor_column(name: str, index):
    """An all-null Series of the column's DECLARED dtype. `frame[c] = pd.NA` builds an object column, and the float64 cast then dies far from the assignment -- the exact failure the first real AOE-stamp move produced."""
    import pandas as pd

    return pd.Series([None] * len(index), index=index, dtype=SENSOR_DTYPES[name])


def update_block(sensors, block, cols: list[str]):
    """Update one block on the sensors table from the rows a run produced. No row lost, NO UNTOUCHED ROW BLANKED.

    THE UPDATE IS THE POINT. Drop-the-columns-then-left-join is correct only when the new frame covers every registration; a run narrowed by source or by shard silently erased the record block for 232 sensors once -- they held daily files on disk and no statistics describing them, and nothing noticed because a later stage recomputed the span from the files rather than trusting the column. `DataFrame.update` writes only where the new frame HAS a row.
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
