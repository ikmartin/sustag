"""Measure what admitting the IWQIS `WQP` namespace would do, before any contract changes.

    python -m src.build2.wqp_admission

WHY THIS EXISTS. `sources.iwqis` admits `^WQS\\d+$`, so a second IIHR namespace in the same export -- 16 stations, 2.03M rows, 1.62M nitrate observations -- is reachable by nothing. Widening the regex touches typing, identity, the cohort filter and the AOE stamp at once, and three of those fail QUIETLY: a site typed `stream` by fall-through enters the cohort with a basin belonging to something else, a new merge candidate withdraws an EXISTING gauge until a human answers, and a changed AOE stamp nulls the snap, merge, network and type blocks on every registration. This measures all of it first.

READ-ONLY AND NEVER RAISES, like `units_check`. Nothing here writes to `interim2/` or `processed2/`, every step is independent, and a step that cannot run says so and yields to the next -- a missing network layer must not cost the four checks that do not need it.

THE TWO RESULTS THE PLAN BRANCHES ON are step 2 (what the 16 snap to, and whether any is inside a waterbody polygon) and step 5 (whether the AOE geometry and its stamp survive the union). The rest is blast radius: how many existing gauges a pending merge would withhold, and how many decided pairs a change to the turbidity rule would reopen.
"""

from __future__ import annotations

import argparse
import re

import numpy as np
import pandas as pd

from . import config, schema
from .sources import iwqis

# The namespace this module is about. Deliberately NOT `iwqis._UID_RE` -- that is the rule under test, and reading it here would make the report agree with whatever the adapter currently says.
WQP_RE = re.compile(r"^WQP\d+$")

# Channels whose agreement means "same weather" rather than "same instrument", under the proposed rule. Step 4 measures what moving these costs.
TURBIDITY = ("turbidity_fnu", "turbidity_ntu", "turbidity_unknown")


def _station_name(place, nickname, uid):
    """`place (nickname)`, dropping the parenthetical where it adds nothing. The proposed `iwqis.discover` rule.

    Appending rather than choosing is the whole point -- `river at town` is the place and the nickname is what the station IS, and only the second one carries a type. The containment test keeps "Saturated Waterway 1 at Reinbeck" from becoming "... (Saturated Waterway 1)".
    """
    out = []
    for p, n, u in zip(place, nickname, uid):
        p, n = str(p).strip(), str(n).strip()
        if not p:
            out.append(n or str(u))
        elif n and n.lower() not in p.lower():
            out.append(f"{p} ({n})")
        else:
            out.append(p)
    return np.array(out, dtype=object)


def registry_rows() -> pd.DataFrame:
    """The WQP registry rows, with the station name as built today and as proposed.

    `current` is `iwqis.discover`'s rule verbatim (`river at town`, else nickname, else uid); `proposed` appends the nickname as a parenthetical instead of discarding it. The WQP namespace inverts the WQS convention -- `river` holds the watercourse and `nickname` holds what the station IS -- so today's rule drops the only evidence that nine of these are engineered structures.
    """
    r = iwqis._registry()
    r = r[r["uid"].astype(str).str.strip().str.match(WQP_RE, na=False)].copy()
    r["uid"] = r.uid.str.strip()

    name = r["nickname"].fillna("").str.strip()
    river = r.get("river", pd.Series([""] * len(r), index=r.index)).fillna("").str.strip()
    town = r.get("town", pd.Series([""] * len(r), index=r.index)).fillna("").str.strip()

    current = np.where(river.ne("") & town.ne(""), river + " at " + town,
                       np.where(name.ne(""), name, r["uid"]))
    place = np.where(river.ne("") & town.ne(""), river + " at " + town,
                     np.where(river.ne(""), river, town))
    proposed = _station_name(place, name.to_numpy(), r["uid"].to_numpy())

    desc = r.get("description", pd.Series([""] * len(r), index=r.index)).fillna("").str.strip()
    return pd.DataFrame({
        "uid": r.uid.to_numpy(),
        "site_uid": [schema.make_uid(iwqis.NAME, u) for u in r.uid],
        "lat": pd.to_numeric(r.latitude, errors="coerce").to_numpy(),
        "lon": pd.to_numeric(r.longitude, errors="coerce").to_numpy(),
        "source_drainage_area_km2": pd.to_numeric(r.get("draining_area"), errors="coerce").to_numpy() * 2.589988,
        "nickname": name.to_numpy(),
        "current": current,
        "proposed": proposed,
        "site_type_raw": (desc + " | " + name).str.strip(" |").to_numpy(),
    }).sort_values("uid", ignore_index=True)


