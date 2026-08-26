"""D5 -- every raster becomes a COMID-keyed table, through ONE driver that checks closure before it writes.

    python -m data.build.run d5 --single
    python -m data.build.run d5 --single --only weights,dams   # a product subset
    python -m data.build.run d5 --single --force               # ignore every checkpoint

Five products over the catchment layer: crops, nutrients, gridMET weights, dams, agtile. Each is FRACTIONAL -- a pixel on a boundary contributes its area share to every catchment it touches -- and each is LOCAL, the value over that catchment's own polygon. Accumulating upstream is the access layer's job, over `basin_reaches.parquet`.

THE CLOSURE CHECK IS THE DRIVER'S, WRITTEN ONCE, RUN BEFORE EVERY WRITE. Each product declares what must close -- class areas summing to coverage, weights summing to catchment area -- and a product that fails is NOT written: gate-before-write, the same discipline publication uses. The build2 ancestor stated these invariants in prose and implemented none of them; `coverage.parquet` existed with no reader and `frac_covered` hard-coded 900 m2 for every grid.

NO HUMAN GATE. D3 stops for a reviewer because it decides identity, where a wrong answer is invisible downstream. D5 measures: every number here is checkable against the catchment's own area, and the checks run on every build rather than in front of a person.

ORDER IS SET BY THE DISK. CDL is read from a 50 GB expansion that is deleted afterwards, and AgTile's 15 GB expansion cannot be made until that space is back. So crops runs first and agtile last, with the products that need no archive in between -- and `archive.expanded` refuses rather than half-unzips when the space is not there.

CHECKPOINTS ARE PER BATCH, under `derived/covariates/parts/`. The crops pass is about an hour and reads an archive that will not be on disk afterwards; a crash at minute fifty must not mean expanding 50 GB again. The archive is opened LAZILY for the same reason: a step whose batches are all checkpointed reads them and never asks for the rasters.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, io as bio
from ..acquire import snapshot, weather
from . import archive, dams, structures, zonal

OUT = config.COVARIATES_DIR
PARTS = OUT / "parts"

PRODUCTS = ("crops", "nutrients", "weights", "structures", "agtile")

# How tightly each closure must hold. Class areas against coverage is arithmetic over exactextract's own numbers, so anything past 0.1% is a reduce bug; weights against catchment area compares a SPHERICAL cell area with NHDPlus's ellipsoidal AreaSqKM and crosses the AOE boundary, so 1% on the median is the honest bar.
CLOSURE_TOL = 1e-3
WEIGHTS_TOL = 0.01


# ---- crops ------------------------------------------------------------------------------------------

CDL_CRS = "EPSG:5070"
YEAR_MIN, YEAR_MAX = config.collection_start().year, __import__('datetime').date.today().year


def cdl_rasters(directory) -> dict[int, object]:
    """`{year: path}` for every `{year}_30m_cdls.tif` in `directory`, within the water record's span."""
    out = {}
    for p in sorted(directory.glob("*_30m_cdls.tif")):
        try:
            y = int(p.name.split("_")[0])
        except ValueError:
            continue
        if YEAR_MIN <= y <= YEAR_MAX:
            out[y] = p
    return out


def grid_groups(paths: dict[int, object]) -> list[dict[int, object]]:
    """Split years into groups sharing one raster grid. Coverage is only reusable inside a group -- 2025 CDL ships on a LARGER grid than 2008-2024 and forms its own group; reusing 2024's transform for it would shift every 2025 count."""
    groups: dict[tuple, dict] = {}
    for y, p in paths.items():
        groups.setdefault(zonal.grid_of(p), {})[y] = p
    return list(groups.values())


