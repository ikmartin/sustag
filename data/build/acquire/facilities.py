"""A3: treatment facilities -- EPA point-source outfalls, their nitrogen loads, and POTW attributes.

WHY THIS SOURCE EXISTS. The treatment-pair discovery: paired sensors bracketing a plant are a measured intervention, and the plant between them was locatable from no store the pipeline carried. Point-source effluent is also a first-class nitrate covariate -- a measured INPUT to the river, not a quantity fitted on ambient water, so it carries no leakage flag.

THREE EPA PRODUCTS, JOINED ON THE NPDES PERMIT NUMBER:
- the ICIS-NPDES discharge-points layer (`NPDES_OUTFALLS_LAYER.csv`, refreshed weekly at the source): every permitted outfall with per-outfall NAD83 coordinates, facility type, SIC/NAICS, permit status. The LOCATION authority. Drinking-water plants enter through the same door -- a nitrate-removal facility discharges residuals under an NPDES permit even though its intake is not public data.
- the raw ICIS-NPDES DMR archives, filtered to the nitrogen family: reported effluent measurements per (permit, outfall, monitoring period) -- the loading tool's computed product has no stable bulk URL, so loads are derived from these with the tool's documented methodology.
- the CWNS POTW inventory: treatment level, design flow, population served.

THE PULL IS BULK-DOWNLOAD THEN CLIP, like the network layers: the outfall CSV is national (~a few hundred MB zipped), fetched whole, archived verbatim under `acquired/facilities/vendor/`, and the AOE subset written beside it. The vendor files are the raw truth the snapshot manifests; column drift in them classifies as schema drift like any source. Source documentation is archived in `reference/water-treatment-facilities/`.
"""

from __future__ import annotations

import datetime as dt
import io as _io
import zipfile

import numpy as np
import pandas as pd

from .. import config
from .. import io as bio

VENDOR = config.ACQ_FACILITIES / "vendor"
OUTFALLS_URL = "https://echo.epa.gov/files/echodownloads/npdes_outfalls_layer.zip"

OUTFALLS_PATH = config.ACQ_FACILITIES / "outfalls.parquet"
CWNS_PATH = config.ACQ_FACILITIES / "cwns_potw.parquet"

# The loading tool publishes DMR-computed loads from 2007 on; the collection window starts 2008.
def _load_years() -> list[int]:
    return list(range(max(2008, config.collection_start().year), dt.date.today().year))


def _download(url: str, dest_name: str, force: bool = False):
    """Fetch one vendor file into the archive, atomically. Returns the path or None on failure."""
    import requests

    VENDOR.mkdir(parents=True, exist_ok=True)
    dest = VENDOR / dest_name
    if dest.exists() and not force:
        return dest
    try:
        r = requests.get(url, timeout=600)
        r.raise_for_status()
    except Exception as e:
        print(f"    {dest_name}: {type(e).__name__}: {str(e)[:80]} -- skipped")
        return None
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    print(f"    {dest_name}: {len(r.content) / 1e6:.1f} MB archived")
    return dest


