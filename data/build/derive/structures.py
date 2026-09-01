"""Structures on the network: point layers with an exact station along their reach.

POINTS ARE PUBLISHED AS POINTS. Aggregating a structure's location away into "exists somewhere in this catchment" discards exactly what a flow-path question needs -- which of two sensors is the structure between? Every structure point carries `(comid, snap_pos_m, snap_dist_m)` from the same convention as the sensor snap (position in metres along the flowline from its upstream end), so structures and sensors ORDER TOGETHER along a reach: sensor A at 300 m, dam at 850 m, sensor B at 1,400 m is a query, not a drawing exercise.

TWO POINT LAYERS: dam points (from `dams._dam_points`) and treatment-facility outfalls (from the ICIS-NPDES pull, already AOE-clipped, joined to CWNS POTW attributes on the permit number). The raw nitrogen DMR rows stay in the acquired store at their native grain; turning them into honest annual loads is a documented-methodology aggregation that belongs where its assumptions can be chosen, not baked here.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from .. import config, io as bio, verify
from ..acquire import facilities as fac_mod
from ..acquire import network, snapshot

# A structure further than this from any flowline is a structure on something unmapped; its point row
# keeps null reach columns rather than claiming a river it is not on. Same constant family as the dam
# reach keying.
SNAP_MAX_M = 500.0


def _input_stamp(*paths) -> str:
    """`io.input_stamp`, re-exported under the name this module's products already call."""
    return bio.input_stamp(*paths)


def snap_points(gdf) -> pd.DataFrame:
    """(comid, snap_pos_m, snap_dist_m) per point, nearest flowline, EQUAL_AREA metres. Null beyond SNAP_MAX_M."""
    import geopandas as gpd
    from shapely import STRtree

    fl = gpd.read_parquet(network._path("flowlines"), columns=["COMID", "geometry"]).to_crs(config.EQUAL_AREA)
    p = gdf.to_crs(config.EQUAL_AREA)
    tree = STRtree(fl.geometry.values)
    idx = tree.nearest(p.geometry.values)
    lines = fl.geometry.values[idx]
    dist = p.geometry.values.distance(lines)
    pos = np.array([float(ln.project(pt)) for ln, pt in zip(lines, p.geometry.values)])
    comid = pd.to_numeric(fl.COMID.to_numpy()[idx], errors="coerce")
    far = dist > SNAP_MAX_M
    out = pd.DataFrame({
        "comid": pd.array(np.where(far, np.nan, comid), dtype="Int64"),
        "snap_pos_m": np.where(far, np.nan, np.round(pos, 1)),
        "snap_dist_m": np.round(dist, 1),
    }, index=gdf.index)
    return out


def build_dam_points(force: bool = False) -> pd.DataFrame | None:
    """The dam POINT layer: exact coordinates + NID attributes + the reach station. None before the NID pull."""
    from . import dams

    out_p = config.COVARIATES_DIR / "dam_points.parquet"
    if out_p.exists() and not force:
        try:
            return bio.read_parquet(out_p, expect={
                "aoe": config.aoe_stamp(),
                "inputs": _input_stamp(config.ACQ_ATTRIBUTES / "nid.parquet")})
        except bio.StaleArtifactError:
            print("  dam_points: AOE or NID changed -- rebuilding")
    if not (config.ACQ_ATTRIBUTES / "nid.parquet").exists():
        return None
    pts = dams._dam_points()
    # AOE clip via the catchment join dams.build already trusts -- a point in no AOE catchment is outside.
    import geopandas as gpd

    cats = gpd.read_parquet(network._path("catchments"), columns=["FEATUREID", "geometry"])
    j = pts.sjoin(cats.rename(columns={"FEATUREID": "containing_comid"}), predicate="within", how="inner")
    j = j.drop(columns=[c for c in ("index_right",) if c in j.columns])
    snapped = snap_points(j)
    out = pd.DataFrame({
        "lat": j.geometry.y, "lon": j.geometry.x,
        "containing_comid": pd.to_numeric(j.containing_comid, errors="coerce").astype("Int64"),
        **{c: j[c] for c in j.columns if c not in ("geometry", "containing_comid")},
    }).join(snapped)
    bio.write_parquet(out.reset_index(drop=True), out_p,
                      stamps={"aoe": config.aoe_stamp(),
                              "inputs": _input_stamp(config.ACQ_ATTRIBUTES / "nid.parquet")})
    on_reach = int(out.comid.notna().sum())
    print(f"  dam_points.parquet: {len(out):,} dam(s) in the AOE, {on_reach:,} stationed on a reach")
    return out


