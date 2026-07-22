"""Compute drainage basins (v0-v3) per site and select a preferred basin.

The D8 raster primitives (direction500m.png loader, pixel mapping, geo-referencing) live in
src/data/d8.py (shared with the runtime site_view), so this builder imports them from there.

basin0 : NWIS authoritative -- NLDI nwissite, addressed by uid. USGS SITES ONLY. Real COMID.
basin1 : NEAREST-FLOWLINE SNAP. Find the closest NHD reach to the sensor, then take that COMID's
         upstream basin. The default for everything without a basin0. Real COMID.
basin2 : CONTAINING CATCHMENT -- NLDI /comid/position, i.e. whatever catchment polygon the point
         falls inside. Kept as a cross-check only; see below for why it is not the default.
basin3 : D8 raster BFS flood-fill. Cross-check only -- never selected by the auto rule.
preferred: basin0 if eligible and present, else basin1.

WHY basin1 IS THE SNAP AND NOT /comid/position:
    /comid/position returns the catchment polygon CONTAINING the point, not the nearest stream. In
    NHDPlus, mainstem reaches through divergences carry AreaSqKM = 0 -- no catchment of their own --
    so a sensor sitting ON such a mainstem falls inside a neighbouring tributary's catchment and the
    service returns the tributary. WQS0061 is the worked example: the Soldier River (order 5,
    1057 km2) is 4 m away, but /comid/position returned an unnamed order-2 reach 510 m away with
    15.6 km2, understating the basin by 98.5% -- silently. 2% of Iowa reaches have zero-area
    catchments, 305 of them at order >= 4, so this is a live hazard for any mainstem pin.

WHY basin0 STILL EXISTS: where a site has an NWIS id, the authority's own delineation beats
inferring one from coordinates.

COMID is the join key for the pre-accumulated USGS/EPA attribute tables (TOT_/CAT_/ACC_ prefixes:
tile drainage, base-flow index, subsurface contact time, SPARROW yields). It is a property of the
sensor's snapped reach, not of the polygon, so it must always come from the authority (NLDI) and
never be copied between delineations.

THE ARCHIVE (.preferred_basin_archive.csv) is a read-only cache of reviewed selections. It is
consulted to OVERRIDE the auto rule in _build_preferred_csv; nothing in this repo writes it, so it
is refreshed by hand after a review pass. --recalculate deliberately ignores it: that is a HARD
RESET, re-picking every site from the auto rule so the sites can be re-reviewed and the cache
rewritten. Dropping manual basin3/basin4 selections is the intent, not a failure mode.

The legacy "archive matches -> nothing to do" early-exit and the reconstruct-from-archive restore
path were dropped. This builder recomputes any missing {0,1,2,3} parquet (respecting --force) and
always rewrites preferred_basin.csv. It is a rebuild path, not a hot loop.

Outputs: processed/basins/data/{uid}_basin{0,1,2,3}.parquet, processed/basins/meta/preferred_basin.csv.
Site coords <- processed/water/meta/site_location_metadata.csv.
BOTH basin1 and flag_river read processed/map_overlays/iowa_flowlines.parquet, so make_data.py runs
_make_map_overlays BEFORE this builder. A missing layer disables flag_river with a warning, but
basin1 RAISES -- silently degrading the default delineation to the weaker /comid/position method is
exactly the failure this redesign exists to prevent.

Usage
-----
    python -m src.build._make_basins [--force] [--recalculate] [--usgs-only] [--wqs-only]
                                     [--report-auto]
"""

import argparse
import math
import sys
from collections import deque
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from rasterio.features import shapes
from shapely.geometry import Point, shape
from shapely.ops import unary_union

_THIS_DIR = Path(__file__).resolve().parent  # src/build
_SRC = _THIS_DIR.parent  # src
sys.path.insert(0, str(_SRC.parent))  # repo root on path

from src.data import d8
from src.data.crs import EQUAL_AREA_CRS

_BASIN_DATA_DIR = _SRC / "data" / "processed" / "basins" / "data"
_META_DIR = _SRC / "data" / "processed" / "basins" / "meta"
_PREFERRED_CSV = _META_DIR / "preferred_basin.csv"
_ARCHIVE_CSV = _META_DIR / ".preferred_basin_archive.csv"

