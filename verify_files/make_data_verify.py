"""Snapshot the processed data store, for comparison after the build2 rewrite.

The rewrite changes `src/data/` IN PLACE, so this is the only chance to record a "before". Run it again after the rewrite and diff on (scope, metric).

Every row carries a `match` column saying what a comparison should expect, because a verify file that does not distinguish "must be identical" from "will legitimately move" is a trap -- someone reads a correct difference as a regression:

    exact      same underlying observations, same rule -> byte-equal
    tol_1pct   same rasters through the same aggregation; only cell numbering changes
    tol_5pct   delineation method changes (NLDI/flowline-snap -> upstream catchment accumulation)
    context    EXPECTED to change (scope, cohort, grid size). Recorded to interpret the rest.

Sites in the four known duplicate-registration pairs are flagged in `note`: merging them concatenates two records, so their water metrics move by design.

    python verify_files/make_data_verify.py
"""

from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_OUT = Path(__file__).resolve().parent / "data_verify.csv"

import src.data.access as A  # noqa: E402
from src.build.config import get_covariate_bbox  # noqa: E402

# Near-coincident registrations of one physical gauge. Merging concatenates their records.
MERGE_PAIRS = {"WQS0032", "USGS-05483600", "WQS0081", "USGS-05481000",
               "WQS0024", "USGS-05451210", "WQS0082", "USGS-05482500"}

CROP_YEAR, SURPLUS_YEAR, WEATHER_YEAR = 2020, 2015, 2020

rows: list[dict] = []


def add(scope, metric, value, match, note=""):
    rows.append(dict(scope=scope, metric=metric, value=value, match=match, note=note))