# ---- 1. typing -----------------------------------------------------------------------------------

def step_typing(w: pd.DataFrame) -> pd.DataFrame:
    """What `site_type.from_name` returns for each WQP site under each naming rule, plus a WQS regression check."""
    from .chunk3 import site_type

    print("\n[1] typing evidence -- what the name rule can see")
    out = w.assign(
        type_current=[site_type.from_name(n) for n in w.current],
        type_proposed=[site_type.from_name(n) for n in w.proposed],
        type_raw=[site_type.from_name(n) for n in w.site_type_raw],
    )
    print(f"{'uid':9s} {'from current':>18s} {'from proposed':>18s} {'from raw':>18s}  proposed station_name")
    for r in out.itertuples():
        print(f"{r.uid:9s} {_s(r.type_current):>18s} {_s(r.type_proposed):>18s} {_s(r.type_raw):>18s}  {r.proposed}")
    n_now = int(out.type_current.notna().sum())
    n_new = int((out.type_proposed.notna() | out.type_raw.notna()).sum())
    print(f"\n  {n_now}/{len(out)} typed by the current rule; {n_new}/{len(out)} once the nickname and the "
          f"description are readable. The remainder fall through to `stream`.")

    # The regression that matters: a WQS station picking up a spurious type from a nickname it was ignoring.
    reg = iwqis._registry()
    q = reg[reg["uid"].astype(str).str.strip().str.match(r"^WQS\d+$", na=False)].copy()
    nm = q["nickname"].fillna("").str.strip()
    rv = q.get("river", pd.Series([""] * len(q), index=q.index)).fillna("").str.strip()
    tw = q.get("town", pd.Series([""] * len(q), index=q.index)).fillna("").str.strip()
    cur = np.where(rv.ne("") & tw.ne(""), rv + " at " + tw, np.where(nm.ne(""), nm, q["uid"]))
    place = np.where(rv.ne("") & tw.ne(""), rv + " at " + tw, np.where(rv.ne(""), rv, tw))
    prop = _station_name(place, nm.to_numpy(), q["uid"].to_numpy())
    moved = [(u, a, b, site_type.from_name(a), site_type.from_name(b))
             for u, a, b in zip(q.uid.str.strip(), cur, prop)
             if site_type.from_name(a) != site_type.from_name(b)]
    if moved:
        print(f"\n  WQS REGRESSION -- {len(moved)} station(s) change type under the proposed rule:")
        for u, a, b, ta, tb in moved:
            print(f"    {u}  {ta or '-'} -> {tb or '-'}   {a!r} -> {b!r}")
    else:
        print(f"\n  no WQS station changes type under the proposed rule ({len(q)} checked)")
    return out


# ---- 2. the snap ---------------------------------------------------------------------------------

