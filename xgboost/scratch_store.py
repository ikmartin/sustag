"""Build a SCRATCH published store from the assembled records, for development before p1 completes.

WHY THIS EXISTS AND WHEN IT SHOULD NOT BE USED. p1 halts wholesale at the quality gate, so until a person answers the quality queue there is no published store and nothing in this project can resolve a cohort. This assembles the store p1 *would* write, minus the quality masks, into a scratch directory, and points `data.access` at it for the life of one process.

IT IS NOT THE REAL STORE AND MUST NEVER BE CONFUSED FOR ONE. No quality masks are applied, so records here include days a reviewer may later withhold; the partition is not computed; nothing is verified. Numbers produced against it are a DEVELOPMENT SIGNAL -- they establish that the modeling chain runs and roughly where it lands, not what the model scores. Re-run every experiment against the real store once p1 completes.

Usage:  import scratch_store; scratch_store.activate()   # before importing cohort
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

SCRATCH = Path(__file__).resolve().parent / "cache" / "scratch_published"


def build(force: bool = False) -> Path:
    """Assemble a published-shaped store from derived artifacts. Returns its root."""
    from data.build import config as bcfg

    water = SCRATCH / "water"
    if (SCRATCH / "sensors.parquet").exists() and not force:
        return SCRATCH
    water.mkdir(parents=True, exist_ok=True)

    sensors = pd.read_parquet(bcfg.SENSORS_PATH)
    sensors.to_parquet(SCRATCH / "sensors.parquet")

    merged = bcfg.WATER_MERGED
    n = 0
    for src in sorted(merged.glob("*.parquet")):
        dst = water / src.name
        if not dst.exists():
            shutil.copyfile(src, dst)
        n += 1
    # covariates: the access layer reads them from published/comid_features, which p1 also gates
    feats = SCRATCH / "comid_features"
    feats.mkdir(parents=True, exist_ok=True)
    cov_src = bcfg.STORES / "derived" / "covariates"
    for src in sorted(cov_src.glob("*.parquet")):
        dst = feats / src.name
        if not dst.exists():
            shutil.copyfile(src, dst)
    print(f"  scratch covariates: {len(list(feats.glob('*.parquet')))} product(s)")

    # the access layer reads these two lazily; empty frames keep it from raising
    for name, cols in (("quality.parquet", ["code", "channel", "verdict"]),
                       ("proximity.parquet", ["uid_a", "uid_b", "sep_m"])):
        p = SCRATCH / name
        if not p.exists():
            pd.DataFrame(columns=cols).to_parquet(p)
    print(f"  scratch store: {n} record(s), {len(sensors):,} sensor rows -> {SCRATCH}")
    return SCRATCH


def activate(force: bool = False) -> Path:
    """Build the scratch store and repoint `data.access.config` at it, in this process only."""
    root = build(force=force)
    from data.access import config as acfg

    acfg.PUBLISHED = root
    acfg.PUB_SENSORS = root / "sensors.parquet"
    acfg.PUB_WATER = root / "water"
    acfg.PUB_QUALITY = root / "quality.parquet"
    acfg.PUB_PROXIMITY = root / "proximity.parquet"
    acfg.PUB_COMID_FEATURES = root / "comid_features"
    print(f"  data.access repointed at the SCRATCH store (development only)")
    return root


if __name__ == "__main__":
    activate(force="--force" in sys.argv)
