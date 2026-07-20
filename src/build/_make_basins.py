"""Compute drainage basins (v1, v2, v3) per site and select a preferred basin.

Faithful port of data/basins/make_basins.py. The D8 raster primitives (direction500m.png loader,
pixel mapping, geo-referencing) now live in src/data/d8.py (shared with the runtime site_view), so
this builder imports them from there. NOT a re-grain -- basins port unchanged in logic.

basin1 : NLDI position snap (any CONUS site).
basin2 : authoritative -- NLDI nwissite (USGS) or IWQIS KMZ (WQS).
basin3 : D8 raster BFS flood-fill (IWQIS web-app algorithm; cross-check).
preferred: USGS with basin2 -> basin2, else basin1.

NOTE (deviation from legacy): the archive incremental machinery is SIMPLIFIED. The archive
(.preferred_basin_archive.csv) is still consulted to OVERRIDE preferred-basin selection in
_build_preferred_csv, but the legacy "archive matches -> nothing to do" early-exit and the
missing-parquet reconstruct-from-archive restore path were dropped. This builder recomputes any
missing {1,2,3} parquet (respecting --force) and always rewrites preferred_basin.csv. Since the
basins data is already built + relocated, this is a rebuild path, not a hot loop.

Outputs: processed/basins/data/{uid}_basin{1,2,3}.parquet, processed/basins/meta/preferred_basin.csv.
Rivers cache -> raw/basins/cache/rivers.gpkg. Site coords <- processed/water/meta/site_location_metadata.csv.

Usage
-----
    python -m src.build._make_basins [--force] [--recalculate] [--usgs-only] [--iwqis-only]
"""

import argparse
import io
import math
import re
import sys
import zipfile
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
_CACHE_DIR = _SRC / "data" / "raw" / "basins" / "cache"  # rivers.gpkg (raster handled by d8)

_KEY_COLS = ["basin_name", "basin_type", "selection_mode", "reviewed"]


def _metadata_path() -> Path:
    return _SRC / "data" / "processed" / "water" / "meta" / "site_location_metadata.csv"


# ── Basin 1: NLDI position snap ───────────────────────────────────────────────

_NLDI_BASE = "https://api.water.usgs.gov/nldi/linked-data"


def _compute_basin1(uid: str, lat: float, lon: float, timeout: int = 60) -> gpd.GeoDataFrame:
    """NLDI position-snap basin for a single site."""
    pos = requests.get(
        f"{_NLDI_BASE}/comid/position", params={"coords": f"POINT({lon} {lat})", "f": "json"}, timeout=timeout
    )
    pos.raise_for_status()
    feats = pos.json().get("features", [])
    if not feats:
        raise ValueError(f"No NHDPlus catchment near ({lat}, {lon}) for {uid}.")
    comid = feats[0]["properties"]["comid"]
    resp = requests.get(
        f"{_NLDI_BASE}/comid/{comid}/basin", params={"f": "json", "simplified": "true"}, timeout=timeout
    )
    resp.raise_for_status()
    gdf = gpd.GeoDataFrame.from_features(resp.json()["features"], crs="EPSG:4326")
    if gdf.empty:
        raise ValueError(f"NLDI returned empty basin for COMID {comid} ({uid}).")
    gdf = gdf[["geometry"]].copy()
    gdf["site_uid"] = uid
    gdf["comid"] = comid
    gdf["area_km2"] = gdf.to_crs(EQUAL_AREA_CRS).area / 1e6
    return gdf[["site_uid", "comid", "area_km2", "geometry"]]


# ── Basin 2: NLDI nwissite (USGS) or IWQIS KMZ (WQS) ─────────────────────────

_IWQIS_BASE = "https://iowawis.org/layers/basins"
_IWQIS_STATIONS_URL = "https://iwqis.iowawis.org/app/inc/inc_get_object.php?id=0&subid=0"
_SEARCH_RADIUS_DEG = 0.03


