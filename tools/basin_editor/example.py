"""The shipped example case: delineate at USGS 05455100 (Old Mans Creek near Iowa City, IA -- 520.6 km2 by the authority's own reckoning).

    python tools/basin_editor/example.py

Asserts that basin1's reach-set area agrees with NHDPlus's own `totdasqkm` within 1% -- the same
number two independent accumulations must both produce -- and prints all four methods' areas side
by side (basin0 makes one live NLDI call; pass --offline to skip it).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LAT, LON = 41.606406, -91.615722        # USGS 05455100, Old Mans Creek near Iowa City
USGS_ID = "05455100"


def main(offline: bool = False) -> dict:
    from basin_editor import delineate

    results = {}
    b1 = delineate.basin1(LAT, LON)
    results["basin1"] = b1
    assert b1["status"] == "ok", f"basin1 failed: {b1['status']}"
    rel = abs(b1["area_km2"] - b1["vaa_km2"]) / b1["vaa_km2"]
    assert rel <= 0.01, f"basin1 area {b1['area_km2']:.1f} vs VAA {b1['vaa_km2']:.1f} ({rel:.2%})"
    print(f"  basin1  {b1['area_km2']:>10,.1f} km2  ({len(b1['comids']):,} reaches; "
          f"VAA says {b1['vaa_km2']:,.1f}; {rel:.3%} apart)  OK")

    b2 = delineate.basin2(LAT, LON)
    results["basin2"] = b2
    print(f"  basin2  {b2['area_km2']:>10,.1f} km2  ({b2['status']})")

    b3 = delineate.basin3(LAT, LON, expected_km2=b1["area_km2"])
    results["basin3"] = b3
    print(f"  basin3  {(b3['area_km2'] or float('nan')):>10,.1f} km2  ({b3['status']})")

    if not offline:
        b0 = delineate.basin0(USGS_ID)
        results["basin0"] = b0
        print(f"  basin0  {(b0['area_km2'] or float('nan')):>10,.1f} km2  ({b0['status']})")

    try:
        inside = delineate.sensors_within(b1["polygon"]) if b1["polygon"] is not None else None
    except FileNotFoundError:
        inside = None
        print("  sensors inside: published store not built yet (run the pipeline through p1)")
    if inside is not None:
        print(f"  sensors inside basin1: {len(inside)} "
              f"({inside.site_type.value_counts().to_dict()})")
        results["sensors_within"] = inside
    ws = delineate.to_weighted_set(b1)
    print(f"  weighted set: {len(ws):,} comids at weight 1 -- store THIS in your project files")
    return results


if __name__ == "__main__":
    main(offline="--offline" in sys.argv)
