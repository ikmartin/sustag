"""Step 1: build the pairwise table + the power check.

For every site pair with >= MIN_OVERLAP concurrent observed days, compute:
  - rho_raw    : Spearman of daily-max nitrate (concurrent days)
  - rho_resid  : Spearman of the statewide-mean-removed anomaly (the decisive "beyond rest_of_state")
  - euclid_km  : sensor-to-sensor great-circle distance
  - connected  : basins hydrologically nested (same containment component, size > 1)
  - direct     : a direct containment edge (one nested immediately in the other)
  - n_hops     : shortest path in the containment graph (NaN if disconnected)
Writes pairs.parquet. Prints the POWER CHECK (how many connected pairs even exist) up front -- if that is tiny, the graph premise is underpowered on Iowa data alone, itself a finding.
"""

import networkx as nx
import numpy as np
import pandas as pd

import common as C

wide = C.build_wide()
print(f"sites with usable daily-nitrate: {wide.shape[1]}; date span {wide.index.min().date()}..{wide.index.max().date()}")

raw_corr = wide.corr(method="spearman", min_periods=C.MIN_OVERLAP)
resid_corr = C.residual_wide(wide).corr(method="spearman", min_periods=C.MIN_OVERLAP)
ovl = C.overlap_counts(wide)
ug, comp, dg = C.containment_info()
loc = C.locations()
area = C.basin_areas()

sites = list(wide.columns)
# containment component sizes (a component of size > 1 == a real nesting cluster)
comp_size = pd.Series(comp).value_counts().to_dict()

rows = []
for i, a in enumerate(sites):
    for b in sites[i + 1 :]:
        n = int(ovl.loc[a, b])
        if n < C.MIN_OVERLAP:
            continue
        connected = a in comp and b in comp and comp[a] == comp[b] and comp_size.get(comp[a], 1) > 1
        direct = ug.has_edge(a, b)
        hops = np.nan
        if connected:
            try:
                hops = nx.shortest_path_length(ug, a, b)
            except nx.NetworkXNoPath:
                connected = False
        eu = C.haversine_km(*loc[a], *loc[b]) if a in loc and b in loc else np.nan
        # upstream = the smaller-basin member (nested deeper); informational (rho is symmetric)
        up = a if (area.get(a, np.inf) <= area.get(b, np.inf)) else b
        rows.append(
            dict(a=a, b=b, overlap_days=n, euclid_km=eu, connected=connected, direct=direct,
                 n_hops=hops, upstream=up, rho_raw=raw_corr.loc[a, b], rho_resid=resid_corr.loc[a, b])
        )

pairs = pd.DataFrame(rows)
pairs.to_parquet(C.HERE / "pairs.parquet")

# ── power check ────────────────────────────────────────────────────────────────
n_pairs = len(pairs)
n_conn = int(pairs.connected.sum())
n_direct = int(pairs.direct.sum())
print("\n===== POWER CHECK =====")
print(f"pairs with >= {C.MIN_OVERLAP}d overlap : {n_pairs}")
print(f"  hydrologically CONNECTED (nested)  : {n_conn}")
print(f"  of which DIRECT containment edges  : {n_direct}")
print(f"  DISCONNECTED (different basins)    : {n_pairs - n_conn}")
print(f"containment components of size > 1   : {sum(1 for v in comp_size.values() if v > 1)}")
if n_conn > 0:
    print("hop distribution among connected pairs:", pairs.loc[pairs.connected, "n_hops"].value_counts().sort_index().to_dict())
print(f"\nwrote {C.HERE/'pairs.parquet'}  ({n_pairs} rows)")