def _fetch_basin_nwissite(uid: str, timeout: int = 60) -> gpd.GeoDataFrame:
    resp = requests.get(
        f"{_NLDI_BASE}/nwissite/{uid}/basin", params={"f": "json", "simplified": "true"}, timeout=timeout
    )
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        raise ValueError(f"NLDI returned no features for {uid}.")
    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    if gdf.empty:
        raise ValueError(f"NLDI returned empty basin for {uid}.")
    gdf = gdf[["geometry"]].copy()
    gdf["site_uid"] = uid
    gdf["comid"] = None
    gdf["area_km2"] = gdf.to_crs(EQUAL_AREA_CRS).area / 1e6
    return gdf[["site_uid", "comid", "area_km2", "geometry"]]


def _load_iwqis_station_list() -> pd.DataFrame:
    resp = requests.get(_IWQIS_STATIONS_URL, timeout=120)
    resp.raise_for_status()
    stations = re.findall(r"\[(\d+),([-\d.]+),([-\d.]+),\d+", resp.text)
    df = pd.DataFrame(stations, columns=["id", "lat", "lon"])
    df["id"] = df["id"].astype(int)
    df["lat"] = df["lat"].astype(float)
    df["lon"] = df["lon"].astype(float)
    return df


def _try_kmz(station_id: int, timeout: int = 30) -> gpd.GeoDataFrame | None:
    resp = requests.get(f"{_IWQIS_BASE}/p{station_id}.kmz", timeout=timeout)
    if not resp.ok:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            kml_name = next((n for n in zf.namelist() if n.endswith(".kml")), None)
            if not kml_name:
                return None
            gdf = gpd.read_file(io.BytesIO(zf.read(kml_name)), driver="KML")
    except Exception:
        return None
    if gdf.empty:
        return None
    gdf["geometry"] = gdf["geometry"].buffer(0)
    return gdf[~gdf["geometry"].is_empty]


def _fetch_basin_iwqis(uid, lat, lon, station_df, v3_area_km2=None, timeout=30) -> gpd.GeoDataFrame:
    numeric_id = int(uid.replace("WQS", ""))
    dist = ((station_df["lat"] - lat) ** 2 + (station_df["lon"] - lon) ** 2) ** 0.5
    nearby = station_df[dist < _SEARCH_RADIUS_DEG].copy()
    nearby["dist"] = dist[nearby.index]
    nearby = nearby.sort_values("dist")
    candidate_ids = list(nearby["id"])
    if numeric_id not in candidate_ids:
        candidate_ids.append(numeric_id)

    best_gdf, best_score, site_pt = None, float("inf"), Point(lon, lat)
    for sid in candidate_ids:
        raw = _try_kmz(sid, timeout=timeout)
        if raw is None:
            continue
        proj = raw.to_crs(EQUAL_AREA_CRS)
        area = proj.area.sum() / 1e6
        if raw.geometry.union_all().distance(site_pt) > 0.15:
            continue
        centroid = proj.centroid.to_crs("EPSG:4326").iloc[0]
        centroid_dist = ((centroid.y - lat) ** 2 + (centroid.x - lon) ** 2) ** 0.5
        if v3_area_km2 and v3_area_km2 > 1.0:
            ratio = area / v3_area_km2
            if ratio < 0.05 or ratio > 20.0:
                continue
            score = centroid_dist * max(ratio, 1.0 / ratio)
        else:
            score = centroid_dist
        if score < best_score:
            best_score, best_gdf = score, raw
    if best_gdf is None:
        raise ValueError(f"Basin 2 not possible for {uid}.")
    best_gdf = best_gdf[["geometry"]].copy()
    best_gdf["site_uid"] = uid
    best_gdf["comid"] = None
    best_gdf["area_km2"] = best_gdf.to_crs(EQUAL_AREA_CRS).area / 1e6
    return best_gdf[["site_uid", "comid", "area_km2", "geometry"]]