# Relative area disagreement above which a site is flagged for manual review. Both checks are
# |other - basin1| / area(basin1), because basin1 (the nearest-flowline snap) is the default
# delineation and therefore the natural reference.
#   flag_area_mismatch_v2 -- basin1 vs the containing catchment. Fires when /comid/position picked
#                            a different reach, which is the WQS0061 lateral-snap failure.
#   flag_area_mismatch_v3 -- basin1 vs the D8 raster. An independent check that shares no code or
#                            data with NHDPlus, so it catches errors common to both vector methods.
# basin3 is a 500 m raster against NHDPlus vector, so a few percent of v3 disagreement is
# measurement artifact rather than delineation error.
AREA_FLAG_MISMATCH_RATIO = 0.04

# Tie tolerance for the nearest-flowline snap: among reaches whose distance to the sensor is within
# this many metres of the closest one, prefer the largest upstream drainage area. Resolves
# confluences toward the mainstem without dragging a genuine tributary sensor onto it.
SNAP_TIE_TOLERANCE_M = 25.0

# When a site has an operator-reported drainage area, disambiguate among reaches within this radius
# by matching that area instead of taking the strictly nearest reach. Fixes sensors that sit a few
# hundred metres from the mainstem they monitor but a few metres from a parallel tributary (WQS0023
# is 38 m from a 50 km2 reach, 497 m from the 6091 km2 Wapsipinicon it actually gauges). ONLY used
# when a reported area exists, so an arbitrary map pin (deploy path) always gets pure-nearest.
SNAP_AREA_MATCH_RADIUS_M = 600.0
# A reported area this far (in log-ratio) from every nearby reach is treated as unmatchable -- the
# coordinates are probably wrong -- so the snap falls back to nearest and lets flag_area_mismatch_v3
# surface it for manual review rather than confidently snapping to the wrong reach.
SNAP_AREA_MATCH_MAX_LOGERR = 0.69  # ~2x

# flag_river: the sensor sits within RIVER_FLAG_SEARCH_KM of a flowline whose TOTAL UPSTREAM
# drainage area exceeds RIVER_FLAG_DA_KM2. This is a PROXIMITY RISK flag, not a correctness test.
#
# It exists for the case internal-consistency checks cannot see: a sensor on a small inlet running
# into a large river, where basin1 AND basin3 both snap to the large river. They then agree with
# each other (no flag_area_mismatch_v3) and with the river's own drainage area, so every self-check
# passes while the delineation is wrong. WQS0065 and WQS0066 are the worked examples.
# Expect legitimate mainstem-monitoring sites to flag too; that is the intended trade.
#
# The RADIUS matters as much as the threshold. WQS0065 and WQS0066 sit 1.82 km and 2.12 km from the
# Missouri River, so the 1 km window the old Natural Earth flag used missed both of them while
# their basin1 delineations had already snapped to the Missouri (765,000 km2). 3 km is the smallest
# radius that catches both. Flagged counts at DA > 10,000 km2 across the current 85 sites:
#   1 km -> 10 (misses both)   2 km -> 13 (misses both)   3 km -> 18   4 km -> 20   5 km -> 21
RIVER_FLAG_DA_KM2 = 10_000
RIVER_FLAG_SEARCH_KM = 3.0

# Every flag column written to preferred_basin.csv. The widget derives its filter and label lists
# from the `flag_` prefix, so adding one here is enough -- see widget/components/basin_editor.py.
FLAG_COLS = [
    "flag_area",
    "flag_river",
    "flag_not_contained",
    "flag_basin1_over_basin0",
    "flag_area_mismatch_v2",
    "flag_area_mismatch_v3",
]


def _metadata_path() -> Path:
    return _SRC / "data" / "processed" / "water" / "meta" / "site_location_metadata.csv"


# ── Basins 0-2: NLDI-derived (uid lookup / nearest-flowline snap / containing catchment) ──────

_NLDI_BASE = "https://api.water.usgs.gov/nldi/linked-data"


