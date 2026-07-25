"""Run the whole R12 precondition test end to end: pairs -> analysis -> figures.

    python run_all.py            # uses the cached daily-nitrate matrix if present
    python run_all.py --force    # rebuild the daily-nitrate matrix from scratch

Outputs: pairs.parquet, summary.json, fig1..fig4.png. See REPORT.md for the interpretation.
"""

import runpy
import sys

if "--force" in sys.argv:
    import common

    common.build_wide(force=True)

STEPS = [
    "01_build_pairs.py", "02_analyze.py", "03_plots.py",  # core pairwise test + figs
    "04_directed_2hop.py", "05_far_outliers.py",          # follow-ups A, B
    "06_reach_graph.py", "07_upstream_aggregate.py", "08_downstream.py",  # follow-ups C, D, E
]
for step in STEPS:
    print(f"\n########## {step} ##########")
    runpy.run_path(step, run_name="__main__")
