"""E2 -- the static basin-aggregate experiment. Builds the block and runs one masked sweep per pool.

    python 03_run_exp.py --full                  # both tasks, agency-only pool
    python 03_run_exp.py --full --pool all       # include the volunteer programme (read 02's verdict first)
    python 03_run_exp.py --small --task reg      # shakedown

WHY STATIC AND NOT A TIME SERIES. Median 4 samples per station over 25 years, median span 0.5 yr, 9% of results since 2020. A "most recent grab sample" feature would be years stale at most stations and its staleness would vary by organisation -- a nuisance variable wearing a nitrate feature's clothes. A basin-integrated long-run LEVEL asks only for a handful of samples anywhere inside the basin, which is what the data actually provides.

WHY IT IS DEPLOYMENT-LEGAL, which the in-situ discharge block is not. A co-located gauge fails the arbitrary-pin test because the pin has no gauge. A basin aggregate over third-party historical grab samples does not: delineate the basin at any pin, pool whatever samples fall inside. Nothing is read from the target sensor and nothing requires the target to be instrumented.

TWO DISCIPLINES THAT MAKE IT HONEST
  * stations within --exclude-km of the cohort sensor are dropped, mirroring _except_the_target in the neighbour work: without it the "aggregate" is partly the target's own history
  * the block is STATIC and spans decades, so it cannot leak daily variation -- only the level, which is the between-site quantity being predicted. That is legitimate only because the same aggregate is computable at an ungauged pin, which is why the exclusion is not optional

ARMS
    base            the shipped recipe
    grab_level      basin mean + p90 of nitrate as N
    grab_exceed     fraction of basin samples >= 10 mg/L -- the discrete analogue of the CLF label
    grab_coverage   station count + sample count + median era ONLY -- THE NEGATIVE CONTROL
    grab_all        everything

READ grab_coverage FIRST. Station density tracks population, road access and watershed-project funding, all of which correlate with land use. If the coverage-only arm carries the effect, the block is a sampling-effort fingerprint, not a nitrate measurement. Judge on lofo_between_r2 / lofo_between_rate_r2 rather than the headline -- that is the channel this targets -- against the measured floors, and note REG between_r2's floor is 0.0258, the loosest of them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import CACHE, VIOLATION_MGL, cohort, is_volunteer, say

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[0] / "specific-small-experiments"))

from run_exp import MaskedSweep, run_experiment  # noqa: E402
from src.eval.cook import _STRUCTURAL  # noqa: E402
from src.features.recipes import recipe_maker  # noqa: E402

SAMPLES, MEMBERSHIP = CACHE / "wqp_nitrate.parquet", CACHE / "audit_basins.parquet"

_COLS = {
    "level": ["grab_n_mean", "grab_n_p90"],
    "exceed": ["grab_n_exceed"],
    "cover": ["grab_n_stations", "grab_n_samples", "grab_n_era"],
}
_ALL = [c for v in _COLS.values() for c in v]


def build_aggregate(pool: str, exclude_km: float) -> pd.DataFrame:
    """site -> the static block. `pool` is 'agency' or 'all'; `exclude_km` drops stations sitting on the cohort sensor."""
    if not (SAMPLES.exists() and MEMBERSHIP.exists()):
        raise SystemExit("run 01_fetch_wqp.py and 02_audit.py first")
    d = pd.read_parquet(SAMPLES)
    d["vol"] = is_volunteer(d.OrganizationFormalName)
    d["dt"] = pd.to_datetime(d.ActivityStartDate, errors="coerce")
    if pool == "agency":
        d = d[~d.vol]
    mem = pd.read_parquet(MEMBERSHIP)
    j = mem.merge(d, left_on="station", right_on="MonitoringLocationIdentifier")

    if exclude_km > 0:
        from src.data.access import get_location
        import io, urllib.parse, urllib.request
        p = {"statecode": "US:19", "siteType": "Stream", "characteristicName": "Nitrate", "mimeType": "csv", "zip": "no"}
        raw = urllib.request.urlopen("https://www.waterqualitydata.us/data/Station/search?" + urllib.parse.urlencode(p), timeout=600).read().decode(errors="replace")
        stn = pd.read_csv(io.StringIO(raw), dtype=str, low_memory=False)
        stn["slon"] = pd.to_numeric(stn.LongitudeMeasure, errors="coerce")
        stn["slat"] = pd.to_numeric(stn.LatitudeMeasure, errors="coerce")
        j = j.merge(stn[["MonitoringLocationIdentifier", "slon", "slat"]], on="MonitoringLocationIdentifier", how="left")
        own = {}
        for s in cohort():
            q = get_location(s)
            own[s] = (q[0], q[1]) if abs(q[0]) > 50 else (q[1], q[0])
        olon = j.site.map(lambda s: own.get(s, (np.nan, np.nan))[0])
        olat = j.site.map(lambda s: own.get(s, (np.nan, np.nan))[1])
        lat0 = np.radians((olat + j.slat) / 2)
        dist = 111.32 * np.hypot((olon - j.slon) * np.cos(lat0), olat - j.slat)
        before = len(j)
        j = j[~(dist < exclude_km)]
        say(f"  excluded {before - len(j):,} samples within {exclude_km} km of their own cohort sensor")

    g = j.groupby("site")
    agg = pd.DataFrame({
        "grab_n_mean": g.n_mgl.mean(),
        "grab_n_p90": g.n_mgl.quantile(0.9),
        "grab_n_exceed": g.n_mgl.apply(lambda v: (v >= VIOLATION_MGL).mean()),
        "grab_n_stations": g.station.nunique(),
        "grab_n_samples": g.size(),
        "grab_n_era": g.dt.median().dt.year,
    }).reindex(cohort())
    say(f"  aggregate built for {agg.grab_n_samples.notna().sum()}/{len(agg)} basins "
        f"(median {agg.grab_n_samples.median():.0f} samples, {agg.grab_n_stations.median():.0f} stations)")
    return agg


def build(task: str, agg: pd.DataFrame):
    shipped = recipe_maker(task, window=1)

    def wide(site_uid):
        df = shipped(site_uid)
        row = agg.loc[site_uid] if site_uid in agg.index else None
        for c in _ALL:
            df[c] = float(row[c]) if row is not None and pd.notna(row[c]) else np.nan
        return df

    def masks(pool):
        drop = set(_STRUCTURAL) | {"nitrate_con", "violation"}
        cols = [c for c in pool.columns if c not in drop]
        missing = [c for c in _ALL if c not in pool.columns]
        if missing:
            raise RuntimeError(f"columns absent from the pool: {missing}")
        blk = {k: [c for c in cols if c in set(v)] for k, v in _COLS.items()}
        nbr = {c for v in blk.values() for c in v}
        base = [c for c in cols if c not in nbr]
        return {"base": base,
                "grab_level": base + blk["level"],
                "grab_exceed": base + blk["exceed"],
                "grab_coverage": base + blk["cover"],
                "grab_all": base + sorted(nbr)}

    return MaskedSweep(wide=wide, masks=masks, base_cols=lambda pool: masks(pool)["base"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=["reg", "clf", "both"], default="both")
    ap.add_argument("--pool", choices=["agency", "all"], default="agency", help="read 02_audit.py's verdict before choosing 'all'")
    ap.add_argument("--exclude-km", type=float, default=1.0, help="drop grab stations this close to the cohort sensor itself")
    ap.add_argument("--mode", default="full", choices=["test", "small", "med", "full"])
    ap.add_argument("--extra", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()

    say(f"building the basin aggregate  (pool={a.pool}, exclude {a.exclude_km} km)")
    agg = build_aggregate(a.pool, a.exclude_km)
    for task in (["reg", "clf"] if a.task == "both" else [a.task]):
        say(f"\n{'=' * 78}\n{task.upper()}  static discrete-sample basin aggregate\n{'=' * 78}")
        run_experiment(
            question=("Does a static basin-integrated aggregate of third-party DISCRETE nitrate samples improve "
                      "the between-site channel over the shipped recipe, and is it the level or just sampling effort?"),
            recipes=lambda t, _a=agg: build(t, _a),
            task=task, kind="masked", mode=a.mode, extra=a.extra, seed=a.seed,
            id=f"grab_{a.pool}",
        )


if __name__ == "__main__":
    main()