def _basin0_eligible(uid: str) -> bool:
    """Whether basin0 (NWIS authoritative, addressed by uid) can exist for this site at all."""
    return uid.startswith("USGS-")


def _fetch_site_comid(uid: str, timeout: int = 60) -> int | None:
    """COMID of the reach an NWIS site is registered on.

    The /basin endpoint returns empty properties, so the comid needs this separate GET against the
    site endpoint. Returns None rather than raising if the site is absent from NLDI -- a basin with
    a missing comid still beats no basin.
    """
    resp = requests.get(f"{_NLDI_BASE}/nwissite/{uid}", params={"f": "json"}, timeout=timeout)
    resp.raise_for_status()
    feats = resp.json().get("features", [])
    if not feats:
        return None
    comid = feats[0].get("properties", {}).get("comid")
    return int(comid) if comid is not None else None


def _basin_for_comid(uid: str, comid: int, timeout: int = 60) -> gpd.GeoDataFrame:
    """Upstream basin polygon for a COMID, in the canonical 4-column schema."""
    resp = requests.get(
        f"{_NLDI_BASE}/comid/{comid}/basin", params={"f": "json", "simplified": "true"}, timeout=timeout
    )
    resp.raise_for_status()
    gdf = gpd.GeoDataFrame.from_features(resp.json()["features"], crs="EPSG:4326")
    if gdf.empty:
        raise ValueError(f"NLDI returned an empty basin for COMID {comid} ({uid}).")
    gdf = gdf[["geometry"]].copy()
    gdf["site_uid"] = uid
    gdf["comid"] = int(comid)  # int, not object -- these frames get concatenated in access.py
    gdf["area_km2"] = gdf.to_crs(EQUAL_AREA_CRS).area / 1e6
    return gdf[["site_uid", "comid", "area_km2", "geometry"]]


def _compute_basin0(uid: str, lat: float, lon: float, timeout: int = 60) -> gpd.GeoDataFrame | None:
    """Authoritative NWIS basin, addressed by site uid. USGS only; None for anything else.

    Returning None (rather than raising) for non-USGS uids means _build_type logs a skip and never
    writes, so no non-USGS basin0 parquet can be created even under --force.
    """
    if not _basin0_eligible(uid):
        return None

    # Geometry first, comid second: if the comid GET fails we still keep the polygon.
    resp = requests.get(
        f"{_NLDI_BASE}/nwissite/{uid}/basin", params={"f": "json", "simplified": "true"}, timeout=timeout
    )
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        raise ValueError(f"NLDI returned no features for {uid}.")
    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    if gdf.empty:
        raise ValueError(f"NLDI returned an empty basin for {uid}.")

    try:
        comid = _fetch_site_comid(uid, timeout=timeout)
    except requests.RequestException as e:
        print(f"  {uid}: basin fetched but comid lookup failed ({e}) -- writing comid=None.")
        comid = None

    gdf = gdf[["geometry"]].copy()
    gdf["site_uid"] = uid
    gdf["comid"] = comid
    gdf["area_km2"] = gdf.to_crs(EQUAL_AREA_CRS).area / 1e6
    return gdf[["site_uid", "comid", "area_km2", "geometry"]]