def build_facilities(force: bool = False) -> pd.DataFrame | None:
    """The facility layer: ICIS outfalls + CWNS attributes + the reach station. None before the facilities pull."""
    out_p = config.COVARIATES_DIR / "facilities.parquet"
    if out_p.exists() and not force:
        try:
            return bio.read_parquet(out_p, expect={"aoe": config.aoe_stamp(),
                                                   "inputs": _input_stamp(fac_mod.OUTFALLS_PATH,
                                                                          fac_mod.CWNS_PATH)})
        except bio.StaleArtifactError:
            print("  facilities: AOE or inputs changed -- rebuilding (CWNS is a manual acquisition, so "
                  "this is the path that notices it arriving)")
    if not fac_mod.OUTFALLS_PATH.exists():
        return None
    import geopandas as gpd

    o = pd.read_parquet(fac_mod.OUTFALLS_PATH)
    g = gpd.GeoDataFrame(o, geometry=gpd.points_from_xy(o.lon, o.lat), crs=4326)
    snapped = snap_points(g)
    out = pd.concat([o.reset_index(drop=True), snapped.reset_index(drop=True)], axis=1)

    if fac_mod.CWNS_PATH.exists():
        c = pd.read_parquet(fac_mod.CWNS_PATH).drop_duplicates("npdes_id")
        c = c.set_index(c.npdes_id.astype(str).str.strip())
        for x in ("treatment_level", "design_flow_mgd", "population_2022"):
            if x in c.columns:
                out[f"cwns_{x}"] = out.npdes_id.astype(str).str.strip().map(c[x])
        n = int(out.get("cwns_treatment_level", pd.Series(dtype=object)).notna().sum())
        print(f"  CWNS attributes joined on the permit number ({n:,} outfall(s) matched)")
    bio.write_parquet(out, out_p,
                      stamps={"aoe": config.aoe_stamp(),
                              "inputs": _input_stamp(fac_mod.OUTFALLS_PATH, fac_mod.CWNS_PATH)})
    on_reach = int(out.comid.notna().sum())
    print(f"  facilities.parquet: {len(out):,} outfall(s), {on_reach:,} stationed on a reach")
    return out


def main(force: bool = False) -> None:
    d = build_dam_points(force=force)
    if d is None:
        print("  NID not on disk; dam points skipped (run the a3 attributes branch)")
    f = build_facilities(force=force)
    if f is None:
        print("  outfall layer not on disk; facilities skipped (run the a3 facilities branch)")
    build_dmr_loads(force=force)


# ---- DMR effluent loading ------------------------------------------------------------------------------

DMR_LOADS_PATH = config.COVARIATES_DIR / "dmr_loads.parquet"

# The nitrogen species we publish, keyed on DMR parameter code, with the factor that converts the reported
# quantity to kilograms of N. SPECIES ARE KEPT APART, NOT SUMMED: ammonia, nitrate and Kjeldahl are
# different molecules with different fates in a stream, and a facility reporting several would be counted
# many times over by a single `kg_n`. Which of them a model wants -- and whether to add them -- is a
# modelling decision the build refuses to make.
#
# `71850` reports as NO3 while every other code reports as N, the same speciation trap WQP and the
# registry's turbidity split both carry. 14.007/62.004 converts NO3 to N.
DMR_SPECIES = {
    "00610": ("ammonia", 1.0),
    "00620": ("nitrate", 1.0),
    "00630": ("nitrite_nitrate", 1.0),
    "00615": ("nitrite", 1.0),
    "00625": ("kjeldahl", 1.0),
    "00600": ("total_n", 1.0),
    "71850": ("nitrate", 14.007 / 62.004),
}

# Effluent leaving the plant. `G` is RAW SEWAGE INFLUENT -- 188,875 rows in FY2023 -- and summing it as
# discharge would report the load a plant RECEIVES as the load it emits.
DMR_EFFLUENT_CODES = ("1", "EG")

_MGD_TO_LD = 3_785_411.784      # million US gallons per day -> litres per day
_FLOW_PCODES = ("50050", "00056", "82220")

