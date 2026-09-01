"""Figures for the report. Regenerated with it, never hand-made, never committed as artwork.

Each figure exists because a table of the same numbers is harder to read, not to decorate the document. Three earn their place: the two variance decompositions, which are share-of-total quantities that a stacked bar makes immediately legible, and the redundancy matrix, where the point is the block structure rather than any individual coefficient.

Matplotlib only, `Agg` backend, no seaborn -- the report must regenerate on a headless machine with nothing extra installed.
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                       # noqa: E402
import pandas as pd                      # noqa: E402

from . import config                     # noqa: E402

DPI = 130


def _save(fig, name: str) -> str:
    config.IMAGES.mkdir(parents=True, exist_ok=True)
    p = config.IMAGES / name
    fig.savefig(p, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return name


def temporal_decomposition(tbl: pd.DataFrame) -> str | None:
    """Stacked space / time / interaction shares. The interaction bar is the whole point of the figure."""
    if tbl is None or not len(tbl):
        return None
    d = tbl.sort_values("interaction")
    fig, ax = plt.subplots(figsize=(8, 0.32 * len(d) + 1.4))
    y = np.arange(len(d))
    ax.barh(y, d.space, color="#c8d6e5", label="space (static map)")
    ax.barh(y, d.time, left=d.space, color="#8395a7", label="time (national trend)")
    ax.barh(y, d.interaction, left=d.space + d.time, color="#ee5253", label="interaction (spatiotemporal)")
    ax.set_yticks(y, d["product"], fontsize=8)
    ax.set_xlim(0, 1)
    ax.set_xlabel("share of variance")
    ax.set_title("Only the interaction term is information a year dummy cannot supply", fontsize=10)
    ax.legend(fontsize=7, loc="lower right", framealpha=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    return _save(fig, "temporal_decomposition.png")


def within_basin_variance(tbl: pd.DataFrame) -> str | None:
    """Share of catchment-grain variance a basin aggregate destroys, per source."""
    if tbl is None or not len(tbl):
        return None
    d = tbl.sort_values("within")
    fig, ax = plt.subplots(figsize=(8, 0.32 * len(d) + 1.4))
    y = np.arange(len(d))
    colors = ["#ee5253" if w < 0.10 else "#feca57" if w < 0.40 else "#10ac84" for w in d.within]
    ax.barh(y, d.within, color=colors)
    ax.axvline(0.10, color="#576574", lw=0.8, ls="--")
    ax.set_yticks(y, d.source, fontsize=8)
    ax.set_xlim(0, 1)
    ax.set_xlabel("within-basin share of variance (what aggregation destroys)")
    ax.set_title("Left of the dashed line the source is already a basin constant", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    return _save(fig, "within_basin_variance.png")


def redundancy_matrix(corr: pd.DataFrame, theme: str) -> str | None:
    """Rank-correlation heatmap for one theme. The block structure is the message, not any single cell."""
    if corr is None or corr.empty or len(corr) < 2:
        return None
    fig, ax = plt.subplots(figsize=(0.42 * len(corr) + 3.2, 0.42 * len(corr) + 2.6))
    im = ax.imshow(corr.to_numpy(dtype=float), vmin=-1, vmax=1, cmap="RdBu_r")
    ax.set_xticks(range(len(corr)), corr.columns, rotation=90, fontsize=7)
    ax.set_yticks(range(len(corr)), corr.index, fontsize=7)
    for i in range(len(corr)):
        for j in range(len(corr)):
            v = corr.iat[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=5.5,
                        color="white" if abs(v) > 0.6 else "#2f3542")
    fig.colorbar(im, ax=ax, shrink=0.7, label="Spearman")
    ax.set_title(f"{theme}: rank agreement", fontsize=10)
    return _save(fig, f"redundancy_{theme}.png")