def snap_comid(
    lat: float, lon: float, flowlines: gpd.GeoDataFrame, sindex=None, reported_area_km2: float | None = None
) -> tuple[int, float, str]:
    """COMID of the NHD reach the sensor sits on. Returns (comid, distance_m, name).

    Base rule -- nearest by distance, then among reaches within SNAP_TIE_TOLERANCE_M of the closest,
    the largest upstream drainage area (resolves confluences toward the mainstem).

    Area refinement -- when reported_area_km2 is given, disambiguate among reaches within
    SNAP_AREA_MATCH_RADIUS_M by matching that area in log-space instead of taking the strictly
    nearest reach, UNLESS the best match is worse than SNAP_AREA_MATCH_MAX_LOGERR (probable bad
    coordinates -> fall back to nearest and let flag_area_mismatch_v3 flag it). Reported areas only
    exist for known sites, so a deploy-path map pin (reported_area_km2=None) always gets the base
    rule -- the two paths stay identical wherever no area is available.

    Reaches with TotDASqKM == 0 (NHDPlus divergence artifacts with no catchment) are never valid
    targets and are excluded first -- they are the reaches /comid/position kept mis-snapping to.

    Raises ValueError outside the flowlines extent. Falling back to /comid/position there would
    silently reintroduce the lateral-snap bug this function exists to fix.
    """
    pt = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326").to_crs(EQUAL_AREA_CRS).geometry.iloc[0]
    sindex = sindex if sindex is not None else flowlines.sindex

    # sindex positions index into `flowlines`; take those rows FIRST, then drop the zero-drainage
    # divergence artifacts. Filtering flowlines before .iloc would misalign the positions.
    hits = gpd.GeoDataFrame()
    for radius_m in (500, 2_000, 10_000):
        hits = flowlines.iloc[list(sindex.query(pt.buffer(radius_m), predicate="intersects"))]
        if "TotDASqKM" in hits:
            hits = hits[hits["TotDASqKM"] > 0]
        if not hits.empty:
            break
    if hits.empty:
        raise ValueError(
            f"No NHD flowline within 10 km of ({lat}, {lon}) -- outside the cached flowlines extent. "
            "Rebuild src/data/processed/map_overlays/iowa_flowlines.parquet over a wider geometry."
        )

    d = hits.geometry.distance(pt)

    if reported_area_km2 and reported_area_km2 > 0 and "TotDASqKM" in hits:
        cand = hits[d <= SNAP_AREA_MATCH_RADIUS_M]
        if not cand.empty:
            logerr = (cand["TotDASqKM"] / reported_area_km2).apply(lambda x: abs(math.log(x)))
            if logerr.min() <= SNAP_AREA_MATCH_MAX_LOGERR:
                best = cand.loc[logerr.idxmin()]
                return int(best["COMID"]), float(d[best.name]), str(best.get("GNIS_NAME", "") or "").strip()

    near = hits[d <= d.min() + SNAP_TIE_TOLERANCE_M]
    best = near.loc[near["TotDASqKM"].idxmax()] if "TotDASqKM" in near else hits.loc[d.idxmin()]
    return int(best["COMID"]), float(d.min()), str(best.get("GNIS_NAME", "") or "").strip()


def _compute_basin1(
    uid: str,
    lat: float,
    lon: float,
    flowlines: gpd.GeoDataFrame | None = None,
    sindex=None,
    reported_area_km2: float | None = None,
    timeout: int = 60,
) -> gpd.GeoDataFrame:
    """NEAREST-FLOWLINE SNAP basin -- the default delineation.

    Snaps to the closest NHD reach and takes that COMID's upstream basin, instead of trusting
    /comid/position to pick the right reach. See the module docstring for why (WQS0061).

    reported_area_km2, when known, refines reach selection (see snap_comid). It is passed for known
    sites and left None for an arbitrary map pin, so the deploy path always gets the pure-nearest
    rule.
    """
    if flowlines is None:
        flowlines = _load_flowlines()
    if flowlines is None:
        raise RuntimeError(
            "basin1 needs the NHD flowlines layer; run `python -m src.build._make_map_overlays` first."
        )
    comid, dist_m, name = snap_comid(lat, lon, flowlines, sindex, reported_area_km2=reported_area_km2)
    if dist_m > 250:
        print(f"  {uid}: nearest reach is {dist_m:.0f} m away ({name or 'unnamed'}) -- check the coordinates.")
    return _basin_for_comid(uid, comid, timeout=timeout)


def _compute_basin2(uid: str, lat: float, lon: float, timeout: int = 60) -> gpd.GeoDataFrame:
    """CONTAINING-CATCHMENT basin -- NLDI /comid/position. Cross-check only, never the default.

    Returns whichever catchment polygon the point falls inside, which is NOT necessarily the reach
    the sensor sits on: mainstem reaches at divergences have no catchment of their own, so a point
    on the mainstem resolves to a neighbouring tributary.
    """
    pos = requests.get(
        f"{_NLDI_BASE}/comid/position", params={"coords": f"POINT({lon} {lat})", "f": "json"}, timeout=timeout
    )
    pos.raise_for_status()
    feats = pos.json().get("features", [])
    if not feats:
        raise ValueError(f"No NHDPlus catchment near ({lat}, {lon}) for {uid}.")
    return _basin_for_comid(uid, feats[0]["properties"]["comid"], timeout=timeout)


