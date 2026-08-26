"""D1 -- the extent: seed boxes unioned with every seed's basin, stamped by REGION, not by vertices.

WHY A GEOMETRY AND NOT A BIGGER BOX. Full containment is affordable only because the region follows drainage divides: the union measures ~4.28M km2 against the boxes' 3.00M (1.43x), while the union's BOUNDING BOX is 8.42M km2 (2.80x) -- the Missouri arm is a narrow branching region a rectangle turns into the entire high plains. Raster volume scales with area; the box version does not fit on disk and the polygon does.

THE STAMP IS A STATEMENT ABOUT THE EXTENT, NOT ITS VERTICES. The union is rebuilt from scratch each run, and GEOS re-nodes the whole boundary when any operand changes -- exact equality said "different" for a rebuild whose area moved 1 km2 in 4.28M, and the measured WQP admission produced equals()==True with a CHANGED hash. So sameness is measured: symmetric difference under the sliver floor keeps the stored stamp and the stored bytes. A moved stamp nulls every extent-dependent column on every sensor, so a false move is a full re-derive bought with nothing.

THE EXTENT NEVER RE-EXPANDS ON ITS OWN. Sensors discovered inside it later are used but do not enlarge it, or each pass admits sensors whose basins admit more and the extent walks to CONUS. Expansion is a deliberate manual event (a new seed pass).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import warnings

import pandas as pd

from .. import config, verify

# Union artifacts: hairline slivers where two polygon boundaries almost coincide -- 315 basins leave
# parts of ~0 km2 against a 4.28M km2 body, and their only effect is making the result a MultiPolygon,
# which `pygeoogc.esri_query` rejects outright. Real disconnected drainage is orders of magnitude larger.
# Doubles as the region-sameness tolerance: a symmetric difference under one sliver is not a moved extent.
MIN_PART_KM2 = 1.0

# A basin larger than any CONUS drainage cannot be a correct delineation of anything in scope: NLDI
# returned 5.06M km2 for a Cape Cod site snapped onto the Mississippi, and two such polygons inflated
# the AOE 1.43x -> 1.94x. The Mississippi at Natchez, ~2.97M km2, is the physical ceiling.
MAX_CONUS_KM2 = 3.5e6


def seed_basin_frame():
    """Every cached seed basin, repaired and flagged. The acquired store is the input of record."""
    import geopandas as gpd

    paths = sorted(config.ACQ_SEED_BASINS.glob("*.parquet"))
    if not paths:
        raise SystemExit("no seed basins in the acquired store -- run a1 first")
    g = pd.concat([gpd.read_parquet(p) for p in paths], ignore_index=True)
    g = gpd.GeoDataFrame(g, geometry="geometry", crs=4326)
    # NLDI returns at least one self-intersecting polygon; unrepaired, the union raises a side-location
    # conflict rather than returning a wrong answer -- a hard requirement, not a tidy-up.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        g["geometry"] = g.geometry.make_valid().buffer(0)
    g["km2"] = g.to_crs(config.EQUAL_AREA).area / 1e6
    g["oversized"] = g.km2 > MAX_CONUS_KM2
    for r in g[g.oversized].itertuples():
        print(f"  OVERSIZED {r.site_uid}: {r.km2:,.0f} km2 -- excluded from the union")
    return g


def build(basins) -> "object":
    """Union the seed boxes with the non-flagged basins; drop sliver parts. Shapely geometry, EPSG:4326."""
    from shapely.geometry import MultiPolygon
    from shapely.ops import unary_union

    parts = [config.seed_geometry()]
    use = basins[~basins.oversized]
    if len(use) < len(basins):
        print(f"  excluded {len(basins) - len(use)} oversized basin(s) from the union")
    parts.append(unary_union(list(use.geometry)))
    geom = unary_union(parts)
    if geom.geom_type == "MultiPolygon":
        keep = [p for p in geom.geoms if config.equal_area_km2(p) >= MIN_PART_KM2]
        dropped = len(geom.geoms) - len(keep)
        if dropped:
            print(f"  dropped {dropped} sliver part(s) below {MIN_PART_KM2} km2")
        geom = keep[0] if len(keep) == 1 else MultiPolygon(keep)
    return geom


def _prior():
    """The extent to measure sameness against: this store's, else -- once, at the rewrite boundary -- frozen build2's.

    The fallback keeps STAMP CONTINUITY across the rewrite: every adopted store was cut against the old extent, and re-stamping an unchanged region would invalidate all of it for nothing. It fires only while `derived/aoe.parquet` does not exist, and says so.
    """
    import geopandas as gpd

    if config.AOE_PATH.exists():
        return gpd.read_parquet(config.AOE_PATH), ""
    legacy = config.DATA / "processed2" / "aoe.parquet"
    if legacy.exists():
        return gpd.read_parquet(legacy), " (frozen build2's -- stamp continuity across the rewrite)"
    return None, ""


def _stable(geom) -> tuple["object", str | None]:
    """(geometry, stamp) reusing the prior when the REGION has not moved; (geom, None) when it has."""
    prior, note = _prior()
    if prior is None:
        return geom, None
    prev_geom, prev_stamp = prior.geometry.iloc[0], str(prior.stamp.iloc[0])
    try:
        moved = config.equal_area_km2(geom.symmetric_difference(prev_geom))
    except Exception as e:                              # noqa: BLE001 -- an unmeasurable difference is a difference
        print(f"  could not compare against the prior extent ({type(e).__name__}); stamping fresh")
        return geom, None
    if moved <= MIN_PART_KM2:
        print(f"  extent unchanged ({moved:.3f} km2 differs, under the {MIN_PART_KM2} km2 sliver floor) "
              f"-- keeping stamp {prev_stamp}{note}")
        return prev_geom, prev_stamp
    print(f"  extent moved by {moved:,.1f} km2 -- restamping; every extent-dependent column re-derives")
    return geom, None


def main(force: bool = False, snapshot: str | None = None) -> dict:
    import geopandas as gpd

    config.ensure_dirs()
    basins = seed_basin_frame()
    geom = build(basins)
    geom, stamp = _stable(geom)
    stamp = stamp or hashlib.sha256(geom.wkb).hexdigest()[:12]

    seed_km2 = config.equal_area_km2(config.seed_geometry())
    aoe_km2 = config.equal_area_km2(geom)
    g = gpd.GeoDataFrame({
        "stamp": [stamp], "n_seeds": [int(len(basins))], "area_km2": [aoe_km2],
        "seed_area_km2": [seed_km2], "snapshot": [snapshot or ""],
        "generated_at": [dt.datetime.now().isoformat(timespec="seconds")],
    }, geometry=[geom], crs=4326)
    tmp = config.AOE_PATH.with_name(config.AOE_PATH.name + ".tmp")
    g.to_parquet(tmp)
    tmp.replace(config.AOE_PATH)
    config.aoe_geometry.cache_clear()
    config.aoe_stamp.cache_clear()
    print(f"  AOE {aoe_km2:,.0f} km2 from {len(basins)} seed basin(s) "
          f"({aoe_km2 / seed_km2:.2f}x the boxes' {seed_km2:,.0f}); stamp {stamp}")
    return dict(stamp=stamp, area_km2=aoe_km2)


# ---- verify ---------------------------------------------------------------------------------------

@verify.check("d1", "AOE exists, stamped, larger than the boxes and under 2x them")
def _check_aoe():
    if not config.AOE_PATH.exists():
        return None, "d1 has not run"
    import geopandas as gpd

    g = gpd.read_parquet(config.AOE_PATH)
    a, s = float(g.area_km2.iloc[0]), float(g.seed_area_km2.iloc[0])
    ok = s < a < 2 * s
    return ok, f"{a:,.0f} km2, {a / s:.2f}x the seed boxes, stamp {g.stamp.iloc[0]}"