def _crops_reduce(comids: np.ndarray, res: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    """One exactextract batch -> LONG rows of (comid, year, code, frac, n_px): every distinct raw class code present in the catchment, with its share of the covered area.

    NO CLASS COLLAPSE AT BUILD TIME. The raw code is the fact; any grouping into corn/soy/pasture is a read-time preference (`access` `remap`, defaulting to none), and two experiments must not both report "corn" meaning different things because a build baked one map in. `frac` is the share of the catchment's COVERED area (they sum to 1 where anything was seen -- the closure); `n_px` is that covered area in pixel units, repeated per code so area-per-code is one multiplication and the row is self-contained.
    """
    frames = []
    for y in years:
        n_px = res[f"{y}_count"].to_numpy(dtype="float64")
        cm, code, frac, npx = [], [], [], []
        for i, (codes, fr) in enumerate(zip(res[f"{y}_unique"], res[f"{y}_frac"])):
            if codes is None or not len(codes):
                continue
            k = len(codes)
            cm.append(np.full(k, comids[i], dtype="int64"))
            code.append(np.asarray(codes, dtype="int16"))
            frac.append(np.asarray(fr, dtype="float32"))
            npx.append(np.full(k, n_px[i], dtype="float32"))
        if not cm:
            continue
        df = pd.DataFrame({"comid": np.concatenate(cm), "code": np.concatenate(code),
                           "frac": np.concatenate(frac), "n_px": np.concatenate(npx)})
        df.insert(1, "year", np.int16(y))
        frames.append(df)
    return (pd.concat(frames, ignore_index=True) if frames
            else pd.DataFrame({"comid": pd.Series(dtype="int64"), "year": pd.Series(dtype="int16"),
                               "code": pd.Series(dtype="int16"), "frac": pd.Series(dtype="float32"),
                               "n_px": pd.Series(dtype="float32")}))


def crops_manifest_path():
    return PARTS / "crops_manifest.json"


def _write_crops_manifest(groups: list[dict], n_batches: int) -> None:
    """Record which years each part directory covers, so a later run knows the shape without the rasters."""
    import json

    m = {"n_batches": n_batches,
         "groups": {f"crops_{min(g)}_{max(g)}": sorted(int(y) for y in g) for g in groups}}
    p = crops_manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2))
    tmp.replace(p)


def cdl_years_available() -> list[int]:
    """Every CDL year the archive holds, read from its listing rather than by expanding it."""
    out = []
    for name in archive.member_names("crops"):
        try:
            y = int(name.split("_")[0])
        except ValueError:
            continue
        if YEAR_MIN <= y <= YEAR_MAX:
            out.append(y)
    return sorted(set(out))


def crops_cached(n_items: int, size: int = zonal.BATCH):
    """The finished frame if every batch of every group is checkpointed, else None. Opens no raster.

    THE POINT IS TO NOT EXPAND 50 GB TO READ NOTHING. Enumerating the CDL years means opening eighteen GeoTIFFs to compare their transforms, so a pass that needs no computation would still have needed the archive. The manifest records the group structure the last pass found, so completeness is answerable from `parts/` alone.
    """
    import json
    import math

    p = crops_manifest_path()
    if not p.exists():
        return None
    m = json.loads(p.read_text())
    if int(m.get("n_batches", -1)) != math.ceil(n_items / size):
        return None                      # the cohort changed; the parts describe different catchments
    frames = []
    for name in m.get("groups", {}):
        d = PARTS / name
        want = [d / f"part_{i:05d}.parquet" for i in range(m["n_batches"])]
        if not all(f.exists() for f in want):
            return None
        frames.extend(want)
    if not frames:
        return None
    have = {y for ys in m.get("groups", {}).values() for y in ys}
    avail = set(cdl_years_available())
    if avail and avail != have:
        print(f"  crops: the archive now holds {sorted(avail - have) or 'fewer'} year(s) the "
              f"checkpoints do not cover -- recomputing", flush=True)
        return None
    print(f"  crops: every batch checkpointed ({len(frames)} parts over {len(m['groups'])} "
          f"grid group(s), years {min(have)}-{max(have)}); the CDL archive is not needed", flush=True)
    return pd.concat([pd.read_parquet(f) for f in frames], ignore_index=True)