# ── Basin 3: D8 raster BFS flood-fill (uses src/data/d8) ─────────────────────

_SNAP_RADIUS = 15


def _inflow_count(direction: np.ndarray, col: int, row: int) -> int:
    count = 0
    for dc, dr, exp in d8.NEIGHBOR_CHECKS:
        nc, nr = col + dc, row + dr
        if 0 <= nc < d8.W and 0 <= nr < d8.H and direction[nr, nc] == exp:
            count += 1
    return count


def _snap_to_stream(direction: np.ndarray, col: int, row: int) -> tuple[int, int]:
    best_col, best_row = col, row
    best_score = (_inflow_count(direction, col, row), 0)
    r = _SNAP_RADIUS
    for dr in range(-r, r + 1):
        for dc in range(-r, r + 1):
            nc, nr = col + dc, row + dr
            if not (0 <= nc < d8.W and 0 <= nr < d8.H) or direction[nr, nc] == 0:
                continue
            score = (_inflow_count(direction, nc, nr), -(abs(dc) + abs(dr)))
            if score > best_score:
                best_score, best_col, best_row = score, nc, nr
    return best_col, best_row


def _bfs(direction: np.ndarray, col: int, row: int) -> np.ndarray:
    mask = np.zeros((d8.H, d8.W), dtype=np.uint8)
    mask[row, col] = 1
    queue = deque([(col, row)])
    while queue:
        cx, cy = queue.popleft()
        for dx, dy, expected in d8.NEIGHBOR_CHECKS:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < d8.W and 0 <= ny < d8.H and not mask[ny, nx] and direction[ny, nx] == expected:
                mask[ny, nx] = 1
                queue.append((nx, ny))
    return mask


def _compute_basin3(uid: str, lat: float, lon: float, direction: np.ndarray) -> gpd.GeoDataFrame | None:
    col, row = d8.ll_to_image_pixel(lat, lon)
    if not (0 <= col < d8.W and 0 <= row < d8.H) or direction[row, col] == 0:
        return None
    mask = _bfs(direction, col, row)
    if mask.sum() < 10:
        sc, sr = _snap_to_stream(direction, col, row)
        if (sc, sr) != (col, row):
            mask = _bfs(direction, sc, sr)
    polys = [shape(geom) for geom, val in shapes(mask, transform=d8.TRANSFORM) if val == 1]
    if not polys:
        return None
    gdf = gpd.GeoDataFrame(geometry=[unary_union(polys)], crs="EPSG:4326")
    gdf["site_uid"] = uid
    gdf["area_km2"] = gdf.to_crs(EQUAL_AREA_CRS).area / 1e6
    return gdf[["site_uid", "area_km2", "geometry"]]


# ── helpers: distance, flowlines ─────────────────────────────────────────────────


def _dist_km(lat: float, lon: float, gdf: gpd.GeoDataFrame) -> float:
    pt = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326").to_crs(EQUAL_AREA_CRS).geometry.iloc[0]
    poly = gdf.to_crs(EQUAL_AREA_CRS).geometry.union_all()
    return pt.distance(poly) / 1000.0


_FLOWLINES_PATH = _SRC / "data" / "processed" / "map_overlays" / "iowa_flowlines.parquet"


def _load_flowlines() -> gpd.GeoDataFrame | None:
    """NHD flowlines with TotDASqKM, projected to equal-area. None if unavailable.

    Built by _make_map_overlays.py, which make_data.py runs BEFORE basins for this reason. The
    layer is currently clipped to Iowa (streamorde >= 3); to generalize, re-run that builder over
    a wider geometry -- pynhd's NHD("flowline_mr").bygeom works anywhere. A site outside the cached
    extent simply finds no flowlines and flag_river comes out False, so coverage gaps degrade to a
    missing warning rather than a wrong answer.
    """
    if not _FLOWLINES_PATH.exists():
        print(f"  flag_river: {_FLOWLINES_PATH.name} not found -- flag will be False for every site.")
        print("              Run `python -m src.build._make_map_overlays` first to enable it.")
        return None
    fl = gpd.read_parquet(_FLOWLINES_PATH)
    if "TotDASqKM" not in fl.columns:
        print("  flag_river: flowlines layer has no TotDASqKM column -- flag will be False.")
        return None
    return fl.to_crs(EQUAL_AREA_CRS)


