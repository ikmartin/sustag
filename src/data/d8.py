"""D8 flow-direction raster primitives (shared by build + runtime).

The IWQIS 500 m flow-direction raster (direction500m.png) and its geo-referencing. Both the
basin builder (basin3 BFS delineation) and the runtime read path (site_view's dist_to_sensor
flow field) need these, so by the sorting rule they live on the DATA side -- src/build may
import this, but nothing here imports src/build.

Each cell stores its downslope neighbour as a numeric-keypad direction (7 8 9 / 4 5 6 / 1 2 3;
0 = nodata). NEIGHBOR_CHECKS maps a neighbour offset to the direction code an UPSTREAM neighbour
must hold to drain into the centre cell.
"""

import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Geod
from rasterio.transform import Affine

_DATA = Path(__file__).resolve().parent               # src/data
_RASTER = _DATA / "raw" / "basins" / "cache" / "direction500m.png"
_RASTER_URL = "https://iwqis.iowawis.org/app/inc/watershed/direction500m.png"

# ── geo-referencing of the 500 m raster (verbatim from make_basins) ────────────
W = 1741
H = 1057
RES = 0.004167
LON_UL = RES * (0 - 0.5) - 97.154167 - RES / 2
LAT_UL = 44.53785 + (0 - 0.5) * (-RES) + RES / 2
TRANSFORM = Affine(RES, 0, LON_UL, 0, -RES, LAT_UL)

# neighbour offset (dc, dr) -> direction code an upstream neighbour must hold to flow into centre
NEIGHBOR_CHECKS = [
    (-1, -1, 3), (0, -1, 2), (+1, -1, 1),
    (-1,  0, 6),              (+1,  0, 4),
    (-1, +1, 9), (0, +1, 8), (+1, +1, 7),
]

_DIRECTION: np.ndarray | None = None


def load_direction_array() -> np.ndarray:
    """The D8 direction raster as an (H, W) uint8 array (cached in-process).

    Reads src/data/raw/basins/cache/direction500m.png, else the legacy cache, else downloads it.
    """
    global _DIRECTION
    if _DIRECTION is not None:
        return _DIRECTION

    if not _RASTER.exists():
        import requests
        print("  Downloading direction500m.png...")
        _RASTER.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(_RASTER_URL, timeout=120)
        resp.raise_for_status()
        _RASTER.write_bytes(resp.content)

    direction = np.array(Image.open(_RASTER).convert("RGB"))[:, :, 0].astype(np.uint8)
    assert direction.shape == (H, W), f"Unexpected raster shape: {direction.shape}"
    _DIRECTION = direction
    return direction


def ll_to_image_pixel(lat: float, lon: float) -> tuple[int, int]:
    """(lat, lon) -> (col, row) pixel index in the D8 raster."""
    col = int((lon + 97.154167) / RES + 0.5)
    row = int((44.53785 - lat) / RES + 0.5)
    return col, row


# ── flow distance: cell -> sensor outlet, along the D8 drainage network ────────
# Ported from make_grid. Walking UP the D8 tree from a sensor's snapped pour point gives the
# flow distance to every upstream cell in one pass. Used by site_view's dist_to_sensor.

_GEOD = Geod(ellps="WGS84")
# direction code -> downstream (dcol, drow); inverse of NEIGHBOR_CHECKS.
_D8_STEP = {7: (-1, -1), 8: (0, -1), 9: (1, -1), 4: (-1, 0), 6: (1, 0), 1: (-1, 1), 2: (0, 1), 3: (1, 1)}
_OUTLET_SNAP_RADIUS = 10   # cells (~5 km) searched for the main-stem outlet
_OUTLET_ACC_FRAC = 0.5     # min fraction of window-max accumulation to count as main stem
NODE_SNAP_RADIUS = 4       # cells (~2 km) to recover a node straddling the basin divide

_ACCUM: np.ndarray | None = None
_FLOW_FIELD_CACHE: dict[tuple, np.ndarray] = {}


def _flow_accumulation(direction: np.ndarray) -> np.ndarray:
    """Upstream-cell count for every D8 cell (computed once, cached grid-wide)."""
    global _ACCUM
    if _ACCUM is not None:
        return _ACCUM
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


def _build_flow_field(direction: np.ndarray, col: int, row: int) -> np.ndarray:
    """Metres-to-outlet for every cell draining to (col, row)."""
    dist = np.full((H, W), np.nan)
    dist[row, col] = 0.0
    q = deque([(col, row)])
    while q:
        cx, cy = q.popleft()
        clon, clat = TRANSFORM * (cx + 0.5, cy + 0.5)
        base = dist[cy, cx]
        for dx, dy, expected in NEIGHBOR_CHECKS:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < W and 0 <= ny < H and np.isnan(dist[ny, nx]) and direction[ny, nx] == expected:
                nlon, nlat = TRANSFORM * (nx + 0.5, ny + 0.5)
                _, _, seg = _GEOD.inv(clon, clat, nlon, nlat)
                dist[ny, nx] = base + seg
                q.append((nx, ny))
    return dist


def flow_distance_field_ll(lat: float, lon: float) -> np.ndarray:
    """Per-cell flow distance (m) to the outlet of the sensor at (lat, lon). Cached per rounded
    (lat, lon). Raises ValueError if the sensor is outside the D8 raster extent (Iowa)."""
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