def step_snap(w: pd.DataFrame) -> pd.DataFrame:
    """Snap the 16 against the real network layers; report the reach, its FTYPE/WBAREACOMI, and waterbody containment.

    This is the step that decides whether any WQP site types as `lake` at rank 3, which is what would silently drop it from the graph. `_inside` is the same point-in-polygon `site_type.classify` uses, so the answer here is the answer there.
    """
    print("\n[2] snap -- what the 16 anchor on")
    try:
        import geopandas as gpd

        from .chunk1 import network
        from .chunk3 import site_type, snap as snapmod
    except Exception as e:                                    # noqa: BLE001
        print(f"  unavailable: {type(e).__name__}: {e}")
        return pd.DataFrame()
    if not network.exists():
        print("  network layers are missing -- run chunk 1 branch B (network) first")
        return pd.DataFrame()

    fl = gpd.read_parquet(network._path("flowlines"))
    cat = gpd.read_parquet(network._path("catchments"))
    vaa = pd.read_parquet(network._path("vaa"))
    block = snapmod.snap(w[["site_uid", "lat", "lon", "source_drainage_area_km2"]], fl, cat, vaa)
    b = pd.concat([w[["uid", "site_uid", "lat", "lon"]].reset_index(drop=True),
                   block.reset_index(drop=True).drop(columns=["site_uid"], errors="ignore")], axis=1)

    low = {c.lower(): c for c in fl.columns}
    keep = [low[c] for c in ("comid", "ftype", "wbareacomi", "gnis_name") if c in low]
    attrs = fl[keep].copy()
    attrs.columns = [c.lower() for c in attrs.columns]
    attrs["comid"] = pd.to_numeric(attrs.comid, errors="coerce")
    b = b.merge(attrs.drop_duplicates("comid"), on="comid", how="left")

    inside = {}
    try:
        wb = site_type.waterbodies(b.wbareacomi)
        if len(wb):
            inside = site_type._inside(b.rename(columns={"site_uid": "site_uid"}), wb)
    except Exception as e:                                    # noqa: BLE001
        print(f"  waterbody lookup failed ({type(e).__name__}), so no rank-3 evidence: {e}")
    b["in_waterbody"] = b.site_uid.map(inside)

    print(f"{'uid':9s}{'comid':>10s}{'dist_m':>8s}{'status':>7s}{'ord':>4s}{'cat_km2':>9s}"
          f"  {'ftype':<15s}{'wbareacomi':>11s}  in_wb  gnis")
    for r in b.itertuples():
        print(f"{r.uid:9s}{_i(r.comid):>10s}{_f(getattr(r, 'snap_dist_m', np.nan), 1):>8s}"
              f"{str(getattr(r, 'snap_status', '-')):>7s}{_i(getattr(r, 'stream_order', np.nan)):>4s}"
              f"{_f(getattr(r, 'catchment_area_km2', np.nan), 1):>9s}  {str(r.ftype or '-'):<15s}"
              f"{_i(r.wbareacomi):>11s}  {_i(r.in_waterbody):>5s}  {r.gnis_name or ''}")
    n_lake = int(b.in_waterbody.notna().sum())
    print(f"\n  {n_lake} of {len(b)} sit inside a waterbody polygon"
          f"{' -- these would type `lake` at rank 3 and leave the graph' if n_lake else ''}")
    far = b[b.get("snap_status", pd.Series(dtype=object)).eq("far")]
    if len(far):
        print(f"  {len(far)} snapped beyond {snapmod.MAX_SNAP_M:.0f} m: {', '.join(far.uid)}")
    return b


def _s(v) -> str:
    """A type name, or `-`. `v or '-'` is wrong here: `from_name` returns None, pandas stores it as NaN, and NaN is truthy."""
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)


def _i(v) -> str:
    return "-" if v is None or (isinstance(v, float) and not np.isfinite(v)) or pd.isna(v) else f"{int(v)}"


def _f(v, n: int = 2) -> str:
    return "-" if v is None or pd.isna(v) else f"{float(v):.{n}f}"


# ---- 3. the merge-gate blast radius ---------------------------------------------------------------

def step_merge(snapped: pd.DataFrame) -> pd.DataFrame:
    """New shared-COMID pairs, and which EXISTING gauges a pending decision would withhold from the cohort.

    `identity.apply_decisions` nulls `physical_gauge_group` for BOTH members of an undecided pair, and `chunk6.filter` then drops them on `identity_undecided`. So proposing a pair against an established gauge withdraws that gauge until a person answers -- which is the cost this step quantifies, in nitrate days.
    """
    print("\n[3] merge gate -- what a pending WQP pair would withhold")
    if not len(snapped) or "comid" not in snapped.columns:
        print("  step 2 produced no snap, so no pair can be proposed")
        return pd.DataFrame()
    if not config.REGISTRATIONS_PATH.exists():
        print(f"  {config.REGISTRATIONS_PATH} is missing")
        return pd.DataFrame()

    reg = pd.read_parquet(config.REGISTRATIONS_PATH)
    have = reg.dropna(subset=["comid"])[["site_uid", "station_name", "comid", "n_nitrate_days"]].copy()
    have["comid"] = pd.to_numeric(have.comid, errors="coerce")
    new = snapped.dropna(subset=["comid"])[["site_uid", "comid"]].copy()

    rows = []
    for r in new.itertuples():
        hit = have[have.comid == r.comid]
        for h in hit.itertuples():
            rows.append(dict(new_uid=r.site_uid, existing_uid=h.site_uid, comid=int(r.comid),
                             existing_name=h.station_name, existing_days=h.n_nitrate_days))
    # WQP-to-WQP pairs matter too: they are new on both sides and withhold nothing, but they are the
    # ones a reviewer actually has to think about.
    own = new.groupby("comid").site_uid.apply(list)
    self_pairs = [(c, v) for c, v in own.items() if len(v) > 1]

    out = pd.DataFrame(rows)
    if len(out):
        print(f"  {len(out)} new pair(s) against an EXISTING registration:")
        print(f"{'new':18s}{'existing':22s}{'nitrate days':>13s}  name")
        for r in out.sort_values("existing_days", ascending=False).itertuples():
            print(f"{r.new_uid:18s}{r.existing_uid:22s}{_i(r.existing_days):>13s}  {r.existing_name}")
        tot = pd.to_numeric(out.existing_days, errors="coerce").sum()
        print(f"\n  answering these {len(out)} row(s) is what keeps {int(out.existing_uid.nunique())} "
              f"existing gauge(s) and {int(tot):,} nitrate days in the cohort")
    else:
        print("  no WQP site shares a reach with an existing registration")
    if self_pairs:
        print(f"\n  {len(self_pairs)} WQP-only pair group(s), new on both sides:")
        for c, v in self_pairs:
            print(f"    comid {int(c)}: {', '.join(v)}")
    return out