def _compute_basin2(uid, lat, lon, station_df, v3_area_km2, timeout=60) -> gpd.GeoDataFrame:
    """Authoritative basin: NLDI nwissite (USGS) or IWQIS KMZ (WQS)."""
    if uid.startswith("USGS-"):
        try:
            return _fetch_basin_nwissite(uid, timeout=timeout)
        except requests.HTTPError:
            pos = requests.get(
                f"{_NLDI_BASE}/comid/position", params={"coords": f"POINT({lon} {lat})", "f": "json"}, timeout=timeout
            )
            pos.raise_for_status()
            feats = pos.json().get("features", [])
            if not feats:
                raise ValueError(f"No NHD catchment near ({lat}, {lon}) for {uid}.")
            comid = feats[0]["properties"]["comid"]
            resp = requests.get(
                f"{_NLDI_BASE}/comid/{comid}/basin", params={"f": "json", "simplified": "true"}, timeout=timeout
            )
            resp.raise_for_status()
            gdf = gpd.GeoDataFrame.from_features(resp.json()["features"], crs="EPSG:4326")
            if gdf.empty:
                raise ValueError(f"NLDI returned empty basin for COMID {comid} ({uid}).")
            gdf = gdf[["geometry"]].copy()
            gdf["site_uid"] = uid
            gdf["comid"] = comid
            gdf["area_km2"] = gdf.to_crs(EQUAL_AREA_CRS).area / 1e6
            return gdf[["site_uid", "comid", "area_km2", "geometry"]]
    else:
        return _fetch_basin_iwqis(uid, lat, lon, station_df, v3_area_km2, timeout=timeout)


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


# ── helpers: distance, rivers ─────────────────────────────────────────────────


def _dist_km(lat: float, lon: float, gdf: gpd.GeoDataFrame) -> float:
    pt = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326").to_crs(EQUAL_AREA_CRS).geometry.iloc[0]
    poly = gdf.to_crs(EQUAL_AREA_CRS).geometry.union_all()
    return pt.distance(poly) / 1000.0


_RIVERS_URL = "https://naciscdn.org/naturalearth/10m/physical/ne_10m_rivers_lake_centerlines.zip"
_RIVERS_CACHE = _CACHE_DIR / "rivers.gpkg"


def _load_river_geometry() -> gpd.GeoDataFrame:
    """Mississippi + Missouri centerlines (EPSG:5070), cached locally."""
    if not _RIVERS_CACHE.exists():
        print("  Downloading Natural Earth 10m rivers...")
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_DIR / "_ne_rivers_tmp.zip"
        resp = requests.get(_RIVERS_URL, timeout=120)
        resp.raise_for_status()
        tmp.write_bytes(resp.content)
        rivers = gpd.read_file(f"/vsizip/{tmp}")
        rivers[rivers["name"].isin(["Mississippi", "Missouri"])].to_file(_RIVERS_CACHE, driver="GPKG")
        tmp.unlink()
    return gpd.read_file(_RIVERS_CACHE).to_crs(EQUAL_AREA_CRS)


# ── archive helpers ───────────────────────────────────────────────────────────


def _archive_check_parquets(archive: pd.DataFrame) -> list[tuple[str, str]]:
    return [
        (r["site_uid"], r["basin_name"])
        for _, r in archive.iterrows()
        if not (_BASIN_DATA_DIR / r["basin_name"]).exists()
    ]


def _archive_print_divergences(archive: pd.DataFrame) -> int:
    current = pd.read_csv(_PREFERRED_CSV)
    merged = archive.merge(current, on="site_uid", suffixes=("_arch", "_curr"), how="inner")
    diffs: dict[str, list] = {}
    for col in _KEY_COLS:
        a_col, c_col = f"{col}_arch", f"{col}_curr"
        if a_col not in merged.columns or c_col not in merged.columns:
            continue
        for _, r in merged[merged[a_col].astype(str) != merged[c_col].astype(str)].iterrows():
            diffs.setdefault(r["site_uid"], []).append(f"{col}: archive={r[a_col]!r} current={r[c_col]!r}")
    if diffs:
        print(f"\n{len(diffs)} site(s) diverge from the archive:")
        for uid, changes in diffs.items():
            print(f"  {uid}: " + "; ".join(changes))
        print("  -> Review; run --recalculate to rebuild from scratch.")
    return len(diffs)


