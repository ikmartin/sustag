"""Four delineations per site, cached, plus the auto rule that picks one -- and every advisory flag.

    basin0  NLDI `nwissite/{id}/basin` -- the authority's own polygon, READ from the acquired store
            (`acquire.seed_basins.fetch_authority_basins` owns the network call). USGS members only.
    basin1  local upstream accumulation of catchments from the site's snapped `comid`. The default.
    basin2  the same accumulation from `catchment_comid` -- a cross-check, never auto-selected.
    basin3  D8 raster flood-fill over HydroSHEDS 15s. Independent of NHDPlus in code and in data.

A BASIN THAT IS NOT PHYSICAL IS NOT DELINEATED. Off-network sites (wells, failed snaps, non-surface types) and sub-catchment sites (an edge-of-field logger's 10 ha contributing area is two orders under its catchment) keep their rows with a null geometry and a stated `no_basin_reason` -- the chapter's rule that they carry no authoritative basin. `IWQIS:WQS0029` is the case the type guard exists for: a water-supply well that snaps to the Des Moines River, and every method happily hands it the river's 14,288 km2 -- an ordinary area for an ordinary reach, wrong only in its premise.

THE AUTO RULE IS A PRECEDENCE, NOT A SCORE: basin0 where the authority has one, else basin1. basin2 and basin3 are unreachable from it by design and become preferred only by a recorded human decision in the basin ledger, which is keyed on the MEMBER SET and never blocks. Flags never touch the selection -- they populate the review queue and nothing else.

CANDIDATES ARE CACHED PER SITE under `derived/basins/candidates/{method}/`, because the pass is slow (the catchment dissolve is ~15 minutes for a full cohort) and every method is a pure function of its inputs. The cache key is the SITE ID -- a position on a reach -- so a site that re-snaps re-delineates, which is correct because the snap is the delineation's input.

WHY THE DISSOLVE IS ONE PASS OVER THE LAYER. Catchments are 1.49M rows in TWO row groups, so a COMID pushdown still reads half the file -- a per-site read would move 400 MB hundreds of times. The layer is read once as WKB and each basin converts only its own slice. `shapely.coverage_union_all` is the natural tool and does not work: NHDPlus catchments are not correctly noded, and it raises on any batch tried, down to 5,000 polygons. Plain `union_all` is used instead, in batches, which bounds peak memory on the basins that touch a million reaches.

BASIN3 IS ONLY AVAILABLE OVER PART OF THE AOE. `flowdir_15s.tif` is clipped to -105..-87, 37..49 while the AOE runs wider. A basin that reaches the raster edge is TRUNCATED, not small, so it is recorded as `edge_truncated` and given no area: reporting the truncated figure would fire `flag_d8_disagrees` on precisely the largest basins, where the cross-check is worth the most and the raster has the least to say.

FLAGS: FIVE INTERNAL CHECKS AND ONE EXTERNAL, advisory only. The internal ones compare a basin against another measurement of itself; they catch a delineation that is inconsistent. They cannot catch the failure that matters most, because it is consistent: a sensor on a small inlet whose basin snapped to the big river beside it agrees with every method that also snapped there. `flag_river_capture` is the only check that looks outside -- the station NAME against the reach's GNIS name (56 of 62 large-river sites share a word; a site named for a different watercourse than its reach is the capture signature). Snap distance and reported drainage area were both measured and rejected as discriminators: wide rivers put honest gauges 100-430 m from the centreline, and five of six suspicious reported areas were the 2.589988 km2 one-square-mile placeholder.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config, contracts, io as bio, ledger
from ..acquire import seed_basins
from . import accumulate

METHODS = ("basin0", "basin1", "basin2", "basin3")
AUTO_ORDER = ("basin0", "basin1")     # basin2/basin3 are cross-checks; only a human may choose them

CACHE = config.BASINS_DIR / "candidates"           # {method}/{site_id}.parquet

_UNION_BATCH = 50_000


def _fname(site_id: str) -> str:
    return site_id.replace(":", "__")


def _cache_path(method: str, site_id: str) -> Path:
    return CACHE / method / f"{_fname(site_id)}.parquet"


def read(method: str, site_id: str):
    """The cached candidate as a one-row GeoDataFrame, or None. A missing file is a normal answer."""
    import geopandas as gpd

    p = _cache_path(method, site_id)
    if not p.exists():
        return None
    g = gpd.read_parquet(p)
    return g if len(g) and g.geometry.iloc[0] is not None else None


def _write(method: str, site_id: str, geom, comid=None, status: str = "ok"):
    """One candidate to cache. A failed method is still written, with a null geometry and its reason.

    Recording the failure is what makes the pass resumable: a site NLDI has no basin for must not be retried on every run, and `basin0_status` is the evidence the review sheet shows instead of an empty column.
    """
    import geopandas as gpd

    p = _cache_path(method, site_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = gpd.GeoDataFrame(
        {"site_id": [site_id], "method": [method], "comid": [comid], "status": [status],
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

        from ..acquire import network

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


# ---- eligibility ------------------------------------------------------------------------------------

# Why a site has no basin. The distinction is the whole point: three of these are ANSWERS and one is a FAILURE, and only the failure is worth a person's time.
NO_BASIN_SURFACE = "no_surface_drainage"       # the reach carries no upstream drainage -- a coastline
NO_BASIN_OFF_NETWORK = "off_network"           # wells, failed snaps, non-surface types: no network position
NO_BASIN_SUB_CATCHMENT = "sub_catchment_scale"  # drains far less than its catchment; a basin would be fiction
NO_BASIN_FAILED = "all_methods_failed"         # something that could have worked did not


def no_basin_class(site) -> str | None:
    """The reason this site gets NO delineation at all, from its row alone -- or None to delineate.

    Off-network and sub-catchment sites carry no authoritative basin by the chapter's rule. The coastline case is caught later by the topology (`accumulate` refuses a `Coastline` reach) -- two routes to the same conclusion, one shared vocabulary.
    """
    from . import site_type

    if not bool(site.on_network):
        return NO_BASIN_OFF_NETWORK
    if str(site.site_type) in site_type.SUB_CATCHMENT:
        return NO_BASIN_SUB_CATCHMENT
    return None


def delineable(sites: pd.DataFrame) -> pd.DataFrame:
    """The rows the four methods run for; the rest keep their reasons in `select`."""
    return sites[[no_basin_class(r) is None for r in sites.itertuples()]].reset_index(drop=True)


# ---- the four methods -------------------------------------------------------------------------------

def build_local(sites: pd.DataFrame, net: accumulate.Network, force: bool = False,
                methods=("basin1", "basin2")) -> pd.DataFrame:
    """basin1 and basin2 for every delineable site, by accumulation then dissolve.

    Both methods share one `Catchments` read, and the layer is only opened when something is actually missing from cache -- a resumed run with nothing to do pays nothing.
    """
    todo = [(g, m) for g in sites.itertuples() for m in methods
            if force or not _cache_path(m, g.site_id).exists()]
    print(f"  local accumulation: {len(sites) * len(methods) - len(todo)} cached, {len(todo)} to build",
          flush=True)
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
        _write(method, g.site_id, geom, comid=(None if cid is None or pd.isna(cid) else int(cid)),
               status=status)
        rows.append({"site_id": g.site_id, "method": method, "status": status,
                     "acc_km2": acc_km2, "vaa_totda_km2": totda, "n_reaches": len(reaches)})
        if n % 25 == 0 or len(reaches) > 100_000:
            print(f"    {n}/{len(todo)} {g.site_id} {method} {len(reaches):,} reaches", flush=True)
    return pd.DataFrame(rows)


def build_basin0(sites: pd.DataFrame, force: bool = False) -> pd.DataFrame:
    """The authority's delineation per site -- a PURE READ of the acquired store, never a fetch.

    The polygon belongs to a USGS MEMBER of the site's bundle (the authority registered the gauge, not our minted position), so members are tried in sorted order and the first with a stored basin wins. `not_fetched` names the case a rerun of a3 fixes; `no_nldi_basin` is NLDI's own recorded miss and is common enough to be unremarkable -- the Delaware at Chester and the Susquehanna at Columbia are both absent from its index.
    """
    out = []
    for g in sites.itertuples():
        p = _cache_path("basin0", g.site_id)
        if p.exists() and not force:
            continue
        members = [m for m in str(g.bundle).split("+") if m.startswith("USGS:")]
        if not members:
            _write("basin0", g.site_id, None, status="not_usgs")
            out.append({"site_id": g.site_id, "status": "not_usgs"})
            continue
        geom, status = None, "no_nldi_basin"
        for m in sorted(members):
            geom, status = seed_basins.read_authority_basin(m)
            if status == "ok":
                break
        _write("basin0", g.site_id, geom, status=status)
        out.append({"site_id": g.site_id, "status": status})
    df = pd.DataFrame(out, columns=["site_id", "status"])
    if len(df):
        print(f"  basin0 outcomes: {df.status.value_counts().to_dict()}")
        nf = int((df.status == "not_fetched").sum())
        if nf:
            print(f"    {nf} authority basin(s) never fetched -- run `a3 --only basins` and rerun d4")
    return df


def _flood(direction: np.ndarray, col: int, row: int) -> np.ndarray:
    """Cells draining to (col,row): breadth-first against the D8 flow direction."""
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


def build_basin3(sites: pd.DataFrame, expected_km2: dict | None = None, force: bool = False) -> pd.DataFrame:
    """The D8 flood-fill for every site inside the raster's window. Returns the per-site outcome.

    `expected_km2` maps site_id to its basin1 area and is what makes the outlet snap trustworthy: without an expected area `d8._snap_outlet` accepts any cell holding half the search window's maximum accumulation, which on the calibration cohort put 28 of 116 sites on the wrong river -- always a small basin captured by a large one nearby. With it the candidate must land inside a band around the known area, and refusing to snap is preferred to snapping wrong.
    """
    from rasterio.features import shapes
    from shapely.geometry import shape
    from shapely.ops import unary_union

    from src.data import d8

    todo = [g for g in sites.itertuples() if force or not _cache_path("basin3", g.site_id).exists()]
    print(f"  basin3: {len(sites) - len(todo)} cached, {len(todo)} to build", flush=True)
    if not todo:
        return pd.DataFrame(columns=["site_id", "status"])

    direction = d8.load_direction_array()
    expected_km2 = expected_km2 or {}
    out = []
    for n, g in enumerate(todo, 1):
        status, geom = "ok", None
        col, row = d8.ll_to_image_pixel(float(g.lat), float(g.lon))
        if not (0 <= col < d8.W and 0 <= row < d8.H):
            status = "outside_raster"
        else:
            exp = expected_km2.get(g.site_id)
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
        _write("basin3", g.site_id, geom, status=status)
        out.append({"site_id": g.site_id, "status": status})
        if n % 25 == 0:
            print(f"    {n}/{len(todo)}", flush=True)
    df = pd.DataFrame(out)
    print(f"  basin3 outcomes: {df.status.value_counts().to_dict()}")
    return df


# ---- evidence, flags, selection ---------------------------------------------------------------------

def candidate_table(sites: pd.DataFrame) -> pd.DataFrame:
    """Every cached candidate for every site, one row per site: `{method}_km2`, `{method}_status`.

    Reads the caches rather than the builders' return values, so it reports what is ON DISK -- a resumed run and a fresh one produce the same table.
    """
    rows = []
    for g in sites.itertuples():
        rec = {"site_id": g.site_id}
        for m in METHODS:
            c = read(m, g.site_id)
            p = _cache_path(m, g.site_id)
            if c is None and p.exists():
                import geopandas as gpd

                c = gpd.read_parquet(p)                 # a recorded failure: status but no geometry
            rec[f"{m}_km2"] = float(c.area_km2.iloc[0]) if c is not None and len(c) else np.nan
            rec[f"{m}_status"] = str(c.status.iloc[0]) if c is not None and len(c) else "missing"
        rows.append(rec)
    return pd.DataFrame(rows)


# Tolerances. `area_vs_vaa` is tight because it compares our accumulation against NHDPlus's own over the same topology -- it agrees to 0.01% on the calibration cohort, so anything past 5% is a broken walk, not a rounding error. `d8` is wide because a 15 arc-second raster against NHDPlus vector will never agree closely: the old build used 4% and its flag fired on 61 of 116 sites, which is not a flag, it is noise.
AREA_VS_VAA_TOL = 0.05
BASIN0_TOL = 0.10
D8_TOL = 0.35
MAX_CONUS_KM2 = 3.5e6

# How far outside its own basin a site may sit before that means something. NOT zero: a gauge sits ON the outlet reach, so the boundary runs through it. 250 m IS THE RESOLUTION OF THE CLAIM: measured, the distances form two populations -- basin0 selections 50-165 m outside NLDI's GENERALISED polygon (saying nothing), and 0.2-9.1 km cases that are structural (the snapped reach carries no catchment of its own, the divergence case `comid_agree == False` records). The second kind is not an area error -- those basins still reproduce `vaa.totdasqkm` to four decimals -- but it costs a polygon containing its own gauge, which a reviewer can fix by selecting basin3.
NOT_CONTAINED_TOL_KM = 0.25

# River capture: a reach this large is a river whose basin dwarfs anything a capture would have taken instead; the alternative must be this much smaller, this close.
RIVER_DA_KM2 = 10_000.0
ALT_RADIUS_M = 1_500.0
ALT_DA_FACTOR = 100.0

_STOP = {"river", "creek", "at", "near", "nr", "the", "of", "ditch", "abv", "above", "below",
         "blw", "and", "trib", "tributary", "fork", "branch", "run", "lake", "pond"}

FLAG_COLS = ["flag_area_vs_vaa", "flag_basin0_disagrees", "flag_d8_disagrees",
             "flag_not_contained", "flag_oversized", "flag_river_capture"]


def _words(s) -> set[str]:
    """Content words of a name, lowercased. Watercourse nouns are stripped -- every name has 'river' in it."""
    if s is None or (isinstance(s, float) and np.isnan(s)) or pd.isna(s):
        return set()
    return {w for w in re.sub(r"[^a-z ]", " ", str(s).lower()).split()
            if len(w) > 2 and w not in _STOP}


def informative(name, uid=None) -> bool:
    """Does this name say anything about WHERE the site is?

    A station called `WQS9903` does not, and must not be tested against a reach name -- it would mismatch every time and flag a site whose delineation is fine. The reliable test is against the uid itself, because an uninformative station name is almost always the registration id repeated.
    """
    w = _words(name)
    if not w:
        return False
    if uid is not None:
        tag = re.sub(r"[^a-z]", "", str(uid).lower())
        if all(x in tag for x in w):        # every "word" is a fragment of the id -- it names nothing
            return False
    return True


def river_capture(site_name, reach_name, reach_da, alternatives, uid=None) -> bool:
    """True when a site's basin looks like it was captured by a large river beside it.

    `alternatives` is `[(gnis_name, totdasqkm, distance_m), ...]` for reaches near the site other than the one it snapped to. All three conditions must hold, and each rules out a different kind of false positive: the assigned reach is large (a capture only matters when the wrong answer is huge); a much smaller reach is close (there has to be something it should have been instead); the names point at the smaller one (which is what separates capture from a real mainstem gauge).
    """
    if reach_da is None or pd.isna(reach_da) or reach_da < RIVER_DA_KM2:
        return False
    near = [(n, da, d) for n, da, d in alternatives
            if d <= ALT_RADIUS_M and pd.notna(da) and da > 0 and reach_da / da >= ALT_DA_FACTOR]
    if not near:
        return False
    if not informative(site_name, uid):
        return False                       # a uid-only station name cannot be compared to anything
    sw = _words(site_name)
    # THE ASSIGNED REACH GETS THE FIRST SAY. If the station is named for the river it snapped to, it is on that river and nothing else matters -- checking the neighbours first fires on every big-river gauge with a same-named tributary beside it, which is most of them.
    if informative(reach_name) and (sw & _words(reach_name)):
        return False
    return bool(any(sw & _words(n) for n, _, _ in near)) or informative(reach_name)


def capture_evidence(sites: pd.DataFrame, net, radius_m: float = ALT_RADIUS_M) -> pd.DataFrame:
    """What `river_capture` needs, per site: the assigned reach's name and drainage area, and its neighbours'.

    One pass over the flowline layer for the whole cohort -- the layer has no usable spatial index on disk, so the neighbourhood search is an in-memory STRtree over every reach. The `alt_*` columns describe the SMALLEST nearby reach: the one a captured site most likely belongs to, and the one a reviewer wants named.
    """
    import geopandas as gpd
    from shapely import STRtree

    from ..acquire import network

    fl = gpd.read_parquet(network._path("flowlines"), columns=["COMID", "GNIS_NAME", "geometry"])
    fl = fl.to_crs(config.EQUAL_AREA)
    comids = pd.to_numeric(fl.COMID, errors="coerce").to_numpy()
    names = fl.GNIS_NAME.to_numpy()
    geoms = fl.geometry.values
    tree = STRtree(geoms)

    pts = gpd.GeoSeries(gpd.points_from_xy(sites.lon, sites.lat), crs=4326).to_crs(config.EQUAL_AREA).values
    rows = []
    for site, pt in zip(sites.itertuples(), pts):
        cid = None if pd.isna(site.comid) else int(site.comid)
        reach_name = None
        reach_da = np.nan if cid is None else float(net.totda.get(cid, np.nan))
        hits = tree.query(pt.buffer(radius_m))
        alts = []
        for j in hits:
            c = int(comids[j])
            d = float(pt.distance(geoms[j]))
            if c == cid:
                reach_name = names[j]
                continue
            alts.append((names[j], float(net.totda.get(c, np.nan)), d))
        if reach_name is None and cid is not None:
            k = np.where(comids == cid)[0]
            reach_name = names[k[0]] if len(k) else None
        best = min((a for a in alts if pd.notna(a[1]) and a[1] > 0), key=lambda a: a[1], default=None)
        rows.append({
            "site_id": site.site_id,
            "reach_name": reach_name,
            "reach_da_km2": reach_da,
            "alt_name": None if best is None else best[0],
            "alt_da_km2": np.nan if best is None else best[1],
            "alt_dist_m": np.nan if best is None else best[2],
            "n_alternatives": len(alts),
            "flag_river_capture": river_capture(site.station_name, reach_name, reach_da, alts,
                                                uid=site.canonical_uid),
        })
    return pd.DataFrame(rows)


def _outside(d, tol_km: float) -> bool:
    """Is the site outside its own basin by more than `tol_km`? NaN reads as "no basin", not as "outside"."""
    return bool(pd.notna(d) and float(d) > tol_km)


def _ratio_off(a, b, tol: float) -> bool:
    """|a-b|/b beyond `tol`. False whenever either side is missing or the base is zero -- an absent comparison is not a disagreement."""
    if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
        return False
    return abs(a - b) / abs(b) > tol


def evaluate(row) -> dict:
    """Every flag for one site, as a dict of booleans. `row` carries the areas and the capture evidence.

    A SITE WITH NO BASIN GETS NO FLAGS. Every one of these is a claim about a delineation, and where nothing was delineated the claim has no subject. Without this the wells came back into the review by the side door: a well has no basin because it is a well, and it was still queued as `river_capture` because its snapped reach is the Des Moines River.
    """
    m = row.get("basin_method")
    if not (m if isinstance(m, str) else None):
        return {c: False for c in FLAG_COLS}
    return {
        "flag_area_vs_vaa": _ratio_off(row.get("basin1_km2"), row.get("vaa_totda_km2"), AREA_VS_VAA_TOL),
        "flag_basin0_disagrees": _ratio_off(row.get("basin0_km2"), row.get("basin1_km2"), BASIN0_TOL),
        "flag_d8_disagrees": _ratio_off(row.get("basin3_km2"), row.get("basin1_km2"), D8_TOL),
        "flag_not_contained": _outside(row.get("dist_to_basin_km"), NOT_CONTAINED_TOL_KM),
        "flag_oversized": bool((row.get("basin_km2") or 0) > MAX_CONUS_KM2),
        "flag_river_capture": bool(row.get("flag_river_capture", False)),
    }


def corroboration(row) -> tuple[int, str]:
    """How many INDEPENDENT delineations confirm this basin, and which. Returns `(n, "basin0,basin3")`.

    THE POINT IS THE SITES WITH NO AUTHORITY: IWQIS, MPCA and the USGS registrations NLDI's index omits -- the majority, and for them the snap is trusted rather than verified. A method confirms when it EXISTS and its disagreement flag did NOT fire, so the thresholds are the calibrated ones rather than a new number. basin2 counts ONLY where `comid_agree` is False: where the two ids match it is bit-for-bit the same computation on the same reach, and counting it would hand every unremarkable site a free corroboration. `n == 0` is not wrong; it is unverified, which is a different thing and worth being able to select on.
    """
    ok = []
    if pd.notna(row.get("basin0_km2")) and not _ratio_off(row.get("basin0_km2"), row.get("basin1_km2"), BASIN0_TOL):
        ok.append("basin0")
    if pd.notna(row.get("basin3_km2")) and not _ratio_off(row.get("basin3_km2"), row.get("basin1_km2"), D8_TOL):
        ok.append("basin3")
    if (row.get("comid_agree") is False and pd.notna(row.get("basin2_km2"))
            and not _ratio_off(row.get("basin2_km2"), row.get("basin1_km2"), BASIN0_TOL)):
        ok.append("basin2")
    return len(ok), ",".join(ok)


def method_of(row) -> str | None:
    """The selected method as a string, or None. NOT a bare truthiness test on the column: a null renders as float NaN, which is TRUTHY, and passing it to a path expecting a method name is how a site with no basin once crashed the selection rather than being recorded as having none."""
    m = row.get("basin_method") if hasattr(row, "get") else getattr(row, "basin_method", None)
    return m if isinstance(m, str) and m else None


def geometry_for(site_id: str, method):
    """The cached geometry for one site and method, or None. A null method is a normal answer."""
    if not isinstance(method, str) or not method:
        return None
    g = read(method, site_id)
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


def basin_ledger():
    """The basin ledger through the generic kit: keyed on the MEMBER SET, vocabulary = the methods, never gates."""
    import dataclasses

    return dataclasses.replace(ledger.BASINS, vocabulary=tuple(METHODS))


def auto_choice(row) -> str | None:
    """The method the auto rule picks: basin0 if the authority has one, else basin1. None when neither exists."""
    for m in AUTO_ORDER:
        if row.get(f"{m}_status") == "ok" and pd.notna(row.get(f"{m}_km2")):
            return m
    return None


def select(cand: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame:
    """Add `basin_method`, `basin_km2`, `selection_mode` and `no_basin_reason` to the candidate table.

    A human decision wins outright, including one that picks a method with no geometry -- that is a statement about the delineation ("none of these is right"), and silently falling back to the auto rule would hide it. Decisions are keyed on the site's MEMBER SET, so they survive re-snaps and re-minted ids. `selection_mode` is `manual`, `auto`, or `none`.

    A SITE WITH NO BASIN IS NOT NECESSARILY A SITE WITH A PROBLEM. `no_basin_reason` separates the three answers (off-network, sub-catchment scale, no surface drainage) from a delineation that merely failed, and the review sheet only asks about the fourth kind.
    """
    led = basin_ledger()
    dec = {k[0]: v for k, v in led.decisions().items()}
    bundle_of = dict(zip(sites.site_id, sites.bundle))
    reasons = {r.site_id: no_basin_class(r) for r in sites.itertuples()}

    out = cand.copy()
    method, mode = [], []
    for r in out.to_dict("records"):
        d = dec.get(bundle_of.get(r["site_id"]))
        if d:
            method.append(d)
            mode.append("manual")
        else:
            a = auto_choice(r)
            method.append(a)
            mode.append("auto" if a else "none")
    # `string` dtype, so an unselected site carries pd.NA rather than a float NaN that reads as truthy.
    out["basin_method"] = pd.array(method, dtype="string")
    out["selection_mode"] = pd.array(mode, dtype="string")
    out["basin_km2"] = [r.get(f"{m}_km2", np.nan) if m else np.nan
                        for m, r in zip(method, out.to_dict("records"))]
    out["no_basin_reason"] = pd.array(
        [None if m is not None else (reasons.get(r["site_id"])
                                     or ("no_surface_drainage" if r.get("basin1_status") == "no_drainage_reach"
                                         else NO_BASIN_FAILED))
         for m, r in zip(method, out.to_dict("records"))], dtype="string")
    return out
