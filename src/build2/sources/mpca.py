"""MPCA / MN-DNR continuous nitrate network, from the Cooperative Stream Gaging API.

WHY THIS SOURCE EXISTS SEPARATELY. 22 of its 32 stations sit on a USGS gage, but only 2 of those publish nitrate to NWIS -- MPCA operates the sensor and the series lives only in the Minnesota system. Neither a USGS nor a WQP query returns the other 30, so omitting this adapter loses 30 corn-belt nitrate sites and, because the AOE is seeded from sensors, shortens the corn-belt region by about 130 km of its northern edge.

UNITS: mg/L AS N, NO CONVERSION. The source never documents this -- `var_name` reads "NO3 + NO2 (mg/L)" without stating as-N or as-NO3, and the bundled readme is only a quality-flag dictionary -- so it was settled by measurement. Cedar River near Austin appears in both systems, as CSG 48020005 and USGS 05457000, and over 56,125 exactly-matching timestamps in 2018-2019 the median ratio MPCA/USGS is 1.0000, the median absolute difference 0.0000 mg/L, and 60% of readings are identical to three decimals. USGS 99133 is defined as milligrams per litre AS NITROGEN, so CSG is the same quantity in the same units; as-NO3 would have given 4.4269. The CSG record even begins at the exact USGS begin timestamp, 2018-03-01 19:30 UTC -- one instrument feeding two systems.

That comparison also independently confirms `_RECORDING_TZ` below: get the recording offset wrong and essentially none of those 56,125 timestamps would align.

Data is already on disk under `raw/water/MPCA_CSG/`, fetched by experiments/research/mpca-csg. Variable 341 is nitrate; the rest of the suite (discharge 262, stage 232, water temp 450, spec cond 824, pH 860, DO 866, turbidity 811, precipitation 11) is kept, which is what makes these stations useful as graph nodes even where the nitrate record is a single season.
"""

from __future__ import annotations

import io as _io
import re
import zipfile

import numpy as np
import pandas as pd

from .. import config
from . import base

NAME = "MPCA"

_DIR = config.RAW_WATER / "MPCA_CSG"
_SITES = _DIR / "mpca_csg_sites.csv"

# CSG records in Central STANDARD time year-round, not civil Central. Verified against the API's own epoch: for site 15031001 the API reports provisional.end = 1781032500 = 2026-06-09 19:15 UTC, and the CSV's last level reading is 13:15 -- a 6-hour offset in June, when civil Central is UTC-5. Localizing to "America/Chicago" would read summer timestamps as CDT and place every one an hour early for the eight DST months. This is the station's RECORDING convention and is separate from the civil zone used to define a local calendar day, which stays America/Chicago.
_RECORDING_TZ = "Etc/GMT+6"

NITRATE_VAR = 341
VAR_LABEL = {11: "precipitation", 232: "level", 262: "discharge", 341: "nitrate", 430: "rel_humidity",
             441: "soil_temp", 450: "water_temp", 461: "air_tmax", 471: "air_tmin", 481: "air_temp",
             490: "pet", 500: "wind_dir", 511: "wind_speed", 512: "wind_gust", 558: "pressure",
             750: "solar_rad", 811: "turbidity", 824: "spec_cond", 860: "ph", 866: "diss_oxy"}


def _sites() -> pd.DataFrame:
    if not _SITES.exists():
        return pd.DataFrame()
    return pd.read_csv(_SITES, dtype={"csg_id": str, "usgs_id": str, "variables": str})


def _variable_ids(cell) -> list[int]:
    """Variable ids from whatever form the site list holds them in.

    Parsed by regex rather than by splitting on a separator: the column has been written both as "232,262,341" and as a numpy repr "[232 262 341]", and a comma-split silently yields ONE token for the latter, so every station reads as having no nitrate and `pcodes` comes out null for the whole source without erroring.
    """
    return [int(x) for x in re.findall(r"\d+", str(cell or ""))]


