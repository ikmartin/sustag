"""D8 flow-direction raster primitives (shared by build + runtime).

A hydrologically-conditioned D8 flow-direction raster + its geo-referencing. Both the basin builder (basin3 BFS delineation) and the runtime read path (site_view's dist_to_sensor flow field) need these, so by the sorting rule they live on the DATA side -- src/build may import this, but nothing here imports src/build.

Two sources are supported, preferred in this order:

  1. HydroSHEDS v1 15 arc-second Drainage Direction (DIR), clipped to the covariate_bbox, as a
     GeoTIFF at flowdir_15s.tif. 15 arc-sec = 463.83 m -- the SAME resolution as the legacy Iowa
     raster, so no resampling. ESRI D8 encoding (E=1, SE=2, S=4, SW=8, W=16, NW=32, N=64, NE=128;
     0=ocean outlet, 255=inland sink). This is the wide-coverage source for out-of-Iowa basins.
     Obtain it by downloading the North America 15s DIR from https://www.hydrosheds.org/ and
     clipping to the region (e.g. `gdalwarp -te <minlon> <minlat> <maxlon> <maxlat>`).

  2. The legacy IWQIS 500 m Iowa raster (direction500m.png), auto-downloaded. Numeric-keypad D8
     encoding (7 8 9 / 4 5 6 / 1 2 3; 0=nodata). Iowa-only -- used as a fallback until the
     HydroSHEDS raster is provided, so the existing Iowa pipeline keeps working.

The public surface (load_direction_array, H, W, TRANSFORM, NEIGHBOR_CHECKS, ll_to_image_pixel, flow_distance_field_ll, sample_field) is the same regardless of source; the encoding tables and geo-referencing are selected at load time.
"""

import sys
from collections import deque
from functools import lru_cache
from pathlib import Path

import numpy as np
from pyproj import Geod
from rasterio.transform import Affine

_DATA = Path(__file__).resolve().parent  # src/data
_CACHE_DIR = _DATA / "raw" / "basins" / "cache"
_DERIVED_CACHE = _DATA / "cache"  # gitignored, regenerable derived artifacts (flow accumulation)
_RASTER_HS = _CACHE_DIR / "flowdir_15s.tif"  # HydroSHEDS 15s DIR clipped to covariate_bbox (preferred)
_RASTER_LEGACY = _CACHE_DIR / "direction500m.png"  # legacy Iowa 500 m raster (fallback)
_RASTER_LEGACY_URL = "https://iwqis.iowawis.org/app/inc/watershed/direction500m.png"

# ── D8 encodings ───────────────────────────────────────────────────────────────
# Each maps: code -> downstream (dcol, drow); and neighbour offset (dc, dr) -> the code an UPSTREAM
# neighbour there must hold to flow INTO the centre cell (its downstream step == (-dc, -dr)).
_ESRI_STEP = {1: (1, 0), 2: (1, 1), 4: (0, 1), 8: (-1, 1), 16: (-1, 0), 32: (-1, -1), 64: (0, -1), 128: (1, -1)}
_ESRI_CHECKS = [
    (-1, -1, 2), (0, -1, 4), (1, -1, 8),
    (-1,  0, 1),             (1,  0, 16),
    (-1,  1, 128), (0, 1, 64), (1, 1, 32),
]
_IWQIS_STEP = {7: (-1, -1), 8: (0, -1), 9: (1, -1), 4: (-1, 0), 6: (1, 0), 1: (-1, 1), 2: (0, 1), 3: (1, 1)}
_IWQIS_CHECKS = [
    (-1, -1, 3), (0, -1, 2), (+1, -1, 1),
    (-1,  0, 6),             (+1,  0, 4),
    (-1, +1, 9), (0, +1, 8), (+1, +1, 7),
]

# legacy IWQIS 500 m geo-referencing (verbatim from make_basins), used only for that raster
_LEG_W, _LEG_H, _LEG_RES = 1741, 1057, 0.004167
_LEG_LON_UL = _LEG_RES * (0 - 0.5) - 97.154167 - _LEG_RES / 2
_LEG_LAT_UL = 44.53785 + (0 - 0.5) * (-_LEG_RES) + _LEG_RES / 2

