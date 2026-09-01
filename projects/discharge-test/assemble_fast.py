"""Assemble every site's basin-mean weather by reading the store ONCE instead of once per site.

THE COST WAS NEVER THE ARITHMETIC. `api.get_weather` opens all 76 quarter files on every call to select row groups from footer statistics -- excellent for one basin, and paid 4,927 times it is ~3 s of fixed scan per site against ~5 s total, so the cohort costs about seven hours to compute a weighted average. Reading each quarter once and reducing every site from it turns 4,927 x 76 file opens into 76.

The reduction here MUST match `api.get_weather` exactly or every downstream number is quietly wrong, so it is replicated rather than reinvented: an area-weighted mean per date with `w_km2` weights. `get_weather` additionally makes the denominator NaN-aware; that path is dead in this store (the AOE clip drops all-NaN cells before storage, measured at zero nulls over a full quarter) and `--validate` asserts agreement against `get_weather` itself rather than trusting the argument.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

import cohort                                                      # noqa: E402
import config                                                      # noqa: E402

sys.path.append(str(Path(__file__).resolve().parents[2]))
from data.access.machinery import aggregate as agg                 # noqa: E402
from data.build import config as bcfg                              # noqa: E402
from data.build.acquire import weather as W                        # noqa: E402


def site_weights(sites: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """(site_idx, cell_id, w_km2) for every site, plus code -> row index. Local only; no store read."""
    rows, index = [], {}
    for i, r in sites.reset_index(drop=True).iterrows():
        b = cohort.basin(r.code, int(r.comid))
        if not len(b):
            continue
        ww = agg.weather_weights(agg.from_comids(b.comid.tolist()))
        if not len(ww):
            continue
        index[r.code] = len(index)
        rows.append(pd.DataFrame({"site": index[r.code], "cell_id": ww.cell_id.to_numpy(),
                                  "w_km2": ww.w_km2.to_numpy()}))
    return (pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()), index


def _quarters():
    return sorted((bcfg.ACQ_WEATHER / W.DAILY_DIR_NAME).glob("gridmet_*Q*.parquet"))


def run(limit: int | None = None, validate: int = 0, sites: pd.DataFrame | None = None,
        out_name: str = "weather", join_target: bool = True) -> None:
    """Reduce and cache. `sites` overrides the gauged cohort, which is how the ungauged prediction set is built; `join_target` is False there, since those records have no discharge to join to."""
    import pyarrow.parquet as pq
    from scipy import sparse

    config.ensure_dirs()
    s = cohort.sites() if sites is None else sites
    if limit:
        s = s.head(limit)
    t0 = time.time()
    wmap, index = site_weights(s)
    n_site = len(index)
    print(f"weights: {n_site:,} sites, {len(wmap):,} (site, cell) pairs in {time.time()-t0:.0f}s", flush=True)

    cells = np.sort(wmap.cell_id.unique())
    col = pd.Series(np.arange(len(cells)), index=cells)
    Wm = sparse.csr_matrix((wmap.w_km2.to_numpy("float64"),
                            (wmap.site.to_numpy(), col.reindex(wmap.cell_id).to_numpy())),
                           shape=(n_site, len(cells)))
    denom = np.asarray(Wm.sum(axis=1)).ravel()
    print(f"cells touched: {len(cells):,} of the store", flush=True)

    acc = {v: [] for v in config.WEATHER_VARS}
    dates_all = []
    qs = _quarters()
    for qi, p in enumerate(qs, 1):
        t = pq.read_table(p, columns=["cell_id", "date", *config.WEATHER_VARS]).to_pandas()
        t = t[t.cell_id.isin(col.index)]
        if not len(t):
            continue
        t["ci"] = col.reindex(t.cell_id).to_numpy()
        dts = np.sort(t.date.unique())
        di = pd.Series(np.arange(len(dts)), index=dts)
        t["di"] = di.reindex(t.date).to_numpy()
        for v in config.WEATHER_VARS:
            V = np.full((len(cells), len(dts)), np.nan, dtype="float64")
            V[t.ci.to_numpy(), t.di.to_numpy()] = pd.to_numeric(t[v], errors="coerce").to_numpy()
            ok = ~np.isnan(V)
            num = Wm @ np.nan_to_num(V, nan=0.0)
            den = Wm @ ok.astype("float64")          # NaN-aware, exactly as get_weather does it
            acc[v].append(np.divide(num, den, out=np.full_like(num, np.nan), where=den > 0))
        dates_all.append(dts)
        if qi % 10 == 0:
            print(f"  quarter {qi}/{len(qs)}  {(time.time()-t0)/60:.1f} min", flush=True)

    dates = np.concatenate(dates_all)
    order = np.argsort(dates)
    dates = dates[order]
    stacked = {v: np.concatenate(acc[v], axis=1)[:, order] for v in config.WEATHER_VARS}
    print(f"reduced: {n_site:,} sites x {len(dates):,} days in {(time.time()-t0)/60:.1f} min", flush=True)

    if validate:
        _validate(s, index, stacked, dates, validate)

    out_dir = config.CACHE / out_name
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for code, i in index.items():
        d = pd.DataFrame({v: stacked[v][i] for v in config.WEATHER_VARS})
        d.insert(0, "date", dates)
        if join_target:
            rec = cohort.record(code)
            if config.TARGET not in rec.columns:
                continue
            d = d.set_index("date").join(rec[[config.TARGET]], how="inner").reset_index()
        if len(d):
            d.to_parquet(out_dir / f"{code}.parquet", index=False)
            written += 1
    print(f"wrote {written:,} site frames in {(time.time()-t0)/60:.1f} min total", flush=True)


def _validate(sites, index, stacked, dates, n: int) -> None:
    """Assert the batched reduction reproduces `api.get_weather` exactly. A weighting bias here is silent."""
    rng = np.random.default_rng(20260827)
    codes = [c for c in index if c in set(sites.code)]
    pick = rng.choice(codes, size=min(n, len(codes)), replace=False)
    worst = 0.0
    for code in pick:
        row = sites[sites.code == code].iloc[0]
        b = cohort.basin(code, int(row.comid))
        ref = cohort.weather(b.comid.tolist()).set_index("date")
        mine = pd.DataFrame({v: stacked[v][index[code]] for v in config.WEATHER_VARS}, index=dates)
        common = ref.index.intersection(mine.index)
        for v in config.WEATHER_VARS:
            a = pd.to_numeric(ref.loc[common, v], errors="coerce").to_numpy("float64")
            b2 = pd.to_numeric(mine.loc[common, v], errors="coerce").to_numpy("float64")
            m = ~(np.isnan(a) | np.isnan(b2))
            if m.any():
                worst = max(worst, float(np.nanmax(np.abs(a[m] - b2[m]))))
    print(f"VALIDATE: max |batched - get_weather| over {len(pick)} sites = {worst:.3e}", flush=True)
    if worst > 1e-9:
        raise SystemExit(f"batched reduction disagrees with get_weather by {worst:.3e} -- refusing to write")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--validate", type=int, default=0)
    a = ap.parse_args()
    run(limit=a.limit, validate=a.validate)
