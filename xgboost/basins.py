"""Basin delineation for the cohort -- the one piece of geometry the access layer deliberately does not publish.

WHY THIS LIVES HERE. `data.access` publishes the network, the snap and the upstream walk, but not basins: which delineation a model uses is a preference (reach-set accumulation, D8 flood fill, NLDI's answer, a hand-drawn polygon), and different experiments legitimately want different ones. So the project builds its own and owns the consequences.

THE METHOD IS ACCUMULATE-AFTER-SNAP, and its guards are the ones the pipeline paid for: a coastline reach has no drainage line to walk (walking one collects every river reaching the sea along it -- 251,225 km2 for a site whose own reach drains 20), an oversized basin is a snap that landed on the wrong river, and a `far` snap is a coordinate the network could not place. Each is recorded as a column rather than silently dropped, because a bad delineation that reaches the model as features is the failure mode worth catching.

CACHED BY SNR CODE. The walk is seconds; hundreds of sites across dozens of experiments is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# APPEND, never insert: this directory is named `xgboost`, so putting the repo root at the
# FRONT of sys.path would shadow the real xgboost library with this project. Appending keeps
# `data.access` importable while `import xgboost` still finds site-packages.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from data.access import api                                        # noqa: E402
from data.access.machinery import accumulate as acc                # noqa: E402

import config                                                      # noqa: E402

MAX_CONUS_KM2 = 3.5e6      # above any real CONUS drainage: the Mississippi at Natchez is ~2.97M
AREA_TOL = 0.05            # |walked - VAA totdasqkm| / VAA above this is flagged, not dropped


def _path(code: str) -> Path:
    return config.CACHE / "basins" / f"{code}.parquet"


def build(code: str, comid: int, force: bool = False) -> pd.DataFrame:
    """The weighted COMID set upstream of `comid`, cached by SNR code.

    Columns: comid, w (1.0 -- a plain accumulate has no partial membership), areasqkm, pathlength, flow_dist_m, pathtimema. Empty frame when the walk is refused (non-drainage reach) or the reach is unknown.
    """
    p = _path(code)
    if p.exists() and not force:
        return pd.read_parquet(p)
    config.ensure_dirs()
    net = acc.Network.load()
    if not net.drains(comid):
        out = pd.DataFrame(columns=["comid", "w", "areasqkm", "pathlength", "flow_dist_m", "pathtimema"])
        out.to_parquet(p)
        return out
    comids, _km2, _own, _note = net.accumulate(comid)
    vaa = api.get_network("vaa")
    v = vaa.set_index("comid")
    keep = [c for c in comids if c in v.index]
    sub = v.loc[keep]
    # flow distance to the outlet: pathlength is measured to the TERMINAL outlet, so the difference is the
    # channel distance from each upstream reach down to this sensor's reach. Kilometres in VAA, metres here.
    base = float(v.loc[comid, "pathlength"]) if comid in v.index else 0.0
    out = pd.DataFrame({
        "comid": sub.index.astype("int64"),
        "w": 1.0,
        "areasqkm": pd.to_numeric(sub.get("areasqkm"), errors="coerce").to_numpy(),
        "pathlength": pd.to_numeric(sub.get("pathlength"), errors="coerce").to_numpy(),
        "pathtimema": pd.to_numeric(sub.get("pathtimema"), errors="coerce").to_numpy()
        if "pathtimema" in sub.columns else pd.NA,
    })
    out["flow_dist_m"] = ((out.pathlength - base) * 1000.0).clip(lower=0.0)
    out.to_parquet(p)
    return out


def audit(sites: pd.DataFrame) -> pd.DataFrame:
    """One row per cohort site: walked area, VAA's own total drainage area, their discrepancy, and the guards.

    The guards are FLAGS, not filters -- the caller decides. `refused` means the reach does not drain (coastline); `oversized` is above any CONUS drainage; `area_off` is a walked area disagreeing with the authority's by more than AREA_TOL, which is how a snap onto the wrong river shows itself.
    """
    vaa = api.get_network("vaa").set_index("comid")
    rows = []
    for r in sites.itertuples():
        comid = int(r.comid) if pd.notna(r.comid) else None
        if comid is None:
            rows.append(dict(code=r.snr_id, comid=None, n_reaches=0, km2=float("nan"),
                             vaa_km2=float("nan"), area_err=float("nan"),
                             refused=True, oversized=False, area_off=False))
            continue
        wset = build(r.snr_id, comid)
        km2 = float(wset.areasqkm.sum()) if len(wset) else float("nan")
        vaa_km2 = float(vaa.loc[comid, "totdasqkm"]) if comid in vaa.index else float("nan")
        err = abs(km2 - vaa_km2) / vaa_km2 if vaa_km2 and vaa_km2 == vaa_km2 and vaa_km2 > 0 else float("nan")
        rows.append(dict(code=r.snr_id, comid=comid, n_reaches=len(wset), km2=km2, vaa_km2=vaa_km2,
                         area_err=err, refused=not len(wset),
                         oversized=bool(km2 == km2 and km2 > MAX_CONUS_KM2),
                         area_off=bool(err == err and err > AREA_TOL)))
    return pd.DataFrame(rows)