# ── module state, set at load time ─────────────────────────────────────────────
_DIRECTION: np.ndarray | None = None
_SOURCE: str | None = None  # "hydrosheds" | "legacy"
W: int | None = None
H: int | None = None
TRANSFORM: Affine | None = None
_D8_STEP: dict = {}
NEIGHBOR_CHECKS: list = []
NODATA: set = set()

_GEOD = Geod(ellps="WGS84")
_OUTLET_SNAP_RADIUS = 10   # cells (~5 km) searched for the main-stem outlet
_OUTLET_ACC_FRAC = 0.5     # min fraction of window-max accumulation to count as main stem
NODE_SNAP_RADIUS = 4       # cells (~2 km) to recover a node straddling the basin divide

_ACCUM: np.ndarray | None = None
_FLOW_FIELD_CACHE: dict[tuple, np.ndarray] = {}


def load_direction_array() -> np.ndarray:
    """The D8 direction raster as an (H, W) uint8 array (cached in-process). Prefers the HydroSHEDS GeoTIFF; falls back to the legacy Iowa PNG (auto-downloaded). Sets W/H/TRANSFORM and the encoding tables (_D8_STEP/NEIGHBOR_CHECKS/NODATA) to match the chosen source."""
    global _DIRECTION, _SOURCE, W, H, TRANSFORM, _D8_STEP, NEIGHBOR_CHECKS, NODATA
    if _DIRECTION is not None:
        return _DIRECTION

    if _RASTER_HS.exists():
        import rasterio

        with rasterio.open(_RASTER_HS) as ds:
            arr = ds.read(1)
            TRANSFORM = ds.transform
        _DIRECTION = arr.astype(np.uint8, copy=False)
        _SOURCE = "hydrosheds"
        _D8_STEP, NEIGHBOR_CHECKS, NODATA = _ESRI_STEP, _ESRI_CHECKS, {0, 255}
    else:
        if not _RASTER_LEGACY.exists():
            import requests

            print("  Downloading direction500m.png (legacy Iowa D8 raster)...")
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            resp = requests.get(_RASTER_LEGACY_URL, timeout=120)
            resp.raise_for_status()
            _RASTER_LEGACY.write_bytes(resp.content)
        from PIL import Image

        _DIRECTION = np.array(Image.open(_RASTER_LEGACY).convert("RGB"))[:, :, 0].astype(np.uint8)
        TRANSFORM = Affine(_LEG_RES, 0, _LEG_LON_UL, 0, -_LEG_RES, _LEG_LAT_UL)
        _SOURCE = "legacy"
        _D8_STEP, NEIGHBOR_CHECKS, NODATA = _IWQIS_STEP, _IWQIS_CHECKS, {0}

    H, W = _DIRECTION.shape
    return _DIRECTION


def ll_to_image_pixel(lat: float, lon: float) -> tuple[int, int]:
    """(lat, lon) -> (col, row) pixel index. Legacy uses its original centre-rounding formula; HydroSHEDS uses the raster affine (floor of the inverse transform)."""
    if TRANSFORM is None:
        load_direction_array()
    if _SOURCE == "legacy":
        col = int((lon + 97.154167) / _LEG_RES + 0.5)
        row = int((44.53785 - lat) / _LEG_RES + 0.5)
        return col, row
    fcol, frow = ~TRANSFORM * (lon, lat)
    return int(np.floor(fcol)), int(np.floor(frow))


# ── flow distance: cell -> sensor outlet, along the D8 drainage network ────────
# Walking UP the D8 tree from a sensor's snapped pour point gives the flow distance to every
# upstream cell in one pass. Source-agnostic: uses TRANSFORM for cell centres and the loaded
# encoding tables for topology.


def _accum_cache_file() -> Path:
    """On-disk path for the cached flow accumulation, keyed on the identity of the raster it derives from. A new or edited raster yields a new filename, so a stale array can never be read back."""
    src = _RASTER_HS if _SOURCE == "hydrosheds" else _RASTER_LEGACY
    st = src.stat()
    return _DERIVED_CACHE / f"d8_accum_{_SOURCE}_{H}x{W}_{st.st_size}_{st.st_mtime_ns}.npy"


