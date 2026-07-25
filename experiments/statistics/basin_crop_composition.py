"""basin_crop_composition.py -- does a basin's crop composition depend on its size?

Tests the "real agronomy" hypothesis directly, WITHOUT the feature pipeline: crop composition is
computed straight from the raw CDL pixel counts (get_data -> sd.crops) weighted only by each cell's
basin-membership fraction -- no agg_crops, no distance buckets, no exp-decay, no /basin_area. So if a
basin-area vs composition correlation survives here, it's geography, not an artifact of features.py /
transformers.py.

For each site:  pct_<crop> = Sum_over(cells, years) [ crop_px * frac_in_basin ]
                             / Sum_over(cells, years, classes) [ class_px * frac_in_basin ]
i.e. the long-run (all years pooled) area-weighted share of that class in the basin, in [0, 1].

Produces one scatter of basin area (km^2, log x) vs pct_<crop>, annotated with the Pearson and
Spearman correlations, saved next to this script.

The same check runs for N surplus: `surplus` plots basin-mean N-surplus intensity (kg N/ha,
total_kg_N mass / area, all years pooled, membership-weighted) vs basin area.

Usage:
    python basin_crop_composition.py corn          (crop composition share, [0,1])
    python basin_crop_composition.py surplus       (N-surplus intensity, kg/ha)
    (valid crops: alfalfa corn fallow hay_pasture nonag other small_grains soybeans)
"""

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]  # repo root
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

from src.data.access import get_site_ids, get_data

CLASSES = ["Alfalfa", "Corn", "Fallow", "Hay_Pasture", "Nonag", "Other", "Small_Grains", "Soybeans"]


def _site_composition(sd) -> dict | None:
    """Long-run area-weighted class shares for one site, straight from raw pixels. None if unusable."""
    if sd is None or sd.crops is None or sd.grid is None:
        return None
    grid = sd.grid[["node_id", "frac_cell_in_basin"]]
    crops = sd.crops.merge(grid, on="node_id")  # raw pixel counts per (cell, year) + membership frac
    w = crops["frac_cell_in_basin"].to_numpy()
    weighted = {c: float(np.nansum(crops[c].to_numpy() * w)) for c in CLASSES}
    total = sum(weighted.values())
    if total <= 0:
        return None
    return {c: weighted[c] / total for c in CLASSES}


def _site_surplus_intensity(sd) -> float | None:
    """Long-run basin-mean N-surplus intensity (kg N/ha) for one site, straight from raw surplus: the
    total N mass over all cells & years (basin-membership-weighted) divided by the same-weighted total
    area. No agg_surplus, no buckets, no /basin_area helper. None if unusable."""
    if sd is None or getattr(sd, "surplus", None) is None or sd.grid is None:
        return None
    grid = sd.grid[["node_id", "cell_area", "frac_cell_in_basin"]]
    s = sd.surplus.merge(grid, on="node_id")  # raw total_kg_N per (cell, year) + area + membership frac
    frac = s["frac_cell_in_basin"].to_numpy()
    mass = float(np.nansum(s["total_kg_N"].to_numpy() * frac))  # kg N, summed over cells & years
    area_ha = float(np.nansum((s["cell_area"].to_numpy() / 1e4) * frac))  # ha, summed the same way
    return mass / area_ha if area_ha > 0 else None


def collect(target: str) -> pd.DataFrame:
    """target = a crop class name (-> composition share in [0,1]) or 'surplus' (-> basin-mean kg/ha).
    y is computed straight from raw pixels/surplus, bypassing the agg_* feature pipeline."""
    rows = []
    for uid in [str(u) for u in get_site_ids()]:
        try:
            sd = get_data(uid)
        except Exception:
            continue
        if sd is None or not sd.basin_area:
            continue
        if target == "surplus":
            y = _site_surplus_intensity(sd)
        else:
            comp = _site_composition(sd)
            y = None if comp is None else comp[target]
        if y is None:
            continue
        rows.append({"site": uid, "area_km2": sd.basin_area / 1e6, "y": y})
    return pd.DataFrame(rows)


def main():
    valid = {c.lower(): c for c in CLASSES}
    if len(sys.argv) != 2:
        sys.exit("usage: python basin_crop_composition.py <target>   e.g. corn | surplus\n"
                 f"valid: surplus, {', '.join(valid)}")
    arg = sys.argv[1].strip().lower()
    if arg == "surplus":
        target, is_share, ylab, src = "surplus", False, "surplus (basin mean, kg N/ha)", "raw N-surplus"
    elif arg in valid:
        target, is_share, ylab, src = valid[arg], True, f"pct_{arg}", "raw CDL pixels"
    else:
        sys.exit(f"unknown target {arg!r}; valid: surplus, {', '.join(valid)}")

    df = collect(target)
    if len(df) < 3:
        sys.exit(f"only {len(df)} usable sites -- not enough to correlate")

    area, y = df["area_km2"].to_numpy(), df["y"].to_numpy()
    r_p, p_p = pearsonr(area, y)                         # linear, on raw area
    r_pl, p_pl = pearsonr(np.log10(area), y)             # linear vs log-area (area spans decades)
    r_s, p_s = spearmanr(area, y)                        # rank (scale-free)

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(area, y, s=28, alpha=0.75, edgecolor="k", linewidth=0.4)
    ax.set_xscale("log")
    ax.set_xlabel("basin area (km$^2$, log scale)")
    ax.set_ylabel(ylab)
    if is_share:
        ax.set_ylim(0, 1)
    ax.set_title(f"Basin area vs {arg}  (n={len(df)} sites, {src}, no aggregation code)")
    ax.grid(True, which="both", alpha=0.25)

    txt = (f"Pearson r (area)      = {r_p:+.3f}  (p={p_p:.1e})\n"
           f"Pearson r (log area)  = {r_pl:+.3f}  (p={p_pl:.1e})\n"
           f"Spearman ρ          = {r_s:+.3f}  (p={p_s:.1e})")
    ax.text(0.03, 0.97, txt, transform=ax.transAxes, va="top", ha="left", family="monospace",
            fontsize=9, bbox=dict(boxstyle="round", fc="white", ec="0.6", alpha=0.9))

    out = _HERE / f"basin_crop_composition_{arg}.png"
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print(f"n sites: {len(df)}")
    print(f"{arg}: min={y.min():.4g} mean={y.mean():.4g} max={y.max():.4g}")
    print(f"Pearson r (area)     = {r_p:+.3f}  (p={p_p:.2e})")
    print(f"Pearson r (log area) = {r_pl:+.3f}  (p={p_pl:.2e})")
    print(f"Spearman rho         = {r_s:+.3f}  (p={p_s:.2e})")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
