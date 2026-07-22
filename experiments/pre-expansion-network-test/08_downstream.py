"""Follow-up E: downstream neighbours as predictors + the either-direction coverage gain.

A pin with no monitored site UPSTREAM may still sit upstream of a monitored site (a downstream gauge
integrates the pin's water). Since Spearman is symmetric, a downstream neighbour predicts the pin at
the SAME per-edge strength an upstream neighbour would; the new value is COVERAGE -- more pins have a
monitor downstream (any mainstem gauge below them) than upstream (a monitor in their own catchment).

For each site s (as a stand-in for a pin):
  upstream monitors  = dg.predecessors(s)   (basins ⊂ B_s)  -> best = LARGEST (least diluted vs s)
  downstream monitors= dg.successors(s)     (basins ⊃ B_s)  -> best = SMALLEST (least diluted vs s)
We report structural coverage (who has a neighbour at all), the nearest-neighbour residual predictive
power each direction, and the either-direction feature (nearest neighbour in area, up or down).
"""

import warnings

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import common as C

wide = C.build_wide()
rwide = C.residual_wide(wide)
area = C.basin_areas()
_, _, dg = C.containment_info()
SITES = list(wide.columns)


def spr(a, b):
    df = pd.concat([rwide[a].rename("x"), rwide[b].rename("y")], axis=1).dropna()
    return df["x"].corr(df["y"], method="spearman") if len(df) >= C.MIN_OVERLAP else np.nan


rows = []
for s in SITES:
    if not np.isfinite(area.get(s, np.nan)):
        continue
    up = [i for i in dg.predecessors(s) if i in wide.columns and np.isfinite(area.get(i, np.nan))]
    dn = [i for i in dg.successors(s) if i in wide.columns and np.isfinite(area.get(i, np.nan))]
    best_up = max(up, key=lambda i: area[i]) if up else None   # largest upstream sub-basin (closest to s)
    near_dn = min(dn, key=lambda i: area[i]) if dn else None    # smallest downstream super-basin (closest to s)
    rho_up = spr(s, best_up) if best_up else np.nan
    rho_dn = spr(s, near_dn) if near_dn else np.nan
    # either-direction: the area-closest neighbour (least dilution), up or down
    cands = []
    if best_up:
        cands.append((area[s] / area[best_up], rho_up, "up", best_up))
    if near_dn:
        cands.append((area[near_dn] / area[s], rho_dn, "down", near_dn))
    ratio_e, rho_e, dir_e = (min(cands)[0], min(cands)[1], min(cands)[2]) if cands else (np.nan, np.nan, None)
    rows.append(dict(s=s, has_up=bool(up), has_dn=bool(dn), rho_up=rho_up, rho_dn=rho_dn,
                     rho_either=rho_e, dir_either=dir_e,
                     dn_ratio=(area[near_dn] / area[s]) if near_dn else np.nan))

r = pd.DataFrame(rows)
r.to_parquet(C.HERE / "downstream.parquet")
N = len(r)

# ── coverage ────────────────────────────────────────────────────────────────────
up_only = int((r.has_up & ~r.has_dn).sum())
dn_only = int((r.has_dn & ~r.has_up).sum())
both = int((r.has_up & r.has_dn).sum())
neither = int((~r.has_up & ~r.has_dn).sum())
print(f"=== structural coverage (N={N} sites as pin stand-ins) ===")
print(f"  has an UPSTREAM monitor   : {int(r.has_up.sum())}  ({r.has_up.mean():.0%})")
print(f"  has a DOWNSTREAM monitor  : {int(r.has_dn.sum())}  ({r.has_dn.mean():.0%})")
print(f"  upstream-only : {up_only}   downstream-only : {dn_only}   both : {both}   neither : {neither}")
print(f"  >>> downstream RESCUES {dn_only} pins that have NO upstream monitor")
print(f"  >>> either-direction covers {int((r.has_up|r.has_dn).sum())}/{N} ({(r.has_up|r.has_dn).mean():.0%}) "
      f"vs upstream-only {int(r.has_up.sum())}/{N} ({r.has_up.mean():.0%})")

# ── predictive power (residual Spearman, measurable pairs only) ──────────────────
def mm(col, mask):
    s = r.loc[mask, col].dropna()
    return f"n={len(s):2d}  mean {s.mean():+.3f}  median {s.median():+.3f}"


print(f"\n=== nearest-neighbour residual predictive power ===")
print(f"  best UPSTREAM neighbour  : {mm('rho_up', r.has_up)}")
print(f"  nearest DOWNSTREAM nbr   : {mm('rho_dn', r.has_dn)}   (≈ upstream by symmetry; dilution-governed)")
print(f"  EITHER-direction nearest : {mm('rho_either', r.has_up | r.has_dn)}   (this is the coverage-max feature)")

# downstream dilution: rho vs downstream/pin area ratio
dd = r.dropna(subset=["rho_dn", "dn_ratio"])
from scipy.stats import spearmanr

rs, _ = spearmanr(dd.dn_ratio, dd.rho_dn)
print(f"\n  downstream dilution: Spearman(area_ratio_down/pin, rho_dn) = {rs:+.3f}  "
      f"(bigger downstream basin -> weaker, same law as upstream)")

# ── figure ──────────────────────────────────────────────────────────────────────
fig, (axl, axr) = plt.subplots(1, 2, figsize=(10.8, 4.3))
axl.bar(["upstream\nonly", "downstream\nonly", "both", "neither"], [up_only, dn_only, both, neither],
        color=["#1a9850", "#2166ac", "#8073ac", "#cccccc"])
for i, v in enumerate([up_only, dn_only, both, neither]):
    axl.text(i, v + 0.5, str(v), ha="center", fontsize=10)
axl.set_ylabel("sites (pin stand-ins)")
axl.set_title(f"Coverage: downstream rescues {dn_only} pins with no upstream monitor\n"
              f"either-direction reaches {int((r.has_up|r.has_dn).sum())}/{N} vs upstream-only {int(r.has_up.sum())}/{N}", fontsize=11)
cats = ["best\nupstream", "nearest\ndownstream", "either\ndirection"]
vals = [r.loc[r.has_up, "rho_up"].mean(), r.loc[r.has_dn, "rho_dn"].mean(), r.loc[r.has_up | r.has_dn, "rho_either"].mean()]
ns = [int(r.has_up.sum()), int(r.has_dn.sum()), int((r.has_up | r.has_dn).sum())]
axr.bar(cats, vals, color=["#1a9850", "#2166ac", "#8073ac"])
for i, (v, n) in enumerate(zip(vals, ns)):
    axr.text(i, v + 0.004, f"{v:+.3f}\nn={n}", ha="center", fontsize=9)
axr.set_ylim(0, 0.62)
axr.set_ylabel("mean residual Spearman ρ")
axr.set_title("Nearest-neighbour predictive power by direction\n(symmetric magnitude; either-direction = best coverage)", fontsize=11)
fig.subplots_adjust(top=0.85, bottom=0.16, wspace=0.28)
fig.savefig(C.HERE / "fig10_downstream.png", dpi=130)
print(f"\nwrote fig10, downstream.parquet")