def _flow_accumulation(direction: np.ndarray) -> np.ndarray:
    """Upstream-cell count for every D8 cell (computed once, cached grid-wide and on disk).

    A Kahn topological sort over all H*W cells in a pure-Python loop -- ~38 s for the 2880x4320 HydroSHEDS raster. It is a pure function of the direction raster, so it is memoized to src/data/cache/ and re-read (~0.1 s) on later processes. That matters most for the deploy/widget path, which delineates an arbitrary pin and so can never precompute anything per site, yet still pays this before its first prediction.
    """
    global _ACCUM
    if _ACCUM is not None:
        return _ACCUM

    cache = _accum_cache_file()
    if cache.exists():
        try:
            _ACCUM = np.load(cache)
            return _ACCUM
        except Exception as e:
            print(f"  [warn] unreadable flow-accumulation cache {cache.name} ({e}); recomputing.")

    down = np.full((H, W, 2), -1, np.int32)
    indeg = np.zeros((H, W), np.int32)
    for code, (dc, dr) in _D8_STEP.items():
        rs, cs = np.where(direction == code)
        nc, nr = cs + dc, rs + dr
        ok = (nc >= 0) & (nc < W) & (nr >= 0) & (nr < H)
        down[rs[ok], cs[ok], 0] = nc[ok]
        down[rs[ok], cs[ok], 1] = nr[ok]
        np.add.at(indeg, (nr[ok], nc[ok]), 1)
    acc = np.ones((H, W), np.int64)
    q = deque(zip(*np.where(indeg == 0)))
    while q:
        r, c = q.popleft()
        dc, dr = down[r, c]
        if dc < 0:
            continue
        acc[dr, dc] += acc[r, c]
        indeg[dr, dc] -= 1
        if indeg[dr, dc] == 0:
            q.append((dr, dc))
    _ACCUM = acc
    try:
        _DERIVED_CACHE.mkdir(parents=True, exist_ok=True)
        for old in _DERIVED_CACHE.glob(f"d8_accum_{_SOURCE}_*.npy"):
            old.unlink()  # a superseded raster's array; the filename key makes these dead weight
        np.save(cache, acc)
    except Exception as e:
        print(f"  [warn] could not cache flow accumulation ({e}); it will be recomputed next process.")
    return acc


def _snap_outlet(direction: np.ndarray, col: int, row: int) -> tuple[int, int]:
    """Snap a sensor pixel to the nearest main-stem cell (pour-point snapping)."""
    acc = _flow_accumulation(direction)
    R = _OUTLET_SNAP_RADIUS
    c0, c1 = max(0, col - R), min(W, col + R + 1)
    r0, r1 = max(0, row - R), min(H, row + R + 1)
    sub = acc[r0:r1, c0:c1]
    rs, cs = np.where(sub >= _OUTLET_ACC_FRAC * sub.max())
    cc, rr = cs + c0, rs + r0
    j = int(np.argmin((cc - col) ** 2 + (rr - row) ** 2))
    return int(cc[j]), int(rr[j])


@lru_cache(maxsize=1)
def _hop_lengths() -> np.ndarray:
    """Hop length in metres for each NEIGHBOR_CHECKS direction out of each raster row: H rows x 8 directions.

    Both supported rasters are north-up and axis-aligned (TRANSFORM.b == TRANSFORM.d == 0), so a cell centre's latitude is a function of its row alone and a neighbour's longitude offset is a function of dx alone. Geodesic distance on an ellipsoid of revolution is invariant under rotation in longitude, so a hop's length depends only on (row, dx, dy) -- never on the column. That collapses the BFS's per-cell-per-neighbour geodesic solve into one table of H x 8 entries (23,040 for HydroSHEDS) built once with a single vectorized Geod.inv.

    The table is not bit-identical to solving at each cell's own longitude: pyproj's solver rounds differently at different absolute longitudes, giving deviations up to ~1e-9 m per hop (relative ~3e-12, and ~4e-7 m accumulated over the longest observed flow path).
    """
    if TRANSFORM is None:
        load_direction_array()
    if abs(TRANSFORM.b) > 1e-12 or abs(TRANSFORM.d) > 1e-12:
        raise ValueError(f"_hop_lengths assumes a north-up, axis-aligned transform; got b={TRANSFORM.b}, d={TRANSFORM.d}.")

    rows = np.arange(H, dtype=float)
    a, c, e, f = TRANSFORM.a, TRANSFORM.c, TRANSFORM.e, TRANSFORM.f
    clon = np.full(H, a * 0.5 + c)
    clat = e * (rows + 0.5) + f
    table = np.empty((H, len(NEIGHBOR_CHECKS)))
    for k, (dx, dy, _) in enumerate(NEIGHBOR_CHECKS):
        nlon = np.full(H, a * (dx + 0.5) + c)
        nlat = e * (rows + dy + 0.5) + f
        _, _, seg = _GEOD.inv(clon, clat, nlon, nlat)
        table[:, k] = seg
    table.setflags(write=False)
    return table


