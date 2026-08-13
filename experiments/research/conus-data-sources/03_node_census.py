"""G4 (partial) -- the CONUS node population for the GNN, by modality.

A GNN needs nodes, not labels. Counts every USGS continuous series CONUS-wide per covariate family, so the node population can be sized before any graph exists. Families come from `experiments/research/new-water-features/pcodes.py`, which established that USGS codes one physical quantity under several pcodes (turbidity has five) and that querying one code per quantity undercounts.

WHAT THIS DOES NOT DO. Connected coverage on the reach graph. Straight-line proximity is an UPPER BOUND on donor availability -- two sites 5 km apart on different tributaries share no water -- and the CONUS reach graph does not exist yet. See the report for the method and its cost.

Writes data/node_census.csv.

    python experiments/research/conus-data-sources/03_node_census.py
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
_OUT = Path(__file__).resolve().parent / "data" / "node_census.csv"

CONUS_BBOX = [-125.0, 24.0, -66.5, 49.5]

# family -> pcodes. Multi-pcode families are the ones a naive single-code query undercounts.
FAMILIES = {
    "discharge":     ["00060"],
    "stage":         ["00065", "63160", "63158"],
    "water_temp":    ["00010", "00011"],
    "spec_cond":     ["00095"],
    "turbidity":     ["63680", "63675", "72213", "63682", "00070"],
    "diss_oxy":      ["00300", "00301"],
    "ph":            ["00400"],
    "nitrate":       ["99133", "99137", "00630"],
    "precip_site":   ["00045", "99772"],
    "groundwater":   ["72019", "62611", "62610"],
    "soil_moisture": ["74207", "72181"],
    "soil_temp":     ["72253", "62846"],
    "velocity":      ["72255", "72254"],
    "fdom":          ["32295", "32322", "32330"],
}


def _auth() -> None:
    with open(_ROOT / "src" / "build" / "api-keys.toml", "rb") as f:
        os.environ["API_USGS_PAT"] = tomllib.load(f)["usgs"]


def main() -> None:
    _auth()
    from dataretrieval import waterdata

    rows = []
    for fam, pcodes in FAMILIES.items():
        per_pcode, frames = {}, []
        for pc in pcodes:
            try:
                res = waterdata.get_time_series_metadata(parameter_code=pc, bbox=CONUS_BBOX, skip_geometry=True)
                df = res[0] if isinstance(res, tuple) else res
            except Exception as e:
                print(f"  {fam}/{pc}: {type(e).__name__}")
                continue
            if df is None or len(df) == 0:
                per_pcode[pc] = 0
                continue
            per_pcode[pc] = df.monitoring_location_id.nunique()
            frames.append(df[["monitoring_location_id"]])
        union = pd.concat(frames).monitoring_location_id.nunique() if frames else 0
        best = max(per_pcode.values()) if per_pcode else 0
        rows.append(dict(family=fam, n_pcodes=len(pcodes), sites_union=union,
                         sites_best_single=best, undercount=union - best,
                         per_pcode="; ".join(f"{k}={v}" for k, v in per_pcode.items())))
        print(f"  {fam:14} union={union:6,d}  best_single={best:6,d}  undercount={union-best:+5,d}")

    out = pd.DataFrame(rows).sort_values("sites_union", ascending=False)
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(_OUT, index=False)

    tot = out.sites_union.sum()
    print(f"\n  total series-carrying sites summed over families: {tot:,} (sites appear in several families)")
    print(f"  nitrate labels vs discharge nodes: "
          f"{int(out.loc[out.family=='nitrate','sites_union'].iloc[0]):,} vs "
          f"{int(out.loc[out.family=='discharge','sites_union'].iloc[0]):,}")
    print(f"\nwrote {_OUT.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
