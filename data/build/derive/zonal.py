"""Fractional zonal statistics over the catchment layer -- the engine every raster product runs on.

A PIXEL CONTRIBUTES TO EVERY CATCHMENT IT TOUCHES, in proportion to the area it lends each one. It does not pick a winner. A 30 m corn pixel split 0.55/0.30/0.15 across three catchments adds exactly that to each one's corn total, and the three fractions sum to 1. So every "count" here is an AREA IN PIXEL UNITS and therefore a float: `corn_px = 1834.62` means 1834.62 pixels' worth of corn.

WHY `exactextract` AND NOT A ZONE RASTER. The obvious implementation burns each catchment's id into an int32 raster and bincounts -- one integer owner per pixel, which is the whole-pixel rule this module exists to avoid. Measured against the catchment layer's own `AreaSqKM` over 7,049 catchments in an Iowa window:

    whole-pixel rasterize     median error 0.10%, p90 0.94%    0.3 s / 4096^2 window
    3x supersampling          median error 0.01%, p90 0.11%    0.6 s + 1.3 s counting
    exactextract              median error 0.00%, p90 0.00%    0.48 ms per catchment

Exact, and faster than either -- about 12 minutes for all 1.49M catchments against one raster.

RASTERS SHARING A GRID ARE PASSED TOGETHER, because the coverage fractions are a property of geometry alone and the library computes them once for the whole group. Measured: one raster 0.48 ms/catchment, three rasters 0.64 ms -- a marginal 0.08 ms rather than another 0.48. That is why crops hands over all its CDL years in a single call instead of looping years, and it is the single biggest cost decision in D5. `extract` VERIFIES the shared grid rather than trusting the caller -- the build2 ancestor stated the rule in prose only, and a mixed group silently pays for a second coverage computation per group.

BATCHES ARE SPATIALLY ORDERED. Features are walked in Morton (Z-order) sequence of their bounding-box centres, so consecutive catchments read overlapping raster windows and the block cache does its job. NHDPlus's own row order is not spatial.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config

BATCH = 20_000

# Ops requested per raster. `count` is total coverage in cell units, `unique` the distinct values present and `frac` their share of that coverage -- so `frac * count` is the covered area per value, in pixel units.
CATEGORICAL_OPS = ("count", "unique", "frac")
CONTINUOUS_OPS = ("count", "mean")


def _morton(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Z-order key for scaled integer coordinates. Interleaved bits, so nearby points get nearby keys."""
    def spread(v):
        v = v.astype("uint64") & 0x1FFFFF
        v = (v | (v << 32)) & 0x1F00000000FFFF
        v = (v | (v << 16)) & 0x1F0000FF0000FF
        v = (v | (v << 8)) & 0x100F00F00F00F00F
        v = (v | (v << 4)) & 0x10C30C30C30C30C3
        v = (v | (v << 2)) & 0x1249249249249249
        return v
    return spread(x) | (spread(y) << 1)


def grid_of(path) -> tuple:
    """One raster's grid identity: (width, height, rounded transform, crs). Equal grids share coverage."""
    import rasterio

    with rasterio.open(path) as d:
        return (d.width, d.height, tuple(np.round(np.asarray(d.transform)[:6], 3)), str(d.crs))


def pixel_area_km2(path) -> float:
    """One pixel's area in km2, from the raster's own transform. NOT hard-coded per product -- the 900 m2 CDL constant leaked into a coverage figure once and was wrong for every other grid. Projected grids only; a degree-lattice raster has no single pixel area and its product must convert per cell."""
    import rasterio

    with rasterio.open(path) as d:
        return abs(d.transform.a * d.transform.e) / 1e6