def _reconstruct_parquet(uid, basin_type, basin_name, lat, lon, direction, station_df) -> bool:
    out = _BASIN_DATA_DIR / basin_name
    try:
        if basin_type == 1:
            gdf = _compute_basin1(uid, lat, lon)
        elif basin_type == 2:
            v3_path = _BASIN_DATA_DIR / f"{uid}_basin3.parquet"
            v3_area = float(gpd.read_parquet(v3_path)["area_km2"].iloc[0]) if v3_path.exists() else None
            gdf = _compute_basin2(uid, lat, lon, station_df, v3_area)
        elif basin_type == 3:
            gdf = _compute_basin3(uid, lat, lon, direction)
            if gdf is None:
                print(f"  {uid}: outside Iowa domain, cannot reconstruct basin3.")
                return False
        else:
            print(f"  {uid}: basin_type {basin_type} cannot be reconstructed automatically.")
            return False
        gdf.to_parquet(out)
        return True
    except Exception as e:
        print(f"  {uid}: reconstruction failed — {e}")
        return False


def _restore_from_archive(archive, meta, direction, station_df) -> None:
    coords = meta.set_index("site_uid")[["latitude", "longitude"]].to_dict("index")
    rows = []
    for _, ar in archive.iterrows():
        uid, basin_name, basin_type = ar["site_uid"], ar["basin_name"], int(ar["basin_type"])
        if not (_BASIN_DATA_DIR / basin_name).exists():
            print(f"  Reconstructing {uid} (basin{basin_type})...")
            if uid not in coords:
                print(f"  {uid}: not in site metadata — skipping.")
                continue
            lat, lon = coords[uid]["latitude"], coords[uid]["longitude"]
            if not _reconstruct_parquet(uid, basin_type, basin_name, lat, lon, direction, station_df):
                fb_type = 2 if uid.startswith("USGS-") and (_BASIN_DATA_DIR / f"{uid}_basin2.parquet").exists() else 1
                fb_name = f"{uid}_basin{fb_type}.parquet"
                if (_BASIN_DATA_DIR / fb_name).exists():
                    print(f"  {uid}: falling back to basin{fb_type} (auto).")
                    ar = ar.copy()
                    ar["basin_name"], ar["basin_type"], ar["selection_mode"], ar["reviewed"] = (
                        fb_name,
                        fb_type,
                        "auto",
                        False,
                    )
                else:
                    print(f"  {uid}: no fallback parquet — skipping.")
                    continue
        rows.append(ar.to_dict())
    pd.DataFrame(rows).to_csv(_PREFERRED_CSV, index=False)
    print(f"Restored preferred_basin.csv with {len(rows)} entries.")


# ── preferred basin metadata ──────────────────────────────────────────────────


def _build_preferred_csv(meta, rivers_proj, archive=None) -> None:
    """Select preferred basin per site, compute distances/areas/flags, write CSV."""
    rivers_union = rivers_proj.geometry.union_all()
    arch_lookup = {} if archive is None else archive.set_index("site_uid").to_dict("index")
    rows = []
    for _, site_row in meta.iterrows():
        uid = site_row["site_uid"]
        lat, lon = float(site_row["latitude"]), float(site_row["longitude"])
        gdf = {
            t: (
                gpd.read_parquet(_BASIN_DATA_DIR / f"{uid}_basin{t}.parquet")
                if (_BASIN_DATA_DIR / f"{uid}_basin{t}.parquet").exists()
                else None
            )
            for t in (1, 2, 3)
        }
        nan = float("nan")
        area = {t: float(gdf[t]["area_km2"].sum()) if gdf[t] is not None else nan for t in (1, 2, 3)}
        dist = {t: _dist_km(lat, lon, gdf[t]) if gdf[t] is not None else nan for t in (1, 2, 3)}

        if uid in arch_lookup:
            ar = arch_lookup[uid]
            basin_type, basin_name = int(ar["basin_type"]), ar["basin_name"]
            sel_mode, reviewed = ar["selection_mode"], ar["reviewed"]
        else:
            basin_type = 2 if (uid.startswith("USGS-") and gdf[2] is not None) else 1
            basin_name, sel_mode, reviewed = f"{uid}_basin{basin_type}.parquet", "auto", False

        if basin_type in (1, 2, 3):
            pref_area = area[basin_type]
        else:
            p4 = _BASIN_DATA_DIR / basin_name
            pref_area = float(gpd.read_parquet(p4)["area_km2"].sum()) if p4.exists() else nan

        pt_proj = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326").to_crs(EQUAL_AREA_CRS).geometry.iloc[0]
        existing_dists = [d for d in dist.values() if not math.isnan(d)]
        rows.append(
            dict(
                site_uid=uid,
                basin_name=basin_name,
                basin_type=basin_type,
                dist_to_1=dist[1],
                dist_to_2=dist[2],
                dist_to_3=dist[3],
                area1=area[1],
                area2=area[2],
                area3=area[3],
                selection_mode=sel_mode,
                reviewed=reviewed,
                flag_area=(not math.isnan(pref_area)) and pref_area > 50_000,
                flag_river=pt_proj.distance(rivers_union) < 1_000,
                flag_not_contained=bool(existing_dists) and all(d > 0 for d in existing_dists),
                flag_basin1_over_basin2=gdf[2] is not None and basin_type == 1,
            )
        )
    df = pd.DataFrame(rows)
    df.to_csv(_PREFERRED_CSV, index=False)
    n_flagged = df[["flag_area", "flag_river", "flag_not_contained", "flag_basin1_over_basin2"]].any(axis=1).sum()
    print(f"preferred_basin.csv: {len(df)} sites, {n_flagged} flagged.")