def main() -> None:
    ids = A.get_site_ids()
    meta = A.get_metadata().set_index("site_uid")
    bmeta = A.get_basin_metadata().set_index("site_uid")

    # ---- global context: expected to change, needed to read everything else ----
    grid = pd.read_parquet(_ROOT / "src/data/interim/grid_global.parquet")
    add("GLOBAL", "n_sites", len(ids), "context", "cohort grows; build2 does not filter sites")
    add("GLOBAL", "n_grid_cells", len(grid), "context", "AOE is ~2.7x larger")
    add("GLOBAL", "covariate_bbox", str(list(get_covariate_bbox())), "context", "replaced by the 3 AOE boxes")
    g = A.get_basin_graph()
    add("GLOBAL", "graph_nodes", g.number_of_nodes(), "context", "")
    add("GLOBAL", "graph_edges", g.number_of_edges(), "context", "")
    import networkx as nx
    add("GLOBAL", "graph_is_dag", int(nx.is_directed_acyclic_graph(g)), "exact",
        "0 today: the duplicate-registration 2-cycles. MUST be 1 after merge")

    # ---- per-site ----
    wyear = pd.read_parquet(_ROOT / f"src/data/interim/weather_global_{WEATHER_YEAR}.parquet",
                            columns=["global_node_id", "precip_gridmet"])
    wmean = wyear.groupby("global_node_id").precip_gridmet.mean()

    for uid in ids:
        note = "duplicate-registration pair; record changes on merge" if uid in MERGE_PAIRS else ""
        if uid in meta.index:
            add(uid, "lat", round(float(meta.at[uid, "latitude"]), 6), "exact", note)
            add(uid, "lon", round(float(meta.at[uid, "longitude"]), 6), "exact", note)

        w = A.get_water(uid)
        v = w.nitrate_con.dropna()
        add(uid, "water_n_days", int(len(v)), "exact", note)
        add(uid, "water_first_day", str(v.index.min())[:10], "exact", note)
        add(uid, "water_last_day", str(v.index.max())[:10], "exact", note)
        add(uid, "water_median", round(float(v.median()), 4), "exact", note)
        add(uid, "water_max", round(float(v.max()), 4), "exact", note)
        add(uid, "water_n_ge10", int((v >= 10).sum()), "exact", note)

        if uid in bmeta.index:
            # basin_name is a FILENAME ("{uid}_basin{N}.parquet"); the area column is area{N}.
            m = re.search(r"_basin(\d)\.parquet$", str(bmeta.at[uid, "basin_name"]))
            an = f"area{m.group(1)}" if m else None
            if m and an in bmeta.columns and pd.notna(bmeta.at[uid, an]):
                add(uid, "basin_area_km2", round(float(bmeta.at[uid, an]), 4), "tol_5pct",
                    "delineation method changes")
                add(uid, "basin_variant", m.group(1), "context", "basin0-3 selection is retired in build2")

        try:
            gr = A.get_grid(uid)
        except Exception as e:
            add(uid, "grid_status", f"FAIL {type(e).__name__}", "exact", note)
            continue
        add(uid, "grid_n_cells", int(len(gr)), "tol_5pct", "")
        add(uid, "grid_coverage", round(float(gr.frac_cell_in_basin.sum()), 4), "tol_5pct", "")
        add(uid, "grid_area_km2", round(float((gr.cell_area * gr.frac_cell_in_basin).sum() / 1e6), 4),
            "tol_5pct", "")
        add(uid, "grid_max_dist_km", round(float(np.nanmax(gr.dist_to_sensor)) / 1000, 4), "tol_5pct", "")

        frac = gr.set_index("global_node_id").frac_cell_in_basin

        def wmean_of(df, col, year=None):
            d = df if year is None else df[df.year == year]
            if not len(d):
                return None
            s = d.set_index("global_node_id")[col].reindex(frac.index)
            w = frac.reindex(s.index)
            ok = s.notna() & w.notna()
            return None if not ok.any() else float((s[ok] * w[ok]).sum() / w[ok].sum())

        try:
            c = A.get_crops(uid)
            tot = c[[x for x in c.columns if x not in ("node_id", "global_node_id", "year")]].sum(axis=1)
            c = c.assign(corn_frac=c.Corn / tot.replace(0, np.nan))
            v = wmean_of(c, "corn_frac", CROP_YEAR)
            if v is not None:
                add(uid, f"corn_frac_{CROP_YEAR}", round(v, 6), "tol_1pct", "")
        except Exception:
            pass
        try:
            v = wmean_of(A.get_surplus(uid), "surplus_kgha", SURPLUS_YEAR)
            if v is not None:
                add(uid, f"surplus_kgha_{SURPLUS_YEAR}", round(v, 6), "tol_1pct", "")
        except Exception:
            pass
        try:
            a = A.get_agtile(uid)
            v = wmean_of(a.assign(tile_frac=a.tile_cells / a.cell_px.replace(0, np.nan)), "tile_frac")
            if v is not None:
                add(uid, "tile_frac", round(v, 6), "tol_1pct", "")
        except Exception:
            pass
        s = wmean.reindex(frac.index)
        ok = s.notna()
        if ok.any():
            add(uid, f"precip_gridmet_mean_{WEATHER_YEAR}",
                round(float((s[ok] * frac[ok]).sum() / frac[ok].sum()), 6), "tol_1pct", "")

    df = pd.DataFrame(rows)
    # self-consistency invariant: the grid must reproduce the basin's own area
    a = df[df.metric == "grid_area_km2"].set_index("scope").value.astype(float)
    b = df[df.metric == "basin_area_km2"].set_index("scope").value.astype(float)
    j = (a / b.reindex(a.index)).dropna()
    add("GLOBAL", "median_grid_over_basin_area", round(float(j.median()), 6), "tol_1pct",
        "self-consistency; must hold in BOTH builds regardless of scope")

    df = pd.DataFrame(rows)
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(_OUT, index=False)
    print(f"wrote {_OUT.relative_to(_ROOT)}: {len(df):,} rows, {df.scope.nunique()} scopes")
    print(df.groupby("match").size().to_string())
    print(f"\nsites covered: {df[df.scope != 'GLOBAL'].scope.nunique()} of {len(ids)}")
    print(f"metrics per site: {sorted(df[df.scope != 'GLOBAL'].metric.unique())}")


if __name__ == "__main__":
    main()
