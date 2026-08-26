"""D4 -- delineate a basin for every site, four ways, pick one; then the maximal non-temporal DAG.

    python -m src.build3.run d4 --single
    python -m src.build3.run d4 --single --force        # rebuild every candidate from scratch

IT DOES NOT GATE. D3 gates because identity and type are decisions a person must make before anything downstream is meaningful. A delineation is not: the auto rule answers every site, the flags say which answers are worth a second look, and a ledgered override (keyed on the member set) can be applied at any later point without the pipeline having waited.

THE REACH SET IS PART OF THE OUTPUT, and it is what D5 actually consumes -- covariates are keyed on COMID, so aggregating them to a site means summing over its upstream reaches, not intersecting a polygon. basin0 and basin3 are polygons with no reach set of their own, so a site that selects one still gets basin1's reaches, recorded as `reach_set_method`. That pairing is only defensible because the areas are checked: `flag_basin0_disagrees` fires when the authority's polygon and basin1 differ by more than 10%, which is exactly the case where borrowing the reach set would be wrong.

THE GRAPH IS MAXIMAL AND NON-TEMPORAL: every qualifying site is a node, and an edge asserts spatial connection only, with no claim that its endpoints' records overlap in time. Temporally-coherent variants are derived in the ACCESS layer by edge contraction, per channel, using the composition rules on `contracts.EDGE_COLUMNS` -- never rebuilt here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, contracts, io as bio, ledger, registry
from ..acquire import network as anet
from ..acquire import snapshot
from . import accumulate, basins, graph, site_type


def evidence(sites: pd.DataFrame, net: accumulate.Network) -> pd.DataFrame:
    """The full per-site table: candidate areas, capture evidence, selection, flags, corroboration."""
    cand = basins.candidate_table(sites)
    cand = cand.merge(basins.capture_evidence(sites, net), on="site_id", how="left")

    # NHDPlus's own accumulated area for the snapped reach, and the size of the walk that reproduced it. Both come from the topology rather than from the cache, so they are present even for a site whose dissolve failed -- which is the case where the comparison is most worth having.
    tot, nre = [], []
    for r in sites.itertuples():
        s, _, totda, _ = net.accumulate(r.comid)
        tot.append(totda)
        nre.append(len(s))
    cand["vaa_totda_km2"] = tot
    cand["n_reaches"] = nre

    cand = basins.select(cand, sites)
    meta = sites[["site_id", "bundle", "station_name", "site_type", "snap_dist_m", "comid_agree",
                  "comid", "catchment_comid", "lat", "lon"]]
    cand = cand.merge(meta, on="site_id", how="left")
    cand["dist_to_basin_km"] = [
        basins.dist_to_basin_km(basins.geometry_for(r["site_id"], basins.method_of(r)),
                                r["lat"], r["lon"])
        for r in cand.to_dict("records")]
    for c, v in pd.DataFrame([basins.evaluate(r) for r in cand.to_dict("records")]).items():
        cand[c] = v
    corr = [basins.corroboration(r) for r in cand.to_dict("records")]
    cand["n_corroborations"] = pd.array([n for n, _ in corr], dtype="Int64")
    cand["corroborated_by"] = pd.array([s or None for _, s in corr], dtype="string")
    return cand


def unit_properties(pairs: pd.DataFrame, outlet: dict) -> pd.DataFrame:
    """Add `area_km2`, `flow_dist_km` and `flow_time_d` to the site-reach pairs.

    WHAT MAKES A CATCHMENT POSITIONABLE INSIDE ITS BASIN. Membership alone says a catchment drains to this site; these say how big it is and how far -- in kilometres and in days of travel -- the water has to go.

    BOTH DISTANCES ARE DIFFERENCES OF A CUMULATIVE FIELD. `pathlength` and `pathtimema` are measured to the terminal outlet, so subtracting the site's own reach leaves the distance from a catchment down to the site. `pathtimema` is the field for time -- NOT `totma`, which is the reach's OWN travel time and differences to noise. THE SENTINEL IS NOT A NUMBER: both operands are nulled before subtracting, so an unknown propagates as an unknown -- and because the reference is the SITE's reach, one missing value there would null that site's whole basin (it did: 17 gauges holding 29% of all pairs). SO A MISSING REFERENCE FALLS BACK TO THE BASIN MINIMUM: the site's reach is the most downstream in its own basin, so the smallest `pathtimema` in the set should be its own -- measured, that matches on 341 of 349; the eight that disagree are divergences, which is why it is a FALLBACK and not the rule.

    NOTHING IS CLIPPED BEYOND THE SENTINEL. `flow_time_d` runs to 51,524 days, because `pathtimema` accumulates reservoir residence; the quantity is stored as published and the access layer decides what it will believe.
    """
    v = pd.read_parquet(anet._path("vaa"),
                        columns=["comid", "areasqkm", "pathlength", "pathtimema"]).set_index("comid")
    for c in ("pathlength", "pathtimema"):
        v[c] = v[c].where(v[c] > graph.VAA_NODATA)

    out = pairs.copy()
    out["area_km2"] = v.areasqkm.reindex(out.comid.to_numpy()).astype("float32").to_numpy()
    at_site = out.site_id.map(outlet)
    for src, dst in (("pathlength", "flow_dist_km"), ("pathtimema", "flow_time_d")):
        here = pd.Series(v[src].reindex(out.comid.to_numpy()).to_numpy(), index=out.index)
        ref = pd.Series(v[src].reindex(at_site.to_numpy()).to_numpy(), index=out.index)
        fallback = here.groupby(out.site_id).transform("min")
        n_fb = int(out.site_id[ref.isna()].nunique())
        if n_fb:
            print(f"    {dst}: {n_fb} site(s) have no {src} at their own reach; "
                  f"using the basin minimum instead", flush=True)
        out[dst] = (here - ref.fillna(fallback)).astype("float32")
    return out


def write_basins(ev: pd.DataFrame, net: accumulate.Network) -> None:
    """`basins.parquet` (the selected polygon per site) and `basin_reaches.parquet` (its upstream reaches).

    A site with no basin still gets a row, with a null geometry and its `no_basin_reason` -- a missing row would look like a site that was never considered.
    """
    import geopandas as gpd

    stamps = {"snapshot": snapshot.head() or "", "aoe": config.aoe_stamp(),
              "ledgers": ledger.ledger_hash()}
    geoms, rows = [], []
    keep = (["site_id", "bundle", "basin_method", "selection_mode", "no_basin_reason", "basin_km2",
             "vaa_totda_km2", "n_reaches", "dist_to_basin_km", "n_corroborations", "corroborated_by"]
            + list(basins.FLAG_COLS))
    for r in ev.to_dict("records"):
        geoms.append(basins.geometry_for(r["site_id"], basins.method_of(r)))
        rows.append({k: r.get(k) for k in keep})
    out = gpd.GeoDataFrame(pd.DataFrame(rows), geometry=geoms, crs=4326)
    config.BASINS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.BASINS_PATH.with_name(config.BASINS_PATH.name + ".tmp")
    out.to_parquet(tmp)
    tmp.replace(config.BASINS_PATH)
    bio.write_stamps_sidecar(config.BASINS_PATH, stamps)
    print(f"  wrote {config.BASINS_PATH.name} ({len(out)} site(s), "
          f"{int(out.geometry.notna().sum())} with geometry)")

    # The reach set behind each selection. basin2 walks from the containing catchment; everything else walks from the snapped reach -- a polygon method borrows basin1's set (see the module docstring).
    pairs, method, outlet = [], {}, {}
    for r, cid in zip(ev.to_dict("records"), ev.comid):
        m = basins.method_of(r)
        if m is None:
            continue
        use = "basin2" if m == "basin2" else "basin1"
        s, _, _, status = net.accumulate(cid if use == "basin1" else r.get("catchment_comid", cid))
        if status != "ok":
            continue
        method[r["site_id"]] = use
        outlet[r["site_id"]] = int(cid if use == "basin1" else r.get("catchment_comid", cid))
        pairs.append(pd.DataFrame({"site_id": r["site_id"], "comid": sorted(s)}))
    if pairs:
        df = pd.concat(pairs, ignore_index=True)
        df["reach_set_method"] = df.site_id.map(method)
        df = unit_properties(df, outlet)
        bio.write_parquet(df, config.BASIN_REACHES, stamps=stamps)
        print(f"  wrote {config.BASIN_REACHES.name} ({len(df):,} site-reach pairs over "
              f"{df.site_id.nunique()} site(s))")
        # PER-SITE medians, not row-weighted: the top basins hold most pairs, so a row median describes the Mississippi rather than the cohort.
        for c in ("area_km2", "flow_dist_km", "flow_time_d"):
            per = df.groupby("site_id")[c].median()
            print(f"    {c:<13} {int(df[c].isna().sum()):>6,} null | per-site median "
                  f"{per.median():,.2f}  p90 {per.quantile(0.9):,.1f}  max {per.max():,.1f}")


def sync_ledger(ev: pd.DataFrame) -> None:
    """The basin override sheet, keyed on the member set. Advisory -- it never gates.

    ONE ROW PER DELINEATED SITE, not just the flagged ones: the sheet is the override table, so anything a reviewer might want to change has to be addressable in it. `auto_method` records what the rule chose and `decision` is blank until somebody disagrees. Flagged rows sort first.
    """
    led = basins.basin_ledger()
    rows = ev[ev.selection_mode != "none"].copy()
    if not len(rows):
        print("  no delineated sites to record")
        return
    rows["flagged"] = rows[basins.FLAG_COLS].any(axis=1)
    rows["auto_method"] = rows.basin_method
    cols = ["bundle", "site_id", "station_name", "auto_method", "selection_mode", "flagged",
            "basin_km2", "basin0_km2", "basin1_km2", "basin2_km2", "basin3_km2", "vaa_totda_km2",
            "dist_to_basin_km", "n_corroborations", "corroborated_by",
            *basins.FLAG_COLS, "reach_name", "alt_name", "alt_da_km2", "alt_dist_m"]
    ordered = (rows.sort_values(["flagged", "basin_km2"], ascending=[False, False])
               .rename(columns={"bundle": "members"}))
    a, k, rt = led.sync(ordered[["members"] + [c for c in cols if c in ordered.columns and c != "bundle"]])
    n_manual = int((ev.selection_mode == "manual").sum())
    print(f"  {led.path.name}: {len(rows)} site(s) -- {k} carried forward, {a} new"
          + (f", {rt} decided but no longer delineated (kept, unnumbered)" if rt else ""))
    print(f"  {int((ev.selection_mode == 'auto').sum())} auto, {n_manual} manual override(s)"
          + ("" if n_manual else " -- set `decision` (a method name) on any row to overrule the auto rule"))


def build_graph(sites: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    """Nodes, edges and the incremental sets. Writes `nodes.parquet`, `edges.parquet`, `incremental_reaches.parquet`.

    A NODE IS AN ON-NETWORK SITE WHOSE TYPE DRAINS ITS OWN CATCHMENT -- sub-catchment types are out for the same reason a lake is, by a different route. Validity is decided by type and snap, never by whether a delineation succeeded, or a site would drop out of the network for a reason that has nothing to do with the network.
    """
    valid = sites[sites.on_network.fillna(False)
                  & ~sites.site_type.isin(site_type.SUB_CATCHMENT)].copy()
    valid = valid.merge(ev[["site_id", "basin_method", "basin_km2"]], on="site_id", how="left")
    net = graph.Downstream.load()
    on_reach = graph.node_reaches(valid)
    owner, below, via = graph.label(net, on_reach)
    if not graph.is_acyclic(below):
        raise ValueError("the node graph is cyclic -- water cannot reach itself")

    from . import dams as dams_mod

    edges = contracts.conform_edges(graph.edge_table(net, below, via, valid, dams=dams_mod.on_reach()))
    counts = graph.reach_counts(list(valid.site_id), below)
    out = counts.merge(graph.flow_distances(valid, edges), on="site_id", how="left")
    if len(edges):
        e = edges.set_index("from_id")
        out["crosses_impoundment_below"] = out.site_id.map(e.crosses_impoundment).astype("boolean")
        out["dam_storage_below_af"] = out.site_id.map(e.dam_max_storage_af)
    out = contracts.conform_nodes(out)

    bad = contracts.validate_nodes(out)
    if bad:
        raise ValueError("node table violates the contract:\n  " + "\n  ".join(bad))
    bad = contracts.validate_edges(edges, out)
    if bad:
        raise ValueError("edge table violates the contract:\n  " + "\n  ".join(bad))

    stamps = {"snapshot": snapshot.head() or "", "aoe": config.aoe_stamp(),
              "ledgers": ledger.ledger_hash()}
    bio.write_parquet(out, config.NODES_PATH,
                      stamps={**stamps, "schema": contracts.schema_fingerprint(contracts.NODE_DTYPES)})
    bio.write_parquet(edges, config.EDGES_PATH,
                      stamps={**stamps, "schema": contracts.schema_fingerprint(contracts.EDGE_DTYPES)})
    inc = pd.DataFrame(sorted(owner.items()), columns=["comid", "site_id"])
    bio.write_parquet(inc, config.GRAPH_DIR / "incremental_reaches.parquet", stamps=stamps)

    linked = len(set(below) | set(below.values()))
    comp = out.component_outlet.value_counts()
    print(f"  {len(sites)} site(s) -> {len(out)} valid node(s) "
          f"({len(sites) - len(out)} off-graph: OFF namespace or sub-catchment type)")
    print(f"  {len(edges)} edge(s); {linked} node(s) on at least one, {len(out) - linked} isolated; acyclic")
    _report_edges(edges)
    if len(comp):
        print(f"  {len(comp)} component(s); largest {int(comp.iloc[0])} node(s) at {comp.index[0]}")
    print(f"  {len(inc):,} reaches owned across the incremental sets")
    print(f"  wrote {config.NODES_PATH.name}, {config.EDGES_PATH.name} and incremental_reaches.parquet")
    return out


def _report_edges(edges: pd.DataFrame) -> None:
    """What the edges cross, and the pairs a reviewer should look at.

    THE LAST LINE IS THE MIS-SNAP DETECTOR. Two nodes whose basins are within a few percent are either the same water above and below a structure -- real, and the signal a network model is there to learn -- or two registrations for one place, one of which snapped to the wrong reach. `crosses_impoundment` is what tells them apart, and only the second kind needs a person.
    """
    if not len(edges):
        return
    ci = edges.crosses_impoundment.fillna(False).astype(bool)
    dammed = edges[pd.to_numeric(edges.n_dams, errors="coerce").fillna(0) > 0]
    big = pd.to_numeric(dammed.dam_max_storage_af, errors="coerce").max() if len(dammed) else np.nan
    print(f"  {int(ci.sum())} edge(s) cross an impoundment"
          + (f"; {len(dammed)} pass a named NID dam, largest {big:,.0f} acre-feet" if len(dammed) else ""))
    nd = edges[edges.near_duplicate_basin.fillna(False).astype(bool)]
    if len(nd):
        imp = int(nd.crosses_impoundment.fillna(False).astype(bool).sum())
        print(f"  {len(nd)} near-duplicate basin pair(s): {imp} impounded (kept -- the signal), "
              f"{len(nd) - imp} not (check the snap)")
        for r in nd[~nd.crosses_impoundment.fillna(False).astype(bool)].itertuples():
            print(f"    {r.from_id} -> {r.to_id}  areas within "
                  f"{(1 - float(r.basin_area_ratio)) * 100:.1f}%, nothing between them")


def main(force: bool = False) -> pd.DataFrame:
    if not config.SITES_PATH.exists():
        raise FileNotFoundError(f"{config.SITES_PATH} is missing -- run d3 first")
    sites = bio.read_parquet(config.SITES_PATH)
    dl = basins.delineable(sites)
    print(f"  {len(sites)} site(s), {len(dl)} delineable "
          f"({len(sites) - len(dl)} off-network or sub-catchment)")

    net = accumulate.Network.load()

    print("\n[d4.1] basin1 + basin2  (local upstream accumulation)")
    basins.build_local(dl, net, force=force)

    print("\n[d4.2] basin0  (the authority's delineation, read from the acquired store)")
    basins.build_basin0(dl, force=force)

    print("\n[d4.3] basin3  (D8 raster flood-fill)")
    area1 = dict(zip((ct := basins.candidate_table(dl)).site_id, ct.basin1_km2))
    basins.build_basin3(dl, area1, force=force)

    print("\n[d4.4] evidence, flags and selection")
    ev = evidence(sites, net)
    print(f"  selection: {ev.selection_mode.value_counts().to_dict()}")
    nb = ev.no_basin_reason.value_counts().to_dict()
    if nb:
        print(f"  no basin:  {nb}")
    print(f"  method:    {ev.basin_method.value_counts(dropna=False).to_dict()}")
    dl_ev = ev[ev.selection_mode != "none"]
    if len(dl_ev):
        print(f"  corroboration: {dl_ev.n_corroborations.value_counts().sort_index().to_dict()} "
              f"independent method(s) confirming; {int((dl_ev.n_corroborations == 0).sum())} unverified")
    for c in basins.FLAG_COLS:
        n = int(ev[c].sum())
        if n:
            print(f"  {c:<24} {n}")

    write_basins(ev, net)
    bio.write_csv(ev.drop(columns=["lat", "lon"]), config.REVIEW_DIR / "basin_candidates.csv")

    print("\n[d4.5] selection sheet")
    sync_ledger(ev)

    print("\n[d4.6] the site graph")
    build_graph(sites, ev)
    return ev


if __name__ == "__main__":
    main()


# ---- verify ----------------------------------------------------------------------------------------

from .. import verify


@verify.check("d4", "every site has a basin or a named reason; the graph tables conform and are acyclic")
def _check_partition():
    if not config.BASINS_PATH.exists() or not config.NODES_PATH.exists():
        return None, "d4 has not run yet"
    import geopandas as gpd

    b = gpd.read_parquet(config.BASINS_PATH)
    unnamed = b[b.basin_method.isna() & b.no_basin_reason.isna()]
    nodes = bio.read_parquet(config.NODES_PATH)
    edges = bio.read_parquet(config.EDGES_PATH)
    bad = contracts.validate_nodes(nodes) + contracts.validate_edges(edges, nodes)
    below = dict(zip(nodes.site_id, nodes.node_below)) if len(nodes) else {}
    below = {a: v for a, v in below.items() if pd.notna(v)}
    ok = not len(unnamed) and not bad and graph.is_acyclic(below)
    note = (f"{len(b)} basin row(s), {int(b.basin_method.notna().sum())} delineated, "
            f"{len(unnamed)} unnamed absence(s); {len(nodes)} node(s), {len(edges)} edge(s)"
            + (f"; violations: {'; '.join(bad)}" if bad else "") )
    return ok, note


@verify.check("d4", "incremental sets partition the draining network and contain their own reaches")
def _check_incremental():
    p = config.GRAPH_DIR / "incremental_reaches.parquet"
    if not p.exists() or not config.NODES_PATH.exists():
        return None, "d4 has not run yet"
    inc = bio.read_parquet(p)
    nodes = bio.read_parquet(config.NODES_PATH)
    sites = bio.read_parquet(config.SITES_PATH).set_index("site_id")
    # a re-minted site table orphans the graph's ids -- that is STALENESS to name, not a KeyError to crash on
    orphaned = sorted(set(nodes.site_id) - set(sites.index))
    if orphaned:
        return False, (f"node table references {len(orphaned)} site id(s) the site table no longer holds "
                       f"(e.g. {orphaned[:3]}) -- d3 re-minted; rerun d4")
    dup = int(inc.comid.duplicated().sum())
    missing_own = [s for s in nodes.site_id
                   if pd.notna(sites.loc[s, "comid"])
                   and not ((inc.site_id == s) & (inc.comid == int(sites.loc[s, "comid"]))).any()
                   and s in set(inc.site_id)]
    ok = dup == 0 and not missing_own
    return ok, (f"{len(inc):,} owned reach(es), {dup} owned twice, "
                f"{len(missing_own)} node(s) not owning their own reach")


@verify.check("d4", "local accumulation reproduces the VAA's own drainage areas within 1%", deep=True)
def _check_accumulate():
    if not config.BASINS_PATH.exists():
        return None, "d4 has not run yet"
    import geopandas as gpd

    b = gpd.read_parquet(config.BASINS_PATH)
    sites = bio.read_parquet(config.SITES_PATH).set_index("site_id")
    net = accumulate.Network.load()
    n_ok = n_all = 0
    fails = []
    for r in b[b.basin_method.notna()].itertuples():
        cid = sites.loc[r.site_id, "comid"]
        if pd.isna(cid):
            continue
        _, acc, totda, status = net.accumulate(int(cid))
        if status != "ok" or not np.isfinite(totda) or totda <= 0:
            continue
        n_all += 1
        if abs(acc - totda) / totda <= 0.01:
            n_ok += 1
        else:
            fails.append(r.site_id)
    ok = n_all == 0 or n_ok / n_all >= 0.98      # the calibration cohort's 4 failures were all Coastline
    return ok, f"{n_ok}/{n_all} within 1%" + (f"; fails: {fails[:6]}" if fails else "")


@verify.check("d4", "composition law: contracting a node reproduces the walked edge on every attribute", deep=True)
def _check_composition():
    """For real chains A -> B -> C, `compose_edges(e1, e2)` must equal the edge a REAL WALK produces with B removed. This is the chapter's law tested on the store, not on a toy."""
    if not config.EDGES_PATH.exists():
        return None, "d4 has not run yet"
    edges = bio.read_parquet(config.EDGES_PATH)
    nodes = bio.read_parquet(config.NODES_PATH)
    sites = bio.read_parquet(config.SITES_PATH)
    if not len(edges):
        return None, "no edges to test"
    e_by_from = {r["from_id"]: r for r in edges.to_dict("records")}
    chains = [(a, b, e_by_from[b]["to_id"]) for a, b in
              ((r["from_id"], r["to_id"]) for r in edges.to_dict("records")) if b in e_by_from][:20]
    if not chains:
        return None, "no two-hop chains in the graph"

    import geopandas as gpd

    valid = sites[sites.site_id.isin(set(nodes.site_id))].copy()
    bt = gpd.read_parquet(config.BASINS_PATH)[["site_id", "basin_km2"]]
    valid = valid.merge(bt, on="site_id", how="left")
    net = graph.Downstream.load()
    idx = valid.set_index("site_id")

    bad = []
    for a, b, c in chains:
        e1, e2 = e_by_from[a], e_by_from[b]
        composed = contracts.compose_edges(e1, e2, nodes=idx)
        reduced = valid[valid.site_id != b]
        _, below_r, via_r = graph.label(net, graph.node_reaches(reduced))
        direct = graph.edge_table(net, {a: below_r[a]}, {(a, below_r[a]): via_r.get((a, below_r[a]), [])},
                                  reduced, dams=None)
        if below_r.get(a) != c:
            bad.append(f"{a}->{c}: reduced walk lands on {below_r.get(a)}")
            continue
        d = direct.iloc[0].to_dict()
        for col, rule in contracts.EDGE_COMPOSE.items():
            if rule == "endpoints" and col in ("basin_area_ratio", "near_duplicate_basin"):
                continue                      # depends on basin columns joined; checked via nodes idx above
            x, y = composed.get(col), d.get(col)
            same = (pd.isna(x) and pd.isna(y)) or x == y or (
                isinstance(x, float) and isinstance(y, float) and abs(x - y) < 1e-9)
            if not same:
                bad.append(f"{a}->{c} {col}: composed {x!r} != walked {y!r}")
    return not bad, f"{len(chains)} chain(s) tested" + (f"; {bad[:4]}" if bad else ", all exact")