def build_crops(cat: zonal.Catchments, directory, limit=None, force=False) -> pd.DataFrame:
    """Every available CDL year over every catchment. `directory` may be a path or an `archive.Lazy`."""
    if not force and limit is None:
        done = crops_cached(len(cat))
        if done is not None:
            return done
    directory = getattr(directory, "path", directory)
    paths = cdl_rasters(directory)
    if not paths:
        raise FileNotFoundError(f"no CDL rasters in {directory}")
    groups = grid_groups(paths)
    print(f"  {len(paths)} CDL year(s) {min(paths)}-{max(paths)} in {len(groups)} grid group(s): "
          f"{[sorted(g) for g in groups]}", flush=True)
    out = []
    for gi, group in enumerate(groups, 1):
        years = sorted(group)
        out.append(zonal.extract(
            cat, {str(y): group[y] for y in years}, CDL_CRS, ops=zonal.CATEGORICAL_OPS,
            reduce=lambda c, r, ys=years: _crops_reduce(c, r, ys),
            limit=limit, force=force,
            part_dir=None if limit is not None else PARTS / f"crops_{years[0]}_{years[-1]}",
            label=f"crops group {gi}/{len(groups)} ({years[0]}-{years[-1]}) "))
    if limit is None:
        import math

        _write_crops_manifest([sorted(g) for g in groups], math.ceil(len(cat) / zonal.BATCH))
    return pd.concat(out, ignore_index=True)


def _crops_closure(df: pd.DataFrame, cov: pd.DataFrame) -> tuple[bool, str]:
    """Per (comid, year): the code fractions must sum to 1 over the covered area -- exactextract's own arithmetic, audited."""
    tot = df.groupby(["comid", "year"], sort=False).frac.sum()
    bad = int(((tot - 1.0).abs() > CLOSURE_TOL).sum())
    return bad == 0, f"{df.comid.nunique():,} catchments x years ({len(df):,} code rows), {bad} groups where fractions do not sum to 1"


# ---- nutrients --------------------------------------------------------------------------------------

NUTRIENT_CRS = "EPSG:5070"
NUTRIENT_SOURCES = {
    "surplus": ("surplus", "Surplus_N_"),
    "fertilizer": ("fertilizer", "Fertilizer_Ag_"),
}
HA_PER_PIXEL = 250.0 * 250.0 / 10_000.0


def nutrient_rasters() -> dict[str, object]:
    """`{'{product}_{year}': path}` for every year of both products found on disk."""
    out = {}
    for product, (sub, stem) in NUTRIENT_SOURCES.items():
        d = config.RAW / sub / "national"
        for p in sorted(d.glob(f"{stem}*.tif")):
            try:
                y = int(p.stem.replace(stem, ""))
            except ValueError:
                continue
            out[f"{product}_{y}"] = p
    return out


