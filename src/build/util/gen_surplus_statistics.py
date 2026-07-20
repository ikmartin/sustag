"""Build-only: compute the global surplus min/max colour-scale reference the widget reads.

Ported from data/surplus/gen_surplus_statistics.py. Writes one row per year of
min/max surplus_kgha (+ total_kg_N) to src/data/processed/surplus/meta/surplus_stats.csv, which
src.data.surplus_viz reads via _min_surplus() / _max_surplus() to normalise the YlOrRd surplus
overlay. Zeros are dropped before the min/max so nodata pixels don't flatten the colour scale.

Source: the pixel-level surplus chunks (src/data/raw/surplus/surplus[0-9]*.parquet, written by
build_source.py). The legacy version read the merged surplus_raw/iowa_nitrogen_surplus.parquet;
this tree has no merged table, so the equivalent pixel-level values come straight from the chunks.
raw/surplus/ is gitignored, so this only matters when regenerating from raw; the committed
surplus_global.parquet + a committed surplus_stats.csv cover normal use.

Usage
-----
    python -m src.build.util.gen_surplus_statistics
"""

import pandas as pd
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent  # src/build/util
_SRC = _THIS_DIR.parents[1]  # src
_SOURCE_DIR = _SRC / "data" / "raw" / "surplus"  # pixel-level surplus chunks (build_source.py output)
_META_DIR = _SRC / "data" / "processed" / "surplus" / "meta"  # where surplus_viz reads the stats
_STATS_FILE = _META_DIR / "surplus_stats.csv"


def gen_surplus_statistics(source_dir: Path = _SOURCE_DIR, stats_file: Path = _STATS_FILE) -> pd.DataFrame:
    """Per-year min/max of surplus_kgha and total_kg_N (zeros excluded), written to
    surplus_stats.csv. min_surplus_kgha / max_surplus_kgha are the ones the widget colour scale
    uses (surplus_viz._min_surplus / _max_surplus)."""
    source_dir, stats_file = Path(source_dir), Path(stats_file)
    chunk_files = sorted(source_dir.glob("surplus[0-9]*.parquet"))
    if not chunk_files:
        raise FileNotFoundError(f"No surplus chunks (surplus*.parquet) in {source_dir}. Run build_source.py first.")

    print(f"Creating surplus stats at {stats_file}...", flush=True, end="")
    surplus_df = pd.concat(
        [pd.read_parquet(f, columns=["year", "surplus_kgha", "total_kg_N"]) for f in chunk_files],
        ignore_index=True,
    )

    # Drop zeros before the min/max so nodata / empty pixels don't set the colour-scale bounds.
    nonzero_df = surplus_df[(surplus_df.surplus_kgha != 0) & (surplus_df.total_kg_N != 0)]
    stats = nonzero_df.groupby("year").agg(
        min_surplus_kgha=("surplus_kgha", "min"),
        max_surplus_kgha=("surplus_kgha", "max"),
        min_total_kg_N=("total_kg_N", "min"),  # (legacy used "max" here -- a bug; unused by the widget)
        max_total_kg_N=("total_kg_N", "max"),
    )

    stats_file.parent.mkdir(parents=True, exist_ok=True)
    stats.to_csv(stats_file)
    print("done.")
    return stats


if __name__ == "__main__":
    stats = gen_surplus_statistics()
    print(stats.head())
