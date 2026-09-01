"""Turn a cached site frame into model rows: lagged and accumulated weather, static basin descriptors, target.

THE TARGET IS SPECIFIC DISCHARGE, mm/day, log-transformed. Raw m3/s across basins spanning four orders of magnitude in drainage area is mostly a prediction of AREA -- a model would score well by learning size and nothing about hydrology. `Q [m3/s] * 86400 s / (A [km2] * 1e6 m2) * 1000 mm/m` reduces to `Q * 86.4 / A`, which is the depth of water leaving the basin per day and is what a rainfall-runoff response actually produces. The log keeps a heavy right tail from owning the loss; scores are computed after transforming BACK, so they are comparable to NWM's on the same units.

RAINFALL IS NOT A POINT PREDICTOR AND THE FEATURES SAY SO. A hydrograph is a convolution of recent rainfall with the basin's response, so today's `pr` alone explains little at any but the flashiest site. Lags carry the shape of the response; rolling sums carry storage; cumulative `pr - pet` carries how wet the profile already was, which is what decides whether the next millimetre runs off or soaks in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

import cohort                                                      # noqa: E402
import config                                                      # noqa: E402

MM_PER_DAY = 86.4          # (m3/s -> mm/day) / km2


def specific_discharge(q_cms, area_km2) -> np.ndarray:
    """Q in m3/s over a drainage area in km2, as mm/day."""
    return np.asarray(q_cms, dtype="float64") * MM_PER_DAY / float(area_km2)


def site_frame(code: str, area_km2: float) -> pd.DataFrame:
    """One site's model rows. Empty when the cache is missing or nothing survives the target transform."""
    p = config.CACHE / "weather" / f"{code}.parquet"
    if not p.exists():
        return pd.DataFrame()
    d = pd.read_parquet(p).sort_values("date").reset_index(drop=True)
    if config.TARGET not in d.columns:
        return pd.DataFrame()

    for v in config.WEATHER_VARS:
        s = pd.to_numeric(d[v], errors="coerce")
        for k in config.LAGS:
            d[f"{v}_lag{k}"] = s.shift(k)
        for w in config.WINDOWS:
            d[f"{v}_sum{w}"] = s.rolling(w, min_periods=w).sum()
    pr = pd.to_numeric(d.pr, errors="coerce")
    pet = pd.to_numeric(d.pet, errors="coerce")
    for w in config.WINDOWS:
        # ANTECEDENT WETNESS, not rainfall: the same 20 mm runs off a saturated basin and soaks into a dry
        # one, and the difference between supply and demand over the preceding weeks is what separates them.
        d[f"awb{w}"] = (pr - pet).rolling(w, min_periods=w).sum()
    d["doy_sin"] = np.sin(2 * np.pi * d.date.dt.dayofyear / 365.25)
    d["doy_cos"] = np.cos(2 * np.pi * d.date.dt.dayofyear / 365.25)

    q = specific_discharge(pd.to_numeric(d[config.TARGET], errors="coerce"), area_km2)
    # Non-positive specific discharge cannot be logged and is not rainfall-runoff: it is a reversal, a
    # gauge artefact, or a dry channel reported as a negative. Dropped as missing rather than clamped,
    # because clamping would invent a floor the data never asserted.
    d["q_mm"] = np.where(q > 0, q, np.nan)
    d["y"] = np.log(d.q_mm + config.EPS_MM)
    return d.dropna(subset=["y"])


def runoff_ratio(sites: pd.DataFrame) -> pd.Series:
    """code -> long-term discharge depth / precipitation depth, from the cache. NaN where nothing is cached."""
    out = {}
    for _, r in sites.iterrows():
        p = config.CACHE / "weather" / f"{r.code}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p, columns=["pr", config.TARGET]).dropna()
        if not len(d) or d.pr.mean() <= 0:
            continue
        out[r.code] = float(d[config.TARGET].mean() * MM_PER_DAY / float(r.totdasqkm) / d.pr.mean())
    return pd.Series(out, dtype="float64")


def plausible(sites: pd.DataFrame, quiet: bool = False) -> pd.DataFrame:
    """Drop sites whose water balance cannot be right. See `config.MIN_RUNOFF_RATIO` for why this is physics."""
    rr = runoff_ratio(sites)
    keep = rr[(rr >= config.MIN_RUNOFF_RATIO) & (rr <= config.MAX_RUNOFF_RATIO)].index
    out = sites[sites.code.isin(set(keep))].reset_index(drop=True)
    if not quiet:
        print(f"  runoff-ratio filter: {len(out):,} of {len(sites):,} sites kept "
              f"({len(sites)-len(out):,} implausible or uncached)", flush=True)
    return out