# PHYSICAL BOUNDS, on the registry's principle: instrument-impossible, never site-implausible. Self-reported
# DMR values carry sentinels, unit mislabels and typing errors, and the product of two of them is spectacular
# -- unbounded, FY2023 alone yields a monthly load of 8.2e12 kg N, more nitrogen than the world produces.
# Measured on FY2023 effluent rows:
#   flow   max 80,385,869 MGD -- two hundred times the Mississippi; 3,261 rows above 5,000 MGD, which
#          already exceeds any real plant (the largest in the US, Stickney, is ~1,400 MGD). 16 negatives.
#   conc   max 860,000,000 mg/L -- 860x the density of water; min -9999, a fill value wearing a number.
# Bounds are set well above anything real so they null garbage and not data: a strong industrial waste
# stream runs a few thousand mg/L of N, and no permitted outfall approaches 10,000 MGD.
DMR_MAX_MGD = 10_000.0
DMR_MAX_MG_L = 10_000.0
# The reported-quantity ceiling needs its own bound: it is not a product of two fields, so bounding the
# inputs does not reach it. Measured on FY2023's 221,534 effluent quantity rows, the distribution stops
# dead well below it -- p99 1,200 kg/day, p99.9 9,157, p99.99 37,333 -- and then FOUR rows sit above a
# million, topping out at 41,078,607 kg/day, which is ten times the nitrogen the whole Mississippi carries.
# 1,000,000 is five times the largest plausible plant (Stickney at 1,400 MGD and typical strength is about
# 212,000 kg/day), so it nulls those four and touches nothing else.
DMR_MAX_KG_D = 1_000_000.0
DMR_SENTINELS = (-9999.0, -99999.0, -999999.0)


def _dmr_year(path, comid_by_outfall) -> pd.DataFrame:
    """One fiscal-year archive -> monthly kg of N per (permit, outfall, species). See `build_dmr_loads`."""
    import numpy as np

    cols = ["EXTERNAL_PERMIT_NMBR", "PERM_FEATURE_NMBR", "PARAMETER_CODE", "MONITORING_LOCATION_CODE",
            "VALUE_TYPE_CODE", "STATISTICAL_BASE_CODE", "STANDARD_UNIT_DESC",
            "DMR_VALUE_STANDARD_UNITS", "DMR_VALUE_ID", "MONITORING_PERIOD_END_DATE", "NODI_CODE"]
    d = pd.read_parquet(path, columns=cols)
    d["PARAMETER_CODE"] = d.PARAMETER_CODE.astype(str)
    d = d[d.MONITORING_LOCATION_CODE.astype(str).isin(DMR_EFFLUENT_CODES)]
    # Repeats differ only in violation code and carry identical values; 5,999 of them in FY2023.
    d = d.drop_duplicates("DMR_VALUE_ID")
    d["month"] = pd.to_datetime(d.MONITORING_PERIOD_END_DATE, errors="coerce").dt.to_period("M")
    d = d[d.month.notna()]

    # NODI `C` is "no discharge", a MEASURED ZERO rather than a gap -- 263,064 rows in FY2023. Treating it
    # as missing would bias every mean upward by dropping exactly the periods a plant emitted nothing.
    val = pd.to_numeric(d.DMR_VALUE_STANDARD_UNITS, errors="coerce")
    val = val.mask(val.isin(DMR_SENTINELS))          # exact fill values, before any arithmetic
    val = val.mask(val < 0)                          # a negative discharge is not a measurement
    val = val.mask(d.NODI_CODE.astype(str).str.upper() == "C", 0.0)
    d = d.assign(_val=val)

    flow = d[d.PARAMETER_CODE.isin(_FLOW_PCODES) & (d.STANDARD_UNIT_DESC == "MGD")]
    flow = flow[flow._val <= DMR_MAX_MGD]
    flow = (flow.groupby(["EXTERNAL_PERMIT_NMBR", "PERM_FEATURE_NMBR", "month"])._val
            .mean().rename("mgd").reset_index())

    n = d[d.PARAMETER_CODE.isin(DMR_SPECIES)].copy()
    n["species"] = n.PARAMETER_CODE.map(lambda c: DMR_SPECIES[c][0])
    n["_factor"] = n.PARAMETER_CODE.map(lambda c: DMR_SPECIES[c][1])
    n["_val"] = n._val * n._factor

    # EPA's own hierarchy. Tier 1 is a reported QUANTITY and needs no flow; tier 2 multiplies a reported
    # concentration by the period's flow. A row that is neither is dropped rather than guessed at.
    q = n[(n.STANDARD_UNIT_DESC == "kg/d") & n.VALUE_TYPE_CODE.astype(str).str.startswith("Q")
          & (n._val <= DMR_MAX_KG_D)]
    q = (q.groupby(["EXTERNAL_PERMIT_NMBR", "PERM_FEATURE_NMBR", "month", "species"])._val
         .mean().rename("kg_per_day").reset_index().assign(tier="Q_reported"))

    c = n[(n.STANDARD_UNIT_DESC == "mg/L") & n.VALUE_TYPE_CODE.astype(str).str.startswith("C")
          & (n._val <= DMR_MAX_MG_L)]
    c = (c.groupby(["EXTERNAL_PERMIT_NMBR", "PERM_FEATURE_NMBR", "month", "species"])._val
         .mean().rename("mg_l").reset_index())
    c = c.merge(flow, on=["EXTERNAL_PERMIT_NMBR", "PERM_FEATURE_NMBR", "month"], how="inner")
    c["kg_per_day"] = c.mg_l * c.mgd * _MGD_TO_LD / 1e6      # mg/L x L/d -> mg/d -> kg/d
    c = c[["EXTERNAL_PERMIT_NMBR", "PERM_FEATURE_NMBR", "month", "species",
           "kg_per_day"]].assign(tier="C_times_flow")

    both = pd.concat([q, c], ignore_index=True)
    # Tier 1 wins where both exist: a reported quantity is the plant's own arithmetic, not ours.
    both["_rank"] = (both.tier == "C_times_flow").astype("int8")
    both = both.sort_values("_rank").drop_duplicates(
        ["EXTERNAL_PERMIT_NMBR", "PERM_FEATURE_NMBR", "month", "species"]).drop(columns="_rank")

    both = both[both.kg_per_day <= DMR_MAX_KG_D]     # the computed tier-2 product needs the same ceiling
    both["days"] = both.month.dt.days_in_month
    both["kg_n"] = both.kg_per_day * both.days
    both = both.rename(columns={"EXTERNAL_PERMIT_NMBR": "npdes_id",
                                "PERM_FEATURE_NMBR": "perm_feature_nmbr"})
    both["npdes_id"] = both.npdes_id.astype(str).str.strip().str.upper()
    both["perm_feature_nmbr"] = both.perm_feature_nmbr.astype(str).str.strip()
    both = both.merge(comid_by_outfall, on=["npdes_id", "perm_feature_nmbr"], how="left")
    both["month"] = both.month.dt.to_timestamp()
    return both[["npdes_id", "perm_feature_nmbr", "comid", "month", "species",
                 "kg_n", "kg_per_day", "tier"]]


