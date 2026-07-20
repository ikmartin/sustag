"""
The methods here verify the data pipelines internal coherence:

  * global_tables       -- the interim tables load with the expected schema.
  * water / basins      -- the read accessors + D8 raster load and are self-consistent.
  * get_data            -- this assembles every field; grid membership + joins are coherent.
  * virtual_equals_real -- build_virtual_site_data(stored basin) reproduces get_data(uid) exactly
                           (the invariant that the virtual and real site paths are one code path).

Run: python -m src.selftest [name ...]
"""

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

_SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(_SRC.parent))

from src.data import access

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
SAMPLE = 5


class Result:
    def __init__(self, name, status, summary):
        self.name, self.status, self.summary = name, status, summary


def _sample_sites(n=SAMPLE):
    uids = [str(u) for u in access.get_site_ids()]
    step = max(1, len(uids) // n)
    return uids[::step][:n]


def test_global_tables() -> Result:
    from src.data.site_view import _grid_global

    gg = _grid_global()
    cg, sp = access._crops_global(), access._surplus_global()
    wf = access._weather_global_files()
    bad = []
    if not {"global_node_id", "x", "y", "lat", "lon", "cell_area", "geometry"}.issubset(gg.columns):
        bad.append("grid_global schema")
    if not {"global_node_id", "year"}.issubset(cg.columns):
        bad.append("crops_global schema")
    if not {"global_node_id", "year", "surplus_kgha", "total_kg_N"}.issubset(sp.columns):
        bad.append("surplus_global schema")
    if not wf:
        bad.append("no weather_global years")
    if not gg["global_node_id"].is_unique:
        bad.append("grid_global global_node_id not unique")
    return Result(
        "global_tables",
        PASS if not bad else FAIL,
        f"grid {len(gg):,} cells, crops {cg['year'].nunique()}y, surplus {sp['year'].nunique()}y, "
        f"weather {len(wf)}y; issues {bad}",
    )


def test_water() -> Result:
    ids = access.get_site_ids()
    ok = (
        len(ids) > 0
        and not access.get_metadata().empty
        and not access.get_water(str(ids[0])).empty
        and not access.get_all_stats().empty
    )
    return Result("water", PASS if ok else FAIL, f"{len(ids)} sites; metadata/water/stats readable")


def test_basins() -> Result:
    from src.data import d8

    meta = access.get_basin_metadata()
    direction = d8.load_direction_array()
    bad = []
    if direction.shape != (d8.H, d8.W):
        bad.append("raster shape")
    if access.get_all_basins().empty:
        bad.append("get_all_basins empty")
    # D8 pixel mapping round-trips into the raster extent for all sites
    locs = access.get_metadata()
    for _, r in locs.iterrows():
        c, rw = d8.ll_to_image_pixel(r["latitude"], r["longitude"])
        if not (0 <= c < d8.W and 0 <= rw < d8.H):
            bad.append(f"{r['site_uid']}:pixel-oob")
            break
    return Result("basins", PASS if not bad else FAIL, f"{len(meta)} sites in preferred_basin; issues {bad}")


def test_get_data() -> Result:
    from src.data.site_view import _grid_global

    gids = set(_grid_global()["global_node_id"])
    sample = _sample_sites()
    bad = []
    for uid in sample:
        sd = access.get_data(uid)
        if not {"basin", "crops", "grid", "surplus", "water", "weather"}.issubset(set(sd.available())):
            bad.append(f"{uid}:fields")
            continue
        g = sd.grid
        if not set(g["global_node_id"]).issubset(gids):
            bad.append(f"{uid}:membership")
        if not g["frac_cell_in_basin"].between(0, 1).all():
            bad.append(f"{uid}:frac")
        if not np.isfinite(g["dist_to_sensor"]).all():
            bad.append(f"{uid}:dist")
        if list(g["node_id"]) != list(range(len(g))):
            bad.append(f"{uid}:node_id")
        if not (sd.basin_area > 0):
            bad.append(f"{uid}:area")
        if not set(sd.crops["global_node_id"]).issubset(set(g["global_node_id"])):
            bad.append(f"{uid}:crops_cells")
        if not set(sd.surplus["global_node_id"]).issubset(set(g["global_node_id"])):
            bad.append(f"{uid}:surplus_cells")
    return Result("get_data", PASS if not bad else FAIL, f"{len(sample)} sites; issues {bad}")


def test_virtual_equals_real() -> Result:
    sample = _sample_sites()
    bad = []
    for uid in sample:
        lon, lat = access.get_location(uid)
        st = access.get_stats(uid).iloc[0]
        ws = pd.to_datetime(st["start_date"]).tz_localize(None).normalize() - pd.Timedelta(days=60)
        we = pd.to_datetime(st["last_date"]).tz_localize(None).normalize() + pd.Timedelta(days=60)
        virt = access.build_virtual_site_data(access.get_basin(uid), lat, lon, weather_start=ws, weather_end=we)
        real = access.get_data(uid)
        for fld in ("grid", "crops", "surplus", "weather"):
            if not getattr(virt, fld).reset_index(drop=True).equals(getattr(real, fld).reset_index(drop=True)):
                bad.append(f"{uid}:{fld}")
        if abs(virt.basin_area - real.basin_area) > 1e-6:
            bad.append(f"{uid}:area")
    return Result("virtual_equals_real", PASS if not bad else FAIL, f"{len(sample)} sites; mismatches {bad}")


def test_splits() -> Result:
    """The leakage-aware CV: conflict components form, folds cover 0..4, and a holdout has NO
    hard (both-axes) leaks across the train/test boundary."""
    from src.splits import conflict_graph as cg

    groups = cg.split_groups()
    folds = cg.make_folds()
    tr, te = cg.holdout_split(test_size=0.2)
    audit = cg.audit_split(tr, te)
    hard = 0 if audit.empty else int((audit["severity"] == "hard").sum())
    ok = len(set(groups.values())) > 0 and set(folds["fold"]) == {0, 1, 2, 3, 4} and hard == 0
    return Result(
        "splits",
        PASS if ok else FAIL,
        f"{len(groups)} sites, {len(set(groups.values()))} conflict components, "
        f"folds {sorted(set(folds['fold']))}, holdout {len(tr)}/{len(te)}, hard leaks {hard}",
    )


def test_features() -> Result:
    """build_feature_frame produces a well-formed, mostly-finite model matrix for a real site.

    Asserts the expected feature FAMILIES are present (column *count* varies legitimately with
    basin size -- a compact basin has only the near `_b0` distance bucket, which predict NaN-fills).
    """
    from src.features.recipes import build_feature_frame

    bad = []
    sample = _sample_sites(3)
    for uid in sample:
        sd = access.get_data(uid)
        for task in ("reg", "clf"):
            fr = build_feature_frame(sd, task=task)
            cols = set(fr.columns)
            has = (
                len(fr) > 0
                and {"date", "doy_sin", "doy_cos"} <= cols
                and any(c.startswith("precip_in_1d") for c in cols)  # weather family
                and any("Corn" in c for c in cols)  # crop family
                and any(c.startswith("rest_of_state_nitrate_lag") for c in cols)  # neighbour
            )
            if not has:
                bad.append(f"{uid}:{task}:families")
                continue
            finite = np.isfinite(fr.select_dtypes("number").to_numpy()).mean()
            if finite < 0.80:
                bad.append(f"{uid}:{task}:finite={finite:.2f}")
    return Result("features", PASS if not bad else FAIL, f"{len(sample)} sites x (reg, clf); issues {bad}")


_TESTS = {
    "global_tables": test_global_tables,
    "water": test_water,
    "basins": test_basins,
    "get_data": test_get_data,
    "virtual_equals_real": test_virtual_equals_real,
    "splits": test_splits,
    "features": test_features,
}


def main(argv=None) -> int:
    names = [a for a in (sys.argv[1:] if argv is None else argv) if not a.startswith("-")] or list(_TESTS)
    unknown = [n for n in names if n not in _TESTS]
    if unknown:
        print(f"unknown test(s): {unknown}; available: {list(_TESTS)}")
        return 2
    results = [_TESTS[n]() for n in names]
    width = max(len(r.name) for r in results)
    failed = False
    for r in results:
        print(f"  [{r.status:4}] {r.name:<{width}}  {r.summary}")
        failed |= r.status == FAIL
    n_pass = sum(r.status == PASS for r in results)
    n_fail = sum(r.status == FAIL for r in results)
    print(f"\n{n_pass} passed, {n_fail} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