def _nearby_river(lat: float, lon: float, flowlines, sindex) -> tuple[float, str]:
    """(drainage area km2, name) of the largest river within RIVER_FLAG_SEARCH_KM of the site."""
    if flowlines is None:
        return float("nan"), ""
    pt = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326").to_crs(EQUAL_AREA_CRS).geometry.iloc[0]
    hits = flowlines.iloc[list(sindex.query(pt.buffer(RIVER_FLAG_SEARCH_KM * 1000), predicate="intersects"))]
    if hits.empty or hits["TotDASqKM"].isna().all():
        return float("nan"), ""
    top = hits.loc[hits["TotDASqKM"].idxmax()]
    return float(top["TotDASqKM"]), str(top.get("GNIS_NAME", "") or "").strip()


# ── preferred basin metadata ──────────────────────────────────────────────────


def _area_mismatch(base: float, other: float) -> bool:
    """Whether `other` differs from `base` by more than AREA_FLAG_MISMATCH_RATIO.

    Always relative to `base` (basin1, the default delineation). NaN or a zero base -> False, so
    a missing cross-check reads as "nothing to say" rather than as a flag.
    """
    if math.isnan(base) or math.isnan(other) or base <= 0:
        return False
    return abs(other - base) / base > AREA_FLAG_MISMATCH_RATIO


def _preferred_area(basin_type: int, basin_name: str, area: dict) -> float:
    """Area of the selected basin. Types 0-3 come from the scan; type 4 is read off disk.

    basin4 files are written by the widget (access.update_basin), which does a bare to_parquet on a
    frame that may carry geometry ALONE -- so area_km2 cannot be assumed present.
    """
    if basin_type in (0, 1, 2, 3):
        return area[basin_type]
    p4 = _BASIN_DATA_DIR / basin_name
    if not p4.exists():
        return float("nan")
    b4 = gpd.read_parquet(p4)
    if "area_km2" in b4.columns:
        return float(b4["area_km2"].sum())
    return float(b4.to_crs(EQUAL_AREA_CRS).area.sum()) / 1e6


