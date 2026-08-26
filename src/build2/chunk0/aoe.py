"""The AOE geometry: seed boxes unioned with every seed's upstream basin.

WHY A GEOMETRY AND NOT A BIGGER BOX. Full basin containment is only affordable because the region follows drainage divides. Measured on this cohort: the union is 4.09M km2 against the seed boxes' 3.00M, 1.36x -- while the BOUNDING BOX of that same union is 8.42M km2, 2.80x. The difference is the Missouri arm, a narrow branching region reaching the Dakotas and eastern Montana that a rectangle turns into the entire high plains. Raster volume scales with area, so the box version does not fit on disk and the polygon does.

The consequence is that bbox-only sources must clip their results back immediately, or the saving is given away exactly where the rasters are largest.

STAMPED, because the extent is now a computed artifact rather than three numbers in a config file. Every clip records `aoe_stamp()`; a join against a differently-stamped table refuses instead of silently mixing extents. This is the version tag `global_node_id` never had, and its absence is what made a region change corrupt four tables at once.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from .. import config


# Union artifacts: slivers left where two polygon boundaries almost but not quite coincide. Real disconnected drainage is orders of magnitude larger than this, so the threshold cannot swallow one.
MIN_PART_KM2 = 1.0


def build(basins) -> "object":
    """Union the seed basins with the seed boxes, then drop sliver parts. Returns a shapely geometry in EPSG:4326.

    THE SLIVERS ARE NOT COSMETIC. Unioning 315 basins leaves hairline fragments -- here two parts of ~0 km2 against a main body of 4,284,001 -- and their only effect is to make the result a MultiPolygon. `pygeoogc.esri_query` rejects MultiPolygon outright, so the network download fails on a geometry that is 99.99997% a single polygon.
    """
    from shapely.geometry import MultiPolygon
    from shapely.ops import unary_union

    parts = [config.seed_geometry()]
    if basins is not None and len(basins):
        use = basins
        if "nldi_basin_flag" in basins.columns:
            # The ONE exclusion, and it is about the extent rather than the site: a polygon larger than any CONUS basin cannot be a correct delineation of anything in scope, and two of them cost a 36% larger raster download. The row keeps its geometry, its area and its flag on the site table.
            use = basins[basins.nldi_basin_flag.isna()]
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


def _stable(geom) -> tuple["object", str | None]:
    """`(geometry, stamp)` reusing what is on disk when the extent has not moved. `(geom, None)` when it has.

    Returning the OLD geometry too, not just the old stamp: keeping the new bytes under the old stamp would make the next run hash something different again and re-open the same question.
    """
    if not config.AOE_PATH.exists():
        return geom, None
    try:
        import geopandas as gpd

        old = gpd.read_parquet(config.AOE_PATH)
        prev, prev_stamp = old.geometry.iloc[0], str(old.stamp.iloc[0])
    except Exception as e:                             # noqa: BLE001 -- an unreadable AOE is a new one
        print(f"  could not read the existing AOE ({type(e).__name__}); stamping fresh")
        return geom, None
    # MEASURED, NOT COMPARED EXACTLY. `equals()` is exact topological equality, and the rebuild is
    # `seed_boxes | every basin` from scratch rather than a union onto what is on disk -- so adding one seed
    # re-nodes the whole boundary and `equals()` says False for a region that did not move. It said False for
    # a rebuild whose area differed by 1 km2 in 4.28 million. The question the stamp answers is whether the
    # EXTENT changed, and the honest test of that is how much area the two disagree about.
    try:
        moved = config.equal_area_km2(geom.symmetric_difference(prev))
    except Exception as e:                             # noqa: BLE001 -- an unmeasurable difference is a difference
        print(f"  could not compare against the existing AOE ({type(e).__name__}); stamping fresh")
        return geom, None
    if moved <= MIN_PART_KM2:
        print(f"  extent unchanged ({moved:.3f} km2 differs, under the {MIN_PART_KM2} km2 sliver "
              f"threshold) -- keeping stamp {prev_stamp}")
        return prev, prev_stamp
    print(f"  extent moved by {moved:,.1f} km2 -- restamping")
    return geom, None


def write(geom, n_seeds: int) -> dict:
    """Persist the geometry with its stamp and provenance. Returns the summary for the caller to print.

    Atomic, and it clears `config`'s cached geometry and stamp -- both are `lru_cache`d, so a process that read the old AOE earlier in the same run would otherwise keep clipping against it.

    THE STAMP IS A STATEMENT ABOUT THE EXTENT, NOT ABOUT ITS VERTICES. Unioning a basin that lies wholly inside the existing geometry can still re-node the boundary, and the stamp is `sha256(wkb)` -- so the bytes move while the region does not. `run_chunk0._invalidate_stale` then nulls every `aoe_dependent` column on every registration and the snap, merge, type and network blocks are all re-derived to arrive back where they started. Measured on the WQP admission: 14 basins unioned, `equals()` True, symmetric difference 0.0 km2, stamp `f36c0630dd1f` -> `02a9f70f13d4`.

    So where the new geometry is TOPOLOGICALLY EQUAL to the one on disk, both the old stamp and the old bytes are kept. `equals()` rather than `==` is the whole point: it asks whether the two describe the same region, which is the question the stamp is supposed to answer.
    """
    import hashlib

    import geopandas as gpd

    geom, stamp = _stable(geom)
    stamp = stamp or hashlib.sha256(geom.wkb).hexdigest()[:12]
    seed_km2 = config.equal_area_km2(config.seed_geometry())
    aoe_km2 = config.equal_area_km2(geom)
    g = gpd.GeoDataFrame(
        {
            "stamp": [stamp],
            "n_seeds": [n_seeds],
            "area_km2": [aoe_km2],
            "seed_area_km2": [seed_km2],
            "generated_at": [dt.datetime.now().isoformat(timespec="seconds")],
        },
        geometry=[geom], crs=4326)
    config.AOE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.AOE_PATH.with_name(config.AOE_PATH.name + ".tmp")
    g.to_parquet(tmp)
    tmp.replace(config.AOE_PATH)
    config.aoe_geometry.cache_clear()
    config.aoe_stamp.cache_clear()
    return dict(stamp=stamp, n_seeds=n_seeds, area_km2=aoe_km2, seed_area_km2=seed_km2,
                ratio=aoe_km2 / seed_km2 if seed_km2 else float("nan"))


def summarise(s: dict) -> str:
    return (f"  AOE {s['area_km2']:,.0f} km2 from {s['n_seeds']} seeds "
            f"({s['ratio']:.2f}x the seed boxes' {s['seed_area_km2']:,.0f} km2)\n"
            f"  stamp {s['stamp']}")
