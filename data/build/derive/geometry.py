"""D3 -- geometry: the sensor snap, declared coordinate precision, and the proximity table.

STATED COORDINATES ARE THE CLOSEST THING TO GROUND TRUTH of sensor position, and every distance in this stage is computed from them -- never from snapped positions. The snap residual (`snap_dist_m`) is CONTEXT for reach reliability, not positional uncertainty: a sensor 300 m from any mapped flowline is on unmapped water or coarse geometry, not doubtfully located.

DECLARED PRECISION is the rounding half-width primitive. Captured by the adapters where the source text is in hand; BACKFILLED here for registrations acquired before capture existed, by re-reading the archived source text (the IWQIS registry, the MPCA site CSV, the archived USGS GeoJSON) -- the raw archive retains the text, so the cohort does not wait for a re-acquisition. The uncertainty region is a RECTANGLE, per coordinate, because precision is declared per coordinate: a `43.21, -92.7503` station is +-557 m north-south and +-4 m east-west, a shape no radius can express.

THE PROXIMITY TABLE is a published data product: every unordered pair of located sensors within `config.PROXIMITY_RADIUS_M` of each other's STATED coordinates, with the stated separation and the rectangle-derived bounds on what the separation could truly be. It is the identity gate's candidate universe AND the modeling layer's raw material for grouping sensors into nodes; the generous radius exists for the second consumer, and the gate's far-tighter threshold lives in the identity stage where the decision is made. Recomputed whole every pass -- it costs seconds and partial updates would need their own correctness argument.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from .. import config, contracts, verify
from .. import io as bio
from . import snap as snap_mod

M_PER_DEG_LAT = 111_320.0

PROXIMITY_COLS = ["uid_a", "uid_b", "sep_m", "min_sep_m", "max_sep_m",
                  "comid_a", "comid_b", "same_comid"]


# ---- declared-precision backfill ------------------------------------------------------------------

def _dp_iwqis() -> pd.DataFrame:
    """(site_uid, coord_dp_lat, coord_dp_lon) from the archived registry text."""
    from ..acquire.sources import base, iwqis

    if not iwqis._REGISTRY.exists():
        return pd.DataFrame(columns=["site_uid", "coord_dp_lat", "coord_dp_lon"])
    r = pd.read_csv(iwqis._REGISTRY, dtype=str)
    return pd.DataFrame({"site_uid": "IWQIS:" + r["uid"].astype(str).str.strip(),
                         "coord_dp_lat": base.text_dp(r["latitude"]),
                         "coord_dp_lon": base.text_dp(r["longitude"])})


def _dp_mpca() -> pd.DataFrame:
    from ..acquire.sources import base, mpca

    if not mpca._SITES.exists():
        return pd.DataFrame(columns=["site_uid", "coord_dp_lat", "coord_dp_lon"])
    r = pd.read_csv(mpca._SITES, dtype=str)
    return pd.DataFrame({"site_uid": "MPCA:" + r["csg_id"].astype(str).str.strip(),
                         "coord_dp_lat": base.text_dp(r.get("lat")),
                         "coord_dp_lon": base.text_dp(r.get("lon"))})


def _dp_usgs_geojson() -> pd.DataFrame:
    """Coordinate text out of every archived monitoring-locations response, floats kept as STRINGS."""
    rows = []
    for p in sorted(config.ACQ_USGS_GEOJSON.glob("*.json")):
        try:
            doc = json.loads(p.read_text(), parse_float=lambda x: x)
        except Exception:
            continue
        for feat in doc.get("features", []):
            coords = (feat.get("geometry") or {}).get("coordinates") or [None, None]
            lon_t, lat_t = (list(coords) + [None, None])[:2]
            mlid = str(feat.get("id", ""))
            if mlid.startswith("USGS-"):
                rows.append(dict(site_uid=f"USGS:{mlid[5:]}",
                                 coord_dp_lat=_dp_text(lat_t), coord_dp_lon=_dp_text(lon_t)))
    return pd.DataFrame(rows, columns=["site_uid", "coord_dp_lat", "coord_dp_lon"])


def _dp_text(t) -> object:
    s = str(t or "").strip()
    if not re.match(r"^-?\d+(\.\d+)?$", s):
        return pd.NA
    return len(s.partition(".")[2])


def backfill_coord_dp(sensors: pd.DataFrame) -> pd.DataFrame:
    """Fill `coord_dp_*` wherever archived source text answers and the column is still null.

    Backfill only -- an adapter-captured value is never overwritten, because the adapter had the response in hand and the archive may lag it.
    """
    frames = [_dp_iwqis(), _dp_mpca(), _dp_usgs_geojson()]
    dp = pd.concat([f for f in frames if len(f)], ignore_index=True)
    if not len(dp):
        return sensors
    dp = dp.drop_duplicates("site_uid").set_index("site_uid")
    out = sensors.set_index("site_uid")
    for c in ("coord_dp_lat", "coord_dp_lon"):
        fill = out[c].isna() & out.index.isin(dp.index)
        out.loc[fill, c] = out.index[fill].map(dp[c])
    n = int(out.coord_dp_lat.notna().sum())
    print(f"  coord_dp: {n:,}/{len(out):,} registrations carry declared precision "
          f"({int(sensors.coord_dp_lat.notna().sum()):,} before backfill)")
    return out.reset_index()


# ---- uncertainty rectangles -----------------------------------------------------------------------

def half_widths_m(lat, dp_lat, dp_lon) -> tuple[np.ndarray, np.ndarray]:
    """Per-sensor rounding half-widths in metres: (half_lat_m, half_lon_m). NaN dp -> 0 (no declared slack).

    `0.5 * 10^-dp` degrees, converted at the sensor's own latitude. A null dp means the source text was unavailable -- the honest default is zero slack, not invented slack, and the verify check reports how much of the cohort that covers.
    """
    lat = np.asarray(lat, dtype="float64")
    dla = pd.to_numeric(pd.Series(dp_lat), errors="coerce").to_numpy(dtype="float64")
    dlo = pd.to_numeric(pd.Series(dp_lon), errors="coerce").to_numpy(dtype="float64")
    half_lat = np.where(np.isnan(dla), 0.0, 0.5 * np.power(10.0, -dla) * M_PER_DEG_LAT)
    mlon = M_PER_DEG_LAT * np.cos(np.radians(lat))
    half_lon = np.where(np.isnan(dlo), 0.0, 0.5 * np.power(10.0, -dlo) * mlon)
    return half_lat, half_lon


def _rect_bounds(dx, dy, ax, ay, bx, by) -> tuple[np.ndarray, np.ndarray]:
    """(min_sep, max_sep) between two axis-aligned uncertainty rectangles separated by (dx, dy) with half-widths (ax, ay), (bx, by): componentwise interval arithmetic, exact for rectangles."""
    gx = np.maximum(0.0, np.abs(dx) - (ax + bx))
    gy = np.maximum(0.0, np.abs(dy) - (ay + by))
    mn = np.hypot(gx, gy)
    mx = np.hypot(np.abs(dx) + ax + bx, np.abs(dy) + ay + by)
    return mn, mx


# ---- the proximity table --------------------------------------------------------------------------

def proximity_table(sensors: pd.DataFrame) -> pd.DataFrame:
    """Every unordered pair of located sensors within the radius, from STATED coordinates."""
    import geopandas as gpd
    from scipy.spatial import cKDTree

    s = sensors[sensors.lat.notna() & sensors.lon.notna()].reset_index(drop=True)
    g = gpd.GeoSeries(gpd.points_from_xy(s.lon, s.lat), crs=4326).to_crs(config.EQUAL_AREA)
    xy = np.c_[g.x.values, g.y.values]
    pairs = np.array(sorted(cKDTree(xy).query_pairs(config.PROXIMITY_RADIUS_M)))
    if not len(pairs):
        return pd.DataFrame(columns=PROXIMITY_COLS)
    i, j = pairs[:, 0], pairs[:, 1]
    d = xy[i] - xy[j]
    sep = np.hypot(d[:, 0], d[:, 1])

    hla, hlo = half_widths_m(s.lat, s.coord_dp_lat, s.coord_dp_lon)
    mn, mx = _rect_bounds(d[:, 0], d[:, 1], hlo[i], hla[i], hlo[j], hla[j])

    uid = s.site_uid.to_numpy()
    comid = pd.to_numeric(s.comid, errors="coerce").to_numpy() if "comid" in s.columns \
        else np.full(len(s), np.nan)
    out = pd.DataFrame({
        "uid_a": uid[i], "uid_b": uid[j],
        "sep_m": np.round(sep, 2), "min_sep_m": np.round(mn, 2), "max_sep_m": np.round(mx, 2),
        "comid_a": comid[i], "comid_b": comid[j],
    })
    # sort each pair lexically so the key is canonical whatever the tree returned
    flip = out.uid_a > out.uid_b
    out.loc[flip, ["uid_a", "uid_b"]] = out.loc[flip, ["uid_b", "uid_a"]].to_numpy()
    out.loc[flip, ["comid_a", "comid_b"]] = out.loc[flip, ["comid_b", "comid_a"]].to_numpy()
    out["same_comid"] = (out.comid_a == out.comid_b) & out.comid_a.notna()
    out["comid_a"] = out.comid_a.astype("Int64")
    out["comid_b"] = out.comid_b.astype("Int64")
    return out.sort_values(["uid_a", "uid_b"], ignore_index=True)


# ---- the stage ------------------------------------------------------------------------------------

SNAP_BLOCK = [c for c, _, blk, _ in contracts.SENSOR_COLUMNS if blk == "snap"]


def main(force: bool = False, snapshot: str | None = None) -> pd.DataFrame:
    config.ensure_dirs()
    sensors = pd.read_parquet(config.SENSORS_PATH)

    sensors = backfill_coord_dp(sensors)

    todo = sensors if force else sensors[sensors.snap_status.isna() | sensors.comid.isna()
                                         | (sensors.snap_status == "no_location")]
    if len(todo):
        print(f"  snapping {len(todo):,} sensor(s) ({len(sensors) - len(todo):,} already snapped)")
        block = snap_mod.main(todo)
        sensors = contracts.update_block(sensors, block, SNAP_BLOCK)

    out = contracts.conform_sensors(sensors)
    bad = contracts.validate_sensors(out)
    if bad:
        raise ValueError("sensors table violates the contract:\n  " + "\n  ".join(bad))
    bio.write_parquet(out, config.SENSORS_PATH,
                      stamps={"snapshot": snapshot or "", "aoe": config.aoe_stamp(),
                              "schema": contracts.schema_fingerprint(contracts.SENSOR_DTYPES)})

    prox = proximity_table(out)
    bio.write_parquet(prox, config.PROXIMITY_PATH,
                      stamps={"aoe": config.aoe_stamp(),
                              "radius_m": str(config.PROXIMITY_RADIUS_M)})
    print(f"  proximity table: {len(prox):,} pair(s) within {config.PROXIMITY_RADIUS_M:.0f} m "
          f"({int(prox.same_comid.sum()):,} same-reach)")
    return prox


# ---- verify ---------------------------------------------------------------------------------------

@verify.check("d3", "proximity bounds are ordered: min_sep <= sep <= max_sep, rowwise")
def _check_bounds_order():
    if not config.PROXIMITY_PATH.exists():
        return None, "no proximity table yet"
    p = pd.read_parquet(config.PROXIMITY_PATH)
    bad = int(((p.min_sep_m > p.sep_m + 0.01) | (p.sep_m > p.max_sep_m + 0.01)).sum())
    return bad == 0, f"{len(p):,} pairs; {bad} violate the ordering"


@verify.check("d3", "coordinate-sentinel registrations have no geometry and no pairs")
def _check_sentinels_have_no_geometry():
    if not config.SENSORS_PATH.exists() or not config.PROXIMITY_PATH.exists():
        return None, "stores not built yet"
    s = pd.read_parquet(config.SENSORS_PATH,
                        columns=["site_uid", "lat", "location_missing_reason", "comid", "snap_status"])
    noloc = s[s.location_missing_reason.notna()]
    if not len(noloc):
        return True, "no coordinate-sentinel registrations in the cohort"
    bad_geom = int(noloc.lat.notna().sum() + noloc.comid.notna().sum())
    p = pd.read_parquet(config.PROXIMITY_PATH, columns=["uid_a", "uid_b"])
    in_pairs = int(p.uid_a.isin(set(noloc.site_uid)).sum() + p.uid_b.isin(set(noloc.site_uid)).sum())
    ok = bad_geom == 0 and in_pairs == 0
    return ok, f"{len(noloc)} sentinel registration(s); {bad_geom} with geometry, {in_pairs} in pairs"


@verify.check("d3", "declared precision covers the cohort where source text exists")
def _check_dp_coverage():
    if not config.SENSORS_PATH.exists():
        return None, "no sensors table yet"
    s = pd.read_parquet(config.SENSORS_PATH, columns=["site_uid", "source", "coord_dp_lat", "lat"])
    loc = s[s.lat.notna()]
    by = loc.groupby("source").coord_dp_lat.agg(lambda v: f"{int(v.notna().sum())}/{len(v)}").to_dict()
    # IWQIS and MPCA ship text files, so full coverage is expected there; USGS depends on the GeoJSON archive.
    iw = loc[loc.source == "IWQIS"]
    ok = bool(len(iw) == 0 or iw.coord_dp_lat.notna().all())
    return ok, f"dp coverage by source: {by}"


@verify.check("d3", "the proximity table matches a direct recomputation", deep=True)
def _check_table_fresh():
    if not config.PROXIMITY_PATH.exists() or not config.SENSORS_PATH.exists():
        return None, "stores not built yet"
    fresh = proximity_table(pd.read_parquet(config.SENSORS_PATH))
    disk = pd.read_parquet(config.PROXIMITY_PATH)
    same = len(fresh) == len(disk) and fresh[["uid_a", "uid_b"]].equals(disk[["uid_a", "uid_b"]])
    return same, f"{len(disk):,} on disk vs {len(fresh):,} recomputed"