# ---- 4. the turbidity rule ------------------------------------------------------------------------

def step_turbidity() -> pd.DataFrame:
    """How many existing merge candidates were decided on turbidity, and would reopen if it became weak evidence."""
    print("\n[4] turbidity -- blast radius of moving it to WEAK_FOR_IDENTITY")
    p = config.MERGE_CANDIDATES
    if not p.exists():
        print(f"  {p} is missing -- chunk 3 has not produced a candidate sheet")
        return pd.DataFrame()
    cand = pd.read_csv(p, dtype=str)
    if "on_channel" not in cand.columns:
        # The sheet on disk predates the current `identity.py`. Recomputing is the honest fallback: the
        # question is which channel would decide each pair TODAY, not which one decided it whenever the
        # sheet was last written.
        print(f"  the candidate sheet predates `on_channel` -- recomputing from the canonical records")
        cand = cand.assign(on_channel=[_would_decide(a, b) for a, b in zip(cand.uid_a, cand.uid_b)])
    hit = cand[cand.on_channel.isin(TURBIDITY)]
    dec = pd.read_csv(config.MERGE_DECISIONS, dtype=str) if config.MERGE_DECISIONS.exists() else pd.DataFrame()
    decided = set()
    if len(dec) and {"uid_a", "uid_b", "decision"} <= set(dec.columns):
        d = dec[dec.decision.fillna("").str.strip().ne("")]
        decided = {tuple(sorted((a, b))) for a, b in zip(d.uid_a, d.uid_b)}
    open_rows = [r for r in hit.itertuples() if tuple(sorted((r.uid_a, r.uid_b))) not in decided]
    print(f"  {len(hit)} of {len(cand)} candidate pair(s) are decided on a turbidity channel; "
          f"{len(hit) - len(open_rows)} already have a recorded decision (which survives on the uid key), "
          f"{len(open_rows)} would need re-reviewing")
    for r in open_rows:
        print(f"    {r.uid_a} / {r.uid_b}  on {r.on_channel}, proposed {getattr(r, 'proposed', '?')}")
    return hit


def _would_decide(uid_a: str, uid_b: str) -> str | None:
    """Which channel `identity._shared_channel` would pick for this pair today. None where either has no record."""
    from .chunk3 import identity

    fa, fb = identity._canonical(uid_a), identity._canonical(uid_b)
    if fa is None or fb is None:
        return None
    ca, cb = identity.channels_of(fa), identity.channels_of(fb)
    return identity._shared_channel(ca, cb, fa, fb)


# ---- 5. the AOE stamp -----------------------------------------------------------------------------