def site_frame_nodrop(code: str, area_km2: float, out_name: str = "weather") -> pd.DataFrame:
    """`site_frame` for a site with NO observed discharge: same features, no target, nothing dropped.

    Separate from `site_frame` rather than a flag on it, because the two differ in what they may discard: the training path drops rows the target cannot represent, and a prediction path that inherited that behaviour would silently emit a series with holes wherever the (absent) target was unusable.
    """
    p = config.CACHE / out_name / f"{code}.parquet"
    if not p.exists():
        return pd.DataFrame()
    d = pd.read_parquet(p)
    # COERCED EXPLICITLY. The gauged path gets datetime for free from the join against the record's index;
    # this path has no target to join to, so the store's date32 arrives as objects and `.dt` fails.
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date").reset_index(drop=True)
    for v in config.WEATHER_VARS:
        s = pd.to_numeric(d[v], errors="coerce")
        for k in config.LAGS:
            d[f"{v}_lag{k}"] = s.shift(k)
        for w in config.WINDOWS:
            d[f"{v}_sum{w}"] = s.rolling(w, min_periods=w).sum()
    pr = pd.to_numeric(d.pr, errors="coerce")
    pet = pd.to_numeric(d.pet, errors="coerce")
    for w in config.WINDOWS:
        d[f"awb{w}"] = (pr - pet).rolling(w, min_periods=w).sum()
    d["doy_sin"] = np.sin(2 * np.pi * d.date.dt.dayofyear / 365.25)
    d["doy_cos"] = np.cos(2 * np.pi * d.date.dt.dayofyear / 365.25)
    return d


STATIC_COLS = ["totdasqkm", "areasqkm", "slope", "streamorde", "maxelevsmo", "minelevsmo", "lengthkm"]


def attach_statics(d: pd.DataFrame, comid: int, vaa, attrs, keep=None) -> pd.DataFrame:
    """Add the reach geometry and catchment attributes to a per-site frame.

    ONE implementation, called by both the training and the prediction paths. It was briefly two, and the prediction path simply did not add them -- so every static arrived as NaN, the model fell back to weather alone, and it produced a near-constant runoff ratio of 0.103 against the cohort's observed 0.349. Nothing raised; the series was merely wrong, and only the water-balance check caught it.
    """
    keep = STATIC_COLS if keep is None else keep
    for c in keep:
        d[c] = float(vaa[c].get(comid, np.nan)) if c in getattr(vaa, "columns", []) else np.nan
    if attrs is not None and len(attrs) and comid in attrs.index:
        for c in attrs.columns:
            d[c] = pd.to_numeric(pd.Series([attrs.loc[comid, c]]), errors="coerce").iloc[0]
    return d


def build_pool(sites: pd.DataFrame, stride: int | None = None, quiet: bool = False) -> pd.DataFrame:
    """Pooled rows across sites, thinned by `stride` days.

    Consecutive days of a hydrograph are near-duplicates -- the autocorrelation that makes lag features work also makes neighbouring rows redundant -- so a stride costs little information and buys the memory to hold the whole cohort.
    """
    stride = config.DAY_STRIDE if stride is None else stride
    keep_static = ["totdasqkm", "areasqkm", "slope", "streamorde", "maxelevsmo", "minelevsmo", "lengthkm"]
    vaa = cohort.vaa_statics(None).set_index("comid")
    attrs = cohort.attributes(sites.comid.astype("int64").tolist())
    attrs = attrs.set_index("comid") if len(attrs) else pd.DataFrame()

    frames = []
    for i, r in sites.reset_index(drop=True).iterrows():
        d = site_frame(r.code, float(r.totdasqkm))
        if not len(d):
            continue
        d = d.iloc[::stride]
        d = attach_statics(d, int(r.comid), vaa, attrs, keep_static)
        d["site"] = r.code
        frames.append(d)
        if not quiet and (i + 1) % 500 == 0:
            print(f"  pooled {i+1:,}/{len(sites):,} sites, {sum(len(f) for f in frames):,} rows", flush=True)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def feature_columns(pool: pd.DataFrame) -> list[str]:
    """Everything but the identifiers, the raw target and the untransformed weather of the same day."""
    drop = {"date", "site", "y", "q_mm", config.TARGET, *config.WEATHER_VARS}
    return [c for c in pool.columns if c not in drop and pd.api.types.is_numeric_dtype(pool[c])]