def _build_flow_field(direction: np.ndarray, col: int, row: int) -> np.ndarray:
    """Metres-to-outlet for every cell draining to (col, row).

    Expands the upstream frontier one BFS level at a time, whole level vectorized. Every D8 cell drains to exactly one downstream cell, so the "flows into" relation is a forest and the upstream walk from an outlet is a tree: each cell is reached exactly once, by a unique parent. Visit order therefore cannot affect any distance, which is what lets the level expansion replace the per-cell deque loop without changing results.
    """
    dist = np.full((H, W), np.nan)
    dist[row, col] = 0.0
    hop = _hop_lengths()
    fr_r = np.array([row], dtype=np.intp)
    fr_c = np.array([col], dtype=np.intp)

    while fr_r.size:
        base = dist[fr_r, fr_c]
        nxt_r, nxt_c = [], []
        for k, (dx, dy, expected) in enumerate(NEIGHBOR_CHECKS):
            nr, nc = fr_r + dy, fr_c + dx
            on = (nr >= 0) & (nr < H) & (nc >= 0) & (nc < W)
            nr, nc = nr[on], nc[on]
            if nr.size == 0:
                continue
            take = (direction[nr, nc] == expected) & np.isnan(dist[nr, nc])
            if not take.any():
                continue
            nr, nc = nr[take], nc[take]
            dist[nr, nc] = base[on][take] + hop[fr_r[on][take], k]
            nxt_r.append(nr)
            nxt_c.append(nc)
        if not nxt_r:
            break
        fr_r = np.concatenate(nxt_r)
        fr_c = np.concatenate(nxt_c)
    return dist


def flow_distance_field_ll(lat: float, lon: float) -> np.ndarray:
    """Per-cell flow distance (m) to the outlet of the sensor at (lat, lon). Cached per rounded (lat, lon). Raises ValueError if the sensor is outside the raster extent."""
    key = (round(lat, 6), round(lon, 6))
    if key in _FLOW_FIELD_CACHE:
        return _FLOW_FIELD_CACHE[key]
    direction = load_direction_array()
    col, row = ll_to_image_pixel(lat, lon)
    if not (0 <= col < W and 0 <= row < H):
        raise ValueError(f"sensor ({lat}, {lon}) is outside the D8 raster extent.")
    ocol, orow = _snap_outlet(direction, col, row)
    field = _build_flow_field(direction, ocol, orow)
    _FLOW_FIELD_CACHE[key] = field
    return field


def sample_field(field: np.ndarray, col: int, row: int) -> float:
    """Flow distance at a node pixel, recovering basin-divide straddlers via a small search."""
    if 0 <= col < W and 0 <= row < H and np.isfinite(field[row, col]):
        return float(field[row, col])
    R = NODE_SNAP_RADIUS
    c0, c1 = max(0, col - R), min(W, col + R + 1)
    r0, r1 = max(0, row - R), min(H, row + R + 1)
    sub = field[r0:r1, c0:c1]
    fin = np.isfinite(sub)
    if not fin.any():
        return float("nan")
    rs, cs = np.where(fin)
    j = int(np.argmin((cs + c0 - col) ** 2 + (rs + r0 - row) ** 2))
    return float(sub[rs[j], cs[j]])
