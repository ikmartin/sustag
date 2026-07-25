"""Follow-up B: the far-but-correlated pairs (rho_resid >= 0.25 AND euclid_km >= 350).
Is their high correlation real, or chance / similar seasonal baselines?

Three discriminators:
  1. connected?   -- a pair on ONE long river (e.g. the Mississippi) is hydrologically nested yet far
                     in Euclidean terms; that is real connectivity, not coincidence.
  2. rho_pure     -- correlation after removing each site's OWN day-of-year climatology (not just the
                     statewide mean). If rho_resid was driven by a shared SEASONAL SHAPE ("similar
                     baselines"), rho_pure collapses. If it was genuine day-to-day synchrony, it holds.
  3. overlap/chance -- with autocorrelated series the effective N is small; compare the outliers to the
                     full distribution of rho_resid among all >=350 km pairs (are they a rare tail?).
"""

import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import common as C
from src.data.access import get_location_desc

RHO_MIN, DIST_MIN = 0.25, 350.0

wide = C.build_wide()
pairs = pd.read_parquet(C.HERE / "pairs.parquet")

# own-day-of-year climatology removal -> pure anomalies -> their pairwise Spearman
doy = wide.index.dayofyear
clim = wide.groupby(doy).transform("mean")  # per site, its mean value on each calendar day
pure = wide - clim
pure_corr = pure.corr(method="spearman", min_periods=C.MIN_OVERLAP)
pairs["rho_pure"] = [pure_corr.loc[a, b] for a, b in zip(pairs.a, pairs.b)]

far = pairs[pairs.euclid_km >= DIST_MIN]
out = far[far.rho_resid >= RHO_MIN].sort_values("rho_resid", ascending=False)

print(f"=== chance reference: all pairs with Euclidean distance >= {DIST_MIN:.0f} km ===")
print(f"  n={len(far)};  rho_resid mean={far.rho_resid.mean():+.3f} sd={far.rho_resid.std():.3f};  "
      f"fraction >= {RHO_MIN}: {(far.rho_resid>=RHO_MIN).mean():.3f}  ({len(out)} pairs)")
print(f"  under a mean-0 sd={far.rho_resid.std():.2f} noise model, P(rho>= {RHO_MIN}) ~ "
      f"{1 - 0.5*(1+math.erf((RHO_MIN-far.rho_resid.mean())/(far.rho_resid.std()*2**0.5))):.3f}")
print(f"  of the {len(out)} outliers: connected={int(out.connected.sum())}, "
      f"disconnected={int((~out.connected).sum())}; median overlap_days={int(out.overlap_days.median())}")

print(f"\n=== the {len(out)} far+correlated outlier pairs ===")
print(f"  {'dist':>5} {'ρ_res':>6} {'ρ_pure':>7} {'conn':>5} {'ovl_d':>6}  sites")
kept = 0
for _, r in out.iterrows():
    da = (get_location_desc(r.a) or r.a)[:32]
    db = (get_location_desc(r.b) or r.b)[:32]
    print(f"  {r.euclid_km:5.0f} {r.rho_resid:+6.3f} {r.rho_pure:+7.3f} {str(bool(r.connected)):>5} "
          f"{int(r.overlap_days):6d}  {da}  ↔  {db}")
    kept += 1
    if kept >= 25:
        print(f"  ... ({len(out)-25} more)")
        break

# how much of the far-outlier correlation is seasonal-baseline vs genuine synchrony?
frac_survive = (out.rho_pure >= 0.15).mean()
print(f"\n=== verdict inputs ===")
print(f"  mean rho_resid (statewide-removed) over outliers: {out.rho_resid.mean():+.3f}")
print(f"  mean rho_pure  (own-doy-removed)   over outliers: {out.rho_pure.mean():+.3f}")
print(f"  -> collapse from removing each site's OWN seasonal shape: "
      f"{out.rho_resid.mean()-out.rho_pure.mean():+.3f}")
print(f"  fraction of outliers whose synchrony SURVIVES doy removal (rho_pure>=0.15): {frac_survive:.2f}")

# fig6: (L) the full >=350km distribution -- outliers are the tail of a broad positive shift, not a
# special cluster; (R) rho_resid vs rho_pure -- most far correlation survives removing seasonal shape
fig, (axl, axr) = plt.subplots(1, 2, figsize=(10.5, 4.2))
axl.hist(far.rho_resid.dropna(), bins=30, color="#999999", edgecolor="white")
axl.axvline(0, color="k", lw=0.6)
axl.axvline(RHO_MIN, color="#d73027", ls="--", lw=1, label=f"outlier cut ρ={RHO_MIN}")
axl.axvline(far.rho_resid.mean(), color="#2166ac", ls="-", lw=1.2, label=f"mean {far.rho_resid.mean():+.2f}")
axl.set_xlabel("residual Spearman ρ"); axl.set_ylabel("far pairs (≥350 km)")
axl.set_title(f"Far pairs: broad, positively-shifted (mean {far.rho_resid.mean():+.2f}),\nnot a spike of anomalies — {len(out)}/{len(far)} exceed {RHO_MIN}")
axl.legend(fontsize=8)
axr.scatter(out.rho_resid, out.rho_pure, s=18, alpha=0.7, color="#2166ac")
lim = [-0.2, 0.85]
axr.plot(lim, lim, color="k", lw=0.6, ls=":")
axr.axhline(0.15, color="#d73027", ls="--", lw=1, label="survives (ρ_pure≥0.15)")
axr.set_xlim(lim); axr.set_ylim(lim)
axr.set_xlabel("ρ  (statewide-mean removed)"); axr.set_ylabel("ρ_pure  (own seasonal shape ALSO removed)")
axr.set_title(f"{frac_survive:.0%} survive removing each site's own seasonal cycle\n→ genuine synchrony, not just 'similar baselines'")
axr.legend(fontsize=8)
fig.tight_layout()
fig.savefig(C.HERE / "fig6_far_outliers.png", dpi=130)
print(f"wrote {C.HERE/'fig6_far_outliers.png'}")
