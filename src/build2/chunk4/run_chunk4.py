"""Chunk 4 — delineate a basin for every physical gauge, four ways, and pick one.

    python -m src.build2.run chunk4
    python -m src.build2.run chunk4 --force        # rebuild every candidate from scratch
    python -m src.build2.run chunk4 --review       # also render the review panel

Four methods per gauge (`basins`), six advisory flags (`flags`), and one auto rule: basin0 if the authority has one, else basin1. Every delineated gauge lands in `basin-review/basin_decisions.csv` with the auto choice recorded and a blank `decision` beside it, and writing a method there overrules the rule for that gauge on the next run.

IT DOES NOT STOP THE BUILD. Chunks 2 and 5 gate because identity and cohort membership are decisions a person must make before anything downstream is meaningful. A delineation is not: the auto rule answers every gauge, the flags say which answers are worth a second look, and an override can be applied at any later point without the pipeline having waited. The in-depth judgement -- four polygons over real terrain -- belongs to the map widget, which needs the read layer that chunk 6 builds.

ONE ROW PER PHYSICAL GAUGE, NOT PER REGISTRATION. A merged pair is one gauge and gets one basin, delineated from the CANONICAL member's snap. That is a deliberate choice rather than whichever member the loop reaches first: the members of a sequential merge sit metres apart and their basins would be near-identical, so the difference is invisible in the output and would still be arbitrary.

THE REACH SET IS PART OF THE OUTPUT, and it is what chunk 5 will actually consume -- covariates are keyed on COMID, so aggregating them to a gauge means summing over its upstream reaches, not intersecting a polygon. basin0 and basin3 are polygons with no reach set of their own, so a gauge that selects one still gets basin1's reaches, recorded as `reach_set_method`. That pairing is only defensible because the areas are checked: `flag_basin0_disagrees` fires when the authority's polygon and basin1 differ by more than 10%, which is exactly the case where borrowing the reach set would be wrong.
"""

from __future__ import annotations

