"""
Is SOIL data worth adding?  Site-level incremental-value test.
================================================================================

Static features (lat/lon/basin geometry) improved the cross-site model, and lat/lon
are just dumb proxies for spatially-varying catchment properties -- soil being a prime
candidate. But soil is also spatial, so it may be REDUNDANT with lat/lon. The only
question that matters is whether soil adds signal *beyond* the static features we have.

Static features only matter in the BETWEEN-SITE task (which site runs high vs low), so
we collapse to one row per site and test there directly:

  target    : per-site mean daily nitrate (the between-site level)
  base      : lat, lon, log_basin_area, mean/max cell distance  (site_static)
  +soil     : base + basin-AREA-WEIGHTED topsoil summary from USDA SDA
              (clay, silt, sand, organic matter, Ksat, AWC, pH, CEC; 0-30 cm).
              Queried over the basin polygon (NOT the sensor point, which sits in an
              unrepresentative alluvial strip), then area-weighted across map units.

We compare base vs +soil with out-of-fold R2 under two CV schemes:
  LOO   leave-one-site-out
  LOBO  leave-basins-out (GroupKFold by data/splits.py conflict component)
for two simple models (Ridge; shallow RandomForest), plus a partial-correlation
pre-check (does each soil var correlate with site-mean nitrate AFTER removing lat/lon?).

If +soil clears base out-of-sample -> worth wiring a site_soil() accessor into
features.py. If it barely moves -> lat/lon already captured it; the early "soil won't
matter" conclusion stands. Soil queries are slow live HTTP, so they are cached.

Scratch / untracked.  Run:  python isaac/_experiment5.py
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
import requests
from sklearn.linear_model import Ridge, LinearRegression
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import LeaveOneOut, GroupKFold, cross_val_predict
from sklearn.metrics import r2_score

from data.access import get_site_ids, get_data
from data.features import daily_nitrate, site_static
from data.splits import split_groups

MIN_DAYS = 1000
SOIL_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_soil_cache_basin.parquet")
BASE_COLS = ["lat", "lon", "log_basin_area", "mean_dist_to_sensor", "max_dist_to_sensor"]
# SDA raw column -> friendly name (topsoil agronomic properties relevant to N leaching)
SOIL_MAP = {"claytotal_r": "clay", "silttotal_r": "silt", "sandtotal_r": "sand",
            "om_r": "om", "ksat_r": "ksat", "awc_r": "awc", "ph1to1h2o_r": "ph", "cec7_r": "cec"}
SOIL_COLS = list(SOIL_MAP.values())
SIMPLIFY_DEG = 0.01     # ~1 km basin-polygon simplification (soil is coarse; keeps WKT small/fast)
_BASIN_GROUP = split_groups()


def _sda(sql, timeout=120):
    """Minimal SDA POST with a timeout (the archive helper has none -> can hang)."""
    r = requests.post("https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest",
                      data={"query": sql, "format": "json+columnname"}, timeout=timeout)
    r.raise_for_status()
    table = (r.json() or {}).get("Table", [])
    return pd.DataFrame(table[1:], columns=table[0]) if table else pd.DataFrame()


def basin_soil(uid):
    """Basin-representative topsoil (0-30 cm) summary, area-weighted over the whole basin.

    Querying the sensor POINT returns the (often unrepresentative) alluvial strip under
    the gauge. Instead we query the basin polygon as an area of interest: get every soil
    map unit intersecting it and its area, take each map unit's component-/thickness-
    weighted topsoil properties, then area-weight across the basin. Two SDA calls.
    """
    nan = {v: np.nan for v in SOIL_COLS}
    try:
        g = get_data(uid).basin
        geom = (g.geometry.iloc[0] if hasattr(g, "geometry") else g).buffer(0).simplify(SIMPLIFY_DEG)
        wkt = geom.wkt
        # (1) map units intersecting the basin + their area (planar deg^2 -> relative weights)
        areas = _sda(f"SELECT mp.mukey, SUM(mp.mupolygongeo.STArea()) AS area FROM mupolygon mp "
                     f"WHERE mp.mupolygongeo.STIntersects(geometry::STGeomFromText('{wkt}',4326))=1 "
                     f"GROUP BY mp.mukey")
        if areas.empty:
            return nan
        areas["area"] = pd.to_numeric(areas["area"], errors="coerce")
        # (2) topsoil properties for those map units (component + horizon level)
        inlist = ",".join(f"'{m}'" for m in areas["mukey"])
        cols = ",".join("ch." + c for c in SOIL_MAP)
        h = _sda(f"SELECT co.mukey, co.comppct_r, ch.hzdept_r, ch.hzdepb_r, {cols} "
                 f"FROM component co JOIN chorizon ch ON co.cokey=ch.cokey WHERE co.mukey IN ({inlist})")
        for c in ["comppct_r", "hzdept_r", "hzdepb_r", *SOIL_MAP]:
            h[c] = pd.to_numeric(h[c], errors="coerce")
        top = h[h["hzdept_r"] < 30].copy()
        top["w"] = top["comppct_r"] * (np.minimum(top["hzdepb_r"], 30) - top["hzdept_r"]).clip(lower=0)
        # per map unit: component/thickness-weighted topsoil property
        per_mu = {}
        for mk, grp in top.groupby("mukey"):
            per_mu[mk] = {name: (np.average(grp[raw], weights=grp["w"])
                                 if (grp[raw].notna() & (grp["w"] > 0)).any() else np.nan)
                          for raw, name in SOIL_MAP.items()}
        props = pd.DataFrame(per_mu).T.join(areas.set_index("mukey")["area"]).dropna(subset=["area"])
        # area-weight each property across the basin (over map units where it's defined)
        out = {}
        for name in SOIL_COLS:
            ok = props[name].notna() & (props["area"] > 0)
            out[name] = float(np.average(props.loc[ok, name], weights=props.loc[ok, "area"])) if ok.any() else np.nan
        return out
    except Exception:
        return nan


def load_soil(sites, force=False):
    """Per-site basin soil summary, cached to parquet INCREMENTALLY (slow flaky HTTP:
    save after every site so a crash/timeout never loses progress; re-runs resume)."""
    cache = pd.read_parquet(SOIL_CACHE) if (os.path.exists(SOIL_CACHE) and not force) else pd.DataFrame(columns=SOIL_COLS)
    for i, s in enumerate(sites):
        if s in cache.index:
            continue
        cache.loc[s] = basin_soil(s)
        cache.index.name = "site"
        cache.to_parquet(SOIL_CACHE)
        print(f"  soil [{i+1}/{len(sites)}] {s}: "
              + ("ok" if cache.loc[s].notna().any() else "MISSING"), flush=True)
    return cache.loc[sites]


def build_site_table(sites):
    """One row per site: base static features + soil summary + target (mean daily nitrate)."""
    base = pd.DataFrame({s: site_static(s) for s in sites}).T
    base["nitrate_mean"] = {s: daily_nitrate(s).dropna().mean() for s in sites}
    soil = load_soil(sites)
    tab = base.join(soil)
    tab["group"] = [_BASIN_GROUP.get(s, -i - 1) for i, s in enumerate(tab.index)]  # basin component
    return tab.astype({c: float for c in BASE_COLS + SOIL_COLS + ["nitrate_mean"]})


def oof_r2(model, X, y, scheme, groups=None):
    """Out-of-fold R2 under 'LOO' (leave-one-out) or 'LOBO' (GroupKFold by basin)."""
    if scheme == "LOO":
        pred = cross_val_predict(model, X, y, cv=LeaveOneOut())
    else:
        pred = cross_val_predict(model, X, y, groups=groups, cv=GroupKFold(5))
    return r2_score(y, pred)


def partial_corr(tab):
    """Corr of each soil var with site-mean nitrate AFTER removing lat/lon/basin (residuals)."""
    B = tab[BASE_COLS].values
    y_res = tab["nitrate_mean"].values - LinearRegression().fit(B, tab["nitrate_mean"]).predict(B)
    out = {}
    for c in SOIL_COLS:
        c_res = tab[c].values - LinearRegression().fit(B, tab[c]).predict(B)
        out[c] = np.corrcoef(y_res, c_res)[0, 1]
    return pd.Series(out).sort_values(key=lambda s: s.abs(), ascending=False)


def main():
    sites = []
    for uid in get_site_ids():
        try:
            if daily_nitrate(uid).dropna().shape[0] >= MIN_DAYS:
                sites.append(uid)
        except Exception:
            pass
    print(f"candidate sites: {len(sites)}  (fetching/caching soil...)")

    tab = build_site_table(sites).dropna(subset=SOIL_COLS + ["nitrate_mean"])
    print(f"sites with complete soil + target: {len(tab)}  "
          f"(target mean={tab.nitrate_mean.mean():.2f}, std={tab.nitrate_mean.std():.2f})\n")

    y, groups = tab["nitrate_mean"], tab["group"]
    X_base = tab[BASE_COLS]
    X_soil = tab[BASE_COLS + SOIL_COLS]
    models = {
        "Ridge":        make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "RandomForest": RandomForestRegressor(n_estimators=400, max_depth=3, min_samples_leaf=3, random_state=42),
    }

    header = f"{'model':14s} {'CV':6s} {'base_R2':>8s} {'+soil_R2':>9s} {'delta':>7s}"
    print(header); print("-" * len(header))
    for mname, model in models.items():
        for scheme in ("LOO", "LOBO"):
            r_base = oof_r2(model, X_base, y, scheme, groups)
            r_soil = oof_r2(model, X_soil, y, scheme, groups)
            print(f"{mname:14s} {scheme:6s} {r_base:8.3f} {r_soil:9.3f} {r_soil - r_base:+7.3f}")

    print("\npartial corr of each soil var with site-mean nitrate (controlling for lat/lon/basin):")
    print(partial_corr(tab).to_string(float_format=lambda v: f"{v:+.3f}"))


if __name__ == "__main__":
    main()