def _build_preferred_csv(meta, flowlines=None, archive=None, report_auto=False) -> None:
    """Select preferred basin per site, compute distances/areas/flags, write CSV."""
    fl_sindex = flowlines.sindex if flowlines is not None else None
    arch_lookup = {} if archive is None else archive.set_index("site_uid").to_dict("index")
    rows, auto_divergences = [], []
    for _, site_row in meta.iterrows():
        uid = site_row["site_uid"]
        lat, lon = float(site_row["latitude"]), float(site_row["longitude"])
        gdf = {
            t: (
                gpd.read_parquet(_BASIN_DATA_DIR / f"{uid}_basin{t}.parquet")
                if (_BASIN_DATA_DIR / f"{uid}_basin{t}.parquet").exists()
                else None
            )
            for t in (0, 1, 2, 3)
        }
        nan = float("nan")
        area = {t: float(gdf[t]["area_km2"].sum()) if gdf[t] is not None else nan for t in (0, 1, 2, 3)}
        dist = {t: _dist_km(lat, lon, gdf[t]) if gdf[t] is not None else nan for t in (0, 1, 2, 3)}

        # Auto rule: prefer the authority (basin0), else the nearest-flowline snap (basin1).
        # basin2 and basin3 are cross-checks and are deliberately unreachable from here -- they can
        # only become preferred via the archive or the widget.
        auto_type = 0 if (_basin0_eligible(uid) and gdf[0] is not None) else 1

        if uid in arch_lookup:
            ar = arch_lookup[uid]
            basin_type, basin_name = int(ar["basin_type"]), ar["basin_name"]
            sel_mode, reviewed = ar["selection_mode"], ar["reviewed"]
            if report_auto and basin_type != auto_type:
                auto_divergences.append(f"  {uid}: archive=basin{basin_type}  auto_would_pick=basin{auto_type}")
        else:
            basin_type = auto_type
            basin_name, sel_mode, reviewed = f"{uid}_basin{basin_type}.parquet", "auto", False

        pref_area = _preferred_area(basin_type, basin_name, area)

        river_da, river_name = _nearby_river(lat, lon, flowlines, fl_sindex)
        existing_dists = [d for d in dist.values() if not math.isnan(d)]
        rows.append(
            dict(
                site_uid=uid,
                basin_name=basin_name,
                basin_type=basin_type,
                dist_to_0=dist[0],
                dist_to_1=dist[1],
                dist_to_2=dist[2],
                dist_to_3=dist[3],
                area0=area[0],
                area1=area[1],
                area2=area[2],
                area3=area[3],
                selection_mode=sel_mode,
                reviewed=reviewed,
                river_da_km2=river_da,
                river_name=river_name,
                flag_area=(not math.isnan(pref_area)) and pref_area > 50_000,
                # Proximity RISK, not a correctness test -- see RIVER_FLAG_DA_KM2 above.
                flag_river=(not math.isnan(river_da)) and river_da > RIVER_FLAG_DA_KM2,
                flag_not_contained=bool(existing_dists) and all(d > 0 for d in existing_dists),
                # The authority exists but a human chose the snap over it.
                flag_basin1_over_basin0=gdf[0] is not None and basin_type == 1,
                # Both checks are against basin1, the default delineation, and are computed even
                # when basin0 is preferred: they compare delineation METHODS, so a disagreement is
                # informative regardless of which basin was selected. NaN-safe -> False.
                flag_area_mismatch_v2=_area_mismatch(area[1], area[2]),
                flag_area_mismatch_v3=_area_mismatch(area[1], area[3]),
            )
        )
    df = pd.DataFrame(rows)
    df.to_csv(_PREFERRED_CSV, index=False)
    n_flagged = df[FLAG_COLS].any(axis=1).sum()
    print(f"preferred_basin.csv: {len(df)} sites, {n_flagged} flagged.")
    r = f"{AREA_FLAG_MISMATCH_RATIO:.0%}"
    print(f"  flag_area_mismatch_v2 (|area2-area1|/area1 > {r}): {int(df.flag_area_mismatch_v2.sum())}")
    print(f"  flag_area_mismatch_v3 (|area3-area1|/area1 > {r}): {int(df.flag_area_mismatch_v3.sum())}")
    print(
        f"  flag_river (river > {RIVER_FLAG_DA_KM2:,} km2 within {RIVER_FLAG_SEARCH_KM} km): {int(df.flag_river.sum())}"
    )
    if report_auto:
        if auto_divergences:
            print(f"\n{len(auto_divergences)} site(s) where the archive overrides the auto rule:")
            print("\n".join(auto_divergences))
        else:
            print("\nAuto rule agrees with the archive on every site.")


def _build_type(uids, coords, basin_type, compute, force=False) -> None:
    label = {
        0: "Basin 0 (NWIS authoritative, USGS only)",
        1: "Basin 1 (nearest-flowline snap)",
        2: "Basin 2 (containing catchment)",
        3: "Basin 3 (D8 raster)",
    }[basin_type]
    print(f"── {label} — {len(uids)} sites ──")
    ok = skip = fail = 0
    for i, uid in enumerate(uids, 1):
        out = _BASIN_DATA_DIR / f"{uid}_basin{basin_type}.parquet"
        if out.exists() and not force:
            skip += 1
            continue
        try:
            gdf = compute(uid, coords[uid]["latitude"], coords[uid]["longitude"])
            if gdf is None:
                print(f"  ({i}/{len(uids)}) {uid}: no basin, skipping.")
                skip += 1
                continue
            gdf.to_parquet(out)
            ok += 1
        except Exception as e:
            print(f"  ({i}/{len(uids)}) {uid}: failed — {e}")
            fail += 1
    print(f"Basin{basin_type}: {ok} saved, {skip} skipped, {fail} failed.\n")


