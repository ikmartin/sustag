"""Histogram of the number of non-nan nitrate ROWS per site across the full candidate site list.

This is the picture behind filter_sites.MIN_NITRATE_ROWS: the water build's quantity filter now drops a site unless its count of non-nan nitrate observations EXCEEDS that threshold (replacing the old sparsity + lifespan cutoffs). Run this to see the cohort distribution and where the threshold falls, then tune MIN_NITRATE_ROWS in src/build/water/filter_sites.py.

Reads the full list from raw_site_list.csv (fetch_sites.py) and each site's native series via the shared reader, so it counts exactly what the filter counts.

    python nitrate_row_counts.py            # print the keep/drop split, write nitrate_row_counts.png (+ .csv)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from src.build.water._paths import raw_site_list_path, read_raw_series
from src.build.water.filter_sites import MIN_NITRATE_ROWS


def nitrate_row_counts() -> pd.Series:
    """Non-nan nitrate row count per site over the full raw_site_list (0 if no raw data on disk)."""
    sites = sorted(pd.read_csv(raw_site_list_path(), dtype={"site_uid": str})["site_uid"].astype(str))
    counts = {}
    for uid in sites:
        s = read_raw_series(uid)
        counts[uid] = 0 if s is None else int(s["nitrate_con"].notna().sum())
    return pd.Series(counts, name="nitrate_rows").sort_values()


def _plot(counts: pd.Series, path: Path) -> None:
    nz = counts[counts > 0]
    n_zero = int((counts == 0).sum())
    bins = np.logspace(np.log10(nz.min()), np.log10(nz.max()), 40)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(nz, bins=bins, color="#4477aa", edgecolor="white")
    ax.set_xscale("log")
    ax.axvline(MIN_NITRATE_ROWS, color="crimson", ls="--", lw=1.6, label=f"MIN_NITRATE_ROWS = {MIN_NITRATE_ROWS:,}")
    ax.axvspan(nz.min(), MIN_NITRATE_ROWS, color="crimson", alpha=0.06)
    kept, dropped = int((counts > MIN_NITRATE_ROWS).sum()), int((counts <= MIN_NITRATE_ROWS).sum())
    ax.set_title(f"Non-nan nitrate rows per site (n={len(counts)}; {n_zero} have no raw data)\nkeep {kept} (right of line) / drop {dropped} (left, incl. empty)")
    ax.set_xlabel("non-nan nitrate rows (log scale)")
    ax.set_ylabel("sites")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main() -> None:
    counts = nitrate_row_counts()
    counts.rename_axis("site_uid").reset_index().to_csv(_HERE / "nitrate_row_counts.csv", index=False)

    kept = int((counts > MIN_NITRATE_ROWS).sum())
    print(f"\n{len(counts)} candidate sites | non-nan nitrate rows: median {int(counts.median()):,} (min {int(counts.min())}, max {int(counts.max()):,})")
    print(f"  {int((counts == 0).sum())} sites with no raw data (count 0)")
    print(f"  MIN_NITRATE_ROWS = {MIN_NITRATE_ROWS:,}  ->  keep {kept}, drop {len(counts) - kept}")
    print(f"  smallest counts above 0: {sorted(int(v) for v in counts if v > 0)[:10]}")

    _plot(counts, _HERE / "nitrate_row_counts.png")
    print(f"  wrote {_HERE / 'nitrate_row_counts.png'}, nitrate_row_counts.csv")


if __name__ == "__main__":
    main()
