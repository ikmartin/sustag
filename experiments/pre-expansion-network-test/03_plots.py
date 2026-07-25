"""Step 3: figures (PNG, Agg backend). Four graphs telling the story:
  fig1_raw_vs_residual.png   -- why the raw correlation is misleading (seasonality) and the residual is the real test
  fig2_excess_by_hop.png     -- THE headline: residual excess is strong at 1 hop, gone by 2
  fig3_resid_distributions.png -- the full distributions, not just means
  fig4_resid_vs_distance.png -- Euclidean distance is a poor axis for this (motivates the graph, at 1 hop)
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import common as C

pairs = pd.read_parquet(C.HERE / "pairs.parquet")
conn = pairs[pairs.connected]
disc = pairs[~pairs.connected]
one = conn[conn.n_hops == 1]
two = conn[conn.n_hops == 2]
BLUE, GREY, GREEN = "#2166ac", "#999999", "#1a9850"


# fig1 -- raw vs residual, connected vs disconnected
fig, ax = plt.subplots(figsize=(6.4, 4.2))
groups = ["RAW\nconnected", "RAW\ndisconnected", "RESIDUAL\nconnected", "RESIDUAL\ndisconnected"]
vals = [conn.rho_raw.mean(), disc.rho_raw.mean(), conn.rho_resid.mean(), disc.rho_resid.mean()]
cols = [BLUE, GREY, BLUE, GREY]
ax.bar(groups, vals, color=cols)
for i, v in enumerate(vals):
    ax.text(i, v + 0.01, f"{v:+.3f}", ha="center", fontsize=9)
ax.axhline(0, color="k", lw=0.6)
ax.set_ylabel("mean Spearman ρ")
ax.set_title("Raw ρ is dominated by shared seasonality;\nresidual (statewide-mean removed) reveals the true edge signal")
fig.tight_layout()
fig.savefig(C.HERE / "fig1_raw_vs_residual.png", dpi=130)

# fig2 -- residual excess by hop (THE headline)
fig, ax = plt.subplots(figsize=(6.4, 4.2))
cats = ["disconnected\n(baseline)", "1 hop\n(direct nesting)", "2 hops"]
means = [disc.rho_resid.mean(), one.rho_resid.mean(), two.rho_resid.mean()]
ns = [len(disc), len(one), len(two)]
bars = ax.bar(cats, means, color=[GREY, GREEN, "#b8e186"])
for i, (v, n) in enumerate(zip(means, ns)):
    ax.text(i, v + 0.005, f"{v:+.3f}\nn={n}", ha="center", fontsize=9)
ax.axhline(disc.rho_resid.mean(), color=GREY, ls="--", lw=0.8)
ax.set_ylim(0, 0.27)
ax.set_ylabel("mean residual Spearman ρ")
ax.set_title("Signal beyond `rest_of_state` is concentrated at DIRECT neighbours\nand decays to ~0 within two hops")
fig.tight_layout()
fig.savefig(C.HERE / "fig2_excess_by_hop.png", dpi=130)

# fig3 -- residual distributions
fig, ax = plt.subplots(figsize=(6.4, 4.2))
data = [disc.rho_resid.dropna(), two.rho_resid.dropna(), one.rho_resid.dropna()]
labels = [f"disconnected\n(n={len(disc)})", f"2 hops\n(n={len(two)})", f"1 hop\n(n={len(one)})"]
bp = ax.boxplot(data, tick_labels=labels, showfliers=False, patch_artist=True)
for patch, c in zip(bp["boxes"], [GREY, "#b8e186", GREEN]):
    patch.set_facecolor(c)
ax.axhline(0, color="k", lw=0.6)
ax.set_ylabel("residual Spearman ρ")
ax.set_title("Residual co-movement distributions\n(1-hop clearly shifted positive; 2-hop ≈ disconnected)")
fig.tight_layout()
fig.savefig(C.HERE / "fig3_resid_distributions.png", dpi=130)

# fig4 -- residual rho vs euclidean distance, connected vs disconnected
fig, ax = plt.subplots(figsize=(6.8, 4.2))
ax.scatter(disc.euclid_km, disc.rho_resid, s=8, alpha=0.25, color=GREY, label="disconnected")
ax.scatter(two.euclid_km, two.rho_resid, s=10, alpha=0.4, color="#b8e186", label="connected 2-hop")
ax.scatter(one.euclid_km, one.rho_resid, s=16, alpha=0.7, color=GREEN, label="connected 1-hop")
ax.axhline(0, color="k", lw=0.6)
ax.set_xlabel("sensor–sensor Euclidean distance (km)")
ax.set_ylabel("residual Spearman ρ")
ax.set_title("Euclidean distance is a poor axis: 1-hop signal persists even\nfar apart — the graph edge, not proximity, is what matters", fontsize=11)
ax.legend(fontsize=8)
fig.subplots_adjust(top=0.88, bottom=0.13, left=0.1, right=0.97)
fig.savefig(C.HERE / "fig4_resid_vs_distance.png", dpi=130)

print("wrote fig1..fig4 to", C.HERE)