def _build_type(uids, coords, basin_type, compute, force=False) -> None:
    label = {1: "Basin 1 (NLDI position snap)", 2: "Basin 2 (authoritative)", 3: "Basin 3 (D8 raster)"}[basin_type]
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


def main(api_keys=None, force=False, usgs_only=False, iwqis_only=False, recalculate=False) -> None:
    _BASIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _META_DIR.mkdir(parents=True, exist_ok=True)

    meta = pd.read_csv(_metadata_path())
    coords = meta.set_index("site_uid")[["latitude", "longitude"]].to_dict("index")

    all_uids = sorted(meta["site_uid"])
    if iwqis_only:
        uids = [u for u in all_uids if u.startswith("WQS")]
    elif usgs_only:
        uids = [u for u in all_uids if u.startswith("USGS-")]
    else:
        uids = all_uids

    print("Will attempt to build basin1, basin2 and basin3 for all sites.")
    print("  - basin1: get nearest USGS basin")
    print("  - basin2: lookup by site_uid (DOES NOT EXIST FOR ALL SITES)")
    print("  - basin3: build basin from D8 tif")
    direction = d8.load_direction_array()
    station_df = None
    if not usgs_only:
        print("Fetching IWQIS station registry...")
        station_df = _load_iwqis_station_list()

    # basin3 first (used by basin2 IWQIS validation), then basin1, basin2
    _build_type(uids, coords, 3, lambda uid, lat, lon: _compute_basin3(uid, lat, lon, direction), force=force)
    _build_type(uids, coords, 1, lambda uid, lat, lon: _compute_basin1(uid, lat, lon), force=force)

    def _b2(uid, lat, lon):
        v3 = _BASIN_DATA_DIR / f"{uid}_basin3.parquet"
        v3_area = float(gpd.read_parquet(v3)["area_km2"].iloc[0]) if v3.exists() else None
        return _compute_basin2(uid, lat, lon, station_df, v3_area)

    _build_type(uids, coords, 2, _b2, force=force)

    print("── Building preferred_basin.csv ──")
    archive = pd.read_csv(_ARCHIVE_CSV) if (_ARCHIVE_CSV.exists() and not recalculate) else None
    _build_preferred_csv(meta, _load_river_geometry(), archive=archive)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="Rewrite existing parquets.")
    parser.add_argument("--recalculate", action="store_true", help="Recompute all, ignoring the archive.")
    parser.add_argument("--usgs-only", action="store_true")
    parser.add_argument("--iwqis-only", action="store_true")
    args = parser.parse_args()
    main(
        force=args.force or args.recalculate,
        recalculate=args.recalculate,
        usgs_only=args.usgs_only,
        iwqis_only=args.iwqis_only,
    )
