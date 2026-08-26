"""Upstream accumulation over the NHDPlus network, locally.

WHY LOCAL AND NOT NLDI. Hundreds of sites is hundreds of requests to a service that has already answered 5,063,593 km2 for a Cape Cod estuary. The catchments and the value-added attributes are both on disk, the walk is seconds for the whole cohort, and it agrees with StreamCat's `ws` accumulation by construction because it is the same traversal over the same topology.

THE TRAVERSAL IS `fromnode`/`tonode`, NOT `terminalpa` + `hydroseq`. Reach A is immediately upstream of reach B exactly when `A.tonode == B.fromnode`, so upstream-of-X is a breadth-first walk backwards over that relation. Selecting instead on "same terminal path, higher hydroseq" is tempting and wrong -- it admits every parallel branch draining to the same outlet, which is most of the basin's siblings rather than its ancestors.

VERIFIED AGAINST NHDPlus ITSELF. `vaa.totdasqkm` is the total drainage area NHDPlus computed for each reach, by its own accumulation, independently of ours. Summing `areasqkm` over the walked set reproduces it to within 1% on 388 of 392 sites of the calibration cohort; the verify layer keeps that check running against this machinery.

THE FAILURES ARE THE COASTLINES. A site snapped to a `Coastline` reach has no drainage line to walk: walking upstream from one collects every river that reaches the sea along it -- 124,184 reaches and 251,225 km2 for a site whose own reach drains 20 km2. NLDI does the same thing and returns the same nonsense, which is where its 5M km2 answers come from. `NON_DRAINAGE_FTYPE` (declared beside this machinery) refuses the walk instead.
"""

from __future__ import annotations

import collections

import numpy as np
import pandas as pd

from .. import config

# Properties of the NHDPlus VAA data, declared with the machinery that walks it. A Coastline carries no
# upstream drainage: walking from one is a category error, not an approximation. NHDPlus writes
# -9998/-9999 where a value is UNKNOWN; it is not a small number.
NON_DRAINAGE_FTYPE = {"Coastline"}
VAA_NODATA = -9998.0

_VAA_COLS = ["comid", "fromnode", "tonode", "totdasqkm", "areasqkm", "ftype", "divergence"]


class Network:
    """The AOE flow network, indexed for upstream traversal. Build once, walk many times.

    Holds three dicts and no geometry: adjacency by node, each reach's upstream node, and the per-reach areas. About 1 s to build and ~200 MB, against a flowline layer that never has to be opened.
    """

    def __init__(self, vaa: pd.DataFrame):
        c = vaa.comid.to_numpy()
        self.fromnode = dict(zip(c, vaa.fromnode.to_numpy()))
        self.area = dict(zip(c, vaa.areasqkm.to_numpy()))
        self.totda = dict(zip(c, vaa.totdasqkm.to_numpy()))
        self.ftype = dict(zip(c, vaa.ftype.to_numpy()))
        self.by_tonode: dict[int, list[int]] = collections.defaultdict(list)
        for cid, tn in zip(c, vaa.tonode.to_numpy()):
            self.by_tonode[tn].append(cid)

    @classmethod
    def load(cls) -> "Network":
        return cls(pd.read_parquet(config.ACQ_NETWORK / "vaa.parquet", columns=_VAA_COLS))

    def drains(self, comid) -> bool:
        """False for a reach that carries no upstream drainage -- see `NON_DRAINAGE_FTYPE`."""
        return str(self.ftype.get(int(comid), "")) not in NON_DRAINAGE_FTYPE

    def upstream(self, comid, max_reaches: int | None = None) -> set[int]:
        """Every reach at or above `comid`, by breadth-first walk over `tonode -> fromnode`.

        `max_reaches` defaults to the size of the network, which makes it a guard against an impossible condition rather than a size limit -- `seen` is a set, so the walk terminates on its own, and a basin legitimately cannot contain more reaches than the AOE holds. It must NOT be set to a "reasonable basin size": the Mississippi at Baton Rouge drains 2.88M km2 and touches most of the AOE, so any figure chosen from intuition rejects the largest real basins first.
        """
        cap = max_reaches if max_reaches is not None else len(self.fromnode)
        start = int(comid)
        seen, stack = {start}, [start]
        while stack:
            c = stack.pop()
            fn = self.fromnode.get(c)
            if fn is None:
                continue
            for u in self.by_tonode.get(fn, ()):
                u = int(u)
                if u not in seen:
                    seen.add(u)
                    stack.append(u)
                    if len(seen) > cap:
                        raise RuntimeError(f"upstream walk from {start} exceeded {cap:,} reaches -- "
                                           f"more than the whole network, so the topology is cyclic")
        return seen

    def accumulate(self, comid) -> tuple[set[int], float, float, str]:
        """`(reach set, summed areasqkm, vaa totdasqkm, status)` for one reach.

        Status is `ok`, `no_drainage_reach` where the reach carries no upstream drainage, or `no_comid`.
        """
        if comid is None or pd.isna(comid):
            return set(), np.nan, np.nan, "no_comid"
        cid = int(comid)
        if not self.drains(cid):
            return set(), np.nan, float(self.totda.get(cid, np.nan)), "no_drainage_reach"
        s = self.upstream(cid)
        acc = float(np.nansum([self.area.get(x, 0.0) for x in s]))
        return s, acc, float(self.totda.get(cid, np.nan)), "ok"


# Turning a reach set into a polygon is `basins.Catchments` -- geometry is the delineation's business, and the layer is read once for the whole cohort rather than per basin. This module stays topology and area only.
