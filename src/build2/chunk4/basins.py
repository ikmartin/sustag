"""Four delineations per gauge, cached, plus the auto rule that picks one.

    basin0  NLDI `nwissite/{id}/basin` -- the authority's own polygon. USGS registrations only.
    basin1  local upstream accumulation of catchments from the snapped `comid`. The default.
    basin2  the same accumulation from `catchment_comid` -- a cross-check, never auto-selected.
    basin3  D8 raster flood-fill over HydroSHEDS 15s. Independent of NHDPlus in code and in data.

A BASIN THAT IS NOT PHYSICAL IS NOT DELINEATED. Two kinds of gauge have no watershed to find, and neither gets a fabricated one: wells and groundwater sites, which sit below the surface, and gauges on `Coastline` reaches, which carry no upstream drainage. They keep their rows, with a null geometry and a stated `no_basin_reason`, and they are not put in front of a reviewer -- there is nothing to choose between four correctly empty methods.

THIS IS NOT COHORT FILTERING, which stays in chunk 6. Chunk 6 decides who is trainable; chunk 4 declines to invent an object. The difference matters because a fabricated basin is undetectable downstream: `IWQIS:WQS0029` is a water-supply well that snaps to the Des Moines River, and every method hands it the river's 14,288 km2 -- an ordinary area for an ordinary reach, wrong only in its premise.

THE AUTO RULE IS A PRECEDENCE, NOT A SCORE: basin0 where the authority has one, else basin1. basin2 and basin3 are unreachable from it by design and become preferred only by a recorded human decision. Flags never touch the selection -- they populate the review queue and nothing else.

CANDIDATES ARE CACHED PER GAUGE under `interim2/basins/{method}/`, because the pass is slow (the catchment dissolve is ~15 minutes for the cohort) and every method is a pure function of its inputs. `--force` refetches.

WHY THE DISSOLVE IS ONE PASS OVER THE LAYER. Catchments are 1.49M rows in TWO row groups, so a COMID pushdown still reads half the file -- a per-gauge read would move 400 MB 388 times. The layer is read once as WKB and each basin converts only its own slice. `shapely.coverage_union_all` is the natural tool and does not work: NHDPlus catchments are not correctly noded, and it raises on any batch tried, down to 5,000 polygons. Plain `union_all` is used instead, in batches, which bounds peak memory on the basins that touch a million reaches.

BASIN3 IS ONLY AVAILABLE OVER PART OF THE AOE. `flowdir_15s.tif` is clipped to -105..-87, 37..49 -- the old build's covariate box -- while the AOE runs to -113.9 and down to 29.5. A basin that reaches the raster edge is TRUNCATED, not small, so it is recorded as `edge_truncated` and given no area: reporting the truncated figure would fire `flag_d8_disagrees` on precisely the largest basins, where the cross-check is worth the most and the raster has the least to say.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config, schema
from . import accumulate

METHODS = ("basin0", "basin1", "basin2", "basin3")
AUTO_ORDER = ("basin0", "basin1")     # basin2/basin3 are cross-checks; only a human may choose them

CACHE = config.INTERIM / "basins"                  # {method}/{uid}.parquet -- the candidates
BASIN_DIR = config.PROCESSED / "basins"            # {gauge_uid}.parquet -- the selected basin

# Polygons per `union_all` call. The dissolve is linear in polygon count either way; batching only caps how many shapely objects exist at once, which is what keeps a 1.2M-reach basin inside the machine's memory.
_UNION_BATCH = 50_000

_NLDI_WORKERS = 8


def _cache_path(method: str, uid: str) -> Path:
    return CACHE / method / f"{schema.uid_to_filename(uid)}.parquet"


def read(method: str, uid: str):
    """The cached candidate as a one-row GeoDataFrame, or None. A missing file is a normal answer."""
    import geopandas as gpd

    p = _cache_path(method, uid)
    if not p.exists():
        return None
    g = gpd.read_parquet(p)
    return g if len(g) and g.geometry.iloc[0] is not None else None


def _write(method: str, uid: str, geom, comid=None, status: str = "ok"):
    """One candidate to cache. A failed method is still written, with a null geometry and its reason.

    Recording the failure is what makes the pass resumable: a site NLDI has no basin for must not be retried on every run, and `basin0_status` is the evidence the review panel shows instead of an empty column.
    """
    import geopandas as gpd

    p = _cache_path(method, uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = gpd.GeoDataFrame(
        {"site_uid": [uid], "method": [method], "comid": [comid], "status": [status],
         "area_km2": [config.equal_area_km2(geom) if geom is not None else np.nan]},
        geometry=[geom], crs=4326)
    tmp = p.with_name(p.name + ".tmp")
    df.to_parquet(tmp)
    tmp.replace(p)
    return df


class Catchments:
    """The NHDPlus catchment layer, read once, dissolved per basin on demand.

    Holds the layer as WKB rather than geometry: 1.49M catchments cost 2.6 GB as bytes and would cost roughly 10 GB as shapely objects, so only the COMIDs a basin actually needs are ever materialised.
    """

    def __init__(self):
        import pyarrow.parquet as pq

        from ..chunk1 import network

        t = pq.read_table(network._path("catchments"), columns=["FEATUREID", "geometry"])
        self._wkb = t.column("geometry").combine_chunks()
        ids = t.column("FEATUREID").to_numpy()
        self._row = pd.Series(np.arange(len(ids), dtype="int64"), index=ids)
        self._row = self._row[~self._row.index.duplicated()]

    def dissolve(self, comids) -> tuple[object, int]:
        """`(geometry, n_catchments)` for a COMID set, or `(None, 0)`.

        A COMID with no catchment is skipped rather than raised on. 27 reaches in the AOE genuinely carry none -- coastlines and a handful of divergences -- and a basin missing one of them is short by that reach's area, which `flag_area_vs_vaa` measures against NHDPlus's own accumulation.
        """
        import shapely

        rows = self._row.reindex(sorted({int(c) for c in comids})).dropna().astype("int64").to_numpy()
        if not len(rows):
            return None, 0
        parts = []
        for i in range(0, len(rows), _UNION_BATCH):
            chunk = rows[i:i + _UNION_BATCH]
            g = shapely.from_wkb(np.asarray(self._wkb.take(chunk).to_pylist(), dtype=object))
            parts.append(shapely.union_all(g))
        return (parts[0] if len(parts) == 1 else shapely.union_all(np.asarray(parts, dtype=object))), len(rows)


def _stamp_undelineable(gauges: pd.DataFrame, methods) -> set:
    """Record `subsurface` for every gauge whose type rules a basin out, and return their uids.

    OVERWRITES A CACHED GEOMETRY rather than merely skipping the work. A well delineated by an earlier pass has a real polygon on disk -- the river's -- and leaving it there would mean the guard changed the report while the artifact still said otherwise. The cache is the answer, so the cache is what gets corrected.
    """
    hit = set()
    for g in gauges.itertuples():
        why = undelineable(getattr(g, "site_type", None))
        if why is None:
            continue
        hit.add(g.site_uid)
        for m in methods:
            c = read(m, g.site_uid)
            p = _cache_path(m, g.site_uid)
            if c is not None or not p.exists():
                _write(m, g.site_uid, None, status=why)
    if hit:
        print(f"  {len(hit)} gauge(s) with no overland catchment ({', '.join(SUBSURFACE_TYPES)}): "
              f"recorded as `{NO_BASIN_SUBSURFACE}`, not delineated", flush=True)
    return hit


def build_local(gauges: pd.DataFrame, net: accumulate.Network, force: bool = False,
                methods=("basin1", "basin2")) -> pd.DataFrame:
    """basin1 and basin2 for every gauge, by accumulation then dissolve. Returns the per-method evidence.

    Both methods share one `Catchments` read, and the layer is only opened when something is actually missing from cache -- a resumed run with nothing to do pays nothing.
    """
    todo = [(g, m) for g in gauges.itertuples()
            for m in methods
            if (force or not _cache_path(m, g.site_uid).exists())
            and undelineable(getattr(g, "site_type", None)) is None]
    _stamp_undelineable(gauges, methods)
    print(f"  local accumulation: {len(gauges) * len(methods) - len(todo)} cached, {len(todo)} to build", flush=True)

    rows, cat = [], None
    for n, (g, method) in enumerate(todo, 1):
        cid = g.comid if method == "basin1" else g.catchment_comid
        reaches, acc_km2, totda, status = net.accumulate(cid)
        geom = None
        if status == "ok":
            if cat is None:
                print("    reading the catchment layer ...", flush=True)
                cat = Catchments()
            geom, n_cat = cat.dissolve(reaches)
            if geom is None:
                status = "no_catchments"
        _write(method, g.site_uid, geom, comid=(None if cid is None or pd.isna(cid) else int(cid)),
               status=status)
        rows.append({"site_uid": g.site_uid, "method": method, "status": status,
                     "acc_km2": acc_km2, "vaa_totda_km2": totda, "n_reaches": len(reaches)})
        if n % 25 == 0 or len(reaches) > 100_000:
            print(f"    {n}/{len(todo)} {g.site_uid} {method} {len(reaches):,} reaches", flush=True)
    return pd.DataFrame(rows)


def build_basin0(gauges: pd.DataFrame, force: bool = False, workers: int = _NLDI_WORKERS) -> pd.DataFrame:
    """The authority's delineation for every USGS gauge, from NLDI's `nwissite` index.

    A non-USGS gauge is not asked for and is cached as `not_usgs` so the run does not re-decide it every pass; a USGS gauge NLDI has no COMID for caches as `no_nldi_basin`, which is common enough to be unremarkable -- the Delaware at Chester and the Susquehanna at Columbia are both absent from that index.
    """
    from pynhd import NLDI

    skip = _stamp_undelineable(gauges, ("basin0",))
    todo = [g for g in gauges.itertuples()
            if (force or not _cache_path("basin0", g.site_uid).exists()) and g.site_uid not in skip]
    print(f"  basin0: {len(gauges) - len(todo)} cached or not delineable, {len(todo)} to fetch", flush=True)
    if not todo:
        return pd.DataFrame(columns=["site_uid", "status"])

    nldi = NLDI()

    def one(g):
        if str(g.source) != "USGS":
            _write("basin0", g.site_uid, None, status="not_usgs")
            return {"site_uid": g.site_uid, "status": "not_usgs"}
        try:
            geom = nldi.get_basins(str(g.source_site_id)).geometry.iloc[0]
        except Exception:
            _write("basin0", g.site_uid, None, status="no_nldi_basin")
            return {"site_uid": g.site_uid, "status": "no_nldi_basin"}
        _write("basin0", g.site_uid, geom, status="ok")
        return {"site_uid": g.site_uid, "status": "ok"}

    out = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for n, r in enumerate(pool.map(one, todo), 1):
            out.append(r)
            if n % 25 == 0:
                print(f"    {n}/{len(todo)}", flush=True)
    df = pd.DataFrame(out)
    print(f"  basin0 outcomes: {df.status.value_counts().to_dict()}")
    return df


def _flood(direction: np.ndarray, col: int, row: int) -> np.ndarray:
    """Cells draining to (col,row): breadth-first against the D8 flow direction. Same walk as `_make_basins._bfs`."""
    import collections

    from src.data import d8

    mask = np.zeros((d8.H, d8.W), dtype=np.uint8)
    mask[row, col] = 1
    q = collections.deque([(col, row)])
    while q:
        cx, cy = q.popleft()
        for dx, dy, expected in d8.NEIGHBOR_CHECKS:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < d8.W and 0 <= ny < d8.H and not mask[ny, nx] and direction[ny, nx] == expected:
                mask[ny, nx] = 1
                q.append((nx, ny))
    return mask


def _touches_edge(mask: np.ndarray) -> bool:
    return bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())


def build_basin3(gauges: pd.DataFrame, expected_km2: dict | None = None, force: bool = False) -> pd.DataFrame:
    """The D8 flood-fill for every gauge inside the raster's window. Returns the per-gauge outcome.

    `expected_km2` maps site_uid to its basin1 area and is what makes the outlet snap trustworthy: without an expected area `d8._snap_outlet` accepts any cell holding half the search window's maximum accumulation, which on this cohort put 28 of 116 sites on the wrong river -- always a small basin captured by a large one nearby. With it the candidate must land inside a band around the known area, and refusing to snap is preferred to snapping wrong.
    """
    from rasterio.features import shapes
    from shapely.geometry import shape
    from shapely.ops import unary_union

    from src.data import d8

    skip = _stamp_undelineable(gauges, ("basin3",))
    todo = [g for g in gauges.itertuples()
            if (force or not _cache_path("basin3", g.site_uid).exists()) and g.site_uid not in skip]
    print(f"  basin3: {len(gauges) - len(todo)} cached or not delineable, {len(todo)} to build", flush=True)
    if not todo:
        return pd.DataFrame(columns=["site_uid", "status"])

    direction = d8.load_direction_array()
    expected_km2 = expected_km2 or {}
    out = []
    for n, g in enumerate(todo, 1):
        status, geom = "ok", None
        col, row = d8.ll_to_image_pixel(float(g.lat), float(g.lon))
        if not (0 <= col < d8.W and 0 <= row < d8.H):
            status = "outside_raster"
        else:
            exp = expected_km2.get(g.site_uid)
            try:
                col, row = d8._snap_outlet(direction, col, row,
                                           None if exp is None or not np.isfinite(exp) else exp * 1e6)
            except ValueError:
                status = "no_compatible_outlet"
            if status == "ok":
                mask = _flood(direction, col, row)
                if mask.sum() == 0:
                    status = "empty"
                elif _touches_edge(mask):
                    status = "edge_truncated"
                else:
                    polys = [shape(s) for s, v in shapes(mask, mask=mask.astype(bool),
                                                         transform=d8.TRANSFORM) if v == 1]
                    geom = unary_union(polys) if polys else None
                    status = "ok" if geom is not None else "empty"
        _write("basin3", g.site_uid, geom, status=status)
        out.append({"site_uid": g.site_uid, "status": status})
        if n % 25 == 0:
            print(f"    {n}/{len(todo)}", flush=True)
    df = pd.DataFrame(out)
    print(f"  basin3 outcomes: {df.status.value_counts().to_dict()}")
    return df


def candidate_table(gauges: pd.DataFrame) -> pd.DataFrame:
    """Every cached candidate for every gauge, one row per gauge: `{method}_km2`, `{method}_status`, `{method}_comid`.

    Reads the caches rather than the builders' return values, so it reports what is ON DISK -- a resumed run and a fresh one produce the same table.
    """
    rows = []
    for g in gauges.itertuples():
        rec = {"site_uid": g.site_uid}
        for m in METHODS:
            c = read(m, g.site_uid)
            p = _cache_path(m, g.site_uid)
            if c is None and p.exists():
                import geopandas as gpd

                c = gpd.read_parquet(p)                 # a recorded failure: status but no geometry
            rec[f"{m}_km2"] = float(c.area_km2.iloc[0]) if c is not None and len(c) else np.nan
            rec[f"{m}_status"] = str(c.status.iloc[0]) if c is not None and len(c) else "missing"
        rows.append(rec)
    return pd.DataFrame(rows)


def _why_no_basin(row) -> str:
    """The reason a gauge ended up without one. Read from the candidates' own statuses, not re-derived.

    Order matters only in that `subsurface` is checked first: a well's methods are all stamped `subsurface`, so nothing else can be true of it, while `no_drainage_reach` is a property of the reach the site sits on.
    """
    if row.get("basin1_status") == NO_BASIN_SUBSURFACE:
        return NO_BASIN_SUBSURFACE
    if row.get("basin1_status") == "no_drainage_reach":
        return NO_BASIN_SURFACE
    return NO_BASIN_FAILED


def auto_choice(row) -> str | None:
    """The method the auto rule picks: basin0 if the authority has one, else basin1. None when neither exists."""
    for m in AUTO_ORDER:
        if row.get(f"{m}_status") == "ok" and pd.notna(row.get(f"{m}_km2")):
            return m
    return None


def method_of(row) -> str | None:
    """The selected method as a string, or None. NOT a bare truthiness test on the column.

    `basin_method` is null for a gauge no method could delineate, and pandas renders that null as `float('nan')` -- which is TRUTHY. `if row["basin_method"]` therefore passes NaN straight through to a path expecting a method name, which is how a gauge with no basin at all crashed the selection rather than being recorded as having none.
    """
    m = row.get("basin_method") if hasattr(row, "get") else getattr(row, "basin_method", None)
    return m if isinstance(m, str) and m else None


def geometry_for(uid: str, method):
    """The cached geometry for one gauge and method, or None. A null method is a normal answer."""
    if not isinstance(method, str) or not method:
        return None
    g = read(method, uid)
    return None if g is None else g.geometry.iloc[0]


def dist_to_basin_km(geom, lat: float, lon: float) -> float:
    """Distance from a site to its own basin, km; 0 inside. NaN when there is no basin to be outside of.

    A site outside the polygon delineated FROM it means the delineation belongs to somewhere else -- the check that catches a snap onto the far side of a divide, which every area comparison passes because the wrong basin is a perfectly ordinary size.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    if geom is None:
        return np.nan
    p = gpd.GeoSeries([Point(float(lon), float(lat))], crs=4326).to_crs(config.EQUAL_AREA).iloc[0]
    g = gpd.GeoSeries([geom], crs=4326).to_crs(config.EQUAL_AREA).iloc[0]
    return float(p.distance(g) / 1000.0)


