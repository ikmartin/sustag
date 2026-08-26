"""Edge composition and charge sets -- the graph law a sensor-DAG builder needs on day one.

A MODELING-SIDE LIBRARY, NOT A PIPELINE STAGE. The data layer publishes raw material (records, the flow network, the proximity table) and builds no graph; this package is the demo that the material suffices, and the home of the hardest-won law from the retired build-side graph:

DERIVE VARIANTS BY EDGE CONTRACTION, NEVER VERTEX DELETION. Drop B from A -> B -> C and the variant needs the composed edge A -> C, or the flow relation is silently falsified. That imposes the rule this module enforces: EVERY edge attribute states how it composes along a contracted path, and composing an attribute with no rule raises. EXACTNESS COMES FROM THE CHARGE SET -- every summable attribute is measured over the edge's charge set (the from-node's own reach plus the strictly-intervening reaches, empty for a co-reach pair), so consecutive edges' charge sets tile the channel and contraction loses nothing at the dropped node. The executable invariant: contract a two-hop chain and re-derive the direct edge by walking the network with the middle node removed; every attribute must match.
"""

from __future__ import annotations

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
    ("from_id",               "string",  "key", "upstream node (node id)"),
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