class Catchments:
    """The catchment layer, held as WKB, handed out in spatially ordered batches.

    Geometry is stored as bytes (~2.6 GB for 1.49M catchments) and materialised one batch at a time -- as shapely objects the layer is roughly 10 GB, which does not fit alongside a raster read.
    """

    def __init__(self, path=None):
        import pyarrow.parquet as pq
        import shapely

        from ..acquire import network

        self.n_repaired = 0
        self._src = path or network._path("catchments")
        t = pq.read_table(self._src, columns=["FEATUREID", "AreaSqKM", "geometry"])
        self.comid = t.column("FEATUREID").to_numpy().astype("int64")
        self.area_km2 = t.column("AreaSqKM").to_numpy()
        self._wkb = t.column("geometry").combine_chunks()

        b = np.vstack([shapely.bounds(shapely.from_wkb(
            np.asarray(self._wkb.slice(i, 200_000).to_pylist(), dtype=object)))
            for i in range(0, len(self.comid), 200_000)])
        self.bounds = b
        cx, cy = (b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2
        # ~11 m resolution in degrees, which is finer than any catchment and cheap to compute.
        self.order = np.argsort(_morton(((cx + 180) * 1e4).astype("int64"),
                                        ((cy + 90) * 1e4).astype("int64")), kind="stable")

    def __len__(self):
        return len(self.comid)

    def release(self) -> None:
        """Drop the WKB, reloading it on the next `batches` call.

        The geometry is ~2.6 GB and is dead weight once a product's raster work is finished, but the D5 driver builds ONE instance and hands it to every product in turn -- so this reloads rather than invalidating, and a caller that releases between products pays one re-read instead of holding the layer through a concatenation that needs the room.
        """
        self._wkb = None

    def _blobs(self):
        import pyarrow.parquet as pq

        if self._wkb is None:
            self._wkb = pq.read_table(self._src, columns=["geometry"]).column(
                "geometry").combine_chunks()
        return self._wkb

    def batches(self, crs: str, size: int = BATCH, limit: int | None = None, skip=None):
        """Yield (comid array, GeoDataFrame in `crs`) in spatial order.

        The frame carries only `comid` and geometry: exactextract copies whatever else it is given into every output row, and nothing else is needed to join the result back.

        `skip` is a predicate on the batch index. A skipped batch yields its comids with `None` for the geometry and never materialises it -- resuming a checkpointed pass would otherwise decode and reproject every batch it is about to discard, which on a 41-batch resume is 820,000 polygons built only to be dropped, at the moment memory is scarcest.
        """
        import geopandas as gpd
        import shapely

        idx = self.order if limit is None else self.order[:limit]
        for i in range(0, len(idx), size):
            take = idx[i:i + size]
            if skip is not None and skip(i // size):
                yield self.comid[take], None
                continue
            g = shapely.from_wkb(np.asarray(self._blobs().take(take).to_pylist(), dtype=object))
            # INVALID GEOMETRY SEGFAULTS exactextract, so it is repaired here rather than passed on. NHDPlus ships 6,815 invalid catchments, 0.459% of the layer, and one of them killed a nutrients pass three times -- a native crash with no Python traceback, which is why it read as a memory problem. `make_valid` returns only Polygons and MultiPolygons for this layer and changes area by at most 1.7e-15 deg2, so the defect is ring order and self-touching rings rather than anything real, and repairing it costs nothing measurable.
            #
            # Checked per batch rather than once for the layer because `is_valid` is a cheap C loop over 20,000 features and doing it here keeps the repair beside the only consumer that can be hurt by skipping it.
            bad = ~shapely.is_valid(g)
            if bad.any():
                g[bad] = shapely.make_valid(g[bad])
                self.n_repaired += int(bad.sum())
            gdf = gpd.GeoDataFrame({"comid": self.comid[take]}, geometry=g, crs=4326)
            yield self.comid[take], (gdf if str(crs).upper() in ("EPSG:4326", "4326") else gdf.to_crs(crs))


def _adopt_or_clear(part_dir, inputs: str | None, label: str = "") -> None:
    """Drop a checkpoint directory whose parts were computed from DIFFERENT inputs.

    `expect_values` guards axes that appear as values in the output -- product, year. It cannot see an input that changed without changing the output's vocabulary: a re-pulled agtile raster, a regenerated cell-id raster, a CDL year rewritten in place. Those are the same blind spot the nutrients checkpoints had, one level up, so they are keyed the way `d8._accum_cache_file` keys its cache -- on the identity of what was read.

    A MISSING stamp is ADOPTED, not treated as a mismatch: the parts predating this guard are the output of a four-hour pass and there is no evidence against them. Only a stamp that disagrees clears the directory, which is the same coverage-not-equality reasoning the network layers use.
    """
    if inputs is None:
        return
    p = part_dir / ".inputs"
    if p.exists():
        if p.read_text().strip() == inputs:
            return
        stale = sorted(part_dir.glob("part_*.parquet"))
        for q in stale:
            q.unlink(missing_ok=True)
        if stale:
            print(f"  {label}inputs changed -- discarded {len(stale)} checkpoint part(s)", flush=True)
    p.write_text(inputs + "\n")


def _raster_groups(paths) -> list[list]:
    """Split `paths` into groups whose combined working set fits GDAL's block cache. One group means no split.

    A feature-sequential pass visits every raster for each feature, so the cache must hold one working set PER RASTER at once. The nutrients pass broke on exactly this: 216 gTREND years tiled 1168x1856 float32 -- 8.7 MB a block -- against a default cache of 5% of RAM, which is 99 blocks for 216 rasters. Every catchment then re-decompressed what the previous one evicted; a profile put 99% of samples inside `gdal_LZWDecode` and the pass ran at ~0.8 s per catchment, projecting nine days over 1.49M.

    The mismatch is in the source tiling, not in the request: one of those blocks covers 292 x 464 km where the median catchment is 1.38 km2, so a single catchment read decompresses ~135,000 km2. Nothing can make that read cheap -- but it need only happen once per block, which it does as soon as the working set fits.

    Grouping is preferred to `strategy="raster-sequential"`, which also fixes the cache but recomputes each feature's coverage fraction once per raster: measured over 12 rasters it was 7x SLOWER (2.84 s against 0.40 s) for bit-identical values. Groups keep the cheap strategy and simply ask for less at a time.

    Half the cache is the budget, and four blocks per raster the estimate, since a window can straddle a 2x2 block corner and the reduce still needs room. Small-tiled or few-raster sources (CDL year groups, the single-raster agtile and weights passes) come back as one group and are unaffected.
    """
    import numpy as np
    import rasterio
    from rasterio.env import get_gdal_config

    paths = list(paths)
    try:
        with rasterio.open(paths[0]) as src:
            bh, bw = src.block_shapes[0]
            block_bytes = bh * bw * np.dtype(src.dtypes[0]).itemsize
        cache = int(get_gdal_config("GDAL_CACHEMAX") or 0) or 64 << 20
    except Exception as exc:                           # noqa: BLE001 -- unprobeable
        # LOUDLY, because the fallback IS the pathological path: one group is what ran the nutrients pass
        # at nine days, and a silent degrade would restore it with nothing printed to say so.
        print(f"  WARNING: cannot size raster groups ({type(exc).__name__}: {exc}) -- "
              f"running all {len(paths)} rasters at once", flush=True)
        return [paths]
    per_group = max(1, int(cache / 2 // (4 * block_bytes)))
    if per_group >= len(paths):
        return [paths]
    return [paths[i:i + per_group] for i in range(0, len(paths), per_group)]


def extract(cat: Catchments, rasters: dict, crs: str, ops=CATEGORICAL_OPS,
            reduce=None, size: int = BATCH, limit: int | None = None,
            label: str = "", part_dir=None, force: bool = False, expect_values=(),
            inputs: str | None = None) -> pd.DataFrame:
    """Run `rasters` over every catchment and return the concatenated per-batch results.

    Parameters
    ----------
    cat : Catchments
    rasters : dict
        `{name: path}`. All of them MUST share one grid -- checked here, not trusted: the coverage fractions are only reusable inside a grid, and a mixed group silently pays a second coverage computation per raster.
    crs : str
        The rasters' CRS. Catchments are reprojected into it; the raster is never reprojected.
    ops : Sequence[str], default `CATEGORICAL_OPS`
    reduce : callable, optional
        `(comids, batch_frame) -> DataFrame`, applied per batch. Without it the raw exactextract frame is kept, which for `frac`/`unique` means arrays in cells and a large object frame.
    size : int, default 20000
    limit : int, optional
        Stop after this many catchments; for smoke tests, never for a build.
    label : str
        Progress prefix.
    part_dir : Path, optional
        Checkpoint directory. Each batch is written as `part_{i}.parquet` and an existing part is reused, so an interrupted pass resumes at the batch it died on rather than at the start. Batch boundaries are a pure function of the spatial ordering and `size`, so a part always describes the same catchments.
    force : bool, default False
        Recompute parts that already exist.

    Returns
    -------
    DataFrame
        Concatenated reduced batches.
    """
    import time

    from exactextract import exact_extract

    names = list(rasters)
    grids = {n: grid_of(rasters[n]) for n in names}
    if len(set(grids.values())) > 1:
        raise ValueError(f"rasters in one extract() group must share a grid; got "
                         f"{ {n: g[:2] for n, g in grids.items()} }")
    paths = [str(rasters[n]) for n in names]
    n_total = len(cat) if limit is None else min(limit, len(cat))
    if part_dir is not None:
        part_dir.mkdir(parents=True, exist_ok=True)
        _adopt_or_clear(part_dir, inputs, label)

    def _part(i):
        return None if part_dir is None else part_dir / f"part_{i:05d}.parquet"

    # RESULTS ARE NOT HELD IN MEMORY WHEN THEY ARE ON DISK. Accumulating every batch and concatenating at the end costs twice the output: nutrients is 1.49M catchments x 36 product-years = 53.5M rows, so the list holds ~1.6 GB while exactextract is still allocating against it, and the final concat needs another 1.6 GB beside it. On a 16 GB machine already holding 2.6 GB of catchment WKB that is what turns a long pass into a native allocation failure -- which surfaces as a segfault inside GDAL rather than as a MemoryError. Checkpointed runs therefore stream: the loop keeps nothing, and the parts are read back only once the raster work is finished and the geometry can be released.
    streaming = part_dir is not None
    done, t0, worked = 0, time.time(), 0
    out, written = [], []
    repaired0 = cat.n_repaired
    # A CHECKPOINT IS ONLY VALID FOR THE REQUEST THAT WROTE IT. Skipping on existence alone reuses a part
    # written for a NARROWER set of rasters, so widening the request silently produces the old output while
    # every batch reports "cached": the nutrients product was two components and became twelve, and without
    # this the ten new ones would never have been computed. `expect_values` names values the part must
    # carry -- checked from the parquet footer, no rows read.
    def _stale(p) -> bool:
        """True when a part cannot serve the CURRENT request. Every declared axis is checked, not one.

        Guarding a single axis is only half a guard: the nutrients product is keyed on (product, year), and checking products alone would let a component gaining three new years report every batch cached. Each pair is (column, required values); a part missing any value on any axis is stale.
        """
        if not expect_values:
            return False
        import pyarrow.parquet as pq

        for col, need in expect_values:
            try:
                have = set(pq.read_table(p, columns=[col]).column(col).to_pylist())
            except Exception:                              # noqa: BLE001 -- unreadable is stale
                return True
            if not set(need) <= have:
                return True
        return False

    def _usable(i) -> bool:
        p = _part(i)
        if p is None or not p.exists():
            return False
        if _stale(p):
            p.unlink(missing_ok=True)
            return False
        return True

    groups = _raster_groups(paths)
    if len(groups) > 1:
        print(f"  {label}{len(paths)} rasters exceed the block cache -- "
              f"{len(groups)} groups of <={len(groups[0])}", flush=True)
    skip = _usable if not force else None
    for i, (comids, gdf) in enumerate(cat.batches(crs, size=size, limit=limit, skip=skip)):
        done += len(comids)
        part = _part(i)
        if gdf is None:                       # checkpointed; geometry was never built
            written.append(part)
            continue
        res = pd.concat([exact_extract(g if len(g) > 1 else g[0], gdf, list(ops),
                                       output="pandas", strategy="feature-sequential")
                         for g in groups], axis=1) if len(groups) > 1 else exact_extract(
            paths if len(paths) > 1 else paths[0], gdf, list(ops),
            output="pandas", strategy="feature-sequential")
        res = _rename(res, rasters, ops)
        df = reduce(comids, res) if reduce is not None else res.assign(comid=comids)
        if part is not None:
            tmp = part.with_name(part.name + ".tmp")
            df.to_parquet(tmp, index=False)
            tmp.replace(part)
            written.append(part)
        if streaming:
            del res, df
        else:
            out.append(df)
        worked += len(comids)
        el = time.time() - t0
        print(f"    {label}{done:,}/{n_total:,}  {el/60:.1f} min elapsed, "
              f"{el/worked*(n_total-done)/60:.0f} min left", flush=True)

    n_rep = cat.n_repaired - repaired0
    if n_rep:
        # The repair is invisible otherwise, and 6,815 silently mended polygons is a fact about the INPUT layer worth a line -- a jump in this number means NHDPlus changed under us.
        print(f"    {label}repaired {n_rep:,} invalid catchment geometr"
              f"{'y' if n_rep == 1 else 'ies'} before extraction", flush=True)

    if not streaming:
        return pd.concat(out, ignore_index=True)
    cat.release()
    return pd.concat([pd.read_parquet(p) for p in written], ignore_index=True)


def _rename(res: pd.DataFrame, rasters: dict, ops) -> pd.DataFrame:
    """`Surplus_N_2000_mean` -> `surplus_2000_mean`, keyed on the FILE STEM.

    exactextract names its output from the raster's filename, not from anything we pass it, and the caller's key is rarely the filename (`surplus_2000` against `Surplus_N_2000`). Matching on the stem is the only mapping that is exact; substring matching looked fine on CDL, where the key happened to be a prefix of the stem, and failed the moment a product named its files differently. With a single raster exactextract drops the prefix and the column is bare `count`/`mean`.
    """
    from pathlib import Path

    names = list(rasters)
    if len(names) == 1:
        return res.rename(columns={op: f"{names[0]}_{op}" for op in ops})
    cols = {}
    for n in names:
        stem = Path(str(rasters[n])).stem
        for op in ops:
            cols[f"{stem}_{op}"] = f"{n}_{op}"
    missing = [c for c in cols if c not in res.columns]
    if missing:
        raise KeyError(f"exactextract returned no column for {missing[:6]}"
                       f"{' ...' if len(missing) > 6 else ''}; got {list(res.columns)[:6]} ...")
    return res.rename(columns=cols)


def cell_id_raster(path, west: float, north: float, res: float, crs: str, ids: np.ndarray):
    """Write a raster whose pixel VALUE is a grid cell's id, for use as the value layer of a weight matrix.

    `west`/`north` are the raster's upper-left CORNER, not a cell centre. The trick that makes a weight matrix free: running the zonal engine with cell ids as the values returns, per catchment, the ids it overlaps and the fraction of it in each -- which is the catchment x cell overlap area, measured the same way and by the same code as every other product here.
    """
    import rasterio
    from rasterio.transform import from_origin

    h, w = ids.shape
    tr = from_origin(west, north, res, res)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="int32", crs=crs, transform=tr, tiled=True, compress="lzw") as d:
        d.write(ids.astype("int32"), 1)
    return path


def coverage_table(cat: Catchments) -> pd.DataFrame:
    """`comid, area_km2` for every catchment -- the denominator table area closure is checked against."""
    return pd.DataFrame({"comid": cat.comid, "area_km2": cat.area_km2})
