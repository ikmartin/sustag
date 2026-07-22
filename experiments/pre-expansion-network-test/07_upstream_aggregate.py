"""Follow-up D: an AREA-WEIGHTED aggregate of all upstream monitored nitrate as a single predictor.

For a target site s with basin B, let U = all other sites whose basin is inside B (upstream). Build a
weighted daily series M from their nitrate and ask how well M predicts s (Spearman, raw and residual).

  Variant 1 (naive):    weight_i = area(B_i) / area(B)          -- full basin area; nested basins OVERLAP
                        so an inner site is counted inside every basin that contains it (double-count).
  Variant 2 (disjoint): weight_i = incr_i / area(B),  incr_i = area(B_i) - Σ area(immediate monitored
                        children of i).  Each point of B is credited to the SMALLEST monitored basin
                        covering it, so s2 is only weighted by the area it adds beyond s1 (its child) --
                        avoids double-counting s1, whose signal s2 already carries.  (= the user's
                        M = area(B1)/area(B3)·s1 + area(B2-B1)/area(B3)·s2 .)

Because B_i ⊂ B for every upstream site, overlap area = area(B_i). Spearman is scale-invariant, so the
/area(B) denominator does not affect the correlation -- only the RELATIVE weighting (v1 vs v2) does, and
that only differs when B contains a nested CHAIN of monitored sites.
"""

import networkx as nx
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import common as C

wide = C.build_wide()
rwide = C.residual_wide(wide)
area = C.basin_areas()
_, _, dg = C.containment_info()  # child -> parent (child's basin inside parent's basin)


# immediate-containment children (reach graph), reused from follow-up C's construction
def is_immediate(s, t):
    for r in dg.successors(s):
        if r != s and r != t and dg.has_edge(r, t):
            return False
    return True


H = nx.DiGraph()
H.add_nodes_from(dg.nodes)
for s, t in dg.edges():
    if is_immediate(s, t):
        H.add_edge(s, t)


def weighted_series(frame, sites, weights):
    """Σ w_i * value_i(t) over available sites (missing days omitted); NaN if none report that day."""
    w = pd.Series(weights)
    return frame[list(sites)].mul(w, axis=1).sum(axis=1, min_count=1)


def spearman(a, b, min_n=C.MIN_OVERLAP):
    df = pd.concat([a.rename("x"), b.rename("y")], axis=1).dropna()
    return df["x"].corr(df["y"], method="spearman") if len(df) >= min_n else np.nan


rows = []
for s in wide.columns:
    U = [i for i in dg.predecessors(s) if i in wide.columns and i != s]  # sites whose basin ⊂ B_s
    if not U or not np.isfinite(area.get(s, np.nan)):
        continue
    a_s = area[s]
    w1, w2 = {}, {}
    for i in U:
        ai = area.get(i, np.nan)
        if not np.isfinite(ai):
            continue
        child_area = sum(area.get(j, 0.0) for j in H.successors(i) if j in U)  # immediate monitored children
        incr = max(0.0, ai - child_area)
        w1[i] = ai / a_s
        w2[i] = incr / a_s
    if not w1:
        continue
    keep = list(w1)
    M1r = weighted_series(wide, keep, w1)
    M2r = weighted_series(wide, keep, w2)
    M1a = weighted_series(rwide, keep, w1)
    M2a = weighted_series(rwide, keep, w2)
    # single best-neighbour baseline: the immediate upstream child with the largest disjoint weight
    best = max(keep, key=lambda i: w2[i])
    rows.append(dict(
        s=s, n_up=len(keep), n_nested_chain=sum(1 for i in keep if any(j in keep for j in H.successors(i))),
        rho1_raw=spearman(M1r, wide[s]), rho2_raw=spearman(M2r, wide[s]),
        rho1_res=spearman(M1a, rwide[s]), rho2_res=spearman(M2a, rwide[s]),
        rho_best_res=spearman(rwide[best], rwide[s]),
    ))

res = pd.DataFrame(rows).dropna(subset=["rho1_res", "rho2_res"])
res.to_parquet(C.HERE / "upstream_aggregate.parquet")


def m(col):
    return f"mean {res[col].mean():+.3f}  median {res[col].median():+.3f}"


print(f"target sites with >=1 upstream monitored site (usable): {len(res)}")
print(f"  of which have a nested CHAIN of upstream sites (where v1 vs v2 differ): "
      f"{(res.n_nested_chain > 0).sum()}")
print(f"  median # upstream sites aggregated: {int(res.n_up.median())}  (max {int(res.n_up.max())})\n")
print("=== predictive power of the aggregate M on s (Spearman across target sites) ===")
print(f"  RAW      variant 1 (naive area) : {m('rho1_raw')}")
print(f"  RAW      variant 2 (disjoint)   : {m('rho2_raw')}")
print(f"  RESIDUAL variant 1 (naive area) : {m('rho1_res')}")
print(f"  RESIDUAL variant 2 (disjoint)   : {m('rho2_res')}")
print(f"  RESIDUAL single best neighbour  : {m('rho_best_res')}   (aggregation baseline)")

chain = res[res.n_nested_chain > 0]
print(f"\n=== on the {len(chain)} sites with a nested upstream CHAIN (where the variants actually differ) ===")
print(f"  RESIDUAL variant 1 (naive)   : mean {chain.rho1_res.mean():+.3f}")
print(f"  RESIDUAL variant 2 (disjoint): mean {chain.rho2_res.mean():+.3f}")
print(f"  RESIDUAL single best neighbour: mean {chain.rho_best_res.mean():+.3f}")

# figure
fig, (axl, axr) = plt.subplots(1, 2, figsize=(10.6, 4.3))
cats = ["single best\nneighbour", "M: variant 1\n(naive area)", "M: variant 2\n(disjoint)"]
vals = [res.rho_best_res.mean(), res.rho1_res.mean(), res.rho2_res.mean()]
axl.bar(cats, vals, color=["#1a9850", "#7fbf7b", "#2166ac"])
for i, v in enumerate(vals):
    axl.text(i, v + 0.004, f"{v:+.3f}", ha="center", fontsize=9)
axl.set_ylabel("mean residual Spearman ρ (M vs s)")
axl.set_title(f"Area-weighted upstream aggregate vs s  (n={len(res)} sites)", fontsize=11)
axr.scatter(res.rho1_res, res.rho2_res, s=22, alpha=0.7, color="#2166ac")
lim = [min(res.rho1_res.min(), res.rho2_res.min()) - 0.05, max(res.rho1_res.max(), res.rho2_res.max()) + 0.05]
axr.plot(lim, lim, color="k", lw=0.6, ls=":")
axr.set_xlim(lim); axr.set_ylim(lim)
axr.set_xlabel("ρ  variant 1 (naive area)")
axr.set_ylabel("ρ  variant 2 (disjoint)")
axr.set_title("Per-site: disjoint vs naive weighting\n(points above the line = disjoint helps)", fontsize=11)
fig.subplots_adjust(top=0.88, bottom=0.15, wspace=0.3)
fig.savefig(C.HERE / "fig9_upstream_aggregate.png", dpi=130)
print(f"\nwrote fig9, upstream_aggregate.parquet")