def load_decisions() -> pd.DataFrame:
    """Human basin decisions, blank rows dropped. A blank `decision` means NOT YET REVIEWED, not an error.

    Lives here rather than in the review module because `select` has to apply them and the review module imports this one -- the other direction would be a cycle.
    """
    p = config.BASIN_DECISIONS
    if not p.exists():
        return pd.DataFrame(columns=["site_uid", "decision"])
    d = pd.read_csv(p, dtype=str).fillna("")
    val = d.decision.str.strip().str.lower()
    bad = set(val[val != ""]) - set(METHODS)
    if bad:
        raise ValueError(f"{p.name} has unrecognised methods {sorted(bad)}; expected one of {list(METHODS)}")
    out = d[val != ""].copy()
    out["decision"] = val[val != ""]
    return out[["site_uid", "decision"]].reset_index(drop=True)


# Why a gauge has no basin. The distinction is the whole point: two of these are ANSWERS and one is a FAILURE, and only the failure is worth a person's time.
NO_BASIN_SURFACE = "no_surface_drainage"   # the reach carries no upstream drainage -- a coastline, an estuary
NO_BASIN_SUBSURFACE = "subsurface"         # the site is below ground; there is no overland catchment
NO_BASIN_FAILED = "all_methods_failed"     # something that could have worked did not

