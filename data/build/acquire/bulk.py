"""A3 -- the bulk fetch: observations, network layers, weather, attribute tables. Everything fetched, nothing derived.

Branch order mirrors the dependency and API topology: records (waterdata API), network (hydro.usgs.gov), weather (NCSS), attributes (EPA/S3/NID) -- records' internal fan-out is the only API concurrency knob, and the other branches do not fan out, so running them serially here keeps the load story identical to the ancestor's measured one. NWM is A3-late: it needs the sensor-COMID list D3 produces, captured as a parameter file so derivation stays pure.
"""

from __future__ import annotations

from .. import config


def main(force: bool = False, only: str | None = None, snapshot: str | None = None) -> None:
    from . import attributes, facilities, network, records, snapshot, weather

    config.ensure_dirs()
    branches = {"records": lambda: records.main(force=force),
                "network": lambda: network.main(force=force),
                "weather": lambda: weather.main(force=force),
                "attributes": lambda: attributes.main(force=force),
                "facilities": lambda: facilities.main(force=force)}
    want = [only] if only else list(branches)
    bad = [w for w in want if w not in branches]
    if bad:
        raise SystemExit(f"unknown A3 branch {bad}; known: {list(branches)}")
    for name in want:
        print(f"\n[a3:{name}]", flush=True)
        branches[name]()
    # EVERY A3 PASS ENDS BY CUTTING A SNAPSHOT -- the drift path is dead without it: HEAD never advances,
    # A4 never has a parent to diff against, and no revision is ever classified.
    print("\n[a3:snapshot]", flush=True)
    from . import snapshot as snap_store

    if snap_store.head() is None:
        print("  no snapshot store yet -- run the adoption pass first (snapshot.adopt)")
    else:
        snap_store.cut(notes=f"a3 pass ({', '.join(want)})")