def _read_zip_csv(path, name_hint: str | None = None) -> pd.DataFrame | None:
    """The first (or hinted) CSV member of a vendor zip, as strings."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            return None
        pick = next((n for n in names if name_hint and name_hint.lower() in n.lower()), names[0])
        return pd.read_csv(z.open(pick), dtype=str, low_memory=False)


def fetch_outfalls(force: bool = False) -> pd.DataFrame | None:
    """The AOE's permitted outfalls, from the national layer: per-outfall coordinates + facility metadata."""
    zp = _download(OUTFALLS_URL, "npdes_outfalls_layer.zip", force=force)
    if zp is None:
        return None
    if OUTFALLS_PATH.exists() and not force and OUTFALLS_PATH.stat().st_mtime >= zp.stat().st_mtime:
        return pd.read_parquet(OUTFALLS_PATH)
    d = _read_zip_csv(zp, "OUTFALLS")
    if d is None:
        print("    outfall zip holds no CSV -- vendor layout moved; treat as schema drift")
        return None
    cols = {c.upper(): c for c in d.columns}
    lat_c, lon_c = cols.get("LATITUDE83"), cols.get("LONGITUDE83")
    if not lat_c or not lon_c:
        print(f"    outfall CSV lacks LATITUDE83/LONGITUDE83 -- schema drift; columns: {list(d.columns)[:8]}")
        return None
    lat = pd.to_numeric(d[lat_c], errors="coerce")
    lon = pd.to_numeric(d[lon_c], errors="coerce")
    # Coordinate precision from the vendor TEXT, same discipline as the sensor adapters.
    from .sources import base

    keep = pd.DataFrame({
        "npdes_id": d[cols.get("EXTERNAL_PERMIT_NMBR", "EXTERNAL_PERMIT_NMBR")].astype(str).str.strip(),
        "facility_name": d.get(cols.get("FACILITY_NAME", ""), pd.Series(dtype=str)),
        "facility_type": d.get(cols.get("FACILITY_TYPE_CODE", ""), pd.Series(dtype=str)),
        "sic": d.get(cols.get("SIC_CODES", ""), pd.Series(dtype=str)),
        "naics": d.get(cols.get("NAICS_CODES", ""), pd.Series(dtype=str)),
        "permit_type": d.get(cols.get("PERMIT_TYPE_DESC", ""), pd.Series(dtype=str)),
        "permit_status": d.get(cols.get("PERMIT_STATUS_CODE", ""), pd.Series(dtype=str)),
        "latlong_type": d.get(cols.get("LATLONG_TYPE", ""), pd.Series(dtype=str)),
        "state": d.get(cols.get("STATE_CODE", ""), pd.Series(dtype=str)),
        "lat": lat,
        "lon": lon,
        "coord_dp_lat": base.text_dp(d[lat_c]),
        "coord_dp_lon": base.text_dp(d[lon_c]),
    })
    keep = keep[keep.lat.notna() & keep.lon.notna()]
    # Clip to the AOE -- the national layer holds ~1M outfalls and the AOE holds the ones a model can use.
    from shapely import points
    from shapely.prepared import prep

    geom = prep(config.aoe_geometry())
    pts = points(keep.lon.to_numpy(dtype=float), keep.lat.to_numpy(dtype=float))
    inside = np.fromiter((geom.contains(p) for p in pts), dtype=bool, count=len(pts))
    keep = keep[inside].reset_index(drop=True)
    bio.write_parquet(keep, OUTFALLS_PATH, stamps={"aoe": config.aoe_stamp()})
    print(f"    {len(keep):,} outfall(s) in the AOE "
          f"({keep.npdes_id.nunique():,} permits)")
    return keep


# The nitrogen parameter family, matched on the DMR parameter description.
_N_WORDS = ("nitrogen", "nitrate", "nitrite", "kjeldahl", "ammonia")

# Raw ICIS-NPDES DMR archives, one per fiscal year. 300-640 MB zipped apiece, so they follow the
# EPHEMERAL archive discipline (the CDL pattern): download, filter to the nitrogen family, write the
# small parquet, DELETE the zip. The nitrogen parquet is the acquired artifact; a re-filter re-downloads.
DMR_RAW_URL = "https://echo.epa.gov/files/echodownloads/npdes_dmrs_fy{year}.zip"
DMR_DIR = config.ACQ_FACILITIES / "dmr_n"