if __name__ == "__main__" and __package__ in (None, ""):
    import pathlib as _pl
    import runpy as _rp
    import sys as _sys

    _root = _pl.Path(__file__).resolve().parents[3]
    _sys.path.insert(0, str(_root))
    _rp.run_module("src.build2.chunk4.run_chunk4", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

import argparse

import numpy as np
import pandas as pd

from .. import config, io as bio, schema
from . import accumulate, basins, flags, graph

# One row per gauge and per upstream reach: the delineation in the form chunk 5 needs.
BASIN_REACHES = config.BASIN_REACHES          # re-exported; the path is declared once, in config


def gauges(reg: pd.DataFrame) -> pd.DataFrame:
    """The physical gauges: canonical members of merge groups, unmerged registrations, AND A RECORD.

    A GAUGE IS SOMETHING THAT MEASURED SOMETHING. `registrations.parquet` is the NWIS site index plus the
    nitrate networks -- 15,924 rows, of which 9,801 returned no observation of any parameter in the declared
    window. Those are catalogue entries, not sensors: 9,798 are USGS, 9,778 arrived from the census sweep,
    and 8,632 of them read `stream` only because the type fell through with nothing to fall back on.

    Without this test chunk 4 delineates 15,362 basins twice over, spends 15,194 NLDI calls on `basin0`, and
    builds a 14,329-node graph in which two thirds of the nodes measure nothing. With it: 5,989 delineations,
    5,824 calls, 5,623 nodes -- discharge 4,602, stage 2,344, temperature 1,371, spec cond 842, DO 605,
    turbidity 523, pH 502, nitrate 315. That is what PLAN.md means by "every NWIS continuous series in the
    AOE"; the predicate for it was simply never written.

    `channels_present` IS THE RIGHT EVIDENCE and it is chunk 2's, so this reaches backward rather than
    forward. It is set for any registration that canonicalised at least one channel, which keeps every
    discharge-only and stage-only site the network features depend on -- the test is "measured anything",
    not "measured nitrate".
    """
    keep = reg.is_group_canonical.fillna(False) | reg.physical_gauge_group.isna()
    keep &= reg.channels_present.notna()
    return reg[keep].reset_index(drop=True)


def evidence(g: pd.DataFrame, net: accumulate.Network) -> pd.DataFrame:
    """The full per-gauge table: candidate areas, capture evidence, selection, flags."""
    cand = basins.candidate_table(g)
    cand = cand.merge(flags.capture_evidence(g, net), on="site_uid", how="left")

    # NHDPlus's own accumulated area for the snapped reach, and the size of the walk that reproduced it. Both come from the topology rather than from the cache, so they are present even for a gauge whose dissolve failed -- which is the case where the comparison is most worth having.
    tot, nre = [], []
    for r in g.itertuples():
        s, _, totda, _ = net.accumulate(r.comid)
        tot.append(totda)
        nre.append(len(s))
    cand["vaa_totda_km2"] = tot
    cand["n_reaches"] = nre

    cand = basins.select(cand)
    meta = g[["site_uid", "station_name", "site_type", "snap_dist_m", "comid_agree", "comid",
              "catchment_comid", "lat", "lon"]]
    cand = cand.merge(meta, on="site_uid", how="left")
    cand["dist_to_basin_km"] = [
        basins.dist_to_basin_km(basins.geometry_for(r["site_uid"], basins.method_of(r)),
                                r["lat"], r["lon"])
        for r in cand.to_dict("records")]
    for c, v in pd.DataFrame([flags.evaluate(r) for r in cand.to_dict("records")]).items():
        cand[c] = v
    corr = [flags.corroboration(r) for r in cand.to_dict("records")]
    cand["n_corroborations"] = pd.array([n for n, _ in corr], dtype="Int64")
    cand["corroborated_by"] = pd.array([s or None for _, s in corr], dtype="string")
    return cand


# NHDPlus writes this where a value is unknown. It is not a small number, it is a missing one, and differencing two of them produces a plausible-looking zero.
_VAA_NODATA = -9998.0


def unit_properties(pairs: pd.DataFrame, outlet: dict) -> pd.DataFrame:
    """Add `area_km2`, `flow_dist_km` and `flow_time_d` to the gauge-reach pairs.

    WHAT MAKES A CATCHMENT POSITIONABLE INSIDE ITS BASIN. Membership alone says a catchment drains to this gauge; these say how big it is and how far -- in kilometres and in days of travel -- the water has to go. Everything downstream that wants to weight a covariate by position starts here, and the zoning itself belongs to the feature layer, which is why this stores the quantities and not the zones.

    BOTH DISTANCES ARE DIFFERENCES OF A CUMULATIVE FIELD. `pathlength` and `pathtimema` are measured to the terminal outlet, so subtracting the gauge's own reach leaves the distance from a catchment down to the gauge. `pathtimema` is the field to use for time -- NOT `totma`, which is the reach's OWN travel time (median 0.083 d, about two hours) and differences to noise around zero.

    THE SENTINEL IS NOT A NUMBER. NHDPlus writes -9999 for unknown, on 2.5% of reaches for `totma` and 3.0% for `pathtimema`; differencing two sentinels gives a confident 0.0, which would read as "at the gauge". Both operands are nulled before subtracting, so an unknown propagates as an unknown -- and because the reference is the GAUGE's reach, one missing value there nulls that gauge's whole basin. It did: 17 gauges holding 29% of all pairs came back entirely null for `flow_time_d`.

    SO A MISSING REFERENCE FALLS BACK TO THE BASIN MINIMUM. The gauge's reach is the most downstream in its own basin, so the smallest `pathtimema` in the set should be the gauge's own -- measured, it matches on 341 of the 349 gauges that have both, and the minimum exists for 365 of 366. The eight that disagree are divergences, where the reach set contains a path running below the outlet; for them the fallback would shift every distance, which is why it is a FALLBACK and not the rule.

    NOTHING IS CLIPPED BEYOND THE SENTINEL. `flow_time_d` runs to 51,524 days, because `pathtimema` accumulates reservoir residence rather than travel, and 19% of all pairs exceed a year -- concentrated in the continental basins, since the per-gauge median is 1.62 days. Clipping needs a defensible threshold and there is not one; the quantity is stored as published and the feature layer decides what it will believe.
    """
    from ..chunk1 import network

    v = pd.read_parquet(network._path("vaa"),
                        columns=["comid", "areasqkm", "pathlength", "pathtimema"]).set_index("comid")
    for c in ("pathlength", "pathtimema"):
        v[c] = v[c].where(v[c] > _VAA_NODATA)

    out = pairs.copy()
    out["area_km2"] = v.areasqkm.reindex(out.comid.to_numpy()).astype("float32").to_numpy()
    at_gauge = out.site_uid.map(outlet)
    for src, dst in (("pathlength", "flow_dist_km"), ("pathtimema", "flow_time_d")):
        here = pd.Series(v[src].reindex(out.comid.to_numpy()).to_numpy(), index=out.index)
        ref = pd.Series(v[src].reindex(at_gauge.to_numpy()).to_numpy(), index=out.index)
        fallback = here.groupby(out.site_uid).transform("min")
        n_fb = int(out.site_uid[ref.isna()].nunique())
        if n_fb:
            print(f"    {dst}: {n_fb} gauge(s) have no {src} at their own reach; "
                  f"using the basin minimum instead", flush=True)
        out[dst] = (here - ref.fillna(fallback)).astype("float32")
    return out


def write_basins(ev: pd.DataFrame, net: accumulate.Network) -> None:
    """`basins.parquet` (the selected polygon per gauge) and `basin_reaches.parquet` (its upstream reaches).

    A gauge with no basin still gets a row, with a null geometry and its `selection_mode` of `none` -- chunk 6 decides what to do about it, and a missing row would look like a gauge that was never considered.
    """
    import geopandas as gpd

    geoms, rows = [], []
    for r in ev.to_dict("records"):
        geoms.append(basins.geometry_for(r["site_uid"], basins.method_of(r)))
        rows.append({k: r.get(k) for k in
                     ["site_uid", "basin_method", "selection_mode", "no_basin_reason", "basin_km2",
                      "vaa_totda_km2", "n_reaches", "dist_to_basin_km",
                      "n_corroborations", "corroborated_by"] + list(flags.FLAG_COLS)})
    out = gpd.GeoDataFrame(pd.DataFrame(rows), geometry=geoms, crs=4326)
    out["aoe_stamp"] = config.aoe_stamp()
    config.BASINS.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.BASINS.with_name(config.BASINS.name + ".tmp")
    out.to_parquet(tmp)
    tmp.replace(config.BASINS)
    print(f"  wrote {config.BASINS.name} ({len(out)} gauges, "
          f"{int(out.geometry.notna().sum())} with geometry)")

    # The reach set behind each selection. basin2 walks from the containing catchment; everything else walks from the snapped reach -- see the module docstring for why a polygon method borrows basin1's set.
    pairs, method, outlet = [], {}, {}
    for r, cid in zip(ev.to_dict("records"), ev.comid):
        m = basins.method_of(r)
        if m is None:
            continue
        use = "basin2" if m == "basin2" else "basin1"
        s, _, _, status = net.accumulate(cid if use == "basin1" else r.get("catchment_comid", cid))
        if status != "ok":
            continue
        method[r["site_uid"]] = use
        outlet[r["site_uid"]] = int(cid if use == "basin1" else r.get("catchment_comid", cid))
        pairs.append(pd.DataFrame({"site_uid": r["site_uid"], "comid": sorted(s)}))
    if pairs:
        df = pd.concat(pairs, ignore_index=True)
        df["reach_set_method"] = df.site_uid.map(method)
        df = unit_properties(df, outlet)
        bio.write_parquet(df, BASIN_REACHES, index=False)
        print(f"  wrote {BASIN_REACHES.name} ({len(df):,} gauge-reach pairs over "
              f"{df.site_uid.nunique()} gauges)")
        # PER-GAUGE medians, not row-weighted: the top five basins hold 63% of all pairs, so a median over
        # rows describes the Mississippi rather than the cohort.
        for c in ("area_km2", "flow_dist_km", "flow_time_d"):
            per = df.groupby("site_uid")[c].median()
            print(f"    {c:<13} {int(df[c].isna().sum()):>6,} null | per-gauge median "
                  f"{per.median():,.2f}  p90 {per.quantile(0.9):,.1f}  max {per.max():,.1f}")


def main(force: bool = False, review: bool = False, skip_basin0: bool = False,
         skip_basin3: bool = False) -> pd.DataFrame:
    if not config.REGISTRATIONS_PATH.exists():
        raise FileNotFoundError(f"{config.REGISTRATIONS_PATH} is missing -- run chunk 0 first")
    reg = pd.read_parquet(config.REGISTRATIONS_PATH)
    g = gauges(reg)
    print(f"  {len(reg):,} registrations -> {len(g):,} physical gauges")

    net = accumulate.Network.load()

    print("\n[4.1] basin1 + basin2  (local upstream accumulation)")
    basins.build_local(g, net, force=force)

    print("\n[4.2] basin0  (NLDI, the authority's own delineation)")
    if skip_basin0:
        print("  skipped")
    else:
        basins.build_basin0(g, force=force)

    print("\n[4.3] basin3  (D8 raster flood-fill)")
    if skip_basin3:
        print("  skipped")
    else:
        area1 = dict(zip(basins.candidate_table(g).site_uid, basins.candidate_table(g).basin1_km2))
        basins.build_basin3(g, area1, force=force)

    print("\n[4.4] evidence, flags and selection")
    ev = evidence(g, net)
    print(f"  selection: {ev.selection_mode.value_counts().to_dict()}")
    nb = ev.no_basin_reason.value_counts().to_dict()
    if nb:
        print(f"  no basin:  {nb}")
    print(f"  method:    {ev.basin_method.value_counts(dropna=False).to_dict()}")
    dl = ev[ev.selection_mode != "none"]
    print(f"  corroboration: {dl.n_corroborations.value_counts().sort_index().to_dict()} "
          f"independent method(s) confirming; {int((dl.n_corroborations == 0).sum())} unverified")
    no_auth = dl[dl.basin0_status != "ok"]
    print(f"  of the {len(no_auth)} gauge(s) with NO authority basin, "
          f"{int((no_auth.n_corroborations > 0).sum())} are corroborated by another method")
    for c in flags.FLAG_COLS:
        n = int(ev[c].sum())
        if n:
            print(f"  {c:<24} {n}")

    write_basins(ev, net)
    bio.write_csv(ev, config.BASIN_CANDIDATES)

    print("\n[4.5] selection sheet")
    _record(ev, review=review)

    print("\n[4.6] the sensor graph")
    build_graph(g, ev)
    return ev


def build_graph(g: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    """Nodes, edges and the DAG columns. Writes `nodes.parquet` and `node_edges.parquet`.

    RUNS AFTER THE BASINS ONLY BECAUSE THE BASIN COLUMNS RIDE ALONG, not because the graph needs them: the walk depends on the snap and nothing else. A node is a gauge whose type both drains a surface catchment and drains ITS OWN -- `NOT_TRAINABLE`, so an edge-of-field sensor whose 10 ha contributing area is two orders of magnitude under its catchment is out for the same reason a lake is, by a different route. Validity is decided by type and snap, never by whether a delineation succeeded, or a gauge would drop out of the network for a reason that has nothing to do with the network.
    """
    from ..chunk3.site_type import NOT_TRAINABLE

    valid = g[g.comid.notna() & ~g.site_type.isin(NOT_TRAINABLE)].copy()
    net = graph.Downstream.load()
    on_reach = graph.node_reaches(valid)
    owner, below, via = graph.label(net, on_reach)
    if not graph.is_acyclic(below):
        raise ValueError("the node graph is cyclic -- water cannot reach itself")

    counts = graph.reach_counts(list(valid.site_uid), below)
    out = valid.merge(counts, on="site_uid", how="left")

    keep = [c for c in ("site_uid", "basin_method", "basin_km2", "n_reaches",
                        "n_corroborations", "corroborated_by") if c in ev.columns]
    if len(keep) > 1:
        out = out.drop(columns=[c for c in keep if c != "site_uid"], errors="ignore")
        out = out.merge(ev[keep], on="site_uid", how="left")
    area = pd.to_numeric(out.get("basin_km2"), errors="coerce")
    out["log_basin_km2"] = np.log10(area.where(area > 0))
    out["area_rank"] = area.rank(ascending=False, method="min").astype("Int64")

    # THE EDGES FIRST, because two of the node columns summarise them. `dams.on_reach` is keyed on the reach
    # a structure impounds rather than the catchment holding its coordinate -- a dam is built across a
    # divide, so the containing catchment is often a tributary's. None where the NID pull has not run, which
    # the edge contract distinguishes from a genuine zero.
    from ..chunk5 import dams

    edges = graph.edge_table(net, below, via, out, dams=dams.on_reach())
    edges = schema.conform_edges(edges)

    # The node distance columns are READ OFF the edges rather than recomputed, so the two tables cannot
    # disagree about the same channel. See `graph.flow_distances`.
    out = out.drop(columns=["flow_dist_below_km", "flow_dist_nearest_node_km"], errors="ignore")
    out = out.merge(graph.flow_distances(out, edges), on="site_uid", how="left")

    if len(edges):
        e = edges.set_index("from_uid")
        out["crosses_impoundment_below"] = out.site_uid.map(e.crosses_impoundment).astype("boolean")
        out["dam_storage_below_af"] = out.site_uid.map(e.dam_max_storage_af)

    out = schema.conform_nodes(out)
    bad = schema.validate_nodes(out)
    if bad:
        raise ValueError("node table violates the contract:\n  " + "\n  ".join(bad))
    bio.write_parquet(out, config.NODES_PATH)

    bad = schema.validate_edges(edges, out)
    if bad:
        raise ValueError("edge table violates the contract:\n  " + "\n  ".join(bad))
    bio.write_parquet(edges, config.NODE_EDGES)

    linked = len(set(below) | set(below.values()))
    comp = out.component_outlet.value_counts()
    print(f"  {len(g)} gauges -> {len(out)} valid nodes ({len(g) - len(out)} untrainable type or unsnapped)")
    print(f"  {len(edges)} edges; {linked} nodes on at least one, {len(out) - linked} isolated; acyclic")
    _report_edges(edges)
    print(f"  {len(comp)} component(s); largest {int(comp.iloc[0])} nodes at "
          f"{comp.index[0]}" if len(comp) else "  no components")
    print(f"  wrote {config.NODES_PATH.name} and {config.NODE_EDGES.name}")
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
            print(f"    {r.from_uid} -> {r.to_uid}  areas within "
                  f"{(1 - float(r.basin_area_ratio)) * 100:.1f}%, nothing between them")


def _record(ev: pd.DataFrame, review: bool = False) -> None:
    """Write the auto/manual selection sheet. Does NOT stop the build.

    CHUNK 3 DOES NOT GATE, unlike chunks 2 and 5. Identity and site type have to be settled by a person because a wrong answer is invisible downstream and changes the unit of analysis. A delineation is different: every gauge gets the auto rule's answer, that answer is recorded with the evidence behind it, and a human can overrule any of them later without the build having waited. Blocking on 39 flagged basins bought a queue nobody could work through in a terminal, when the tool that can actually answer the question -- the map widget, showing four polygons over real terrain -- is a later chunk's job.

    ONE ROW PER DELINEATED GAUGE, which is the old `preferred_basin.csv` shape. Not just the flagged ones: the sheet is the override table, so anything a reviewer might want to change has to be addressable in it, and a row that appears only once something is suspicious cannot be used to correct something that merely looks fine. `auto_method` records what the rule chose and `decision` is blank until somebody disagrees.
    """
    from . import basin_review

    rows = basin_review.sheet_rows(ev)
    if not len(rows):
        print("  no delineated gauges to record")
        return
    a, k, rt = basin_review.sync_decisions_file(rows)
    print(f"  {config.BASIN_DECISIONS.name}: {len(rows)} gauge(s) -- {k} carried forward, {a} new"
          + (f", {rt} decided but no longer delineated (kept, unnumbered)" if rt else ""))
    n_manual = int((ev.selection_mode == "manual").sum())
    print(f"  {int((ev.selection_mode == 'auto').sum())} auto, {n_manual} manual override(s)"
          + ("" if n_manual else " -- set `decision` on any row to overrule the auto rule"))

    if review:
        png = basin_review.out_dir() / "basin_review.png"
        print(f"  rendering {png.name} ...", flush=True)
        basin_review.main(ev)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="rebuild every candidate, ignoring the cache")
    ap.add_argument("--review", action="store_true", help="also render the review panel (off by default)")
    ap.add_argument("--skip-basin0", action="store_true", help="no NLDI calls this pass")
    ap.add_argument("--skip-basin3", action="store_true", help="no D8 raster this pass")
    a = ap.parse_args()
    main(force=a.force, review=a.review, skip_basin0=a.skip_basin0, skip_basin3=a.skip_basin3)