def main(api_keys=None, force=False, usgs_only=False, wqs_only=False, recalculate=False, report_auto=False) -> None:
    _BASIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _META_DIR.mkdir(parents=True, exist_ok=True)

    meta = pd.read_csv(_metadata_path())
    coords = meta.set_index("site_uid")[["latitude", "longitude"]].to_dict("index")

    # Operator-reported drainage area (sq mi -> km2) refines the basin1 snap for known sites; absent
    # for a deploy-path map pin, so the two paths agree wherever no area exists.
    KM2_PER_SQMI = 2.58999
    reported_area = {
        uid: (float(a) * KM2_PER_SQMI if pd.notna(a) else None)
        for uid, a in meta.set_index("site_uid").get("drainage_area", pd.Series(dtype=float)).items()
    }

    all_uids = sorted(meta["site_uid"])
    if wqs_only:
        uids = [u for u in all_uids if u.startswith("WQS")]
    elif usgs_only:
        uids = [u for u in all_uids if u.startswith("USGS-")]
    else:
        uids = all_uids

    # basin0 is USGS-only regardless of the CLI filters. Belt and braces with _compute_basin0's
    # None return, so a non-USGS basin0 parquet cannot be written even under --force.
    usgs_uids = [u for u in uids if _basin0_eligible(u)]

    flowlines = _load_flowlines()
    if flowlines is None:
        raise RuntimeError(
            "basin1 (nearest-flowline snap) needs processed/map_overlays/iowa_flowlines.parquet.\n"
            "Run `python -m src.build._make_map_overlays` first. Refusing to fall back to the\n"
            "containing-catchment method, which is the delineation bug this snap exists to fix."
        )
    sindex = flowlines.sindex

    print(f"Building basins for {len(uids)} site(s).")
    print(f"  - basin0: NLDI nwissite, authoritative (USGS only -- {len(usgs_uids)} site(s))")
    print("  - basin1: nearest-flowline snap (all sites; THE DEFAULT)")
    print("  - basin2: NLDI containing catchment (all sites; cross-check, never auto-selected)")
    print("  - basin3: D8 raster flood-fill (all sites; cross-check, never auto-selected)")
    direction = d8.load_direction_array()

    _build_type(usgs_uids, coords, 0, lambda uid, lat, lon: _compute_basin0(uid, lat, lon), force=force)
    _build_type(
        uids,
        coords,
        1,
        lambda uid, lat, lon: _compute_basin1(uid, lat, lon, flowlines, sindex, reported_area.get(uid)),
        force=force,
    )
    _build_type(uids, coords, 2, lambda uid, lat, lon: _compute_basin2(uid, lat, lon), force=force)
    _build_type(uids, coords, 3, lambda uid, lat, lon: _compute_basin3(uid, lat, lon, direction), force=force)

    print("── Building preferred_basin.csv ──")
    archive = pd.read_csv(_ARCHIVE_CSV) if (_ARCHIVE_CSV.exists() and not recalculate) else None
    if archive is None:
        print("  Hard reset: archive ignored, every site re-picked by the auto rule (basin0/basin1).")
        print("  Any basin2/basin3/basin4 selection is dropped -- re-review, then refresh the archive.")
    _build_preferred_csv(meta, flowlines, archive=archive, report_auto=report_auto)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Rewrite existing parquets.")
    parser.add_argument("--recalculate", action="store_true", help="Recompute all, ignoring the archive.")
    parser.add_argument("--usgs-only", action="store_true")
    parser.add_argument(
        "--wqs-only",
        "--iwqis-only",
        dest="wqs_only",
        action="store_true",
        help="Build only WQS sites. --iwqis-only is a deprecated alias.",
    )
    parser.add_argument(
        "--report-auto",
        action="store_true",
        help="Print sites where the archive overrides what the auto rule would pick.",
    )
    args = parser.parse_args()
    main(
        force=args.force or args.recalculate,
        recalculate=args.recalculate,
        usgs_only=args.usgs_only,
        wqs_only=args.wqs_only,
        report_auto=args.report_auto,
    )
