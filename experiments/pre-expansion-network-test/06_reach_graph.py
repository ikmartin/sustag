"""Follow-up C (the GNN-relevant redo): rebuild the graph as the IMMEDIATE-containment (reach) network
and re-test 1-hop vs 2-hop.

The step-01 graph was point-in-polygon containment -> transitively CLOSED, so every chain A in M in B
collapsed to a direct A->B edge and 2-hop chains could not exist. That is a construction artifact, not
hydrology. Here we build the graph the user specified, which mimics the river-reach network a GNN would
use:

    s -- t  iff  B(s) subset B(t) [or vice-versa]  AND  no other site r has B(s) subset B(r) subset B(t)

i.e. the transitive reduction: an edge only to the NEAREST enclosing monitored basin. Now a real chain
A in M in B is a 2-hop path A->M->B, and we can ask whether nitrate signal PROPAGATES through the
intermediate node -- the thing multi-hop message passing exists to exploit.

We report residual co-movement for 1-hop (immediate), 2-hop CHAIN (A in M in B), 2-hop SIBLING, and --
the decisive GNN test -- the PARTIAL correlation of a 2-hop chain (A,B) controlling for the intermediate
M. If M screens off A (partial ~ 0), a 1-hop neighbour feature already carries everything and multi-hop
is redundant. If A adds signal beyond M (partial > 0), multi-hop -- a GNN -- has something to carry.
Reproduces fig4 on the new graph as fig8.
"""

import networkx as nx
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import common as C

wide = C.build_wide()
resid_corr = C.residual_wide(wide).corr(method="spearman", min_periods=C.MIN_OVERLAP)
pairs = pd.read_parquet(C.HERE / "pairs.parquet")
_, comp0, dg = C.containment_info()  # dg: transitively-CLOSED containment (child->parent = child in parent)


def is_immediate(s, t):
    """edge s->t (B(s) in B(t)) survives iff no monitored r sits strictly between: B(s) in B(r) in B(t)."""
    for r in dg.successors(s):  # r with B(s) in B(r)  (dg is closed, so successors = all enclosers)
        if r != s and r != t and dg.has_edge(r, t):
            return False
    return True


# transitive reduction -> immediate-containment (reach) DiGraph H; undirected UG for hop distances
H = nx.DiGraph()
H.add_nodes_from(dg.nodes)
for s, t in dg.edges():
    if is_immediate(s, t):
        H.add_edge(s, t)
UG = H.to_undirected()
comp = {}
for i, cc in enumerate(nx.connected_components(UG)):
    for s in cc:
        comp[s] = i
print(f"reach graph: {H.number_of_edges()} immediate edges (was {dg.number_of_edges()} closed edges); "
      f"{sum(1 for v in pd.Series(comp).value_counts() if v > 1)} components of size>1")


def classify(a, b):
    nested = dg.has_edge(a, b) or dg.has_edge(b, a)  # one basin contains the other (directed fact)
    connected = a in comp and b in comp and comp[a] == comp[b] and (comp[a] != comp.get(a + "___") )
    same = a in comp and b in comp and comp[a] == comp[b]
    if not same:
        return "disconnected", np.nan
    try:
        h = nx.shortest_path_length(UG, a, b)
    except nx.NetworkXNoPath:
        return "disconnected", np.nan
    if nested:
        return ("chain", h)  # A in M in ... in B: a directed containment path (real upstream/downstream)
    return ("sibling", h)


cls, hops = zip(*[classify(a, b) for a, b in zip(pairs.a, pairs.b)])
pairs = pairs.assign(cls=cls, rhop=hops)
disc_base = pairs.loc[pairs.cls == "disconnected", "rho_resid"].mean()

print(f"\ndisconnected baseline residual rho = {disc_base:+.3f}")
print("=== residual co-movement on the REACH graph ===")
for lab, mask in [
    ("1-hop immediate (adjacent nesting)", (pairs.cls == "chain") & (pairs.rhop == 1)),
    ("2-hop CHAIN  (A in M in B)", (pairs.cls == "chain") & (pairs.rhop == 2)),
    ("3+-hop chain", (pairs.cls == "chain") & (pairs.rhop >= 3)),
    ("2-hop SIBLING (A->P<-B)", (pairs.cls == "sibling") & (pairs.rhop == 2)),
    ("sibling 3+hop", (pairs.cls == "sibling") & (pairs.rhop >= 3)),
]:
    s = pairs[mask]
    if len(s):
        print(f"  {lab:36s} n={len(s):4d}  resid_rho={s.rho_resid.mean():+.3f}  excess={s.rho_resid.mean()-disc_base:+.3f}")

