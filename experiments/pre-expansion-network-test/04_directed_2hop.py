"""Follow-up A: does 2-hop carry signal when it is a genuine CONTAINMENT CHAIN (one basin transitively inside the other, i.e. a directed upstream->downstream path) rather than two SIBLING tributaries under a shared parent?

In step 02 "2-hop" lumped both. The containment graph is DIRECTED (child -> parent = child's sensor inside parent's basin = child upstream). So:
  nested   : a directed path exists between the pair (one basin contains the other)  -> real connection
  sibling  : connected undirected but NO directed path (share a downstream parent, neither contains other)
We split 2-hop by this and compare residual co-movement. Prediction: nested 2-hop > sibling 2-hop, but weaker than 1-hop because the extra hop means a bigger area ratio (more dilution).
"""

import networkx as nx
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import common as C

pairs = pd.read_parquet(C.HERE / "pairs.parquet")
ug, comp, dg = C.containment_info()
area = C.basin_areas()


def relation(a, b):
    if not (a in comp and b in comp and comp[a] == comp[b] and pd.notna(area.get(a)) ):
        pass
    same_comp = a in comp and b in comp and comp[a] == comp[b]
    if not same_comp:
        return "disconnected"
    # size of that component (singletons are their own component -> handled by same_comp on 2 distinct sites)
    if nx.has_path(dg, a, b) or nx.has_path(dg, b, a):
        return "nested"  # directed reachable: one basin transitively contains the other
    return "sibling"  # common downstream parent, neither contains the other


pairs["rel"] = [relation(a, b) for a, b in zip(pairs.a, pairs.b)]
pairs["area_ratio"] = [
    max(area.get(a, np.nan), area.get(b, np.nan)) / max(1e-9, min(area.get(a, np.nan), area.get(b, np.nan)))
    for a, b in zip(pairs.a, pairs.b)
]

disc_base = pairs.loc[pairs.rel == "disconnected", "rho_resid"].mean()


def line(sub, label):
    print(f"  {label:28s} n={len(sub):4d}  resid_rho={sub.rho_resid.mean():+.3f}  "
          f"raw_rho={sub.rho_raw.mean():+.3f}  median_area_ratio={sub.area_ratio.median():6.1f}  "
          f"excess={sub.rho_resid.mean()-disc_base:+.3f}")


print(f"disconnected baseline residual rho = {disc_base:+.3f}\n")
print("=== by relation x hop ===")
for h in [1.0, 2.0, 3.0]:
    for r in ["nested", "sibling"]:
        sub = pairs[(pairs.n_hops == h) & (pairs.rel == r)]
        if len(sub):
            line(sub, f"{int(h)}-hop {r}")
print()
print("=== the headline contrast: 2-hop nested vs 2-hop sibling ===")
two_nested = pairs[(pairs.n_hops == 2) & (pairs.rel == "nested")]
two_sib = pairs[(pairs.n_hops == 2) & (pairs.rel == "sibling")]
line(two_nested, "2-hop NESTED (chain)")
line(two_sib, "2-hop SIBLING")
# control for dilution: 2-hop nested tends to bigger area ratios; compare within a ratio band
for lo, hi in [(0, 20), (20, 1e9)]:
    tn = two_nested[(two_nested.area_ratio >= lo) & (two_nested.area_ratio < hi)]
    ts = two_sib[(two_sib.area_ratio >= lo) & (two_sib.area_ratio < hi)]
    print(f"  area_ratio [{lo},{hi}):  nested resid={tn.rho_resid.mean():+.3f} (n={len(tn)})   "
          f"sibling resid={ts.rho_resid.mean():+.3f} (n={len(ts)})")

n_2hop_nested = int(((pairs.n_hops == 2) & (pairs.rel == "nested")).sum())
print(f"\n>>> 2-hop NESTED chains found: {n_2hop_nested}  "
      "(containment is transitive: A in M in B => A in B => a DIRECT 1-hop edge, so chains never sit at 2 hops)")

# figure: only the non-empty categories; annotate the structural collapse
fig, ax = plt.subplots(figsize=(6.6, 4.2))
cats, vals, ns, cols = [], [], [], []
for lab, mask, c in [
    ("1-hop\n(direct containment,\nincl. collapsed chains)", (pairs.n_hops == 1), "#1a9850"),
    ("2-hop\nSIBLING\n(A→P←B)", (pairs.n_hops == 2) & (pairs.rel == "sibling"), "#b8e186"),
    ("disconnected", pairs.rel == "disconnected", "#999999"),
]:
    s = pairs[mask]
    cats.append(lab); vals.append(s.rho_resid.mean()); ns.append(len(s)); cols.append(c)
ax.bar(cats, vals, color=cols)
for i, (v, n) in enumerate(zip(vals, ns)):
    ax.text(i, v + 0.006, f"{v:+.3f}\nn={n}", ha="center", fontsize=9)
ax.axhline(disc_base, color="#999999", ls="--", lw=0.8)
ax.set_ylim(0, max(vals) * 1.3)
ax.set_ylabel("mean residual Spearman ρ")
ax.set_title(f"2-hop 'one-contains-the-other' chains = {n_2hop_nested} pairs (empty by construction):\n"
             "transitive containment collapses every chain to a 1-hop edge; 2-hop = siblings only")
fig.tight_layout()
fig.savefig(C.HERE / "fig5_directed_2hop.png", dpi=130)
print(f"wrote {C.HERE/'fig5_directed_2hop.png'}")