def step_aoe(w: pd.DataFrame) -> None:
    """Whether unioning the 16 NLDI basins moves the AOE geometry, and whether it moves the STAMP.

    The stamp is `sha256(geometry.wkb)`, so a union that changes nothing geometrically can still change it if GEOS re-nodes a boundary -- and `chunk0.run_chunk0._invalidate_stale` then nulls the snap, merge, network and type blocks on every registration. `equals()` is topological equality, which is the question actually being asked.
    """
    print("\n[5] AOE stamp -- does admitting these 16 invalidate the build")
    if not config.AOE_PATH.exists():
        print(f"  {config.AOE_PATH} is missing -- chunk 0 has not run")
        return
    try:
        import geopandas as gpd
        from shapely.ops import unary_union

        from .chunk0 import basins as c0basins
    except Exception as e:                                    # noqa: BLE001
        print(f"  unavailable: {type(e).__name__}: {e}")
        return

    old = gpd.read_parquet(config.AOE_PATH)
    old_geom = old.geometry.iloc[0]
    old_stamp = str(old.stamp.iloc[0]) if "stamp" in old.columns else config.aoe_stamp()

    print(f"  fetching {len(w)} NLDI basin(s) -- about {6 * len(w)}s")
    polys = []
    nldi = c0basins._nldi()
    for r in w.itertuples():
        try:
            geom, how = c0basins._fetch_one(nldi, iwqis.NAME, r.uid, float(r.lon), float(r.lat),
                                            r.site_type_raw, r.proposed)
            if geom is not None and not geom.is_empty:
                polys.append(geom)
            else:
                print(f"    {r.uid}: {how}")
        except Exception as e:                                # noqa: BLE001
            print(f"    {r.uid}: {type(e).__name__}: {e}")
    if not polys:
        print("  no basin came back; the AOE is unchanged by construction")
        return

    new_geom = unary_union([old_geom, *polys])
    same = bool(new_geom.equals(old_geom))
    sym = config.equal_area_km2(new_geom.symmetric_difference(old_geom))
    import hashlib

    new_stamp = hashlib.sha256(new_geom.wkb).hexdigest()[:len(old_stamp)]
    print(f"  {len(polys)}/{len(w)} basins fetched")
    print(f"  geometry equal to the one on disk: {same}")
    print(f"  symmetric difference: {sym:,.1f} km2" if np.isfinite(sym) else "  symmetric difference: n/a")
    print(f"  stamp {old_stamp} -> {new_stamp}  ({'UNCHANGED' if new_stamp == old_stamp else 'CHANGED'})")
    if same and new_stamp != old_stamp:
        print("  the extent did not move but the stamp did -- this is exactly the case the stamp-stability "
              "fix in `chunk0.aoe.write` exists to absorb; without it every aoe_dependent column is nulled")


# ---- the export the registry cannot reach ----------------------------------------------------------

def step_reconcile() -> list[str]:
    """Uids present in the export with no registry row -- observations nothing can register."""
    print("\n[0] export vs registry")
    try:
        import pyarrow as pa
        import pyarrow.csv as pv
    except Exception as e:                                    # noqa: BLE001
        print(f"  unavailable: {type(e).__name__}: {e}")
        return []
    files = iwqis._chunk_files()
    if not files:
        print(f"  no export under {iwqis._CHUNKS}")
        return []
    opts = pv.ConvertOptions(column_types={"site_uid": pa.string(), "nitrate_con": pa.float64()},
                             include_columns=["site_uid", "nitrate_con"])
    big = pa.concat_tables([pv.read_csv(f, convert_options=opts) for f in files]).to_pandas()
    big["site_uid"] = big.site_uid.str.strip().str.upper()
    exp = big.groupby("site_uid").agg(rows=("nitrate_con", "size"), nitrate=("nitrate_con", "count"))
    known = set(iwqis._registry()["uid"].astype(str).str.strip().str.upper())
    gaps = exp[~exp.index.isin(known)]
    print(f"  export carries {len(exp)} station(s); registry carries {len(known)} row(s)")
    if len(gaps):
        print(f"  {len(gaps)} station(s) in the export with NO registry row -- no coordinates, so "
              f"`schema.validate` cannot admit them and nothing can read their data:")
        for u, r in gaps.iterrows():
            print(f"    {u}  {int(r.rows):,} rows, {int(r.nitrate):,} nitrate")
    else:
        print("  every station in the export has a registry row")
    return list(gaps.index)


def main() -> None:
    print(f"\n{'=' * 78}\nWQP admission dry run -- measures only, writes nothing\n{'=' * 78}")
    steps = []
    try:
        w = registry_rows()
        print(f"\n  {len(w)} WQP registry row(s)")
    except Exception as e:                                    # noqa: BLE001
        print(f"\n  cannot read the registry: {type(e).__name__}: {e}")
        return
    for fn, args in ((step_reconcile, ()), (step_typing, (w,)), (step_snap, (w,))):
        try:
            steps.append(fn(*args))
        except Exception as e:                                # noqa: BLE001
            print(f"  step failed: {type(e).__name__}: {e}")
            steps.append(pd.DataFrame())
    snapped = steps[-1] if isinstance(steps[-1], pd.DataFrame) else pd.DataFrame()
    for fn, args in ((step_merge, (snapped,)), (step_turbidity, ()), (step_aoe, (w,))):
        try:
            fn(*args)
        except Exception as e:                                # noqa: BLE001
            print(f"  step failed: {type(e).__name__}: {e}")
    print(f"\n{'=' * 78}\n")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    main()