# ── the decisive GNN test: partial correlation of a 2-hop chain, controlling for the intermediate ──
def partial(rab, ram, rbm):
    d = np.sqrt(max(1e-9, (1 - ram**2) * (1 - rbm**2)))
    return (rab - ram * rbm) / d


two = pairs[(pairs.cls == "chain") & (pairs.rhop == 2)]
rows = []
for _, r in two.iterrows():
    a, b = r.a, r.b
    # the intermediate(s): sites M with a->M->b (immediate) in H
    mids = [m for m in H.successors(a) if H.has_edge(m, b)] + [m for m in H.successors(b) if H.has_edge(m, a)]
    for m in set(mids):
        ram, rbm, rab = resid_corr.loc[a, m], resid_corr.loc[b, m], resid_corr.loc[a, b]
        if np.isfinite(ram) and np.isfinite(rbm) and np.isfinite(rab):
            rows.append(dict(rab=rab, ram=ram, rbm=rbm, pcorr=partial(rab, ram, rbm)))
            break
pc = pd.DataFrame(rows)
print("\n=== GNN test: does a 2-hop chain (A,B) retain signal AFTER controlling for the intermediate M? ===")
print(f"  n chains with a usable intermediate: {len(pc)}")
print(f"  raw 2-hop chain resid rho (A,B)      : {pc.rab.mean():+.3f}")
print(f"  PARTIAL rho (A,B | M)                : {pc.pcorr.mean():+.3f}")
print(f"  -> {pc.rab.mean()-pc.pcorr.mean():+.3f} of it is screened off by M "
      f"({'M screens A off => 1-hop feature suffices, GNN redundant' if pc.pcorr.mean() < 0.05 else 'A adds signal beyond M => multi-hop/GNN could help'})")

# ── fig7: residual by reach-graph class ──
fig, ax = plt.subplots(figsize=(7.2, 4.3))
cats, vals, ns, cols = [], [], [], []
for lab, mask, c in [
    ("1-hop\nimmediate", (pairs.cls == "chain") & (pairs.rhop == 1), "#1a9850"),
    ("2-hop\nCHAIN", (pairs.cls == "chain") & (pairs.rhop == 2), "#66bd63"),
    ("2-hop\nSIBLING", (pairs.cls == "sibling") & (pairs.rhop == 2), "#b8e186"),
    ("disconnected", pairs.cls == "disconnected", "#999999"),
]:
    s = pairs[mask]
    cats.append(lab); vals.append(s.rho_resid.mean()); ns.append(len(s)); cols.append(c)
ax.bar(cats, vals, color=cols)
for i, (v, n) in enumerate(zip(vals, ns)):
    ax.text(i, v + 0.005, f"{v:+.3f}\nn={n}", ha="center", fontsize=9)
ax.axhline(disc_base, color="#999999", ls="--", lw=0.8)
ax.set_ylim(0, max(v for v in vals if np.isfinite(v)) * 1.25)
ax.set_ylabel("mean residual Spearman ρ")
ax.set_title("Reach graph (immediate containment): 2-hop chains DO exist now —\ndo they carry signal?", fontsize=11)
fig.subplots_adjust(top=0.86, bottom=0.16)
fig.savefig(C.HERE / "fig7_reach_by_hop.png", dpi=130)

# ── fig8: reproduce fig4 on the reach graph (residual rho vs Euclidean distance) ──
fig, ax = plt.subplots(figsize=(7.4, 4.6))
disc = pairs[pairs.cls == "disconnected"]
sib2 = pairs[(pairs.cls == "sibling") & (pairs.rhop == 2)]
ch2 = pairs[(pairs.cls == "chain") & (pairs.rhop == 2)]
one = pairs[(pairs.cls == "chain") & (pairs.rhop == 1)]
ax.scatter(disc.euclid_km, disc.rho_resid, s=8, alpha=0.22, color="#999999", label="disconnected")
ax.scatter(sib2.euclid_km, sib2.rho_resid, s=12, alpha=0.4, color="#b8e186", label="2-hop sibling")
ax.scatter(ch2.euclid_km, ch2.rho_resid, s=26, alpha=0.75, color="#66bd63", label="2-hop CHAIN (A⊂M⊂B)")
ax.scatter(one.euclid_km, one.rho_resid, s=20, alpha=0.7, color="#1a9850", label="1-hop immediate")
ax.axhline(0, color="k", lw=0.6)
ax.set_xlabel("sensor–sensor Euclidean distance (km)")
ax.set_ylabel("residual Spearman ρ")
ax.set_title("Residual co-movement vs distance, on the reach (immediate-containment) graph", fontsize=11)
ax.legend(fontsize=8, loc="upper right")
fig.subplots_adjust(top=0.90, bottom=0.13)
fig.savefig(C.HERE / "fig8_reach_resid_vs_distance.png", dpi=130)
print(f"\nwrote fig7, fig8")
