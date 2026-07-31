"""Q3 (implementation) + Q4 (why the model shifted).

Q3 parity: does the CURRENT src/data/d8.py legacy path reproduce the pre-refactor (git-HEAD) d8 byte-for-byte? If yes, the recipe's legacy dist_to_sensor is unchanged by the d8 rewrite, so any model shift is the RASTER, not the refactor.

Q4 bucketing sensitivity: for real sites, how much does dist_to_sensor differ legacy-vs-HydroSHEDS, and — the model-relevant number — what fraction of grid cells CHANGE distance bucket under the REG (50 km) and CLF (2 km / 5 km) edges? Cells are identical across rasters (grid_global is fixed); only dist_to_sensor changes, so bucket flips isolate the effect on the aggregated covariates.

    python experiments/raster-investigation/dist_impl_and_buckets.py
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from src.data import access, d8, site_view

_HEAD_D8 = Path("/private/tmp/claude-501/-Users-isaac-dev-sustag/408994b9-8d9b-485d-9731-ba0bfa7507ce/scratchpad/d8_head.py")
_REAL_HS = d8._RASTER_HS
_REG_EDGES = [50_000.0]
_CLF_EDGES = [2_000.0, 5_000.0]


def _reset_d8(source):
    d8._DIRECTION = d8._ACCUM = None
    d8._FLOW_FIELD_CACHE = {}
    d8._SOURCE = None
    d8._RASTER_HS = _REAL_HS if source == "hydrosheds" else Path("/nonexistent_force_legacy.tif")


def q3_parity():
    print("== Q3: current-d8 (legacy path) vs pre-refactor git-HEAD d8 ==")
    if not _HEAD_D8.exists():
        print(f"  (skip: {_HEAD_D8} not found)"); return
    spec = importlib.util.spec_from_file_location("d8_head", _HEAD_D8)
    head = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(head)
    head._RASTER = _REAL_HS.parent / "direction500m.png"  # point the HEAD module at the real legacy raster
    uid = access.get_site_ids()[0]
    lon, lat = access.get_location(uid)
    fh = head.flow_distance_field_ll(lat, lon)
    _reset_d8("legacy")
    fc = d8.flow_distance_field_ll(lat, lon)
    both = np.isfinite(fh) & np.isfinite(fc)
    maxdiff = float(np.nanmax(np.abs(fh[both] - fc[both]))) if both.any() else float("nan")
    print(f"  site {uid}: finite cells HEAD={int(np.isfinite(fh).sum())} current-legacy={int(np.isfinite(fc).sum())} | max|Δ|={maxdiff:.6g} m")
    print(f"  => legacy path {'IDENTICAL (refactor preserved it)' if maxdiff == 0 else 'DIFFERS -- refactor changed legacy!'}\n")


def _bucket(dist, edges):
    return np.digitize(dist, edges)  # 0..len(edges)


def q4_buckets(n_sites=8):
    print("== Q4: legacy vs HydroSHEDS dist_to_sensor + distance-bucket flips ==")
    sites = access.get_site_ids()[:n_sites]
    tot = {"reg": [0, 0], "clf": [0, 0]}  # [flipped, total]
    dd = []
    for uid in sites:
        basin = access.get_basin(uid)
        lon, lat = access.get_location(uid)
        _reset_d8("legacy")
        gl = site_view.build_site_view(basin, lat, lon, label=uid).set_index("node_id")["dist_to_sensor"]
        _reset_d8("hydrosheds")
        gh = site_view.build_site_view(basin, lat, lon, label=uid).set_index("node_id")["dist_to_sensor"]
        j = gl.to_frame("leg").join(gh.rename("hs"), how="inner").dropna()
        if j.empty:
            continue
        med = float(np.median(np.abs(j["leg"] - j["hs"])) / 1000)
        rf = (_bucket(j["leg"].to_numpy(), _REG_EDGES) != _bucket(j["hs"].to_numpy(), _REG_EDGES)).mean()
        cf = (_bucket(j["leg"].to_numpy(), _CLF_EDGES) != _bucket(j["hs"].to_numpy(), _CLF_EDGES)).mean()
        tot["reg"][0] += (_bucket(j["leg"].to_numpy(), _REG_EDGES) != _bucket(j["hs"].to_numpy(), _REG_EDGES)).sum(); tot["reg"][1] += len(j)
        tot["clf"][0] += (_bucket(j["leg"].to_numpy(), _CLF_EDGES) != _bucket(j["hs"].to_numpy(), _CLF_EDGES)).sum(); tot["clf"][1] += len(j)
        dd.append(med)
        print(f"  {uid:15s} cells={len(j):4d} median|Δdist|={med:5.2f}km | REG-bucket flips {rf:5.1%} | CLF-bucket flips {cf:5.1%}")
    print(f"\n  overall: median|Δdist|~{np.median(dd):.2f}km | REG flips {tot['reg'][0]/max(1,tot['reg'][1]):.1%} | CLF flips {tot['clf'][0]/max(1,tot['clf'][1]):.1%}")
    print("  (CLF's 2/5 km edges are near the sensor where a ~1 km shift flips many cells -> the covariates move,")
    print("   even though both rasters are ~equally faithful to NHD -- this is the model-shift mechanism.)")


if __name__ == "__main__":
    q3_parity()
    q4_buckets()