def build_dmr_loads(force: bool = False) -> pd.DataFrame | None:
    """Monthly effluent nitrogen per outfall, placed on the network. `None` when the archives are absent.

    THIS IS A COVARIATE ON THE NETWORK, NOT A SENSOR AND NOT A RECORD. A treatment plant is a source term on a reach, so it lands in `derived/covariates/` beside the dam and facility tables; the sensors table must never grow rows for it.

    One row per (permit, outfall, month, species). Species stay apart -- see `DMR_SPECIES` -- and each row names the `tier` that produced it, because a reported quantity and a concentration multiplied by flow are different measurements and a model may reasonably weight them differently.

    **SUMMING `kg_n` ACROSS SPECIES DOUBLE-COUNTS, and the rows do not stop you.** `total_n` subsumes ammonia, Kjeldahl and nitrate+nitrite, and **32.4% of outfall-months report more than one species**, so a plain `groupby(...).kg_n.sum()` inflates the mass -- silently, because every row is individually correct. A consumer must pick a species rule first: prefer `total_n` where the outfall-month reports it, and otherwise sum only the disjoint components. Keeping the species apart is what makes that choice available; it is not an invitation to add them up.

    The outfall join is what makes the placement honest: DMR reports at permit x `PERM_FEATURE_NMBR`, and 55,745 permits hold more than one outfall, so a permit-grain load is not a location on a river. Outfalls that never snapped carry a null `comid` rather than being dropped -- the load is real even where we cannot place it, and a silent drop would understate the network.

    ABOUT HALF THE ROWS ARE UNPLACED, and all three reasons are legitimate. Measured over the 19 fiscal years: 5,829,599 rows across 42,602 outfalls, 51% carrying a `comid`. Of the 19,322 permits with no placement, 11,660 are simply OUTSIDE THE AOE -- the DMR archives are national while the outfalls layer is clipped to our region. Within the AOE, 4,549 (permit, outfall) pairs exist in the layer with a null `comid` because the outfall never snapped within `SNAP_MAX_M`, and 4,937 outfall numbers DMR reports against (`001A`, `001B`, `000`, ...) are absent from ICIS's outfalls layer entirely.

    A tempting repair is to attribute an unmatched outfall's load to another outfall on the same permit. That is a modelling preference -- it places a discharge at a point it may not come from -- and the build declines it.
    """
    from ..acquire import facilities

    from ..acquire import facilities as _fac

    raw = sorted((config.ACQ_FACILITIES / "dmr_n").glob("fy*.parquet"))
    inputs = _input_stamp(*raw, _fac.OUTFALLS_PATH)
    if DMR_LOADS_PATH.exists() and not force:
        try:
            # BOTH stamps. The extent decides which outfalls are in scope; the archives decide what there
            # is to load. Stamping only the first meant adding a fiscal year was a silent no-op.
            return bio.read_parquet(DMR_LOADS_PATH,
                                    expect={"aoe": config.aoe_stamp(), "inputs": inputs})
        except bio.StaleArtifactError:
            print("  dmr loads: inputs or AOE changed -- rebuilding", flush=True)

    if not raw:
        print("  dmr loads: no DMR archives on disk -- run the facilities acquisition first")
        return None
    if not facilities.OUTFALLS_PATH.exists():
        print("  dmr loads: no outfalls layer -- run the facilities acquisition first")
        return None

    out = pd.read_parquet(facilities.OUTFALLS_PATH)
    key = "npdes_id" if "npdes_id" in out.columns else "EXTERNAL_PERMIT_NMBR"
    feat = "perm_feature_nmbr" if "perm_feature_nmbr" in out.columns else "PERM_FEATURE_NMBR"
    snapped = build_facilities()
    if snapped is not None and "comid" in snapped.columns:
        out = snapped
        key = "npdes_id" if "npdes_id" in out.columns else key
        feat = "perm_feature_nmbr" if "perm_feature_nmbr" in out.columns else feat
    xwalk = out[[key, feat, "comid"]].rename(columns={key: "npdes_id", feat: "perm_feature_nmbr"})
    xwalk["npdes_id"] = xwalk.npdes_id.astype(str).str.strip().str.upper()
    xwalk["perm_feature_nmbr"] = xwalk.perm_feature_nmbr.astype(str).str.strip()
    xwalk = xwalk.dropna(subset=["perm_feature_nmbr"]).drop_duplicates(["npdes_id", "perm_feature_nmbr"])

    frames = []
    for p in raw:
        f = _dmr_year(p, xwalk)
        frames.append(f)
        print(f"    {p.stem}: {len(f):,} (outfall, month, species) row(s)", flush=True)
    d = pd.concat(frames, ignore_index=True).sort_values(["npdes_id", "perm_feature_nmbr", "month"])
    d = d.drop_duplicates(["npdes_id", "perm_feature_nmbr", "month", "species"], keep="last")
    DMR_LOADS_PATH.parent.mkdir(parents=True, exist_ok=True)
    bio.write_parquet(d.reset_index(drop=True), DMR_LOADS_PATH,
                      stamps={"aoe": config.aoe_stamp(), "inputs": inputs})
    placed = int(d.comid.notna().sum())
    print(f"  dmr loads: {len(d):,} row(s), {placed:,} placed on a reach ({placed / max(len(d), 1):.0%}), "
          f"{d.species.nunique()} species", flush=True)
    return d


