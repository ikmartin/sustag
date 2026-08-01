"""Refit the island models with the nitrate-removal outflow sites dropped from the cohort.

WHY. ~6 cohort sites sit immediately downstream of an engineered nitrate-removal feature (CREP wetland weir, restored oxbow, saturated buffer, nutrient-trading reach). Their nitrate is set by the structure, not by the watershed, so every covariate the island recipe has says "high-nitrate ag catchment" while the sensor reads 0.85. No feature in any recipe can express that. This measures what they cost.

HOW. train.py has no --sites argument -- build() calls get_site_ids() directly (train.py:163) and passes sites=None into compare_many/fit_full. So the cohort is narrowed by rebinding that name. train.py and cook.py EACH hold their own binding from `from src.data.access import get_site_ids`, so patching src.data.access alone does nothing; both module attributes must be set.

src.features.features.get_site_ids is deliberately NOT patched. features.py:740 uses it to build the statewide pooled baselines (`rest_of_state_nitrate_lag*`, `roll_n_avg_except_this7d`). Patching it would change those FEATURE VALUES as well as the cohort, and the delta would no longer be attributable to the cohort change alone. Left alone, the features are identical between arms and only the training/eval population moves.

RUN. `python experiments/treatment-site-holdout/run.py` from the repo root. Both arms fit in one invocation, on purpose: fulltrain_logs.json's existing island_REG/island_CLF entries predate the 2026-07-31 D8 fix and are NOT a valid baseline, so the full-cohort arm is refitted here rather than read back.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

import src.data.access as access  # noqa: E402
import src.eval.cook as cook  # noqa: E402
import src.models.train as train  # noqa: E402

# Sites immediately downstream of an engineered nitrate-removal feature, all in the 116-site cohort. Basis is the IWQIS metadata (`iwqis_description` / `iwqis_river` / `iwqis_nickname`), not distance.
DROP = {
    "WQS0008",  # CREP wetland OUTFLOW, Slough Creek -- description says so outright; inflow pair is WQS0012 (median 8.39 -> 5.17)
    "WQS0026",  # Catfish Creek nutrient-trading study, downstream member; pair WQS0025 (median 3.06 -> 0.93)
    "WQS0107",  # Eagle Creek Oxbow                  -- median 0.85
    "WQS0060",  # Frye Oxbow                         -- median 1.19
    "WQS0108",  # Prairie Creek / Boone River Oxbow  -- median 1.32
    "WQS0080",  # Saturated Buffer                   -- median 4.23
    "WQS0066",  # routing and dilution, not treatment. Borderline, median 3.78, 25% below WQS0065.
}

# Judgment call, also discluded. An engineered channel is not a removal structure -- its 25% deficit against WQS0065 is routing and dilution, not treatment. Add it to DROP to test that reading.
_BORDERLINE = {"WQS0066"}  # Monona-Harrison Ditch


def _patch(drop: set[str]) -> None:
    """Rebind get_site_ids in every module that resolves the cohort for training. NOT features.py -- see module docstring."""
    real = access.get_site_ids

    def filtered():
        return [s for s in real() if str(s) not in drop]

    train.get_site_ids = filtered
    cook.get_site_ids = filtered


def _fit(task: str, name: str) -> None:
    recipe = train._RECIPES[(task, "island")]
    train.build(
        train._next_free_name(name),
        recipe,
        target_col=train._TARGETS[task],
        task=task,
        xgb=train.xgb_for(recipe, task),
    )


def main() -> None:
    n_all = len(access.get_site_ids())
    missing = DROP - {str(s) for s in access.get_site_ids()}
    if missing:
        raise SystemExit(f"not in the cohort, so nothing to drop: {sorted(missing)}")

    for task in ("reg", "clf"):
        _patch(set())  # full cohort -- the paired baseline, refitted post-D8-fix
        print(f"\n########## {task.upper()}  full cohort ({n_all} sites) ##########")
        _fit(task, f"treat_all_island_{task.upper()}")

        _patch(DROP)
        print(f"\n########## {task.upper()}  minus {len(DROP)} removal-outflow sites ({n_all - len(DROP)}) ##########")
        _fit(task, f"treat_drop_island_{task.upper()}")

    print(
        "\nBoth arms are in logs/fulltrain_logs.json. Compare treat_all_* against treat_drop_* -- "
        "and read lofo_macro_r2 / lofo_macro_auc, not the pooled headline: dropping 6 of 116 sites "
        "changes the pooled denominator, so a pooled gain is partly arithmetic. The per-site median is the honest read."
    )


if __name__ == "__main__":
    main()