def discover() -> pd.DataFrame:
    s = _sites()
    if s.empty:
        return base.conform_discovery(pd.DataFrame(), NAME)
    has_nitrate = s.variables.apply(lambda v: NITRATE_VAR in _variable_ids(v))
    out = pd.DataFrame({
        "source_site_id": s.csg_id.astype(str),
        "station_name": s["name"],
        "lat": pd.to_numeric(s.lat, errors="coerce"),
        "lon": pd.to_numeric(s.lon, errors="coerce"),
        "state": "mn",
        "county": pd.NA,
        "huc": pd.NA,
        "agency": s.providers.fillna("PCA"),
        "datum": pd.NA,
        "altitude_m": np.nan,
        "source_drainage_area_km2": np.nan,
        "site_type_raw": pd.NA,
        "regime": "continuous",
        "pcodes": np.where(has_nitrate, str(NITRATE_VAR), pd.NA),
        "reported_units": "mg/L as N",
        "detection_limit": np.nan,
        "tz": "America/Chicago",
    })
    out = out.dropna(subset=["lat", "lon"])
    out = out[config.in_seed_boxes(out.lat.values, out.lon.values)]
    return base.conform_discovery(out.reset_index(drop=True), NAME)


def _read_zip(native_id: str) -> pd.DataFrame | None:
    """Long-format observations from the station's archive: one CSV per variable inside one ZIP."""
    zp = _DIR / f"csg_{native_id}.zip"
    if not zp.exists():
        return None
    frames = []
    with zipfile.ZipFile(_io.BytesIO(zp.read_bytes())) as z:
        for n in z.namelist():
            m = re.match(rf"csg_{native_id}_(\d+)_(.+)\.csv$", n)
            if not m:
                continue
            d = pd.read_csv(z.open(n), dtype={"station": str})
            d["variable"] = int(m.group(1))
            frames.append(d)
    return pd.concat(frames, ignore_index=True) if frames else None


# MPCA'S MISSING-DATA FILL VALUE, WHICH ARRIVES AS A NUMBER. `-99999` is not a reading; it is "no reading",
# and `pd.to_numeric` has no way to know that. Left alone it propagates: `io.daily_max` takes a day's maximum,
# so a day whose only sample is the sentinel becomes a -99,999 mg/L day, and six sites carried exactly that
# into `interim2/water/daily/`. 10,439 native readings across the cohort, all MPCA, all at this one value --
# verified in the raw zip for 43007002, where the source's own quality flag reads `Unchecked` on 2,496 of them
# and `Threshold Exceedance` on 1,283. There is no positive counterpart; `+99999` does not occur.
#
# Mapped HERE rather than in chunk 6 because it is a source-specific encoding, and reading a documented fill
# value as missing is reading the format correctly rather than deriving anything -- which is what keeps chunk
# 1's "everything fetched, nothing derived" rule intact. It applies to every variable, not just nitrate.
#
# The quality flags themselves are deliberately NOT filtered on. `Ice` alone is 73,109 rows and `Unknown
# measurement` 84,155; how much of that to trust is a real question and a much larger one than a fill value.
_SENTINEL = -99999.0


def fetch(native_id: str, start=None, end=None, min_nitrate_days: int = 0) -> pd.DataFrame | None:
    """Wide native-resolution observations, one column per variable, named by label where known.

    The `quality` flag is carried through per variable as `{label}_quality` rather than dropped: it is the source's own statement about whether a value is checked, and there is nowhere later to recover it.
    """
    raw = _read_zip(native_id)
    if raw is None or raw.empty:
        return None
    raw["tstamp"] = pd.to_datetime(raw.tstamp, errors="coerce")
    raw["value"] = pd.to_numeric(raw.value, errors="coerce").replace(_SENTINEL, pd.NA)
    raw = raw[raw.tstamp.notna()]
    if raw.empty:
        return None
    raw["label"] = raw.variable.map(VAR_LABEL).fillna("var" + raw.variable.astype(str))

    wide = raw.pivot_table(index="tstamp", columns="label", values="value", aggfunc="max")
    if "quality" in raw.columns:
        # `quality` mixes numeric codes (200) with words ("Unchecked"), so the pivot yields object columns holding both int and str and pyarrow refuses to write them. Cast to string.
        raw["quality"] = raw.quality.astype("string")
        q = raw.pivot_table(index="tstamp", columns="label", values="quality",
                            aggfunc="first").add_suffix("_quality")
        wide = wide.join(q.astype("string"), how="left")
    wide.index = pd.DatetimeIndex(wide.index).tz_localize(_RECORDING_TZ).tz_convert("UTC")
    wide = wide[wide.index.notna()]
    d = base.normalise_obs(wide)
    if start is not None:
        d = d[d.index >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        d = d[d.index <= pd.Timestamp(end, tz="UTC")]
    return d if len(d) else None