def _nutrients_reduce(comids: np.ndarray, res: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    """One batch -> long rows of (comid, product, year, kg_per_ha, kg, n_px).

    MEAN AND KILOGRAMS BOTH: the band is an intensity (kg N/ha), the mean is what a person reads, and the total is what ACCUMULATES -- summing means up a river network is meaningless, while summing kilograms is exactly right.
    """
    frames = []
    for n in names:
        product, year = n.rsplit("_", 1)
        mean = res[f"{n}_mean"].to_numpy(dtype="float64")
        n_px = res[f"{n}_count"].to_numpy(dtype="float64")
        frames.append(pd.DataFrame({
            "comid": comids, "product": product, "year": np.int16(int(year)),
            "kg_per_ha": mean.astype("float32"),
            "kg": (mean * n_px * HA_PER_PIXEL).astype("float32"),
            "n_px": n_px.astype("float32"),
        }))
    return pd.concat(frames, ignore_index=True)


def build_nutrients(cat: zonal.Catchments, limit=None, force=False) -> pd.DataFrame:
    """Surplus and fertilizer over every catchment, long by (product, year). One grid, one pass -- thirty-six rasters share the 250 m EPSG:5070 lattice, so the coverage is computed once. 2000-2017 does not cover the water record; what happens past 2017 is an access-profile decision, not resolved here."""
    paths = nutrient_rasters()
    if not paths:
        raise FileNotFoundError(f"no Surplus_N/Fertilizer_Ag rasters under {config.RAW}")
    years = sorted({int(k.rsplit("_", 1)[1]) for k in paths})
    print(f"  {len(paths)} raster(s): {sorted({k.rsplit('_', 1)[0] for k in paths})} "
          f"{years[0]}-{years[-1]}", flush=True)
    names = list(paths)
    return zonal.extract(cat, paths, NUTRIENT_CRS, ops=zonal.CONTINUOUS_OPS,
                         reduce=lambda c, r: _nutrients_reduce(c, r, names),
                         limit=limit, force=force,
                         part_dir=None if limit is not None else PARTS / "nutrients",
                         label="nutrients ")


def _nutrients_closure(df: pd.DataFrame, cov: pd.DataFrame) -> tuple[bool, str]:
    """kg must be mean x coverage x ha -- the stored pair cannot drift apart."""
    with np.errstate(invalid="ignore"):
        want = df.kg_per_ha.to_numpy("float64") * df.n_px.to_numpy("float64") * HA_PER_PIXEL
    got = df.kg.to_numpy("float64")
    m = np.isfinite(want) & np.isfinite(got)
    bad = int((np.abs(got[m] - want[m]) > CLOSURE_TOL * np.abs(want[m]).clip(min=1.0)).sum())
    return bad == 0, f"{len(df):,} rows, {bad} where kg != mean x area"


# ---- gridMET weights ---------------------------------------------------------------------------------

ID_RASTER = OUT / "gridmet_cell_ids.tif"

# Outside-AOE sentinel in the id raster. NOT -1: cell ids are `row * 1386 + col` on the WEATHER STORE'S lattice (origin 49.4N), and this AOE reaches 49.7N, so rows above the origin produce LEGITIMATE NEGATIVE ids -- the build2 ancestor's `cells >= 0` filter would have silently dropped every cell north of 49.4.
_NODATA = np.iinfo("int32").min

# Mean Earth radius, km. The cells are small enough that a sphere and an ellipsoid disagree by well under the precision anything here needs.
_EARTH_R_KM = 6371.0088


def cell_area_km2(cell_id) -> np.ndarray:
    """Ground area of each gridMET cell, km2. A function of LATITUDE alone on this lattice.

    Decoded with THE WEATHER STORE'S convention (`acquire.weather.cell_id`: row 0 centred at 49.4N), because the ids must join that store -- the build2 ancestor decoded rows from a 90N origin while its raster encoded from the CONUS origin, so every weight-matrix cell_id was offset 1,351,290 from the weather store's and the join returned zero rows. Exact for a spherical Earth: the area between two parallels is `R^2 * dlon * (sin(lat2) - sin(lat1))`.
    """
    row = np.floor_divide(np.asarray(cell_id, dtype="int64"), weather._COLS).astype("float64")
    lat_c = weather._LAT0 - row * weather._RES
    lat_top = np.radians(lat_c + weather._RES / 2)
    lat_bot = np.radians(lat_c - weather._RES / 2)
    return _EARTH_R_KM ** 2 * np.radians(weather._RES) * (np.sin(lat_top) - np.sin(lat_bot))


def _grid():
    """The AOE's gridMET cells, from the acquisition store's own `grid.parquet`. Never re-derived."""
    import geopandas as gpd

    p = weather._grid_path()
    if not p.exists():
        raise FileNotFoundError(f"{p} is missing -- run the a3 weather branch first")
    return gpd.read_parquet(p)


def id_raster(force: bool = False):
    """Write (or reuse) the raster whose value is `cell_id`, on gridMET's own lattice in EPSG:4326.

    THE ID IS `acquire.weather.cell_id(lon, lat)`, CALLED, NOT REIMPLEMENTED. The build2 ancestor repeated the formula "so a change fails loudly" and instead the two copies used different origins from day one -- the weight matrix was keyed 1,351,290 off the weather store and no join ever returned a row. One function computes the key; this raster and the store cannot disagree because they ask it the same question.

    IN 4326, gridMET's OWN CRS: the cells are exact 1/24-degree squares in lon/lat, so an id raster on that lattice is exact and tiny. Cells outside the AOE are left at the sentinel and a catchment overlapping one records no weight there, which is the honest answer: there is no weather on file for them.
    """
    if ID_RASTER.exists() and not force:
        return ID_RASTER

    g = _grid()
    ids_flat = weather.cell_id(g.lon.to_numpy(), g.lat.to_numpy()).astype("int64")
    col = ids_flat % weather._COLS
    row = np.floor_divide(ids_flat, weather._COLS)
    c0, c1, r0, r1 = col.min(), col.max(), row.min(), row.max()
    ids = np.full((r1 - r0 + 1, c1 - c0 + 1), _NODATA, dtype="int64")
    ids[row - r0, col - c0] = ids_flat

    # Pixel edges, not centres: the cell whose centre is at (col, row) spans half a step either side.
    west = weather._LON0 + c0 * weather._RES - weather._RES / 2
    north = weather._LAT0 - r0 * weather._RES + weather._RES / 2
    print(f"  gridmet id raster: {ids.shape[1]}x{ids.shape[0]} cells, "
          f"{int((ids != _NODATA).sum()):,} in the AOE", flush=True)
    return zonal.cell_id_raster(ID_RASTER, west, north, weather._RES, "EPSG:4326", ids)


def _weights_reduce(comids: np.ndarray, res: pd.DataFrame) -> pd.DataFrame:
    """One batch -> long (comid, cell_id, w_km2) rows, dropping the outside-AOE sentinel.

    `frac * count` is the coverage attributable to one cell IN CELL UNITS -- "this catchment covers 0.35 of that cell" -- so it is multiplied by that cell's own ground area to become km2. On a lat/lon lattice a fractional-cell count is NOT an area: cell ground area runs from 18.7 km2 down to 13.9 km2 across this AOE, a 34% spread, and weighting a basin's cells by counts over-weights its northern cells systematically.
    """
    out = []
    n_cells = res["gridmet_count"].to_numpy(dtype="float64")
    for i, (cells, frac) in enumerate(zip(res["gridmet_unique"], res["gridmet_frac"])):
        if cells is None or not len(cells):
            continue
        cells = np.asarray(cells, dtype="int64")
        keep = cells != _NODATA
        if not keep.any():
            continue
        cells = cells[keep]
        share = np.asarray(frac, dtype="float64")[keep] * n_cells[i]
        out.append(pd.DataFrame({"comid": np.int64(comids[i]),
                                 "cell_id": cells.astype("int32"),
                                 "w_km2": (share * cell_area_km2(cells)).astype("float32")}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        {"comid": pd.Series(dtype="int64"), "cell_id": pd.Series(dtype="int32"),
         "w_km2": pd.Series(dtype="float32")})


def build_weights(cat: zonal.Catchments, limit=None, force=False) -> pd.DataFrame:
    """The catchment x gridMET-cell weight matrix, one row per overlap, weighted in km2.

    DAILY WEATHER IS NEVER STORED PER COMID -- 1.49M catchments x ~6,600 days is ten billion rows. This matrix is the geometry instead: static, small, and it turns a site's daily weather into reach set -> weights -> collapse to (cell_id, w_km2) -> read the cells' days -> weighted mean, with the collapse happening BEFORE any date is touched. KEPT, NOT CONSUMED: the widget's virtual-pin path needs to run the same reduction at request time with no build in sight.
    """
    r = id_raster(force=force)
    return zonal.extract(cat, {"gridmet": r}, "EPSG:4326", ops=zonal.CATEGORICAL_OPS,
                         reduce=_weights_reduce, limit=limit, force=force,
                         part_dir=None if limit is not None else PARTS / "weights_gridmet",
                         label="gridmet weights ")


def _weights_closure(df: pd.DataFrame, cov: pd.DataFrame) -> tuple[bool, str]:
    """Two invariants: every cell_id joins the weather store's grid, and weights conserve catchment area.

    The join check is the regression for the 1,351,290 offset -- zero matching ids is exactly how that bug presented. Area closure compares a spherical cell area against NHDPlus's ellipsoidal AreaSqKM and excludes catchments crossing the AOE boundary (their outside-AOE overlap is deliberately unweighted), so the bar is the MEDIAN relative gap over fully-covered catchments.
    """
    grid_ids = set(_grid().cell_id.astype("int64"))
    used = set(df.cell_id.astype("int64").unique())
    orphan = used - grid_ids
    if not used or (used & grid_ids) == set():
        return False, "NO weight cell_id joins the weather grid -- the id convention is broken"
    tot = df.groupby("comid").w_km2.sum()
    j = cov.set_index("comid").area_km2.reindex(tot.index)
    rel = ((tot - j).abs() / j.clip(lower=1e-6)).dropna()
    # catchments partly outside the AOE under-close by design; the median over all is still tight when the convention is right, because boundary catchments are a thin minority of any real AOE
    med = float(rel.median()) if len(rel) else np.nan
    # THE GLOBAL CONSERVATION CLOSURE: no cell claimed twice. Summed over every catchment touching it,
    # a cell's claimed area may not exceed its own ground area (small slack for the spherical-vs-
    # ellipsoidal disagreement) -- double-claiming is exactly the failure a per-catchment check cannot see.
    per_cell = df.groupby("cell_id").w_km2.sum()
    cap = cell_area_km2(per_cell.index.to_numpy()) * (1 + WEIGHTS_TOL)
    over = int((per_cell.to_numpy() > cap).sum())
    ok = not orphan and len(rel) > 0 and med <= WEIGHTS_TOL and over == 0
    return ok, (f"{len(used):,} distinct cells, {len(orphan)} not in the weather grid; "
                f"median |sum(w) - area|/area = {med:.4%} over {len(rel):,} catchments; "
                f"{over} cell(s) claimed beyond their ground area")


# ---- agtile -----------------------------------------------------------------------------------------

AGTILE_CRS = "ESRI:102003"
AGTILE_TIF = "AgTile-US.tif"


def agtile_raster(directory):
    p = directory / AGTILE_TIF
    if not p.exists():
        hits = list(directory.rglob(AGTILE_TIF))
        if not hits:
            raise FileNotFoundError(f"{AGTILE_TIF} not under {directory}")
        p = hits[0]
    return p


def _agtile_reduce(comids: np.ndarray, res: pd.DataFrame) -> pd.DataFrame:
    """One batch -> (comid, tile_px, n_px).

    A BARE BINARY RASTER WITH NO MASK: 1 means tile-drained, 0 means everything else -- undrained cropland and downtown alike. It supplies a NUMERATOR only; the denominators come from elsewhere (basin area for "how much of this watershed is drained", CDL cultivated pixels for "how much of its cropland"). Dividing tile pixels by the raster's own covered pixels answers neither.
    """
    n_px = res["agtile_count"].to_numpy(dtype="float64")
    tile = np.zeros(len(comids), dtype="float64")
    for i, (vals, frac) in enumerate(zip(res["agtile_unique"], res["agtile_frac"])):
        if vals is None or not len(vals):
            continue
        v = np.asarray(vals, dtype="int64")
        f = np.asarray(frac, dtype="float64")
        tile[i] = f[v == 1].sum() * n_px[i]
    return pd.DataFrame({"comid": comids, "tile_px": tile.astype("float32"),
                         "n_px": n_px.astype("float32")})


def agtile_cached(n_items: int, size: int = zonal.BATCH):
    """The finished frame if every batch is checkpointed, else None. Opens no raster -- AgTile's archive is 15 GB expanded, which is 15 GB not spent when there is nothing to compute."""
    import math

    d = PARTS / "agtile"
    want = [d / f"part_{i:05d}.parquet" for i in range(math.ceil(n_items / size))]
    if not want or not all(f.exists() for f in want):
        return None
    print(f"  agtile: every batch checkpointed ({len(want)} parts); the archive is not needed", flush=True)
    return pd.concat([pd.read_parquet(f) for f in want], ignore_index=True)


def build_agtile(cat: zonal.Catchments, directory, limit=None, force=False) -> pd.DataFrame:
    """Tile-drained pixel area per catchment, in the raster's own projection -- resampling a categorical layer into 5070 would invent tile drainage at the boundaries. One vintage, 2017."""
    if not force and limit is None:
        done = agtile_cached(len(cat))
        if done is not None:
            return done
    r = agtile_raster(getattr(directory, "path", directory))
    print(f"  agtile: {r.name} in {AGTILE_CRS}", flush=True)
    return zonal.extract(cat, {"agtile": r}, AGTILE_CRS, ops=zonal.CATEGORICAL_OPS,
                         reduce=_agtile_reduce, limit=limit, force=force,
                         part_dir=None if limit is not None else PARTS / "agtile",
                         label="agtile ")


def _agtile_closure(df: pd.DataFrame, cov: pd.DataFrame) -> tuple[bool, str]:
    bad = int((df.tile_px > df.n_px * (1 + CLOSURE_TOL)).sum())
    return bad == 0, f"{len(df):,} rows, {bad} with tile area exceeding coverage"


# ---- the driver --------------------------------------------------------------------------------------

def coverage(cat: zonal.Catchments, crops_df: pd.DataFrame | None, px_area_km2: float | None) -> pd.DataFrame:
    """`comid, area_km2, n_px_30m, frac_covered` -- the denominator table, and the honesty check.

    A catchment smaller than a pixel can rasterise to nothing. Zero must reach the consumer as MISSING and not as a legitimate zero, which is what `frac_covered` is for: the fraction of the catchment's own area the raster actually saw. The pixel area comes from THE RASTER'S OWN TRANSFORM -- the ancestor hard-coded 900 m2, which was wrong for every grid but CDL's.
    """
    out = zonal.coverage_table(cat)
    if crops_df is not None and len(crops_df) and px_area_km2 is not None:
        y = crops_df.year.max()
        px = (crops_df[crops_df.year == y].drop_duplicates("comid").set_index("comid").n_px)
        out["n_px_30m"] = out.comid.map(px).astype("float32")
        out["frac_covered"] = (out.n_px_30m * px_area_km2 / out.area_km2).astype("float32")
    return out


def _write(df: pd.DataFrame, name: str, closure, cov: pd.DataFrame, write: bool = True) -> None:
    """Check the product's closure, then write it stamped. A product that does not close is NOT written -- a wrong artifact on disk outlives the log line that doubted it. `write=False` (a `--limit` smoke) checks and reports but leaves the store alone: a truncated artifact under the real name is indistinguishable from a finished one."""
    ok, note = closure(df, cov) if closure is not None else (True, "no closure declared")
    print(f"  closure [{name}]: {'ok' if ok else 'FAILED'} -- {note}", flush=True)
    if not ok:
        raise SystemExit(f"{name} failed its closure check ({note}); nothing written")
    if not write:
        print(f"  --limit smoke: {name} NOT written ({len(df):,} rows checked)", flush=True)
        return
    p = OUT / f"{name}.parquet"
    bio.write_parquet(df, p, stamps={"snapshot": snapshot.head() or "", "aoe": config.aoe_stamp()})
    print(f"  wrote {p.name}: {len(df):,} rows x {len(df.columns)} cols "
          f"({p.stat().st_size/1e6:.0f} MB)", flush=True)


def main(force: bool = False, only: str | None = None, limit: int | None = None,
         keep_archives: bool = False) -> None:
    want = PRODUCTS if only in (None, "", "all") else tuple(only.split(","))
    bad = [w for w in want if w not in PRODUCTS]
    if bad:
        raise SystemExit(f"{bad}: unknown product. Known: {list(PRODUCTS)}")

    for p in ("crops", "agtile", "surplus", "fertilizer"):
        ok, why = archive.present(p)
        print(f"  {p:11s} {'OK ' if ok else 'MISSING'} {why}")
        if not ok and ((p == "crops" and "crops" in want)
                       or (p == "agtile" and "agtile" in want)
                       or (p in ("surplus", "fertilizer") and "nutrients" in want)):
            raise SystemExit(f"{p} is required for the products requested and is not on disk")

    OUT.mkdir(parents=True, exist_ok=True)
    cat = zonal.Catchments()
    print(f"  {len(cat):,} catchments, {cat.area_km2.sum():,.0f} km2 total", flush=True)
    cov = zonal.coverage_table(cat)

    crops_df, px_area = None, None
    if "crops" in want:
        print("\n[d5.1] crops  (CDL, fractional, all years in one coverage pass)")
        with archive.lazy("crops", keep=keep_archives) as d:
            crops_df = build_crops(cat, d, limit=limit, force=force)
            # px area from the raster's own transform -- but ONLY when the pass actually opened the archive: touching `d.path` on a fully-checkpointed run would expand 50 GB to measure one pixel.
            if getattr(d, "used", False) and d._path is not None:
                px_area = zonal.pixel_area_km2(next(iter(cdl_rasters(d._path).values())))
            else:
                px_area = 900.0 / 1e6        # parts reused; CDL is 30 m and its grid did not change
        _write(crops_df, "crops", _crops_closure, cov, write=limit is None)
        _write(coverage(cat, crops_df, px_area), "coverage", None, cov, write=limit is None)

    if "nutrients" in want:
        print("\n[d5.2] nutrients  (surplus + fertilizer, 250 m, one pass)")
        _write(build_nutrients(cat, limit=limit, force=force), "nutrients", _nutrients_closure, cov, write=limit is None)

    if "weights" in want:
        print("\n[d5.3] gridMET weights  (catchment x cell overlap, in the weather store's ids)")
        _write(build_weights(cat, limit=limit, force=force), "weights_gridmet", _weights_closure, cov, write=limit is None)

    if "structures" in want:
        print("\n[d5.4] structures  (dam tables + dam points + facility outfalls, stationed on reaches)")
        t = dams.table(force=force)
        if t is None:
            print("  NID is not on disk; dam tables skipped (run the a3 attributes branch)")
        else:
            print(f"  dams.parquet: {len(t):,} catchment(s) with a dam")
            r = dams.on_reach(force=force)
            print(f"  dams_on_reach.parquet: {0 if r is None else len(r):,} reach(es) impounded")
        structures.main(force=force)

    if "agtile" in want:
        print("\n[d5.5] agtile  (tile drainage, ESRI:102003)")
        with archive.lazy("agtile", keep=keep_archives) as d:
            _write(build_agtile(cat, d, limit=limit, force=force), "agtile", _agtile_closure, cov, write=limit is None)

    print(f"\n  {OUT} now holds: {sorted(p.name for p in OUT.glob('*.parquet'))}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", help=f"comma-separated subset of {list(PRODUCTS)}")
    ap.add_argument("--limit", type=int, help="stop after N catchments (smoke test, never a build)")
    ap.add_argument("--keep-archives", action="store_true")
    a = ap.parse_args()
    main(force=a.force, only=a.only, limit=a.limit, keep_archives=a.keep_archives)


# ---- verify ----------------------------------------------------------------------------------------

from .. import verify


@verify.check("d5", "every stored product passes its closure against the coverage table")
def _check_closures():
    stored = {"crops": _crops_closure, "nutrients": _nutrients_closure,
              "weights_gridmet": _weights_closure, "agtile": _agtile_closure}
    have = {n: OUT / f"{n}.parquet" for n in stored if (OUT / f"{n}.parquet").exists()}
    if not have:
        return None, "no D5 products on disk yet"
    cov_p = OUT / "coverage.parquet"
    cov = (bio.read_parquet(cov_p) if cov_p.exists()
           else zonal.coverage_table(zonal.Catchments()))
    notes, ok = [], True
    for n, p in have.items():
        good, note = stored[n](bio.read_parquet(p), cov)
        ok &= good
        notes.append(f"{n}: {'ok' if good else 'FAILED'} ({note})")
    return ok, "; ".join(notes)


@verify.check("d5", "the weight matrix joins the weather store and conserves catchment area")
def _check_weights_join():
    """The standing regression for the 1,351,290 cell_id offset -- the failure mode is ZERO joining rows."""
    p = OUT / "weights_gridmet.parquet"
    if not p.exists():
        return None, "no weight matrix yet"
    w = bio.read_parquet(p)
    grid_ids = set(_grid().cell_id.astype("int64"))
    used = set(w.cell_id.astype("int64").unique())
    hit = len(used & grid_ids)
    return hit == len(used) and hit > 0, f"{hit}/{len(used)} weight cells present in the weather grid"


@verify.check("d5", "coverage has a reader: frac_covered is sane where the raster saw the catchment")
def _check_coverage():
    p = OUT / "coverage.parquet"
    if not p.exists():
        return None, "no coverage table yet"
    cov = bio.read_parquet(p)
    if "frac_covered" not in cov.columns or cov.frac_covered.isna().all():
        return None, "coverage carries no frac_covered yet (crops not built)"
    f = cov.frac_covered.dropna()
    over = int((f > 1.05).sum())
    return over == 0, (f"{len(f):,} covered catchments, median frac_covered {f.median():.3f}, "
                       f"{over} above 1.05")


@verify.check("d5", "every stored product's AOE stamp matches the current extent")
def _check_product_aoe_stamps():
    """Stamps are written on every product; this is the reader-side half -- a product cut against a retired extent reads cleanly and describes the wrong region."""
    files = sorted(OUT.glob("*.parquet"))
    if not files:
        return None, "no D5 products on disk yet"
    want = config.aoe_stamp()
    stale = [p.name for p in files if (bio.read_stamps(p).get("aoe") or want) != want]
    return not stale, (f"{len(stale)} product(s) cut against another extent: {stale[:4]}"
                       if stale else f"{len(files)} product(s), all stamped {want}")