@verify.check("d5", "DMR loads are bounded, non-negative, and every row names its derivation tier")
def _check_dmr_loads():
    """The loading product's own invariants. Publishing an unbounded self-reported field is how a single outfall came to claim 8.2e12 kg of nitrogen in a month.

    Placement is deliberately NOT asserted: about half the rows carry no `comid` for three legitimate reasons (see `build_dmr_loads`), and a check demanding placement would be demanding the build guess.
    """
    if not DMR_LOADS_PATH.exists():
        return None, "no DMR loading product yet"
    d = pd.read_parquet(DMR_LOADS_PATH,
                        columns=["kg_n", "kg_per_day", "species", "tier", "comid"])
    if not len(d):
        return None, "DMR loading product is empty"
    bad = []
    if (d.kg_n < 0).any():
        bad.append(f"{int((d.kg_n < 0).sum()):,} negative kg_n")
    if (d.kg_per_day > DMR_MAX_KG_D).any():
        bad.append(f"{int((d.kg_per_day > DMR_MAX_KG_D).sum()):,} above the {DMR_MAX_KG_D:,.0f} kg/day ceiling")
    unknown_species = set(d.species.unique()) - {v[0] for v in DMR_SPECIES.values()}
    if unknown_species:
        bad.append(f"undeclared species {sorted(unknown_species)}")
    unknown_tier = set(d.tier.unique()) - {"Q_reported", "C_times_flow"}
    if unknown_tier:
        bad.append(f"undeclared tier {sorted(unknown_tier)}")
    if d.tier.isna().any():
        bad.append(f"{int(d.tier.isna().sum()):,} row(s) with no tier")
    placed = int(d.comid.notna().sum())
    if bad:
        return False, "; ".join(bad)
    return True, (f"{len(d):,} row(s), {d.species.nunique()} species, "
                  f"{placed:,} placed on a reach ({placed / len(d):.0%})")
