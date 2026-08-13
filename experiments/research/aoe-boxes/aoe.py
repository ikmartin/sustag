"""Flow-distance areas of interest: network traversal and clustering.

The deployment question is "how far along the river network from a real sensor are we still willing to place a virtual one?" That distance -- `max_dist_from_sensor_km` -- is what defines the area worth collecting data for, and it is what clusters the sensors: two sensors belong to the same region when their reachable reach sets intersect. Straight-line clustering has no such meaning, and its `eps` is a free parameter with nothing behind it.

WHY NOT THE LOCAL D8 MACHINERY. `src/data/d8.py` is bounded by `flowdir_15s.tif`, an 18x12 degree Midwest window, and traverses UPSTREAM only. The sensor clusters this has to cover include California, Florida, the Chesapeake and the Gulf. So the network comes from national NHDPlus VAA instead, and reach coordinates from the NWM retrospective's `latitude`/`longitude` arrays -- VAA carries topology but no geometry, and pulling flowline geometry for 2.7M reaches to draw a map would be absurd.

Distances are along-network. Downstream is a walk down `hydroseq -> dnhydroseq`; upstream is the reverse expansion. Both accumulate `lengthkm`, and both run from the same seed, so "within D" means within D in either direction.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

EQUAL_AREA = "EPSG:5070"


@dataclass
class Network:
    """NHDPlus V2 reach network with coordinates, indexed positionally.

    comid/lat/lon/order/lengthkm are parallel arrays. dn[i] is the index of i's downstream reach, or -1 at a terminal. up_off/up_idx are CSR reverse adjacency: the upstream neighbours of i are up_idx[up_off[i]:up_off[i+1]].
    """

    comid: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    order: np.ndarray
    lengthkm: np.ndarray
    dn: np.ndarray
    up_off: np.ndarray
    up_idx: np.ndarray

    @property
    def n(self) -> int:
        return len(self.comid)

    def index_of(self, comids) -> np.ndarray:
        """Positional index of each comid; -1 where absent."""
        pos = np.searchsorted(self.comid, comids)
        pos = np.clip(pos, 0, self.n - 1)
        ok = self.comid[pos] == np.asarray(comids)
        return np.where(ok, pos, -1)

    def reachable(self, seed: int, max_km: float, min_order: int = 1) -> tuple[np.ndarray, np.ndarray]:
        """Reach indices within `max_km` of along-network distance from `seed`, and those distances.

        Dijkstra rather than plain BFS because reach lengths vary and divergences make the graph not quite a tree. Bounded by max_km, so the frontier stays small. `min_order` filters the RESULT, not the traversal -- a low-order reach can still be the only path to a high-order one.
        """
        dist = {seed: 0.0}
        pq = [(0.0, seed)]
        while pq:
            d, i = heapq.heappop(pq)
            if d > dist.get(i, np.inf):
                continue
            nbrs = []
            if self.dn[i] >= 0:
                nbrs.append(self.dn[i])
            nbrs.extend(self.up_idx[self.up_off[i]:self.up_off[i + 1]].tolist())
            for j in nbrs:
                nd = d + float(self.lengthkm[j])
                if nd <= max_km and nd < dist.get(j, np.inf):
                    dist[j] = nd
                    heapq.heappush(pq, (nd, j))
        idx = np.fromiter(dist.keys(), dtype=np.int64, count=len(dist))
        dst = np.fromiter(dist.values(), dtype=np.float64, count=len(dist))
        if min_order > 1:
            keep = self.order[idx] >= min_order
            idx, dst = idx[keep], dst[keep]
        return idx, dst


def build_network(vaa_path: Path, xy_path: Path) -> Network:
    """Join national VAA topology to NWM reach coordinates, keeping the intersection."""
    vaa = pd.read_parquet(vaa_path, columns=["comid", "hydroseq", "dnhydroseq", "lengthkm", "streamorde"])
    xy = pd.read_parquet(xy_path)  # comid, lat, lon, order
    d = vaa.merge(xy[["comid", "lat", "lon"]], on="comid", how="inner").sort_values("comid").reset_index(drop=True)

    comid = d.comid.to_numpy(np.int64)
    hydro = d.hydroseq.to_numpy(np.int64)
    dnhyd = d.dnhydroseq.to_numpy(np.int64)

    # downstream link: dnhydroseq -> the row whose hydroseq matches. 0 means terminal.
    order_h = np.argsort(hydro)
    hs = hydro[order_h]
    pos = np.searchsorted(hs, dnhyd)
    pos_c = np.clip(pos, 0, len(hs) - 1)
    hit = (hs[pos_c] == dnhyd) & (dnhyd != 0)
    dn = np.where(hit, order_h[pos_c], -1).astype(np.int64)

    # CSR reverse adjacency
    src = np.flatnonzero(dn >= 0)
    tgt = dn[src]
    o = np.argsort(tgt, kind="stable")
    up_idx = src[o].astype(np.int64)
    counts = np.bincount(tgt, minlength=len(comid))
    up_off = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)

    return Network(comid=comid, lat=d.lat.to_numpy(np.float64), lon=d.lon.to_numpy(np.float64),
                   order=d.streamorde.to_numpy(np.int32), lengthkm=d.lengthkm.to_numpy(np.float64),
                   dn=dn, up_off=up_off, up_idx=up_idx)


def snap(net: Network, lat, lon, max_km: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Nearest reach to each point, in equal-area metres. Returns (index, distance_km); index -1 beyond max_km."""
    import geopandas as gpd
    from scipy.spatial import cKDTree

    reaches = gpd.GeoSeries(gpd.points_from_xy(net.lon, net.lat), crs=4326).to_crs(EQUAL_AREA)
    pts = gpd.GeoSeries(gpd.points_from_xy(lon, lat), crs=4326).to_crs(EQUAL_AREA)
    tree = cKDTree(np.c_[reaches.x, reaches.y])
    d, i = tree.query(np.c_[pts.x, pts.y], k=1)
    km = d / 1000.0
    return np.where(km <= max_km, i, -1), km


def clusters(reach_sets: list[np.ndarray], max_boxes: int | None = None) -> list[list[int]]:
    """Group sensors whose reachable reach sets intersect.

    Every sensor lands in exactly one group, singletons included -- nothing is dropped as noise, which is how the straight-line version silently lost a Texas sensor. `max_boxes` SELECTS the largest groups; it never merges, because merging distant groups (California with Washington) spans empty country and is strictly worse than dropping one.
    """
    import networkx as nx

    owner: dict[int, int] = {}
    g = nx.Graph()
    g.add_nodes_from(range(len(reach_sets)))
    for s, reaches in enumerate(reach_sets):
        for r in reaches.tolist():
            if r in owner:
                g.add_edge(owner[r], s)
            else:
                owner[r] = s
    out = sorted((sorted(c) for c in nx.connected_components(g)), key=len, reverse=True)
    return out[:max_boxes] if max_boxes else out