def fetch_loads(force: bool = False) -> None:
    """Nitrogen-family DMR rows per fiscal year -- reported effluent measurements at per-outfall grain.

    The loading tool's own computed annual loads have no stable bulk URL, so the RAW DMRs are the source and the honest aggregation to loads happens at derive time with the methodology the loading tool documents (reference/water-treatment-facilities/). Kept per (permit, outfall, monitoring period, parameter, statistical base): richer than the tool's facility-year grain, and the filter keeps ~1% of the rows.
    """
    import requests

    DMR_DIR.mkdir(parents=True, exist_ok=True)
    for year in _load_years():
        out = DMR_DIR / f"fy{year}.parquet"
        if out.exists() and not force:
            continue
        url = DMR_RAW_URL.format(year=year)
        print(f"    fy{year}: downloading (ephemeral) ...", flush=True)
        try:
            r = requests.get(url, timeout=1800)
            r.raise_for_status()
        except Exception as e:
            print(f"    fy{year}: {type(e).__name__}: {str(e)[:80]} -- skipped")
            continue
        keep_frames = []
        with zipfile.ZipFile(_io.BytesIO(r.content)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names:
                print(f"    fy{year}: zip holds no CSV -- schema drift")
                continue
            with z.open(names[0]) as f:
                for chunk in pd.read_csv(f, dtype=str, chunksize=500_000, low_memory=False):
                    cols = {c.upper(): c for c in chunk.columns}
                    pc = next((cols[k] for k in cols if "PARAMETER_DESC" in k), None)
                    if pc is None:
                        print(f"    fy{year}: no PARAMETER_DESC column -- schema drift; "
                              f"{list(chunk.columns)[:8]}")
                        keep_frames = []
                        break
                    p = chunk[pc].fillna("").str.lower()
                    hit = chunk[p.str.contains("|".join(_N_WORDS))]
                    if len(hit):
                        keep_frames.append(hit)
        if keep_frames:
            n = pd.concat(keep_frames, ignore_index=True)
            n["fiscal_year"] = year
            bio.write_parquet(n, out)
            print(f"    fy{year}: {len(n):,} nitrogen-family DMR rows kept")


def fetch_cwns(force: bool = False) -> pd.DataFrame | None:
    """CWNS POTW attributes keyed on the NPDES permit number, assembled from the survey's relational tables.

    The CWNS portal is an interactive application with no stable bulk URL, so its national zip is a MANUAL acquisition like CDL: `vendor/cwns_national.zip`, whose members are RELATIONAL -- `FACILITY_PERMIT` carries the NPDES join, `EFFLUENT` the treatment level, `FLOW` the design flow (MGD), `POPULATION_WASTEWATER` the served population -- all keyed on `CWNS_ID`. One row per NPDES permit here; a facility holding several permits contributes to each. Absent zip degrades to no attributes, printed.
    """
    zp = VENDOR / "cwns_national.zip"
    if not zp.exists():
        print("    no cwns_national.zip in the vendor archive -- POTW attributes skipped "
              "(download the national dataset from the EPA CWNS portal)")
        return None
    if CWNS_PATH.exists() and not force and CWNS_PATH.stat().st_mtime >= zp.stat().st_mtime:
        return pd.read_parquet(CWNS_PATH)

    def member(name):
        with zipfile.ZipFile(zp) as z:
            hit = next((n for n in z.namelist() if n.upper().endswith(name)), None)
            if hit is None:
                return None
            return pd.read_csv(z.open(hit), dtype=str, low_memory=False)

    permits = member("FACILITY_PERMIT.CSV")
    if permits is None or "PERMIT_NUMBER" not in permits.columns:
        print("    cwns zip lacks FACILITY_PERMIT -- schema drift; attributes skipped")
        return None
    npdes = permits[permits.PERMIT_SOURCE.fillna("").str.upper().eq("NPDES")]
    out = npdes[["CWNS_ID", "PERMIT_NUMBER", "STATE_CODE"]].drop_duplicates()

    fac = member("FACILITIES.CSV")
    if fac is not None:
        out = out.merge(fac[["CWNS_ID", "FACILITY_NAME"]].drop_duplicates("CWNS_ID"),
                        on="CWNS_ID", how="left")
    eff = member("EFFLUENT.CSV")
    if eff is not None:
        out = out.merge(eff[["CWNS_ID", "CURRENT_EFFLUENT_TREATMENT_LEVEL"]]
                        .drop_duplicates("CWNS_ID"), on="CWNS_ID", how="left")
    flow = member("FLOW.CSV")
    if flow is not None:
        f = flow.copy()
        f["CURRENT_DESIGN_FLOW"] = pd.to_numeric(f.CURRENT_DESIGN_FLOW, errors="coerce")
        f = f.groupby("CWNS_ID", as_index=False).CURRENT_DESIGN_FLOW.sum()
        out = out.merge(f.rename(columns={"CURRENT_DESIGN_FLOW": "design_flow_mgd"}),
                        on="CWNS_ID", how="left")
    pop = member("POPULATION_WASTEWATER.CSV")
    if pop is not None and "TOTAL_RES_POPULATION_2022" in pop.columns:
        p = pop.copy()
        p["TOTAL_RES_POPULATION_2022"] = pd.to_numeric(p.TOTAL_RES_POPULATION_2022, errors="coerce")
        p = p.groupby("CWNS_ID", as_index=False).TOTAL_RES_POPULATION_2022.max()
        out = out.merge(p.rename(columns={"TOTAL_RES_POPULATION_2022": "population_2022"}),
                        on="CWNS_ID", how="left")

    out = out.rename(columns={"CWNS_ID": "cwns_id", "PERMIT_NUMBER": "npdes_id",
                              "STATE_CODE": "state", "FACILITY_NAME": "facility_name",
                              "CURRENT_EFFLUENT_TREATMENT_LEVEL": "treatment_level"})
    bio.write_parquet(out, CWNS_PATH)
    print(f"    cwns: {len(out):,} NPDES-permitted facility row(s); treatment level on "
          f"{int(out.treatment_level.notna().sum()):,}, design flow on "
          f"{int(out.design_flow_mgd.notna().sum()):,}")
    return out


def main(force: bool = False, snapshot: str | None = None) -> None:
    config.ACQ_FACILITIES.mkdir(parents=True, exist_ok=True)
    print("  outfalls:")
    fetch_outfalls(force=force)
    print("  nitrogen loads:")
    fetch_loads(force=force)
    print("  cwns attributes:")
    fetch_cwns(force=force)
