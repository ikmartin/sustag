"""A3 -- the bulk fetch: observations, network layers, weather, attribute tables. Everything fetched, nothing derived.

Branch order mirrors the dependency and API topology: records (waterdata API), network (hydro.usgs.gov), weather (NCSS), attributes (EPA/S3/NID) -- records' internal fan-out is the only API concurrency knob, and the other branches do not fan out, so running them serially here keeps the load story identical to the ancestor's measured one. NWM is A3-late: it needs the site-COMID list D3 produces, captured as a parameter file so derivation stays pure.
"""

from __future__ import annotations

from .. import config


def main(force: bool = False, only: str | None = None, snapshot: str | None = None) -> None:
    from . import attributes, network, records, seed_basins, weather

    config.ensure_dirs()
    branches = {"records": lambda: records.main(force=force),
                "network": lambda: network.main(force=force),
                "weather": lambda: weather.main(force=force),
                "attributes": lambda: attributes.main(force=force),
                "basins": lambda: seed_basins.fetch_authority_basins(force=force)}
    want = [only] if only else list(branches)
    bad = [w for w in want if w not in branches]
    if bad:
        raise SystemExit(f"unknown A3 branch {bad}; known: {list(branches)}")
    for name in want:
        print(f"\n[a3:{name}]", flush=True)
        branches[name]()