# A basin here would be fiction, so none is attempted.
NOT_DELINEABLE = (NO_BASIN_SURFACE, NO_BASIN_SUBSURFACE)

# Types with no overland catchment. NARROWER THAN `site_type.NO_SURFACE_BASIN`, which also excludes lakes, ponds, estuaries and tidal streams -- those sit ON the surface and a watershed drains to them, so a basin is a real object even where chunk 6 declines to train on it. A well is not on the surface at all.
SUBSURFACE_TYPES = ("well", "groundwater")


def undelineable(site_type) -> str | None:
    """Reason this gauge cannot have a basin, from its TYPE alone -- or None.

    THE WELL AT BOONE IS THE CASE THIS EXISTS FOR. `IWQIS:WQS0029` is a water-supply well; it snaps to the Des Moines River like any riverside site and every method happily hands it the river's 14,288 km2. Nothing downstream can detect that, because the number is a perfectly ordinary basin area for a perfectly ordinary reach -- the error is in the premise, not the arithmetic.

    The estuaries are caught by the topology instead (`accumulate` refuses a `Coastline` reach), which is why there are two routes to the same conclusion and one shared vocabulary for it.
    """
    return NO_BASIN_SUBSURFACE if str(site_type) in SUBSURFACE_TYPES else None


def select(cand: pd.DataFrame) -> pd.DataFrame:
    """Add `basin_method`, `basin_km2`, `selection_mode` and `no_basin_reason` to the candidate table.

    A human decision wins outright, including one that picks a method with no geometry -- that is a statement about the delineation ("none of these is right"), and silently falling back to the auto rule would hide it. `selection_mode` is `manual`, `auto`, or `none`.

    A GAUGE WITH NO BASIN IS NOT NECESSARILY A GAUGE WITH A PROBLEM. Four estuaries snap to `Coastline` reaches and sixteen sites are wells or groundwater; in neither case does a watershed exist to be found, and delineating one would mean inventing a patch of sea or a catchment for a hole in the ground. `no_basin_reason` separates both from a delineation that merely failed, and the review queue only asks about the third kind.
    """
    d0 = load_decisions()
    dec = dict(zip(d0.site_uid, d0.decision))
    out = cand.copy()
    method, mode = [], []
    for r in out.to_dict("records"):
        d = dec.get(r["site_uid"])
        if d:
            method.append(d)
            mode.append("manual")
        else:
            a = auto_choice(r)
            method.append(a)
            mode.append("auto" if a else "none")
    # `string` dtype, so an unselected gauge carries pd.NA rather than a float NaN that reads as truthy.
    out["basin_method"] = pd.array(method, dtype="string")
    out["selection_mode"] = pd.array(mode, dtype="string")
    out["basin_km2"] = [r.get(f"{m}_km2", np.nan) if m else np.nan
                        for m, r in zip(method, out.to_dict("records"))]
    out["no_basin_reason"] = pd.array([None if m is not None else _why_no_basin(r)
                                       for m, r in zip(method, out.to_dict("records"))], dtype="string")
    return out
